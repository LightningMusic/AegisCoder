<#
.SYNOPSIS
Tests Ollama installation and functionality

.DESCRIPTION
Checks if Ollama is running, lists available models, and tests the local-code:7b model
#>

param(
    [string]$OllamaHostName = "127.0.0.1:11434"
)

Write-Host "=== Ollama Service Check ===" -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod "http://$OllamaHostName/api/tags" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "[OK] Ollama is accessible at http://$OllamaHostName" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Ollama is not accessible at http://$OllamaHostName" -ForegroundColor Red
    Write-Host "Make sure Ollama is running: ollama serve" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== Available Models ===" -ForegroundColor Cyan
try {
    & ollama list
} catch {
    Write-Host "[FAIL] Failed to list models" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Testing local-code:7b Model ===" -ForegroundColor Cyan
Write-Host "Sending test prompt (this may take 10-30 seconds)..."
try {
    $process = Start-Process ollama -ArgumentList 'run local-code:7b "Say: Model is working correctly."' -NoNewWindow -PassThru
    $process.WaitForExit(120000)  # 2 minute timeout
    
    if ($process.ExitCode -eq 0) {
        Write-Host "[OK] Model test passed" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] Model test failed with exit code: $($process.ExitCode)" -ForegroundColor Red
    }
} catch {
    Write-Host "[FAIL] Error running model test: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Testing Ollama API ===" -ForegroundColor Cyan
try {
    $tags = Invoke-RestMethod "http://$OllamaHostName/api/tags" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "[OK] Ollama API responding" -ForegroundColor Green
    Write-Host "Found $($tags.models.Count) model(s)" -ForegroundColor Cyan
} catch {
    Write-Host "[FAIL] Ollama API test failed: $_" -ForegroundColor Red
}
