# One manually initiated W1 attempt using the existing, unchanged qualification runner.
[CmdletBinding()]
param([switch]$CheckOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$toolsPath = Join-Path $projectRoot '.runtime/external-tools.json'
if (-not (Test-Path -LiteralPath $toolsPath)) {
    throw 'Missing local tool settings. Check the prepared tool configuration.'
}
$tools = Get-Content -LiteralPath $toolsPath -Raw | ConvertFrom-Json
foreach ($tool in @($tools.python, $tools.node, $tools.git)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw 'A prepared tool is missing. Check the prepared tool configuration.' }
}
$python = $tools.python
$oldPath = $env:PATH
$oldPythonPath = $env:PYTHONPATH
$oldUnbuffered = $env:PYTHONUNBUFFERED
$env:PATH = (Split-Path -Parent $tools.node) + ';' + (Split-Path -Parent $tools.git) + ';' + $oldPath
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUNBUFFERED = '1'

function Get-ContainerInventory {
    $ids = @(& $python scripts/linux.py --timeout 60 -- docker ps -aq)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect container inventory.' }
    $volumes = @(& $python scripts/linux.py --timeout 60 -- docker volume ls -q)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect volume inventory.' }
    return [pscustomobject]@{ containers = @($ids | Where-Object { $_ }); volumes = @($volumes | Where-Object { $_ }) }
}

try {
    if (-not $CheckOnly -and @(Get-Process -Name codex -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'NOT STARTED: The desktop development client is active. Close it normally, wait 30 seconds, and retry.'
    }
    # Standard input avoids Windows PowerShell 5.1's native -c quote removal.
    @'
import sys
from pathlib import Path
sys.path.insert(0, "scripts/monitoring")
import evdt.workload.correlation
from current_scope import verify_shared
from workload_profile import select
from host_memory_policy import policy
assert verify_shared()["passed"]
p = select("W1", "chromium-automated")
policy()
assert len(p["assets"]) == 10 and p["warmup_samples"] == 60 and p["measured_samples"] == 300
assert Path("qualification/browser/node_modules/playwright/cli.js").is_file()
assert Path("environments/dev.toml").is_file()
print("Tool, runtime-source, and unchanged W1 policy checks: PASSED")
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'Preparation checks failed. No workload was started.' }
    & $tools.node --version
    if ($LASTEXITCODE -ne 0) { throw 'Node could not start. No workload was started.' }
    if ($CheckOnly) {
        Write-Host 'CHECK ONLY: no test, Linux worker, browser, or services were started.'
        return
    }

    $stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd-HHmmss')
    $suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $relative = 'evidence/p1/external-w1-' + $stamp + '-' + $suffix
    $session = Join-Path $projectRoot $relative
    New-Item -ItemType Directory -Path $session | Out-Null
    $runEvidence = $relative + '/w1'
    $awakeRelative = '.runtime/external-awake-' + $stamp + '-' + $suffix
    $awakeDirectory = Join-Path $projectRoot $awakeRelative
    $summary = [ordered]@{
        status = 'incomplete'; passed = $false; scope = 'one unchanged W1 attempt; not all Phase 1'
        started_local = (Get-Date).ToString('o'); finished_local = $null
        workload_exit_code = $null; workload_passed = $false
        cleanup_verified = $false; sleep_request_released = $false
        phase_1_complete = $false; evidence_directory = $runEvidence
        error = $null
    }
    $summaryPath = Join-Path $session 'operator-result.json'
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    $baseline = $null
    $awake = $null
    try {
        Write-Host ('RESULT FOLDER: ' + $session)
        Write-Host ('START TIME: ' + (Get-Date).ToString('HH:mm:ss'))
        Write-Host ('If still running at ' + (Get-Date).AddMinutes(25).ToString('HH:mm:ss') + ', press Ctrl+C ONCE and allow cleanup.')
        Write-Host 'Checking existing Docker resources (normally under one minute).'
        $baseline = Get-ContainerInventory
        $baseline | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $session 'inventory-before.json') -Encoding UTF8
        $awake = Start-Process -FilePath $python -ArgumentList @('-u', 'scripts/windows_awake.py', 'hold', $awakeRelative, '--lease-seconds', '1800', '--max-seconds', '1800') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $session 'sleep-helper.log') -RedirectStandardError (Join-Path $session 'sleep-helper-error.log')
        $statePath = Join-Path $awakeDirectory 'state.json'
        for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $statePath); $i++) { Start-Sleep -Milliseconds 100 }
        if (-not (Test-Path -LiteralPath $statePath)) { throw 'Sleep prevention did not start. No workload was started.' }
        $sleepState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($sleepState.status -ne 'holding') { throw 'Sleep prevention is not active. No workload was started.' }
        Write-Host 'Temporary sleep prevention: active for at most 30 minutes. Keep the lid open and power connected.'
        Write-Host 'Starting ONE ten-bus attempt. The automated Chromium browser is hidden; no dashboard window is expected.'
        Write-Host 'Expect: memory precheck about 45 seconds; setup usually 1-5 minutes; browser stage about 6-10 minutes; final checks/cleanup 1-5 minutes.'
        Write-Host 'Quiet periods are normal. Do not start a second command during the attempt.'
        # Continue permits native stderr to be logged on Windows PowerShell 5.1;
        # the native exit code and unchanged JSON acceptance reports decide success.
        $ErrorActionPreference = 'Continue'
        try {
            & $python -u scripts/monitoring/qualify_sustained_measurement.py --workload W1 --browser chromium-automated --evidence-dir $runEvidence 2>&1 |
                Tee-Object -FilePath (Join-Path $session 'runner-console.log') |
                ForEach-Object { Write-Host $_ }
            $summary.workload_exit_code = $LASTEXITCODE
        } finally { $ErrorActionPreference = 'Stop' }
        $qualificationPath = Join-Path $projectRoot ($runEvidence + '/qualification.json')
        $measurementPath = Join-Path $projectRoot ($runEvidence + '/measurement.json')
        if ((Test-Path -LiteralPath $qualificationPath) -and (Test-Path -LiteralPath $measurementPath)) {
            $qualification = Get-Content -LiteralPath $qualificationPath -Raw | ConvertFrom-Json
            $measurement = Get-Content -LiteralPath $measurementPath -Raw | ConvertFrom-Json
            $summary.workload_passed = ($summary.workload_exit_code -eq 0 -and $qualification.passed -eq $true -and $measurement.passed -eq $true -and $measurement.W1_passed -eq $true)
        }
    } catch {
        $summary.error = $_.Exception.Message
        Write-Host ('ATTEMPT ERROR: ' + $summary.error)
    } finally {
        Write-Host 'Verifying cleanup and releasing the temporary sleep request. Please wait.'
        if ($null -ne $baseline) {
            try {
                $after = Get-ContainerInventory
                $after | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $session 'inventory-after.json') -Encoding UTF8
                $extraContainers = @($after.containers | Where-Object { $_ -notin $baseline.containers })
                $extraVolumes = @($after.volumes | Where-Object { $_ -notin $baseline.volumes })
                $missingVolumes = @($baseline.volumes | Where-Object { $_ -notin $after.volumes })
                $summary.cleanup_verified = ($extraContainers.Count -eq 0 -and $extraVolumes.Count -eq 0 -and $missingVolumes.Count -eq 0)
            } catch { Write-Host 'Cleanup inventory could not be verified; preserve results for review.' }
        }
        if ($null -ne $awake) {
            if (Test-Path -LiteralPath (Join-Path $awakeDirectory 'state.json')) {
                & $python scripts/windows_awake.py stop $awakeRelative | Out-Null
                $awake.WaitForExit(10000) | Out-Null
                try {
                    $released = Get-Content -LiteralPath (Join-Path $awakeDirectory 'state.json') -Raw | ConvertFrom-Json
                    $summary.sleep_request_released = ($released.status -eq 'released')
                } catch { Write-Host 'Sleep-request release could not be verified; it expires automatically within 30 minutes.' }
            }
        } else { $summary.sleep_request_released = $true }
        $summary.passed = ($summary.workload_passed -and $summary.cleanup_verified -and $summary.sleep_request_released)
        $summary.status = $(if ($summary.passed) { 'W1 passed; Phase 1 review pending' } else { 'failed or incomplete; stop and review' })
        $summary.finished_local = (Get-Date).ToString('o')
        $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
        Write-Host ''
        Write-Host ('FINAL RESULT: ' + $summary.status.ToUpperInvariant())
        Write-Host ('W1 workload passed: ' + $summary.workload_passed)
        Write-Host ('Cleanup verified: ' + $summary.cleanup_verified)
        Write-Host ('Sleep request released: ' + $summary.sleep_request_released)
        Write-Host ('RESULT FOLDER: ' + $session)
        Write-Host 'No retry was launched. Review the result folder before another attempt.'
        Write-Host 'A W1 pass alone does not complete Phase 1. Saved results and remaining checks need review.'
    }
    if (-not $summary.passed) { exit 1 }
} finally {
    $env:PATH = $oldPath
    $env:PYTHONPATH = $oldPythonPath
    $env:PYTHONUNBUFFERED = $oldUnbuffered
}
