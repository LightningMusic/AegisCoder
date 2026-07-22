<#
.SYNOPSIS
Starts Aider in the current directory

.DESCRIPTION
Sets up environment and launches Aider with local Ollama model in the current working directory

.EXAMPLE
cd C:\Coding\my-project
.\Start-Aider-Here.ps1
#>

param(
    [string]$OllamaHostName = "127.0.0.1:11434",
    [string]$ModelName = "local-code:7b",
    [int]$MapTokens = 1024
)

# Set environment
$env:OLLAMA_HOST = $OllamaHostName
$env:OLLAMA_API_BASE = "http://$OllamaHostName"
$env:AIDER_ANALYTICS = "false"

$currentPath = Get-Location

Write-Host ""
Write-Host "========================================================" -ForegroundColor Magenta
Write-Host "Starting Aider" -ForegroundColor Magenta
Write-Host "========================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Project: $currentPath" -ForegroundColor Cyan
Write-Host "Model: $ModelName" -ForegroundColor Cyan
Write-Host ""

# Validate Ollama
Write-Host "Checking Ollama connection..." -ForegroundColor Gray
try {
    $null = Invoke-RestMethod "http://$OllamaHostName/api/tags" -TimeoutSec 3 -ErrorAction Stop
    Write-Host "[OK] Ollama is accessible" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Cannot reach Ollama at http://$OllamaHostName" -ForegroundColor Red
    Write-Host "Start Ollama with: ollama serve" -ForegroundColor Yellow
    exit 1
}

# Initialize git if needed
if (-not (Test-Path ".git")) {
    Write-Host "Initializing git repository..." -ForegroundColor Gray
    git init --quiet
    Write-Host "[OK] Git repository initialized" -ForegroundColor Green
}

# Launch Aider
Write-Host ""
Write-Host "Launching Aider..." -ForegroundColor Cyan
Write-Host "Type '/help' for commands, '/exit' to quit" -ForegroundColor Gray
Write-Host ""

# SAFETY: --edit-format diff is mandatory here. Without it, Aider falls
# back to "whole" edit format for unrecognized local models, which means
# the model must retype an entire file to change anything. A 7B model
# under load can silently drop existing code when it does this -- this
# was the leading suspect in a prior incident where a file lost content
# during an unattended run. "diff" mode only ever touches changed lines
# and fails cleanly on a bad match instead of wiping the file.
# --no-gitignore suppresses the interactive "Add .aider* to .gitignore?"
# prompt so this launcher never blocks waiting for keyboard input.
& aider `
    --model "ollama_chat/$ModelName" `
    --weak-model "ollama_chat/$ModelName" `
    --editor-model "ollama_chat/$ModelName" `
    --edit-format diff `
    --no-auto-commits `
    --no-dirty-commits `
    --analytics-disable `
    --no-check-update `
    --no-gitignore `
    --map-tokens $MapTokens
