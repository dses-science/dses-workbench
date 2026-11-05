"""Pulsar .fil recorder keep-up bench (2026-11-04, for the Pi 5 question).

A repeating 1M-sample noise buffer (cheap: GNU Radio's Gaussian noise source
costs a whole Pi 5 core at 16 MS/s) -> throttle(rate) -> one of:
  app     the 1.7 recorder as the application wires it: sigproc_fil.
          FilterbankChain (stream_to_vector -> fft_vcc -> |.|^2 [-> integrate_ff]
          -> FilterbankSpectraSink, rows written in Python)
  legacy  the 1.1.8-1.6.x FilterbankSink (numpy channelizer on a worker thread)
  raw     the bare compiled chain into a file_sink (lower bound: no Python at all)
writing to the real recordings disk. Reports process CPU (of ncores x 100),
samples padded/lost, and bytes on disk versus expected. Geometry presets:
b0329 (16 MS/s, 2044 ch, integrate 1 = the 28-sigma Haswell recipe) and uhf
(20 MS/s, 256 ch, integrate 16 = the 410-430 MHz Haswell recipe).

  python tools/fil_load_bench.py --preset b0329 --engine app --secs 20 --out ~/Documents/DSES_SA_Recordings
("python" and "cpp" are accepted as aliases of legacy and raw.)
"""
import os, sys, time, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--preset", default="b0329", choices=["b0329", "uhf"])
ap.add_argument("--engine", default="app", choices=["app", "legacy", "raw", "python", "cpp"])
ap.add_argument("--secs", type=float, default=20)
ap.add_argument("--out", default=os.environ.get("TEMP", "/tmp"))
ap.add_argument("--cores", type=int, default=0)
ap.add_argument("--vecbuf", type=int, default=0,
                help="raw engine: min output buffer (vectors) on the chain; 0 = GR default")
args = ap.parse_args()
args.engine = {"python": "legacy", "cpp": "raw"}.get(args.engine, args.engine)
if args.cores and sys.platform != "win32":
    os.sched_setaffinity(0, range(args.cores))
import numpy as np
from gnuradio import gr, blocks, fft as grfft
import sigproc_fil

geom = {"b0329": (16e6, 2044, 1), "uhf": (20e6, 256, 16)}[args.preset]
rate, nchans, integ = geom
rng = np.random.default_rng(7)
buf = (rng.standard_normal(1 << 20) + 1j * rng.standard_normal(1 << 20)).astype(np.complex64) * 0.05
src = blocks.vector_source_c(buf.tolist(), True)
thr = blocks.throttle(gr.sizeof_gr_complex, rate, True)
probe = blocks.probe_rate(gr.sizeof_gr_complex, 500.0, 0.15)
path = os.path.join(args.out, f"fil_bench_{args.preset}_{args.engine}.fil")
tb = gr.top_block()
tb.connect(src, thr)
tb.connect(thr, probe)
sink = None
if args.engine == "legacy":
    sink = sigproc_fil.FilterbankSink(path, nchans=nchans, samp_rate=rate, center_freq_mhz=1420.406,
                                      tstart_mjd=sigproc_fil.unix_to_mjd(time.time()), integrate=integ,
                                      source_name="bench")
    tb.connect(thr, sink)
elif args.engine == "app":
    sink = sigproc_fil.FilterbankChain(path, nchans=nchans, samp_rate=rate, center_freq_mhz=1420.406,
                                       tstart_mjd=sigproc_fil.unix_to_mjd(time.time()), integrate=integ,
                                       source_name="bench")
    sink.connect(tb, thr)
else:
    s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, nchans)
    fftb = grfft.fft_vcc(nchans, True, np.hanning(nchans).tolist(), True, 1)
    mag2 = blocks.complex_to_mag_squared(nchans)
    fsink = blocks.file_sink(gr.sizeof_float * nchans, path, False)
    if args.vecbuf:
        for b in (s2v, fftb, mag2):
            b.set_min_output_buffer(args.vecbuf)
    if integ > 1:
        integb = blocks.integrate_ff(integ, nchans)
        tb.connect(thr, s2v, fftb, mag2, integb, fsink)
    else:
        tb.connect(thr, s2v, fftb, mag2, fsink)
c0 = time.process_time(); t0 = time.perf_counter()
tb.start(); time.sleep(args.secs); tb.stop(); tb.wait()
if sink is not None:
    sink.close()
else:
    fsink.close()
wall = time.perf_counter() - t0
cpu = (time.process_time() - c0) / wall * 100
delivered = probe.rate()
size = os.path.getsize(path)
expected = 4.0 * rate * wall / integ
print(f"=== {args.preset} {args.engine} {rate/1e6:g} MS/s {nchans} ch x{integ} cores={args.cores or 'all'}: "
      f"delivered {delivered/1e6:.2f} MS/s, cpu {cpu:.0f}%, file {size/1e6:.0f} MB = {size/expected*100:.0f}% of expected")
if sink is not None:
    print(f"    padded(lost) samples {sink.queue_padded_samples} = {sink.queue_padded_samples/(rate*wall)*100:.1f}% of stream, "
          f"gap events {sink.gap_events}, timebase_broken {sink.timebase_broken}, rows {sink.nrows}")
for p in (path, path + ".gaps.json"):
    try:
        os.remove(p)
    except OSError:
        pass        # Windows keeps the file_sink handle until exit; harmless
