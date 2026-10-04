"""Linux desktop-entry test: the shipped dses-workbench.desktop template must
launch from an install path with a space and from one without (the Pi's).

The Desktop Entry Specification requires an Exec argument that contains a
space to be quoted; through 1.6.0 the template's Exec line was unquoted, so a
menu entry made from a path with a space was unusable. Needs GLib's Python
bindings (python3-gi) - run it on Linux, or from Windows through WSL:

    python3 test_desktop_entry.py [dses-workbench.desktop]
    wsl -d Ubuntu -- python3 /mnt/c/.../test_desktop_entry.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        print("test_desktop_entry.py: python3-gi (GLib) not available - skipped")
        return 0
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().with_name("dses-workbench.desktop")
    template = src.read_text(encoding="utf-8")
    root = tempfile.mkdtemp(prefix="dses_desktop_test_")
    fails = []
    try:
        for label, install in (("path with a space", root + "/my install dir"),
                               ("path without a space", root + "/Applications/dses-workbench")):
            os.makedirs(install + "/icons")
            with open(install + "/launcher.sh", "w") as f:
                f.write("#!/bin/bash\necho ran\n")
            os.chmod(install + "/launcher.sh", 0o755)
            path = install + "/t.desktop"
            with open(path, "w", encoding="utf-8") as f:
                f.write(template.replace("__INSTALL_DIR__", install))
            kf = GLib.KeyFile()
            kf.load_from_file(path, GLib.KeyFileFlags.NONE)
            _, argv = GLib.shell_parse_argv(kf.get_string("Desktop Entry", "Exec"))
            try:
                info = Gio.DesktopAppInfo.new_from_filename(path)
            except TypeError:           # GLib returned NULL: the entry is unusable
                info = None
            good = argv == [install + "/launcher.sh"] and info is not None
            print(f"{label:22s} argv={argv}  loads={info is not None}  {'OK' if good else 'BROKEN'}")
            if not good:
                fails.append(f"{label}: the template's Exec line does not resolve to launcher.sh")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    for f in fails:
        print("FAIL:", f)
    print("DESKTOP ENTRY TEST:", "PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
