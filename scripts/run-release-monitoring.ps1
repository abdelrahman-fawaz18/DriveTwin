# Run one release monitoring qualification.
[CmdletBinding()]
param(
    [ValidateSet('monitoring-smoke', 'W0-30', 'W1-30')]
    [string]$Workload = 'monitoring-smoke',
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$isSmoke = $Workload -eq 'monitoring-smoke'
$checkpoint = if ($isSmoke) { 'P6-A2' } else { 'P6-B1' }
$attemptMinutes = if ($isSmoke) { 15 } else { 55 }
$sleepMinutes = if ($isSmoke) { 25 } else { 60 }
$sleepSeconds = [string]($sleepMinutes * 60)
$assetCount = if ($Workload -eq 'W1-30') { 10 } else { 1 }
$measuredSeconds = if ($isSmoke) { 30 } else { 1800 }
$expectedWarmupEvents = 60 * $assetCount
$expectedMeasuredEvents = $measuredSeconds * $assetCount
$expectedTotalEvents = $expectedWarmupEvents + $expectedMeasuredEvents
if (-not $CheckOnly -and @(Get-Process -Name Codex -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'NOT STARTED: The desktop development client is active. Close it normally and wait 30 seconds.'
}

$tools = Get-Content -LiteralPath '.runtime/external-tools.json' -Raw | ConvertFrom-Json
foreach ($tool in @($tools.python, $tools.node, $tools.git)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw 'NOT STARTED: A prepared tool is missing. Review the qualification prerequisites.'
    }
}
$python = $tools.python
$env:PATH = (Split-Path -Parent $tools.node) + ';' + (Split-Path -Parent $tools.git) + ';' + $env:PATH
$env:PYTHONPATH = 'scripts/release_checks;scripts/monitoring;src'
$env:PYTHONUNBUFFERED = '1'

function Get-DockerIds([string[]]$dockerCommand) {
    $identifiers = @(& $python scripts/linux.py --timeout 25 -- docker @dockerCommand)
    if ($LASTEXITCODE -ne 0) { throw ('Docker inventory check failed: ' + ($dockerCommand -join ' ')) }
    $identifiers | Where-Object { $_ }
}

function Get-DockerInventory {
    [pscustomobject]@{
        containers = @(Get-DockerIds @('ps', '-aq', '--no-trunc'))
        volumes = @(Get-DockerIds @('volume', 'ls', '-q'))
        networks = @(Get-DockerIds @('network', 'ls', '-q', '--no-trunc'))
    }
}

function Test-SameInventory($Before, $After) {
    foreach ($kind in @('containers', 'volumes', 'networks')) {
        $beforeIds = $Before.$kind
        $afterIds = $After.$kind
        if (@($beforeIds | Where-Object { $_ -notin $afterIds }).Count -gt 0 -or
            @($afterIds | Where-Object { $_ -notin $beforeIds }).Count -gt 0) { return $false }
    }
    return $true
}

# Pass Python on standard input to preserve quotes in Windows PowerShell 5.1.
$sourceDigest = @'
import sys
from pathlib import Path
from smoke_policy import CAPACITY, monitoring_profile, verify_browser_tools, verify_chromium_binary, verify_sources

workload = sys.argv[1]
source = verify_sources(workload=workload)
monitoring_profile(workload)
verify_browser_tools()
if workload in CAPACITY:
    verify_chromium_binary()
if not Path("environments/dev.toml").is_file():
    raise RuntimeError("Missing local Ubuntu launcher configuration")
print(source["release_sha256"])
'@ | & $python - $Workload
if ($LASTEXITCODE -ne 0) { throw 'NOT STARTED: The committed source, tools, or profile did not verify.' }
Write-Host ("Committed source, pinned browser tools, and $checkpoint $Workload profile: PASSED")
if ($CheckOnly) {
    Write-Host 'CHECK ONLY: no attempt or sleep request started.'
    return
}

if ($Workload -eq 'W1-30') {
    $oneBus = Get-ChildItem -LiteralPath 'evidence/p6' -Directory -Filter 'external-b1-w0-30-*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($null -eq $oneBus) { throw 'NOT STARTED: Run and review W0-30 first.' }
    $prior = Get-Content -LiteralPath (Join-Path $oneBus.FullName 'operator-result.json') -Raw | ConvertFrom-Json
    if (-not $prior.passed -or $prior.workload -ne 'W0-30' -or
        $prior.release_sha256 -ne $sourceDigest -or -not $prior.cleanup_verified -or
        -not $prior.sleep_request_released) {
        throw 'NOT STARTED: The latest W0-30 lacks a pass with cleanup on this release. Review the qualification prerequisites.'
    }
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd-HHmmss')
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$attemptPrefix = if ($isSmoke) { 'external-a2' } else { 'external-b1-' + $Workload.ToLowerInvariant() }
$relative = 'evidence/p6/' + $attemptPrefix + '-' + $stamp + '-' + $suffix
$session = Join-Path $projectRoot $relative
New-Item -ItemType Directory -Path $session | Out-Null
$runEvidence = $relative + $(if ($isSmoke) { '/smoke' } else { '/monitoring' })
$awakeRelative = '.runtime/p6-external-awake-' + $stamp + '-' + $suffix
$statePath = Join-Path $projectRoot ($awakeRelative + '/state.json')
$summaryPath = Join-Path $session 'operator-result.json'
$summary = [ordered]@{
    status = 'incomplete'; passed = $false; checkpoint = $checkpoint; workload = $Workload
    release_sha256 = $sourceDigest
    started_local = (Get-Date).ToString('o'); finished_local = $null
    smoke_exit_code = $null; smoke_passed = $false; fixture_started = $null
    cleanup_verified = $false; sleep_request_released = $false
    evidence_directory = $runEvidence; error = $null
}
$summary | ConvertTo-Json | Set-Content -LiteralPath $summaryPath -Encoding UTF8
$baseline = $null
$awake = $null
try {
    Write-Host ('RESULT FOLDER: ' + $session)
    Write-Host ('START TIME: ' + (Get-Date).ToString('HH:mm:ss'))
    Write-Host ('If still running at ' + (Get-Date).AddMinutes($attemptMinutes).ToString('HH:mm:ss') + ', press Ctrl+C ONCE and allow cleanup.')
    Write-Host 'Checking existing Docker resources. Allow about one minute.'
    $baseline = Get-DockerInventory
    $baseline | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $session 'inventory-before.json') -Encoding UTF8
    $sleepHelper = @{
        FilePath = $python
        ArgumentList = @('-u', 'scripts/windows_awake.py', 'hold', $awakeRelative,
            '--lease-seconds', $sleepSeconds, '--max-seconds', $sleepSeconds)
        WorkingDirectory = $projectRoot
        WindowStyle = 'Hidden'
        PassThru = $true
        RedirectStandardOutput = Join-Path $session 'sleep-helper.log'
        RedirectStandardError = Join-Path $session 'sleep-helper-error.log'
    }
    $awake = Start-Process @sleepHelper
    for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $statePath); $i++) {
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $statePath) -or
        (Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).status -ne 'holding') {
        throw 'NOT STARTED: Temporary sleep prevention could not be confirmed.'
    }
    Write-Host ("Temporary sleep prevention is active for at most $sleepMinutes minutes. Keep power connected and the lid open.")
    Write-Host ("Starting ONE $checkpoint $Workload attempt. The 2 GiB launch gate remains active. Chromium runs hidden.")
    if (-not $isSmoke) {
        Write-Host 'The paced window lasts 31 minutes after setup. Quiet periods are expected.'
    }
    try {
        $ErrorActionPreference = 'Continue'
        & $python -u scripts/release_checks/qualify_monitoring.py --workload $Workload --evidence-dir $runEvidence 2>&1 |
            Tee-Object -FilePath (Join-Path $session 'runner-console.log') |
            ForEach-Object { Write-Host $_ }
        $summary.smoke_exit_code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    $qualificationPath = Join-Path $projectRoot ($runEvidence + '/qualification.json')
    $measurementPath = Join-Path $projectRoot ($runEvidence + '/measurement.json')
    if (Test-Path -LiteralPath $qualificationPath) {
        $qualification = Get-Content -LiteralPath $qualificationPath -Raw | ConvertFrom-Json
        $summary.fixture_started = $qualification.fixture_started
        if (Test-Path -LiteralPath $measurementPath) {
            $measurement = Get-Content -LiteralPath $measurementPath -Raw | ConvertFrom-Json
            $summary.smoke_passed = ($summary.smoke_exit_code -eq 0 -and
                $qualification.checkpoint -eq $checkpoint -and
                $qualification.passed -eq $true -and $qualification.fixture_started -eq $true -and
                $qualification.shared_core.release_sha256 -eq $sourceDigest -and
                $qualification.shared_core_after.release_sha256 -eq $sourceDigest -and
                $measurement.passed -eq $true -and $measurement.profile.workload -eq $Workload -and
                @($measurement.profile.assets).Count -eq $assetCount -and
                $measurement.events.warmup_events -eq $expectedWarmupEvents -and
                $measurement.events.measured_events -eq $expectedMeasuredEvents -and
                $measurement.events.unique_generated -eq $expectedTotalEvents -and
                $measurement.events.accepted -eq $expectedTotalEvents)
            if (-not $isSmoke) {
                $summary.smoke_passed = ($summary.smoke_passed -and $measurement.phase6_release_workload_passed)
                if ($Workload -eq 'W1-30') {
                    $summary.smoke_passed = ($summary.smoke_passed -and $measurement.Q12_W1_passed)
                }
            }
        }
    }
} catch {
    $summary.error = $_.Exception.Message
    Write-Host ('ATTEMPT ERROR: ' + $summary.error)
} finally {
    Write-Host 'Verifying Docker cleanup and releasing the temporary sleep request. Please wait.'
    if ($null -ne $baseline) {
        try {
            $after = Get-DockerInventory
            $after | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $session 'inventory-after.json') -Encoding UTF8
            $summary.cleanup_verified = Test-SameInventory $baseline $after
        } catch { Write-Host 'Docker cleanup could not be verified. Preserve the results for review.' }
    }
    if ($null -eq $awake) {
        $summary.sleep_request_released = $true
    } elseif (Test-Path -LiteralPath $statePath) {
        try {
            & $python scripts/windows_awake.py stop $awakeRelative | Out-Null
            $exited = $awake.WaitForExit(10000)
            $released = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $summary.sleep_request_released = ($exited -and $released.status -eq 'released')
        } catch { Write-Host 'Sleep request release could not be verified. The request is time-limited.' }
    }
    $summary.passed = ($summary.smoke_passed -and $summary.cleanup_verified -and $summary.sleep_request_released)
    $summary.status = if ($summary.passed) {
        if ($isSmoke) { 'P6-A2 SMOKE PASSED; EVIDENCE REVIEW PENDING' }
        else { "P6-B1 $Workload PASSED; EVIDENCE REVIEW PENDING" }
    } elseif ($summary.fixture_started -eq $false -and $summary.cleanup_verified -and $summary.sleep_request_released) {
        'NOT STARTED; LAUNCH CHECKS BLOCKED'
    } else {
        'FAILED OR INCOMPLETE; REVIEW REQUIRED'
    }
    $summary.finished_local = (Get-Date).ToString('o')
    $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host ('FINAL RESULT: ' + $summary.status)
    Write-Host ('Cleanup verified: ' + $summary.cleanup_verified)
    Write-Host ('Sleep request released: ' + $summary.sleep_request_released)
    Write-Host ('RESULT FOLDER: ' + $session)
}
if (-not $summary.passed) { exit 1 }
