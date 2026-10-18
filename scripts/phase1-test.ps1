# ECONITH Phase 1 — Windows operator entrypoint. Never trades.
# Examples:
# powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Doctor
# powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\phase1-test.ps1 -Mode Quick -Setup
param(
    [ValidateSet("Doctor","Quick","Diagnostic","Hour","Day","Report")]
    [string]$Mode="Doctor",
    [switch]$Setup,
    [string]$OutputBase="",
    [string]$TrialDir=""
)
$ErrorActionPreference="Stop"
$repo=[System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $repo
$python=Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python="python" }
try {
    & $python --version
    if ($LASTEXITCODE -ne 0) { throw "Python unavailable" }
} catch {
    Write-Error "Install Python 3.12, then rerun this command."
    exit 2
}
if ($Setup) {
    if (-not (Test-Path (Join-Path $repo ".venv\Scripts\python.exe"))) {
        & $python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $python=Join-Path $repo ".venv\Scripts\python.exe"
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$extra=@()
if ($OutputBase) { $extra+=@("--output-base",$OutputBase) }
switch ($Mode) {
    "Doctor" {
        & $python -m ci.phase1_operator_lab doctor @extra
    }
    "Quick" {
        & $python -m ci.phase1_operator_lab smoke --allow-public-market-data --profile quick @extra
    }
    "Diagnostic" {
        & $python -m ci.phase1_operator_lab smoke --allow-public-market-data --profile diagnostic @extra
    }
    "Hour" {
        & $python -m ci.phase1_operator_lab smoke --allow-public-market-data --profile hour @extra
    }
    "Day" {
        & $python -m ci.phase1_operator_lab smoke --allow-public-market-data --profile day @extra
    }
    "Report" {
        if (-not $TrialDir) { Write-Error "-TrialDir is required in Report mode";exit 2 }
        & $python -m ci.phase1_operator_lab report --trial-dir $TrialDir
    }
}
exit $LASTEXITCODE
