"""Windows launcher test: launcher.ps1 with stand-in programs (no radio, no GUI).

Born of the 1.6.0 fault found 2026-10-04: the launcher passed the program path
to Python unquoted (Start-Process joins -ArgumentList with spaces and does not
quote), so from any folder whose path contained a space Python got the path cut
off at the space, failed, and its minimized console closed unread - nothing
appeared to happen. Run this before every release cut on Windows:

    .conda\\python.exe test_launcher.py [launcher.ps1]

It needs what the launcher needs (a Radioconda the launcher can find, with
PySide6/pyqtgraph/scipy) plus pywin32 for the dialog check. Cases:

  A  folder WITH SPACES, program runs 14 s and exits 0, extra argument with a
     space -> the program receives its full path and the argument intact and
     runs in its own folder; the launcher returns 0 after its 10 s watch; no
     dialog.
  B  folder with spaces, program exits 3 at once -> a "DSES Radio Astronomy
     Workbench" message box naming exit code 3 and the two diagnostic lines;
     the launcher returns 1.
  C  program exits 0 at once (a cancelled startup dialog) -> no dialog, the
     launcher returns 0 promptly.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TITLE = "DSES Radio Astronomy Workbench"
PROG = {
    "A": "import os, sys, time\n"
         "here = os.path.dirname(os.path.abspath(__file__))\n"
         "open(os.path.join(here, 'started.txt'), 'w').write(repr(sys.argv) + '\\n' + os.getcwd())\n"
         "time.sleep(14)\n",
    "B": "import sys\nsys.exit(3)\n",
    "C": "import sys\nsys.exit(0)\n",
}
DIRS = {"A": "mock ok space", "B": "mock fail space", "C": "mock_quick0"}


def main():
    if sys.platform != "win32":
        print("test_launcher.py: Windows only - skipped")
        return 0
    import win32con
    import win32gui

    launcher_src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().with_name("launcher.ps1")
    scratch = Path(tempfile.mkdtemp(prefix="dses_launcher_test_"))
    fails = []

    def setup(case):
        d = scratch / DIRS[case]
        d.mkdir(parents=True)
        shutil.copy(launcher_src, d / "launcher.ps1")
        (d / "dses_workbench.py").write_text(PROG[case])
        return d

    def boxes():
        out = []

        def cb(h, acc):
            if (win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == TITLE
                    and win32gui.GetClassName(h) == "#32770"):
                acc.append(h)
        win32gui.EnumWindows(cb, out)
        return out

    def box_text(h):
        parts = []
        win32gui.EnumChildWindows(h, lambda c, a: a.append(win32gui.GetWindowText(c)), parts)
        return "\n".join(parts)

    def run(case, extra=()):
        d = setup(case)
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(d / "launcher.ps1"), *extra]
        t0 = time.time()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        seen = ""
        while p.poll() is None and time.time() - t0 < 40:
            for h in boxes():
                seen = box_text(h)
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.5)
        if p.poll() is None:
            p.kill()
            fails.append(f"{case}: launcher did not return")
        return d, p.returncode, time.time() - t0, seen, p.stdout.read()

    try:
        d, rc, dt, seen, out = run("A", extra=["extra arg"])
        st = d / "started.txt"
        got = st.read_text() if st.exists() else ""
        print(f"A  rc={rc}  {dt:.1f} s  dialog={'yes' if seen else 'no'}  program saw: ...{got.replace(chr(10), ' | cwd: ')[-90:]}")
        if "mock ok space\\\\dses_workbench.py'" not in got:
            fails.append("A: program did not receive its full path (space in the folder name)")
        if "'extra arg'" not in got:
            fails.append("A: extra argument with a space was split")
        if not got.rstrip().endswith("mock ok space"):
            fails.append("A: working directory is not the program folder")
        if rc != 0 or seen:
            fails.append(f"A: expected rc 0 and no dialog (rc {rc}, dialog {bool(seen)})")
        if not 9.0 <= dt <= 18.0:
            fails.append(f"A: launcher watch time {dt:.1f} s, expected about 10 s")

        d, rc, dt, seen, out = run("B")
        print(f"B  rc={rc}  {dt:.1f} s  dialog={'yes' if seen else 'no'}")
        if "exit code 3" not in seen:
            fails.append("B: message box missing or without the exit code")
        if 'cd /d "' not in seen or "mock fail space" not in seen or "dses_workbench.py" not in seen:
            fails.append("B: message box lacks the two diagnostic lines")
        if rc != 1:
            fails.append(f"B: launcher rc {rc}, expected 1")

        d, rc, dt, seen, out = run("C")
        print(f"C  rc={rc}  {dt:.1f} s  dialog={'yes' if seen else 'no'}")
        if rc != 0 or seen:
            fails.append(f"C: expected rc 0 and no dialog (rc {rc}, dialog {bool(seen)})")
        if dt > 8:
            fails.append(f"C: launcher lingered {dt:.1f} s after a clean exit")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    for f in fails:
        print("FAIL:", f)
    print("LAUNCHER TEST:", "PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
