#!/usr/bin/env bash
# make-release.sh — build a distributable zip of the DSES Radio Astronomy Workbench.
#
# Reads APP_VERSION from dses_workbench.py, copies the runtime files
# into dist/dses-workbench-<version>/, zips the folder, prints the
# path to the resulting archive.
#
# Run from the project root:    ./make-release.sh
#
# See make-release.ps1 for the matching Windows version. Both produce
# bit-identical bundle contents (modulo file timestamps).
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f dses_workbench.py ]; then
    echo "dses_workbench.py not found in $(pwd)" >&2
    exit 1
fi

version="$(grep -oE '^APP_VERSION[[:space:]]*=[[:space:]]*"[^"]+"' dses_workbench.py \
            | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [ -z "$version" ]; then
    echo "Could not find APP_VERSION in dses_workbench.py" >&2
    exit 1
fi

bundle="dses-workbench-$version"
dist="dist"
stage="$dist/$bundle"
archive="$dist/$bundle.zip"

echo "Building release for version $version"

rm -rf "$stage" "$archive"
mkdir -p "$stage/icons"

copy_if_present() {
    local src="$1" dst="$2"
    if [ -e "$src" ]; then
        cp "$src" "$dst"
    else
        echo "  (missing, skipped) $src" >&2
    fi
}

# Runtime files
for f in \
    dses_workbench.py \
    dses_spectrum_analyzer.py \
    dses_radio.py \
    sigproc_fil.py \
    pulsar_planner.py \
    pulsar_sim.py \
    ezra_txt.py \
    driftscan_review.py \
    fold_analysis.py \
    fold_pdf.py \
    iq_to_fil.py \
    updater.py \
    LICENSE \
    launcher.bat \
    launcher.ps1 \
    launcher.sh \
    install-shortcut.ps1 \
    install-shortcut.command \
    dses-workbench.desktop \
    environment.yml ; do
    copy_if_present "$f" "$stage/"
done
copy_if_present icons/dses_workbench.ico "$stage/icons/"
copy_if_present icons/dses_workbench.png "$stage/icons/"
copy_if_present icons/dses_workbench.icns "$stage/icons/"

# Pre-built SoapySDRPlay3 module for Windows (SDRplay support). Not on
# conda-forge, so we ship it; install guide §1A says where to copy it.
mkdir -p "$stage/sdrplay"
copy_if_present vendor/windows/sdrPlaySupport.dll "$stage/sdrplay/"
copy_if_present vendor/windows/README.txt "$stage/sdrplay/"

# Install guide PDF (the DSES-styled PDF is the deliverable; the .docx
# is a developer-side intermediate and stays out of the bundle).
copy_if_present DSES_Radio_Astronomy_Workbench_Installation.pdf "$stage/"

# Default SigMF playback sample (large, ~500+ MB) — lets users without any
# SDR attached launch the program and see live spectrum from a recorded file.
copy_if_present sample.sigmf-data "$stage/"
copy_if_present sample.sigmf-meta "$stage/"

# PRESTO bridge + build recipes (the app's Analyze/Quick-look features shell
# out to these; Help points users at presto/build_presto.sh).
mkdir -p "$stage/presto"
copy_if_present presto/presto_bridge.py "$stage/presto/"
copy_if_present presto/build_presto.sh "$stage/presto/"
copy_if_present presto/build_presto_macos.sh "$stage/presto/"
copy_if_present presto/extend_ut1.sh "$stage/presto/"
copy_if_present presto/README.md "$stage/presto/"
[ -d presto/par ] && cp -R presto/par "$stage/presto/"

# Local-import completeness check (guards against the 1.1.8 incident: the
# app grew module files that never made it into the ship list, and the
# published zip died at startup with ModuleNotFoundError). Fail the build if
# any staged .py does a top-level import of a repo-local module that is not
# itself staged.
fail=0
repo_mods="$(ls *.py | sed 's/\.py$//')"
staged_mods="$(find "$stage" -name '*.py' -exec basename {} .py \;)"
for py in "$stage"/*.py; do
    while read -r mod; do
        [ -z "$mod" ] && continue
        if echo "$repo_mods" | grep -qx "$mod" && ! echo "$staged_mods" | grep -qx "$mod"; then
            echo "ERROR: $(basename "$py") imports '$mod' but $mod.py is not staged" >&2
            fail=1
        fi
    done < <(grep -oE '^[[:space:]]*(import|from)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*' "$py" \
             | awk '{print $2}')
done
if [ "$fail" -ne 0 ]; then
    echo "Staging tree is missing local modules - add them to the ship list." >&2
    exit 1
fi
echo "Local-import completeness check passed."

# Make the Unix launcher + the macOS shortcut installer executable inside the
# bundle so a fresh install can run them (a plain unzip preserves these bits;
# the .command must be +x to run from a Finder double-click).
chmod +x "$stage/launcher.sh"
[ -f "$stage/install-shortcut.command" ] && chmod +x "$stage/install-shortcut.command"

# Zip — use python's zipfile so we don't depend on a system zip binary.
python3 -c "
import os, zipfile
stage='$stage'; archive='$archive'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(stage):
        for name in files:
            full = os.path.join(root, name)
            arc = os.path.relpath(full, '$dist')
            z.write(full, arc)
"

size=$(stat -c %s "$archive" 2>/dev/null || stat -f %z "$archive")

# Emit the .sha256 sidecar with the EXACT name and format the in-app updater
# fetches: updater.sha256_url_for() turns "...-<v>.zip" into "...-<v>.sha256"
# (NOT ".zip.sha256"), and fetch_published_sha256() reads "<hex>  <filename>".
# A hand-named sidecar 404'd the 1.3.0 update on the Mac (2026-08-06).
sidecar="${archive%.zip}.sha256"
hash=$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$archive")
printf '%s  %s\n' "$hash" "$(basename "$archive")" > "$sidecar"

echo ""
echo "Wrote $archive ($((size / 1024)) KB)"
echo "Wrote $sidecar (updater-convention name + format)"
echo "Staging tree retained at $stage"
