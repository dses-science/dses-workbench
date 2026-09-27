"""Drift-Scan Review — the morning-after quicklook for ezRA drift-scan
recordings (Observe menu).

Opens one or more ezRA-format .txt files and produces one standard report
per file: header sanity (including a stale-az/el check against the
Recording panel), recording integrity (cadence, gaps, power stability),
spur census, a bandpass-flattened waterfall and velocity profile, and a
transit finder with a Gaussian fit (tau/2 end-stamp correction applied).
With several files loaded it also trends the transit peak by day — the
sag-monitor view used at Haswell.

It reads BOTH dialects of the format (learned from the September 2026
field data):
  * DSES Workbench files — rows are power in dB, one azDeg/elDeg header;
  * ezRA ezCol files — rows are LINEAR RMS amplitude (squared here for
    power), half the rows are reference integrations flagged with a
    trailing letter (skipped), and azDeg/elDeg lines REPEAT mid-file when
    the operator moves the dish (pointing is tracked per row).

Deliberately complementary to ezRA: this is the triage layer that decides
whether a file is worth feeding onward into ezCon — it does not duplicate
ezCon/ezSky/ezGal.
"""

import datetime as dt
import math
import os

import numpy as np

HI_LINE_MHZ = 1420.405751786
C_KMS = 299792.458
KNOWN_SPUR_FAMILY_MHZ = (1420.0005, 1420.0010, 1420.0083)   # seen at Haswell + Ray


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class EzraData:
    """One parsed ezRA drift-scan file (antenna rows only)."""

    def __init__(self):
        self.path = ""
        self.version_line = ""      # first header line, verbatim
        self.dialect = "unknown"    # "workbench-db" | "ezcol-linear"
        self.lat = self.lon = self.amsl = None
        self.name = ""
        self.fmin = self.fmax = None
        self.nbin = 0
        self.gain_text = ""
        self.times = []             # datetime (UTC) per antenna row
        self.power = None           # (rows, chans) linear power, float64
        self.az = None              # per-row az (deg)
        self.el = None              # per-row el (deg)
        self.dec = None             # per-row declination (deg)
        self.ref_rows = 0           # ezCol reference rows skipped
        self.bad_rows = 0           # unparseable rows skipped

    @property
    def freqs(self):
        n = self.power.shape[1]
        step = (self.fmax - self.fmin) / n
        return np.linspace(self.fmin, self.fmax, n, endpoint=False) + step / 2

    @property
    def velocities(self):
        return (HI_LINE_MHZ - self.freqs) / HI_LINE_MHZ * C_KMS


def _dec_of(az_deg, el_deg, lat_deg):
    """Declination of a beam at az/el from latitude; el > 90 folds over the
    zenith toward the opposite azimuth (ezCol's over-the-top convention)."""
    if el_deg > 90.0:
        el_deg = 180.0 - el_deg
        az_deg = (az_deg + 180.0) % 360.0
    az, el, lat = (math.radians(x) for x in (az_deg, el_deg, lat_deg))
    s = (math.sin(lat) * math.sin(el)
         + math.cos(lat) * math.cos(el) * math.cos(az))
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def _is_number(tok):
    try:
        float(tok)
        return True
    except ValueError:
        return False


def load_ezra(path):
    """Parse an ezRA drift-scan .txt (either dialect) into EzraData."""
    d = EzraData()
    d.path = path
    az, el = 180.0, 90.0
    rows, times, azs, els = [], [], [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            if line.startswith("from "):
                d.version_line = line.strip()
            elif line.startswith("lat "):
                p = line.split()
                try:
                    d.lat, d.lon, d.amsl = float(p[1]), float(p[3]), float(p[5])
                    d.name = p[7] if len(p) > 7 else ""
                except (IndexError, ValueError):
                    pass
            elif line.startswith("freqMin"):
                p = line.split()
                d.fmin, d.fmax, d.nbin = float(p[1]), float(p[3]), int(p[5])
            elif line.startswith("azDeg"):
                p = line.split()
                az, el = float(p[1]), float(p[3])
            elif line.startswith("#"):
                low = line.lower()
                if "power in db" in low:
                    d.dialect = "workbench-db"
                elif "rms power" in low or "sum of squares" in low:
                    d.dialect = "ezcol-linear"
                if "gain" in low and not d.gain_text:
                    d.gain_text = line.lstrip("# ").strip()
            elif line[:2] == "20" and "T" in line[:20]:
                p = line.split()
                if len(p) < 10:
                    continue
                if not _is_number(p[-1]):
                    d.ref_rows += 1          # ezCol reference integration
                    continue
                try:
                    row = np.array(p[1:], dtype=np.float64)
                except ValueError:
                    d.bad_rows += 1
                    continue
                try:
                    t = dt.datetime.fromisoformat(p[0])
                except ValueError:
                    d.bad_rows += 1
                    continue
                times.append(t.replace(tzinfo=dt.timezone.utc))
                rows.append(row)
                azs.append(az)
                els.append(el)
    if not rows:
        raise ValueError(f"no data rows found in {os.path.basename(path)}")
    n = min(len(r) for r in rows)
    A = np.vstack([r[:n] for r in rows])
    if d.dialect == "unknown":
        # dB rows are typically negative; linear RMS rows are positive.
        d.dialect = ("workbench-db" if np.median(A) < 0.0 else "ezcol-linear")
    d.power = 10.0 ** (A / 10.0) if d.dialect == "workbench-db" else A ** 2
    d.times = times
    d.az = np.array(azs)
    d.el = np.array(els)
    lat = d.lat if d.lat is not None else 0.0
    d.dec = np.array([_dec_of(a, e, lat) for a, e in zip(azs, els)])
    return d


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _median_filter(x, size):
    """1-D median filter (numpy-only; edges use shrinking windows)."""
    half = size // 2
    out = np.empty_like(x)
    for i in range(len(x)):
        lo, hi = max(0, i - half), min(len(x), i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def _lst_hours(t_utc, lon_deg):
    j2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)
    days = (t_utc - j2000).total_seconds() / 86400.0
    gmst = (18.697374558 + 24.06570982441908 * days) % 24.0
    return (gmst + lon_deg / 15.0) % 24.0


class ReviewResult:
    """Everything the report and the plots need for one file."""

    def __init__(self, data):
        self.data = data
        self.notes = []             # header-sanity findings (strings)
        self.cadence_s = 0.0
        self.cadence_exact = True
        self.gaps = []              # (index, seconds, iso time)
        self.power_pp_db = 0.0
        self.power_rms_db = 0.0
        self.spurs = []             # (freq_mhz, db_above, width_ch, known)
        self.dc_channel = None
        self.flat = None            # (rows, chans) bandpass-flattened, %
        self.profile = None         # averaged velocity profile, %
        self.lightcurve = None      # per-row % (line band or continuum)
        self.lc_kind = ""           # "line band" | "total power"
        self.fit = None             # dict: peak, err, utc, lst, fwhm_min, ...

    def summary_text(self):
        d = self.data
        L = [f"=== Drift-Scan Review: {os.path.basename(d.path)} ==="]
        if d.version_line:
            L.append(d.version_line)
        L.append(f"dialect {d.dialect}; band {d.fmin}-{d.fmax} MHz "
                 f"({d.power.shape[1]} ch); site {d.lat} {d.lon} {d.amsl} "
                 f"'{d.name}'")
        decs = sorted(set(np.round(d.dec, 1)))
        L.append(f"pointing az {d.az[0]:.4g} el {d.el[0]:.4g} -> Dec "
                 + ", ".join(f"{x:+.1f}" for x in decs)
                 + (" (CHANGES MID-FILE)" if len(decs) > 1 else ""))
        for n in self.notes:
            L.append(f"NOTE: {n}")
        L.append(f"rows {d.power.shape[0]}"
                 + (f" (+{d.ref_rows} reference rows skipped)" if d.ref_rows else "")
                 + f", cadence {self.cadence_s:.1f} s"
                 + (" exact" if self.cadence_exact else " (jitter)")
                 + f", gaps {len(self.gaps)}, power p-p "
                 f"{self.power_pp_db:.2f} dB (rms {self.power_rms_db:.3f})")
        for i, secs, when in self.gaps[:5]:
            L.append(f"  gap {secs:.0f} s after {when}")
        if self.spurs:
            L.append("spurs: " + ", ".join(
                f"{f:.4f} MHz +{db:.1f} dB/{w}ch" + (" (known family)" if k else "")
                for f, db, w, k in self.spurs[:8]))
        if self.fit:
            f = self.fit
            L.append(f"transit ({self.lc_kind}): peak {f['peak']:.3f} "
                     f"+- {f['err']:.3f} ({'%' if self.lc_kind == 'line band' else 'of Tsys'}), "
                     f"FWHM {f['fwhm_min']:.1f} min, center {f['utc']} UT "
                     f"= LST {f['lst']:.4f} (tau/2 corrected)")
        else:
            L.append("transit: no significant peak found")
        return "\n".join(L)


def review(data, site=None, current_azel=None):
    """Run the standard review. `site` = dict with lat_deg/lon_deg/amsl
    (the app's settings); `current_azel` = (az, el) from the Recording
    panel for the staleness check. Both optional."""
    r = ReviewResult(data)
    d = data

    # --- header sanity -----------------------------------------------------
    if d.fmin is not None:
        if not (d.fmin <= HI_LINE_MHZ <= d.fmax):
            if abs((d.fmin + d.fmax) / 2 - HI_LINE_MHZ) < 25.0:
                r.notes.append(
                    f"hydrogen line (1420.4058 MHz) is OUTSIDE the recorded "
                    f"band {d.fmin}-{d.fmax} — an HI observation here sees "
                    f"no gas")
    if site:
        try:
            if (abs(d.lat - site["lat_deg"]) > 0.02
                    or abs(d.lon - site["lon_deg"]) > 0.02):
                r.notes.append(
                    f"header site {d.lat}/{d.lon} differs from the app's "
                    f"site settings {site['lat_deg']}/{site['lon_deg']} — "
                    f"sky coordinates (and ezRA products) follow the header")
        except (TypeError, KeyError):
            pass
    if current_azel is not None:
        ca, ce = current_azel
        if abs(d.az[-1] - ca) > 0.05 or abs(d.el[-1] - ce) > 0.05:
            r.notes.append(
                f"header az/el {d.az[-1]:.4g}/{d.el[-1]:.4g} differs from "
                f"the Recording panel's current {ca:.4g}/{ce:.4g} — one of "
                f"them is stale")

    # --- integrity ---------------------------------------------------------
    ts = d.times
    dts = np.array([(ts[i + 1] - ts[i]).total_seconds()
                    for i in range(len(ts) - 1)]) if len(ts) > 1 else np.array([])
    if len(dts):
        r.cadence_s = float(np.median(dts))
        r.cadence_exact = bool(np.percentile(np.abs(dts - r.cadence_s), 95) < 1.5)
        for i in np.where(dts > 3 * r.cadence_s)[0]:
            r.gaps.append((int(i), float(dts[i]), ts[i].strftime("%H:%M:%S")))
    tot = d.power.mean(axis=1)
    tot_db = 10 * np.log10(np.maximum(tot, 1e-300))
    r.power_pp_db = float(tot_db.max() - tot_db.min())
    r.power_rms_db = float(tot_db.std())

    # --- bandpass, spurs, DC ----------------------------------------------
    bp = d.power.mean(axis=0)
    bp_db = 10 * np.log10(np.maximum(bp, 1e-300))
    resid = bp_db - _median_filter(bp_db, 101)
    fr = d.freqs
    hot = np.where(resid > 0.5)[0]
    group = []
    def _flush(g):
        if not g:
            return
        k = g[int(np.argmax(resid[g]))]
        known = any(abs(fr[k] - s) < 0.003 for s in KNOWN_SPUR_FAMILY_MHZ)
        r.spurs.append((float(fr[k]), float(resid[k]), len(g), known))
    for i in hot:
        if group and i - group[-1] <= 2:
            group.append(i)
        else:
            _flush(group)
            group = [i]
    _flush(group)
    # DC artefact = the strongest spur within 3 channels of band center
    center = len(fr) // 2
    for f, db, w, _k in r.spurs:
        if abs(f - (d.fmin + d.fmax) / 2) < 3 * (fr[1] - fr[0]) and db > 5:
            r.dc_channel = int(np.argmin(np.abs(fr - f)))
    spur_ch = np.zeros(len(fr), bool)
    for f, db, w, _k in r.spurs:
        k = int(np.argmin(np.abs(fr - f)))
        spur_ch[max(0, k - w):k + w + 1] = True

    # --- flattened waterfall + profile -------------------------------------
    ref = np.median(d.power, axis=0)
    flat = 100.0 * (d.power / np.maximum(ref, 1e-300) - 1.0)
    flat[:, spur_ch] = np.nan
    flat[:, :4] = np.nan
    flat[:, -4:] = np.nan
    r.flat = flat.astype(np.float32)
    r.profile = np.nanmean(flat, axis=0)

    # --- light curves + transit fit ----------------------------------------
    v = d.velocities
    line_band = (np.abs(v) < 120) & ~spur_ch
    lc_line = np.nanmean(np.where(line_band[None, :], flat, np.nan), axis=1)
    base = np.median(tot)
    lc_cont = 100.0 * (tot / base - 1.0)
    # pick whichever shows the stronger peak against its own scatter
    def _signif(x):
        med = np.median(x)
        mad = np.median(np.abs(x - med)) + 1e-12
        return (np.max(x) - med) / (1.4826 * mad)
    if (not line_band.any() or not np.isfinite(lc_line).any()
            or _signif(lc_cont) >= _signif(lc_line)):
        r.lightcurve, r.lc_kind, lc = lc_cont, "total power", lc_cont
    else:
        r.lightcurve, r.lc_kind, lc = lc_line, "line band", lc_line
    r.fit = _fit_transit(d, lc, r.cadence_s)
    return r


def _fit_transit(d, lc, cadence_s):
    """Gaussian + linear-baseline fit around the strongest peak; tau/2
    end-stamp correction. Returns None when nothing significant."""
    n = len(lc)
    if n < 30 or cadence_s <= 0:
        return None
    # Detrend with a wide running median (~3 h) so a diurnal power slope or
    # slowly-varying line background cannot bias the baseline or the gate.
    lc = np.where(np.isfinite(lc), lc, np.nanmedian(lc))
    wmed = min(n | 1, max(31, int(10800 / cadence_s)) | 1)
    lc_d = lc - _median_filter(lc, wmed)
    k = int(np.nanargmax(lc_d))
    med = np.nanmedian(lc_d)
    mad = np.nanmedian(np.abs(lc_d - med)) + 1e-12
    if (lc_d[k] - med) / (1.4826 * mad) < 5.0:
        return None
    half_w = max(10, int(45 * 60 / cadence_s))          # ~90 min window
    lo, hi = max(0, k - half_w), min(n, k + half_w)
    x = np.arange(lo, hi, dtype=float)
    y = lc_d[lo:hi].astype(float)
    ok = np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 15:
        return None
    # Levenberg-free least squares: joint (center, width) grid + linear
    # solve for amplitude and offset at each grid point.
    sigmas = np.geomspace(1.2, half_w / 2.0, 36)
    mus = np.linspace(k - 6, k + 6, 25)
    best = None
    for sig in sigmas:
        for mu in mus:
            g = np.exp(-0.5 * ((x - mu) / sig) ** 2)
            M = np.column_stack([g, np.ones_like(x)])
            coef, res, *_ = np.linalg.lstsq(M, y, rcond=None)
            rss = (float(res[0]) if len(res)
                   else float(np.sum((M @ coef - y) ** 2)))
            if best is None or rss < best[0]:
                best = (rss, mu, sig, coef)
    rss, mu, sig, coef = best
    amp = float(coef[0])
    dof = max(1, len(x) - 4)
    err = float(np.sqrt(rss / dof))
    if amp <= 0:
        return None
    # center row -> UTC, minus tau/2 (rows are stamped at integration END)
    i0 = int(np.clip(np.floor(mu), 0, n - 1))
    fracr = float(mu - i0)
    t0 = d.times[i0]
    t1 = d.times[min(i0 + 1, n - 1)]
    t_center = t0 + (t1 - t0) * fracr - dt.timedelta(seconds=cadence_s / 2)
    lst = _lst_hours(t_center, d.lon if d.lon is not None else 0.0)
    fwhm_min = 2.3548 * sig * cadence_s / 60.0
    dec0 = float(np.median(d.dec))
    beam = fwhm_min / 60.0 * 15.0 * math.cos(math.radians(dec0)) / 1.0027379
    return {
        "peak": amp if amp < 5 else amp,   # % or fraction-of-Tsys*100
        "err": err,
        "mu_row": float(mu),
        "utc": t_center.strftime("%H:%M:%S"),
        "utc_dt": t_center,
        "lst": float(lst),
        "fwhm_min": float(fwhm_min),
        "beam_deg": float(beam),
    }


def trend(results):
    """Peak-vs-day trend across several reviewed files (the sag monitor).
    Returns (dates, peaks, errs, slope_per_day, slope_err) or None."""
    pts = []
    for r in results:
        if r.fit:
            pts.append((r.fit["utc_dt"].date(), r.fit["peak"], r.fit["err"]))
    if len(pts) < 3:
        return None
    pts.sort()
    d0 = pts[0][0]
    days = np.array([(p[0] - d0).days for p in pts], dtype=float)
    peaks = np.array([p[1] for p in pts])
    scatter = max(np.std(peaks - np.poly1d(
        np.polyfit(days, peaks, 1))(days)), 1e-9)
    w = np.ones_like(peaks) / scatter
    slope, icpt = np.polyfit(days, peaks, 1, w=w)
    span = np.sqrt(np.sum((days - days.mean()) ** 2)) + 1e-12
    return ([p[0] for p in pts], peaks,
            np.full(len(pts), scatter), float(slope), float(scatter / span))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def make_dialog(parent=None, site=None, current_azel=None, start_dir=""):
    """Build the Drift-Scan Review dialog (import-light for the app)."""
    from PySide6 import QtCore, QtWidgets
    import pyqtgraph as pg

    class DriftScanReviewDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Drift-Scan Review")
            self.resize(1000, 640)
            self._results = []
            root = QtWidgets.QHBoxLayout(self)

            left = QtWidgets.QVBoxLayout()
            self._list = QtWidgets.QListWidget()
            self._list.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection)
            left.addWidget(self._list, 1)
            row = QtWidgets.QHBoxLayout()
            add = QtWidgets.QPushButton("Add files…")
            add.clicked.connect(self._add_files)
            rem = QtWidgets.QPushButton("Remove")
            rem.clicked.connect(self._remove_selected)
            row.addWidget(add)
            row.addWidget(rem)
            left.addLayout(row)
            self._run = QtWidgets.QPushButton("Run review")
            self._run.clicked.connect(self._run_review)
            left.addWidget(self._run)
            self._copy = QtWidgets.QPushButton("Copy summary")
            self._copy.clicked.connect(self._copy_summary)
            self._copy.setEnabled(False)
            left.addWidget(self._copy)
            self._save = QtWidgets.QPushButton("Save report…")
            self._save.clicked.connect(self._save_report)
            self._save.setEnabled(False)
            left.addWidget(self._save)
            lw = QtWidgets.QWidget()
            lw.setLayout(left)
            lw.setFixedWidth(280)
            root.addWidget(lw)

            self._tabs = QtWidgets.QTabWidget()
            self._report = QtWidgets.QPlainTextEdit()
            self._report.setReadOnly(True)
            self._report.setStyleSheet("font-family: Consolas, monospace;")
            self._tabs.addTab(self._report, "Report")
            self._wf = pg.PlotWidget(title="Waterfall (bandpass-flattened, %)")
            self._wf_img = pg.ImageItem()
            self._wf.addItem(self._wf_img)
            self._wf.setLabel("bottom", "velocity", units="km/s")
            self._wf.setLabel("left", "row")
            self._tabs.addTab(self._wf, "Waterfall")
            self._prof = pg.PlotWidget(title="Velocity profile (file average)")
            self._prof.setLabel("bottom", "velocity", units="km/s")
            self._prof.setLabel("left", "line/continuum", units="%")
            self._tabs.addTab(self._prof, "Profile")
            self._lc = pg.PlotWidget(title="Transit light curve + fit")
            self._lc.setLabel("bottom", "UT", units="h")
            self._tabs.addTab(self._lc, "Transit")
            self._tr = pg.PlotWidget(title="Peak vs day (sag monitor)")
            self._tr.setLabel("bottom", "day")
            self._tr.setLabel("left", "transit peak")
            self._tabs.addTab(self._tr, "Trend")
            root.addWidget(self._tabs, 1)
            self._file_selector = QtWidgets.QComboBox()
            self._file_selector.currentIndexChanged.connect(self._show_result)
            left.insertWidget(0, self._file_selector)

        # -- file management ------------------------------------------------
        def _add_files(self):
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Add ezRA drift-scan files", start_dir,
                "ezRA drift scan (*.txt);;All files (*)")
            for p in paths:
                if not self._list.findItems(p, QtCore.Qt.MatchExactly):
                    self._list.addItem(p)

        def add_path(self, p):
            self._list.addItem(p)

        def _remove_selected(self):
            for it in self._list.selectedItems():
                self._list.takeItem(self._list.row(it))

        # -- run ------------------------------------------------------------
        def _run_review(self):
            paths = [self._list.item(i).text()
                     for i in range(self._list.count())]
            if not paths:
                return
            self._results = []
            texts = []
            self._file_selector.blockSignals(True)
            self._file_selector.clear()
            errors = []
            for p in paths:
                try:
                    data = load_ezra(p)
                    res = review(data, site=site, current_azel=current_azel)
                    self._results.append(res)
                    texts.append(res.summary_text())
                    self._file_selector.addItem(os.path.basename(p))
                except Exception as exc:            # report, keep going
                    errors.append(f"{os.path.basename(p)}: {exc}")
            tr = trend(self._results)
            if tr:
                dates, peaks, errs, slope, serr = tr
                texts.append(
                    f"=== Trend over {len(dates)} transits: "
                    f"{slope * 1000:+.2f} +- {serr * 1000:.2f} "
                    f"m-units/day ({dates[0]} .. {dates[-1]}) ===")
            if errors:
                texts.append("=== Errors ===\n" + "\n".join(errors))
            self._report.setPlainText("\n\n".join(texts))
            self._file_selector.blockSignals(False)
            if self._results:
                self._file_selector.setCurrentIndex(0)
                self._show_result(0)
                self._copy.setEnabled(True)
                self._save.setEnabled(True)
            self._plot_trend(tr)

        def _show_result(self, idx):
            if not (0 <= idx < len(self._results)):
                return
            r = self._results[idx]
            v = r.data.velocities
            img = np.nan_to_num(r.flat, nan=0.0)
            self._wf_img.setImage(img.T, autoLevels=False,
                                  levels=(-2.0, max(5.0, np.nanmax(img))))
            self._wf_img.setRect(QtCore.QRectF(
                float(v.min()), 0.0, float(v.max() - v.min()), img.shape[0]))
            self._prof.clear()
            self._prof.plot(v, np.nan_to_num(r.profile, nan=0.0),
                            pen=pg.mkPen("#156082", width=2))
            self._lc.clear()
            hrs = np.array([t.hour + t.minute / 60 + t.second / 3600
                            for t in r.data.times])
            self._lc.plot(hrs, r.lightcurve, pen=pg.mkPen("#888888"))
            if r.fit:
                mu = r.fit["mu_row"]
                sig = r.fit["fwhm_min"] * 60 / r.cadence_s / 2.3548
                xx = np.linspace(max(0, mu - 4 * sig),
                                 min(len(hrs) - 1, mu + 4 * sig), 200)
                yy = (r.fit["peak"]
                      * np.exp(-0.5 * ((xx - mu) / sig) ** 2))
                hh = np.interp(xx, np.arange(len(hrs)), hrs)
                self._lc.plot(hh, yy + np.median(r.lightcurve),
                              pen=pg.mkPen("#B86A18", width=2))

        def _plot_trend(self, tr):
            self._tr.clear()
            if not tr:
                return
            dates, peaks, errs, slope, serr = tr
            days = [(x - dates[0]).days for x in dates]
            self._tr.plot(days, peaks, pen=None, symbol="o",
                          symbolBrush="#156082")
            xx = np.array([days[0], days[-1]], dtype=float)
            c = np.polyfit(days, peaks, 1)
            self._tr.plot(xx, np.polyval(c, xx),
                          pen=pg.mkPen("#B86A18", width=2))

        # -- export ---------------------------------------------------------
        def summary(self):
            return self._report.toPlainText()

        def _copy_summary(self):
            QtWidgets.QApplication.clipboard().setText(self.summary())

        def _save_report(self):
            out, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save review summary", start_dir or "",
                "Text (*.txt)")
            if not out:
                return
            with open(out, "w", encoding="utf-8") as f:
                f.write(self.summary() + "\n")
            base = os.path.splitext(out)[0]
            try:
                from pyqtgraph.exporters import ImageExporter
                for name, w in (("waterfall", self._wf),
                                ("profile", self._prof),
                                ("transit", self._lc),
                                ("trend", self._tr)):
                    ImageExporter(w.plotItem).export(f"{base}_{name}.png")
            except Exception:
                pass

    return DriftScanReviewDialog()
