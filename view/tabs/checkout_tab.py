import os
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Any
from view.tabs.base_tab import BaseTab


class CheckoutTab(BaseTab):
    """
    Checkout Tab (View) — Phase 2
    ==============================
    Auto Start Checkout automation.

    Layout:
      1. CRT Excel File Selection + preview grid
      2. DUT Identity (JIRA, MID, CFGPN, FW, Slots, TGZ, Hot Folder)
         + Dummy Lot Prefix
         + DUT Locations
         + Test Cases (PASSING / FORCE FAIL)
      3. SLATE Completion Detection method
      4. Tester Selection + status badges
      5. Action buttons
      6. Results panel
    """

    _BADGE_COLOURS = {
        "IDLE":        ("#888888", "white"),
        "PENDING":     ("#0078d4", "white"),
        "RUNNING":     ("#005a9e", "white"),
        "COLLECTING":  ("#8764b8", "white"),
        "SUCCESS":     ("#107c10", "white"),
        "FAILED":      ("#a80000", "white"),
        "TIMEOUT":     ("#ca5010", "white"),
    }

    # Exact CRT column names from crt_excel_template.json [24]
    # "Product  Name" has DOUBLE SPACE — intentional
    _CRT_GRID_COLUMNS = [
        ("Material description",   200),
        ("CFGPN",                   80),
        ("FW Wave ID",              80),
        ("FIDB_ASIC_FW_REV",       110),
        ("Product  Name",          100),   # DOUBLE SPACE [24]
        ("CRT Customer",           100),
        ("SSD Drive Type",          90),
        ("ABIT Release (Yes/No)",   90),
        ("SFN2 Release (Yes/No)",   90),
        ("CRT Checkout (Yes/No)",   90),   # Fix #1: added missing column
    ]

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Checkout")
        self._badge_labels              = {}
        self._phase_labels              = {}
        self._tester_vars               = {}
        self._tester_frame: Any         = None
        self._tester_row                = 1
        self._init_vars()
        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────
    # VARIABLE INITIALISATION  (must run before _build_ui)
    # ──────────────────────────────────────────────────────────────────────

    def _init_vars(self):
        """Register every checkout tk.*Var in context so get_var() never returns None."""
        ctx = self.context
        _sv = lambda name, val="": ctx.set_var(name, tk.StringVar(value=val)) \
              if ctx.get_var(name) is None else None
        _bv = lambda name, val=False: ctx.set_var(name, tk.BooleanVar(value=val)) \
              if ctx.get_var(name) is None else None
        _iv = lambda name, val=0: ctx.set_var(name, tk.IntVar(value=val)) \
              if ctx.get_var(name) is None else None

        _sv('checkout_mid')
        _sv('checkout_cfgpn')
        _sv('checkout_fw_ver')
        _sv('checkout_env', 'ABIT')                          # Fix #2: ENV field
        _sv('checkout_tgz_path')
        _sv('checkout_hot_folder',
            ctx.config.get('checkout', {}).get(
                'hot_folder', r'C:\test_program\playground_queue'))
        _sv('checkout_lot_prefix')
        _sv('checkout_dut_locations')
        _sv('checkout_detect_method', "AUTO")
        _sv('checkout_tc_passing_label', "passing")
        _sv('checkout_tc_fail_label', "force_fail_1")
        _sv('checkout_tc_fail_desc')
        _sv('checkout_cfgpn_filter')
        _sv('checkout_excel_path',
            ctx.config.get('cat', {}).get(
                'crt_excel_path',
                r'\\sifsmodtestrep\ModTestRep\crab\crt_from_sap.xlsx'
            ))
        _sv('checkout_webhook_url',                          # Fix #4: Teams webhook
            ctx.config.get('teams', {}).get('webhook_url', ''))
        _iv('checkout_dut_slots', 1)
        _iv('checkout_timeout_min', 60)
        _bv('checkout_tc_passing', True)
        _bv('checkout_tc_force_fail', False)
        _bv('checkout_notify_teams', True)

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # Common paddings for internal elements
        P_PADX = 5
        P_PADY = 3

        # ── Section 1: CRT Excel File Selection ──────────────────────────
        excel_frame = ttk.LabelFrame(self, text="CRT Excel File Selection", padding="10")
        excel_frame.grid(row=0, column=0, sticky="we", padx=10, pady=(10, 5))
        excel_frame.columnconfigure(1, weight=1)

        ttk.Label(excel_frame, text="Excel File:").grid(row=0, column=0, sticky=tk.W, padx=(0, P_PADX), pady=P_PADY)
        ttk.Entry(excel_frame,
                  textvariable=self.context.get_var('checkout_excel_path'),
                  width=70).grid(row=0, column=1, sticky="we", pady=P_PADY)
        ttk.Button(excel_frame, text="Browse Excel File",
                   command=self._browse_excel).grid(row=0, column=2, padx=(P_PADX, 0), pady=P_PADY)

        ttk.Label(excel_frame, text="Filter CFGPN:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, P_PADX), pady=P_PADY)
        ttk.Entry(excel_frame,
                  textvariable=self.context.get_var('checkout_cfgpn_filter'),
                  width=20).grid(row=1, column=1, sticky=tk.W, pady=P_PADY)
        ttk.Button(excel_frame, text="Load CRT Data",
                   command=self._load_crt_data).grid(row=1, column=2, padx=(P_PADX, 0), pady=P_PADY)

        # ── Section 2: CRT Data Preview Grid ─────────────────────────────
        grid_frame = ttk.LabelFrame(self, text="CRT Data Preview", padding="10")
        grid_frame.grid(row=1, column=0, sticky="we", padx=10, pady=5)
        grid_frame.columnconfigure(0, weight=1)

        cols = [c for c, _ in self._CRT_GRID_COLUMNS]
        self._grid = ttk.Treeview(grid_frame, columns=cols, show="headings",
                                   height=3, selectmode="browse")
        for col_name, col_width in self._CRT_GRID_COLUMNS:
            self._grid.heading(col_name, text=col_name)
            self._grid.column(col_name, width=col_width, minwidth=40, stretch=False)

        grid_scroll_y = ttk.Scrollbar(grid_frame, orient=tk.VERTICAL,   command=self._grid.yview)
        grid_scroll_x = ttk.Scrollbar(grid_frame, orient=tk.HORIZONTAL, command=self._grid.xview)
        self._grid.configure(yscrollcommand=grid_scroll_y.set,
                             xscrollcommand=grid_scroll_x.set)
        self._grid.grid(row=0, column=0, sticky="nsew")
        grid_scroll_y.grid(row=0, column=1, sticky="ns")
        grid_scroll_x.grid(row=1, column=0, sticky="ew")
        self._grid.bind("<<TreeviewSelect>>", self._on_grid_row_select)

        grid_status_frame = ttk.Frame(self)
        grid_status_frame.grid(row=2, column=0, sticky="we", padx=10, pady=(0, 5))
        self._grid_selection_label = ttk.Label(
            grid_status_frame,
            text="No selection — click a row to auto-fill DUT fields",
            foreground="#0078d4", font=("Segoe UI", 9, "bold"))
        self._grid_selection_label.pack(side=tk.LEFT)
        self._grid_row_count_label = ttk.Label(grid_status_frame, text="", foreground="#555555", font=("Segoe UI", 8))
        self._grid_row_count_label.pack(side=tk.RIGHT)

        # ── Section 3: DUT Identity ───────────────────────────────────────
        dut_frame = ttk.LabelFrame(self, text="DUT Identity", padding="10")
        dut_frame.grid(row=3, column=0, sticky="we", padx=10, pady=5)
        dut_frame.columnconfigure(1, weight=1)
        dut_frame.columnconfigure(3, weight=1)
        dut_frame.columnconfigure(5, weight=1)
        dut_frame.columnconfigure(7, weight=1)

        # Row 0: JIRA Key, MID, CFGPN, FW Wave
        ttk.Label(dut_frame, text="JIRA:").grid(row=0, column=0, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('issue_var'),
                  width=15).grid(row=0, column=1, sticky="we", pady=P_PADY, padx=(0, 15))
        ttk.Label(dut_frame, text="MID:").grid(row=0, column=2, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_mid'),
                  width=25).grid(row=0, column=3, sticky="we", pady=P_PADY, padx=(0, 15))
        ttk.Label(dut_frame, text="CFGPN:").grid(row=0, column=4, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_cfgpn'),
                  width=15).grid(row=0, column=5, sticky="we", pady=P_PADY, padx=(0, 15))
        ttk.Label(dut_frame, text="Wave:").grid(row=0, column=6, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_fw_ver'),
                  width=10).grid(row=0, column=7, sticky="we", pady=P_PADY)

        # Row 1: Slots, Lot Prefix, Locations
        ttk.Label(dut_frame, text="Slots:").grid(row=1, column=0, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Spinbox(dut_frame, textvariable=self.context.get_var('checkout_dut_slots'),
                    from_=1, to=32, width=6).grid(row=1, column=1, sticky=tk.W, pady=P_PADY)
        ttk.Label(dut_frame, text="Lot:").grid(row=1, column=2, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_lot_prefix'),
                  width=15).grid(row=1, column=3, sticky="we", pady=P_PADY, padx=(0, 15))
        ttk.Label(dut_frame, text="Loc:").grid(row=1, column=4, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_dut_locations'),
                  width=35).grid(row=1, column=5, columnspan=3, sticky="we", pady=P_PADY)

        # Row 2: TGZ Source
        ttk.Label(dut_frame, text="TGZ:").grid(row=2, column=0, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_tgz_path'),
                  width=60).grid(row=2, column=1, columnspan=6, sticky="we", pady=P_PADY)
        ttk.Button(dut_frame, text="…", width=3,
                   command=self._browse_tgz).grid(row=2, column=7, sticky=tk.W, padx=(5, 0), pady=P_PADY)

        # Row 4: Test Cases (PASS/FAIL)
        test_case_frame = ttk.Frame(dut_frame)
        test_case_frame.grid(row=3, column=0, columnspan=8, sticky="we", pady=P_PADY)
        ttk.Label(test_case_frame, text="TC:").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(test_case_frame, text="PASS", variable=self.context.get_var('checkout_tc_passing')).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_passing_label'), width=15).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Checkbutton(test_case_frame, text="FAIL", variable=self.context.get_var('checkout_tc_force_fail')).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_fail_label'), width=15).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_fail_desc'), width=30).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 0))

        # Row 4: Hot folder
        ttk.Label(dut_frame, text="Hot Folder:").grid(row=4, column=0, sticky=tk.W, pady=P_PADY, padx=(0, P_PADX))
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_hot_folder'),
                  width=70).grid(row=4, column=1, columnspan=7, sticky="we", pady=P_PADY)

        # ── Section 4: Side-by-Side Detection & Testers ──────────────────
        side_frame = ttk.Frame(self)
        side_frame.grid(row=4, column=0, sticky="we", padx=10, pady=5)
        side_frame.columnconfigure(0, weight=1)
        side_frame.columnconfigure(1, weight=1)

        detect_frame = ttk.LabelFrame(side_frame, text="SLATE Detection", padding="10")
        detect_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        ttk.Label(detect_frame, text="Method:").grid(row=0, column=0, sticky=tk.W, pady=P_PADY)
        ttk.Combobox(detect_frame,
                     textvariable=self.context.get_var('checkout_detect_method'),
                     values=["AUTO", "LOG", "FOLDER", "CPU", "TIMEOUT"],
                     state="readonly", width=12).grid(row=0, column=1, padx=10, pady=P_PADY, sticky=tk.W)

        ttk.Label(detect_frame, text="Time (m):").grid(row=1, column=0, sticky=tk.W, pady=P_PADY)
        ttk.Spinbox(detect_frame,
                    textvariable=self.context.get_var('checkout_timeout_min'),
                    from_=5, to=480, width=6).grid(row=1, column=1, padx=10, pady=P_PADY, sticky=tk.W)

        ttk.Checkbutton(detect_frame, text="Notify Teams",
                        variable=self.context.get_var('checkout_notify_teams')).grid(
            row=2, column=0, columnspan=2, pady=(10, 0), sticky=tk.W)

        tester_outer = ttk.LabelFrame(side_frame, text="Tester Selection", padding="10")
        tester_outer.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        tester_actions = ttk.Frame(tester_outer)
        tester_actions.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(tester_actions, text="↻ Refresh", width=12,
                   command=self._refresh_testers).pack(side=tk.RIGHT)
        ttk.Button(tester_actions, text="Select All", width=12,
                   command=self._select_all).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(tester_actions, text="Deselect All", width=12,
                   command=self._deselect_all).pack(side=tk.LEFT)
        
        self._tester_container = ttk.Frame(tester_outer)
        self._tester_container.pack(fill=tk.BOTH, expand=True)
        self._tester_frame = self._tester_container
        self._tester_row   = 0
        self._refresh_testers()

        # ── Section 6: Action Buttons ─────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=5, column=0, pady=10)

        self.checkout_btn = ttk.Button(btn_frame, text="▶ Start Checkout",
                                       style='Accent.TButton',
                                       command=self._start_checkout)
        self.checkout_btn.pack(side=tk.LEFT, padx=10)
        self.context.lockable_buttons.append(self.checkout_btn)
        ttk.Button(btn_frame, text="Generate XML Only", width=20,
                   command=self._generate_xml_only).pack(side=tk.LEFT, padx=10)

        # ── Section 7: Results ────────────────────────────────────────────
        results_frame = ttk.LabelFrame(self, text="Checkout Results", padding="10")
        results_frame.grid(row=6, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.rowconfigure(6, weight=1)
        self.results_text = tk.Text(results_frame, height=5, state=tk.DISABLED,
                                    font=("Consolas", 9), wrap=tk.WORD)
        results_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL,
                                        command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=results_scroll.set)
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ──────────────────────────────────────────────────────────────────────
    # TESTER REFRESH
    # ──────────────────────────────────────────────────────────────────────

    def _refresh_testers(self):
        # Fix #3: destroy ALL children cleanly instead of fragile grid_slaves() row check
        for widget in self._tester_frame.winfo_children():
            widget.destroy()

        self._tester_vars  = {}
        self._badge_labels = {}
        self._phase_labels = {}

        testers = self.context.controller.checkout_controller.get_available_testers()
        if not testers:
            ttk.Label(self._tester_frame, text="No testers registered.",
                      foreground="orange").grid(row=self._tester_row, column=0,
                                                columnspan=5, sticky=tk.W, pady=5)
            return

        for i, (hostname, env) in enumerate(testers):
            var = tk.BooleanVar(value=True)
            self._tester_vars[hostname] = var
            ttk.Checkbutton(self._tester_frame, text=f"{hostname}  ({env})",
                            variable=var).grid(row=self._tester_row + i, column=0,
                                               sticky=tk.W, padx=(5, 20), pady=4)
            badge = ttk.Label(self._tester_frame, text="IDLE", width=14,
                              anchor=tk.CENTER, relief=tk.FLAT,
                              foreground="white", background="#888888")
            badge.grid(row=self._tester_row + i, column=1, padx=5, pady=4)
            self._badge_labels[hostname] = badge
            phase_lbl = ttk.Label(self._tester_frame, text="",
                                  foreground="#555555", font=("Segoe UI", 8))
            phase_lbl.grid(row=self._tester_row + i, column=2, sticky=tk.W, padx=5, pady=4)
            self._phase_labels[hostname] = phase_lbl

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_excel(self):
        path = filedialog.askopenfilename(
            title="Select CRT Excel File",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if path:
            self.context.get_var('checkout_excel_path').set(path)

    def _browse_tgz(self):
        # Default browse location — confirmed path from Phase 1 compile output
        _DEFAULT_TGZ_DIR = r"P:\temp\BENTO\RELEASE_TGZ"

        # Fall back gracefully if P: drive is not mapped
        initial_dir = _DEFAULT_TGZ_DIR if os.path.isdir(_DEFAULT_TGZ_DIR) else "/"

        path = filedialog.askopenfilename(
            title="Select compiled TGZ",
            initialdir=initial_dir,
            filetypes=[("TGZ archives", "*.tgz"), ("All files", "*.*")])
        if path:
            self.context.get_var('checkout_tgz_path').set(path)

    def _load_crt_data(self):
        excel_path = self.context.get_var('checkout_excel_path').get().strip()
        cfgpn_flt  = self.context.get_var('checkout_cfgpn_filter').get().strip()
        self.context.controller.checkout_controller.load_crt_grid(
            excel_path=excel_path, cfgpn_filter=cfgpn_flt)

    def _on_grid_row_select(self, event):
        sel = self._grid.selection()
        if not sel:
            return
        values = self._grid.item(sel[0], "values")
        cols   = [c for c, _ in self._CRT_GRID_COLUMNS]

        def _get(col_name):
            return values[cols.index(col_name)] if col_name in cols and cols.index(col_name) < len(values) else ""

        mid     = _get("Material description")
        cfgpn   = _get("CFGPN")
        fw_wave = _get("FW Wave ID")
        self.context.get_var('checkout_mid').set(mid)
        self.context.get_var('checkout_cfgpn').set(cfgpn)
        self.context.get_var('checkout_fw_ver').set(fw_wave)

        # Fix #2: Auto-set ENV from ABIT/SFN2 Release columns
        abit_rel = _get("ABIT Release (Yes/No)")
        sfn2_rel = _get("SFN2 Release (Yes/No)")
        if abit_rel.strip().lower() == "yes":
            self.context.get_var('checkout_env').set("ABIT")
        elif sfn2_rel.strip().lower() == "yes":
            self.context.get_var('checkout_env').set("SFN2")

        self._grid_selection_label.configure(
            text=f"Selected: {mid}  |  CFGPN: {cfgpn}  |  FW: {fw_wave}",
            foreground="#0078d4")

    def _select_all(self):
        for var in self._tester_vars.values():
            var.set(True)

    def _deselect_all(self):
        for var in self._tester_vars.values():
            var.set(False)

    def _import_xml(self):
        """Browse for an existing SLATE XML and autofill form fields from it."""
        path = filedialog.askopenfilename(
            title="Select SLATE Profile XML",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")])
        if path:
            self.context.controller.checkout_controller.load_from_xml(path)

    def _generate_xml_only(self):
        params = self._collect_params()
        if params is None:
            return
        self.context.controller.checkout_controller.generate_xml_only(params)

    def _start_checkout(self):
        params = self._collect_params()
        if params is None:
            return
        self.lock_gui()
        self.context.controller.checkout_controller.start_checkout(params)

    def _collect_params(self):
        """Gather and validate all form inputs. Returns params dict or None."""
        jira_key      = self.context.get_var('issue_var').get().strip()
        tgz_path      = self.context.get_var('checkout_tgz_path').get().strip()
        hot_folder    = self.context.get_var('checkout_hot_folder').get().strip()
        mid           = self.context.get_var('checkout_mid').get().strip()
        cfgpn         = self.context.get_var('checkout_cfgpn').get().strip()
        fw_ver        = self.context.get_var('checkout_fw_ver').get().strip()
        dut_slots     = self.context.get_var('checkout_dut_slots').get()
        method        = self.context.get_var('checkout_detect_method').get()
        timeout_m     = self.context.get_var('checkout_timeout_min').get()
        notify        = self.context.get_var('checkout_notify_teams').get()
        hostnames     = [h for h, v in self._tester_vars.items() if v.get()]
        env           = self.context.get_var('checkout_env').get().strip()   # Fix #2
        webhook_url   = self.context.get_var('checkout_webhook_url').get().strip()  # Fix #4

        # New fields
        lot_prefix    = self.context.get_var('checkout_lot_prefix').get().strip()
        dut_locs_raw  = self.context.get_var('checkout_dut_locations').get().strip()
        dut_locations = dut_locs_raw.split() if dut_locs_raw else []
        tc_passing    = self.context.get_var('checkout_tc_passing').get()
        tc_fail       = self.context.get_var('checkout_tc_force_fail').get()
        passing_label = self.context.get_var('checkout_tc_passing_label').get().strip()
        fail_label    = self.context.get_var('checkout_tc_fail_label').get().strip()
        fail_desc     = self.context.get_var('checkout_tc_fail_desc').get().strip()

        # Validation
        if not jira_key or jira_key.endswith("-"):
            self.show_error("Input Error", "Enter a valid JIRA key (e.g. TSESSD-1234).")
            return None
        if not env:
            self.show_error("Input Error", "Select an ENV (ABIT / SFN2 / CNFG).")
            return None
        if not tgz_path:                                             # Fix #3: tgz_path validation
            self.show_error("Input Error", "Enter the TGZ path.")
            return None
        if not hot_folder:
            self.show_error("Input Error", "Enter the hot folder path.")
            return None
        if not hostnames:
            self.show_error("Input Error", "Select at least one tester.")
            return None
        if not lot_prefix:
            self.show_error("Input Error", "Enter a Dummy Lot prefix (e.g. JAANTJB).")
            return None
        if not dut_locations:
            self.show_error("Input Error", "Enter at least one DUT location (e.g. 0,0).")
            return None
        if not tc_passing and not tc_fail:
            self.show_error("Input Error", "Select at least one test case (PASSING or FORCE FAIL).")
            return None

        # Build test cases list
        test_cases = []
        if tc_passing:
            test_cases.append({"type": "passing",    "label": passing_label or "passing"})
        if tc_fail:
            test_cases.append({"type": "force_fail", "label": fail_label or "force_fail_1",
                                "description": fail_desc})

        return {
            "jira_key":        jira_key,
            "env":             env,             # Fix #2: included in params
            "tgz_path":        tgz_path,
            "hot_folder":      hot_folder,
            "mid":             mid,
            "cfgpn":           cfgpn,
            "fw_ver":          fw_ver,
            "dut_slots":       dut_slots,
            "lot_prefix":      lot_prefix,
            "dut_locations":   dut_locations,
            "test_cases":      test_cases,
            "detect_method":   method,
            "timeout_seconds": timeout_m * 60,
            "notify_teams":    notify,
            "webhook_url":     webhook_url,     # Fix #4: included in params
            "hostnames":       hostnames,
        }

    # ──────────────────────────────────────────────────────────────────────
    # BADGE HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _set_badge(self, hostname, state):
        badge = self._badge_labels.get(hostname)
        if not badge:
            return
        fg, bg = self._BADGE_COLOURS.get(state.upper(), ("#888888", "white"))
        badge.configure(text=state.upper(), foreground=fg, background=bg)

    def _set_phase(self, hostname, phase):
        lbl = self._phase_labels.get(hostname)
        if lbl:
            lbl.configure(text=phase)

    def _clear_results(self):                                     # Fix #6
        self.results_text.configure(state=tk.NORMAL)
        self.results_text.delete("1.0", tk.END)
        self.results_text.configure(state=tk.DISABLED)

    def _open_queue_folder(self):                                 # Fix #7
        queue_path = r"P:\temp\BENTO\CHECKOUT_QUEUE"
        if os.path.isdir(queue_path):
            os.startfile(queue_path)
        else:
            self.show_error("Folder Not Found",
                            f"Queue folder not found:\n{queue_path}\n\n"
                            "Ensure P: drive is mapped.")

    def _append_result(self, text):
        self.results_text.configure(state=tk.NORMAL)
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)
        self.results_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_crt_grid_loaded(self, crt_data):
        for row in self._grid.get_children():
            self._grid.delete(row)
        cols = [c for c, _ in self._CRT_GRID_COLUMNS]
        for row_dict in crt_data.get("rows", []):
            values = [str(row_dict.get(col, "")) for col in cols]
            self._grid.insert("", tk.END, values=values)
        count = crt_data.get("count", 0)
        self._grid_row_count_label.configure(text=f"{count} row(s) loaded")
        self._grid_selection_label.configure(
            text="Click a row to auto-fill DUT fields", foreground="#cc6600")
        self.log(f"✓ CRT grid loaded: {count} row(s)")

    def on_autofill_completed(self, mid, cfgpn, fw_ver):
        self.context.get_var('checkout_mid').set(mid)
        self.context.get_var('checkout_cfgpn').set(cfgpn)
        self.context.get_var('checkout_fw_ver').set(fw_ver)
        self.log(f"✓ Auto-filled: MID={mid}  CFGPN={cfgpn}  FW={fw_ver}")

    def on_xml_imported(self, data: dict):
        """
        Populate form fields from a parsed SLATE Profile XML.

        data keys (all optional / may be empty):
          tgz_path, env, mid, lot_prefix, dut_locations (list[str]), dut_slots (int)
        """
        if data.get("tgz_path"):
            self.context.get_var('checkout_tgz_path').set(data["tgz_path"])

        if data.get("env") and data["env"] in ("ABIT", "SFN2", "CNFG"):
            self.context.get_var('checkout_env').set(data["env"])

        if data.get("mid"):
            self.context.get_var('checkout_mid').set(data["mid"])

        if data.get("lot_prefix"):
            self.context.get_var('checkout_lot_prefix').set(data["lot_prefix"])

        locs = data.get("dut_locations", [])
        if locs:
            self.context.get_var('checkout_dut_locations').set(" ".join(locs))
            self.context.get_var('checkout_dut_slots').set(len(locs))

        filled = []
        if data.get("tgz_path"):   filled.append("TGZ")
        if data.get("env"):        filled.append(f"ENV={data['env']}")
        if data.get("mid"):        filled.append(f"MID={data['mid']}")
        if data.get("lot_prefix"): filled.append(f"Lot={data['lot_prefix']}")
        if locs:                   filled.append(f"{len(locs)} DUT(s)")

        summary = "  ".join(filled) if filled else "(nothing recognised)"
        self.log(f"✓ XML imported: {summary}")

    def on_checkout_started(self, hostname):
        self._set_badge(hostname, "PENDING")
        self._set_phase(hostname, "Preparing XML…")

    def on_checkout_progress(self, hostname, phase):
        if "slate" in phase.lower() or "waiting" in phase.lower():
            self._set_badge(hostname, "RUNNING")
        elif "collect" in phase.lower():
            self._set_badge(hostname, "COLLECTING")
        self._set_phase(hostname, phase)

    def on_checkout_completed(self, hostname, result):
        status = result.get("status", "failed").upper()
        self._set_badge(hostname, status)
        self._set_phase(hostname, "")

        elapsed    = result.get("elapsed", 0)
        detail     = result.get("detail", "")
        test_cases = result.get("test_cases", [])

        self._append_result(f"[{hostname}] {status}  ({elapsed}s)  {detail}")
        for tc in test_cases:
            icon = "✓" if tc.get("status") == "success" else "✗"
            self._append_result(
                f"   {icon} {tc.get('label','?')} ({tc.get('type','?')}): "
                f"{tc.get('status','?')} in {tc.get('elapsed',0)}s")

        running = [lbl for lbl in self._badge_labels.values()
                   if lbl.cget("text") in ("PENDING", "RUNNING", "COLLECTING")]
        if not running:
            self.unlock_gui()
