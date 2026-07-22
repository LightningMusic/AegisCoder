# AegisCoder - Move-AIFolder.ps1
#
# Safely relocates C:\Coding\AI into C:\Coding\Python Projects\AegisCoder\AI
# without breaking pathing. This does NOT do a raw Move-Item. It:
#
#   1. Runs prerequisite checks (source exists, destination is clear, etc.)
#   2. Scans the source for anything that looks like a venv and skips it
#      (venvs must be rebuilt fresh at the new location, never moved)
#   3. Scans all text files in the source for hardcoded references to the
#      OLD path (C:\Coding\AI) and reports every match with file and line
#      number, BEFORE anything is touched
#   4. Copies everything to the destination with Robocopy (verified,
#      logged, resumable) -- the source is only deleted after the copy
#      is confirmed to have succeeded
#   5. Re-scans the NEW location for the same old-path references so you
#      know exactly what still needs manual fixing afterward
#
# DEFAULT MODE IS DRY RUN. Nothing is copied, moved, or deleted unless you
# pass -Execute. Run it once without -Execute first and read the report.
#
# Usage:
#   .\Move-AIFolder.ps1                  (dry run -- report only, no changes)
#   .\Move-AIFolder.ps1 -Execute         (actually perform the move)
#   .\Move-AIFolder.ps1 -Execute -FixPaths   (also auto-replace old path
#                                              strings in text files at the
#                                              new location; each modified
#                                              file gets a .bak backup)
#
# Run this from anywhere; it does not need to be run from inside either
# folder.

param(
    [string]$SourcePath = "C:\Coding\AI",
    [string]$DestPath = "C:\Coding\Python Projects\AegisCoder\AI",
    [switch]$Execute,
    [switch]$FixPaths,
    [switch]$Force
)

$OldPathVariants = @(
    "C:\Coding\AI",
    "C:/Coding/AI",
    "C:\\Coding\\AI"
)

# File types worth scanning for hardcoded path references.
$ScanExtensions = @(
    "*.py", "*.ps1", "*.psm1", "*.json", "*.env", "*.env.example",
    "*.md", "*.cfg", "*.ini", "*.toml", "*.txt", "*.yaml", "*.yml"
)

function Write-OK    { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Info  { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Section {
    param($msg)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host $msg
    Write-Host "============================================================"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "AegisCoder - Move AI Folder"
Write-Host "============================================================"
Write-Host ""
Write-Info "Source:      $SourcePath"
Write-Info "Destination: $DestPath"
if (-not $Execute) {
    Write-Warn "DRY RUN MODE -- no files will be changed. Pass -Execute to perform the move."
}
Write-Host ""

# ----------------------------------------------------------------
# Step 1: Prerequisite checks
# ----------------------------------------------------------------
Write-Section "STEP 1: PREREQUISITE CHECKS"

if (-not (Test-Path $SourcePath)) {
    Write-Err "Source path does not exist: $SourcePath"
    exit 1
}
Write-OK "Source folder found"

$DestParent = Split-Path -Parent $DestPath
if (-not (Test-Path $DestParent)) {
    Write-Err "Destination parent folder does not exist: $DestParent"
    Write-Info "AegisCoder's project folder must already exist before running this script."
    exit 1
}
Write-OK "Destination parent folder found: $DestParent"

if (Test-Path $DestPath) {
    if (-not $Force) {
        Write-Err "Destination already exists: $DestPath"
        Write-Info "Remove it first, or re-run with -Force to merge into it."
        exit 1
    } else {
        Write-Warn "Destination already exists -- will merge into it (-Force was passed)"
    }
} else {
    Write-OK "Destination is clear"
}

# Confirm this drive has enough free space (rough check, uses source size)
try {
    $sourceSizeBytes = (Get-ChildItem -Path $SourcePath -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    $sourceSizeMB = [math]::Round($sourceSizeBytes / 1MB, 1)
    $destDrive = (Get-Item $DestParent).PSDrive
    $freeMB = [math]::Round($destDrive.Free / 1MB, 1)
    Write-Info "Source size: $sourceSizeMB MB. Free space on destination drive: $freeMB MB."
    if ($freeMB -lt ($sourceSizeMB * 1.2)) {
        Write-Warn "Free space is tight (less than 120% of source size). Consider freeing up space first."
    } else {
        Write-OK "Sufficient free space"
    }
} catch {
    Write-Warn "Could not calculate size/space check: $_"
}

# ----------------------------------------------------------------
# Step 2: venv and git safety checks
# ----------------------------------------------------------------
Write-Section "STEP 2: VENV / GIT SAFETY CHECK"

$venvDirs = Get-ChildItem -Path $SourcePath -Directory -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("venv", ".venv", "env", ".env-dir") }

$excludeDirs = @()
if ($venvDirs) {
    Write-Warn "Found what looks like a virtual environment folder(s):"
    foreach ($v in $venvDirs) {
        Write-Warn "  - $($v.FullName)"
        $excludeDirs += $v.FullName
    }
    Write-Info "Virtual envs contain absolute paths baked into their activate scripts."
    Write-Info "These will be EXCLUDED from the copy. Rebuild them fresh at the new"
    Write-Info "location afterward (e.g. 'python -m venv venv' or 'uv venv')."
} else {
    Write-OK "No venv-style folders detected"
}

$gitDir = Join-Path $SourcePath ".git"
if (Test-Path $gitDir) {
    Write-Warn "Source contains a .git folder -- it is its own git repository."
    Write-Info "It will still be moved (git repos are portable), but if the"
    Write-Info "destination (AegisCoder) is ALSO a git repo, this will create a"
    Write-Info "nested repository, which git handles ambiguously. Review after moving."
} else {
    Write-OK "No .git folder in source -- not its own repository"
}

# ----------------------------------------------------------------
# Step 3: scan source for hardcoded old-path references
# ----------------------------------------------------------------
Write-Section "STEP 3: SCANNING FOR HARDCODED PATH REFERENCES"

function Scan-ForOldPaths {
    param([string]$RootPath, [string]$Label)

    $matches = @()
    foreach ($ext in $ScanExtensions) {
        $files = Get-ChildItem -Path $RootPath -Filter $ext -Recurse -File -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            try {
                $lines = Get-Content -Path $f.FullName -ErrorAction Stop
            } catch {
                continue
            }
            for ($i = 0; $i -lt $lines.Count; $i++) {
                foreach ($old in $OldPathVariants) {
                    if ($lines[$i] -like "*$old*") {
                        $matches += [PSCustomObject]@{
                            File = $f.FullName
                            Line = $i + 1
                            Text = $lines[$i].Trim()
                        }
                        break
                    }
                }
            }
        }
    }

    if ($matches.Count -eq 0) {
        Write-OK "$Label -- no hardcoded old-path references found"
    } else {
        Write-Warn "$Label -- found $($matches.Count) reference(s) to the old path:"
        foreach ($m in $matches) {
            Write-Host "    $($m.File):$($m.Line)" -ForegroundColor Yellow
            Write-Host "        $($m.Text)" -ForegroundColor DarkYellow
        }
    }
    return $matches
}

$preMoveMatches = Scan-ForOldPaths -RootPath $SourcePath -Label "Source scan"

if ($preMoveMatches.Count -gt 0) {
    Write-Host ""
    Write-Info "These files reference the OLD path and will need updating after the move."
    Write-Info "Re-run this script with -Execute -FixPaths to auto-replace them at the"
    Write-Info "new location (each modified file gets a .bak backup first)."
}

if (-not $Execute) {
    Write-Section "DRY RUN COMPLETE"
    Write-Info "No files were changed. Review the output above, then re-run with -Execute."
    exit 0
}

# ----------------------------------------------------------------
# Step 4: perform the copy (Robocopy -- verified, logged, resumable)
# ----------------------------------------------------------------
Write-Section "STEP 4: COPYING FILES"

$LogDir = Join-Path $env:TEMP "AegisCoder-Move"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("robocopy-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

$robocopyArgs = @(
    "`"$SourcePath`"",
    "`"$DestPath`"",
    "/E",           # copy subfolders including empty ones
    "/COPY:DAT",    # data, attributes, timestamps
    "/R:2",         # retry twice on a failed file
    "/W:5",         # wait 5s between retries
    "/LOG:`"$LogFile`"",
    "/TEE",         # show output in console AND log it
    "/NFL",         # don't list every file name (keeps console readable)
    "/NDL"
)

foreach ($ex in $excludeDirs) {
    $robocopyArgs += "/XD"
    $robocopyArgs += "`"$ex`""
}

Write-Info "Running Robocopy (log: $LogFile)..."
$proc = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -NoNewWindow -Wait -PassThru

# Robocopy exit codes 0-7 are all "success" in various forms; 8+ means real errors.
if ($proc.ExitCode -ge 8) {
    Write-Err "Robocopy reported errors (exit code $($proc.ExitCode)). See log: $LogFile"
    Write-Err "SOURCE WAS NOT DELETED. Nothing was lost. Review the log and re-run."
    exit 1
}
Write-OK "Copy completed (robocopy exit code $($proc.ExitCode) -- this is a success code)"

# ----------------------------------------------------------------
# Step 5: verify the copy before touching the source
# ----------------------------------------------------------------
Write-Section "STEP 5: VERIFYING COPY"

function Get-FileCount {
    param($Path, $Exclude)
    $items = Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue
    if ($Exclude) {
        $items = $items | Where-Object {
            $full = $_.FullName
            -not ($Exclude | Where-Object { $full.StartsWith($_) })
        }
    }
    return $items.Count
}

$sourceCount = Get-FileCount -Path $SourcePath -Exclude $excludeDirs
$destCount = Get-FileCount -Path $DestPath

Write-Info "Source file count (excluding venvs): $sourceCount"
Write-Info "Destination file count: $destCount"

if ($destCount -lt $sourceCount) {
    Write-Err "Destination has FEWER files than source. The copy may be incomplete."
    Write-Err "SOURCE WAS NOT DELETED. Review $DestPath manually before proceeding."
    exit 1
}
Write-OK "File counts match (or destination has more, e.g. from a prior -Force merge)"

# ----------------------------------------------------------------
# Step 6: remove the source now that the copy is verified
# ----------------------------------------------------------------
Write-Section "STEP 6: REMOVING SOURCE"

Write-Warn "About to permanently delete: $SourcePath"
Write-Warn "(Excluded venv folders inside it, if any, are also being deleted --"
Write-Warn " rebuild those fresh at the new location.)"
$confirm = Read-Host "Type YES to confirm deletion of the source folder"
if ($confirm -ne "YES") {
    Write-Info "Skipped deletion. Both copies now exist:"
    Write-Info "  Source (unchanged):  $SourcePath"
    Write-Info "  Destination (new):   $DestPath"
    Write-Info "Delete the source manually once you've verified the new location."
    exit 0
}

try {
    Remove-Item -Path $SourcePath -Recurse -Force -ErrorAction Stop
    Write-OK "Source folder removed: $SourcePath"
} catch {
    Write-Err "Could not remove source folder: $_"
    Write-Info "The destination copy is safe and complete. Remove the source manually."
    exit 1
}

# ----------------------------------------------------------------
# Step 7: re-scan the new location and optionally fix paths
# ----------------------------------------------------------------
Write-Section "STEP 7: POST-MOVE PATH SCAN"

$postMoveMatches = Scan-ForOldPaths -RootPath $DestPath -Label "Destination scan"

if ($postMoveMatches.Count -gt 0 -and $FixPaths) {
    Write-Host ""
    Write-Info "Auto-fixing hardcoded path references (-FixPaths was passed)..."
    $filesToFix = $postMoveMatches | Select-Object -ExpandProperty File -Unique

    foreach ($file in $filesToFix) {
        try {
            $backup = "$file.bak"
            Copy-Item -Path $file -Destination $backup -Force
            $content = Get-Content -Path $file -Raw
            foreach ($old in $OldPathVariants) {
                $newPathForVariant = if ($old -eq "C:/Coding/AI") {
                    $DestPath -replace '\\', '/'
                } else {
                    $DestPath
                }
                $content = $content.Replace($old, $newPathForVariant)
            }
            Set-Content -Path $file -Value $content -NoNewline
            Write-OK "Fixed: $file (backup at $backup)"
        } catch {
            Write-Warn "Could not auto-fix $file : $_"
        }
    }
} elseif ($postMoveMatches.Count -gt 0) {
    Write-Host ""
    Write-Warn "The files listed above at the NEW location still reference the OLD path."
    Write-Info "Re-run with -Execute -FixPaths to auto-replace them (backups are made"
    Write-Info "automatically as .bak files), or edit them by hand."
}

if ($venvDirs) {
    Write-Host ""
    Write-Warn "Remember: venv folder(s) were excluded from the move and must be"
    Write-Warn "rebuilt fresh inside the new location:"
    foreach ($v in $venvDirs) {
        $relativeName = Split-Path -Leaf $v.FullName
        Write-Info "  cd `"$DestPath`""
        Write-Info "  python -m venv $relativeName    (or: uv venv)"
    }
}

Write-Section "MOVE COMPLETE"
Write-OK "AI folder is now at: $DestPath"
Write-Info "Next steps:"
Write-Info "  1. Review any -FixPaths output above"
Write-Info "  2. If a venv was excluded, rebuild it (see above) and reinstall deps"
Write-Info "  3. Test that Ollama/AegisCoder still work from the new location"
Write-Info "     before deleting the Robocopy log at: $LogFile"
Write-Host ""