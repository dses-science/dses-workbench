"""Main-window chrome for the DSES Radio Astronomy Workbench (1.9 redesign).

Approved by Rick 2026-11-20 (design/gui-redesign-2026-11/PROPOSAL.md in the
dev clone, layout "A" + one tabbed settings column):

  * ONE ACTION REGISTRY (`WindowChrome.A`): every command is a QAction with
    its icon, tooltip, shortcut and checked/enabled state. The menu bar and
    the toolbars are both built from it, so a toolbar command always has a
    menu entry too (a toolbar that runs out of width hides buttons behind
    its "»" — they stay reachable from the menus).
  * Every action DRIVES THE EXISTING PANEL CONTROL (or the slot that control
    calls) and listens back to it, so the toolbar, the menus and the panels
    can never disagree. The panels keep their widgets and their names: the
    test suites and bench tools reach into them.
  * Two toolbar rows: big icons with names (Recording, Observation, Tools,
    Window), then a compact row (Tuning, Display). "Show button names" in
    View > Toolbars turns the names off ([ui] toolbar_names).
  * Fixed status-bar fields: recording state, settings warning, frequency,
    rate, gain, radio, dropped samples, UTC, LST.
  * Panel arrangements: the default stacks all six panels as tabs in one
    right-hand column ("Two columns (1.8 layout)" brings the old one back).
    The window layout is saved under [window] layout_v2 (saveState version
    2); the 1.8 key dock_state is never read or written, so going back to
    1.8 finds its own layout untouched (see _save_geometry / showEvent).

PySide6 only. Icons come from dses_icons (drawn, no fonts or files).
"""
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from dses_icons import icon

LAYOUT_KEY = 'layout_v2'        # [window] key holding the saved arrangement
LAYOUT_VERSION = 2              # saveState/restoreState version for that key

FORMAT_SHORT = {'iq': "I/Q", 'fil': ".fil", 'ezra': "ezRA"}
SCIENCE_DOCKS = ("dock_observation", "dock_tuning", "dock_rx", "dock_recording")
DISPLAY_DOCKS = ("dock_spectrum_display", "dock_waterfall_display")
RIGHT_COLUMN_WIDTH = 345


class WindowChrome(QtCore.QObject):
    """Builds and owns the menus, toolbars and status fields of `win` (a
    finished dses_workbench main window whose panels already exist)."""

    def __init__(self, win):
        super().__init__(win)
        self.w = win
        self.A = {}
        self.toolbars = []
        self._panels_hidden_set = None
        self._ovf_count = 0
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_fields()
        # Tabbed panels show their names down the window's outer edge (the
        # PyCharm look): six horizontal tabs in a 345 px column are elided
        # to "Ob…", "Tu…".
        win.setTabPosition(Qt.RightDockWidgetArea, QtWidgets.QTabWidget.East)
        win.setTabPosition(Qt.LeftDockWidgetArea, QtWidgets.QTabWidget.West)
        self.set_button_names(win._app_settings.get_bool('ui', 'toolbar_names'))
        if win._playback_mode:
            self._apply_playback()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ================================================================ actions
    def _act(self, key, text, icon_name=None, tip="", shortcut=None,
             checkable=False, slot=None, role=None):
        a = QtGui.QAction(text, self.w)
        a.setObjectName("act_" + key)
        if icon_name:
            a.setIcon(icon(icon_name))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip.split("\n")[0])
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
        a.setCheckable(checkable)
        # macOS moves items whose TEXT looks like About / Preferences / Quit
        # into the application menu; say explicitly which ones may go there.
        a.setMenuRole(role if role is not None else QtGui.QAction.NoRole)
        if slot is not None:
            (a.toggled if checkable else a.triggered).connect(slot)
        self.A[key] = a
        return a

    @staticmethod
    def _set_checked(act, on):
        """Mirror a panel change into an action without re-triggering it."""
        if act.isChecked() != bool(on):
            act.blockSignals(True)
            act.setChecked(bool(on))
            act.blockSignals(False)

    def _status(self, text, ms=6000):
        self.w._status_bar.showMessage(text, ms)

    def _dock(self, objname):
        for d in self.w._docks:
            if d.objectName() == objname:
                return d
        return None

    def show_panel(self, objname, focus=None):
        d = self._dock(objname)
        if d is None:
            return
        d.show()
        d.raise_()
        if focus is not None:
            focus.setFocus()

    def _build_actions(self):
        w, fp, wf = self.w, self.w._fft_plot, self.w._waterfall_plot
        A = self._act

        # --- File
        A("set_folder", "Set Recording Folder…", "folder_set",
          "Choose where recordings are written", slot=w._on_change_recording_dir)
        A("open_folder", "Open Recordings Folder", "folder",
          "Open the recordings folder in the file browser",
          slot=lambda: QtGui.QDesktopServices.openUrl(
              QtCore.QUrl.fromLocalFile(w.recording_dir)))
        A("exit", "Exit", "exit", shortcut="Ctrl+Q",
          role=QtGui.QAction.QuitRole, slot=w.close)

        # --- Recording
        rec = A("record", "Record", "record",
                "Start recording in the selected format.\n"
                "With Start at set, this ARMS the recording instead; Repeat\n"
                "makes a series (Schedule… opens those settings).",
                shortcut="Ctrl+R", checkable=True)
        rec.toggled.connect(lambda on: w.set_record(1 if on else 0))
        w._record_combo_box.currentIndexChanged.connect(
            lambda i: self._set_checked(rec, i == 1))
        A("stop", "Stop", "stop",
          "Stop recording (also cancels an armed start or a repeating series)",
          slot=lambda: w.set_record(0))
        A("quicklook", "Quick look", "quicklook",
          "While a .fil recording runs: fold what has been captured so far,\n"
          "without stopping the recording", slot=w._on_quicklook_clicked)
        A("schedule", "Schedule…", "schedule",
          "Record for, Start at and Repeat — opens the Recording panel",
          slot=lambda: self.show_panel("dock_recording", w._rec_duration_edit))
        an = A("analyze", "Analyze When Done", None,
               "Run the PRESTO pipeline when a .fil recording stops",
               checkable=True, slot=w._analyze_check.setChecked)
        an.setChecked(w._analyze_check.isChecked())
        w._analyze_check.toggled.connect(lambda on: self._set_checked(an, on))
        self.fmt_group = QtGui.QActionGroup(w)
        for i, code in enumerate(w._record_format_options):
            a = A("fmt_" + code, w._record_format_labels[i], None, checkable=True)
            self.fmt_group.addAction(a)
            a.triggered.connect(
                lambda _c=False, i=i: w._record_format_combo.setCurrentIndex(i))
        A("format", "Format", "format",
          "Recording format: raw I/Q (SigMF), filterbank (.fil) or drift scan (ezRA)")
        w._record_format_combo.currentIndexChanged.connect(self._sync_format)
        self._sync_format(w._record_format_combo.currentIndex())

        # --- Observe
        self.mode_group = QtGui.QActionGroup(w)
        live = A("live", "Live", "live",
                 "Live spectrum at the tuned frequency", checkable=True)
        sweep = A("sweep", "Sweep", "sweep",
                  "Stepped wide scan from Start to Stop (RFI survey) — the\n"
                  "Sweep settings are in the Tuning panel", checkable=True)
        for a in (live, sweep):
            self.mode_group.addAction(a)
        live.setChecked(w._mode_live_btn.isChecked())
        sweep.setChecked(w._mode_sweep_btn.isChecked())
        live.triggered.connect(lambda: w._mode_live_btn.setChecked(True))
        sweep.triggered.connect(lambda: w._mode_sweep_btn.setChecked(True))
        w._mode_live_btn.toggled.connect(lambda on: on and self._set_checked(live, True))
        w._mode_sweep_btn.toggled.connect(lambda on: on and self._set_checked(sweep, True))
        A("planner", "Pulsars in View…", "planner",
          "Which pulsars are above the horizon here, with P0, DM, flux in the\n"
          "tuned band, and time left before each sets",
          shortcut="Ctrl+P", slot=lambda: w._show_pulsar_planner())
        A("refresh_cat", "Refresh Pulsar Catalog", None,
          "Re-download the ATNF catalog (cached locally; works offline afterwards)",
          slot=lambda: w._show_pulsar_planner(force_refresh=True))
        A("selftest", "B210 Self Test…", "selftest",
          "Transmit a simulated pulsar from the B210's own TX, record it,\n"
          "fold it with PRESTO and grade PASS/FAIL",
          slot=w._show_selftest_dialog)
        A("driftscan", "Drift-Scan Review…", "driftscan",
          "The morning-after report for recorded ezRA drift-scan files",
          slot=w._show_driftscan_review)

        # --- Radio
        A("device", "Change Device…", "device", "Pick a different attached radio",
          slot=w._on_change_device_clicked)
        A("set_freq", "Set Frequency…", None,
          "Type a centre frequency in the toolbar's frequency box",
          shortcut="Ctrl+F", slot=self._focus_freq_box)

        # --- View: display
        lv = A("lineview", "Line View", "lineview",
               "One click to watch the hydrogen line: about 1 kHz resolution, a\n"
               "two-second average, Average trace, zoom on 1420.406 MHz when it\n"
               "is in band. Click again to put your settings back.",
               shortcut="Ctrl+L", checkable=True, slot=fp.set_line_view)
        fp.line_view_changed.connect(lambda on: self._set_checked(lv, on))
        self.trace_group = QtGui.QActionGroup(w)
        for i in range(fp._trace_combo.count()):
            a = A("trace_" + fp._trace_combo.itemData(i), fp._trace_combo.itemText(i),
                  None, checkable=True)
            self.trace_group.addAction(a)
            a.triggered.connect(lambda _c=False, i=i: fp._trace_combo.setCurrentIndex(i))
        A("trace", "Trace", "trace",
          "How the trace is drawn when there are more FFT bins than pixels:\n"
          "Average (a thin line), Peak (the noise band), or both")
        fp._trace_combo.currentIndexChanged.connect(self._sync_trace)
        self._sync_trace(fp._trace_combo.currentIndex())
        A("autoscale", "Autoscale", "autoscale",
          "Fit the spectrum's Y axis to the trace", shortcut="Ctrl+Shift+A",
          slot=fp._on_autoscale_y)
        A("autoscale_wf", "Waterfall Levels", "autoscale_wf",
          "Fit the waterfall colour range to the current data",
          slot=wf._on_autoscale_clicked)
        A("reset_axes", "Reset Axes", "home",
          "Back to the full span and the default levels", shortcut="Ctrl+0",
          slot=fp.reset_axes)
        mx = A("maxhold", "Max Hold", "maxhold",
               "Hold the highest level seen in each bin", checkable=True,
               slot=fp._max_check.setChecked)
        mn = A("minhold", "Min Hold", "minhold",
               "Hold the lowest level seen in each bin", checkable=True,
               slot=fp._min_check.setChecked)
        mx.setChecked(fp._max_check.isChecked())
        mn.setChecked(fp._min_check.isChecked())
        fp._max_check.toggled.connect(lambda on: self._set_checked(mx, on))
        fp._min_check.toggled.connect(lambda on: self._set_checked(mn, on))
        A("reset_holds", "Reset Holds", "reset", "Start both hold traces again",
          slot=lambda: (fp.request_reset_max.emit(), fp.request_reset_min.emit()))
        A("markers", "Clear Markers", "markers_clear",
          "Remove markers A and B. On the plot: left-click places A,\n"
          "right-click places B, double-click clears both.",
          slot=fp.clear_markers)
        dk = A("darkbg", "Dark Background", "darkbg", "Dark or light plot background",
               checkable=True,
               slot=lambda on: (fp._dark_bg_check.setChecked(on),
                                wf._dark_bg_check.setChecked(on)))
        dk.setChecked(fp._dark_bg_check.isChecked())
        fp._dark_bg_check.toggled.connect(lambda on: self._set_checked(dk, on))
        gr = A("grid", "Grid", "grid", "Spectrum grid", checkable=True,
               slot=fp._grid_check.setChecked)
        gr.setChecked(fp._grid_check.isChecked())
        fp._grid_check.toggled.connect(lambda on: self._set_checked(gr, on))

        # --- View: window
        pa = A("panels", "Settings Panels", "panels",
               "Show or hide the settings panels — more room for the plots",
               shortcut="F9", checkable=True, slot=self.set_panels_visible)
        pa.setChecked(True)
        for d in w._docks:
            d.visibilityChanged.connect(self._refresh_panels_check)
        A("layout_default", "One Column (default)", None,
          "All settings panels as tabs in one column on the right",
          slot=lambda: self._restore_snapshot('_default_dock_state'))
        A("layout_classic", "Two Columns (1.8 layout)", None,
          "Display panels on the left, science panels on the right, as in 1.2–1.8",
          slot=lambda: self._restore_snapshot('_classic_dock_state'))
        A("layout_reset", "Reset Window Layout", "layout_reset",
          "Put the toolbars and panels back where they start",
          slot=lambda: self._restore_snapshot('_default_dock_state'))
        A("button_names", "Show Button Names", None,
          "Names under the big toolbar icons (off = icons only)", checkable=True,
          slot=self._on_button_names_toggled)
        A("fullscreen", "Full Screen", "fullscreen",
          shortcut=QtGui.QKeySequence.FullScreen, checkable=True,
          slot=lambda on: w.showFullScreen() if on else w.showNormal())

        # --- Help
        A("guide", "User Guide", "guide", "The in-app user guide",
          shortcut=QtGui.QKeySequence.HelpContents, slot=w._show_help_dialog)
        A("update", "Check for Updates…", "update", slot=w._check_for_updates_manual,
          role=QtGui.QAction.ApplicationSpecificRole)
        A("about", "About…", "about", slot=w._show_about_dialog,
          role=QtGui.QAction.AboutRole)

    def _sync_format(self, i):
        code = self.w._record_format_options[i]
        self.A["fmt_" + code].setChecked(True)
        a = self.A.get("format")
        if a is not None:
            a.setText("Format: " + FORMAT_SHORT[code])
            a.setIconText("Format: " + FORMAT_SHORT[code])

    def _sync_trace(self, i):
        code = self.w._fft_plot._trace_combo.itemData(i)
        if ("trace_" + str(code)) in self.A:
            self.A["trace_" + code].setChecked(True)

    def _focus_freq_box(self):
        box = getattr(self, "freq_box", None)
        if box is not None and box.isVisible():
            box.setFocus()
            box.selectAll()
        else:
            self.show_panel("dock_tuning", self.w._freq_manual_line_edit)

    # ---------------------------------------------------------------- panels
    def set_panels_visible(self, on):
        """Hide or show every settings panel. Hiding remembers which were
        open, so showing them again restores exactly that set (a panel the
        user had closed on purpose stays closed)."""
        docks = list(self.w._docks)
        if on:
            keep = self._panels_hidden_set
            for d in docks:
                d.setVisible(keep is None or d.objectName() in keep)
            self._panels_hidden_set = None
        else:
            self._panels_hidden_set = {d.objectName() for d in docks if d.isVisible()}
            for d in docks:
                d.setVisible(False)
        self._refresh_panels_check()

    def _refresh_panels_check(self, *_):
        try:
            any_vis = any(d.isVisible() for d in self.w._docks)
        except RuntimeError:            # docks already deleted (shutdown)
            return
        self._set_checked(self.A["panels"], any_vis)

    def arrange_default(self):
        """All six panels as tabs in ONE right-hand column, tab names down
        the right edge; the plots get the rest of the window."""
        w = self.w
        docks = list(w._docks)
        for d in docks:
            d.setFloating(False)
            w.removeDockWidget(d)
        first = docks[0]
        w.addDockWidget(Qt.RightDockWidgetArea, first)
        first.show()
        for d in docks[1:]:
            w.tabifyDockWidget(first, d)
            d.show()
        first.raise_()
        w.resizeDocks([first], [RIGHT_COLUMN_WIDTH], Qt.Horizontal)

    def arrange_classic(self):
        """The 1.2–1.8 arrangement: display panels stacked on the left,
        science panels stacked on the right."""
        w = self.w
        for d in list(w._docks):
            d.setFloating(False)
            w.removeDockWidget(d)
        left = [self._dock(n) for n in DISPLAY_DOCKS]
        right = [self._dock(n) for n in SCIENCE_DOCKS]
        for area, group in ((Qt.LeftDockWidgetArea, left), (Qt.RightDockWidgetArea, right)):
            for d in group:
                if d is not None:
                    w.addDockWidget(area, d)
                    d.show()

    def _restore_snapshot(self, attr):
        st = getattr(self.w, attr, None)
        if st is None:
            return
        self.w.restoreState(st)
        for d in self.w._docks:
            tb = d.titleBarWidget()
            if tb is not None and hasattr(tb, "_sync"):
                tb._sync()
        self._refresh_panels_check()

    # ================================================================= menus
    def _menu_from(self, menu, keys):
        for k in keys:
            if k == "-":
                menu.addSeparator()
            elif isinstance(k, tuple):            # (submenu title, [keys])
                self._menu_from(menu.addMenu(k[0]), k[1])
            else:
                menu.addAction(self.A[k])
        return menu

    def _fill_band_menu(self, m):
        w = self.w
        m.clear()
        grp = QtGui.QActionGroup(m)
        for v, lab in zip(w._freq_preset_options, w._freq_preset_labels):
            a = m.addAction(lab)
            a.setCheckable(True)
            a.setChecked(v == w.freq_preset)
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, v=v: w.set_freq_preset(v))

    def _fill_rate_menu(self, m):
        w = self.w
        m.clear()
        grp = QtGui.QActionGroup(m)
        cur = w._samp_rate_combo_box.currentIndex()
        for i, lab in enumerate(w._samp_rate_labels):
            a = m.addAction(lab)
            a.setCheckable(True)
            a.setChecked(i == cur)
            grp.addAction(a)
            a.triggered.connect(
                lambda _c=False, i=i: w._samp_rate_combo_box.setCurrentIndex(i))

    def _fill_preset_menu(self, m):
        w = self.w
        m.clear()
        for i in range(w._observation_combo.count()):
            a = m.addAction(w._observation_combo.itemText(i))
            a.setCheckable(True)
            a.setChecked(i == w._observation_combo.currentIndex())
            a.setEnabled(w._observation_combo.isEnabled())
            a.triggered.connect(lambda _c=False, i=i: self.apply_preset(i))

    def _dynamic_menu(self, parent, title, filler):
        m = parent.addMenu(title)
        m.aboutToShow.connect(lambda m=m: filler(m))
        filler(m)
        return m

    def apply_preset(self, i):
        """Same as choosing in the Observation panel's combo."""
        self.w._observation_combo.setCurrentIndex(i)
        self.w._apply_observation(i)

    def _build_menus(self):
        w = self.w
        bar = QtWidgets.QMenuBar(w)
        self.menus = {}

        def top(title):
            m = bar.addMenu(title)
            self.menus[title.replace("&", "")] = m
            return m

        self._menu_from(top("&File"), ["set_folder", "open_folder", "-", "exit"])

        view = top("&View")
        self.toolbars_menu = view.addMenu("Toolbars")
        panels = view.addMenu("Panels")
        for d in w._docks:
            panels.addAction(d.toggleViewAction())
        panels.addSeparator()
        self._menu_from(panels, ["layout_default", "layout_classic"])
        self._menu_from(view, ["panels", "-", "lineview",
                               ("Trace", ["trace_average", "trace_peak", "trace_both"]),
                               "maxhold", "minhold", "reset_holds", "-",
                               "autoscale", "autoscale_wf", "reset_axes", "markers", "-",
                               "darkbg", "grid", "-", "layout_reset", "fullscreen"])

        radio = top("&Radio")
        self._menu_from(radio, ["device", "-", "set_freq"])
        self._dynamic_menu(radio, "Band", self._fill_band_menu)
        self._dynamic_menu(radio, "Sample Rate", self._fill_rate_menu)
        opts = getattr(w, "_antenna_options", None) or []
        if len(opts) > 1 and hasattr(w, "_antenna_combo_box"):
            ant = radio.addMenu("Antenna")
            grp = QtGui.QActionGroup(ant)
            for i, n in enumerate(opts):
                a = ant.addAction(n)
                a.setCheckable(True)
                a.setChecked(i == w._antenna_combo_box.currentIndex())
                grp.addAction(a)
                a.triggered.connect(
                    lambda _c=False, i=i: w._antenna_combo_box.setCurrentIndex(i))
        radio.addSeparator()
        self._menu_from(radio, [("Mode", ["live", "sweep"])])

        obs = top("&Observe")
        self._dynamic_menu(obs, "Preset", self._fill_preset_menu)
        self._menu_from(obs, ["-", "planner", "refresh_cat", "-", "selftest", "-",
                              "driftscan"])

        self._menu_from(top("Recor&ding"),
                        ["record", "stop", "-",
                         ("Format", ["fmt_iq", "fmt_fil", "fmt_ezra"]),
                         "schedule", "-", "quicklook", "analyze", "-",
                         "open_folder", "set_folder"])
        self._menu_from(top("&Help"), ["guide", "-", "update", "about"])
        w.setMenuBar(bar)
        self.menubar = bar

    # ============================================================== toolbars
    def _tb(self, title, objname, big):
        tb = QtWidgets.QToolBar(title, self.w)
        tb.setObjectName(objname)                 # required by saveState()
        tb.setAllowedAreas(Qt.TopToolBarArea | Qt.BottomToolBarArea)
        tb.setFloatable(False)
        if sys.platform == "darwin":
            # macOS: no drag grip. A movable toolbar sets Qt::SizeAllCursor
            # when the mouse crosses its grip, and macOS has no system cursor
            # for that shape, so Qt builds one from a bitmap
            # (QCocoaCursor::createCursorData -> QImage::toCGImage); on the
            # Mac (macOS 27.2.1, Qt 6.11.1) that crashed the program with a
            # SIGSEGV inside CGImageCreate (1.9.0 crash report, 2026-11-22).
            # Without the grip Qt never asks for that cursor. Arrangement
            # stays available: View > Toolbars, Panels and Reset Window Layout.
            tb.setMovable(False)
        tb._dses_big = big
        self.toolbars_menu.addAction(tb.toggleViewAction())
        self.toolbars.append(tb)
        return tb

    def _menu_button(self, tb, act, menu):
        """A toolbar button that pops a menu (Format, Trace, Band)."""
        btn = QtWidgets.QToolButton(tb)
        btn.setDefaultAction(act)
        btn.setMenu(menu)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        tb.addWidget(btn)
        return btn

    def _build_toolbars(self):
        w, A = self.w, self.A
        tb = self._tb("Recording", "tb_recording", True)
        tb.addAction(A["record"])
        tb.addAction(A["stop"])
        self._menu_button(tb, A["format"],
                          self._menu_from(QtWidgets.QMenu(tb), ["fmt_iq", "fmt_fil", "fmt_ezra"]))
        tb.addAction(A["schedule"])
        tb.addAction(A["quicklook"])
        w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Observation", "tb_observation", True)
        self.preset_combo = QtWidgets.QComboBox()
        for i in range(w._observation_combo.count()):
            self.preset_combo.addItem(w._observation_combo.itemText(i))
        self.preset_combo.setCurrentIndex(w._observation_combo.currentIndex())
        self.preset_combo.setToolTip(w._observation_combo.toolTip())
        self.preset_combo.setMinimumContentsLength(18)
        self.preset_combo.activated.connect(self.apply_preset)
        w._observation_combo.currentIndexChanged.connect(self.preset_combo.setCurrentIndex)
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(4, 2, 4, 0)
        v.setSpacing(1)
        v.addWidget(self.preset_combo)
        self.preset_caption = QtWidgets.QLabel("Preset")
        self.preset_caption.setAlignment(Qt.AlignCenter)
        v.addWidget(self.preset_caption)
        tb.addWidget(box)
        tb.addAction(A["live"])
        tb.addAction(A["sweep"])
        w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Tools", "tb_tools", True)
        for k in ("planner", "selftest", "driftscan"):
            tb.addAction(A[k])
        w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Window", "tb_window", True)
        for k in ("panels", "fullscreen", "guide"):
            tb.addAction(A[k])
        w.addToolBar(Qt.TopToolBarArea, tb)

        w.addToolBarBreak(Qt.TopToolBarArea)

        tb = self._tb("Tuning", "tb_tuning", False)
        band_menu = QtWidgets.QMenu(tb)
        band_menu.aboutToShow.connect(lambda: self._fill_band_menu(band_menu))
        self._fill_band_menu(band_menu)
        band_act = QtGui.QAction(icon("band"), "Band", tb)
        band_act.setToolTip("Band (feed) presets — the same as the Tuning panel's Pulsar Band")
        self._menu_button(tb, band_act, band_menu)
        self.freq_box = QtWidgets.QLineEdit()
        self.freq_box.setObjectName("freq_box")
        self.freq_box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.freq_box.setMaximumWidth(125)
        self.freq_box.setToolTip(
            "Centre frequency in MHz. Type and press Enter (Ctrl+F jumps here).\n"
            "A plain number is MHz; 1.4204G or 1420406k also work.\n"
            "This sets the Tuning panel to Manual at that frequency.")
        f = self.freq_box.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        f.setBold(True)
        self.freq_box.setFont(f)
        self.freq_box.editingFinished.connect(self._on_freq_entered)
        tb.addWidget(self.freq_box)
        tb.addWidget(QtWidgets.QLabel(" MHz  "))
        g = QtWidgets.QLabel()
        g.setPixmap(icon("gain").pixmap(18, 18))
        g.setToolTip("RX gain")
        tb.addWidget(g)
        self.gain_spin = QtWidgets.QDoubleSpinBox()
        self.gain_spin.setObjectName("gain_spin")
        rng = w._gain_range
        self.gain_spin.setDecimals(0 if float(rng.step) >= 1 else 1)
        self.gain_spin.setRange(float(rng.min), float(rng.max))
        self.gain_spin.setSingleStep(float(rng.step) or 1.0)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setValue(float(w.gain))
        self.gain_spin.setKeyboardTracking(False)
        self.gain_spin.setToolTip(
            "RX gain — the same control as the Radio panel's RX Gain\n"
            "(on a HackRF the Radio panel's Stages row shows the split)")
        self.gain_spin.valueChanged.connect(self._on_gain)
        w._gain_win._spin.valueChanged.connect(self._mirror_gain)
        tb.addWidget(self.gain_spin)
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.setObjectName("rate_combo")
        self.rate_combo.addItems(w._samp_rate_labels)
        self.rate_combo.setCurrentIndex(w._samp_rate_combo_box.currentIndex())
        self.rate_combo.setToolTip("Sample rate — the same control as the Radio panel's")
        self.rate_combo.currentIndexChanged.connect(w._samp_rate_combo_box.setCurrentIndex)
        w._samp_rate_combo_box.currentIndexChanged.connect(self._mirror_rate)
        tb.addWidget(self.rate_combo)
        w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Display", "tb_display", False)
        tb.addAction(A["lineview"])
        self.lineview_button = tb.widgetForAction(A["lineview"])
        self._menu_button(tb, A["trace"],
                          self._menu_from(QtWidgets.QMenu(tb),
                                          ["trace_average", "trace_peak", "trace_both"]))
        tb.addSeparator()
        for k in ("autoscale", "autoscale_wf", "reset_axes"):
            tb.addAction(A[k])
        tb.addSeparator()
        for k in ("maxhold", "minhold", "reset_holds"):
            tb.addAction(A[k])
        tb.addSeparator()
        tb.addAction(A["markers"])
        w.addToolBar(Qt.TopToolBarArea, tb)
        self.toolbars_menu.addSeparator()
        self.toolbars_menu.addAction(A["button_names"])

    def set_button_names(self, on):
        """Big toolbars: names under the icons (on) or icons only (off).
        The compact row is always icons only, except Line View, which keeps
        its name beside the icon while names are on."""
        on = bool(on)
        self._set_checked(self.A["button_names"], on)
        for tb in self.toolbars:
            if tb._dses_big:
                tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon if on
                                      else Qt.ToolButtonIconOnly)
                tb.setIconSize(QtCore.QSize(24, 24) if on else QtCore.QSize(20, 20))
            else:
                tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
                tb.setIconSize(QtCore.QSize(20, 20))
            for b in tb.findChildren(QtWidgets.QToolButton):
                if b.menu() is not None and b.parent() is tb:
                    b.setToolButtonStyle(tb.toolButtonStyle())
        if self.lineview_button is not None:
            self.lineview_button.setToolButtonStyle(
                Qt.ToolButtonTextBesideIcon if on else Qt.ToolButtonIconOnly)
        self.preset_caption.setVisible(on)

    def _on_button_names_toggled(self, on):
        self.set_button_names(on)
        self.w._save_setting('ui', 'toolbar_names', bool(on))

    def _on_freq_entered(self):
        w = self.w
        txt = self.freq_box.text().strip().replace(",", "")
        if not txt:
            return self.refresh()
        try:
            hz = float(txt) * 1e6               # a plain number is MHz
        except ValueError:
            hz = w._parse_eng_or_none(txt)      # 1.4204G, 1420406k, 1420.4MHz
        if hz is None or hz <= 0:
            self._status(f"Not a frequency: {txt!r}")
            return self.refresh()
        if abs(hz - w.center_freq) < 0.5 and w.freq_preset == 0:
            return self.refresh()
        w.set_freq_preset(0)
        w.set_freq_manual(hz - w.freq_offset - w.freq_offset_0)
        self.refresh()

    def _on_gain(self, v):
        w = self.w
        if abs(float(v) - float(w.gain)) < 1e-9:
            return
        w._gain_win.set_value(v)
        w.set_gain(v)

    def _mirror_gain(self, v):
        if abs(self.gain_spin.value() - float(v)) > 1e-9:
            self.gain_spin.blockSignals(True)
            self.gain_spin.setValue(float(v))
            self.gain_spin.blockSignals(False)

    def _mirror_rate(self, i):
        if self.rate_combo.currentIndex() != i and 0 <= i < self.rate_combo.count():
            self.rate_combo.blockSignals(True)
            self.rate_combo.setCurrentIndex(i)
            self.rate_combo.blockSignals(False)

    def _apply_playback(self):
        """Same rules as the panels in playback: no recording, no gain or
        rate (they come from the file), no Sweep, no Self Test."""
        why = "Not available in playback (no radio attached)"
        for k in ("record", "stop", "quicklook", "schedule", "sweep",
                  "fmt_iq", "fmt_fil", "fmt_ezra", "format"):
            self.A[k].setEnabled(False)
            self.A[k].setToolTip(why)
        for wdg in (self.gain_spin, self.rate_combo):
            wdg.setEnabled(False)
            wdg.setToolTip(why)

    # ============================================================ status bar
    def _build_status_fields(self):
        sb = self.w._status_bar

        def field(name, tip, mono=False):
            lab = QtWidgets.QLabel()
            lab.setObjectName(name)
            lab.setToolTip(tip)
            lab.setContentsMargins(6, 0, 6, 0)
            if mono:
                lab.setStyleSheet("font-family: Consolas, Menlo, 'DejaVu Sans Mono', monospace;")
            return lab

        # Recording: drawn red dot (no font glyph) + text.
        self.f_rec = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(self.f_rec)
        h.setContentsMargins(6, 0, 6, 0)
        h.setSpacing(4)
        self.f_rec_dot = QtWidgets.QLabel()
        self.f_rec_dot.setPixmap(icon("record").pixmap(14, 14))
        self.f_rec_text = QtWidgets.QLabel()
        self.f_rec_text.setObjectName("status_rec")
        h.addWidget(self.f_rec_dot)
        h.addWidget(self.f_rec_text)
        self.f_rec.setToolTip("Recording state: elapsed and time left, or the armed start")
        sb.addPermanentWidget(self.f_rec)

        self.f_warn = field("status_warn", "")
        self.f_warn.setStyleSheet("color: #7a4300; background: #fde9b8; font-weight: 600;"
                                  " border-radius: 3px; padding: 1px 6px;")
        sb.addPermanentWidget(self.f_warn)
        self.f_freq = field("status_freq", "Centre frequency")
        self.f_rate = field("status_rate", "Sample rate")
        self.f_gain = field("status_gain", "RX gain")
        self.f_dev = field("status_dev", "The radio in use")
        self.f_ovf = field("status_ovf",
                           "Dropped-sample (overflow) events since the program started.\n"
                           "The overflow box in the Recording panel shows the recent ones.")
        self.f_utc = field("status_utc", "Coordinated Universal Time", mono=True)
        self.f_lst = field("status_lst",
                           "Local sidereal time at the site ([site] lon_deg) — the\n"
                           "right ascension crossing the meridian now", mono=True)
        for lab in (self.f_freq, self.f_rate, self.f_gain, self.f_dev, self.f_ovf,
                    self.f_utc, self.f_lst):
            sb.addPermanentWidget(lab)
        mon = getattr(self.w, "_overflow_monitor", None)
        if mon is not None:
            mon.chars_received.connect(self._count_overflows)
        # A narrow window drops the fields it cannot fit instead of being
        # held wide by them (all of them shown = about 1040 px; the 1.8
        # window could shrink to 650). Least useful first; the recording
        # state and the settings warning are never dropped. Each droppable
        # field claims no minimum width so the window can still shrink and
        # tell us, through the status bar's resize, what room there is.
        self._drop_order = [self.f_dev, self.f_utc, self.f_rate, self.f_gain,
                            self.f_lst, self.f_ovf, self.f_freq]
        for lab in self._drop_order:
            lab.setMinimumWidth(1)
        sb.installEventFilter(self)

    STATUS_MESSAGE_ROOM = 260       # px kept for the messages on the left

    def eventFilter(self, obj, ev):
        if obj is self.w._status_bar and ev.type() == QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0, self._fit_status_fields)
        return False

    def _fit_status_fields(self):
        sb = self.w._status_bar
        room = sb.width() - self.STATUS_MESSAGE_ROOM
        for fixed in (self.f_rec, self.f_warn):
            if fixed.isVisibleTo(self.w):
                room -= fixed.sizeHint().width() + 4
        for lab in reversed(self._drop_order):          # most useful first
            need = lab.sizeHint().width() + 4
            keep = need <= room
            if keep:
                room -= need
            if lab.isVisibleTo(self.w) != keep:
                lab.setVisible(keep)

    def _count_overflows(self, chars):
        n = str(chars).count("O")
        if n:
            self._ovf_count += n
            self._paint_ovf()

    def _paint_ovf(self):
        self.f_ovf.setText(f"O: {self._ovf_count}")
        self.f_ovf.setStyleSheet("color: #c0392b; font-weight: 600;" if self._ovf_count
                                 else "color: #5b6b7a;")

    def refresh(self):
        """1 Hz: status fields, the frequency box, and states that have no
        change signal of their own (Quick look's availability)."""
        w = self.w
        if not self.freq_box.hasFocus():
            self.freq_box.setText(f"{w.center_freq / 1e6:.6f}")
        if not w._playback_mode:
            self.A["quicklook"].setEnabled(w._quicklook_btn.isEnabled())
        self.A["sweep"].setEnabled(w._mode_sweep_btn.isEnabled() and not w._playback_mode)
        self.f_freq.setText(f"{w.center_freq / 1e6:.4f} MHz")
        self.f_rate.setText(f"{w.samp_rate / 1e6:g} MS/s")
        self.f_gain.setText("gain —" if w._playback_mode else f"gain {w.gain:g} dB")
        if w._playback_mode:
            self.f_dev.setText("Playback")
        else:
            self.f_dev.setText(w._source.display_label if w._source is not None else "—")
        self._paint_ovf()
        now = time.time()
        self.f_utc.setText(time.strftime("UTC %H:%M:%S", time.gmtime(now)))
        try:
            import pulsar_planner as pp
            lst = pp.lst_hours(now, w._app_settings.get_float('site', 'lon_deg'))
            s = int(round(lst * 3600)) % 86400
            self.f_lst.setText(f"LST {s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        except Exception:
            self.f_lst.setText("LST —")
        warn = getattr(w, "_consequences_warning", None)
        self.f_warn.setVisible(bool(warn))
        if warn:
            self.f_warn.setText("Check settings")
            self.f_warn.setToolTip("The Observation panel's settings check says: "
                                   + warn + ".")
        self._paint_rec(now)
        self._fit_status_fields()

    def _paint_rec(self, now):
        w = self.w
        red = ("color: white; background: #c0392b; font-weight: 700;"
               " border-radius: 3px; padding: 1px 6px;")
        if getattr(w, "_rec_start_time", None) and w.record:
            el = time.monotonic() - w._rec_start_time
            txt = "REC " + w._fmt_hms(el)
            if getattr(w, "_rec_duration_s", 0) > 0:
                txt += f"  ({w._fmt_hms(max(0, w._rec_duration_s - el))} left)"
            self.f_rec_dot.setVisible(True)
            self.f_rec_text.setText(txt)
            self.f_rec_text.setStyleSheet(red)
        elif getattr(w, "_rec_sched_ts", None):
            left = max(0, round(w._rec_sched_ts - now))
            self.f_rec_dot.setVisible(False)
            self.f_rec_text.setText("Armed: starts in " + w._fmt_hms(left))
            self.f_rec_text.setStyleSheet("color: #7a4300; background: #fde9b8;"
                                          " font-weight: 600; border-radius: 3px;"
                                          " padding: 1px 6px;")
        else:
            self.f_rec_dot.setVisible(False)
            self.f_rec_text.setText("Not recording")
            self.f_rec_text.setStyleSheet("color: #5b6b7a;")
