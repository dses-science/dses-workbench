"""Tests for per-stage gain on SoapySDR radios (1.8.1).

Ray Uberecken, 2026-11-19: "I run the only gain I can find at 47dB." The
Workbench capped a HackRF at 47 dB through one overall number; SDR Console
ran the same radio at LNA 40 + VGA 26 with the amplifier off. No HackRF is
in the lab, so the radio is a stand-in that answers gr-soapy's per-element
calls exactly as SoapyHackRF does (AMP 0/14, LNA 0-40 by 8, VGA 0-62 by 2).

  1. Discovery: stages found, the slider range becomes LNA + VGA = 102 dB;
     a driver outside SOAPY_STAGE_GAINS, or a device missing a stage, stays
     on the single overall gain.
  2. The slider fills the LNA first, then the VGA, in the stages' steps;
     AMP is never touched. 66 dB = LNA 40 + VGA 26 (SDR Console's setting).
  3. A stage set directly is snapped to its step and clamped.
  4. The Radio panel row in the real window: boxes show the stages, a box
     edit moves the slider to the sum without reshuffling the split, the
     slider refills the boxes, values are saved and restored, and a restart
     re-applying the saved slider value leaves the split alone.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_gain_stages.py
"""
import os, sys, tempfile, time
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg = tempfile.mkdtemp(prefix="dses_gain_cfg_")
os.environ["XDG_CONFIG_HOME"] = _cfg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from PySide6 import QtWidgets, QtCore
import dses_workbench as A

checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


class _Range:
    def __init__(self, lo, hi, step):
        self._v = (lo, hi, step)
    def minimum(self): return self._v[0]
    def maximum(self): return self._v[1]
    def step(self): return self._v[2]


class FakeHackRFBlock:
    """gr-soapy's gain calls as SoapyHackRF answers them."""
    RANGES = {"AMP": (0.0, 14.0, 14.0), "LNA": (0.0, 40.0, 8.0), "VGA": (0.0, 62.0, 2.0)}

    def __init__(self, stages=("AMP", "LNA", "VGA")):
        self.stages = list(stages)
        self.values = {n: 0.0 for n in self.stages}
        self.calls = []
    def list_gains(self, chan): return list(self.stages)
    def get_gain_range(self, chan, name=None): return _Range(*self.RANGES[name])
    def set_gain(self, chan, *a):
        self.calls.append(a)
        if len(a) == 2:
            self.values[a[0]] = float(a[1])
        else:
            raise AssertionError("the overall set_gain must not be used for a staged radio")
    def get_gain(self, chan, name=None): return self.values[name]


def make_source(driver="hackrf", stages=("AMP", "LNA", "VGA")):
    src = object.__new__(A.SoapyGenericSource)
    src._driver = driver
    src._invert_gain = False
    src.gain_range = A.SOAPY_DEFAULTS.get(driver, {}).get('gain', (0.0, 76.0, 1.0))
    src.block = FakeHackRFBlock(stages)
    src._discover_gain_stages()
    return src


# --- 1. discovery -----------------------------------------------------------
src = make_source()
ok([s[0] for s in src.gain_stages] == ["AMP", "LNA", "VGA"], f"stages {src.gain_stages}")
ok(src.gain_range == (0.0, 102.0, 1.0), f"slider range {src.gain_range}, want 0-102")
other = make_source(driver="airspy")
ok(other.gain_stages == [] and other.gain_range == A.SOAPY_DEFAULTS['airspy']['gain'],
   "a driver outside SOAPY_STAGE_GAINS must stay on the overall gain")
short = make_source(stages=("AMP", "LNA"))
ok(short.gain_stages == [] and short.gain_range == A.SOAPY_DEFAULTS['hackrf']['gain'],
   "a device missing a policy stage must stay on the overall gain")
nostep = object.__new__(A.SoapyGenericSource)
nostep._driver, nostep._invert_gain, nostep.gain_range = "hackrf", False, (0.0, 47.0, 1.0)
nostep.block = FakeHackRFBlock()
nostep.block.RANGES = {k: (v[0], v[1], 0.0) for k, v in FakeHackRFBlock.RANGES.items()}   # as the RSP1B driver does
nostep._discover_gain_stages()
ok({s[0]: s[3] for s in nostep.gain_stages} == {"AMP": 14.0, "LNA": 8.0, "VGA": 2.0},
   f"a driver reporting step 0 must get the hardware's steps: {nostep.gain_stages}")
print("1. discovery OK: HackRF -> AMP/LNA/VGA, slider 0-102 dB; other drivers and odd devices unchanged")

# --- 2. the slider fills LNA first, then VGA; AMP untouched ------------------
for overall, lna, vga in ((0, 0, 0), (8, 8, 0), (30, 24, 6), (47, 40, 6), (66, 40, 26), (102, 40, 62), (150, 40, 62)):
    src.set_gain(overall)
    got = (src.stage_gain("LNA"), src.stage_gain("VGA"), src.stage_gain("AMP"))
    ok(got == (lna, vga, 0.0), f"slider {overall}: LNA/VGA/AMP {got}, want ({lna}, {vga}, 0)")
src.set_gain(66)
ok(src.slider_gain() == 66.0 and src.total_gain() == 66.0, "66 dB = LNA 40 + VGA 26")
src.set_gain_stage("AMP", 14)
src.set_gain(47)
ok(src.stage_gain("AMP") == 14.0 and src.slider_gain() == 46.0 and src.total_gain() == 60.0,
   f"AMP must survive a slider move: {src._stage_values}")
src.set_gain_stage("AMP", 0)
print("2. slider split OK: 47 -> LNA 40 / VGA 6, 66 -> LNA 40 / VGA 26 (SDR Console's), AMP never touched")

# --- 3. a stage set directly: snapped to its step, clamped ------------------
ok(src.set_gain_stage("LNA", 21) == 16.0 and src.set_gain_stage("LNA", 99) == 40.0, "LNA snaps to 8s, clamps at 40")
ok(src.set_gain_stage("VGA", 27) == 26.0 and src.set_gain_stage("VGA", -5) == 0.0, "VGA snaps to 2s, clamps at 0")
ok(src.set_gain_stage("AMP", 9) == 0.0 and src.set_gain_stage("AMP", 14) == 14.0, "AMP is 0 or 14")
ok(src.set_gain_stage("NOPE", 3) == 0.0, "an unknown stage is ignored")
src.set_gain_stage("AMP", 0)
ok(all(len(c) == 2 for c in src.block.calls), "only per-element set_gain calls reached the driver")
print("3. direct stage setting OK")

# --- 4. the Radio panel row in the real window -------------------------------
(Path(_cfg) / "DSES_Analyzer").mkdir(parents=True, exist_ok=True)
A._settings_path = lambda: Path(_cfg) / "DSES_Analyzer" / "settings.ini"
A._front_messagebox = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok
for _n in ("information", "question", "warning", "critical"):
    setattr(QtWidgets.QMessageBox, _n, staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok))
A.find_all_radios = lambda: []
qapp = QtWidgets.QApplication(sys.argv)
QtWidgets.QApplication.setApplicationName("DSES_Analyzer")
tb = A.dses_workbench()
tb.start(); tb.flowgraph_started.set(); tb.show()
ok(tb._gain_stage_widget is None and tb._gain_stage_spins == {}, "no stage row in playback / single-gain radios")


def pump(s=0.15):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 30)


# hand the window a staged "HackRF": what __init__ does for a live one
src = make_source(); src.set_gain(47)
tb._playback_mode = False
tb._source = src
tb._gain_win._spin.setRange(0.0, 102.0)
tb._build_gain_stage_row(src); pump()
sp = tb._gain_stage_spins
ok(set(sp) == {"AMP", "LNA", "VGA"} and tb._gain_stage_widget is not None, "stage row not built")
ok((sp["LNA"].value(), sp["VGA"].value(), sp["AMP"].value()) == (40.0, 6.0, 0.0), "boxes should show LNA 40 / VGA 6 / AMP 0")
ok(sp["LNA"].singleStep() == 8.0 and sp["VGA"].singleStep() == 2.0 and sp["AMP"].maximum() == 14.0, "box steps/ranges")
ok(tb.gain == 46.0, f"slider should read the applied sum 46, got {tb.gain}")
# a box edit sets that stage alone and moves the slider to the sum
sp["VGA"].setValue(26); pump()
ok(src.stage_gain("VGA") == 26.0 and src.stage_gain("LNA") == 40.0 and tb.gain == 66.0
   and abs(tb._gain_win._spin.value() - 66.0) < 1e-6, f"VGA box -> 26: {src._stage_values}, slider {tb.gain}")
sp["LNA"].setValue(16); pump()                    # a deliberate odd split: LNA 16 / VGA 26
ok(src.stage_gain("LNA") == 16.0 and src.stage_gain("VGA") == 26.0 and tb.gain == 42.0, "LNA box -> 16 must not be reshuffled")
sp["AMP"].setValue(14); pump()
ok(src.stage_gain("AMP") == 14.0 and tb.gain == 42.0 and src.total_gain() == 56.0, "AMP is outside the slider")
# a restart re-applying the saved slider value leaves the split alone
tb.set_gain(42.0); pump()
ok((src.stage_gain("LNA"), src.stage_gain("VGA")) == (16.0, 26.0), "re-applying the same slider value reshuffled the stages")
# moving the slider refills LNA first and updates the boxes
tb.set_gain(66.0); pump()
ok((sp["LNA"].value(), sp["VGA"].value(), sp["AMP"].value()) == (40.0, 26.0, 14.0) and tb.gain == 66.0,
   f"slider 66 -> boxes {[s.value() for s in sp.values()]}")
# saved, and restored into a fresh radio
st = tb._app_settings
ok(st.get_float_or('rx', 'gain_stage_hackrf_lna', None) == 40.0 and st.get_float_or('rx', 'gain_stage_hackrf_vga', None) == 26.0
   and st.get_float_or('rx', 'gain_stage_hackrf_amp', None) == 14.0, "stage values not saved")
sp["LNA"].setValue(24); pump()                    # save an odd split, then "restart"
src2 = make_source(); src2.set_gain(st.get_float('rx', 'gain_db'))
tb._source = src2
tb._gain_stage_widget.setParent(None); tb._gain_stage_spins = {}
tb._build_gain_stage_row(src2); pump()
ok((src2.stage_gain("LNA"), src2.stage_gain("VGA"), src2.stage_gain("AMP")) == (24.0, 26.0, 14.0),
   f"restart did not restore the saved split: {src2._stage_values}")
print("4. Radio panel row OK: boxes, box edit moves the slider without reshuffling, slider refills LNA first, saved and restored")

tb._source = None; tb._playback_mode = True
tb.stop(); tb.wait(); tb.close()
print(f"\nALL GAIN-STAGE TESTS PASSED ({checks} checks)")
