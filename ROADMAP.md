# DSES Radio Astronomy Workbench — Roadmap

Feature ideas and planned work, by target version. This file is the shared
cross-machine record (Mac + Windows) — keep it committed and pushed.

Conventions: `[ ]` planned, `[x]` shipped (note the commit), `[-]` dropped
(note why). Move items between versions freely until they ship.

## v1.6.1 — SHIPPED 2026-10-04 (cut + published from Windows, release commit 8d44a06, tag v1.6.1; Rick: "Fix the launcher and cut 1.6.1")

Bug-fix release (third digit); the program itself is unchanged from 1.6.0.

- [x] **Windows launcher could not start from a path containing a space**
      (found 2026-10-04 from Ray's report that his copy would no longer
      start). `Start-Process -ArgumentList` does not quote, so python got
      the program path cut at the first space and died in a minimized
      console nobody could read. launcher.ps1 now quotes arguments with
      whitespace and reports a program that stops within 10 s with a
      non-zero exit code (message box with the exit code and the two
      diagnostic lines). Tests: `test_launcher.py` (fails on 1.6.0).
- [x] **Linux desktop-entry template quotes Exec** (same class of fault for
      an install path with a space). Test: `test_desktop_entry.py` (GLib).
- [x] **Guide**: one permanent folder, not Downloads; the desktop shortcut
      is "(Recommended)" and the text says the program never creates it by
      itself; new troubleshooting entry "Windows: nothing happens when I
      start it". Release_Workflow 4.5: run the launcher test before a cut.

## v1.6.0 — SHIPPED 2026-09-27 (cut + published from Windows, release commit dc0a936, tag v1.6.0; Rick: "Release 1.6.0 now with what's on main. Ray needs it today.")

Feature release (second digit) per the 1.5.0 rule. Contents since v1.5.3:
the Drift-Scan Review quicklook (Observe menu; ff0ff56, its own ROADMAP
item below), the scheduled recording start below (dbd960f), full-precision
az/el in ezRA headers (f70f603 — the Pi re-enters az 359.9176 / el 87.4457
when it takes this release at a scan break), control docks limited to the
left/right columns (e8e6ed9), the patient WSL probe behind the automatic
PRESTO analysis (a44a0e5), the k0gd@cnssys.com contact address, and — from
the cut itself — build_doc.py's silent PDF build (Distiller first with a
bounded, automatic fallback to Microsoft Print to PDF; stale-makepy-cache
auto-heal) with tools/verify_pdf.py. zip sha256 `1cd1e447…` (64,619,739 bytes), sidecar,
guide PDF (cover 1.6.0, §9 gained Drift-Scan Review + Start at), manifest in
`sw_distribution/dses-workbench/`, legacy `b210_sa/manifest.json` rewritten;
36 forward-slash entries, one top folder, completeness guard, staging import
of 12 modules + shim; live verification through updater.py's own flow from
BOTH manifest URLs = PASS; 1.5.3 zip still 200.

- [x] **Recording: SCHEDULED START ("Start at") — landed 2026-09-26** (Rick's
      TODO, same day: "the recording has a duration setting but needs an
      optional start time, with a countdown to the start; the countdown to
      the finish is already there"). New `Start at:` row under `Record for:`
      with a UTC/Local selector; forms `HH:MM[:SS]` (next occurrence), `+30`
      / `+1:30` (delay), `YYYY-MM-DD HH:MM[:SS]`. Setting Record → Recording
      ARMS: amber status "Armed — recording starts at …", the counter slot
      shows an amber "⏱ starts in H:MM:SS (at …), then records … until …",
      the source-visibility question is evaluated AT the scheduled instant
      (`sets_before(unix_ts=)`; "below the mask at the scheduled start" when
      so) and the hydrogen-line question asked up front, so the 1 Hz fire
      (`_start_recording(scheduled=True)`) never asks anything; Stopped
      cancels; a fire with no radio resets Record to Stopped with a status
      note; the timed-recording controls lock while armed as while
      recording. Settings `[recording] rec_start` / `rec_start_tz`. A typo
      is refused with a dialog rather than starting now. Tests:
      test_rec_schedule.py (parser pinned against a fixed clock in UTC and
      local, 24 checks). Help + Installing.md §9 synced.

## v1.5.0 — SHIPPED 2026-09-11 (cut + published from Windows, release commit 6ed8d46, tag v1.5.0)

**Versioning from here (Rick, 2026-09-11):** feature release = second
digit, bug-fix release = third digit; the 1.3.x/1.4.x point releases
carried features while the program was still developmental.

- [x] **Pulsars in View: BEST BAND + unconstrained BEST F** (from Rick's
      hands-on pass of the 1.5.0 candidate, same day): the fastest-detection
      column was limited to the Tuning-preset bands — right when read as
      "the feeds the dish has", so it is now called **Best band** (same
      math), and a new **Best f** runs the same detection-time model with
      the feed list taken away: 5%-step log grid 100 MHz–6 GHz, golden-
      section refined, ~100 evaluations/row (0.2 ms; ~1 s whole catalog).
      One shared model (`_detect_time_fn`) so the Best f tooltip states the
      feed penalty: B0329+54 296 MHz vs 408 (1.2x), Crab 169 vs 408 (6.5x),
      Vela within 2%, DM-500 sources kept high by scattering, flat spectra
      run to the top of the range (flagged "as high as you can go"). The
      search stops at 100 MHz — flux power law and sky model untrusted
      below — and says so.
- [x] **Planner sky math** (IERS download off + closed form precessed):
      see the Backlog entry marked DONE 2026-09-11 below for the numbers.
- [x] **Pulsars in View: PLAN FOR A DATE AND TIME** (Rick's request
      2026-09-10; landed same day). The dialog computed everything for
      `time.time()`, so it could only answer "what is up while I stand
      here". New reference-time row: a `Plan for:` checkbox + date/time box
      (calendar popup) read as **UTC or Local**, `-1 d / -1 h / +1 h / +1 d`
      steps, and a `Now` button. Altitude, azimuth, time-above-mask, next
      window and the green viability highlight are all computed for the
      chosen instant (`visible_now(unix_ts=…)`, which already took one).
      Any date works, past included — point it at the start of an old
      recording to see what was overhead when the data was taken. Planned
      state is loud: window title, an amber `PLANNED …(+n h from now)`
      readout with the site's LST, "up then"/"viable then" wording, and a
      context comment line on copied tables. Refreshes are debounced 350 ms
      (a full recompute is 0.7-3.9 s for a 4,400-row catalog), and the box
      tracks the clock while it shows "now" (never while it has focus).
      New in pulsar_planner: `lst_hours`, `next_transit_h`, `max_alt_deg`.
- [x] **Pulsars in View: HEADER AND PER-CELL EXPLANATIONS** (same request).
      Every column header now carries help (was 2 of 11), from a single
      `PLANNER_COLUMNS` table so labels and help cannot drift apart, and
      **every individual cell explains its own value**: the catalog
      position behind a name, the source's culmination and next meridian
      crossing, the delay this DM produces across the tuned band and inside
      one channel (with a plain statement when the DM is unmeasurable
      there), which catalog anchors a flux estimate came from and its
      spectral index, every input to Min rec plus whether it fits the time
      left, Best f against the current tuning, the clock time the source
      crosses the mask, and when a below-mask window opens. Built lazily by
      `_TipItem.data(ToolTipRole)` — ~50,000 strings would otherwise be
      built per refresh and nearly all thrown away. Tests:
      `test_pulsar_planner_dialog.py` (new, headless, 38 checks) and three
      sidereal cases in `test_pulsar_planner.py`.
- [x] At the cut (done 2026-09-11): APP_VERSION 1.5.0, Installing.md §9
      synced from the in-app Help, §3 link + folder names bumped, guide PDF
      rebuilt (cover 1.5.0), zip d50d9d1d… (63,865,251 bytes) published to
      sw_distribution/dses-workbench/ with the sidecar, guide and manifest,
      legacy b210_sa/manifest.json rewritten, live verification through
      updater.py's own flow PASS from both manifest URLs, tag v1.5.0.

## v1.1.8 — SHIPPED 2026-08-03 (cut + published from Windows)

**Release plan (Rick, 2026-08-02):** no point release for the two Ray UI
fixes — everything below ships together as 1.1.8 when the timebase and
ezRA work are done.

**Ray's hardware (for reference):** an old **HackRF** product, not a B210.
Implications: his "best-ever H-line" validation of 1.1.7 was on an 8-bit
zero-IF SDR via the Soapy path; and the timebase/gap-padding design must
NOT assume UHD — gr-uhd emits precise `rx_time` tags on overflow (exact
gap length), while the Soapy path (HackRF/SDRplay/RTL) may only give us
overflow 'O' counts, so precise padding for UHD + best-effort counting/
flagging for Soapy sources.

- [x] **Ray's weak-signal A/B verification — CLOSED 2026-08-01 by field
      results:** on 1.1.7, Ray's morning data was "the best Hydrogen line
      measurements he has ever made" (via Rick). No further A/B needed; the
      1420.5/1418/1425.6 sig-gen ground truth stays on record above.

- [x] **Ray bug: RX gain slider clipped on his screen — FIXED 2026-08-02:**
      the sidebar scroll area forced its horizontal scrollbar OFF, so when
      the content minimum exceeded the viewport (bigger fonts / narrow
      window) the right edge clipped with no recourse. Policy is now
      AsNeeded — a scrollbar appears only when needed; verified no bar at
      normal widths. Ray's screen is the acceptance test. (Original
      symptom notes below.)
      Original report: the right end of
      the gain slider is cut off so he cannot drag to max gain. Likely a
      sidebar layout/minimum-width (or DPI/scaling) issue. Workaround NOW:
      type the value into the RX Gain spin box next to the slider. Fix:
      make sure the slider stretches/shrinks with the panel and the max is
      always reachable; test at small window sizes + 125/150% display
      scaling.

- [x] **Ray bug: waterfall doesn't track spectrum x-axis zoom/pan — FIXED
      2026-08-02:** waterfall ViewBox x-axis now linked to the spectrum
      plot's (`link_x_to` / pyqtgraph setXLink; the waterfall image was
      already in true frequency coordinates). Verified live on the B210:
      zooming the spectrum to 1.4185–1.4225 GHz moved the waterfall to the
      identical span with carrier stripes aligned under the spectrum
      peaks; retunes propagate through the link. (Original symptom notes
      below.)
      Original report: when
      the spectrum plot's horizontal scale changes (interactive zoom/pan),
      the waterfall keeps showing the full band, so rows no longer line up
      with the spectrum above — "useless in that case." Fix: link the
      waterfall ViewBox x-range to the spectrum plot's (pyqtgraph
      setXLink or an x-range-changed handler), so both views always show
      the same frequency span. Check the axis stays correct in Sweep mode
      and after retune/sample-rate changes.

- [x] **① Display sensitivity fix — integrate the full stream — SHIPPED in
      release 1.1.7 (2026-07-18, commit 0cf1648).** History:
      What shipped in the working tree: SampleBufferSink queues every chunk
      (bounded, drop-oldest, counted); keep_one_in_n target 20→400 vec/s
      (N=1 below ~26 MS/s); SpectrumProcessor Welch-averages every block per
      tick (scipy.fft batch, workers=-1; float64 accumulation); proactive
      per-tick CPU budget via a learned per-block cost (no GUI stalls, slow
      boxes shed coverage gracefully); max/min holds are now TRUE per-block
      peak/min detectors (a 51 µs burst reads full amplitude, not −27 dB);
      retune barrier (flush + one-drain blanking) keeps stale-frequency
      samples out of live view AND sweep tiles; sweep capture retries until
      a clean post-retune frame exists. Verified: 7-test suite in
      `test_display_sensitivity.py` (0 dBFS calibration exact; 34× noise-
      floor scatter reduction ≈ √N; barrier; budget cap; peak holds) +
      benchmarked 34–40 ms/tick at 26 MS/s for all FFT sizes (= stride 1,
      full coverage, on the dev box). Adversarially reviewed (15-agent
      workflow): 9 findings confirmed, all fixed. **Release note needed:**
      default averaging is now linear power, so the displayed noise floor
      reads ~+2.5 dB vs ≤1.1.6 (the old dB-domain average was biased low —
      this is a correction, not a regression).
      **GUI smoke test PASSED 2026-07-18 in playback mode** (B210 was NOT
      connected to the dev PC — plug it in for the live-RF leg): app
      launches, razor-flat Welch floor at 65536-pt/152.6 Hz RBW, max hold
      verified as a per-block peak detector with quantitatively correct
      order statistics (~+4 dB gap at ~15 blocks/tick vs ~+9 dB at ~9700),
      FFT-size change 65536→1024 instant and clean, GUI responsive
      throughout. (Dev note: launching outside the launcher needs
      UHD_IMAGES_DIR pointed at radioconda's images for live B210 use.)
      **LIVE B210 leg PASSED 2026-07-18** (serial 8003886, 1422 MHz,
      16 MS/s, RX2, 40 dB): razor-flat Welch floor at −116 dBm with sub-dB
      scatter, three weak narrowband signals clearly visible above it (the
      Ray demonstration), max hold ~+10 dB with correct order statistics,
      waterfall smooth, ~242% of one core used (64-core box → huge
      headroom; scipy workers spreading the batch FFT). Remaining for this
      item: only the A/B against other SDR software on Ray's exact
      scenario (need his app/settings/signal details).
      **SHIPPED in release 1.1.7 (2026-07-18, commit 0cf1648)** — cut
      fast-track (including the hold-detector toggle) so Ray can test
      tonight; the other roadmap items below continue toward a later
      release. Sig-gen ground truth from Rick's bench for the A/B: at
      1422 MHz center only the 1420.5 MHz line follows the generator
      on/off; 1418 and 1425.6 MHz are internal B210 spurs.
      Ray Uberecken (AA0L) reports (verbal)
      that in a different application the Workbench receives weak
      signals WORSE than other software on the SAME hardware.
      **PRIME SUSPECT FOUND (code inspection 2026-07-17):** the display FFT
      processes only the latest `fft_size` samples per timer tick
      (`_tick()` → `self._sink.latest(n)`) — at 20 MS/s / 1024-pt / ~30-60 Hz
      that is ~0.3% of the stream; the rest never reaches the display.
      Software that Welch-averages EVERY frame between screen updates shows
      a far deeper-averaged noise floor (up to ~18x lower sigma at 20 MS/s),
      which is exactly "weak signals better in other apps, same hardware."
      **Fix: accumulate/Welch-average all (or a sizable fraction of) the
      blocks since the last tick before the EMA.** NOTE: display-only — the
      .fil recording path processes every sample, so pulsar recordings and
      the B0950+08 noise limit are unaffected. Precision itself audited
      clean: display FFT is float64, linear-power averaging already default,
      ENBW-aware scaling; recording float32 is standard for 12-bit ADC data.
      Verify the fix (and rule out secondary causes) with an A/B vs
      SDR# / GQRX / SDRangel, one antenna + calibrated weak signal:
      - RF gain defaults / AGC: are we leaving front-end gain on the table?
      - Receive-chain config: antenna port selection, LNA path, bandwidth
        vs sample-rate filter rolloff at band edges.
      - FFT processing: window choice, FFT size vs RBW, averaging depth vs
        other apps' defaults.
      - **RX overflows**: dropped samples discard integration time — ties
        into the timebase item; heavy drops = real sensitivity loss.
      - Wire format sc8 vs sc16 on the USB link, DC-offset / IQ-balance
        correction settings.
      Get the exact scenario from Ray (app, mode, signal type, hardware,
      settings) and reproduce with a calibrated weak signal first.

- [x] **Field-analysis cluster (quick-look + auto post-processing +
      self-contained PDFs) — SHIPPED in 1.1.8 (2026-08-03):**
      one pipeline (`fold_analysis.py` + `fold_pdf.py`) serves all three:
      readfile sanity → rfifind mask → band-edge zap → catalog prepfold
      (or manual -p/-dm for magnetars/tests) → parse → verdict →
      self-contained PDF next to the `.fil` (chart + commands + numbers +
      plain-language verdict per the fold-PDF convention). PRESTO runs
      native (Mac/Linux) or via WSL (Windows, presto_bridge) with a
      graceful "not installed" path. App UI: "Analyze when done" checkbox
      (default on) + "Quick look" button (snapshots the growing file
      mid-recording, min 60 s), worker thread, results dialog with
      Open-PDF. **Verdict RFI guards encode the review lessons:** a
      catalog fold whose periodicity optimizes to DM≈0 reports
      TERRESTRIAL SIGNAL (verified live: the bench carrier produced
      χ²=17,275 and was correctly rejected); DM far from catalog →
      SUSPECT; "no detection ≠ bad recording" wording for weak sources.
      Tests: `test_fold_analysis.py` (synthetic 0.5-s pulsar → DETECTION
      χ²≈293 with 2-page PDF; snapshot truncation; data-check-only path) +
      live GUI round-trip on the B210 (record → auto-analysis → dialog →
      PDF). Bonus live proof: the recording's 54 real overflow gaps were
      rx_time-measured and padded (3.4 s) by the 1.1.8 timebase feature —
      the live tag round-trip we couldn't previously induce.
      **Follow-up found:** FilterbankSink still does inline DSP on the GR
      thread and overflows at 16 MS/s/2044ch (padding compensates, but it
      should get the ezRA-style worker-thread treatment — prevention over
      cure).
      Original item:
      Quick-look PRESTO analysis during a long recording — while a
      multi-hour recording runs, let the user (or a timer) trigger a draft
      PRESTO fold on the data captured so far WITHOUT interrupting the
      recording. Feasible because `FilterbankWriter` appends whole spectra
      and flushes, so the on-disk `.fil` is always valid up to the last
      complete spectrum, and SIGPROC headers don't encode sample count
      (PRESTO infers it from file size). Design sketch:
      1. Snapshot: copy the header + an integer number of complete spectra
         (`floor((size - hdr_len) / (nchans * nbits/8))`) to
         `<basename>_quicklook.fil` so PRESTO never reads a file mid-append.
      2. Run `readfile` + `prepfold -psr <source_name>` on the snapshot via
         `presto/presto_bridge.py` (WSL on Windows, native on Mac/Linux) in a
         low-priority background subprocess — recording thread untouched.
      3. Show the result in the recording panel: sigma / χ²_red readout and
         the prepfold plot (PNG/PDF), per the fold-PDF convention.
      Uses the source name + RA/Dec already wired into the header (1.1.6).
      Caveats: needs ~10+ min of data before a fold is meaningful; on the
      Pi 5, nice/ionice the PRESTO job to avoid UHD overflows; Windows
      requires the WSL PRESTO stack from `presto/build_presto.sh`.

- [x] **Pulsar visibility planner ("what's up now?") — BUILT 2026-08-04 for
      1.2.0** (`pulsar_planner.py`, Observe menu / Ctrl+P; ATNF psrcat
      cached locally, flux follows the tuned band, magnetars marked and
      never flux-filtered, exact catalog RA/Dec into the .fil header,
      set-before-finish warning). Original notes: a built-in subset of
      the Murmur/ATNF planning step from
      `DSES_PulsarGuide_Planning_2026.pdf` (Training Part 1):
      1. *In-view list on request:* a "Pulsars in view" button/dialog showing
         pulsars currently above the horizon at the observing site — name,
         RA/Dec, current az/el, P0, DM, S400 flux, and **time remaining above
         the elevation mask**. Selecting one fills the recording Source field
         (and gives exact catalog RA/Dec for the `.fil` header, upgrading the
         current approximate parse-from-name in `sigproc_fil.py`).
      2. *Availability warning:* when a Source + "record for" duration are
         set, warn at recording start (and live in the panel) if the pulsar
         sets below the mask before the recording would finish.
      Design notes:
      - Needs a new `[site]` settings group: lat/lon/elevation + minimum
        elevation mask (deg). Preset dropdown for known DSES sites (Haswell,
        home QTHs) plus custom entry.
      - Catalog: ATNF psrcat (internet assumed OK) — fetch via the psrcat web
        query or `psrqpy`, but CACHE the catalog locally (~few MB) so the
        field boxes work offline after first fetch; filter by dec reachable
        from the site.
      - Flux column must follow the tuned band: DSES records pulsars anywhere
        from 0.1–2 GHz (400 MHz and 1400 MHz bands most common), so sort by
        S400 or S1400 (or nearest available Sxxx) based on the current center
        frequency, not a hardcoded band.
      - Include **magnetars**: psrcat carries them (TYPE AXP/SGR) but flux
        fields are often sparse — don't let a flux filter silently hide them;
        consider a "show magnetars" toggle or a TYPE column, and the McGill
        Magnetar Catalog as a supplementary source if psrcat coverage proves
        too thin.

- [x] **One-click post-processing at end of recording — DONE, absorbed
      into the field-analysis cluster above (2026-08-02).** Original notes:
      PRESTO v6 + tempo2 will always demand expertise for *real* analysis,
      but the app can run a canned, reasonably-good pipeline automatically
      when a recording finishes (opt-in checkbox, e.g. "Analyze when done"),
      so the on-site team gets an immediate good/marginal/no-detection
      verdict. Pipeline = what we hand-ran for the Haswell validation:
      1. `readfile` sanity check (header parses, byte-exact spectra count).
      2. `rfifind` to build an RFI mask (this is the step field crews most
         often skip and most often need).
      3. `prepfold -psr <source>` with the mask (catalog fold; topocentric
         first — no par file or tempo2 knowledge required of the user; a
         `-par` bary fold via tempo2 as an "advanced" option).
      4. Results card in the app: detection sigma, χ²_red, best DM vs
         catalog DM, and the prepfold plot; PDF written next to the `.fil`
         per the fold-PDF convention (`<basename>_prepfold.pdf`).
      5. Verdict heuristic from sigma/χ² thresholds, with the caveat text
         explaining "no detection ≠ bad recording" for weak sources.
      Shares all infrastructure with the mid-recording quick-look item
      (snapshot not needed here — file is closed) and the visibility planner
      (source name + catalog params already known). Design notes:
      - Gate on PRESTO availability per platform: Windows → WSL bridge
        (`presto/presto_bridge.py`); Mac → `presto6` radioconda env; Linux
        site box → conda-forge `presto` v6 (linux-64). The current Pi 5 is
        aarch64 (no conda-forge build — would need a source build), but Rick
        plans to REPLACE the on-site Pi with a powerful multi-core x86-64
        Linux machine in the near future, where conda-forge v6 installs
        directly — so don't invest in an aarch64 build; just show a clear
        "PRESTO not installed" message when it's absent.
      - Long recordings → long folds: run niced in the background with
        progress + cancel; the app must stay usable (or start a new
        recording) while analysis runs.
      - Magnetars / sources without catalog ephemerides: offer a manual
        P0/DM entry or par-file picker instead of `-psr`.

- [x] **Self-contained fold PDFs — DONE, absorbed into the field-analysis
      cluster above (2026-08-02, fold_pdf.py).** Original notes:
      whenever a PRESTO chart is written to PDF (the auto post-processing
      item above, the quick-look, or a manual fold), the PDF must carry the
      interpretive commentary with it, not just the raw prepfold plot, so
      the results never have to be chased down "somewhere else". Contents:
      - Page 1: the prepfold chart as today.
      - A commentary page (or header block): recording metadata (source,
        site, center freq/BW, tsamp, duration, start MJD), the exact PRESTO
        commands run, a results table (sigma, χ²_red, best DM vs catalog DM,
        best P vs catalog P), and plain-language verdict + caveats — the
        kind of notes from the Haswell validation (e.g. "DM rails to 0 at
        20 MHz BW — narrow-band artifact, not RFI").
      - Implementation: no new dependency needed — PySide6 can render a
        QTextDocument to PDF (QPdfWriter) and append/merge with the chart;
        numbers parse from prepfold's `.bestprof`.
      - Same rule applies to agent-produced fold PDFs (see CLAUDE.md fold
        convention).

- [ ] **System-1 antenna-steering integration (preload pulsar target)** —
      *status: WAITING ON the System-1 team* (they own the antenna-steering
      software + hardware). Rick has asked them (2026-07) for an API so the
      Workbench can push the selected pulsar's data (name, RA/Dec,
      ideally the catalog ephemeris) into their steering software — the
      operator picks a target once in the SA and the dish knows where to
      point, saving time and flattening the site-operator learning curve.
      Cooperation task — do not let it drop; follow up with System-1 on the
      API spec. Design notes for when the API exists:
      - Natural trigger point: the pulsar visibility planner's "pick" action
        (which already yields name + exact catalog RA/Dec) gains a
        "Send to antenna" button.
      - Keep the client thin and optional: a small module speaking whatever
        System-1 exposes (REST/socket/file drop TBD), enabled via settings
        (endpoint/host), silently absent when not configured so non-System-1
        sites see no change.
      - Open questions for System-1: API transport + schema, coordinate
        epoch (J2000 assumed), one-shot slew vs. tracking handoff, and
        whether the SA should also read BACK the current az/el to display.

- [ ] **Demodulators + audio chain for RFI identification** — click a
      suspect signal on the spectrum/waterfall and LISTEN to it: an ear
      identifies FM broadcast, hum-modulated power-line buzz, pager bursts,
      digital chatter, etc. far faster than staring at the waterfall.
      1. Secondary channel: a small DDC (freq-xlating filter/decimator) that
         tunes within the already-streaming band — no interruption to the
         main display or an in-progress recording.
      2. Demodulators, GNU Radio built-ins to start: AM, NFM, WFM, SSB
         (USB/LSB), CW (BFO), plus raw envelope. Squelch + volume + audio
         bandwidth controls.
      3. Audio out via the GR audio sink (portaudio is already in the conda
         env); optionally record the demodulated audio to WAV (libsndfile
         also present) for RFI reports.
      4. **AI auto-detect of the right demodulator (stretch):** phase it —
         (a) cheap classical heuristics first (occupied BW, envelope
         variance, FM deviation, cyclostationary hints → suggest AM/FM/SSB/
         digital), (b) then a small trained modulation classifier
         (RadioML-style CNN on IQ snippets) if the heuristics disappoint.
         Run it on the DDC output, suggest — don't force — the demod.
      UI sketch: right-click a signal → "Listen here", a compact demod
      panel (mode, squelch, volume, audio-record), tuned marker shown on
      the spectrum. Settings persist in a new `[audio]`/`[demod]` group.

- [x] **Recording timebase integrity — SHIPPED in 1.1.8 (2026-08-03):** `FilterbankSink` now reads gr-uhd `rx_time` overflow tags,
      measures each gap exactly, and zero-pads it live (100 µs threshold;
      10 s/event and 60 s/recording caps → beyond that the file keeps
      recording but is flagged TIMEBASE BROKEN); live gap readout in the
      REC counter, summary in the saved-status line, full event log in a
      `.gaps.json` sidecar; Soapy sources (Ray's HackRF etc.) record
      classically with the overflow panel as their indicator. 5-test suite
      `test_timebase_padding.py` incl. an end-to-end GR flowgraph with
      injected rx_time tags. Remaining before checking off: a live-B210
      recording with induced overflows (CPU-stress during capture) to see
      a real tag round-trip, and a Help/guide PDF sync at release time.
      Original notes: — root-caused 2026-07-17 while re-folding the Haswell
      B0329+54 recording per Dan Layne's review: a rigid no-search
      ephemeris fold exposes a smooth ~1.2-rotation phase drift over the
      36.6-min recording ≈ **3.9×10⁻⁴ fractional timebase error** — five
      orders beyond pulsar/Doppler physics, so it's OUR clock. The header
      tsamp already uses `get_actual_samp_rate()` (checked), so the prime
      suspect is **dropped samples at RX overflow**: each drop silently
      shortens the sample-count clock vs real time (the app SHOWS 'O's
      live in the overflow sidebar but doesn't count or log them). The
      drift is why prepfold searches report unphysical P/P-dot; detection
      sigma survives (search absorbs it) but absolute timing/TOAs don't.
      Fixes, in order of value:
      1. Count overflow events (timestamped) during a recording; write
         them into the `.fil`-adjacent metadata/SigMF and surface them in
         the recording panel + results card ("N overflows ≈ X ms lost").
      2. Gap-padding: on detected drops, insert the missing number of
         samples (zeros or noise) so the sample clock tracks wall time —
         the standard professional fix. **PROVEN 2026-07-17 by manual
         repair:** the drift function was mapped with 22 fixed-period
         window folds (smooth drip + ONE +0.219-rotation step at
         t≈550 s); padding 0.558 s of noise at the step in a copy of the
         `.fil` took the fold from 22.4σ (split profile, DM artifact 36)
         to **28.0σ, textbook single profile, DM back at 25.3**. Padding
         works; the app should do it automatically at overflow time
         (where the true gap length is knowable from UHD timestamps —
         post-hoc repair only recovers it modulo the pulse period).
      3. Optional: external/GPSDO reference support for absolute clock
         accuracy at the site (doesn't fix drops, fixes rate).
      Full analysis with plots: `DSES_SA_Recordings/…B0329+54…_prepfold-
      par-refined.pdf` (2026-07-17 re-fold).
      - Az/el + set-time math is plain sidereal-time + spherical trig (numpy,
        no astropy dependency): cos(HA_set) = (sin el_min − sin lat · sin dec)
        / (cos lat · cos dec); circumpolar → "always up".

- [x] **Drift-scan recording support (ezRA `.txt` format) — SHIPPED in
      1.1.8 (2026-08-03), field-verified 2026-08-02:** third recording format
      "Drift scan (ezRA .txt)" with Az/El fields in the recording panel,
      `[site]` settings (Haswell defaults), ezCol filename convention with
      same-hour letter suffixes, dish-proven geometry defaults (4096 bins,
      31e3 integrations, central-80% band trim). Threaded sink (GR callback
      only copies; scipy-FFT worker integrates). VERIFIED with three live
      B210 GUI captures: format/rows/header correct, and the group's own
      ezCon.py produced a `.ezb` from a real off-air capture (exit 0).
      **Bonus root-cause fix for the systemic RX overflows:** the GR default
      source-edge buffer gives a Python sink only a few ms of slack at
      16 MS/s, so any GIL pause overflowed the radio (this is what plagued
      the Haswell .fil recordings). `set_min_output_buffer(4 Mi samples)`
      on the UHD source (~260 ms cushion) + a display-CPU throttle while
      recording → THIRD live capture ran overflow-free at full 7.9 s/row
      cadence. Remaining: release-time docs sync only.
      Original notes: incorporate
      the role of **ezCol** (the data-collection module of Ted Cline's free
      open-source **ezRA** — Easy Radio Astronomy — suite,
      https://github.com/tedcline/ezRA) so the Workbench can serve
      as the drift-scan data collector: record integrated frequency spectra
      in the **ezRA `.txt` data-file format**, feeding the rest of the suite
      (ezCon → .ezb condensed files → ezPlot/ezSky/ezGal/ezGLon analysis &
      sky maps). Notes:
      - Primary use: 1420 MHz hydrogen-line drift scans on the
        DSES-Drift-Scan box; complements (not replaces) the `.fil`/SigMF
        pulsar recording modes — this is a third recording format targeting
        long-timescale integrated spectra rather than fast time series.
      - **Recon done 2026-08-01 (GitHub):** the whole suite is Python3 on
        **Windows AND Linux** (numpy/matplotlib), so the downstream chain
        (ezCon/ezPlot/ezSky) runs on our dev boxes, WSL, and the site box —
        install it there and use it as the acceptance test on our output.
        **ezCol itself is RTL-SDR-only (pyrtlsdr)** — it cannot drive the
        B210 at all, which is exactly the gap our collector fills for the
        drift-scan dish.
      - Format is fully recoverable from `ezCol.py` source (no spec-only
        development needed): header = `from <rev> <cmd>`, `lat/long/amsl/
        name`, `freqMin/freqMax/freqBinQty`, a coordinate line (azDeg/elDeg
        or raH/decDeg …), `# times are in UTC`, `# gain`, then one row per
        integration: `<UTC timestamp> <RMS power per bin> <flags>`; RMS
        power = sqrt(mean of squares) over ezColIntegQty FFTs; filename
        `data/<prefix>YYMMDD_HH<letter>.txt`.
      - **LOCAL TREASURE (found 2026-08-01):**
        `~/Documents/DSES/Science/HI_and_Drift_Scan/ezRABase/` holds a full
        ezRA install (incl. doc PDFs for ezCon/ezPlot/ezSky), the site's
        actual collector variant `ezColS251110aP.py` (SoapySDR-based — CAN
        drive the B210 via Soapy's uhd factory; `ezColS251110a_B210.py` is
        its B210 copy), the exact dish command line (`ezCol Command.txt`:
        center 1418.405 MHz, 10 MS/s, 4096 bins, integQty 31e3 → ~12.7 s
        per row, lat 38.3808 lon -103.156 amsl 4400 name DSES, az 180
        el 45), and TWO real reference datasets: Nov 2025 dish drift scans
        in `ezRA_Data_Collected_with_ezCol/` (4096-bin, "RMS power in dB")
        and Aug-Sep 2025 in `ezRA_Data_Collected_with_GNURadio/` (2048-bin,
        stamped `from ezColG.py` — a prior GNU Radio collector whose source
        is NOT on this machine, maybe on the site box; its output shows the
        downstream tools tolerate header variations). Correction to the
        note above: stock ezCol is RTL-only, but the group's Soapy variant
        did drive the B210 — our in-app writer is the BETTER path (one
        tool, Welch integrator, timebase fix, recording panel), not the
        only one.
      - Validation plan: clone the real Nov-2025 header verbatim (swap
        provenance line), match its dB-RMS row format and cadence, and diff/
        run through the local ezCon/ezPlot against those reference files.
      - Natural fit with the existing recording panel (Source name, timed
        recording, elapsed counter) and the site/az-el metadata from the
        visibility-planner item (drift scans want LST + pointing recorded).

## Observation presets + consequences readout — IN 1.1.8, DONE 2026-08-03

- [x] **"Observation" preset selector** — Rick chose 1.1.8; shipped 2026-08-03. Answers "what are you trying
      to do tonight?" with a coherent, validated parameter bundle; every knob
      stays adjustable after (combo drops to Manual on deviation, like the
      sample-rate combo):
      | Preset | Sets | Basis |
      |---|---|---|
      | Pulsar — L-band | 16 MS/s, .fil, 2044 ch, Int 1, 1422 band | validated Haswell geometry (127.7 µs; 28σ B0329+54) |
      | Pulsar — UHF | 20 MS/s, .fil, 256 ch, Int 16, 408 band | proven 204.8 µs UHF geometry |
      | Magnetar / high-DM | L-band, max channels, Int 1 | narrow channels beat DM smearing |
      | H-line drift scan | ezRA fmt, ~2 MS/s @ 1420.405, Az 0/El 87 | existing ez defaults |
      | RFI survey | Sweep mode | exists |
      | Manual (expert) | touches nothing | today's behavior |
      Non-B210 radios: preset adapts (clamp rate, keep ratios) instead of
      making it the user's problem.
- [x] **Live "consequences" line** under the recording controls: time
      resolution, channel width, DM smearing @ example DM, GB/hr, host
      headroom; turns red on self-defeating combos (formulas:
      tsamp = ch×int/rate; Δν = rate/ch; disk B/s = 4×rate/int).
- [x] **Label the display group as display-only** (FFT size/window/avg do NOT
      affect recordings — rename "Spectrum Controls" to say so).
- Full expression (Observation menu, first-run wizard, visibility-planner
  tie-in "B0329+54 rises 21:40 → Observe") belongs to the 1.2.0 redesign.

## v1.3.0 — SHIPPED 2026-08-06 (cut + published from Windows)

**Group announcement SENT (Rick, 2026-08-06):** email to the DSES radio
astronomers covering 1.3.0 — led by the planner's **"What do I need?"**
solver framed as a planning tool that needs NO radio at all (which band /
bandwidth can measure a given source's DM — the B0950+08 July lesson,
automated), then the B210 Self Test (radio proves its own pulsar chain,
no cable, PASS/FAIL vs injected truth, verified Windows + macOS), the
full simulator modes (any catalog pulsar/magnetar or fully custom,
70 MHz–6 GHz), the B0950 milestone (first genuine DM measurement by our
chain: injected 2.97, measured 3.4), update path (Help → Check for
Updates), and getting-started steps for members without an SDR
(playback mode + planner work radio-less).

Landed: pulsar_sim.py (noise-carrier synthesis, coherent dispersion
verified to 0.06 µs, seamless loop, geometry-aware grading, geometry
solver), Observe → B210 Self Test (three modes, TX always at minimum
gain, full state save/restore, live consequences readout, stage
explanations, PASS/FAIL banner), planner "What do I need?…" + Self Test
"Suggest geometry", tools/b210_bit.py bench harness, test_pulsar_sim.py
(math suite + offline PRESTO round trip + solver-vs-measurements).
Hardware-verified on BOTH dev machines; Mac: zero TX underflows, period
exact even at 4 MS/s. Publish incident (sha sidecar name 404'd the Mac
updater) closed same-day; make-release now emits the sidecar itself.

## v1.2.0 — SHIPPED 2026-08-05 (cut + published from Windows)

**Group announcement SENT (Rick, 2026-08-05):** email to the six DSES radio
astronomers covering both recent releases — 1.1.8 (presets, recording
integrity, auto-analysis PDFs, ezRA drift scan, true sample rates, Ray's
fixes) and 1.2.0 (dockable-panels redesign, pulsar visibility planner,
hot-plug detection, waterfall new-at-top, macOS Qt fix) — plus
getting-started steps (Radioconda + conda one-liner + zip + launcher;
playback mode needs no radio) for members who haven't installed yet.

Landed: QMainWindow shell, four dockable panels (rearrange/tab/tear-off/
hide, persisted via saveState), menu bar (File/View/Radio/Recording/Help),
full-width status bar with recording-status mirroring. Verified live on
the B210 incl. persistence of a floating panel across relaunch.
REMAINING before the 1.2.0 cut: Rick's hands-on pass; macOS test (native
menu bar!) + drift-scan box test (dock behavior on headless Openbox/xrdp
— re-check the 0x0-screen guards); docs sync + PDF; version bump; cut.


- [x] **Replace the fixed two-column sidebar with a menu bar + dockable
      panels — CORE LANDED on main 2026-08-04 (not yet cut).** DECIDED: dockable panels (PyCharm/Chirp style), not MDI.
      Rationale — the ~300 px sidebar is the root cause of a recurring
      class of bugs, not a cosmetic preference: Ray's unreachable gain
      slider (clipped), status messages truncated below the Record combo,
      the QToolBar-overflow workaround already in the code (Integrate
      would vanish into a "»" menu), and every new feature (Az/El,
      analysis row) fighting for pixels. Three recording formats + sweep +
      analysis have outgrown the space.
      Design targets:
      - Menu bar: File / View / Radio / Recording / Analysis / Help.
        Rarely-touched settings (site coordinates, calibration, FFT
        window, updater) move into roomy dialogs where they can be
        EXPLAINED, not abbreviated.
      - Main window keeps only what you watch while observing: spectrum,
        waterfall, and a slim toolbar for frequency / gain / record.
      - Qt QDockWidget panels: dock, tab, tear off, or hide; layout
        persisted per user (`saveState`/`restoreState`), so a laptop and
        the Haswell projector can each have a fitting layout.
      - A real QStatusBar at the bottom: full width, no truncation,
        details-on-click for long messages.
      Cautions: substantial refactor of a ~6k-line single file; will
      churn the geometry-persistence code that was hard-won on
      Linux/Openbox (frame-vs-client coords, empty-screen guard); test on
      Windows, macOS, and the headless site box. Estimate ~2 focused days.
      **Sequencing: ship 1.1.8 and 1.1.9 FIRST** (that work is done and
      the field wants it), then do this as the headline of 1.2.0 with
      nothing else competing.

## v1.1.9 items — ROLLED INTO 1.2.0 (Rick, 2026-08-04); both LANDED on main 2026-08-04

- [x] **Hot-plug receiver detection (Rick, 2026-08-03) — DONE 2026-08-04, verified live end-to-end:** when the SA opens
      with no receiver detected (today: playback mode or the exit dialog),
      allow the receiver to be connected or powered on later and get
      detected WITHOUT restarting the app. Design sketch: in playback/
      no-radio mode, poll `find_all_radios()` on a slow timer (~5 s; USB
      enumeration is cheap when empty) or offer a "Rescan for radios"
      button on the Device row + picker dialog; on detection, offer to
      switch (tear down the playback graph, build the live source —
      the flowgraph rebuild machinery already exists in the device-switch
      path). Also covers the B210 powered off at session start at Haswell.

- [x] **Waterfall scroll direction (Rick, 2026-08-03) — DONE 2026-08-04 (new-at-top, axis reads age):** new rows currently
      appear at the BOTTOM and history scrolls up; the convention Rick is
      used to (SDR#/GQRX/SDRangel) is new-at-top, history flowing down.
      WHY it is this way: `WaterfallPlotWidget.on_frame` does
      `np.roll(self._data, -1, axis=0)` + writes the new row at
      `self._data[-1, :]`, and the ImageItem rect maps row order directly —
      an implementation accident, not a choice. Fix: roll +1 and write row
      0 (or flip the rect/y-axis), and make the Time axis read as age
      (newest at top). Check both: normal frames AND the first_frame
      reset path, plus Sweep mode's waterfall behavior. Consider a
      settings toggle only if anyone defends the current direction;
      otherwise just adopt the convention.

## Validation tooling (after 1.1.8 ships, possibly after 1.2.0)

- [x] **B210-TX pulsar simulator = BUILT-IN TEST (DECIDED 2026-08-03; BIT
      framing Rick 2026-08-03) — DONE 2026-08-05 incl. the in-app UI;
      SHIPPED in release 1.3.0 (2026-08-06) together with the advanced
      simulator modes and the geometry solver, after passing hardware
      tests on BOTH dev machines (Mac: zero TX underflows, period exact
      even at 4 MS/s).**
      **IN-APP "Self Test" SHIPPED (Observe menu, per Rick): modal dialog
      (duration spin, live countdown, PASS/FAIL readout, Open Fold PDF),
      full radio state save/restore (tuning model incl. preset/manual/
      offsets, rate, gain, antenna), TX spliced into the RUNNING app
      flowgraph via lock/unlock, private FilterbankSink to
      <recordings>/self_test/, PRESTO fold on the shared analysis worker
      slot, grade() vs injected truth. Guards: playback / non-B210 /
      sweep / recording / analysis-in-progress. LIVE-VERIFIED 2026-08-05
      end-to-end in the real app on the real B210 (driver script through
      the production path): PASS — DETECTION, chi2 248 (30 s capture),
      P exact, DM 54.8 of 50, and STATE-restore byte-identical (Rick's
      1422 MHz / 16 MS/s / gain 40 / A:RX2 all back). Help text added
      (Observe menu section); pulsar_sim.py added to BOTH make-release
      ship lists (lazy import — the completeness guard can't see it).**
      **BENCH-PROVEN 2026-08-05 (Windows, B210 s/n 8003886).** What landed:
      `pulsar_sim.py` (synthesis core: SimSpec ground truth incl. a
      `dm_resolution` honesty metric, grid-quantized seamless loop,
      Gaussian-envelope NOISE carrier, coherent cold-plasma dispersion —
      sign convention derived AND verified to 0.06 us against the law;
      `grade()` PASS/FAIL vs injected truth), `test_pulsar_sim.py`
      (fast math suite + `--presto` offline round trip: synth -> app
      channelizer -> .fil -> WSL prepfold = DETECTION, P exact, DM 48.0
      vs 50 injected — the FIRST end-to-end DM validation ever on this
      pipeline), and `tools/b210_bit.py` (bench harness: one B210 full
      duplex, TX loops the waveform out TX/RX-A at MIN gain, RX2-A ->
      FilterbankSink .fil -> analyze_fil -> grade). HEADLINE: **internal
      TX->RX leakage alone carries the test — NO cable, NO pad, NO
      accessories** (15 s probe: pulse ~10 sigma AND the dispersion sweep
      visibly marching across the band). Full 90 s graded run: DETECTION,
      chi2_red 1118, P recovered 100.00000 ms (0.000% off), **DM 51.45
      vs 50 injected (3% on real hardware)**, 0 gap events. GEOMETRY
      LESSON (quantified B0950+08 physics): DM leverage = sweep vs pulse
      width; 10 ms pulses over a 6 ms sweep -> +/-22 DM slack (prepfold
      wandered to 19 of 26.76); BIT default is now P=100 ms, duty 2%,
      DM 50 @ 420 MHz/2 MS/s -> 11.2 ms sweep vs 2 ms pulses =
      dm_resolution +/-4.5. Defaults deliberately far from 1420 MHz.
      (The former TODO — hosting the TX branch in the app — is the shipped
      Self Test above. Site note: at Haswell the feed stays on receiver A;
      the self test parks RX on the A-side RX2 port and restores the
      antenna afterwards, so no recabling is ever needed.) synthesize the pulsar in software and
      transmit it from the SAME B210's TX side (full duplex; no second
      unit needed) while the app records — but as an APP FEATURE, not a
      standalone tool: a B210 is single-process, so the app must host the
      TX chain, which is what a self-test wants anyway. UI: a "Self test"
      action (candidate: 7th Observation entry or button) → TX/RX port A
      plays the dispersed pulsar → pad/cable → RX2 (site: cable into the
      B-side RX while the feed stays on A; characterize internal TX→RX
      leakage as a possible no-cable mode on the bench first; ALWAYS
      minimum TX gain — 1420 MHz is a protected band, no radiating next
      to the dish) → record a few min .fil → existing analysis pipeline →
      compare recovered (P, DM, sigma) to injected ground truth → plain
      PASS/FAIL with numbers. Run it before each Haswell session: proves
      SDR→channelizer→writer→timebase→PRESTO→verdict healthy before
      spending telescope time. LIMIT: TX/RX share the B210 clock, so
      clock faults cancel — the E4438C leg below is the independent-clock
      test. Implementation core: (a) generate
      baseband I/Q of a dispersed, profile-shaped, NOISE-carrier pulse
      train from parameters (P, DM, duty, profile, band, level) — noise
      bursts fold with realistic statistics, unlike the gated carrier
      that produced chi2=inf; (b) add a uhd.usrp_sink TX branch to the
      app's flowgraph while testing (loop must hold an integer
      pulse-period count with seamless phase — choose fs so P*fs is
      integer); (c) auto-compare fold results to the injected ground
      truth for the PASS/FAIL. Unlocks, in value order: END-TO-END
      DM validation (inject DM 26.8, require the pipeline to RECOVER it —
      L-band/16 MHz sweep 1.2 ms; 420 MHz/20 MHz sweep ~60 ms; the
      hardware sim box has no dispersion so the DM dimension has never
      been testable), realistic chi2/sigma statistics, B0329+54's actual
      double-peaked profile at its exact 714.5 ms period, later
      p-dot/orbital/RFI-injection cases. TX and RX share the B210 clock —
      good for controlled tests; use the E4438C leg below when clock
      independence matters. ~1 day incl. bench verification.
      HISTORY: the original plan was the E4438C's internal ARB, but
      interrogation over SCPI (2026-08-03, s/n MY49071480, fw C.05.82)
      showed options 506/UNB/UNJ only — NO 601/602 baseband generator,
      and Rick's serial is in the license-key range where a bare eBay A7
      board may not enable (E4400-60761 + entitlement needed). Rick
      decided NOT to swap units; the B210 TX is the simulator.
- [ ] **E4438C precision leg (kept, reduced role):** the unit's internal
      pulse generator + UNB attenuator still contribute what the B210
      can't: gated pulses at an EXACT catalog period with the ESG locked
      to the GPS 10 MHz reference (period recovery becomes a timing
      test with an independent clock), and calibrated absolute-level
      threshold sweeps (0.01 dB steps to -136 dBm) for a proper
      sensitivity curve. Script over LAN SCPI at 192.168.10.66:5025.
      UPGRADE PATH (2026-08-03): the front-panel I/Q inputs are the
      STANDARD analog vector modulator (601/602 only adds the internal
      digital source) — drive them from a dual-channel phase-synchronous
      AWG playing precomputed I/Q (DC-coupled, ~0.5 Vrms/50 ohm, null
      I/Q offsets from the front panel; P*fs integer for the loop) and
      this unit becomes a FULL independent-clock vector pulsar sim with
      calibrated level. Memory math: full B0329 period @25 MSa/s ~18
      Mpts/ch (deep-memory AWG), but a 4 MHz test bandwidth @5-10 MSa/s
      is a few Mpts and still gives a ~12 ms DM sweep at UHF. No AWG on
      hand (2026-08-03). AFFORDABLE PICK (verified specs): Siglent
      SDG2042X ~$400 — 2 ch, 16-bit, 8 Mpts/ch, TrueArb point-by-point
      1 uSa/s-75 MSa/s (exact fs control for the P*fs-integer loop
      seam), rear-panel 10 MHz In/Out with external clock select (GPS
      lock), LAN SCPI. 8 Mpts is adequate via the PULSAR-CHOICE TRICK
      (memory = P*fs, and we control both): B0950+08 (253 ms, a real
      DSES target) fits a FULL period at 31 MSa/s = full 16-20 MHz RF
      BW; B0329 fits at 11 MSa/s (~9 MHz); UHF DM tests fit multiple
      periods. NOTE 18 Mpts is NOT the ceiling: magnetar-period sims
      (P=2-12 s) need 30-60 Mpts even at reduced BW, multi-period
      trains N*18, p-dot drift records more still.
      RECOMMENDED (2026-08-03, datasheet-verified): **SDG3082X +
      SDG-3000X-40MPTS memory option** (~$1-1.3k, get quote) — 2 ch,
      16-bit, 1.2 GSa/s, TrueArb 10 mSa/s-600 MSa/s, rear 10 MHz ref
      IN/OUT, 20 Mpts std -> 40 Mpts WITH THE OPTION (order it
      installed — ESG lesson), plus Sequence playback (chain segments:
      multi-period trains without linear memory cost). 40 Mpts = 2.2x
      the B0329 full-BW case, fits a 6 s magnetar at 4 MHz BW, 6 unique
      B0950 periods at full BW. SKIP the SDG6000X (costs more than
      3000X, only 20 Mpts — sells analog BW we don't need). Premium
      fallback if the wall is ever hit: SDG7032A (512 Mpts, vector/IQ
      mode, ~$4k) or used Keysight 33622A + 336MEM2U (64 Mpts/ch).
      **AWG PURCHASE CANCELED (Rick, 2026-08-06)** — the external-I/Q
      vector upgrade is off the table for now; the analysis above is
      kept for reference should it ever come back. What remains of this
      leg without an AWG: the E4438C's internal pulse generator + UNB
      attenuator (gated pulses at an exact catalog period, GPS-locked
      10 MHz reference, calibrated absolute levels for sensitivity
      sweeps) — no dispersion, no vector modulation. The B210 simulator
      (shipped 2026-08-05) covers the vector/DM side. A NEW project is
      incoming from Rick that supersedes this priority.

## Backlog / unscheduled

- [ ] **Startup failures later than the launcher's 10 s window are still
      silent on Windows** (from the 1.6.1 work, 2026-10-04). The launcher
      now reports a program that stops within 10 s, but a fault after the
      heavy imports (damaged settings, a radio-open error on a slow host)
      can land later, and the console is minimized. App side: an
      excepthook armed from the first line of dses_workbench.py until the
      main window is shown — write the traceback to `startup_error.txt`
      beside settings.ini and show a native message box naming it; disarm
      once the window is up so exceptions in slots keep today's behaviour.
      Guide section 6 then points at that file.

- [x] **Drift-Scan Review — IMPLEMENTED ON MAIN 2026-09-27 (rollover
      trigger met; driftscan_review.py + test_driftscan_review.py, 27
      checks; Observe menu + Help; both ship lists; validated against
      Haswell 09-24/26, Ray night 8, Rich el92 — awaiting Rick's
      hands-on before the 1.6.0 cut). Original spec: the morning-after
      quicklook for ezRA recordings
      (proposed 2026-09-19, from the Ray/Rich/Haswell September arc; Rick:
      write it up). SCHEDULED FOR 1.6.0 (Rick, 2026-09-19): it IS the next
      release's feature, but implementation does not start until the
      Haswell post-restore ROLLOVER is observed (expected late September);
      1.6.0 then bundles this + f70f603 (az/el precision) and is cut at
      the rollover-timed Pi scan break (update + re-enter full-precision
      az/el + fresh segment before Glenn's October session).** Every drift-scan night this month — Ray's eight
      files, Rich's twelve-stripe survey, and every Haswell post-restore
      check — needed the SAME hand-written analysis before anyone could
      answer "did I detect it? is the recording clean?": parse the ezRA
      `.txt`, check the header, census gaps and spurs, flatten the
      bandpass, plot the waterfall and line profile, fit the transit. ezRA
      owns the deep end (ezCon/ezSky/ezGal: condensation, RFI rejection,
      sky maps, l–v diagrams, arm reconstruction — Rich's 09-18 plots
      prove it) and we must NOT duplicate that; the gap is the per-night
      triage layer BELOW it, which ezRA does not do and which its fragile
      Windows toolchain (backslash-path trap, .conda-python crash) makes
      hard for users to improvise. New tool (menu: Observe → Drift-Scan
      Review…, also usable on the file just recorded): open an ezRA `.txt`
      and produce one standard report —
      * Header sanity: version, band vs the HI line, lat/long/amsl vs the
        app's site settings, az/el STALENESS check (flag when the header
        az/el disagrees with the current Recording-panel values — Ray's
        recurring trap);
      * Recording integrity: row count, cadence exactness, gap census,
        total-power stability (rms, p-p);
      * Spur census: narrow features vs a median-filtered bandpass, with
        the known 1420.000-family called out;
      * Waterfall (bandpass-flattened) and averaged line profile in
        velocity units, DC-artefact position marked;
      * Transit finder: light curve over a chosen velocity band, Gaussian
        fit → peak %, FWHM, UT/LST centroid with the τ/2 end-stamp
        correction applied, vs the sidereal prediction from a prior night
        if one is loaded (the Ray workflow) — and for a stationary dish,
        peak-vs-day trending (the Haswell sag monitor / Cyg A calibration
        series);
      * One-click export: figures + a text summary block suitable for
        pasting into email (the FINDINGS workflow).
      Reference implementations: HI_and_Drift_Scan\analysis_2026-09\
      ray_noise\analyze_n8.py + ray_n8_figure.py (per-night quicklook),
      rich_survey\survey_fingerprint.py/survey_checks.py (multi-file
      census), and the Haswell transit Gaussian fit in SEGMENT5_NOTES
      "POST-RESTORE DAY 1". Complementary to ezRA by design: the output
      of this tool is the DECISION to feed the file onward into ezCon.
- [ ] **Recording-panel pointing: live Dec readout + Dec-first entry
      (proposed 2026-09-21, from Ray's night-9 header — az 102.5/el 4,
      a pointing that never comes within 50 deg of the galactic plane;
      the true pointing had to be solved from the sky).** Two stages:
      (a) CHEAP, bundle whenever convenient: a read-only consequences
      line under the Az/El spins — "-> Dec +xx.x" (and, for ezRA
      format, when that dec crosses the galactic plane) — computed
      from the site lat/long already in settings with the planner's
      existing sky math (precess/lst helpers); nonsense entries then
      read as nonsense. (b) Dec-FIRST ENTRY as the alternative mode:
      for a drift scan the constant coordinate is DECLINATION (beam RA
      changes all night — "RA/Dec entry" is really Dec entry), so let
      the user type a target Dec and have the app compute El (default
      meridian az 180/0, or a user-fixed azimuth like Ray's ~187) and
      fill the Az/El fields; the ezCol header format is unchanged —
      the app just fills it correctly. Serves the manually-steered
      home stations (Ray: fixed az, jack-screw el) and the
      Dec-stepping survey directly.
- [ ] **Constant-statistics display while recording** (from Ray's
      2026-09-06 report, diagnosed 09-07): with recording active the
      display tick budget drops 0.4→0.12 and on a loaded host the Welch
      block count collapses toward 1; in dB-averaging mode the trace then
      reads up to −2.51 dB low (log-of-exponential-mean bias) and much
      noisier — level "recovers" when recording stops. Data is untouched.
      Fix: accumulate blocks ACROSS ticks to a target N while recording
      (slower update instead of a biased trace), or correct the small-N
      bias + show a "display averaging reduced" status note.
- [x] **Planner sky math: IERS download OFF + closed form PRECESSED — DONE
      2026-09-11 (Rick's call, same morning).** Two findings corrected the
      09-10 note: (1) astropy 8 re-downloads the 3.7 MB IERS-A table on
      EVERY process start (its cache is write-only), so the cost was 16 s
      per launch with internet, 84-99 s black-holed — and in the black-holed
      case it then RAISED and the planner silently fell back to the closed
      form; "0.87 s with auto_download=False" had been that fallback, not
      astropy. The working setting is `auto_download=False` AND
      `auto_max_age=None` (bundled table): measured 0.06" from a fresh
      download at the same instant, ~1 s, no network. (2) astropy is a
      dev-only extra — the Haswell Pi (`/home/dses/radioconda`) and the
      Windows production install (`C:\ProgramData\radioconda`) have NO
      astropy and were always on the closed form, which was 18.5' high in
      altitude: all precession (J2000 catalog positions treated as of-date,
      26 yr x 50"/yr). `precess_j2000()` (IAU 1976, Meeus 21.b reproduced
      to 0.00", astropy FK5 to 0.1") now feeds altaz(), the rise/set/next-
      window searches (precess once, step with `_altaz_of_date`), and the
      transit/culmination helpers. Whole-catalog residual vs astropy:
      median 0.2-0.4', p95 0.46', max 0.49' at 1995/now/+1 yr/2035 (was
      18.5'). Cyg A's transit moved +53 s and its culmination +4.3' — the
      SEGMENT5 "apparent Dec" lesson, now built in. `LAST_ENGINE` says
      which path ran; the Alt tooltip quotes it. Nutation + aberration
      (the remaining 0.5') are the only further step, not worth taking.
- [ ] **Mid-integration row timestamps for ezRA drift-scan files** (from
      the Sept campaign analysis): rows are stamped at integration END, so
      transit fits read τ/2 late (13 s at 25 s rows, 32 s at 64 s). Stamp
      at mid-integration instead; note the convention change in the file
      header comment so analyses can tell which convention wrote a file.
- [x] **RENAMED to "DSES Radio Astronomy Workbench" — DONE 2026-09-08
      (Rick's decision 2026-08-24; name + slug `dses-workbench` confirmed
      2026-09-08). Ships as 1.4.0.** Display identity, module
      (`dses_workbench.py`, old name kept as a launch shim), zip prefix,
      .desktop, icons, docs, AND the gpstime folder
      (`sw_distribution/dses-workbench/`, Rick: "b210_sa" named one radio)
      all moved in ONE release rather than the two-step plan above — safe
      because the updater follows the manifest's download_url and accepts
      any zip top folder, so the old `b210_sa/manifest.json` stays online
      as a pointer and 1.4.0 migrates the stored URL out of settings.ini.
      Deliberately NOT renamed: the `DSES_Analyzer` config-dir name and
      `DSES_SA_Recordings` (user data continuity). Still to do outside
      the repo: website/page wording, the Observer's Guide title.
- [ ] **ONE task-focused PDF: "DSES Radio Astronomy Workbench - Observer's
      Guide" (Rick 2026-08-24; one-doc structure agreed 2026-08-24).**
      Part 0 = common setup (2-3 pp, written once); Parts 1 and 2 below,
      each SELF-CONTAINED so a member can print Part 0 + their activity
      only; future Part 3 = pointing-calibration campaign once proven.
      Original two-activity scope:
      (1) PULSARS: plan with the visibility planner -> choose preset ->
      record .fil -> verify integrity -> fold (catalog + manual P/DM) ->
      read the fold PDF; (2) DRIFT SCAN: HI preset (incl. LO offset) ->
      ezRA recording + UTC rollover -> multi-day campaign practice ->
      ezCon/ezPlot/ezSky chain -> what good data look like (sharp spur,
      DC status). House style via build_doc.py; write AFTER the rename so
      they carry the "DSES Radio Astronomy Workbench" identity; fold in the hard-won lessons from the
      Aug 2026 campaigns (header checks, spur sharpness as a health test).

- [ ] **LO offset for shift-less Soapy radios (HackRF, RTL-SDR) via host
      rotator:** the 2026-08-19 LO-offset feature covers UHD (tune_request)
      and Soapy drivers exposing a DSP shift stage (LimeSDR-class). HackRF /
      RTL-SDR have no hardware shift stage, so they fall back to classic
      tuning (DC artefact mid-band). A host-side fallback is possible: tune
      hardware to center+offset, splice a rotator_cc (+offset) after the
      source — the DC artefact then sits at −offset in the displayed band
      (moved OFF the target but still in-band; full out-of-band removal
      would need oversample+xlating-FIR). Needs a HackRF on the bench to
      validate before shipping; design notes in the SoapyGenericSource
      LO-offset comment block.

- [ ] **B0950+08 re-observation plan (observing, not software):** processing
      gains on the 2026-07-11 recording are exhausted (see
      `…B0950+08…_prepfold-refined.pdf`, 2026-07-17): cleanup lands best-fit
      P/DM on catalog values but sigma is noise-limited at ~9. To reach a
      publishable detection: 90+ min integration (~20σ), several sessions to
      catch scintillation maxima, wider capture BW if the feed allows, and
      record only after the v1.1.7 overflow/gap-padding fix ships.

- [ ] **In-place update: offer to refresh the desktop launcher (Rick,
      2026-09-08, from the 1.4.0 rollout).** Today only the "Install a new
      copy" path creates a shortcut; an in-place update leaves the existing
      Windows .lnk / macOS .app / Linux .desktop alone. That is safe (they
      run launcher.*, which starts whatever is installed) but it means a
      product rename or icon change never reaches the desktop until the
      user runs install-shortcut.* by hand — both dev machines needed that
      manual step for 1.4.0. Proposal: after a successful in-place install,
      a checkbox/prompt "Refresh the desktop shortcut" (default ON when the
      shipped APP_NAME or icon differs from the installed one, else OFF)
      that runs install-shortcut.ps1 / install-shortcut.command /
      _make_linux_desktop_entry with NO version suffix, then offers to
      remove the old-named launcher if its name differs. Needs the
      installer to know the pre-update APP_NAME (read it from the backed-up
      module in .dses_backup, or persist it in settings.ini). Keep the
      new-copy behavior (suffixed, side-by-side) unchanged.
- [ ] **build_doc.py: port the Windows PDF step to PDFMaker ("Save as Adobe
      PDF") — Rick, 2026-09-08.** Today build_doc prints Word -> PostScript
      -> acrodist.exe (the Distiller printer path). It failed twice on
      2026-09-08: a stale pywin32 gen_py cache (CLSIDToPackageMap), then
      a modal "Adobe PDF" dialog because the printer's "Rely on system
      fonts only" option had been re-enabled — the build hung silently
      with an orphaned hidden WINWORD, and only a Win32 window-text dump
      revealed why. DOCUMENT_STANDARDS.md §8.2 already names Word's
      "Save as Adobe PDF" (the Acrobat PDFMaker add-in) as the PREFERRED
      export (validated 23-Aug-2026: subset-embedded house fonts,
      selectable text, no printer, no Distiller, no printer-preference
      dependency). Port: drive PDFMaker from Word COM instead of
      PrintOut/acrodist, keep the printer path as the fallback, keep the
      pypdf verification (extractable chars high, images ~0, subsetted
      MinionPro/MyriadPro/SourceCodePro), and update §8.2's "reference
      implementation: build_doc.py" pointer. Both dev machines: the Mac
      path (LibreOffice) is untouched. Small tooling item; do it before
      the next docs-heavy release (the Observer's Guide).
- [ ] Website version of the Haswell trip report (derive from the finished
      `.docx`, do not rebuild — see CLAUDE.md handoff notes)
- [ ] **On-site computer upgrade (hardware, not app):** replace the
      Raspberry Pi 5 drift-scan box with a powerful multi-core x86-64 Linux
      machine (planned, near future). App implications: conda-forge PRESTO
      v6 installs directly (enables on-site post-processing), multi-core
      folds are fast, and the headless/0×0-screen + aarch64 constraints go
      away. Keep the existing headless safeguards regardless — the new box
      will likely also run headless over xrdp/VNC.

## Shipped

### v1.1.6 (2026-07-12, commit 3a1aa2d)

- [x] Sweep mode restored (recovered from dangling commits; ported to HEAD)
- [x] Recording panel: Source name, elapsed counter, red REC indicator,
      "record for" duration with auto-stop
- [x] RA/Dec (`src_raj`/`src_dej`) in the `.fil` header via pulsar-name lookup
- [x] Launcher headless virtual-monitor support (Linux)
- [x] Geometry safeguard for empty/0×0 screens
- [x] LF line-ending enforcement for shipped scripts (post-release CRLF fix,
      a5ba701)
