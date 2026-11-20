"""Startup with a radio that has gain stages (a stand-in HackRF) builds the REAL
main window — the test that would have caught 1.8.1.

1.8.1 built the HackRF gain-stage row before the spectrum processor existed;
the row's last step called the processor and the window died during startup on
every HackRF (Ray Uberecken, 2026-11-19, the launcher's startup-failure box).
test_gain_stages.py built the row on a window that already existed, so it never
saw the startup order. This test constructs the whole window with a streaming
stand-in block that answers gr-soapy's calls as SoapyHackRF does, then checks:

  1. the window builds and the flowgraph streams frames;
  2. the Stages row is there (AMP, LNA, VGA) and the slider reads LNA + VGA;
  3. the spectrum processor is given the total gain, AMP included;
  4. a saved split (LNA 40 / VGA 26 / AMP 14) survives a restart, and the
     processor starts with it.

Run before EVERY cut:
    PATH=.conda/Library/bin:$PATH .conda/python.exe test_startup_staged_radio.py
"""
import os, sys, tempfile, time, traceback
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg = tempfile.mkdtemp(prefix="dses_staged_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from PySide6 import QtWidgets, QtCore
from gnuradio import gr, blocks
import gnuradio
import dses_workbench as A

(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
for _n in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _n,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))


class _Range:
    def __init__(self, lo, hi, step):
        self._v = (lo, hi, step)
    def minimum(self): return self._v[0]
    def maximum(self): return self._v[1]
    def step(self): return self._v[2]


class FakeHackRFBlock(gr.hier_block2):
    """A streaming complex source that answers gr-soapy's calls as SoapyHackRF does."""
    RANGES = {"AMP": (0.0, 14.0, 14.0), "LNA": (0.0, 40.0, 8.0), "VGA": (0.0, 62.0, 2.0)}

    def __init__(self):
        gr.hier_block2.__init__(self, "fake_hackrf", gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self._src = blocks.null_source(gr.sizeof_gr_complex)
        self._thr = blocks.throttle(gr.sizeof_gr_complex, 2e6, True)
        self.connect(self._src, self._thr, self)
        self.values = {n: 0.0 for n in self.RANGES}
        self.rate, self.freq = 2e6, 1420.4e6

    def list_gains(self, chan): return list(self.RANGES)
    def get_gain_range(self, chan, name=None):
        return _Range(*self.RANGES[name]) if name else _Range(0.0, 116.0, 1.0)
    def set_gain(self, chan, *a):
        if len(a) == 2:
            self.values[a[0]] = float(a[1])
    def get_gain(self, chan, name=None):
        return self.values.get(name, 0.0) if name else sum(self.values.values())
    def set_gain_mode(self, chan, auto): pass
    def set_sample_rate(self, chan, r): self.rate = float(r)
    def get_sample_rate(self, chan): return self.rate
    def get_sample_rate_range(self, chan): return [_Range(2e6, 20e6, 0.0)]
    def set_frequency(self, chan, *a): self.freq = float(a[-1])
    def get_frequency(self, chan, *a): return self.freq
    def get_frequency_range(self, chan, *a): return [_Range(1e6, 6e9, 0.0)]
    def list_frequencies(self, chan): return ["RF"]
    def list_antennas(self, chan): return ["TX/RX"]
    def set_antenna(self, chan, name): pass
    def get_antenna(self, chan): return "TX/RX"
    def set_bandwidth(self, chan, bw): pass


class _FakeSoapyModule:
    @staticmethod
    def source(*a, **k):
        return FakeHackRFBlock()


sys.modules["gnuradio.soapy"] = _FakeSoapyModule
gnuradio.soapy = _FakeSoapyModule
A._soapy_probe_rates = lambda dev_args: [2e6, 4e6, 8e6, 10e6, 16e6, 20e6]
A.find_all_radios = lambda: [{"driver": "hackrf", "serial": "0000000000000000fake",
                              "product": "HackRF One", "label": "HackRF One #0 fake [hackrf]"}]

app = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


# --- 1. the window builds with a staged radio and streams ---------------------
try:
    tb = A.dses_workbench()
except Exception:
    traceback.print_exc(limit=4)
    raise SystemExit("FAIL: the main window did not build with a HackRF attached (the 1.8.1 fault)")
ok(not tb._playback_mode and "HackRF" in tb._source.display_label,
   f"expected the stand-in HackRF, got {tb._source.display_label}")
tb.start(); tb.flowgraph_started.set(); tb.show()
frames = []
tb._processor.frame_ready.connect(lambda a, m, n: frames.append(1))
pump(2.0)
ok(len(frames) >= 5, f"only {len(frames)} display frames in 2 s")
print(f"1. window built with {tb._source.display_label}; {len(frames)} frames in 2 s")

# --- 2. the Stages row -----------------------------------------------------------
ok(sorted(tb._gain_stage_spins) == ["AMP", "LNA", "VGA"], f"stage boxes {sorted(tb._gain_stage_spins)}")
ok(tb._source.gain_range == (0.0, 102.0, 1.0), f"slider range {tb._source.gain_range}")
ok(tb.gain == tb._source.slider_gain(), f"slider {tb.gain} vs stages {tb._source.slider_gain()}")
ok(abs(tb._processor._gain_db - tb._source.total_gain()) < 1e-6,
   f"processor started with {tb._processor._gain_db}, stages total {tb._source.total_gain()}")
print(f"2. Stages row present; slider {tb.gain:g} dB = LNA {tb._source.stage_gain('LNA'):g} "
      f"+ VGA {tb._source.stage_gain('VGA'):g}; processor {tb._processor._gain_db:g} dB")

# --- 3. the processor follows the stages (AMP included) ---------------------------
tb._gain_stage_spins["VGA"].setValue(26)
tb._gain_stage_spins["LNA"].setValue(40)
tb._gain_stage_spins["AMP"].setValue(14)
pump(0.3)
ok(abs(tb._processor._gain_db - 80.0) < 1e-6, f"processor gain {tb._processor._gain_db}, want 80 (40+26+14)")
print(f"3. processor gain follows the stages: {tb._processor._gain_db:g} dB (LNA 40 + VGA 26 + AMP 14)")
tb.stop(); tb.wait(); tb.close(); pump(0.3)

# --- 4. a restart keeps the saved split and the processor starts with it ---------
tb = A.dses_workbench()
st = (tb._source.stage_gain("LNA"), tb._source.stage_gain("VGA"), tb._source.stage_gain("AMP"))
ok(st == (40.0, 26.0, 14.0), f"restart split {st}, want (40, 26, 14)")
ok(abs(tb._processor._gain_db - 80.0) < 1e-6, f"processor gain after restart {tb._processor._gain_db}, want 80")
print(f"4. restart: LNA/VGA/AMP {st}, slider {tb.gain:g}, processor {tb._processor._gain_db:g} dB")
tb.close()
print(f"\nALL STAGED-RADIO STARTUP TESTS PASSED ({checks} checks)")
