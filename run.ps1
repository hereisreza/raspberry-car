# Run the robot control server locally on Windows for development/testing.
# On a non-Raspberry Pi machine, core_motor.py automatically falls back to
# dry-run mode (no gpiozero/lgpio required).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$venvPython = ".\.venv\Scripts\python.exe"

& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt

$hostAddr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$port = if ($env:PORT) { $env:PORT } else { "8000" }

Write-Host "Starting server on http://$($hostAddr):$($port)"
& $venvPython -m uvicorn server:app --host $hostAddr --port $port
