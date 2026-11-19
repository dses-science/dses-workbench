"""Hydrogen-line simulator bench: the E4438C makes a weak, broad hump (FM by
its internal noise source) and the REAL Workbench window, on the attached
radio, is asked what it draws.

Why (2026-11-19): Ray Uberecken's screenshots showed the hydrogen hump at
+0.47 dB in SDR Console and at less than half that in the Workbench's band
centre, unexplained from pictures. With a controllable hump the display can be
measured instead of read off a screenshot:

  A. Shape: where the hump sits, how wide it is, how strong at a high level.
  B. Amplitude fidelity: the level is stepped down a known ladder; the hump
     height the plot DRAWS (a true RF-off baseline, the hump core against its
     shoulders) is compared with the power arithmetic, at "Ray's" settings
     (65536-point FFT) and in Line view.
  C. Response time: the hump is switched on and the drawn height is timed to
     63% and 90%, in Line view and with Ray's minute-long average.

Instrument rules (C:\\CNS-Systems\\TEST_EQUIPMENT.md): the generator's found
state is read first and restored in a finally (FREQ, POW, OUTP, OUTP:MOD and
the FM settings); the level is capped at --max-dbm; one TCP session, closed
at the end.

Run under radioconda (or the dev env for a B210):
  python tools/siggen_line_test.py --driver uhd_b200 [--out DIR] [--skip-slow]
"""
import argparse, json, os, socket, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ap = argparse.ArgumentParser()
ap.add_argument("--driver", default=None)
ap.add_argument("--host", default="192.168.10.66")
ap.add_argument("--line-mhz", type=float, default=1420.33)
ap.add_argument("--dev-khz", type=float, default=60.0, help="FM deviation of the noise modulation")
ap.add_argument("--max-dbm", type=float, default=-60.0)
ap.add_argument("--out", default=None)
ap.add_argument("--skip-slow", action="store_true", help="skip the 2-minute response run at alpha 0.003")
ap.add_argument("--gain-ladder", action="store_true",
                help="D: the same hump at a ladder of RX gains (does the drawn height depend on gain?)")
ap.add_argument("--only-gain", action="store_true", help="run A and D only")
args = ap.parse_args()
_cfg = tempfile.mkdtemp(prefix="dses_siggen_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from pathlib import Path
from PySide6 import QtWidgets, QtCore
import dses_workbench as A


class E4438C:
    KEYS = (":FREQ", ":POW", ":OUTP", ":OUTP:MOD", ":FM:STAT", ":FM:DEV", ":FM:SOUR",
            ":FM:INT:FUNC:SHAP", ":FM:INT:FREQ")

    def __init__(self, host, max_dbm):
        self.s = socket.create_connection((host, 5025), timeout=6)
        self.s.settimeout(6)
        self.max_dbm = max_dbm
        self.found = {k: self.q(k + "?") for k in self.KEYS}

    def q(self, cmd):
        self.s.sendall((cmd + "\n").encode())
        out = b""
        while not out.endswith(b"\n"):
            out += self.s.recv(4096)
        return out.decode().strip()

    def w(self, cmd):
        self.s.sendall((cmd + "\n").encode())
        err = self.q(":SYST:ERR?")
        if not err.startswith("+0"):
            raise RuntimeError(f"E4438C rejected {cmd!r}: {err}")

    def level(self, dbm):
        if dbm > self.max_dbm:
            raise RuntimeError(f"level {dbm} dBm above the {self.max_dbm} dBm cap")
        self.w(f":POW {dbm:.2f} DBM")

    def rf(self, on):
        self.w(":OUTP " + ("ON" if on else "OFF"))

    def restore(self):
        # modulation first, RF state last, exactly as found
        for k in (":FM:STAT", ":FM:DEV", ":FM:SOUR", ":FM:INT:FUNC:SHAP", ":FM:INT:FREQ",
                  ":OUTP:MOD", ":POW", ":FREQ", ":OUTP"):
            try:
                self.s.sendall((f"{k} {self.found[k]}\n").encode())
            except OSError:
                pass
        try:
            now = {k: self.q(k + "?") for k in self.KEYS}
            self.q(":SYST:ERR?")
        except OSError:
            now = {}
        self.s.close()
        return now


if args.driver:
    _all = A.find_all_radios
    A.find_all_radios = lambda: [d for d in _all() if d["driver"] == args.driver]
(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
for _n in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _n,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
out_dir = args.out or tempfile.mkdtemp(prefix="dses_siggen_out_")
os.makedirs(out_dir, exist_ok=True)

qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
assert not tb._playback_mode, "no live radio found"
tb.resize(1700, 950)
tb.start(); tb.flowgraph_started.set(); tb.show()
fp = tb._fft_plot
F0 = args.line_mhz * 1e6
DEV = args.dev_khz * 1e3


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def drawn():
    x, y = fp._curve.getOriginalDataset()
    return np.asarray(x, float), np.asarray(y, float)


def config(fft, alpha, smooth, trace='average'):
    fp._fft_size_combo.setCurrentIndex(fp._fft_size_combo.findData(fft))
    fp._avg_slider.setValue(int(round(alpha * 1000)))
    fp._smooth_spin.setValue(smooth)
    fp._trace_combo.setCurrentIndex(fp._trace_combo.findData(trace))
    fp._max_check.setChecked(False); fp._min_check.setChecked(False)
    fp.set_frequency_range(tb.center_freq, tb.samp_rate)
    pump(1.0)


def hump_db(x, on, off, core, lo_sh, hi_sh):
    """Drawn hump height: ON minus OFF (dB), the core against the mean of
    the two shoulders (which removes any gain drift between the two)."""
    d = on - np.interp(x, off[0], off[1])
    c = np.abs(x - F0) < core
    sh = ((x > F0 + lo_sh[0]) & (x < F0 + lo_sh[1])) | ((x > F0 + hi_sh[0]) & (x < F0 + hi_sh[1]))
    return float(np.mean(d[c]) - np.mean(d[sh])), d


names = [n for n, _ in A.OBSERVATION_PRESETS]
tb._apply_observation(next(i for i, n in enumerate(names) if n.startswith("Hydrogen")))
pump(2.0)
print(f"radio: {tb._source.display_label}; centre {tb.center_freq/1e6:.3f} MHz, {tb.samp_rate/1e6:g} MS/s, "
      f"LO offset {tb.lo_offset/1e6:g} MHz, RX gain {tb.gain:g} dB; results -> {out_dir}", flush=True)

gen = E4438C(args.host, args.max_dbm)
print("generator found:", {k: v for k, v in gen.found.items()}, flush=True)
res = {"radio": tb._source.display_label, "found": gen.found, "line_mhz": args.line_mhz, "dev_khz": args.dev_khz}
CORE = 0.35 * DEV                       # +-21 kHz at 60 kHz deviation
# Shoulders far from the hump: at a strong level the generator's skirts reach
# hundreds of kHz (first run: shoulders 240-390 kHz out read ~1 dB high at the
# reference level, which made every weaker step look 5-23% too tall).
SH_LO, SH_HI = (-0.85e6, -0.60e6), (0.60e6, 0.95e6)
try:
    gen.rf(False)
    gen.w(f":FREQ {F0:.1f}")
    gen.w(":FM:SOUR INT")
    gen.w(":FM:INT:FUNC:SHAP NOIS")
    gen.w(f":FM:DEV {DEV:.1f}")
    gen.w(":FM:STAT ON")
    gen.w(":OUTP:MOD ON")

    # --- A. shape at a high level; find the level for a +12 dB hump ---------
    config(4096, 0.1, 0)
    pump(4.0); off = drawn()
    L0 = -95.0
    for _ in range(4):
        gen.level(L0); gen.rf(True); pump(4.0)
        x, on = drawn()
        h, d = hump_db(x, on, off, CORE, SH_LO, SH_HI)
        gen.rf(False)
        if 9.0 <= h <= 16.0:
            break
        L0 = min(args.max_dbm, L0 + (12.0 - h) if h > 0.5 else L0 + 15.0)
        pump(3.0); off = drawn()
    r = 10 ** (d / 10.0) - 1.0
    w = (np.abs(x - F0) < 6 * DEV) & (r > 0)
    cen = float(np.sum(x[w] * r[w]) / np.sum(r[w]))
    half = x[w][r[w] > 0.5 * r[w].max()]
    fwhm = float(half.max() - half.min())
    r0 = 10 ** (h / 10.0) - 1.0
    res["A"] = {"L0_dbm": L0, "hump_db": h, "centroid_mhz": cen / 1e6, "fwhm_khz": fwhm / 1e3}
    print(f"A. shape at {L0:.1f} dBm: hump core +{h:.2f} dB over the floor, centroid {cen/1e6:.4f} MHz "
          f"(set {args.line_mhz:.4f}), FWHM {fwhm/1e3:.0f} kHz with {args.dev_khz:g} kHz noise FM", flush=True)
    tails = {k: float(np.mean(d[np.abs(np.abs(x - F0) - k * 1e3) < 15e3])) for k in (100, 200, 300, 500, 700)}
    res["A"]["skirts_db"] = tails
    print("   skirts (ON-OFF dB at +-offset): " + ", ".join(f"{k} kHz {v:+.2f}" for k, v in tails.items()), flush=True)

    # --- B. amplitude ladder, two display configurations -----------------------
    ladder = [0.0, -5.0, -10.0, -13.0, -16.0, -20.0, -25.0]
    res["B"] = {}
    b_configs = [] if args.only_gain else [
        ("Ray's settings (FFT 65536, Blackman, Average)", (65536, 0.05, 0), 9.0),
        ("Line view (FFT 2048, alpha 0.05, smooth 9)", (2048, 0.05, 9), 7.0)]
    for label, cfg, wait in b_configs:
        if cfg[0] == 65536:
            fp._window_combo.setCurrentText("blackman")
        config(*cfg)
        gen.rf(False); pump(wait); off = drawn()
        # this configuration's own reference at L0 (resolution changes the reading of nothing
        # for a hump 100+ kHz wide, but measure rather than assume)
        gen.rf(True)
        meas = []
        for step in ladder:
            gen.level(L0 + step); pump(wait)
            x, on = drawn()
            meas.append(hump_db(x, on, off, CORE, SH_LO, SH_HI)[0])
        # reference = the -10 dB step (strong enough to read to a percent, weak
        # enough that its skirts are out of the shoulders); every other step is
        # predicted from it by the level difference alone
        REF = -10.0
        h_ref = meas[ladder.index(REF)]
        r_ref = 10 ** (h_ref / 10.0) - 1.0
        rows = [(step, float(10 * np.log10(1.0 + r_ref * 10 ** ((step - REF) / 10.0))), h_m)
                for step, h_m in zip(ladder, meas)]
        gen.rf(False); pump(wait)
        x, on = drawn(); h_zero, _ = hump_db(x, on, off, CORE, SH_LO, SH_HI)
        res["B"][label] = {"rows": rows, "rf_off_residual_db": h_zero}
        print(f"B. {label}: reference +{h_ref:.2f} dB at {L0 + REF:.1f} dBm; RF-off residual {h_zero:+.3f} dB", flush=True)
        print("     level step   predicted   drawn    drawn/predicted", flush=True)
        for step, h_p, h_m in rows:
            print(f"     {step:+6.1f} dB    {h_p:7.3f}   {h_m:7.3f}    {h_m / h_p if h_p > 1e-6 else float('nan'):6.2f}", flush=True)

    # --- C. response time when the hump appears -----------------------------------
    target = (L0 - 25.0) if args.only_gain else next(
        L0 + s for s, hp, _ in res["B"]["Line view (FFT 2048, alpha 0.05, smooth 9)"]["rows"] if hp < 1.3)
    res["C"] = {}
    runs = [] if args.only_gain else [("Line view (alpha 0.05)", (2048, 0.05, 9), 8.0, 14.0)]
    if not args.skip_slow and not args.only_gain:
        runs.append(("Ray's average (FFT 65536, alpha 0.003)", (65536, 0.003, 0), 100.0, 130.0))
    for label, cfg, settle, watch in runs:
        config(*cfg)
        gen.level(target); gen.rf(False); pump(settle); off = drawn()
        gen.rf(True); t0 = time.monotonic(); ts, hs = [], []
        while time.monotonic() - t0 < watch:
            pump(0.25)
            x, on = drawn()
            ts.append(time.monotonic() - t0); hs.append(hump_db(x, on, off, CORE, SH_LO, SH_HI)[0])
        ts, hs = np.array(ts), np.array(hs)
        final = float(np.mean(hs[ts > 0.75 * watch]))
        t63 = float(ts[np.argmax(hs >= 0.632 * final)]); t90 = float(ts[np.argmax(hs >= 0.90 * final)])
        res["C"][label] = {"final_db": final, "t63_s": t63, "t90_s": t90, "t": ts.tolist(), "h": hs.tolist()}
        print(f"C. {label}: hump of +{final:.2f} dB switched on -> 63% drawn after {t63:.1f} s, 90% after {t90:.1f} s", flush=True)
        gen.rf(False)

    # --- D. the same hump at a ladder of RX gains ---------------------------------
    # Low gain raises the receiver's own noise, so the same sky signal is a
    # smaller fraction of what is displayed: the hump is drawn shorter with
    # nothing wrong in the display. (Ray's Workbench and SDR Console set the
    # HackRF's gain stages differently.)
    if args.gain_ladder or args.only_gain:
        g_found = float(tb.gain)
        glo, ghi = tb._source.gain_range[0], tb._source.gain_range[1]
        config(2048, 0.05, 9)
        gen.level(L0 - 16.0)
        rowsD = []
        for g in [v for v in (70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0) if glo <= v <= ghi]:
            tb.set_gain(g); pump(1.5)
            gen.rf(False); pump(6.0); off = drawn()
            floor = float(np.median(off[1]))
            gen.rf(True); pump(6.0)
            x, on = drawn()
            rowsD.append((g, floor, hump_db(x, on, off, CORE, SH_LO, SH_HI)[0]))
        gen.rf(False)
        tb.set_gain(g_found); pump(1.0)
        res["D"] = {"level_dbm": L0 - 16.0, "rows": rowsD, "gain_restored": float(tb.gain)}
        print(f"D. the same hump ({L0 - 16.0:.1f} dBm) at different RX gains (found {g_found:g} dB, restored {tb.gain:g}):", flush=True)
        print("     RX gain   floor on screen   hump drawn", flush=True)
        for g, floor, h in rowsD:
            print(f"     {g:5.0f} dB   {floor:9.2f} dB     {h:6.2f} dB", flush=True)
finally:
    now = gen.restore()
    same = all(abs(float(now.get(k, "nan")) - float(v)) < 1e-6 if k not in (":FM:SOUR", ":FM:INT:FUNC:SHAP")
               else now.get(k) == v for k, v in gen.found.items()) if now else False
    print("generator restored to its found state:", same, now, flush=True)
    res["restored"] = same
    tb.stop(); tb.wait(); tb.close()

json.dump(res, open(os.path.join(out_dir, "siggen_line_test.json"), "w"), indent=1)
try:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2), dpi=130)
    for (label, v), c in zip(res["B"].items(), ("#0369a1", "#b45309")):
        rows = np.array(v["rows"])
        ax[0].plot(rows[:, 1], rows[:, 2], "o-", color=c, label=label)
    lim = max(max(np.array(v["rows"])[:, 1].max() for v in res["B"].values()), 1.0) * 1.05
    ax[0].plot([0.02, lim], [0.02, lim], "k:", lw=1, label="drawn = predicted")
    ax[0].set_xscale("log"); ax[0].set_yscale("log")
    ax[0].set_xlabel("hump height predicted from the level step (dB)"); ax[0].set_ylabel("hump height the plot draws (dB)")
    ax[0].set_title("Amplitude fidelity of the drawn trace", fontsize=10); ax[0].legend(fontsize=7.5); ax[0].grid(alpha=0.3, which="both")
    for (label, v), c in zip(res["C"].items(), ("#b45309", "#0369a1")):
        ax[1].plot(v["t"], np.array(v["h"]) / v["final_db"], color=c, label=f"{label}: 90% in {v['t90_s']:.0f} s")
    ax[1].axhline(0.9, color="k", ls=":", lw=0.8); ax[1].set_xscale("log")
    ax[1].set_xlabel("seconds after the hump is switched on"); ax[1].set_ylabel("fraction of the final height drawn")
    ax[1].set_title("Response of the trace", fontsize=10); ax[1].legend(fontsize=7.5); ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, "siggen_line_test.png"))
    print("figure:", os.path.join(out_dir, "siggen_line_test.png"), flush=True)
except Exception as exc:
    print("no figure:", type(exc).__name__, exc, flush=True)
print("SIGGEN LINE TEST DONE", flush=True)
