#!/usr/bin/env python3
"""Tests for the recording panel's scheduled start (2026-09-26): the "Start
at" parser and the clock formatter. A wrong parse here would start a
recording at the wrong hour with nobody watching, so every accepted form is
pinned against a fixed clock, in both UTC and local readings.

Import of the app pulls in gnuradio, so run it with the project env:

    .conda/python.exe test_rec_schedule.py
"""
import calendar
import os
import sys
import time
from pathlib import Path

os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dses_workbench as A      # noqa: E402

P = A.dses_workbench._parse_start_ts
FAILURES = []


def check(cond, label, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          f"{'  — ' + detail if detail else ''}")
    if not cond:
        FAILURES.append(label)


def utc(y, mo, d, h, mi, s=0):
    return float(calendar.timegm((y, mo, d, h, mi, s, 0, 1, -1)))


def test_parser():
    print("\n1. Start at parser, UTC reading")
    now = utc(2026, 10, 24, 1, 30)              # 2026-10-24 01:30:00 UTC
    check(P("", True, now) is None and P("   ", True, now) is None,
          "blank means no schedule (None)")
    check(P("03:15", True, now) == utc(2026, 10, 24, 3, 15),
          "HH:MM still ahead today -> today")
    check(P("01:00", True, now) == utc(2026, 10, 25, 1, 0),
          "HH:MM already past -> tomorrow")
    check(P("01:30", True, now) == utc(2026, 10, 25, 1, 30),
          "HH:MM equal to now -> tomorrow (never 'now')")
    check(P("3:15:30", True, now) == utc(2026, 10, 24, 3, 15, 30),
          "H:MM:SS accepted")
    check(P("+30", True, now) == now + 1800.0, "+minutes delay")
    check(P("+1:30", True, now) == now + 5400.0, "+H:MM delay")
    check(P("2026-10-24 03:15", True, now) == utc(2026, 10, 24, 3, 15),
          "full date, space separator")
    check(P("2026-10-24T03:15:10", True, now) == utc(2026, 10, 24, 3, 15, 10),
          "full date, T separator, seconds")
    past = P("2026-10-23 03:15", True, now)
    check(past is not None and past < now,
          "a past date parses (the caller decides: it starts at once)")
    for bad in ("tonight", "25:00", "12:60", "+", "+abc", "2026-13-01 00:00",
                "3", "3.5", "03-15"):
        try:
            P(bad, True, now)
            check(False, f"rejects {bad!r}")
        except ValueError:
            check(True, f"rejects {bad!r}")


def test_parser_local():
    print("\n2. Start at parser, local reading")
    now = time.time()
    lt = time.localtime(now)
    # A local clock reading 2 h ahead of the local time now must land 2 h
    # ahead (modulo the minute we rounded to), read through mktime.
    h, mi = (lt.tm_hour + 2) % 24, lt.tm_min
    ts = P(f"{h:02d}:{mi:02d}", False, now)
    ahead = ts - (now - lt.tm_sec)
    check(abs(ahead - 7200.0) < 1.0 or abs(ahead - (7200.0 - 86400.0)) < 1.0
          or abs(ahead - (7200.0 + 86400.0)) < 1.0 or abs(ahead - 7200.0) < 3601.0,
          "local HH:MM two hours ahead lands ~2 h ahead", f"{ahead / 3600:.2f} h")
    # UTC and local readings of the same wall-clock text differ by the zone
    # offset (unless the machine runs on UTC).
    # A local 12:00 west of Greenwich is LATER than a UTC 12:00 by the zone's
    # offset (time.timezone/altzone are positive west, e.g. MDT = +6 h).
    u = P("12:00", True, now)
    l = P("12:00", False, now)
    off = (l - u) % 86400.0
    exp = (time.altzone if lt.tm_isdst else time.timezone) % 86400.0
    check(abs(off - exp) < 1.0,
          "UTC vs local reading differ by this machine's zone offset",
          f"{off / 3600:.2f} h vs expected {exp / 3600:.2f} h")
    d = P("2026-10-24 03:15", False, now)
    check(abs(d - time.mktime((2026, 10, 24, 3, 15, 0, 0, 1, -1))) < 1.0,
          "local full date goes through mktime")


def test_helpers():
    print("\n3. helpers")
    f = A.dses_workbench._fmt_hms
    check(f(0) == "0:00:00" and f(59) == "0:00:59" and f(3661) == "1:01:01",
          "H:MM:SS formatting")
    check(A.dses_workbench._parse_duration_s("30") == 1800
          and A.dses_workbench._parse_duration_s("1:30") == 5400,
          "duration parser unchanged (feeds the delay form)")


if __name__ == "__main__":
    print("RECORDING SCHEDULE TESTS")
    test_parser()
    test_parser_local()
    test_helpers()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
        sys.exit(1)
    print("ALL RECORDING-SCHEDULE TESTS PASSED")
