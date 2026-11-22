"""GUI-redesign prototype: dress the REAL main window in the proposed layout.

Design step only. Nothing here edits dses_workbench.py: apply(win, variant)
takes a finished window and adds, on top of it,
  * one ACTION REGISTRY (every command is a QAction with an icon, tooltip,
    shortcut and enabled/checked state), from which the menus, the toolbars
    and the ribbon are all built — so a command can never exist in a toolbar
    without also being in a menu;
  * the full menu bar;
  * the toolbars (variant 'A' = two rows, text under the big icons;
    'A2' = one row, icons only) or a ribbon (variant 'B');
  * fixed status-bar fields (REC, frequency, rate, gain, device, overflows,
    UTC, LST);
  * the six existing panels re-stacked as tabs in ONE right-hand column.

Every action drives the EXISTING panel control (or the same slot the panel
uses), and listens back, so panel and toolbar can never disagree. That is
the stage-1 pattern the proposal recommends.

PySide6 only.
"""
import os
import time

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from proto_icons import icon

FORMAT_SHORT = {'iq': "I/Q", 'fil': ".fil", 'ezra': "ezRA"}
PANEL_SHORT = {            # tab text only; objectName (the saved-layout key) unchanged
    "dock_observation": "Observation",
    "dock_tuning": "Tuning",
    "dock_rx": "Radio",
    "dock_recording": "Recording",
    "dock_spectrum_display": "Spectrum",
    "dock_waterfall_display": "Waterfall",
}


class Redesign(QtCore.QObject):
    def __init__(self, win, variant="A", illustrate_rec=False):
        super().__init__(win)
        self.w = win
        self.variant = variant
        self.illustrate_rec = illustrate_rec
        self.A = {}                      # action registry
        self._build_actions()
        self._build_menus()
        if variant == "B":
            self._build_ribbon()
        else:
            self._build_toolbars(labelled=(variant == "A"))
        self._build_status_fields()
        self._arrange_panels()

    # ------------------------------------------------------------ helpers
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
        # into the application menu. Say what each one is, explicitly.
        a.setMenuRole(role if role is not None else QtGui.QAction.NoRole)
        if slot is not None:
            (a.toggled if checkable else a.triggered).connect(slot)
        self.A[key] = a
        return a

    def _status(self, text, ms=6000):
        self.w._status_bar.showMessage(text, ms)

    def _playback_guard(self, what):
        if self.w._playback_mode:
            self._status(f"{what}: not available in playback (no radio attached).")
            return True
        return False

    def _show_panel(self, objname, focus=None):
        for d in self.w._docks:
            if d.objectName() == objname:
                d.show()
                d.raise_()
                if focus is not None:
                    focus.setFocus()
                return

    # ------------------------------------------------------------ actions
    def _build_actions(self):
        w, fp, wf = self.w, self.w._fft_plot, self.w._waterfall_plot
        A = self._act

        # File
        A("set_folder", "Set Recording Folder…", "folder_set",
          "Choose where recordings are written", slot=w._on_change_recording_dir)
        A("open_folder", "Open Recordings Folder", "folder",
          "Open the recordings folder in the file browser",
          slot=lambda: QtGui.QDesktopServices.openUrl(
              QtCore.QUrl.fromLocalFile(w.recording_dir)))
        A("prefs", "Preferences…", "prefs",
          "Site, ezRA geometry, sweep settle time, update checks — the settings\n"
          "that today live only in settings.ini (stage 2)",
          shortcut="Ctrl+,",
          role=QtGui.QAction.PreferencesRole,
          slot=lambda: self._status("Preferences dialog: stage 2 of the plan."))
        A("exit", "Exit", "exit", shortcut="Ctrl+Q",
          role=QtGui.QAction.QuitRole, slot=w.close)

        # Recording
        rec = A("record", "Record", "record",
                "Start recording (Start at / Repeat in the Recording panel apply)",
                shortcut="Ctrl+R", checkable=True)
        rec.toggled.connect(self._on_record_toggled)
        w._record_combo_box.currentIndexChanged.connect(
            lambda i: self._set_checked(rec, i == 1))
        A("stop", "Stop", "stop", "Stop recording (also cancels an armed start or a series)",
          slot=lambda: (self._set_checked(rec, False), w.set_record(0)))
        A("quicklook", "Quick look", "quicklook",
          "Fold what this .fil recording has captured so far, without stopping it",
          slot=w._on_quicklook_clicked)
        A("schedule", "Schedule…", "schedule",
          "Record for / Start at / Repeat — opens the Recording panel",
          slot=lambda: self._show_panel("dock_recording", w._rec_duration_edit))
        A("analyze", "Analyze When Done", None,
          "Run the PRESTO pipeline when a .fil recording stops", checkable=True,
          slot=w._analyze_check.setChecked)
        self.A["analyze"].setChecked(w._analyze_check.isChecked())
        w._analyze_check.toggled.connect(lambda on: self._set_checked(self.A["analyze"], on))
        self.fmt_group = QtGui.QActionGroup(self.w)
        for i, code in enumerate(w._record_format_options):
            a = A("fmt_" + code, w._record_format_labels[i], None, checkable=True)
            self.fmt_group.addAction(a)
            a.triggered.connect(lambda _c=False, i=i: w._record_format_combo.setCurrentIndex(i))
        w._record_format_combo.currentIndexChanged.connect(self._sync_format)
        A("format", "Format", "format", "Recording format: raw I/Q, filterbank .fil, ezRA drift scan")
        self._sync_format(w._record_format_combo.currentIndex())

        # Observe
        self.mode_group = QtGui.QActionGroup(self.w)
        live = A("live", "Live", "live", "Live spectrum at the tuned frequency", checkable=True)
        sweep = A("sweep", "Sweep", "sweep", "Stepped wide scan from Start to Stop (RFI survey)",
                  checkable=True)
        for a in (live, sweep):
            self.mode_group.addAction(a)
        live.setChecked(w._mode_live_btn.isChecked())
        sweep.setChecked(w._mode_sweep_btn.isChecked())
        live.triggered.connect(lambda: w._mode_live_btn.setChecked(True))
        sweep.triggered.connect(lambda: w._mode_sweep_btn.setChecked(True))
        w._mode_live_btn.toggled.connect(lambda on: on and self._set_checked(live, True))
        w._mode_sweep_btn.toggled.connect(lambda on: on and self._set_checked(sweep, True))
        sweep.setEnabled(w._mode_sweep_btn.isEnabled())
        A("planner", "Pulsars in View…", "planner",
          "Which pulsars are up now, flux in the tuned band, time left before each sets",
          shortcut="Ctrl+P", slot=lambda: w._show_pulsar_planner())
        A("refresh_cat", "Refresh Pulsar Catalog", None,
          slot=lambda: w._show_pulsar_planner(force_refresh=True))
        A("selftest", "B210 Self Test…", "selftest",
          "Transmit a simulated pulsar from the B210 and grade the whole chain",
          slot=w._show_selftest_dialog)
        A("driftscan", "Drift-Scan Review…", "driftscan",
          "The morning-after report for recorded ezRA drift-scan files",
          slot=w._show_driftscan_review)

        # Radio
        A("device", "Change Device…", "device", "Pick a different attached radio",
          slot=w._on_change_device_clicked)
        A("set_freq", "Set Frequency…", None, "Type a centre frequency", shortcut="Ctrl+F",
          slot=self._focus_freq_box)

        # View / display
        lv = A("lineview", "Line View", "lineview",
               "One click to watch the hydrogen line: ~1 kHz resolution, a 2-second\n"
               "average, Average trace, zoom on 1420.406 MHz. Click again to undo.",
               shortcut="Ctrl+L", checkable=True, slot=fp.set_line_view)
        fp.line_view_changed.connect(lambda on: self._set_checked(lv, on))
        self.trace_group = QtGui.QActionGroup(self.w)
        for i in range(fp._trace_combo.count()):
            a = A("trace_" + fp._trace_combo.itemData(i), fp._trace_combo.itemText(i),
                  None, checkable=True)
            self.trace_group.addAction(a)
            a.triggered.connect(lambda _c=False, i=i: fp._trace_combo.setCurrentIndex(i))
        fp._trace_combo.currentIndexChanged.connect(self._sync_trace)
        A("trace", "Trace", "trace", "How bins are drawn: Average / Peak / Average + peak")
        self._sync_trace(fp._trace_combo.currentIndex())
        A("autoscale", "Autoscale", "autoscale", "Fit the spectrum Y axis to the trace",
          shortcut="Ctrl+Shift+A", slot=fp._on_autoscale_y)
        A("autoscale_wf", "Waterfall levels", "autoscale_wf",
          "Fit the waterfall colour range to the current data", slot=wf._on_autoscale_clicked)
        mx = A("maxhold", "Max hold", "maxhold", "Hold the highest level seen in each bin",
               checkable=True, slot=fp._max_check.setChecked)
        mn = A("minhold", "Min hold", "minhold", "Hold the lowest level seen in each bin",
               checkable=True, slot=fp._min_check.setChecked)
        mx.setChecked(fp._max_check.isChecked()); mn.setChecked(fp._min_check.isChecked())
        fp._max_check.toggled.connect(lambda on: self._set_checked(mx, on))
        fp._min_check.toggled.connect(lambda on: self._set_checked(mn, on))
        A("reset_holds", "Reset holds", "reset", "Start both hold traces again",
          slot=lambda: (fp.request_reset_max.emit(), fp.request_reset_min.emit()))
        A("markers", "Clear markers", "markers_clear",
          "Remove markers A and B (also: double-click the plot)", slot=fp.clear_markers)
        A("reset_axes", "Reset axes", "home", "Back to the full span and the default levels",
          shortcut="Ctrl+0", slot=fp.reset_axes)
        A("darkbg", "Dark Background", "darkbg", checkable=True,
          slot=lambda on: (fp._dark_bg_check.setChecked(on), wf._dark_bg_check.setChecked(on)))
        self.A["darkbg"].setChecked(fp._dark_bg_check.isChecked())
        A("grid", "Grid", "grid", checkable=True, slot=fp._grid_check.setChecked)
        self.A["grid"].setChecked(fp._grid_check.isChecked())

        # Window
        A("panels", "Settings Panels", "panels",
          "Show or hide the settings panels (more room for the plots)",
          shortcut="F9", checkable=True, slot=self._set_panels_visible)
        self.A["panels"].setChecked(True)
        A("layout_reset", "Reset Window Layout", "layout_reset",
          "Put toolbars and panels back where they started")
        A("fullscreen", "Full Screen", "fullscreen", shortcut=QtGui.QKeySequence.FullScreen,
          checkable=True,
          slot=lambda on: w.showFullScreen() if on else w.showNormal())

        # Help
        A("guide", "User Guide", "guide", shortcut=QtGui.QKeySequence.HelpContents,
          slot=w._show_help_dialog)
        A("update", "Check for Updates…", "update", slot=w._check_for_updates_manual,
          role=QtGui.QAction.ApplicationSpecificRole)
        A("about", "About…", "about", slot=w._show_about_dialog,
          role=QtGui.QAction.AboutRole)

    def _set_checked(self, act, on):
        if act.isChecked() != bool(on):
            act.blockSignals(True)
            act.setChecked(bool(on))
            act.blockSignals(False)
            for b in getattr(act, "_mirror_buttons", []):
                b.setChecked(bool(on))

    def _on_record_toggled(self, on):
        if on and self._playback_guard("Record"):
            self._set_checked(self.A["record"], False)
            return
        self.w.set_record(1 if on else 0)

    def _sync_format(self, i):
        code = self.w._record_format_options[i]
        self.A["fmt_" + code].setChecked(True)
        a = self.A.get("format")
        if a is not None:
            a.setText(f"Format: {FORMAT_SHORT[code]}")
            a.setIconText("Format: " + FORMAT_SHORT[code])

    def _sync_trace(self, i):
        code = self.w._fft_plot._trace_combo.itemData(i)
        self.A["trace_" + code].setChecked(True)
        a = self.A.get("trace")
        if a is not None:
            a.setIconText({"average": "Average", "peak": "Peak", "both": "Both"}[code])

    def _set_panels_visible(self, on):
        for d in self.w._docks:
            d.setVisible(on)

    def _focus_freq_box(self):
        box = getattr(self, "freq_box", None)
        if box is not None:
            box.setFocus()
            box.selectAll()

    # -------------------------------------------------------------- menus
    def _menu_from(self, menu, keys):
        for k in keys:
            if k == "-":
                menu.addSeparator()
            elif isinstance(k, tuple):            # (submenu title, [keys])
                sub = menu.addMenu(k[0])
                self._menu_from(sub, k[1])
            elif k in self.A:
                menu.addAction(self.A[k])
        return menu

    def _band_menu(self, parent_menu=None):
        w = self.w
        m = QtWidgets.QMenu("Band", self.w) if parent_menu is None else parent_menu
        grp = QtGui.QActionGroup(m)
        for v, lab in zip(w._freq_preset_options, w._freq_preset_labels):
            a = m.addAction(lab)
            a.setCheckable(True)
            a.setChecked(v == w.freq_preset)
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, v=v: self._set_band(v))
        return m

    def _set_band(self, v):
        self.w.set_freq_preset(v)
        self.w._freq_preset_callback(v)
        self._refresh_fields()

    def _rate_menu(self, parent_menu):
        w = self.w
        grp = QtGui.QActionGroup(parent_menu)
        for i, lab in enumerate(w._samp_rate_labels):
            a = parent_menu.addAction(lab)
            a.setCheckable(True)
            a.setChecked(i == w._samp_rate_combo_box.currentIndex())
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, i=i: w._samp_rate_combo_box.setCurrentIndex(i))
        return parent_menu

    def _panel_menu(self, menu):
        for d in self.w._docks:
            a = d.toggleViewAction()
            a.setText(PANEL_SHORT.get(d.objectName(), d.windowTitle()))
            menu.addAction(a)
        return menu

    def _build_menus(self):
        bar = QtWidgets.QMenuBar(self.w)
        self.top_menus = []
        _add = bar.addMenu
        def _keep(title):
            m = _add(title)
            self.top_menus.append(m)
            return m
        bar.addMenu = _keep
        self._menu_from(bar.addMenu("&File"),
                        ["set_folder", "open_folder", "-", "prefs", "-", "exit"])
        view = bar.addMenu("&View")
        self.toolbars_menu = view.addMenu("Toolbars")
        self._panel_menu(view.addMenu("Panels"))
        self._menu_from(view, ["panels", "-", "lineview",
                               ("Trace", ["trace_average", "trace_peak", "trace_both"]),
                               "maxhold", "minhold", "reset_holds", "-",
                               "autoscale", "autoscale_wf", "reset_axes", "markers", "-",
                               "darkbg", "grid", "-", "layout_reset", "fullscreen"])
        radio = bar.addMenu("&Radio")
        self._menu_from(radio, ["device", "-", "set_freq"])
        self._band_menu(radio.addMenu("Band"))
        self._rate_menu(radio.addMenu("Sample Rate"))
        if getattr(self.w, "_antenna_options", None) and len(self.w._antenna_options) > 1:
            ant = radio.addMenu("Antenna")
            for i, n in enumerate(self.w._antenna_options):
                ant.addAction(n).triggered.connect(
                    lambda _c=False, i=i: self.w._antenna_combo_box.setCurrentIndex(i))
        radio.addSeparator()
        self._menu_from(radio, [("Mode", ["live", "sweep"])])
        obs = bar.addMenu("&Observe")
        pre = obs.addMenu("Preset")
        for i in range(self.w._observation_combo.count()):
            pre.addAction(self.w._observation_combo.itemText(i)).triggered.connect(
                lambda _c=False, i=i: self._apply_preset(i))
        self._menu_from(obs, ["-", "planner", "refresh_cat", "-", "selftest", "-",
                              "driftscan"])
        self._menu_from(bar.addMenu("Recor&ding"),
                        ["record", "stop", "-",
                         ("Format", ["fmt_iq", "fmt_fil", "fmt_ezra"]),
                         "schedule", "-", "quicklook", "analyze", "-",
                         "open_folder", "set_folder"])
        self._menu_from(bar.addMenu("&Help"), ["guide", "-", "update", "about"])
        self.w.setMenuBar(bar)
        self.menubar = bar

    def _apply_preset(self, i):
        self.w._observation_combo.setCurrentIndex(i)
        self.w._apply_observation(i)
        self._refresh_fields()

    # ----------------------------------------------------------- toolbars
    def _tb(self, title, objname, style, icon_px):
        tb = QtWidgets.QToolBar(title, self.w)
        tb.setObjectName(objname)                 # required by saveState()
        tb.setToolButtonStyle(style)
        tb.setIconSize(QtCore.QSize(icon_px, icon_px))
        tb.setAllowedAreas(Qt.TopToolBarArea | Qt.BottomToolBarArea)
        tb.setFloatable(False)
        self.toolbars_menu.addAction(tb.toggleViewAction())
        return tb

    def _menu_button(self, tb, act, menu_keys=None, menu=None):
        """A toolbar button that pops a menu (format, trace, band)."""
        btn = QtWidgets.QToolButton(tb)
        btn.setDefaultAction(act)
        if menu is None:
            menu = self._menu_from(QtWidgets.QMenu(btn), menu_keys)
        btn.setMenu(menu)
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        btn.setToolButtonStyle(tb.toolButtonStyle())
        tb.addWidget(btn)
        return btn

    def _preset_combo(self):
        c = QtWidgets.QComboBox()
        for i in range(self.w._observation_combo.count()):
            c.addItem(self.w._observation_combo.itemText(i))
        c.setCurrentIndex(self.w._observation_combo.currentIndex())
        c.setToolTip(self.w._observation_combo.toolTip())
        c.setMinimumContentsLength(18)
        c.activated.connect(self._apply_preset)
        self.w._observation_combo.currentIndexChanged.connect(c.setCurrentIndex)
        return c

    def _freq_widgets(self, tb, compact=False):
        w = self.w
        band = self._menu_button(tb, QtGui.QAction(icon("band"), "Band", tb),
                                 menu=self._band_menu())
        band.setToolTip("Band (feed) presets")
        self.freq_box = QtWidgets.QLineEdit()
        self.freq_box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.freq_box.setMaximumWidth(120)
        self.freq_box.setToolTip("Centre frequency in MHz — type and press Enter (Ctrl+F)")
        f = self.freq_box.font(); f.setPointSizeF(f.pointSizeF() * 1.15); f.setBold(True)
        self.freq_box.setFont(f)
        self.freq_box.editingFinished.connect(self._on_freq_entered)
        tb.addWidget(self.freq_box)
        tb.addWidget(QtWidgets.QLabel(" MHz  "))
        g = QtWidgets.QLabel(); g.setPixmap(icon("gain").pixmap(18, 18)); tb.addWidget(g)
        self.gain_spin = QtWidgets.QDoubleSpinBox()
        self.gain_spin.setDecimals(0)
        self.gain_spin.setRange(w._gain_range.min, w._gain_range.max)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setValue(w.gain)
        self.gain_spin.setToolTip("RX gain (the same control as the Radio panel)")
        self.gain_spin.valueChanged.connect(self._on_gain)
        tb.addWidget(self.gain_spin)
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.addItems(w._samp_rate_labels)
        self.rate_combo.setCurrentIndex(w._samp_rate_combo_box.currentIndex())
        self.rate_combo.setToolTip("Sample rate (the same control as the Radio panel)")
        self.rate_combo.currentIndexChanged.connect(w._samp_rate_combo_box.setCurrentIndex)
        w._samp_rate_combo_box.currentIndexChanged.connect(self.rate_combo.setCurrentIndex)
        tb.addWidget(self.rate_combo)
        self._refresh_fields()

    def _on_freq_entered(self):
        try:
            hz = float(self.freq_box.text().replace(",", "")) * 1e6
        except ValueError:
            self._refresh_fields()
            return
        self.w.set_freq_preset(0)
        self.w._freq_preset_callback(0)
        self.w.set_freq_manual(hz)
        self.w._freq_manual_line_edit.setText(str(hz))
        self._refresh_fields()

    def _on_gain(self, v):
        if self._playback_guard("Gain"):
            return
        self.w._gain_win.set_value(v)
        self.w.set_gain(v)

    def _build_toolbars(self, labelled):
        big = Qt.ToolButtonTextUnderIcon if labelled else Qt.ToolButtonIconOnly
        px = 24 if labelled else 20
        tb = self._tb("Recording", "tb_recording", big, px)
        for k in ("record", "stop"):
            tb.addAction(self.A[k])
        self._menu_button(tb, self.A["format"], ["fmt_iq", "fmt_fil", "fmt_ezra"])
        for k in ("schedule", "quicklook"):
            tb.addAction(self.A[k])
        self.w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Observation", "tb_observation", big, px)
        if labelled:
            box = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(box)
            v.setContentsMargins(4, 2, 4, 0); v.setSpacing(1)
            v.addWidget(self._preset_combo())
            cap = QtWidgets.QLabel("Preset"); cap.setAlignment(Qt.AlignCenter)
            v.addWidget(cap)
            tb.addWidget(box)
        else:
            tb.addWidget(self._preset_combo())
        tb.addAction(self.A["live"]); tb.addAction(self.A["sweep"])
        self.w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Tools", "tb_tools", big, px)
        for k in ("planner", "selftest", "driftscan"):
            tb.addAction(self.A[k])
        self.w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Window", "tb_window", big, px)
        for k in ("panels", "fullscreen", "guide"):
            tb.addAction(self.A[k])
        self.w.addToolBar(Qt.TopToolBarArea, tb)

        if labelled:
            self.w.addToolBarBreak(Qt.TopToolBarArea)
        small = Qt.ToolButtonIconOnly
        tb = self._tb("Tuning", "tb_tuning", small, 20)
        self._freq_widgets(tb)
        self.w.addToolBar(Qt.TopToolBarArea, tb)

        tb = self._tb("Display", "tb_display", small, 20)
        tb.addAction(self.A["lineview"])
        if labelled:     # a labelled Line view: the one display button people hunt for
            btn = tb.widgetForAction(self.A["lineview"])
            if btn is not None:
                btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._menu_button(tb, self.A["trace"], ["trace_average", "trace_peak", "trace_both"])
        for k in ("-", "autoscale", "autoscale_wf", "reset_axes", "-", "maxhold", "minhold",
                  "reset_holds", "-", "markers"):
            tb.addSeparator() if k == "-" else tb.addAction(self.A[k])
        self.w.addToolBar(Qt.TopToolBarArea, tb)

    # --------------------------------------------------------------- ribbon
    def _build_ribbon(self):
        rib = QtWidgets.QTabWidget()
        rib.setObjectName("ribbon")
        rib.setDocumentMode(True)
        rib.setStyleSheet(
            "QTabWidget#ribbon QTabBar::tab { background: transparent; color: #1e3a5f;"
            " border: none; padding: 5px 14px; margin: 0; font-weight: 600; }"
            "QTabWidget#ribbon QTabBar::tab:selected { background: #f7fafc; color: #156082;"
            " border: 1px solid #b9cfe0; border-bottom: none;"
            " border-top-left-radius: 4px; border-top-right-radius: 4px; }"
            "QWidget#ribbonPage { background: #f7fafc; border-top: 1px solid #b9cfe0;"
            " border-bottom: 1px solid #b9cfe0; }")
        file_btn = QtWidgets.QToolButton()
        file_btn.setText("  File  ")
        file_btn.setStyleSheet("QToolButton { background: #156082; color: white;"
                               " font-weight: 600; border: none; padding: 5px 10px; }")
        file_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        file_btn.setMenu(self._menu_from(QtWidgets.QMenu(file_btn),
                                         ["set_folder", "open_folder", "-", "prefs", "-",
                                          "update", "about", "-", "exit"]))
        rib.setCornerWidget(file_btn, Qt.TopLeftCorner)

        def page(groups):
            pg = QtWidgets.QWidget(); pg.setObjectName("ribbonPage")
            pg.setAttribute(Qt.WA_StyledBackground, True)
            h = QtWidgets.QHBoxLayout(pg); h.setContentsMargins(6, 4, 6, 2); h.setSpacing(4)
            for gi, (title, items) in enumerate(groups):
                if gi:
                    line = QtWidgets.QFrame(); line.setFrameShape(QtWidgets.QFrame.VLine)
                    line.setStyleSheet("color: #c9d6e2;"); h.addWidget(line)
                g = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(g)
                g.setFixedHeight(84)
                v.setContentsMargins(2, 0, 2, 0); v.setSpacing(0)
                row = QtWidgets.QHBoxLayout(); row.setSpacing(2)
                for it in items:
                    if isinstance(it, QtWidgets.QWidget):
                        row.addWidget(it)
                        continue
                    key, menu_keys = (it if isinstance(it, tuple) else (it, None))
                    b = QtWidgets.QToolButton()
                    b.setDefaultAction(self.A[key])
                    b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                    b.setIconSize(QtCore.QSize(32, 32))
                    b.setAutoRaise(True)
                    b.setMinimumWidth(58)
                    if menu_keys:
                        b.setMenu(self._menu_from(QtWidgets.QMenu(b), menu_keys))
                        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
                    row.addWidget(b)
                v.addLayout(row, 1)
                cap = QtWidgets.QLabel(title); cap.setAlignment(Qt.AlignCenter)
                cap.setStyleSheet("color: #5b6b7a; font-size: 8pt;")
                v.addWidget(cap)
                h.addWidget(g, 0, Qt.AlignTop)
            h.addStretch(1)
            return pg

        # The tuning group needs real widgets: build them on a throwaway
        # toolbar-like container so _freq_widgets can be reused.
        tune = QtWidgets.QToolBar(); tune.setIconSize(QtCore.QSize(20, 20))
        tune.setStyleSheet("QToolBar { border: none; background: transparent; }")
        self._freq_widgets(tune)
        pre = QtWidgets.QWidget(); pv = QtWidgets.QVBoxLayout(pre)
        pv.setContentsMargins(2, 6, 2, 0); pv.addWidget(self._preset_combo()); pv.addStretch(1)
        tw = QtWidgets.QWidget(); tv = QtWidgets.QVBoxLayout(tw)
        tv.setContentsMargins(0, 6, 0, 0); tv.addWidget(tune); tv.addStretch(1)

        fmt = ("format", ["fmt_iq", "fmt_fil", "fmt_ezra"])
        trace = ("trace", ["trace_average", "trace_peak", "trace_both"])
        rib.addTab(page([("Recording", ["record", "stop", fmt, "quicklook"]),
                         ("Observation", [pre, "live", "sweep"]),
                         ("Tuning", [tw]),
                         ("Display", ["lineview", "autoscale"]),
                         ("Tools", ["planner", "selftest", "driftscan"])]), "Home")
        rib.addTab(page([("Spectrum", ["lineview", trace, "autoscale", "reset_axes"]),
                         ("Holds", ["maxhold", "minhold", "reset_holds"]),
                         ("Markers", ["markers"]),
                         ("Waterfall", ["autoscale_wf"]),
                         ("Look", ["darkbg", "grid"])]), "Display")
        rib.addTab(page([("Recording", ["record", "stop", fmt]),
                         ("When", ["schedule"]),
                         ("Analysis", ["quicklook"]),
                         ("Folder", ["open_folder", "set_folder"])]), "Record")
        rib.addTab(page([("Pulsars", ["planner"]), ("Test", ["selftest"]),
                         ("Drift scan", ["driftscan"]), ("Radio", ["device"])]), "Tools")
        rib.addTab(page([("Window", ["panels", "layout_reset", "fullscreen"]),
                         ("Help", ["guide", "update", "about"])]), "View")
        rib.setFixedHeight(118)
        # On Windows/Linux the ribbon replaces the menu bar (Office style).
        # On macOS the system menu bar stays at the top of the screen anyway.
        self.w.setMenuWidget(rib)
        self.ribbon = rib

    # ----------------------------------------------------------- status bar
    def _build_status_fields(self):
        sb = self.w._status_bar
        def field(tip, min_w=0, mono=False):
            lab = QtWidgets.QLabel()
            lab.setToolTip(tip)
            lab.setContentsMargins(6, 0, 6, 0)
            if min_w:
                lab.setMinimumWidth(min_w)
            if mono:
                lab.setStyleSheet("font-family: Consolas, Menlo, monospace;")
            sb.addPermanentWidget(lab)
            return lab
        self.f_rec = field("Recording state: elapsed and time left, or the armed start", 150)
        self.f_freq = field("Centre frequency")
        self.f_rate = field("Sample rate")
        self.f_gain = field("RX gain")
        self.f_dev = field("Radio in use")
        self.f_ovf = field("Dropped-sample (overflow) events since start — click the Radio\n"
                           "panel's overflow box for the detail")
        self.f_utc = field("UTC", mono=True)
        self.f_lst = field("Local sidereal time at the site (from [site] lon_deg)", mono=True)
        t = QtCore.QTimer(self)
        t.setInterval(1000)
        t.timeout.connect(self._refresh_fields)
        t.start()
        self._refresh_fields()

    def _refresh_fields(self):
        w = self.w
        if hasattr(self, "freq_box") and not self.freq_box.hasFocus():
            self.freq_box.setText(f"{w.center_freq / 1e6:.6f}")
        if not hasattr(self, "f_freq"):
            return
        self.f_freq.setText(f"{w.center_freq / 1e6:.4f} MHz")
        self.f_rate.setText(f"{w.samp_rate / 1e6:g} MS/s")
        self.f_gain.setText("gain —" if w._playback_mode else f"gain {w.gain:g} dB")
        self.f_dev.setText("Playback" if w._playback_mode
                           else (w._source.display_label if w._source else "—"))
        try:
            n = w._overflow_widget._text.toPlainText().count("O")
        except Exception:
            n = 0
        self.f_ovf.setText(f"O: {n}")
        self.f_ovf.setStyleSheet("color: #c0392b; font-weight: 600;" if n else "color: #5b6b7a;")
        now = time.time()
        self.f_utc.setText(time.strftime("UTC %H:%M:%S", time.gmtime(now)))
        try:
            import pulsar_planner as pp
            lst = pp.lst_hours(now, w._app_settings.get_float('site', 'lon_deg'))
            hh = int(lst); mm = int((lst - hh) * 60); ss = int(((lst - hh) * 60 - mm) * 60)
            self.f_lst.setText(f"LST {hh:02d}:{mm:02d}:{ss:02d}")
        except Exception:
            self.f_lst.setText("LST —")
        if self.illustrate_rec:
            self.f_rec.setText("● REC 0:12:41  (47:19 left)")
            self.f_rec.setStyleSheet("color: white; background: #c0392b; font-weight: 700;"
                                     " border-radius: 3px; padding: 1px 6px;")
        elif getattr(w, "_rec_start_time", None):
            el = int(time.monotonic() - w._rec_start_time)
            self.f_rec.setText(f"● REC {el // 3600}:{el % 3600 // 60:02d}:{el % 60:02d}")
            self.f_rec.setStyleSheet("color: white; background: #c0392b; font-weight: 700;"
                                     " border-radius: 3px; padding: 1px 6px;")
        elif getattr(w, "_rec_sched_ts", None):
            left = int(w._rec_sched_ts - now)
            self.f_rec.setText(f"Armed: starts in {left // 3600}:{left % 3600 // 60:02d}")
            self.f_rec.setStyleSheet("color: #8a5a00; background: #fde9b8; font-weight: 600;"
                                     " border-radius: 3px; padding: 1px 6px;")
        else:
            self.f_rec.setText("Not recording")
            self.f_rec.setStyleSheet("color: #5b6b7a;")

    # --------------------------------------------------------------- panels
    def _arrange_panels(self):
        """All six panels as tabs in ONE right-hand column; plots get the
        rest of the window. Object names are untouched (saved-layout keys)."""
        w = self.w
        docks = list(w._docks)
        both = Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        for d in docks:
            d.setAllowedAreas(both)
            d.setWindowTitle(PANEL_SHORT.get(d.objectName(), d.windowTitle()))
            tb = d.titleBarWidget()
            if tb is not None and hasattr(tb, "_label"):
                tb._label.setText(d.windowTitle())
            w.removeDockWidget(d)
        first = docks[0]
        w.addDockWidget(Qt.RightDockWidgetArea, first)
        first.show()
        for d in docks[1:]:
            w.tabifyDockWidget(first, d)
            d.show()
        w.setTabPosition(Qt.RightDockWidgetArea, QtWidgets.QTabWidget.East)
        first.raise_()
        w.resizeDocks([first], [345], Qt.Horizontal)


def apply(win, variant="A", illustrate_rec=False):
    return Redesign(win, variant, illustrate_rec)
