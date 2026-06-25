# AegisCoder - Remote access setup
# Configures Tailscale and generates an access token so you can reach
# AegisCoder from your phone or any other device from anywhere.
#
# What this script does:
#   1. Checks if Tailscale is installed, gives install command if not
#   2. Generates a random access token and writes it to .env
#   3. Sets REMOTE_ACCESS_ENABLED=true in .env
#   4. Adds a Windows Firewall rule for the engine port on the Tailscale adapter
#   5. Prints the URL and token to configure on your phone
#
# Run from the AegisCoder project root:
#   .\scripts\Setup-Remote.ps1

param(
    [int]$Port = 8765,
    [switch]$Force
)

$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = "$Root\.env"
$EnvExample = "$Root\.env.example"

Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder - Remote Access Setup"
Write-Host "============================================================"
Write-Host ""

# Ensure .env exists
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Host "[INFO] Created .env from .env.example"
    } else {
        Write-Host "[ERROR] .env.example not found at $Root"
        Write-Host "        Run this script from the AegisCoder project root."
        exit 1
    }
}

# ----------------------------------------------------------------
# Step 1: Tailscale check
# ----------------------------------------------------------------
Write-Host "==> Checking Tailscale..."

$tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if ($null -eq $tailscaleCmd) {
    Write-Host "[WARN] Tailscale is not installed."
    Write-Host ""
    Write-Host "       Install it with:"
    Write-Host "         winget install --id Tailscale.Tailscale -e"
    Write-Host ""
    Write-Host "       Then on your phone, install the Tailscale app and sign in"
    Write-Host "       to the same account. Both devices will be on your private"
    Write-Host "       Tailscale network and can reach each other from anywhere."
    Write-Host ""
    Write-Host "       Re-run this script after installing Tailscale."
    Write-Host ""
    $continueAnyway = Read-Host "Continue setup without Tailscale? (y/N)"
    if ($continueAnyway -ne "y" -and $continueAnyway -ne "Y") {
        exit 0
    }
    $TailscaleIP = "[your-tailscale-ip]"
} else {
    Write-Host "[OK] Tailscale found"
    try {
        $tsStatus = tailscale ip -4 2>&1
        if ($LASTEXITCODE -eq 0 -and $tsStatus -match '\d+\.\d+\.\d+\.\d+') {
            $TailscaleIP = $tsStatus.Trim()
            Write-Host "[OK] Tailscale IPv4: $TailscaleIP"
        } else {
            Write-Host "[WARN] Tailscale is installed but not logged in or not connected."
            Write-Host "       Log in with: tailscale up"
            $TailscaleIP = "[your-tailscale-ip]"
        }
    } catch {
        $TailscaleIP = "[your-tailscale-ip]"
    }
}

# ----------------------------------------------------------------
# Step 2: Generate token
# ----------------------------------------------------------------
Write-Host ""
Write-Host "==> Generating access token..."

# Read existing token if present
$envContent = Get-Content $EnvFile -Raw -Encoding UTF8
$existingToken = ""
if ($envContent -match 'ACCESS_TOKEN=(.+)') {
    $existingToken = $Matches[1].Trim()
}

if ($existingToken -and -not $Force) {
    Write-Host "[INFO] Access token already set."
    Write-Host "       Use -Force to generate a new one (old token will stop working)."
    $AccessToken = $existingToken
} else {
    # Generate 8 random alphanumeric characters -- easy to type on phone
    $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
    $AccessToken = -join ((1..8) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Write-Host "[OK] Generated new token: $AccessToken"
}

# ----------------------------------------------------------------
# Step 3: Write .env settings
# ----------------------------------------------------------------
Write-Host ""
Write-Host "==> Updating .env..."

# Update or add REMOTE_ACCESS_ENABLED
if ($envContent -match 'REMOTE_ACCESS_ENABLED=') {
    $envContent = $envContent -replace 'REMOTE_ACCESS_ENABLED=\S*', 'REMOTE_ACCESS_ENABLED=true'
} else {
    $envContent += "`nREMOTE_ACCESS_ENABLED=true"
}

# Update or add ACCESS_TOKEN
if ($envContent -match 'ACCESS_TOKEN=') {
    $envContent = $envContent -replace 'ACCESS_TOKEN=.*', "ACCESS_TOKEN=$AccessToken"
} else {
    $envContent += "`nACCESS_TOKEN=$AccessToken"
}

[System.IO.File]::WriteAllText($EnvFile, $envContent, [System.Text.Encoding]::ASCII)
Write-Host "[OK] .env updated"

# ----------------------------------------------------------------
# Step 4: Firewall rule
# ----------------------------------------------------------------
Write-Host ""
Write-Host "==> Configuring Windows Firewall..."

$ruleName = "AegisCoder-Engine-Tailscale"
$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if ($null -ne $existingRule -and -not $Force) {
    Write-Host "[OK] Firewall rule already exists: $ruleName"
} else {
    try {
        if ($null -ne $existingRule) {
            Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        }
        New-NetFirewallRule `
            -DisplayName $ruleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -Profile Any `
            -Description "Allows AegisCoder engine access via Tailscale network" `
            | Out-Null
        Write-Host "[OK] Firewall rule created: port $Port inbound allowed"
    } catch {
        Write-Host "[WARN] Could not create firewall rule: $_"
        Write-Host "       You may need to run this script as Administrator."
        Write-Host "       Or add the rule manually: allow TCP inbound on port $Port"
    }
}

# ----------------------------------------------------------------
# Step 5: Summary
# ----------------------------------------------------------------
Write-Host ""
Write-Host "============================================================"
Write-Host "Remote access configured"
Write-Host "============================================================"
Write-Host ""
Write-Host "  Access token : $AccessToken"
Write-Host "  Engine port  : $Port"
Write-Host ""
if ($TailscaleIP -ne "[your-tailscale-ip]") {
    Write-Host "  Phone URL    : http://$($TailscaleIP):$Port"
} else {
    Write-Host "  Phone URL    : http://[your-tailscale-ip]:$Port"
    Write-Host "                 Run 'tailscale ip -4' after connecting to get your IP"
}
Write-Host ""
Write-Host "  On your phone:"
Write-Host "    1. Install the Tailscale app and sign in to the same account"
Write-Host "    2. Open the URL above in your phone's browser"
Write-Host "    3. Enter the token shown above when prompted"
Write-Host ""
Write-Host "  Keep this token private -- anyone with it can control your agent."
Write-Host ""
Write-Host "  To start the engine with remote access:"
Write-Host "    cd `"$Root`""
Write-Host "    uv run python main.py"
Write-Host ""