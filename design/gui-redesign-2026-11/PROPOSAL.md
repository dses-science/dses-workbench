# DSES Radio Astronomy Workbench — GUI redesign proposal

**Status:** design proposal for Rick's decision, 20 November 2026. No program code has been changed; everything here is new files under `design/gui-redesign-2026-11/`.

**The ask (Rick, 19 November 2026):** a more traditional look and feel, with a menu, groups of shortcut icons, and so on. The bar is SDR Console V3.3.

**Recommendation in one paragraph:** keep the plots as the centre of the window and put a full menu bar above them, with two rows of ordinary toolbars. The top row has big icons with their names underneath (Recording, Observation, Tools, Window). The second row is compact (frequency, gain, rate, and the display buttons). Every command is also in a menu. The six existing settings panels keep their contents but stack as tabs in one column on the right, so the plots get about three quarters of the window instead of 57%. The status bar gains fixed fields: recording state, frequency, rate, gain, radio, dropped samples, UTC and LST. Icons are drawn in code, as the panel title-bar icons already are. Saved settings all keep working, and the new window layout is saved under a new key so going back to 1.8 is harmless.

## 1. Pictures

All pictures are the **real program** rendered in playback with a private settings file, with the proposed menus and toolbars added on top (`prototype/proto_redesign.py`). The plots show the bundled 408 MHz sample, so the spectrum is flat. They are the same in every picture.

| File | What it shows |
|---|---|
| `mockups/00_current_window.png` | The window today (1.8.x), 1440 × 900 |
| `mockups/01_A_two_toolbars.png` | **Recommended, A:** menu bar + two toolbar rows, names under the big icons. The red REC field in the status bar is an illustration of a running recording |
| `mockups/02_A2_one_icon_toolbar.png` | A2: the same commands in one row, icons only |
| `mockups/03_B_ribbon.png` | B: an Office / SDR Console style ribbon (Home tab shown) |
| `mockups/04_menus.png` | Every menu of A opened, with the Trace, Band, Preset and Format submenus |
| `mockups/icon_sheet.png` | All 37 toolbar icons at 32, 24 and 16 px, and greyed out |

You can also click through it. This opens a real window in playback, never opens a radio, and never touches your settings.ini:

```powershell
cd C:\Users\rick\Documents\DSES\Science\DSES_Workbench; $env:PATH = "$PWD\.conda\Library\bin;$PWD\.conda;$env:PATH"; .\.conda\python.exe design\gui-redesign-2026-11\prototype\run_prototype.py A
```

Replace `A` with `A2`, `B`, or `old` (today's window) to compare. In the prototype, Record and the gain box say "not available in playback", as the panels do. Preferences is a placeholder (stage 2).

## 2. What exists today

**Window structure (1.2.0 design, 4 August 2026).** It is a QMainWindow with the plots as the centre. Six dockable panels sit in two columns: the display panels on the left, the science panels on the right. Each panel has a tinted title bar with three drawn buttons (dock back, enlarge, hide). There is a thin menu bar and a full-width status bar. The 1.2.0 redesign replaced a fixed 300 px sidebar that clipped controls (Ray's unreachable gain slider, truncated status messages, the Integrate box hidden in a "»" overflow menu). Docking fixed the clipping. It also left most commands reachable only inside the panels, and the panels take a lot of width.

**Measured at 1440 × 900 (picture 00):** the two panel columns take 43% of the width and the plots 57%. All four right-hand panels are cut off: the Mode group, the gain slider, and most of Recording.

**Menus today: 17 actions.**

| Menu | Items |
|---|---|
| File | Set Recording Folder, Open Recordings Folder, Exit |
| View | Display Panels (left) ▸, Control Panels (right) ▸, Line View (Ctrl+L), Full Screen |
| Radio | Change Device |
| Observe | Pulsars in View (Ctrl+P), Refresh Pulsar Catalog, B210 Self Test, Drift-Scan Review |
| Recording | Start Recording, Stop Recording |
| Help | User Guide (F1), Check for Updates, About |

**Panels today, and everything in them:**

| Panel (object name, kept) | Controls |
|---|---|
| Observation (`dock_observation`) | Preset (6 presets + Manual), consequences line, Mode Live / Sweep |
| Tuning (`dock_tuning`) | Band (408, 680.5, 1299.5, 1420.4 HI feed, 1666 OH, 2304, Manual), Coarse Tune, Fine Tune, LO Offset, Manual Frequency; Sweep group (Start, Stop, Step, status) |
| Radio (`dock_rx`) | Sample Rate, Manual Rate, RX Gain, Stages row (HackRF, 1.8.1), Antenna (multi-port radios), Device |
| Recording (`dock_recording`) | Folder, Format (I/Q, .fil, ezRA), Source, Fold P, Fold DM, Channels, Integrate, Az, El, Analyze when done, Quick look, Record for, Start at + UTC/Local, Repeat (+ interval, count), Record (Stopped / Recording), status line, red REC counter, Overflow box |
| Spectrum Display (`dock_spectrum_display`) | Line view; FFT size, Window, Normalize, Remove DC spike, Avg α, Power averaging, Smooth, Trace; Max/Min hold + resets, Detector; Baseline mode, Store reference, Flatten width; Units, Cal offset, Y min/max, Autoscale, Reset Axes, Linear; Grid, Axis labels, Dark background; trace Color, Width, Alpha, Label |
| Waterfall Display (`dock_waterfall_display`) | Intensity min/max, Autoscale intensity, Colormap, Axis labels, Grid, Dark background, Rows |

On the plot itself: the cursor read-out, marker A (left click), marker B (right click), and clear (double click).

**Settings with no control anywhere** (settings.ini only): `[site]` latitude, longitude, height, name and SEFD; `[recording]` ezRA FFT bins, integration frames, keep fraction and file prefix; `[sweep]` settle time; `[updates]` auto-check. The planner sets the elevation mask. The 1.2.0 plan called for a Preferences dialog for exactly these, and it was never built.

## 3. The proposed window (A)

### 3.1 Menu tree

Items marked ★ are new commands. The rest exist today, either in the menus or in a panel.

- **File:** Set Recording Folder… · Open Recordings Folder · ★Preferences… (Ctrl+,) · Exit (Ctrl+Q)
- **View:** Toolbars ▸ (one tick per toolbar) · Panels ▸ (one tick per panel) · ★Settings Panels (F9: hide or show the whole column) · Line View (Ctrl+L) · Trace ▸ (Average / Peak / Average + peak) · Max hold · Min hold · Reset holds · Autoscale (Ctrl+Shift+A) · Waterfall levels · Reset axes (Ctrl+0) · Clear markers · Dark Background · Grid · ★Reset Window Layout · Full Screen (F11)
- **Radio:** Change Device… · ★Set Frequency… (Ctrl+F: jumps to the frequency box) · Band ▸ (the seven feed presets) · Sample Rate ▸ · Antenna ▸ (multi-port radios only) · Mode ▸ (Live / Sweep)
- **Observe:** Preset ▸ (the six observation presets + Manual) · Pulsars in View… (Ctrl+P) · Refresh Pulsar Catalog · B210 Self Test… · Drift-Scan Review…
- **Recording:** Record (Ctrl+R) · Stop · Format ▸ (I/Q, .fil, ezRA) · Schedule… (opens the Recording panel at Record for / Start at / Repeat) · Quick look · Analyze When Done · Open Recordings Folder · Set Recording Folder…
- **Help:** User Guide (F1) · Check for Updates… · About…

### 3.2 Toolbars, and why each item earns a one-click button

**Row 1: big icons with their names underneath.** These are the things you do at the start and end of a session.

| Group | Button | Why it is one click |
|---|---|---|
| Recording | Record | The most important action in the program; today it is a combo box at the bottom of a cut-off panel |
| | Stop | Also cancels an armed start or a repeating series, the same as setting the combo to Stopped |
| | Format: I/Q ▾ | The format decides the whole data product; you should see it before pressing Record |
| | Schedule | Record for / Start at / Repeat are set once per night; the button opens the panel at those fields |
| | Quick look | "Is this session working?" while a .fil recording runs |
| Observation | Preset ▾ | "What am I doing tonight?" is the first choice of a session |
| | Live / Sweep | The two modes; RFI hunting is one click away |
| Tools | Pulsars in View | The planner; used before every pulsar session |
| | B210 Self Test | Proves the chain before telescope time; it explains itself when the radio is not a B210 |
| | Drift-Scan Review | The morning-after report |
| Window | Settings Panels | Hides the right column for a full-width plot (projector, small screen) |
| | Full Screen | |
| | User Guide | |

**Row 2: compact.** These are the things you touch while watching.

| Group | Item | Why |
|---|---|---|
| Tuning | Band ▾ | The feed presets without opening Tuning |
| | Frequency box (MHz) | Type a frequency and press Enter; bold, like SDR Console's frequency display |
| | Gain (dB) | The control people adjust most; Ray's half-height line was a gain question |
| | Sample rate ▾ | |
| Display | **Line view** (with its name) | The one display button people hunt for; Ray's use case |
| | Trace ▾ | Average / Peak / Both; the 1.8.0 detector |
| | Autoscale, Waterfall levels, Reset axes | Asked for every time the gain or band changes |
| | Max hold, Min hold, Reset holds | RFI hunting |
| | Clear markers | The marker mouse gestures are not discoverable; this makes them visible |

Toolbars are ordinary Qt toolbars. They can be dragged to another position, hidden from View ▸ Toolbars, and their positions are saved with the layout.

### 3.3 Status bar

Messages stay on the left, as now. On the right are fixed fields that are always visible:

`● REC 0:12:41 (47:19 left)` | `1420.4060 MHz` | `2 MS/s` | `gain 66 dB` | `HackRF 0000…` | `O: 0` | `UTC 03:12:44` | `LST 20:01:31`

- The REC field is red while recording, amber while armed ("starts in 1:12"), and grey otherwise.
- The overflow count turns red when samples are dropped.
- LST comes from `[site] lon_deg`, the same calculation the planner uses.
- Proposed (not drawn): when the Observation panel's consequences line turns amber (a risky geometry, or the hydrogen line out of band), the same warning also appears as an amber status-bar field. 1.5.2 taught that a warning must sit where the user is looking, and the consequences line lives in a panel that can be hidden.

### 3.4 Settings panels

The six panels are unchanged inside. They stack as tabs in one right-hand column, with the tab names written vertically down the right edge (Observation, Tuning, Radio, Recording, Spectrum, Waterfall), as in PyCharm or Visual Studio. One click shows a panel. F9 hides the whole column. A panel can still be dragged out to float (a second monitor) or docked on the left.

Why keep the panels and not move everything into dialogs: most settings in Tuning, Radio, Spectrum and Waterfall are adjusted while watching the plots, and a dialog in front of the plot hides what you are adjusting. Dialogs suit the settings you set once: those go to Preferences (stage 2).

The panels' insides do not move in stage 1. The test suites and bench tools reach into them by widget name, and keeping them where they are keeps those tools working.

### 3.5 Keyboard

New keys: Ctrl+R Record, Ctrl+F frequency box, Ctrl+Shift+A Autoscale, Ctrl+0 Reset axes, F9 panels, Ctrl+, Preferences, Ctrl+Q Exit. Existing keys stay: Ctrl+L, Ctrl+P, F1, F11. On macOS Qt turns Ctrl into ⌘ by itself. Cmd+H and Cmd+M are left alone because they belong to the system. Autoscale is not on Ctrl+A, which is Select All in every text box.

## 4. The choices, with a recommendation

### 4.1 The top of the window

| | A: two rows, names under the big icons (**recommended**) | A2: one row, icons only | B: ribbon |
|---|---|---|---|
| Looks like | Classic Windows / SDR# / GQRX | Compact SDR programs | SDR Console, Microsoft Office |
| Learnable | Names on the main buttons; tooltips on the small ones | Tooltips only | Names on everything |
| Height | About 75 px | About 30 px | About 115 px |
| Width needed | Row 1 about 1130 px, row 2 about 770 px: fits a 1280 screen | About 1340 px: on a 1280 screen the Display buttons fall behind the "»" (still in the menus) | Fits; less shown per tab |
| macOS | Native menu bar + toolbars: normal Mac look | Same | The Mac menu bar stays at the top of the screen anyway, so commands appear twice |
| Effort / risk | Standard Qt, low | Standard Qt, low | Qt has no ribbon. About 300+ lines of our own widget code, including its behaviour when the window is narrow and keyboard access. Medium |

**Why A:** it is the "menu + groups of shortcut icons" you described, it uses only standard Qt (low risk on the three platforms and over xrdp), and the names under the main buttons make it learnable for occasional users. A2 is not really a separate design: "names under icons / icons only" is one Qt setting and can be a View ▸ Toolbars choice. B is the closest to SDR Console. But all the commands live in one action list, so a ribbon can be added later on top of A without redoing anything (stage 3).

### 4.2 The settings panels

| | One tabbed column on the right (**recommended**) | Keep two columns (today) | Settings in dialogs |
|---|---|---|---|
| Plot width at 1440 | About 75% | 57% | About 100% |
| Seeing a panel | One click on its tab; one at a time | Several at once, all cut off | Opens a dialog over the plot |
| Risk | Low: the same docks, rearranged | None | High: rewrites every panel |

**Recommended:** one column by default, plus a View ▸ Panels ▸ "Two columns (1.8 layout)" item for anyone who wants the old arrangement back. The prototype confirmed that Qt can rebuild it (section 5).

### 4.3 Icons

**Recommended: drawn in code** (`prototype/proto_icons.py`, 37 icons; see the icon sheet). This is the same choice and the same reason as the panel title-bar icons in 1.2.0: Unicode glyphs failed on macOS and Linux. Drawn icons use no fonts, files or SVG plugin, need no licence, add one module to the release zip, are sharp at any screen scale, and use the house colours.

The alternative is a bundled icon set: Tabler (MIT), Lucide (ISC) or Material Symbols (Apache-2.0), all allowed in a GPL-3.0 program. Those sets have nothing for a pulsar, a drift scan, a max hold or a waterfall, and they need Qt's SVG image plugin present on every install, including the Pi's radioconda. That is one more thing to break silently.

Redraw requests are cheap: each icon is five to fifteen lines.

## 5. Saved layouts and settings

**Every settings.ini key keeps its name and meaning.** Nothing in the "deliberately not renamed" list in CLAUDE.md is touched. The dock object names (the keys inside the saved layout) stay the same; only the tab text is shorter ("Spectrum", "Waterfall").

**Tested** (`prototype/layout_compat.py`, real window, both directions):

1. The new window restoring a 1.8 `dock_state` (a fresh one, and a copy of yours read from your settings.ini, never written) succeeds. It puts the panels back in the old two columns, and the toolbars stay. So if the new version read the old key, every upgraded install would get toolbars **and** the old two columns, never the new arrangement.
2. A 1.8 window restoring a state saved by the new window also succeeds. It moves the Spectrum and Waterfall panels into the right column, even though 1.8 forbids that by its own rules (Qt does not check on restore). Harmless, but not what 1.8 should look like.
3. A state saved with version number 2 and restored asking for version 0 is refused cleanly, with no change.

**So:** the new version saves its layout under a **new key** (`[window] layout_v2`, saved as version 2) and never writes the old `dock_state`. On the first start after the update, everyone gets the new default arrangement. Going back to 1.8 finds its own old layout untouched. View ▸ Panels ▸ "Two columns (1.8 layout)" brings back the old arrangement deliberately. View ▸ Reset Window Layout replaces today's dock-back button as the way out of a tangled layout; the dock-back button stays.

The toolbar style (names under icons or not) is a new `[ui] toolbar_style` key.

## 6. Platforms

- **Windows:** the reference platform for the mockups (Windows 11, 150% scale).
- **macOS:** the menu bar becomes the system menu bar, as now. Qt moves items whose text looks like "About", "Preferences" or "Quit" into the application menu. Every action gets an explicit menu role so that happens only to About, Preferences and Exit, on purpose. The macOS "unified title and toolbar" look stays off at first (it changes how toolbars can be moved). Check on the Mac: menu placement, Cmd shortcuts, the toolbar drop-down buttons, vertical tab text.
- **Linux, the Pi (LXDE / Openbox over xrdp, 0 × 0 screen):** the toolbar drop-down buttons (Format, Trace, Band) and the combo boxes pop up the same way the panels' combo boxes do. Those collapsed on the 0 × 0 screen until the launcher's virtual monitor fixed it, so verify there that menus, drop-downs and the "»" overflow open properly. Also check the minimum window size over xrdp. Do the update at a natural scan break, as always.

## 7. Staged plan

| Stage | Contents | Visible change | Release |
|---|---|---|---|
| 0 | One action list module (`dses_actions.py` or inside the main file): every command as a QAction with icon, tooltip, key and state. The current menus are built from it. Drawn-icon module | None | Rides along with any release |
| 1 | Full menu tree; toolbars (A, with the names-under-icons choice); status-bar fields including the amber warning; one-column tabbed panels; `layout_v2` + "Two columns" + Reset Window Layout; Help "Control panels" section rewritten; Installing.md section 9 regenerated; guide screenshots | The new window | **1.9.0**, a feature release |
| 2 | Preferences dialog for the settings with no control (site, SEFD, ezRA geometry, sweep settle, update checks, elevation mask). Slim the Observation panel (its preset and mode are now on the toolbar) | Small | 1.9.x or 1.10.0 |
| 3 (optional) | Ribbon on the same action list; toolbar customising; tune step buttons | Optional | Later |

**Rough effort:** stages 0 and 1, two to three days including tests and the three-platform pass. Stage 2, about a day.

The 1.8 work in flight (Ray's HackRF gain round) is untouched by stages 0 and 1. Stage 1 should start once 1.8.x has settled.

## 8. Test plan

- **Existing suites must keep passing** (they use the panel widgets by name, which stage 1 keeps): test_display_app_smoke, test_trace_detector, test_rec_schedule, test_rec_series, test_gain_stages, test_startup_staged_radio, test_pulsar_planner_dialog, test_driftscan_review, test_display_sensitivity, test_fil_chain, test_ezra_chain, test_ezra_txt, test_timebase_padding, test_pulsar_planner, test_pulsar_sim, test_fold_analysis, test_build_doc, test_launcher, test_desktop_entry.
- **New: test_main_window_actions.py** (headless, playback, private settings):
  - every toolbar item is also in a menu, and has an icon and a tooltip;
  - each toolbar item and its panel control follow each other both ways (Line view, holds, Trace, Format, Live/Sweep, preset, gain, rate);
  - Record refuses in playback;
  - the layout round trip through `layout_v2`, and the old `dock_state` is left untouched;
  - "Two columns" restores the 1.8 arrangement;
  - menu roles are set on the macOS-sensitive items;
  - the window builds at 1024 × 700 (overflowed items are still in menus).
- **New: a startup test with a HackRF stand-in** (as test_startup_staged_radio): the toolbar's gain box and the Stages row agree. Startup order bit us in 1.8.1.
- **Hands-on:** Windows (you); the Mac (native menu bar, Cmd keys, drop-downs, vertical tabs); the Pi over xrdp (drop-downs on the 0 × 0 screen, overflow, layout restore, at a scan break); a live B210 and a Soapy radio (gain box and Stages row; Record, Stop and Schedule from the toolbar on a real recording).

## 9. Decisions for Rick

**Decided 20 November 2026 (Rick): layout A, and one tabbed settings column.** Items 3 to 5 proceed on the recommendations below unless Rick says otherwise. Item 6: built at once in a separate git worktree (Rick: "Worktree now"), so main stays free for 1.8.x fixes.

**Built the same afternoon (stages 0 and 1), not yet committed:** worktree `.claude/worktrees/gui-redesign`, branch `worktree-gui-redesign`, based on main at f26bdde. This design folder moved into the worktree with it (the copy left in the main checkout stops at the proposal).

- New modules: `dses_window.py` (action registry, menus, toolbars, status fields, panel arrangements) and `dses_icons.py` (the drawn icons). Both are in both make-release ship lists.
- `dses_workbench.py`: the old menu builder and column toggles are gone; the chrome is built last in `__init__`; the layout is saved under `[window] layout_v2`; the settings warning is published for the status bar; the Help section on the window is rewritten.
- New `test_main_window_actions.py` (about 70 checks).
- Pictures of the built window: `mockups/10_built_window.png` (1440 wide) and `mockups/11_built_window_1024.png`.

Differences from the mockup:
- The panel tabs keep their full names ("Spectrum Display").
- The status fields drop out one by one on a narrow window. With all of them shown the window could not go below 1040 px; the built window goes down to about 900 px (1.8: 650).

Not done yet: Installing.md section 9 regeneration (at the cut); ROADMAP and CLAUDE.md notes (at the merge, to avoid conflicts with main); hands-on on Windows, the Mac and the Pi; a live radio.

The prototype in `prototype/` dresses the 1.8 window; run it from the main checkout, not from this branch.

1. **Top of the window:** A (two rows, names under the big icons), A2 (one row of icons), or B (ribbon)? Recommendation: A.
2. **Settings panels:** one tabbed column on the right with a "Two columns" option, or keep today's two columns? Recommendation: one column.
3. **Icons:** drawn in code (as now), or a bundled open-source set? Recommendation: drawn. Mark any icon on the sheet you want redrawn.
4. **Saved layouts:** everyone starts on the new arrangement after the update (old layout kept, "Two columns" brings it back)? Recommendation: yes.
5. **Toolbar contents:** anything to add or remove? Candidates left out on purpose: tune up/down step buttons, colormap, FFT size, the planner's catalog refresh.
6. **Timing:** stage 0 + 1 as 1.9.0 once the 1.8.x HackRF round settles?

## Files

- `PROPOSAL.md`: this document
- `NOTES_WIP.md`: working notes from 19 November (the inventory and the reasoning trail)
- `mockups/`: pictures 00 to 04 and the icon sheet
- `prototype/proto_harness.py`: builds the real window with private settings and no radio
- `prototype/proto_redesign.py`: the proposed menus, toolbars, ribbon, status fields and panel arrangement, all driving the existing controls
- `prototype/proto_icons.py`: the drawn icons (run it to rewrite the sheet)
- `prototype/render_mockups.py`, `grab_current.py`: the pictures
- `prototype/run_prototype.py`: the clickable prototype
- `prototype/layout_compat.py`: the saved-layout tests in section 5
- `prototype/_proto_settings/`: the prototype's private settings (throwaway)
