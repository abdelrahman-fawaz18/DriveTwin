# Compatibility entry point for the P6-A2 smoke profile.
[CmdletBinding()]
param([switch]$CheckOnly)

& (Join-Path $PSScriptRoot 'run-release-monitoring.ps1') -Workload 'monitoring-smoke' -CheckOnly:$CheckOnly
