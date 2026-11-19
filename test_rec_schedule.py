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


def test_series():
    """Repeating schedule (2026-11-19): the timing arithmetic. A wrong start
    here records the wrong patch of sky for days with nobody watching."""
    print("\n4. repeating series: intervals, starts, the plan")
    W = A.dses_workbench
    I, S, PL = W._repeat_interval_s, W._series_start, W._series_plan
    sid = I('sidereal')
    check(abs(sid - 86164.0905) < 1e-6 and I('solar') == 86400.0, "sidereal 86164.0905 s, solar 86400 s")
    check(I('custom', "24:50:28") == 89428.0 and I('custom', "1500") == 90000.0, "custom H:MM:SS and plain minutes")
    for bad in ("", "0:00:30", "nonsense"):
        try:
            I('custom', bad); check(False, f"custom {bad!r} rejected")
        except ValueError:
            check(True, f"custom {bad!r} rejected")
    a = utc(2026, 11, 18, 22, 20)                     # Ray's plane crossing, 22:20 UT
    check(S(a, 'sidereal', sid, 0) == a, "recording 0 starts at the anchor")
    d1 = a + 86400 - S(a, 'sidereal', sid, 1)
    check(abs(d1 - 235.9095) < 1e-3, "next sidereal start is 3 m 55.91 s earlier by the clock", f"{d1:.4f} s")
    d30 = a + 30 * 86400 - S(a, 'sidereal', sid, 30)
    check(abs(d30 - 30 * 235.9095) < 1e-2, "30 days on: 1 h 57 m 57 s earlier, no accumulated error", f"{d30:.2f} s")
    check(S(a, 'solar', 86400.0, 7) == a + 7 * 86400, "solar (UTC): same clock time a week on")
    # local solar day keeps the WALL-CLOCK time whatever the UTC offset does
    loc = time.mktime((2026, 10, 30, 21, 15, 0, 0, 1, -1))
    st5 = time.localtime(S(loc, 'solar', 86400.0, 5, utc=False))
    check((st5.tm_mon, st5.tm_mday, st5.tm_hour, st5.tm_min) == (11, 4, 21, 15),
          "solar (local): 21:15 stays 21:15 across a daylight-saving change",
          time.strftime("%Y-%m-%d %H:%M", st5))
    dur = 3 * 3600.0
    k, st, run = PL(a, 'sidereal', sid, dur, a - 600)
    check((k, st, run) == (0, a, dur), "before the first start: wait for it, full length")
    k, st, run = PL(a, 'sidereal', sid, dur, a + 1800)
    check(k == 0 and st == a + 1800 and abs(run - (dur - 1800)) < 1e-6,
          "woken 30 min into a window: start now, end on schedule", f"run {run:.0f} s")
    k, st, run = PL(a, 'sidereal', sid, dur, a + dur - 20)
    check(k == 1 and st == a + sid and run == dur, "20 s left in a window is not worth a file: the next one")
    k, st, run = PL(a, 'sidereal', sid, dur, a + dur + 5)
    check(k == 1 and st == a + sid, "between recordings: the next start")
    # a restart ten days later with the original anchor re-entered. Start 10
    # fell 39 m 19 s before the tenth clock day, and its window is 3 h long.
    now = a + 10 * 86400 + 4 * 3600                   # after window 10 has closed
    k, st, run = PL(a, 'sidereal', sid, dur, now)
    check(k == 11 and abs(st - (a + 11 * sid)) < 1e-6 and run == dur and st > now,
          "resume from the original anchor: the next sidereal start, same phase", f"k={k}")
    now = a + 10 * 86400 + 3600                       # still inside window 10
    k, st, run = PL(a, 'sidereal', sid, dur, now)
    check(k == 10 and st == now and abs(run - (a + 10 * sid + dur - now)) < 1e-6,
          "resume inside a window: record the rest of it", f"run {run:.0f} s")
    k, st, run = PL(a, 'sidereal', sid, 2.0, a + 100.0)
    check(k == 1, "a 2 s test recording: the half-length rule, not the 60 s one")


if __name__ == "__main__":
    print("RECORDING SCHEDULE TESTS")
    test_parser()
    test_parser_local()
    test_helpers()
    test_series()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
        sys.exit(1)
    print("ALL RECORDING-SCHEDULE TESTS PASSED")
