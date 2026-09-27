#!/usr/bin/env python3
"""
fold_analysis.py -- the DSES canned PRESTO pipeline ("is the recording good?").

One code path serves three app features (v1.1.9 roadmap items):
  * end-of-recording automatic post-processing ("Analyze when done"),
  * the mid-recording quick look (fold a snapshot WITHOUT touching the
    still-growing .fil),
  * self-contained result PDFs (chart + commands + numbers + verdict via
    fold_pdf.py, per the CLAUDE.md fold-PDF convention).

The pipeline is the sequence hand-run for the Haswell validation and refined
by Dan Layne's review: readfile sanity -> rfifind mask -> band-edge zap ->
prepfold against the pulsar catalog -> parse -> verdict. PRESTO runs natively
where installed (Mac/Linux) or through WSL on Windows (presto_bridge). All
functions are Qt-free so the pipeline is testable headless; the app wraps it
in a worker thread.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sigproc_fil import read_sigproc_header, radec_from_name

# Verdict thresholds on prepfold's reduced chi-squared (flat profile = 1).
# Calibrated on the Haswell recordings: B0329+54 gave 8.5-16.8 across folds,
# B0950+08 (real but weak) 3.4-3.9. Below ~1.5 nothing periodic is present.
CHI2_DETECTION = 5.0
CHI2_CANDIDATE = 1.8
EDGE_ZAP_FRACTION = 0.05          # zap this fraction of channels at EACH edge
QUICKLOOK_MIN_SECONDS = 60.0      # need at least this much data to bother


class PrestoUnavailable(RuntimeError):
    pass


def _conda_roots():
    """Best guesses at conda/mamba install roots on this machine (dirs that
    usually contain `bin/` and `envs/`). Most-specific first; deduped; only
    existing dirs returned."""
    roots, seen = [], set()

    def add(p):
        if not p:
            return
        p = Path(p)
        if str(p) not in seen:
            seen.add(str(p))
            roots.append(p)

    # The interpreter / activated env we run under. base == the env itself;
    # an activated env sits at <root>/envs/<name>, so climb two to the root.
    cp = os.environ.get("CONDA_PREFIX")
    if cp:
        cp = Path(cp)
        add(cp.parent.parent if cp.parent.name == "envs" else cp)
    if os.environ.get("CONDA_EXE"):
        add(Path(os.environ["CONDA_EXE"]).parent.parent)
    sp = Path(sys.prefix)
    add(sp.parent.parent if sp.parent.name == "envs" else sp)

    # Common install roots by name, under $HOME and a few system prefixes
    # (radioconda first — that is the DSES SDR/pulsar stack; /home/dses is the
    # Linux drift-scan box).
    for base in (Path.home(), Path("/opt"), Path("/usr/local"),
                 Path("/home/dses")):
        for name in ("radioconda", "miniforge3", "mambaforge", "miniconda3",
                     "anaconda3", "miniconda", "anaconda"):
            add(base / name)
    return [r for r in roots if r.is_dir()]


def _candidate_bin_dirs():
    """Directories that might hold PRESTO / TEMPO2 binaries, best-first:
    $PRESTO/bin, every conda env's bin (envs named '*presto*' preferred, so a
    real PRESTO wins over a stray same-named script), then the usual native
    locations (Homebrew, MacPorts, /usr/local, a source build in $HOME)."""
    dirs, seen = [], set()

    def add(d):
        if not d:
            return
        d = Path(d)
        if str(d) not in seen:
            seen.add(str(d))
            dirs.append(d)

    if os.environ.get("PRESTO"):
        add(Path(os.environ["PRESTO"]) / "bin")
    for root in _conda_roots():
        envs = root / "envs"
        if envs.is_dir():
            env_dirs = [e for e in envs.iterdir() if e.is_dir()]
            # PRESTO-named envs first, newest-looking name first within that
            # group (so 'presto6' beats 'presto' — v6's tempo2 polyco path is
            # the one we wire up); everything else after, in name order.
            presto = sorted((e for e in env_dirs
                             if "presto" in e.name.lower()),
                            key=lambda e: e.name, reverse=True)
            other = sorted((e for e in env_dirs
                            if "presto" not in e.name.lower()),
                           key=lambda e: e.name)
            for e in presto + other:
                add(e / "bin")
        add(root / "bin")
    home = Path.home()
    for d in ("/opt/homebrew/bin", "/opt/local/bin", "/usr/local/bin",
              "/usr/bin", home / "presto" / "bin", home / "presto-v6" / "bin"):
        add(d)
    return dirs


def _which_aggressive(exe):
    """shutil.which, then a sweep of _candidate_bin_dirs — so a tool installed
    in a conda env we are NOT running from is still found. Full path or None."""
    p = shutil.which(exe)
    if p:
        return p
    name = (exe + ".exe") if os.name == "nt" else exe
    for d in _candidate_bin_dirs():
        cand = d / name
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return None


def _native_env(bindir):
    """Env for running discovered PRESTO tools from `bindir` WITHOUT activating
    its conda env: put `bindir` first on PATH (so readfile/rfifind/gs siblings
    resolve), set PGPLOT_DIR to the env's pgplot data (prepfold's plotting),
    and — for PRESTO v6, whose -par/-psr polycos come from tempo2 — add a
    discovered tempo2 to PATH and set TEMPO2 to its data dir."""
    env = os.environ.copy()
    bindir = str(bindir)
    prefix = os.path.dirname(bindir)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    pg = os.path.join(prefix, "share", "pgplot")
    if os.path.isdir(pg) and not env.get("PGPLOT_DIR"):
        env["PGPLOT_DIR"] = pg
    t2 = _which_aggressive("tempo2")
    if t2:
        t2bin = os.path.dirname(t2)
        env["PATH"] = t2bin + os.pathsep + env["PATH"]
        if not env.get("TEMPO2"):
            data = os.path.join(os.path.dirname(t2bin), "share", "tempo2")
            if os.path.isdir(data):
                env["TEMPO2"] = data
    return env


class PrestoRunner:
    """Run PRESTO tools natively (Mac/Linux, or Windows if on PATH) or via WSL.

    Discovery is deliberately aggressive: PRESTO usually lives in a dedicated
    conda env (e.g. radioconda's `presto`/`presto6`) whose bin dir is NOT on
    PATH when the app runs from the base env, so a plain
    `shutil.which('prepfold')` misses it. We also probe $PRESTO and every conda
    env before giving up (and, on Windows, fall back to WSL)."""

    def __init__(self):
        self.mode = None
        self._bindir = None
        self._env = None
        prepfold = _which_aggressive("prepfold")
        if prepfold:
            self.mode = "native"
            self._bindir = os.path.dirname(prepfold)
            self._env = _native_env(self._bindir)
        elif sys.platform == "win32":
            try:
                from presto import presto_bridge
            except ImportError:
                sys.path.insert(0, str(Path(__file__).parent / "presto"))
                try:
                    import presto_bridge
                except ImportError:
                    presto_bridge = None
            else:
                presto_bridge = presto_bridge
            self._wsl_state = "no-bridge"
            if presto_bridge is not None:
                if presto_bridge.wsl_available():
                    probe = presto_bridge.run("bash", "-c", "command -v prepfold")
                    if probe.returncode == 0 and probe.stdout.strip():
                        self.mode = "wsl"
                        self._bridge = presto_bridge
                    else:
                        self._wsl_state = "no-prepfold"
                else:
                    self._wsl_state = "unreachable"
                    self._wsl_error = getattr(presto_bridge, "LAST_ERROR", "")

    @property
    def available(self):
        return self.mode is not None

    def describe(self):
        if self.mode == "native":
            return f"native PRESTO ({self._bindir})"
        if self.mode == "wsl":
            return "PRESTO in WSL via presto_bridge"
        if sys.platform == "win32":
            state = getattr(self, "_wsl_state", "no-bridge")
            if state == "unreachable":
                why = getattr(self, "_wsl_error", "") or "no answer"
                return ("PRESTO lives in WSL on Windows, and WSL did not "
                        f"answer ({why}). The first call after WSL has idled "
                        "out can take a minute while it starts, so try the "
                        "analysis again; if it keeps failing, run "
                        "`wsl -d Ubuntu -- true` in a terminal to see why.")
            if state == "no-prepfold":
                return ("WSL answered, but prepfold is not on its PATH — run "
                        "presto/build_presto.sh inside the Ubuntu distro (it "
                        "writes ~/.presto_env, which every call sources).")
        return ("PRESTO not found — looked on PATH, $PRESTO, and every conda "
                "env. Install it (Mac/Linux) or run presto/build_presto.sh "
                "inside WSL (Windows).")

    def run(self, tool, *args, cwd=None, timeout=3600, nice=True):
        """Run a tool low-priority (protects a live recording) with cwd set
        so PRESTO products land in the analysis directory."""
        if self.mode == "native":
            import subprocess
            exe = tool
            if self._bindir:                      # prefer the discovered env's
                name = (tool + ".exe") if os.name == "nt" else tool
                cand = os.path.join(self._bindir, name)
                if os.path.exists(cand):
                    exe = cand
            cmd = [exe] + [str(a) for a in args]
            if nice and sys.platform != "win32":
                cmd = ["nice", "-n", "10"] + cmd
            return subprocess.run(cmd, cwd=cwd, capture_output=True,
                                  text=True, timeout=timeout, env=self._env)
        if self.mode == "wsl":
            parts = (("nice", "-n", "10", tool) if nice else (tool,))
            return self._bridge.run(*parts, *args, cwd_win=cwd,
                                    timeout=timeout)
        raise PrestoUnavailable(self.describe())

    def render_ps_to_png(self, ps_path, png_path, dpi=120):
        """prepfold's plot is PostScript; gs renders it. Under WSL, gs's
        AppArmor profile often can't write under /mnt/*, so render to the
        WSL home dir and copy out."""
        if self.mode == "native":
            r = self.run("gs", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                         f"-r{dpi}", f"-sOutputFile={png_path}", ps_path,
                         nice=False)
            return r.returncode == 0 and os.path.exists(png_path)
        if self.mode == "wsl":
            tmp = "/root/dses_fold_render.png"
            inner = ("gs -dBATCH -dNOPAUSE -sDEVICE=png16m "
                     f"-r{dpi} -sOutputFile={tmp} "
                     + self._bridge.to_wsl_path(str(ps_path))
                     + " >/dev/null 2>&1 && cp " + tmp + " "
                     + self._bridge.to_wsl_path(str(png_path)))
            r = self._bridge._exec(inner)
            return r.returncode == 0 and os.path.exists(png_path)
        raise PrestoUnavailable(self.describe())


_catalog_index = None   # {UPPER_NAME: (period_s, dm)} once loaded; {} if none


def _read_catalog_text(runner):
    """Text of PRESTO's psr_catalog.txt, or None. Native: read the file from
    the discovered env's share/presto (or $PRESTO). WSL: cat it over the
    bridge."""
    cands = []
    if getattr(runner, "_bindir", None):
        cands.append(Path(runner._bindir).parent / "share" / "presto"
                     / "psr_catalog.txt")
    if os.environ.get("PRESTO"):
        for sub in ("share/presto", "lib", "."):
            cands.append(Path(os.environ["PRESTO"]) / sub / "psr_catalog.txt")
    for p in cands:
        try:
            if p.is_file():
                return p.read_text(errors="replace")
        except OSError:
            continue
    if getattr(runner, "mode", None) == "wsl":
        try:
            r = runner._bridge.run(
                "bash", "-lc",
                'cat "$PRESTO/share/presto/psr_catalog.txt" 2>/dev/null || '
                'cat "$(dirname "$(command -v prepfold)")/../share/presto/'
                'psr_catalog.txt" 2>/dev/null')
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:
            pass
    return None


def _build_catalog_index(runner):
    """Parse psr_catalog.txt into {UPPER_NAME: (period_s, dm)} keyed by both the
    B-name and the J-name. Empty dict if the catalog can't be read."""
    text = _read_catalog_text(runner)
    if not text:
        return {}
    idx, header = {}, None
    ni = pj = fi = di = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = raw.split(";")
        if header is None:
            if "NAME" in fields and "F0" in fields and "DM" in fields:
                header = fields
                col = lambda nm: header.index(nm) if nm in header else None
                ni, pj, fi, di = col("NAME"), col("PSRJ"), col("F0"), col("DM")
            continue
        try:
            f0 = float(fields[fi]); dm = float(fields[di])
        except (ValueError, IndexError):
            continue  # units row, missing F0/DM ('*'), or short line
        if f0 <= 0:
            continue
        p_s = 1.0 / f0
        for i in (ni, pj):
            if i is not None and i < len(fields):
                key = fields[i].strip().upper()
                if key and key != "*":
                    idx[key] = (p_s, dm)
    return idx


def catalog_lookup(name, runner=None):
    """(period_s, dm) for a pulsar NAME (B or J designation) from PRESTO's
    psr_catalog.txt, or None if PRESTO/its catalog isn't reachable or the name
    isn't listed. The parsed catalog is cached after the first load."""
    global _catalog_index
    key = (name or "").strip().upper()
    if not key:
        return None
    if _catalog_index is None:
        try:
            runner = runner or PrestoRunner()
            _catalog_index = (_build_catalog_index(runner)
                              if runner.available else {})
        except Exception:
            _catalog_index = {}
    return _catalog_index.get(key)


def snapshot_fil(src, dst):
    """Copy a growing .fil to `dst`, truncated to a whole number of complete
    spectra — always a valid filterbank even while the writer appends
    (whole rows + flush; SIGPROC headers carry no sample count)."""
    hdr = read_sigproc_header(src)
    row = hdr["nchans"] * hdr["nbits"] // 8
    hdr_len = hdr["_header_bytes"]
    size = os.path.getsize(src)
    nrows = (size - hdr_len) // row
    if nrows <= 0:
        raise ValueError("no complete spectra in the file yet")
    total = hdr_len + nrows * row
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        remaining = total
        while remaining > 0:
            buf = fin.read(min(1 << 22, remaining))
            if not buf:
                break
            fout.write(buf)
            remaining -= len(buf)
    return {"rows": nrows, "seconds": nrows * hdr["tsamp"], "header": hdr}


def _nsub_for(nchans, cap=128):
    """Largest divisor of nchans that is <= cap. prepfold refuses to pick
    -nsub itself for awkward channel counts (e.g. 2044 = 2^2*7*73)."""
    best = 1
    d = 1
    while d * d <= nchans:
        if nchans % d == 0:
            for v in (d, nchans // d):
                if v <= cap and v > best:
                    best = v
        d += 1
    return best


def _save_log(workdir, name, cp):
    """Persist a tool's stdout/stderr next to its products so a failed step
    is never opaque."""
    try:
        with open(Path(workdir) / f"{name}.log", "w", encoding="utf-8",
                  errors="replace") as f:
            f.write(f"# exit code: {cp.returncode}\n\n")
            f.write(cp.stdout or "")
            if (cp.stderr or "").strip():
                f.write("\n\n# ---- stderr ----\n" + cp.stderr)
    except OSError:
        pass


def _parse_prepfold_stdout(text):
    out = {}
    # 'inf' happens on overwhelming signals (the lab pulsar simulator drives
    # the off-pulse variance to ~0); float('inf') parses it fine.
    m = re.search(r"Maximum reduced chi-squared found\s*=\s*(inf|[0-9.]+)",
                  text)
    if m:
        out["chi2_red"] = float(m.group(1))
    m = re.search(r"Best DM\s*\(pc cm\^-3\)\s*=\s*([0-9.]+)", text)
    if m:
        out["best_dm"] = float(m.group(1))
    m = re.search(r"Best period\s*\(s\)\s*=\s*([0-9.]+)", text)
    if m:
        out["best_p_s"] = float(m.group(1))
    m = re.search(r"\(DM\s*=\s*([0-9.]+)\)", text)
    if m:
        out["catalog_dm"] = float(m.group(1))
    return out


def _verdict(results, source_name, quick, catalog_fold=False):
    chi2 = results.get("chi2_red")
    if chi2 is None:
        return ("NO FOLD", "The fold step did not complete; see prepfold.log "
                           "in the analysis folder.")
    what = "quick-look fold" if quick else "fold"
    import math
    if math.isinf(chi2):
        if catalog_fold:
            return ("SUSPECT",
                    "The fold significance is OFF-SCALE (infinite reduced "
                    "chi-squared) — real sky pulsars never do this; a test "
                    "signal or overwhelming RFI dominates the recording.")
        return ("DETECTION",
                f"Off-scale periodic signal (reduced chi-squared = infinity: "
                f"the {what} of {source_name} dominates the recording so "
                f"completely that the off-pulse variance is ~zero). For a "
                f"test source this is a pass with flying colors.")
    # RFI guards for catalog folds (the hard-won B0950+08 / bench lessons):
    # a real pulsar detection optimizes NEAR its catalog DM; terrestrial
    # signals (carriers, spurs, test signals) rail to DM ~ 0 no matter how
    # enormous their chi-squared gets.
    best_dm = results.get("best_dm")
    cat = results.get("catalog_dm")
    if catalog_fold and chi2 >= CHI2_CANDIDATE and best_dm is not None:
        if best_dm < 1.0:
            return ("TERRESTRIAL SIGNAL",
                    f"Strong periodicity (reduced chi-squared {chi2:.1f}) "
                    f"but it optimizes to DM ≈ 0 — zero dispersion means a "
                    f"LOCAL signal (RFI, a carrier, a test source), not "
                    f"{source_name}. Inspect the band for interference; this "
                    f"is not a pulsar detection.")
        if cat and abs(best_dm - cat) > max(5.0, 0.5 * cat):
            return ("SUSPECT",
                    f"Periodic signal (reduced chi-squared {chi2:.1f}) but "
                    f"its best DM ({best_dm:.1f}) is far from the catalog "
                    f"value ({cat:.1f}) — treat with suspicion: possible "
                    f"RFI, or a corrupted fold. Manual review recommended.")
    if chi2 >= CHI2_DETECTION:
        return ("DETECTION",
                f"Strong periodic signal: reduced chi-squared {chi2:.2f} "
                f"(flat noise = 1). The {what} of {source_name} looks like a "
                f"solid detection — the recording is good.")
    if chi2 >= CHI2_CANDIDATE:
        return ("CANDIDATE",
                f"Possible signal: reduced chi-squared {chi2:.2f}. Treat as "
                f"a candidate, not a publishable detection — weak pulsars "
                f"(scintillation, short integration) can sit here even in a "
                f"good recording. More integration time is the usual cure.")
    return ("NO DETECTION",
            f"No significant periodicity (reduced chi-squared {chi2:.2f}). "
            f"NOTE: no detection does NOT mean a bad recording — a weak or "
            f"scintillating pulsar can be invisible in a short stretch. The "
            f"readfile/rfifind checks above say whether the DATA is healthy.")


def analyze_fil(fil_path, *, source_name="", runner=None, quick=False,
                fold_p_s=None, fold_dm=None,
                progress=lambda msg: None):
    """Run the canned pipeline on `fil_path`. Returns a result dict; writes
    `<basename>_prepfold.pdf` (or `_quicklook.pdf`) next to the .fil and
    leaves working products in `<basename>_analysis/`."""
    t0 = time.monotonic()
    fil_path = Path(fil_path)
    runner = runner or PrestoRunner()
    if not runner.available:
        raise PrestoUnavailable(runner.describe())

    hdr = read_sigproc_header(fil_path)
    nchans = hdr["nchans"]
    dur_s = None  # readfile reports it; compute locally too
    nrows = (os.path.getsize(fil_path) - hdr["_header_bytes"]) \
        // (nchans * hdr["nbits"] // 8)
    dur_s = nrows * hdr["tsamp"]

    workdir = fil_path.parent / (fil_path.stem + "_analysis")
    workdir.mkdir(exist_ok=True)
    base = fil_path.stem
    cmds = []

    progress("readfile sanity check…")
    r = runner.run("readfile", str(fil_path), cwd=str(workdir), timeout=600)
    _save_log(workdir, "readfile", r)
    cmds.append("readfile " + fil_path.name)
    readfile_ok = (r.returncode == 0
                   and re.search(r"Time per file|Total time",
                                 (r.stdout or "")) is not None)

    progress("rfifind RFI mask…")
    r = runner.run("rfifind", "-time", "2.0", "-o", base, str(fil_path),
                   cwd=str(workdir), timeout=3600)
    _save_log(workdir, "rfifind", r)
    cmds.append(f"rfifind -time 2.0 -o {base} {fil_path.name}")
    mask = workdir / f"{base}_rfifind.mask"
    mask_ok = r.returncode == 0 and mask.exists()
    m = re.search(r"Number of\s+bad\s+intervals:\s*\d+\s*\(\s*([0-9.]+)%\)",
                  (r.stdout or ""))
    masked_pct = float(m.group(1)) if m else None

    # Band-edge zap: outer EDGE_ZAP_FRACTION of channels on each side
    # (anti-alias skirts; PRESTO channel 0 = lowest frequency).
    zap_n = max(1, int(round(nchans * EDGE_ZAP_FRACTION)))
    ignorechan = f"0:{zap_n - 1},{nchans - zap_n}:{nchans - 1}"

    results = {}
    fold_png = None
    # Fold target: a catalog pulsar name (B/J designation) or a manual
    # period/DM override (magnetars, sources without catalog ephemerides,
    # synthetic tests).
    manual = fold_p_s is not None
    is_psr = manual or (bool(source_name)
                        and radec_from_name(source_name) != (0.0, 0.0))
    if is_psr:
        nsub = _nsub_for(nchans)
        if manual:
            source_name = source_name or f"{fold_p_s*1e3:.1f} ms source"
            progress(f"prepfold -topo -p {fold_p_s}…")
            args = ["-noxwin", "-topo", "-p", f"{fold_p_s}",
                    "-dm", f"{fold_dm or 0.0}", "-nsub", str(nsub),
                    "-ignorechan", ignorechan, "-o", base + "_fold"]
        else:
            progress(f"prepfold -psr {source_name}…")
            args = ["-noxwin", "-psr", source_name, "-nsub", str(nsub),
                    "-ignorechan", ignorechan, "-o", base + "_fold"]
        if mask_ok:
            args += ["-mask", str(mask)]
        r = runner.run("prepfold", *args, str(fil_path),
                       cwd=str(workdir), timeout=7200)
        _save_log(workdir, "prepfold", r)
        cmds.append("prepfold " + " ".join(
            a if not str(a).endswith(".mask") else Path(a).name
            for a in args) + " " + fil_path.name)
        results = _parse_prepfold_stdout((r.stdout or "") + (r.stderr or ""))
        # Locate the plot; render the .ps if prepfold's own png failed.
        pngs = sorted(workdir.glob(base + "_fold*.png"))
        if pngs:
            fold_png = pngs[0]
        else:
            pss = sorted(workdir.glob(base + "_fold*.ps"))
            if pss:
                progress("rendering fold plot…")
                cand = workdir / (pss[0].stem + ".png")
                if runner.render_ps_to_png(pss[0], cand):
                    fold_png = cand

    label, verdict_text = _verdict(results, source_name, quick,
                                   catalog_fold=not manual) if is_psr \
        else ("DATA CHECK",
              "No catalog pulsar name on this recording — ran the data-"
              "health checks only (readfile + rfifind). Set a Source name "
              "before recording to get an automatic fold.")

    # ----- build the self-contained PDF --------------------------------
    progress("writing PDF…")
    fmin = hdr["fch1"] + (nchans - 1) * hdr["foff"]
    meta = [
        f"|Source:   {source_name or '(none)'}    File: {fil_path.name}",
        f"|Band:     {fmin:.3f}-{hdr['fch1']:.3f} MHz, {nchans} channels"
        f"    tsamp {hdr['tsamp']*1e6:.1f} us    length {dur_s:.1f} s"
        f" ({nrows} spectra)",
        f"|Start:    MJD {hdr['tstart']:.6f}    telescope_id"
        f" {hdr.get('telescope_id', '?')} (DSES convention: 12 = Haswell)",
    ]
    tbl = ["|Check                Result",
           f"|readfile             {'OK' if readfile_ok else 'FAILED'}",
           f"|rfifind mask         "
           + (f"OK ({masked_pct:.2f}% flagged)" if mask_ok and masked_pct
              is not None else ("OK" if mask_ok else "FAILED")),
           f"|band-edge zap        outer {EDGE_ZAP_FRACTION*100:.0f}% each"
           f" side ({ignorechan})"]
    if results:
        tbl.append(f"|reduced chi-squared  {results.get('chi2_red', '—')}")
        if "best_dm" in results:
            cat = results.get("catalog_dm")
            tbl.append(f"|best DM              {results['best_dm']:.2f}"
                       + (f"   (catalog {cat:.2f})" if cat else ""))
        if "best_p_s" in results:
            tbl.append(f"|best period          {results['best_p_s']*1e3:.4f} ms")
    commentary = "\n".join([
        "## What this is",
        ("Mid-recording QUICK LOOK on a snapshot of the still-growing "
         "recording." if quick else
         "Automatic end-of-recording analysis.") +
        " Generated by the DSES Radio Astronomy Workbench's canned PRESTO pipeline "
        "(readfile sanity, rfifind RFI mask, band-edge zap, catalog fold). "
        f"Analyzed {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC in "
        f"{time.monotonic()-t0:.0f} s.",
        "",
        "## Recording", *meta,
        "",
        "## Commands", *("|" + c for c in cmds),
        "",
        "## Results", *tbl,
        "",
        f"## Verdict: {label}",
        verdict_text,
        "",
        "Working products (mask, .pfd, .bestprof, plots) are in "
        f"{workdir.name}/ beside the recording.",
    ])
    suffix = "_quicklook.pdf" if quick else "_prepfold.pdf"
    pdf_path = fil_path.parent / (base + suffix)
    charts = [(str(fold_png),
               f"{source_name} — canned catalog fold (mask + edge zap)")] \
        if fold_png else []
    import fold_pdf
    fold_pdf.build_pdf(str(pdf_path),
                       f"{base} — {'quick look' if quick else 'analysis'}",
                       commentary, charts)

    return {"verdict": label, "verdict_text": verdict_text,
            "pdf": str(pdf_path), "workdir": str(workdir),
            "chi2_red": results.get("chi2_red"),
            "best_dm": results.get("best_dm"),
            "catalog_dm": results.get("catalog_dm"),
            "best_p_s": results.get("best_p_s"),
            "readfile_ok": readfile_ok, "mask_ok": mask_ok,
            "masked_pct": masked_pct, "duration_s": dur_s,
            "elapsed_s": time.monotonic() - t0}
