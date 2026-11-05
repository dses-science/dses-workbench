"""Fold the SAME synthetic pulsar capture written two ways through PRESTO and
compare the results — the acceptance check for moving the .fil channelizer
into the flowgraph (1.7, sigproc_fil.FilterbankChain) off the byte-validated
numpy channelizer (FilterbankWriter / FilterbankSink).

  legacy : FilterbankWriter.push(iq)                        (numpy channelizer)
  chain  : vector_source -> FilterbankChain (fft_vcc, C++)  (the 1.7 recorder)

Both files are folded with prepfold at the injected period/DM (no search)
via fold_analysis (WSL PRESTO on Windows, native elsewhere), and the row
data are compared directly as well.

  PATH=.conda/Library/bin:$PATH .conda/python.exe tools/fil_fold_compare.py [--secs 60] [--keep]
"""
import os, sys, time, argparse, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--secs", type=float, default=60.0)
ap.add_argument("--keep", action="store_true", help="keep the two .fil files + fold products")
ap.add_argument("--no-fold", action="store_true", help="only compare the written rows")
args = ap.parse_args()
import numpy as np
from gnuradio import gr, blocks
import sigproc_fil as sf
import pulsar_sim as ps

fs, fc = 2e6, 420e6
period, dm = 0.1, 50.0
iq, spec = ps.synth(fs, fc, period, dm, duty=0.02, n_periods=10,
                    amplitude=0.35, noise_floor=1.0, seed=11)
reps = int(np.ceil(args.secs * fs / spec.n_samples))
print(f"capture: {spec.describe()}  x{reps} -> {reps * spec.n_samples / fs:.0f} s at {fs/1e6:g} MS/s")
d = tempfile.mkdtemp(prefix="fil_fold_compare_")
tstart = sf.unix_to_mjd(time.time())
NCH = 128

# legacy: numpy channelizer
p_legacy = os.path.join(d, "SIM_legacy.fil")
w = sf.FilterbankWriter(p_legacy, nchans=NCH, samp_rate=fs, center_freq_mhz=fc / 1e6,
                        tstart_mjd=tstart, integrate=1, source_name="SIMBIT")
for _ in range(reps):
    w.push(iq)
info_l = w.close()

# chain: the 1.7 recorder fed by a vector source repeating the same buffer
p_chain = os.path.join(d, "SIM_chain.fil")
chain = sf.FilterbankChain(p_chain, nchans=NCH, samp_rate=fs, center_freq_mhz=fc / 1e6,
                           tstart_mjd=tstart, integrate=1, source_name="SIMBIT")
src = blocks.vector_source_c(np.tile(iq.astype(np.complex64), reps).tolist(), False)
tb = gr.top_block()
chain.connect(tb, src)
tb.run()
info_c = chain.close()
print(f"rows: legacy {info_l['ntime']}  chain {info_c['ntime']}")


def rows_of(path):
    with open(path, "rb") as f:
        raw = f.read()
    end = raw.index(b"HEADER_END") + len(b"HEADER_END")
    return raw[:end], np.frombuffer(raw[end:], dtype="<f4").reshape(-1, NCH)


h_l, r_l = rows_of(p_legacy)
h_c, r_c = rows_of(p_chain)
n = min(len(r_l), len(r_c))
rel = np.abs(r_l[:n] - r_c[:n]) / (np.abs(r_l[:n]) + 1e-3 * r_l.mean())
print(f"header identical: {h_l[:h_l.index(b'rawdatafile')] == h_c[:h_c.index(b'rawdatafile')]}")
print(f"row data: max rel diff {rel.max():.2e}, rms {np.sqrt(np.mean(rel**2)):.2e} over {n} rows")

if not args.no_fold:
    import fold_analysis
    results = {}
    for tag, path in (("legacy", p_legacy), ("chain", p_chain)):
        t0 = time.perf_counter()
        res = fold_analysis.analyze_fil(path, source_name="SIMBIT",
                                        fold_p_s=spec.period_s, fold_dm=spec.dm,
                                        progress=lambda m: None)
        results[tag] = res
        print(f"{tag:7s}: verdict {res.get('verdict')}  chi2_red {res.get('chi2_red')}  "
              f"sigma {res.get('sigma')}  P {res.get('best_p_s')}  DM {res.get('best_dm')}  "
              f"({time.perf_counter()-t0:.0f} s)")
    a, b = results["legacy"], results["chain"]
    for k in ("chi2_red", "sigma", "best_p_s", "best_dm"):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va:
            print(f"  {k:9s}: legacy {va:.6g}  chain {vb:.6g}  ratio {vb/va:.4f}")
if args.keep:
    print("kept:", d)
else:
    shutil.rmtree(d, ignore_errors=True)
