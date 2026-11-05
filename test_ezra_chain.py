"""Tests for the 1.7 compiled ezRA recorder (ezra_txt.EzraChain:
stream_to_vector -> fft_vcc (no window) -> |.|^2 -> integrate_ff(sub) ->
EzraRowSink), against the Python EzraIntegrator it replaces.

  1. sub_integration picks a divisor of integ_frames spanning <= 0.4 s.
  2. Numerics: rows written by the chain equal EzraIntegrator's rows (same
     I/Q, same keep fraction) to single-precision rounding, in linear power
     and in the written dB; row count exact for whole rows.
  3. The written file: header (freqMin/freqMax/binQty) identical to the
     legacy sink's; dB values agree to < 0.001 dB.
  4. Splice / unsplice on a running flowgraph (as the app does).

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_ezra_chain.py
"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from gnuradio import gr, blocks
import ezra_txt as ez

SITE = dict(lat_deg=38.8, lon_deg=-104.7, amsl=1800, site_name="chain-test",
            az_deg=180.0, el_deg=5.0, gain_text="40")

# --- 1. sub-integration choice --------------------------------------------
for integ, fps, want_max in ((31000, 8e6 / 4096, 0.4), (31000, 2e6 / 4096, 0.4), (40, 2e6 / 4096, 0.4), (7, 1000.0, 0.4)):
    sub = ez.sub_integration(integ, fps)
    assert integ % sub == 0 and sub / fps <= want_max + 1e-9, (integ, fps, sub)
print(f"1. sub_integration OK: 31000 @ 8 MS/s -> {ez.sub_integration(31000, 8e6/4096)} frames "
      f"({ez.sub_integration(31000, 8e6/4096)*4096/8e6:.2f} s), 31000 @ 2 MS/s -> {ez.sub_integration(31000, 2e6/4096)}, "
      f"prime 7 -> {ez.sub_integration(7, 1000.0)}")


def run_chain(iq, *, bins, integ, keep, rate):
    tmp = tempfile.mktemp(suffix=".txt")
    chain = ez.EzraChain(tmp, fft_bins=bins, integ_frames=integ, samp_rate=rate,
                         center_freq_mhz=1422.0, keep_fraction=keep, **SITE)
    src = blocks.vector_source_c(iq.tolist(), False)
    tb = gr.top_block()
    chain.connect(tb, src)
    tb.run()
    info = chain.close()
    return tmp, chain, info


def read_rows(path):
    rows = []
    hdr = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) > 10 and ("T" in parts[0]):
                rows.append(np.array(parts[1:], dtype=np.float64))
            else:
                hdr.append(line.rstrip())
    return hdr, np.array(rows)


rng = np.random.default_rng(3)
BINS, INTEG, KEEP, RATE = 4096, 40, 0.8, 8e6
n_rows = 3
iq = ((rng.standard_normal(BINS * INTEG * n_rows) + 1j * rng.standard_normal(BINS * INTEG * n_rows)) * 0.05).astype(np.complex64)
# a narrow "line" so the rows have structure
iq += (0.02 * np.exp(2j * np.pi * 0.1 * np.arange(iq.size))).astype(np.complex64)

# --- 2. numerics vs EzraIntegrator ------------------------------------------
ref_rows = []
integ_ref = ez.EzraIntegrator(fft_bins=BINS, integ_frames=INTEG, keep_fraction=KEEP,
                              on_row=lambda r: ref_rows.append(np.array(r)))
integ_ref.push(iq)
path, chain, info = run_chain(iq, bins=BINS, integ=INTEG, keep=KEEP, rate=RATE)
assert chain.sub > 1 and INTEG % chain.sub == 0, chain.sub
assert info["nrows"] == n_rows == len(ref_rows), (info["nrows"], len(ref_rows))
hdr, rows_db = read_rows(path)
assert rows_db.shape == (n_rows, chain.kept_bins), rows_db.shape
ref_db = 10.0 * np.log10(np.array(ref_rows))
diff_db = np.abs(rows_db - np.round(ref_db, 4))
print(f"2. numerics OK: {n_rows} rows x {chain.kept_bins} bins, sub-integration {chain.sub} frames; "
      f"written dB vs EzraIntegrator max diff {diff_db.max():.4f} dB (4-decimal rows)")
assert diff_db.max() <= 0.0011, diff_db.max()

# --- 3. header identical to the legacy sink's -------------------------------
tmp2 = tempfile.mktemp(suffix=".txt")
legacy = ez.EzraTxtSink(tmp2, fft_bins=BINS, integ_frames=INTEG, samp_rate=RATE,
                        center_freq_mhz=1422.0, keep_fraction=KEEP, **SITE)
src = blocks.vector_source_c(iq.tolist(), False)
tb = gr.top_block(); tb.connect(src, legacy); tb.run(); legacy.close()
hdr2, rows2 = read_rows(tmp2)
assert hdr == hdr2, (hdr, hdr2)
assert rows2.shape == rows_db.shape
d2 = np.abs(rows_db - rows2).max()
assert d2 <= 0.0011, d2
os.remove(path); os.remove(tmp2)
print(f"3. file OK: header identical to the legacy EzraTxtSink ({hdr[2]}); rows agree to {d2:.4f} dB")

# --- 4. splice / unsplice on a running flowgraph -----------------------------
tb = gr.top_block()
src = blocks.vector_source_c(iq.tolist(), True)
thr = blocks.throttle(gr.sizeof_gr_complex, 2e6, True)
null = blocks.null_sink(gr.sizeof_gr_complex)
tb.connect(src, thr, null)
tb.start(); time.sleep(0.2)
tmp3 = tempfile.mktemp(suffix=".txt")
chain = ez.EzraChain(tmp3, fft_bins=BINS, integ_frames=200, samp_rate=2e6,
                     center_freq_mhz=1420.406, keep_fraction=KEEP, **SITE)
tb.lock()
try:
    chain.connect(tb, thr)
finally:
    tb.unlock()
time.sleep(1.5)            # 200 frames x 2.048 ms = 0.41 s per row -> ~3 rows
tb.lock()
try:
    chain.disconnect(tb, thr)
finally:
    tb.unlock()
info = chain.close()
tb.stop(); tb.wait()
assert info["nrows"] >= 2, info
os.remove(tmp3)
print(f"4. splice/unsplice on a running flowgraph OK: {info['nrows']} rows in 1.5 s at 2 MS/s")

print("\nALL EZRA CHAIN TESTS PASSED")
