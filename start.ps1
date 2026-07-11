# DogeMiner launcher for Windows (PowerShell)
#   Run with:  powershell -ExecutionPolicy Bypass -File .\start.ps1
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads BOM-less
# scripts as ANSI, and multi-byte characters (emoji, em-dashes) decode into
# smart quotes that terminate strings early and break parsing.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host ">> Starting DogeMiner Full Stack (backend + frontend)..."

# find a python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "Python 3.10+ is required. Install it from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    & $py.Source -m venv venv
}

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

Write-Host "Installing dependencies..."
& $venvPy -m pip install -r backend/requirements.txt -q

Write-Host "Installing optional GPU (OpenCL) extras (skipped on failure)..."
try {
    & $venvPy -m pip install -r backend/requirements-gpu.txt -q
} catch {
    Write-Host "  -> GPU extras unavailable; CPU mining still works."
}

Write-Host ""
Write-Host "[OK] Backend ready."
Write-Host "Open http://localhost:8000 - or http://doge.local (mDNS) from any device on your network"
Write-Host "   - Pick a pool (zpool needs no registration; wallet = login)"
Write-Host "   - Choose CPU or GPU mode and click START MINING"
Write-Host ""

Write-Host "Live activity (stratum wire, hashing, shares, chain requests) streams below."
Write-Host "Verbose lines are tagged [v]. The browser live feed mirrors this with a VERBOSE toggle."
Write-Host ""

# -u = unbuffered stdout so verbose data appears live in this terminal
$env:PYTHONUNBUFFERED = "1"
& $venvPy -u -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
