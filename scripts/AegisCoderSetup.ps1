# AegisCoder Full Setup
# Installs and configures everything needed to run AegisCoder.
# Run this once from the AegisCoder project root as Administrator.
#
#   .\AegisCoderSetup.ps1
#   .\AegisCoderSetup.ps1 -SkipOllama       (if Ollama already set up)
#   .\AegisCoderSetup.ps1 -SkipRemote       (skip Tailscale/remote access)
#   .\AegisCoderSetup.ps1 -SkipBuild        (skip PyInstaller .exe build)
#
# What this does (in order):
#   1. Verify prerequisites (admin, git, uv, Ollama+Aider via existing setup)
#   2. Install Python dependencies into the AegisCoder venv
#   3. Create .env from .env.example if it does not exist
#   4. Configure remote access (Tailscale check + token generation)
#   5. Create Start Menu shortcut
#   6. Optionally build AegisCoder.exe with PyInstaller

param(
    [switch]$SkipOllama,
    [switch]$SkipRemote,
    [switch]$SkipBuild,
    [switch]$Force
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder Full Setup"
Write-Host "============================================================"
Write-Host ""

# ----------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------
function Write-OK    { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Info  { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Section { param($msg)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host $msg
    Write-Host "============================================================"
}

# ----------------------------------------------------------------
# Step 1: Prerequisites
# ----------------------------------------------------------------
Write-Section "CHECKING PREREQUISITES"

# Admin
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Err "This script must be run as Administrator."
    Write-Info "Right-click PowerShell and choose 'Run as Administrator'."
    exit 1
}
Write-OK "Running as Administrator"

# Git
try {
    $gitVer = git --version
    Write-OK "Git: $gitVer"
} catch {
    Write-Err "Git is not installed or not on PATH."
    Write-Info "Install with: winget install --id Git.Git -e"
    exit 1
}

# uv
try {
    $uvVer = uv --version
    Write-OK "uv: $uvVer"
} catch {
    Write-Err "uv is not installed or not on PATH."
    Write-Info "Run C:\Coding\AI\CluadesLocalCodingSetup.ps1 first to install uv."
    exit 1
}

# Ollama
if (-not $SkipOllama) {
    try {
        $ollamaVer = ollama --version
        Write-OK "Ollama: $ollamaVer"
    } catch {
        Write-Warn "Ollama not found on PATH."
        Write-Info "Run C:\Coding\AI\CluadesLocalCodingSetup.ps1 first to install Ollama."
        Write-Info "Or re-run with -SkipOllama if Ollama is installed but not on PATH."
    }

    # Check Ollama API
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop
        Write-OK "Ollama API responding"
    } catch {
        Write-Warn "Ollama API not responding. Start Ollama first, or it will be started by AegisCoder at runtime."
    }

    # Check for local-code:7b model
    try {
        $models = (Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5).models
        $hasModel = $models | Where-Object { $_.name -like "local-code*" }
        if ($hasModel) {
            Write-OK "Model found: $($hasModel[0].name)"
        } else {
            Write-Warn "local-code:7b model not found."
            Write-Info "Run C:\Coding\AI\CluadesLocalCodingSetup.ps1 to create it."
        }
    } catch {
        Write-Warn "Could not check Ollama models (API not running)."
    }
}

# ----------------------------------------------------------------
# Step 2: Python dependencies
# ----------------------------------------------------------------
Write-Section "INSTALLING PYTHON DEPENDENCIES"

Push-Location $Root

if (-not (Test-Path "$Root\pyproject.toml")) {
    Write-Info "Initialising Python project with uv..."
    uv init --bare --python 3.11 | Out-Null
    uv python pin 3.11 | Out-Null
    Write-OK "Python 3.11 pinned"
}

Write-Info "Installing dependencies (this may take a few minutes on first run)..."
uv add fastapi "uvicorn[standard]" pywebview pywin32 psutil httpx python-dotenv aider-chat
if ($LASTEXITCODE -ne 0) {
    Write-Err "Dependency installation failed. Check output above."
    Pop-Location
    exit 1
}
uv add --dev pytest pyinstaller
Write-OK "Dependencies installed"

Pop-Location

# ----------------------------------------------------------------
# Step 3: Environment file
# ----------------------------------------------------------------
Write-Section "SETTING UP ENVIRONMENT"

$EnvFile = "$Root\.env"
$EnvExample = "$Root\.env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-OK "Created .env from .env.example"
    } else {
        Write-Warn ".env.example not found -- skipping .env creation"
    }
} else {
    Write-OK ".env already exists"
}

# Make sure logs and data dirs exist
New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$Root\data\projects" | Out-Null
Write-OK "Runtime directories ready"

# ----------------------------------------------------------------
# Step 4: Remote access
# ----------------------------------------------------------------
if (-not $SkipRemote) {
    Write-Section "CONFIGURING REMOTE ACCESS"

    Write-Info "Checking Tailscale..."
    $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($null -eq $tailscale) {
        Write-Warn "Tailscale not found."
        Write-Info "Install with: winget install --id Tailscale.Tailscale -e"
        Write-Info "Then sign in on both your laptop and phone with the same account."
        Write-Info "Skipping remote access configuration."
    } else {
        Write-OK "Tailscale is installed"

        try {
            $tsIP = (tailscale ip -4 2>&1).Trim()
            if ($LASTEXITCODE -eq 0 -and $tsIP -match '\d+\.\d+\.\d+\.\d+') {
                Write-OK "Tailscale IPv4: $tsIP"
            } else {
                Write-Warn "Tailscale not connected. Run: tailscale up"
                $tsIP = "[run 'tailscale ip -4' after connecting]"
            }
        } catch {
            $tsIP = "[run 'tailscale ip -4' to find your IP]"
        }

        # Generate token if not set
        $envContent = Get-Content $EnvFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        $existingToken = ""
        if ($envContent -match 'ACCESS_TOKEN=(.+)') { $existingToken = $Matches[1].Trim() }

        if (-not $existingToken -or $Force) {
            $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
            $newToken = -join ((1..8) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
            if ($envContent) {
                if ($envContent -match 'ACCESS_TOKEN=') {
                    $envContent = $envContent -replace 'ACCESS_TOKEN=\S*', "ACCESS_TOKEN=$newToken"
                } else {
                    $envContent += "`nACCESS_TOKEN=$newToken"
                }
                if ($envContent -match 'REMOTE_ACCESS_ENABLED=') {
                    $envContent = $envContent -replace 'REMOTE_ACCESS_ENABLED=\S*', 'REMOTE_ACCESS_ENABLED=true'
                } else {
                    $envContent += "`nREMOTE_ACCESS_ENABLED=true"
                }
                [System.IO.File]::WriteAllText($EnvFile, $envContent, [System.Text.Encoding]::ASCII)
            }
            Write-OK "Access token generated: $newToken"
            $existingToken = $newToken
        } else {
            Write-OK "Access token already set (use -Force to regenerate)"
        }

        # Firewall rule
        $ruleName = "AegisCoder-Engine-Tailscale"
        $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if ($null -eq $existingRule -or $Force) {
            if ($null -ne $existingRule) { Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue }
            try {
                New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
                    -LocalPort 8765 -Action Allow -Profile Any | Out-Null
                Write-OK "Firewall rule created for port 8765"
            } catch {
                Write-Warn "Could not create firewall rule: $_"
            }
        } else {
            Write-OK "Firewall rule already exists"
        }

        Write-Host ""
        Write-Info "Phone access:"
        Write-Info "  URL:   http://${tsIP}:8765"
        Write-Info "  Token: $existingToken"
        Write-Info "  Install Tailscale on your phone and sign in to the same account."
    }
} else {
    Write-Info "Skipping remote access setup (-SkipRemote)"
}

# ----------------------------------------------------------------
# Step 5: Start Menu shortcut
# ----------------------------------------------------------------
Write-Section "CREATING START MENU SHORTCUT"

try {
    $StartMenu = [Environment]::GetFolderPath("CommonPrograms")
    $ShortcutPath = "$StartMenu\AegisCoder.lnk"
    $WScriptShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-WindowStyle Hidden -Command `"cd '$Root'; uv run python main.py`""
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.Description = "AegisCoder - Local AI Coding Agent"
    $Shortcut.Save()
    Write-OK "Start Menu shortcut created: $ShortcutPath"
} catch {
    Write-Warn "Could not create Start Menu shortcut: $_"
}

# ----------------------------------------------------------------
# Step 6: PyInstaller build (optional)
# ----------------------------------------------------------------
if (-not $SkipBuild) {
    Write-Section "BUILDING AEGISCODER.EXE"
    Write-Info "This bundles everything into a single .exe (takes a few minutes)..."
    Push-Location $Root
    & "$Root\scripts\Build-Exe.ps1"
    $BuildOk = $LASTEXITCODE -eq 0
    Pop-Location

    if ($BuildOk) {
        # Update shortcut to point at the .exe
        try {
            $StartMenu = [Environment]::GetFolderPath("CommonPrograms")
            $ShortcutPath = "$StartMenu\AegisCoder.lnk"
            $WScriptShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
            $Shortcut.TargetPath = "$Root\dist\AegisCoder.exe"
            $Shortcut.Arguments = ""
            $Shortcut.WorkingDirectory = "$Root\dist"
            $Shortcut.Save()
            Write-OK "Start Menu shortcut updated to point at AegisCoder.exe"
        } catch {
            Write-Warn "Could not update shortcut: $_"
        }
    }
} else {
    Write-Info "Skipping .exe build (-SkipBuild)"
    Write-Info "Launch with: cd `"$Root`" && uv run python main.py"
}

# ----------------------------------------------------------------
# Done
# ----------------------------------------------------------------
Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder setup complete"
Write-Host "============================================================"
Write-Host ""
Write-Info "To launch AegisCoder:"
if (-not $SkipBuild -and (Test-Path "$Root\dist\AegisCoder.exe")) {
    Write-Info "  $Root\dist\AegisCoder.exe"
    Write-Info "  (or use the Start Menu shortcut)"
} else {
    Write-Info "  cd `"$Root`""
    Write-Info "  uv run python main.py"
}
Write-Host ""