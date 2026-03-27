import os
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from typing import Any, List, Dict
from view.tabs.base_tab import BaseTab


class CheckoutTab(BaseTab):
    """
    Checkout Tab (View) — Phase 2
    ==============================
    Auto Start Checkout automation.

    Layout:
      1. Profile Generation Table (replaces CRT Excel File Selection)
         - Editable grid with 11 columns matching CRT Automation Tools:
           Auto-populated from CRT: Form_Factor, Material_Desc, CFGPN, MCTO_#1, Dummy_Lot
           User-editable (additional): Step, MID, Tester, Primitive, Dut, ATTR_OVERWRITE
         - All columns are editable by user (auto-populated can be overridden)
         - Mimics CRT Automation Tools profile generation process
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

    # Profile Generation Table columns
    # Mirrors CRT Automation Tools _profile_gen_headers + additional_headers_profile_gen()
    # First 5 columns are auto-populated from CRT data (but still editable by user)
    # Last 6 columns are user-editable (additional headers for profile generation)
    # ATTR_OVERWRITE has a special edit dialog
    _PROFILE_GEN_COLUMNS = [
        ("Form_Factor",         120),
        ("Material_Desc",       150),
        ("CFGPN",               120),
        ("MCTO_#1",             100),
        ("Dummy_Lot",           120),
        ("Step",                 80),
        ("MID",                 120),
        ("Tester",              120),
        ("Primitive",           100),
        ("Dut",                  60),
        ("ATTR_OVERWRITE",      200),
    ]

    # Columns that are auto-populated from CRT data (but still user-editable)
    _AUTO_POPULATED_COLS = {"Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot"}

    # Columns that are user-editable (additional profile gen headers)
    _EDITABLE_COLS = {
        "Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot",
        "Step", "MID", "Tester", "Primitive", "Dut", "ATTR_OVERWRITE",
    }

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Checkout")
        self._badge_labels              = {}
        self._phase_labels              = {}
        self._tester_vars               = {}
        self._tester_frame: Any         = None
        self._tester_row                = 1
        self._profile_data: List[Dict]  = []   # Internal data store for profile table
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

        _sv('checkout_tgz_path')
        _sv('checkout_hot_folder',
            ctx.config.get('checkout', {}).get(
                'hot_folder', r'C:\test_program\playground_queue'))
        _sv('checkout_detect_method', "AUTO")
        _sv('checkout_tc_passing_label', "passing")
        _sv('checkout_tc_fail_label', "force_fail_1")
        _sv('checkout_tc_fail_desc')
        _sv('checkout_cfgpn_filter')
        _sv('checkout_excel_path',
            ctx.config.get('cat', {}).get(
                'crt_excel_path',
                os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Documents', 'incoming_crt.xlsx')
            ))
        _sv('checkout_webhook_url',                          # Fix #4: Teams webhook
            ctx.config.get('notifications', {}).get('teams_webhook_url', ''))
        _iv('checkout_timeout_min', 60)
        _bv('checkout_tc_passing', True)
        _bv('checkout_tc_force_fail', False)
        _bv('checkout_notify_teams', True)

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Section 1: Profile Generation Table ──────────────────────────
        # Replaces old CRT Excel File Selection + CRT Data Preview
        profile_frame = ttk.LabelFrame(self, text="Profile Generation", padding="5")
        profile_frame.grid(row=0, column=0, sticky="we", pady=2)
        profile_frame.columnconfigure(0, weight=1)

        # ── Workflow Actions bar ──────────────────────────────────────────
        actions_frame = ttk.Frame(profile_frame)
        actions_frame.grid(row=0, column=0, sticky="we", pady=(0, 4))

        ttk.Button(actions_frame, text="Add Row",
                   command=self._profile_add_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions_frame, text="Remove Row",
                   command=self._profile_remove_row).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions_frame, text="Import from Excel",
                   command=self._profile_import_excel).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions_frame, text="Export to Excel",
                   command=self._profile_export_excel).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions_frame, text="Load from CRT",
                   command=self._profile_load_from_crt).pack(side=tk.LEFT, padx=2)

        # ── CRT Source (Excel path + CFGPN filter for Load from CRT) ─────
        crt_source_frame = ttk.Frame(profile_frame)
        crt_source_frame.grid(row=1, column=0, sticky="we", pady=(0, 4))
        crt_source_frame.columnconfigure(1, weight=1)

        ttk.Label(crt_source_frame, text="Excel File:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 4))
        ttk.Entry(crt_source_frame,
                  textvariable=self.context.get_var('checkout_excel_path'),
                  width=60).grid(row=0, column=1, sticky="we")
        ttk.Button(crt_source_frame, text="Browse",
                   command=self._browse_excel).grid(row=0, column=2, padx=(4, 8))
        ttk.Label(crt_source_frame, text="Filter CFGPN:").grid(
            row=0, column=3, sticky=tk.W, padx=(0, 4))
        ttk.Entry(crt_source_frame,
                  textvariable=self.context.get_var('checkout_cfgpn_filter'),
                  width=15).grid(row=0, column=4, sticky=tk.W)

        # ── Profile Generation Grid ───────────────────────────────────────
        grid_container = ttk.Frame(profile_frame)
        grid_container.grid(row=2, column=0, sticky="nsew")
        grid_container.columnconfigure(0, weight=1)

        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        self._profile_grid = ttk.Treeview(
            grid_container, columns=cols, show="headings",
            height=5, selectmode="browse")
        for col_name, col_width in self._PROFILE_GEN_COLUMNS:
            self._profile_grid.heading(col_name, text=col_name)
            self._profile_grid.column(col_name, width=col_width, minwidth=40, stretch=True)

        profile_scroll_y = ttk.Scrollbar(grid_container, orient=tk.VERTICAL,
                                         command=self._profile_grid.yview)
        profile_scroll_x = ttk.Scrollbar(grid_container, orient=tk.HORIZONTAL,
                                         command=self._profile_grid.xview)
        self._profile_grid.configure(yscrollcommand=profile_scroll_y.set,
                                     xscrollcommand=profile_scroll_x.set)
        self._profile_grid.grid(row=0, column=0, sticky="nsew")
        profile_scroll_y.grid(row=0, column=1, sticky="ns")
        profile_scroll_x.grid(row=1, column=0, sticky="ew")

        # Double-click to edit a cell
        self._profile_grid.bind("<Double-1>", self._on_profile_cell_double_click)
        # Single click on row to auto-fill DUT fields
        self._profile_grid.bind("<<TreeviewSelect>>", self._on_profile_row_select)

        # ── Status bar under grid ─────────────────────────────────────────
        profile_status_frame = ttk.Frame(profile_frame)
        profile_status_frame.grid(row=3, column=0, sticky="we", pady=(2, 0))
        self._profile_status_label = ttk.Label(
            profile_status_frame,
            text="No data — use 'Load from CRT' or 'Add Row' to populate",
            foreground="#cc6600", font=("Segoe UI", 8))
        self._profile_status_label.pack(side=tk.LEFT)
        self._profile_row_count_label = ttk.Label(
            profile_status_frame, text="0 row(s)",
            foreground="#555555", font=("Segoe UI", 8))
        self._profile_row_count_label.pack(side=tk.RIGHT)

        # Add initial empty row with "None" in Dummy_Lot (matching the tool's default)
        self._profile_add_default_row()

        # ── Section 3: Checkout Paths & Test Cases ────────────────────────
        dut_frame = ttk.LabelFrame(self, text="Checkout Paths & Test Cases", padding="5")
        dut_frame.grid(row=3, column=0, sticky="we", pady=2)
        dut_frame.columnconfigure(1, weight=1)

        # Row 0: TGZ Source
        ttk.Label(dut_frame, text="TGZ:").grid(row=0, column=0, sticky=tk.W, pady=0)
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_tgz_path'),
                  width=60).grid(row=0, column=1, columnspan=6, sticky="we", pady=0)
        ttk.Button(dut_frame, text="…", width=3,
                   command=self._browse_tgz).grid(row=0, column=7, padx=(2, 0), pady=0)

        # Row 1: Test Cases (PASS/FAIL)
        test_case_frame = ttk.Frame(dut_frame)
        test_case_frame.grid(row=1, column=0, columnspan=8, sticky="we", pady=0)
        ttk.Label(test_case_frame, text="TC:").pack(side=tk.LEFT)
        ttk.Checkbutton(test_case_frame, text="PASS", variable=self.context.get_var('checkout_tc_passing')).pack(side=tk.LEFT, padx=2)
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_passing_label'), width=10).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(test_case_frame, text="FAIL", variable=self.context.get_var('checkout_tc_force_fail')).pack(side=tk.LEFT, padx=5)
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_fail_label'), width=10).pack(side=tk.LEFT, padx=2)
        ttk.Entry(test_case_frame, textvariable=self.context.get_var('checkout_tc_fail_desc'), width=30).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # Row 2: Hot folder
        ttk.Label(dut_frame, text="Hot Folder:").grid(row=2, column=0, sticky=tk.W, pady=0)
        ttk.Entry(dut_frame, textvariable=self.context.get_var('checkout_hot_folder'),
                  width=70).grid(row=2, column=1, columnspan=7, sticky="we", pady=0)

        # ── Section 4: Side-by-Side Detection & Testers ──────────────────
        side_frame = ttk.Frame(self)
        side_frame.grid(row=4, column=0, sticky="we", pady=1)
        side_frame.columnconfigure(0, weight=1)
        side_frame.columnconfigure(1, weight=1)

        detect_frame = ttk.LabelFrame(side_frame, text="SLATE Detection", padding="2")
        detect_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))

        ttk.Label(detect_frame, text="Method:").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(detect_frame,
                     textvariable=self.context.get_var('checkout_detect_method'),
                     values=["AUTO", "LOG", "FOLDER", "CPU", "TIMEOUT"],
                     state="readonly", width=8).grid(row=0, column=1, padx=2, sticky=tk.W)

        ttk.Label(detect_frame, text="Time:").grid(row=1, column=0, sticky=tk.W)
        ttk.Spinbox(detect_frame,
                    textvariable=self.context.get_var('checkout_timeout_min'),
                    from_=5, to=480, width=5).grid(row=1, column=1, sticky=tk.W)

        ttk.Checkbutton(detect_frame, text="Teams",
                        variable=self.context.get_var('checkout_notify_teams')).grid(
            row=1, column=2, padx=2, sticky=tk.W)

        # Fix #4: Webhook URL field (shown when Teams notify is on)
        ttk.Label(detect_frame, text="Webhook:").grid(row=2, column=0, sticky=tk.W, pady=(2, 0))
        ttk.Entry(detect_frame,
                  textvariable=self.context.get_var('checkout_webhook_url'),
                  width=40).grid(row=2, column=1, columnspan=3, sticky="we", pady=(2, 0))

        tester_outer = ttk.LabelFrame(side_frame, text="Tester Selection", padding="2")
        tester_outer.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        ttk.Button(tester_outer, text="↻ Refresh", width=10,
                   command=self._refresh_testers).pack(side=tk.TOP, anchor=tk.E)
        
        self._tester_container = ttk.Frame(tester_outer)
        self._tester_container.pack(fill=tk.BOTH, expand=True)
        self._tester_frame = self._tester_container
        self._tester_row   = 0
        self._refresh_testers()

        # ── Section 6: Action Buttons ─────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=5, column=0, pady=1)

        self.checkout_btn = ttk.Button(btn_frame, text="▶ Start Checkout",
                                       style='Accent.TButton',
                                       command=self._start_checkout)
        self.checkout_btn.pack(side=tk.LEFT, padx=3)
        self.context.lockable_buttons.append(self.checkout_btn)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹ Stop Checkout",
                                   command=self._stop_checkout)
        self.stop_btn.pack(side=tk.LEFT, padx=3)
        self.stop_btn.state(["disabled"])  # Initially disabled
        
        ttk.Button(btn_frame, text="Generate XML Only", width=18,
                   command=self._generate_xml_only).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="📥 Import XML", width=14,
                   command=self._import_xml).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="Select All", width=10,
                   command=self._select_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="Deselect All", width=12,
                   command=self._deselect_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="📂 Queue Folder", width=15,   # Fix #7
                   command=self._open_queue_folder).pack(side=tk.LEFT, padx=3)

        # ── Section 7: Results ────────────────────────────────────────────
        results_frame = ttk.LabelFrame(self, text="Checkout Results", padding="2")
        results_frame.grid(row=6, column=0, sticky="we", pady=1)

        results_btn_bar = ttk.Frame(results_frame)                # Fix #6: Clear button bar
        results_btn_bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(results_btn_bar, text="Clear", width=6,
                   command=self._clear_results).pack(side=tk.RIGHT)

        self.results_text = tk.Text(results_frame, height=3, state=tk.DISABLED,
                                    font=("Consolas", 9), wrap=tk.WORD)
        results_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL,
                                        command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=results_scroll.set)
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ──────────────────────────────────────────────────────────────────────
    # PROFILE GENERATION TABLE — DATA MANAGEMENT
    # ──────────────────────────────────────────────────────────────────────

    def _profile_add_default_row(self):
        """Add the default initial row with 'None' in Dummy_Lot."""
        row_data = {
            "Form_Factor": "",
            "Material_Desc": "",
            "CFGPN": "",
            "MCTO_#1": "",
            "Dummy_Lot": "None",
            "Step": "",
            "MID": "",
            "Tester": "",
            "Primitive": "",
            "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()

    def _profile_add_row(self):
        """Add a new empty row to the profile generation table."""
        row_data = {
            "Form_Factor": "",
            "Material_Desc": "",
            "CFGPN": "",
            "MCTO_#1": "",
            "Dummy_Lot": "",
            "Step": "",
            "MID": "",
            "Tester": "",
            "Primitive": "",
            "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()
        self._update_profile_status()

    def _profile_remove_row(self):
        """Remove the selected row from the profile generation table."""
        sel = self._profile_grid.selection()
        if not sel:
            self.show_error("No Selection", "Select a row to remove.")
            return
        # Find the index of the selected item
        item_id = sel[0]
        idx = self._profile_grid.index(item_id)
        if 0 <= idx < len(self._profile_data):
            self._profile_data.pop(idx)
        self._refresh_profile_grid()
        self._update_profile_status()

    def _refresh_profile_grid(self):
        """Refresh the Treeview grid from internal _profile_data."""
        for row in self._profile_grid.get_children():
            self._profile_grid.delete(row)
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        for row_dict in self._profile_data:
            values = [str(row_dict.get(col, "")) for col in cols]
            self._profile_grid.insert("", tk.END, values=values)
        self._profile_row_count_label.configure(
            text=f"{len(self._profile_data)} row(s)")

    def _update_profile_status(self):
        """Update the status label under the profile grid."""
        count = len(self._profile_data)
        if count == 0:
            self._profile_status_label.configure(
                text="No data — use 'Load from CRT' or 'Add Row' to populate",
                foreground="#cc6600")
        else:
            self._profile_status_label.configure(
                text=f"{count} row(s) loaded — double-click a cell to edit",
                foreground="#0078d4")

    # Mapping from CRT Excel column names → profile grid column names
    _CRT_TO_PROFILE_MAP = {
        "Material description": "Material_Desc",
        "CFGPN":                "CFGPN",
        "MCTO_#1":              "MCTO_#1",
        "Form_Factor":          "Form_Factor",
        "Dummy_Lot":            "Dummy_Lot",
        "Step Name":            "Step",
        "Step Status":          "Step",
        # Direct grid column names (pass-through)
        "Material_Desc":        "Material_Desc",
        "Step":                 "Step",
        "MID":                  "MID",
        "Tester":               "Tester",
        "Primitive":            "Primitive",
        "Dut":                  "Dut",
        "ATTR_OVERWRITE":       "ATTR_OVERWRITE",
    }

    def _profile_import_excel(self):
        """Import profile generation data from an Excel file."""
        excel_path = self.context.get_var('checkout_excel_path').get().strip()
        initial_dir = os.path.dirname(excel_path) if excel_path and os.path.isdir(os.path.dirname(excel_path)) else "/"
        path = filedialog.askopenfilename(
            title="Import Profile Table from Excel",
            initialdir=initial_dir,
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if not path:
            return
        try:
            import pandas as pd
            if path.endswith(".xlsx"):
                df = pd.read_excel(path, engine="openpyxl", dtype=str)
            else:
                df = pd.read_excel(path, engine="xlrd", dtype=str)
            df = df.fillna("")

            cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
            # Build reverse lookup: for each grid col, find matching Excel col
            excel_to_grid = {}
            for excel_col in df.columns:
                grid_col = self._CRT_TO_PROFILE_MAP.get(excel_col)
                if grid_col and grid_col in cols:
                    excel_to_grid[excel_col] = grid_col

            self._profile_data = []
            for _, row in df.iterrows():
                row_dict = {col: "" for col in cols}
                # Map Excel columns to grid columns
                for excel_col, grid_col in excel_to_grid.items():
                    val = str(row.get(excel_col, "")).strip()
                    if val and val.lower() != "none":
                        row_dict[grid_col] = val
                # Also try direct column name match for any unmapped columns
                for col in cols:
                    if not row_dict.get(col) and col in df.columns:
                        val = str(row.get(col, "")).strip()
                        if val and val.lower() != "none":
                            row_dict[col] = val
                self._profile_data.append(row_dict)
            self._refresh_profile_grid()
            self._update_profile_status()
            self.log(f"✓ Profile table imported: {len(self._profile_data)} row(s) from {os.path.basename(path)}")
        except Exception as e:
            self.show_error("Import Error", f"Cannot import file:\n{path}\n\n{e}")

    def _profile_export_excel(self):
        """Export profile generation data to an Excel file."""
        if not self._profile_data:
            self.show_error("No Data", "No profile data to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Profile Table to Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return
        try:
            import pandas as pd
            df = pd.DataFrame(self._profile_data)
            df.to_excel(path, index=False, engine="openpyxl")
            self.log(f"✓ Profile table exported to {os.path.basename(path)}")
        except Exception as e:
            self.show_error("Export Error", f"Cannot export file:\n{path}\n\n{e}")

    def _profile_load_from_crt(self):
        """
        Load CRT data and auto-populate the profile generation table.
        Reads from the CRT Excel file, auto-fills Form_Factor, Material_Desc,
        CFGPN, MCTO_#1, Dummy_Lot from CRT data, and leaves Step, MID, Tester,
        Primitive, Dut, ATTR_OVERWRITE for user input. All columns remain editable.
        """
        excel_path = self.context.get_var('checkout_excel_path').get().strip()
        cfgpn_flt  = self.context.get_var('checkout_cfgpn_filter').get().strip()

        # Delegate to controller which loads CRT data in background
        self.context.controller.checkout_controller.load_crt_for_profile(
            excel_path=excel_path, cfgpn_filter=cfgpn_flt)

    # ──────────────────────────────────────────────────────────────────────
    # PROFILE GRID — INLINE EDITING
    # ──────────────────────────────────────────────────────────────────────

    def _on_profile_cell_double_click(self, event):
        """Handle double-click on a profile grid cell for inline editing."""
        region = self._profile_grid.identify_region(event.x, event.y)
        if region != "cell":
            return

        col_id = self._profile_grid.identify_column(event.x)
        item_id = self._profile_grid.identify_row(event.y)
        if not item_id or not col_id:
            return

        # Convert column identifier (#1, #2, ...) to column name
        col_idx = int(col_id.replace("#", "")) - 1
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        if col_idx < 0 or col_idx >= len(cols):
            return
        col_name = cols[col_idx]

        # All columns are editable — auto-populated columns can be overridden by user
        row_idx = self._profile_grid.index(item_id)
        if row_idx < 0 or row_idx >= len(self._profile_data):
            return

        current_value = self._profile_data[row_idx].get(col_name, "")

        # Special handling for ATTR_OVERWRITE — open dialog
        if col_name == "ATTR_OVERWRITE":
            self._show_attr_overwrite_dialog(row_idx)
            return

        # For other editable columns, use inline entry editing
        self._start_inline_edit(item_id, col_id, col_idx, row_idx, col_name, current_value)

    def _start_inline_edit(self, item_id, col_id, col_idx, row_idx, col_name, current_value):
        """Create an inline Entry widget over the cell for editing."""
        # Get cell bounding box
        bbox = self._profile_grid.bbox(item_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox

        # Create entry widget
        edit_entry = ttk.Entry(self._profile_grid, width=w // 8)
        edit_entry.insert(0, current_value)
        edit_entry.select_range(0, tk.END)
        edit_entry.place(x=x, y=y, width=w, height=h)
        edit_entry.focus_set()

        def _save_edit(event=None):
            new_value = edit_entry.get().strip()
            self._profile_data[row_idx][col_name] = new_value
            self._refresh_profile_grid()
            edit_entry.destroy()

        def _cancel_edit(event=None):
            edit_entry.destroy()

        edit_entry.bind("<Return>", _save_edit)
        edit_entry.bind("<Escape>", _cancel_edit)
        edit_entry.bind("<FocusOut>", _save_edit)

    def _show_attr_overwrite_dialog(self, row_idx):
        """
        Show the ATTR_OVERWRITE edit dialog, mimicking the CRT Automation Tools.
        Format: Section;AttrName;AttrValue;Section;AttrName;AttrValue;...
        """
        current_value = self._profile_data[row_idx].get("ATTR_OVERWRITE", "")

        dialog = tk.Toplevel(self.winfo_toplevel())
        dialog.title("ATTR_OVERWRITE Editor")
        dialog.geometry("600x450")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        # ── Input fields ──────────────────────────────────────────────────
        input_frame = ttk.LabelFrame(dialog, text="Add Attribute Override", padding="5")
        input_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(input_frame, text="Section:").grid(row=0, column=0, sticky=tk.W, padx=2)
        section_var = tk.StringVar()
        section_combo = ttk.Combobox(input_frame, textvariable=section_var,
                                     values=["MAM", "MCTO", "CFGPN", "EQUIPMENT"],
                                     width=15)
        section_combo.grid(row=0, column=1, sticky="we", padx=2)

        ttk.Label(input_frame, text="Attr Name:").grid(row=1, column=0, sticky=tk.W, padx=2)
        attr_name_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=attr_name_var, width=20).grid(
            row=1, column=1, sticky="we", padx=2)

        ttk.Label(input_frame, text="Attr Value:").grid(row=2, column=0, sticky=tk.W, padx=2)
        attr_value_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=attr_value_var, width=20).grid(
            row=2, column=1, sticky="we", padx=2)

        input_frame.columnconfigure(1, weight=1)

        # ── Entries grid ──────────────────────────────────────────────────
        entries_frame = ttk.LabelFrame(dialog, text="Current Overrides", padding="5")
        entries_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        entry_cols = ["Section", "Attr Name", "Attr Value"]
        entries_tree = ttk.Treeview(entries_frame, columns=entry_cols,
                                    show="headings", height=6, selectmode="browse")
        for col in entry_cols:
            entries_tree.heading(col, text=col)
            entries_tree.column(col, width=150, stretch=True)
        entries_tree.pack(fill=tk.BOTH, expand=True)

        # Populate from existing value
        if current_value:
            parts = current_value.split(";")
            for i in range(0, len(parts), 3):
                if i + 2 < len(parts):
                    entries_tree.insert("", tk.END,
                                        values=(parts[i], parts[i+1], parts[i+2]))

        # ── Buttons ───────────────────────────────────────────────────────
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        def _add_entry():
            s = section_var.get().strip().upper()
            n = attr_name_var.get().strip().upper()
            v = attr_value_var.get().strip().upper()
            if not s or not n or not v:
                return
            # Check for duplicate
            for child in entries_tree.get_children():
                vals = entries_tree.item(child, "values")
                if vals[0] == s and vals[1] == n and vals[2] == v:
                    return
            entries_tree.insert("", tk.END, values=(s, n, v))
            section_var.set("")
            attr_name_var.set("")
            attr_value_var.set("")

        def _remove_entry():
            sel = entries_tree.selection()
            if sel:
                entries_tree.delete(sel[0])

        def _remove_all():
            for child in entries_tree.get_children():
                entries_tree.delete(child)

        def _save():
            parts = []
            for child in entries_tree.get_children():
                vals = entries_tree.item(child, "values")
                parts.append(f"{vals[0]};{vals[1]};{vals[2]}")
            self._profile_data[row_idx]["ATTR_OVERWRITE"] = ";".join(parts)
            self._refresh_profile_grid()
            dialog.destroy()

        def _cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="Add", command=_add_entry).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Remove", command=_remove_entry).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Remove All", command=_remove_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Save", command=_save).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="Cancel", command=_cancel).pack(side=tk.RIGHT, padx=2)

    def _on_profile_row_select(self, event):
        """When a profile row is selected, update the status bar with details."""
        sel = self._profile_grid.selection()
        if not sel:
            return
        values = self._profile_grid.item(sel[0], "values")
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]

        def _get(col_name):
            idx = cols.index(col_name) if col_name in cols else -1
            return values[idx] if 0 <= idx < len(values) else ""

        cfgpn = _get("CFGPN")
        dummy_lot = _get("Dummy_Lot")
        mid = _get("MID")
        step = _get("Step")
        tester = _get("Tester")

        self._profile_status_label.configure(
            text=(f"Selected: CFGPN={cfgpn}  Lot={dummy_lot}  "
                  f"MID={mid}  Step={step}  Tester={tester}"),
            foreground="#0078d4")

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

    def _browse_excel(self):
        """Browse for a CRT Excel file to use as profile generation source."""
        path = filedialog.askopenfilename(
            title="Select CRT Excel File",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if path:
            self.context.get_var('checkout_excel_path').set(path)

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
        self.stop_btn.state(["!disabled"])  # Enable Stop button
        self.context.controller.checkout_controller.start_checkout(params)

    def _stop_checkout(self):
        """Signal the controller to stop the running checkout."""
        self.context.controller.checkout_controller.stop_checkout()
        self.stop_btn.state(["disabled"])

    def _collect_params(self):
        """Gather and validate all form inputs. Returns params dict or None."""
        # Paths & test case fields (still in UI)
        tgz_path      = self.context.get_var('checkout_tgz_path').get().strip()
        hot_folder    = self.context.get_var('checkout_hot_folder').get().strip()
        method        = self.context.get_var('checkout_detect_method').get()
        timeout_m     = self.context.get_var('checkout_timeout_min').get()
        notify        = self.context.get_var('checkout_notify_teams').get()
        hostnames     = [h for h, v in self._tester_vars.items() if v.get()]
        webhook_url   = self.context.get_var('checkout_webhook_url').get().strip()

        tc_passing    = self.context.get_var('checkout_tc_passing').get()
        tc_fail       = self.context.get_var('checkout_tc_force_fail').get()
        passing_label = self.context.get_var('checkout_tc_passing_label').get().strip()
        fail_label    = self.context.get_var('checkout_tc_fail_label').get().strip()
        fail_desc     = self.context.get_var('checkout_tc_fail_desc').get().strip()

        # Collect profile generation table data (replaces old DUT Identity fields)
        profile_table = self._profile_data.copy() if self._profile_data else []

        # Validation
        if not tgz_path:
            self.show_error("Input Error", "Enter the TGZ path.")
            return None
        if not hot_folder:
            self.show_error("Input Error", "Enter the hot folder path.")
            return None
        if not hostnames:
            self.show_error("Input Error", "Select at least one tester.")
            return None
        if not profile_table:
            self.show_error("Input Error",
                            "Profile table is empty. Use 'Load from CRT' or 'Add Row'.")
            return None
        if not tc_passing and not tc_fail:
            self.show_error("Input Error",
                            "Select at least one test case (PASSING or FORCE FAIL).")
            return None

        # Build test cases list
        test_cases = []
        if tc_passing:
            test_cases.append({"type": "passing",    "label": passing_label or "passing"})
        if tc_fail:
            test_cases.append({"type": "force_fail", "label": fail_label or "force_fail_1",
                                "description": fail_desc})

        return {
            "tgz_path":        tgz_path,
            "hot_folder":      hot_folder,
            "test_cases":      test_cases,
            "detect_method":   method,
            "timeout_seconds": timeout_m * 60,
            "notify_teams":    notify,
            "webhook_url":     webhook_url,
            "hostnames":       hostnames,
            "profile_table":   profile_table,
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

    def on_profile_data_loaded(self, profile_rows: List[Dict]):
        """
        Callback when CRT data is loaded for profile generation.
        Populates the profile generation table with auto-filled CRT columns
        (Form_Factor, Material_Desc, CFGPN, MCTO_#1, Dummy_Lot) and empty
        editable columns (Step, MID, Tester, Primitive, Dut, ATTR_OVERWRITE).
        All columns remain user-editable.
        """
        self._profile_data = profile_rows
        self._refresh_profile_grid()
        self._update_profile_status()
        count = len(profile_rows)
        self.log(f"✓ Profile table loaded: {count} row(s) from CRT data")

    def on_crt_grid_loaded(self, crt_data):
        """
        Legacy callback — converts CRT grid data into profile generation rows.
        Auto-populates Form_Factor, Material_Desc, CFGPN, MCTO_#1, Dummy_Lot
        from CRT data. Leaves Step, MID, Tester, Primitive, Dut, ATTR_OVERWRITE
        for user input.
        """
        rows = crt_data.get("rows", [])
        self._profile_data = []
        for row_dict in rows:
            material_desc = str(row_dict.get("Material description", "")).strip()
            cfgpn = str(row_dict.get("CFGPN", "")).strip()
            profile_row = {
                "Form_Factor": str(row_dict.get("Form_Factor", "")).strip(),
                "Material_Desc": material_desc,
                "CFGPN": cfgpn,
                "MCTO_#1": str(row_dict.get("MCTO_#1", "")).strip(),
                "Dummy_Lot": str(row_dict.get("Dummy_Lot", material_desc or "None")).strip(),
                "Step": "",
                "MID": "",
                "Tester": "",
                "Primitive": "",
                "Dut": "",
                "ATTR_OVERWRITE": "",
            }
            self._profile_data.append(profile_row)

        if not self._profile_data:
            self._profile_add_default_row()

        self._refresh_profile_grid()
        self._update_profile_status()
        count = crt_data.get("count", 0)
        self.log(f"✓ Profile table loaded from CRT: {count} row(s)")

    def on_autofill_completed(self, mid, cfgpn, fw_ver):
        """Log auto-fill results. DUT Identity fields are now in the profile table."""
        self.log(f"✓ Auto-filled: MID={mid}  CFGPN={cfgpn}  FW={fw_ver}")

    def on_xml_imported(self, data: dict):
        """
        Populate form fields from a parsed SLATE Profile XML.

        data keys (all optional / may be empty):
          tgz_path, env, mid, lot_prefix, dut_locations (list[str]), dut_slots (int)
        """
        if data.get("tgz_path"):
            self.context.get_var('checkout_tgz_path').set(data["tgz_path"])

        filled = []
        if data.get("tgz_path"):   filled.append("TGZ")
        if data.get("env"):        filled.append(f"ENV={data['env']}")
        if data.get("mid"):        filled.append(f"MID={data['mid']}")
        if data.get("lot_prefix"): filled.append(f"Lot={data['lot_prefix']}")

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
            self.stop_btn.state(["disabled"])
            self.unlock_gui()

    def get_profile_table_data(self) -> List[Dict]:
        """Return the current profile generation table data."""
        return self._profile_data.copy()
