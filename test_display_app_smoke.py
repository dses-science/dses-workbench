"""Headless end-to-end smoke of the display chain inside the REAL main window
(1.7 compiled display channelizer), in SigMF playback mode so no radio is
needed. Settings are isolated in a temp config dir; the startup message
boxes are auto-accepted.

Checks: the window builds and the flowgraph streams frames; an FFT-size
change swaps the compiled chain under lock/unlock on the running flowgraph
and the trace follows; a window change and a retune reset keep frames
flowing; DC suppress can be toggled; playback keeps running throughout.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_display_app_smoke.py
"""
import os, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg = tempfile.mkdtemp(prefix="dses_smoke_cfg_")
# Isolate settings.ini (QStandardPaths.AppDataLocation): %APPDATA% on Windows,
# XDG_CONFIG_HOME on Linux, nothing simple on macOS (there the run would read
# and write the real settings — acceptable for a smoke, noted here).
os.environ["APPDATA"] = _cfg
os.environ["XDG_CONFIG_HOME"] = _cfg
os.environ["XDG_DATA_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PySide6 import QtWidgets, QtCore

import dses_workbench as A

# Settings isolation: the app derives settings.ini from Qt's AppDataLocation,
# which on Windows ignores %APPDATA% (it asks the shell), so point the app's
# own resolver at the temp dir explicitly.
from pathlib import Path as _Path
(_Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: _Path(_cfg) / "DSES_Analyzer" / "settings.ini"

# Auto-accept every message box (the "no radio -> playback" notice and any
# other modal the startup path may raise).
for _name in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _name,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))
# The startup notices go through the app's own front-most message box helper
# (a QMessageBox instance + exec, which would block headless): answer Ok.
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
LIVE = "--live" in sys.argv      # use the one attached radio instead of playback
# --driver <name>: with several radios attached, use only that SoapySDR/UHD
# driver (e.g. airspy, sdrplay, uhd) — the startup picker is a modal dialog.
if "--driver" in sys.argv:
    _drv = sys.argv[sys.argv.index("--driver") + 1]
    _all = A.find_all_radios
    A.find_all_radios = lambda: [d for d in _all() if d["driver"] == _drv]
if not LIVE:
    # No radio: force playback of the bundled sample.
    A.find_all_radios = lambda: []
    assert A.find_default_sample_path() is not None, "bundled sample.sigmf-* missing next to dses_workbench.py"

qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
assert tb._playback_mode != LIVE, ("expected a live radio" if LIVE else "expected playback mode with no radio")
tb.start()
tb.flowgraph_started.set()
tb.show()

frames = []
tb._processor.frame_ready.connect(lambda avg, mx, mn: frames.append(np.asarray(avg)))


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


pump(3.0)
n0 = len(frames)
assert n0 >= 10, f"only {n0} display frames in 3 s (expected ~30 at 10 Hz)"
blocks = tb._processor._last_blocks_used
assert blocks > 0, "no FFT blocks reached the accumulator"
size0 = tb._display.fft_size
print(f"1. window up ({'LIVE ' + tb._source.display_label if LIVE else 'playback'}), {n0} frames in 3 s, "
      f"{blocks} blocks/tick at FFT {size0}, rate {tb.samp_rate/1e6:g} MS/s, decim {tb._display.decim}")

# FFT-size change: swaps the compiled chain on the RUNNING flowgraph.
new_size = 4096 if size0 != 4096 else 2048
tb._fft_plot.request_fft_size.emit(new_size)
assert tb._display.fft_size == new_size and tb._processor.fft_size == new_size
frames.clear(); pump(2.0)
assert frames and len(frames[-1]) == new_size, \
    f"after the swap: {len(frames)} frames, last len {len(frames[-1]) if frames else None}"
print(f"2. FFT {size0} -> {new_size} swapped live: {len(frames)} frames in 2 s, "
      f"{tb._processor._last_blocks_used} blocks/tick")

# Window change is live (fft_vcc.set_window) and the retune barrier works.
tb._fft_plot.request_window.emit("hann")
assert tb._processor.window_name == "hann"
tb._processor.reset_averaging()
frames.clear(); pump(1.5)
assert len(frames) >= 5, f"frames stalled after window change / reset: {len(frames)}"
print(f"3. window -> hann + reset_averaging: {len(frames)} frames in 1.5 s")

# DC suppress toggle (processor-side, no relock).
tb._fft_plot._dc_check.setChecked(False)
tb._fft_plot._dc_check.setChecked(True)
frames.clear(); pump(1.0)
assert len(frames) >= 3
f = frames[-1]
assert np.all(np.isfinite(f)), "non-finite values in the trace"
print(f"4. DC suppress toggled, trace finite, span {f.min():.1f}..{f.max():.1f} dB")

# Back to the original size (second swap) and a clean shutdown.
tb._fft_plot.request_fft_size.emit(size0)
frames.clear(); pump(1.5)
assert frames and len(frames[-1]) == size0
print(f"5. swapped back to {size0}")

# --record-fil: a real .fil recording through the app's own start/stop path
# (live radio only): the compiled FilterbankChain is spliced under lock /
# unlock beside the display chain, rows are written, the file closes clean.
if LIVE and "--record-fil" in sys.argv:
    tb.set_record_format('fil')
    tb._rec_start_text = ""              # start now, no scheduled start
    tb.set_record(1)
    assert tb._fil_sink is not None and isinstance(tb._fil_sink, A.sigproc_fil.FilterbankChain), \
        "recording did not start on the compiled chain"
    fil_path = tb._fil_sink_path
    frames.clear(); pump(8.0)
    rows_live = tb._fil_sink.nrows
    disp_frames = len(frames)
    tb.set_record(0)
    assert tb._fil_sink is None
    size = os.path.getsize(fil_path)
    expect = 4.0 * tb.samp_rate * 8.0 / max(1, tb._fil_integrate)
    assert rows_live > 0 and size > 0.8 * expect, f"recording short: {rows_live} rows, {size} bytes vs ~{expect:.0f}"
    assert disp_frames >= 60, f"display starved while recording: {disp_frames} frames in 8 s"
    print(f"6. live .fil recording OK: {rows_live} rows, {size/1e6:.0f} MB in 8 s at "
          f"{tb.samp_rate/1e6:g} MS/s / {tb._fil_nchans} ch, display kept {disp_frames} frames; "
          f"file {os.path.basename(fil_path)}")
    for p in (fil_path, fil_path + ".gaps.json"):
        try:
            os.remove(p)
        except OSError:
            pass

tb.stop(); tb.wait()
tb.close()
print("7. flowgraph stopped cleanly")
print("DISPLAY APP SMOKE PASSED")
