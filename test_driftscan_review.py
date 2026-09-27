"""Offscreen tests for driftscan_review.py: loader (both dialects, ref
rows, mid-file el change), integrity/gap/spur detection, synthetic transit
recovery, header-sanity notes, and a dialog smoke test.
Run: .conda/python.exe test_driftscan_review.py  (offscreen Qt, no radio).
"""
import datetime as dt
import math
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np

import driftscan_review as dr

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


def _write_synthetic(path, dialect, rows=400, chans=256, cadence=30,
                     transit_row=200, transit_amp=0.30, sigma_rows=8,
                     gap_after=None, spur_ch=60, el_change_at=None):
    """Synthetic ezRA file with a Gaussian continuum transit and one spur."""
    fmin, fmax = 1419.61, 1421.21
    t0 = dt.datetime(2026, 9, 20, 1, 0, 0)
    rng = np.random.default_rng(7)
    with open(path, "w", newline="\n") as f:
        if dialect == "workbench-db":
            f.write("from ezCol-compatible DSES_Workbench 1.5.3\n")
        else:
            f.write("from ezCol250313a.py -ezColAzimuth 180.0\n")
        f.write("lat 38.3808 long -103.156 amsl 4400 name TEST\n")
        f.write(f"freqMin {fmin} freqMax {fmax} freqBinQty {chans}\n")
        f.write("azDeg 180 elDeg 65\n")
        f.write("# times are in UTC\n# gain 47\n")
        if dialect == "workbench-db":
            f.write("# frequency spectrums of RMS power in dB\n")
        else:
            f.write("# frequency spectrums of RMS power = "
                    "sqrt(mean of sum of squares)\n")
        t = t0
        for i in range(rows):
            if gap_after is not None and i == gap_after:
                t += dt.timedelta(seconds=cadence * 5)
            if el_change_at is not None and i == el_change_at:
                f.write("azDeg 180 elDeg 75\n")
            scale = 1.0 + transit_amp * math.exp(
                -0.5 * ((i - transit_row) / sigma_rows) ** 2)
            band = np.ones(chans) * scale
            band[spur_ch] *= 1.6                      # narrow spur
            band *= 1.0 + rng.normal(0, 0.002, chans)  # noise
            if dialect == "workbench-db":
                vals = 10 * np.log10(band * 0.09)      # ~ -10 dB level
                f.write(t.strftime("%Y-%m-%dT%H:%M:%S") + " "
                        + " ".join(f"{x:.4f}" for x in vals) + "\n")
            else:
                amps = np.sqrt(band) * 5.0
                f.write(t.strftime("%Y-%m-%dT%H:%M:%S") + " "
                        + " ".join(f"{x:.5f}" for x in amps) + "\n")
                # interleave a reference row (flagged R), ezCol style
                f.write(t.strftime("%Y-%m-%dT%H:%M:%S") + " "
                        + " ".join(f"{x:.5f}" for x in amps) + " R\n")
            t += dt.timedelta(seconds=cadence)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="dsr_test_")

    # ---- Workbench-dB dialect --------------------------------------------
    p1 = _write_synthetic(os.path.join(tmp, "wb.txt"), "workbench-db",
                          gap_after=100)
    d1 = dr.load_ezra(p1)
    check("wb dialect", d1.dialect == "workbench-db")
    check("wb rows", d1.power.shape == (400, 256))
    check("wb ref rows none", d1.ref_rows == 0)
    check("wb dec constant",
          abs(np.median(d1.dec) - (65 - (90 - 38.3808))) < 0.1)
    r1 = dr.review(d1, site={"lat_deg": 38.3808, "lon_deg": -103.156},
                   current_azel=(180.0, 65.0))
    check("wb cadence", abs(r1.cadence_s - 30.0) < 0.1)
    check("wb gap found", len(r1.gaps) == 1 and 170 < r1.gaps[0][1] < 190)
    check("wb spur found",
          any(abs(f - d1.freqs[60]) < 0.01 for f, _db, _w, _k in r1.spurs))
    check("wb transit found", r1.fit is not None)
    if r1.fit:
        check("wb transit peak ~30%",
              abs(r1.fit["peak"] - 30.0) < 3.0)
        # center: injected at row 200; timestamp there minus tau/2 = 15 s
        expected = d1.times[200] - dt.timedelta(seconds=15)
        check("wb transit center",
              abs((r1.fit["utc_dt"] - expected).total_seconds()) < 45)
        check("wb lc kind continuum", r1.lc_kind == "total power")
    check("wb no stale note",
          not any("stale" in n for n in r1.notes))
    r1b = dr.review(d1, current_azel=(0.0, 87.0))
    check("wb stale note fires",
          any("stale" in n for n in r1b.notes))
    r1c = dr.review(d1, site={"lat_deg": 38.885, "lon_deg": -104.5})
    check("wb site-mismatch note",
          any("site settings" in n for n in r1c.notes))
    check("wb summary text", "Drift-Scan Review" in r1.summary_text())

    # ---- ezCol-linear dialect --------------------------------------------
    p2 = _write_synthetic(os.path.join(tmp, "ez.txt"), "ezcol-linear",
                          el_change_at=300)
    d2 = dr.load_ezra(p2)
    check("ez dialect", d2.dialect == "ezcol-linear")
    check("ez ref rows skipped", d2.ref_rows == 400)
    check("ez antenna rows", d2.power.shape[0] == 400)
    decs = sorted(set(np.round(d2.dec, 1)))
    check("ez mid-file el change -> two decs", len(decs) == 2)
    r2 = dr.review(d2)
    check("ez transit found", r2.fit is not None)
    if r2.fit:
        check("ez transit peak ~30%", abs(r2.fit["peak"] - 30.0) < 3.0)

    # ---- HI-out-of-band note ---------------------------------------------
    p3 = os.path.join(tmp, "oob.txt")
    src = open(p1).read().replace("freqMin 1419.61 freqMax 1421.21",
                                  "freqMin 1421.2 freqMax 1422.8")
    open(p3, "w", newline="\n").write(src)
    d3 = dr.load_ezra(p3)
    r3 = dr.review(d3)
    check("HI out-of-band note",
          any("OUTSIDE" in n for n in r3.notes))

    # ---- trend ------------------------------------------------------------
    results = []
    for day, amp in ((20, 0.30), (21, 0.31), (22, 0.32), (23, 0.33)):
        p = _write_synthetic(os.path.join(tmp, f"t{day}.txt"),
                             "workbench-db", transit_amp=amp)
        # shift dates
        s = open(p).read().replace("2026-09-20", f"2026-09-{day}")
        open(p, "w", newline="\n").write(s)
        results.append(dr.review(dr.load_ezra(p)))
    tr = dr.trend(results)
    check("trend computed", tr is not None)
    if tr:
        _dates, _peaks, _errs, slope, _serr = tr
        check("trend slope ~ +1 %/day", 0.5 < slope < 1.5)

    # ---- dialog smoke (offscreen) -----------------------------------------
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    dlg = dr.make_dialog(site={"lat_deg": 38.3808, "lon_deg": -103.156},
                         current_azel=(180.0, 65.0), start_dir=tmp)
    dlg.add_path(p1)
    dlg.add_path(p2)
    dlg._run_review()
    check("dialog report populated",
          "Drift-Scan Review" in dlg.summary())
    check("dialog two files reviewed", len(dlg._results) == 2)
    out = os.path.join(tmp, "report.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(dlg.summary())
    check("dialog summary saved", os.path.getsize(out) > 200)
    dlg.close()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
