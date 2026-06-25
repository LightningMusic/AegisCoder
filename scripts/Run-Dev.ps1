# AegisCoder - Development launcher
# Runs the engine directly with uvicorn (Phase 1 mode).
# Usage: .\scripts\Run-Dev.ps1
# Usage with custom project: .\scripts\Run-Dev.ps1 -ProjectPath "C:\your\project"

param(
    [string]$ProjectPath = ""
)

$Root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder - Development Mode"
Write-Host "============================================================"
Write-Host ""

# Verify we are in the right place
if (-not (Test-Path "$Root\main.py")) {
    Write-Host "[ERROR] main.py not found at $Root"
    Write-Host "        Run this script from inside the AegisCoder project folder."
    exit 1
}

# Verify uv is available
try {
    $null = uv --version
} catch {
    Write-Host "[ERROR] uv not found on PATH."
    Write-Host "        Run C:\Coding\AI\CluadesLocalCodingSetup.ps1 first."
    exit 1
}

# Copy .env.example to .env on first run
if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host "[INFO] Created .env from .env.example -- review values if needed"
}

# Make sure log and data folders exist
New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$Root\data\projects" | Out-Null

if ($ProjectPath -ne "") {
    Write-Host "[INFO] Default project path: $ProjectPath"
    Write-Host "[INFO] Pass this as project_path in your WebSocket messages"
}

Write-Host "[INFO] Engine starting at http://127.0.0.1:8765"
Write-Host "[INFO] Health check: http://127.0.0.1:8765/api/health"
Write-Host "[INFO] WebSocket:    ws://127.0.0.1:8765/ws/chat"
Write-Host "[INFO] Press Ctrl+C to stop"
Write-Host ""

Push-Location $Root
uv run python main.py
Pop-Location