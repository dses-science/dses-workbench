#!/usr/bin/env python3
"""
ezra_txt.py -- DSES ezRA drift-scan data writer.

Writes integrated frequency-spectrum rows in the ezRA ``.txt`` data-file
format consumed by Ted Cline's ezRA suite (ezCon -> ezPlot/ezSky/ezGal;
https://github.com/tedcline/ezRA), so the Workbench can act as the
drift-scan data collector for radios ezCol does not drive (the 60-ft dish's
Ettus B210 via UHD).

Format (validated byte-for-byte against the dish's own Nov-2025 ezCol output
and the Aug/Sep-2025 ezColG GNU Radio files in
``~/Documents/DSES/Science/HI_and_Drift_Scan/ezRABase/``):

    from <provenance string>
    lat <deg> long <deg> amsl <m-or-ft> name <observatory>
    freqMin <MHz> freqMax <MHz> freqBinQty <bins>
    azDeg <deg> elDeg <deg>
    # times are in UTC
    # gain <text>
    # frequency spectrums of RMS power in dB
    <YYYY-MM-DDTHH:MM:SS> <bin0dB> <bin1dB> ... <binN-1dB>

Rows are ascending in frequency: bin 0 = freqMin, last bin = freqMax edge.
No trailing flags (4096-bin reference rows have exactly 4097 fields).

Like ezCol (which keeps the central 8 MHz of a 10 MS/s capture), the writer
can trim the anti-alias filter skirts via ``keep_fraction``; freqMin/Max and
freqBinQty always describe the bins actually written. Also like ezCol,
multi-day recordings roll to a NEW file at UTC midnight (``roll_daily=True``)
— one file per UTC day, each with a full header, so ezCon consumes a
multi-day campaign as its usual series of daily files.

Pure Python/numpy; the GNU Radio sink is defined only when gnuradio imports,
mirroring sigproc_fil.py.
"""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import fft as scipy_fft


def ezra_filename(prefix, utc=None):
    """ezCol-convention file name: <prefix>YYMMDD_HH.txt (UTC)."""
    utc = utc or datetime.now(timezone.utc)
    return f"{prefix}{utc:%y%m%d_%H}.txt"


def ezra_unique_path(directory, prefix, utc=None):
    """ezCol-convention path in `directory` that does not exist yet: a
    same-hour rerun appends a letter (DSES251113_19.txt -> ..._19a.txt),
    exactly like ezCol."""
    name = ezra_filename(prefix, utc)
    path = Path(directory) / name
    for letter in "abcdefghijklmnopqrstuvwxyz":
        if not path.exists():
            break
        path = Path(directory) / (name[:-4] + letter + ".txt")
    return path


class EzraTxtWriter:
    """Append ezRA .txt spectrum rows to an open file.

    Feed already-integrated linear-power spectra (ascending frequency,
    ``bin_qty`` bins) via :meth:`write_row`; they are stored as dB.

    With ``roll_daily=True`` (and a ``roll_prefix``), the writer follows
    ezCol's multi-day convention: at the first row on a new UTC date it
    closes the current file and opens ``<roll_prefix>YYMMDD_HH.txt`` in the
    same directory with a fresh, identical header — one file per UTC day
    instead of one ever-growing file. ``nrows`` counts the whole session;
    ``paths`` lists every file written.
    """

    def __init__(self, path, *, lat_deg, lon_deg, amsl, site_name,
                 freq_min_mhz, freq_max_mhz, bin_qty, az_deg, el_deg,
                 gain_text="", provenance="DSES_Workbench",
                 roll_daily=False, roll_prefix=""):
        self.bin_qty = int(bin_qty)
        self.nrows = 0
        self.paths = []
        self._roll_daily = bool(roll_daily)
        self._roll_prefix = roll_prefix
        # ezCon.py hard-gates on the first line starting with the literal
        # bytes "from ezCol" (that's why the group's GNU Radio predecessor
        # called itself "ezColG"). Guarantee compatibility regardless of the
        # provenance text supplied.
        if not provenance.startswith("ezCol"):
            provenance = "ezCol-compatible " + provenance
        self._hdr = dict(
            provenance=provenance, lat_deg=lat_deg, lon_deg=lon_deg,
            amsl=amsl, site_name=site_name, freq_min_mhz=freq_min_mhz,
            freq_max_mhz=freq_max_mhz, az_deg=az_deg, el_deg=el_deg,
            gain_text=gain_text)
        self._open(path)

    def _open(self, path):
        """Open `path` and write the ezRA header. The file covers the UTC
        date of its FIRST row (adopted in write_row) until rolled."""
        self.path = Path(path)
        self._file_date = None
        h = self._hdr
        self._fh = open(self.path, "w", newline="\n")
        self._fh.write(f"from {h['provenance']}\n")
        self._fh.write(f"lat {h['lat_deg']:g} long {h['lon_deg']:g} "
                       f"amsl {h['amsl']:g} name {h['site_name']}\n")
        self._fh.write(f"freqMin {h['freq_min_mhz']:g} "
                       f"freqMax {h['freq_max_mhz']:g} "
                       f"freqBinQty {self.bin_qty}\n")
        # .10g, not :g — %g rounds to 6 significant figures, which turned
        # az 359.9176 into 359.918 in the header (encoder-precision az/el
        # arrived with the 2026-09 site protocol).
        self._fh.write(f"azDeg {h['az_deg']:.10g} elDeg {h['el_deg']:.10g}\n")
        self._fh.write("# times are in UTC\n")
        self._fh.write(f"# gain {h['gain_text'] or 'xx'}\n")
        self._fh.write("# frequency spectrums of RMS power in dB\n")
        self._fh.flush()
        self.paths.append(str(self.path))

    def _roll(self, utc):
        """ezCol-style daily rollover: close the day's file, open the next
        UTC day's (fresh header, same settings)."""
        self._fh.flush()
        self._fh.close()
        self._open(ezra_unique_path(self.path.parent, self._roll_prefix, utc))

    def write_row(self, power_linear, utc=None):
        """Append one spectrum row. `power_linear` is an ascending-frequency
        mean-power array of bin_qty bins; stored as 10*log10(power) dB."""
        p = np.asarray(power_linear, dtype=np.float64)
        if p.size != self.bin_qty:
            raise ValueError(f"expected {self.bin_qty} bins, got {p.size}")
        utc = utc or datetime.now(timezone.utc)
        if self._roll_daily:
            if self._file_date is None:          # file covers its 1st row's day
                self._file_date = utc.date()
            elif utc.date() != self._file_date:
                self._roll(utc)
                self._file_date = utc.date()
        db = 10.0 * np.log10(np.maximum(p, 1e-30))
        stamp = utc.strftime("%Y-%m-%dT%H:%M:%S")
        self._fh.write(stamp + " " + " ".join(f"{v:.4f}" for v in db) + "\n")
        self.nrows += 1

    def close(self):
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        return {"path": str(self.path), "nrows": self.nrows,
                "bin_qty": self.bin_qty,
                "paths": list(self.paths), "files": len(self.paths)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class EzraIntegrator:
    """Channelize live I/Q and hand completed integration rows to a callback.

    FFTs of ``fft_bins`` (no window, matching ezCol), accumulated over
    ``integ_frames`` frames; the central ``keep_fraction`` of the band is
    kept (ezCol practice: central 8 MHz of a 10 MS/s capture) and passed to
    ``on_row(power_linear)`` as ascending-frequency mean power.
    """

    def __init__(self, *, fft_bins, integ_frames, on_row, keep_fraction=1.0):
        self.fft_bins = int(fft_bins)
        self.integ_frames = max(1, int(integ_frames))
        self.keep_fraction = float(keep_fraction)
        keep = int(round(self.fft_bins * self.keep_fraction / 2)) * 2
        keep = max(2, min(self.fft_bins, keep))
        self._lo = (self.fft_bins - keep) // 2
        self._hi = self._lo + keep
        self.kept_bins = keep
        self._on_row = on_row
        self._acc = np.zeros(self.fft_bins, dtype=np.float64)
        self._nacc = 0
        self._resid = np.empty(0, dtype=np.complex64)

    @staticmethod
    def kept_span_mhz(center_mhz, samp_rate, kept_bins, fft_bins):
        """freqMin/freqMax (MHz) of the kept central bins."""
        bw = samp_rate / 1e6
        half_kept = bw * kept_bins / fft_bins / 2.0
        return center_mhz - half_kept, center_mhz + half_kept

    def push(self, iq):
        iq = np.asarray(iq, dtype=np.complex64).ravel()
        if self._resid.size:
            iq = np.concatenate([self._resid, iq])
        nfull = (len(iq) // self.fft_bins) * self.fft_bins
        if nfull:
            # Local channelizer (NOT sigproc_fil.channelize_detect, whose
            # numpy FFT is byte-validated for the .fil path and must not
            # change): scipy's pocketfft with workers=-1 releases the GIL
            # and spreads across cores, which matters here — this runs on
            # the sink's worker thread beside the GNU Radio Python
            # callbacks, and a GIL-bound FFT starved them into RX
            # overflows at 16 MS/s (observed live 2026-08-02).
            blocks = iq[:nfull].reshape(-1, self.fft_bins)
            spec = scipy_fft.fft(blocks, axis=1, workers=-1)
            power = np.fft.fftshift(
                (spec.real.astype(np.float64) ** 2
                 + spec.imag.astype(np.float64) ** 2), axes=1)
            # Bulk accumulation: one vectorized sum per push (plus one per
            # row boundary, which at ~31e3 frames/row is rare). The original
            # per-frame Python loop could not keep up at 16 MS/s and caused
            # RX overflows during recording.
            i, nfr = 0, power.shape[0]
            while i < nfr:
                take = min(self.integ_frames - self._nacc, nfr - i)
                self._acc += power[i:i + take].sum(axis=0, dtype=np.float64)
                self._nacc += take
                i += take
                if self._nacc >= self.integ_frames:
                    mean = self._acc[self._lo:self._hi] / self._nacc
                    self._on_row(mean)
                    self._acc[:] = 0.0
                    self._nacc = 0
        self._resid = iq[nfull:].copy()


try:
    from gnuradio import gr
    import threading
    import time as _time
    _HAVE_GR = True
except Exception:              # pragma: no cover
    _HAVE_GR = False


if _HAVE_GR:
    class EzraTxtSink(gr.sync_block):
        """GNU Radio sink: complex baseband in -> ezRA ``.txt`` rows on disk.

        One output row per ``integ_frames`` FFTs of ``fft_bins`` bins,
        timestamped at row completion (UTC), central ``keep_fraction`` of
        the band kept.

        Threaded: ``work()`` only copies the incoming buffer into a bounded
        queue; a daemon worker does the FFT/integration/writing. Doing the
        DSP inline on the GNU Radio thread caused RX overflows at 16 MS/s
        (observed live 2026-08-02). If the worker ever falls behind the
        queue bound, excess samples are counted in ``dropped_samples``
        (each drop only shortens one ~13 s integration slightly).
        """

        MAX_QUEUE_SAMPLES = 1 << 23    # 8 M samples ≈ 64 MB of backlog

        def __init__(self, path, *, fft_bins, integ_frames, samp_rate,
                     center_freq_mhz, lat_deg, lon_deg, amsl, site_name,
                     az_deg, el_deg, gain_text="", keep_fraction=1.0,
                     provenance="DSES_Workbench",
                     roll_daily=False, roll_prefix=""):
            gr.sync_block.__init__(self, name="ezra_txt_sink",
                                   in_sig=[np.complex64], out_sig=None)
            self._integ = EzraIntegrator(
                fft_bins=fft_bins, integ_frames=integ_frames,
                on_row=self._emit_row, keep_fraction=keep_fraction)
            fmin, fmax = EzraIntegrator.kept_span_mhz(
                center_freq_mhz, samp_rate, self._integ.kept_bins, fft_bins)
            self._writer = EzraTxtWriter(
                path, lat_deg=lat_deg, lon_deg=lon_deg, amsl=amsl,
                site_name=site_name, freq_min_mhz=fmin, freq_max_mhz=fmax,
                bin_qty=self._integ.kept_bins, az_deg=az_deg, el_deg=el_deg,
                gain_text=gain_text, provenance=provenance,
                roll_daily=roll_daily, roll_prefix=roll_prefix)
            self.dropped_samples = 0
            self._q = []
            self._q_samples = 0
            self._lock = threading.Lock()
            self._stopping = threading.Event()
            self._closed = False
            self._worker = threading.Thread(
                target=self._worker_loop, name="ezra-txt-worker", daemon=True)
            self._worker.start()

        def _emit_row(self, power_linear):
            if self._writer._fh is not None:
                self._writer.write_row(power_linear)

        @property
        def path(self):
            return self._writer.path

        @property
        def nrows(self):
            return self._writer.nrows

        def work(self, input_items, output_items):
            buf = input_items[0]
            n = len(buf)
            with self._lock:
                if self._q_samples + n <= self.MAX_QUEUE_SAMPLES:
                    self._q.append(np.array(buf, dtype=np.complex64))
                    self._q_samples += n
                else:
                    self.dropped_samples += n
            return n

        def _drain(self):
            with self._lock:
                batch, self._q = self._q, []
                self._q_samples = 0
            if not batch:
                return False
            # One big slab per pass: fewer, larger FFT/ufunc calls hold the
            # GIL far less than many small ones (the GR Python callbacks
            # must stay responsive or the radio overflows).
            self._integ.push(np.concatenate(batch) if len(batch) > 1
                             else batch[0])
            return True

        def _worker_loop(self):
            while not self._stopping.is_set():
                if not self._drain():
                    _time.sleep(0.005)
            self._drain()          # final drain so no queued samples are lost

        def stop(self):
            self.close()
            return True

        def close(self):
            if self._closed:
                return self._writer.close()
            self._closed = True
            self._stopping.set()
            self._worker.join(timeout=10.0)
            return self._writer.close()

    # -----------------------------------------------------------------------
    # 1.7: the channelizer runs in the flowgraph (compiled blocks). Python
    # sees a few partial rows per second and sums them in double precision
    # to the row boundary; EzraTxtWriter (header, format, UTC-midnight
    # rollover) is untouched. Motivation: the Python EzraTxtSink above does a
    # scipy FFT over the whole stream on a worker thread — cheap at the
    # Pi's 2 MS/s drift scan, a full core's worth on an 8 MS/s HackRF host
    # like Ray's (measured 2026-11-04/05).
    # -----------------------------------------------------------------------
    def sub_integration(integ_frames, frames_per_second, target_s=0.4):
        """The compiled integrator's frame count: the largest divisor of
        ``integ_frames`` that spans at most ``target_s`` seconds (so Python
        sees a handful of partial rows per second and every row boundary
        still falls on exactly ``integ_frames`` frames)."""
        integ_frames = max(1, int(integ_frames))
        cap = max(1, int(target_s * frames_per_second))
        best = 1
        for d in range(1, min(cap, integ_frames) + 1):
            if integ_frames % d == 0:
                best = d
        return best

    class EzraRowSink(gr.sync_block):
        """Row-domain ezRA writer: input = power rows (float32, ``fft_bins``
        wide, low->high, each the SUM over ``sub`` frames from integrate_ff).
        Accumulates in float64 to ``integ_frames`` frames, keeps the central
        ``keep_fraction`` and writes one ezRA row through EzraTxtWriter."""

        def __init__(self, path, *, fft_bins, integ_frames, sub, samp_rate,
                     center_freq_mhz, lat_deg, lon_deg, amsl, site_name,
                     az_deg, el_deg, gain_text="", keep_fraction=1.0,
                     provenance="DSES_Workbench",
                     roll_daily=False, roll_prefix=""):
            fft_bins = int(fft_bins)
            gr.sync_block.__init__(self, name="ezra_row_sink",
                                   in_sig=[(np.float32, fft_bins)], out_sig=None)
            self.fft_bins = fft_bins
            self.integ_frames = max(1, int(integ_frames))
            self.sub = max(1, int(sub))
            keep = int(round(fft_bins * float(keep_fraction) / 2)) * 2
            keep = max(2, min(fft_bins, keep))
            self._lo = (fft_bins - keep) // 2
            self._hi = self._lo + keep
            self.kept_bins = keep
            fmin, fmax = EzraIntegrator.kept_span_mhz(
                center_freq_mhz, samp_rate, keep, fft_bins)
            self._writer = EzraTxtWriter(
                path, lat_deg=lat_deg, lon_deg=lon_deg, amsl=amsl,
                site_name=site_name, freq_min_mhz=fmin, freq_max_mhz=fmax,
                bin_qty=keep, az_deg=az_deg, el_deg=el_deg,
                gain_text=gain_text, provenance=provenance,
                roll_daily=roll_daily, roll_prefix=roll_prefix)
            self._acc = np.zeros(fft_bins, dtype=np.float64)
            self._nacc = 0
            self.dropped_samples = 0        # no worker queue here: always 0
            self._closed = False

        @property
        def path(self):
            return self._writer.path

        @property
        def nrows(self):
            return self._writer.nrows

        def work(self, input_items, output_items):
            rows = input_items[0]
            k = rows.shape[0]
            if self._closed or self._writer._fh is None:
                return k
            i = 0
            while i < k:
                need_rows = (self.integ_frames - self._nacc) // self.sub
                take = max(1, min(need_rows, k - i))
                self._acc += rows[i:i + take].sum(axis=0, dtype=np.float64)
                self._nacc += take * self.sub
                i += take
                if self._nacc >= self.integ_frames:
                    mean = self._acc[self._lo:self._hi] / self._nacc
                    self._writer.write_row(mean)
                    self._acc[:] = 0.0
                    self._nacc = 0
            return k

        def stop(self):
            self.close()
            return True

        def close(self):
            if self._closed:
                return self._writer.close()
            self._closed = True
            return self._writer.close()

    class EzraChain:
        """The compiled ezRA recorder: builds and splices

            upstream -> stream_to_vector(fft_bins) -> fft_vcc(fft_bins, no window)
                     -> complex_to_mag_squared -> integrate_ff(sub) -> EzraRowSink

        and presents EzraTxtSink's interface (path, nrows, kept_bins,
        dropped_samples, close()). No window and the central keep fraction
        match EzraIntegrator (ezCol practice), so rows agree with it to
        single-precision rounding (test_ezra_chain.py pins this)."""

        def __init__(self, path, *, fft_bins, integ_frames, samp_rate,
                     keep_fraction=1.0, **sink_kwargs):
            from gnuradio import blocks as _blocks, fft as _fft
            n = int(fft_bins)
            self.fft_bins = n
            self.integ_frames = max(1, int(integ_frames))
            self.sub = sub_integration(self.integ_frames, float(samp_rate) / n)
            self._s2v = _blocks.stream_to_vector(gr.sizeof_gr_complex, n)
            self._fft = _fft.fft_vcc(n, True, [], True, 1)       # [] = no window
            self._mag2 = _blocks.complex_to_mag_squared(n)
            self._integ = _blocks.integrate_ff(self.sub, n) if self.sub > 1 else None
            self.sink = EzraRowSink(
                path, fft_bins=n, integ_frames=self.integ_frames, sub=self.sub,
                samp_rate=samp_rate, keep_fraction=keep_fraction, **sink_kwargs)
            self._writer = self.sink._writer    # the app reads .paths after a rollover
            for b in (self._s2v, self._fft, self._mag2, self._integ):
                if b is not None:
                    b.set_min_output_buffer(64)
            self._upstream = None

        def connect(self, tb, upstream):
            self._upstream = upstream
            tb.connect((upstream, 0), (self._s2v, 0))
            tb.connect((self._s2v, 0), (self._fft, 0))
            tb.connect((self._fft, 0), (self._mag2, 0))
            if self._integ is not None:
                tb.connect((self._mag2, 0), (self._integ, 0))
                tb.connect((self._integ, 0), (self.sink, 0))
            else:
                tb.connect((self._mag2, 0), (self.sink, 0))

        def disconnect(self, tb, upstream=None):
            up = upstream if upstream is not None else self._upstream
            if up is not None:
                tb.disconnect((up, 0), (self._s2v, 0))
            tb.disconnect((self._s2v, 0), (self._fft, 0))
            tb.disconnect((self._fft, 0), (self._mag2, 0))
            if self._integ is not None:
                tb.disconnect((self._mag2, 0), (self._integ, 0))
                tb.disconnect((self._integ, 0), (self.sink, 0))
            else:
                tb.disconnect((self._mag2, 0), (self.sink, 0))

        @property
        def path(self):
            return self.sink.path

        @property
        def nrows(self):
            return self.sink.nrows

        @property
        def kept_bins(self):
            return self.sink.kept_bins

        @property
        def dropped_samples(self):
            return self.sink.dropped_samples

        def close(self):
            return self.sink.close()
