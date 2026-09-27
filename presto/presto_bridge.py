"""presto_bridge.py — call PRESTO / TEMPO / TEMPO2 (installed in WSL Ubuntu)
from Windows Python, e.g. the DSES Radio Astronomy Workbench's radioconda interpreter.

The Linux side is set up by build_presto.sh, which writes ~/.presto_env
(activating the PRESTO venv and exporting PRESTO/TEMPO/TEMPO2). This module
shells out to wsl.exe, sources that env, translates Windows paths to /mnt/...,
shell-quotes arguments, and returns the captured tool output. Pure stdlib — no
dependency on the app, so it can be imported anywhere or run standalone.

Quick self-test (from a Windows shell / the radioconda python):
    python presto_bridge.py                       # environment report
    python presto_bridge.py "C:\\Users\\rick\\Documents\\DSES_SA_Recordings\\X.fil"
"""
from __future__ import annotations

import os
import time
import shlex
import subprocess
import sys

WSL_EXE = r"C:\Windows\System32\wsl.exe"
# Distro that build_presto.sh was run in (override with DSES_WSL_DISTRO).
DEFAULT_DISTRO = os.environ.get("DSES_WSL_DISTRO", "Ubuntu")
# Sourced first in every command so PRESTO/TEMPO/TEMPO2 resolve (written by
# build_presto.sh). Silently ignored if it doesn't exist yet.
_ENV_SETUP = 'source "$HOME/.presto_env" 2>/dev/null'


class PrestoUnavailable(RuntimeError):
    """Raised when WSL or the target distro can't be reached."""


def _wsl() -> str:
    return WSL_EXE if os.path.isfile(WSL_EXE) else "wsl.exe"


def _env() -> dict:
    # WSL_UTF8=1 makes wsl.exe emit UTF-8 rather than UTF-16, so text decoding
    # is clean.
    return {**os.environ, "WSL_UTF8": "1"}


def to_wsl_path(win_path) -> str:
    r"""C:\Users\rick\x.fil  ->  /mnt/c/Users/rick/x.fil for absolute drive
    paths; otherwise just flip backslashes to forward slashes."""
    p = str(win_path)
    if len(p) >= 2 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def _maybe_path(arg) -> str:
    r"""Translate an argument that looks like an absolute Windows path
    (C:\... or C:/...); pass everything else through untouched."""
    s = str(arg)
    if len(s) >= 3 and s[1] == ":" and s[2] in "\\/":
        return to_wsl_path(s)
    return s


# Why the last wsl_available() said no (for the message the user sees).
LAST_ERROR = ""


def wsl_available(distro: str = DEFAULT_DISTRO, attempts: int = 3,
                  wait_s: float = 5.0) -> bool:
    """True if wsl.exe launches and the named distro is reachable.

    Patient on purpose (2026-09-27): after WSL has idled out, the first call
    boots its VM and the distro, which can outlast one probe on a busy
    machine — the automatic analysis after a recording once reported
    "PRESTO not found" and then found it 12 s later. So: up to `attempts`
    probes of 45 s with a short pause between, and the last failure text is
    kept in LAST_ERROR. Callers run this on a worker thread."""
    global LAST_ERROR
    last = ""
    for i in range(max(1, attempts)):
        try:
            r = subprocess.run([_wsl(), "-d", distro, "--", "true"],
                               capture_output=True, text=True, timeout=45,
                               encoding="utf-8", errors="replace", env=_env())
            if r.returncode == 0:
                LAST_ERROR = ""
                return True
            last = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        except subprocess.TimeoutExpired:
            last = "no answer from wsl.exe within 45 s"
        except Exception as exc:                    # wsl.exe missing, etc.
            last = f"{type(exc).__name__}: {exc}"
        if i + 1 < attempts:
            time.sleep(wait_s)
    LAST_ERROR = last
    return False


def _exec(inner_cmd: str, distro: str = DEFAULT_DISTRO, cwd_win=None,
          timeout=None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a raw shell command inside the distro's login bash, after sourcing
    ~/.presto_env (and optionally cd-ing into a translated Windows dir)."""
    if cwd_win:
        inner_cmd = "cd " + shlex.quote(to_wsl_path(cwd_win)) + " && " + inner_cmd
    full = f"{_ENV_SETUP}; {inner_cmd}"
    cmd = [_wsl(), "-d", distro, "--", "bash", "-lc", full]
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, env=_env(), check=check)
    except FileNotFoundError as exc:
        raise PrestoUnavailable("wsl.exe not found — is WSL installed?") from exc


def run(tool: str, *args, distro: str = DEFAULT_DISTRO, cwd_win=None,
        timeout=None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a PRESTO/TEMPO/TEMPO2 tool in WSL. Args that look like absolute
    Windows paths are auto-translated to /mnt/... ; all args are shell-quoted.
    Returns the CompletedProcess (stdout/stderr captured as text).

        cp = run("readfile", r"C:\\Users\\rick\\Documents\\DSES_SA_Recordings\\X.fil")
        print(cp.stdout)
    """
    parts = [tool] + [_maybe_path(a) for a in args]
    inner = " ".join(shlex.quote(p) for p in parts)
    return _exec(inner, distro=distro, cwd_win=cwd_win, timeout=timeout, check=check)


# --- convenience wrappers (thin; use run() directly for anything else) -----
def readfile(fil, **kw):            return run("readfile", fil, **kw)
def prepfold(fil, *opts, **kw):     return run("prepfold", *opts, fil, **kw)
def accelsearch(dat, *opts, **kw):  return run("accelsearch", *opts, dat, **kw)
def rfifind(fil, *opts, **kw):      return run("rfifind", *opts, fil, **kw)
def prepdata(fil, *opts, **kw):     return run("prepdata", *opts, fil, **kw)
def tempo(*args, **kw):             return run("tempo", *args, **kw)
def tempo2(*args, **kw):            return run("tempo2", *args, **kw)


def environment_report(distro: str = DEFAULT_DISTRO) -> str:
    """Confirm the toolchain resolves inside the distro; handy diagnostics."""
    if not wsl_available(distro):
        return (f"WSL distro {distro!r} not available.\n"
                "Enable WSL (admin: `wsl --install`, reboot), then run "
                "build_presto.sh inside it.")
    r = _exec("echo \"distro: $(lsb_release -ds 2>/dev/null || uname -a)\"; "
              "echo 'tools:'; command -v readfile prepfold accelsearch rfifind "
              "tempo tempo2 2>&1; "
              "echo \"PRESTO=$PRESTO\"; echo \"TEMPO=$TEMPO\"; echo \"TEMPO2=$TEMPO2\"",
              distro=distro)
    out = r.stdout or ""
    if r.stderr.strip():
        out += "\n[stderr]\n" + r.stderr
    return out.rstrip()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cp = readfile(sys.argv[1])
        sys.stdout.write(cp.stdout)
        if cp.stderr.strip():
            sys.stderr.write("\n[stderr]\n" + cp.stderr)
        sys.exit(cp.returncode)
    print(environment_report())
