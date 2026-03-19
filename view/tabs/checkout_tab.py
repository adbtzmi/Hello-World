import tkinter as tk
from tkinter import ttk, filedialog
from view.tabs.base_tab import BaseTab


class CheckoutTab(BaseTab):
    """
    Checkout Tab (View) — Phase 2
    ==============================
    Auto Start Checkout automation.

    Layout mirrors the CRAB GUI screenshot:
      - Excel File Selection row (path entry + Browse button)
      - CRT Data Preview Grid (Treeview) with exact CRT column names [24]
      - DUT identity fields (auto-filled from grid or manually entered)
      - SLATE Completion Detection method selector
      - Tester selection + status badges
      - Action buttons: Start Checkout / Generate XML Only / Load CRT Data

    Tab order: appears AFTER the Compile tab in the notebook.
    """

    # Badge colours (identical to CompileTab)
    _BADGE_COLOURS = {
        "IDLE":        ("#888888", "white"),
        "PENDING":     ("#0078d4", "white"),
        "RUNNING":     ("#005a9e", "white"),
        "COLLECTING":  ("#8764b8", "white"),
        "SUCCESS":     ("#107c10", "white"),
        "FAILED":      ("#a80000", "white"),
        "TIMEOUT":     ("#ca5010", "white"),
    }

    # CRT Excel column display names [24] — EXACT match required
    # "Product  Name" has DOUBLE SPACE intentionally
    _CRT_GRID_COLUMNS = [
        ("Material description", 200),
        ("CFGPN",                 80),
        ("FW Wave ID",             80),
        ("FIDB_ASIC_FW_REV",      110),
        ("Product  Name",         100),   # DOUBLE SPACE [24]
        ("CRT Customer",          100),
        ("SSD Drive Type",         90),
        ("ABIT Release (Yes/No)",  90),
        ("SFN2 Release (Yes/No)",  90),
    ]

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Checkout")
        self._badge_labels = {}
        self._phase_labels = {}
        self._tester_vars  = {}
        self._tester_frame = None
        self._tester_row   = 1
        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Section 1: CRT Excel File Selection (mirrors CRAB layout) ────
        excel_frame = ttk.LabelFrame(self, text="CRT Excel File Selection", padding="8")
        excel_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=4)
        excel_frame.columnconfigure(1, weight=1)

        self.context.set_var('checkout_excel_path', tk.StringVar(
            value=self.context.config.get('cat', {}).get(
                'crt_excel_path',
                r'\\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx'
            )
        ))
        ttk.Label(excel_frame, text="Excel File:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        ttk.Entry(excel_frame,
                  textvariable=self.context.get_var('checkout_excel_path'),
                  width=70).grid(row=0, column=1, sticky=(tk.W, tk.E))
        ttk.Button(excel_frame, text="Browse Excel File",
                   command=self._browse_excel).grid(row=0, column=2, padx=(6, 0))

        # CFGPN filter for grid
        ttk.Label(excel_frame, text="Filter CFGPN:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(4, 0))
        self.context.set_var('checkout_cfgpn_filter', tk.StringVar())
        ttk.Entry(excel_frame,
                  textvariable=self.context.get_var('checkout_cfgpn_filter'),
                  width=20).grid(row=1, column=1, sticky=tk.W, pady=(4, 0))
        ttk.Button(excel_frame, text="Load CRT Data",
                   command=self._load_crt_data).grid(row=1, column=2, padx=(6, 0), pady=(4, 0))

        # ── Section 2: CRT Data Preview Grid (mirrors CRAB Excel Preview Grid)
        grid_frame = ttk.LabelFrame(self, text="CRT Data Preview", padding="5")
        grid_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=4)
        grid_frame.columnconfigure(0, weight=1)

        cols = [c for c, _ in self._CRT_GRID_COLUMNS]
        self._grid = ttk.Treeview(grid_frame, columns=cols, show="headings",
                                   height=6, selectmode="browse")

        for col_name, col_width in self._CRT_GRID_COLUMNS:
            self._grid.heading(col_name, text=col_name)
            self._grid.column(col_name,  width=col_width, minwidth=40, stretch=False)

        grid_scroll_y = ttk.Scrollbar(grid_frame, orient=tk.VERTICAL,
                                       command=self._grid.yview)
        grid_scroll_x = ttk.Scrollbar(grid_frame, orient=tk.HORIZONTAL,
                                       command=self._grid.xview)
        self._grid.configure(yscrollcommand=grid_scroll_y.set,
                             xscrollcommand=grid_scroll_x.set)

        self._grid.grid(row=0, column=0, sticky=(tk.N, tk.S, tk.E, tk.W))
        grid_scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        grid_scroll_x.grid(row=1, column=0, sticky=(tk.E, tk.W))

        # Bind row-click to auto-fill DUT fields
        self._grid.bind("<<TreeviewSelect>>", self._on_grid_row_select)

        # Status bar below grid — mirrors CRAB "No selection / Double-click hint"
        grid_status_frame = ttk.Frame(self)
        grid_status_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 4))
        self._grid_selection_label = ttk.Label(
            grid_status_frame,
            text="No selection — click a row to auto-fill DUT fields",
            foreground="#cc6600"
        )
        self._grid_selection_label.pack(side=tk.LEFT)
        self._grid_row_count_label = ttk.Label(
            grid_status_frame, text="", foreground="#555555"
        )
        self._grid_row_count_label.pack(side=tk.RIGHT)

        # ── Section 3: DUT Identity ───────────────────────────────────────
        dut_frame = ttk.LabelFrame(self, text="DUT Identity", padding="8")
        dut_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=4)
        dut_frame.columnconfigure(1, weight=1)
        dut_frame.columnconfigure(3, weight=1)

        # Row 0: JIRA Key + MID
        ttk.Label(dut_frame, text="JIRA Key:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.context.set_var('checkout_jira_key', tk.StringVar(value="TSESSD-"))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_jira_key'),
                  width=22).grid(row=0, column=1, sticky=tk.W, pady=2)

        ttk.Label(dut_frame, text="Material Desc (MID):").grid(
            row=0, column=2, sticky=tk.W, pady=2, padx=(20, 0))
        self.context.set_var('checkout_mid', tk.StringVar())
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_mid'),
                  width=28).grid(row=0, column=3, sticky=(tk.W, tk.E), pady=2)

        # Row 1: CFGPN + FW Wave ID
        ttk.Label(dut_frame, text="CFGPN:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('checkout_cfgpn', tk.StringVar())
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_cfgpn'),
                  width=22).grid(row=1, column=1, sticky=tk.W, pady=2)

        ttk.Label(dut_frame, text="FW Wave ID:").grid(
            row=1, column=2, sticky=tk.W, pady=2, padx=(20, 0))
        self.context.set_var('checkout_fw_ver', tk.StringVar())
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_fw_ver'),
                  width=28).grid(row=1, column=3, sticky=(tk.W, tk.E), pady=2)

        # Row 2: DUT Slots + TGZ path
        ttk.Label(dut_frame, text="DUT Slots:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('checkout_dut_slots', tk.IntVar(value=4))
        ttk.Spinbox(dut_frame, textvariable=self.context.get_var('checkout_dut_slots'),
                    from_=1, to=32, width=6).grid(row=2, column=1, sticky=tk.W, pady=2)

        ttk.Label(dut_frame, text="TGZ Source:").grid(
            row=2, column=2, sticky=tk.W, pady=2, padx=(20, 0))
        self.context.set_var('checkout_tgz_path', tk.StringVar())
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_tgz_path'),
                  width=28).grid(row=2, column=3, sticky=(tk.W, tk.E), pady=2)
        ttk.Button(dut_frame, text="…",
                   command=self._browse_tgz).grid(row=2, column=4, padx=(4, 0))

        # Row 3: Hot folder
        ttk.Label(dut_frame, text="Hot Folder:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('checkout_hot_folder', tk.StringVar(
            value=self.context.config.get('checkout', {}).get(
                'hot_folder', r'C:\test_program\playground_queue'
            )
        ))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_hot_folder'),
                  width=50).grid(row=3, column=1, columnspan=3, sticky=(tk.W, tk.E), pady=2)

        # ── Section 4: SLATE Detection ────────────────────────────────────
        detect_frame = ttk.LabelFrame(self, text="SLATE Completion Detection", padding="8")
        detect_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=4)

        ttk.Label(detect_frame, text="Detection method:").grid(row=0, column=0, sticky=tk.W)
        self.context.set_var('checkout_detect_method', tk.StringVar(value="AUTO"))
        ttk.Combobox(detect_frame,
                     textvariable=self.context.get_var('checkout_detect_method'),
                     values=["AUTO", "LOG", "FOLDER", "CPU", "TIMEOUT"],
                     state="readonly", width=12).grid(row=0, column=1, padx=6, sticky=tk.W)
        ttk.Label(detect_frame,
                  text="AUTO: LOG → FOLDER → CPU → TIMEOUT",
                  foreground="#666666").grid(row=0, column=2, padx=10, sticky=tk.W)

        ttk.Label(detect_frame, text="Timeout (min):").grid(
            row=1, column=0, sticky=tk.W, pady=4)
        self.context.set_var('checkout_timeout_min', tk.IntVar(value=60))
        ttk.Spinbox(detect_frame,
                    textvariable=self.context.get_var('checkout_timeout_min'),
                    from_=5, to=480, width=6).grid(row=1, column=1, sticky=tk.W, pady=4)

        self.context.set_var('checkout_notify_teams', tk.BooleanVar(value=True))
        ttk.Checkbutton(detect_frame, text="Send Teams notification on completion",
                        variable=self.context.get_var('checkout_notify_teams')).grid(
            row=2, column=0, columnspan=4, sticky=tk.W)

        # ── Section 5: Tester Selection ───────────────────────────────────
        tester_outer = ttk.LabelFrame(self, text="Tester Selection", padding="8")
        tester_outer.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=4)

        ttk.Label(tester_outer, text="Select testers:").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 4))
        ttk.Button(tester_outer, text="↻ Refresh",
                   command=self._refresh_testers).grid(row=0, column=4, padx=10)

        self._tester_frame = tester_outer
        self._tester_row   = 1
        self._refresh_testers()

        # ── Section 6: Action Buttons ─────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=6, column=0, pady=8)

        self.checkout_btn = ttk.Button(btn_frame, text="▶ Start Checkout",
                                       style='Accent.TButton',
                                       command=self._start_checkout)
        self.checkout_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="Generate XML Only",
                   command=self._generate_xml_only).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Select All",
                   command=self._select_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Deselect All",
                   command=self._deselect_all).pack(side=tk.LEFT, padx=5)

        # ── Section 7: Results ────────────────────────────────────────────
        results_frame = ttk.LabelFrame(self, text="Checkout Results", padding="5")
        results_frame.grid(row=7, column=0, sticky=(tk.W, tk.E), pady=4)

        self.results_text = tk.Text(results_frame, height=6, state=tk.DISABLED,
                                    font=("Consolas", 9), wrap=tk.WORD)
        results_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL,
                                        command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=results_scroll.set)
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ──────────────────────────────────────────────────────────────────────
    # TESTER REGISTRY REFRESH
    # ──────────────────────────────────────────────────────────────────────

    def _refresh_testers(self):
        """Reload testers from registry and rebuild checkbox + badge rows."""
        for widget in self._tester_frame.grid_slaves():
            try:
                if int(widget.grid_info().get("row", 0)) >= self._tester_row:
                    widget.destroy()
            except Exception:
                pass

        self._tester_vars  = {}
        self._badge_labels = {}
        self._phase_labels = {}

        testers = self.context.controller.checkout_controller.get_available_testers()
        if not testers:
            ttk.Label(self._tester_frame, text="No testers registered.",
                      foreground="orange").grid(row=self._tester_row, column=0,
                                                columnspan=5, sticky=tk.W)
            return

        for i, (hostname, env) in enumerate(testers):
            var = tk.BooleanVar(value=True)
            self._tester_vars[hostname] = var

            ttk.Checkbutton(self._tester_frame, text=f"{hostname}  ({env})",
                            variable=var).grid(row=self._tester_row + i, column=0,
                                               sticky=tk.W, padx=(0, 20))

            badge = ttk.Label(self._tester_frame, text="IDLE", width=14,
                              anchor=tk.CENTER, relief=tk.FLAT,
                              foreground="white", background="#888888")
            badge.grid(row=self._tester_row + i, column=1, padx=5)
            self._badge_labels[hostname] = badge

            phase_lbl = ttk.Label(self._tester_frame, text="",
                                  foreground="#555555", font=("Segoe UI", 8))
            phase_lbl.grid(row=self._tester_row + i, column=2, sticky=tk.W, padx=5)
            self._phase_labels[hostname] = phase_lbl

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_excel(self):
        path = filedialog.askopenfilename(
            title="Select CRT Excel File",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
        )
        if path:
            self.context.get_var('checkout_excel_path').set(path)

    def _browse_tgz(self):
        path = filedialog.askopenfilename(
            title="Select compiled TGZ",
            filetypes=[("TGZ archives", "*.tgz"), ("All files", "*.*")],
        )
        if path:
            self.context.get_var('checkout_tgz_path').set(path)

    def _load_crt_data(self):
        """Load CRT Excel into the preview grid."""
        excel_path  = self.context.get_var('checkout_excel_path').get().strip()
        cfgpn_flt   = self.context.get_var('checkout_cfgpn_filter').get().strip()
        self.context.controller.checkout_controller.load_crt_grid(
            excel_path=excel_path, cfgpn_filter=cfgpn_flt
        )

    def _on_grid_row_select(self, event):
        """When user clicks a grid row, auto-fill the DUT identity fields."""
        sel = self._grid.selection()
        if not sel:
            return
        row_id  = sel[0]
        values  = self._grid.item(row_id, "values")
        cols    = [c for c, _ in self._CRT_GRID_COLUMNS]

        def _get(col_name):
            if col_name in cols:
                idx = cols.index(col_name)
                return values[idx] if idx < len(values) else ""
            return ""

        mid     = _get("Material description")
        cfgpn   = _get("CFGPN")
        fw_wave = _get("FW Wave ID")

        self.context.get_var('checkout_mid').set(mid)
        self.context.get_var('checkout_cfgpn').set(cfgpn)
        self.context.get_var('checkout_fw_ver').set(fw_wave)
        self._grid_selection_label.configure(
            text=f"Selected: {mid}  |  CFGPN: {cfgpn}  |  FW: {fw_wave}",
            foreground="#0078d4"
        )

    def _select_all(self):
        for var in self._tester_vars.values():
            var.set(True)

    def _deselect_all(self):
        for var in self._tester_vars.values():
            var.set(False)

    def _generate_xml_only(self):
        params = self._collect_params()
        if params is None:
            return
        self.context.controller.checkout_controller.generate_xml_only(params)

    def _start_checkout(self):
        params = self._collect_params()
        if params is None:
            return
        self.checkout_btn.configure(state=tk.DISABLED)
        self.context.controller.checkout_controller.start_checkout(params)

    def _collect_params(self):
        """Gather and validate all form inputs. Returns params dict or None."""
        jira_key   = self.context.get_var('checkout_jira_key').get().strip()
        tgz_path   = self.context.get_var('checkout_tgz_path').get().strip()
        hot_folder = self.context.get_var('checkout_hot_folder').get().strip()
        mid        = self.context.get_var('checkout_mid').get().strip()
        cfgpn      = self.context.get_var('checkout_cfgpn').get().strip()
        fw_ver     = self.context.get_var('checkout_fw_ver').get().strip()
        dut_slots  = self.context.get_var('checkout_dut_slots').get()
        method     = self.context.get_var('checkout_detect_method').get()
        timeout_m  = self.context.get_var('checkout_timeout_min').get()
        notify     = self.context.get_var('checkout_notify_teams').get()
        hostnames  = [h for h, v in self._tester_vars.items() if v.get()]

        if not jira_key or jira_key.endswith("-"):
            self.show_error("Input Error", "Enter a valid JIRA key (e.g. TSESSD-1234).")
            return None
        if not hot_folder:
            self.show_error("Input Error", "Enter the hot folder path.")
            return None
        if not hostnames:
            self.show_error("Input Error", "Select at least one tester.")
            return None

        return {
            "jira_key":        jira_key,
            "tgz_path":        tgz_path,
            "hot_folder":      hot_folder,
            "mid":             mid,
            "cfgpn":           cfgpn,
            "fw_ver":          fw_ver,
            "dut_slots":       dut_slots,
            "detect_method":   method,
            "timeout_seconds": timeout_m * 60,
            "notify_teams":    notify,
            "hostnames":       hostnames,
        }

    # ──────────────────────────────────────────────────────────────────────
    # BADGE HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _set_badge(self, hostname: str, state: str):
        badge = self._badge_labels.get(hostname)
        if not badge:
            return
        fg, bg = self._BADGE_COLOURS.get(state.upper(), ("#888888", "white"))
        badge.configure(text=state.upper(), foreground=fg, background=bg)

    def _set_phase(self, hostname: str, phase: str):
        lbl = self._phase_labels.get(hostname)
        if lbl:
            lbl.configure(text=phase)

    def _append_result(self, text: str):
        self.results_text.configure(state=tk.NORMAL)
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)
        self.results_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS — called by BentoApp (relayed from CheckoutController)
    # ──────────────────────────────────────────────────────────────────────

    def on_crt_grid_loaded(self, crt_data: dict):
        """
        Populate the CRT preview grid with data from the Excel file.
        Called by CheckoutController.load_crt_grid() after async read.
        Mirrors CRAB's "Excel Data Preview Grid" exactly.
        """
        # Clear existing rows
        for row in self._grid.get_children():
            self._grid.delete(row)

        cols = [c for c, _ in self._CRT_GRID_COLUMNS]
        rows = crt_data.get("rows", [])

        for row_dict in rows:
            values = [str(row_dict.get(col, "")) for col in cols]
            self._grid.insert("", tk.END, values=values)

        count = crt_data.get("count", len(rows))
        self._grid_row_count_label.configure(text=f"{count} row(s) loaded")
        self._grid_selection_label.configure(
            text="Click a row to auto-fill DUT fields",
            foreground="#cc6600"
        )
        self.log(f"✓ CRT grid loaded: {count} row(s) | CFGPN={crt_data.get('cfgpn', '')} | FW={crt_data.get('fw_wave_id', '')}")

    def on_autofill_completed(self, mid: str, cfgpn: str, fw_ver: str):
        """Called by CheckoutController after CRT Excel lookup succeeds."""
        self.context.get_var('checkout_mid').set(mid)
        self.context.get_var('checkout_cfgpn').set(cfgpn)
        self.context.get_var('checkout_fw_ver').set(fw_ver)
        self.log(f"✓ Auto-filled: MID={mid}  CFGPN={cfgpn}  FW={fw_ver}")

    def on_checkout_started(self, hostname: str):
        """Update badge when checkout begins on a tester."""
        self._set_badge(hostname, "PENDING")
        self._set_phase(hostname, "Preparing XML…")

    def on_checkout_progress(self, hostname: str, phase: str):
        """Update badge + phase label during run."""
        if "slate" in phase.lower() or "waiting" in phase.lower():
            self._set_badge(hostname, "RUNNING")
        elif "collect" in phase.lower():
            self._set_badge(hostname, "COLLECTING")
        self._set_phase(hostname, phase)

    def on_checkout_completed(self, hostname: str, result: dict):
        """Update badge and results panel when checkout finishes."""
        status = result.get("status", "failed").upper()
        self._set_badge(hostname, status)
        self._set_phase(hostname, "")

        elapsed = result.get("elapsed", 0)
        detail  = result.get("detail", "")
        self._append_result(f"[{hostname}] {status}  ({elapsed}s)  {detail}")

        # Re-enable button when all testers have finished
        running = [
            lbl for lbl in self._badge_labels.values()
            if lbl.cget("text") in ("PENDING", "RUNNING", "COLLECTING")
        ]
        if not running:
            self.checkout_btn.configure(state=tk.NORMAL)
