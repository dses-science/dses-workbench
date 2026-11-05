"""dses_radio -- the B210 (and generic radio) layer shared by the DSES Radio Astronomy
Workbench and the DSES EVE modem.

Factored out of dses_workbench.py on 2026-09-11 (EVE Design + ICD section 9: "a code-
sharing boundary, not a runtime one"). The Workbench's behavior is unchanged: it imports
these names from here. The EVE modem imports the same module from the Workbench clone.

Contents
  ensure_uhd_images()      point UHD_IMAGES_DIR at a firmware directory if unset
  find_b200_uhd()          UHD B200-family discovery -> [{driver, serial, product, label}]
  RadioSource              the receive-side interface the Workbench talks to
  UhdB200Source            uhd.usrp_source wrapper: deep buffers, receiver A/B ports,
                           the VERIFIED LO-offset tune path (tune_with_lo_offset)
  tune_with_lo_offset()    that tune path as a function, usable on a usrp_sink too
  make_usrp_sink()         uhd.usrp_sink with the DSES conventions (TX/RX port, gain)
  RealtimeMode             GC confinement + 1 ms Windows timer for the life of a capture
  set_reference(), wait_ref_locked(), set_time_utc_on_pps(), gpio helpers
                           B210 external reference / PPS / time / keying-line support
                           (used by the EVE modem; harmless to the Workbench)

House rules that live here: deep output buffers on every edge leaving a USRP source
(4 Mi samples), TX gain is the caller's responsibility (the Workbench always passes the
minimum), never trust get_samp_rates() for the rate envelope, verify an LO offset by
reading actual_dsp_freq back.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from gnuradio import gr, uhd

DEEP_BUFFER_SAMPLES = 1 << 22          # ~32 MB of complex64 per edge, ~260 ms at 16 MS/s
DRIVER_UHD_B200 = "uhd_b200"


# ---------------------------------------------------------------------------------------
# firmware images
# ---------------------------------------------------------------------------------------
def ensure_uhd_images() -> Optional[str]:
    """Point UHD_IMAGES_DIR at a directory holding the B200 firmware if the environment
    does not already. Conda envs ship UHD without images; a radioconda install has them.
    Without this a cold (just-powered) B210 cannot be firmware-loaded and discovery finds
    nothing. Returns the directory used, or None."""
    if os.environ.get("UHD_IMAGES_DIR"):
        return os.environ["UHD_IMAGES_DIR"]
    home = Path.home()
    for cand in (Path(sys.prefix) / "Library" / "share" / "uhd" / "images",
                 Path(sys.prefix) / "share" / "uhd" / "images",
                 home / "radioconda" / "Library" / "share" / "uhd" / "images",
                 home / "radioconda" / "share" / "uhd" / "images",
                 Path("C:/ProgramData/radioconda/Library/share/uhd/images"),
                 Path("/home/dses/radioconda/share/uhd/images")):
        if (cand / "usrp_b200_fw.hex").is_file():
            os.environ["UHD_IMAGES_DIR"] = str(cand)
            return str(cand)
    return None


# ---------------------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------------------
def _addr_to_dict(a):
    """Pull serial/product/name out of a uhd device_addr object. to_dict()
    has been observed to return {} in some Python contexts even when the
    string form has full info, so we fall back to parsing str(a)."""
    for fn in ((lambda: a.to_dict()),
               (lambda: {k: a.get(k) for k in a.keys()}),
               (lambda: dict(a))):
        try:
            d = fn()
            if d:
                return d
        except Exception:
            continue
    d = {}
    for line in str(a).splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            d[k.strip()] = v.strip()
    return d


def find_b200_uhd():
    """Return UHD B200-family devices as {driver, serial, product, label}."""
    ensure_uhd_images()
    try:
        addrs = uhd.find('type=b200')
    except Exception as exc:
        print(f"UHD discovery failed: {exc}", file=sys.stderr)
        return []
    out = []
    for a in addrs:
        d = _addr_to_dict(a)
        serial = d.get('serial', '')
        product = d.get('product', d.get('type', 'B200'))
        out.append({
            'driver':  DRIVER_UHD_B200,
            'serial':  serial,
            'product': product,
            'label':   f"USRP {product} — {serial or '(no serial)'}",
        })
    return out


# ---------------------------------------------------------------------------------------
# the verified LO-offset tune
# ---------------------------------------------------------------------------------------
def _safe_log(s: str) -> None:
    """stderr print that can never take a tune down with it: a console whose
    handle refuses writes raises OSError [WinError 1] from print() (seen
    2026-11-04 in Rick's PowerShell session), and an exception out of the
    log call used to skip the classic fallback re-tune that followed it."""
    try:
        print(s, file=sys.stderr)
    except Exception:
        pass


def tune_with_lo_offset(block, center_hz: float, offset_hz: float, chan: int = 0,
                        log: Callable[[str], None] = _safe_log) -> bool:
    """Tune a usrp_source or usrp_sink to center_hz with the LO parked offset_hz away
    (the DSP shifts the band back). VERIFIED, not trusted: some UHD builds honor the
    MANUAL rf policy but never apply the AUTO dsp compensation (the Pi's radioconda
    gr-uhd 3.10.12 did exactly this on 2026-08-19/24 - the band ended up centered on
    the LO while the app believed it was on target). The tune result's actual_dsp_freq
    is checked; if the compensation is missing an explicit MANUAL-dsp request is issued,
    both sign conventions, with readback. Returns True if the offset is honest, False if
    the radio was left in classic tuning (offset 0) at center_hz."""
    if not offset_hz:
        block.set_center_freq(center_hz, chan)
        return True
    res = block.set_center_freq(uhd.tune_request(center_hz, offset_hz), chan)
    try:
        dsp = float(res.actual_dsp_freq)
    except Exception:
        dsp = None
    if dsp is not None and abs(abs(dsp) - abs(offset_hz)) < 1.0:
        return True
    def _say(msg):
        try:
            log(msg)
        except Exception:
            pass                 # a broken console must never alter the tune
    for sign in (-1.0, 1.0):
        try:
            req = uhd.tune_request()
            req.rf_freq_policy = uhd.tune_request.POLICY_MANUAL
            req.rf_freq = center_hz + offset_hz
            req.dsp_freq_policy = uhd.tune_request.POLICY_MANUAL
            req.dsp_freq = sign * offset_hz
            r2 = block.set_center_freq(req, chan)
            if abs(abs(float(r2.actual_dsp_freq)) - abs(offset_hz)) < 1.0:
                _say(f"B210 LO offset: UHD dsp AUTO compensation absent; "
                     f"using MANUAL dsp {sign * offset_hz:+.0f} Hz")
                return True
        except Exception as exc:
            _say(f"B210 manual-dsp tune failed: {exc}")
            break
    # Re-tune FIRST, then say so: the radio must never be left on the failed
    # offset attempt because the message could not be written.
    block.set_center_freq(center_hz, chan)
    _say("B210 LO offset could not be applied faithfully on this UHD; "
         "falling back to classic tuning.")
    return False


# ---------------------------------------------------------------------------------------
# receive-side interface (the Workbench's RadioSource family)
# ---------------------------------------------------------------------------------------
class RadioSource:
    """Common interface for whatever produces baseband I/Q samples for the
    flowgraph — currently UHD's USRP source; SoapySDR-backed devices land
    in a sibling subclass. The main class doesn't talk to UHD directly any
    more; it talks through this interface so the wiring is swappable.

    Subclasses set `self.block` to the GR block emitting complex64 samples
    and implement the three set_* methods. `samp_rate_options` and
    `gain_range` let the UI adapt to the device's capabilities (subclasses
    override when the defaults — B210's range — aren't right)."""

    # The GR source block. Connected to the display chain at
    # dses_workbench.__init__'s "Connections" section.
    block = None

    # Discrete sample-rate choices the sidebar combo offers. Defaults to
    # a conservative generic set almost any SDR can do; hardware subclasses
    # override with the device's actually-supported list. (This used to be
    # list(FFT_SIZES) — an unknown Soapy driver would offer "rates" of
    # 1–262 kHz that were really FFT lengths.)
    samp_rate_options = [1e6, 2e6, 2.048e6, 4e6, 5e6, 8e6, 10e6]

    # (min_db, max_db, step_db) for the RX-gain slider in the sidebar.
    gain_range = (0.0, 76.0, 1.0)

    # Short label for the sidebar Device button and the window title.
    display_label = "(unknown radio)"

    # Verbose hardware description for SigMF metadata (core:hw field).
    hw_info = "(unknown radio)"

    # RF input ports the device exposes (e.g. ['TX/RX', 'RX2'] on a B210,
    # ['Tuner 1 50ohm', 'Tuner 2 50ohm'] on an RSPduo). Subclasses fill this
    # from the live block. A single-port radio leaves it length-1 (or empty)
    # and the sidebar omits the Antenna combo.
    antennas = []

    # The port currently selected; set by subclasses after opening.
    current_antenna = ""

    # Hardware LO offset (Hz) currently applied; 0 = LO on the displayed
    # center (classic zero-IF behavior, DC artefact mid-band).
    lo_offset = 0.0

    def set_samp_rate(self, hz: float) -> None:
        raise NotImplementedError

    def set_center_freq(self, hz: float) -> None:
        raise NotImplementedError

    def lo_offset_supported(self) -> bool:
        """True if this radio can park its LO away from the displayed center
        (the DDC/BB shift stage brings the band back, so the display and all
        recorded frequencies are unchanged — only the DC artefact moves)."""
        return False

    def set_lo_offset(self, hz: float) -> bool:
        """Apply LO offset `hz` and re-tune. Returns True if the hardware
        honored it; False leaves the radio in classic (offset 0) tuning."""
        return hz == 0.0

    def set_gain(self, db: float) -> None:
        raise NotImplementedError

    def set_antenna(self, name: str) -> None:
        # Default: radios with a single fixed port don't need to do anything.
        pass

    def antenna_needs_restart(self, name: str) -> bool:
        """True if applying antenna `name` changes the active RX frontend in a
        way that requires the flowgraph to be restarted (lock/unlock) for the
        change to take effect. Default: live setter is enough."""
        return False

    def samp_rate_range(self):
        """(min_hz, max_hz) the device will accept for a manual sample-rate
        entry. Default: the span of the discrete `samp_rate_options`; hardware-
        backed subclasses override with the device's real reported limits."""
        opts = self.samp_rate_options or [1e6]
        return (float(min(opts)), float(max(opts)))

    def freq_range(self):
        """(min_hz, max_hz) center frequencies the device can tune to, or
        None if unknown. Hardware-backed subclasses override with real
        limits; the base can't know, so the sweep range just isn't clamped."""
        return None

    def get_actual_samp_rate(self) -> float:
        """The rate the device is really running, which can differ from the
        requested value after the driver snaps to an achievable rate. Return
        0.0 when unknown (the caller then keeps the requested value)."""
        return 0.0


class UhdB200Source(RadioSource):
    """Wraps `uhd.usrp_source` for B200-family devices (B200 / B210)."""

    # 0.625 and 1.25 MHz are the validated DSES lab simulator geometries
    # (UHF 625 kHz / L-band 1.25 MHz) — needed so a live .fil capture matches
    # the proven offline tsamp; the B210 supports rates well below 1 MHz.
    # Every preset is "clean" for the AD9361 in UHD's automatic master-clock
    # mode: MCR = rate × 2^n lands inside the 5–61.44 MHz clock range, so
    # decimation stays on the half-band filter chain (flat passband) rather
    # than falling back to CIC filtering (passband droop — a smeared
    # calibration error in a radio-astronomy spectrum). 61.44 MS/s is the
    # device's single-channel ceiling (56 MHz max analog bandwidth); rates
    # ≥ 25 MS/s need USB 3 and a host that can drink from the hose —
    # watch the overflow/gap indicators, especially when recording.
    samp_rate_options = [0.625e6, 1e6, 1.25e6, 2e6, 4e6, 5e6, 8e6, 10e6,
                         16e6, 20e6, 25e6, 30.72e6, 40e6, 50e6, 56e6,
                         61.44e6]
    gain_range = (0.0, 76.0, 1.0)

    def __init__(self, serial: str, samp_rate: float, center_freq: float,
                 gain: float, antenna: str = "", stream_args: str = 'recv_frame_size=8192,num_recv_frames=1024'):
        ensure_uhd_images()
        self._serial = serial
        # Cache the live freq/gain so we can re-apply them after a receiver
        # (subdev) switch, which resets per-frontend state.
        self._cur_freq = center_freq
        self._cur_gain = gain
        self._lo_offset = 0.0
        self.block = uhd.usrp_source(
            ",".join((f'serial={serial}', '')),
            uhd.stream_args(
                cpu_format="fc32",
                args=stream_args,
                channels=list(range(0, 1)),
            ),
        )
        # Deep output buffers on every edge leaving the source: at 16 MS/s
        # the GR default gives a downstream Python sink only a few ms of
        # slack, so ANY GIL pause longer than that backs the chain up and
        # the radio prints 'O' (the constant Haswell-recording overflows,
        # root-caused 2026-08-02). 4 Mi samples ≈ 32 MB/edge ≈ 260 ms of
        # cushion at 16 MS/s — Python-thread scheduling jitter is absorbed
        # instead of dropped.
        self.block.set_min_output_buffer(DEEP_BUFFER_SAMPLES)
        self.block.set_samp_rate(samp_rate)
        self.block.set_time_unknown_pps(uhd.time_spec(0))
        self.block.set_center_freq(center_freq, 0)

        # The B210 has two RX frontends, mapped as subdevs "A:A" and "A:B"
        # (receiver A and receiver B). The B200 has just "A:A". The default
        # subdev spec lists all available frontends.
        try:
            self._subdevs = self.block.get_subdev_spec(0).split()
        except Exception:
            self._subdevs = []
        try:
            ports = list(self.block.get_antennas(0))   # e.g. ['TX/RX', 'RX2']
        except Exception:
            ports = []
        self._ports = ports
        self._recv_labels = ['A', 'B', 'C', 'D'][:len(self._subdevs)]
        if len(self._subdevs) >= 2 and ports:
            # Composite "<receiver> : <port>" — covers all physical inputs.
            self.antennas = [f"{r} : {p}"
                             for r in self._recv_labels for p in ports]
        else:
            # Single-receiver B200 (or unknown): plain port names.
            self.antennas = ports
        # The frontend the streamer is currently mapped to. Default mapping
        # for channel 0 is the first subdev (receiver A).
        self._current_subdev = self._subdevs[0] if self._subdevs else None

        chosen = self._pick_initial(antenna)
        self._apply(chosen)
        self.block.set_gain(gain, 0)
        self.display_label = f"USRP B210 — {serial}"
        self.hw_info = f"Ettus USRP B210 (s/n {serial})"

    @property
    def serial(self) -> str:
        return self._serial

    def _pick_initial(self, saved: str) -> str:
        """Saved port if still valid; else prefer an RX2 input; else first."""
        if saved and saved in self.antennas:
            return saved
        for a in self.antennas:
            if a.endswith("RX2"):
                return a
        return self.antennas[0] if self.antennas else "RX2"

    def _split(self, name: str):
        """(subdev_spec_or_None, port) for a composite or plain antenna name."""
        if ' : ' in name:
            recv, port = name.split(' : ', 1)
            idx = self._recv_labels.index(recv) if recv in self._recv_labels else 0
            spec = self._subdevs[idx] if idx < len(self._subdevs) else None
            return spec, port
        return None, name

    def _apply(self, name: str) -> None:
        spec, port = self._split(name)
        if spec is not None and spec != self._current_subdev:
            # Remap channel 0 to a different RX frontend (receiver A↔B).
            # set_subdev_spec only takes effect when the RX streamer is
            # (re)created — the caller must do this while the flowgraph is
            # stopped or inside a lock()/unlock() cycle (see
            # antenna_needs_restart). Per-frontend params reset on the
            # remap, so re-apply freq + gain.
            self.block.set_subdev_spec(spec, 0)
            self._tune()
            self.block.set_gain(self._cur_gain, 0)
            self._current_subdev = spec
        self.block.set_antenna(port, 0)
        self.current_antenna = name

    def antenna_needs_restart(self, name: str) -> bool:
        spec, _ = self._split(name)
        return spec is not None and spec != self._current_subdev

    def set_samp_rate(self, hz: float) -> None:
        # UHD snaps to the nearest achievable rate internally; read the result
        # back with get_actual_samp_rate().
        self.block.set_samp_rate(hz)
        if self._lo_offset:
            # The analog filter is centered on the LO, not on the displayed
            # band — re-fit it to the new rate (see _apply_bandwidth).
            self._apply_bandwidth()

    def _apply_bandwidth(self) -> None:
        """Fit the AD9361 analog filter to the offset geometry. The filter is
        a lowpass at complex baseband centered on the LO; the wanted band
        occupies [lo_off − rate/2, lo_off + rate/2] relative to the LO, so
        the filter must open to 2·|lo_off| + rate when the LO is parked
        off-center (capped at the 56 MHz device limit). With offset 0 this
        is just the sample rate — the driver's default policy."""
        try:
            rate = float(self.block.get_samp_rate())
        except Exception:
            rate = 2e6
        bw = min(56e6, 2.0 * abs(self._lo_offset) + rate)
        try:
            self.block.set_bandwidth(bw, 0)
        except Exception as exc:
            print(f"B210 set_bandwidth({bw:.0f}) failed: {exc}",
                  file=sys.stderr)

    def _tune(self) -> bool:
        """(Re)tune to the cached center with the current LO offset through
        tune_with_lo_offset(); on failure the offset is dropped and the radio
        is left in classic tuning (the caller sees False)."""
        if not self._lo_offset:
            self.block.set_center_freq(self._cur_freq, 0)
            return True
        if tune_with_lo_offset(self.block, self._cur_freq, self._lo_offset, 0):
            return True
        self._lo_offset = 0.0
        self.lo_offset = 0.0
        self._apply_bandwidth()
        self.block.set_center_freq(self._cur_freq, 0)
        return False

    def lo_offset_supported(self) -> bool:
        return True

    def set_lo_offset(self, hz: float) -> bool:
        self._lo_offset = float(hz)
        self.lo_offset = self._lo_offset
        self._apply_bandwidth()
        return self._tune()

    def set_center_freq(self, hz: float) -> None:
        self._cur_freq = hz
        self._tune()

    def set_gain(self, db: float) -> None:
        self._cur_gain = db
        self.block.set_gain(db, 0)

    def set_antenna(self, name: str) -> None:
        self._apply(name)

    def samp_rate_range(self):
        """The B200-family's true rate envelope.

        Deliberately NOT from get_samp_rates(): UHD answers that query
        relative to the CURRENT master clock (e.g. "0.031–16 MHz" while
        clocked for 16 MS/s), but in automatic master-clock mode a new rate
        request re-clocks the AD9361 — verified on hardware 2026-08-02:
        every preset up to 61.44 MS/s lands EXACT starting from a 16 MS/s
        clock. Clamping against the momentary query capped every rate
        change at the boot rate's ceiling (even the old 25 MHz preset
        silently became 16), so report the chip's real envelope and let
        the driver snap + actual-rate readback handle the rest."""
        return (62.5e3, 61.44e6)

    def freq_range(self):
        """The B210's RF tuning range, from UHD."""
        try:
            r = self.block.get_freq_range(0)     # uhd.meta_range_t
            lo, hi = float(r.start()), float(r.stop())
            if hi > lo > 0:
                return (lo, hi)
        except Exception:
            pass
        return None

    def get_actual_samp_rate(self) -> float:
        try:
            return float(self.block.get_samp_rate())
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------------------
# transmit side
# ---------------------------------------------------------------------------------------
def make_usrp_sink(serial: str, samp_rate: float, center_freq: float, gain_db: float,
                   antenna: str = "TX/RX", lo_offset_hz: float = 0.0, stream_args: str = ""):
    """uhd.usrp_sink with the DSES conventions. The caller decides the gain (the
    Workbench passes 0.0 = minimum, its protected-band rule; the EVE modem is the only
    place with real drive, behind its own interlocks). Feed it from an edge with a deep
    minimum output buffer (see the self-test lesson of 2026-08-05: at >= 4 MS/s the sink
    starves on ordinary buffers whenever the GIL pauses)."""
    ensure_uhd_images()
    tx = uhd.usrp_sink(f"serial={serial}" if serial else "",
                       uhd.stream_args(cpu_format="fc32", args=stream_args, channels=[0]))
    tx.set_samp_rate(float(samp_rate))
    tune_with_lo_offset(tx, float(center_freq), float(lo_offset_hz), 0)
    tx.set_antenna(antenna, 0)
    tx.set_gain(float(gain_db), 0)
    return tx


# ---------------------------------------------------------------------------------------
# host stall hygiene
# ---------------------------------------------------------------------------------------
class RealtimeMode:
    """Cap host-side stall length for the duration of a capture or transmission:
    confine Python's GC to cheap young-generation collections (gc.freeze + a high
    gen-1/gen-2 threshold; GC stays ENABLED so memory is bounded), and on Windows
    replace the 15.6 ms scheduler quantum with 1 ms (macOS and Linux already schedule
    at ~1 ms). begin()/end() are idempotent; usable as a context manager."""

    GC_RT_THRESHOLD = (700, 50_000, 50_000)

    def __init__(self, threshold=None):
        self.threshold = threshold or self.GC_RT_THRESHOLD
        self.active = False
        self._saved_gc = None
        self._timer_period = False

    def begin(self) -> None:
        if self.active:
            return
        self.active = True
        try:
            self._saved_gc = gc.get_threshold()
            gc.freeze()
            gc.set_threshold(*self.threshold)
        except Exception:
            self._saved_gc = None
        if sys.platform == "win32":
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._timer_period = True
            except Exception:
                self._timer_period = False

    def end(self) -> None:
        if not self.active:
            return
        self.active = False
        try:
            if self._saved_gc:
                gc.set_threshold(*self._saved_gc)
            gc.unfreeze()
        except Exception:
            pass
        if sys.platform == "win32" and self._timer_period:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._timer_period = False

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, *exc):
        self.end()
        return False


# ---------------------------------------------------------------------------------------
# reference, time, keying line (B210; used by the EVE modem)
# ---------------------------------------------------------------------------------------
def set_reference(block, clock_source: str = "external", time_source: Optional[str] = None,
                  mboard: int = 0) -> None:
    """Select the 10 MHz reference ('internal' | 'external' | 'gpsdo') and the PPS/time
    source ('none' | 'internal' | 'external' | 'gpsdo'; default: same as the clock, or
    'external' when the clock is external). Works on usrp_source and usrp_sink alike;
    the setting is per motherboard, so one call covers both streams on one B210."""
    block.set_clock_source(clock_source, mboard)
    if time_source is None:
        time_source = "external" if clock_source == "external" else clock_source
    block.set_time_source(time_source, mboard)


def ref_locked(block, mboard: int = 0) -> bool:
    """The ref_locked sensor. NOTE: on the B210 it reads False with the INTERNAL reference
    (bench, 2026-09-11) - it reports lock to an external/GPSDO 10 MHz only."""
    try:
        return bool(block.get_mboard_sensor("ref_locked", mboard).to_bool())
    except Exception:
        return False


def wait_ref_locked(block, timeout_s: float = 5.0, mboard: int = 0) -> bool:
    """Poll the ref_locked sensor. With the internal reference it reads true at once."""
    deadline = time.monotonic() + timeout_s
    while True:
        if ref_locked(block, mboard):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def set_time_utc_on_pps(block, utc_now: Optional[float] = None, mboard: int = 0,
                        settle_s: float = 1.2) -> float:
    """Set the USRP time to UTC at the next PPS edge and verify it took.

    Waits for a PPS edge (so the 'next' edge is a full second away), programs
    time_next_pps = the next whole UTC second, waits for that edge, and reads
    get_time_last_pps() back. Returns the difference (s) between the device time at that
    PPS and the whole-second UTC it should be: |difference| < 1e-6 when the set took.
    With time_source 'internal' the B210 generates its own PPS, so the routine still
    completes (the epoch is then only as good as the host clock)."""
    last = block.get_time_last_pps(mboard).get_real_secs()
    t0 = time.monotonic()
    while block.get_time_last_pps(mboard).get_real_secs() == last and time.monotonic() - t0 < settle_s:
        time.sleep(0.02)
    now = time.time() if utc_now is None else utc_now
    next_sec = int(now) + 1
    block.set_time_next_pps(uhd.time_spec(next_sec))          # gr-uhd: no mboard argument
    time.sleep(settle_s)
    got = block.get_time_last_pps(mboard).get_real_secs()
    return got - next_sec


def gpio_write_at(block, value: int, device_time_s: float, bank: str = "FP0", mask: int = 0x01,
                  mboard: int = 0) -> None:
    """Timed GPIO through UHD command time. CAVEAT (bench 2026-09-11, B210, gr-uhd
    3.10.12 / UHD 4.8 on Windows): the write took effect IMMEDIATELY, not at the
    command time - the readback was already high before the deadline. Treat as
    unverified; the EVE station keys host-timed (T_lead 200 ms is easy for the host)."""
    block.set_command_time(uhd.time_spec(device_time_s), mboard)
    try:
        block.set_gpio_attr(bank, "OUT", value & mask, mask, mboard)
    finally:
        block.clear_command_time(mboard)


def gpio_setup_output(block, bank: str = "FP0", mask: int = 0x01, mboard: int = 0) -> None:
    """Make the masked lines of `bank` GPIO outputs under manual (not ATR) control."""
    block.set_gpio_attr(bank, "CTRL", 0x00, mask, mboard)     # manual control
    block.set_gpio_attr(bank, "DDR", mask, mask, mboard)      # direction: output
    block.set_gpio_attr(bank, "OUT", 0x00, mask, mboard)


def gpio_write(block, value: int, bank: str = "FP0", mask: int = 0x01, mboard: int = 0) -> None:
    block.set_gpio_attr(bank, "OUT", value & mask, mask, mboard)


def gpio_read(block, bank: str = "FP0", mboard: int = 0) -> int:
    return int(block.get_gpio_attr(bank, "READBACK", mboard))
