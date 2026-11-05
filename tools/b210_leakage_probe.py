"""How strong is the B210's internal TX->RX leakage at MINIMUM TX gain — the
path the Self Test injects its pulses through? (2026-11-05: the Self Test
went marginal on two units at once; this measures the actual level.)

One process, like the Self Test: usrp_source on RX2-A and usrp_sink on
TX/RX-A at the same centre, TX a CW tone at +fs/8 (amplitude --amp) at the
minimum TX gain, and read the RX spectrum at the tone bin against the floor
with the tone on and with the TX muted (amplitude 0).

  PATH=.conda/Library/bin:$PATH UHD_IMAGES_DIR=... .conda/python.exe tools/b210_leakage_probe.py [--freq 420e6] [--rate 2e6] [--rx-gain 40] [--tx-gain 0]
"""
import os, sys, time, argparse
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--freq", type=float, default=420e6)
ap.add_argument("--rate", type=float, default=2e6)
ap.add_argument("--rx-gain", type=float, default=40.0)
ap.add_argument("--tx-gain", type=float, default=0.0)
ap.add_argument("--amp", type=float, default=0.5)
ap.add_argument("--secs", type=float, default=3.0)
args = ap.parse_args()
import numpy as np
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
import dses_workbench as A
import dses_radio as R
from gnuradio import gr, blocks, uhd

devs = R.find_b200_uhd(); assert devs, "no B210"
serial = devs[0]["serial"]
src = R.UhdB200Source(serial=serial, samp_rate=args.rate, center_freq=args.freq, gain=args.rx_gain, antenna="RX2")
n = 4096
chan = A.DisplayChannelizer(n, A.WINDOWS["blackman-harris"](n))
tb = gr.top_block()
tb.connect(src.block, chan.head); chan.connect_chain(tb)

tx = uhd.usrp_sink(f"serial={serial}", uhd.stream_args(cpu_format="fc32", channels=[0]))
tx.set_samp_rate(args.rate); tx.set_center_freq(args.freq, 0); tx.set_antenna("TX/RX", 0); tx.set_gain(args.tx_gain, 0)
k = n // 8
t = np.arange(1 << 18)
tone = (args.amp * np.exp(2j * np.pi * (k / n) * t)).astype(np.complex64)   # bin-centred at +fs/8
vec = blocks.vector_source_c(tone.tolist(), True)
vec.set_min_output_buffer(1 << 20)
tb.connect(vec, tx)


def measure(label):
    chan.accumulator.flush()
    time.sleep(0.5); chan.accumulator.take()
    time.sleep(args.secs)
    s, _, _, c = chan.accumulator.take()
    p = s / max(1, c)
    db = 10 * np.log10(p + 1e-30)
    c0 = n // 2
    tone_db = db[c0 + k - 2: c0 + k + 3].max()
    floor = np.median(np.r_[db[c0 + 200: c0 + k - 50], db[c0 + k + 50: c0 + n // 2 - 200]])
    print(f"{label}: tone bin {tone_db:.1f} dB, floor {floor:.1f} dB -> tone {tone_db - floor:+.1f} dB above floor "
          f"({c} blocks); DC bin {db[c0]:.1f} dB")
    return tone_db - floor


tb.start()
time.sleep(1.0)
on = measure(f"TX ON  (amp {args.amp}, TX gain {args.tx_gain} dB, RX gain {args.rx_gain} dB, {args.freq/1e6:g} MHz)")
tb.lock(); tb.disconnect(vec, tx); tb.unlock()
time.sleep(0.5)
off = measure("TX OFF (sink unplugged)")
tb.stop(); tb.wait()
print(f"LEAKAGE: tone {on:+.1f} dB above the RX floor with TX on, {off:+.1f} dB with TX off  (B210 {serial})")
