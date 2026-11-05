#!/usr/bin/env python3
"""
b210_bit.py -- B210 pulsar built-in test, bench harness.

One B210, one process, full duplex: TX plays the pulsar_sim waveform on a
seamless loop (vector_source repeat=True) out of TX/RX-A while RX records
RX2-A straight into a SIGPROC .fil via the app's own FilterbankSink. The
recording is then folded with PRESTO at the injected period/DM and graded
PASS/FAIL against the ground truth the waveform was built from.

Signal path options (same code either way):
  * NO CABLE (first try): TX-to-RX internal leakage inside the B210. TX
    gain stays at MINIMUM -- if leakage alone folds, the BIT needs no
    accessories at all.
  * CABLE: TX/RX-A -> 30 dB pad -> RX2-A for a controlled level.

TX gain defaults to 0 dB and the default frequency is 420 MHz -- nowhere
near the protected 1420 MHz band; keep it that way for radiated-leakage
hygiene (the roadmap rule).

Same-clock caveat: TX and RX share the B210's master clock, so this test
cannot see oscillator error between source and receiver -- the recovered
period is exact by construction. That leg belongs to the E4438C precision
source (see ROADMAP "Validation tooling").

NOTE (2026-11-05): the in-app Self Test now measures the injection path
first (tools/b210_leakage_probe.py does the same standalone) and raises the
TX gain in 10 dB steps to 40 dB until the tone stands 30 dB above the RX
floor; this harness takes an explicit --tx-gain (default 0, the August
assumption, which no longer injects a usable signal on either lab unit).

Usage (from the project root, .conda env):
    python tools/b210_bit.py                     # 90 s leakage-path test
    python tools/b210_bit.py --duration 180 --amp 0.5
    python tools/b210_bit.py --skip-analysis     # record only
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np


def _ensure_uhd_images():
    """Point UHD_IMAGES_DIR at a dir that has the B200 firmware if the env
    doesn't already. The project .conda env ships UHD without images; a
    radioconda install has them. Without this, a cold (just-powered) B210
    can't be firmware-loaded and discovery finds nothing."""
    if os.environ.get("UHD_IMAGES_DIR"):
        return
    home = Path.home()
    for cand in (Path(sys.prefix) / "Library" / "share" / "uhd" / "images",
                 Path(sys.prefix) / "share" / "uhd" / "images",
                 home / "radioconda" / "Library" / "share" / "uhd" / "images",
                 home / "radioconda" / "share" / "uhd" / "images",
                 Path("C:/ProgramData/radioconda/Library/share/uhd/images")):
        if (cand / "usrp_b200_fw.hex").is_file():
            os.environ["UHD_IMAGES_DIR"] = str(cand)
            return


_ensure_uhd_images()

from gnuradio import blocks, gr, uhd

import pulsar_sim as ps
import sigproc_fil as sf


def _addr_to_dict(a):
    """UHD device_addr -> plain dict (same fallbacks as the app's)."""
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


def find_b210_serial():
    """Serial of the single attached B200-family device, or None."""
    try:
        addrs = uhd.find('type=b200')
        serials = sorted({_addr_to_dict(a).get("serial", "")
                          for a in addrs} - {""})
        if len(serials) == 1:
            return serials[0]
        if len(serials) > 1:
            print(f"Multiple B2xx devices found ({', '.join(serials)}); "
                  f"pick one with --serial.", file=sys.stderr)
    except Exception as e:
        print(f"Device discovery failed: {e}", file=sys.stderr)
    return None


class BitFlowgraph(gr.top_block):
    """TX loop + RX .fil recorder on one B210."""

    def __init__(self, serial, iq, samp_rate, center_freq, tx_gain, rx_gain,
                 fil_path, nchans, source_name):
        gr.top_block.__init__(self, "b210_bit")

        addr = f"serial={serial}" if serial else ""

        self.tx = uhd.usrp_sink(
            addr,
            uhd.stream_args(cpu_format="fc32", channels=[0]),
        )
        self.tx.set_samp_rate(samp_rate)
        self.tx.set_center_freq(center_freq, 0)
        self.tx.set_antenna("TX/RX", 0)
        self.tx.set_gain(tx_gain, 0)

        self.rx = uhd.usrp_source(
            addr,
            uhd.stream_args(
                cpu_format="fc32",
                args="recv_frame_size=8192,num_recv_frames=1024",
                channels=[0],
            ),
        )
        self.rx.set_min_output_buffer(1 << 22)
        self.rx.set_samp_rate(samp_rate)
        self.rx.set_center_freq(center_freq, 0)
        self.rx.set_antenna("RX2", 0)   # TX owns the shared TX/RX-A port
        self.rx.set_gain(rx_gain, 0)

        self.src = blocks.vector_source_c(iq.astype(np.complex64), True)
        self.sink = sf.FilterbankSink(
            str(fil_path), nchans=nchans, samp_rate=samp_rate,
            center_freq_mhz=center_freq / 1e6,
            tstart_mjd=sf.unix_to_mjd(time.time()),
            source_name=source_name)

        self.connect(self.src, self.tx)
        self.connect(self.rx, self.sink)


def main():
    ap = argparse.ArgumentParser(
        description="B210 pulsar built-in test (bench harness)")
    ap.add_argument("--serial", default=None, help="B210 serial (auto if one)")
    ap.add_argument("--freq", type=float, default=420e6,
                    help="center frequency, Hz (default 420 MHz -- keep away "
                         "from 1420 MHz)")
    ap.add_argument("--rate", type=float, default=2e6,
                    help="sample rate, Hz (default 2 MS/s)")
    ap.add_argument("--period", type=float, default=0.1,
                    help="pulse period, s (default 0.1)")
    ap.add_argument("--dm", type=float, default=50.0,
                    help="dispersion measure to inject (default 50: with the "
                         "default band this sweeps 11 ms against 2 ms pulses, "
                         "so the DM check has real leverage)")
    ap.add_argument("--duty", type=float, default=0.02,
                    help="pulse FWHM as a fraction of the period")
    ap.add_argument("--amp", type=float, default=0.5,
                    help="peak envelope amplitude, 0..1 of DAC full scale")
    ap.add_argument("--tx-gain", type=float, default=0.0,
                    help="TX gain, dB (default 0 = minimum; see --freq note)")
    ap.add_argument("--rx-gain", type=float, default=40.0,
                    help="RX gain, dB (default 40)")
    ap.add_argument("--duration", type=float, default=90.0,
                    help="recording length, seconds (default 90)")
    ap.add_argument("--nchans", type=int, default=128,
                    help=".fil channels (default 128)")
    ap.add_argument("--out", default=None,
                    help="output directory (default: DSES_SA_Recordings/"
                         "b210_bit under Documents)")
    ap.add_argument("--skip-analysis", action="store_true",
                    help="record only; no PRESTO fold")
    args = ap.parse_args()

    serial = args.serial or find_b210_serial()
    if serial is None:
        print("No B210 found. Connect one or pass --serial.", file=sys.stderr)
        return 2

    outdir = Path(args.out) if args.out else (
        Path.home() / "Documents" / "DSES_SA_Recordings" / "b210_bit")
    outdir.mkdir(parents=True, exist_ok=True)

    iq, spec = ps.synth(args.rate, args.freq, args.period, args.dm,
                        duty=args.duty, n_periods=10, amplitude=args.amp,
                        noise_floor=0.0, seed=2026)
    print(f"DM resolution of this geometry: +/-{spec.dm_resolution:.1f}")
    print("Injected:", spec.describe())

    stamp = time.strftime("%Y%m%d_%H%M%S")
    fil_path = outdir / f"B210BIT_{stamp}.fil"

    tb = BitFlowgraph(serial, iq, args.rate, args.freq, args.tx_gain,
                      args.rx_gain, fil_path, args.nchans, "B210BIT")
    print(f"B210 s/n {serial}: TX/RX-A -> loop TX @ {args.tx_gain:g} dB, "
          f"RX2-A @ {args.rx_gain:g} dB, {args.freq / 1e6:g} MHz, "
          f"{args.rate / 1e6:g} MS/s")
    print(f"Recording {args.duration:g} s -> {fil_path}")

    tb.start()
    try:
        t_end = time.time() + args.duration
        while time.time() < t_end:
            time.sleep(1.0)
            left = max(0.0, t_end - time.time())
            print(f"\r  {left:5.0f} s left, gap events "
                  f"{tb.sink.gap_events}", end="", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted -- keeping what was recorded.")
    finally:
        tb.stop()
        tb.wait()
        info = tb.sink.close()
        print()
        if info:
            print("Recorder:", info)

    size_mb = fil_path.stat().st_size / 1e6
    print(f"Wrote {fil_path} ({size_mb:.1f} MB), "
          f"gap events {tb.sink.gap_events}, "
          f"padded samples {tb.sink.gap_samples}")

    if args.skip_analysis:
        return 0

    import fold_analysis
    print("\nFolding with PRESTO at the injected P/DM (searches around "
          "both)...")
    try:
        res = fold_analysis.analyze_fil(
            fil_path, source_name="B210BIT",
            fold_p_s=spec.period_s, fold_dm=spec.dm,
            progress=lambda m: print("  " + m))
    except fold_analysis.PrestoUnavailable as e:
        print(f"PRESTO unavailable: {e}", file=sys.stderr)
        return 3

    print(f"\nVerdict: {res.get('verdict')} | chi2_red "
          f"{res.get('chi2_red')} | P {res.get('best_p_s')} s | "
          f"DM {res.get('best_dm')}")
    ok, checks = ps.grade(spec, res)
    print("\nBUILT-IN TEST " + ("PASS" if ok else "FAIL"))
    for c in checks:
        print("  " + c)
    pdfs = sorted(fil_path.parent.glob(fil_path.stem + "_prepfold*.pdf"))
    if pdfs:
        print(f"\nFold report: {pdfs[-1]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
