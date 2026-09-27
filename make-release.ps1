# make-release.ps1 - build a distributable zip of the DSES Radio Astronomy Workbench.
#
# Reads APP_VERSION from dses_workbench.py, copies the runtime files
# into dist\dses-workbench-<version>\, zips the folder, prints the
# path to the resulting archive.
#
# Run from the project root:    .\make-release.ps1
#
# Files included in the bundle:
#   dses_workbench.py             - the application
#   dses_spectrum_analyzer.py     - launch shim under the pre-1.4.0 module name
#   LICENSE                       - GPL-3.0
#   launcher.bat / .ps1           - Windows launcher
#   launcher.sh                   - Linux / macOS launcher
#   install-shortcut.ps1          - Windows desktop-shortcut installer
#   dses-workbench.desktop - Linux desktop file (template)
#   icons\dses_workbench.{ico,png,icns} - icons
#   environment.yml               - reference for env reproducibility
#   DSES_Radio_Astronomy_Workbench_Installation.pdf - install / update guide (if built)
#
# Excluded: .conda\, .git\, .vscode\, __pycache__, CLAUDE.md,
#           icons\generate-icon.py, make-release.*, settings files.

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
Push-Location $root
try {
    # --- Pull version from the source file ---
    $main = Join-Path $root 'dses_workbench.py'
    if (-not (Test-Path $main)) {
        throw "dses_workbench.py not found in $root"
    }
    $verMatch = Select-String -Path $main -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $verMatch) {
        throw "Could not find APP_VERSION in $main"
    }
    $version = $verMatch.Matches[0].Groups[1].Value
    Write-Host "Building release for version $version"

    # --- Lay out the staging dir ---
    $bundle = "dses-workbench-$version"
    $dist   = Join-Path $root 'dist'
    $stage  = Join-Path $dist $bundle
    $zip    = Join-Path $dist "$bundle.zip"

    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
    if (Test-Path $zip)   { Remove-Item -Force $zip }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'icons') | Out-Null

    # --- Copy runtime files ---
    $files = @(
        'dses_workbench.py',
        'dses_spectrum_analyzer.py',   # launch shim under the pre-1.4.0 name
        'dses_radio.py',               # shared B210 layer (also used by the EVE modem)
        'sigproc_fil.py',
        'pulsar_planner.py',
        'pulsar_sim.py',
        'ezra_txt.py',
        'driftscan_review.py',
        'fold_analysis.py',
        'fold_pdf.py',
        'iq_to_fil.py',
        'updater.py',
        'LICENSE',
        'launcher.bat',
        'launcher.ps1',
        'launcher.sh',
        'install-shortcut.ps1',
        'install-shortcut.command',
        'dses-workbench.desktop',
        'environment.yml'
    )
    foreach ($f in $files) {
        if (Test-Path $f) {
            Copy-Item $f -Destination $stage
        } else {
            Write-Warning "Missing (skipped): $f"
        }
    }
    foreach ($f in @('icons\dses_workbench.ico', 'icons\dses_workbench.png', 'icons\dses_workbench.icns')) {
        if (Test-Path $f) { Copy-Item $f -Destination (Join-Path $stage 'icons') }
        else { Write-Warning "Missing (skipped): $f" }
    }
    # Pre-built SoapySDRPlay3 module for Windows (SDRplay support). Not on
    # conda-forge, so we ship it; the install guide section 1A tells the user where
    # to copy it. README.txt records the ABI it was built against.
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'sdrplay') | Out-Null
    foreach ($f in @('vendor\windows\sdrPlaySupport.dll', 'vendor\windows\README.txt')) {
        if (Test-Path $f) { Copy-Item $f -Destination (Join-Path $stage 'sdrplay') }
        else { Write-Warning "Missing (skipped): $f" }
    }
    # Install guide PDF (the DSES-styled PDF is the deliverable; the .docx
    # is a developer-side intermediate and stays out of the bundle).
    foreach ($doc in @('DSES_Radio_Astronomy_Workbench_Installation.pdf')) {
        if (Test-Path $doc) { Copy-Item $doc -Destination $stage }
        else { Write-Warning "Missing (skipped): $doc - run build_doc.py first" }
    }
    # Default SigMF playback sample - ships so users without any SDR
    # attached can still launch and see live spectrum. Large (~500+ MB).
    foreach ($f in @('sample.sigmf-data', 'sample.sigmf-meta')) {
        if (Test-Path $f) {
            Copy-Item $f -Destination $stage
        } else {
            Write-Warning "Missing playback sample (skipped): $f"
        }
    }
    # PRESTO bridge + build recipes (the app's Analyze/Quick-look features
    # shell out to these; Help points users at presto/build_presto.sh).
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'presto') | Out-Null
    foreach ($f in @('presto\presto_bridge.py', 'presto\build_presto.sh',
                     'presto\build_presto_macos.sh', 'presto\extend_ut1.sh',
                     'presto\README.md')) {
        if (Test-Path $f) { Copy-Item $f -Destination (Join-Path $stage 'presto') }
        else { Write-Warning "Missing (skipped): $f" }
    }
    if (Test-Path 'presto\par') {
        Copy-Item 'presto\par' -Destination (Join-Path $stage 'presto') -Recurse
    }

    # --- Local-import completeness check (guards against the 1.1.8 incident:
    # the app grew module files that never made it into the ship list above,
    # and the published zip died at startup with ModuleNotFoundError). Scan
    # every staged .py for top-level local imports and fail the build if the
    # imported module is a repo file that is not in the staging tree. ---
    $repoPy = Get-ChildItem -Path $root -Filter '*.py' -File | ForEach-Object { $_.BaseName }
    $stagedPy = Get-ChildItem -Path $stage -Filter '*.py' -File -Recurse | ForEach-Object { $_.BaseName }
    $missing = @()
    Get-ChildItem -Path $stage -Filter '*.py' -File | ForEach-Object {
        Select-String -Path $_.FullName -Pattern '^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)' |
            ForEach-Object {
                $mod = $_.Matches[0].Groups[1].Value
                if (($repoPy -contains $mod) -and -not ($stagedPy -contains $mod)) {
                    $missing += "$($_.Filename): imports '$mod' but $mod.py is not staged"
                }
            }
    }
    if ($missing.Count -gt 0) {
        $missing | Sort-Object -Unique | ForEach-Object { Write-Error $_ -ErrorAction Continue }
        throw "Staging tree is missing local modules - add them to the ship list above."
    }
    Write-Host "Local-import completeness check passed."

    # --- Normalize Unix-consumed text files to LF in the staging tree ---
    # Checkout line endings become SHIPPED line endings, and a Windows
    # autocrlf checkout gave launcher.sh a CRLF shebang in the 1.1.6 zip --
    # which silently broke launching on Linux/macOS. .gitattributes now forces
    # LF in fresh checkouts; this is the build-time guarantee regardless of
    # how the tree was checked out.
    $lfExts = @('.sh', '.command', '.desktop', '.py', '.yml')
    Get-ChildItem -Path $stage -Recurse -File |
        Where-Object { $lfExts -contains $_.Extension } |
        ForEach-Object {
            $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
            $hasCR = $false
            for ($i = 0; $i -lt $bytes.Length; $i++) {
                if ($bytes[$i] -eq 13) { $hasCR = $true; break }
            }
            if ($hasCR) {
                $text = [System.Text.Encoding]::UTF8.GetString($bytes)
                $text = $text.Replace("`r`n", "`n").Replace("`r", "`n")
                [System.IO.File]::WriteAllBytes($_.FullName,
                    [System.Text.Encoding]::UTF8.GetBytes($text))
                Write-Host "  normalized to LF: $($_.Name)"
            }
        }

    # --- Zip ---
    # Build the zip with .NET ZipArchive, writing entry paths with FORWARD
    # SLASHES. We deliberately do NOT use Compress-Archive: Windows PowerShell
    # 5.1's Compress-Archive writes entry paths with backslashes (a violation of
    # the ZIP spec, which requires '/'), which extract as literal-backslash
    # *filenames* on macOS/Linux and silently break the in-app updater there.
    # Files only; the extractor recreates directories.
    # (Retry wraps the whole build: a real-time AV scan or a just-launched copy
    # can briefly lock a freshly-staged file.)
    Add-Type -AssemblyName System.IO.Compression | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    $stageFull = (Resolve-Path $stage).Path
    $prefix = Split-Path $stageFull -Leaf          # the versioned top-folder name
    $maxAttempts = 5
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            if (Test-Path $zip) { Remove-Item -Force $zip }
            $fs = [System.IO.File]::Open($zip, [System.IO.FileMode]::CreateNew)
            $archive = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
            try {
                Get-ChildItem -Path $stage -Recurse -File | ForEach-Object {
                    $rel = $_.FullName.Substring($stageFull.Length + 1).Replace('\', '/')
                    $entry = $archive.CreateEntry("$prefix/$rel", [System.IO.Compression.CompressionLevel]::Optimal)
                    $es = $entry.Open()
                    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
                    $es.Write($bytes, 0, $bytes.Length)
                    $es.Dispose()
                }
            } finally {
                $archive.Dispose(); $fs.Dispose()
            }
            break
        } catch {
            if ($attempt -eq $maxAttempts) { throw }
            Write-Warning ("Zip attempt {0}/{1} failed ({2}); retrying in 3s..." -f $attempt, $maxAttempts, $_.Exception.Message)
            if (Test-Path $zip) { Remove-Item -Force $zip -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 3
        }
    }
    $size = (Get-Item $zip).Length
    # Emit the .sha256 sidecar with the EXACT name and format the in-app
    # updater fetches: updater.sha256_url_for() turns "...-<v>.zip" into
    # "...-<v>.sha256" (NOT ".zip.sha256"), and fetch_published_sha256()
    # reads "<hex>  <filename>". A hand-named sidecar 404'd the 1.3.0
    # update on the Mac (2026-08-06) — generate it here so publishing is
    # just "upload everything in dist/".
    $hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    $sidecar = $zip -replace '\.zip$', '.sha256'
    "$hash  $(Split-Path $zip -Leaf)`n" | Out-File -FilePath $sidecar -Encoding ascii -NoNewline
    Write-Host ""
    Write-Host "Wrote $zip ($([math]::Round($size/1KB, 1)) KB)"
    Write-Host "Wrote $sidecar (updater-convention name + format)"
    Write-Host "Staging tree retained at $stage"
} finally {
    Pop-Location
}
