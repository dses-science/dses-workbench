"""Tests for the 1.7 compiled .fil recorder (sigproc_fil.FilterbankChain:
stream_to_vector -> fft_vcc -> |.|^2 [-> integrate_ff] -> FilterbankSpectraSink).

  1. Numerics: rows written by the chain match channelize_detect() (the
     byte-validated numpy channelizer behind FilterbankSink / iq_to_fil) to
     single-precision rounding, at integrate 1 and 16, and the header is
     byte-identical to the legacy writer's.
  2. Gap padding in rows: an rx_time overflow tag injected upstream arrives
     at the sink scaled to row units; the gap is measured against the time
     reference and zero-padded to within one row; the .gaps.json sidecar
     records it. Untagged streams write exactly the data; the per-event cap
     and the jitter threshold behave as in FilterbankSink.
  3. Splice/unsplice on a running flowgraph (lock/unlock), as the app does.

Run: PATH=.conda/Library/bin:$PATH .conda/python.exe test_fil_chain.py
"""
import os, sys, json, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from gnuradio import gr, blocks
import pmt
import sigproc_fil as sf


def rx_time_tag(offset, seconds):
    t = gr.tag_t()
    t.offset = offset
    t.key = pmt.intern("rx_time")
    frac, full = np.modf(seconds)
    t.value = pmt.make_tuple(pmt.from_uint64(int(full)), pmt.from_double(float(frac)))
    return t


def run_chain(data, tags, samp_rate=1e5, nchans=64, integrate=1, batch_rows=1, **kw):
    """batch_rows=1 so a finite stream writes EVERY row (exact counts); the
    live recorder's default batch (32) drops the final partial batch, which
    test 4 covers separately."""
    tmp = tempfile.mktemp(suffix=".fil")
    src = blocks.vector_source_c(data.tolist(), False, 1, tags)
    chain = sf.FilterbankChain(tmp, nchans=nchans, samp_rate=samp_rate,
                               center_freq_mhz=420.0, tstart_mjd=60000.0,
                               integrate=integrate, source_name="chain-test",
                               batch_rows=batch_rows, **kw)
    tb = gr.top_block()
    chain.connect(tb, src)
    tb.run()
    info = chain.close()
    return tmp, chain, info


def read_rows(path):
    hdr = sf.read_sigproc_header(path) if hasattr(sf, "read_sigproc_header") else None
    with open(path, "rb") as f:
        raw = f.read()
    # locate the end of the header the way the module's reader does
    end = raw.index(b"HEADER_END") + len(b"HEADER_END")
    nch = int(np.frombuffer(raw[raw.index(b"nchans") + 6: raw.index(b"nchans") + 10], "<i4")[0])
    data = np.frombuffer(raw[end:], dtype="<f4").reshape(-1, nch)
    return raw[:end], data[:, ::-1]          # back to LOW->HIGH


rng = np.random.default_rng(7)
RATE, NCH = 1e5, 64

# --- 1. numerics vs the numpy channelizer -----------------------------------
for nchans, integ in ((256, 1), (2044, 1), (256, 16)):
    n_in = nchans * integ * 40
    iq = ((rng.standard_normal(n_in) + 1j * rng.standard_normal(n_in)) * 0.05).astype(np.complex64)
    iq[: nchans * 3] += 0.5 * np.exp(2j * np.pi * (nchans // 5) / nchans * np.arange(nchans * 3))
    path, chain, info = run_chain(iq, [], samp_rate=16e6, nchans=nchans, integrate=integ)
    hdr_chain, rows = read_rows(path)
    ref = sf.channelize_detect(iq, nchans, window=True)
    if integ > 1:
        nout = ref.shape[0] // integ
        ref = ref[: nout * integ].reshape(nout, integ, nchans).sum(axis=1)
    assert rows.shape == ref.shape, (rows.shape, ref.shape)
    rel = np.abs(rows - ref) / (np.abs(ref) + 1e-3 * ref.mean())
    # legacy writer for the header comparison
    legacy = tempfile.mktemp(suffix=".fil")
    w = sf.FilterbankWriter(legacy, nchans=nchans, samp_rate=16e6, center_freq_mhz=420.0,
                            tstart_mjd=60000.0, integrate=integ, source_name="chain-test")
    w.push(iq); w.close()
    hdr_legacy, rows_legacy = read_rows(legacy)
    # rawdatafile (the file's own name) differs between the two temp files; strip it
    def _strip(h):
        i = h.index(b"rawdatafile")
        return h[:i]
    assert _strip(hdr_chain) == _strip(hdr_legacy), "header differs from the legacy writer"
    assert rows_legacy.shape == rows.shape
    # FFTW (chain) vs pocketfft (numpy path), both single precision: a few
    # 1e-5 relative at the very most (float32 eps x sqrt(N) accumulation).
    assert rel.max() < 1e-4, f"nchans {nchans} x{integ}: max rel diff {rel.max():.2e}"
    os.remove(path); os.remove(legacy)
    print(f"1. numerics OK: {nchans} ch x{integ}: {rows.shape[0]} rows, rel diff vs numpy "
          f"channelizer max {rel.max():.1e} / rms {np.sqrt(np.mean(rel**2)):.1e}, "
          f"header identical to the legacy writer")

# --- 2a. one 0.5 s gap at input sample 50_000 -------------------------------
data = (rng.standard_normal(100_000) + 1j * rng.standard_normal(100_000)).astype(np.complex64)
tags = [rx_time_tag(0, 1000.0),
        rx_time_tag(50_000, 1000.0 + 50_000 / RATE + 0.5)]
path, chain, info = run_chain(data, tags)
row_samples = NCH
gap_rows_true = 0.5 * RATE / row_samples             # 781.25 rows of missing time
# Row resolution: the tag lands on a whole row (floor of 781.25) and the pad
# is a whole number of rows, so the file can be off by up to one row (64
# samples = 0.64 ms here) from the exact 50_000-sample gap — never more.
assert abs(info["ntime"] - (100_000 / NCH + gap_rows_true)) <= 1.0, (info["ntime"], gap_rows_true)
assert info["gap_events"] == 1, info
assert abs(info["gap_samples"] - 50_000) <= row_samples, info
sidecar = path + ".gaps.json"
assert os.path.exists(sidecar), "gaps sidecar missing"
side = json.load(open(sidecar))
assert abs(side["events"][0]["missing_samples"] - 50_000) <= row_samples
assert not side["timebase_broken"]
hdr, rows = read_rows(path)
# the padded rows are exactly zero and sit where the gap was
gap_row0 = 50_000 // NCH
pad_rows = info["gap_samples"] // row_samples
assert np.all(rows[gap_row0: gap_row0 + pad_rows] == 0.0), "pad rows are not zero / not at the gap"
assert rows[gap_row0 - 1].sum() > 0 and rows[gap_row0 + pad_rows].sum() > 0, "data rows around the pad missing"
os.remove(path); os.remove(sidecar)
print(f"2a. single gap OK: {info['ntime']} rows = data + {pad_rows} zero rows "
      f"({info['gap_samples']} samples padded for a 50000-sample gap, within one row), sidecar written")

# --- 2b. no tags -> exactly the data, no sidecar ----------------------------
path, chain, info = run_chain(data, [])
assert info["ntime"] == 100_000 // NCH and info["gap_events"] == 0
assert not os.path.exists(path + ".gaps.json")
os.remove(path)
print("2b. untagged stream (Soapy-style source) OK: data only, no sidecar")

# --- 2c. per-event cap + truncation flag ------------------------------------
tags = [rx_time_tag(0, 0.0), rx_time_tag(50_000, 50_000 / RATE + 20.0)]
path, chain, info = run_chain(data, tags)
assert abs(info["gap_samples"] - int(10.0 * RATE)) <= row_samples, info
side = json.load(open(path + ".gaps.json"))
assert side["events"][0]["truncated"] is True
os.remove(path); os.remove(path + ".gaps.json")
print("2c. per-event pad cap + truncated flag OK")

# --- 2d. jitter below one row -> ignored ------------------------------------
tags = [rx_time_tag(0, 0.0), rx_time_tag(50_000, 50_000 / RATE + 0.00005)]
path, chain, info = run_chain(data, tags)
assert info["gap_events"] == 0 and info["ntime"] == 100_000 // NCH
assert not os.path.exists(path + ".gaps.json")
os.remove(path)
print("2d. sub-row jitter ignored OK")

# --- 2e. two gaps: rounding does not accumulate (both measured vs the reference)
tags = [rx_time_tag(0, 0.0),
        rx_time_tag(30_000, 30_000 / RATE + 0.3),
        rx_time_tag(60_000, 60_000 / RATE + 0.3 + 0.7)]
path, chain, info = run_chain(data, tags)
assert info["gap_events"] == 2
assert abs(info["gap_samples"] - 100_000) <= row_samples, info   # total 1.0 s, within one row
os.remove(path); os.remove(path + ".gaps.json")
print(f"2e. two gaps OK: {info['gap_samples']} samples padded for 100000 missing (within one row)")

# --- 3. splice / unsplice on a running flowgraph -----------------------------
tb = gr.top_block()
src = blocks.vector_source_c(data.tolist(), True)
thr = blocks.throttle(gr.sizeof_gr_complex, 2e6, True)
null = blocks.null_sink(gr.sizeof_gr_complex)
tb.connect(src, thr, null)
tb.start(); time.sleep(0.2)
tmp = tempfile.mktemp(suffix=".fil")
chain = sf.FilterbankChain(tmp, nchans=256, samp_rate=2e6, center_freq_mhz=1420.0,
                           tstart_mjd=60000.0, source_name="splice")
tb.lock()
try:
    chain.connect(tb, thr)
finally:
    tb.unlock()
time.sleep(1.0)
tb.lock()
try:
    chain.disconnect(tb, thr)
finally:
    tb.unlock()
info = chain.close()
tb.stop(); tb.wait()
assert info["ntime"] > 2000, f"only {info['ntime']} rows in ~1 s at 2 MS/s / 256 ch (expect ~7800)"
os.remove(tmp)
print(f"3. splice/unsplice on a running flowgraph OK: {info['ntime']} rows in 1 s")

# --- 4. the live recorder's row batching: same rows, tail < batch dropped ---
n_in = 256 * 100
iq = ((rng.standard_normal(n_in) + 1j * rng.standard_normal(n_in)) * 0.05).astype(np.complex64)
path, chain, info = run_chain(iq, [], samp_rate=2e6, nchans=256, batch_rows=32)
assert chain.sink.batch_rows == 32
hdr, rows = read_rows(path)
ref = sf.channelize_detect(iq, 256, window=True)
assert 0 <= ref.shape[0] - rows.shape[0] < 32, (rows.shape, ref.shape)
rel = np.abs(rows - ref[: rows.shape[0]]) / (np.abs(ref[: rows.shape[0]]) + 1e-3 * ref.mean())
assert rel.max() < 1e-4
# a gap with the batch on: still measured and padded within one row
tags = [rx_time_tag(0, 1000.0), rx_time_tag(12_800, 1000.0 + 12_800 / 2e6 + 0.05)]
path2, chain2, info2 = run_chain(iq, tags, samp_rate=2e6, nchans=256, batch_rows=32)
assert info2["gap_events"] == 1 and abs(info2["gap_samples"] - 0.05 * 2e6) <= 256, info2
os.remove(path); os.remove(path2); os.remove(path2 + ".gaps.json")
print(f"4. batched sink OK: {rows.shape[0]} of {ref.shape[0]} rows written (tail < 32 dropped at "
      f"stream end), rows identical, gap padded {info2['gap_samples']} of 100000 samples")

print("\nALL .FIL CHAIN TESTS PASSED")
