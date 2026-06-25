# AegisCoder - Build single .exe with PyInstaller
# Run from the AegisCoder project root: .\scripts\Build-Exe.ps1
#
# Output: dist\AegisCoder.exe
# The .exe includes the engine, UI static files, and all dependencies.
# It does NOT include Ollama or the models -- those stay in their
# existing locations and are launched by the app at runtime.

param(
    [switch]$Clean
)

$Root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder - PyInstaller Build"
Write-Host "============================================================"
Write-Host ""

Push-Location $Root

if ($Clean) {
    Write-Host "==> Cleaning previous build..."
    Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Remove-Item -Force "AegisCoder.spec" -ErrorAction SilentlyContinue
    Write-Host "[OK] Cleaned"
}

Write-Host "==> Checking PyInstaller..."
try {
    $null = uv run pyinstaller --version
} catch {
    Write-Host "[INFO] Installing PyInstaller..."
    uv add --dev pyinstaller
}

Write-Host ""
Write-Host "==> Building AegisCoder.exe..."

$StaticDir = "$Root\ui\static"
$AddData = "$StaticDir;ui/static"

uv run pyinstaller `
    --onefile `
    --windowed `
    --name AegisCoder `
    --add-data "$AddData" `
    --hidden-import "engine.safety.job_object" `
    --hidden-import "engine.safety.watchdog" `
    --hidden-import "engine.safety.deletion_guard" `
    --hidden-import "engine.safety.runtime_budget" `
    --hidden-import "engine.safety.process_manager" `
    --hidden-import "engine.middleware.auth" `
    --hidden-import "engine.planning.architect" `
    --hidden-import "engine.planning.executor" `
    --hidden-import "engine.planning.plan_schema" `
    --hidden-import "engine.projects.registry" `
    --hidden-import "engine.projects.state" `
    --hidden-import "engine.projects.models" `
    --hidden-import "aider.coders" `
    --hidden-import "aider.models" `
    --hidden-import "aider.io" `
    --collect-all "webview" `
    main.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[OK] Build complete: dist\AegisCoder.exe"
    Write-Host "[INFO] File size: $([math]::Round((Get-Item 'dist\AegisCoder.exe').Length / 1MB, 1)) MB"
} else {
    Write-Host ""
    Write-Host "[ERROR] Build failed. Check output above."
    Pop-Location
    exit 1
}

Pop-Location