#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: DSES Radio Astronomy Workbench
# Description: Radio-astronomy workbench for the Ettus USRP B210 and other
# SDRs -- live spectrum/waterfall, pulsar observation planning, science-
# format recording, PRESTO folding, HI drift-scan campaigns, and a B210
# pulsar simulator/self test.
# GNU Radio version: 3.10.12.0
#
# Renamed from "DSES Spectrum Analyzer" / dses_spectrum_analyzer.py in 1.4.0
# (2026-09-08): the old name stopped describing the program. The old module
# name survives only as a launch shim (dses_spectrum_analyzer.py -> here).
# Originally generated from dses_spectrum_analyzer.grc (the .grc was never
# renamed) and then hand-polished. Migrated off gnuradio.qtgui (PyQt5) to a
# native PySide6 + PyQtGraph display so the whole app runs on one Qt
# binding (Qt6), then extended past B210-only to a multi-radio app via
# SoapySDR.

import os
import sys
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"


# --- Keep PyQt5 (Qt5) OUT of this process. -------------------------------
# This app is PySide6/Qt6-only, but `from gnuradio import uhd` optionally
# imports a PyQt5 widget (ReplayMsgPushButton, unused here), which loads the
# whole Qt5 stack NEXT TO Qt6. Two Qts in one process is why macOS printed
# duplicate-ObjC-class warnings at every launch, and it can be fatal: Qt6's
# QGuiApplication.setOverrideCursor(WaitCursor) built its cursor bitmap via a
# QImage call that resolved into Qt5's copy -> SIGSEGV in QImage::format()
# (crash report 2026-08-05, opening the pulsar planner). gnuradio.uhd guards
# that import with `except ModuleNotFoundError`, so raising exactly that from
# a meta-path hook makes gnuradio skip its PyQt5 extras cleanly — UHD itself
# is untouched. Must be installed before ANY gnuradio/Qt import.
class _PyQt5Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "PyQt5" or name.startswith("PyQt5."):
            raise ModuleNotFoundError(
                f"{name} is blocked: this app is PySide6-only "
                "(see _PyQt5Blocker at the top of dses_workbench.py)")
        return None


sys.meta_path.insert(0, _PyQt5Blocker())

# Quiet known-benign chatter from underlying native libraries before any of
# them get imported. See the README; users can override any of these from
# the shell because we use setdefault rather than overwriting.
os.environ.setdefault("LIBUSB_DEBUG", "0")
os.environ.setdefault("LIBUSB_LOG_LEVEL", "0")
os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "warning")
os.environ.setdefault("SOAPY_SDR_LOG_LEVEL", "WARNING")

# --- Windows DLL setup so SoapySDR device modules load ---
# Must run BEFORE any gnuradio.soapy / SoapySDR import. SoapySDR's support
# modules (rtlsdr, hackrf, airspy, bladerf, lime, plutosdr, audio, …) live in
# <prefix>\Library\lib\SoapySDR\modules0.8 and depend on vendor DLLs in
# <prefix>\Library\bin. When the app is launched via python.exe rather than an
# activated conda shell, that directory isn't on the DLL search path and every
# module fails with "LoadLibrary() failed: The specified module could not be
# found". The SDRplay module additionally needs the SDRplay API directory.
if os.name == "nt":
    _lib_bin = os.path.join(sys.prefix, "Library", "bin")
    _dll_dirs = [d for d in (_lib_bin, r"C:\Program Files\SDRplay\API\x64")
                 if os.path.isdir(d)]
    for _d in _dll_dirs:
        try:
            os.add_dll_directory(_d)
        except (AttributeError, OSError):
            pass
    if _dll_dirs:
        os.environ["PATH"] = os.pathsep.join(_dll_dirs) + os.pathsep + os.environ.get("PATH", "")
    # Windows searches System32 before PATH, and some machines have an older
    # C:\Windows\System32\libusb-1.0.dll (installed by Zadig / other SDR tools)
    # that lacks symbols the rtlsdr/hackrf/airspy/bladerf modules need — they
    # then fail with "The specified procedure could not be found". Pre-load
    # Radioconda's own libusb-1.0.dll by full path so it's already resident and
    # wins the base-name match for every module loaded afterward.
    _libusb = os.path.join(_lib_bin, "libusb-1.0.dll")
    if os.path.isfile(_libusb):
        try:
            import ctypes
            ctypes.CDLL(_libusb)
        except OSError:
            pass

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QObject, Signal, Slot, QTimer
import pyqtgraph as pg
pg.setConfigOption('imageAxisOrder', 'row-major')
pg.setConfigOption('background', 'k')
pg.setConfigOption('foreground', 'w')

# Scrollbars: keep the tidy "as needed" policy everywhere. On macOS the native
# scrollbar is a translucent overlay that fades out after a scroll gesture and
# can't be grabbed (and "always on" only keeps the empty track visible, not the
# thumb). Applying a stylesheet to the scrollbar forces Qt's non-native
# rendering — a solid, persistent, grabbable bar that appears whenever content
# overflows. Empty stylesheet on other platforms keeps their native look.
_VBAR_POLICY = Qt.ScrollBarAsNeeded
_SCROLLBAR_QSS = ("""
QScrollBar:vertical { width: 14px; background: palette(mid); margin: 0px; }
QScrollBar::handle:vertical { background: palette(dark); min-height: 28px;
    border-radius: 6px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent; }
""" if sys.platform == "darwin" else "")

import numpy as np
from scipy.signal import windows as scipy_windows
from scipy.ndimage import median_filter
from scipy import fft as scipy_fft   # ~2x numpy.fft for batch transforms + workers=

from gnuradio import blocks
from gnuradio import eng_notation
from gnuradio import gr
from gnuradio import uhd

# Shared, OS-neutral SIGPROC filterbank core (same module the offline
# iq_to_fil converter and PulsarLab's engine use) — provides the live .fil
# recording sink. Lives next to this script.
import sigproc_fil
import ezra_txt
from dses_radio import (_addr_to_dict, DRIVER_UHD_B200, find_b200_uhd,  # noqa: F401
                        RadioSource, UhdB200Source, RealtimeMode,
                        ensure_uhd_images)
ensure_uhd_images()   # dev envs have UHD but no firmware images

# In-app upgrade helper (download/verify/extract/install). OS-neutral core; the
# Qt install dialog + per-OS shortcut/relaunch live here in the app.
import updater
import shutil
import subprocess

from argparse import ArgumentParser
import calendar
from datetime import datetime
from pathlib import Path
import configparser
import ctypes
import gc
import re
import signal
import threading
import time


# === App metadata ===
APP_NAME        = "DSES Radio Astronomy Workbench"
APP_VERSION     = "1.6.0"
APP_AUTHOR      = "Richard M Hambly (K0GD)"
APP_AUTHOR_EMAIL = "k0gd@cnssys.com"
APP_COPYRIGHT   = "Copyright © 2026 Richard M Hambly (K0GD)"
APP_LICENSE     = "GPL-3.0-or-later"
APP_DESCRIPTION = ("Radio-astronomy workbench for the Ettus USRP B210 and other "
                   "SDRs: live spectrum and waterfall, pulsar observation "
                   "planning, science-format recording, PRESTO folding, HI "
                   "drift-scan campaigns, and a built-in pulsar simulator. "
                   "Formerly the DSES Spectrum Analyzer (through 1.3.4).")
# Standalone install/upgrade guide PDF — lives next to the release zips on the
# distribution server. The update dialog points users here *before* the zip.
GUIDE_PDF_BASENAME = "DSES_Radio_Astronomy_Workbench_Installation.pdf"


# === Window function table (drop-in for gnuradio.fft.window) ===
WINDOWS = {
    "rectangular":     lambda n: np.ones(n, dtype=np.float64),
    "hamming":         scipy_windows.hamming,
    "hann":            scipy_windows.hann,
    "blackman":        scipy_windows.blackman,
    "blackman-harris": scipy_windows.blackmanharris,
    "flat-top":        scipy_windows.flattop,
}
# Larger sizes give finer RBW (= sample_rate / N) and a lower per-bin noise
# floor (~3 dB per doubling) — the real lever for weak-signal / deep-space work.
# The max here MUST be <= CHUNK_SIZE (the flowgraph vector the sample sink holds)
# so latest(n) always has n *contiguous* samples for a clean FFT. The plot
# downsamples the long curves so drawing stays cheap.
FFT_SIZES = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]


def _pretty_rate(hz: float) -> str:
    """Format a sample rate in Hz as a short human-readable string for the
    sample-rate combo. 1_000_000 -> '1 MHz', 2_048_000 -> '2.048 MHz',
    250_000 -> '250 kHz'."""
    mhz = hz / 1e6
    if mhz >= 1:
        if abs(mhz - round(mhz)) < 1e-6:
            return f"{int(round(mhz))} MHz"
        return f"{mhz:g} MHz"
    return f"{hz / 1e3:g} kHz"


# === User-editable settings (INI-backed, persisted across runs) ===

def _default_recording_dir():
    return str(Path.home() / "Documents" / "DSES_SA_Recordings")


DEFAULTS = {
    'tuning': {
        'preset_hz':       408e6,
        'coarse_hz':       0.0,
        'fine_hz':         0.0,
        'manual_hz':       100e6,
        # LO offset (Hz): park the hardware LO away from the displayed
        # center so the zero-IF DC artefact lands off-target (|offset| >
        # samp_rate/2 pushes it out of the recorded band entirely). Added
        # after the 2026-08 drift scan put the B210's DC bin 1.3 km/s from
        # the HI rest frequency. 0 = classic behavior.
        'lo_offset_hz':    0.0,
    },
    'rx': {
        'gain_db':         40.0,
        'samp_rate_hz':    20e6,
        # Saved (driver, serial) identifies the radio to open at startup.
        # Driver: 'uhd_b200' for B200/B210, or a Soapy driver name
        # ('sdrplay', 'rtlsdr', 'hackrf', 'airspy', 'bladerf', …).
        # Serial: 'auto' picks the first found (silent if there's only one).
        # Both are updated by the device-picker dialog when the user
        # chooses a different radio.
        'device_driver':   'uhd_b200',
        'device_serial':   'auto',
        # RF input/antenna port. Blank = use the radio's default. Only
        # meaningful on radios with more than one port (B210 TX/RX vs RX2,
        # RSPduo tuner 1 vs 2, RSPdx antenna A/B/C). Saved when the user
        # picks a port from the sidebar's Antenna combo.
        'antenna':         '',
    },
    'recording': {
        # Set at runtime from _default_recording_dir() if blank.
        'directory':       '',
        # Recording format: 'iq' = raw I/Q to SigMF (the original mode, kept);
        # 'fil' = live-channelized SIGPROC filterbank (.fil), written straight
        # to disk so the giant raw I/Q is never stored.
        'format':          'iq',
        # .fil mode geometry: polyphase/FFT channel count and the number of
        # power frames integrated per output sample (tsamp = nchans*integrate/
        # samp_rate). Defaults match the validated lab L-band geometry.
        'fil_nchans':      2048,
        'fil_integrate':   1,
        # Optional source/pulsar name folded into the output filename AND written
        # into the SIGPROC .fil header (source_name). Blank = timestamp-only name.
        'source_name':     '',
        # Optional target recording length; blank = record until stopped. Accepts
        # minutes ("30") or H:MM / HH:MM:SS ("1:30"); auto-stops when reached.
        'rec_duration':    '',
        # Optional scheduled START (2026-09-26); blank = start the moment Record
        # is set. A clock time "HH:MM[:SS]" (its next occurrence), a delay "+30"
        # / "+1:30", or a date "YYYY-MM-DD HH:MM[:SS]", read as UTC or local
        # time per rec_start_tz. Record then ARMS: the counter counts down to
        # the start and the recording starts itself.
        'rec_start':       '',
        'rec_start_tz':    'UTC',
        # Run the canned PRESTO pipeline (readfile + rfifind + catalog fold
        # -> self-contained PDF) automatically when a .fil recording stops.
        'analyze_when_done': True,
        # Manual fold override for a known-period source with no catalog entry
        # (e.g. the lab pulsar simulator): period in milliseconds forces a full
        # prepfold -topo -p; DM in pc/cm^3 (blank/0 = undispersed). Blank period
        # = fold by catalog Source name, or data-health checks only.
        'fold_period_ms':  '',
        'fold_dm':         '',
        # --- Drift-scan (ezRA .txt) format geometry. Defaults mirror the
        # dish's own ezCol command line (Nov-2025 campaign): 4096-bin FFT,
        # 31e3 integrations (~12.7 s/row at 10 MS/s), central 80% of the
        # band kept (trims the anti-alias skirts). Pointing defaults to the
        # dish's drift-scan park position, az 0 / el 87 (Rick, 2026-08-02);
        # user edits persist here.
        'ez_fft_bins':      4096,
        'ez_integ_frames':  31000,
        'ez_keep_fraction': 0.8,
        'ez_prefix':        'DSES',   # ezCol filename prefix: <prefix>YYMMDD_HH.txt
        'ez_az_deg':        0.0,
        'ez_el_deg':        87.0,
    },
    # Observing-site identity written into ezRA drift-scan files (and, later,
    # used by the pulsar visibility planner). Defaults = DSES Haswell 60-ft,
    # from the dish's ezCol command line.
    'site': {
        'lat_deg':  38.3808,
        'lon_deg':  -103.156,
        'amsl':     4400.0,
        'name':     'DSES',
        # Minimum elevation the dish can usefully observe at; the visibility
        # planner treats anything below this as "not up". 2° per the field
        # crew (Rick, 2026-09-07): the 60-ft tracks nearly to the horizon.
        'el_mask_deg': 2.0,
        # On-axis system equivalent flux density (Jy) for the planner's
        # minimum-recording-duration estimate. Default = the 60-ft measured
        # on Cygnus A at 1420 MHz (Sept 2026 calibration move). The single
        # number is used at every band, so low-band estimates read
        # optimistic (sky temperature rises steeply below ~1 GHz).
        'sefd_jy': 4000.0,
    },
    'spectrum': {
        'fft_size':        1024,
        'window':          'blackman-harris',
        'normalize_window': False,
        'dc_suppress':     True,   # hide the zero-IF center-DC spike by default
        'y_unit':          'relative',  # 'relative' | 'dbfs' | 'dbm'
        # dBm calibration is stored PER DEVICE in the [calibration] section
        # (keyed by driver+serial), not here — radios differ in absolute scale.
        'avg_alpha':       0.1,     # ~19-frame integration by default (was 1.0 = none)
        'power_avg':       True,    # linear-power (radiometric, unbiased). False = legacy
                                    # dB "video" averaging (read ~2.5 dB low for noise).
                                    # v1.1.7: intra-tick Welch averaging is inherently
                                    # linear, so linear is now the honest default; note
                                    # the noise floor reads ~2.5 dB higher than <=1.1.6.
        'smooth_bins':     0,       # spectral smoothing off (harms narrow lines)
        'baseline_mode':   'off',   # 'off' | 'reference' (ON/OFF) | 'flatten' (median)
        'flatten_bins':    151,     # median window for the Flatten bandpass estimate
        'max_hold':        False,
        'min_hold':        False,
        'hold_detector':   'peak',  # 'peak' = per-FFT-block extremes (transient
                                    # catcher; sits ~+10/-10*log10(N) dB from the
                                    # mean on noise); 'average' = per-frame Welch
                                    # means (holds stay near the baseline)
        'y_min':           -140.0,
        'y_max':           10.0,
        # Spectrum X (frequency) view, remembered across runs. 0/0 = unset ->
        # use the full span for the current tuning.
        'x_min':           0.0,
        'x_max':           0.0,
        'linear_scale':    False,
        'grid':            True,
        'axis_labels':     True,
        'dark_background': True,
        # Trace styling is stored per-background so the user can dial it in
        # independently for each — e.g. bright sky-blue on black vs. navy on
        # white. The active set follows `dark_background`.
        'trace_color_dark':  '#00bfff',  # deepskyblue
        'trace_width_dark':  1,
        'trace_alpha_dark':  1.0,
        'trace_label_dark':  'Data 0',
        'trace_color_light': '#003f7f',  # dark navy, legible on white
        'trace_width_light': 1,
        'trace_alpha_light': 1.0,
        'trace_label_light': 'Data 0',
    },
    'waterfall': {
        'intensity_min':   -140.0,
        'intensity_max':   10.0,
        # Per-background colormap (active follows `dark_background`).
        'colormap_dark':   'viridis',
        'colormap_light':  'inferno',
        'axis_labels':     True,
        'grid':            False,
        'dark_background': True,
        'rows':            256,
    },
    'ui': {
        'control_panels_visible': True,
    },
    'sweep': {
        # Swept (stepped) spectrum-analyzer mode: tune the radio across a
        # range start->stop in chunks of step_hz, FFT each chunk, stitch into
        # one wide trace. Useful for RFI surveys spanning more than the
        # radio's instantaneous bandwidth. enabled persists the mode toggle;
        # step_hz=0 means "auto from sample rate" (~80%).
        'enabled':         False,
        'start_hz':        100e6,
        'stop_hz':         1000e6,
        'step_hz':         0.0,
        'settle_ms':       150,
    },
    'window': {
        # Main-window position/size as plain integers (human-readable and
        # corruption-proof, unlike an opaque saveGeometry() blob). width/height
        # of 0 means "not saved yet" → open at the default size.
        'x':      0,
        'y':      0,
        'width':  0,
        'height': 0,
    },
    'updates': {
        # Master switch — set to false to disable the auto-check entirely.
        'auto_check':           True,
        # URL of the manifest.json describing the latest release. See
        # Installing.md "Release workflow" for the expected JSON format.
        # Moved from .../b210_sa/ with the 1.4.0 rename (the old folder was
        # named for one radio; the program drives many). The old URL stays
        # online as a pointer manifest and is migrated out of existing
        # settings files by Settings._migrate_legacy_keys.
        'manifest_url':         'https://gpstime.com/sw_distribution/dses-workbench/manifest.json',
        # ISO-8601 timestamp of the last successful check (set by the app).
        # Used to debounce repeated launches.
        'last_check_iso':       '',
        # Don't open the notification dialog more often than this many
        # hours regardless of how many times the app is relaunched.
        'check_interval_hours': 24,
        # If the user clicks "Skip this version" on the notification, the
        # version they skipped is stored here so we don't nag them again
        # until a newer one appears.
        'dismissed_version':    '',
    },
}


# Manifest URLs that earlier releases wrote into settings.ini as their default.
# A stored value equal to one of these is the shipped default, not a user
# choice, and is replaced by the current default on load.
_LEGACY_MANIFEST_URLS = (
    'https://gpstime.com/sw_distribution/b210_sa/manifest.json',
)


def _settings_path():
    """Cross-platform config location. Matches launcher.ps1's
    %APPDATA%\\DSES_Analyzer on Windows; main() sets QApplication app name
    to 'DSES_Analyzer' with no org, so AppDataLocation resolves to:
        Windows: %APPDATA%/DSES_Analyzer/
        macOS:   ~/Library/Application Support/DSES_Analyzer/
        Linux:   ~/.local/share/DSES_Analyzer/
    """
    base = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.AppDataLocation)
    if not base:
        base = str(Path.home() / ".config" / "DSES_Analyzer")
    cfg_dir = Path(base)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "settings.ini"


class Settings:
    """INI-backed settings store. Missing keys fall back to DEFAULTS, so the
    file is always usable even if the user deletes individual lines. Save
    rewrites the whole file with a header comment explaining what it is."""

    def __init__(self, path=None):
        self.path = Path(path) if path else _settings_path()
        self._cp = configparser.ConfigParser()
        # Preserve case of keys (configparser lowercases by default).
        self._cp.optionxform = lambda optionstr: optionstr
        if self.path.exists():
            try:
                # utf-8-sig transparently strips a UTF-8 BOM if present —
                # Windows editors (Notepad, PowerShell Set-Content -Encoding
                # UTF8) often add one, and configparser would otherwise die
                # with "File contains no section headers" on the BOM'd line 1.
                self._cp.read(self.path, encoding="utf-8-sig")
            except (configparser.Error, OSError) as exc:
                print(f"Settings: failed to read {self.path}: {exc}", file=sys.stderr)
        self._migrate_legacy_keys()
        self._fill_missing_with_defaults()
        # Materialise defaults for the recording directory on first run.
        if not self._cp.get('recording', 'directory'):
            self._cp.set('recording', 'directory', _default_recording_dir())
        # Write the file back so the user has a complete template to edit.
        try:
            self.save()
        except OSError as exc:
            print(f"Settings: failed to write {self.path}: {exc}", file=sys.stderr)

    def _migrate_legacy_keys(self):
        """Rename keys that changed meaning across versions. Runs before
        _fill_missing_with_defaults so the user's existing value seeds the
        new key instead of being replaced by the built-in default."""
        # v1.0.0 → v1.1.0: trace styling and colormap became per-background.
        # The pre-1.1.0 single value becomes the dark-background value.
        legacy = [
            ('spectrum',  'trace_color',  'trace_color_dark'),
            ('spectrum',  'trace_width',  'trace_width_dark'),
            ('spectrum',  'trace_alpha',  'trace_alpha_dark'),
            ('spectrum',  'trace_label',  'trace_label_dark'),
            ('waterfall', 'colormap',     'colormap_dark'),
        ]
        for section, old, new in legacy:
            if (self._cp.has_section(section)
                    and self._cp.has_option(section, old)
                    and not self._cp.has_option(section, new)):
                self._cp.set(section, new, self._cp.get(section, old))
                self._cp.remove_option(section, old)
        # Obsolete: window geometry was briefly stored as a base64
        # saveGeometry() blob; it's now plain x/y/width/height. Drop the dead
        # key so it doesn't linger in the file.
        if self._cp.has_section('window') and self._cp.has_option('window', 'geometry_b64'):
            self._cp.remove_option('window', 'geometry_b64')
        # v1.3.3 → next: the planner's default elevation mask dropped from
        # 20° to 2° (the 60-ft usefully observes nearly to the horizon —
        # field request, 2026-09-07). A saved value of exactly the old
        # default is almost certainly the shipped default, not a choice:
        # drop it so the new default fills in. Any other value is kept.
        if (self._cp.has_section('site')
                and self._cp.has_option('site', 'el_mask_deg')):
            try:
                if float(self._cp.get('site', 'el_mask_deg')) == 20.0:
                    self._cp.remove_option('site', 'el_mask_deg')
            except ValueError:
                pass
        # v1.3.4 -> 1.4.0 (product rename): the release channel moved from
        # sw_distribution/b210_sa/ to sw_distribution/dses-workbench/. Every
        # earlier install persisted the old URL in its settings.ini, so a
        # stored value equal to the legacy default is dropped here and the
        # new default fills in. A user-customized URL is left alone.
        if (self._cp.has_section('updates')
                and self._cp.has_option('updates', 'manifest_url')
                and self._cp.get('updates', 'manifest_url').strip()
                in _LEGACY_MANIFEST_URLS):
            self._cp.remove_option('updates', 'manifest_url')
        # v1.5.0 -> next: the L-band Tuning preset moved from the feed's
        # nominal 1422.000 MHz onto the hydrogen line (1420.406 MHz) after
        # a home station recorded a 2 MS/s "HI" run whose band held no
        # hydrogen (Ray, 2026-09-12; the 1.6 MHz shift is immaterial to
        # pulsar folds). A stored preset equal to the old value MUST be
        # remapped: startup looks the saved value up in the options list,
        # and an unmatched value raises in the radio-button callback.
        if (self._cp.has_section('tuning')
                and self._cp.has_option('tuning', 'preset_hz')):
            try:
                if float(self._cp.get('tuning', 'preset_hz')) == 1422e6:
                    self._cp.set('tuning', 'preset_hz', '1420406000.0')
            except ValueError:
                pass

    def _fill_missing_with_defaults(self):
        for section, kvs in DEFAULTS.items():
            if not self._cp.has_section(section):
                self._cp.add_section(section)
            for k, v in kvs.items():
                if not self._cp.has_option(section, k):
                    self._cp.set(section, k, self._stringify(v))

    @staticmethod
    def _stringify(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        return str(v)

    def get_str(self, section, key):
        return self._cp.get(section, key)

    def get_int(self, section, key):
        return int(float(self._cp.get(section, key)))

    def get_float(self, section, key):
        return float(self._cp.get(section, key))

    def get_bool(self, section, key):
        return self._cp.get(section, key).strip().lower() in ('true', 'yes', '1', 'on')

    def get_float_or(self, section, key, default):
        """Read a float from a possibly-absent section/key (e.g. the dynamic
        per-device [calibration] section, which isn't in DEFAULTS)."""
        if self._cp.has_option(section, key):
            try:
                return float(self._cp.get(section, key))
            except ValueError:
                return default
        return default

    def set(self, section, key, value):
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, self._stringify(value))

    def save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(f"# {APP_NAME} v{APP_VERSION} settings\n")
            f.write(f"# File: {self.path}\n")
            f.write("#\n")
            f.write("# This file is plain text and may be edited with any editor while the\n")
            f.write("# program is closed. Comments begin with '#' or ';'. Missing keys are\n")
            f.write("# filled in from built-in defaults on next launch. To wipe everything\n")
            f.write("# back to defaults, use 'Restore Defaults' in Help → About.\n\n")
            self._cp.write(f)

    def reset_to_defaults(self):
        self._cp = configparser.ConfigParser()
        self._cp.optionxform = lambda optionstr: optionstr
        self._fill_missing_with_defaults()
        self._cp.set('recording', 'directory', _default_recording_dir())
        self.save()


# The flowgraph groups samples into CHUNK_SIZE-sample vectors (stream_to_vector)
# and the sample sink queues them for the display integrator. It MUST be >= the
# largest FFT in FFT_SIZES so a full-length FFT gets that many *contiguous*
# samples, and every FFT_SIZES entry divides it exactly, so a queued chunk
# reshapes into whole FFT blocks with no remainder.
CHUNK_SIZE = 65536  # must be >= max(FFT_SIZES)

# Bound on the sink's pending-chunk queue (memory/backlog guard). At the
# highest supported rates the flowgraph delivers ~300-400 chunks/s and the
# display drains ~10x/s, so ~40 chunks accumulate per tick; 96 gives ample
# headroom (96 x 512 KB = 48 MB worst case) while a stalled GUI can't hoard
# unbounded sample memory. Overflow drops the OLDEST chunks (display favors
# fresh data) and is counted, so the integrator can report true coverage.
MAX_PENDING_CHUNKS = 96


class SampleBufferSink(gr.sync_block):
    """Queues arriving CHUNK_SIZE-sample complex-baseband vectors for the
    display integrator (drain()), and keeps the newest one for latest().
    Fed by stream_to_vector (+ keep_one_in_n at extreme rates) so this
    pure-Python sync_block sees whole vectors, not raw samples — Python
    can't keep up with 20 MS/s sample-by-sample, but ~300 vector
    callbacks/s is cheap. Sensitivity fix (v1.1.7): the queue lets the
    display integrate (Welch-average) every sample the flowgraph delivers
    instead of one FFT-frame snapshot per screen tick."""

    def __init__(self, chunk_size=CHUNK_SIZE):
        gr.sync_block.__init__(
            self,
            name="sample_buffer_sink",
            in_sig=[(np.complex64, chunk_size)],
            out_sig=None,
        )
        self._lock = threading.Lock()
        self._chunk_size = int(chunk_size)
        self._buf = np.zeros(self._chunk_size, dtype=np.complex64)
        self._filled = False
        self._pending = []        # queued chunk copies, oldest first
        self._dropped = 0         # chunks discarded since the last drain()
        self._blank = False       # discard the next non-empty drain (retune barrier)

    @property
    def chunk_size(self):
        return self._chunk_size

    def set_capacity(self, _n):
        # Capacity is fixed at chunk_size (set at flowgraph construction).
        # Kept as a no-op so SpectrumProcessor.set_fft_size still calls it
        # without needing flowgraph relock.
        return

    def work(self, input_items, output_items):
        chunks = input_items[0]
        n = len(chunks)
        if n > 0:
            with self._lock:
                for c in chunks:
                    # Copy: GNU Radio reuses its buffers after work() returns.
                    if len(self._pending) >= MAX_PENDING_CHUNKS:
                        self._pending.pop(0)
                        self._dropped += 1
                    self._pending.append(np.array(c, dtype=np.complex64))
                self._buf[:] = chunks[-1]
                self._filled = True
        return n

    def latest(self, n):
        """Return the most recent n samples (chronological order), or None
        if no chunk has arrived yet or n exceeds chunk_size."""
        n = int(n)
        with self._lock:
            if not self._filled or n > self._chunk_size or n <= 0:
                return None
            return self._buf[-n:].copy()

    def drain(self):
        """Take every queued chunk (oldest first) plus the count of chunks
        dropped to the queue bound since the previous drain. After a flush(),
        the first NON-EMPTY drain is discarded (returned as empty): those
        chunks can contain pre-retune samples that were already in flight in
        the GR pipeline / partial stream_to_vector fill / LO settle when the
        flush ran, which a Qt-side flush cannot see."""
        with self._lock:
            pending, self._pending = self._pending, []
            dropped, self._dropped = self._dropped, 0
            if self._blank and pending:
                self._blank = False
                return [], dropped
        return pending, dropped

    def flush(self):
        """Retune barrier: discard queued chunks, invalidate latest(), and
        arm drain-blanking so in-flight stale samples are dropped too. Call
        on retune / rate change so stale-frequency samples never enter the
        new average."""
        with self._lock:
            self._pending = []
            self._dropped = 0
            self._filled = False
            self._blank = True


def _fmt_hz(hz: float) -> str:
    """Human-readable RF frequency for labels/tooltips: 1e3 -> '1 kHz',
    2e9 -> '2 GHz', 1.766e9 -> '1.766 GHz'."""
    hz = float(hz)
    if hz >= 1e9:
        return f"{hz / 1e9:g} GHz"
    if hz >= 1e6:
        return f"{hz / 1e6:g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:g} kHz"
    return f"{hz:g} Hz"


class SpectrumProcessor(QObject):
    """Drains a SampleBufferSink on a QTimer, Welch-averages a windowed FFT
    over EVERY block that arrived since the last tick (the v1.1.7 display
    sensitivity fix — previously one FFT-frame snapshot per tick, ~0.3% of
    the stream), applies exponential averaging plus optional max/min hold,
    and emits `frame_ready(avg_db, max_db, min_db)` for the plot widgets.

    Numerics: per-block transforms run in single precision (complex64 — the
    B210's 12-bit samples leave ~5 orders of magnitude of headroom) while
    the across-blocks power average accumulates in float64: single-precision
    transform, double-precision accumulate. An adaptive stride sheds blocks
    if the per-tick math overruns its time budget (slow machines / huge
    FFTs), degrading coverage gracefully instead of freezing the GUI."""

    frame_ready = Signal(object, object, object)  # avg_db, max_db|None, min_db|None

    def __init__(self, sink, fft_size=1024, window_name="blackman-harris",
                 update_hz=10.0, normalize=False, dc_suppress=True,
                 unit='relative', gain_db=0.0, cal_offset_db=0.0,
                 power_avg=False, smooth_bins=0,
                 baseline_mode='off', flatten_bins=151, parent=None):
        super().__init__(parent)
        self._sink = sink
        self._fft_size = int(fft_size)
        self._window_name = window_name
        self._window = WINDOWS[window_name](self._fft_size).astype(np.float64)
        self._window32 = self._window.astype(np.float32)   # batch-path window
        # Per-tick CPU budget: the block count is bounded BEFORE the math via
        # a learned per-block cost estimate, so even a full backlog (event
        # loop stalled by a dialog/drag) can never freeze the GUI. Blocks are
        # strided (not truncated) so the average still spans the whole tick.
        self._tick_budget_frac = 0.4                    # fraction of the interval
        self._budget_frac_normal = 0.4
        self._budget_frac_recording = 0.12  # yield GIL/CPU to recording sinks
        self._sec_per_block = 2e-8 * self._fft_size     # learned each tick (EMA)
        self._stride = 1               # diagnostics: last tick's stride
        self._last_blocks_used = 0     # diagnostics: blocks in last average
        self._last_dropped = 0         # diagnostics: chunks lost to backlog
        self._normalize = bool(normalize)
        self._dc_suppress = bool(dc_suppress)
        self._unit = unit if unit in ('relative', 'dbfs', 'dbm') else 'relative'
        self._gain_db = float(gain_db)
        self._cal_offset_db = float(cal_offset_db)
        # Averaging domain: linear power (radiometrically correct, unbiased) or
        # dB/log ("video" averaging, reads ~2.5 dB low for noise). Spectral
        # smoothing: display-only boxcar over N bins (0 = off).
        self._power_avg = bool(power_avg)
        self._smooth_bins = max(0, int(smooth_bins))
        # Hold detector: True = per-FFT-block peak/min (true transient
        # detector), False = holds track the per-tick Welch mean.
        self._hold_peak = True
        # Baseline / bandpass removal (display-only): 'off', 'reference'
        # (subtract a stored OFF-source spectrum → ON/OFF), or 'flatten'
        # (subtract a wide-median bandpass estimate). Flattens the floor to ~0.
        self._baseline_mode = baseline_mode if baseline_mode in ('off', 'reference', 'flatten') else 'off'
        self._reference = None          # stored OFF-source base-dB spectrum
        self._flatten_bins = max(3, int(flatten_bins))
        self._recompute_window_norm()
        self._avg_alpha = 1.0
        self._max_on = False
        self._min_on = False
        # Averaging state holds POWER when _power_avg else dB. Converted to dB
        # (and smoothed) only at emit time.
        self._avg = None
        self._max = None
        self._min = None
        self._sink.set_capacity(max(8192, self._fft_size * 2))

        self._timer = QTimer(self)
        self._timer.setInterval(max(20, int(1000.0 / update_hz)))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _recompute_window_norm(self):
        """Cache the window's coherent-gain sum (for dBFS scaling) and its
        noise-equivalent bandwidth in bins (to reproduce the legacy 'relative'
        reference as an offset from dBFS)."""
        w = self._window
        wsum = float(np.sum(w))
        self._wsum = wsum if wsum else 1.0
        wpow = float(np.sum(w * w)) or 1.0
        self._enbw = (self._fft_size * wpow) / (self._wsum * self._wsum)

    @Slot(int)
    def set_fft_size(self, n):
        self._fft_size = int(n)
        self._window = WINDOWS[self._window_name](self._fft_size).astype(np.float64)
        self._window32 = self._window.astype(np.float32)
        self._sink.set_capacity(max(8192, self._fft_size * 2))
        self._recompute_window_norm()
        self._sec_per_block = 2e-8 * self._fft_size   # re-learn cost at this size
        self._avg = self._max = self._min = None
        self._reference = None          # different bin count invalidates the OFF ref

    @Slot(str)
    def set_window(self, name):
        if name not in WINDOWS:
            return
        self._window_name = name
        self._window = WINDOWS[name](self._fft_size).astype(np.float64)
        self._window32 = self._window.astype(np.float32)
        self._recompute_window_norm()

    @Slot(float)
    def set_average_alpha(self, alpha):
        """alpha in (0, 1]. 1.0 = no averaging, smaller = more smoothing."""
        self._avg_alpha = float(np.clip(alpha, 1e-3, 1.0))

    @Slot(bool)
    def set_max_hold(self, on):
        self._max_on = bool(on)
        if not on:
            self._max = None

    @Slot(bool)
    def set_min_hold(self, on):
        self._min_on = bool(on)
        if not on:
            self._min = None

    @Slot()
    def reset_max_hold(self):
        self._max = None

    @Slot()
    def reset_min_hold(self):
        self._min = None

    @Slot(bool)
    def set_power_avg(self, on):
        """Average in linear power (True, unbiased/radiometric) or dB (False,
        legacy). Switching resets the average since the state domain changes."""
        on = bool(on)
        if on != self._power_avg:
            self._power_avg = on
            self._avg = self._max = self._min = None

    @Slot(int)
    def set_smooth_bins(self, n):
        self._smooth_bins = max(0, int(n))

    @Slot(bool)
    def set_recording_active(self, on):
        """While ANY recording runs, shrink the display's per-tick CPU
        budget: the Welch math's GIL time otherwise starves the Python
        recording sinks on the GNU Radio threads and the radio overflows
        (observed live at 16 MS/s, 2026-08-02). The adaptive stride sheds
        display coverage automatically; the recording gets the cycles."""
        self._tick_budget_frac = (self._budget_frac_recording if on
                                  else self._budget_frac_normal)

    @Slot(str)
    def set_hold_detector(self, mode):
        """'peak' = holds track per-FFT-block extremes (transient catcher;
        on noise they sit ~+10*log10(ln N) / -10*log10(N) dB from the mean);
        'average' = holds track the per-tick Welch mean and stay near the
        baseline. Switching resets the holds — the accumulated extremes
        change meaning between modes."""
        peak = (mode != 'average')
        if peak != self._hold_peak:
            self._hold_peak = peak
            self._max = self._min = None

    @Slot(str)
    def set_baseline_mode(self, mode):
        if mode in ('off', 'reference', 'flatten'):
            self._baseline_mode = mode

    @Slot(int)
    def set_flatten_bins(self, n):
        self._flatten_bins = max(3, int(n))

    @Slot()
    def store_reference(self):
        """Capture the current averaged spectrum as the OFF-source reference for
        ON/OFF display. Store it in the base-dB domain (pre-unit-offset) so it
        cancels the bandpass regardless of the display unit."""
        if self._avg is not None:
            self._reference = self._base_db(self._avg)

    @Slot()
    def clear_reference(self):
        self._reference = None

    @Slot()
    def reset_averaging(self):
        """Drop the running average + holds — call on any retune / sample-rate
        change so a long integration doesn't smear across the new frequency axis.
        Also flushes the sink's pending-chunk queue: samples captured at the OLD
        frequency must not be Welch-averaged into the new one (matters for Sweep
        stepping, which retunes every few hundred ms)."""
        self._avg = self._max = self._min = None
        flush = getattr(self._sink, "flush", None)
        if flush is not None:
            flush()

    @Slot(bool)
    def set_window_normalized(self, on):
        self._normalize = bool(on)

    @Slot(bool)
    def set_dc_suppress(self, on):
        self._dc_suppress = bool(on)

    @Slot(str)
    def set_unit(self, unit):
        if unit in ('relative', 'dbfs', 'dbm'):
            self._unit = unit

    @Slot(float)
    def set_gain_db(self, g):
        """Current RX front-end gain (dB), used to keep dBm gain-independent."""
        self._gain_db = float(g)

    @Slot(float)
    def set_cal_offset_db(self, c):
        self._cal_offset_db = float(c)

    def _display_offset(self):
        """Constant dB shift from the dBFS base to the selected display unit."""
        if self._unit == 'dbm':
            # dBm ≈ dBFS − front-end gain + a calibration constant (approx).
            return self._cal_offset_db - self._gain_db
        if self._unit == 'dbfs':
            return 0.0
        # 'relative' (legacy): full-scale reference when the window is
        # normalized, otherwise the noise-power reference used historically.
        return 0.0 if self._normalize else -10.0 * np.log10(self._enbw)

    def set_update_hz(self, hz):
        self._timer.setInterval(max(20, int(1000.0 / float(hz))))

    def _tick(self):
        n = self._fft_size
        t_start = time.perf_counter()
        chunks, dropped = self._sink.drain()
        self._last_dropped = dropped
        pk = mnm = None
        if chunks:
            # Welch path: every chunk splits into whole FFT blocks (each
            # FFT_SIZES entry divides CHUNK_SIZE), all averaged in linear
            # power into ONE low-variance frame for this tick.
            blocks = np.concatenate(chunks).reshape(-1, n)
            # Proactive budget: cap the block count BEFORE the math using
            # the learned per-block cost, so even a 96-chunk backlog cannot
            # stall the GUI thread.
            budget = self._timer.interval() * 1e-3 * self._tick_budget_frac
            cap = max(1, int(budget / max(self._sec_per_block, 1e-9)))
            stride = -(-blocks.shape[0] // cap)          # ceil division
            if stride > 1:
                blocks = blocks[::stride]
            self._stride = stride
            if self._dc_suppress:
                # Zero-IF radios (HackRF, RTL-SDR, …) put a DC-offset /
                # LO-leakage spike in the center bin. Per-block complex-mean
                # subtraction, display-only: the recorders tap the raw stream.
                blocks = blocks - blocks.mean(axis=1, keepdims=True)
            spec = scipy_fft.fft(blocks * self._window32, axis=1, workers=-1)
            pblocks = np.abs(spec) ** 2                  # per-block power, float32
            # Mean over blocks accumulates in float64 (single-precision
            # transform, double-precision accumulate).
            power = np.fft.fftshift(np.mean(pblocks, axis=0, dtype=np.float64))
            # In 'peak' detector mode the holds see every FFT block, so a
            # burst shorter than a tick registers at full amplitude instead
            # of being diluted by the tick mean. In 'average' mode pk/mnm
            # stay None and the holds track the per-tick Welch mean below.
            if self._max_on and self._hold_peak:
                pk = np.fft.fftshift(pblocks.max(axis=0)).astype(np.float64)
            if self._min_on and self._hold_peak:
                mnm = np.fft.fftshift(pblocks.min(axis=0)).astype(np.float64)
            nblk = blocks.shape[0]
            self._last_blocks_used = nblk
            elapsed = time.perf_counter() - t_start
            self._sec_per_block = (0.7 * self._sec_per_block
                                   + 0.3 * elapsed / max(1, nblk))
        else:
            # Fallback (flowgraph just started/stalled, or drain blanked
            # after a retune flush): legacy single-frame snapshot; returns
            # without painting if latest() was invalidated by the flush.
            samples = self._sink.latest(n)
            if samples is None:
                return
            if self._dc_suppress:
                samples = samples - samples.mean()
            spec = np.fft.fftshift(np.fft.fft(samples * self._window))
            power = np.abs(spec) ** 2
            self._last_blocks_used = 1
        # Base scale is dBFS: |X|² / (Σw)²  →  a full-scale tone reads 0 dBFS.
        scale = self._wsum * self._wsum
        power = power / scale
        if pk is not None:
            pk = pk / scale
        if mnm is not None:
            mnm = mnm / scale
        # Average in the chosen domain. Linear power is the unbiased estimator of
        # mean power (correct for radiometry / weak-signal integration); dB/log
        # averaging is ~28% jaggier and reads ~2.5 dB low for noise.
        val = power if self._power_avg else 10.0 * np.log10(power + 1e-20)

        a = self._avg_alpha
        if self._avg is None or len(self._avg) != n:
            self._avg = val.copy()
        else:
            self._avg = a * val + (1.0 - a) * self._avg
        if self._max_on:
            # Per-block peak when available (Welch path), else this tick's val.
            src = val if pk is None else (
                pk if self._power_avg else 10.0 * np.log10(pk + 1e-20))
            if self._max is None or len(self._max) != n:
                self._max = np.array(src, dtype=np.float64)
            else:
                np.maximum(self._max, src, out=self._max)
        if self._min_on:
            src = val if mnm is None else (
                mnm if self._power_avg else 10.0 * np.log10(mnm + 1e-20))
            if self._min is None or len(self._min) != n:
                self._min = np.array(src, dtype=np.float64)
            else:
                np.minimum(self._min, src, out=self._min)

        off = self._display_offset()
        self.frame_ready.emit(
            self._to_display(self._avg, off),
            self._to_display(self._max, off) if self._max_on else None,
            self._to_display(self._min, off) if self._min_on else None,
        )

    def _base_db(self, arr):
        """Base-dB (pre-unit-offset) view of an averaging-state array."""
        return 10.0 * np.log10(arr + 1e-20) if self._power_avg else np.asarray(arr)

    def _to_display(self, arr, off):
        """Convert an averaging-state array to the emitted dB trace: linear→dB
        (if power-averaging) → baseline/bandpass removal → spectral smoothing →
        the unit offset (a constant, so it commutes with the earlier steps)."""
        if arr is None:
            return None
        db = self._apply_baseline(self._base_db(arr).copy())
        return self._smooth(db) + off

    def _apply_baseline(self, db):
        """Remove the instrument bandpass so a weak line stands proud of ~0.
        'reference' subtracts a stored OFF-source spectrum (ON/OFF, dB); 'flatten'
        subtracts a wide running median (narrow lines survive a wide median)."""
        if self._baseline_mode == 'reference':
            ref = self._reference
            if ref is not None and len(ref) == len(db):
                return db - ref
            return db                       # no valid reference stored yet
        if self._baseline_mode == 'flatten':
            w = self._flatten_bins
            w = w if (w % 2 == 1) else w + 1
            w = min(w, len(db) - 1 if len(db) > 3 else 3)
            return db - median_filter(db, size=w, mode='reflect')
        return db

    def _smooth(self, db):
        """Optional frequency-domain (across-bin) smoothing of the display trace,
        done in the POWER domain — a proper wider-RBW boxcar. It conserves power,
        so levels stay physically meaningful and a narrow peak drops by only a
        predictable ~10·log10(width) instead of collapsing toward the floor the
        way a dB-domain average does. Reflect-padded so band edges aren't
        fabricated. Off (0/1) by default; it still broadens narrow lines, so use
        it for continuum, not narrow-line search."""
        k = self._smooth_bins
        if k <= 1:
            return db
        w = k if (k % 2 == 1) else k + 1        # odd window
        pad = w // 2
        kernel = np.ones(w, dtype=np.float64) / w
        power = np.power(10.0, db / 10.0)
        smoothed = np.convolve(np.pad(power, pad, mode='reflect'), kernel, mode='valid')
        return 10.0 * np.log10(smoothed + 1e-20)


def _make_pen(color, width, alpha):
    c = QtGui.QColor(color)
    c.setAlphaF(float(alpha))
    return pg.mkPen(c, width=width)


def _apply_plot_theme(plot, title, dark):
    """Switch a pg.PlotWidget between dark (black bg / white axes) and light
    (white bg / black axes). pg.setConfigOption('foreground', ...) only
    affects newly-created items, so we update axes and title per-plot."""
    bg = 'k' if dark else 'w'
    fg = 'w' if dark else 'k'
    plot.setBackground(bg)
    for name in ('left', 'bottom', 'right', 'top'):
        ax = plot.getAxis(name)
        if ax is None:
            continue
        ax.setPen(fg)
        ax.setTextPen(fg)
    plot.setTitle(title, color=fg)


class FftPlotWidget(QtWidgets.QWidget):
    """Spectrum (FFT) plot with a collapsible right-side control panel
    reproducing the qtgui freq_sink controls: FFT size, window, averaging,
    max/min hold, Y-axis (min/max/autoscale), grid, axis labels, trace
    color / width / alpha / label."""

    request_fft_size = Signal(int)
    request_window = Signal(str)
    request_average = Signal(float)
    request_max_hold = Signal(bool)
    request_min_hold = Signal(bool)
    request_hold_detector = Signal(str)  # 'peak' | 'average'
    request_reset_max = Signal()
    request_reset_min = Signal()
    request_window_normalized = Signal(bool)
    request_dc_suppress = Signal(bool)
    request_unit = Signal(str)           # 'relative' | 'dbfs' | 'dbm'
    request_cal_offset = Signal(float)   # dB, for dBm mode
    request_power_avg = Signal(bool)     # average in linear power vs dB
    request_smooth = Signal(int)         # spectral smoothing width in bins
    request_baseline_mode = Signal(str)  # 'off' | 'reference' | 'flatten'
    request_store_reference = Signal()   # capture OFF-source reference
    request_flatten_bins = Signal(int)   # median width for 'flatten'
    # Fires on every user-driven control change; args: (settings_key, value).
    # The main window listens once and persists to the INI.
    control_changed = Signal(str, object)

    def __init__(self, center_freq, samp_rate, parent=None):
        super().__init__(parent)
        self._center_freq = float(center_freq)
        self._samp_rate = float(samp_rate)
        self._y_min = -140.0
        self._y_max = 10.0
        # Remember/restore the plot view (X freq zoom + Y range), whether it is
        # changed via the Min/Max boxes, a mouse zoom/pan, or Autoscale.
        # Persistence stays off until the startup restore finishes, and is
        # debounced so a mouse drag doesn't spam the INI.
        self._persist_ranges_enabled = False
        self._range_restore_done = False
        self._saved_x = None            # (x0, x1) from settings, applied last
        self._x_view = None             # current X view captured from the viewbox
        self._range_save_timer = QtCore.QTimer(self)
        self._range_save_timer.setSingleShot(True)
        self._range_save_timer.setInterval(400)
        self._range_save_timer.timeout.connect(self._persist_ranges)
        self._linear = False      # False = dB (log) scale, True = linear amplitude
        self._y_unit = 'relative' # 'relative' | 'dbfs' | 'dbm' (dB-scale unit)
        self._labels_on = True    # axis-label visibility (mirrors the checkbox)
        self._dark_bg = True
        # Per-background trace styling. The Trace controls in the panel show
        # whichever set matches `_dark_bg`. Edits write to the active set;
        # toggling the background swaps them. apply_settings() will overwrite
        # both from the INI on startup.
        self._trace_dark = {
            'color': QtGui.QColor("deepskyblue"),
            'width': 1, 'alpha': 1.0, 'label': "Data 0",
        }
        self._trace_light = {
            'color': QtGui.QColor("#003f7f"),
            'width': 1, 'alpha': 1.0, 'label': "Data 0",
        }
        # Live values mirror the active slot; used by handlers + pen building.
        self._line_color = self._trace_dark['color']
        self._line_width = self._trace_dark['width']
        self._line_alpha = self._trace_dark['alpha']
        self._line_label = self._trace_dark['label']

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._plot = pg.PlotWidget()
        # Capture every X/Y view change (boxes, mouse zoom/pan, Autoscale) so it
        # can be remembered across runs. Handler is guarded until the panel's
        # spin boxes exist.
        self._plot.getPlotItem().getViewBox().sigRangeChanged.connect(
            self._on_view_range_changed)
        self._plot.setLabel('left', 'Relative Gain', units='dB')
        self._plot.setLabel('bottom', 'Frequency', units='Hz')
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setYRange(self._y_min, self._y_max)
        _apply_plot_theme(self._plot, self._plot_title(), self._dark_bg)
        self._curve = self._plot.plot(
            pen=_make_pen(self._line_color, self._line_width, self._line_alpha),
            name=self._line_label)
        self._max_curve = self._plot.plot(
            pen=_make_pen(QtGui.QColor(50, 220, 50), 1, 1.0), name="Max hold")
        self._min_curve = self._plot.plot(
            pen=_make_pen(QtGui.QColor(220, 50, 50), 1, 1.0), name="Min hold")
        self._max_curve.hide()
        self._min_curve.hide()
        # Keep large FFTs (up to 65536-pt) cheap to draw: downsample to the
        # visible pixel columns using peak mode (preserves narrow peaks) and clip
        # to the view. Without this, three long curves at 10 Hz bog the GUI.
        for _c in (self._curve, self._max_curve, self._min_curve):
            _c.setDownsampling(auto=True, method='peak')
            _c.setClipToView(True)

        # Cursor read-out: red text showing the frequency + level wherever the
        # mouse is over the plot, like the GNU Radio qtgui freq sink. The values
        # are just the cursor's x/y in data coordinates (not the nearest sample),
        # so the operator traces a signal by moving along it. Requested by Dan L.
        self._readout = pg.TextItem(color=(255, 60, 60), anchor=(0, 1))
        self._readout.setZValue(1000)                          # above the traces
        self._readout.setVisible(False)
        self._plot.addItem(self._readout, ignoreBounds=True)   # don't drive autorange
        self._plot.scene().sigMouseMoved.connect(self._on_plot_mouse_moved)
        self._plot.viewport().installEventFilter(self)         # hide on mouse-leave

        # Frozen markers: left-click freezes marker A, right-click freezes B
        # (re-clicking moves that one); double-click (or a middle-click) clears
        # both. Double-click is the macOS clear, since Mac mice have no middle
        # button. With both set, the B label also shows the A->B difference in
        # frequency and level.
        self._frozen = {'A': None, 'B': None}                  # data (x, y) or None
        self._freeze_dots = pg.ScatterPlotItem(size=14, pxMode=True, symbol='+')
        self._freeze_dots.setZValue(1001)
        self._plot.addItem(self._freeze_dots, ignoreBounds=True)
        self._label_a = pg.TextItem(color=(255, 215, 0), anchor=(0, 1))   # gold  = A
        self._label_b = pg.TextItem(color=(0, 220, 255), anchor=(0, 1))   # cyan  = B
        for _lab in (self._label_a, self._label_b):
            _lab.setZValue(1001)
            _lab.setVisible(False)
            self._plot.addItem(_lab, ignoreBounds=True)
        self._plot.setMenuEnabled(False)   # free the right button for marker B
        self._plot.setToolTip(
            "Left-click: place marker A   ·   Right-click: place marker B\n"
            "Double-click: clear both markers")
        self._plot.scene().sigMouseClicked.connect(self._on_plot_clicked)

        layout.addWidget(self._plot, 1)

        self._toggle_btn = QtWidgets.QToolButton()
        self._toggle_btn.setText("▸")
        self._toggle_btn.setToolTip("Hide/show controls")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)
        self._toggle_btn.toggled.connect(self._on_toggle_panel)
        toggle_col = QtWidgets.QVBoxLayout()
        toggle_col.setContentsMargins(0, 0, 0, 0)
        toggle_col.addWidget(self._toggle_btn)
        toggle_col.addStretch(1)
        self._toggle_wrap = QtWidgets.QWidget()
        self._toggle_wrap.setLayout(toggle_col)
        layout.addWidget(self._toggle_wrap)

        self._panel = self._build_panel()
        # Scroll the controls so a tall stack never forces the whole window
        # taller than the screen — it scrolls within the panel instead.
        self._panel_scroll = QtWidgets.QScrollArea()
        self._panel_scroll.setWidget(self._panel)
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panel_scroll.setVerticalScrollBarPolicy(_VBAR_POLICY)
        self._panel_scroll.verticalScrollBar().setStyleSheet(_SCROLLBAR_QSS)
        self._panel_scroll.setFixedWidth(248)   # 230 panel + room for scrollbar
        self._panel_scroll.setMinimumHeight(80)  # let the window shrink past it
        layout.addWidget(self._panel_scroll)

    @staticmethod
    def _fmt_avg(slider_val):
        """'α (≈N)' where N = effective frames averaged by the EMA = (2-α)/α."""
        a = max(1, int(slider_val)) / 1000.0
        return f"{a:.3f} (≈{(2.0 - a) / a:.0f})"

    def _build_panel(self):
        panel = QtWidgets.QGroupBox("Spectrum Display")
        panel.setToolTip(
            "Display only: FFT size, window, averaging and holds shape what\n"
            "you SEE, never what is recorded. Recording geometry (channels,\n"
            "integration) lives in the Recording group.")
        # Fixed width (matched on the waterfall panel) so the spectrum and
        # waterfall plot regions stay equal-width regardless of content.
        panel.setFixedWidth(230)
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # FFT group
        fft_group = QtWidgets.QGroupBox("FFT")
        f = QtWidgets.QFormLayout(fft_group)
        f.setContentsMargins(4, 4, 4, 4)
        self._fft_size_combo = QtWidgets.QComboBox()
        for n in FFT_SIZES:
            self._fft_size_combo.addItem(str(n), n)
        self._fft_size_combo.setCurrentText("1024")
        self._fft_size_combo.currentIndexChanged.connect(
            lambda _i: self.request_fft_size.emit(self._fft_size_combo.currentData()))
        self._fft_size_combo.currentIndexChanged.connect(
            lambda _i: self.control_changed.emit('fft_size', self._fft_size_combo.currentData()))
        self._fft_size_combo.currentIndexChanged.connect(
            lambda _i: self._refresh_title())   # RBW depends on the FFT size
        f.addRow("Size:", self._fft_size_combo)

        self._window_combo = QtWidgets.QComboBox()
        for name in WINDOWS.keys():
            self._window_combo.addItem(name)
        self._window_combo.setCurrentText("blackman-harris")
        self._window_combo.currentTextChanged.connect(self.request_window.emit)
        self._window_combo.currentTextChanged.connect(
            lambda s: self.control_changed.emit('window', s))
        f.addRow("Window:", self._window_combo)

        self._norm_check = QtWidgets.QCheckBox("Normalize window")
        self._norm_check.toggled.connect(self.request_window_normalized.emit)
        self._norm_check.toggled.connect(
            lambda on: self.control_changed.emit('normalize_window', on))
        f.addRow(self._norm_check)

        self._dc_check = QtWidgets.QCheckBox("Remove DC spike")
        self._dc_check.setToolTip(
            "Suppress the center-frequency spike from a zero-IF radio's DC offset "
            "/ LO leakage (HackRF, RTL-SDR, …) by subtracting the DC component "
            "before the FFT. Affects the display only — recordings stay raw.")
        self._dc_check.toggled.connect(self.request_dc_suppress.emit)
        self._dc_check.toggled.connect(
            lambda on: self.control_changed.emit('dc_suppress', on))
        f.addRow(self._dc_check)

        self._avg_slider = QtWidgets.QSlider(Qt.Horizontal)
        self._avg_slider.setRange(1, 1000)
        self._avg_slider.setValue(1000)
        # Keep a grabbable floor. On macOS/Linux a QSlider collapses to zero
        # width when a neighbour claims the space (Windows keeps a wider
        # minimum), so a squeezed slider becomes invisible and un-draggable.
        self._avg_slider.setMinimumWidth(60)
        self._avg_slider.setToolTip(
            "Exponential averaging. Lower α = heavier integration = a smoother, "
            "less jaggy noise floor (at the cost of slower response). The ≈N is "
            "the effective number of frames averaged. This is the main control "
            "for a clean floor in weak-signal work.")
        self._avg_slider.valueChanged.connect(lambda v: self.request_average.emit(v / 1000.0))
        self._avg_slider.valueChanged.connect(
            lambda v: self.control_changed.emit('avg_alpha', v / 1000.0))
        self._avg_value_lbl = QtWidgets.QLabel(self._fmt_avg(1000))
        # Reserve room for the WIDEST reading ("0.001 (≈1999)") so the label
        # never grows with the value and squeezes the slider. Without this,
        # dragging alpha to the far left widened this text until the slider
        # vanished on macOS/Linux (Windows fonts happened to stay narrow enough).
        self._avg_value_lbl.setFixedWidth(
            self._avg_value_lbl.fontMetrics().horizontalAdvance("0.000 (≈1999)") + 4)
        self._avg_slider.valueChanged.connect(
            lambda v: self._avg_value_lbl.setText(self._fmt_avg(v)))
        avg_row = QtWidgets.QHBoxLayout()
        avg_row.addWidget(self._avg_slider, 1)
        avg_row.addWidget(self._avg_value_lbl)
        f.addRow("Avg α:", avg_row)

        self._power_avg_check = QtWidgets.QCheckBox("Power (RMS) averaging")
        self._power_avg_check.setToolTip(
            "Average in linear power (radiometrically correct, unbiased) rather "
            "than in dB. Recommended for quantitative / deep-space work: it is "
            "~28% smoother and removes the ~2.5 dB low-bias of dB averaging — so "
            "the noise floor reads ~2.5 dB HIGHER (its true level). A CW-tone "
            "calibration is unaffected; re-check a noise-based dBm cal.")
        self._power_avg_check.toggled.connect(self.request_power_avg.emit)
        self._power_avg_check.toggled.connect(
            lambda on: self.control_changed.emit('power_avg', on))
        f.addRow(self._power_avg_check)

        self._smooth_spin = QtWidgets.QSpinBox()
        self._smooth_spin.setRange(0, 501)
        self._smooth_spin.setSingleStep(2)
        self._smooth_spin.setSpecialValueText("off")
        self._smooth_spin.setToolTip(
            "Frequency-domain smoothing width in bins (0 = off). A power-domain "
            "(wider-RBW) average that conserves level. Thins the noise like SDR "
            "Console's Smoothing, but still WIDENS a narrow line and drops its "
            "peak ~10·log10(width) — keep it OFF when hunting a narrow line (e.g. "
            "1420 MHz HI); use temporal averaging instead. Best for continuum.")
        self._smooth_spin.valueChanged.connect(self.request_smooth.emit)
        self._smooth_spin.valueChanged.connect(
            lambda n: self.control_changed.emit('smooth_bins', n))
        f.addRow("Smooth (bins):", self._smooth_spin)
        v.addWidget(fft_group)

        # Hold group
        hold_group = QtWidgets.QGroupBox("Hold")
        g = QtWidgets.QGridLayout(hold_group)
        g.setContentsMargins(4, 4, 4, 4)
        self._max_check = QtWidgets.QCheckBox("Max hold")
        self._max_check.toggled.connect(self._on_max_toggled)
        self._max_check.toggled.connect(
            lambda on: self.control_changed.emit('max_hold', on))
        max_reset = QtWidgets.QPushButton("Reset")
        max_reset.clicked.connect(self.request_reset_max.emit)
        g.addWidget(self._max_check, 0, 0)
        g.addWidget(max_reset, 0, 1)
        self._min_check = QtWidgets.QCheckBox("Min hold")
        self._min_check.toggled.connect(self._on_min_toggled)
        self._min_check.toggled.connect(
            lambda on: self.control_changed.emit('min_hold', on))
        min_reset = QtWidgets.QPushButton("Reset")
        min_reset.clicked.connect(self.request_reset_min.emit)
        g.addWidget(self._min_check, 1, 0)
        g.addWidget(min_reset, 1, 1)
        g.addWidget(QtWidgets.QLabel("Detector:"), 2, 0)
        self._hold_detector_combo = QtWidgets.QComboBox()
        self._hold_detector_combo.addItem("Peak (per FFT)", 'peak')
        self._hold_detector_combo.addItem("Average (per frame)", 'average')
        self._hold_detector_combo.setToolTip(
            "What the hold traces accumulate.\n"
            "Peak (per FFT): the extreme of every FFT block — catches even a\n"
            "  single-block transient at full amplitude. On pure noise the max\n"
            "  sits ~+10 dB above the average and the min keeps sinking\n"
            "  (~-10·log10(N) dB): that is the statistics of extremes, not a\n"
            "  bug. Min hold then shows what is ALWAYS present — steady\n"
            "  carriers stand up out of the collapsing noise floor.\n"
            "Average (per frame): the extreme of each display update's Welch\n"
            "  average — both holds stay within a few dB of the baseline.")
        self._hold_detector_combo.currentIndexChanged.connect(
            self._on_hold_detector_changed)
        g.addWidget(self._hold_detector_combo, 2, 1)
        v.addWidget(hold_group)

        # Baseline / bandpass-removal group (radio-astronomy: flatten the
        # instrument bandpass so a weak line stands proud of ~0).
        self._baseline_group = base_group = QtWidgets.QGroupBox("Baseline")
        bf = QtWidgets.QFormLayout(base_group)
        bf.setContentsMargins(4, 4, 4, 4)
        self._baseline_combo = QtWidgets.QComboBox()
        for _label, _code in (("Off", "off"),
                              ("Reference (ON/OFF)", "reference"),
                              ("Flatten (median)", "flatten")):
            self._baseline_combo.addItem(_label, _code)
        self._baseline_combo.setToolTip(
            "Remove the instrument bandpass shape so a faint line pops off a flat "
            "zero.\n"
            "• Reference — point OFF-source, click Store, then go ON-source; the "
            "display becomes ON−OFF (dB).\n"
            "• Flatten — subtract a wide running-median estimate of the bandpass "
            "(no OFF needed); wide enough that narrow lines survive.")
        self._baseline_combo.currentIndexChanged.connect(self._on_baseline_changed)
        bf.addRow("Mode:", self._baseline_combo)

        self._store_ref_btn = QtWidgets.QPushButton("Store reference (OFF)")
        self._store_ref_btn.setToolTip(
            "Capture the current averaged spectrum as the OFF-source reference. "
            "Average heavily first so the reference is clean.")
        self._store_ref_btn.clicked.connect(self.request_store_reference.emit)
        self._store_ref_btn.setEnabled(False)
        bf.addRow(self._store_ref_btn)

        self._flatten_spin = QtWidgets.QSpinBox()
        self._flatten_spin.setRange(3, 2001)
        self._flatten_spin.setSingleStep(2)
        self._flatten_spin.setToolTip(
            "Median window (bins) for Flatten mode. Make it several times WIDER "
            "than any line you want to keep, but narrower than the bandpass "
            "curvature.")
        self._flatten_spin.setEnabled(False)
        self._flatten_spin.valueChanged.connect(self.request_flatten_bins.emit)
        self._flatten_spin.valueChanged.connect(
            lambda n: self.control_changed.emit('flatten_bins', n))
        bf.addRow("Flatten width:", self._flatten_spin)
        v.addWidget(base_group)

        # Y-axis group
        y_group = QtWidgets.QGroupBox("Y-Axis")
        yf = QtWidgets.QFormLayout(y_group)
        yf.setContentsMargins(4, 4, 4, 4)

        self._unit_combo = QtWidgets.QComboBox()
        for _label, _code in (("Relative dB", "relative"), ("dBFS", "dbfs"),
                              ("dBm (approx)", "dbm")):
            self._unit_combo.addItem(_label, _code)
        self._unit_combo.setToolTip(
            "Vertical scale:\n"
            "• Relative dB — historical uncalibrated reference.\n"
            "• dBFS — dB below the ADC full scale (device-independent).\n"
            "• dBm (approx) — dBFS − RX gain + the calibration offset below. "
            "Approximate on uncalibrated SDRs; set the offset against a known "
            "source.")
        self._unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        yf.addRow("Units:", self._unit_combo)

        self._cal_spin = QtWidgets.QDoubleSpinBox()
        self._cal_spin.setRange(-300.0, 300.0)
        self._cal_spin.setDecimals(1)
        self._cal_spin.setSingleStep(1.0)
        self._cal_spin.setSuffix(" dB")
        self._cal_spin.setToolTip(
            "Calibration offset added when Units = dBm. Feed a known-level "
            "signal and adjust until the reading matches its true dBm; changing "
            "the RX gain afterwards is compensated automatically.")
        self._cal_spin.setEnabled(False)   # only meaningful in dBm mode
        self._cal_spin.valueChanged.connect(self.request_cal_offset.emit)
        self._cal_spin.valueChanged.connect(
            lambda v: self.control_changed.emit('cal_offset_db', v))
        yf.addRow("Cal offset:", self._cal_spin)

        self._ymin_spin = QtWidgets.QDoubleSpinBox()
        self._ymin_spin.setRange(-300, 100); self._ymin_spin.setValue(self._y_min)
        self._ymin_spin.valueChanged.connect(self._on_yrange_changed)
        yf.addRow("Min:", self._ymin_spin)
        self._ymax_spin = QtWidgets.QDoubleSpinBox()
        self._ymax_spin.setRange(-200, 200); self._ymax_spin.setValue(self._y_max)
        self._ymax_spin.valueChanged.connect(self._on_yrange_changed)
        yf.addRow("Max:", self._ymax_spin)
        autoscale_btn = QtWidgets.QPushButton("Autoscale")
        autoscale_btn.clicked.connect(self._on_autoscale_y)
        yf.addRow(autoscale_btn)
        reset_axes_btn = QtWidgets.QPushButton("Reset Axes")
        reset_axes_btn.setToolTip(
            "Restore the default Y range and full-span frequency view "
            "(undoes manual min/max edits and any mouse zoom/pan). "
            "Leaves FFT size, window, traces, and colors unchanged.")
        reset_axes_btn.clicked.connect(self.reset_axes)
        yf.addRow(reset_axes_btn)
        self._linear_check = QtWidgets.QCheckBox("Linear scale")
        self._linear_check.setToolTip(
            "Plot linear magnitude instead of dB (log). The dB Min/Max boxes "
            "don't apply in linear mode — the Y axis auto-fits. Affects the "
            "spectrum plot only; the waterfall stays in dB.")
        self._linear_check.toggled.connect(self._on_linear_scale_toggled)
        self._linear_check.toggled.connect(
            lambda on: self.control_changed.emit('linear_scale', on))
        yf.addRow(self._linear_check)
        v.addWidget(y_group)

        # Display group
        disp_group = QtWidgets.QGroupBox("Display")
        dv = QtWidgets.QVBoxLayout(disp_group)
        dv.setContentsMargins(4, 4, 4, 4)
        self._grid_check = QtWidgets.QCheckBox("Grid")
        self._grid_check.setChecked(True)
        self._grid_check.toggled.connect(lambda on: self._plot.showGrid(x=on, y=on, alpha=0.3))
        self._grid_check.toggled.connect(
            lambda on: self.control_changed.emit('grid', on))
        dv.addWidget(self._grid_check)
        self._labels_check = QtWidgets.QCheckBox("Axis labels")
        self._labels_check.setChecked(True)
        self._labels_check.toggled.connect(self._on_labels_toggled)
        self._labels_check.toggled.connect(
            lambda on: self.control_changed.emit('axis_labels', on))
        dv.addWidget(self._labels_check)
        self._dark_bg_check = QtWidgets.QCheckBox("Dark background")
        self._dark_bg_check.setChecked(self._dark_bg)
        self._dark_bg_check.toggled.connect(self._on_dark_bg_toggled)
        self._dark_bg_check.toggled.connect(
            lambda on: self.control_changed.emit('dark_background', on))
        dv.addWidget(self._dark_bg_check)
        v.addWidget(disp_group)

        # Trace group
        tr_group = QtWidgets.QGroupBox("Trace")
        tf = QtWidgets.QFormLayout(tr_group)
        tf.setContentsMargins(4, 4, 4, 4)
        self._color_btn = QtWidgets.QPushButton()
        self._color_btn.setStyleSheet(f"background-color: {self._line_color.name()};")
        self._color_btn.clicked.connect(self._on_color_clicked)
        tf.addRow("Color:", self._color_btn)
        self._width_spin = QtWidgets.QSpinBox()
        self._width_spin.setRange(1, 10); self._width_spin.setValue(self._line_width)
        self._width_spin.valueChanged.connect(self._on_width_changed)
        self._width_spin.valueChanged.connect(
            lambda v: self.control_changed.emit(self._trace_key('trace_width'), int(v)))
        tf.addRow("Width:", self._width_spin)
        self._alpha_slider = QtWidgets.QSlider(Qt.Horizontal)
        self._alpha_slider.setRange(10, 100); self._alpha_slider.setValue(int(self._line_alpha * 100))
        self._alpha_slider.valueChanged.connect(self._on_alpha_changed)
        self._alpha_slider.valueChanged.connect(
            lambda v: self.control_changed.emit(self._trace_key('trace_alpha'), v / 100.0))
        tf.addRow("Alpha:", self._alpha_slider)
        self._label_edit = QtWidgets.QLineEdit(self._line_label)
        self._label_edit.editingFinished.connect(self._on_label_changed)
        self._label_edit.editingFinished.connect(
            lambda: self.control_changed.emit(self._trace_key('trace_label'), self._label_edit.text()))
        tf.addRow("Label:", self._label_edit)
        v.addWidget(tr_group)

        v.addStretch(1)
        return panel

    @staticmethod
    def _to_linear(db):
        # The processor emits power-dB (10·log10(power)); linear amplitude is
        # 10^(dB/20). Display-only transform; the data pipeline stays in dB.
        return np.power(10.0, np.asarray(db) / 20.0)

    @Slot(object, object, object)
    def on_frame(self, avg_db, max_db, min_db):
        n = len(avg_db)
        freqs = self._center_freq + np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self._samp_rate))
        avg = self._to_linear(avg_db) if self._linear else avg_db
        mx = (self._to_linear(max_db) if self._linear else max_db) if max_db is not None else None
        mn = (self._to_linear(min_db) if self._linear else min_db) if min_db is not None else None
        self._curve.setData(freqs, avg)
        if mx is not None:
            self._max_curve.setData(freqs, mx); self._max_curve.show()
        else:
            self._max_curve.hide()
        if mn is not None:
            self._min_curve.setData(freqs, mn); self._min_curve.show()
        else:
            self._min_curve.hide()

    def set_frequency_range(self, center_freq, bandwidth):
        self._center_freq = float(center_freq)
        self._samp_rate = float(bandwidth)
        self._plot.setXRange(center_freq - bandwidth / 2.0,
                             center_freq + bandwidth / 2.0, padding=0)
        self._refresh_title()   # RBW depends on the sample rate

    def set_y_axis(self, y_min, y_max):
        self._y_min = float(y_min); self._y_max = float(y_max)
        self._plot.setYRange(y_min, y_max)
        for spin, val in ((self._ymin_spin, y_min), (self._ymax_spin, y_max)):
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)

    def reset_axes(self):
        """Snap the spectrum view back to defaults: built-in Y range plus the
        full-span frequency view for the current center/sample rate. Undoes
        manual Y min/max edits and any mouse pan/zoom, without touching FFT
        size, window, averaging, traces, or colors. The Y values are persisted
        so the reset survives a restart."""
        if self._linear:
            # Linear mode has no fixed dB range; just auto-fit Y.
            self._plot.enableAutoRange(axis='y')
        else:
            y_min = DEFAULTS['spectrum']['y_min']
            y_max = DEFAULTS['spectrum']['y_max']
            self._plot.disableAutoRange()
            self.set_y_axis(y_min, y_max)
            self.control_changed.emit('y_min', y_min)
            self.control_changed.emit('y_max', y_max)
        # Restore the full frequency span (re-derives X from center/bandwidth).
        self.set_frequency_range(self._center_freq, self._samp_rate)

    def _on_toggle_panel(self, on):
        self._panel_scroll.setVisible(on)
        self._toggle_btn.setText("▸" if on else "◂")

    def take_panel(self):
        """Detach the control panel so the main window can host it in a
        QDockWidget (1.2.0): the panel leaves this widget's layout (the plot
        takes the full width), the collapse toggle disappears (the dock's
        close button and View-menu toggle replace it), and the caller owns
        the returned group box."""
        panel = self._panel_scroll.takeWidget()
        # The fixed 230 px width was for the old embedded-sidebar layout (kept
        # the two plot regions equal). In a dock it would pin the contents at
        # 230 px however wide the (floating) panel is stretched — so relax it
        # to a minimum and let the panel grow with its dock.
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(16777215)   # QWIDGETSIZE_MAX (undo setFixedWidth)
        self._panel_scroll.hide()
        self._toggle_wrap.hide()
        return panel

    def _on_yrange_changed(self, _v):
        self._plot.setYRange(self._ymin_spin.value(), self._ymax_spin.value())

    def _on_view_range_changed(self, _vb, ranges, *_a):
        """Capture the X (freq) + Y view whenever it changes — Min/Max boxes, a
        mouse zoom/pan, and Autoscale all funnel through the viewbox. Mirror Y
        into the spin boxes and (debounced) persist both axes."""
        if not hasattr(self, '_ymin_spin'):
            return  # too early — panel not built yet
        try:
            (x0, x1), (y0, y1) = ranges
        except (TypeError, ValueError):
            return
        self._x_view = (float(x0), float(x1))
        self._y_min, self._y_max = float(y0), float(y1)
        for spin, val in ((self._ymin_spin, self._y_min),
                          (self._ymax_spin, self._y_max)):
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)
        if self._persist_ranges_enabled:
            self._range_save_timer.start()

    def _persist_ranges(self):
        """Write the current view to [spectrum] (via control_changed)."""
        self.control_changed.emit('y_min', self._y_min)
        self.control_changed.emit('y_max', self._y_max)
        if self._x_view is not None:
            self.control_changed.emit('x_min', self._x_view[0])
            self.control_changed.emit('x_max', self._x_view[1])

    def _on_autoscale_y(self):
        """One-shot: fit Y to the visible trace(s) and lock it in, so the result
        is a concrete, remembered range (plain enableAutoRange floats and is
        never captured). Falls back to auto-range if there is no data yet."""
        ys = []
        for c in (self._curve, getattr(self, '_max_curve', None),
                  getattr(self, '_min_curve', None)):
            if c is None or not c.isVisible():
                continue
            d = c.getData()[1]
            if d is not None and len(d):
                ys.append(np.asarray(d, dtype=float))
        if ys:
            ally = np.concatenate(ys)
            ally = ally[np.isfinite(ally)]
            if len(ally):
                lo, hi = float(ally.min()), float(ally.max())
                pad = max(1.0, 0.05 * (hi - lo))
                self._plot.setYRange(lo - pad, hi + pad)  # -> sigRangeChanged
                return
        self._plot.enableAutoRange(axis='y')

    def restore_x_view(self):
        """Re-apply a saved X (freq) zoom on top of the full span, but only if it
        sits within the current tuning's span (a changed center/bandwidth falls
        back to the full view)."""
        if not self._saved_x:
            return
        x0, x1 = self._saved_x
        if not (np.isfinite(x0) and np.isfinite(x1) and x1 > x0):
            return
        lo = self._center_freq - self._samp_rate / 2.0
        hi = self._center_freq + self._samp_rate / 2.0
        tol = 0.01 * self._samp_rate
        if x0 >= lo - tol and x1 <= hi + tol:
            self._plot.setXRange(x0, x1, padding=0)

    def finish_range_restore(self):
        """Run once after startup: apply the saved X zoom (Y is already restored
        by apply_settings), then start remembering live view changes."""
        if self._range_restore_done:
            return
        self._range_restore_done = True
        self.restore_x_view()
        self._persist_ranges_enabled = True

    def _on_labels_toggled(self, on):
        self._labels_on = bool(on)
        self._update_y_label()
        self._plot.setLabel('bottom', 'Frequency' if on else '', units='Hz' if on else '')

    def _update_y_label(self):
        """Set the left-axis label to match the scale mode + unit + visibility."""
        if not self._labels_on:
            self._plot.setLabel('left', '', units='')
        elif self._linear:
            self._plot.setLabel('left', 'Magnitude (linear)', units='')
        elif self._y_unit == 'dbfs':
            self._plot.setLabel('left', 'Power', units='dBFS')
        elif self._y_unit == 'dbm':
            self._plot.setLabel('left', 'Power (approx)', units='dBm')
        else:
            self._plot.setLabel('left', 'Relative Gain', units='dB')

    def _y_unit_suffix(self):
        """dB-scale unit label used by the cursor read-out and markers."""
        return {'dbfs': 'dBFS', 'dbm': 'dBm'}.get(self._y_unit, 'dB')

    def _on_unit_changed(self, _i):
        self._y_unit = self._unit_combo.currentData() or 'relative'
        self._cal_spin.setEnabled(self._y_unit == 'dbm')
        self.request_unit.emit(self._y_unit)
        self.control_changed.emit('y_unit', self._y_unit)
        self._update_y_label()

    def set_cal_offset_value(self, v):
        """Set the dBm calibration offset programmatically (per-device restore)
        without re-saving it as a user edit."""
        with _SignalBlocker(self._cal_spin):
            self._cal_spin.setValue(float(v))
        self.request_cal_offset.emit(float(v))

    def _on_baseline_changed(self, _i):
        mode = self._baseline_combo.currentData() or 'off'
        self._store_ref_btn.setEnabled(mode == 'reference')
        self._flatten_spin.setEnabled(mode == 'flatten')
        self.request_baseline_mode.emit(mode)
        self.control_changed.emit('baseline_mode', mode)
        # Baseline-removed traces hover near 0 dB; auto-fit Y for them and
        # restore the configured fixed range when switching back to Off.
        if self._linear:
            return
        if mode == 'off':
            self._plot.setYRange(self._ymin_spin.value(), self._ymax_spin.value())
        else:
            self._plot.enableAutoRange(axis='y')

    # --- Cursor read-out (frequency / level under the mouse) ----------------

    @staticmethod
    def _format_freq(hz):
        """Frequency string with the unit chosen by magnitude (the x axis is in
        absolute Hz, so this normally reads out in MHz)."""
        a = abs(hz)
        if a >= 1e9:
            return f"{hz / 1e9:.6f} GHz"
        if a >= 1e6:
            return f"{hz / 1e6:.5f} MHz"
        if a >= 1e3:
            return f"{hz / 1e3:.3f} kHz"
        return f"{hz:.1f} Hz"

    def _format_readout(self, x_hz, y):
        freq = self._format_freq(x_hz)
        # In linear mode the y axis is a unitless magnitude, not dB.
        return (f"{freq}, {y:.4g}" if self._linear
                else f"{freq}, {y:.2f} {self._y_unit_suffix()}")

    def _on_plot_mouse_moved(self, scene_pos):
        """Update the red cursor read-out as the mouse moves over the plot."""
        vb = self._plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            self._readout.setVisible(False)          # over the axes/margins
            return
        pt = vb.mapSceneToView(scene_pos)
        x, y = pt.x(), pt.y()
        self._readout.setText(self._format_readout(x, y))
        self._readout.setAnchor(self._edge_anchor(x, y))
        self._readout.setPos(x, y)
        self._readout.setVisible(True)

    def _edge_anchor(self, x, y):
        """Anchor a label toward the plot interior so it stays on-screen when
        the point is near an edge."""
        (x0, x1), (y0, y1) = self._plot.getPlotItem().getViewBox().viewRange()
        ax = 1.0 if x > (x0 + x1) / 2.0 else 0.0
        ay = 0.0 if y > (y0 + y1) / 2.0 else 1.0
        return (ax, ay)

    def eventFilter(self, obj, event):
        # Hide the read-out when the pointer leaves the plot's viewport.
        if (event.type() == QtCore.QEvent.Type.Leave
                and obj is self._plot.viewport()):
            self._readout.setVisible(False)
        return super().eventFilter(obj, event)

    # --- Frozen markers (left = A, right = B, double-click/middle = clear) ---

    def _format_marker(self, tag, xy):
        return f"{tag}  {self._format_readout(xy[0], xy[1])}"

    def _format_delta(self, a, b):
        """A->B difference: signed Δfrequency and Δlevel."""
        df = b[0] - a[0]
        dy = b[1] - a[1]
        df_str = ("+" if df >= 0 else "") + self._format_freq(df)
        dy_str = f"{dy:+.4g}" if self._linear else f"{dy:+.2f} {self._y_unit_suffix()}"
        return f"Δ  {df_str}, {dy_str}"

    def _place_label(self, label, xy):
        label.setAnchor(self._edge_anchor(*xy))
        label.setPos(*xy)

    def _on_plot_clicked(self, ev):
        """Left = freeze marker A, right = freeze marker B. Clear both with a
        double-click OR a middle-click — double-click is the macOS-friendly clear,
        since Mac mice/trackpads have no middle button."""
        vb = self._plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        btn = ev.button()
        if ev.double() or btn == Qt.MiddleButton:
            self._frozen['A'] = self._frozen['B'] = None
        elif btn in (Qt.LeftButton, Qt.RightButton):
            pt = vb.mapSceneToView(ev.scenePos())
            self._frozen['A' if btn == Qt.LeftButton else 'B'] = (pt.x(), pt.y())
        else:
            return
        self._update_frozen()
        ev.accept()

    def _update_frozen(self):
        """Redraw the frozen A/B markers, labels, and the A->B delta."""
        a, b = self._frozen['A'], self._frozen['B']
        spots = []
        if a is not None:
            spots.append({'pos': a, 'pen': pg.mkPen((255, 215, 0), width=2)})
            self._label_a.setText(self._format_marker('A', a))
            self._place_label(self._label_a, a)
        self._label_a.setVisible(a is not None)
        if b is not None:
            spots.append({'pos': b, 'pen': pg.mkPen((0, 220, 255), width=2)})
            text = self._format_marker('B', b)
            if a is not None:
                text += "\n" + self._format_delta(a, b)
            self._label_b.setText(text)
            self._place_label(self._label_b, b)
        self._label_b.setVisible(b is not None)
        self._freeze_dots.setData(spots)

    def clear_markers(self):
        """Clear both frozen A/B markers and redraw — used when the x-axis
        meaning changes (entering/leaving Sweep) so a stale marker can't point
        at the wrong frequency."""
        self._frozen['A'] = self._frozen['B'] = None
        self._update_frozen()

    # --- Plot title with the resolution bandwidth -------------------------

    def _plot_title(self):
        """Title text including the current FFT resolution bandwidth — the bin
        spacing = sample_rate / FFT size. Safe to call before the control panel
        (and its FFT-size combo) exists."""
        n = self._fft_size_combo.currentData() if hasattr(self, '_fft_size_combo') else None
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1024
        rbw = self._samp_rate / n if n else 0.0
        return f"Spectrum — RBW {self._format_freq(rbw)}"

    def _refresh_title(self):
        """Re-apply the title (e.g. after the FFT size or sample rate changes),
        keeping the current theme's foreground colour."""
        self._plot.setTitle(self._plot_title(), color='w' if self._dark_bg else 'k')

    def _on_linear_scale_toggled(self, on):
        self._linear = bool(on)
        self._update_y_label()
        # The dB Min/Max spinboxes are meaningless in linear mode; disable them
        # and let the Y axis auto-fit the linear data. Back in dB, restore the
        # saved fixed range.
        self._ymin_spin.setEnabled(not self._linear)
        self._ymax_spin.setEnabled(not self._linear)
        if self._linear:
            self._plot.enableAutoRange(axis='y')
        else:
            self._plot.setYRange(self._ymin_spin.value(), self._ymax_spin.value())

    def _on_dark_bg_toggled(self, on):
        # Save the live trace styling into the OLD slot before switching.
        self._snapshot_trace_to_slot(self._dark_bg)
        self._dark_bg = bool(on)
        _apply_plot_theme(self._plot, self._plot_title(), self._dark_bg)
        # Pull the NEW slot's trace styling back into the live fields and UI.
        self._restore_trace_from_slot(self._dark_bg)

    def _theme_suffix(self):
        return "dark" if self._dark_bg else "light"

    def _trace_key(self, base):
        return f"{base}_{self._theme_suffix()}"

    def _slot_for(self, dark):
        return self._trace_dark if dark else self._trace_light

    def _snapshot_trace_to_slot(self, dark):
        slot = self._slot_for(dark)
        slot['color'] = self._line_color
        slot['width'] = self._line_width
        slot['alpha'] = self._line_alpha
        slot['label'] = self._line_label

    def _restore_trace_from_slot(self, dark):
        slot = self._slot_for(dark)
        self._line_color = slot['color']
        self._line_width = slot['width']
        self._line_alpha = slot['alpha']
        self._line_label = slot['label']
        with _SignalBlocker(self._width_spin, self._alpha_slider, self._label_edit):
            self._color_btn.setStyleSheet(f"background-color: {self._line_color.name()};")
            self._width_spin.setValue(self._line_width)
            self._alpha_slider.setValue(int(round(self._line_alpha * 100)))
            self._label_edit.setText(self._line_label)
        self._curve.setPen(_make_pen(self._line_color, self._line_width, self._line_alpha))

    def _on_max_toggled(self, on):
        self.request_max_hold.emit(on)
        if not on:
            self._max_curve.hide()

    def _on_min_toggled(self, on):
        self.request_min_hold.emit(on)
        if not on:
            self._min_curve.hide()

    def _on_hold_detector_changed(self, _idx):
        mode = self._hold_detector_combo.currentData() or 'peak'
        self.request_hold_detector.emit(mode)
        self.control_changed.emit('hold_detector', mode)

    def _on_color_clicked(self):
        c = QtWidgets.QColorDialog.getColor(self._line_color, self, "Trace color")
        if c.isValid():
            self._line_color = c
            # Keep the active per-bg slot in lock-step with the live value so
            # a later bg-toggle's snapshot is a no-op rather than the only
            # thing keeping the slot fresh.
            self._slot_for(self._dark_bg)['color'] = c
            self._color_btn.setStyleSheet(f"background-color: {c.name()};")
            self._curve.setPen(_make_pen(c, self._line_width, self._line_alpha))
            self.control_changed.emit(self._trace_key('trace_color'), c.name())

    def _on_width_changed(self, w):
        self._line_width = int(w)
        self._slot_for(self._dark_bg)['width'] = self._line_width
        self._curve.setPen(_make_pen(self._line_color, self._line_width, self._line_alpha))

    def _on_alpha_changed(self, v):
        self._line_alpha = v / 100.0
        self._slot_for(self._dark_bg)['alpha'] = self._line_alpha
        self._curve.setPen(_make_pen(self._line_color, self._line_width, self._line_alpha))

    def _on_label_changed(self):
        self._line_label = self._label_edit.text()
        self._slot_for(self._dark_bg)['label'] = self._line_label

    # --- programmatic setters (used to push values from Settings into the UI) ---
    # Each one blocks signals so applying a saved value doesn't re-trigger a save.
    def apply_settings(self, settings):
        """Push every value from the spectrum section of `settings` into the UI."""
        section = 'spectrum'
        # Load both per-background trace slots from disk so toggling is instant
        # and doesn't need to re-read.
        for which in ('dark', 'light'):
            slot = self._slot_for(which == 'dark')
            color = QtGui.QColor(settings.get_str(section, f'trace_color_{which}'))
            if color.isValid():
                slot['color'] = color
            slot['width'] = settings.get_int(section, f'trace_width_{which}')
            slot['alpha'] = settings.get_float(section, f'trace_alpha_{which}')
            slot['label'] = settings.get_str(section, f'trace_label_{which}')

        with _SignalBlocker(self._fft_size_combo, self._window_combo, self._norm_check,
                            self._dc_check, self._unit_combo, self._cal_spin,
                            self._avg_slider, self._power_avg_check, self._smooth_spin,
                            self._baseline_combo, self._flatten_spin,
                            self._max_check,
                            self._min_check, self._hold_detector_combo,
                            self._ymin_spin, self._ymax_spin,
                            self._grid_check, self._labels_check, self._dark_bg_check,
                            self._width_spin, self._alpha_slider, self._label_edit,
                            self._linear_check):
            idx = self._fft_size_combo.findData(settings.get_int(section, 'fft_size'))
            if idx >= 0:
                self._fft_size_combo.setCurrentIndex(idx)
            self._window_combo.setCurrentText(settings.get_str(section, 'window'))
            self._norm_check.setChecked(settings.get_bool(section, 'normalize_window'))
            self._dc_check.setChecked(settings.get_bool(section, 'dc_suppress'))
            uidx = self._unit_combo.findData(settings.get_str(section, 'y_unit'))
            if uidx >= 0:
                self._unit_combo.setCurrentIndex(uidx)
            # The dBm calibration offset is per-device: the main window restores
            # it from the [calibration] section via set_cal_offset_value().
            a = settings.get_float(section, 'avg_alpha')
            _av = int(round(max(0.001, min(1.0, a)) * 1000))
            self._avg_slider.setValue(_av)
            self._avg_value_lbl.setText(self._fmt_avg(_av))
            self._power_avg_check.setChecked(settings.get_bool(section, 'power_avg'))
            self._smooth_spin.setValue(settings.get_int(section, 'smooth_bins'))
            bidx = self._baseline_combo.findData(settings.get_str(section, 'baseline_mode'))
            if bidx >= 0:
                self._baseline_combo.setCurrentIndex(bidx)
            self._flatten_spin.setValue(settings.get_int(section, 'flatten_bins'))
            self._max_check.setChecked(settings.get_bool(section, 'max_hold'))
            self._min_check.setChecked(settings.get_bool(section, 'min_hold'))
            hidx = self._hold_detector_combo.findData(
                settings.get_str(section, 'hold_detector'))
            if hidx >= 0:
                self._hold_detector_combo.setCurrentIndex(hidx)
            self._ymin_spin.setValue(settings.get_float(section, 'y_min'))
            self._ymax_spin.setValue(settings.get_float(section, 'y_max'))
            self._grid_check.setChecked(settings.get_bool(section, 'grid'))
            self._labels_check.setChecked(settings.get_bool(section, 'axis_labels'))
            self._dark_bg_check.setChecked(settings.get_bool(section, 'dark_background'))
            self._linear_check.setChecked(settings.get_bool(section, 'linear_scale'))
            # Trace controls are filled from the active slot below via
            # _restore_trace_from_slot; nothing to set here.
        # Apply the effects that the blocked signals would normally have triggered.
        self._dark_bg = self._dark_bg_check.isChecked()
        self._labels_on = self._labels_check.isChecked()
        self._plot.setYRange(self._ymin_spin.value(), self._ymax_spin.value())
        self._plot.showGrid(x=self._grid_check.isChecked(), y=self._grid_check.isChecked(), alpha=0.3)
        self._y_unit = self._unit_combo.currentData() or 'relative'
        self._cal_spin.setEnabled(self._y_unit == 'dbm')
        _bmode = self._baseline_combo.currentData() or 'off'
        self._store_ref_btn.setEnabled(_bmode == 'reference')
        self._flatten_spin.setEnabled(_bmode == 'flatten')
        self._on_labels_toggled(self._labels_check.isChecked())
        # Apply the saved scale mode (sets label, spinbox-enable, Y range).
        self._on_linear_scale_toggled(self._linear_check.isChecked())
        _apply_plot_theme(self._plot, self._plot_title(), self._dark_bg)
        # Populate the Trace controls + pen from whichever slot is active.
        self._restore_trace_from_slot(self._dark_bg)
        # Remembered X (freq) zoom: stash it and re-apply after tuning is set
        # (set_frequency_range would otherwise reset X to the full span). Runs
        # once, deferred so all startup tuning calls have settled.
        xr = (settings.get_float(section, 'x_min'),
              settings.get_float(section, 'x_max'))
        self._saved_x = xr if xr[1] > xr[0] else None
        QtCore.QTimer.singleShot(0, self.finish_range_restore)

    def emit_settings_to_processor(self):
        """Re-emit request_* signals from current UI state. Used after
        apply_settings (which blocked signals) so the processor catches up."""
        self.request_fft_size.emit(self._fft_size_combo.currentData())
        self.request_window.emit(self._window_combo.currentText())
        self.request_window_normalized.emit(self._norm_check.isChecked())
        self.request_dc_suppress.emit(self._dc_check.isChecked())
        self.request_unit.emit(self._y_unit)
        self.request_cal_offset.emit(self._cal_spin.value())
        self.request_power_avg.emit(self._power_avg_check.isChecked())
        self.request_smooth.emit(self._smooth_spin.value())
        self.request_baseline_mode.emit(self._baseline_combo.currentData() or 'off')
        self.request_flatten_bins.emit(self._flatten_spin.value())
        self.request_average.emit(self._avg_slider.value() / 1000.0)
        self.request_max_hold.emit(self._max_check.isChecked())
        self.request_min_hold.emit(self._min_check.isChecked())
        self.request_hold_detector.emit(
            self._hold_detector_combo.currentData() or 'peak')


class _SignalBlocker:
    """Context manager that blocks signals on a set of QObjects."""
    def __init__(self, *objs):
        self._objs = objs
        self._prev = []
    def __enter__(self):
        self._prev = [o.blockSignals(True) for o in self._objs]
        return self
    def __exit__(self, *_):
        for o, prev in zip(self._objs, self._prev):
            o.blockSignals(prev)


class WaterfallPlotWidget(QtWidgets.QWidget):
    """Scrolling waterfall (pg.ImageItem) with control panel: intensity
    min/max, autoscale, colormap, grid/axis toggles, row count."""

    # Fires on every user-driven control change; args: (settings_key, value).
    control_changed = Signal(str, object)

    def __init__(self, center_freq, samp_rate, rows=256, parent=None):
        super().__init__(parent)
        self._center_freq = float(center_freq)
        self._samp_rate = float(samp_rate)
        self._rows = int(rows)
        self._intensity_min = -140.0
        self._intensity_max = 10.0
        self._dark_bg = True
        # Per-background colormap. The Colormap combo shows the one matching
        # `_dark_bg`; edits update the matching slot; toggling the background
        # swaps them. apply_settings() overwrites both from the INI.
        self._cmap_dark = "viridis"
        self._cmap_light = "inferno"
        self._colormap_name = self._cmap_dark
        self._data = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._plot = pg.PlotWidget()
        # y reads as AGE: 0 at the top is "now", larger = older (newest rows
        # enter at the top; see on_frame).
        self._plot.setLabel('left', 'Age', units='rows')
        self._plot.setLabel('bottom', 'Frequency', units='Hz')
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.invertY(True)
        _apply_plot_theme(self._plot, "Waterfall", self._dark_bg)
        self._img = pg.ImageItem()
        self._plot.addItem(self._img)
        self._img.setLevels((self._intensity_min, self._intensity_max))
        self._apply_colormap()
        layout.addWidget(self._plot, 1)

        self._toggle_btn = QtWidgets.QToolButton()
        self._toggle_btn.setText("▸")
        self._toggle_btn.setCheckable(True); self._toggle_btn.setChecked(True)
        self._toggle_btn.toggled.connect(self._on_toggle_panel)
        toggle_col = QtWidgets.QVBoxLayout()
        toggle_col.setContentsMargins(0, 0, 0, 0)
        toggle_col.addWidget(self._toggle_btn); toggle_col.addStretch(1)
        self._toggle_wrap = QtWidgets.QWidget(); self._toggle_wrap.setLayout(toggle_col)
        layout.addWidget(self._toggle_wrap)

        self._panel = self._build_panel()
        # Scroll the controls (see FftPlotWidget) so the window can shrink.
        self._panel_scroll = QtWidgets.QScrollArea()
        self._panel_scroll.setWidget(self._panel)
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panel_scroll.setVerticalScrollBarPolicy(_VBAR_POLICY)
        self._panel_scroll.verticalScrollBar().setStyleSheet(_SCROLLBAR_QSS)
        self._panel_scroll.setFixedWidth(248)
        self._panel_scroll.setMinimumHeight(80)
        layout.addWidget(self._panel_scroll)

    def _build_panel(self):
        panel = QtWidgets.QGroupBox("Waterfall Display")
        panel.setToolTip(
            "Display only: intensity, colormap and rows shape what you SEE,\n"
            "never what is recorded.")
        # Matched with the spectrum panel — see FftPlotWidget._build_panel.
        panel.setFixedWidth(230)
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        ig = QtWidgets.QGroupBox("Intensity")
        igl = QtWidgets.QFormLayout(ig); igl.setContentsMargins(4, 4, 4, 4)
        self._imin_spin = QtWidgets.QDoubleSpinBox(); self._imin_spin.setRange(-300, 100)
        self._imin_spin.setValue(self._intensity_min)
        self._imin_spin.valueChanged.connect(self._on_intensity_changed)
        self._imin_spin.valueChanged.connect(
            lambda v: self.control_changed.emit('intensity_min', v))
        igl.addRow("Min:", self._imin_spin)
        self._imax_spin = QtWidgets.QDoubleSpinBox(); self._imax_spin.setRange(-200, 200)
        self._imax_spin.setValue(self._intensity_max)
        self._imax_spin.valueChanged.connect(self._on_intensity_changed)
        self._imax_spin.valueChanged.connect(
            lambda v: self.control_changed.emit('intensity_max', v))
        igl.addRow("Max:", self._imax_spin)
        auto = QtWidgets.QPushButton("Autoscale intensity")
        auto.clicked.connect(self._on_autoscale_clicked)
        igl.addRow(auto)
        v.addWidget(ig)

        cg = QtWidgets.QGroupBox("Colormap")
        cl = QtWidgets.QVBoxLayout(cg); cl.setContentsMargins(4, 4, 4, 4)
        self._cmap_combo = QtWidgets.QComboBox()
        for n in ["viridis", "plasma", "inferno", "magma", "turbo", "cividis", "gray"]:
            self._cmap_combo.addItem(n)
        self._cmap_combo.setCurrentText(self._colormap_name)
        self._cmap_combo.currentTextChanged.connect(self._on_cmap_changed)
        self._cmap_combo.currentTextChanged.connect(
            lambda s: self.control_changed.emit(f'colormap_{"dark" if self._dark_bg else "light"}', s))
        cl.addWidget(self._cmap_combo)
        v.addWidget(cg)

        dg = QtWidgets.QGroupBox("Display")
        dv = QtWidgets.QVBoxLayout(dg); dv.setContentsMargins(4, 4, 4, 4)
        self._labels_check = QtWidgets.QCheckBox("Axis labels"); self._labels_check.setChecked(True)
        self._labels_check.toggled.connect(self._on_labels_toggled)
        self._labels_check.toggled.connect(
            lambda on: self.control_changed.emit('axis_labels', on))
        dv.addWidget(self._labels_check)
        self._grid_check = QtWidgets.QCheckBox("Grid")
        self._grid_check.toggled.connect(lambda on: self._plot.showGrid(x=on, y=False, alpha=0.3))
        self._grid_check.toggled.connect(
            lambda on: self.control_changed.emit('grid', on))
        dv.addWidget(self._grid_check)
        self._dark_bg_check = QtWidgets.QCheckBox("Dark background")
        self._dark_bg_check.setChecked(self._dark_bg)
        self._dark_bg_check.toggled.connect(self._on_dark_bg_toggled)
        self._dark_bg_check.toggled.connect(
            lambda on: self.control_changed.emit('dark_background', on))
        dv.addWidget(self._dark_bg_check)
        rows_form = QtWidgets.QFormLayout()
        self._rows_spin = QtWidgets.QSpinBox(); self._rows_spin.setRange(32, 4096)
        self._rows_spin.setSingleStep(32); self._rows_spin.setValue(self._rows)
        self._rows_spin.valueChanged.connect(self._on_rows_changed)
        self._rows_spin.valueChanged.connect(
            lambda v: self.control_changed.emit('rows', int(v)))
        rows_form.addRow("Rows:", self._rows_spin)
        dv.addLayout(rows_form)
        v.addWidget(dg)

        v.addStretch(1)
        return panel

    def _apply_colormap(self):
        cm = None
        for source in ('matplotlib', None):
            try:
                cm = pg.colormap.get(self._colormap_name, source=source) if source \
                    else pg.colormap.get(self._colormap_name)
                if cm is not None:
                    break
            except Exception:
                cm = None
        if cm is None:
            try:
                cm = pg.colormap.get('viridis', source='matplotlib')
            except Exception:
                cm = pg.colormap.get('CET-L17')
        lut = cm.getLookupTable(0.0, 1.0, 256)
        self._img.setLookupTable(lut)

    @Slot(object, object, object)
    def on_frame(self, avg_db, _max_db, _min_db):
        n = len(avg_db)
        first_frame = (self._data is None
                       or self._data.shape[1] != n
                       or self._data.shape[0] != self._rows)
        if first_frame:
            self._data = np.full((self._rows, n), self._intensity_min, dtype=np.float32)
        # New row at index 0 = the TOP of the display (the plot has
        # invertY(True), so image row 0 maps to y=0 at the top): newest data
        # enters at the top and history flows downward, matching the
        # SDR#/GQRX/SDRangel convention (Rick, 2026-08-03 — the old
        # roll(-1)/write-last direction was an implementation accident).
        self._data = np.roll(self._data, 1, axis=0)
        self._data[0, :] = avg_db
        self._img.setImage(self._data, autoLevels=False,
                           levels=(self._intensity_min, self._intensity_max))
        # setRect must run AFTER setImage — pyqtgraph divides by the current
        # image dimensions to derive the affine transform.
        if first_frame:
            self._update_rect()

    def _update_rect(self):
        x0 = self._center_freq - self._samp_rate / 2.0
        x1 = self._center_freq + self._samp_rate / 2.0
        self._img.setRect(QtCore.QRectF(x0, 0.0, x1 - x0, float(self._rows)))

    def set_frequency_range(self, center_freq, bandwidth):
        self._center_freq = float(center_freq)
        self._samp_rate = float(bandwidth)
        if self._data is not None:
            self._update_rect()

    def link_x_to(self, fft_widget):
        """Slave this waterfall's frequency axis to the spectrum plot's, so
        interactive zoom/pan on either plot keeps both views showing the
        SAME frequency span (Ray's report 2026-08-01: an unlinked waterfall
        goes 'useless' the moment the spectrum's horizontal scale changes).
        Retunes stay coherent too: the spectrum's setXRange propagates
        through the link."""
        self._plot.setXLink(fft_widget._plot.getPlotItem())

    def set_intensity_range(self, lo, hi):
        self._intensity_min = float(lo); self._intensity_max = float(hi)
        self._img.setLevels((lo, hi))
        for spin, val in ((self._imin_spin, lo), (self._imax_spin, hi)):
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)

    def _on_toggle_panel(self, on):
        self._panel_scroll.setVisible(on)
        self._toggle_btn.setText("▸" if on else "◂")

    def take_panel(self):
        """Detach the control panel so the main window can host it in a
        QDockWidget (1.2.0): the panel leaves this widget's layout (the plot
        takes the full width), the collapse toggle disappears (the dock's
        close button and View-menu toggle replace it), and the caller owns
        the returned group box."""
        panel = self._panel_scroll.takeWidget()
        # Same as the spectrum panel: relax the old sidebar fixed width so the
        # contents grow with the dock instead of pinning at 230 px.
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(16777215)   # QWIDGETSIZE_MAX (undo setFixedWidth)
        self._panel_scroll.hide()
        self._toggle_wrap.hide()
        return panel

    def _on_intensity_changed(self, _v):
        self._intensity_min = self._imin_spin.value()
        self._intensity_max = self._imax_spin.value()
        self._img.setLevels((self._intensity_min, self._intensity_max))

    def _on_autoscale_clicked(self):
        if self._data is not None:
            lo = float(np.percentile(self._data, 5))
            hi = float(np.percentile(self._data, 99))
            self.set_intensity_range(lo, hi)
            # set_intensity_range blocks the spin signals, so persist explicitly.
            self.control_changed.emit('intensity_min', lo)
            self.control_changed.emit('intensity_max', hi)

    def _on_cmap_changed(self, name):
        self._colormap_name = name
        # Keep the in-memory slot for the current background in sync so a
        # later bg-toggle snapshots correct values.
        if self._dark_bg:
            self._cmap_dark = name
        else:
            self._cmap_light = name
        self._apply_colormap()

    def _on_labels_toggled(self, on):
        self._plot.setLabel('left', 'Time' if on else '', units='rows' if on else '')
        self._plot.setLabel('bottom', 'Frequency' if on else '', units='Hz' if on else '')

    def _on_dark_bg_toggled(self, on):
        # Snapshot the live colormap to the OLD slot, then switch.
        if self._dark_bg:
            self._cmap_dark = self._colormap_name
        else:
            self._cmap_light = self._colormap_name
        self._dark_bg = bool(on)
        _apply_plot_theme(self._plot, "Waterfall", self._dark_bg)
        # Restore the NEW slot's colormap into the live field + UI.
        self._colormap_name = self._cmap_dark if self._dark_bg else self._cmap_light
        with _SignalBlocker(self._cmap_combo):
            self._cmap_combo.setCurrentText(self._colormap_name)
        self._apply_colormap()

    def _on_rows_changed(self, n):
        self._rows = int(n)
        self._data = None

    def apply_settings(self, settings):
        """Push every value from the waterfall section of `settings` into the UI."""
        section = 'waterfall'
        # Load both per-background colormap slots so toggling is instant.
        self._cmap_dark = settings.get_str(section, 'colormap_dark')
        self._cmap_light = settings.get_str(section, 'colormap_light')

        with _SignalBlocker(self._imin_spin, self._imax_spin, self._cmap_combo,
                            self._labels_check, self._grid_check, self._dark_bg_check,
                            self._rows_spin):
            self._imin_spin.setValue(settings.get_float(section, 'intensity_min'))
            self._imax_spin.setValue(settings.get_float(section, 'intensity_max'))
            self._labels_check.setChecked(settings.get_bool(section, 'axis_labels'))
            self._grid_check.setChecked(settings.get_bool(section, 'grid'))
            self._dark_bg_check.setChecked(settings.get_bool(section, 'dark_background'))
            self._rows_spin.setValue(settings.get_int(section, 'rows'))
            # Colormap combo follows the active slot.
            self._dark_bg = self._dark_bg_check.isChecked()
            self._colormap_name = self._cmap_dark if self._dark_bg else self._cmap_light
            self._cmap_combo.setCurrentText(self._colormap_name)
        # Apply effects normally triggered by the blocked signals.
        self._intensity_min = self._imin_spin.value()
        self._intensity_max = self._imax_spin.value()
        self._img.setLevels((self._intensity_min, self._intensity_max))
        self._apply_colormap()
        self._on_labels_toggled(self._labels_check.isChecked())
        self._plot.showGrid(x=self._grid_check.isChecked(), y=False, alpha=0.3)
        _apply_plot_theme(self._plot, "Waterfall", self._dark_bg)
        self._rows = self._rows_spin.value()
        self._data = None  # force waterfall to re-init at the new row count


class Range:
    """Tiny replacement for qtgui.Range — just a record."""
    def __init__(self, rmin, rmax, step, default, nsteps):
        self.min = rmin; self.max = rmax
        self.step = step; self.default = default; self.nsteps = nsteps


class RangeWidget(QtWidgets.QWidget):
    """Drop-in replacement for qtgui.RangeWidget. Signature matches:
        RangeWidget(range_obj, callback, label, style, value_type, orientation)
    where range_obj exposes .min/.max/.step/.default/.nsteps. Calls
    callback(value_type(v)) when the user edits the value."""

    def __init__(self, range_obj, callback, label, style="counter_slider",
                 value_type=float, orientation=Qt.Horizontal, parent=None):
        super().__init__(parent)
        rmin, rmax, rstep, rdefault, _ = (range_obj.min, range_obj.max,
                                          range_obj.step, range_obj.default,
                                          range_obj.nsteps)
        self._callback = callback
        self._type = value_type
        self._rmin = float(rmin); self._rmax = float(rmax)
        self._rstep = float(rstep) if rstep > 0 else (self._rmax - self._rmin) / 100.0

        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(2, 2, 2, 2); layout.setHorizontalSpacing(6)
        layout.addWidget(QtWidgets.QLabel(label + ":"), 0, 0)

        if value_type is int:
            self._spin = QtWidgets.QSpinBox()
            self._spin.setRange(int(rmin), int(rmax))
            self._spin.setSingleStep(max(1, int(self._rstep)))
            self._spin.setValue(int(rdefault))
        else:
            self._spin = QtWidgets.QDoubleSpinBox()
            self._spin.setRange(self._rmin, self._rmax)
            self._spin.setSingleStep(self._rstep)
            self._spin.setDecimals(self._decimals_for(self._rstep))
            self._spin.setValue(float(rdefault))
        layout.addWidget(self._spin, 0, 1)

        self._steps = max(100, int((self._rmax - self._rmin) / self._rstep))
        self._slider = QtWidgets.QSlider(orientation)
        self._slider.setRange(0, self._steps)
        self._slider.setValue(self._v_to_s(float(rdefault)))
        layout.addWidget(self._slider, 1, 0, 1, 2)

        self._updating = False
        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    @staticmethod
    def _decimals_for(step):
        if step >= 1:
            return 0
        s = f"{step:.10f}".rstrip('0').rstrip('.')
        return min(6, len(s.split('.')[1])) if '.' in s else 0

    def _v_to_s(self, v):
        if self._rmax == self._rmin:
            return 0
        frac = (v - self._rmin) / (self._rmax - self._rmin)
        return int(round(frac * self._steps))

    def _s_to_v(self, s):
        frac = s / self._steps if self._steps else 0
        return self._rmin + frac * (self._rmax - self._rmin)

    def _on_slider(self, s):
        if self._updating: return
        self._updating = True
        v = self._s_to_v(s)
        if self._type is int:
            v = int(round(v))
        self._spin.setValue(v)
        self._updating = False
        self._emit(v)

    def _on_spin(self, v):
        if self._updating: return
        self._updating = True
        self._slider.setValue(self._v_to_s(float(v)))
        self._updating = False
        self._emit(v)

    def _emit(self, v):
        try:
            self._callback(self._type(v))
        except Exception as exc:
            print(f"RangeWidget callback failed: {exc}", file=sys.stderr)

    def set_value(self, v):
        self._updating = True
        if self._type is int:
            self._spin.setValue(int(v))
        else:
            self._spin.setValue(float(v))
        self._slider.setValue(self._v_to_s(float(v)))
        self._updating = False


# === Device discovery + picker ===

# When resolve_device_serial returns this sentinel, the main class builds a
# SigMF-playback flowgraph instead of opening a USRP. The sample files
# (sample.sigmf-data, sample.sigmf-meta) ship in the release bundle and live
# next to dses_workbench.py.
PLAYBACK_SENTINEL = "__PLAYBACK__"
SIGMF_SAMPLE_BASENAME = "sample"


def find_default_sample_path():
    """Locate the bundled SigMF sample. Returns the basename path (no
    extension) if both .sigmf-data and .sigmf-meta exist next to this
    source file, otherwise None."""
    here = Path(__file__).resolve().parent
    base = here / SIGMF_SAMPLE_BASENAME
    if base.with_suffix('.sigmf-data').is_file() and \
       base.with_suffix('.sigmf-meta').is_file():
        return str(base)
    return None


def load_sigmf_meta(base_path):
    """Read .sigmf-meta JSON next to base_path. Returns (sample_rate_hz,
    center_freq_hz, datatype) or raises on malformed metadata."""
    import json
    with open(base_path + '.sigmf-meta', encoding='utf-8') as f:
        meta = json.load(f)
    g = meta.get('global', {})
    sr = float(g['core:sample_rate'])
    dtype = g.get('core:datatype', 'cf32_le')
    caps = meta.get('captures', [])
    cf = float(caps[0].get('core:frequency', 0)) if caps else 0.0
    return sr, cf, dtype


# Soapy drivers we surface to the picker. Anything not in this list is
# still enumerable via SoapySDR.Device.enumerate() with no filter, but we
# only advertise these to avoid showing internal/loopback adapters.
SOAPY_KNOWN_DRIVERS = ("sdrplay", "rtlsdr", "hackrf", "airspy",
                       "airspyhf", "bladerf", "lime", "plutosdr")


_soapy_log_filter_installed = False


def _install_soapy_log_filter(soapy):
    """Register a SoapySDR log handler that drops ONE known-benign message and
    re-emits everything else to stderr.

    conda-forge's SoapySDR build returns a fixed, NUL-padded getRootPath(), so
    its module loader also tries to dlopen the NUL-truncated install-root path
    (a directory, not a module) and logs a harmless 'loadModule(<root>)' ERROR
    at the first enumerate. The real modules in .../SoapySDR/modules0.8 still
    load fine — we hide only that line. A genuine module failure names an actual
    .so/.dll/.dylib, so it is never dropped; every other message passes through.
    Idempotent."""
    global _soapy_log_filter_installed
    if _soapy_log_filter_installed:
        return
    lvl_name = {}
    for nm in ("FATAL", "CRITICAL", "ERROR", "WARNING", "NOTICE",
               "INFO", "DEBUG", "TRACE", "SSI"):
        v = getattr(soapy, "SOAPY_SDR_" + nm, None)
        if v is not None:
            lvl_name[v] = nm

    def _handler(level, message):
        try:
            msg = message or ""
            if "loadModule(" in msg and not any(
                    ext in msg for ext in (".so", ".dll", ".dylib")):
                return  # the benign NUL-truncated-root load; drop it
            print(f"[SoapySDR:{lvl_name.get(level, level)}] {msg}",
                  file=sys.stderr)
        except Exception:
            pass  # a logging filter must never raise

    try:
        soapy.registerLogHandler(_handler)
        _soapy_log_filter_installed = True
    except Exception:
        pass  # SoapySDR too old for a Python log handler — leave logging as-is


def find_soapy_devices():
    """Return SoapySDR devices (RSPx, RTL-SDR, HackRF, …) as
    {driver, serial, product, label}. Skipped silently if SoapySDR isn't
    importable."""
    try:
        import SoapySDR
    except Exception:
        return []
    _install_soapy_log_filter(SoapySDR)
    # Enumerate each known driver explicitly rather than calling
    # SoapySDR.Device.enumerate() with no args. A no-arg enumerate also runs
    # the bundled "remote" module's discovery, which pings IPv6 SSDP
    # multicast and logs a noisy (benign) ERROR on hosts with no IPv6 route.
    # Per-driver enumeration only invokes the driver we ask for, so that
    # network scan never happens.
    out = []
    for drv in SOAPY_KNOWN_DRIVERS:
        try:
            addrs = SoapySDR.Device.enumerate(f"driver={drv}")
        except Exception as exc:
            print(f"SoapySDR discovery failed for {drv}: {exc}",
                  file=sys.stderr)
            continue
        for a in addrs:
            try:
                d = dict(a)
            except Exception:
                continue
            # Each Soapy driver names its serial field differently. Try the
            # common keys; fall back to the driver name + index for display.
            serial = (d.get('serial') or d.get('serial_number')
                      or d.get('device_id') or '')
            product = (d.get('label') or d.get('product')
                       or d.get('hardware') or drv)
            # SDRPlay-specific: SoapySDR's label is "SDRplay Dev{N} {model} {serial}"
            # (sometimes with a colon, sometimes without). Strip the prefix and
            # the trailing duplicate serial so we keep only the model, e.g. "RSP1B".
            if drv == 'sdrplay':
                m = re.match(
                    r'^SDRplay\s+Dev\d+:?\s+(?P<model>\S+)(?:\s+\S+)?\s*$',
                    product, re.IGNORECASE)
                if m:
                    product = m.group('model')
            out.append({
                'driver':  drv,
                'serial':  serial,
                'product': product,
                'label':   f"{product} — {serial or '(no serial)'}  [{drv}]",
            })
    return out


def find_all_radios():
    """Combined enumeration of every supported SDR backend."""
    return find_b200_uhd() + find_soapy_devices()


def _bring_to_front(win):
    """Raise/activate a top-level window so it opens in front. On macOS an app
    launched from a terminal opens its windows behind the terminal until the
    app is activated; this makes startup dialogs and the main window visible
    immediately. It doesn't pin them on top afterward.

    Also clears an inherited *minimized* state: the Windows launcher starts the
    process with its console minimized to keep it out of the way, and Qt's
    first top-level window inherits that show-state — so without this the
    spectrum window itself would open minimized. Only the console should stay
    minimized; the GUI should be visible."""
    try:
        if win.windowState() & Qt.WindowMinimized:
            win.setWindowState(win.windowState() & ~Qt.WindowMinimized)
        win.raise_()
        win.activateWindow()
    except Exception:
        pass


def _make_int_spinbox(lo, hi, value, tooltip):
    """A QSpinBox that reliably shows its initial value on every platform.

    Windows quirk: when setValue() is a no-op because `value` already equals
    the range minimum (a fresh spinbox's default 0 is clamped up to the minimum
    by setRange), the embedded line edit is never refreshed and the field
    renders BLANK — while macOS shows it fine. Force the editor text so the
    value is always visible. (This is the Integrate field, whose default is 1 =
    its minimum.)"""
    s = QtWidgets.QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    le = s.lineEdit()
    if le is not None:
        le.setText(s.textFromValue(s.value()))
    s.setToolTip(tooltip)
    return s


def _front_messagebox(parent, icon, title, text,
                      buttons=None):
    """Modal message box that opens in front of everything — including another
    app's windows (e.g. the Terminal that launched us on macOS, where a
    plain raise_() isn't enough to clear another application). The stay-on-top
    flag floats it above the terminal; it's dismissed immediately so it
    doesn't linger on top."""
    if buttons is None:
        buttons = QtWidgets.QMessageBox.Ok
    box = QtWidgets.QMessageBox(icon, title, text, buttons, parent)
    box.setWindowModality(Qt.ApplicationModal)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.show()
    _bring_to_front(box)
    return box.exec()


class DevicePickerDialog(QtWidgets.QDialog):
    """Modal dialog listing every attached SDR. selected_device() returns
    the picked device dict (with 'driver' and 'serial' keys); Cancel
    returns None."""

    def __init__(self, devices, current_driver=None, current_serial=None,
                 parent=None, prompt="Pick a radio to use:"):
        super().__init__(parent)
        self.setWindowTitle("Choose Radio")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)  # float above the terminal
        self.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(QtWidgets.QLabel(prompt))

        self._list = QtWidgets.QListWidget()
        font = QtGui.QFont("Courier New", 10)
        font.setStyleHint(QtGui.QFont.Monospace)
        self._list.setFont(font)
        for dev in devices:
            item = QtWidgets.QListWidgetItem(dev['label'])
            item.setData(Qt.UserRole, dev)
            self._list.addItem(item)
            if (dev['driver'] == current_driver
                    and dev['serial'] == (current_serial or '')):
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self._list)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_device(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def showEvent(self, event):
        super().showEvent(event)
        _bring_to_front(self)


def resolve_device(saved_driver, saved_serial, parent=None):
    """Decide which SDR to open. Returns one of:
        - a device dict {driver, serial, product, label}
        - PLAYBACK_SENTINEL if no device is attached but the bundled SigMF
          sample is present
        - None if the user canceled the picker and there's nothing to fall
          back on.
    Selection rule: no radio → playback/error; exactly one radio → use it
    silently; two or more radios → always show the picker so the user
    chooses, pre-selecting the saved (driver, serial) from settings.
    `saved_driver` and `saved_serial` come from settings."""
    devices = find_all_radios()

    if not devices:
        sample = find_default_sample_path()
        if sample is None:
            _front_messagebox(
                parent, QtWidgets.QMessageBox.Critical, "No radio found",
                "No supported SDR was detected (looked for UHD B200/B210 and "
                "SoapySDR-recognised devices: SDRPlay, RTL-SDR, HackRF, "
                "Airspy, BladeRF, Lime, PlutoSDR), and no bundled SigMF "
                "sample file was found next to the program.\n\n"
                "Plug a supported radio in and relaunch, or place "
                f"'{SIGMF_SAMPLE_BASENAME}.sigmf-data' and "
                f"'{SIGMF_SAMPLE_BASENAME}.sigmf-meta' alongside "
                "dses_workbench.py to enable demo playback.")
            return None
        # OK continues into playback; Cancel exits right here — no waiting
        # for the full app to come up just to close it (useful when the
        # radio SHOULD have been found: reseat the USB and relaunch).
        answer = _front_messagebox(
            parent, QtWidgets.QMessageBox.Information, "Playback mode",
            "No radio detected — starting in SigMF playback mode.\n\n"
            f"File: {Path(sample).name}.sigmf-data\n\n"
            "The sample loops continuously. Sample rate, gain, and recording "
            "are disabled (no hardware). Tuning is enabled and digitally "
            "shifts the spectrum within the recording's bandwidth — tune "
            "outside it and you'll just see noise."
            "\n\nIf a radio IS plugged in, its USB link probably failed "
            "to enumerate — reseat the USB cable at the radio end (check "
            "Device Manager for an 'Unknown USB Device'). If that does "
            "not clear it, fully power-cycle the radio: disconnect USB "
            "AND any external DC supply for 15 seconds — a wedged USB "
            "chip survives everything short of a true cold start. Then "
            "relaunch."
            "\n\nCancel exits now instead of starting playback.",
            buttons=(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel))
        if answer == QtWidgets.QMessageBox.Cancel:
            return None
        return PLAYBACK_SENTINEL

    saved_driver = (saved_driver or '').strip()
    saved_serial = (saved_serial or '').strip()
    # Backwards-compat: pre-v1.0.0 INIs only had device_serial; assume B210.
    if saved_serial and not saved_driver:
        saved_driver = DRIVER_UHD_B200

    # Exactly one radio attached → use it silently.
    if len(devices) == 1:
        return devices[0]

    # Two or more radios attached → always let the user choose. The saved
    # (driver, serial) is pre-selected in the picker, so the common case is
    # a single Enter to confirm the same radio as last time.
    dlg = DevicePickerDialog(devices, current_driver=saved_driver,
                             current_serial=saved_serial, parent=parent)
    if dlg.exec() == QtWidgets.QDialog.Accepted:
        return dlg.selected_device()
    return None


# === About + Help dialogs ===

class AboutDialog(QtWidgets.QDialog):
    """Version, author, license, dependencies, plus the Restore Defaults
    button. Defaults wipes the settings file back to built-ins and emits
    `defaults_requested` so the main window can re-apply across all widgets."""

    defaults_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(480)

        layout = QtWidgets.QVBoxLayout(self)

        # Header
        title = QtWidgets.QLabel(f"<h2>{APP_NAME}</h2>"
                                 f"<p>Version <b>{APP_VERSION}</b></p>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        info = QtWidgets.QLabel(
            f"<p>{APP_DESCRIPTION}</p>"
            f"<p><b>Author:</b> {APP_AUTHOR} &lt;{APP_AUTHOR_EMAIL}&gt;<br>"
            f"<b>{APP_COPYRIGHT}</b><br>"
            f"<b>License:</b> {APP_LICENSE}</p>"
            f"<p>This program is free software: you can redistribute it and/or "
            f"modify it under the terms of the GNU General Public License as "
            f"published by the Free Software Foundation, either version 3 of "
            f"the License, or (at your option) any later version. See the "
            f"LICENSE file for the full text.</p>"
            f"<p><b>Built on:</b> GNU Radio · UHD · SoapySDR · PySide6 · "
            f"PyQtGraph · NumPy · SciPy</p>"
            f"<p><b>Settings file:</b><br><code>{settings.path}</code></p>"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(info)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        defaults_btn = QtWidgets.QPushButton("Restore Defaults…")
        defaults_btn.setToolTip("Reset every setting to its built-in default. "
                                "The settings file is overwritten.")
        defaults_btn.clicked.connect(self._on_defaults_clicked)
        btn_row.addWidget(defaults_btn)
        open_btn = QtWidgets.QPushButton("Open Settings Folder")
        open_btn.clicked.connect(self._on_open_settings_folder)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_defaults_clicked(self):
        ans = QtWidgets.QMessageBox.question(
            self, "Restore Defaults",
            "This will reset every setting to its built-in default and "
            "overwrite the settings file. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ans == QtWidgets.QMessageBox.Yes:
            self._settings.reset_to_defaults()
            self.defaults_requested.emit()
            QtWidgets.QMessageBox.information(
                self, "Defaults restored",
                "Settings have been reset to their built-in values.")

    def _on_open_settings_folder(self):
        folder = str(self._settings.path.parent)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))


HELP_TEXT_HTML = f"""
<h2>{APP_NAME} — User Guide</h2>

<p>The DSES Radio Astronomy Workbench (through version 1.3.4 the <i>DSES
Spectrum Analyzer</i>) is one program for the club's SDR-based observing: a
live spectrum analyzer and waterfall for the Ettus USRP B210 and other
software-defined radios (SDRPlay RSP1A/RSP1B/RSPduo/RSPdx, RTL-SDR, HackRF,
Airspy, BladeRF, LimeSDR, PlutoSDR via SoapySDR); a pulsar visibility
planner; recording to SIGPROC filterbank, SigMF, and ezRA drift-scan
formats; PRESTO folding and quick-look analysis; multi-day HI drift-scan
campaigns; and a B210 self test that doubles as a pulsar simulator. It began
as an RFI survey tool and still serves for general spectrum monitoring.</p>

<h3>Starting up — device selection</h3>
<p>At launch the program enumerates attached SDRs (UHD + SoapySDR) and
decides what to use:</p>
<ul>
<li><b>One supported SDR attached</b>: opens it silently and remembers the
driver and serial in the settings file.</li>
<li><b>Two or more radios attached</b>: a picker dialog always appears so
you can choose which one to use. Your previous choice is pre-selected, so
you can just press Enter to use the same radio as last time. Your selection
is remembered for next launch.</li>
<li><b>No SDR attached, but a bundled SigMF sample is present</b>: the
program falls back to <b>playback mode</b> — see below. It keeps watching
for a receiver in the background: connect or power one on and, within a few
seconds, a dialog offers a one-click restart to use it — no manual
shut-down-and-relaunch dance.</li>
<li><b>No SDR and no sample</b>: an error dialog explains how to fix it
and the program exits.</li>
</ul>
<p>The window title always shows which radio is feeding the display, and
the <b>RX</b> group has a <code>Device:</code> button you can click to
re-open the picker. The new choice takes effect on the next launch.</p>

<h3>Playback mode</h3>
<p>If no SDR is attached, the program looks next to the application file
for <code>sample.sigmf-data</code> + <code>sample.sigmf-meta</code> and, if
both are found, plays the file back in a continuous loop. The window title
shows <b>[Playback]</b>. The data source is the file, paced to the original
capture's sample rate.</p>
<ul>
<li><b>Sample Rate</b>, <b>RX Gain</b>, and <b>Recording</b> controls are
disabled — they have no meaning for a recorded file. Sample rate comes
from the file's metadata.</li>
<li><b>Tuning is enabled</b> and works as a digital frequency shift
(<code>blocks.rotator_cc</code>) on the file's baseband. Tuning to the
file's actual center frequency (read from the .sigmf-meta) shows the
recording's true content; tuning to other frequencies within
±(sample_rate/2) of the file's center lets you "look around" inside the
recorded bandwidth. Tune well outside that window and you'll just see
noise / wrap-around — exactly what you'd expect, since the recording
doesn't contain data at those frequencies.</li>
<li>All visualization controls (Spectrum panel, Waterfall panel) work
normally.</li>
<li>To replace the sample with your own, save a SigMF recording, rename the
two files to <code>sample.sigmf-data</code> and <code>sample.sigmf-meta</code>,
and drop them next to the program. (You don't need to change any code.)</li>
</ul>

<h3>Control panels (dockable)</h3>
<p>Every control panel is dockable. On the <b>right</b>:
<b>Observation</b>, <b>Tuning</b>, <b>Radio</b>, and <b>Recording</b> — the
science settings. On the <b>left</b>, beside the plots they belong to:
<b>Spectrum Display</b> and <b>Waterfall Display</b>. Drag a panel by its
title bar to rearrange, stack panels as tabs, tear one off into its own
floating window (handy on a second monitor), or close it; the <b>View</b>
menu is organised by column — <b>Display Panels (left)</b> and <b>Control
Panels (right)</b> — and each of those opens onto a <i>Show this column</i>
switch that hides or restores the whole column in one click, followed by
that column's individual panels. Hiding a column remembers which of its
panels were open, so showing it again brings back exactly those (a panel you
had closed on purpose stays closed). Your arrangement is remembered across
runs. To float a panel, <b>drag it out by its title bar</b>. The title
bar's three buttons act on it afterwards: the first <b>docks a floating
panel back</b> into the window — which restores the default panel layout,
the one arrangement Qt reliably rebuilds, so a panel can always be
recovered. The second <b>enlarges</b> a floating panel just enough that all
of its controls are visible; while enlarged the button shows a double-box
<i>restore</i> icon and a click returns the panel to its previous size
(resizing the panel by hand clears that state, so the next click enlarges
afresh). It is greyed out while the panel is docked, where the layout sets
the size. <b>✕</b> hides the panel. When
a column runs out of room Qt stacks panels as tabs along its edge — those
tabs are colored (pastel blue, DSES teal when selected) so the stack is easy
to spot. The two display panels are deliberately restricted to the left column
(they describe the plots, so they stay next to them) — they can still be
reordered there, tabbed together, or floated freely. Control panels dock in
either column but never along the top or bottom of the window, so a panel on
its way to a floating spot is not snapped into a strip across the top. The
menu bar (File / View / Radio / Recording / Help) duplicates the important
actions, and long status messages — recording filenames, analysis progress —
appear in the full-width <b>status bar</b> at the bottom of the window where
they are never truncated.</p>
<h4>Observe menu — Pulsars in View (Ctrl+P)</h4>
<p>Answers "what can I record right now?" from the ATNF catalog: every
pulsar above the site's elevation mask, sorted by flux <i>in the band you
are tuned to</i> (S400 below ~900 MHz, S1400 above), with current az/el,
period, DM, and how long each stays up. Selecting one fills the recording
<b>Source</b> field and hands the recorder that pulsar's exact catalog
RA/Dec for the <code>.fil</code> header — better than the position the app
otherwise infers from the name.</p>
<ul>
<li><b>Plan for a date and time</b>: the table normally shows the sky
<i>now</i>. Tick <b>Plan for</b> — or just edit the date/time box, or use the
<code>-1 d</code> / <code>-1 h</code> / <code>+1 h</code> / <code>+1 d</code>
steps — and everything is recomputed for that instant instead: altitude,
azimuth, time above the mask, next window, and which rows count as viable.
Read the box as <b>UTC</b> (the convention in every file this app writes) or
as your <b>Local</b> clock; switching keeps the same instant. Any date works,
past as well as future, so you can also ask what was overhead when an old
recording was made. The window title, the copied-table header line and the
readout beside the box all say <b>PLANNED</b> so a planned table is never
mistaken for the live sky, and <b>Now</b> puts it back. The readout also gives
the site's <b>local sidereal time</b> — a source transits when LST equals its
right ascension.</li>
<li><b>Hover for an explanation</b>: every column header explains what the
column is, and <i>every individual cell</i> explains what its own value means
— the delay this pulsar's DM produces across the band you are tuned to and
inside one channel, when this source next crosses the meridian and how high,
the clock time it crosses the elevation mask, which catalog anchors a flux
estimate came from, and what went into its Min&nbsp;rec. If a number looks
surprising, hover it before believing it.</li>
<li><b>Search</b>: type part of a name (<code>b0329</code>,
<code>J0332</code>), or filter numerically — <code>dm&lt;30</code>,
<code>p&lt;0.1</code> (seconds), <code>flux&gt;10</code>,
<code>alt&gt;40</code>, or <code>magnetar</code>. Terms combine, so
<code>dm&lt;30 flux&gt;50</code> finds bright, low-dispersion targets.</li>
<li><b>Include below mask</b>: also lists sources that are not up yet, and
the <b>Next window</b> column says when each rises above the mask and how
long the window lasts. (A source can be circumpolar — never setting — and
still spend hours below a usable elevation.)</li>
<li><b>Include magnetars</b>: magnetars are marked ★ and are never removed
by a flux filter, because the catalog usually carries no flux for them.</li>
<li><b>Flux at tuned freq</b>: estimates each source's flux <i>at the
frequency you are tuned to</i> by power-law interpolation between the
catalog's S400 and S1400 (using the source's own spectral index when both
exist, a typical −1.6 otherwise) — labeled <code>est@…</code> so you know
it is an estimate. Unchecked, the nearest catalog band is quoted
verbatim.</li>
<li><b>Min rec</b>: the radiometer minimum recording length for an 8-σ
folded detection at the current sample rate, from the site SEFD
(<code>[site] sefd_jy</code>, measured on Cygnus&nbsp;A) and the catalog
W50 pulse width (5% duty assumed when the catalog has none). <b>Rows
highlighted green are viable now</b> — up, with Min&nbsp;rec fitting
inside Time&nbsp;left. It is an aid, not a gate: one SEFD serves every
band (low-band numbers read optimistic) and RFI, scintillation, and
pointing loss add on top.</li>
<li><b>Best band</b>: of the bands the dish has feeds for (the Tuning
presets), the one where this source detects fastest — flux scaled to each
band, SEFD scaled by sky temperature, and pulse broadening from channel DM
smearing plus empirical interstellar scattering. Steep-spectrum low-DM
sources are sent low; high-DM sources are kept high, where scattering has
not destroyed the pulse. Approximate physics — the band to <i>try
first</i>, not a guarantee.</li>
<li><b>Best f</b>: the same model with the feed list taken away — the
frequency anywhere from 100 MHz to 6 GHz where the source detects fastest,
i.e. what a feed built for it would want to be. Hover the cell to see how
far the dish's best real band falls short (often not far: for most bright
pulsars the 408 MHz feed is within a few tens of percent of the optimum).
An answer at the very bottom of the range means "as low as you can go",
not a measured optimum — real pulsar spectra turn over there and the model
does not know it.</li>
<li><b>Copy for reports</b>: Ctrl+C copies the selected rows (with a
header line, and a comment line naming the site, the instant, the mask and
the tuning the numbers came from) as tab-separated text that pastes cleanly
into email, Excel, or Word; right-click offers Copy cell / Copy rows / Copy
whole table.</li>
<li><b>What do I need?</b>: solves the dispersion arithmetic backwards for
the selected source — which of the dish's bands (and how much bandwidth)
would make its <i>DM measurable</i>, and what to set in the self-test
simulator. Dispersion delay goes as 1/frequency², so a small DM at L-band
is simply unresolvable: the fold still detects the pulsar, but its DM
search slides toward zero and means nothing. The answer says so plainly
rather than leaving you to discover it after the drive.</li>
<li>The catalog is downloaded once and cached beside your recordings, so
the planner keeps working at a site with no internet. <b>Observe → Refresh
Pulsar Catalog</b> re-downloads it.</li>
<li>If a <b>Source</b> and a <b>Record for</b> duration are both set, the
app warns at record time when that pulsar would set before the recording
finishes.</li>
</ul>
<h4>Observe menu — B210 Self Test</h4>
<p>A built-in test of the <i>entire</i> pulsar chain — SDR, channelizer,
filterbank writer, timebase, PRESTO fold, verdict — with no test equipment
at all. The B210's own transmitter plays a synthetic pulsar (100 ms period,
DM 50, noise-carrier pulses with real cold-plasma dispersion) at
<b>minimum TX gain</b> on 420 MHz, far from the protected hydrogen-line
band; the receiver records the B210's internal TX→RX leakage — no cable or
attenuator needed. After the capture (default 90 s) the app folds the
recording at the injected period and DM and grades PASS/FAIL: the period
must come back exact, the DM near 50 (a DM stuck at 0 means dispersion was
lost), and the significance high. Your tuning, sample rate, gain, and
antenna are saved before the test and restored right after the capture,
while the fold runs. Run it before packing for a field session: a PASS
means a real pulsar that reaches the feed will survive the pipeline.
Requires a USRP B200/B210 and an installed PRESTO. The recording and its
fold PDF land in a <code>self_test</code> folder inside your recordings
folder.</p>
<p>Beyond the standard test, two advanced modes make it a general pulsar
<i>simulator</i>: <b>Simulate a catalog pulsar</b> picks any source from
the ATNF catalog (magnetars included) and injects its exact catalog period
and DM, and <b>Custom</b> opens every parameter — frequency (any B210
frequency, 70–6000 MHz), sample rate, period, DM, duty cycle, amplitude,
RX gain, channels, and capture length. A live readout translates the
chosen geometry into what matters: the dispersion sweep across the band
and the DM resolution it can honestly support, pulse width vs sample time,
and pulses per capture — narrow bands at high frequency constrain DM
weakly, and the readout says so before you spend the time. After picking a
catalog source, <b>Suggest geometry</b> goes further and solves for
settings that can actually measure that source's DM, filling them in for
you (it knows the internal leakage weakens at low frequency and that the
duplex transmitter holds its timing best at or below 2 MS/s). TX gain is
always locked at minimum: the internal leakage is all the test needs, so
even the protected 1420 MHz band is safe.</p>

<h4>Observe menu — Drift-Scan Review</h4>
<p>The morning-after quicklook for drift-scan recordings: open one or more
ezRA-format <code>.txt</code> files (written by the Workbench <i>or</i> by
ezRA's own ezCol — both dialects are read, including ezCol's interleaved
reference rows and mid-file elevation changes) and get one standard report
per file. It checks the header (software version, whether the hydrogen
line is inside the recorded band, the site coordinates against your
settings, and whether the file's az/el disagrees with the Recording
panel's current values — the classic stale-header trap), the recording
itself (row cadence, gaps, total-power stability), and the spectrum
(narrow spurs against a median bandpass, with the known 1420.000-family
carriers called out). The plots show a bandpass-flattened waterfall, the
averaged velocity profile, and the transit light curve with a Gaussian
fit — peak, width, and the transit center in UT and local sidereal time,
with the half-integration timestamp correction applied. Load several
nights at once and the Trend tab plots the fitted peak day by day — the
pointing/sag monitor used on the Haswell dish. <b>Copy summary</b> puts
the whole text report on the clipboard; <b>Save report…</b> writes it to
disk with PNGs of the plots. The review is deliberately the layer
<i>below</i> ezRA's ezCon/ezSky/ezGal: it answers "did I detect it, and
is the file clean?" so you know what is worth feeding onward.</p>
<h4>Observation</h4>
<p>The "what are you trying to do tonight?" selector. Pick a goal and every
science-critical setting — band, sample rate, recording format, channels,
integration — is set to a validated bundle in one step:</p>
<ul>
<li><b>Pulsar — L-band</b>: 16 MHz, filterbank, 2044 channels, Integrate 1
(127.7 µs samples) at the L-band feed (1420.4 MHz) — the geometry behind
the 28σ B0329+54 detection at Haswell.</li>
<li><b>Pulsar — UHF</b>: 20 MHz, filterbank, 256 channels, Integrate 16
(204.8 µs) centered at 420 MHz — the proven Haswell UHF geometry.</li>
<li><b>Magnetar / high-DM</b>: L-band with 4096 channels — narrower channels
tolerate the larger dispersion of magnetars and distant pulsars.</li>
<li><b>Hydrogen line — drift scan</b>: ezRA .txt format at 1420.406 MHz,
2 MHz span, with the receiver's DC artefact moved out of band by an LO
offset. On radios with no LO shift stage (HackRF, RTL-SDR) the offset
cannot be applied in hardware, so the preset adapts: if the radio can run
8 MS/s it centers 1422.0 MHz instead — the line stays in band and the
artefact sits 1.6 MHz away from any galactic gas (a configuration proven
in the field by Ray Uberecken, AA0L); a radio that cannot reach that rate
keeps the line-centered tuning with the artefact blanked in analysis. The
status bar states which arrangement was applied. Set the dish Az/El in
the Recording group. Starting an ezRA recording whose band does not
contain the hydrogen line asks for confirmation first (and the status bar
warns as soon as the tuning goes off the line).</li>
<li><b>RFI survey — sweep</b>: switches to Sweep mode; set the range in the
Sweep group.</li>
<li><b>Manual (expert)</b>: touches nothing. The combo drops back here by
itself when you change any of the settings a preset controls — the label
never claims a bundle the settings no longer match. The app always starts
here; your individual settings persist on their own.</li>
</ul>
<p>Below the selector, a live <b>consequences line</b> translates the current
settings into what they mean for the data: time resolution, channel width,
per-channel dispersion smearing (at a reference DM of 30), and disk usage
per hour. It turns amber when a combination is risky — sample rate beyond
the validated recording geometry, or time resolution too coarse for pulsar
work. On radios that can't reach a preset's rate, the request is clamped and
snapped as usual and the readout shows what you actually got. Display
settings (FFT size, window, averaging) are deliberately untouched by
presets: they shape what you <i>see</i>, never what is recorded.</p>
<p><b>A preset never starts a recording.</b> It only configures — review the
settings, make any adjustments, then start the recording yourself with the
<b>Record</b> control when you're ready. (The RFI-survey preset does begin
sweeping the display immediately, exactly as the Sweep mode button would,
but nothing is written to disk.)</p>
<h4>Mode</h4>
<ul>
<li><b>Live</b>: real-time FFT of the radio's instantaneous bandwidth around
one tuned center frequency (the traditional view).</li>
<li><b>Sweep</b>: stepped scan across a wide range — for RFI surveys that span
more than the radio's instantaneous bandwidth. The radio is retuned across the
range and each step's FFT is stitched into one wide trace. See the <b>Sweep</b>
group below. Unavailable in Playback.</li>
</ul>
<h4>Tuning</h4>
<ul>
<li><b>Pulsar Band</b>: preset frequencies for common pulsar observation
bands. Choose <i>Manual</i> to use the Manual Frequency field instead.</li>
<li><b>Coarse Tune</b>: ±100 MHz offset from the selected preset (or from
the manual frequency).</li>
<li><b>Fine Tune</b>: ±10 MHz offset, layered on top of Coarse Tune.</li>
<li><b>Manual Frequency</b>: used when the <i>Manual</i> preset is selected.
Accepts engineering notation, e.g. <code>1.42G</code> or <code>408M</code>.</li>
<li><b>LO Offset</b>: parks the receiver's local oscillator this far from the
displayed center; the receiver's digital downconverter shifts the band back, so
the display and every recorded frequency are unchanged — but the receiver's own
DC spike (present in any zero-IF SDR, and sitting exactly on the displayed
center by default) moves off your target. Set it beyond half the sample rate to
push the spike out of the recorded band entirely. Vital for narrow-line work:
tuned to the hydrogen line with offset 0, the spike lands <i>inside the
line</i>. The <i>Hydrogen line — drift scan</i> observation preset sets +1.5 MHz
automatically. Avoid offsets at integer multiples of the sample rate — on the
B210 those raise a spur in the band (bench-measured). Requires hardware support (B200/B210: yes; Soapy radios: only
if the driver exposes a shift stage — HackRF and RTL-SDR do not, and the app
falls back to classic tuning with a status-bar note).</li>
</ul>

<p>The Tuning group is disabled in Sweep mode (the center frequency is chosen
automatically per step).</p>
<h4>Sweep (visible in Sweep mode)</h4>
<ul>
<li><b>Start / Stop</b>: bottom and top of the swept range. Keep them within the
connected radio's tuning range.</li>
<li><b>Step</b>: Hz per tuning step. Type <code>auto</code> for ~80% of the
current sample rate (recommended — keeps the clean middle of each FFT and avoids
the DC spike plus band-edge rolloff). For tighter spacing enter a value, e.g.
<code>2M</code>.</li>
<li><b>Status</b>: current step number and tune frequency, or <i>Idle</i> in Live.</li>
</ul>
<p>Sweep runs continuously, redrawing the wide trace after each pass; the
waterfall adds one row per pass. Sample rate and gain still apply to each step's
FFT. Averaging, Max/Min hold, and baseline removal are forced off while sweeping
(they would smear across retunes) and restored on return to Live; frozen cursor
markers are cleared when entering or leaving Sweep.</p>
<h4>RX</h4>
<ul>
<li><b>Sample Rate</b>: per-radio, up to the hardware's true maximum (e.g.
B210: 0.625–61.44 MHz — 56 MHz analog bandwidth; SDRPlay: 2–10 MHz; RTL-SDR:
0.25–3.2 MHz). The presets are not arbitrary: each divides the radio's
master clock evenly, so decimation stays on the flat half-band filter chain.
Rates that don't divide the clock cleanly can fall back to CIC filtering,
whose passband droop shows up as a bowl-shaped gain error across the
spectrum — poison for calibrated radio astronomy. Higher rate = wider
spectrum but more disk and host load when recording: the radio isn't the
only limit. USB bandwidth and host CPU set a practical ceiling — if the
overflow panel or a recording's gap counter climbs at a high rate, the host
can't keep up; step down. (16 MHz .fil recording is the validated DSES
pulsar geometry.)</li>
<li><b>Manual Rate (Hz)</b>: type any rate the SDR supports — useful for
real-pulsar capture geometries that aren't in the preset list. The value is
clamped to the device's reported min/max (hover for the range) and the radio
snaps to the nearest rate it can actually deliver, which is then shown back.
Accepts engineering notation (e.g. <code>24M</code>, <code>625k</code>). The
combo clears when the active rate isn't one of the presets.</li>
<li><b>RX Gain</b>: per-radio range and meaning. The slider's min/max
matches what the driver reports (e.g. B210: 0–76 dB on the AD9361 gain
table; SDRPlay: 0–48 dB, internally inverted so higher = stronger signal;
RTL-SDR: 0–49.6 dB). AGC, if the driver defaults it on, is disabled at
startup so the slider always takes effect.</li>
<li><b>Antenna</b>: appears only when the open radio has more than one RF
input. For a B210 this lists all four physical connectors as
<code>A : RX2</code>, <code>A : TX/RX</code>, <code>B : RX2</code>,
<code>B : TX/RX</code> — receiver A and receiver B, each with its two SMA
ports — and switching includes hopping between the two receivers. An
RSPduo lists its two tuners. Pick the connector your cable is actually
plugged into; the choice is remembered per radio. Single-port radios (most
RTL dongles, the RSP1B) don't show this control.</li>
<li><b>Device</b>: shows the currently-open radio and re-opens the picker
on click.</li>
</ul>

<h4>Recording</h4>
<ul>
<li><b>Folder</b>: where recordings land. Defaults to
<code>~/Documents/DSES_SA_Recordings</code>. Change it with this button or
from <b>File → Set Recording Folder…</b>; the folder is checked for
writability and the next recording uses it immediately (it cannot be changed
while a recording is running, so a run is never split across two folders).
<b>File → Open Recordings Folder</b> opens the current one in your file
manager.</li>
<li><b>Source</b>: optional source / pulsar name (e.g. <code>B0329+54</code>).
When set it is folded into the recording filename and written into the SIGPROC
<code>.fil</code> header (<code>source_name</code>, plus RA/Dec derived from the
name) and the SigMF description, so PRESTO/prepfold pick it up. Blank gives a
timestamp-only filename. Locked while recording.</li>
<li><b>Format</b>: <i>Raw I/Q (SigMF)</i> writes full-rate complex samples to
a SigMF <code>.sigmf-meta</code>/<code>.sigmf-data</code> pair — exact, but
large (e.g. ~192&nbsp;MB/s at 24&nbsp;Msps). <i>Filterbank (.fil)</i>
channelizes the stream live and writes a SIGPROC filterbank
(<code>telescope_id&nbsp;12</code>) straight to disk, so the giant raw I/Q is
never stored. The <code>.fil</code> is what PRESTO folds; it is produced by
the same validated code as the offline <code>iq_to_fil.py</code> converter.
<i>Drift scan (ezRA .txt)</i> writes integrated spectra (one row every
~10&ndash;15&nbsp;s with the default geometry) in the ezRA data format, so the
file feeds Ted Cline's free ezRA suite (ezCon&nbsp;&rarr;&nbsp;ezPlot/ezSky/ezGal)
directly &mdash; hydrogen-line drift scans with the same radio that records
pulsars. The filename follows the ezCol convention
(<code>&lt;prefix&gt;YYMMDD_HH.txt</code>), and &mdash; also like ezCol &mdash;
a multi-day recording rolls to a <b>new file at each UTC midnight</b> (one
file per UTC day, each with a full header), so ezCon consumes a long campaign
as its usual series of daily files.</li>
<li><b>Channels</b> / <b>Integrate</b> (filterbank only): the FFT channel
count and how many power frames are summed per output sample, so
<code>tsamp&nbsp;=&nbsp;channels&nbsp;&times;&nbsp;integrate&nbsp;/&nbsp;sample&nbsp;rate</code>.
Locked while recording.</li>
<li><b>Az / El</b> (drift scan only): the dish pointing written into the
ezRA file header (<code>azDeg</code>/<code>elDeg</code>). The observing-site
identity (latitude, longitude, altitude, name) comes from the
<code>[site]</code> section of the settings file &mdash; defaults are the DSES
Haswell 60-ft dish. FFT bins, integration count, and the band-edge trim
follow the dish's proven ezCol geometry and are adjustable via
<code>[recording]</code> <code>ez_*</code> settings.</li>
<li><b>Record for</b>: optional fixed length — minutes (e.g. <code>30</code>) or
<code>H:MM</code> / <code>HH:MM:SS</code> (e.g. <code>1:30</code>). The recording
auto-stops when it is reached and the counter shows a countdown; blank records
until you stop it. Locked while recording.</li>
<li><b>Start at</b>: optional scheduled start — a clock time
(<code>03:15</code>, the next time it comes round), a delay
(<code>+30</code> minutes, <code>+1:30</code>), or a full date
(<code>2026-10-24 03:15</code>), read as UTC or your local clock per the
selector beside it. Setting <b>Record</b> to <i>Recording</i> then <i>arms</i>
instead of starting: the status turns amber, the counter shows a countdown
to the start (and, with <b>Record for</b>, the planned end), the
source-visibility and hydrogen-line questions are asked up front so the
start itself needs nobody at the keyboard, and <i>Stopped</i> cancels. Blank
starts at once.</li>
<li><b>Record</b>: <i>Stopped</i> / <i>Recording</i>. Recording always
starts <i>Stopped</i> on launch. While recording, a red <b>REC</b> counter
shows elapsed time (or the countdown when a duration is set).</li>
<li><b>Analyze when done</b> / <b>Quick look</b> (filterbank + PRESTO): when
a <code>.fil</code> recording stops, the canned PRESTO pipeline runs
automatically — <code>readfile</code> sanity, an <code>rfifind</code> RFI
mask, band-edge zapping, and a <code>prepfold</code> fold — by catalog pulsar
when Source is a known designation (the <b>Fold&nbsp;P&nbsp;(ms)</b> /
<b>Fold&nbsp;DM</b> boxes then preview its catalogue values and lock, and the
fold uses the pulsar's full ephemeris), or at a manual <b>Fold&nbsp;P&nbsp;(ms)</b>
for a source with no catalogue entry (e.g. the lab pulsar simulator) — and
delivers a <b>self-contained PDF</b>
next to the recording: chart, commands, numbers, and a plain-language
verdict. Verdicts are honest about failure modes: a periodicity that
optimizes to DM&nbsp;≈&nbsp;0 is reported as a <i>terrestrial signal</i>, not
a detection, and "no detection" explicitly does not mean a bad recording.
<b>Quick look</b> does the same on a snapshot of the still-growing file
mid-recording, without interrupting it. Requires PRESTO (Mac/Linux: native
install; Windows: WSL via <code>presto/build_presto.sh</code>).</li>
<li><b>Timebase integrity</b> (filterbank, USRP/UHD radios): if the host
briefly can't drain samples (an RX overflow — the 'O' characters in the
sidebar), the dropped stretch would silently shorten the file's sample
clock and smear a later pulsar fold. The recorder measures each gap from
the radio's own timestamps and inserts the exact number of zero samples,
so the <code>.fil</code> timebase keeps tracking real time. The REC counter
shows any gaps live (e.g. <i>2 gaps, 45&nbsp;ms padded</i>) and a
<code>.gaps.json</code> file with the details is written next to the
recording. Non-UHD radios (HackRF, RTL-SDR, SDRplay) don't provide
per-gap timestamps; for them, watch the Overflow panel — a clean panel
means a clean timebase.</li>
</ul>

<h3>Spectrum (top plot)</h3>
<p>Live FFT magnitude in dB. Use the control panel on the right side to
adjust:</p>
<ul>
<li><b>FFT Size</b>: 256–8192. Larger = finer frequency resolution but
slower response and more averaging-window flicker.</li>
<li><b>Window</b>: Blackman-Harris is the default — low sidelobes, good
for RFI hunting. Hann/Hamming have narrower main lobes; Rectangular has
the sharpest peak but the worst sidelobes.</li>
<li><b>Avg α</b>: exponential averaging. 1.0 = no smoothing (every frame
is a fresh measurement). Smaller = more smoothing.</li>
<li><b>Max / Min hold</b>: overlay traces showing the highest/lowest value
ever seen at each bin. Use <b>Reset</b> to clear. The <b>Detector</b> combo
picks what they accumulate. <i>Peak (per FFT)</i>: the extreme of every FFT
block — even a sub-millisecond burst registers at full amplitude, making Max
hold a true transient-RFI catcher; on pure noise the Max trace settles
~10&nbsp;dB above the average and the Min trace keeps sinking (the statistics
of extremes over many samples — not a malfunction), so Min hold reveals
what is <i>always</i> present: steady carriers stand up out of the collapsing
noise floor. <i>Average (per frame)</i>: extremes of each display update's
deep average — both holds stay within a few dB of the baseline, useful for
tracking slow drifts.</li>
<li><b>Y-Axis</b>: dB min/max, or click <b>Autoscale</b> to fit the
current data. <b>Reset Axes</b> snaps the plot back to the default dB range
and full-span frequency view — handy after you've zoomed/panned with the
mouse or nudged the min/max and want to get un-lost. It leaves FFT size,
window, traces, and colors untouched.</li>
<li><b>Linear scale</b>: plots linear magnitude instead of dB (the default
log scale). In linear mode the Y axis auto-fits and the dB Min/Max boxes are
disabled. Affects the spectrum plot only — the waterfall stays in dB.</li>
<li><b>Trace</b>: color, line width, alpha, label.</li>
</ul>

<h3>Waterfall (bottom plot)</h3>
<p>Scrolling 2-D image of FFT vs. time. Newest row at the <b>top</b>,
history flowing down (the SDR#/GQRX convention); the left axis reads as age
in rows.</p>
<ul>
<li><b>Intensity Min/Max</b>: dB range that maps to the colormap.
<b>Autoscale intensity</b> picks the 5%–99% percentile of the current
data.</li>
<li><b>Colormap</b>: viridis (default), plasma, inferno, magma, turbo,
cividis, gray.</li>
<li><b>Rows</b>: how many history rows to display (default 256).</li>
</ul>

<h3>Persistence</h3>
<p>All selections are saved to a plain-text INI file and restored on next
launch. The file location is shown in <b>Help → About</b>; you can open
the folder directly with the <b>Open Settings Folder</b> button.</p>
<p>To revert everything to factory defaults, use <b>Restore Defaults</b>
in the About dialog.</p>

<h3>Update checks</h3>
<p>If the developer has configured a manifest URL, the program checks for
a newer release in the background at launch (no more than once every 24
hours). When a newer version is found, a non-modal dialog opens with the
release notes and a button that opens the download page in your browser —
you can ignore it and keep using the app, or click <b>Skip this version</b>
to not be reminded about that particular version again.</p>
<p>The check is read-only and never auto-downloads or auto-installs. To
trigger a check manually, use <b>Help → Check for Updates…</b>. To disable
auto-checks, set <code>auto_check = false</code> under <code>[updates]</code>
in the settings file. If the manifest URL has not been configured yet, the
auto-check is silently skipped. Installs upgraded from 1.3.4 or earlier keep
working: their stored manifest URL (the old <code>b210_sa</code> release
folder) is moved to the new <code>dses-workbench</code> folder automatically
on first launch, and the old folder keeps a pointer to the current release.</p>

<h3>Tips for pulsar work</h3>
<ul>
<li>The 1420.4 MHz preset tunes the L-band feed directly to the
neutral-hydrogen line (1420.406 MHz), so HI stays in band at any sample
rate. For drift scans prefer the <b>Hydrogen line</b> Observation preset,
which also sets the rate, format and LO offset.</li>
<li>1666 MHz preset covers the OH maser band.</li>
<li>Use <b>Avg α</b> ≈ 0.05 and <b>Max hold</b> to find intermittent
RFI sources.</li>
<li>The waterfall reveals time-structured interference (e.g. radar sweeps,
ADS-B bursts) that the live spectrum smears out.</li>
</ul>
"""


class HelpDialog(QtWidgets.QDialog):
    """Scrollable user guide. Read-only HTML."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — User Guide")
        self.resize(700, 600)

        layout = QtWidgets.QVBoxLayout(self)

        text = QtWidgets.QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(HELP_TEXT_HTML)
        layout.addWidget(text, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


# === Overflow capture (UHD and gr-soapy write 'O' to FD-level stderr on RX overflow) ===

# Match runs of bare 'O' characters that aren't part of a word (so "OOO"
# scrolls past, but normal stderr text like "Operating" or "Boost_108600"
# is ignored).
_OVERFLOW_RE = re.compile(r'\bO+\b')


class OverflowMonitor(QtCore.QObject):
    """Redirects FD 2 (C-level stderr) into a pipe, scans the byte stream
    for RX overflow indicators ('O' characters, written by both UHD and
    gr-soapy), and emits them via a Qt signal. All stderr output is passed
    through to the original console unchanged, so info logs and tracebacks
    still appear there."""

    chars_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stopped = False
        self._saved_stderr_fd = -1
        self._read_fd = -1
        self._write_fd = -1
        self._reader_thread = None

    def start(self):
        try:
            sys.stderr.flush()
        except Exception:
            pass
        self._read_fd, self._write_fd = os.pipe()
        self._saved_stderr_fd = os.dup(2)
        os.dup2(self._write_fd, 2)
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="stderr-overflow-monitor", daemon=True)
        self._reader_thread.start()

    def stop(self):
        self._stopped = True
        # Restore the real stderr so any shutdown messages go to the console,
        # then close the pipe so the reader thread can exit.
        if self._saved_stderr_fd >= 0:
            try:
                os.dup2(self._saved_stderr_fd, 2)
            except OSError:
                pass
        if self._write_fd >= 0:
            try:
                os.close(self._write_fd)
            except OSError:
                pass
            self._write_fd = -1

    def _reader_loop(self):
        try:
            while not self._stopped:
                try:
                    data = os.read(self._read_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                # Pass-through to the real console.
                if self._saved_stderr_fd >= 0:
                    try:
                        os.write(self._saved_stderr_fd, data)
                    except OSError:
                        pass
                # Extract standalone 'O' runs.
                text = data.decode('utf-8', errors='replace')
                matches = _OVERFLOW_RE.findall(text)
                if matches:
                    # AutoConnection → QueuedConnection across threads, so the
                    # slot runs on the GUI thread safely.
                    self.chars_received.emit(''.join(matches))
        except Exception:
            # Never let the reader thread propagate exceptions to nothing.
            pass


class OverflowDisplayWidget(QtWidgets.QGroupBox):
    """Sidebar group that streams RX overflow 'O' characters into a fixed
    4-line text view. Both UHD and gr-soapy print 'O' to stderr when the
    host can't drain samples fast enough, so this works for any radio.
    Older lines are auto-dropped by Qt itself via `setMaximumBlockCount`,
    so the widget never grows past 4 lines and there's nothing to scroll."""

    MAX_LINES = 4
    LINE_WIDTH = 40  # chars per logical line — fits the narrowest sidebar
    IDLE_CLEAR_MS = 15000  # auto-clear if no new overflow chars for this long

    def __init__(self, parent=None):
        super().__init__("Overflow ('O' = dropped samples)", parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setReadOnly(True)
        # NoWrap + explicit '\n' every LINE_WIDTH chars → each block is one
        # visual line. setMaximumBlockCount then caps history at MAX_LINES;
        # Qt drops the oldest block when we add a 5th, no scroll needed.
        self._text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text.setMaximumBlockCount(self.MAX_LINES)
        self._text.setFixedHeight(64)
        font = QtGui.QFont("Courier New", 10)
        font.setStyleHint(QtGui.QFont.Monospace)
        self._text.setFont(font)
        self._text.setPlaceholderText("(no overflows)")
        layout.addWidget(self._text)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        layout.addWidget(clear_btn)

        # Chars currently on the bottom line; reset to 0 each time we wrap.
        self._line_chars = 0

        # Single-shot timer restarted on every emit. When it fires, no new
        # overflow chars have arrived for IDLE_CLEAR_MS — wipe the display.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_CLEAR_MS)
        self._idle_timer.timeout.connect(self.clear)

    @Slot(str)
    def append_chars(self, s):
        if not s:
            return
        # Cursor-based incremental insert — much cheaper than setPlainText,
        # which would re-lay out the whole document on every emit and caused
        # the freeze/jitter at high overflow rates.
        cursor = self._text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        remaining = s
        while remaining:
            free = self.LINE_WIDTH - self._line_chars
            if free <= 0:
                cursor.insertText('\n')
                self._line_chars = 0
                free = self.LINE_WIDTH
            chunk = remaining[:free]
            cursor.insertText(chunk)
            self._line_chars += len(chunk)
            remaining = remaining[free:]
        # Keep the view pinned to the newest line.
        self._text.moveCursor(QtGui.QTextCursor.End)
        # Restart the idle-clear countdown.
        self._idle_timer.start()

    @Slot()
    def clear(self):
        self._text.clear()
        self._line_chars = 0
        self._idle_timer.stop()


# === Auto-update check ===

def _parse_version(v):
    """Parse a dotted version string into a tuple of ints. Returns () on
    parse failure (which compares as 'lower than anything')."""
    try:
        return tuple(int(p) for p in str(v).split('.'))
    except (ValueError, TypeError):
        return ()


def _friendly_check_error(exc, manifest_url=""):
    """Turn an update-check exception into a message the user can act on. A TLS
    certificate-verification failure is usually local — antivirus "HTTPS
    scanning" or a stale/expired root cached in the OS certificate store — but
    it can also be a genuinely expired/invalid server certificate. Name both
    possibilities and point at the manual-download page."""
    import ssl
    reason = getattr(exc, 'reason', None)
    is_cert = (isinstance(exc, ssl.SSLCertVerificationError)
               or isinstance(reason, ssl.SSLCertVerificationError)
               or 'CERTIFICATE_VERIFY_FAILED' in str(exc))
    if not is_cert:
        return f"{type(exc).__name__}: {exc}"
    raw = reason if reason is not None else exc
    detail = str(raw)
    if detail.startswith('(') and getattr(raw, 'args', None):
        detail = str(raw.args[0])  # unwrap a bare ('msg',) repr into clean text
    msg = ("The update server's security certificate could not be verified:\n"
           f"{detail}\n\n"
           "This usually means a problem on THIS PC -- antivirus \"HTTPS "
           "scanning\" (Avast/AVG/ESET/Kaspersky/Bitdefender) intercepting the "
           "connection, or a stale/expired root certificate cached in the "
           "system certificate store -- but it can also mean the update "
           "server's own certificate has expired or is invalid.")
    if '/' in manifest_url:
        base = manifest_url.rsplit('/', 1)[0] + '/'
        msg += f"\n\nYou can still download the update manually from:\n{base}"
    return msg


class UpdateChecker(QtCore.QObject):
    """Fetches a small manifest.json from a configured URL on a background
    thread and, if it advertises a newer version than what's running, emits
    `update_available(latest_version, download_url, release_notes)`. Errors
    are reported via `check_failed(message)` — auto-launches ignore them,
    manual 'Check now' surfaces them."""

    update_available = Signal(str, str, str)  # version, url, notes
    no_update = Signal(str)                   # latest_version
    check_failed = Signal(str)                # human-readable message

    USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings

    @Slot()
    def check_now(self):
        threading.Thread(target=self._do_check, name="update-checker",
                         daemon=True).start()

    def _do_check(self):
        url = self._settings.get_str('updates', 'manifest_url').strip()
        if not url:
            # Not configured — silently skip. Manual 'Check for Updates'
            # menu handler should also check this and tell the user.
            return
        try:
            # updater.open_url verifies against the OS trust store first, then
            # falls back to the bundled certifi roots, so a stale/expired cached
            # intermediate in the system store can't block the check. See
            # updater.open_url for the full rationale.
            with updater.open_url(url, timeout=8,
                                  headers={'User-Agent': self.USER_AGENT}) as resp:
                raw = resp.read()
            import json
            data = json.loads(raw.decode('utf-8'))
        except Exception as exc:
            self.check_failed.emit(_friendly_check_error(exc, url))
            return
        latest = str(data.get('latest_version', '')).strip()
        download_url = str(data.get('download_url', '')).strip()
        notes = str(data.get('release_notes', '')).strip()
        if not latest:
            self.check_failed.emit("Manifest has no 'latest_version' field.")
            return
        # Remember the last successful check so we can debounce repeats.
        self._settings.set('updates', 'last_check_iso',
                           datetime.now().isoformat(timespec='seconds'))
        try:
            self._settings.save()
        except OSError:
            pass
        if _parse_version(latest) > _parse_version(APP_VERSION):
            self.update_available.emit(latest, download_url, notes)
        else:
            self.no_update.emit(latest)


def _guide_url_from_download(download_url):
    """Derive the install/upgrade guide PDF URL from a release zip URL: the
    guide sits next to the zips in the same server folder. Returns "" if the
    download URL is empty/unusable."""
    download_url = (download_url or "").strip()
    if "/" not in download_url:
        return ""
    folder = download_url.rsplit("/", 1)[0]
    return f"{folder}/{GUIDE_PDF_BASENAME}"


class UpdateNotificationDialog(QtWidgets.QDialog):
    """Non-modal: tells the user a new version is available. It steers them to
    the install/upgrade *guide* first, then offers the download zip, skip, or
    close. Emits `dismissed_for_version(version)` if the user clicks Skip."""

    dismissed_for_version = Signal(str)
    install_requested = Signal(str, str)  # download_url, latest_version

    def __init__(self, latest_version, download_url, release_notes,
                 current_version, parent=None):
        super().__init__(parent)
        # Non-modal so the user can keep using the app.
        self.setModal(False)
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(500)

        self._latest = latest_version
        self._url = download_url
        self._guide_url = _guide_url_from_download(download_url)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            f"<h3>Version {latest_version} is available.</h3>"
            f"<p>You're running version {current_version}.</p>"))

        # Steer the user to the guide BEFORE the zip: the zip is not a
        # double-click installer — it must be applied per the guide.
        layout.addWidget(QtWidgets.QLabel(
            "<p><b>Read the installation &amp; upgrade guide first.</b> The "
            "download is a <code>.zip</code> of program files, not an "
            "installer — the guide explains how to apply it safely (where to "
            "unzip, what to replace, and not to reinstall Radioconda).</p>"))

        if release_notes:
            notes = QtWidgets.QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(release_notes)
            notes.setFixedHeight(140)
            layout.addWidget(QtWidgets.QLabel("<b>Release notes:</b>"))
            layout.addWidget(notes)

        if download_url:
            url_lbl = QtWidgets.QLabel(f"Download: <code>{download_url}</code>")
            url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            url_lbl.setWordWrap(True)
            layout.addWidget(url_lbl)

        btns = QtWidgets.QHBoxLayout()
        # Primary action: download + install in-app (the automated path).
        install_btn = QtWidgets.QPushButton("Install Update…")
        install_btn.setEnabled(bool(download_url))
        install_btn.setDefault(True)
        install_btn.clicked.connect(self._on_install)
        btns.addWidget(install_btn)

        # The cautious path: read the guide, or grab the zip to apply by hand.
        guide_btn = QtWidgets.QPushButton("Read the Guide (PDF)")
        guide_btn.setEnabled(bool(self._guide_url))
        guide_btn.clicked.connect(self._on_guide)
        btns.addWidget(guide_btn)

        dl_btn = QtWidgets.QPushButton("Download .zip")
        dl_btn.setEnabled(bool(download_url))
        dl_btn.clicked.connect(self._on_open)
        btns.addWidget(dl_btn)

        btns.addStretch(1)

        skip_btn = QtWidgets.QPushButton("Skip This Version")
        skip_btn.clicked.connect(self._on_skip)
        btns.addWidget(skip_btn)

        remind_btn = QtWidgets.QPushButton("Remind Me Later")
        remind_btn.clicked.connect(self.close)
        btns.addWidget(remind_btn)
        layout.addLayout(btns)

    def _on_install(self):
        if self._url:
            self.install_requested.emit(self._url, self._latest)
            self.close()

    def _on_guide(self):
        if self._guide_url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._guide_url))

    def _on_open(self):
        if self._url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._url))

    def _on_skip(self):
        self.dismissed_for_version.emit(self._latest)
        self.close()


def _create_desktop_shortcut(install_dir, version_label):
    """Best-effort: run the OS's install-shortcut helper from `install_dir`,
    naming the shortcut with `version_label` so a new install gets its own icon
    instead of overwriting an existing one. Failures are non-fatal."""
    install_dir = Path(install_dir)
    try:
        if sys.platform == 'darwin':
            subprocess.run(['bash', str(install_dir / 'install-shortcut.command'),
                            version_label], cwd=str(install_dir),
                           check=False, capture_output=True, text=True)
        elif sys.platform == 'win32':
            subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', str(install_dir / 'install-shortcut.ps1'),
                            '-Suffix', version_label], cwd=str(install_dir),
                           check=False, capture_output=True, text=True)
        else:
            _make_linux_desktop_entry(install_dir, version_label)
    except Exception as exc:
        print(f"Desktop shortcut creation failed: {exc}", file=sys.stderr)


def _make_linux_desktop_entry(install_dir, version_label):
    """Write a versioned .desktop file into ~/.local/share/applications from the
    bundled template (Linux 'desktop shortcut')."""
    tmpl = Path(install_dir) / 'dses-workbench.desktop'
    if not tmpl.is_file():
        return
    text = (tmpl.read_text(encoding='utf-8')
            .replace('__INSTALL_DIR__', str(install_dir))
            .replace('Name=DSES Radio Astronomy Workbench',
                     f'Name=DSES Radio Astronomy Workbench {version_label}'))
    dest_dir = Path.home() / '.local' / 'share' / 'applications'
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f'dses-workbench-{version_label}.desktop').write_text(
        text, encoding='utf-8')


def _relaunch(install_dir):
    """Start a fresh instance via the platform launcher, detached from this one
    (so this process can exit and the new one survives)."""
    install_dir = Path(install_dir)
    try:
        if sys.platform == 'win32':
            subprocess.Popen(['cmd', '/c', 'start', '', 'launcher.bat'],
                             cwd=str(install_dir),
                             creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                                            | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        else:
            subprocess.Popen(['bash', str(install_dir / 'launcher.sh')],
                             cwd=str(install_dir), start_new_session=True)
    except Exception as exc:
        print(f"Relaunch failed: {exc}", file=sys.stderr)


class UpdateInstaller(QtCore.QObject):
    """Runs download -> verify -> extract -> install on a background thread and
    reports progress/result via signals (delivered to the GUI thread)."""

    progress = Signal(int, int)        # bytes_done, bytes_total (0 = unknown)
    status = Signal(str)               # human status line
    done = Signal(bool, str, str)      # success, message, install_path

    def __init__(self, download_url, mode, dest, make_shortcut, version_label,
                 parent=None):
        super().__init__(parent)
        self._url = download_url
        self._mode = mode              # 'in_place' | 'new_copy'
        self._dest = Path(dest)        # in_place: current dir; new_copy: parent
        self._make_shortcut = make_shortcut
        self._version = version_label

    def start(self):
        threading.Thread(target=self._run, name="update-installer",
                         daemon=True).start()

    def _run(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="dses_update_"))
        try:
            self.status.emit("Downloading update…")
            zip_path = tmp / "update.zip"
            updater.download(self._url, zip_path,
                             progress=lambda g, t: self.progress.emit(g, t))
            self.status.emit("Verifying download…")
            updater.verify(zip_path, updater.sha256_url_for(self._url))
            self.status.emit("Extracting…")
            root = updater.extract_release(zip_path, tmp / "x")
            if self._mode == 'in_place':
                self.status.emit("Installing over the current version…")
                updater.install_in_place(root, self._dest)
                self.done.emit(True, "Update installed over the current version.",
                               str(self._dest))
            else:
                self.status.emit("Installing a new copy…")
                new_dir = updater.install_new_copy(root, self._dest)
                if self._make_shortcut:
                    self.status.emit("Creating desktop shortcut…")
                    _create_desktop_shortcut(new_dir, self._version)
                self.done.emit(True, f"Installed a new copy at:\n{new_dir}",
                               str(new_dir))
        except Exception as exc:
            self.done.emit(False, f"{type(exc).__name__}: {exc}", "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class InstallUpdateDialog(QtWidgets.QDialog):
    """Asks WHERE to install: over the current install, or as a new copy in a
    chosen folder (with an optional desktop shortcut for that version)."""

    def __init__(self, current_dir, new_version, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install Update")
        self.setModal(True)
        self.setMinimumWidth(540)
        self._current_dir = Path(current_dir)
        self._new_parent = Path.home()

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            f"<h3>Install version {new_version}</h3>"
            "<p>The download is checksum-verified before anything is changed.</p>"))

        self._rb_overwrite = QtWidgets.QRadioButton(
            "Update this installation (replace the current version)")
        self._rb_overwrite.setChecked(True)
        lay.addWidget(self._rb_overwrite)
        cur_lbl = QtWidgets.QLabel(f"&nbsp;&nbsp;&nbsp;&nbsp;<code>{self._current_dir}</code>")
        cur_lbl.setWordWrap(True)
        cur_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(cur_lbl)

        self._rb_new = QtWidgets.QRadioButton(
            "Install a new copy and keep the current version")
        lay.addWidget(self._rb_new)

        row = QtWidgets.QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QtWidgets.QLabel("Into:"))
        self._folder_lbl = QtWidgets.QLabel(str(self._new_parent))
        self._folder_lbl.setEnabled(False)
        row.addWidget(self._folder_lbl, 1)
        self._folder_btn = QtWidgets.QPushButton("Choose Folder…")
        self._folder_btn.setEnabled(False)
        self._folder_btn.clicked.connect(self._choose_folder)
        row.addWidget(self._folder_btn)
        lay.addLayout(row)

        self._shortcut_cb = QtWidgets.QCheckBox(
            "Add a desktop shortcut for this version")
        self._shortcut_cb.setChecked(True)
        self._shortcut_cb.setEnabled(False)
        shortcut_row = QtWidgets.QHBoxLayout()
        shortcut_row.addSpacing(24)
        shortcut_row.addWidget(self._shortcut_cb)
        shortcut_row.addStretch(1)
        lay.addLayout(shortcut_row)

        self._rb_new.toggled.connect(self._on_mode_toggle)

        bb = QtWidgets.QDialogButtonBox()
        bb.addButton("Install", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_mode_toggle(self, new_on):
        for w in (self._folder_lbl, self._folder_btn, self._shortcut_cb):
            w.setEnabled(new_on)

    def _choose_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Install into folder", str(self._new_parent))
        if d:
            self._new_parent = Path(d)
            self._folder_lbl.setText(d)

    def result_choice(self):
        """Return (mode, dest, make_shortcut)."""
        if self._rb_new.isChecked():
            return ('new_copy', self._new_parent, self._shortcut_cb.isChecked())
        return ('in_place', self._current_dir, False)


# === Radio source abstraction ===

# Per-Soapy-driver default sample-rate lists and gain ranges. SDRPlay
# devices share the same set; RTL-SDR is fixed at 2.4 MHz or 2.048 MHz
# in practice; HackRF can do up to 20 MHz; Airspy two rates; etc.
# These are used as the sidebar combo's options when the running source
# is the matching driver. If the device reports a different range via
# get_sample_rate_range(), the actual values are clamped at use time.
SOAPY_DEFAULTS = {
    # SDRPlay RSPx supports a set of discrete rates up to ~10 MHz.
    # SoapySDRPlay3's overall set_gain accepts 0..48 dB but it's actually
    # *gain reduction* — 0 = max gain, 48 = max attenuation. We invert at
    # the wrapper layer so the slider behaves like every other gain knob:
    # higher number = stronger signal.
    'sdrplay':  {
        'samp_rates': [2e6, 3e6, 4e6, 5e6, 6e6, 7e6, 8e6, 9e6, 10e6],
        'gain':      (0.0, 48.0, 1.0),
        'product':   "SDRPlay",
        'invert_gain': True,
    },
    'rtlsdr':   {
        'samp_rates': [0.25e6, 1.024e6, 1.4e6, 1.8e6, 1.92e6,
                       2.048e6, 2.4e6, 2.56e6, 2.8e6, 3.2e6],
        'gain':      (0.0, 49.6, 1.0),
        'product':   "RTL-SDR",
    },
    'hackrf':   {
        'samp_rates': [2e6, 4e6, 8e6, 10e6, 12.5e6, 16e6, 20e6],
        'gain':      (0.0, 47.0, 1.0),   # combined LNA + VGA approximation
        'product':   "HackRF",
    },
    'airspy':   {
        'samp_rates': [2.5e6, 10e6],
        'gain':      (0.0, 21.0, 1.0),
        'product':   "Airspy",
    },
    'airspyhf': {
        'samp_rates': [0.192e6, 0.256e6, 0.384e6, 0.768e6, 0.912e6],
        'gain':      (0.0, 48.0, 1.0),
        'product':   "Airspy HF+",
    },
    # bladeRF/Lime lists run to the 2.0-micro / LimeSDR-USB single-channel
    # ceiling (61.44 MS/s); on older hardware (bladeRF x40/x115: 40 MS/s max)
    # the use-time clamp against the device's reported range catches it.
    'bladerf':  {
        'samp_rates': [1e6, 2e6, 4e6, 8e6, 10e6, 16e6, 20e6, 25e6, 30e6,
                       40e6, 61.44e6],
        'gain':      (0.0, 60.0, 1.0),
        'product':   "BladeRF",
    },
    'lime':     {
        'samp_rates': [2e6, 4e6, 5e6, 10e6, 15e6, 20e6, 30e6, 30.72e6, 40e6,
                       61.44e6],
        'gain':      (0.0, 70.0, 1.0),
        'product':   "LimeSDR",
    },
    'plutosdr': {
        'samp_rates': [0.6e6, 1e6, 2e6, 4e6, 5e6, 8e6, 10e6, 20e6, 30.72e6, 61.44e6],
        'gain':      (0.0, 73.0, 1.0),
        'product':   "PlutoSDR",
    },
}


# --- Observation presets: validated parameter bundles keyed by observing
# goal. Each entry is (label, settings-dict-or-None); None = Manual (touch
# nothing). Keys: mode 'live'/'sweep'; band = Pulsar Band preset Hz (0 =
# Manual frequency, taken from 'manual'); rate = sample rate Hz; fmt =
# recording format; nchans/integrate = .fil geometry. Values encode
# geometries we have actually validated end-to-end (see ROADMAP and the
# Haswell trip report) — on radios that can't reach a preset's rate the
# normal clamp-and-snap path adapts it and the readback shows the truth.
# lo_off: hardware LO offset (Hz). Nonzero parks the LO off the displayed
# center so the zero-IF DC artefact lands off-target — out of the recorded
# band once |lo_off| > rate/2. The HI preset carries +2 MHz because the
# 2026-08 six-day drift scan measured the B210's DC bin 6.4 kHz (1.3 km/s)
# from the HI rest frequency — inside the line. The pulsar/magnetar bundles
# pin lo_off=0: those geometries are hardware-validated as-is, and their
# wideband folds flatten the single DC channel anyway.
OBSERVATION_PRESETS = [
    ("Pulsar — L-band", dict(
        mode='live', band=1420.406e6, rate=16e6, fmt='fil',
        nchans=2044, integrate=1, lo_off=0.0)),  # 127.7 µs; the 28σ B0329+54
                         # geometry (validated at the feed's nominal 1422.0;
                         # the preset moved onto the HI line 2026-09-13 — a
                         # 1.6 MHz center shift a wideband fold cannot see)
    ("Pulsar — UHF", dict(
        mode='live', band=0, manual=420e6, rate=20e6, fmt='fil',
        nchans=256, integrate=16, lo_off=0.0)),  # 204.8 µs; Haswell 410–430 MHz
    ("Magnetar / high-DM", dict(
        mode='live', band=1420.406e6, rate=16e6, fmt='fil',
        nchans=4096, integrate=1, lo_off=0.0)),  # narrow channels beat DM smear
    ("Hydrogen line — drift scan", dict(
        mode='live', band=0, manual=1420.406e6, rate=2e6, fmt='ezra',
        lo_off=1.5e6,    # 0.75x rate: DC artefact out of band. NOT 2.0 MHz —
                         # bench-measured 2026-08-19: an offset equal to the
                         # sample rate raises a reproducible +16 dB spur at
                         # +346 kHz on the B210; 0.75x rate is spur-free.
                         # Keep offsets away from integer multiples of rate.
        # Radios with NO LO shift stage (HackRF, RTL-SDR) can't move the
        # zero-IF DC artefact in hardware — lo_off falls back to classic
        # tuning and the spike lands ON the line. Ray Uberecken's
        # configuration (2026-09-13, his second and strongest detection)
        # does the same job in the display domain: center 1422.0 at 8 MS/s
        # keeps the line in band with the artefact 1.594 MHz (~340 km/s)
        # from any galactic gas. Applied by _resolve_observation_cfg when
        # the radio reports no LO-offset support AND can reach min_rate;
        # below that (RTL-SDR tops out at 3.2 MS/s) the line-centered
        # config stands and the spike is blanked in analysis, as before.
        alt_no_shift=dict(manual=1422.0e6, rate=8e6, min_rate=6e6))),
    ("RFI survey — sweep", dict(mode='sweep')),
    ("Manual (expert)", None),
]
OBS_MANUAL_IDX = len(OBSERVATION_PRESETS) - 1


class SoapyGenericSource(RadioSource):
    """Wraps gr-soapy's source block for any SoapySDR-recognised radio.
    Sample rate options and gain range come from SOAPY_DEFAULTS when the
    driver is known; otherwise we leave the defaults from the base class.
    The constructor signature mirrors UhdB200Source so the dispatch site
    in dses_workbench.__init__ stays uniform."""

    def __init__(self, driver: str, serial: str, samp_rate: float,
                 center_freq: float, gain: float, product: str = "",
                 antenna: str = ""):
        from gnuradio import soapy
        self._driver = driver
        self._serial = serial
        defaults = SOAPY_DEFAULTS.get(driver, {})
        self.samp_rate_options = list(defaults.get(
            'samp_rates', RadioSource.samp_rate_options))
        self.gain_range = defaults.get('gain', RadioSource.gain_range)
        # See SOAPY_DEFAULTS — some drivers (SDRplay) expose set_gain as
        # *gain reduction*, so we map the slider value through (max - x).
        self._invert_gain = bool(defaults.get('invert_gain', False))
        # Build the SoapySDR device-address string. driver= is required;
        # serial= disambiguates when multiple devices of the same driver
        # are attached.
        dev_args = f"driver={driver}"
        if serial:
            dev_args += f",serial={serial}"
        # gr-soapy source signature is positional:
        #   soapy.source(device, type, nchan, dev_args='', stream_args='',
        #                tune_args=[''], other_settings=[''])
        # We pass device-init args twice (once as device string, once in
        # dev_args) — the block accepts the redundancy.
        self.block = soapy.source(
            dev_args,                # SoapySDR device address
            "fc32",                  # complex64 output
            1,                       # 1 channel
            '',                      # dev_args (already in device string)
            '',                      # stream_args
            [''],                    # tune_args per channel
            [''],                    # other_settings per channel
        )
        # Clamp samp_rate to what the driver supports if known.
        if self.samp_rate_options and samp_rate not in self.samp_rate_options:
            # Pick the closest supported rate ≤ requested.
            below = [r for r in self.samp_rate_options if r <= samp_rate]
            samp_rate = max(below) if below else min(self.samp_rate_options)
        # Clamp gain to the driver's range.
        lo, hi, _ = self.gain_range
        gain = max(lo, min(hi, gain))
        self.block.set_sample_rate(0, samp_rate)
        self.block.set_frequency(0, center_freq)
        # Disable AGC so the user's gain slider actually takes effect.
        # SDRPlay drivers default to AGC=on and silently drop every set_gain
        # call ("Not updating IFGR gain because AGC is enabled") otherwise.
        # Not every driver supports the call; ignore if missing/unsupported.
        try:
            self.block.set_gain_mode(0, False)
        except Exception:
            pass
        # gr-soapy's set_gain(channel, value) takes overall gain in dB.
        # Some drivers (notably SDRPlay) expose multiple gain stages; the
        # overall setter applies the SoapySDR generic distribution.
        try:
            self.block.set_gain(0, self._driver_gain(float(gain)))
        except Exception as exc:
            # Some Soapy drivers fail on overall set_gain when only named
            # stages exist; don't kill the whole flow on a non-critical
            # setter failure.
            print(f"Soapy set_gain warning ({driver}): {exc}",
                  file=sys.stderr)

        # RF input ports this radio exposes (single-port radios report one,
        # e.g. RSP1B → ['RX']; the RSPduo reports its two tuners). Apply the
        # saved port if it's valid for this device.
        try:
            self.antennas = list(self.block.list_antennas(0))
        except Exception:
            self.antennas = []
        if antenna and antenna in self.antennas:
            try:
                self.block.set_antenna(0, antenna)
            except Exception as exc:
                print(f"Soapy set_antenna warning ({driver}): {exc}",
                      file=sys.stderr)
        try:
            self.current_antenna = self.block.get_antenna(0)
        except Exception:
            self.current_antenna = antenna or (
                self.antennas[0] if self.antennas else "")

        nice_name = product or defaults.get('product') or driver
        self.display_label = f"{nice_name} — {serial or '(no serial)'}"
        self.hw_info = (f"{nice_name} (s/n {serial})" if serial
                        else f"{nice_name} via SoapySDR ({driver})")

    def _driver_gain(self, slider_db: float) -> float:
        """Translate the user-facing gain value (higher = more signal) into
        whatever convention the underlying driver wants. For drivers flagged
        'invert_gain' (SDRPlay), the driver treats the number as *gain
        reduction*, so we send (max - slider). For everyone else we pass
        through unchanged."""
        if self._invert_gain:
            lo, hi, _ = self.gain_range
            return hi - (slider_db - lo)
        return slider_db

    def set_samp_rate(self, hz: float) -> None:
        # The UI clamps to samp_rate_range() (the driver's reported limits), so
        # pass the request straight through and let SoapySDR settle on the
        # nearest achievable rate; read it back with get_actual_samp_rate().
        # (Previously this snapped to the discrete preset list, which defeated
        # manual entry.)
        try:
            self.block.set_sample_rate(0, float(hz))
        except Exception as exc:
            print(f"Soapy set_sample_rate warning ({self._driver}): {exc}",
                  file=sys.stderr)

    def samp_rate_range(self):
        """The driver's reported sample-rate limits, via SoapySDR."""
        try:
            rngs = self.block.get_sample_rate_range(0)
            try:
                items = list(rngs)
            except TypeError:
                items = [rngs]
            mins, maxs = [], []
            for rg in items:
                mn = rg.minimum() if hasattr(rg, "minimum") else rg.start()
                mx = rg.maximum() if hasattr(rg, "maximum") else rg.stop()
                mins.append(float(mn))
                maxs.append(float(mx))
            if mins and maxs and max(maxs) > min(mins) > 0:
                return (min(mins), max(maxs))
        except Exception:
            pass
        return RadioSource.samp_rate_range(self)

    def freq_range(self):
        """The driver's reported RF tuning range, via SoapySDR."""
        try:
            rngs = self.block.get_frequency_range(0)
            try:
                items = list(rngs)
            except TypeError:
                items = [rngs]
            mins, maxs = [], []
            for rg in items:
                mn = rg.minimum() if hasattr(rg, "minimum") else rg.start()
                mx = rg.maximum() if hasattr(rg, "maximum") else rg.stop()
                mins.append(float(mn))
                maxs.append(float(mx))
            if mins and maxs and max(maxs) > min(mins) > 0:
                return (min(mins), max(maxs))
        except Exception:
            pass
        return None

    def get_actual_samp_rate(self) -> float:
        try:
            return float(self.block.get_sample_rate(0))
        except Exception:
            return 0.0

    # --- LO offset via SoapySDR tune components -------------------------
    # Radios whose Soapy driver exposes a DSP shift stage alongside the LO
    # (LimeSDR: 'RF'+'BB'; others vary) can park the LO off the displayed
    # center exactly like the B210: RF = center + offset, BB absorbs the
    # difference. Drivers with only an 'RF' element (HackRF, RTL-SDR) have
    # no hardware shift stage — set_lo_offset reports unsupported and the
    # radio keeps classic tuning.

    def _shift_element(self):
        try:
            els = list(self.block.list_frequencies(0))
        except Exception:
            return None
        for name in els:
            if name not in ('RF', 'CORR'):
                return name          # the DSP/BB shift stage, if any
        return None

    def lo_offset_supported(self) -> bool:
        return self._shift_element() is not None

    def set_lo_offset(self, hz: float) -> bool:
        el = self._shift_element()
        if el is None:
            self.lo_offset = 0.0
            return hz == 0.0
        cur = float(getattr(self, '_cur_freq', 0.0) or
                    self.block.get_frequency(0))
        self._cur_freq = cur
        self._lo_offset = float(hz)
        self.lo_offset = self._lo_offset
        # Open the analog filter for the offset geometry where the driver
        # allows it (same reasoning as the B210 path); best-effort.
        try:
            rate = float(self.block.get_sample_rate(0))
            self.block.set_bandwidth(0, min(56e6, 2 * abs(hz) + rate))
        except Exception:
            pass
        return self._tune_offset(el)

    def _tune_offset(self, el) -> bool:
        """Pin RF = center+offset, put the residual in the shift stage. The
        shift element's sign convention differs between drivers, so verify
        by readback and flip if needed; if neither sign lands the overall
        frequency on the requested center, fall back to classic tuning."""
        cur, off = self._cur_freq, self._lo_offset
        if not off:
            self.block.set_frequency(0, cur)
            return True
        for sign in (getattr(self, '_bb_sign', 1.0), -getattr(self, '_bb_sign', 1.0)):
            try:
                self.block.set_frequency(0, 'RF', cur + off)
                self.block.set_frequency(0, el, sign * off)
                if abs(float(self.block.get_frequency(0)) - cur) < 1.0:
                    self._bb_sign = sign
                    return True
            except Exception as exc:
                print(f"Soapy LO-offset tune failed ({self._driver}): {exc}",
                      file=sys.stderr)
                break
        # Revert to classic tuning so the radio is never left mistuned.
        try:
            self.block.set_frequency(0, el, 0.0)
        except Exception:
            pass
        self.block.set_frequency(0, cur)
        self._lo_offset = 0.0
        self.lo_offset = 0.0
        return False

    def set_center_freq(self, hz: float) -> None:
        self._cur_freq = float(hz)
        el = self._shift_element() if getattr(self, '_lo_offset', 0.0) else None
        if el is not None:
            self._tune_offset(el)
        else:
            self.block.set_frequency(0, hz)

    def set_gain(self, db: float) -> None:
        lo, hi, _ = self.gain_range
        db = max(lo, min(hi, float(db)))
        try:
            self.block.set_gain(0, self._driver_gain(db))
        except Exception as exc:
            print(f"Soapy set_gain warning ({self._driver}): {exc}",
                  file=sys.stderr)

    def set_antenna(self, name: str) -> None:
        try:
            self.block.set_antenna(0, name)
            self.current_antenna = name
        except Exception as exc:
            print(f"Soapy set_antenna warning ({self._driver}): {exc}",
                  file=sys.stderr)


def _detach_icon(dock_it):
    """Small pop-out / dock-in icon, drawn rather than taken from a font.

    Qt's standard set has no clear float/dock glyph, and unicode arrows
    render as an ambiguous dash at this size (and may be missing outright in
    the default font on macOS/Linux). Drawing it guarantees the same
    unmistakable mark on every platform: a panel outline with an arrow
    leaving it (float) or entering it (dock).
    """
    pm = QtGui.QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor("#1e3a5f"))
    pen.setWidthF(1.3)
    p.setPen(pen)
    p.drawRect(1, 4, 6, 6)                       # the panel
    if dock_it:                                  # arrow INTO the panel
        p.drawLine(10, 1, 5, 6)
        p.drawLine(5, 6, 8, 6)
        p.drawLine(5, 6, 5, 3)
    else:                                        # arrow OUT of the panel
        p.drawLine(6, 5, 10, 1)
        p.drawLine(7, 1, 10, 1)
        p.drawLine(10, 1, 10, 4)
    p.end()
    return QtGui.QIcon(pm)


def _restore_icon():
    """Double-box 'restore' mark — the universal counterpart to maximize,
    drawn rather than taken from a font so it matches _detach_icon and
    renders identically on Windows/macOS/Linux."""
    pm = QtGui.QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor("#1e3a5f"))
    pen.setWidthF(1.3)
    p.setPen(pen)
    p.drawRect(3, 1, 7, 7)      # back box
    p.fillRect(1, 4, 7, 7, QtGui.QColor("#f7fafc"))
    p.drawRect(1, 4, 7, 7)      # front box, offset — the classic restore mark
    p.end()
    return QtGui.QIcon(pm)


# Planner table: column label + the explanation you get by hovering the
# header. One entry per column so labels and help can never drift apart;
# the per-CELL tooltips, which add this row's own numbers, are built lazily
# by PulsarPlannerDialog._cell_tip (Rick, 2026-09-10: "hover over a header
# or an individual cell and get an explanation of what you are viewing").
PLANNER_COLUMNS = (
    ("Pulsar",
     "ATNF psrcat J2000 designation. A star marks a magnetar (psrcat TYPE\n"
     "AXP/SGR). Choosing a row loads the name into the recording Source\n"
     "field and hands the recorder that pulsar's EXACT catalog RA/Dec for\n"
     "the .fil header, instead of the approximate position the app would\n"
     "otherwise parse out of the name."),
    ("B name",
     "The older B1950-based designation, when the pulsar has one (B0329+54\n"
     "is the same object as J0332+5434). Either name works in the Search\n"
     "box and as a recording Source."),
    ("Alt °",
     "Altitude above the horizon at the reference time, in degrees, and\n"
     "what the elevation mask is compared against. Apparent place when\n"
     "astropy is installed; otherwise the built-in closed form, J2000\n"
     "position precessed to date (the two agree to about half an\n"
     "arcminute — hover a cell to see which one ran). No refraction and\n"
     "no mount corrections, so it is where the SKY is, not what an\n"
     "encoder will read."),
    ("Az °",
     "Azimuth of the source at the reference time: compass bearing, north\n"
     "= 0°, east = 90°, south = 180°, west = 270°. Sky position only —\n"
     "your mount's own pointing corrections still apply on top."),
    ("P0 (s)",
     "Catalog rotation period in seconds (barycentric) — the period you\n"
     "fold at. PRESTO's topocentric best period differs from it by the\n"
     "Earth's own motion, up to about one part in 10,000."),
    ("DM",
     "Dispersion measure in pc cm-3: the column of free electrons to the\n"
     "source, which delays low frequencies more than high ones as 1/f².\n"
     "Hover a cell for the delay it produces across the band you are\n"
     "tuned to and inside one of your channels — a DM whose sweep is much\n"
     "smaller than the pulse cannot be measured, only assumed."),
    ("Flux (mJy)",
     "Period-averaged flux density — the sort key for this table and the\n"
     "input to Min rec. Either the nearest catalog band quoted verbatim\n"
     "(S400 below ~900 MHz, S1400 above) or, with “Flux at tuned freq” on,\n"
     "a power-law estimate at the frequency you are tuned to, labeled\n"
     "est@MHz. Pulsar flux scintillates: factors of a few between nights\n"
     "are normal. A dash means the catalog has no flux for this source —\n"
     "not that the source is faint."),
    ("Min rec",
     "Radiometer minimum recording length for an 8-sigma folded\n"
     "detection at the current sample rate, using the site SEFD\n"
     "([site] sefd_jy — measured on Cygnus A at 1420 MHz) and the\n"
     "catalog W50 pulse width (5% duty assumed when absent). An aid,\n"
     "not a gate: one SEFD serves every band, so low-band numbers\n"
     "read optimistic, and RFI / scintillation / pointing loss add\n"
     "on top."),
    ("Best band",
     "Of the bands the dish has feeds for (the Tuning presets: 408,\n"
     "680.5, 1299.5, 1420.4, 1666 and 2304 MHz), the one where this source\n"
     "detects FASTEST — the minimum estimated time-to-8-sigma, using flux\n"
     "scaled to each band, SEFD scaled by sky temperature (galactic\n"
     "synchrotron brightens the sky at low frequency), and pulse\n"
     "broadening from channel DM smearing + empirical interstellar\n"
     "scattering (which smears high-DM sources into invisibility at low\n"
     "bands). The physics is approximate — treat it as which band to TRY\n"
     "first, not a guarantee. Best f is the same model with the feed\n"
     "list taken away."),
    ("Best f",
     "The frequency ANYWHERE from 100 MHz to 6 GHz where the same model\n"
     "detects this source fastest — what a feed built for it would want\n"
     "to be, and how far the dish's nearest real band (Best band) falls\n"
     "short; hover a cell for the comparison. Same caveats as Best band,\n"
     "plus: the flux power law and the sky model are not trusted below\n"
     "~100 MHz and real pulsar spectra turn over there, so an answer at\n"
     "the very bottom of the range means “as low as you can go”, not a\n"
     "measured optimum."),
    ("Time left",
     "How long the source stays above the elevation mask, counted from the\n"
     "reference time; “circumpolar” means it never drops below the mask in\n"
     "the next 24 h. A recording that outlasts this ends up staring at\n"
     "empty sky — the app warns about that at record time as well."),
    ("Next window",
     "For a source below the mask: how long until it clears the mask, and\n"
     "how long the window then lasts. Windows open 3 m 56 s earlier every\n"
     "day, so something that just misses tonight is easier tomorrow."),
)


class _TipItem(QtWidgets.QTableWidgetItem):
    """Table cell whose tooltip is built ON DEMAND by a callable.

    Rick, 2026-09-10: every cell should explain what you are looking at.
    Built eagerly that is ~50,000 strings for a full catalog listing (11
    columns x 4,400 rows) on every refresh, nearly all of them never read.
    Qt asks for `ToolTipRole` only when the pointer actually rests on a
    cell, so overriding `data()` moves the whole cost to the hover — and
    the answer is cached, so re-hovering the same cell is free."""

    def __init__(self, text, tip=None):
        super().__init__(text)
        self._tip_fn = tip
        self._tip_text = None

    def data(self, role):
        if role == Qt.ToolTipRole and self._tip_fn is not None:
            if self._tip_text is None:
                try:
                    self._tip_text = self._tip_fn() or ""
                except Exception as exc:      # a tooltip must never crash
                    self._tip_text = f"(no explanation available: {exc})"
            return self._tip_text
        return super().data(role)


class _NumericItem(_TipItem):
    """Table cell that DISPLAYS a formatted string but SORTS numerically.

    QTableWidgetItem's default comparison uses the display text, so
    "63.7 (S400)" sorts below "8.9 (S400)" as a string. Carrying the value
    separately and overriding __lt__ keeps the pretty text and the right
    order."""

    def __init__(self, text, value, tip=None):
        super().__init__(text, tip)
        self._value = float(value)
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, _NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class PulsarPlannerDialog(QtWidgets.QDialog):
    """"What's up now?" — pulsars above the site's elevation mask, sorted by
    flux in the tuned band, with time remaining before each sets.

    Picking one fills the recording Source field and hands back the catalog's
    exact RA/Dec, which is strictly better than the app's fallback of parsing
    an approximate position out of the pulsar's name.
    """

    def __init__(self, rows, site, center_hz, parent=None, rate_hz=2e6,
                 nchan=2048):
        super().__init__(parent)
        self.setWindowTitle("Pulsars in view")
        self.resize(1080, 600)
        self.selected = None
        self._rows = rows
        self._site = site
        self._center_hz = center_hz
        self._rate_hz = float(rate_hz or 2e6)
        self._nchan = max(1, int(nchan or 2048))
        self._ref_ts_used = time.time()
        self._setting_when = False

        # A full recompute costs ~2.5 s for a 4,400-row catalog (one astropy
        # transform plus a stepped set-time search per row), and the time and
        # mask controls both fire on every keystroke — so coalesce them.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._refresh)

        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Elevation mask:"))
        self._mask = QtWidgets.QDoubleSpinBox()
        self._mask.setRange(0.0, 89.0)
        self._mask.setDecimals(1)
        self._mask.setSuffix(" °")
        self._mask.setValue(site.get("mask_deg", 2.0))
        self._mask.setToolTip(
            "The lowest elevation you are willing to observe at. Sources\n"
            "below it drop out of the list (or are marked below-mask), and\n"
            "“Time left” counts down to this elevation, not to the horizon.")
        self._mask.valueChanged.connect(self._schedule_refresh)
        top.addWidget(self._mask)
        top.addSpacing(12)
        self._magnetars = QtWidgets.QCheckBox("Include magnetars")
        self._magnetars.setChecked(True)
        self._magnetars.setToolTip(
            "Magnetars (psrcat TYPE AXP/SGR) usually have no catalog flux, so "
            "they would vanish under any flux sort. Kept visible by default.")
        self._magnetars.toggled.connect(self._schedule_refresh)
        top.addWidget(self._magnetars)
        self._show_below = QtWidgets.QCheckBox("Include below mask")
        self._show_below.setToolTip(
            "Also list sources that are not up yet, with when their next "
            "observing window opens and how long it lasts.")
        self._show_below.toggled.connect(self._schedule_refresh)
        top.addWidget(self._show_below)
        self._flux_at_f = QtWidgets.QCheckBox("Flux at tuned freq")
        self._flux_at_f.setChecked(True)
        self._flux_at_f.setToolTip(
            "Estimate each source's flux AT the tuned frequency by power-law\n"
            "interpolation between the catalog's S400 and S1400 (a source's\n"
            "own spectral index when both exist, a typical −1.6 otherwise),\n"
            "and base “Min rec” on that. Unchecked: quote the nearest catalog\n"
            "band verbatim (S400 below ~900 MHz, S1400 above).")
        self._flux_at_f.toggled.connect(self._apply_filter)
        top.addWidget(self._flux_at_f)
        top.addStretch(1)
        self._summary = QtWidgets.QLabel("")
        top.addWidget(self._summary)
        v.addLayout(top)

        # --- reference time --------------------------------------------------
        # Rick, 2026-09-10: the planner has to answer "what will be up when I
        # get to the dish on Saturday evening", not only "what is up while I
        # stand here". Every function in pulsar_planner already took a
        # timestamp; this row is the control that was missing. It reads
        # backwards too — point it at the start of an old recording to see
        # what was overhead when the data was taken.
        tr = QtWidgets.QHBoxLayout()
        self._plan = QtWidgets.QCheckBox("Plan for:")
        self._plan.setToolTip(
            "Off: the table is the sky NOW.\n"
            "On: the table is the sky at the date and time in the box —\n"
            "altitude, azimuth, time above the mask, next window and\n"
            "viability are all computed for that instant. Editing the time,\n"
            "or using a step button, turns this on for you.")
        self._plan.toggled.connect(self._schedule_refresh)
        tr.addWidget(self._plan)
        self._when = QtWidgets.QDateTimeEdit()
        self._when.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._when.setCalendarPopup(True)
        self._when.setToolTip(
            "The instant to plan for — any date, future or past. Type in it,\n"
            "step a field with the arrow keys, or pick a date from the\n"
            "calendar button. The sky repeats 3 m 56 s earlier each day\n"
            "(sidereal drift), so a source that just misses tonight is\n"
            "easier tomorrow.")
        self._when.dateTimeChanged.connect(self._on_when_edited)
        tr.addWidget(self._when)
        self._tz = QtWidgets.QComboBox()
        self._tz.addItems(["UTC", "Local"])
        self._tz.setToolTip(
            "How to read the box: UTC — the convention in every recording\n"
            "header, ezRA file and fold this app writes — or this computer's\n"
            "local clock. Switching keeps the same instant and merely\n"
            "relabels it; tooltips name both clocks either way.")
        self._tz_prev = True                    # box is showing UTC
        self._tz.currentIndexChanged.connect(self._on_tz_changed)
        tr.addWidget(self._tz)
        for _label, _dh in (("-1 d", -24.0), ("-1 h", -1.0),
                            ("+1 h", 1.0), ("+1 d", 24.0)):
            _b = QtWidgets.QToolButton()
            _b.setText(_label)
            _b.setToolTip(f"Step the planned time by {_label}")
            _b.clicked.connect(lambda _=False, d=_dh: self._step_time(d))
            tr.addWidget(_b)
        self._now_btn = QtWidgets.QPushButton("Now")
        self._now_btn.setToolTip("Back to the live sky, and recompute")
        self._now_btn.clicked.connect(self._reset_now)
        tr.addWidget(self._now_btn)
        tr.addSpacing(10)
        self._time_note = QtWidgets.QLabel("")
        self._time_note.setToolTip(
            "The instant this whole table is computed for, on both clocks,\n"
            "plus the site's local sidereal time. LST is what really says\n"
            "where the sky is: a source crosses the meridian — its highest,\n"
            "cleanest point — when LST equals its right ascension.")
        tr.addWidget(self._time_note, 1)
        v.addLayout(tr)
        self._set_when(time.time())

        # Keep the box and the LST readout live while they show "now", so
        # that ticking "Plan for" starts from the current instant. Display
        # only: the table is never recomputed behind the user's back.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()

        srch = QtWidgets.QHBoxLayout()
        srch.addWidget(QtWidgets.QLabel("Search:"))
        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText(
            "name (B0329, J0332), or  dm<30,  p<0.1,  flux>10,  magnetar")
        self._search.setClearButtonEnabled(True)
        self._search.setToolTip(
            "Type part of a name, or filter on numbers:\n"
            "  dm<30      dm>100      p<0.1 (seconds)     p>1\n"
            "  flux>10    alt>40      magnetar\n"
            "Terms combine with AND; a bare word matches the J or B name.")
        self._search.textChanged.connect(self._apply_filter)
        srch.addWidget(self._search, 1)
        v.addLayout(srch)

        self._table = QtWidgets.QTableWidget(0, len(PLANNER_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(
            [c[0] for c in PLANNER_COLUMNS])
        for _c, (_lbl, _tip) in enumerate(PLANNER_COLUMNS):
            self._table.horizontalHeaderItem(_c).setToolTip(_tip)
        self._table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self._table.setSelectionMode(
            QtWidgets.QTableWidget.ExtendedSelection)
        self._table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._accept_row)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._table_menu)
        QtGui.QShortcut(QtGui.QKeySequence.Copy, self._table,
                        activated=self._copy_rows,
                        context=Qt.WidgetWithChildrenShortcut)
        v.addWidget(self._table, 1)

        note = QtWidgets.QLabel(
            "Sorted by flux in the tuned band. Green rows are viable: up at "
            "the reference time, with “Min rec” (the 8-σ radiometer estimate "
            "at the current sample rate) fitting inside “Time left”. "
            "<b>Hover any header or cell</b> for an explanation of what you "
            "are looking at. Ctrl+C or right-click copies rows/cells as text "
            "for reports.")
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        v.addWidget(note)

        btns = QtWidgets.QDialogButtonBox()
        self._use_btn = btns.addButton("Use as Source",
                                       QtWidgets.QDialogButtonBox.AcceptRole)
        self._geom_btn = btns.addButton("What do I need?…",
                                        QtWidgets.QDialogButtonBox.ActionRole)
        self._geom_btn.setToolTip(
            "Solve for the geometry that would make this source's DM "
            "measurable — which band and bandwidth to observe at, and what "
            "to set in the self-test simulator")
        self._geom_btn.clicked.connect(self._show_geometry_advice)
        btns.addButton(QtWidgets.QDialogButtonBox.Close)
        btns.accepted.connect(self._accept_row)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self._refresh()

    # -- reference time ----------------------------------------------------
    #
    # One instant drives the whole dialog: "now" while the Plan box is
    # clear, otherwise whatever is in the date/time box. The box holds a
    # bare wall clock and the UTC/Local combo says how to read it — the
    # conversion is done with time.gmtime/calendar.timegm rather than
    # QDateTime's time-zone API, which has moved twice across the Qt 6
    # releases this app must run on (the Pi's radioconda PySide6 is older
    # than this dev box's).

    def _tz_utc(self):
        return self._tz.currentText() == "UTC"

    def _set_when(self, ts):
        """Show an instant in the box without it counting as a user edit."""
        st = time.gmtime(ts) if self._tz_utc() else time.localtime(ts)
        self._setting_when = True
        try:
            self._when.setDateTime(QtCore.QDateTime(
                QtCore.QDate(st.tm_year, st.tm_mon, st.tm_mday),
                QtCore.QTime(st.tm_hour, st.tm_min, st.tm_sec)))
        finally:
            self._setting_when = False

    def _ts_from_box(self, utc=None):
        """The box's wall clock as a unix timestamp."""
        utc = self._tz_utc() if utc is None else utc
        q = self._when.dateTime()
        d, t_ = q.date(), q.time()
        parts = (d.year(), d.month(), d.day(),
                 t_.hour(), t_.minute(), t_.second(), 0, 1, -1)
        if utc:
            return float(calendar.timegm(parts))
        return float(time.mktime(parts))      # -1 lets mktime settle DST

    def _ref_ts(self):
        """The instant the table is computed for."""
        return self._ts_from_box() if self._plan.isChecked() else time.time()

    def _on_when_edited(self, *_):
        """A hand-edited time means the user wants to plan — say so for
        them rather than making them find the checkbox first."""
        if self._setting_when:
            return
        if not self._plan.isChecked():
            self._plan.setChecked(True)       # toggled -> _schedule_refresh
        else:
            self._schedule_refresh()

    def _on_tz_changed(self, *_):
        """Same instant, other clock: read the box in the zone it was
        showing, then re-render it in the newly chosen one. No recompute —
        tooltips name both clocks, so nothing in the table changes."""
        ts = self._ts_from_box(utc=self._tz_prev)
        self._tz_prev = self._tz_utc()
        self._set_when(ts)
        self._update_time_note()

    def _step_time(self, hours):
        base = self._ref_ts()
        self._plan.blockSignals(True)         # one refresh, not two
        self._plan.setChecked(True)
        self._plan.blockSignals(False)
        self._set_when(base + hours * 3600.0)
        self._schedule_refresh()

    def _reset_now(self):
        self._plan.blockSignals(True)
        self._plan.setChecked(False)
        self._plan.blockSignals(False)
        self._set_when(time.time())
        self._schedule_refresh()

    def _tick_clock(self):
        """Keep the box and the LST readout current while they show "now".

        Not while the box has focus: the first keystroke or calendar click
        switches to planning mode, but until that signal lands the user is
        typing into a field this timer would otherwise overwrite."""
        if self._plan.isChecked() or self._when.hasFocus():
            return
        self._set_when(time.time())
        self._update_time_note()

    def _schedule_refresh(self, *_):
        self._update_time_note()
        self._debounce.start()

    def _update_time_note(self):
        import pulsar_planner
        ts = self._ref_ts()
        lst = pulsar_planner.lst_hours(ts, self._site["lon_deg"])
        stamp = f"{self._fmt_clock(ts)}  ·  LST {self._fmt_lst(lst)}"
        if self._plan.isChecked():
            dh = (ts - time.time()) / 3600.0
            off = (f"{dh:+.1f} h" if abs(dh) < 48.0 else f"{dh / 24.0:+.1f} d")
            self._time_note.setText(f"PLANNED  {stamp}  ({off} from now)")
            self._time_note.setStyleSheet("color: #8a5000; font-weight: bold;")
        else:
            self._time_note.setText(f"Now  {stamp}")
            self._time_note.setStyleSheet("color: gray;")

    # -- formatting helpers ------------------------------------------------

    @staticmethod
    def _fmt_clock(ts, with_date=True):
        """"2026-09-12 03:00 UTC (21:00 local)" — both clocks, always, so a
        tooltip is unambiguous whichever one the box is set to."""
        u, l = time.gmtime(ts), time.localtime(ts)
        uf = time.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M", u)
        same_day = (u.tm_year, u.tm_mon, u.tm_mday) == (l.tm_year, l.tm_mon,
                                                        l.tm_mday)
        lf = time.strftime("%H:%M" if same_day else "%m-%d %H:%M", l)
        return f"{uf} UTC ({lf} local)"

    @staticmethod
    def _fmt_ms(ms):
        """Milliseconds at a readable scale — channel smearing at 2048
        channels is microseconds, and "0.000 ms" says nothing."""
        if ms >= 1.0:
            return f"{ms:.2f} ms"
        if ms >= 0.001:
            return f"{ms * 1000.0:.0f} µs"
        return "under 1 µs"

    @staticmethod
    def _fmt_lst(hours):
        return f"{int(hours):02d}h {int((hours % 1) * 60):02d}m"

    @staticmethod
    def _fmt_packed(v, ra=False):
        """SIGPROC packed sexagesimal (33259.3) -> "03:32:59.3"."""
        neg = v < 0
        v = abs(v)
        d = int(v // 1e4)
        m = int((v - d * 1e4) // 1e2)
        sec = v - d * 1e4 - m * 1e2
        sign = "" if ra else ("-" if neg else "+")
        return f"{sign}{d:02d}:{m:02d}:{sec:04.1f}"

    _COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

    @classmethod
    def _compass(cls, az_deg):
        return cls._COMPASS[int((az_deg % 360.0) / 22.5 + 0.5) % 16]

    @staticmethod
    def _wrap(paras, width=74):
        """Tooltip body: each paragraph wrapped onto its own line(s) (an
        empty entry becomes a blank line). Qt does not reflow plain-text
        tooltips, and a 300-character line makes one wider than the
        screen."""
        import textwrap
        return "\n".join("" if not p else textwrap.fill(p, width)
                         for p in paras)

    def _selected_row(self):
        row = self._table.currentRow()
        if row < 0:
            return None
        name_cell = self._table.item(row, 0).text().replace("  ★", "")
        for r in self._visible:
            if r["name"] == name_cell:
                return r
        return None

    def _show_geometry_advice(self):
        """Invert the dispersion arithmetic for the selected source: what
        frequency/bandwidth actually resolves its DM (Rick 2026-08-06, after
        B0950+08 railed to DM 0 at 420 MHz but measured cleanly at 160)."""
        import pulsar_sim
        r = self._selected_row()
        if r is None:
            QtWidgets.QMessageBox.information(
                self, "No pulsar selected", "Select a row first.")
            return
        if not r.get("p0_s"):
            QtWidgets.QMessageBox.information(
                self, "No period in catalog",
                f"{r['name']} has no catalog period, so its geometry cannot "
                f"be solved.")
            return
        name = r["bname"] if r.get("bname") and r["bname"] != "*" \
            else r["name"]
        rec = pulsar_sim.recommend_geometry(
            float(r["p0_s"]), float(r.get("dm") or 0.0), name=name)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Geometry for {name}")
        lay = QtWidgets.QVBoxLayout(dlg)
        head = QtWidgets.QLabel(
            "Dispersion delay goes as 1/frequency², so a source's DM is only "
            "measurable when the sweep across your band is a decent fraction "
            "of the pulse width. This solves for what that takes.")
        head.setWordWrap(True)
        lay.addWidget(head)
        text = QtWidgets.QPlainTextEdit("\n".join(rec["lines"]))
        text.setReadOnly(True)
        f = QtGui.QFont("Consolas")
        f.setStyleHint(QtGui.QFont.Monospace)
        text.setFont(f)
        text.setMinimumSize(720, 340)
        lay.addWidget(text)
        note = QtWidgets.QLabel(
            "A source whose DM cannot be resolved is still perfectly "
            "detectable — fold at the catalog DM and report the DM as "
            "unconstrained rather than as a measurement.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(780, 520)
        dlg.exec()

    def _refresh(self):
        import pulsar_planner
        self._debounce.stop()               # a manual call satisfies it
        ts = self._ref_ts()
        self._ref_ts_used = ts
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            vis = pulsar_planner.visible_now(
                self._rows, self._site["lat_deg"], self._site["lon_deg"],
                mask_deg=self._mask.value(), center_hz=self._center_hz,
                include_magnetars=self._magnetars.isChecked(),
                height_m=self._site.get("amsl", 0.0),
                include_below=self._show_below.isChecked(),
                unix_ts=ts)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._all = vis
        self._update_time_note()
        # Loud in the title bar as well: a screenshot of a planned table
        # must not be mistaken for the live sky.
        self.setWindowTitle(
            "Pulsars in view" + (f" — planned for {self._fmt_clock(ts)}"
                                 if self._plan.isChecked() else ""))
        self._apply_filter()

    # -- search ------------------------------------------------------------

    @staticmethod
    def _matches(row, query):
        """Free-text + numeric filter. Bare words match the J/B name;
        `dm<30`, `p>1`, `flux>10`, `alt>40` compare numerically; the word
        `magnetar` keeps only magnetars. Terms combine with AND."""
        import re as _re
        for term in query.lower().split():
            m = _re.fullmatch(r"(dm|p|p0|flux|alt|az)([<>]=?)(-?\d+\.?\d*)",
                              term)
            if m:
                key, op, val = m.group(1), m.group(2), float(m.group(3))
                got = {"dm": row.get("dm"), "p": row.get("p0_s"),
                       "p0": row.get("p0_s"), "flux": row.get("flux_mjy"),
                       "alt": row.get("alt_deg"), "az": row.get("az_deg")}[key]
                if got is None:
                    return False        # can't satisfy a numeric test
                if op == "<" and not got < val:   return False
                if op == "<=" and not got <= val: return False
                if op == ">" and not got > val:   return False
                if op == ">=" and not got >= val: return False
                continue
            if term == "magnetar":
                if not row.get("magnetar"):
                    return False
                continue
            hay = (row["name"] + " " + (row.get("bname") or "")).lower()
            if term not in hay.replace(" ", "") and term not in hay:
                return False
        return True

    @staticmethod
    def _fmt_duration(sec):
        if sec < 90:
            return f"{sec:.0f} s"
        if sec < 5400:
            return f"{sec / 60:.0f} min"
        return f"{sec / 3600:.1f} h"

    def _apply_filter(self):
        import pulsar_planner
        q = self._search.text().strip()
        vis = ([r for r in self._all if self._matches(r, q)] if q
               else list(self._all))
        self._visible = vis
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(vis))
        viable_bg = QtGui.QBrush(QtGui.QColor("#d9f2d9"))   # soft green
        viable_fg = QtGui.QBrush(QtGui.QColor("#0a3d0a"))
        n_viable = 0
        sefd = self._site.get("sefd_jy") or 4000.0
        planned = self._plan.isChecked()
        then = "then" if planned else "now"
        ref_ts = self._ref_ts_used
        for i, r in enumerate(vis):
            hrs = r["hours_left"]
            left = ("circumpolar" if hrs >= 23.99
                    else f"{int(hrs)}h {int((hrs % 1) * 60):02d}m")
            # Flux: either the nearest catalog band verbatim, or the
            # power-law estimate at the tuned frequency (checkbox).
            if self._flux_at_f.isChecked():
                fmjy, flabel = pulsar_planner.flux_at_freq(
                    r, self._center_hz)
            else:
                fmjy, flabel = r["flux_mjy"], r["flux_label"]
            flux = "—" if fmjy is None else f"{fmjy:.1f} ({flabel})"
            if r.get("below_mask"):
                left = "— below mask —"
                rise = r.get("rise_in_h")
                win = r.get("window_h") or 0.0
                nxt = ("never (this day)" if rise is None
                       else f"in {int(rise)}h {int((rise % 1) * 60):02d}m"
                            f"  ·  {win:.1f} h long")
                nxt_sort = 1e6 if rise is None else rise
            else:
                nxt, nxt_sort = f"up {then}", -1.0
            # Minimum recording duration (8-sigma folded, radiometer) at the
            # current sample rate — compared against time-above-mask to call
            # a target viable. The header tooltip states the assumptions.
            tmin = pulsar_planner.min_duration_s(
                fmjy, r["p0_s"], sefd, self._rate_hz,
                w50_ms=r.get("w50_ms"))
            bf, bt = pulsar_planner.best_band_mhz(r, sefd, self._rate_hz)
            ff, ft = pulsar_planner.best_freq_mhz(r, sefd, self._rate_hz)
            viable = (tmin is not None and not r.get("below_mask")
                      and tmin <= hrs * 3600.0)
            if viable:
                n_viable += 1
            # Everything a per-cell explanation needs that is not in the
            # catalog row itself. Cheap to build; the TEXT is not built
            # until the pointer rests on the cell (see _TipItem).
            ctx = {"ts": ref_ts, "sefd": sefd, "flux_mjy": fmjy,
                   "flux_label": flabel, "tmin": tmin, "best_f": bf,
                   "best_t": bt, "free_f": ff, "free_t": ft, "hrs": hrs,
                   "viable": viable}

            def item(text, col, sort_value=None, r=r, ctx=ctx):
                tip = lambda: self._cell_tip(r, col, ctx)   # noqa: E731
                if sort_value is None:
                    return _TipItem(text, tip)
                return _NumericItem(text, sort_value, tip)

            cells = [
                item(r["name"] + ("  ★" if r["magnetar"] else ""), 0),
                item("" if r["bname"] == "*" else r["bname"], 1),
                item(f"{r['alt_deg']:.1f}", 2, r["alt_deg"]),
                item(f"{r['az_deg']:.1f}", 3, r["az_deg"]),
                item("—" if r["p0_s"] is None else f"{r['p0_s']:.6f}", 4,
                     r["p0_s"] or 0.0),
                item("—" if r["dm"] is None else f"{r['dm']:.2f}", 5,
                     r["dm"] or 0.0),
                item(flux, 6, fmjy if fmjy is not None else -1.0),
                item("—" if tmin is None else self._fmt_duration(tmin), 7,
                     tmin if tmin is not None else 1e12),
                item("—" if bf is None else f"{bf:.10g}", 8,
                     bf if bf is not None else 1e12),
                item("—" if ff is None else f"{ff:.0f}", 9,
                     ff if ff is not None else 1e12),
                item(left, 10, hrs) if not r.get("below_mask")
                else item(left, 10),
                item(nxt, 11, nxt_sort),
            ]
            for c, it in enumerate(cells):
                if viable:
                    it.setBackground(viable_bg)
                    it.setForeground(viable_fg)
                self._table.setItem(i, c, it)
        # Sort by flux, brightest first — the order an observer wants. Qt
        # sorts on the display string unless a numeric UserRole is set, so
        # the numeric columns carry one (see `item` above); enabling sorting
        # after the fill would otherwise re-sort by column 0 (name).
        self._table.setSortingEnabled(True)
        self._table.sortItems(6, Qt.DescendingOrder)
        self._table.resizeColumnsToContents()
        n_mag = sum(1 for r in vis if r["magnetar"])
        n_up = sum(1 for r in vis if not r.get("below_mask"))
        parts = [f"{n_up} above {self._mask.value():.0f}°"]
        if n_viable:
            parts.append(f"{n_viable} viable {then}")
        if len(vis) != n_up:
            parts.append(f"{len(vis) - n_up} below")
        if n_mag:
            parts.append(f"{n_mag} magnetar{'s' if n_mag != 1 else ''} (★)")
        if self._search.text().strip():
            parts.append(f"filtered from {len(self._all)}")
        self._summary.setText("  ·  ".join(parts))

    # -- per-cell explanations ---------------------------------------------

    def _cell_tip(self, r, col, ctx):
        """What ONE cell means: the column's job, plus what this row's own
        number is telling you. Built on hover (see _TipItem), so the text
        can afford to do real arithmetic — the DM sweep across the tuned
        band, the source's transit time, the set clock time — instead of
        repeating the header."""
        import math
        import pulsar_planner as pp
        site = self._site
        ts = ctx["ts"]
        mask = self._mask.value()
        at = ("the planned " if self._plan.isChecked() else "") \
            + self._fmt_clock(ts)
        bname = r.get("bname") or "*"
        p0, dm, w50 = r.get("p0_s"), r.get("dm"), r.get("w50_ms")
        w_ms = (w50 if (w50 and w50 > 0)
                else (0.05 * p0 * 1000.0 if p0 else None))
        below = bool(r.get("below_mask"))
        paras = []

        if col == 0:                                        # Pulsar
            paras.append(r["name"] + ("" if bname == "*" else f"   =   {bname}")
                         + "   —   ATNF psrcat J2000 designation.")
            paras.append(
                f"Catalog position (J2000): RA {self._fmt_packed(r['raj'], ra=True)}, "
                f"Dec {self._fmt_packed(r['decj'])}. Choosing this row hands the "
                f"recorder that exact position for the .fil header, instead of the "
                f"approximate one the app would parse out of the name.")
            if r.get("magnetar"):
                paras.append(
                    f"Magnetar (psrcat TYPE {r.get('type') or 'AXP/SGR'}) — marked "
                    f"with a star, and never dropped by a flux filter, because the "
                    f"catalog usually carries no flux for these.")
            paras.append("Double-click the row, or “Use as Source”, to load it "
                         "into the recording panel.")

        elif col == 1:                                      # B name
            if bname == "*":
                paras.append("This pulsar has no B1950-era designation — only "
                             "its J2000 name. Nothing is missing.")
            else:
                paras.append(
                    f"{bname} is the older B1950-based name for {r['name']} — the "
                    f"same object. Most observing notes and papers before ~1993 use "
                    f"this form, and either name works in the Search box and as a "
                    f"recording Source.")

        elif col == 2:                                      # Alt
            if below:
                paras.append(
                    f"Altitude {r['alt_deg']:.1f}° at {at} — BELOW your {mask:.1f}° "
                    f"elevation mask, so there is nothing to record yet. “Next "
                    f"window” says when that changes.")
            else:
                paras.append(
                    f"Altitude {r['alt_deg']:.1f}° above the horizon at {at}; your "
                    f"elevation mask is {mask:.1f}°.")
            culm = pp.max_alt_deg(r["dec_deg"], site["lat_deg"],
                                  ra_deg=r["ra_deg"], unix_ts=ts)
            if culm <= 0.0:
                paras.append(
                    f"From this latitude the source never clears the horizon at all "
                    f"(it culminates {abs(culm):.1f}° below it) — no time of day or "
                    f"year helps.")
            else:
                th = pp.next_transit_h(r["ra_deg"], site["lon_deg"], ts,
                                       dec_deg=r["dec_deg"])
                paras.append(
                    f"It culminates at {culm:.1f}° from this site, and next crosses "
                    f"the meridian — highest, least atmosphere, best place to catch "
                    f"it — in {th:.1f} h, at {self._fmt_clock(ts + th * 3600.0)}.")
            if pp.LAST_ENGINE == "astropy":
                paras.append(
                    "Computed with astropy: apparent place (precession, nutation, "
                    "aberration) using the Earth-orientation table bundled with "
                    "astropy — no download; 0.06\" from a freshly fetched one. No "
                    "refraction and no mount corrections, so this is where the SKY "
                    "is, not what your encoders will read.")
            else:
                paras.append(
                    "Computed with the built-in closed form (astropy is not "
                    "installed here): the J2000 catalog position precessed to "
                    "date, without nutation or aberration — good to about half an "
                    "arcminute, 1/80 of the beam. No refraction and no mount "
                    "corrections, so this is where the SKY is, not what your "
                    "encoders will read.")

        elif col == 3:                                      # Az
            paras.append(
                f"Azimuth {r['az_deg']:.1f}° at {at} — bearing {self._compass(r['az_deg'])}, "
                f"measuring north = 0°, east = 90°, south = 180°, west = 270°.")
            paras.append(
                "Sky position only: your mount's own pointing corrections (encoder "
                "offsets, boresight tilt) still apply on top of this number.")

        elif col == 4:                                      # P0
            if p0 is None:
                paras.append(
                    "The catalog has no rotation period for this source, so it "
                    "cannot be folded blind, and Min rec / Best f cannot be "
                    "estimated. Search it out in the literature, or record and "
                    "search for the period yourself.")
            else:
                paras.append(
                    f"Rotation period {p0:.6f} s ({1.0 / p0:.3f} Hz) — the period you "
                    f"fold at. It is barycentric; PRESTO's topocentric best period "
                    f"differs by the Earth's own motion, up to about one part in "
                    f"10,000, which is why folds are quoted -topo or barycentered.")
                if w50 and w50 > 0:
                    paras.append(
                        f"Catalog W50 (width at half maximum) is {w50:.1f} ms = "
                        f"{100.0 * w50 / 1000.0 / p0:.2f}% of the period. A narrow "
                        f"pulse concentrates the same average flux into less time, "
                        f"which is why Min rec falls as the duty cycle falls.")
                else:
                    paras.append(
                        f"The catalog has no W50 for this source, so a 5% duty cycle "
                        f"({0.05 * p0 * 1000.0:.1f} ms) is assumed wherever a pulse "
                        f"width is needed — deliberately conservative.")

        elif col == 5:                                      # DM
            if dm is None:
                paras.append("No dispersion measure in the catalog: fold at a "
                             "searched DM, or take the value from a paper.")
            else:
                import pulsar_sim
                lo = (self._center_hz - self._rate_hz / 2.0) / 1e6
                hi = (self._center_hz + self._rate_hz / 2.0) / 1e6
                sweep_ms = pulsar_sim.sweep_per_dm_s(
                    self._center_hz, self._rate_hz) * dm * 1e3
                chan_ms = pulsar_sim.channel_smear_s(
                    dm, self._rate_hz / self._nchan, self._center_hz) * 1e3
                paras.append(
                    f"Dispersion measure {dm:.2f} pc cm-3 — the column of free "
                    f"electrons between here and the source. It delays low "
                    f"frequencies more than high ones, as 1/f².")
                paras.append(
                    f"Across the band you are tuned to ({lo:.3f}–{hi:.3f} MHz) that "
                    f"is a {self._fmt_ms(sweep_ms)} delay, bottom edge to top; inside "
                    f"one of your {self._nchan} channels it smears the pulse by "
                    f"{self._fmt_ms(chan_ms)} (no dedispersion undoes that part).")
                if w_ms:
                    if sweep_ms < 0.5 * w_ms:
                        paras.append(
                            f"The pulse is about {w_ms:.1f} ms wide, wider than the "
                            f"sweep, so a fold here CANNOT measure this DM — its DM "
                            f"search slides toward zero and the result means nothing. "
                            f"Fold at the catalog value and report the DM as "
                            f"unconstrained; “What do I need?…” solves for a band and "
                            f"bandwidth that could measure it.")
                    elif chan_ms > 0.5 * w_ms:
                        paras.append(
                            f"Channel smearing ({self._fmt_ms(chan_ms)}) is a large "
                            f"fraction of the {w_ms:.1f} ms pulse — use more "
                            f"filterbank channels, or a higher band, or the pulse "
                            f"arrives flattened however long you record.")
                    else:
                        paras.append(
                            f"The sweep is a usable fraction of the {w_ms:.1f} ms "
                            f"pulse, so a fold can genuinely optimize DM at this "
                            f"tuning.")

        elif col == 6:                                      # Flux
            s400, s1400 = r.get("s400"), r.get("s1400")
            f_mhz = self._center_hz / 1e6
            if ctx["flux_mjy"] is None:
                paras.append(
                    "The catalog carries no flux density for this source — common "
                    "for magnetars and for faint recent discoveries. That is silence "
                    "in the catalog, NOT a faint source, so nothing here is hidden by "
                    "a flux filter; but Min rec and Best f cannot be estimated.")
            elif self._flux_at_f.isChecked():
                anchors = ", ".join(
                    p for p in (f"S400 = {s400:.1f} mJy" if s400 else "",
                                f"S1400 = {s1400:.1f} mJy" if s1400 else "") if p)
                if s400 and s1400 and s400 > 0 and s1400 > 0:
                    alpha = math.log(s1400 / s400) / math.log(1400.0 / 400.0)
                    how = (f"this source's own spectral index, {alpha:+.2f}, from "
                           f"{anchors}")
                else:
                    how = (f"a typical pulsar index of -1.6 applied to the one "
                           f"anchor the catalog gives ({anchors})")
                paras.append(
                    f"About {ctx['flux_mjy']:.1f} mJy at the {f_mhz:.3f} MHz you are "
                    f"tuned to — a power-law ESTIMATE (label {ctx['flux_label']}) "
                    f"using {how}.")
                paras.append(
                    "Real spectra turn over below a few hundred MHz and the estimate "
                    "does not know that. Untick “Flux at tuned freq” to see the "
                    "catalog number verbatim instead.")
            else:
                paras.append(
                    f"Catalog {ctx['flux_label']} = {ctx['flux_mjy']:.1f} mJy, quoted "
                    f"as measured at {'400' if ctx['flux_label'] == 'S400' else '1400'} "
                    f"MHz — the nearest catalog band to your {f_mhz:.3f} MHz tuning, "
                    f"not scaled to it. Tick “Flux at tuned freq” to scale it.")
            paras.append(
                "Flux here is PERIOD-AVERAGED (much lower than the peak of the "
                "pulse), it is the sort key for this table, and it is what Min rec "
                "is computed from. Pulsar flux also scintillates — factors of a few "
                "between nights are normal at L band.")

        elif col == 7:                                      # Min rec
            tmin = ctx["tmin"]
            if tmin is None:
                paras.append(
                    "Not estimable: the radiometer equation needs both a flux "
                    "density and a period, and the catalog is missing one of them "
                    "for this source.")
            else:
                width = (f"catalog W50 {w50:.1f} ms "
                         f"(duty {100.0 * w50 / 1000.0 / p0:.2f}%)"
                         if (w50 and w50 > 0 and p0)
                         else "5% duty assumed (no catalog W50)")
                paras.append(
                    f"About {self._fmt_duration(tmin)} of recording to reach an "
                    f"8-sigma FOLDED detection from this site.")
                paras.append(
                    f"Inputs: flux {ctx['flux_mjy']:.1f} mJy, {width}, SEFD "
                    f"{ctx['sefd']:.0f} Jy ([site] sefd_jy — measured on Cygnus A at "
                    f"1420 MHz), bandwidth {self._rate_hz / 1e6:.3f} MHz, one "
                    f"polarization.")
                if below:
                    paras.append(
                        "The source is below the mask at the reference time, so there "
                        "is no window to compare this against yet — see Next window.")
                elif ctx["viable"]:
                    room = ("the source is circumpolar at this mask"
                            if ctx["hrs"] >= 23.99 else
                            f"{self._fmt_duration(ctx['hrs'] * 3600.0)} remain above "
                            f"the mask")
                    paras.append(f"It fits: {room}, so this row is highlighted "
                                 f"green.")
                else:
                    paras.append(
                        f"It does NOT fit the {self._fmt_duration(ctx['hrs'] * 3600.0)} "
                        f"left above the mask — try the Best f band, more bandwidth, "
                        f"or a different night (the Plan-for box will show you one).")
                paras.append(
                    "An aid, not a gate: one SEFD serves every band, so low-band "
                    "numbers read optimistic, and RFI, scintillation and pointing "
                    "loss all add on top.")

        elif col == 8:                                      # Best band
            bf, bt, tmin = ctx["best_f"], ctx["best_t"], ctx["tmin"]
            if bf is None:
                paras.append(
                    "No band can be recommended without a catalog period and flux "
                    "for this source.")
            else:
                line = (f"Of the bands the dish has feeds for, {bf:.10g} MHz detects "
                        f"this source fastest: about {self._fmt_duration(bt)} there")
                if tmin is not None:
                    line += (f", against {self._fmt_duration(tmin)} at the "
                             f"{self._center_hz / 1e6:.3f} MHz you are tuned to now")
                paras.append(line + ".")
                paras.append(
                    "Picked over the Tuning presets (408, 680.5, 1299.5, 1420.4, 1666, "
                    "2304 MHz) by scaling this source's flux to each band, scaling "
                    "SEFD by sky temperature (the galaxy is far brighter at low "
                    "frequency), and broadening the pulse by channel DM smearing plus "
                    "empirical interstellar scattering — which is why high-DM sources "
                    "are kept high and steep-spectrum low-DM ones are sent low. Best f "
                    "runs the same model with the feed list taken away.")
                paras.append(
                    f"Assumes the current {self._rate_hz / 1e6:.3f} MHz bandwidth and "
                    f"256 channels. Approximate physics: the band to TRY first, not a "
                    f"guarantee.")

        elif col == 9:                                      # Best f (any)
            ff, ft = ctx["free_f"], ctx["free_t"]
            bf, bt = ctx["best_f"], ctx["best_t"]
            if ff is None:
                paras.append(
                    "No frequency can be recommended without a catalog period and "
                    "flux for this source.")
            else:
                lo, hi = pp.BEST_F_RANGE_MHZ
                paras.append(
                    f"If you could tune anywhere, about {ff:.0f} MHz detects this "
                    f"source fastest: about {self._fmt_duration(ft)} there.")
                if bf is not None and bt is not None and ft and ft > 0:
                    ratio = bt / ft
                    if ratio < 1.05:
                        paras.append(
                            f"The dish's best real band, {bf:.10g} MHz, is as good as it "
                            f"gets ({self._fmt_duration(bt)}) — a purpose-built feed "
                            f"would buy nothing here.")
                    else:
                        paras.append(
                            f"The dish's best real band, {bf:.10g} MHz, needs {ratio:.1f}x "
                            f"longer ({self._fmt_duration(bt)}) — that factor is what a "
                            f"feed built for this source would buy.")
                if ff <= lo * 1.01 or ff >= hi * 0.99:
                    end, way = (("bottom", "low") if ff <= lo * 1.01
                                else ("top", "high"))
                    paras.append(
                        f"This sits at the {end} of the searched range ({lo:.0f} MHz to "
                        f"{hi / 1000.0:.0f} GHz): the model wants to keep going, so read "
                        f"it as “as {way} as you can go”, not a measured optimum.")
                paras.append(
                    "Same model as Best band — flux scaled by this source's spectral "
                    "index, SEFD scaled by sky temperature, pulse broadened by channel "
                    "DM smearing and interstellar scattering — searched in 5% steps "
                    "from 100 MHz to 6 GHz and refined. Real pulsar spectra turn over "
                    "below a few hundred MHz and the model does not know that: treat a "
                    "very low answer with suspicion.")

        elif col == 10:                                     # Time left
            if below:
                paras.append(
                    f"Below the {mask:.1f}° mask at {at}, so there is no time above "
                    f"it to count. See Next window for the wait.")
            elif ctx["hrs"] >= 23.99:
                paras.append(
                    f"Circumpolar at this mask: from {at} it does not drop below "
                    f"{mask:.1f}° at any point in the next 24 h, so the recording "
                    f"length is your choice, not the sky's.")
            else:
                paras.append(
                    f"{self._fmt_duration(ctx['hrs'] * 3600.0)} above the {mask:.1f}° "
                    f"mask from {at} — it crosses the mask at "
                    f"{self._fmt_clock(ts + ctx['hrs'] * 3600.0)}.")
                paras.append(
                    "A recording that outlasts this ends up staring at empty sky. If "
                    "a Source and a “Record for” duration are both set, the app warns "
                    "about that at record time too.")

        elif col == 11:                                     # Next window
            if not below:
                paras.append(
                    f"Up at {at} — this column is for sources below the mask, where "
                    f"it gives the wait until they clear it and how long the window "
                    f"then lasts.")
            else:
                rise = r.get("rise_in_h")
                win = r.get("window_h") or 0.0
                if rise is None:
                    paras.append(
                        f"It does not clear the {mask:.1f}° mask at any point in the "
                        f"24 h after {at} — too far south for this latitude, or the "
                        f"mask is set higher than the source ever reaches.")
                else:
                    paras.append(
                        f"Rises above {mask:.1f}° in {self._fmt_duration(rise * 3600.0)} "
                        f"— at {self._fmt_clock(ts + rise * 3600.0)} — and then stays "
                        f"up {win:.1f} h.")
                    paras.append(
                        "Set “Plan for” to inside that window to see the source's "
                        "altitude, azimuth and viability as they will be then.")
            paras.append(
                "Windows open 3 m 56 s earlier every day (sidereal drift), so "
                "something that just misses tonight is a little easier tomorrow.")

        return self._wrap(paras)

    # -- clipboard ---------------------------------------------------------

    def _copy_context(self):
        """One comment line naming the site, the instant and the settings the
        copied numbers belong to. A table pasted into a report is worse than
        useless if nobody can tell what time it was for."""
        import pulsar_planner
        ts = self._ref_ts_used
        lst = pulsar_planner.lst_hours(ts, self._site["lon_deg"])
        return ("# Pulsars in view — "
                f"{self._site.get('name') or 'site'}, "
                f"{'planned for ' if self._plan.isChecked() else ''}"
                f"{self._fmt_clock(ts)}, LST {self._fmt_lst(lst)}, "
                f"mask {self._mask.value():.1f} deg, tuned "
                f"{self._center_hz / 1e6:.3f} MHz at "
                f"{self._rate_hz / 1e6:.3f} MS/s")

    def _headers(self):
        return [self._table.horizontalHeaderItem(c).text()
                for c in range(self._table.columnCount())]

    def _copy_rows(self):
        """Ctrl+C / context menu: selected rows as tab-separated text with a
        header line — pastes cleanly into email, Excel, or a report table."""
        rows = sorted({ix.row() for ix in self._table.selectedIndexes()})
        if not rows:
            return
        lines = [self._copy_context(), "\t".join(self._headers())]
        for rr in rows:
            lines.append("\t".join(
                (self._table.item(rr, c).text() if self._table.item(rr, c)
                 else "") for c in range(self._table.columnCount())))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _table_menu(self, pos):
        it = self._table.itemAt(pos)
        menu = QtWidgets.QMenu(self._table)
        if it is not None:
            act_cell = menu.addAction(f"Copy cell  ({it.text()})")
            act_cell.triggered.connect(
                lambda: QtWidgets.QApplication.clipboard().setText(it.text()))
        n_sel = len({ix.row() for ix in self._table.selectedIndexes()})
        act_rows = menu.addAction(
            f"Copy row{'s' if n_sel > 1 else ''} ({max(n_sel, 1)})")
        act_rows.triggered.connect(self._copy_rows)
        act_all = menu.addAction("Copy whole table")
        act_all.triggered.connect(self._copy_all)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_all(self):
        lines = [self._copy_context(), "\t".join(self._headers())]
        for rr in range(self._table.rowCount()):
            lines.append("\t".join(
                (self._table.item(rr, c).text() if self._table.item(rr, c)
                 else "") for c in range(self._table.columnCount())))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _accept_row(self, *_):
        r = self._selected_row()
        if r is None:
            QtWidgets.QMessageBox.information(
                self, "No pulsar selected",
                "Select a row first, or double-click one.")
            return
        self.selected = r
        self.accept()


# === B210 built-in self test (Observe menu) ===
#
# Bench-proven parameters (2026-08-05, s/n 8003886): internal TX->RX leakage
# at MINIMUM TX gain carries the test — no cable, no pad. 420 MHz keeps the
# transmission far from the protected 1420 MHz band. The 11.2 ms dispersion
# sweep against 2 ms pulses gives prepfold real DM leverage (+/-4.5); the
# first bench run recovered DM 51.45 of 50 injected at chi2 1118.
SELFTEST_FREQ_HZ = 420e6
SELFTEST_RATE_HZ = 2e6
SELFTEST_PERIOD_S = 0.1
SELFTEST_DM = 50.0
SELFTEST_DUTY = 0.02
SELFTEST_AMP = 0.5
SELFTEST_RX_GAIN_DB = 40.0
SELFTEST_NCHANS = 128
SELFTEST_SOURCE_NAME = "B210BIT"


class B210SelfTestDialog(QtWidgets.QDialog):
    """Modal driver for the B210 built-in test: the main window hosts the
    actual TX/RX flowgraph surgery (_selftest_* methods); this dialog is the
    countdown, progress, and PASS/FAIL readout. Modal on purpose — every
    control that could disturb the test (retune, rate, record) lives in the
    main window, and 90 s of hands-off is exactly what a self test wants."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._phase = "idle"        # idle -> capture -> analyze -> done
        self._left = 0
        self.setWindowTitle("B210 Self Test")
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Transmits a synthetic dispersed pulsar from this B210's own TX "
            "at <b>minimum gain</b> and records it off the internal TX→RX "
            "leakage — <b>no cable needed</b>. The recording is folded with "
            "PRESTO and graded against the injected truth, proving the whole "
            "chain — SDR, channelizer, writer, timebase, PRESTO — before "
            "telescope time is spent. Your tuning, rate, gain, and antenna "
            "are restored when the capture ends.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        # --- signal choice: bench default / catalog pulsar / custom -------
        sig_box = QtWidgets.QGroupBox("Test signal")
        sv = QtWidgets.QVBoxLayout(sig_box)
        self._mode_default = QtWidgets.QRadioButton(
            f"Standard self test  ({SELFTEST_PERIOD_S*1e3:.0f} ms, "
            f"DM {SELFTEST_DM:g}, {SELFTEST_FREQ_HZ/1e6:g} MHz — "
            f"the bench-proven geometry)")
        self._mode_catalog = QtWidgets.QRadioButton(
            "Simulate a catalog pulsar / magnetar:")
        self._mode_custom = QtWidgets.QRadioButton("Custom (set everything)")
        self._mode_default.setChecked(True)
        sv.addWidget(self._mode_default)
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(self._mode_catalog)
        self._cat_btn = QtWidgets.QPushButton("Pick from catalog…")
        self._cat_btn.clicked.connect(self._pick_catalog)
        crow.addWidget(self._cat_btn)
        self._suggest_btn = QtWidgets.QPushButton("Suggest geometry")
        self._suggest_btn.setToolTip(
            "Solve for a frequency, rate, duty, gain and capture length "
            "that can actually measure this source's DM — then fill them in")
        self._suggest_btn.setEnabled(False)
        self._suggest_btn.clicked.connect(self._suggest_geometry)
        crow.addWidget(self._suggest_btn)
        self._cat_label = QtWidgets.QLabel("(none picked)")
        self._cat_label.setStyleSheet("color: gray;")
        crow.addWidget(self._cat_label, 1)
        sv.addLayout(crow)
        sv.addWidget(self._mode_custom)
        for rb in (self._mode_default, self._mode_catalog, self._mode_custom):
            rb.toggled.connect(self._on_mode_changed)
        lay.addWidget(sig_box)
        self._cat_pick = None       # dict from the planner, once picked

        # --- parameters (editable outside Standard mode) ------------------
        self._grid_box = QtWidgets.QGroupBox("Parameters")
        g = QtWidgets.QGridLayout(self._grid_box)

        def _dspin(lo, hi, val, dec, suffix, tip=""):
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setValue(val)
            if suffix:
                s.setSuffix(suffix)
            if tip:
                s.setToolTip(tip)
            s.valueChanged.connect(self._update_consequences)
            return s

        self._freq_spin = _dspin(70.0, 6000.0, SELFTEST_FREQ_HZ / 1e6, 4,
                                 " MHz", "Any B210 frequency, 70–6000 MHz. "
                                 "TX stays at minimum gain, so even the "
                                 "protected 1420 MHz band is safe — nothing "
                                 "measurable leaves the enclosure.")
        g.addWidget(QtWidgets.QLabel("Frequency:"), 0, 0)
        g.addWidget(self._freq_spin, 0, 1)

        self._rate_combo = QtWidgets.QComboBox()
        rates = [0.625e6, 1e6, 1.25e6, 2e6, 4e6, 5e6, 8e6, 10e6, 16e6]
        for r in rates:
            self._rate_combo.addItem(_pretty_rate(r), r)
        self._rate_combo.setCurrentIndex(rates.index(SELFTEST_RATE_HZ))
        self._rate_combo.currentIndexChanged.connect(self._update_consequences)
        self._rate_combo.setToolTip(
            "Wider bands buy DM leverage (bigger dispersion sweep) at the "
            "cost of disk and USB load. 2 MS/s is the bench-proven default.")
        g.addWidget(QtWidgets.QLabel("Sample rate:"), 0, 2)
        g.addWidget(self._rate_combo, 0, 3)

        self._period_spin = _dspin(1.0, 15000.0, SELFTEST_PERIOD_S * 1e3, 5,
                                   " ms", "Pulse period. Magnetar periods "
                                   "(2–12 s) work; the loop buffer holds "
                                   "one full period minimum.")
        g.addWidget(QtWidgets.QLabel("Period:"), 1, 0)
        g.addWidget(self._period_spin, 1, 1)

        self._dm_spin = _dspin(0.0, 3000.0, SELFTEST_DM, 4, "",
                               "Dispersion measure to inject (pc cm⁻³). "
                               "0 = no dispersion.")
        g.addWidget(QtWidgets.QLabel("DM:"), 1, 2)
        g.addWidget(self._dm_spin, 1, 3)

        self._duty_spin = _dspin(0.1, 50.0, SELFTEST_DUTY * 100, 1, " %",
                                 "Pulse FWHM as a fraction of the period.")
        g.addWidget(QtWidgets.QLabel("Duty:"), 2, 0)
        g.addWidget(self._duty_spin, 2, 1)

        self._amp_spin = _dspin(0.01, 1.0, SELFTEST_AMP, 2, "",
                                "Peak pulse envelope as a fraction of TX DAC "
                                "full scale. The carrier is noise (high crest "
                                "factor), so 0.5 = the classic 6 dB backoff: "
                                "~2% clipping at the pulse crest, invisible "
                                "in the fold. 1.0 clips 37% and distorts the "
                                "pulse statistics.")
        g.addWidget(QtWidgets.QLabel("TX DAC amplitude:"), 2, 2)
        g.addWidget(self._amp_spin, 2, 3)

        self._rxgain_spin = _dspin(0.0, 76.0, SELFTEST_RX_GAIN_DB, 0, " dB",
                                   "RX gain during the test (40 dB is the "
                                   "bench-proven leakage-path value).")
        g.addWidget(QtWidgets.QLabel("RX gain:"), 3, 0)
        g.addWidget(self._rxgain_spin, 3, 1)

        self._nchans_spin = QtWidgets.QSpinBox()
        self._nchans_spin.setRange(16, 4096)
        self._nchans_spin.setValue(SELFTEST_NCHANS)
        self._nchans_spin.setToolTip("Filterbank channels for the .fil.")
        self._nchans_spin.valueChanged.connect(self._update_consequences)
        g.addWidget(QtWidgets.QLabel("Channels:"), 3, 2)
        g.addWidget(self._nchans_spin, 3, 3)

        g.addWidget(QtWidgets.QLabel("Capture:"), 4, 0)
        self._dur_spin = QtWidgets.QSpinBox()
        self._dur_spin.setRange(30, 600)
        self._dur_spin.setValue(90)
        self._dur_spin.setSuffix(" s")
        self._dur_spin.setToolTip(
            "90 s is the bench-proven default; long-period sources need "
            "more to accumulate pulses.")
        self._dur_spin.valueChanged.connect(self._update_consequences)
        g.addWidget(self._dur_spin, 4, 1)
        txnote = QtWidgets.QLabel("TX gain: locked at minimum")
        txnote.setStyleSheet("color: gray;")
        txnote.setToolTip(
            "The roadmap rule: never radiate next to the dish. Internal "
            "leakage at minimum gain is all the test needs.")
        g.addWidget(txnote, 4, 2, 1, 2)
        lay.addWidget(self._grid_box)

        # live consequences readout (observation-presets style)
        self._conseq = QtWidgets.QLabel("")
        self._conseq.setWordWrap(True)
        self._conseq.setStyleSheet("color: gray;")
        lay.addWidget(self._conseq)
        self._grid_box.setEnabled(False)    # Standard mode
        self._update_consequences()

        self._status = QtWidgets.QLabel("Ready.")
        self._status.setWordWrap(True)
        self._status.setTextFormat(QtCore.Qt.PlainText)
        lay.addWidget(self._status)

        # A plain-language line under the status explaining what the current
        # pipeline stage is doing (Rick 2026-08-06: the stage names are nice,
        # but say what the commands are FOR).
        self._explain = QtWidgets.QLabel("")
        self._explain.setWordWrap(True)
        self._explain.setStyleSheet("color: gray; font-style: italic;")
        self._explain.setVisible(False)
        lay.addWidget(self._explain)

        self._results = QtWidgets.QPlainTextEdit()
        self._results.setReadOnly(True)
        self._results.setVisible(False)
        self._results.setMinimumHeight(140)
        lay.addWidget(self._results)

        btns = QtWidgets.QHBoxLayout()
        self._start_btn = QtWidgets.QPushButton("Start Self Test")
        self._start_btn.clicked.connect(self._on_start)
        btns.addWidget(self._start_btn)
        self._pdf_btn = QtWidgets.QPushButton("Open Fold PDF")
        self._pdf_btn.setVisible(False)
        self._pdf_path = ""
        self._pdf_btn.clicked.connect(self._open_pdf)
        btns.addWidget(self._pdf_btn)
        btns.addStretch(1)
        self._close_btn = QtWidgets.QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        btns.addWidget(self._close_btn)
        lay.addLayout(btns)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.resize(680, 640)

    # --- modes / catalog / consequences ----------------------------------
    def _on_mode_changed(self, *_):
        std = self._mode_default.isChecked()
        self._grid_box.setEnabled(not std)
        if std:
            # snap the fields back to the bench-proven defaults
            self._freq_spin.setValue(SELFTEST_FREQ_HZ / 1e6)
            i = self._rate_combo.findData(SELFTEST_RATE_HZ)
            if i >= 0:
                self._rate_combo.setCurrentIndex(i)
            self._period_spin.setValue(SELFTEST_PERIOD_S * 1e3)
            self._dm_spin.setValue(SELFTEST_DM)
            self._duty_spin.setValue(SELFTEST_DUTY * 100)
            self._amp_spin.setValue(SELFTEST_AMP)
            self._rxgain_spin.setValue(SELFTEST_RX_GAIN_DB)
            self._nchans_spin.setValue(SELFTEST_NCHANS)
        elif self._mode_catalog.isChecked() and self._cat_pick:
            self._apply_catalog_pick()
        self._update_consequences()

    def _pick_catalog(self):
        """Reuse the pulsar planner as a picker; visibility is irrelevant for
        a simulation, so below-mask sources are listed too."""
        self._mode_catalog.setChecked(True)
        cat = self._mw._ensure_psr_catalog()
        if cat is None:
            return
        dlg = PulsarPlannerDialog(
            cat.rows, self._mw._site_dict(), self._mw.center_freq, self,
            rate_hz=getattr(self._mw, "samp_rate", 2e6),
            nchan=getattr(self._mw, "_fil_nchans", 2048))
        dlg._show_below.setChecked(True)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.selected:
            r = dlg.selected
            if not r.get("p0_s"):
                QtWidgets.QMessageBox.information(
                    self, "No period in catalog",
                    f"{r['name']} has no catalog period (P0) — it cannot "
                    f"be simulated. Pick another source or use Custom.")
                return
            self._cat_pick = r
            self._apply_catalog_pick()
            self._update_consequences()

    def _suggest_geometry(self):
        """Fill the parameter grid with a geometry that can actually measure
        the picked source's DM. Custom mode, so the user can then tweak."""
        import pulsar_sim
        if not self._cat_pick:
            return
        r = self._cat_pick
        name = r["bname"] if r.get("bname") and r["bname"] != "*" \
            else r["name"]
        rec = pulsar_sim.recommend_geometry(
            float(r["p0_s"]), float(r.get("dm") or 0.0),
            duty=self._duty_spin.value() / 100.0, name=name)
        sim = rec["sim"]
        if not sim:
            QtWidgets.QMessageBox.information(
                self, "No geometry resolves this DM",
                "\n".join(rec["lines"][-2:])
                + "\n\nThe source is still worth simulating — the period "
                  "test remains meaningful; only the DM will be "
                  "unconstrained.")
            return
        self._mode_custom.setChecked(True)     # keeps P/DM, unlocks the grid
        self._period_spin.setValue(float(r["p0_s"]) * 1e3)
        self._dm_spin.setValue(float(r.get("dm") or 0.0))
        self._freq_spin.setValue(sim["center_hz"] / 1e6)
        i = self._rate_combo.findData(sim["rate_hz"])
        if i >= 0:
            self._rate_combo.setCurrentIndex(i)
        self._duty_spin.setValue(sim["duty"] * 100.0)
        self._rxgain_spin.setValue(sim["rx_gain_db"])
        self._nchans_spin.setValue(int(sim["nchans"]))
        self._dur_spin.setValue(int(sim["capture_s"]))
        self._cat_label.setText(
            f"{name} — P {r['p0_s']*1e3:.3f} ms, DM {r.get('dm') or 0:.2f}  "
            f"(geometry solved: DM to ±{sim['dm_res']:.2f})")
        self._cat_label.setStyleSheet("")
        self._update_consequences()

    def _apply_catalog_pick(self):
        r = self._cat_pick
        self._suggest_btn.setEnabled(True)
        name = r["bname"] if r.get("bname") and r["bname"] != "*" else r["name"]
        self._period_spin.setValue(float(r["p0_s"]) * 1e3)
        self._dm_spin.setValue(float(r.get("dm") or 0.0))
        self._cat_label.setText(
            f"{name} — P {r['p0_s']*1e3:.3f} ms, DM {r.get('dm') or 0:.2f}")
        self._cat_label.setStyleSheet("")
        # long-period sources need a longer capture to accumulate pulses
        want = int(min(600, max(90, 30 * float(r["p0_s"]))))
        if want > self._dur_spin.value():
            self._dur_spin.setValue(want)

    def _sim_source_name(self):
        if self._mode_default.isChecked():
            return SELFTEST_SOURCE_NAME
        if self._mode_catalog.isChecked() and self._cat_pick:
            r = self._cat_pick
            name = r["bname"] if r.get("bname") and r["bname"] != "*" \
                else r["name"]
            return "SIM-" + "".join(c for c in name
                                    if c.isalnum() or c in "+-")
        return "B210SIM"

    def _collect_params(self):
        import math
        period = self._period_spin.value() / 1e3
        rate = float(self._rate_combo.currentData())
        n_periods = max(1, min(20, int(math.ceil(1.0 / max(period, 1e-3)))))
        return {
            "freq": self._freq_spin.value() * 1e6,
            "rate": rate,
            "period": period,
            "dm": self._dm_spin.value(),
            "duty": self._duty_spin.value() / 100.0,
            "amp": self._amp_spin.value(),
            "rx_gain": self._rxgain_spin.value(),
            "nchans": int(self._nchans_spin.value()),
            "n_periods": n_periods,
            "source_name": self._sim_source_name(),
        }

    def _update_consequences(self, *_):
        import pulsar_sim
        p = self._collect_params()
        spec = pulsar_sim.SimSpec(
            samp_rate=p["rate"], center_freq_hz=p["freq"],
            period_s=p["period"], dm=p["dm"], duty=p["duty"],
            n_periods=p["n_periods"], amplitude=p["amp"], noise_floor=0.0,
            seed=0)
        tsamp_ms = p["nchans"] / p["rate"] * 1e3
        fwhm_ms = p["duty"] * p["period"] * 1e3
        pulses = int(self._dur_spin.value() / p["period"])
        buf_mb = p["period"] * p["n_periods"] * p["rate"] * 8 / 1e6
        parts = [f"pulse {fwhm_ms:.2f} ms vs tsamp {tsamp_ms:.3f} ms",
                 f"{pulses} pulses in the capture",
                 f"TX buffer {buf_mb:.0f} MB"]
        warn = None
        if p["dm"] > 0:
            parts.insert(0, f"DM sweep {spec.dispersion_sweep_s*1e3:.2f} ms "
                            f"(resolution ±{spec.dm_resolution:.1f})")
            if spec.dm_resolution > max(2.0, 0.5 * p["dm"]):
                warn = ("this geometry can barely constrain the DM — the "
                        "grade will accept a wide range; lower the frequency "
                        "or raise the rate for real DM leverage")
        if fwhm_ms < 3 * tsamp_ms:
            warn = "pulse is under 3 samples wide — raise duty or rate"
        if pulses < 20:
            warn = f"only {pulses} pulses — lengthen the capture"
        if buf_mb > 512:
            warn = "TX buffer too large — lower the rate for this period"
        self._conseq.setText("  ·  ".join(parts)
                             + (f"\nNote: {warn}." if warn else ""))
        self._conseq.setStyleSheet(
            "color: #b45309;" if warn else "color: gray;")

    # --- driving ---------------------------------------------------------
    def _on_start(self):
        p = self._collect_params()
        if p["period"] * p["n_periods"] * p["rate"] * 8 > 512e6:
            QtWidgets.QMessageBox.information(
                self, "Self test", "The TX loop buffer would exceed 512 MB "
                "— lower the sample rate for this pulse period.")
            return
        if self._mode_catalog.isChecked() and not self._cat_pick:
            QtWidgets.QMessageBox.information(
                self, "Self test", "Pick a pulsar from the catalog first.")
            return
        err = self._mw._selftest_begin(self, p)
        if err:
            QtWidgets.QMessageBox.information(self, "Self test", err)
            return
        self._phase = "capture"
        self._left = int(self._dur_spin.value())
        self._status.setStyleSheet(self._STYLE_QUIET)
        self._explain.setText(
            "The TX is looping the dispersed pulse train out of TX/RX-A at "
            "minimum gain; RX2-A is recording the internal leakage straight "
            "into a filterbank .fil — watch the spectrum display and you "
            "can see the pulses.")
        self._explain.setVisible(True)
        self._results.setVisible(False)
        self._pdf_btn.setVisible(False)
        self._start_btn.setEnabled(False)
        self._dur_spin.setEnabled(False)
        self._close_btn.setText("Abort")
        self._timer.start()
        self._tick(first=True)

    def _tick(self, first=False):
        if self._phase != "capture":
            return
        if not first:
            self._left -= 1
        if self._left <= 0:
            self._timer.stop()
            self._phase = "analyze"
            self._close_btn.setText("Close")
            self.on_progress("capture done — radio restored; folding with "
                             "PRESTO…")
            self._mw._selftest_finish_capture(self)
        else:
            gaps = self._mw._selftest_gap_events()
            self._status.setText(
                f"Recording the internal leakage: {self._left} s left"
                + (f"  (gap events {gaps})" if gaps else ""))

    # What each pipeline stage is doing, keyed by a substring of the
    # progress message it emits (first match wins).
    _STAGE_NOTES = (
        ("readfile", "readfile checks that the recording is a well-formed "
                     "SIGPROC filterbank — header, geometry, and duration "
                     "all sane before any science is attempted."),
        ("rfifind", "rfifind scans the data in small time/frequency blocks "
                    "for interference and builds a mask, so RFI can neither "
                    "bury the pulse nor masquerade as one."),
        ("prepfold", "prepfold folds every sample at the injected period "
                     "and dedisperses at the injected DM, then searches "
                     "nearby periods and DMs for the sharpest profile — "
                     "the heart of the test. Its chi-squared says how "
                     "decisively the pulse beats noise."),
        ("rendering", "converting prepfold's PostScript plot for the "
                      "report."),
        ("writing PDF", "assembling the self-contained fold report — "
                        "plots, the exact commands run, results table, "
                        "and verdict — saved next to the .fil."),
        ("folding with PRESTO", "the radio is back on your settings; the "
                                "recording now goes through the same "
                                "PRESTO pipeline a real observation "
                                "gets."),
    )

    # Status-line looks per phase (Rick 2026-08-05: the analysis line that
    # takes over after capture must stand out, not read like fine print).
    _STYLE_QUIET = ""
    _STYLE_BUSY = ("font-weight: bold; color: #92400e; "
                   "background: #fef3c7; border-radius: 4px; padding: 6px;")
    _STYLE_PASS = ("font-weight: bold; font-size: 12pt; color: #166534; "
                   "background: #dcfce7; border-radius: 4px; padding: 6px;")
    _STYLE_FAIL = ("font-weight: bold; font-size: 12pt; color: #991b1b; "
                   "background: #fee2e2; border-radius: 4px; padding: 6px;")

    # --- callbacks from the main window ----------------------------------
    def on_progress(self, msg):
        msg = str(msg)
        self._status.setText(msg)
        if self._phase == "analyze":
            self._status.setStyleSheet(self._STYLE_BUSY)
        for key, note in self._STAGE_NOTES:
            if key in msg:
                self._explain.setText(note)
                self._explain.setVisible(True)
                break

    def on_failed(self, msg):
        self._phase = "done"
        self._status.setText("Self test FAILED to run.")
        self._status.setStyleSheet(self._STYLE_FAIL)
        self._explain.setVisible(False)
        self._results.setPlainText(str(msg))
        self._results.setVisible(True)
        self._start_btn.setEnabled(True)
        self._dur_spin.setEnabled(True)
        self._close_btn.setText("Close")

    def on_result(self, ok, checks, res, pdf_path):
        self._phase = "done"
        verdict = res.get("verdict", "?")
        head = "BUILT-IN TEST PASS" if ok else "BUILT-IN TEST FAIL"
        self._status.setText(head + f"   (PRESTO verdict: {verdict})")
        self._status.setStyleSheet(self._STYLE_PASS if ok
                                   else self._STYLE_FAIL)
        self._explain.setVisible(False)
        lines = list(checks)
        if res.get("chi2_red") is not None:
            lines.append(f"reduced chi-squared {res['chi2_red']:.1f}")
        gaps = self._mw._selftest_capture_info()
        if gaps:
            lines.append(f"recorder: {gaps}")
        self._results.setPlainText("\n".join(lines))
        self._results.setVisible(True)
        self._start_btn.setEnabled(True)
        self._start_btn.setText("Run Again")
        self._dur_spin.setEnabled(True)
        if pdf_path:
            self._pdf_path = pdf_path
            self._pdf_btn.setVisible(True)

    def _open_pdf(self):
        if self._pdf_path:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(self._pdf_path))

    def reject(self):
        if self._phase == "capture":
            self._timer.stop()
            self._mw._selftest_abort()
            self._phase = "idle"
        super().reject()


class _DockTitleBar(QtWidgets.QWidget):
    """Tinted dock title bar with float/dock, maximize, and hide buttons.

    Button order and meaning follow Rick's 2026-08-04 spec: float/dock
    first (drawn pop-out icon), then maximize — the box icon everyone reads
    as 'make this bigger', which here enlarges a FLOATING panel just enough
    to show all its controls (not full screen) and toggles back — then the
    close X. An earlier 'return to default position' button was removed: in
    the floating case it left the panel floating, resized it, and wedged the
    dock button, and it earned its keep less than a working maximize.
    """

    def __init__(self, dock, default_area, parent=None):
        super().__init__(parent)
        self._dock = dock
        self._default_area = default_area
        self._pre_max_geom = None
        self._max_geom = None
        # Tinted header so each panel's title reads as a header rather than
        # blending into its contents (Rick, 2026-08-04). Scoped by objectName
        # so the fill lands on the bar only — the child buttons keep their
        # flat auto-raise look — and the tint matches the dock tab palette.
        self.setObjectName("dockTitleBar")
        self.setStyleSheet("""
            QWidget#dockTitleBar {
                background: #dbeafe;            /* pastel blue  */
                border: 1px solid #93b4d4;
                border-bottom: 2px solid #156082;   /* DSES teal underline */
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
        """)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 3, 3)
        lay.setSpacing(2)
        self._label = QtWidgets.QLabel(dock.windowTitle())
        self._label.setStyleSheet("font-weight: 600; color: #1e3a5f;"
                                  " background: transparent; border: none;")
        lay.addWidget(self._label)
        lay.addStretch(1)

        def _btn(icon, tip, slot):
            b = QtWidgets.QToolButton(self)
            b.setIcon(icon)
            b.setIconSize(QtCore.QSize(12, 12))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFixedSize(18, 18)
            # Transparent over the tint until hovered, so the header reads as
            # one band rather than a row of boxes.
            # Transparent until hovered; a disabled button must NOT light up
            # on hover, or a greyed control still reads as clickable.
            b.setStyleSheet("QToolButton { background: transparent;"
                            " border: none; }"
                            "QToolButton:hover:enabled { background: #bfdcf5;"
                            " border-radius: 3px; }"
                            "QToolButton:disabled { background: transparent; }")
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        _S = QtWidgets.QStyle
        self._icon_dock = _detach_icon(True)
        self._float_btn = _btn(self._icon_dock, "", self._dock_back)
        self._icon_max = self.style().standardIcon(_S.SP_TitleBarMaxButton)
        self._icon_restore = _restore_icon()
        self._max_btn = _btn(self._icon_max, "",   # tooltip set by _sync
                             self._toggle_maximize)
        _btn(self.style().standardIcon(_S.SP_TitleBarCloseButton),
             "Hide this panel (View menu brings it back)", dock.close)

        dock.topLevelChanged.connect(self._sync)
        dock.dockLocationChanged.connect(lambda _a: self._sync())
        # Drag-created floating wrappers change parentage WITHOUT firing
        # either signal above, which is how the buttons got stranded in a
        # stale state. A slow poll guarantees the buttons converge on the
        # truth within a second no matter what Qt did.
        self._sync_timer = QtCore.QTimer(self)
        self._sync_timer.setInterval(1000)
        self._sync_timer.timeout.connect(self._sync)
        self._sync_timer.start()
        self._sync()

    def _main_window(self):
        w = self._dock.parentWidget()
        while w is not None and not isinstance(w, QtWidgets.QMainWindow):
            w = w.parentWidget()
        return w

    def _visually_floating(self):
        """Ground truth, immune to Qt's dock state machine: the panel is
        floating iff its top-level window is NOT the main window. A
        drag-floated dock can sit inside an internal floating wrapper
        (QDockWidgetGroupWindow) where isFloating() lies — it reports False
        because the dock is 'docked' INSIDE the wrapper — which is exactly
        the max-disabled/dead-buttons state Rick kept hitting."""
        mw = self._main_window()
        return mw is not None and self._dock.window() is not mw

    def _dock_back(self):
        """Put the panel back at the end of its home column.

        Order is EVERYTHING here, established empirically (2026-08-04) by a
        real-drag harness that tried five sequences against drag-floated
        docks on the Windows platform: setFloating(False) is a NO-OP on an
        unregistered dock, so remove→setFloating→add (the previous order)
        leaves the panel floating forever — the only failing sequence of
        the five. Working order: removeDockWidget (detach from whatever it
        is in — normal float, drag wrapper, confused slot), addDockWidget
        (register fresh at the end of the home column, minimums honored),
        and setFloating(False) LAST, once registered, which actually drops
        the flag.
        """
        dock = self._dock
        mw = self._main_window()

        if mw is None:
            dock.setFloating(False)
            return
        # restoreState is the ONLY reliable way back. Direct re-dock calls
        # (setFloating(False) / addDockWidget / removeDockWidget, in every
        # order) all end with Qt reporting floating=False, parent=main
        # window, area=correct — while the panel still renders as a window
        # over the plots, because the layout never places it (instrumented
        # 2026-08-04, five sequences). Replaying the pristine layout snapshot
        # rebuilds the whole arrangement through the same code path that lays
        # the window out correctly at startup, which always works.
        state = getattr(mw, "_default_dock_state", None)
        if state is not None:
            mw.restoreState(state)
        else:                       # pre-first-show fallback
            dock.setFloating(False)
            mw.addDockWidget(self._default_area, dock)
        dock.show()
        dock.raise_()
        # The dock is now docked in Qt's model (parented to the main window,
        # area registered) but it KEEPS ITS OLD FLOATING RECTANGLE — verified
        # by instrumentation 2026-08-04: geom stayed 950,515 294x901 through
        # every step, so the panel rendered as a stray window over the plots
        # while reporting itself docked. That was Rick's "clicking dock leaves
        # the window on the main screen". QMainWindow's layout only reflows on
        # its next relayout, so force one and give the dock a sane width.
        lay = mw.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        try:
            w = max(dock.widget().minimumWidth() if dock.widget() else 0, 290)
            mw.resizeDocks([dock], [w], Qt.Horizontal)
        except Exception:
            pass
        self._sync()

    def _toggle_maximize(self):
        """Grow a floating panel to fit its contents, or restore it.

        Deliberately NOT full screen (Rick): the useful size is 'big enough
        that nothing is cut off', clamped to the screen so the title bar
        stays reachable. The button is a real toggle — while enlarged it
        shows a distinct 'restore' (double-box) icon — and the panel stays
        freely resizable in that state; dragging it to a size of your own
        clears the enlarged state so the next click enlarges afresh rather
        than snapping back to a stale rectangle.
        """
        dock = self._dock
        if not self._visually_floating():
            return
        if self._pre_max_geom is not None:
            dock.setGeometry(self._pre_max_geom)
            self._pre_max_geom = None
            self._sync()
            return
        self._pre_max_geom = dock.geometry()
        outer = dock.widget()
        inner = outer.widget() if isinstance(outer, QtWidgets.QScrollArea) else outer
        hint = (inner.sizeHint() if inner is not None else outer.sizeHint())
        try:
            avail = dock.screen().availableGeometry()
        except Exception:
            avail = QtWidgets.QApplication.primaryScreen().availableGeometry()
        w = max(320, min(hint.width() + 40, int(avail.width() * 0.9)))
        h = max(240, min(hint.height() + 60, int(avail.height() * 0.9)))
        dock.resize(w, h)
        # Nudge back on-screen if the new size pushed it past an edge.
        g = dock.frameGeometry()
        x = min(max(g.x(), avail.x()), avail.right() - g.width())
        y = min(max(g.y(), avail.y()), avail.bottom() - g.height())
        dock.move(x, y)
        # Remember what we grew it TO. If the user later resizes the panel
        # by hand, _sync notices the mismatch and drops the enlarged state,
        # so the icon and the behaviour stay honest.
        self._max_geom = QtCore.QRect(dock.geometry())
        self._sync()

    def paintEvent(self, event):
        # A plain QWidget subclass does NOT honor a stylesheet background on
        # its own — Qt only paints it if the widget draws PE_Widget through
        # the style. Without this the header tint silently does nothing.
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, opt, p, self)

    def _sync(self, *_):
        """Buttons follow GROUND TRUTH (window parentage), not isFloating().
        The maximize button is a real toggle: while the panel is still at the
        size we grew it to, it shows the double-box 'restore' icon; if the
        user has since resized the panel by hand, the enlarged state is
        dropped so the icon goes back to 'enlarge' and no stale rectangle is
        restored later."""
        floating = self._visually_floating()
        # Both buttons apply only to a FLOATING panel: nothing to dock back
        # and nothing to enlarge when the layout already owns the panel
        # (Rick, 2026-08-04 — an active-looking dock button on a docked
        # panel invites a click that can only be a no-op or a surprise).
        self._float_btn.setEnabled(floating)
        self._float_btn.setToolTip(
            "Dock this panel back — restores the default panel layout"
            if floating else "Already docked — drag the title bar to float it")
        self._max_btn.setEnabled(floating)
        if not floating:
            self._pre_max_geom = None
            self._max_geom = None
        elif self._max_geom is not None and self._dock.geometry() != self._max_geom:
            self._pre_max_geom = None       # user resized: no longer 'enlarged'
            self._max_geom = None
        enlarged = self._pre_max_geom is not None
        self._max_btn.setIcon(self._icon_restore if enlarged else self._icon_max)
        self._max_btn.setToolTip(
            "Restore this panel to its previous size" if enlarged
            else "Enlarge this floating panel so all its controls fit")


class dses_workbench(gr.top_block, QtWidgets.QMainWindow):

    # Both bases define `connect` and `disconnect`. PySide6's QObject.connect/
    # disconnect win MRO, so `self.(dis)connect((blk, 0), (blk2, 0))` ends up
    # at QObject and dies with "called with wrong argument types (tuple, tuple)".
    # Route through gr.top_block explicitly. Without the disconnect override,
    # _stop_recording would silently fail to remove the SigMF sink and the
    # file would keep growing until the program exits.
    def connect(self, *args, **kwargs):
        return gr.top_block.connect(self, *args, **kwargs)

    def disconnect(self, *args, **kwargs):
        return gr.top_block.disconnect(self, *args, **kwargs)

    def __init__(self):
        gr.top_block.__init__(self, f"{APP_NAME} v{APP_VERSION}", catch_exceptions=True)
        # QMainWindow (1.2.0 redesign): gives us the real menu bar (native on
        # macOS), dockable control panels, and a full-width status bar that
        # ends the truncated-message problem of the old fixed sidebar.
        QtWidgets.QMainWindow.__init__(self)
        self.setObjectName("dses_main")          # required for saveState()
        self.setDockNestingEnabled(True)
        # When a dock column runs out of room Qt stacks panels as tabs along
        # its edge — with the default styling those tabs are easy to miss
        # entirely (Rick, 2026-08-04). Pastel fills + a teal selected tab
        # (DSES house color) make the stack obvious at a glance.
        self.setStyleSheet("""
            QTabBar::tab {
                background: #dbeafe;            /* pastel blue  */
                color: #1e3a5f;
                border: 1px solid #93b4d4;
                border-radius: 4px;
                padding: 4px 10px;
                margin: 2px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #156082;            /* DSES teal    */
                color: #ffffff;
                border-color: #0d4258;
            }
            QTabBar::tab:hover:!selected { background: #bfdcf5; }

            /* Sliders: the stock Qt handle is a thin sliver that is fiddly
               to grab, especially in a narrow dock (Rick, 2026-08-04). A
               taller groove and a wide, high-contrast handle with a visible
               hover/pressed state make them easy targets. */
            QSlider::groove:horizontal {
                height: 8px; border-radius: 4px;
                background: #d7dee3; border: 1px solid #b9c5cc;
            }
            QSlider::sub-page:horizontal {
                height: 8px; border-radius: 4px;
                background: #156082;            /* filled portion, DSES teal */
                border: 1px solid #0d4258;
            }
            QSlider::handle:horizontal {
                width: 18px; height: 20px;
                margin: -7px -1px;              /* overhang the groove */
                border-radius: 5px;
                background: #f7fafc;
                border: 2px solid #156082;
            }
            QSlider::handle:horizontal:hover  { background: #dbeafe; }
            QSlider::handle:horizontal:pressed { background: #156082; }
            QSlider::handle:horizontal:disabled {
                border-color: #b0bcc4; background: #eef1f3;
            }
            QSlider::groove:vertical {
                width: 8px; border-radius: 4px;
                background: #d7dee3; border: 1px solid #b9c5cc;
            }
            QSlider::handle:vertical {
                height: 18px; width: 20px; margin: -1px -7px;
                border-radius: 5px; background: #f7fafc;
                border: 2px solid #156082;
            }

            /* Inner control groups (FFT, Averaging, Sweep, Mode, …): now
               that the redundant outer boxes are flattened (Rick,
               2026-08-05), each group title becomes a small pastel "pill"
               tab — a miniature of the dock header (same pastel blue /
               navy / border family), so the hierarchy reads: strong shaded
               bar = dock, small pill = group. A faint pastel wash lifts
               each group off the window background. */
            QGroupBox {
                border: 1px solid #b9cfe0;
                border-radius: 6px;
                background: rgba(219, 234, 254, 40);
                margin-top: 11px;               /* room for the title pill */
                padding-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                padding: 1px 8px;
                color: #1e3a5f;                 /* dock-header navy  */
                background: #dbeafe;            /* dock-header pastel */
                border: 1px solid #93b4d4;
                border-radius: 4px;
            }
            /* Flattened outer boxes (title moved to the dock header):
               stay invisible — no frame, no wash, no title space. */
            QGroupBox[dsesFlat="true"] {
                border: none;
                background: transparent;
                margin-top: 0px;
                padding-top: 0px;
            }
        """)
        self.setWindowTitle(f"{APP_NAME}  —  v{APP_VERSION}")
        # Set the window/Dock icon to the bundled DSES pulsar on ALL platforms.
        #
        # History: macOS used to be excluded because Qt5 (pulled in by
        # gnuradio.uhd's optional PyQt5 widget) and Qt6 (PySide6) were BOTH
        # loaded, and a raster setWindowIcon ran Qt6 into Qt5's macOS bitmap
        # path (qt_mac_bitmapInfoForImage / QImage::format) and SIGSEGVed —
        # the same collision that later crashed setOverrideCursor. Since the
        # _PyQt5Blocker at the top of this file (2026-08-05), Qt5 never loads,
        # so the hazard is gone and macOS gets the icon too (a dev/git run
        # shows it in the Dock; installed .app runs still use the bundle's
        # .icns from install-shortcut.command).
        try:
            icon_png = Path(__file__).resolve().parent / "icons" / "dses_workbench.png"
            icon = (QtGui.QIcon(str(icon_png)) if icon_png.is_file()
                    else QtGui.QIcon.fromTheme('gnuradio-grc'))
            self.setWindowIcon(icon)
            app = QtWidgets.QApplication.instance()
            if app is not None and not icon.isNull():
                app.setWindowIcon(icon)
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)

        # Start stderr capture BEFORE the USRP source is built so we catch
        # any overflow indicators emitted during stream startup.
        self._overflow_monitor = OverflowMonitor(self)
        self._overflow_monitor.start()

        # INI-backed settings (created early so initial variable values can
        # come from it). Window geometry is also stored here, base64-encoded
        # in the [window] section (QSettings was unreliable on macOS).
        self._app_settings = Settings()
        # While True, control_changed handlers skip writes — used when
        # programmatically applying saved values back into the UI.
        self._applying_settings = False

        # QMainWindow shell: plots are the central widget (the thing you
        # watch while observing); every control group lives in a dockable
        # panel on the right. Menu + status bar are the real QMainWindow
        # ones — the status bar is full-width, so long recording/analysis
        # messages are no longer squeezed into a 300 px column.
        self.setMenuBar(self._build_menu_bar())
        self._status_bar = QtWidgets.QStatusBar(self)
        self.setStatusBar(self._status_bar)

        content = QtWidgets.QWidget()
        content.setObjectName("dses_central")
        self.main_layout = QtWidgets.QHBoxLayout(content)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)
        self.setCentralWidget(content)

        self.plots_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.plots_splitter.setChildrenCollapsible(False)
        self.main_layout.addWidget(self.plots_splitter, 1)

        # --- Dockable control panels (1.2.0). Each panel is a QDockWidget
        # the user can rearrange, tab, tear off, or hide (View menu);
        # QMainWindow.saveState persists the arrangement per machine. Every
        # dock's content sits in its own scroll area with the same scrollbar
        # policy as the old sidebar (AsNeeded, never AlwaysOff — Ray's
        # clipped-gain-slider lesson, 2026-08-01: clipping must never be
        # silent), so tall panels scroll instead of forcing window height
        # and narrow panels scroll instead of clipping controls. ---
        self._docks = []

        def _make_dock(title, objname, area=Qt.RightDockWidgetArea,
                       allowed=None, min_width=290):
            dock = QtWidgets.QDockWidget(title, self)
            dock.setObjectName(objname)          # required for saveState()
            # Restrict where the dock may DROP; floating is always allowed.
            # The layout has two columns and nothing else: display panels are
            # left-column-only, and every other panel may dock left or right
            # but never top or bottom — with those allowed, a panel being
            # dragged to a floating spot snapped into a full-width strip
            # across the top of the window before it got there (Rick,
            # 2026-09-27).
            if allowed is None:
                allowed = Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
            dock.setAllowedAreas(allowed)
            box = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(box)
            lay.setContentsMargins(2, 2, 2, 2)
            lay.addStretch(1)                    # groups insert above this
            scroll = QtWidgets.QScrollArea()
            scroll.setWidget(box)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(_VBAR_POLICY)
            scroll.verticalScrollBar().setStyleSheet(_SCROLLBAR_QSS)
            scroll.setMinimumWidth(min_width)
            scroll.setMinimumHeight(60)
            dock.setWidget(scroll)
            dock.setTitleBarWidget(
                _DockTitleBar(dock, area, dock))
            dock._dses_home_area = area          # column membership, fixed
            self.addDockWidget(area, dock)
            self._docks.append(dock)
            # File the panel's show/hide toggle under its column's submenu.
            entry = self._col_menus.get(area)
            if entry is not None:
                entry[1].addAction(dock.toggleViewAction())
            else:                                  # unknown area: top level
                self._view_menu.insertAction(self._view_menu_sep,
                                             dock.toggleViewAction())
            # Keep the column check mark honest when panels are toggled
            # individually: a column with nothing visible is unchecked.
            dock.visibilityChanged.connect(
                lambda _v, a=area: self._refresh_dock_column_checks(a))
            # Insert groups above the trailing stretch.
            def _add(w, _lay=lay, _title=title):
                # The dock's shaded title bar already names the panel, so an
                # outer group box with the SAME title is redundant chrome
                # (Rick, 2026-08-05): drop its title and outline. Inner groups
                # with their own names ("Sweep", "Mode", …) keep theirs.
                _dock_t = _title.strip().lower()
                _box_t = (w.title().strip().rstrip(':').lower()
                          if isinstance(w, QtWidgets.QGroupBox) else None)
                if _box_t is not None and (
                        _box_t == _dock_t
                        or (_dock_t == "radio" and _box_t == "rx")):
                    w.setTitle("")
                    w.setFlat(True)
                    # Exempt from the QGroupBox pill/outline styling in the
                    # main-window stylesheet (QGroupBox[dsesFlat="true"]).
                    w.setProperty("dsesFlat", True)
                _lay.insertWidget(_lay.count() - 1, w)
            return _add
        self._make_dock = _make_dock             # reused after plots exist

        self._dock_add_observation = _make_dock("Observation", "dock_observation")
        self._dock_add_tuning = _make_dock("Tuning", "dock_tuning")
        self._dock_add_rx = _make_dock("Radio", "dock_rx")
        self._dock_add_recording = _make_dock("Recording", "dock_recording")

        # --- Observation presets: the "what are you trying to do tonight?"
        # selector. Each preset applies a coherent, validated parameter
        # bundle (mode, band, rate, format, .fil geometry) through the same
        # setters the individual controls use; every knob stays adjustable
        # afterward, and the combo drops back to Manual the moment any
        # science-critical control deviates (like the sample-rate combo).
        # Deliberately NOT persisted: the app always launches at Manual,
        # because the underlying settings persist individually and a stale
        # preset label would lie about tweaked values. ---
        self._obs_group = QtWidgets.QGroupBox("Observation")
        _obs_layout = QtWidgets.QVBoxLayout(self._obs_group)
        self._observation_combo = QtWidgets.QComboBox()
        for _label, _ in OBSERVATION_PRESETS:
            self._observation_combo.addItem(_label)
        self._observation_combo.setCurrentIndex(OBS_MANUAL_IDX)
        self._observation_combo.setToolTip(
            "Pick the observing goal and the science-critical settings\n"
            "(band, sample rate, recording format, channels, integration)\n"
            "are set to a validated bundle in one step:\n"
            "  Pulsar — L-band: the proven Haswell geometry (16 MS/s,\n"
            "    .fil, 2044 ch, tsamp 127.7 µs — 28σ B0329+54).\n"
            "  Pulsar — UHF: the proven 420 MHz geometry (20 MS/s,\n"
            "    256 ch × Integrate 16 = tsamp 204.8 µs).\n"
            "  Magnetar / high-DM: L-band with 4096 channels for narrow-\n"
            "    channel dispersion tolerance.\n"
            "  Hydrogen line: ezRA drift-scan format at 1420.406 MHz.\n"
            "  RFI survey: switches to Sweep mode.\n"
            "Everything stays adjustable afterward — changing any of the\n"
            "above drops this back to 'Manual (expert)'. Display controls\n"
            "(FFT size, window, averaging) are not touched: they only\n"
            "affect what you see, never what is recorded.\n"
            "A preset NEVER starts recording: review the settings, adjust\n"
            "as needed, then start it yourself with the Record control.")
        # activated (not currentIndexChanged): fires only on USER selection,
        # so programmatic deviation-resets below can't recurse.
        self._observation_combo.activated.connect(self._apply_observation)
        _obs_layout.addWidget(self._observation_combo)
        self._consequences_label = QtWidgets.QLabel("")
        self._consequences_label.setWordWrap(True)
        self._consequences_label.setToolTip(
            "What the current science settings mean for the data product:\n"
            "time resolution, channel width, dispersion smearing per channel\n"
            "at a reference DM of 30, and disk consumption. Turns amber when\n"
            "a combination is risky (host beyond its validated recording\n"
            "rate, or time resolution too coarse for pulsar work).")
        _obs_layout.addWidget(self._consequences_label)
        self._dock_add_observation(self._obs_group)

        # Mode selector: Live = real-time FFT at the tuned center frequency
        # (the traditional view); Sweep = stepped scan from start to stop.
        self._mode_group = QtWidgets.QGroupBox("Mode")
        _mode_layout = QtWidgets.QHBoxLayout(self._mode_group)
        self._mode_live_btn = QtWidgets.QRadioButton("Live")
        self._mode_sweep_btn = QtWidgets.QRadioButton("Sweep")
        _mode_layout.addWidget(self._mode_live_btn)
        _mode_layout.addWidget(self._mode_sweep_btn)
        _mode_layout.addStretch(1)
        self._dock_add_observation(self._mode_group)

        self._tuning_group = QtWidgets.QGroupBox("Tuning")
        self._tuning_group_layout = QtWidgets.QVBoxLayout(self._tuning_group)
        self._dock_add_tuning(self._tuning_group)

        # Sweep controls — built here, populated after the source is known;
        # hidden in Live mode (shown/hidden by _on_mode_changed).
        self._sweep_group = QtWidgets.QGroupBox("Sweep")
        self._sweep_group_layout = QtWidgets.QFormLayout(self._sweep_group)
        self._dock_add_tuning(self._sweep_group)

        self._rx_group = QtWidgets.QGroupBox("RX")
        self._rx_group_layout = QtWidgets.QVBoxLayout(self._rx_group)
        self._dock_add_rx(self._rx_group)

        self._record_group = QtWidgets.QGroupBox("Recording")
        self._record_group_layout = QtWidgets.QVBoxLayout(self._record_group)
        self._dock_add_recording(self._record_group)

        self._overflow_widget = OverflowDisplayWidget()
        self._dock_add_recording(self._overflow_widget)
        self._overflow_monitor.chars_received.connect(self._overflow_widget.append_chars)

        self.recording_dir = self._app_settings.get_str('recording', 'directory')
        if not self.recording_dir:
            self.recording_dir = str(Path.home() / "Documents" / "DSES_SA_Recordings")
        os.makedirs(self.recording_dir, exist_ok=True)

        # Recording format + .fil geometry (loaded from settings).
        fmt = self._app_settings.get_str('recording', 'format').strip().lower()
        self._record_format = fmt if fmt in ('iq', 'fil', 'ezra') else 'iq'
        self._fil_nchans = max(2, int(self._app_settings.get_int('recording', 'fil_nchans')))
        self._fil_integrate = max(1, int(self._app_settings.get_int('recording', 'fil_integrate')))
        self._fil_sink = None  # current FilterbankSink, or None when not recording
        # Drift-scan (ezRA .txt) geometry + pointing (loaded from settings).
        st = self._app_settings
        self._ez_fft_bins = max(64, int(st.get_int('recording', 'ez_fft_bins')))
        self._ez_integ_frames = max(1, int(st.get_int('recording', 'ez_integ_frames')))
        self._ez_keep_fraction = min(1.0, max(0.1, st.get_float('recording', 'ez_keep_fraction')))
        self._ez_prefix = (st.get_str('recording', 'ez_prefix').strip() or 'DSES')
        self._ez_az_deg = st.get_float('recording', 'ez_az_deg')
        self._ez_el_deg = st.get_float('recording', 'ez_el_deg')
        self._ezra_sink = None  # current EzraTxtSink, or None when not recording
        # Optional source name + timed-recording state (features: name-in-filename,
        # elapsed counter, red REC indicator, "record for" duration + auto-stop).
        self._source_name = self._app_settings.get_str('recording', 'source_name')
        self._rec_duration_text = self._app_settings.get_str('recording', 'rec_duration')
        self._rec_start_text = self._app_settings.get_str('recording', 'rec_start')
        self._rec_start_tz = (self._app_settings.get_str('recording', 'rec_start_tz')
                              or 'UTC')
        # Manual fold override (known-period source without a catalog entry,
        # e.g. the lab pulsar simulator): forces a full prepfold -topo -p.
        self._fold_period_ms = self._app_settings.get_str('recording', 'fold_period_ms')
        self._fold_dm = self._app_settings.get_str('recording', 'fold_dm')
        self._rec_start_time = None   # time.monotonic() at record start, else None
        self._rec_duration_s = 0      # parsed target length in seconds (0 = none)
        self._rec_timer = QtCore.QTimer(self)
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._tick_recording)
        # Scheduled start: the unix time an ARMED recording fires at (None when
        # not armed); the 1 Hz wait timer paints the countdown and fires it.
        self._rec_sched_ts = None
        self._rec_wait_timer = QtCore.QTimer(self)
        self._rec_wait_timer.setInterval(1000)
        self._rec_wait_timer.timeout.connect(self._tick_armed)

        self._recording_dir_button = QtWidgets.QPushButton("Folder: " + self._elided_dir())
        self._recording_dir_button.setToolTip(self.recording_dir)
        self._recording_dir_button.clicked.connect(self._on_change_recording_dir)
        self._record_group_layout.addWidget(self._recording_dir_button)

        # Default size; the saved size/position is applied later in showEvent,
        # once every widget exists (applying it here, mid-construction, gets
        # overwritten when the plots/controls are added afterward).
        self.resize(1280, 780)
        self._geometry_applied = False
        self._last_good_geom = None   # last (x,y,w,h) seen while normally visible
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables (loaded from settings, fall back to DEFAULTS)
        ##################################################
        s = self._app_settings
        self.freq_preset    = freq_preset    = s.get_float('tuning', 'preset_hz')
        self.freq_offset_0  = freq_offset_0  = s.get_float('tuning', 'coarse_hz')
        self.freq_offset    = freq_offset    = s.get_float('tuning', 'fine_hz')
        self.freq_manual    = freq_manual    = s.get_float('tuning', 'manual_hz')
        self.lo_offset      = s.get_float('tuning', 'lo_offset_hz')
        self.samp_rate      = samp_rate      = s.get_float('rx', 'samp_rate_hz')
        self.gain           = gain           = s.get_float('rx', 'gain_db')
        saved_antenna       = s.get_str('rx', 'antenna')
        # `record` is intentionally NOT persisted — always start stopped.
        self.record         = 0
        self.center_freq    = center_freq    = ((freq_manual if freq_preset == 0 else freq_preset)
                                                + freq_offset + freq_offset_0)

        ##################################################
        # Blocks
        ##################################################

        # --- Resolve data source FIRST so the sample-rate combo + gain
        # slider built below can pull their options from the source's
        # capabilities (SDRPlay caps at 10 MHz; B210 goes to 25 MHz; etc.).
        chosen = resolve_device(
            self._app_settings.get_str('rx', 'device_driver'),
            self._app_settings.get_str('rx', 'device_serial'),
            parent=self)
        if not chosen:
            raise SystemExit(0)
        self._playback_mode = (chosen == PLAYBACK_SENTINEL)
        self._playback_path = None
        # In live mode: RadioSource wrapper around the actual radio block.
        # In playback mode: None (the file_source + throttle live in their
        # own attributes).
        self._source: 'RadioSource | None' = None
        # Backwards-compat alias used by _start_recording / _stop_recording
        # and the live-only setter guards. Kept as None in playback mode.
        self.uhd_usrp_source_0 = None

        if self._playback_mode:
            # Sample rate and center frequency come from the file's metadata,
            # not from the saved settings. Override the locals + self vars
            # before any widget construction that uses them.
            self._playback_path = find_default_sample_path()
            assert self._playback_path is not None
            pb_sr, pb_cf, _dtype = load_sigmf_meta(self._playback_path)
            self._playback_center_freq = pb_cf
            self.samp_rate = samp_rate = pb_sr
            self.center_freq = center_freq = pb_cf
            self._device_serial = None
            self._device_driver = None
            self._file_source = blocks.file_source(
                gr.sizeof_gr_complex,
                self._playback_path + '.sigmf-data',
                repeat=True)
            self._throttle = blocks.throttle(gr.sizeof_gr_complex, samp_rate, True)
            self._rotator = blocks.rotator_cc(0.0)
            # No RadioSource for playback; the sidebar uses a single-item
            # rate combo and a disabled gain slider (handled below).
            sr_options = [pb_sr]
            gain_range_tuple = (0.0, 76.0, 1.0)
            device_label_text = f"Playback: {Path(self._playback_path).name}.sigmf-data"
        else:
            assert isinstance(chosen, dict)
            self._device_driver = chosen['driver']
            self._device_serial = chosen['serial']
            self._save_setting('rx', 'device_driver', self._device_driver)
            self._save_setting('rx', 'device_serial', self._device_serial)
            try:
                if self._device_driver == DRIVER_UHD_B200:
                    src: RadioSource = UhdB200Source(
                        serial=chosen['serial'], samp_rate=samp_rate,
                        center_freq=center_freq, gain=gain,
                        antenna=saved_antenna)
                else:
                    src = SoapyGenericSource(
                        driver=self._device_driver,
                        serial=chosen['serial'],
                        samp_rate=samp_rate,
                        center_freq=center_freq,
                        gain=gain,
                        product=chosen.get('product', ''),
                        antenna=saved_antenna)
                    # Snap requested rate to the driver's nearest supported.
                    if src.samp_rate_options:
                        samp_rate = min(
                            src.samp_rate_options,
                            key=lambda r: abs(r - samp_rate))
                        self.samp_rate = samp_rate
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    self, "Radio failed to open",
                    f"Could not open {chosen['label']}:\n\n"
                    f"{type(exc).__name__}: {exc}")
                raise SystemExit(0)
            self._source = src
            self.uhd_usrp_source_0 = src.block
            # Re-apply the saved LO offset (the constructor tunes classic).
            # If this radio can't do it, fall back cleanly to offset 0.
            if self.lo_offset and not src.set_lo_offset(self.lo_offset):
                print(f"LO offset not supported by {src.display_label}; "
                      "using classic tuning.", file=sys.stderr)
                self.lo_offset = 0.0
            sr_options = list(src.samp_rate_options)
            gain_range_tuple = src.gain_range
            device_label_text = f"Device: {src.display_label}"

        # Window title shows the active radio so the user can see at a
        # glance which device is feeding the display.
        if self._playback_mode:
            assert self._playback_path is not None
            title_device = f"Playback: {Path(self._playback_path).name}"
        else:
            assert self._source is not None
            title_device = self._source.display_label
        self.setWindowTitle(f"{APP_NAME}  —  v{APP_VERSION}  —  {title_device}")

        # --- Sample-rate selector: device-aware presets + manual entry ---
        # The combo offers validated quick-pick rates; the Manual Rate box (like
        # Manual Frequency) accepts any rate the SDR supports, clamped to the
        # device's reported range and snapped by the driver to the nearest
        # achievable. Real-pulsar geometries that aren't presets go here.
        self._samp_rate_options = sr_options
        self._samp_rate_labels = [_pretty_rate(r) for r in sr_options]
        self._samp_rate_tool_bar = QtWidgets.QToolBar(self)
        self._samp_rate_tool_bar.addWidget(QtWidgets.QLabel("Sample Rate: "))
        self._samp_rate_combo_box = QtWidgets.QComboBox()
        self._samp_rate_tool_bar.addWidget(self._samp_rate_combo_box)
        for _label in self._samp_rate_labels:
            self._samp_rate_combo_box.addItem(_label)
        self._samp_rate_combo_box.setToolTip(
            "Quick-pick rates for this radio, up to its hardware maximum.\n"
            "All presets divide the device's master clock evenly (flat\n"
            "half-band filtering, no CIC passband droop). Any other rate\n"
            "can be typed into Manual Rate below.\n"
            "High rates are limited by USB and host CPU, not just the\n"
            "radio — if the overflow panel or a recording's gap counter\n"
            "starts climbing, the host can't sustain the rate; step down.")
        self._samp_rate_combo_box.currentIndexChanged.connect(
            self._on_samp_rate_combo)
        self._rx_group_layout.addWidget(self._samp_rate_tool_bar)

        self._samp_rate_manual_tool_bar = QtWidgets.QToolBar(self)
        self._samp_rate_manual_tool_bar.addWidget(
            QtWidgets.QLabel("Manual Rate (Hz): "))
        self._samp_rate_manual_line_edit = QtWidgets.QLineEdit()
        self._samp_rate_manual_tool_bar.addWidget(self._samp_rate_manual_line_edit)
        self._samp_rate_manual_line_edit.editingFinished.connect(
            self._on_samp_rate_manual_edit)
        self._rx_group_layout.addWidget(self._samp_rate_manual_tool_bar)
        # Populate the manual box + combo selection from the opening rate, and
        # show the device's real limits as a tooltip.
        self._refresh_samp_rate_limits_tooltip()
        self._sync_samp_rate_widgets()

        # --- Format selector: raw I/Q (SigMF) vs live filterbank (.fil)
        #     vs drift-scan integrated spectra (ezRA .txt) ---
        self._record_format_options = ['iq', 'fil', 'ezra']
        self._record_format_labels = ['Raw I/Q (SigMF)', 'Filterbank (.fil)',
                                      'Drift scan (ezRA .txt)']
        self._record_format_tool_bar = QtWidgets.QToolBar(self)
        self._record_format_tool_bar.addWidget(QtWidgets.QLabel("Format: "))
        self._record_format_combo = QtWidgets.QComboBox()
        for _label in self._record_format_labels:
            self._record_format_combo.addItem(_label)
        self._record_format_combo.setCurrentIndex(
            self._record_format_options.index(self._record_format))
        self._record_format_combo.setToolTip(
            "Raw I/Q: full-rate complex samples to a SigMF pair (large).\n"
            "Filterbank: channelize live and write a SIGPROC .fil directly "
            "(telescope_id 12) — the raw I/Q is never stored.\n"
            "Drift scan: integrated spectra (~one row per 10-15 s) in the\n"
            "ezRA .txt format for Ted Cline's ezRA suite (ezCon → ezPlot/\n"
            "ezSky). Site identity comes from the [site] settings; set the\n"
            "dish Az/El below.")
        self._record_format_combo.currentIndexChanged.connect(
            lambda i: self.set_record_format(self._record_format_options[i]))
        self._record_format_tool_bar.addWidget(self._record_format_combo)
        self._record_group_layout.addWidget(self._record_format_tool_bar)

        # --- Optional source / pulsar name (plain row, not a QToolBar, so it
        #     doesn't collapse into an overflow menu in the narrow sidebar) ---
        self._source_name_widget = QtWidgets.QWidget(self)
        _src_row = QtWidgets.QHBoxLayout(self._source_name_widget)
        _src_row.setContentsMargins(0, 0, 0, 0)
        _src_row.addWidget(QtWidgets.QLabel("Source:"))
        self._source_name_edit = QtWidgets.QLineEdit(self._source_name)
        self._source_name_edit.setPlaceholderText("optional, e.g. B0329+54")
        self._source_name_edit.setToolTip(
            "Optional source / pulsar name. When set it is added to the recording\n"
            "filename AND written into the SIGPROC .fil header (source_name), which\n"
            "PRESTO/prepfold read. Leave blank for a timestamp-only filename.")
        self._source_name_edit.editingFinished.connect(self._on_source_name_changed)
        _src_row.addWidget(self._source_name_edit, 1)
        self._record_group_layout.addWidget(self._source_name_widget)

        # --- Manual fold override (known-period source with no catalog entry,
        #     e.g. the lab pulsar simulator): a period (+ optional DM) forces a
        #     full topocentric prepfold instead of the data-check-only path.
        #     Same plain two-row grid as .fil geometry (sidebar-width safe). ---
        self._fold_manual_widget = QtWidgets.QWidget(self)
        _fold_grid = QtWidgets.QGridLayout(self._fold_manual_widget)
        _fold_grid.setContentsMargins(0, 0, 0, 0)
        _fold_grid.addWidget(QtWidgets.QLabel("Fold P (ms):"), 0, 0)
        self._fold_period_edit = QtWidgets.QLineEdit(self._fold_period_ms)
        self._fold_period_edit.setPlaceholderText("e.g. 102.4 (simulator)")
        self._fold_period_edit.setToolTip(
            "Manual fold period in milliseconds, for a known-period source with\n"
            "no catalog entry — e.g. the lab pulsar simulator. When Source is a\n"
            "catalog pulsar these boxes instead preview its catalogue period/DM\n"
            "and lock (the fold then uses the full ephemeris). This tooltip is\n"
            "updated live to match the current Source.")
        self._fold_period_edit.editingFinished.connect(self._on_fold_period_changed)
        _fold_grid.addWidget(self._fold_period_edit, 0, 1)
        _fold_grid.addWidget(QtWidgets.QLabel("Fold DM:"), 1, 0)
        self._fold_dm_edit = QtWidgets.QLineEdit(self._fold_dm)
        self._fold_dm_edit.setPlaceholderText("optional, default 0")
        self._fold_dm_edit.setToolTip(
            "Dispersion measure (pc/cm^3) for the manual fold. The lab\n"
            "simulator injects an undispersed signal, so 0 (blank) is right;\n"
            "use the known DM for a genuinely dispersed source.")
        self._fold_dm_edit.editingFinished.connect(self._on_fold_dm_changed)
        _fold_grid.addWidget(self._fold_dm_edit, 1, 1)
        self._record_group_layout.addWidget(self._fold_manual_widget)

        # --- .fil geometry (only meaningful in filterbank mode) ---
        # A plain two-row grid, NOT a QToolBar. A QToolBar collapses any widget
        # that doesn't fit the available width into an overflow ("»") menu, and
        # in the narrow sidebar — especially once its vertical scrollbar appears
        # and steals ~17px — the Integrate spin box would drop into that overflow
        # and look like a blank field (Windows). A grid stacks the two labelled
        # rows and always shows both spin boxes regardless of sidebar width.
        self._fil_geom_widget = QtWidgets.QWidget(self)
        _fil_geom_grid = QtWidgets.QGridLayout(self._fil_geom_widget)
        _fil_geom_grid.setContentsMargins(0, 0, 0, 0)
        _fil_geom_grid.addWidget(QtWidgets.QLabel("Channels:"), 0, 0)
        self._fil_nchans_spin = _make_int_spinbox(
            2, 65536, self._fil_nchans,
            "Filterbank channel count (FFT size). tsamp = nchans*integrate/samp_rate.")
        self._fil_nchans_spin.valueChanged.connect(self.set_fil_nchans)
        _fil_geom_grid.addWidget(self._fil_nchans_spin, 0, 1)
        _fil_geom_grid.addWidget(QtWidgets.QLabel("Integrate:"), 1, 0)
        self._fil_integrate_spin = _make_int_spinbox(
            1, 65536, self._fil_integrate,
            "Power frames summed per output sample (1 = no integration).")
        self._fil_integrate_spin.valueChanged.connect(self.set_fil_integrate)
        _fil_geom_grid.addWidget(self._fil_integrate_spin, 1, 1)
        self._record_group_layout.addWidget(self._fil_geom_widget)

        # --- Dish pointing (only meaningful in drift-scan / ezRA mode):
        #     written into the ezRA .txt header's azDeg/elDeg line. Manual
        #     entry for now; System-1 steering readback can populate it
        #     later (see ROADMAP). Same plain-grid pattern as .fil geometry.
        self._ezra_point_widget = QtWidgets.QWidget(self)
        _ez_grid = QtWidgets.QGridLayout(self._ezra_point_widget)
        _ez_grid.setContentsMargins(0, 0, 0, 0)
        _ez_grid.addWidget(QtWidgets.QLabel("Az (deg):"), 0, 0)
        self._ez_az_spin = QtWidgets.QDoubleSpinBox()
        self._ez_az_spin.setRange(0.0, 360.0)
        # 4 decimals: the Haswell protocol reads the park position off the
        # 16-bit encoder application (e.g. az 359.9176) — one decimal, the
        # original choice when az/el were nominal labels, silently rounded
        # the entered value (Rick, 2026-09-13 restore).
        self._ez_az_spin.setDecimals(4)
        self._ez_az_spin.setValue(self._ez_az_deg)
        self._ez_az_spin.setToolTip(
            "Dish azimuth written into the ezRA drift-scan file header.")
        self._ez_az_spin.valueChanged.connect(self._on_ez_az_changed)
        _ez_grid.addWidget(self._ez_az_spin, 0, 1)
        _ez_grid.addWidget(QtWidgets.QLabel("El (deg):"), 1, 0)
        self._ez_el_spin = QtWidgets.QDoubleSpinBox()
        self._ez_el_spin.setRange(0.0, 90.0)
        self._ez_el_spin.setDecimals(4)
        self._ez_el_spin.setValue(self._ez_el_deg)
        self._ez_el_spin.setToolTip(
            "Dish elevation written into the ezRA drift-scan file header.")
        self._ez_el_spin.valueChanged.connect(self._on_ez_el_changed)
        _ez_grid.addWidget(self._ez_el_spin, 1, 1)
        self._record_group_layout.addWidget(self._ezra_point_widget)

        # --- Automatic post-processing (canned PRESTO pipeline) ---
        self._analysis_row_widget = QtWidgets.QWidget(self)
        _an_row = QtWidgets.QHBoxLayout(self._analysis_row_widget)
        _an_row.setContentsMargins(0, 0, 0, 0)
        self._analyze_check = QtWidgets.QCheckBox("Analyze when done")
        self._analyze_check.setChecked(
            self._app_settings.get_bool('recording', 'analyze_when_done'))
        self._analyze_check.setToolTip(
            "When a filterbank (.fil) recording stops, automatically run the\n"
            "canned PRESTO pipeline: readfile sanity, rfifind RFI mask,\n"
            "band-edge zap, and a prepfold fold — by catalog pulsar (Source)\n"
            "or at a manual Fold P (ms) when set. Results arrive as a\n"
            "self-contained PDF next to the recording, with a plain-language\n"
            "verdict. Needs PRESTO (Mac/Linux: native; Windows: WSL via\n"
            "presto/build_presto.sh).")
        self._analyze_check.toggled.connect(
            lambda on: self._save_setting('recording', 'analyze_when_done', on))
        _an_row.addWidget(self._analyze_check)
        self._quicklook_btn = QtWidgets.QPushButton("Quick look")
        self._quicklook_btn.setToolTip(
            "While a .fil recording runs: snapshot the data captured so far\n"
            "and run the same pipeline on it WITHOUT interrupting the\n"
            "recording — an early answer to \"is this session working?\".\n"
            "Needs a few minutes of data before a fold means anything.")
        self._quicklook_btn.setEnabled(False)
        self._quicklook_btn.clicked.connect(self._on_quicklook_clicked)
        _an_row.addWidget(self._quicklook_btn)
        self._record_group_layout.addWidget(self._analysis_row_widget)

        # --- Optional recording duration (auto-stop) ---
        self._rec_duration_widget = QtWidgets.QWidget(self)
        _dur_row = QtWidgets.QHBoxLayout(self._rec_duration_widget)
        _dur_row.setContentsMargins(0, 0, 0, 0)
        _dur_row.addWidget(QtWidgets.QLabel("Record for:"))
        self._rec_duration_edit = QtWidgets.QLineEdit(self._rec_duration_text)
        self._rec_duration_edit.setPlaceholderText("min or H:MM")
        self._rec_duration_edit.setToolTip(
            "Optional recording length: minutes (e.g. 30) or H:MM / HH:MM:SS\n"
            "(e.g. 1:30). The recording auto-stops when it is reached; the counter\n"
            "below shows a countdown. Leave blank to record until you stop it.")
        self._rec_duration_edit.editingFinished.connect(self._on_rec_duration_changed)
        _dur_row.addWidget(self._rec_duration_edit, 1)
        self._record_group_layout.addWidget(self._rec_duration_widget)

        # --- Optional scheduled start (Rick, 2026-09-26) -------------------
        # Setting Record with a time here ARMS instead of starting: the counter
        # counts down, the safety questions are asked up front, and the
        # recording starts itself with nobody at the keyboard.
        self._rec_start_widget = QtWidgets.QWidget(self)
        _st_row = QtWidgets.QHBoxLayout(self._rec_start_widget)
        _st_row.setContentsMargins(0, 0, 0, 0)
        _st_row.addWidget(QtWidgets.QLabel("Start at:"))
        self._rec_start_edit = QtWidgets.QLineEdit(self._rec_start_text)
        self._rec_start_edit.setPlaceholderText("HH:MM, +min, or date")
        self._rec_start_edit.setToolTip(
            "Optional scheduled start. Forms:\n"
            "  03:15         a clock time - the next time it comes round\n"
            "  +30  +1:30    a delay from when you set Record (minutes, or H:MM)\n"
            "  2026-10-24 03:15   a full date and time\n"
            "Read as UTC or your local clock per the selector. Setting Record to\n"
            "Recording then ARMS: the counter shows a countdown to the start (and\n"
            "the planned end when Record for is set), the source-visibility and\n"
            "hydrogen-line checks are asked now, and the recording starts itself.\n"
            "Stopped cancels. Leave blank to start at once.")
        self._rec_start_edit.editingFinished.connect(self._on_rec_start_changed)
        _st_row.addWidget(self._rec_start_edit, 1)
        self._rec_start_tz_combo = QtWidgets.QComboBox()
        self._rec_start_tz_combo.addItems(["UTC", "Local"])
        self._rec_start_tz_combo.setCurrentText(
            "Local" if self._rec_start_tz == "Local" else "UTC")
        self._rec_start_tz_combo.setToolTip(
            "How to read the Start at time: UTC (the convention in every file\n"
            "this program writes and in the pulsar planner) or this computer's\n"
            "local clock.")
        self._rec_start_tz_combo.currentTextChanged.connect(self._on_rec_start_changed)
        _st_row.addWidget(self._rec_start_tz_combo)
        self._record_group_layout.addWidget(self._rec_start_widget)

        # --- Record selector ---
        self._record_options = [0, 1]
        self._record_labels = ['Stopped', 'Recording']
        self._record_tool_bar = QtWidgets.QToolBar(self)
        self._record_tool_bar.addWidget(QtWidgets.QLabel("Record: "))
        self._record_combo_box = QtWidgets.QComboBox()
        self._record_tool_bar.addWidget(self._record_combo_box)
        for _label in self._record_labels:
            self._record_combo_box.addItem(_label)
        self._record_callback = lambda i: QtCore.QMetaObject.invokeMethod(
            self._record_combo_box, "setCurrentIndex",
            QtCore.Q_ARG("int", self._record_options.index(i)))
        self._record_callback(self.record)
        self._record_combo_box.currentIndexChanged.connect(
            lambda i: self.set_record(self._record_options[i]))
        self._record_group_layout.addWidget(self._record_tool_bar)

        # Status label below the Record combo, updated by _start/_stop_recording.
        # Recording status lives in the panel AND mirrors to the full-width
        # status bar, where long filenames are never truncated (the old
        # 300 px sidebar squeezed these messages — Rick, 2026-08-02).
        class _MirroredLabel(QtWidgets.QLabel):
            def __init__(lbl, mirror, *a):
                super().__init__(*a)
                lbl._mirror = mirror
            def setText(lbl, text):
                super().setText(text)
                try:
                    lbl._mirror(text)
                except Exception:
                    pass
        self._recording_status = _MirroredLabel(
            lambda t: self._status_bar.showMessage(t), "Idle")
        self._recording_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._recording_status.setWordWrap(True)
        self._record_group_layout.addWidget(self._recording_status)

        # Elapsed / countdown counter with a red REC dot; only visible while a
        # recording is actually running (set by _on_recording_started/_stopped).
        self._rec_elapsed_label = QtWidgets.QLabel("")
        self._rec_elapsed_label.setStyleSheet(
            "color: #e74c3c; font-weight: bold;")   # red REC text
        self._rec_elapsed_label.setVisible(False)
        self._record_group_layout.addWidget(self._rec_elapsed_label)

        # Grey out the .fil geometry row unless filterbank format is selected.
        self._update_fil_geom_enabled()
        # Lock/preview the Fold boxes to match a saved catalog Source name.
        self._sync_fold_fields_to_source()

        # --- Gain slider (device-aware range) ---
        gmin, gmax, gstep = gain_range_tuple
        clamped_gain = max(gmin, min(gmax, self.gain))
        if clamped_gain != self.gain:
            self.gain = clamped_gain  # device range is narrower than saved value
        self._gain_range = Range(gmin, gmax, gstep, clamped_gain, 200)
        self._gain_win = RangeWidget(self._gain_range, self.set_gain, "RX Gain (dB)",
                                      "counter_slider", float, Qt.Horizontal)
        self._rx_group_layout.addWidget(self._gain_win)

        # --- Antenna / RF-input selector (only when the radio has >1 port) ---
        # B210: TX/RX vs RX2. RSPduo: tuner 1 vs 2. RSPdx: antenna A/B/C.
        # Single-port radios (RSP1B → ['RX'], most RTL dongles) omit it.
        self._antenna_options = []
        if not self._playback_mode and self._source is not None:
            self._antenna_options = list(self._source.antennas)
        if len(self._antenna_options) > 1 and self._source is not None:
            self._antenna_tool_bar = QtWidgets.QToolBar(self)
            self._antenna_tool_bar.addWidget(QtWidgets.QLabel("Antenna: "))
            self._antenna_combo_box = QtWidgets.QComboBox()
            self._antenna_tool_bar.addWidget(self._antenna_combo_box)
            for _name in self._antenna_options:
                self._antenna_combo_box.addItem(_name)
            cur = self._source.current_antenna
            if cur in self._antenna_options:
                self._antenna_combo_box.setCurrentIndex(
                    self._antenna_options.index(cur))
            self._antenna_combo_box.currentIndexChanged.connect(
                lambda i: self.set_antenna(self._antenna_options[i]))
            self._rx_group_layout.addWidget(self._antenna_tool_bar)

        # --- Device picker button (label already computed above) ---
        self._device_button = QtWidgets.QPushButton(device_label_text)
        self._device_button.setToolTip(
            "Click to pick a different attached radio (when more than one is connected).")
        self._device_button.clicked.connect(self._on_change_device_clicked)
        if self._playback_mode:
            self._device_button.setEnabled(False)
        self._rx_group_layout.addWidget(self._device_button)

        # --- Spectrum display (replaces qtgui freq_sink + waterfall_sink) ---
        # stream_to_vector groups samples into CHUNK_SIZE vectors so the pure-
        # Python sink sees whole vectors (sample-by-sample can't keep up).
        # keep_one_in_n only engages above ~26 MS/s (see _decim_for): at the
        # rates we use, EVERY sample reaches the sink and the display
        # Welch-averages the full stream (v1.1.7 sensitivity fix).
        self._sample_sink = SampleBufferSink(chunk_size=CHUNK_SIZE)
        self._stream_to_vec = blocks.stream_to_vector(gr.sizeof_gr_complex, CHUNK_SIZE)
        self._keep_one_in_n = blocks.keep_one_in_n(
            gr.sizeof_gr_complex * CHUNK_SIZE,
            self._decim_for(samp_rate))
        self._fft_plot = FftPlotWidget(center_freq, samp_rate)
        self._fft_plot.set_y_axis(-140, 10)
        self._fft_plot.set_frequency_range(center_freq, samp_rate)
        self._waterfall_plot = WaterfallPlotWidget(center_freq, samp_rate, rows=256)
        self._waterfall_plot.set_intensity_range(-140, 10)
        # Keep spectrum + waterfall showing the same frequency span under
        # interactive zoom/pan (Ray's 2026-08-01 report).
        self._waterfall_plot.link_x_to(self._fft_plot)

        self._processor = SpectrumProcessor(
            self._sample_sink, fft_size=1024, window_name="blackman-harris",
            update_hz=10.0, gain_db=float(self.gain), parent=self)
        self._processor.frame_ready.connect(self._fft_plot.on_frame)
        self._processor.frame_ready.connect(self._waterfall_plot.on_frame)
        self._processor.frame_ready.connect(self._on_processor_frame_cache)
        # --- Sweep (stepped wide-spectrum) mode: persisted settings, runtime
        #     state, and the per-step settle timer. The Mode/Sweep UI is built
        #     in the sidebar; the sweep loop drives the plots itself. ---
        _sw = self._app_settings
        self._sweep_enabled   = _sw.get_bool('sweep', 'enabled')
        self._sweep_start_hz  = _sw.get_float('sweep', 'start_hz')
        self._sweep_stop_hz   = _sw.get_float('sweep', 'stop_hz')
        self._sweep_step_hz   = _sw.get_float('sweep', 'step_hz')   # 0 = auto
        self._sweep_settle_ms = max(20, int(_sw.get_int('sweep', 'settle_ms')))
        self._sweep_active    = False
        self._sweep_step_idx  = 0
        self._sweep_n_steps   = 0
        self._sweep_kept_bins = 0
        self._sweep_start_idx = 0
        self._sweep_step_actual_hz = 0.0
        self._sweep_wide_db   = None
        self._sweep_max_db    = None
        self._sweep_show_max  = False
        self._sweep_eff_center = 0.0
        self._sweep_eff_bw    = 0.0
        self._sweep_latest_avg_db = None
        self._sweep_capture_retries = 0
        self._sweep_axis_needs_apply = False
        self._sweep_saved_alpha = None
        self._sweep_saved_max = None
        self._sweep_saved_min = None
        self._sweep_saved_baseline = None
        self._hw_freq_range = (self._source.freq_range()
                               if self._source is not None else None)
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setSingleShot(True)
        self._sweep_timer.timeout.connect(self._sweep_capture_and_advance)
        # --- Populate the Sweep group + wire the Mode toggle. Done here (not
        #     in sidebar construction) so the source/processor/plots and the
        #     sweep state already exist when a persisted Sweep mode auto-enters. ---
        if self._hw_freq_range is not None:
            lo, hi = self._hw_freq_range
            self._sweep_start_hz = max(lo, min(hi, self._sweep_start_hz))
            self._sweep_stop_hz  = max(lo, min(hi, self._sweep_stop_hz))
            if self._sweep_stop_hz <= self._sweep_start_hz:
                self._sweep_stop_hz = min(hi, self._sweep_start_hz + 100e6)
        self._sweep_start_edit = QtWidgets.QLineEdit(_fmt_hz(self._sweep_start_hz))
        self._sweep_stop_edit = QtWidgets.QLineEdit(_fmt_hz(self._sweep_stop_hz))
        _step_text = ("auto" if self._sweep_step_hz <= 0 else _fmt_hz(self._sweep_step_hz))
        self._sweep_step_edit = QtWidgets.QLineEdit(_step_text)
        self._sweep_step_edit.setToolTip(
            "Hz per tuning step. Type 'auto' to use ~80% of the current "
            "sample rate (recommended); otherwise enter an explicit step "
            "size, e.g. 5M for 5 MHz.")
        if self._hw_freq_range is not None:
            lo, hi = self._hw_freq_range
            _rng = f"Range: {_fmt_hz(lo)} \u2013 {_fmt_hz(hi)}"
            self._sweep_start_edit.setToolTip(_rng)
            self._sweep_stop_edit.setToolTip(_rng)
        self._sweep_start_edit.editingFinished.connect(self._on_sweep_range_edit)
        self._sweep_stop_edit.editingFinished.connect(self._on_sweep_range_edit)
        self._sweep_step_edit.editingFinished.connect(self._on_sweep_step_edit)
        self._sweep_group_layout.addRow("Start:", self._sweep_start_edit)
        self._sweep_group_layout.addRow("Stop:", self._sweep_stop_edit)
        self._sweep_group_layout.addRow("Step:", self._sweep_step_edit)
        self._sweep_status_label = QtWidgets.QLabel("Idle")
        self._sweep_status_label.setStyleSheet("color: #888;")
        self._sweep_group_layout.addRow("Status:", self._sweep_status_label)
        # setChecked runs before the signals are connected, so apply the
        # persisted mode explicitly via _on_mode_changed after wiring.
        self._mode_live_btn.setChecked(not self._sweep_enabled)
        self._mode_sweep_btn.setChecked(self._sweep_enabled)
        self._mode_live_btn.toggled.connect(lambda on: on and self._on_mode_changed('live'))
        self._mode_sweep_btn.toggled.connect(lambda on: on and self._on_mode_changed('sweep'))
        if self._playback_mode:
            self._mode_sweep_btn.setEnabled(False)
            self._mode_live_btn.setChecked(True)
            self._sweep_group.hide()
            self._mode_group.setToolTip("Sweep is disabled in playback mode.")
        else:
            self._on_mode_changed('sweep' if self._sweep_enabled else 'live')
        self._fft_plot.request_fft_size.connect(self._processor.set_fft_size)
        self._fft_plot.request_window.connect(self._processor.set_window)
        self._fft_plot.request_average.connect(self._processor.set_average_alpha)
        self._fft_plot.request_max_hold.connect(self._processor.set_max_hold)
        self._fft_plot.request_min_hold.connect(self._processor.set_min_hold)
        self._fft_plot.request_hold_detector.connect(self._processor.set_hold_detector)
        self._fft_plot.request_reset_max.connect(self._processor.reset_max_hold)
        self._fft_plot.request_reset_min.connect(self._processor.reset_min_hold)
        self._fft_plot.request_window_normalized.connect(self._processor.set_window_normalized)
        self._fft_plot.request_dc_suppress.connect(self._processor.set_dc_suppress)
        self._fft_plot.request_unit.connect(self._processor.set_unit)
        self._fft_plot.request_cal_offset.connect(self._processor.set_cal_offset_db)
        self._fft_plot.request_power_avg.connect(self._processor.set_power_avg)
        self._fft_plot.request_smooth.connect(self._processor.set_smooth_bins)
        self._fft_plot.request_baseline_mode.connect(self._processor.set_baseline_mode)
        self._fft_plot.request_store_reference.connect(self._processor.store_reference)
        self._fft_plot.request_flatten_bins.connect(self._processor.set_flatten_bins)

        # Keep the two control panels' visibility in lock-step so the spectrum
        # and waterfall plot regions stay equal-width. setChecked is a no-op
        # when the state already matches, so this can't recurse.
        self._fft_plot._toggle_btn.toggled.connect(self._waterfall_plot._toggle_btn.setChecked)
        self._waterfall_plot._toggle_btn.toggled.connect(self._fft_plot._toggle_btn.setChecked)
        # Persist panel visibility (single shared value since they're linked).
        self._fft_plot._toggle_btn.toggled.connect(
            lambda on: self._save_setting('ui', 'control_panels_visible', on))

        # Per-plot setting persistence. Each widget emits control_changed
        # (settings_key, value); we route to the appropriate INI section.
        self._fft_plot.control_changed.connect(self._on_spectrum_control_changed)
        self._waterfall_plot.control_changed.connect(
            lambda k, v: self._save_setting('waterfall', k, v))

        self.plots_splitter.addWidget(self._fft_plot)
        self.plots_splitter.addWidget(self._waterfall_plot)
        self.plots_splitter.setStretchFactor(0, 1)
        self.plots_splitter.setStretchFactor(1, 1)
        self.plots_splitter.setSizes([400, 400])

        # Display-control panels become docks too (Rick, 2026-08-04), but
        # LEFT-column-only: they describe the plots, so they live beside
        # them — draggable/floatable/hidable like the rest, just not
        # droppable into the right column of science controls.
        _add_sd = self._make_dock("Spectrum Display", "dock_spectrum_display",
                                  area=Qt.LeftDockWidgetArea,
                                  allowed=Qt.LeftDockWidgetArea,
                                  min_width=248)
        _add_sd(self._fft_plot.take_panel())
        _add_wd = self._make_dock("Waterfall Display", "dock_waterfall_display",
                                  area=Qt.LeftDockWidgetArea,
                                  allowed=Qt.LeftDockWidgetArea,
                                  min_width=248)
        _add_wd(self._waterfall_plot.take_panel())

        # --- Frequency preset radio group ---
        # The L-band entry tunes to the hydrogen line itself (1420.406 MHz),
        # not the feed's nominal 1422.000 — at narrow spans (2 MS/s home
        # stations) a 1422.0 center leaves the line entirely out of band,
        # which is exactly how Ray recorded a hydrogen-free "HI" run on
        # 2026-09-12. Pulsar folds don't care about the 1.6 MHz shift.
        # Settings._migrate_legacy_keys remaps a stored 1422e6 preset.
        self._freq_preset_options = [408000000.0, 680500000.0, 1299500000.0,
                                     1420406000.0, 1666000000.0, 2304000000.0, 0]
        self._freq_preset_labels = ['408 MHz', '680.5 MHz', '1299.5 MHz',
                                    '1420.4 MHz (HI feed)', '1666 MHz (OH)', '2304 MHz', 'Manual']
        self._freq_preset_group_box = QtWidgets.QGroupBox("Pulsar Band: ")
        self._freq_preset_box = QtWidgets.QVBoxLayout()
        class variable_chooser_button_group(QtWidgets.QButtonGroup):
            def __init__(self, parent=None):
                QtWidgets.QButtonGroup.__init__(self, parent)
            @Slot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._freq_preset_button_group = variable_chooser_button_group()
        self._freq_preset_group_box.setLayout(self._freq_preset_box)
        for i, _label in enumerate(self._freq_preset_labels):
            radio_button = QtWidgets.QRadioButton(_label)
            self._freq_preset_box.addWidget(radio_button)
            self._freq_preset_button_group.addButton(radio_button, i)
        self._freq_preset_callback = lambda i: QtCore.QMetaObject.invokeMethod(
            self._freq_preset_button_group, "updateButtonChecked",
            QtCore.Q_ARG("int", self._freq_preset_options.index(i)))
        self._freq_preset_callback(self.freq_preset)
        # PySide6 / Qt6: use idClicked instead of PyQt5's buttonClicked[int]
        self._freq_preset_button_group.idClicked.connect(
            lambda i: self.set_freq_preset(self._freq_preset_options[i]))
        self._tuning_group_layout.addWidget(self._freq_preset_group_box)

        self._freq_offset_0_range = Range(-100e6, 100e6, 100e3, self.freq_offset_0, 200)
        self._freq_offset_0_win = RangeWidget(self._freq_offset_0_range, self.set_freq_offset_0,
                                               "Coarse Tune (Hz)", "counter_slider",
                                               float, Qt.Horizontal)
        self._tuning_group_layout.addWidget(self._freq_offset_0_win)

        self._freq_offset_range = Range(-10e6, 10e6, 100e3, self.freq_offset, 200)
        self._freq_offset_win = RangeWidget(self._freq_offset_range, self.set_freq_offset,
                                             "Fine Tune (Hz)", "counter_slider",
                                             float, Qt.Horizontal)
        self._tuning_group_layout.addWidget(self._freq_offset_win)

        # LO offset: parks the hardware LO off the displayed center so the
        # zero-IF DC artefact lands off-target (out of band once |offset| >
        # samp_rate/2). Display + recorded frequencies are unchanged.
        self._lo_offset_range = Range(-30e6, 30e6, 100e3, self.lo_offset, 200)
        self._lo_offset_win = RangeWidget(self._lo_offset_range, self.set_lo_offset,
                                          "LO Offset (Hz)", "counter_slider",
                                          float, Qt.Horizontal)
        self._lo_offset_win.setToolTip(
            "Parks the receiver's LO this far from the displayed center; the "
            "DDC shifts the band back, so nothing else changes — but the "
            "receiver's own DC spike moves off your target. Set beyond half "
            "the sample rate to push it out of the recorded band entirely. "
            "0 = classic behavior. (Needs hardware support: B200/B210 yes; "
            "Soapy radios only if the driver has a shift stage.)")
        self._tuning_group_layout.addWidget(self._lo_offset_win)

        self._freq_manual_tool_bar = QtWidgets.QToolBar(self)
        self._freq_manual_tool_bar.addWidget(QtWidgets.QLabel("Manual Frequency (Hz): "))
        self._freq_manual_line_edit = QtWidgets.QLineEdit(str(self.freq_manual))
        self._freq_manual_tool_bar.addWidget(self._freq_manual_line_edit)
        self._freq_manual_line_edit.editingFinished.connect(
            lambda: self.set_freq_manual(eng_notation.str_to_num(str(self._freq_manual_line_edit.text()))))
        self._tuning_group_layout.addWidget(self._freq_manual_tool_bar)

        # --- Recording: SigMF sink is built on demand in _start_recording()
        # and torn down in _stop_recording() via top_block.lock()/unlock().
        # The previous always-on valve+sink combo wrote to disk from launch
        # and the valve toggle didn't actually gate in real time — that was
        # the source of the freeze when Record was clicked at high sample
        # rates (the valve toggle contended with the always-busy sink). ---
        self._sigmf_sink = None  # current sink, or None when not recording

        ##################################################
        # Connections
        ##################################################
        # Display branch: source -> chunk -> decimate -> Python sink. The
        # source differs between live (USRP) and playback (file → throttle →
        # rotator for digital retuning).
        if self._playback_mode:
            self.connect((self._file_source, 0), (self._throttle, 0))
            self.connect((self._throttle, 0), (self._rotator, 0))
            self.connect((self._rotator, 0), (self._stream_to_vec, 0))
        else:
            self.connect((self.uhd_usrp_source_0, 0), (self._stream_to_vec, 0))
        self.connect((self._stream_to_vec, 0), (self._keep_one_in_n, 0))
        self.connect((self._keep_one_in_n, 0), (self._sample_sink, 0))

        # Push the saved spectrum + waterfall + UI settings into the widgets
        # now that everything exists. Top-level values (tuning, gain, samp,
        # recording dir) were applied above via the variable initialisation.
        self._apply_widget_settings()

        # In playback mode, disable everything that depends on a real USRP.
        if self._playback_mode:
            self._apply_playback_ui()

        # Set up the auto-update checker (background thread, fires the
        # update-available signal if the configured manifest URL advertises
        # a newer version). Configured URL = no traffic until the developer
        # publishes one.
        self._setup_update_checker()

    @staticmethod
    def _decim_for(samp_rate, target_vec_per_sec=400):
        """Pick keep_one_in_n's N so the Python sink sees ~target vectors/sec.

        v1.1.7 sensitivity fix: 400 vec/s x 65536 samples ≈ 26 MS/s of
        throughput, so N=1 (nothing dropped) at every rate the B210 pulsar
        band uses; the display integrator then Welch-averages the WHOLE
        stream instead of a 0.3% snapshot (Ray's weak-signal report). The
        vector copies cost ~150 MB/s of memcpy at 20 MS/s — cheap; the FFT
        cost is bounded separately by the integrator's adaptive stride."""
        return max(1, int(round(samp_rate / CHUNK_SIZE / float(target_vec_per_sec))))

    def _apply_playback_ui(self):
        """Disable the controls that have no meaning for a recorded file
        (sample rate, gain, recording) and label the recording status. The
        Tuning group stays enabled — it drives a digital frequency shift
        within the recording's bandwidth via blocks.rotator_cc."""
        self.setWindowTitle(f"{APP_NAME}  —  v{APP_VERSION}  [Playback]")
        self._tuning_group.setToolTip(
            "Virtual tuning: shifts the spectrum digitally within the "
            "recording's bandwidth. Outside ±(samp_rate/2) of the file's "
            "original center frequency you'll just see noise / wrap-around.")
        self._samp_rate_tool_bar.setEnabled(False)
        self._samp_rate_tool_bar.setToolTip("Disabled in playback mode "
                                             "(sample rate comes from the file).")
        self._samp_rate_manual_tool_bar.setEnabled(False)
        self._samp_rate_manual_tool_bar.setToolTip(
            "Disabled in playback mode (sample rate comes from the file).")
        self._gain_win.setEnabled(False)
        self._gain_win.setToolTip("Disabled in playback mode.")
        self._record_tool_bar.setEnabled(False)
        self._record_tool_bar.setToolTip("Recording is disabled in playback mode.")
        self._record_format_tool_bar.setEnabled(False)
        self._fil_geom_widget.setEnabled(False)
        self._recording_dir_button.setEnabled(False)
        if self._playback_path:
            self._recording_status.setText(
                f"Playback (looping): {Path(self._playback_path).name}.sigmf-data")
            self._recording_status.setToolTip(self._playback_path + '.sigmf-data')

    def _on_processor_frame_cache(self, avg_db, _max_db, _min_db):
        """Stash the most recent processor frame for the sweep loop."""
        self._sweep_latest_avg_db = avg_db

    @Slot(bool)
    def _on_sweep_max_hold_toggle(self, on):
        """When in Sweep mode, the Max-hold checkbox controls our cross-pass
        max accumulator (per-frame max-hold doesn't make sense across retunes).
        Unchecking it stops drawing the max trace but does NOT clear the
        accumulator — re-checking immediately shows the running max."""
        if not self._sweep_active:
            return
        self._sweep_show_max = bool(on)
        # Push the current state to the plot right away so the user sees the
        # max trace appear/disappear without waiting for the next pass.
        self._finish_sweep_pass(_refresh_only=True)

    @Slot()
    def _on_sweep_reset_max(self):
        """Clear the cross-pass max-hold accumulator."""
        if not self._sweep_active:
            return
        self._sweep_max_db = None
        self._finish_sweep_pass(_refresh_only=True)

    def _effective_sweep_step_hz(self):
        """Resolved step size: user override if set, else ~80% of samp_rate.
        Clamped to (0, samp_rate]."""
        sr = float(self.samp_rate) if self.samp_rate else 1e6
        step = float(self._sweep_step_hz) if self._sweep_step_hz > 0 else sr * 0.8
        return max(1e3, min(sr, step))

    def _parse_eng_or_none(self, text):
        """Parse a user-entered frequency into Hz, accepting:
          * plain numbers ('690000000', '6.9e8'),
          * SI suffixes case-insensitively ('690M', '690m', '2G', '2g',
            '100k') — the lowercase letters would otherwise mean milli/etc.,
            but this is a radio app where every value is way above 1 Hz, so
            we treat them as the obvious mega/giga/kilo,
          * an optional trailing 'Hz' / 'MHz' / 'GHz' / 'kHz' (any case,
            spaces ignored).
        Returns float Hz, or None if it can't make sense of the input."""
        s = str(text).strip().replace(' ', '')
        if not s:
            return None
        if len(s) >= 2 and s[-2:].lower() == 'hz':
            s = s[:-2]
        # Normalize the SI suffix to the form gnuradio.eng_notation expects:
        # giga = 'G', mega = 'M', kilo = 'k' (lowercase, because eng_notation
        # uses uppercase 'K' for kelvin / nothing). The user may type either
        # case for any of them.
        _suffix_map = {'g': 'G', 'G': 'G',
                       'm': 'M', 'M': 'M',
                       'k': 'k', 'K': 'k'}
        if s and s[-1] in _suffix_map:
            s = s[:-1] + _suffix_map[s[-1]]
        try:
            return float(eng_notation.str_to_num(s))
        except Exception:
            pass
        try:
            return float(s)
        except Exception:
            return None

    def _on_sweep_range_edit(self):
        """Parse Start/Stop edits, clamp to the radio's range, persist, and
        restart the sweep loop if Sweep mode is active."""
        start = self._parse_eng_or_none(self._sweep_start_edit.text())
        stop  = self._parse_eng_or_none(self._sweep_stop_edit.text())
        if start is None or stop is None or stop <= start:
            # Bad input: refresh the edits to the last good values.
            self._sweep_start_edit.setText(_fmt_hz(self._sweep_start_hz))
            self._sweep_stop_edit.setText(_fmt_hz(self._sweep_stop_hz))
            return
        if self._hw_freq_range is not None:
            lo, hi = self._hw_freq_range
            start = max(lo, min(hi, start))
            stop  = max(lo, min(hi, stop))
            if stop <= start:
                stop = min(hi, start + 100e6)
        self._sweep_start_hz = start
        self._sweep_stop_hz  = stop
        self._sweep_start_edit.setText(_fmt_hz(start))
        self._sweep_stop_edit.setText(_fmt_hz(stop))
        self._save_setting('sweep', 'start_hz', float(start))
        self._save_setting('sweep', 'stop_hz',  float(stop))
        if self._sweep_active:
            self._sweep_max_db = None   # bins remap; old max-hold no longer applies
            self._start_sweep_pass()

    def _on_sweep_step_edit(self):
        """Parse the Step edit ('auto' or an eng-notation number), persist,
        and restart the sweep loop."""
        text = self._sweep_step_edit.text().strip().lower()
        if text in ("", "auto", "0"):
            step = 0.0
        else:
            parsed = self._parse_eng_or_none(text)
            step = parsed if (parsed is not None and parsed > 0) else 0.0
        self._sweep_step_hz = step
        self._sweep_step_edit.setText(
            "auto" if step <= 0 else _fmt_hz(step))
        self._save_setting('sweep', 'step_hz', float(step))
        if self._sweep_active:
            self._sweep_max_db = None   # bins remap; old max-hold no longer applies
            self._start_sweep_pass()

    def _on_mode_changed(self, mode: str):
        """Switch between Live and Sweep. Mutually exclusive; persists
        the selection."""
        want_sweep = (mode == 'sweep')
        # Tuning group is meaningless in Sweep (we choose the center per step);
        # disable so the user isn't confused. The Sweep group is hidden in
        # Live so it doesn't clutter the sidebar.
        self._tuning_group.setEnabled(not want_sweep)
        self._fft_plot._baseline_group.setEnabled(not want_sweep)
        self._sweep_group.setVisible(want_sweep)
        if want_sweep and not self._playback_mode:
            self._enter_sweep_mode()
        else:
            self._exit_sweep_mode()
        self._sweep_enabled = want_sweep
        self._save_setting('sweep', 'enabled', want_sweep)
        self._on_science_param_changed()

    def _enter_sweep_mode(self):
        """Force the processor into one-shot mode (alpha=1, no per-frame
        hold) and kick off the first sweep pass. Cross-pass max-hold takes
        over the role of the existing Max-hold checkbox."""
        if self._sweep_active:
            return
        # Save the user's prior averaging/hold settings; force the processor
        # into a "one frame at a time" configuration that's meaningful per
        # tune step. Restored in _exit_sweep_mode.
        self._sweep_saved_alpha = float(self._processor._avg_alpha)
        self._sweep_saved_max   = bool(self._processor._max_on)
        self._sweep_saved_min   = bool(self._processor._min_on)
        self._processor.set_average_alpha(1.0)
        self._processor.set_max_hold(False)
        self._processor.set_min_hold(False)
        # A stored OFF-source reference / flatten is center-frequency specific,
        # so it would corrupt the retuned per-step FFTs — force baseline off and
        # restore it on exit. Frozen markers would point at the wrong frequency
        # once the x-axis becomes the wide swept span, so clear them.
        self._sweep_saved_baseline = self._processor._baseline_mode
        self._processor.set_baseline_mode('off')
        self._fft_plot.clear_markers()
        # Detach the processor's continuous frame_ready from the plots — in
        # Sweep mode each processor frame is a single-tune-step FFT, but the
        # plots are now configured for the WIDE stitched x-axis, so letting
        # those frames through would render single-step data smeared across
        # the whole span (wrong bin geometry, signals at the wrong x). We
        # drive the plots ourselves from _finish_sweep_pass instead. The
        # cache slot stays connected — it's what the sweep loop reads.
        try:
            self._processor.frame_ready.disconnect(self._fft_plot.on_frame)
            self._processor.frame_ready.disconnect(self._waterfall_plot.on_frame)
        except (TypeError, RuntimeError):
            pass
        # Mirror the user's prior Max-hold preference into the cross-pass
        # accumulator so it just keeps working when they enter Sweep.
        self._sweep_show_max = self._sweep_saved_max
        self._sweep_max_db = None
        # Always re-apply the sweep x-axis on entry, even if the grid hasn't
        # changed from a previous sweep run — otherwise the plot keeps the
        # Live axis that _exit_sweep_mode set last time.
        self._sweep_axis_needs_apply = True
        self._sweep_active = True
        self._start_sweep_pass()

    def _exit_sweep_mode(self):
        """Stop the sweep loop, reconnect the live plot pipeline, and restore
        the user's prior processor settings."""
        if not self._sweep_active:
            return
        self._sweep_active = False
        self._sweep_timer.stop()
        # Reattach the processor's continuous frame_ready to the plots so the
        # Live view resumes. Use a try/except in case it's somehow already
        # connected (e.g. an aborted enter_sweep_mode).
        try:
            self._processor.frame_ready.connect(self._fft_plot.on_frame)
            self._processor.frame_ready.connect(self._waterfall_plot.on_frame)
        except Exception:
            pass
        if self._sweep_saved_alpha is not None:
            self._processor.set_average_alpha(self._sweep_saved_alpha)
            self._processor.set_max_hold(self._sweep_saved_max)
            self._processor.set_min_hold(self._sweep_saved_min)
            if self._sweep_saved_baseline is not None:
                self._processor.set_baseline_mode(self._sweep_saved_baseline)
                self._sweep_saved_baseline = None
            self._sweep_saved_alpha = None
        self._fft_plot.clear_markers()
        # Restore the plots' frequency range to the tuned center.
        self._fft_plot.set_frequency_range(self.center_freq, self.samp_rate)
        self._waterfall_plot.set_frequency_range(self.center_freq, self.samp_rate)
        # Retune the radio back to the user's chosen center frequency, and
        # drop sweep-step samples/averages so live view starts clean.
        if self._source is not None:
            self._source.set_center_freq(self.center_freq)
        self._processor.reset_averaging()
        self._sweep_status_label.setText("Idle")

    def _start_sweep_pass(self):
        """(Re)compute the sweep grid from current Start/Stop/Step + FFT size,
        allocate (or reuse) the wide buffer, and start step 0. Reusing the
        previous pass's wide_db is intentional: each step overwrites one
        slot, so the display always shows a complete wide trace with the
        freshly-swept region updated (no flat-line gap on the right)."""
        if not self._sweep_active or self._source is None:
            return
        n = int(self._processor._fft_size)
        sr = float(self.samp_rate)
        step_hz = self._effective_sweep_step_hz()
        kept_bins = max(8, int(round(step_hz / sr * n)))
        # Even kept_bins keeps a symmetric slice around DC (avoids the bin
        # right on top of the LO leakage and keeps left/right symmetric).
        kept_bins -= kept_bins % 2
        if kept_bins >= n:
            kept_bins = n - 2
        start_idx = (n - kept_bins) // 2
        span = self._sweep_stop_hz - self._sweep_start_hz
        n_steps = max(1, int(np.ceil(span / step_hz)))
        # Adjusted step so n_steps tiles [start, start+n_steps*step) exactly.
        actual_step = span / n_steps
        # Recompute kept_bins so the tile width matches actual_step.
        kept_bins = max(8, int(round(actual_step / sr * n)))
        kept_bins -= kept_bins % 2
        kept_bins = min(kept_bins, n - 2)
        start_idx = (n - kept_bins) // 2
        self._sweep_n_steps   = n_steps
        self._sweep_kept_bins = kept_bins
        self._sweep_start_idx = start_idx
        self._sweep_eff_bw     = n_steps * actual_step
        self._sweep_eff_center = self._sweep_start_hz + self._sweep_eff_bw / 2.0
        self._sweep_step_actual_hz = actual_step
        total = n_steps * kept_bins
        grid_changed = (self._sweep_wide_db is None
                        or len(self._sweep_wide_db) != total)
        if grid_changed:
            # Fresh allocation (first pass or grid changed) — paint a flat
            # floor so progressive fill is visible against something. Also
            # reset the max-hold accumulator (bins remap).
            self._sweep_wide_db = np.full(total, -150.0, dtype=np.float32)
            self._sweep_max_db = None
        # Apply the sweep x-axis when the grid changed OR we just entered
        # Sweep mode (the axis_needs_apply one-shot flag). Otherwise leave
        # the plot's visible range alone so a user zoom into a region of
        # interest survives pass-to-pass updates within the same sweep run.
        if grid_changed or self._sweep_axis_needs_apply:
            self._fft_plot.set_frequency_range(self._sweep_eff_center,
                                               self._sweep_eff_bw)
            self._waterfall_plot.set_frequency_range(self._sweep_eff_center,
                                                     self._sweep_eff_bw)
            self._sweep_axis_needs_apply = False
        else:
            # Pass-to-pass update with same grid and no entry — preserve the
            # user's zoom by setting the plot widgets' internal freq state
            # directly, without calling set_frequency_range (which calls
            # setXRange and would reset the view).
            self._fft_plot._center_freq = self._sweep_eff_center
            self._fft_plot._samp_rate = self._sweep_eff_bw
            self._waterfall_plot._center_freq = self._sweep_eff_center
            self._waterfall_plot._samp_rate = self._sweep_eff_bw
        self._sweep_step_idx = 0
        self._sweep_latest_avg_db = None
        self._tune_for_sweep_step(0)
        self._sweep_timer.start(self._sweep_settle_ms)

    def _tune_for_sweep_step(self, idx):
        """Retune the source to step `idx`'s center frequency and update
        the status label."""
        center = (self._sweep_start_hz
                  + (idx + 0.5) * self._sweep_step_actual_hz)
        if self._source is not None:
            self._source.set_center_freq(center)
        # Retune barrier: reset the (alpha=1.0) average and flush the sink —
        # the flush also blanks the next drain, so pre-retune samples still
        # in flight in the GR pipeline are discarded rather than averaged
        # into this step's tile. The first EMITTED frame therefore contains
        # only post-retune data; capture retries until one exists.
        self._processor.reset_averaging()
        self._sweep_status_label.setText(
            f"Step {idx + 1}/{self._sweep_n_steps} @ {_fmt_hz(center)}")

    @Slot()
    def _sweep_capture_and_advance(self):
        """Settle timer fired: capture the cached frame for the current
        step, write its center slice into the wide buffer, advance to the
        next step or finish the pass."""
        if not self._sweep_active:
            return
        avg = self._sweep_latest_avg_db
        # The retune flush blanks the first post-retune drain, so the first
        # CLEAN frame can arrive up to ~2 tick intervals after the retune —
        # possibly after a short settle timer. Re-wait briefly rather than
        # writing a hole (or, worse, capturing nothing pass after pass with
        # a very short settle_ms).
        if avg is None and self._sweep_capture_retries < 5:
            self._sweep_capture_retries += 1
            self._sweep_timer.start(60)
            return
        self._sweep_capture_retries = 0
        kb = self._sweep_kept_bins
        si = self._sweep_start_idx
        i = self._sweep_step_idx
        wrote_slot = False
        if avg is not None and len(avg) >= si + kb and self._sweep_wide_db is not None:
            self._sweep_wide_db[i * kb:(i + 1) * kb] = np.asarray(
                avg[si:si + kb], dtype=np.float32)
            wrote_slot = True
        # Else: no frame arrived yet (very first step before any processor
        # tick) — leave the previous pass's value (or the -150 floor) in
        # place; the next pass will fill it.

        # Push the partial wide buffer to the FFT plot so the trace updates
        # at the step rate instead of jumping once per full pass. Max-hold
        # stays at the prior pass's value until _finish_sweep_pass merges
        # the new pass in — consistent semantics (max-hold = per-pass).
        if wrote_slot and self._sweep_wide_db is not None:
            max_for_plot = (self._sweep_max_db
                            if (self._sweep_show_max
                                and self._sweep_max_db is not None
                                and len(self._sweep_max_db) == len(self._sweep_wide_db))
                            else None)
            self._fft_plot.on_frame(self._sweep_wide_db, max_for_plot, None)

        self._sweep_step_idx += 1
        if self._sweep_step_idx >= self._sweep_n_steps:
            self._finish_sweep_pass()
            if self._sweep_active:
                self._start_sweep_pass()
            return
        self._tune_for_sweep_step(self._sweep_step_idx)
        self._sweep_latest_avg_db = None  # force a fresh frame post-settle
        self._sweep_timer.start(self._sweep_settle_ms)

    def _finish_sweep_pass(self, _refresh_only=False):
        """Push the stitched wide spectrum to the FFT + waterfall plots, and
        update the cross-pass max-hold accumulator. `_refresh_only=True` skips
        the max-hold update (used when the Max-hold checkbox or Reset Max
        button needs to refresh the display without a fresh pass)."""
        if self._sweep_wide_db is None:
            return
        # Update the cross-pass max-hold accumulator from the new pass.
        if not _refresh_only:
            if (self._sweep_max_db is None
                    or len(self._sweep_max_db) != len(self._sweep_wide_db)):
                self._sweep_max_db = self._sweep_wide_db.copy()
            else:
                np.maximum(self._sweep_max_db, self._sweep_wide_db,
                           out=self._sweep_max_db)
        # The plots' x-axis was set by _start_sweep_pass on grid change;
        # don't re-call set_frequency_range here, it would reset any user
        # zoom on every pass. Just push the wide buffer.
        # Pass the cross-pass max array when the user has Max-hold checked
        # (the FftPlotWidget draws it as the green Max-hold trace). Min-hold
        # is not implemented for sweep — pass None.
        max_for_plot = (self._sweep_max_db
                        if (self._sweep_show_max
                            and self._sweep_max_db is not None)
                        else None)
        self._fft_plot.on_frame(self._sweep_wide_db, max_for_plot, None)
        self._waterfall_plot.on_frame(self._sweep_wide_db, None, None)

    def _build_menu_bar(self):
        bar = QtWidgets.QMenuBar(self)

        file_menu = bar.addMenu("&File")
        choose_dir_act = QtGui.QAction("&Set Recording Folder…", self)
        choose_dir_act.setToolTip(
            "Choose where recordings are written (same as the Folder button "
            "in the Recording panel)")
        choose_dir_act.triggered.connect(self._on_change_recording_dir)
        file_menu.addAction(choose_dir_act)
        open_dir_act = QtGui.QAction("&Open Recordings Folder", self)
        open_dir_act.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(self.recording_dir)))
        file_menu.addAction(open_dir_act)
        file_menu.addSeparator()
        exit_act = QtGui.QAction("E&xit", self)
        exit_act.setShortcut(QtGui.QKeySequence.Quit)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        # Dock show/hide toggles are appended here by _make_dock as each
        # panel is created (the menu bar is built first in __init__).
        self._view_menu = bar.addMenu("&View")
        # Two column entries, each a checkable action that shows/hides the
        # WHOLE column, with that column's panels as a submenu underneath
        # (Rick, 2026-08-04). _make_dock files each panel into the submenu
        # matching the area it was created in.
        # Each column is a SUBMENU whose first item toggles the whole column,
        # with that column's panels listed below it. (A checkable action that
        # owns a submenu is not clickable in Qt — clicking only opens the
        # submenu — so the column toggle has to live inside.)
        self._col_menus = {}
        for area, label in ((Qt.LeftDockWidgetArea, "&Display Panels (left)"),
                            (Qt.RightDockWidgetArea, "&Control Panels (right)")):
            sub = self._view_menu.addMenu(label)
            act = QtGui.QAction("Show this column", self)
            act.setCheckable(True)
            act.setChecked(True)
            act.triggered.connect(
                lambda on, a=area: self._set_dock_column_visible(a, on))
            sub.addAction(act)
            sub.addSeparator()
            self._col_menus[area] = (act, sub)
        self._view_menu_sep = self._view_menu.addSeparator()
        fs_act = QtGui.QAction("&Full Screen", self)
        fs_act.setCheckable(True)
        fs_act.setShortcut(QtGui.QKeySequence.FullScreen)
        fs_act.toggled.connect(
            lambda on: self.showFullScreen() if on else self.showNormal())
        self._view_menu.addAction(fs_act)

        radio_menu = bar.addMenu("&Radio")
        dev_act = QtGui.QAction("Change &Device…", self)
        dev_act.triggered.connect(self._on_change_device_clicked)
        radio_menu.addAction(dev_act)

        obs_menu = bar.addMenu("&Observe")
        insight_act = QtGui.QAction("&Pulsars in View…", self)
        insight_act.setShortcut("Ctrl+P")
        insight_act.setToolTip(
            "Which pulsars are above the horizon here, right now — with "
            "P0, DM, flux in the tuned band, and time left before each sets")
        insight_act.triggered.connect(self._show_pulsar_planner)
        obs_menu.addAction(insight_act)
        refresh_cat_act = QtGui.QAction("&Refresh Pulsar Catalog", self)
        refresh_cat_act.setToolTip(
            "Re-download the ATNF catalog (cached locally; works offline "
            "afterwards)")
        refresh_cat_act.triggered.connect(
            lambda: self._show_pulsar_planner(force_refresh=True))
        obs_menu.addAction(refresh_cat_act)
        obs_menu.addSeparator()
        selftest_act = QtGui.QAction("B210 &Self Test…", self)
        selftest_act.setToolTip(
            "Transmit a synthetic dispersed pulsar from the B210's own TX "
            "at minimum gain, record the internal leakage, fold it with "
            "PRESTO, and grade PASS/FAIL — proves the whole chain with no "
            "cable before telescope time is spent")
        selftest_act.triggered.connect(self._show_selftest_dialog)
        obs_menu.addAction(selftest_act)
        obs_menu.addSeparator()
        dsr_act = QtGui.QAction("&Drift-Scan Review…", self)
        dsr_act.setToolTip(
            "Open recorded ezRA drift-scan files (Workbench or ezCol) and "
            "get the standard morning-after report: header sanity, gap and "
            "spur census, flattened waterfall, velocity profile, and a "
            "transit fit — plus a peak-vs-day trend across several files")
        dsr_act.triggered.connect(self._show_driftscan_review)
        obs_menu.addAction(dsr_act)

        rec_menu = bar.addMenu("Recor&ding")
        rec_start_act = QtGui.QAction("&Start Recording", self)
        rec_start_act.triggered.connect(lambda: self.set_record(1))
        rec_menu.addAction(rec_start_act)
        rec_stop_act = QtGui.QAction("S&top Recording", self)
        rec_stop_act.triggered.connect(lambda: self.set_record(0))
        rec_menu.addAction(rec_stop_act)

        help_menu = bar.addMenu("&Help")
        guide_act = QtGui.QAction("&User Guide", self)
        guide_act.setShortcut(QtGui.QKeySequence.HelpContents)
        guide_act.triggered.connect(self._show_help_dialog)
        help_menu.addAction(guide_act)
        update_act = QtGui.QAction("Check for &Updates…", self)
        update_act.triggered.connect(self._check_for_updates_manual)
        help_menu.addAction(update_act)
        about_act = QtGui.QAction("&About…", self)
        about_act.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_act)
        return bar

    def _docks_in_area(self, area):
        """Panels belonging to `area` — by creation area, not current
        position, so a floated panel still counts as part of its column."""
        return [d for d in getattr(self, '_docks', [])
                if getattr(d, '_dses_home_area', None) == area]

    def _set_dock_column_visible(self, area, on):
        """Show/hide a whole column of panels at once (View menu).

        Hiding remembers which panels were visible so showing the column
        again restores exactly that set rather than blindly showing all —
        a panel the user had closed individually stays closed.
        """
        docks = self._docks_in_area(area)
        if not docks:
            return
        if not hasattr(self, '_col_hidden_state'):
            self._col_hidden_state = {}
        if on:
            remembered = self._col_hidden_state.get(area)
            for d in docks:
                d.setVisible(remembered is None or d.objectName() in remembered)
        else:
            self._col_hidden_state[area] = {
                d.objectName() for d in docks if d.isVisible()}
            for d in docks:
                d.setVisible(False)
        self._refresh_dock_column_checks(area)

    def _refresh_dock_column_checks(self, area):
        entry = getattr(self, '_col_menus', {}).get(area)
        if entry is None:
            return
        act, _sub = entry
        any_visible = any(d.isVisible() for d in self._docks_in_area(area))
        if act.isChecked() != any_visible:
            with _SignalBlocker(act):
                act.setChecked(any_visible)

    # --- Pulsar visibility planner ---------------------------------------

    def _site_dict(self):
        s = self._app_settings
        return {"lat_deg": s.get_float('site', 'lat_deg'),
                "lon_deg": s.get_float('site', 'lon_deg'),
                "amsl": s.get_float('site', 'amsl'),
                "mask_deg": s.get_float('site', 'el_mask_deg'),
                "sefd_jy": s.get_float('site', 'sefd_jy'),
                "name": s.get_str('site', 'name')}

    def _ensure_psr_catalog(self, force_refresh=False):
        """Load (or return the cached) ATNF catalog; None + a warning dialog
        on failure. Shared by the planner and the self-test simulator."""
        import pulsar_planner
        cat = getattr(self, '_psr_catalog', None)
        if cat is not None and not force_refresh:
            return cat
        cat = pulsar_planner.Catalog(cache_dir=self.recording_dir)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        self._status_bar.showMessage("Loading pulsar catalog…")
        try:
            ok = cat.load(force_refresh=force_refresh)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not ok:
            self._status_bar.clearMessage()
            QtWidgets.QMessageBox.warning(
                self, "Pulsar catalog unavailable",
                "Could not load the ATNF catalog and no local cache is "
                f"present.\n\n{cat.error or 'No network connection.'}\n\n"
                "Connect once to build the cache; after that the planner "
                "works offline.")
            return None
        self._psr_catalog = cat
        self._status_bar.showMessage(
            f"Pulsar catalog: {len(cat.rows)} sources ({cat.source})", 6000)
        return cat

    def _show_pulsar_planner(self, force_refresh=False):
        """Open the "what's up now?" dialog. The catalog is fetched once and
        cached next to the recordings, so the field boxes work offline."""
        cat = self._ensure_psr_catalog(force_refresh=force_refresh)
        if cat is None:
            return

        dlg = PulsarPlannerDialog(cat.rows, self._site_dict(),
                                  self.center_freq, self,
                                  rate_hz=getattr(self, "samp_rate", 2e6),
                                  nchan=getattr(self, "_fil_nchans", 2048))
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.selected:
            r = dlg.selected
            name = r["bname"] if r["bname"] != "*" else r["name"]
            self._source_name_edit.setText(name)
            self._on_source_name_changed()
            # Exact catalog position beats the app's parse-the-name fallback;
            # it goes straight into the .fil header for PRESTO.
            self._catalog_radec = (r["raj"], r["decj"])
            self._catalog_radec_for = name
            self._save_setting('site', 'el_mask_deg', dlg._mask.value())
            hrs = r["hours_left"]
            left = ("circumpolar" if hrs >= 23.99
                    else f"{int(hrs)}h {int((hrs % 1) * 60):02d}m above mask")
            # The alt/az/time-left quoted here belong to whatever instant the
            # planner was showing; if that was a planned one, say so rather
            # than letting it read as the live sky.
            when = (f"  [as of {dlg._fmt_clock(dlg._ref_ts_used)}]"
                    if dlg._plan.isChecked() else "")
            self._status_bar.showMessage(
                f"Source: {name} — alt {r['alt_deg']:.1f}°, az "
                f"{r['az_deg']:.1f}°, {left}"
                + (f", P0 {r['p0_s']:.6f} s, DM {r['dm']:.2f}"
                   if r["p0_s"] and r["dm"] else "") + when, 15000)

    def _check_source_visibility(self, start_ts=None):
        """Warn if the named source sets before a timed recording finishes.
        Returns True to proceed. Silent when there is no catalog loaded, no
        source name, or no duration — the planner is an aid, not a gate.
        `start_ts`: evaluate from a scheduled start instead of from now."""
        import pulsar_planner
        cat = getattr(self, '_psr_catalog', None)
        name = (self._source_name or "").strip()
        dur = self._parse_duration_s(self._rec_duration_text)
        if cat is None or not name or not dur:
            return True
        row = pulsar_planner.find_by_name(cat.rows, name)
        if row is None:
            return True
        site = self._site_dict()
        sets, left = pulsar_planner.sets_before(
            row, site["lat_deg"], site["lon_deg"], site["mask_deg"], dur,
            unix_ts=start_ts)
        if not sets:
            return True
        mins = left * 60.0
        when = (f"at the scheduled start ({self._fmt_clock_tz(start_ts)})"
                if start_ts is not None else "now")
        problem = (f"{name} is below the {site['mask_deg']:.0f}° elevation "
                   f"mask {when}." if mins < 0.5 else
                   f"{name} drops below the {site['mask_deg']:.0f}° elevation "
                   f"mask {mins:.0f} minutes after the start ({when}), but the "
                   f"recording is set to run for {dur / 60.0:.0f} minutes.")
        r = QtWidgets.QMessageBox.warning(
            self, "Source sets before the recording ends",
            problem + "\n\nThe tail of the recording would be of an empty "
            "sky.\n\nRecord anyway?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    def _show_help_dialog(self):
        HelpDialog(self).exec()

    def _show_about_dialog(self):
        dlg = AboutDialog(self._app_settings, self)
        dlg.defaults_requested.connect(self._on_defaults_requested)
        dlg.exec()

    # --- auto-update plumbing ---

    def _setup_update_checker(self):
        """Create the checker, wire its signals to the notification dialog,
        and kick off a background check if auto-check is on AND we haven't
        checked recently AND a manifest URL is configured."""
        s = self._app_settings
        self._update_checker = UpdateChecker(s, parent=self)
        self._update_checker.update_available.connect(self._show_update_dialog)
        # Don't bother the user with no_update / check_failed on the auto path —
        # those are connected only for the manual menu trigger via _check_for_updates_manual.
        if not s.get_bool('updates', 'auto_check'):
            return
        if not s.get_str('updates', 'manifest_url').strip():
            return  # no URL configured yet
        # Debounce
        last_iso = s.get_str('updates', 'last_check_iso')
        try:
            interval_h = max(1, s.get_int('updates', 'check_interval_hours'))
        except Exception:
            interval_h = 24
        if last_iso:
            try:
                last = datetime.fromisoformat(last_iso)
                if (datetime.now() - last).total_seconds() < interval_h * 3600:
                    return  # checked recently, skip
            except ValueError:
                pass
        # Trigger after the GUI has painted at least once so the dialog
        # doesn't appear before the main window.
        QTimer.singleShot(2000, self._update_checker.check_now)

    @Slot(str, str, str)
    def _show_update_dialog(self, latest, url, notes):
        # Respect a previously-clicked "Skip this version" — don't re-show
        # the dialog unless the manifest now advertises a strictly newer one.
        dismissed = self._app_settings.get_str('updates', 'dismissed_version').strip()
        if dismissed and _parse_version(latest) <= _parse_version(dismissed):
            return
        dlg = UpdateNotificationDialog(latest, url, notes, APP_VERSION, parent=self)
        dlg.dismissed_for_version.connect(self._on_update_dismissed)
        dlg.install_requested.connect(self._install_update)
        dlg.show()  # non-modal, but bring it to the front so it isn't missed
        dlg.raise_()
        dlg.activateWindow()
        # Keep a reference so it isn't garbage-collected when this slot returns.
        self._update_dialog = dlg

    @Slot(str)
    def _on_update_dismissed(self, version):
        self._app_settings.set('updates', 'dismissed_version', version)
        try:
            self._app_settings.save()
        except OSError:
            pass

    @Slot(str, str)
    def _install_update(self, download_url, latest_version):
        """Ask where to install, then download/verify/extract/install on a
        background thread with a progress dialog."""
        if not download_url:
            return
        install_dir = Path(__file__).resolve().parent
        choose = InstallUpdateDialog(install_dir, latest_version, parent=self)
        if choose.exec() != QtWidgets.QDialog.Accepted:
            return
        mode, dest, make_shortcut = choose.result_choice()

        prog = QtWidgets.QProgressDialog("Preparing…", "", 0, 0, self)
        prog.setWindowTitle("Installing Update")
        prog.setCancelButton(None)          # no mid-install cancel — it's destructive
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)

        installer = UpdateInstaller(download_url, mode, dest, make_shortcut,
                                    latest_version, parent=self)

        def on_progress(done_bytes, total_bytes):
            if total_bytes > 0:
                prog.setMaximum(total_bytes)
                prog.setValue(done_bytes)
            else:
                prog.setRange(0, 0)  # indeterminate
        installer.status.connect(prog.setLabelText)
        installer.progress.connect(on_progress)
        installer.done.connect(
            lambda ok, msg, path: self._on_install_done(ok, msg, mode, install_dir, prog))
        # Keep a reference so the installer/dialog survive this slot.
        self._installer = installer
        self._install_progress = prog
        prog.show()
        installer.start()

    def _on_install_done(self, ok, msg, mode, install_dir, prog):
        prog.close()
        if not ok:
            QtWidgets.QMessageBox.critical(
                self, "Update failed",
                f"{msg}\n\nYour current installation was left unchanged.")
            return
        if mode == 'in_place':
            r = QtWidgets.QMessageBox.question(
                self, "Update installed",
                "The update was installed over the current version.\n\n"
                "Restart now to use it?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if r == QtWidgets.QMessageBox.Yes:
                _relaunch(install_dir)
                self.close()  # closeEvent saves settings + stops the flowgraph
        else:
            QtWidgets.QMessageBox.information(self, "Update installed", msg)

    # --- Hot-plug radio detection (playback / no-radio mode) ---------------
    # The app opened without a receiver; poll for one being connected or
    # powered on and offer to switch — no manual restart dance. Enumeration
    # (UHD + Soapy) can block for a second or more, so it runs on a worker
    # thread; only the result crosses back to the GUI thread via a signal.

    class _HotplugSignals(QObject):
        found = Signal(list)

    _HOTPLUG_POLL_MS = 5000

    def _start_hotplug_watch(self):
        self._hotplug_sig = self._HotplugSignals()
        self._hotplug_sig.found.connect(self._on_hotplug_found)
        self._hotplug_busy = False
        self._hotplug_timer = QtCore.QTimer(self)
        self._hotplug_timer.setInterval(self._HOTPLUG_POLL_MS)
        self._hotplug_timer.timeout.connect(self._hotplug_poll)
        self._hotplug_timer.start()

    def _hotplug_poll(self):
        if self._hotplug_busy:
            return                      # previous enumeration still running
        self._hotplug_busy = True

        def _scan():
            try:
                devices = find_all_radios()
            except Exception:
                devices = []
            self._hotplug_busy = False
            if devices:
                self._hotplug_sig.found.emit(devices)

        threading.Thread(target=_scan, name="hotplug-scan",
                         daemon=True).start()

    def _on_hotplug_found(self, devices):
        timer = getattr(self, '_hotplug_timer', None)
        if timer is None:
            return                      # already handled
        timer.stop()
        self._hotplug_timer = None
        labels = "\n".join(f"  •  {d['label']}" for d in devices)
        r = QtWidgets.QMessageBox.question(
            self, "Radio detected",
            f"A receiver is now available:\n\n{labels}\n\n"
            "Restart the program to use it? (Playback stops; your settings "
            "are kept.)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes)
        if r == QtWidgets.QMessageBox.Yes:
            _relaunch(Path(__file__).resolve().parent)
            self.close()   # closeEvent saves settings + stops the flowgraph
        # Declined: stay in playback and don't nag again this session.

    def _check_for_updates_manual(self):
        """Help → Check for Updates… handler. Wires the no_update /
        check_failed signals to a one-shot dialog for this invocation."""
        s = self._app_settings
        url = s.get_str('updates', 'manifest_url').strip()
        if not url:
            QtWidgets.QMessageBox.information(
                self, "Updates",
                "Auto-update is not configured: the '[updates] manifest_url' "
                "setting is empty.\n\nAsk the program's distributor for the "
                "manifest URL and add it to your settings.ini, or use "
                "Help → About → Open Settings Folder to find the file.")
            return
        # Make a one-off checker so its signals don't accumulate handlers.
        ck = UpdateChecker(s, parent=self)
        ck.update_available.connect(self._show_update_dialog)
        ck.no_update.connect(lambda v: QtWidgets.QMessageBox.information(
            self, "Updates",
            f"You're running the latest version ({APP_VERSION}).\n\n"
            f"Manifest reports latest = {v}."))
        ck.check_failed.connect(lambda msg: QtWidgets.QMessageBox.warning(
            self, "Update check failed",
            f"Could not reach the update server.\n\n{msg}"))
        ck.check_now()

    def _on_defaults_requested(self):
        """After 'Restore Defaults' rewrites the INI, push every saved value
        back through the widgets and the flowgraph."""
        s = self._app_settings
        # Tuning + RX + recording dir
        self.recording_dir = s.get_str('recording', 'directory')
        os.makedirs(self.recording_dir, exist_ok=True)
        self._recording_dir_button.setText("Folder: " + self._elided_dir())
        self._recording_dir_button.setToolTip(self.recording_dir)
        # set_* methods drive the flowgraph and re-save to settings, so guard
        # with _applying_settings to avoid redundant writes.
        self._applying_settings = True
        try:
            self.set_samp_rate(s.get_float('rx', 'samp_rate_hz'))
            self.set_gain(s.get_float('rx', 'gain_db'))
            self.set_freq_preset(s.get_float('tuning', 'preset_hz'))
            self.set_freq_offset_0(s.get_float('tuning', 'coarse_hz'))
            self.set_freq_offset(s.get_float('tuning', 'fine_hz'))
            self.set_freq_manual(s.get_float('tuning', 'manual_hz'))
            self.set_lo_offset(s.get_float('tuning', 'lo_offset_hz'))
            self._lo_offset_win.set_value(self.lo_offset)
        finally:
            self._applying_settings = False
        self._apply_widget_settings()

    def _apply_widget_settings(self):
        """Push spectrum/waterfall/UI settings into the plot widgets and
        propagate to the processor."""
        self._applying_settings = True
        try:
            self._fft_plot.apply_settings(self._app_settings)
            self._waterfall_plot.apply_settings(self._app_settings)
            panels_on = self._app_settings.get_bool('ui', 'control_panels_visible')
            self._fft_plot._toggle_btn.setChecked(panels_on)
            self._waterfall_plot._toggle_btn.setChecked(panels_on)
        finally:
            self._applying_settings = False
        # Push the values into the processor explicitly (signals were blocked
        # while we set the UI to avoid the save round-trip).
        self._fft_plot.emit_settings_to_processor()
        # Restore THIS radio's dBm calibration offset (per-device, keyed by
        # driver+serial) on top of the generic spectrum settings.
        self._fft_plot.set_cal_offset_value(self._load_cal_offset())

    def _save_setting(self, section, key, value):
        if self._applying_settings:
            return
        self._app_settings.set(section, key, value)
        try:
            self._app_settings.save()
        except OSError as exc:
            print(f"Settings save failed: {exc}", file=sys.stderr)

    def _cal_device_key(self):
        """Stable [calibration] key for the current radio (driver+serial), or
        None in playback mode where there is no real device to calibrate."""
        if self._playback_mode or not self._device_driver:
            return None
        return f"{self._device_driver}__{self._device_serial or ''}"

    def _load_cal_offset(self):
        """This radio's saved dBm calibration offset (0.0 if never set)."""
        key = self._cal_device_key()
        if key is None:
            return 0.0
        return self._app_settings.get_float_or('calibration', key, 0.0)

    def _on_spectrum_control_changed(self, key, value):
        """Route spectrum control changes to the INI. The dBm calibration
        offset is per-device (keyed by driver+serial); everything else lives in
        the shared [spectrum] section."""
        if key == 'cal_offset_db':
            dev = self._cal_device_key()
            if dev is not None:
                self._save_setting('calibration', dev, float(value))
            return
        self._save_setting('spectrum', key, value)

    def moveEvent(self, event):
        QtWidgets.QWidget.moveEvent(self, event)
        self._remember_geometry()

    def resizeEvent(self, event):
        QtWidgets.QWidget.resizeEvent(self, event)
        self._remember_geometry()

    def _remember_geometry(self):
        """Record the FRAME position (self.pos()) + client size while the window
        is normally visible. We persist the frame top-left — the SAME reference
        move() sets — so save/restore round-trips exactly. Saving geometry() (the
        *client* top-left) but restoring with move() (the frame) drifts the
        window by the frame margins every launch (macOS 0/28, LXDE/Openbox 2/30).
        Ignored until the first-show restore has run and while minimized/
        maximized/fullscreen; a close-time query can also read (0, 0) on some X11
        WMs, so we track during normal use instead."""
        if not self._geometry_applied or not self.isVisible():
            return
        if self.isMinimized() or self.isMaximized() or self.isFullScreen():
            return
        p = self.pos()
        self._last_good_geom = (p.x(), p.y(), self.width(), self.height())

    def showEvent(self, event):
        QtWidgets.QWidget.showEvent(self, event)
        # Apply the saved size/position once, on first show — after every
        # widget exists and the window is being realized, so the resize sticks
        # (doing it during __init__ gets overwritten as later widgets are added).
        if not self._geometry_applied:
            self._geometry_applied = True
            self._restore_geometry()
            self._clamp_window_to_screen()
            # Snapshot the PRISTINE arrangement before any saved state is
            # applied — this is what the panel title bars' home button
            # restores. restoreState is the only mechanism that reliably
            # re-lays-out a dragged-out panel: every direct re-dock call
            # sequence (setFloating/addDockWidget/removeDockWidget, in all
            # orders) leaves Qt reporting the panel docked while it still
            # renders as a window over the plots (instrumented 2026-08-04).
            self._default_dock_state = self.saveState()
            # Restore the saved dock arrangement (positions, tabbing,
            # floating, visibility). Skipped silently on first run or if
            # the saved state predates a dock-name change.
            try:
                cp = self._app_settings._cp
                if cp.has_option('window', 'dock_state'):
                    self.restoreState(QtCore.QByteArray.fromBase64(
                        cp.get('window', 'dock_state').encode('ascii')))
            except Exception:
                pass
            # X11 reparenting WMs (LXDE/Openbox) may not have drawn the title-bar
            # frame yet when showEvent fires, so the move() above can land the
            # client at the requested y and push the title bar off the top edge.
            # Re-apply once the event loop has let the WM decorate the window.
            if sys.platform.startswith('linux'):
                QtCore.QTimer.singleShot(0, self._reapply_geometry)
            # Launched with no radio (playback mode): watch for one being
            # connected / powered on so the user doesn't have to know to
            # restart the app (Rick, 2026-08-03).
            if self._playback_mode:
                self._start_hotplug_watch()

    def _reapply_geometry(self):
        self._restore_geometry()
        self._clamp_window_to_screen()

    def _restore_geometry(self):
        """Apply the saved window size/position (plain ints in the INI). With
        nothing saved (first run), center on the screen on Linux rather than let
        the WM drop the window in the top-left corner where its title bar can be
        hard to grab; macOS/Windows keep their native first-run placement."""
        try:
            gw = self._app_settings.get_int('window', 'width')
            gh = self._app_settings.get_int('window', 'height')
        except Exception:
            gw = gh = 0
        if gw > 0 and gh > 0:
            self.resize(gw, gh)
            try:
                self.move(self._app_settings.get_int('window', 'x'),
                          self._app_settings.get_int('window', 'y'))
            except Exception as exc:
                print(f"Geometry position restore failed: {exc}", file=sys.stderr)
                self._center_on_screen()
        elif sys.platform.startswith('linux'):
            self._center_on_screen()

    def _center_on_screen(self):
        """Center the window on its current (or primary) screen's work area."""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        if avail.isEmpty():
            return   # headless / 0x0 screen — nothing to center against
        self.move(avail.left() + max(0, (avail.width() - self.width()) // 2),
                  avail.top() + max(0, (avail.height() - self.height()) // 2))

    def _clamp_window_to_screen(self):
        """Keep the whole decorated window — title bar included — on-screen, so a
        saved position can't open it with the title bar above the top edge where
        it can't be grabbed, and shrink it if it is larger than this display.
        Works in FRAME coordinates (frameGeometry + a delta move) so the WM
        decorations are accounted for on every platform."""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        if avail.isEmpty():
            # Headless / no connected output: Qt reports a 0x0 screen (seen on the
            # DSES drift-scan box — HDMI disconnected, viewed over RDP). Clamping
            # to a zero rect would shrink/move the window to garbage; leave the
            # restored geometry alone.
            return
        # Frame margins (title bar + borders); 0 until the WM has decorated.
        dw = self.frameGeometry().width() - self.width()
        dh = self.frameGeometry().height() - self.height()
        w = min(self.width(), max(200, avail.width() - dw))
        h = min(self.height(), max(150, avail.height() - dh))
        if w != self.width() or h != self.height():
            self.resize(w, h)
        fg = self.frameGeometry()
        nx = max(avail.left(), min(fg.left(), avail.right() - fg.width() + 1))
        ny = max(avail.top(),  min(fg.top(),  avail.bottom() - fg.height() + 1))
        dx, dy = nx - fg.left(), ny - fg.top()
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)   # shift the frame by delta

    def _save_geometry(self):
        """Persist the FRAME position + client size into the INI as plain
        integers, preferring the last values seen while the window was normally
        visible (see _remember_geometry) over a close-time query, which
        reparenting X11 WMs can report as (0, 0). Restoring the frame position
        with move() round-trips exactly. Used by closeEvent and the signal
        handler; the INI saves reliably on every platform."""
        try:
            if self._last_good_geom is not None:
                x, y, w, h = self._last_good_geom
            else:
                p = self.pos()
                x, y, w, h = p.x(), p.y(), self.width(), self.height()
            self._app_settings.set('window', 'x', int(x))
            self._app_settings.set('window', 'y', int(y))
            self._app_settings.set('window', 'width', int(w))
            self._app_settings.set('window', 'height', int(h))
            # Dock arrangement too: closeEvent AND the SIGINT/SIGTERM handler
            # both come through here, so a terminal Ctrl-C / kill no longer
            # loses the dock layout (gap found in the 1.2.0 Mac test pass —
            # it was previously saved only in closeEvent).
            try:
                state = bytes(self.saveState().toBase64()).decode('ascii')
                self._app_settings.set('window', 'dock_state', state)
            except Exception:
                pass
            self._app_settings.save()
        except Exception as exc:
            print(f"Geometry save failed: {exc}", file=sys.stderr)

    def closeEvent(self, event):
        # Save window geometry + dock arrangement + flush setting edits
        # (all inside _save_geometry, shared with the SIGINT/SIGTERM path).
        self._save_geometry()
        # Stop recording first (gracefully flush the SigMF file) before
        # tearing down the flowgraph.
        try:
            self._stop_recording()
        except Exception:
            pass
        # Restore the real stderr before GR shuts down so any shutdown logs
        # land on the console rather than a closed pipe.
        try:
            self._overflow_monitor.stop()
        except Exception:
            pass
        self.stop()
        self.wait()
        event.accept()

    def _elided_dir(self):
        d = self.recording_dir
        if len(d) > 32:
            return "..." + d[-29:]
        return d

    def _on_change_recording_dir(self):
        """Choose where recordings are written (File menu + the Recording
        panel's Folder button). Takes effect immediately: all three
        recorders build their path from self.recording_dir at the moment
        recording starts. Refused mid-recording so a run can't be split
        across two folders."""
        if self.record:
            QtWidgets.QMessageBox.information(
                self, "Recording in progress",
                "Stop the recording before changing the folder.")
            return
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose recording folder",
            self.recording_dir,
        )
        if not new_dir:
            return
        try:
            os.makedirs(new_dir, exist_ok=True)
            probe = os.path.join(new_dir, ".dses_write_test")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, "Folder not usable",
                f"Recordings could not be written to:\n{new_dir}\n\n{exc}\n\n"
                f"Keeping the previous folder:\n{self.recording_dir}")
            return
        self.recording_dir = new_dir
        self._save_setting('recording', 'directory', new_dir)
        self._recording_dir_button.setText("Folder: " + self._elided_dir())
        self._recording_dir_button.setToolTip(new_dir)
        self._status_bar.showMessage(f"Recording folder: {new_dir}", 8000)

    def _on_change_device_clicked(self):
        """Re-open the device picker. Switching to a different radio needs
        a full flowgraph rebuild, so for now we just save the choice and
        tell the user it'll take effect next launch."""
        devices = find_all_radios()
        if not devices:
            QtWidgets.QMessageBox.warning(
                self, "No radio found",
                "No supported SDR is currently attached. Plug one in and "
                "try again.")
            return
        dlg = DevicePickerDialog(
            devices, current_driver=self._device_driver,
            current_serial=self._device_serial, parent=self,
            prompt="Pick a radio for the next launch:")
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        new_dev = dlg.selected_device()
        if not new_dev:
            return
        if (new_dev['driver'] == self._device_driver
                and new_dev['serial'] == self._device_serial):
            return
        self._save_setting('rx', 'device_driver', new_dev['driver'])
        self._save_setting('rx', 'device_serial', new_dev['serial'])
        QtWidgets.QMessageBox.information(
            self, "Radio changed",
            f"Will use {new_dev['label']} on next launch.\n\n"
            f"(Hot-swap of the radio source is not supported yet — "
            f"close and re-open the program to apply.)")

    def get_freq_preset(self):
        return self.freq_preset

    def set_freq_preset(self, freq_preset):
        self.freq_preset = freq_preset
        self.set_center_freq((self.freq_manual if self.freq_preset == 0 else self.freq_preset) + self.freq_offset + self.freq_offset_0)
        self._freq_preset_callback(self.freq_preset)
        self._save_setting('tuning', 'preset_hz', float(freq_preset))
        self._on_science_param_changed()

    def get_freq_offset_0(self):
        return self.freq_offset_0

    def set_freq_offset_0(self, freq_offset_0):
        self.freq_offset_0 = freq_offset_0
        self.set_center_freq((self.freq_manual if self.freq_preset == 0 else self.freq_preset) + self.freq_offset + self.freq_offset_0)
        self._save_setting('tuning', 'coarse_hz', float(freq_offset_0))

    def get_freq_offset(self):
        return self.freq_offset

    def set_freq_offset(self, freq_offset):
        self.freq_offset = freq_offset
        self.set_center_freq((self.freq_manual if self.freq_preset == 0 else self.freq_preset) + self.freq_offset + self.freq_offset_0)
        self._save_setting('tuning', 'fine_hz', float(freq_offset))

    def get_lo_offset(self):
        return self.lo_offset

    def set_lo_offset(self, hz):
        """Park the hardware LO `hz` away from the displayed center (the
        DDC brings the band back, so display and recordings are unchanged —
        only the zero-IF DC artefact moves). |hz| > samp_rate/2 pushes the
        artefact out of the recorded band entirely. 0 = classic tuning."""
        hz = float(hz)
        applied = hz
        if self._source is not None and not self._playback_mode:
            if not self._source.set_lo_offset(hz):
                applied = 0.0
                if hz:
                    sb = getattr(self, '_status_bar', None)
                if sb is not None:
                    sb.showMessage(
                        f"LO offset not supported by "
                        f"{self._source.display_label} — classic tuning kept",
                        8000)
        self.lo_offset = applied
        self._save_setting('tuning', 'lo_offset_hz', applied)
        lw = getattr(self, '_lo_offset_win', None)
        if lw is not None and applied != hz:
            lw.set_value(applied)
        self._on_science_param_changed()

    def get_freq_manual(self):
        return self.freq_manual

    def set_freq_manual(self, freq_manual):
        self.freq_manual = freq_manual
        self.set_center_freq((self.freq_manual if self.freq_preset == 0 else self.freq_preset) + self.freq_offset + self.freq_offset_0)
        QtCore.QMetaObject.invokeMethod(self._freq_manual_line_edit, "setText",
                                        QtCore.Q_ARG("QString", eng_notation.num_to_str(self.freq_manual)))
        self._save_setting('tuning', 'manual_hz', float(freq_manual))
        self._on_science_param_changed()

    def get_samp_rate(self):
        return self.samp_rate

    def _samp_rate_limits(self):
        """(min_hz, max_hz) the current SDR accepts; falls back to the combo
        span when there's no live source (e.g. playback)."""
        if self._source is not None:
            try:
                lo, hi = self._source.samp_rate_range()
                if hi > lo > 0:
                    return float(lo), float(hi)
            except Exception:
                pass
        opts = self._samp_rate_options or [self.samp_rate]
        return float(min(opts)), float(max(opts))

    def _refresh_samp_rate_limits_tooltip(self):
        lo, hi = self._samp_rate_limits()
        tip = (f"Any rate the SDR supports: {_pretty_rate(lo)} – "
               f"{_pretty_rate(hi)}.\nOut-of-range values are clamped; the "
               f"radio then snaps to the nearest rate it can deliver.")
        self._samp_rate_manual_line_edit.setToolTip(tip)
        self._samp_rate_manual_tool_bar.setToolTip(tip)

    def _sync_samp_rate_widgets(self):
        """Reflect the true current rate: the manual box always shows it, the
        combo selects a matching preset or clears (-1) for a custom rate.
        Signals are blocked so this never re-enters set_samp_rate."""
        with _SignalBlocker(self._samp_rate_manual_line_edit):
            self._samp_rate_manual_line_edit.setText(
                eng_notation.num_to_str(self.samp_rate))
        idx = -1
        for k, r in enumerate(self._samp_rate_options):
            if abs(r - self.samp_rate) <= max(1.0, r * 1e-6):
                idx = k
                break
        with _SignalBlocker(self._samp_rate_combo_box):
            self._samp_rate_combo_box.setCurrentIndex(idx)

    def _on_samp_rate_combo(self, i):
        if 0 <= i < len(self._samp_rate_options):
            self.set_samp_rate(self._samp_rate_options[i])

    def _on_samp_rate_manual_edit(self):
        txt = str(self._samp_rate_manual_line_edit.text())
        try:
            hz = eng_notation.str_to_num(txt)
        except Exception:
            self._sync_samp_rate_widgets()   # bad input -> revert to current
            return
        self.set_samp_rate(hz)

    def set_samp_rate(self, samp_rate):
        if self._playback_mode or self._source is None:
            return  # rate is fixed by the playback file
        lo, hi = self._samp_rate_limits()
        req = max(lo, min(hi, float(samp_rate)))
        self._source.set_samp_rate(req)
        # Trust the rate the radio actually settled on (drivers snap), so the
        # display, decimation, and .fil tsamp all reflect reality.
        actual = self._source.get_actual_samp_rate()
        self.samp_rate = float(actual) if actual and actual > 0 else req
        # If the driver snapped to a different rate than asked for, say so
        # right at the entry box — otherwise the quiet substitution reads as
        # "it took my value" and the recorded tsamp surprises the user later.
        if actual and abs(actual - float(samp_rate)) > 1.0:
            QtWidgets.QToolTip.showText(
                self._samp_rate_manual_line_edit.mapToGlobal(
                    QtCore.QPoint(0, -40)),
                f"Radio can't do {_pretty_rate(float(samp_rate))} exactly — "
                f"running at {_pretty_rate(actual)} instead.",
                self._samp_rate_manual_line_edit)
        self._sync_samp_rate_widgets()
        self._fft_plot.set_frequency_range(self.center_freq, self.samp_rate)
        self._waterfall_plot.set_frequency_range(self.center_freq, self.samp_rate)
        self._processor.reset_averaging()   # RBW/axis changed — start a fresh average
        self._keep_one_in_n.set_n(self._decim_for(self.samp_rate))
        self._save_setting('rx', 'samp_rate_hz', float(self.samp_rate))
        # Stale overflow indicators from the old rate aren't meaningful any
        # more, and there's usually a small burst during retuning.
        self._overflow_widget.clear()
        self._on_science_param_changed()

    def get_record(self):
        return self.record

    def set_record(self, record):
        # Intentionally NOT persisted — always launch with recording stopped.
        new = int(record)
        if new == self.record:
            return
        self.record = new
        self._record_callback(self.record)
        if self.record:
            self._start_recording()
        else:
            self._stop_recording()

    def _update_fil_geom_enabled(self):
        """Format-specific rows only apply to their format, and must not
        change mid-recording."""
        self._fil_geom_widget.setEnabled(
            (self._record_format == 'fil') and not self.record)
        # The manual-fold override only feeds the .fil PRESTO pipeline.
        self._fold_manual_widget.setEnabled(
            (self._record_format == 'fil') and not self.record)
        self._ezra_point_widget.setEnabled(
            (self._record_format == 'ezra') and not self.record)
        # Observation presets are a bundle of the same locked settings.
        combo = getattr(self, '_observation_combo', None)
        if combo is not None:
            combo.setEnabled(not self.record)

    def _on_ez_az_changed(self, v):
        self._ez_az_deg = float(v)
        self._save_setting('recording', 'ez_az_deg', float(v))

    def _on_ez_el_changed(self, v):
        self._ez_el_deg = float(v)
        self._save_setting('recording', 'ez_el_deg', float(v))

    def get_record_format(self):
        return self._record_format

    def set_record_format(self, fmt):
        """Switch between raw-I/Q (SigMF), live-filterbank (.fil), and
        drift-scan (ezRA .txt) recording. Disallowed mid-recording — stop
        first."""
        fmt = str(fmt).lower()
        if fmt not in ('iq', 'fil', 'ezra'):
            fmt = 'iq'
        if fmt == self._record_format:
            return
        if self.record:
            # Revert the combo to the active format; can't switch while live.
            QtWidgets.QMessageBox.information(
                self, "Stop recording first",
                "Stop the current recording before changing the format.")
            with _SignalBlocker(self._record_format_combo):
                self._record_format_combo.setCurrentIndex(
                    self._record_format_options.index(self._record_format))
            return
        self._record_format = fmt
        with _SignalBlocker(self._record_format_combo):
            self._record_format_combo.setCurrentIndex(
                self._record_format_options.index(fmt))
        self._save_setting('recording', 'format', fmt)
        self._update_fil_geom_enabled()
        self._on_science_param_changed()

    def set_fil_nchans(self, n):
        self._fil_nchans = max(2, int(n))
        self._save_setting('recording', 'fil_nchans', self._fil_nchans)
        self._on_science_param_changed()

    def set_fil_integrate(self, n):
        self._fil_integrate = max(1, int(n))
        self._save_setting('recording', 'fil_integrate', self._fil_integrate)
        self._on_science_param_changed()

    # --- Observation presets -------------------------------------------------

    def _resolve_observation_cfg(self, cfg):
        """Adapt a preset bundle to the ACTIVE radio. Today one adaptation
        exists: a preset carrying `alt_no_shift` (the HI drift scan) swaps to
        its display-domain-offset variant when the radio has no LO shift
        stage and can reach the variant's rate. Returns (cfg, note) — note is
        a status-bar sentence when an adaptation was applied, else None."""
        alt = cfg.get('alt_no_shift') if cfg else None
        if not alt:
            return cfg, None
        src = getattr(self, '_source', None)
        if src is None or self._playback_mode or src.lo_offset_supported():
            return cfg, None
        try:
            _, rmax = src.samp_rate_range()
        except Exception:
            rmax = 0.0
        if rmax < float(alt.get('min_rate', alt['rate'])):
            return cfg, None            # RTL-SDR class: line-centered stands
        rate = min(float(alt['rate']), float(rmax))
        out = dict(cfg, manual=float(alt['manual']), rate=rate, lo_off=0.0)
        note = (f"HI preset adapted for {src.display_label}: no LO-offset "
                f"stage, so center {alt['manual']/1e6:.3f} MHz at "
                f"{rate/1e6:g} MS/s keeps the line in band with the DC "
                f"artefact {abs(alt['manual']-1420.405751786e6)/1e6:.3f} MHz "
                f"off it")
        return out, note

    def _apply_observation(self, idx):
        """Apply preset `idx` from OBSERVATION_PRESETS through the same
        setters the individual controls use. Guarded so the setters'
        deviation hooks don't knock the combo back to Manual mid-apply."""
        _, cfg = OBSERVATION_PRESETS[idx]
        if cfg is None:
            return                      # Manual (expert): touch nothing
        cfg, note = self._resolve_observation_cfg(cfg)
        if note:
            sb = getattr(self, '_status_bar', None)
            if sb is not None:
                sb.showMessage(note, 15000)
        if self.record:
            # Science settings are locked while recording (same rule as the
            # geometry spins) — refuse and show why.
            with _SignalBlocker(self._observation_combo):
                self._observation_combo.setCurrentIndex(OBS_MANUAL_IDX)
            QtWidgets.QToolTip.showText(
                self._observation_combo.mapToGlobal(QtCore.QPoint(0, -40)),
                "Stop the recording first — observation settings are locked "
                "while recording.", self._observation_combo)
            return
        self._obs_applying = True
        try:
            mode = cfg.get('mode', 'live')
            if mode == 'sweep':
                self._mode_sweep_btn.setChecked(True)
                return                  # sweep has its own range settings
            self._mode_live_btn.setChecked(True)
            if 'band' in cfg:
                # A preset must produce EXACTLY its advertised center. Stale
                # Coarse/Fine Tune from earlier manual work would silently
                # shift it (live-diagnosed 2026-08-24: the Pi's old campaign
                # left -1.6 MHz here, so the HI preset landed at 1418.8 and
                # every ezRA header in segment 2 is mislabeled).
                self.set_freq_offset_0(0.0)
                self.set_freq_offset(0.0)
                self._freq_offset_0_win.set_value(0.0)
                self._freq_offset_win.set_value(0.0)
                if cfg['band'] == 0:
                    self.set_freq_manual(float(cfg['manual']))
                    self.set_freq_preset(0)
                else:
                    self.set_freq_preset(float(cfg['band']))
            if 'rate' in cfg:
                self.set_samp_rate(float(cfg['rate']))
            if 'fmt' in cfg:
                self.set_record_format(cfg['fmt'])
            # Drive the spins (not the setters) so the widgets show the new
            # geometry; valueChanged forwards to the setters.
            if 'nchans' in cfg:
                self._fil_nchans_spin.setValue(int(cfg['nchans']))
            if 'integrate' in cfg:
                self._fil_integrate_spin.setValue(int(cfg['integrate']))
            if 'lo_off' in cfg:
                self.set_lo_offset(float(cfg['lo_off']))
                self._lo_offset_win.set_value(self.lo_offset)
        finally:
            self._obs_applying = False
        self._update_consequences()

    def _on_science_param_changed(self):
        """Called by every science-critical setter (rate, format, geometry,
        band, mode): refresh the consequences readout, and if an observation
        preset was active, drop the combo back to Manual — the label must
        never claim a bundle the settings no longer match."""
        combo = getattr(self, '_observation_combo', None)
        if combo is None:
            return
        if (not getattr(self, '_obs_applying', False)
                and combo.currentIndex() != OBS_MANUAL_IDX):
            with _SignalBlocker(combo):
                combo.setCurrentIndex(OBS_MANUAL_IDX)
        self._update_consequences()

    def _hi_band_gap(self):
        """When the tuning sits NEAR the hydrogen line (within 25 MHz) but
        the line itself falls OUTSIDE the recorded span, return that span as
        (lo_mhz, hi_mhz); otherwise None. The 25 MHz gate keeps OH/continuum
        drift scans quiet. Shared by the consequences line and the ezRA
        record-start guard."""
        rate = float(self.samp_rate) or 1.0
        hi_off = abs(self.center_freq - 1420.405751786e6)
        if hi_off < 25e6 and hi_off > rate / 2:
            return ((self.center_freq - rate / 2) / 1e6,
                    (self.center_freq + rate / 2) / 1e6)
        return None

    def _update_consequences(self):
        """One live line translating the current science settings into what
        an observer actually cares about. Amber = risky combination."""
        lbl = getattr(self, '_consequences_label', None)
        if lbl is None:
            return
        rate = float(self.samp_rate) or 1.0
        fmt = self._record_format
        warn = None
        if fmt == 'fil':
            ch, integ = self._fil_nchans, self._fil_integrate
            tsamp = ch * integ / rate
            dnu = rate / ch
            f_ghz = max(0.05, abs(self.center_freq) / 1e9)
            # Intra-channel dispersion smearing at a reference DM of 30
            # (mid-range for bright northern pulsars): 8.3 µs × DM ×
            # Δν_MHz / ν_GHz³.
            smear_ms = 8.3e-3 * 30.0 * (dnu / 1e6) / f_ghz ** 3
            gb_hr = 4.0 * rate / integ * 3600.0 / 1e9
            ts_txt = (f"{tsamp*1e6:.1f} µs" if tsamp < 1e-3
                      else f"{tsamp*1e3:.2f} ms")
            lbl.setText(f"tsamp {ts_txt}  ·  chan {_pretty_rate(dnu)}  ·  "
                        f"DM30 smear {smear_ms:.3g} ms  ·  {gb_hr:.1f} GB/hr")
            if rate > 20e6:
                warn = "rate beyond the validated .fil recording geometry"
            elif tsamp > 5e-3:
                warn = "time resolution coarse for pulsar work"
        elif fmt == 'iq':
            gb_hr = 8.0 * rate * 3600.0 / 1e9
            lbl.setText(f"raw I/Q at {_pretty_rate(rate)}  ·  "
                        f"{gb_hr:.0f} GB/hr")
            if gb_hr > 500:
                warn = "very high disk rate"
        else:   # ezra drift scan
            off = float(getattr(self, 'lo_offset', 0.0))
            hi_sep = abs(self.center_freq - 1420.405751786e6)
            if abs(off) > rate / 2:
                dc_txt = "DC artefact out of band"
            elif off:
                dc_txt = f"DC artefact {abs(off)/1e3:.0f} kHz off center"
            elif hi_sep < 0.5e6:
                # Offset-0 with the tuned center on (or within the galactic
                # gas band of) the line: the artefact sits in the science.
                dc_txt = "DC artefact ON the tuned center"
                warn = ("receiver DC artefact sits on the target — set an "
                        "LO offset beyond half the sample rate, or offset "
                        "the tuned center itself (wide-band arrangement)")
            else:
                # Display-domain offset (the Ray configuration): center off
                # the line, artefact at center = harmlessly out of the gas.
                dc_txt = (f"DC artefact on the tuned center, "
                          f"{hi_sep/1e6:.2f} MHz from the HI line")
            # Hydrogen-in-band check: tuned near the line but with the line
            # outside the recorded span = an HI drift scan that cannot see
            # hydrogen (Ray's 2026-09-12 run: center 1422.0 at 2 MS/s left
            # the whole band 0.8 MHz above the gas). Only fires within
            # ±25 MHz of the line so OH / continuum drift scans stay quiet.
            gap = self._hi_band_gap()
            hi_txt = ""
            if gap is not None:
                hi_txt = "  ·  HI line OUT OF the recorded band"
                warn = (f"the hydrogen line (1420.406 MHz) is outside the "
                        f"recorded band ({gap[0]:.3f}–{gap[1]:.3f} MHz) — "
                        f"tune the center to 1420.406, or use the Hydrogen "
                        f"line Observation preset")
                # The Observation dock (this label's home) may be closed —
                # echo the transition to the always-visible status bar too
                # (found the hard way: the 1.5.1 warning fired invisibly
                # in a layout with that panel hidden, Rick 2026-09-13).
                sb = getattr(self, '_status_bar', None)
                if sb is not None and not getattr(self, '_hi_oob_shown', False):
                    sb.showMessage(
                        "Drift-scan warning: the hydrogen line (1420.406 MHz) "
                        f"is OUTSIDE the recorded band ({gap[0]:.3f}–"
                        f"{gap[1]:.3f} MHz)", 15000)
            self._hi_oob_shown = gap is not None
            lbl.setText("integrated spectra, one row per ~10–15 s  ·  "
                        f"~1 MB/hr  ·  {dc_txt}{hi_txt}")
        lbl.setStyleSheet("color: #b45309;" if warn else "color: gray;")
        lbl.setToolTip(lbl.toolTip().split('\n\nWarning:')[0]
                       + (f"\n\nWarning: {warn}." if warn else ""))

    def _start_recording(self, scheduled=False):
        """Begin recording in the selected format. Filterbank (.fil) channelizes
        live and writes a SIGPROC file directly; raw I/Q goes to a SigMF pair.

        With a "Start at" time set this ARMS instead: the questions that can
        stop a recording (source below the mask, hydrogen line out of band)
        are asked now, while someone is at the keyboard, and the wait timer
        calls back with scheduled=True at the instant — that call must never
        ask anything."""
        if self._playback_mode or self.uhd_usrp_source_0 is None:
            return  # nothing real to record from
        start_ts = None
        if not scheduled:
            try:
                start_ts = self._parse_start_ts(self._rec_start_text,
                                                self._rec_start_tz != "Local")
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(
                    self, "Start time not understood",
                    f"{exc}\n\nUse HH:MM (the next time it comes round), "
                    "+minutes or +H:MM (a delay), or YYYY-MM-DD HH:MM. "
                    "Leave it blank to start at once.")
                self.record = 0
                self._record_callback(0)
                return
            if not self._check_source_visibility(start_ts=start_ts):
                self.record = 0
                self._record_callback(0)
                return
        # ezRA drift scans near-but-off the hydrogen line get a hard stop
        # at record START — the consequences-line warning lives in the
        # Observation dock, which may be closed (how a hydrogen-free "HI"
        # run got recorded on 2026-09-12/13 despite the 1.5.1 warning).
        if self._record_format == 'ezra' and not scheduled:
            gap = self._hi_band_gap()
            if gap is not None:
                resp = QtWidgets.QMessageBox.warning(
                    self, "Hydrogen line not in band",
                    "The hydrogen line (1420.406 MHz) is OUTSIDE the "
                    f"recorded band ({gap[0]:.3f}–{gap[1]:.3f} MHz).\n\n"
                    "A drift scan recorded here cannot contain HI. Tune the "
                    "center to 1420.406 MHz, or pick the Hydrogen line "
                    "Observation preset.\n\nRecord anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if resp != QtWidgets.QMessageBox.Yes:
                    self.record = 0
                    self._record_callback(0)
                    return
        if start_ts is not None and start_ts - time.time() > 1.0:
            self._arm_scheduled_start(start_ts)   # wait; _tick_armed starts it
            return
        self._update_fil_geom_enabled()  # lock geometry while live
        if self._record_format == 'fil':
            self._start_fil_recording()
        elif self._record_format == 'ezra':
            self._start_ezra_recording()
        else:
            self._start_sigmf_recording()

    def _start_ezra_recording(self):
        """Splice an EzraTxtSink into the running flowgraph: integrated
        spectra stream straight into an ezRA .txt drift-scan file that the
        ezRA suite (ezCon -> ezPlot/ezSky) consumes directly."""
        if self._ezra_sink is not None:
            return  # already recording
        st = self._app_settings
        # ezCol naming incl. the same-hour rerun letter (shared helper —
        # the sink reuses it when it rolls to a new file at UTC midnight).
        path = str(ezra_txt.ezra_unique_path(self.recording_dir,
                                             self._ez_prefix))
        try:
            sink = ezra_txt.EzraTxtSink(
                path, fft_bins=self._ez_fft_bins,
                integ_frames=self._ez_integ_frames,
                samp_rate=self.samp_rate,
                center_freq_mhz=self.center_freq / 1e6,
                lat_deg=st.get_float('site', 'lat_deg'),
                lon_deg=st.get_float('site', 'lon_deg'),
                amsl=st.get_float('site', 'amsl'),
                site_name=(st.get_str('site', 'name').strip() or 'DSES'),
                az_deg=self._ez_az_deg, el_deg=self._ez_el_deg,
                gain_text=f"{self.gain:g}",
                keep_fraction=self._ez_keep_fraction,
                provenance=f"DSES_Workbench {APP_VERSION}",
                # ezCol convention for multi-day drift scans: a new file per
                # UTC day, rolled automatically at midnight.
                roll_daily=True, roll_prefix=self._ez_prefix)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not create ezRA drift-scan writer:\n\n{exc}")
            self.record = 0
            self._record_callback(0)
            self._update_fil_geom_enabled()
            return
        try:
            self.lock()
            try:
                self.connect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not splice ezRA sink into flowgraph:\n\n{exc}")
            try:
                sink.close()
            except Exception:
                pass
            self.record = 0
            self._record_callback(0)
            self._update_fil_geom_enabled()
            return
        self._ezra_sink = sink
        self._ezra_sink_path = path
        row_s = self._ez_fft_bins * self._ez_integ_frames / self.samp_rate
        self._recording_status.setText(
            f"Recording → {os.path.basename(path)} "
            f"({sink._integ.kept_bins} bins, {row_s:.1f} s/row)")
        self._recording_status.setToolTip(path)
        self._on_recording_started()

    def _stop_ezra_recording(self):
        """Disconnect the EzraTxtSink and close it so the .txt is flushed."""
        sink = self._ezra_sink
        if sink is None:
            return
        self._ezra_sink = None
        try:
            self.lock()
            try:
                self.disconnect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            print(f"Recording disconnect failed: {exc}", file=sys.stderr)
        info = None
        try:
            info = sink.close()
        except Exception as exc:
            print(f"ezRA close failed: {exc}", file=sys.stderr)
        self._on_recording_stopped()
        # After a midnight rollover the session spans several files — report
        # the LAST file plus the day count, and list them all in the tooltip.
        path = (info or {}).get('path') or getattr(self, '_ezra_sink_path', '')
        if path:
            rows = (info or {}).get('nrows', 0)
            nfiles = (info or {}).get('files', 1)
            multi = f" in {nfiles} daily files" if nfiles > 1 else ""
            self._recording_status.setText(
                f"Saved → {os.path.basename(path)} ({rows} rows{multi})")
            self._recording_status.setToolTip(
                "\n".join((info or {}).get('paths', [path])))
        else:
            self._recording_status.setText("Idle")
            self._recording_status.setToolTip("")

    def _start_fil_recording(self):
        """Splice a live FilterbankSink into the running flowgraph: it
        channelizes the SDR stream and writes a SIGPROC .fil (telescope_id 12)
        straight to disk, using the shared sigproc_fil core."""
        if self._fil_sink is not None:
            return  # already recording
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.recording_dir,
                            self._recording_basename(ts) + ".fil")
        _raj, _dej = 0.0, 0.0
        if (getattr(self, '_catalog_radec', None)
                and getattr(self, '_catalog_radec_for', None)
                == self._source_name.strip()):
            _raj, _dej = self._catalog_radec
        try:
            sink = sigproc_fil.FilterbankSink(
                path, nchans=self._fil_nchans, samp_rate=self.samp_rate,
                center_freq_mhz=self.center_freq / 1e6,
                tstart_mjd=sigproc_fil.unix_to_mjd(time.time()),
                integrate=self._fil_integrate,
                source_name=(self._source_name.strip() or "capture"),
                # Exact catalog position when the source came from the
                # visibility planner; otherwise (0,0) and sigproc_fil falls
                # back to parsing the position out of the name.
                src_raj=_raj, src_dej=_dej)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not create filterbank writer:\n\n{exc}")
            self.record = 0
            self._record_callback(0)
            self._update_fil_geom_enabled()
            return
        try:
            self.lock()
            try:
                self.connect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not splice filterbank sink into flowgraph:\n\n{exc}")
            try:
                sink.close()
            except Exception:
                pass
            self.record = 0
            self._record_callback(0)
            self._update_fil_geom_enabled()
            return
        self._fil_sink = sink
        self._fil_sink_path = path
        self._fil_rec_source = self._source_name.strip()  # for auto-analysis
        self._quicklook_btn.setEnabled(True)
        tsamp_ms = self._fil_nchans * self._fil_integrate / self.samp_rate * 1e3
        self._recording_status.setText(
            f"Recording → {os.path.basename(path)} "
            f"({self._fil_nchans} ch, tsamp {tsamp_ms:.4g} ms)")
        self._recording_status.setToolTip(path)
        self._on_recording_started()

    def _start_sigmf_recording(self):
        """Construct a fresh SigMF sink with a timestamped filename and
        splice it into the running flowgraph via top_block.lock()/unlock()."""
        if self._playback_mode or self.uhd_usrp_source_0 is None:
            return  # nothing real to record from
        if self._sigmf_sink is not None:
            return  # already recording
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.recording_dir, self._recording_basename(ts))
        _src = self._source_name.strip()
        try:
            assert self._source is not None  # guarded by _playback_mode check above
            sink = blocks.sigmf_sink_minimal(
                item_size=gr.sizeof_gr_complex,
                filename=base,
                sample_rate=self.samp_rate,
                center_freq=self.center_freq,
                author=APP_AUTHOR,
                description=(f"Spectrum analyzer capture ({self._source.display_label})"
                            + (f" — source {_src}" if _src else "")),
                hw_info=self._source.hw_info,
                is_complex=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not create SigMF sink:\n\n{exc}")
            # Roll the combo back to Stopped so the UI stays truthful.
            self.record = 0
            self._record_callback(0)
            return
        try:
            self.lock()
            try:
                self.connect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Recording failed to start",
                f"Could not splice SigMF sink into flowgraph:\n\n{exc}")
            self.record = 0
            self._record_callback(0)
            return
        self._sigmf_sink = sink
        self._sigmf_sink_path = base
        self._recording_status.setText(
            f"Recording → {os.path.basename(base)}.sigmf-data")
        self._recording_status.setToolTip(f"{base}.sigmf-data")
        self._on_recording_started()

    # --- Canned PRESTO analysis (quick look + analyze-when-done) ----------

    class _AnalysisSignals(QObject):
        progress = Signal(str)
        done = Signal(dict)
        failed = Signal(str)

    def _start_analysis(self, fil_path, source_name, quick,
                        fold_p_s=None, fold_dm=None):
        """Run fold_analysis.analyze_fil on a worker thread; results surface
        via signals on the GUI thread. One analysis at a time. A manual
        fold_p_s (seconds) forces a full -topo -p fold for a known-period
        source that has no catalog entry (e.g. the lab pulsar simulator)."""
        if getattr(self, '_analysis_thread', None) is not None \
                and self._analysis_thread.is_alive():
            QtWidgets.QMessageBox.information(
                self, "Analysis running",
                "An analysis is already in progress — wait for it to finish.")
            return
        sig = self._AnalysisSignals()
        sig.progress.connect(self._on_analysis_progress)
        sig.done.connect(self._on_analysis_done)
        sig.failed.connect(self._on_analysis_failed)
        self._analysis_signals = sig    # keep a ref while the thread runs
        self._analysis_quick = quick

        def _work():
            try:
                import fold_analysis
                res = fold_analysis.analyze_fil(
                    fil_path, source_name=source_name, quick=quick,
                    fold_p_s=fold_p_s, fold_dm=fold_dm,
                    progress=sig.progress.emit)
                sig.done.emit(res)
            except Exception as exc:
                sig.failed.emit(str(exc))

        self._analysis_thread = threading.Thread(
            target=_work, name="fold-analysis", daemon=True)
        self._analysis_thread.start()
        self._on_analysis_progress("starting…")

    def _on_analysis_progress(self, msg):
        kind = "Quick look" if self._analysis_quick else "Analysis"
        self._recording_status.setText(f"{kind}: {msg}")

    def _on_analysis_failed(self, msg):
        self._recording_status.setText("Analysis failed")
        QtWidgets.QMessageBox.warning(
            self, "Analysis failed",
            f"The PRESTO pipeline did not complete:\n\n{msg}")

    def _on_analysis_done(self, res):
        verdict = res.get('verdict', '?')
        self._recording_status.setText(f"Analysis: {verdict}")
        self._recording_status.setToolTip(res.get('pdf', ''))
        lines = [f"Verdict: {verdict}", ""]
        if res.get('chi2_red') is not None:
            lines.append(f"Reduced chi-squared: {res['chi2_red']:.2f}")
        if res.get('best_dm') is not None:
            cat = res.get('catalog_dm')
            lines.append(f"Best DM: {res['best_dm']:.2f}"
                         + (f"  (catalog {cat:.2f})" if cat else ""))
        if res.get('best_p_s') is not None:
            lines.append(f"Best period: {res['best_p_s']*1e3:.4f} ms")
        lines += ["", res.get('verdict_text', ''), "",
                  f"Self-contained PDF:\n{res.get('pdf', '')}"]
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Quick look" if self._analysis_quick
                           else "Recording analysis")
        box.setText("\n".join(lines))
        open_btn = box.addButton("Open PDF", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Close)
        box.exec()
        if box.clickedButton() is open_btn and res.get('pdf'):
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(res['pdf']))

    def _on_quicklook_clicked(self):
        """Snapshot the growing .fil and analyze it without touching the
        recording."""
        if self._fil_sink is None:
            return
        import fold_analysis
        path = self._fil_sink_path
        try:
            hdr_rows = self._fil_sink.nrows
            tsamp = self._fil_nchans * self._fil_integrate / self.samp_rate
            if hdr_rows * tsamp < fold_analysis.QUICKLOOK_MIN_SECONDS:
                QtWidgets.QMessageBox.information(
                    self, "Not enough data yet",
                    f"Only {hdr_rows * tsamp:.0f} s recorded — the quick look "
                    f"needs at least "
                    f"{fold_analysis.QUICKLOOK_MIN_SECONDS:.0f} s (and a fold "
                    f"only means much after several minutes).")
                return
            snap = os.path.splitext(path)[0] + "_snapshot.fil"
            fold_analysis.snapshot_fil(path, snap)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Quick look failed",
                f"Could not snapshot the recording:\n\n{exc}")
            return
        _p, _dm = self._manual_fold_params()
        self._start_analysis(snap, self._source_name.strip(), quick=True,
                             fold_p_s=_p, fold_dm=_dm)

    # --- B210 built-in self test (Observe menu) ---------------------------
    #
    # The dialog (B210SelfTestDialog) is the UI; these methods do the
    # flowgraph surgery: save the user's radio state, retune to the bench-
    # proven test geometry, splice a TX branch (vector_source -> usrp_sink
    # on the SAME B210, minimum gain) plus a private FilterbankSink, record,
    # tear down, restore, then fold with PRESTO and grade against the
    # injected ground truth. Bench-proven 2026-08-05: internal TX->RX
    # leakage suffices — no cable.

    def _show_driftscan_review(self):
        """Observe menu: the drift-scan quicklook (no radio needed)."""
        import driftscan_review
        # Keep the reference on self so the dialog survives exec() scope.
        self._driftscan_review_dialog = driftscan_review.make_dialog(
            parent=self,
            site=self._site_dict(),
            current_azel=(self._ez_az_deg, self._ez_el_deg),
            start_dir=self.recording_dir)
        self._driftscan_review_dialog.exec()

    def _show_selftest_dialog(self):
        reason = self._selftest_unavailable_reason()
        if reason:
            QtWidgets.QMessageBox.information(self, "B210 Self Test", reason)
            return
        # Keep the reference on self: the PRESTO fold outlives exec() when
        # the user closes the dialog mid-analysis, and the worker's queued
        # signals must land on a live QObject, not a collected one.
        self._selftest_dialog = B210SelfTestDialog(self)
        self._selftest_dialog.exec()

    def _selftest_unavailable_reason(self):
        """None when the self test can run now, else a human-readable why."""
        if self._playback_mode:
            return ("The self test needs a live radio — the app is in "
                    "playback mode.")
        if self._device_driver != DRIVER_UHD_B200:
            return ("The self test uses the radio's own transmitter and "
                    "currently supports the USRP B200/B210 only.")
        if self._source is None or self.uhd_usrp_source_0 is None:
            return "No radio is running."
        if self._sweep_active:
            return "Switch to Live mode first — Sweep retunes continuously."
        if self.record or self._fil_sink is not None \
                or self._sigmf_sink is not None or self._ezra_sink is not None:
            return "Stop the recording first."
        if getattr(self, '_analysis_thread', None) is not None \
                and self._analysis_thread.is_alive():
            return "An analysis is already in progress — wait for it to finish."
        if getattr(self, '_selftest_active', False):
            return "A self test is already running."
        return None

    def _selftest_begin(self, dialog, params=None):
        """Configure the radio and splice the TX + recorder chains. Returns
        None on success, else an error string (nothing left spliced).
        `params` (from the dialog's advanced modes) overrides the bench
        defaults: freq/rate/period/dm/duty/amp/rx_gain/nchans/n_periods/
        source_name."""
        reason = self._selftest_unavailable_reason()
        if reason:
            return reason
        import pulsar_sim
        if params is None:
            params = {
                "freq": SELFTEST_FREQ_HZ, "rate": SELFTEST_RATE_HZ,
                "period": SELFTEST_PERIOD_S, "dm": SELFTEST_DM,
                "duty": SELFTEST_DUTY, "amp": SELFTEST_AMP,
                "rx_gain": SELFTEST_RX_GAIN_DB, "nchans": SELFTEST_NCHANS,
                "n_periods": 10, "source_name": SELFTEST_SOURCE_NAME,
            }

        # Save the user's radio state (tuning model, not just the computed
        # center, so the preset/manual/offset widgets restore faithfully).
        self._selftest_saved = {
            'freq_preset': self.freq_preset,
            'freq_manual': self.freq_manual,
            'freq_offset': self.freq_offset,
            'freq_offset_0': self.freq_offset_0,
            'lo_offset': float(self.lo_offset),
            'samp_rate': float(self.samp_rate),
            'gain': float(self.gain),
            'antenna': self._source.current_antenna,
        }
        self._selftest_active = True
        dialog.on_progress("Configuring the radio for the test…")
        QtWidgets.QApplication.processEvents()
        tx = vec = sink = None
        try:
            # Test geometry via the app's own setters so every widget and
            # saved setting stays consistent.
            self.set_freq_offset_0(0.0)
            self.set_freq_offset(0.0)
            # Classic tuning for the test: the simulator's grading was
            # validated with the DC spike at band center, and the TX->RX
            # leakage path assumes the RX LO sits on the TX frequency.
            self.set_lo_offset(0.0)
            self._lo_offset_win.set_value(0.0)
            self.set_freq_manual(params["freq"])
            self.set_freq_preset(0)
            self.set_samp_rate(params["rate"])
            self.set_gain(params["rx_gain"])
            self._gain_win.set_value(params["rx_gain"])
            # RX must sit on an RX2 port — TX owns the shared TX/RX-A port.
            ant = next((a for a in self._source.antennas
                        if a.endswith("RX2")), None)
            if ant and ant != self._source.current_antenna:
                self.set_antenna(ant)
                self._sync_antenna_combo()

            iq, spec = pulsar_sim.synth(
                float(self.samp_rate), float(self.center_freq),
                params["period"], params["dm"], duty=params["duty"],
                n_periods=params["n_periods"], amplitude=params["amp"],
                noise_floor=0.0, seed=2026)
            self._selftest_spec = spec

            tx = uhd.usrp_sink(
                f"serial={self._device_serial}",
                uhd.stream_args(cpu_format="fc32", channels=[0]),
            )
            tx.set_samp_rate(float(self.samp_rate))
            tx.set_center_freq(float(self.center_freq), 0)
            tx.set_antenna("TX/RX", 0)
            tx.set_gain(0.0, 0)     # MINIMUM — the roadmap protected-band rule

            outdir = os.path.join(self.recording_dir, "self_test")
            os.makedirs(outdir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(outdir, f"{params['source_name']}_{ts}.fil")
            sink = sigproc_fil.FilterbankSink(
                path, nchans=params["nchans"],
                samp_rate=float(self.samp_rate),
                center_freq_mhz=float(self.center_freq) / 1e6,
                tstart_mjd=sigproc_fil.unix_to_mjd(time.time()),
                source_name=params["source_name"])
            vec = blocks.vector_source_c(iq, True)
            # Deep buffer on the TX edge: at >=4 MS/s the sink starves on
            # ordinary buffers whenever the GUI/GIL pauses, and TX underflows
            # stretch the pulse spacing — the first live 4 MS/s run FAILED
            # its own period check from exactly that (2026-08-05).
            vec.set_min_output_buffer(1 << 22)

            self.lock()
            try:
                self.connect(vec, tx)
                self.connect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
            # Same host-stall hygiene a real recording gets (GC confinement
            # + 1 ms Windows timer); idempotent and torn down with the test.
            self._begin_realtime_mode()
        except Exception as exc:
            self._selftest_active = False
            if sink is not None:
                try:
                    sink.close()
                except Exception:
                    pass
            self._selftest_restore_settings()
            return f"Could not start the self test:\n\n{exc}"

        self._selftest_tx = tx
        self._selftest_vec = vec
        self._selftest_sink = sink
        self._selftest_path = path
        self._selftest_source_name = params["source_name"]
        self._selftest_info = None
        return None

    def _selftest_gap_events(self):
        sink = getattr(self, '_selftest_sink', None)
        return sink.gap_events if sink is not None else 0

    def _selftest_capture_info(self):
        return getattr(self, '_selftest_info', None)

    def _selftest_teardown(self):
        """Unsplice TX + recorder, close the .fil, release the TX handle,
        restore the user's radio state. Safe to call twice."""
        sink = getattr(self, '_selftest_sink', None)
        tx = getattr(self, '_selftest_tx', None)
        vec = getattr(self, '_selftest_vec', None)
        if sink is not None or tx is not None:
            try:
                self.lock()
                try:
                    if vec is not None and tx is not None:
                        self.disconnect(vec, tx)
                    if sink is not None:
                        self.disconnect((self.uhd_usrp_source_0, 0), (sink, 0))
                finally:
                    self.unlock()
            except Exception as exc:
                print(f"Self test disconnect failed: {exc}", file=sys.stderr)
        if sink is not None:
            try:
                self._selftest_info = sink.close()
            except Exception as exc:
                print(f"Self test sink close failed: {exc}", file=sys.stderr)
        self._selftest_sink = None
        self._selftest_tx = None
        self._selftest_vec = None
        self._end_realtime_mode()
        import gc
        gc.collect()        # release the TX streamer/device handle promptly
        self._selftest_restore_settings()

    def _selftest_restore_settings(self):
        st = getattr(self, '_selftest_saved', None)
        if not st:
            return
        self._selftest_saved = None
        try:
            self.set_freq_manual(st['freq_manual'])
            self.set_freq_offset_0(st['freq_offset_0'])
            self.set_freq_offset(st['freq_offset'])
            self.set_freq_preset(st['freq_preset'])
            self.set_lo_offset(st.get('lo_offset', 0.0))
            self._lo_offset_win.set_value(self.lo_offset)
            self.set_samp_rate(st['samp_rate'])
            self.set_gain(st['gain'])
            self._gain_win.set_value(st['gain'])
            if st['antenna'] and st['antenna'] != self._source.current_antenna:
                self.set_antenna(st['antenna'])
                self._sync_antenna_combo()
        except Exception as exc:
            print(f"Self test settings restore failed: {exc}", file=sys.stderr)

    def _selftest_abort(self):
        self._selftest_teardown()
        self._selftest_active = False
        self._recording_status.setText(
            "Self test aborted — settings restored")

    def _selftest_finish_capture(self, dialog):
        """Capture over: tear down, restore the radio, then fold on a worker
        thread and grade against the injected truth."""
        self._selftest_teardown()
        spec = self._selftest_spec
        path = self._selftest_path
        srcname = getattr(self, '_selftest_source_name',
                          SELFTEST_SOURCE_NAME)

        sig = self._AnalysisSignals()
        sig.progress.connect(dialog.on_progress)
        sig.done.connect(lambda res: self._selftest_grade(dialog, res))
        sig.failed.connect(lambda msg: self._selftest_fail(dialog, msg))
        self._selftest_sig = sig    # keep a ref while the thread runs

        def _work():
            try:
                import fold_analysis
                res = fold_analysis.analyze_fil(
                    path, source_name=srcname,
                    fold_p_s=spec.period_s, fold_dm=spec.dm,
                    progress=sig.progress.emit)
                sig.done.emit(res)
            except Exception as exc:
                sig.failed.emit(str(exc))

        # Claims the shared analysis slot so quick-look/auto-analysis wait.
        self._analysis_thread = threading.Thread(
            target=_work, name="selftest-analysis", daemon=True)
        self._analysis_thread.start()

    def _selftest_fail(self, dialog, msg):
        self._selftest_active = False
        dialog.on_failed(
            f"The capture completed (kept at\n{self._selftest_path})\n"
            f"but the PRESTO fold did not:\n\n{msg}")

    def _selftest_grade(self, dialog, res):
        import pulsar_sim
        self._selftest_active = False
        ok, checks = pulsar_sim.grade(self._selftest_spec, res)
        checks = ["Injected: " + self._selftest_spec.describe(), ""] + checks
        pdf = res.get('pdf', '')
        self._recording_status.setText(
            "Self test " + ("PASS" if ok else "FAIL"))
        dialog.on_result(ok, checks, res, pdf)

    def _stop_recording(self):
        """Pull whichever recording sink is active out of the flowgraph and
        finalize its file. Armed but not yet recording: just cancel."""
        if self._rec_sched_ts is not None:
            self._cancel_scheduled_start("Idle (scheduled start cancelled)")
            return
        if self._fil_sink is not None:
            self._stop_fil_recording()
        elif self._ezra_sink is not None:
            self._stop_ezra_recording()
        else:
            self._stop_sigmf_recording()
        self._update_fil_geom_enabled()  # geometry editable again once stopped

    def _stop_fil_recording(self):
        """Disconnect the FilterbankSink and close it so the .fil is flushed."""
        sink = self._fil_sink
        if sink is None:
            return
        self._fil_sink = None
        try:
            self.lock()
            try:
                self.disconnect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            print(f"Recording disconnect failed: {exc}", file=sys.stderr)
        info = None
        try:
            info = sink.close()  # flush + close the .fil
        except Exception as exc:
            print(f"Filterbank close failed: {exc}", file=sys.stderr)
        self._on_recording_stopped()   # stop the counter, clear the red indicator
        path = getattr(self, '_fil_sink_path', '')
        if path:
            saved = f"Saved → {os.path.basename(path)}"
            tip = path
            ge = (info or {}).get('gap_events', 0)
            qp = (info or {}).get('queue_padded_samples', 0)
            if qp:
                lost = qp / max(1.0, self.samp_rate)
                saved += f"  ⚠ {lost:.0f} s DATA LOST"
                tip += (f"\n\nWARNING: the writer could not keep up and "
                        f"{lost:.0f} s of signal was replaced by zeros (the "
                        f"timebase is still correct). Reduce Channels, raise "
                        f"Integrate, or lower the sample rate for this "
                        f"geometry; see the .gaps.json sidecar.")
            if (info or {}).get('timebase_broken'):
                saved += "  ⚠ TIMEBASE BROKEN"
                tip += ("\nOverflow padding cap exceeded — sample clock no "
                        "longer tracks real time; see the .gaps.json sidecar.")
            elif ge:
                gs = info.get('gap_samples', 0) / max(1.0, self.samp_rate)
                saved += f"  ({ge} gap{'s' if ge != 1 else ''} padded, {gs*1e3:.0f} ms)"
                tip += (f"\n{ge} RX-overflow gap(s) zero-padded so the .fil "
                        f"timebase tracks real time; details in the "
                        f".gaps.json sidecar.")
            self._recording_status.setText(saved)
            self._recording_status.setToolTip(tip)
        else:
            self._recording_status.setText("Idle")
            self._recording_status.setToolTip("")
        self._quicklook_btn.setEnabled(False)
        # Automatic post-processing: the canned PRESTO pipeline on the file
        # that just closed ("is the recording good?"), results as a
        # self-contained PDF next to it.
        if path and self._analyze_check.isChecked():
            _p, _dm = self._manual_fold_params()
            self._start_analysis(path, getattr(self, '_fil_rec_source', ''),
                                 quick=False, fold_p_s=_p, fold_dm=_dm)

    def _stop_sigmf_recording(self):
        """Pull the SigMF sink out of the flowgraph and drop the Python
        reference so its destructor finalizes the data file."""
        sink = self._sigmf_sink
        if sink is None:
            return
        self._sigmf_sink = None
        try:
            self.lock()
            try:
                self.disconnect((self.uhd_usrp_source_0, 0), (sink, 0))
            finally:
                self.unlock()
        except Exception as exc:
            print(f"Recording disconnect failed: {exc}", file=sys.stderr)
        del sink  # let GC run the destructor and flush the file
        self._on_recording_stopped()   # stop the counter, clear the red indicator
        path = getattr(self, '_sigmf_sink_path', '')
        if path:
            self._recording_status.setText(
                f"Saved → {os.path.basename(path)}.sigmf-data")
            self._recording_status.setToolTip(f"{path}.sigmf-data")
        else:
            self._recording_status.setText("Idle")
            self._recording_status.setToolTip("")

    # --- Source name + timed-recording helpers -----------------------------

    def _on_source_name_changed(self):
        self._source_name = self._source_name_edit.text().strip()
        self._save_setting('recording', 'source_name', self._source_name)
        if getattr(self, '_catalog_radec_for', None) != self._source_name:
            self._catalog_radec = None      # hand-typed name: no exact coords
        self._sync_fold_fields_to_source()

    def _source_is_catalog_pulsar(self):
        """True if the Source name is a recognized pulsar designation (B/J), so
        the fold goes through prepfold -psr and the Fold boxes are information-
        only (locked)."""
        from sigproc_fil import radec_from_name
        name = (self._source_name or "").strip()
        return bool(name) and radec_from_name(name) != (0.0, 0.0)

    def _sync_fold_fields_to_source(self):
        """Lock the Fold P / DM boxes for a catalog pulsar — they become an
        information-only preview (catalog period/DM if PRESTO knows them, else
        blank), and the fold always runs prepfold -psr. Editable only for a
        non-catalog source (the simulator / manual). Never overwrites the saved
        manual values (self._fold_period_ms / self._fold_dm)."""
        if self._source_is_catalog_pulsar():
            p_txt = dm_txt = ""
            try:
                import fold_analysis
                info = fold_analysis.catalog_lookup(self._source_name)
            except Exception:
                info = None
            if info:
                p_txt, dm_txt = f"{info[0] * 1e3:.4f}", f"{info[1]:.4f}"
            self._fold_period_edit.setText(p_txt)
            self._fold_dm_edit.setText(dm_txt)
            self._fold_period_edit.setPlaceholderText(
                "" if p_txt else "catalog fold (period not in PRESTO catalog)")
            self._fold_dm_edit.setPlaceholderText("" if dm_txt else "catalog")
            self._set_fold_fields_readonly(True)
        else:
            self._fold_period_edit.setText(self._fold_period_ms)
            self._fold_dm_edit.setText(self._fold_dm)
            self._fold_period_edit.setPlaceholderText("e.g. 102.4 (simulator)")
            self._fold_dm_edit.setPlaceholderText("optional, default 0")
            self._set_fold_fields_readonly(False)

    def _set_fold_fields_readonly(self, readonly):
        """Read-only (catalog info) vs editable (manual). Read-only gets a muted
        background so it plainly reads as 'locked, for information'."""
        for w in (self._fold_period_edit, self._fold_dm_edit):
            w.setReadOnly(readonly)
            w.setStyleSheet(
                "QLineEdit{background:palette(window);color:palette(mid);}"
                if readonly else "")
        self._fold_period_edit.setToolTip(
            "Shown from PRESTO's pulsar catalog. The fold uses the pulsar's\n"
            "full ephemeris (prepfold -psr), so these are information-only and\n"
            "locked while the Source is a catalog pulsar."
            if readonly else
            "Manual fold period in milliseconds. Set this to force a full\n"
            "prepfold (-topo -p) on a known-period source that has no catalog\n"
            "entry — e.g. the lab pulsar simulator.")

    def _on_fold_period_changed(self):
        if self._fold_period_edit.isReadOnly():
            return  # catalog preview — don't persist as a manual value
        self._fold_period_ms = self._fold_period_edit.text().strip()
        self._save_setting('recording', 'fold_period_ms', self._fold_period_ms)

    def _on_fold_dm_changed(self):
        if self._fold_dm_edit.isReadOnly():
            return
        self._fold_dm = self._fold_dm_edit.text().strip()
        self._save_setting('recording', 'fold_dm', self._fold_dm)

    def _manual_fold_params(self):
        """(period_s, dm) for a manual prepfold -topo -p, or (None, None) to
        fold by catalog Source name (-psr) / run data checks only. A recognized
        catalog pulsar always folds via -psr, so the info boxes are ignored."""
        if self._source_is_catalog_pulsar():
            return (None, None)
        txt = (self._fold_period_ms or "").strip()
        if not txt:
            return (None, None)
        try:
            p_s = float(txt) / 1000.0
        except ValueError:
            return (None, None)
        if not (p_s > 0):
            return (None, None)
        dm = 0.0
        dtxt = (self._fold_dm or "").strip()
        if dtxt:
            try:
                dm = float(dtxt)
            except ValueError:
                dm = 0.0
        return (p_s, dm)

    def _on_rec_duration_changed(self):
        self._rec_duration_text = self._rec_duration_edit.text().strip()
        self._save_setting('recording', 'rec_duration', self._rec_duration_text)

    def _on_rec_start_changed(self, *_):
        self._rec_start_text = self._rec_start_edit.text().strip()
        self._rec_start_tz = self._rec_start_tz_combo.currentText()
        self._save_setting('recording', 'rec_start', self._rec_start_text)
        self._save_setting('recording', 'rec_start_tz', self._rec_start_tz)

    @staticmethod
    def _parse_start_ts(text, utc=True, now=None):
        """"Start at" text -> unix time, or None for blank. Raises ValueError
        for anything else, so a typo cannot silently start a recording now.

        Forms: "HH:MM[:SS]" = the next time that clock reading comes round
        (today if still ahead, else tomorrow); "+30" / "+1:30" = a delay in
        minutes or H:MM from now; "YYYY-MM-DD HH:MM[:SS]" = that instant.
        `utc` says how to read the clock; local time goes through mktime so
        a wall-clock time stays a wall-clock time across a DST change."""
        import calendar
        t = (text or "").strip()
        if not t:
            return None
        now = time.time() if now is None else now
        if t.startswith("+"):
            secs = dses_workbench._parse_duration_s(t[1:])
            if secs <= 0:
                raise ValueError(f"delay {t!r} is not minutes or H:MM")
            return now + secs
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", t)
        if m:
            y, mo, d, h, mi, sec = (int(x or 0) for x in m.groups())
            if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= sec < 60):
                raise ValueError(f"time of day out of range in {t!r}")
            parts = (y, mo, d, h, mi, sec, 0, 1, -1)
            return float(calendar.timegm(parts) if utc else time.mktime(parts))
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
        if m:
            h, mi, sec = (int(x or 0) for x in m.groups())
            if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= sec < 60):
                raise ValueError(f"time of day out of range in {t!r}")
            st = time.gmtime(now) if utc else time.localtime(now)
            today = (st.tm_year, st.tm_mon, st.tm_mday, h, mi, sec, 0, 1, -1)
            if utc:
                ts = float(calendar.timegm(today))
                return ts if ts > now else ts + 86400.0
            ts = float(time.mktime(today))
            if ts > now:
                return ts
            tomorrow = (st.tm_year, st.tm_mon, st.tm_mday + 1, h, mi, sec, 0, 1, -1)
            return float(time.mktime(tomorrow))   # mktime normalizes the day
        raise ValueError(f"start time {t!r} not understood")

    def _fmt_clock_tz(self, ts):
        """A clock reading in the Start at zone, with the date when it is not
        today's: "03:15 UTC" / "21:15 local on 2026-10-24"."""
        utc = self._rec_start_tz != "Local"
        st = time.gmtime(ts) if utc else time.localtime(ts)
        today = time.gmtime() if utc else time.localtime()
        out = time.strftime("%H:%M", st) + (" UTC" if utc else " local")
        if (st.tm_year, st.tm_yday) != (today.tm_year, today.tm_yday):
            out += time.strftime(" on %Y-%m-%d", st)
        return out

    def _arm_scheduled_start(self, start_ts):
        """Wait for the scheduled instant: amber ARMED status, countdown in
        the counter slot, the timed-recording controls locked as if live."""
        self._rec_sched_ts = float(start_ts)
        self._recording_status.setText(
            f"Armed — recording starts at {self._fmt_clock_tz(start_ts)}")
        self._recording_status.setStyleSheet(
            "color: black; background-color: #f5b041;"
            " padding: 2px 4px; border-radius: 3px;")     # amber = ARMED
        self._source_name_widget.setEnabled(False)
        self._rec_duration_widget.setEnabled(False)
        self._rec_start_widget.setEnabled(False)
        self._rec_elapsed_label.setStyleSheet(
            "color: #b9770e; font-weight: bold;")          # amber countdown
        self._rec_elapsed_label.setVisible(True)
        self._tick_armed()
        self._rec_wait_timer.start()

    def _cancel_scheduled_start(self, note="Idle"):
        """Leave the armed state without recording (Stopped, or a failed
        fire). Idempotent."""
        self._rec_wait_timer.stop()
        self._rec_sched_ts = None
        self._recording_status.setStyleSheet("")
        self._recording_status.setText(note)
        self._source_name_widget.setEnabled(True)
        self._rec_duration_widget.setEnabled(True)
        self._rec_start_widget.setEnabled(True)
        self._rec_elapsed_label.setVisible(False)
        self._rec_elapsed_label.setText("")
        self._rec_elapsed_label.setStyleSheet(
            "color: #e74c3c; font-weight: bold;")

    def _tick_armed(self):
        """1 Hz while armed: paint the countdown; at the instant, fire."""
        if self._rec_sched_ts is None:
            return
        left = self._rec_sched_ts - time.time()
        if left > 0.5:
            dur = self._parse_duration_s(self._rec_duration_text)
            txt = (f"⏱ starts in {self._fmt_hms(round(left))}"
                   f"  (at {self._fmt_clock_tz(self._rec_sched_ts)})")
            if dur > 0:
                txt += (f", then records {self._fmt_hms(dur)} until "
                        f"{self._fmt_clock_tz(self._rec_sched_ts + dur)}")
            self._rec_elapsed_label.setText(txt)
            return
        # The instant. Leave the armed state first so the start runs the
        # real path, then start with the questions already answered.
        self._rec_wait_timer.stop()
        self._rec_sched_ts = None
        self._start_recording(scheduled=True)
        if self._rec_start_time is None:            # nothing started (radio gone?)
            self._cancel_scheduled_start(
                "Scheduled start failed: no recording source")
            self.record = 0
            self._record_callback(0)

    @staticmethod
    def _sanitize_name(name):
        """A filesystem-safe token from a source name ('' if nothing usable)."""
        keep = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789+.-")
        s = "".join(c if c in keep else "_" for c in (name or "").strip())
        return s.strip("_")[:40]

    def _recording_basename(self, ts):
        """DSES_Workbench[_<source>]_<timestamp>  (no extension)."""
        src = self._sanitize_name(self._source_name)
        parts = ["DSES_Workbench"] + ([src] if src else []) + [ts]
        return "_".join(parts)

    @staticmethod
    def _parse_duration_s(text):
        """'30' -> minutes; 'H:MM' / 'HH:MM:SS' -> that time. Returns seconds, or
        0 if blank/unparseable (meaning 'record until manually stopped')."""
        t = (text or "").strip()
        if not t:
            return 0
        try:
            if ":" in t:
                p = [int(x) for x in t.split(":")]
                if len(p) == 2:
                    return (p[0] * 60 + p[1]) * 60
                if len(p) == 3:
                    return p[0] * 3600 + p[1] * 60 + p[2]
                return 0
            return int(round(float(t) * 60))     # a plain number = minutes
        except ValueError:
            return 0

    @staticmethod
    def _fmt_hms(seconds):
        s = max(0, int(seconds)); h, r = divmod(s, 3600); m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}"

    # --- Real-time hygiene while recording --------------------------------
    # Instrumented headless probes, 2044 ch @ 16 MS/s (2026-08-02): CPython's
    # small gen-0 GC collections are harmless (~0.27 ms each), but the
    # occasional FULL collection scans the entire heap and stalled the GNU
    # Radio thread up to 42.5 ms - long enough to exhaust the B210's ~65 ms
    # USB transport cushion and cause an RX overflow. Designs that disable GC
    # or batch it were measured and rejected: fully-disabled GC leaks cycle
    # garbage over multi-hour sessions, and letting young objects pile up
    # makes each deferred collection cost ~75-130 ms. The winning design
    # keeps GC ENABLED (multi-hour safe) but freezes the existing heap and
    # pushes full collections out of reach:
    #   baseline:  MAX GC pause 42.5 ms, max work() interval 42.4 ms
    #   this mode: MAX GC pause  1.0 ms, max work() interval  7.1 ms
    #              (180 s run: 0 samples lost)
    # All of this is plain CPython and applies identically on Windows, macOS,
    # and Linux; the timer call below is the only platform-specific piece.

    _GC_RT_THRESHOLD = RealtimeMode.GC_RT_THRESHOLD

    def _begin_realtime_mode(self):
        """Cap host-side stall length for the duration of a recording (GC
        confinement + 1 ms Windows timer): dses_radio.RealtimeMode."""
        rt = getattr(self, '_rt_mode', None)
        if rt is None:
            rt = self._rt_mode = RealtimeMode(self._GC_RT_THRESHOLD)
        rt.begin()
        self._rt_active = rt.active

    def _end_realtime_mode(self):
        rt = getattr(self, '_rt_mode', None)
        if rt is not None:
            rt.end()
        self._rt_active = False

    def _on_recording_started(self):
        """Called once a recording sink is actually live: start the elapsed /
        countdown counter, turn the status indicator red, and arm the auto-stop."""
        self._rec_start_time = time.monotonic()
        self._rec_duration_s = self._parse_duration_s(self._rec_duration_text)
        self._begin_realtime_mode()                 # suppress GC / timer stalls
        self._processor.set_recording_active(True)  # yield display CPU to the sink
        self._recording_status.setStyleSheet(
            "color: white; background-color: #c0392b;"
            " padding: 2px 4px; border-radius: 3px;")     # red = RECORDING
        self._source_name_widget.setEnabled(False)        # locked in for this file
        self._rec_duration_widget.setEnabled(False)
        self._rec_start_widget.setEnabled(False)
        self._rec_elapsed_label.setStyleSheet(
            "color: #e74c3c; font-weight: bold;")          # red REC (amber while armed)
        self._rec_elapsed_label.setVisible(True)
        self._tick_recording()                             # paint 0:00 at once
        self._rec_timer.start()

    def _on_recording_stopped(self):
        """Called when a recording sink is torn down: stop the counter and clear
        the red indicator. Idempotent (safe if nothing was running)."""
        self._rec_timer.stop()
        self._rec_start_time = None
        self._end_realtime_mode()                    # restore GC / timer
        self._processor.set_recording_active(False)  # full display budget again
        self._recording_status.setStyleSheet("")           # back to normal colour
        self._source_name_widget.setEnabled(True)
        self._rec_duration_widget.setEnabled(True)
        self._rec_start_widget.setEnabled(True)
        self._rec_elapsed_label.setVisible(False)
        self._rec_elapsed_label.setText("")

    def _rec_gap_suffix(self):
        """Live timebase-gap annotation for the recording counter (v1.1.8):
        shows UHD-tagged overflow gaps as they are detected and padded."""
        sink = self._fil_sink
        if sink is None:
            return ""
        if getattr(sink, 'timebase_broken', False):
            return "   ⚠ TIMEBASE BROKEN (pad cap exceeded)"
        parts = []
        ge = getattr(sink, 'gap_events', 0)
        if ge:
            parts.append(f"{ge} gap{'s' if ge != 1 else ''}, "
                         f"{sink.gap_seconds * 1e3:.0f} ms padded")
        # Worker-deficit padding means SIGNAL was replaced by zeros — a much
        # louder problem than a tagged gap, so surface it prominently.
        qp = getattr(sink, 'queue_padded_samples', 0)
        if qp:
            lost = qp / max(1.0, self.samp_rate)
            parts.append(f"⚠⚠ {lost:.0f} s OF DATA LOST (writer too slow)")
        return ("   ⚠ " + "; ".join(parts)) if parts else ""

    def _tick_recording(self):
        """1 Hz: refresh the elapsed/countdown text; auto-stop at the target."""
        if self._rec_start_time is None:
            return
        # Multi-day drift scan: after the UTC-midnight rollover the sink is
        # writing a NEW daily file — keep the status line naming the current
        # one (reading .path is just an attribute fetch; the worker thread
        # owns the actual file handle).
        sink = getattr(self, '_ezra_sink', None)
        if sink is not None:
            cur = str(sink.path)
            if cur != getattr(self, '_ezra_sink_path', cur):
                self._ezra_sink_path = cur
                self._recording_status.setText(
                    f"Recording → {os.path.basename(cur)} (day "
                    f"{len(sink._writer.paths)} of this scan)")
                self._recording_status.setToolTip(
                    "\n".join(sink._writer.paths))
        elapsed = time.monotonic() - self._rec_start_time
        gaps = self._rec_gap_suffix()
        if self._rec_duration_s > 0:
            if elapsed >= self._rec_duration_s:
                self._rec_elapsed_label.setText(
                    "⏺ REC  " + self._fmt_hms(self._rec_duration_s)
                    + " / " + self._fmt_hms(self._rec_duration_s) + gaps)
                self.set_record(0)          # target reached -> stop_recording
                return
            self._rec_elapsed_label.setText(
                "⏺ REC  " + self._fmt_hms(elapsed)
                + "  (−" + self._fmt_hms(self._rec_duration_s - elapsed)
                + " left)" + gaps)
        else:
            self._rec_elapsed_label.setText(
                "⏺ REC  " + self._fmt_hms(elapsed) + gaps)

    def get_gain(self):
        return self.gain

    def set_gain(self, gain):
        if self._playback_mode or self._source is None:
            return  # no gain knob in playback mode
        self.gain = gain
        self._source.set_gain(self.gain)
        # Keep the dBm scale gain-independent (display-only; no effect on 'relative').
        self._processor.set_gain_db(float(gain))
        self._save_setting('rx', 'gain_db', float(gain))

    def set_antenna(self, name):
        if self._playback_mode or self._source is None:
            return  # no antenna selection in playback mode
        # A plain port change within a receiver (B210 TX/RX↔RX2) takes effect
        # live. Switching the active *receiver* (A↔B) remaps channel 0 to a
        # different RX frontend, which only takes hold when the source block's
        # RX streamer is created fresh — a runtime set_subdev_spec (even under
        # lock/unlock) does not rebind the live streamer. So we tear down and
        # rebuild the source block on a receiver change.
        if self._source.antenna_needs_restart(name):
            if self._sigmf_sink is not None or self._fil_sink is not None:
                QtWidgets.QMessageBox.information(
                    self, "Stop recording first",
                    "Switching between receiver A and receiver B restarts the "
                    "radio, which can't be done while recording. Stop the "
                    "recording, then switch receivers.")
                self._sync_antenna_combo()  # revert combo to the live port
                return
            self._rebuild_source_for_antenna(name)
        else:
            self._source.set_antenna(name)
        self._save_setting('rx', 'antenna', name)

    def _rebuild_source_for_antenna(self, name):
        """Tear the radio source out of the flowgraph and rebuild it on the
        requested antenna/receiver. Needed because the active RX frontend is
        fixed when the source's streamer is created; only a fresh block picks
        up a different receiver. Brief stream gap while it restarts."""
        import gc
        self.stop()
        self.wait()
        self.disconnect((self.uhd_usrp_source_0, 0), (self._stream_to_vec, 0))
        # Release the old device handle fully (the B210 is exclusive-access)
        # before opening it again on the new frontend.
        self._source = None
        self.uhd_usrp_source_0 = None
        gc.collect()
        assert self._device_serial is not None  # always set in live mode
        new = UhdB200Source(
            serial=self._device_serial, samp_rate=self.samp_rate,
            center_freq=self.center_freq, gain=self.gain, antenna=name)
        self._source = new
        self.uhd_usrp_source_0 = new.block
        self.connect((self.uhd_usrp_source_0, 0), (self._stream_to_vec, 0))
        self.start()
        self._overflow_widget.clear()

    def _sync_antenna_combo(self):
        """Set the Antenna combo back to the source's live port without
        re-triggering set_antenna (used when a switch is refused)."""
        combo = getattr(self, '_antenna_combo_box', None)
        if combo is None or self._source is None:
            return
        cur = self._source.current_antenna
        if cur in self._antenna_options:
            with _SignalBlocker(combo):
                combo.setCurrentIndex(self._antenna_options.index(cur))

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq
        self._fft_plot.set_frequency_range(self.center_freq, self.samp_rate)
        self._waterfall_plot.set_frequency_range(self.center_freq, self.samp_rate)
        self._processor.reset_averaging()   # don't smear the average across tunings
        if self._playback_mode:
            # Virtual retune: shift the file's baseband by the offset between
            # the requested center frequency and the file's original center.
            # rotator_cc multiplies samples by exp(j*phase_inc*n), so a
            # negative phase_inc shifts the spectrum down by the desired
            # offset. Outside the file's bandwidth the user just sees the
            # wrap-around / noise floor.
            import math
            offset_hz = self.center_freq - self._playback_center_freq
            phase_inc = -2.0 * math.pi * offset_hz / self.samp_rate
            self._rotator.set_phase_inc(phase_inc)
            return
        if self._source is not None:
            self._source.set_center_freq(self.center_freq)




def _set_macos_app_name(name):
    """macOS shows the *main bundle's* CFBundleName in the bold application menu
    (next to the Apple menu), so a plain `python dses_workbench.py` reads
    'Python' there — which hides that Help/File/etc. belong to this app. Rewrite
    CFBundleName at runtime via the ObjC runtime (no PyObjC dependency). Must run
    BEFORE QApplication builds the native menu bar. Fully guarded: it only mutates
    the info dict if that dict actually implements setObject:forKey: (an immutable
    NSDictionary does not — calling it would raise an ObjC exception that ctypes
    could not catch), and any failure just leaves the default name."""
    if sys.platform != 'darwin':
        return
    try:
        import ctypes
        from ctypes import util, c_void_p, c_char_p, c_uint32, c_bool, CFUNCTYPE
        objc = ctypes.CDLL(util.find_library('objc'))
        cf = ctypes.CDLL(util.find_library('CoreFoundation'))
        objc.objc_getClass.restype = c_void_p
        objc.objc_getClass.argtypes = [c_char_p]
        objc.sel_registerName.restype = c_void_p
        objc.sel_registerName.argtypes = [c_char_p]
        cid = c_void_p
        msg = CFUNCTYPE(cid, cid, cid)(('objc_msgSend', objc))            # (id, SEL)->id
        msg_resp = CFUNCTYPE(c_bool, cid, cid, cid)(('objc_msgSend', objc))  # respondsToSelector:
        msg_set = CFUNCTYPE(None, cid, cid, cid, cid)(('objc_msgSend', objc))  # setObject:forKey:
        bundle = msg(objc.objc_getClass(b'NSBundle'),
                     objc.sel_registerName(b'mainBundle'))
        if not bundle:
            return
        info = msg(bundle, objc.sel_registerName(b'infoDictionary'))
        if not info:
            return
        set_sel = objc.sel_registerName(b'setObject:forKey:')
        if not msg_resp(info, objc.sel_registerName(b'respondsToSelector:'), set_sel):
            return  # immutable dict — don't risk an ObjC exception
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        kUTF8 = 0x08000100
        val = cf.CFStringCreateWithCString(None, name.encode('utf-8'), kUTF8)
        key = cf.CFStringCreateWithCString(None, b'CFBundleName', kUTF8)
        if val and key:
            msg_set(info, set_sel, val, key)
    except Exception:
        pass


def main(top_block_cls=dses_workbench, options=None):

    # macOS: make the application menu read the app name instead of "python".
    # Must precede QApplication (which builds the native menu bar).
    _set_macos_app_name(APP_NAME)
    qapp = QtWidgets.QApplication(sys.argv)
    # Drive QStandardPaths.AppDataLocation to %APPDATA%/DSES_Analyzer (Windows)
    # / ~/Library/Application Support/DSES_Analyzer (mac) / ~/.local/share/...
    # Must be set BEFORE constructing the top block (which builds Settings).
    # NOTE: deliberately NOT renamed with the product (1.4.0): this string is
    # the settings/config folder name on every platform, and changing it would
    # orphan every existing settings.ini and cached radioconda path.
    QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
    QtWidgets.QApplication.setApplicationDisplayName(APP_NAME)
    QtWidgets.QApplication.setApplicationVersion(APP_VERSION)

    tb = top_block_cls()

    tb.start()
    tb.flowgraph_started.set()

    tb.show()
    _bring_to_front(tb)  # open in front (esp. macOS launched from a terminal)

    def sig_handler(sig=None, frame=None):
        # closeEvent doesn't fire on SIGINT/SIGTERM (e.g. Ctrl-C in the
        # terminal or closing the terminal window), so persist window geometry
        # and settings here too — otherwise quitting from the terminal loses
        # the latest configuration.
        try:
            tb._save_geometry()
        except Exception as exc:
            print(f"Settings save on signal failed: {exc}", file=sys.stderr)
        tb.stop()
        tb.wait()
        QtWidgets.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec()

if __name__ == '__main__':
    main()
