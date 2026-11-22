# GUI redesign — working notes (19 November 2026)

**SUPERSEDED 20 November 2026 by `PROPOSAL.md`** (finished on Opus the next day: mockups A / A2 / B, menus sheet, saved-layout test, proposal). Kept as the reasoning trail; read PROPOSAL.md first.

## What exists in this folder

- `mockups/00_current_window.png` — the real 1.8-in-progress window, rendered at 1440 x 900 in playback mode (the "before" picture).
- `mockups/icon_sheet.png` — 37 toolbar icons drawn in code, at 32 / 24 / 16 px and disabled.
- `prototype/proto_harness.py` — builds the REAL main window without touching `dses_workbench.py`: private settings file (`prototype/_proto_settings/`), radio never opened (playback forced), renders without putting a window on screen.
- `prototype/grab_current.py` — writes the "before" picture.
- `prototype/proto_icons.py` — the icon set; run it to rewrite the sheet.

Run (from the repo root, Git Bash): `PATH="$PWD/.conda/Library/bin:$PWD/.conda:$PATH" ./.conda/python.exe design/gui-redesign-2026-11/prototype/<script>.py`

## Still to do (in order)

1. `prototype/proto_redesign.py` — `apply(win, variant)` adds the proposed menu tree, toolbars and status-bar fields to the real window and re-stacks the six panels as tabs in one right-hand column. Wire actions to the existing slots (list below). Variants: A = toolbar with text under icons (recommended), A2 = icons only, B = ribbon.
2. `prototype/render_mockups.py` — writes `mockups/01_A_labelled.png`, `02_A_compact.png`, `03_B_ribbon.png`; `prototype/run_prototype.py` — the same thing on screen so Rick can click it.
3. Test, not guess: what `restoreState()` does with a 1.7-era `[window] dock_state` blob when the window has new toolbars (decides whether saved layouts migrate or start fresh).
4. Write `PROPOSAL.md` (inventory, menu tree, toolbar table with reasons, icon plan, panels/settings, alternatives, recommendation, stages, tests).
5. Short message to Rick with the choices he has to make.

Icon sheet fixes wanted: `layout_reset` reads as a "C" in a box (redraw); the sheet's long captions overlap the next column (cosmetic, sheet only).

## Findings so far (inventory of the current window)

Window = `dses_workbench(gr.top_block, QMainWindow)`, `__init__` at about line 6864, menu bar in `_build_menu_bar` (about 8432), docks via `_make_dock` (7027), custom title bar `_DockTitleBar` (6617), geometry and `dock_state` save/restore in `showEvent` / `_save_geometry` (9006-9140).

**Menus today:** File (Set Recording Folder, Open Recordings Folder, Exit) · View (Display Panels (left) ▸, Control Panels (right) ▸, Line View Ctrl+L, Full Screen) · Radio (Change Device) · Observe (Pulsars in View Ctrl+P, Refresh Pulsar Catalog, B210 Self Test, Drift-Scan Review) · Recording (Start, Stop) · Help (User Guide F1, Check for Updates, About). 17 actions in all; most of the program is reachable only through the panels.

**Panels today (six QDockWidgets, object names must not change):**
- `dock_observation` — preset combo (6 presets + Manual), consequences line, Mode Live/Sweep.
- `dock_tuning` — band radio buttons (408, 680.5, 1299.5, 1420.4 HI feed, 1666 OH, 2304, Manual), Coarse Tune, Fine Tune, LO Offset, Manual Frequency; Sweep group (Start, Stop, Step, Status).
- `dock_rx` ("Radio") — Sample Rate combo, Manual Rate, RX Gain, Antenna (multi-port radios), Device button.
- `dock_recording` — Folder, Format (I/Q, .fil, ezRA), Source, Fold P, Fold DM, Channels, Integrate, Az, El, Analyze when done, Quick look, Record for, Start at + UTC/Local, Repeat (+ custom interval, count), Record combo (Stopped/Recording), status line, red REC counter, Overflow box.
- `dock_spectrum_display` — Line view; FFT (Size, Window, Normalize, Remove DC spike, Avg alpha, Power averaging, Smooth, Trace Average/Peak/Both); Hold (Max, Min, resets, Detector); Baseline (Mode, Store reference, Flatten width); Y-Axis (Units, Cal offset, Min, Max, Autoscale, Reset Axes, Linear); Display (Grid, Axis labels, Dark background); Trace (Color, Width, Alpha, Label).
- `dock_waterfall_display` — Intensity Min/Max, Autoscale intensity, Colormap, Axis labels, Grid, Dark background, Rows.
- On the plot itself: cursor read-out, marker A (left click), marker B (right click), clear (double click).

**Settings with NO control anywhere today (settings.ini only):** `[site]` lat, lon, height, name, `sefd_jy`; `[recording]` `ez_fft_bins`, `ez_integ_frames`, `ez_keep_fraction`, `ez_prefix`; `[sweep] settle_ms`; `[updates] auto_check`. (`el_mask_deg` is set from the planner.) A Preferences dialog would close this — it was a stated 1.2.0 design target that never got built.

**Measured on the "before" picture (1440 x 900):** the two panel columns take 43% of the window width; the plots get 57%. The right column shows four panels at once and every one of them is cut off (Mode group, gain slider, most of Recording).

**Tests and tools reach into the window by attribute name** — these must survive: `_fft_plot`, `_waterfall_plot`, `_processor`, `_display`, `_recording_status`, `_trace_combo`, `_fft_size_combo`, `_max_check`, `_avg_slider`, `_smooth_spin`, `_dc_check`, `_linear_check`, `_power_avg_check`, `_rec_*` widgets (`_rec_repeat_combo`, `_rec_repeat_widget`, `_rec_repeat_count_spin`, `_rec_repeat_custom_edit`, `_rec_start_edit`, `_rec_start_widget`, `_rec_duration_edit`), `_mode_live_btn`, `_mode_sweep_btn`, `_analyze_check`, `_apply_observation`, `set_record`, `set_record_format`. So: keep the panel widgets, add QActions that mirror them; do not move widgets out of the panels.

**Slots the prototype can wire to:** `set_record(0|1)`, `set_record_format('iq'|'fil'|'ezra')`, `_on_quicklook_clicked`, `_on_change_recording_dir`, `_observation_combo` + `_apply_observation(idx)`, `_mode_live_btn/_mode_sweep_btn.setChecked`, `set_freq_preset(hz)`, `set_freq_manual(hz)`, `set_gain(db)` / `_gain_win.set_value`, `_on_change_device_clicked`, `_show_pulsar_planner`, `_show_selftest_dialog`, `_show_driftscan_review`, `_fft_plot.set_line_view(on)` (+ signal `line_view_changed`), `_fft_plot._on_autoscale_y`, `_fft_plot.reset_axes`, `_fft_plot.clear_markers`, `_fft_plot._max_check/_min_check`, `_fft_plot.request_reset_max/min.emit()`, `_fft_plot._trace_combo`, `_fft_plot._dark_bg_check`, `_waterfall_plot._on_autoscale_clicked`, `_show_help_dialog`, `_check_for_updates_manual`, `_show_about_dialog`, `pulsar_planner.lst_hours(unix_ts, lon_deg)` for an LST clock.

## Design direction (to be argued in PROPOSAL.md; Rick decides)

**Top of the window — the real choice:**
- **A (recommended): menu bar + two rows of standard toolbars.** Row 1, text under icons: Recording (Record, Stop, Format ▾, Quick look, Folder) · Observation (Preset ▾, Live, Sweep) · Tools (Pulsars in View, Self Test, Drift-Scan Review) · Window (Panels, Full screen, Guide). Row 2, small: Tuning (Band ▾, frequency box, gain) · Display (Line view, Autoscale Y, Autoscale waterfall, Max hold, Min hold, Reset holds, Trace ▾, Clear markers, Reset axes). Standard Qt: movable, hideable, saved with the layout, native menu bar on macOS. "Text under icons / icons only" is one Qt property, so A2 is a View-menu option, not a separate design.
- **B: a ribbon like SDR Console** (tabs Home / Display / Record / Tools, big icons in captioned groups, no classic menu bar on Windows and Linux). Qt has no ribbon; it is custom code including its own behaviour when the window is narrow, about twice the work, and macOS still shows a system menu bar above it.
- All actions are QActions shared by menus and toolbars, so a ribbon could be added later without redoing stage 1.

**Detailed settings:** keep the six panels and their contents, but stack them by default as tabs in ONE right-hand column (plots gain the whole left column). Still draggable and floatable. Alternatives: leave today's two columns; or move settings into dialogs (poor for things adjusted while watching).

**Full menu tree (draft):** File (Set Recording Folder, Open Recordings Folder, Preferences, Exit) · View (Toolbars ▸, Panels ▸, Line View, Trace ▸, Max hold, Min hold, Reset Holds, Autoscale Spectrum, Autoscale Waterfall, Reset Axes, Clear Markers, Dark Background, Grid, Reset Window Layout, Full Screen) · Radio (Change Device, Band ▸, Set Frequency Ctrl+F, Sample Rate ▸, Antenna ▸, Mode ▸ Live/Sweep) · Observe (Preset ▸, Pulsars in View, Refresh Catalog, B210 Self Test, Drift-Scan Review) · Recording (Start/Stop Ctrl+R, Format ▸, Quick Look, Analyze When Done, Schedule (opens the Recording panel), folders) · Help (User Guide, Check for Updates, About).

**Status bar:** message on the left; fixed fields on the right — REC state with elapsed / time left, centre frequency, sample rate, gain, device, overflow count, UTC, LST.

**Icons:** drawn in code (QPainter, 24-unit grid, several pixel sizes) in one new module; no fonts, no image files, no SVG plugin, nothing to license. Alternative considered: a bundled MIT/Apache SVG set (Tabler, Lucide, Material) — legal under GPL-3.0 but has no pulsar / drift-scan / hold icons and depends on Qt's SVG icon plugin being present on every install, including the Pi.

**Saved layouts and settings:** every `settings.ini` key stays. Store the new arrangement under a NEW key (for example `[window] layout_v2`) and leave `dock_state` alone, so going back to 1.7/1.8 still finds its own layout. Add View ▸ Reset Window Layout.

**Traps to write up:** on macOS Qt moves any menu item whose text looks like "Preferences / Settings / About / Quit" into the application menu — give every action an explicit menu role; do not use Cmd+H or Cmd+M (system); QToolBar hides what does not fit behind a "»" (the old Integrate-box lesson) — fine at the top of the window only because every toolbar action is also in a menu; toolbar drop-downs need a real screen size on the headless Pi (the 0 x 0 screen lesson; the launcher's virtual monitor covers it — verify there).

**Stages (draft):** 0 = action registry, menus built from it, no visible change · 1 = toolbars, full menus, status-bar fields, icon module, new default panel arrangement, layout key v2, Help rewrite (its own release after 1.8.0) · 2 = Preferences dialog for the settings that have no control · 3 = optional: ribbon, toolbar customising. New headless test: every toolbar action is also in a menu, has an icon and tooltip, mirrors its panel control both ways, layout round-trips, old `dock_state` is handled.
