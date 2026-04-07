# -*- coding: utf-8 -*-
import os
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import Any, List, Dict
from view.tabs.base_tab import BaseTab

# Status constants (shared with result_collector model)
_STATUS_PASS    = "PASS"
_STATUS_FAIL    = "FAIL"
_STATUS_RUNNING = "RUNNING"


# ─────────────────────────────────────────────────────────────────────────────
# Tooltip helper (system-default colours)
# ─────────────────────────────────────────────────────────────────────────────

class _ToolTip:
    """Lightweight balloon tooltip for any widget."""

    def __init__(self, widget, text: str, delay: int = 500):
        self._widget = widget
        self._text   = text
        self._delay  = delay
        self._id     = None
        self._tw     = None
        widget.bind("<Enter>",  self._schedule, add="+")
        widget.bind("<Leave>",  self._cancel,   add="+")
        widget.bind("<Button>", self._cancel,   add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._id = self._widget.after(self._delay, self._show)

    def _cancel(self, _=None):
        if self._id:
            self._widget.after_cancel(self._id)
            self._id = None
        if self._tw:
            self._tw.destroy()
            self._tw = None

    def _show(self):
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tw = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(
            tw, text=self._text, justify=tk.LEFT,
            relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 8),
            padx=6, pady=4, wraplength=340,
        ).pack()


def _tip(widget, text: str):
    _ToolTip(widget, text)


# ─────────────────────────────────────────────────────────────────────────────
# CheckoutTab
# ─────────────────────────────────────────────────────────────────────────────

class CheckoutTab(BaseTab):
    """
    Checkout Tab (View) — Enhanced UI/UX, default system theme.

    Layout:
      1. Profile Generation Table
         - Toolbar: Add Row / Remove Row / Import / Export / Load from CRT
         - CRT source row: Excel path + Browse + Filter CFGPN
         - Editable Treeview grid (11 columns)
         - Status bar
      2. Checkout Paths & Test Cases
         - TGZ path
         - Test Cases (PASS / FAIL)
         - Hot Folder
      3. SLATE Detection  |  Tester Selection   (side-by-side)
      4. Action Buttons
      5. Checkout Results
    """

    _BADGE_COLOURS = {
        "IDLE":        ("#888888", "white"),
        "PENDING":     ("#0078d4", "white"),
        "RUNNING":     ("#005a9e", "white"),
        "COLLECTING":  ("#8764b8", "white"),
        "SUCCESS":     ("#107c10", "white"),
        "PARTIAL":     ("#ca5010", "white"),
        "FAILED":      ("#a80000", "white"),
        "TIMEOUT":     ("#ca5010", "white"),
        "XML_DONE":    ("#2d7d9a", "white"),
        "XML_PARTIAL": ("#ca5010", "white"),
        "XML_FAIL":    ("#a80000", "white"),
    }

    # Profile Generation Table columns
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

    _AUTO_POPULATED_COLS = {"Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot"}

    _EDITABLE_COLS = {
        "Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot",
        "Step", "MID", "Tester", "Primitive", "Dut", "ATTR_OVERWRITE",
    }

    _CRT_TO_PROFILE_MAP = {
        "Material description":  "Material_Desc",
        "Material Description":  "Material_Desc",
        "Material_Description":  "Material_Desc",
        "Material_Desc":         "Material_Desc",
        "Material_desc":         "Material_Desc",
        "CFGPN":                 "CFGPN",
        "MCTO_#1":               "MCTO_#1",
        "Form_Factor":           "Form_Factor",
        "Form Factor":           "Form_Factor",
        "Dummy_Lot":             "Dummy_Lot",
        "Dummy Lot":             "Dummy_Lot",
        "Step Name":             "Step",
        "Step Status":           "Step",
        "Step":                  "Step",
        "MID":                   "MID",
        "Tester":                "Tester",
        "Primitive":             "Primitive",
        "Dut":                   "Dut",
        "DUT":                   "Dut",
        "ATTR_OVERWRITE":        "ATTR_OVERWRITE",
        "Attr_Overwrite":        "ATTR_OVERWRITE",
    }

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Checkout")
        self._badge_labels             = {}
        self._phase_labels             = {}
        self._tester_vars              = {}
        self._tester_frame: Any        = None
        self._tester_row               = 0
        self._profile_data: List[Dict] = []
        self._init_vars()
        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────
    # VARIABLE INITIALISATION
    # ──────────────────────────────────────────────────────────────────────

    def _init_vars(self):
        ctx = self.context
        _sv = lambda name, val="": ctx.set_var(name, tk.StringVar(value=val)) \
              if ctx.get_var(name) is None else None
        _bv = lambda name, val=False: ctx.set_var(name, tk.BooleanVar(value=val)) \
              if ctx.get_var(name) is None else None
        _iv = lambda name, val=0: ctx.set_var(name, tk.IntVar(value=val)) \
              if ctx.get_var(name) is None else None

        _sv('checkout_site', 'PENANG')
        _sv('checkout_tgz_path')
        _sv('checkout_recipe_override')
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
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', '..', 'Documents', 'incoming_crt.xlsx')
            ))
        _sv('checkout_webhook_url',
            ctx.config.get('notifications', {}).get('teams_webhook_url', ''))
        _iv('checkout_timeout_min', 60)
        _bv('checkout_tc_passing', True)
        _bv('checkout_tc_force_fail', False)
        _bv('checkout_notify_teams', True)
        _sv('checkout_form_factor', '')
        _bv('checkout_gen_tmptravl', True)
        _bv('checkout_autostart', False)

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI — scrollable canvas wrapper
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        canvas   = tk.Canvas(self, highlightthickness=0, bd=0)
        v_scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")

        self._inner = ttk.Frame(canvas, padding=(8, 6, 8, 12))
        self._inner.columnconfigure(0, weight=1)
        _win = canvas.create_window((0, 0), window=self._inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(_win, width=event.width)

        self._inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _mousewheel)

        f = self._inner
        self._build_profile_section(f, row=0)
        self._build_paths_section(f, row=1)
        self._build_detection_tester_section(f, row=2)
        self._build_action_buttons(f, row=3)
        self._build_results_section(f, row=4)
        self._build_result_collection_section(f, row=5)

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 1 — Profile Generation
    # ──────────────────────────────────────────────────────────────────────

    def _build_profile_section(self, parent, row):
        frm = ttk.LabelFrame(parent, text="  Profile Generation  ", padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 14))
        frm.columnconfigure(0, weight=1)

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = ttk.Frame(frm)
        toolbar.grid(row=0, column=0, sticky="we", pady=(0, 6))

        add_btn = ttk.Button(toolbar, text="Add Row", command=self._profile_add_row)
        add_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(add_btn, "Append a new empty row to the profile table.")

        rem_btn = ttk.Button(toolbar, text="Remove Row", command=self._profile_remove_row)
        rem_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(rem_btn, "Delete the currently selected row from the profile table.")

        imp_btn = ttk.Button(toolbar, text="Import from Excel", command=self._profile_import_excel)
        imp_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(imp_btn, "Import profile rows from an Excel file (.xlsx / .xls).")

        exp_btn = ttk.Button(toolbar, text="Export to Excel", command=self._profile_export_excel)
        exp_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(exp_btn, "Export the current profile table to an Excel file.")

        crt_btn = ttk.Button(toolbar, text="Load from CRT", command=self._profile_load_from_crt)
        crt_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(crt_btn, "Read the CRT Excel file and auto-populate Form_Factor, Material_Desc,\n"
             "CFGPN, MCTO_#1, Dummy_Lot. Editable columns (Step, MID, Tester…) remain blank.")

        hw_btn = ttk.Button(toolbar, text="View/Edit Hardware", command=self._open_hardware_config)
        hw_btn.pack(side=tk.LEFT, padx=(0, 0))
        _tip(hw_btn, "View and edit hardware configuration (DIB_TYPE, MACHINE_MODEL,\n"
             "MACHINE_VENDOR) used for profile generation and tmptravl creation.")

        # ── CRT Source row ────────────────────────────────────────────────
        src_row = ttk.Frame(frm)
        src_row.grid(row=1, column=0, sticky="we", pady=(0, 6))
        src_row.columnconfigure(1, weight=1)

        ttk.Label(src_row, text="Excel File:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 6))
        excel_entry = ttk.Entry(src_row,
                  textvariable=self.context.get_var('checkout_excel_path'))
        excel_entry.grid(row=0, column=1, sticky="we")
        _tip(excel_entry, "Path to the CRT Excel source file (incoming_crt.xlsx).\n"
             "Used by 'Load from CRT' to auto-populate the profile table.")

        browse_btn = ttk.Button(src_row, text="Browse",
                    command=self._browse_excel)
        browse_btn.grid(row=0, column=2, padx=(6, 12))
        _tip(browse_btn, "Browse for a CRT Excel file.")

        ttk.Label(src_row, text="Filter CFGPN:").grid(
            row=0, column=3, sticky=tk.W, padx=(0, 6))
        filter_entry = ttk.Entry(src_row,
                  textvariable=self.context.get_var('checkout_cfgpn_filter'),
                  width=18)
        filter_entry.grid(row=0, column=4, sticky=tk.W)
        _tip(filter_entry, "Optional CFGPN substring filter applied when loading CRT data.\n"
             "Leave blank to load all rows.")

        # ── Profile Grid ──────────────────────────────────────────────────
        grid_container = ttk.Frame(frm)
        grid_container.grid(row=2, column=0, sticky="nsew")
        grid_container.columnconfigure(0, weight=1)

        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        self._profile_grid = ttk.Treeview(
            grid_container, columns=cols, show="headings",
            height=5, selectmode="browse")
        for col_name, col_width in self._PROFILE_GEN_COLUMNS:
            self._profile_grid.heading(col_name, text=col_name, anchor=tk.W)
            self._profile_grid.column(col_name, width=col_width, minwidth=40,
                                      stretch=True, anchor=tk.W)

        pg_scroll_y = ttk.Scrollbar(grid_container, orient=tk.VERTICAL,
                                    command=self._profile_grid.yview)
        pg_scroll_x = ttk.Scrollbar(grid_container, orient=tk.HORIZONTAL,
                                    command=self._profile_grid.xview)
        self._profile_grid.configure(yscrollcommand=pg_scroll_y.set,
                                     xscrollcommand=pg_scroll_x.set)
        self._profile_grid.grid(row=0, column=0, sticky="nsew")
        pg_scroll_y.grid(row=0, column=1, sticky="ns")
        pg_scroll_x.grid(row=1, column=0, sticky="ew")

        self._profile_grid.bind("<Double-1>", self._on_profile_cell_double_click)
        self._profile_grid.bind("<<TreeviewSelect>>", self._on_profile_row_select)
        _tip(self._profile_grid,
             "Double-click any cell to edit it inline.\n"
             "Double-click ATTR_OVERWRITE to open the attribute editor dialog.")

        # ── Status bar ────────────────────────────────────────────────────
        status_bar = ttk.Frame(frm)
        status_bar.grid(row=3, column=0, sticky="we", pady=(4, 0))

        self._profile_status_label = ttk.Label(
            status_bar,
            text="No data — use 'Load from CRT' or 'Add Row' to populate",
            foreground="#cc6600", font=("Segoe UI", 8))
        self._profile_status_label.pack(side=tk.LEFT)

        self._profile_row_count_label = ttk.Label(
            status_bar, text="0 row(s)", font=("Segoe UI", 8))
        self._profile_row_count_label.pack(side=tk.RIGHT)

        # Add initial default row
        self._profile_add_default_row()

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 2 — Checkout Paths & Test Cases
    # ──────────────────────────────────────────────────────────────────────

    def _build_paths_section(self, parent, row):
        frm = ttk.LabelFrame(parent, text="  Checkout Paths & Test Cases  ",
                             padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 14))
        frm.columnconfigure(1, weight=1)

        # Site selection row
        from model.site_config import _DEFAULT_SITES, _DEFAULT_FORM_FACTORS
        ttk.Label(frm, text="Site:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        site_combo = ttk.Combobox(
            frm, textvariable=self.context.get_var('checkout_site'),
            values=list(_DEFAULT_SITES), state="readonly", width=15)
        site_combo.grid(row=0, column=1, sticky=tk.W, pady=(0, 4))
        _tip(site_combo,
             "Select the manufacturing site.\n"
             "Routes MAM queries and other operations to the correct servers.")

        # Form Factor dropdown (global default for all profile rows)
        ttk.Label(frm, text="Form Factor:").grid(
            row=0, column=2, sticky=tk.W, padx=(16, 8), pady=(0, 4))
        ff_combo = ttk.Combobox(
            frm, textvariable=self.context.get_var('checkout_form_factor'),
            values=[""] + list(_DEFAULT_FORM_FACTORS),
            state="readonly", width=10)
        ff_combo.grid(row=0, column=3, sticky=tk.W, pady=(0, 4))
        _tip(ff_combo,
             "Default form factor for new profile rows.\n"
             "Per-row Form_Factor in the profile table takes precedence.")

        # Generate TempTraveler checkbox
        gen_tt_cb = ttk.Checkbutton(
            frm, text="Generate TempTraveler",
            variable=self.context.get_var('checkout_gen_tmptravl'))
        gen_tt_cb.grid(row=0, column=4, sticky=tk.W, padx=(16, 0), pady=(0, 4))
        _tip(gen_tt_cb,
             "Generate a TempTraveler .dat file for each MID.\n"
             "Uses the template in model/resources/template_tmptravl.dat.")

        # Auto Start checkbox — mirrors CAT's autostart_checkbox
        autostart_cb = ttk.Checkbutton(
            frm, text="Auto Start",
            variable=self.context.get_var('checkout_autostart'))
        autostart_cb.grid(row=0, column=5, sticky=tk.W, padx=(16, 0), pady=(0, 4))
        _tip(autostart_cb,
             "Set AutoStart=True in the generated XML profile.\n"
             "When enabled, SLATE automatically starts the test without\n"
             "requiring a manual 'Run Test' click.\n"
             "Mirrors CAT's Auto Start checkbox.")

        # TGZ row
        ttk.Label(frm, text="TGZ:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        tgz_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_tgz_path'))
        tgz_entry.grid(row=1, column=1, sticky="we", pady=(0, 4))
        _tip(tgz_entry, "Path to the compiled .tgz test program archive.\n"
             "Default browse location: P:\\temp\\BENTO\\RELEASE_TGZ")
        tgz_btn = ttk.Button(frm, text="…", width=3, command=self._browse_tgz)
        tgz_btn.grid(row=1, column=2, padx=(6, 0), pady=(0, 4))
        _tip(tgz_btn, "Browse for a compiled TGZ archive.")

        # Recipe override row
        ttk.Label(frm, text="Recipe:").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        recipe_frame = ttk.Frame(frm)
        recipe_frame.grid(row=2, column=1, columnspan=2, sticky="we", pady=(0, 4))
        self._recipe_combo = ttk.Combobox(
            recipe_frame,
            textvariable=self.context.get_var('checkout_recipe_override'),
            width=40,
            state="normal",
        )
        self._recipe_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tip(self._recipe_combo,
             "Recipe file override. Leave empty for auto-detection.\n"
             "Click 'Scan' to list available recipes from the TGZ archive.\n"
             "Format: RECIPE\\ProductName_neosem_STEP.XML")
        scan_btn = ttk.Button(recipe_frame, text="Scan TGZ", width=9,
                              command=self._scan_tgz_recipes)
        scan_btn.pack(side=tk.LEFT, padx=(6, 0))
        _tip(scan_btn, "Scan the selected TGZ archive for available recipe files\n"
             "and populate the dropdown list.")

        # Separator
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=3, column=0, columnspan=3, sticky="we", pady=(0, 4))

        # Test Cases row
        tc_frame = ttk.Frame(frm)
        tc_frame.grid(row=4, column=0, columnspan=3, sticky="we", pady=(0, 4))

        ttk.Label(tc_frame, text="TC:").pack(side=tk.LEFT, padx=(0, 8))

        pass_cb = ttk.Checkbutton(tc_frame, text="PASS",
                      variable=self.context.get_var('checkout_tc_passing'))
        pass_cb.pack(side=tk.LEFT)
        _tip(pass_cb, "Include a passing test case in the checkout run.")

        pass_entry = ttk.Entry(tc_frame,
                   textvariable=self.context.get_var('checkout_tc_passing_label'),
                   width=12)
        pass_entry.pack(side=tk.LEFT, padx=(4, 14))
        _tip(pass_entry, "Label for the passing test case (default: passing).")

        fail_cb = ttk.Checkbutton(tc_frame, text="FAIL",
                      variable=self.context.get_var('checkout_tc_force_fail'))
        fail_cb.pack(side=tk.LEFT)
        _tip(fail_cb, "Include a force-fail test case in the checkout run.")

        fail_lbl_entry = ttk.Entry(tc_frame,
                   textvariable=self.context.get_var('checkout_tc_fail_label'),
                   width=14)
        fail_lbl_entry.pack(side=tk.LEFT, padx=(4, 4))
        _tip(fail_lbl_entry, "Label for the force-fail test case (default: force_fail_1).")

        fail_desc_entry = ttk.Entry(tc_frame,
                   textvariable=self.context.get_var('checkout_tc_fail_desc'),
                   width=28)
        fail_desc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tip(fail_desc_entry, "Optional description for the force-fail test case.")

        # Separator
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky="we", pady=(0, 4))

        # Hot Folder row
        ttk.Label(frm, text="Hot Folder:").grid(
            row=6, column=0, sticky=tk.W, padx=(0, 8))
        hot_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_hot_folder'))
        hot_entry.grid(row=6, column=1, columnspan=2, sticky="we")
        _tip(hot_entry, "Path to the SLATE hot folder where XML profiles are dropped.\n"
             "Default: C:\\test_program\\playground_queue")

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 3 — SLATE Detection + Tester Selection (side-by-side)
    # ──────────────────────────────────────────────────────────────────────

    def _build_detection_tester_section(self, parent, row):
        side_frame = ttk.Frame(parent)
        side_frame.grid(row=row, column=0, sticky="we", pady=(0, 14))
        side_frame.columnconfigure(0, weight=1)
        side_frame.columnconfigure(1, weight=2)

        # ── SLATE Detection ───────────────────────────────────────────────
        detect_frame = ttk.LabelFrame(side_frame, text="  SLATE Detection  ",
                                      padding=(8, 6, 8, 8))
        detect_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        detect_frame.columnconfigure(1, weight=1)

        ttk.Label(detect_frame, text="Method:").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 4))
        method_cb = ttk.Combobox(detect_frame,
                     textvariable=self.context.get_var('checkout_detect_method'),
                     values=["AUTO", "LOG", "FOLDER", "CPU", "TIMEOUT"],
                     state="readonly", width=10)
        method_cb.grid(row=0, column=1, columnspan=2, sticky=tk.W,
                       padx=(6, 0), pady=(0, 4))
        _tip(method_cb,
             "How BENTO detects SLATE completion:\n"
             "  AUTO    – tries LOG → FOLDER → CPU\n"
             "  LOG     – watches SLATE log file\n"
             "  FOLDER  – watches output folder\n"
             "  CPU     – monitors CPU idle\n"
             "  TIMEOUT – waits fixed duration")

        ttk.Label(detect_frame, text="Time (min):").grid(
            row=1, column=0, sticky=tk.W, pady=(0, 4))
        timeout_spin = ttk.Spinbox(detect_frame,
                    textvariable=self.context.get_var('checkout_timeout_min'),
                    from_=5, to=480, width=6)
        timeout_spin.grid(row=1, column=1, sticky=tk.W, padx=(6, 0), pady=(0, 4))
        _tip(timeout_spin, "Maximum wait time per tester (minutes). Range: 5–480.")

        # ── Tester Selection ──────────────────────────────────────────────
        tester_outer = ttk.LabelFrame(side_frame, text="  Tester Selection  ",
                                      padding=(8, 6, 8, 8))
        tester_outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        tester_outer.columnconfigure(0, weight=1)

        # Header bar
        hdr = ttk.Frame(tester_outer)
        hdr.grid(row=0, column=0, sticky="we", pady=(0, 6))
        ttk.Label(hdr, text="Select testers to include in this checkout run:",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        refresh_btn = ttk.Button(hdr, text="↻ Refresh",
                    command=self._refresh_testers)
        refresh_btn.pack(side=tk.RIGHT)
        _tip(refresh_btn, "Reload the tester list from bento_testers.json.")

        # Tester list
        self._tester_container = ttk.Frame(tester_outer)
        self._tester_container.grid(row=1, column=0, sticky="nsew")
        self._tester_frame = self._tester_container
        self._tester_row   = 0
        self._refresh_testers()

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 4 — Action Buttons
    # ──────────────────────────────────────────────────────────────────────

    def _build_action_buttons(self, parent, row):
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=0, pady=(0, 14))

        # Primary: Start
        self.checkout_btn = ttk.Button(
            btn_frame, text="▶ Start Checkout",
            style='Accent.TButton',
            command=self._start_checkout)
        self.checkout_btn.pack(side=tk.LEFT, padx=(0, 3))
        self.context.lockable_buttons.append(self.checkout_btn)
        _tip(self.checkout_btn,
             "Generate XML profiles and start the checkout run on all selected testers.")

        # Stop
        self.stop_btn = ttk.Button(
            btn_frame, text="⏹ Stop Checkout",
            command=self._stop_checkout)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.stop_btn.state(["disabled"])
        _tip(self.stop_btn, "Abort the running checkout on all testers.")

        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 10), pady=4)

        gen_btn = ttk.Button(btn_frame, text="Generate XML Only", width=18,
                   command=self._generate_xml_only)
        gen_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(gen_btn, "Build SLATE XML profile(s) and save to XML_OUTPUT folder.\n"
                      "Does NOT trigger checkout. Inspect XML, then copy to\n"
                      "CHECKOUT_QUEUE manually when ready.")

        imp_btn = ttk.Button(btn_frame, text="📥 Import XML", width=14,
                   command=self._import_xml)
        imp_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(imp_btn, "Load an existing SLATE XML profile and auto-fill TGZ path from it.")

        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(4, 10), pady=4)

        sel_btn = ttk.Button(btn_frame, text="Select All", width=10,
                   command=self._select_all)
        sel_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(sel_btn, "Select all testers.")

        desel_btn = ttk.Button(btn_frame, text="Deselect All", width=12,
                   command=self._deselect_all)
        desel_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(desel_btn, "Deselect all testers.")

        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(4, 10), pady=4)

        queue_btn = ttk.Button(btn_frame, text="📂 Queue Folder", width=15,
                   command=self._open_queue_folder)
        queue_btn.pack(side=tk.LEFT)
        _tip(queue_btn, "Open the BENTO checkout queue folder in Windows Explorer.")

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 5 — Checkout Results
    # ──────────────────────────────────────────────────────────────────────

    def _build_results_section(self, parent, row):
        frm = ttk.LabelFrame(parent, text="  Checkout Results  ",
                             padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 14))
        frm.columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ttk.Frame(frm)
        toolbar.grid(row=0, column=0, sticky="we", pady=(0, 4))
        ttk.Label(toolbar, text="Live output from checkout runs:",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        clear_btn = ttk.Button(toolbar, text="Clear", width=6,
                    command=self._clear_results)
        clear_btn.pack(side=tk.RIGHT)
        _tip(clear_btn, "Clear the results log.")

        # Text area
        result_container = ttk.Frame(frm)
        result_container.grid(row=1, column=0, sticky="nsew")
        result_container.columnconfigure(0, weight=1)

        self.results_text = tk.Text(
            result_container,
            height=5,
            state=tk.DISABLED,
            font=("Consolas", 9),
            wrap=tk.WORD,
        )
        results_scroll = ttk.Scrollbar(result_container, orient=tk.VERTICAL,
                                        command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=results_scroll.set)
        self.results_text.grid(row=0, column=0, sticky="nsew")
        results_scroll.grid(row=0, column=1, sticky="ns")

    # ──────────────────────────────────────────────────────────────────────
    # PROFILE TABLE — DATA MANAGEMENT
    # ──────────────────────────────────────────────────────────────────────

    def _profile_add_default_row(self):
        row_data = {
            "Form_Factor": "", "Material_Desc": "", "CFGPN": "",
            "MCTO_#1": "", "Dummy_Lot": "None", "Step": "",
            "MID": "", "Tester": "", "Primitive": "", "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()

    def _profile_add_row(self):
        row_data = {
            "Form_Factor": "", "Material_Desc": "", "CFGPN": "",
            "MCTO_#1": "", "Dummy_Lot": "", "Step": "",
            "MID": "", "Tester": "", "Primitive": "", "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()
        self._update_profile_status()

    def _profile_remove_row(self):
        sel = self._profile_grid.selection()
        if not sel:
            self.show_error("No Selection", "Select a row to remove.")
            return
        idx = self._profile_grid.index(sel[0])
        if 0 <= idx < len(self._profile_data):
            self._profile_data.pop(idx)
        self._refresh_profile_grid()
        self._update_profile_status()

    def _refresh_profile_grid(self):
        for row in self._profile_grid.get_children():
            self._profile_grid.delete(row)
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        for row_dict in self._profile_data:
            values = [str(row_dict.get(col, "")) for col in cols]
            self._profile_grid.insert("", tk.END, values=values)
        self._profile_row_count_label.configure(
            text=f"{len(self._profile_data)} row(s)")

    def _update_profile_status(self):
        count = len(self._profile_data)
        if count == 0:
            self._profile_status_label.configure(
                text="No data — use 'Load from CRT' or 'Add Row' to populate",
                foreground="#cc6600")
        else:
            self._profile_status_label.configure(
                text=f"{count} row(s) loaded — double-click a cell to edit",
                foreground="#0078d4")

    def _profile_import_excel(self):
        excel_path = self.context.get_var('checkout_excel_path').get().strip()
        initial_dir = (os.path.dirname(excel_path)
                       if excel_path and os.path.isdir(os.path.dirname(excel_path))
                       else "/")
        path = filedialog.askopenfilename(
            title="Import Profile Table from Excel",
            initialdir=initial_dir,
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if not path:
            return
        try:
            import pandas as pd
            engine = "openpyxl" if path.endswith(".xlsx") else "xlrd"
            df = pd.read_excel(path, engine=engine, dtype=str).fillna("")
            cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]

            # Build a normalised lookup for fuzzy column matching
            # Normalise: lowercase, strip, collapse whitespace/underscores
            def _norm(s: str) -> str:
                import re
                return re.sub(r'[\s_]+', '_', s.strip().lower())

            norm_map = {}                       # normalised_key -> grid_col
            for src, dst in self._CRT_TO_PROFILE_MAP.items():
                norm_map[_norm(src)] = dst
            for col in cols:                    # also map grid col names
                norm_map[_norm(col)] = col

            excel_to_grid = {}
            for excel_col in df.columns:
                # 1) Exact match in the explicit map
                grid_col = self._CRT_TO_PROFILE_MAP.get(excel_col)
                # 2) Normalised fallback (handles case / space / underscore variants)
                if not grid_col:
                    grid_col = norm_map.get(_norm(excel_col))
                if grid_col and grid_col in cols:
                    excel_to_grid[excel_col] = grid_col
            self._profile_data = []
            for _, row in df.iterrows():
                row_dict = {col: "" for col in cols}
                for excel_col, grid_col in excel_to_grid.items():
                    val = str(row.get(excel_col, "")).strip()
                    if val and val.lower() != "none":
                        row_dict[grid_col] = val
                for col in cols:
                    if not row_dict.get(col) and col in df.columns:
                        val = str(row.get(col, "")).strip()
                        if val and val.lower() != "none":
                            row_dict[col] = val
                self._profile_data.append(row_dict)
            self._refresh_profile_grid()
            self._update_profile_status()
            self.log(f"✓ Profile table imported: {len(self._profile_data)} row(s) "
                     f"from {os.path.basename(path)}")
        except Exception as e:
            self.show_error("Import Error", f"Cannot import file:\n{path}\n\n{e}")

    def _profile_export_excel(self):
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
            pd.DataFrame(self._profile_data).to_excel(
                path, index=False, engine="openpyxl")
            self.log(f"✓ Profile table exported to {os.path.basename(path)}")
        except Exception as e:
            self.show_error("Export Error", f"Cannot export file:\n{path}\n\n{e}")

    def _open_hardware_config(self):
        """Open the View/Edit Hardware Configuration dialog."""
        from view.dialogs.hardware_config_dialog import HardwareConfigDialog
        HardwareConfigDialog(self.winfo_toplevel())

    def _profile_load_from_crt(self):
        excel_path = self.context.get_var('checkout_excel_path').get().strip()
        cfgpn_flt  = self.context.get_var('checkout_cfgpn_filter').get().strip()
        self.context.controller.checkout_controller.load_crt_for_profile(
            excel_path=excel_path, cfgpn_filter=cfgpn_flt)

    # ──────────────────────────────────────────────────────────────────────
    # PROFILE GRID — INLINE EDITING
    # ──────────────────────────────────────────────────────────────────────

    def _on_profile_cell_double_click(self, event):
        region = self._profile_grid.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id  = self._profile_grid.identify_column(event.x)
        item_id = self._profile_grid.identify_row(event.y)
        if not item_id or not col_id:
            return
        col_idx  = int(col_id.replace("#", "")) - 1
        cols     = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        if col_idx < 0 or col_idx >= len(cols):
            return
        col_name = cols[col_idx]
        row_idx  = self._profile_grid.index(item_id)
        if row_idx < 0 or row_idx >= len(self._profile_data):
            return
        current_value = self._profile_data[row_idx].get(col_name, "")
        if col_name == "ATTR_OVERWRITE":
            self._show_attr_overwrite_dialog(row_idx)
            return
        self._start_inline_edit(item_id, col_id, col_idx, row_idx, col_name, current_value)

    def _start_inline_edit(self, item_id, col_id, col_idx, row_idx, col_name, current_value):
        bbox = self._profile_grid.bbox(item_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        edit_entry = ttk.Entry(self._profile_grid, width=w // 8)
        edit_entry.insert(0, current_value)
        edit_entry.select_range(0, tk.END)
        edit_entry.place(x=x, y=y, width=w, height=h)
        edit_entry.focus_set()

        def _save(event=None):
            new_value = edit_entry.get().strip()
            self._profile_data[row_idx][col_name] = new_value
            self._refresh_profile_grid()
            edit_entry.destroy()
            # Auto-lookup CFGPN/MCTO when Dummy_Lot is edited
            if col_name == "Dummy_Lot" and new_value and new_value.upper() not in ("", "NONE"):
                self._trigger_lot_lookup(row_idx, new_value)
            # Auto-verify MID link when MID is edited
            if col_name == "MID" and new_value and new_value.upper() not in ("", "NONE"):
                self._trigger_mid_verify(row_idx, new_value)

        def _cancel(event=None):
            edit_entry.destroy()

        edit_entry.bind("<Return>",   _save)
        edit_entry.bind("<Escape>",   _cancel)
        edit_entry.bind("<FocusOut>", _save)

    def _show_attr_overwrite_dialog(self, row_idx):
        current_value = self._profile_data[row_idx].get("ATTR_OVERWRITE", "")

        dialog = tk.Toplevel(self.winfo_toplevel())
        dialog.title("ATTR_OVERWRITE Editor")
        dialog.geometry("620x460")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        # Input fields
        input_frame = ttk.LabelFrame(dialog, text="Add Attribute Override", padding="8")
        input_frame.pack(fill=tk.X, padx=10, pady=(10, 4))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Section:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        section_var = tk.StringVar()
        ttk.Combobox(input_frame, textvariable=section_var,
                     values=["MAM", "MCTO", "CFGPN", "EQUIPMENT"],
                     width=18).grid(row=0, column=1, sticky="we", pady=2)

        ttk.Label(input_frame, text="Attr Name:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        attr_name_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=attr_name_var).grid(
            row=1, column=1, sticky="we", pady=2)

        ttk.Label(input_frame, text="Attr Value:").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        attr_value_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=attr_value_var).grid(
            row=2, column=1, sticky="we", pady=2)

        # Entries grid
        entries_frame = ttk.LabelFrame(dialog, text="Current Overrides", padding="8")
        entries_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        entry_cols = ["Section", "Attr Name", "Attr Value"]
        entries_tree = ttk.Treeview(entries_frame, columns=entry_cols,
                                    show="headings", height=7, selectmode="browse")
        for col in entry_cols:
            entries_tree.heading(col, text=col, anchor=tk.W)
            entries_tree.column(col, width=160, stretch=True, anchor=tk.W)
        entries_tree.pack(fill=tk.BOTH, expand=True)

        if current_value:
            parts = current_value.split(";")
            for i in range(0, len(parts), 3):
                if i + 2 < len(parts):
                    entries_tree.insert("", tk.END,
                                        values=(parts[i], parts[i+1], parts[i+2]))

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=(4, 10))

        def _add_entry():
            section = section_var.get().strip()
            attr = attr_name_var.get().strip()
            value = attr_value_var.get().strip()

            if not section:
                messagebox.showwarning("Validation", "Section name is required", parent=dialog)
                return
            if not attr:
                messagebox.showwarning("Validation", "Attribute name is required", parent=dialog)
                return

            # Check for duplicates
            for child in entries_tree.get_children():
                vals = entries_tree.item(child, "values")
                if vals[0] == section and vals[1] == attr and vals[2] == value:
                    return

            entries_tree.insert("", tk.END, values=(section, attr, value))
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
            entries = []
            for child in entries_tree.get_children():
                vals = entries_tree.item(child, "values")
                if len(vals) >= 3:
                    entries.append(f"{vals[0]};{vals[1]};{vals[2]}")

            raw_value = ";".join(entries)

            # Validate format
            from model.checkout_params import validate_attr_overwrite_string
            is_valid, error_msg = validate_attr_overwrite_string(raw_value)
            if not is_valid:
                messagebox.showwarning("Validation Error", error_msg, parent=dialog)
                return

            self._profile_data[row_idx]["ATTR_OVERWRITE"] = raw_value
            self._refresh_profile_grid()
            dialog.destroy()

        ttk.Button(btn_frame, text="Add",        command=_add_entry).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Remove",     command=_remove_entry).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Remove All", command=_remove_all).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Save",       command=_save).pack(side=tk.RIGHT, padx=(3, 0))
        ttk.Button(btn_frame, text="Cancel",     command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 3))

    def _on_profile_row_select(self, event):
        sel = self._profile_grid.selection()
        if not sel:
            return
        values = self._profile_grid.item(sel[0], "values")
        cols   = [c for c, _ in self._PROFILE_GEN_COLUMNS]

        def _get(col_name):
            idx = cols.index(col_name) if col_name in cols else -1
            return values[idx] if 0 <= idx < len(values) else ""

        cfgpn     = _get("CFGPN")
        dummy_lot = _get("Dummy_Lot")
        mid       = _get("MID")
        step      = _get("Step")
        tester    = _get("Tester")

        self._profile_status_label.configure(
            text=(f"Selected: CFGPN={cfgpn}  Lot={dummy_lot}  "
                  f"MID={mid}  Step={step}  Tester={tester}"),
            foreground="#0078d4")

    # ──────────────────────────────────────────────────────────────────────
    # TESTER REFRESH
    # ──────────────────────────────────────────────────────────────────────

    def _refresh_testers(self):
        for widget in self._tester_frame.winfo_children():
            widget.destroy()

        self._tester_vars  = {}
        self._badge_labels = {}
        self._phase_labels = {}

        testers = self.context.controller.checkout_controller.get_available_testers()
        if not testers:
            ttk.Label(self._tester_frame,
                      text="No testers registered.",
                      foreground="orange").grid(
                row=0, column=0, columnspan=5, sticky=tk.W, pady=4)
            return

        for i, (hostname, env) in enumerate(testers):
            var = tk.BooleanVar(value=True)
            self._tester_vars[hostname] = var
            # Auto-fill Tester column when checkbox is toggled
            var.trace_add("write", lambda *_a: self._sync_tester_column())

            ttk.Checkbutton(self._tester_frame,
                             text=f"{hostname}  ({env})",
                             variable=var).grid(
                row=i, column=0, sticky=tk.W, padx=(0, 16), pady=2)

            badge = ttk.Label(self._tester_frame, text="IDLE", width=14,
                               anchor=tk.CENTER, relief=tk.FLAT,
                               foreground="white", background="#888888")
            badge.grid(row=i, column=1, padx=(0, 8), pady=2)
            self._badge_labels[hostname] = badge

            phase_lbl = ttk.Label(self._tester_frame, text="",
                                   foreground="#555555", font=("Segoe UI", 8))
            phase_lbl.grid(row=i, column=2, sticky=tk.W, pady=2)
            self._phase_labels[hostname] = phase_lbl

        # Sync tester column with the initial checkbox state
        self._sync_tester_column()

    def _sync_tester_column(self):
        """Update the 'Tester' column in every profile row with selected hostnames."""
        selected = [h for h, var in self._tester_vars.items() if var.get()]
        tester_value = ", ".join(selected)

        if not self._profile_data:
            return

        changed = False
        for row in self._profile_data:
            if row.get("Tester") != tester_value:
                row["Tester"] = tester_value
                changed = True

        if changed:
            self._refresh_profile_grid()

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_tgz(self):
        _DEFAULT_TGZ_DIR = r"P:\temp\BENTO\RELEASE_TGZ"
        initial_dir = _DEFAULT_TGZ_DIR if os.path.isdir(_DEFAULT_TGZ_DIR) else "/"
        path = filedialog.askopenfilename(
            title="Select compiled TGZ",
            initialdir=initial_dir,
            filetypes=[("TGZ archives", "*.tgz"), ("All files", "*.*")])
        if path:
            self.context.get_var('checkout_tgz_path').set(path)
            # Auto-scan recipes when TGZ is selected
            self._scan_tgz_recipes()

    def _scan_tgz_recipes(self):
        """Scan the selected TGZ archive for available recipe files."""
        tgz_path = self.context.get_var('checkout_tgz_path').get().strip()
        if not tgz_path:
            messagebox.showwarning("No TGZ Selected",
                                   "Please select a TGZ archive first.")
            return

        if not os.path.isfile(tgz_path):
            messagebox.showwarning("TGZ Not Found",
                                   f"TGZ file not found:\n{tgz_path}")
            return

        try:
            from model.recipe_selector import scan_tgz_recipes
            recipes = scan_tgz_recipes(tgz_path)
            if recipes:
                # Format as RECIPE\filename for the combobox
                recipe_values = [f"RECIPE\\{r}" for r in recipes]
                self._recipe_combo['values'] = recipe_values
                # If current value is empty, don't auto-select — leave for auto-detection
                current = self.context.get_var('checkout_recipe_override').get().strip()
                if not current:
                    # Show count but don't auto-select
                    messagebox.showinfo(
                        "Recipes Found",
                        f"Found {len(recipes)} recipe(s) in TGZ.\n"
                        f"Select one from the dropdown to override auto-detection,\n"
                        f"or leave empty for automatic recipe selection.")
            else:
                self._recipe_combo['values'] = []
                messagebox.showwarning(
                    "No Recipes Found",
                    f"No recipe XML files found in:\n{tgz_path}\n\n"
                    f"The TGZ may not contain a recipe/ folder.")
        except Exception as e:
            messagebox.showerror("Scan Error",
                                 f"Failed to scan TGZ:\n{e}")

    def _browse_excel(self):
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
        self.stop_btn.state(["!disabled"])
        self.context.controller.checkout_controller.start_checkout(params)

    def _stop_checkout(self):
        self.context.controller.checkout_controller.stop_checkout()
        self.stop_btn.state(["disabled"])

    def _collect_params(self):
        from model.checkout_params import (
            CheckoutParams, TestCaseConfig,
            parse_attr_overwrite_string, ProfileRowParams,
        )
        from pydantic import ValidationError

        tgz_path    = self.context.get_var('checkout_tgz_path').get().strip()
        hot_folder  = self.context.get_var('checkout_hot_folder').get().strip()
        method      = self.context.get_var('checkout_detect_method').get()
        timeout_m   = self.context.get_var('checkout_timeout_min').get()
        notify      = self.context.get_var('checkout_notify_teams').get()
        site        = self.context.get_var('checkout_site').get().strip()
        form_factor = self.context.get_var('checkout_form_factor').get().strip()
        gen_tmptravl = self.context.get_var('checkout_gen_tmptravl').get()
        autostart   = self.context.get_var('checkout_autostart').get()
        hostnames   = [h for h, v in self._tester_vars.items() if v.get()]
        webhook_url = self.context.get_var('checkout_webhook_url').get().strip()

        tc_passing    = self.context.get_var('checkout_tc_passing').get()
        tc_fail       = self.context.get_var('checkout_tc_force_fail').get()
        passing_label = self.context.get_var('checkout_tc_passing_label').get().strip()
        fail_label    = self.context.get_var('checkout_tc_fail_label').get().strip()
        fail_desc     = self.context.get_var('checkout_tc_fail_desc').get().strip()

        profile_table_raw = self._profile_data.copy() if self._profile_data else []

        # Build test cases list
        test_cases = []
        if tc_passing:
            test_cases.append(TestCaseConfig(type="passing", label=passing_label or "passing"))
        if tc_fail:
            test_cases.append(TestCaseConfig(
                type="force_fail",
                label=fail_label or "force_fail_1",
                description=fail_desc,
            ))

        # Build profile rows with validated attr_overwrites
        profile_rows = []
        for row in profile_table_raw:
            try:
                overwrites = parse_attr_overwrite_string(row.get("ATTR_OVERWRITE", ""))
            except ValueError as e:
                self.show_error("ATTR_OVERWRITE Error", str(e))
                return None
            profile_rows.append(ProfileRowParams(
                mid=row.get("MID", ""),
                lot=row.get("Dummy_Lot", row.get("LOT", "")),
                cfgpn=row.get("CFGPN", ""),
                mcto=row.get("MCTO_#1", row.get("MCTO", "")),
                step=row.get("Step", row.get("STEP", "ABIT")),
                form_factor=row.get("Form_Factor", row.get("FORM_FACTOR", "")) or form_factor,
                tester=row.get("Tester", row.get("TESTER", "")),
                primitive=row.get("Primitive", row.get("PRIMITIVE", "")),
                dut=row.get("Dut", row.get("DUT", "")),
                attr_overwrites=overwrites,
            ))

        # ── Read JIRA key from shared issue_var ──────────────────────
        jira_key = self.context.get_var('issue_var').get().strip().upper()
        if not jira_key or jira_key.endswith("-"):
            # Fallback: try to extract JIRA key from TGZ filename
            # e.g. TSESSD-14270_IBIR-0383_ABIT_passing.tgz
            import re
            tgz_base = os.path.basename(tgz_path)
            m = re.search(r'([A-Za-z]+-\d+)', tgz_base)
            jira_key = m.group(1) if m else ""

        # ── Read recipe override from GUI combobox ────────────────────
        recipe_override = self.context.get_var('checkout_recipe_override').get().strip()

        try:
            params = CheckoutParams(
                jira_key=jira_key if jira_key else "TSESSD-XXXX",
                tgz_path=tgz_path,
                hot_folder=hot_folder or r"C:\test_program\playground_queue",
                hostnames=hostnames,
                site=site or "SINGAPORE",
                test_cases=test_cases,
                profile_table=profile_rows,
                detect_method=method,
                timeout_minutes=int(timeout_m) if timeout_m else 30,
                notify_teams=bool(notify),
                webhook_url=webhook_url,
                generate_tmptravl=bool(gen_tmptravl),
                autostart="True" if autostart else "False",
                recipe_override=recipe_override,
            )
        except ValidationError as e:
            # Show user-friendly validation errors
            errors = []
            for err in e.errors():
                field = " → ".join(str(loc) for loc in err["loc"])
                errors.append(f"• {field}: {err['msg']}")
            self.show_error("Validation Error", "\n".join(errors))
            return None

        return params.to_legacy_dict()

    # ──────────────────────────────────────────────────────────────────────
    # BADGE HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _set_badge(self, hostname, state):
        badge = self._badge_labels.get(hostname)
        if not badge:
            return
        fg, bg = self._BADGE_COLOURS.get(state.upper(), ("#888888", "white"))
        badge.configure(text=state.upper(), foreground=bg, background=fg)

    def _set_phase(self, hostname, phase):
        lbl = self._phase_labels.get(hostname)
        if lbl:
            lbl.configure(text=phase)

    def _clear_results(self):
        self.results_text.configure(state=tk.NORMAL)
        self.results_text.delete("1.0", tk.END)
        self.results_text.configure(state=tk.DISABLED)

    def _open_queue_folder(self):
        queue_path = r"P:\temp\BENTO\CHECKOUT_QUEUE"
        if os.path.isdir(queue_path):
            os.startfile(queue_path)
        else:
            self.show_error("Folder Not Found",
                            f"Queue folder not found:\n{queue_path}\n\n"
                            "Ensure P: drive is mapped.")

    def _append_result(self, text: str):
        self.results_text.configure(state=tk.NORMAL)
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)
        self.results_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_profile_data_loaded(self, profile_rows: List[Dict]):
        self._profile_data = profile_rows
        self._refresh_profile_grid()
        self._sync_tester_column()
        self._update_profile_status()
        self.log(f"✓ Profile table loaded: {len(profile_rows)} row(s) from CRT data")

    def on_crt_grid_loaded(self, crt_data):
        rows = crt_data.get("rows", [])
        self._profile_data = []
        for row_dict in rows:
            material_desc = str(row_dict.get("Material description", "")).strip()
            cfgpn = str(row_dict.get("CFGPN", "")).strip()
            self._profile_data.append({
                "Form_Factor":    str(row_dict.get("Form_Factor", "")).strip(),
                "Material_Desc":  material_desc,
                "CFGPN":          cfgpn,
                "MCTO_#1":        str(row_dict.get("MCTO_#1", "")).strip(),
                "Dummy_Lot":      str(row_dict.get("Dummy_Lot",
                                                   material_desc or "None")).strip(),
                "Step": "", "MID": "", "Tester": "",
                "Primitive": "", "Dut": "", "ATTR_OVERWRITE": "",
            })
        if not self._profile_data:
            self._profile_add_default_row()
        self._refresh_profile_grid()
        self._sync_tester_column()
        self._update_profile_status()
        self.log(f"✓ Profile table loaded from CRT: {crt_data.get('count', 0)} row(s)")

    def on_autofill_completed(self, mid, cfgpn, fw_ver):
        self.log(f"✓ Auto-filled: MID={mid}  CFGPN={cfgpn}  FW={fw_ver}")

    # ── LOT → CFGPN/MCTO AUTO-FILL ──────────────────────────────────────

    def _trigger_lot_lookup(self, row_idx: int, lot: str):
        """Trigger background MAM SOAP lookup for a dummy lot.

        Called when the user edits the Dummy_Lot column. Delegates to
        the checkout controller which runs the query in a background
        thread and calls ``on_lot_lookup_completed()`` on success.
        """
        controller = self.context.controller
        if controller and hasattr(controller, 'checkout_controller'):
            site = self.context.get_var('checkout_site').get().strip()
            controller.checkout_controller.lookup_lot_cfgpn_mcto(
                lot, row_idx, site=site)
        else:
            self.log(f"[WARN] Cannot lookup lot — controller not available")

    def on_lot_lookup_completed(self, row_idx: int, cfgpn: str, mcto: str,
                                form_factor: str = "", material_desc: str = ""):
        """Callback from controller: auto-fill CFGPN, MCTO_#1, Form_Factor, Material_Desc.

        Called on the main thread via ``root.after()``.

        Parameters
        ----------
        row_idx : int
            Row index in the profile table to update.
        cfgpn : str
            BASE CFGPN value from MAM lot lookup.
        mcto : str
            MODULE FGPN (MCTO#1) value from MAM lot lookup.
        form_factor : str, optional
            MODULE FORM FACTOR from MAM or SAP fallback.
        material_desc : str, optional
            MATERIAL DESCRIPTION from MAM or SAP fallback.
        """
        if row_idx < 0 or row_idx >= len(self._profile_data):
            self.log(f"[WARN] Lot lookup returned for invalid row {row_idx}")
            return
        updated = []
        if cfgpn:
            self._profile_data[row_idx]["CFGPN"] = cfgpn
            updated.append(f"CFGPN={cfgpn}")
        if mcto:
            self._profile_data[row_idx]["MCTO_#1"] = mcto
            updated.append(f"MCTO_#1={mcto}")
        if form_factor:
            self._profile_data[row_idx]["Form_Factor"] = form_factor
            updated.append(f"Form_Factor={form_factor}")
        if material_desc:
            self._profile_data[row_idx]["Material_Desc"] = material_desc
            updated.append(f"Material_Desc={material_desc}")
        if updated:
            self._refresh_profile_grid()
            lot = self._profile_data[row_idx].get("Dummy_Lot", "?")
            self.log(f"✓ Auto-filled from lot '{lot}': {', '.join(updated)}")
        else:
            self.log(f"[WARN] Lot lookup returned empty CFGPN/MCTO/Form_Factor/Material_Desc")

    def _trigger_mid_verify(self, row_idx: int, mid: str):
        """Trigger background MAM SOAP verification for a MID.

        Called when the user edits the MID column. Reads the current
        CFGPN and MCTO from the same row and verifies the MID is
        correctly linked to them in MAM.
        """
        controller = self.context.controller
        if controller and hasattr(controller, 'checkout_controller'):
            row = self._profile_data[row_idx] if row_idx < len(self._profile_data) else {}
            expected_cfgpn = row.get("CFGPN", "")
            expected_mcto = row.get("MCTO_#1", "")
            lot_hint = row.get("Dummy_Lot", "")
            controller.checkout_controller.verify_mid_link(
                mid, row_idx,
                expected_cfgpn=expected_cfgpn,
                expected_mcto=expected_mcto,
                lot_hint=lot_hint,
            )
        else:
            self.log(f"[WARN] Cannot verify MID -- controller not available")

    def on_mid_verify_completed(self, row_idx: int, result: dict):
        """Callback from controller: display MID verification result.

        Called on the main thread via ``root.after()``.

        Parameters
        ----------
        row_idx : int
            Row index in the profile table.
        result : dict
            Verification result from ``verify_mid_lot_link()``.
        """
        msg = result.get("message", "")
        valid = result.get("valid", "false")
        lot_cfgpn = result.get("lot_cfgpn", "")
        lot_mcto = result.get("lot_mcto", "")

        if valid == "true":
            self.log(f"✓ {msg}")
        else:
            error = result.get("error", "")
            if error:
                self.log(f"[WARN] {msg}")
            else:
                # Mismatch -- show details
                self.log(f"⚠ {msg}")
                # Optionally highlight the row or show a warning dialog
                cfgpn_match = result.get("cfgpn_match", "false")
                mcto_match = result.get("mcto_match", "false")
                if cfgpn_match != "true" and lot_cfgpn:
                    self.log(
                        f"   Lot has CFGPN={lot_cfgpn}, "
                        f"row expects CFGPN={self._profile_data[row_idx].get('CFGPN', '?')}"
                    )
                if mcto_match != "true" and lot_mcto:
                    self.log(
                        f"   Lot has MCTO={lot_mcto}, "
                        f"row expects MCTO={self._profile_data[row_idx].get('MCTO_#1', '?')}"
                    )

    def on_xml_imported(self, data: dict):
        # ── Auto-fill TGZ path ────────────────────────────────────────
        if data.get("tgz_path"):
            self.context.get_var('checkout_tgz_path').set(data["tgz_path"])

        # ── Populate profile grid from material_rows ──────────────────
        material_rows = data.get("material_rows", [])
        env = data.get("env", "")
        attr_overwrite = data.get("attr_overwrite", "")
        if material_rows:
            self._profile_data = []
            for mrow in material_rows:
                self._profile_data.append({
                    "Form_Factor":    "",
                    "Material_Desc":  "",
                    "CFGPN":          "",
                    "MCTO_#1":        "",
                    "Dummy_Lot":      mrow.get("lot", ""),
                    "Step":           env,
                    "MID":            mrow.get("mid", ""),
                    "Tester":         "",
                    "Primitive":      mrow.get("primitive", ""),
                    "Dut":            mrow.get("dut", ""),
                    "ATTR_OVERWRITE": attr_overwrite,
                })
            self._refresh_profile_grid()
            self._sync_tester_column()
            self._update_profile_status()

        # ── Log summary ───────────────────────────────────────────────
        filled = []
        if data.get("tgz_path"):   filled.append("TGZ")
        if data.get("env"):        filled.append(f"ENV={data['env']}")
        if material_rows:          filled.append(f"{len(material_rows)} MID(s)")
        elif data.get("mid"):      filled.append(f"MID={data['mid']}")
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
        status      = result.get("status", "failed").upper()
        elapsed     = result.get("elapsed", 0)
        detail      = result.get("detail", "")
        test_cases  = result.get("test_cases", [])
        mid_results = result.get("mid_results", {})

        self._set_badge(hostname, status)
        self._set_phase(hostname, "")
        self._append_result(f"[{hostname}] {status}  ({elapsed}s)  {detail}")

        # Per-MID results (mirrors CAT's profile_gen_completed)
        if mid_results:
            for mid, mid_info in mid_results.items():
                mid_status = mid_info.get("status", "unknown")
                mid_detail = mid_info.get("detail", "")
                icon = "\u2713" if mid_status == "success" else "\u2717"
                line = f"   {icon} MID {mid}: {mid_status}"
                if mid_detail:
                    line += f" \u2014 {mid_detail}"
                self._append_result(line)

        # Test case results (existing)
        for tc in test_cases:
            icon = "\u2713" if tc.get("status") == "success" else "\u2717"
            self._append_result(
                f"   {icon} {tc.get('label','?')} ({tc.get('type','?')}): "
                f"{tc.get('status','?')} in {tc.get('elapsed',0)}s")

        running = [lbl for lbl in self._badge_labels.values()
                   if lbl.cget("text") in ("PENDING", "RUNNING", "COLLECTING")]
        if not running:
            self.stop_btn.state(["disabled"])
            self.unlock_gui()

    def on_xml_generation_completed(self, hostname, result):
        """Handle XML-only generation completion (distinct from checkout)."""
        status      = result.get("status", "xml_fail").upper()
        detail      = result.get("detail", "")
        mid_results = result.get("mid_results", {})

        self._set_badge(hostname, status)
        self._set_phase(hostname, "")
        self._append_result(f"[{hostname}] XML Generated — {status}  {detail}")

        # Per-MID results
        if mid_results:
            for mid, mid_info in mid_results.items():
                mid_status = mid_info.get("status", "unknown")
                mid_detail = mid_info.get("detail", "")
                icon = "\u2713" if mid_status == "success" else "\u2717"
                line = f"   {icon} MID {mid}: {mid_status}"
                if mid_detail:
                    line += f" \u2014 {mid_detail}"
                self._append_result(line)

        # Unlock GUI if no more pending/running badges
        running = [lbl for lbl in self._badge_labels.values()
                   if lbl.cget("text") in ("PENDING", "RUNNING", "COLLECTING")]
        if not running:
            self.stop_btn.state(["disabled"])
            self.unlock_gui()

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Result Collection & Test Progress (Task 2)
    # ──────────────────────────────────────────────────────────────────────

    def _build_result_collection_section(self, parent, row):
        """Build the result collection / test progress monitoring section."""
        frm = ttk.LabelFrame(parent, text="  Result Collection & Test Progress  ",
                             padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(14, 0))
        frm.columnconfigure(0, weight=1)

        # ── Config row 1: MIDs file + Target folder ──────────────────────
        cfg1 = ttk.Frame(frm)
        cfg1.grid(row=0, column=0, sticky="we", pady=(0, 4))
        cfg1.columnconfigure(1, weight=1)
        cfg1.columnconfigure(4, weight=1)

        ttk.Label(cfg1, text="MIDs File:").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self._rc_mids_file_var = tk.StringVar()
        ttk.Entry(cfg1, textvariable=self._rc_mids_file_var, width=35).grid(
            row=0, column=1, sticky="we", padx=(0, 4))
        ttk.Button(cfg1, text="…", width=3,
                   command=self._rc_browse_mids).grid(row=0, column=2, padx=(0, 12))

        ttk.Label(cfg1, text="Target Folder:").grid(row=0, column=3, sticky=tk.W, padx=(0, 4))
        self._rc_target_var = tk.StringVar()
        ttk.Entry(cfg1, textvariable=self._rc_target_var, width=35).grid(
            row=0, column=4, sticky="we", padx=(0, 4))
        ttk.Button(cfg1, text="…", width=3,
                   command=self._rc_browse_target).grid(row=0, column=5)

        # ── Config row 2: Machine type, poll interval, options ───────────
        cfg2 = ttk.Frame(frm)
        cfg2.grid(row=1, column=0, sticky="we", pady=(0, 4))

        ttk.Label(cfg2, text="Tester:").pack(side=tk.LEFT, padx=(0, 4))
        self._rc_tester_var = tk.StringVar(value="")
        self._rc_tester_combo = ttk.Combobox(
            cfg2, textvariable=self._rc_tester_var, width=18,
            values=self._rc_get_tester_hostnames(),
            state="readonly")
        self._rc_tester_combo.pack(side=tk.LEFT, padx=(0, 12))
        _tip(self._rc_tester_combo,
             "Select the remote tester to monitor.\n"
             "Paths will be converted to UNC (\\\\hostname\\C$\\...)\n"
             "to access the tester filesystem over the network.")

        ttk.Label(cfg2, text="Machine:").pack(side=tk.LEFT, padx=(0, 4))
        self._rc_machine_var = tk.StringVar(value="Auto-Detect")
        ttk.Combobox(cfg2, textvariable=self._rc_machine_var, width=12,
                     values=["Auto-Detect", "IBIR", "MPT"],
                     state="readonly").pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(cfg2, text="Poll (s):").pack(side=tk.LEFT, padx=(0, 4))
        self._rc_poll_var = tk.IntVar(value=30)
        ttk.Spinbox(cfg2, textvariable=self._rc_poll_var,
                    from_=10, to=300, width=5).pack(side=tk.LEFT, padx=(0, 12))

        self._rc_auto_collect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg2, text="Auto-Collect",
                        variable=self._rc_auto_collect_var).pack(side=tk.LEFT, padx=(0, 8))

        self._rc_auto_spool_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg2, text="Auto-Spool",
                        variable=self._rc_auto_spool_var).pack(side=tk.LEFT, padx=(0, 8))

        self._rc_notify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg2, text="Teams Notify",
                        variable=self._rc_notify_var).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(cfg2, text="Extra Patterns:").pack(side=tk.LEFT, padx=(8, 4))
        self._rc_extra_var = tk.StringVar()
        ttk.Entry(cfg2, textvariable=self._rc_extra_var, width=18).pack(side=tk.LEFT)

        # ── Action buttons ───────────────────────────────────────────────
        btn_row = ttk.Frame(frm)
        btn_row.grid(row=2, column=0, sticky="w", pady=(0, 4))

        self._rc_start_btn = ttk.Button(
            btn_row, text="▶ Start Monitoring",
            command=self._rc_start_monitoring)
        self._rc_start_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tip(self._rc_start_btn,
             "Start auto-detect polling for test completion.\n"
             "Collects tracefile.txt + resultsManager.db when done.")

        self._rc_stop_btn = ttk.Button(
            btn_row, text="⏹ Stop",
            command=self._rc_stop_monitoring, state=tk.DISABLED)
        self._rc_stop_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._rc_refresh_btn = ttk.Button(
            btn_row, text="🔄 Refresh",
            command=self._rc_refresh, state=tk.DISABLED)
        self._rc_refresh_btn.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Separator(btn_row, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(4, 8), pady=2)

        self._rc_collect_btn = ttk.Button(
            btn_row, text="📁 Collect Selected",
            command=self._rc_collect_selected, state=tk.DISABLED)
        self._rc_collect_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._rc_spool_btn = ttk.Button(
            btn_row, text="📊 Spool Selected",
            command=self._rc_spool_selected, state=tk.DISABLED)
        self._rc_spool_btn.pack(side=tk.LEFT, padx=(0, 4))

        # ── Summary bar ─────────────────────────────────────────────────
        summary_row = ttk.Frame(frm)
        summary_row.grid(row=3, column=0, sticky="we", pady=(0, 4))

        self._rc_summary_label = ttk.Label(
            summary_row, text="No monitoring active",
            font=("Segoe UI", 8))
        self._rc_summary_label.pack(side=tk.LEFT)

        self._rc_status_indicator = ttk.Label(
            summary_row, text="", font=("Segoe UI", 9, "bold"))
        self._rc_status_indicator.pack(side=tk.RIGHT, padx=10)

        # ── Treeview — Per-DUT Status ────────────────────────────────────
        tree_container = ttk.Frame(frm)
        tree_container.grid(row=4, column=0, sticky="nsew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)

        rc_cols = ("mid", "location", "name", "status", "fail_info", "collected", "error")
        self._rc_tree = ttk.Treeview(
            tree_container, columns=rc_cols, show="headings",
            selectmode="extended", height=6)

        self._rc_tree.heading("mid",       text="MID")
        self._rc_tree.heading("location",  text="Location")
        self._rc_tree.heading("name",      text="File Name")
        self._rc_tree.heading("status",    text="Status")
        self._rc_tree.heading("fail_info", text="Fail Info")
        self._rc_tree.heading("collected", text="Collected")
        self._rc_tree.heading("error",     text="Error")

        self._rc_tree.column("mid",       width=100, minwidth=70)
        self._rc_tree.column("location",  width=65,  minwidth=45)
        self._rc_tree.column("name",      width=110, minwidth=70)
        self._rc_tree.column("status",    width=75,  minwidth=55)
        self._rc_tree.column("fail_info", width=180, minwidth=90)
        self._rc_tree.column("collected", width=70,  minwidth=50)
        self._rc_tree.column("error",     width=180, minwidth=90)

        rc_vsb = ttk.Scrollbar(tree_container, orient=tk.VERTICAL,
                               command=self._rc_tree.yview)
        rc_hsb = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL,
                               command=self._rc_tree.xview)
        self._rc_tree.configure(yscrollcommand=rc_vsb.set,
                                xscrollcommand=rc_hsb.set)

        self._rc_tree.grid(row=0, column=0, sticky="nsew")
        rc_vsb.grid(row=0, column=1, sticky="ns")
        rc_hsb.grid(row=1, column=0, sticky="we")

        # Tag styles for status coloring
        self._rc_tree.tag_configure("pass",    foreground="green")
        self._rc_tree.tag_configure("fail",    foreground="red")
        self._rc_tree.tag_configure("running", foreground="orange")
        self._rc_tree.tag_configure("unknown", foreground="gray")
        self._rc_tree.tag_configure("error",   foreground="red")

    # ── Result Collection — User Actions ─────────────────────────────────

    def _rc_get_tester_hostnames(self) -> list:
        """Load tester hostnames from bento_testers.json for the Tester dropdown."""
        try:
            import json
            registry_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "bento_testers.json"
            )
            with open(registry_path, "r") as f:
                data = json.load(f)
            hostnames = []
            for val in data.values():
                if isinstance(val, list) and len(val) >= 1:
                    hostnames.append(val[0])  # hostname is first element
                elif isinstance(val, dict):
                    h = val.get("hostname", "")
                    if h:
                        hostnames.append(h)
            return sorted(set(hostnames)) if hostnames else []
        except Exception:
            return []

    def _rc_browse_mids(self):
        path = filedialog.askopenfilename(
            title="Select MIDs.txt File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self._rc_mids_file_var.set(path)

    def _rc_browse_target(self):
        path = filedialog.askdirectory(title="Select Target Shared Folder")
        if path:
            self._rc_target_var.set(path)

    def _rc_start_monitoring(self):
        """Start result collection monitoring via ResultController."""
        mids_file = self._rc_mids_file_var.get().strip()
        if not mids_file:
            self.show_error("Error", "Please select a MIDs.txt file.")
            return
        if not os.path.exists(mids_file):
            self.show_error("Error", f"MIDs file not found:\n{mids_file}")
            return

        target_path = self._rc_target_var.get().strip()
        site        = self.context.get_var('checkout_site').get().strip()
        machine     = self._rc_machine_var.get().strip()
        if machine == "Auto-Detect":
            machine = ""

        poll_interval = self._rc_poll_var.get()
        auto_collect  = self._rc_auto_collect_var.get()
        auto_spool    = self._rc_auto_spool_var.get()
        notify_teams  = self._rc_notify_var.get()

        extra = self._rc_extra_var.get().strip()
        additional_patterns = [p.strip() for p in extra.split(",") if p.strip()] if extra else []

        webhook_url = self.context.get_var('checkout_webhook_url').get().strip()

        controller = self.context.controller
        if not controller or not hasattr(controller, 'result_controller'):
            self.show_error("Error", "Result controller not available.")
            return

        # Update UI state
        self._rc_start_btn.config(state=tk.DISABLED)
        self._rc_stop_btn.config(state=tk.NORMAL)
        self._rc_refresh_btn.config(state=tk.NORMAL)
        self._rc_collect_btn.config(state=tk.NORMAL)
        self._rc_spool_btn.config(state=tk.NORMAL)
        self._rc_status_indicator.config(text="🔄 MONITORING", foreground="blue")

        tester_hostname = self._rc_tester_var.get().strip()

        controller.result_controller.start_monitoring(
            mids_file           = mids_file,
            target_path         = target_path,
            site                = site,
            machine_type        = machine,
            tester_hostname     = tester_hostname,
            poll_interval       = poll_interval,
            auto_collect        = auto_collect,
            auto_spool          = auto_spool,
            additional_patterns = additional_patterns,
            webhook_url         = webhook_url,
            notify_teams        = notify_teams,
        )

    def _rc_stop_monitoring(self):
        controller = self.context.controller
        if controller and hasattr(controller, 'result_controller'):
            controller.result_controller.stop_monitoring()

    def _rc_refresh(self):
        controller = self.context.controller
        if controller and hasattr(controller, 'result_controller'):
            controller.result_controller.refresh_status()

    def _rc_collect_selected(self):
        selected = self._rc_tree.selection()
        if not selected:
            self.show_info("Info", "Please select one or more MIDs to collect.")
            return
        controller = self.context.controller
        if not controller or not hasattr(controller, 'result_controller'):
            return
        for item_id in selected:
            values = self._rc_tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                controller.result_controller.collect_single(mid)

    def _rc_spool_selected(self):
        selected = self._rc_tree.selection()
        if not selected:
            self.show_info("Info", "Please select one or more MIDs to spool.")
            return
        controller = self.context.controller
        if not controller or not hasattr(controller, 'result_controller'):
            return
        for item_id in selected:
            values = self._rc_tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                controller.result_controller.spool_single(mid)

    # ── Result Collection — Controller → View Callbacks ──────────────────

    def on_rc_progress_update(self, summary: dict, entries: dict):
        """Called by ResultController when progress updates."""
        total      = summary.get("total", 0)
        passed     = summary.get("passed", 0)
        failed     = summary.get("failed", 0)
        running    = summary.get("running", 0)
        collected  = summary.get("collected", 0)
        unresolved = summary.get("unresolved", 0)
        machine    = summary.get("machine", "")
        site       = summary.get("site", "")

        unresolved_str = f"  |  ⚠ Unresolved: {unresolved}" if unresolved else ""
        self._rc_summary_label.config(
            text=(
                f"Machine: {machine}  |  Site: {site}  |  "
                f"Total: {total}  |  "
                f"✅ Pass: {passed}  |  ❌ Fail: {failed}  |  "
                f"🔄 Running: {running}  |  📁 Collected: {collected}"
                f"{unresolved_str}"
            )
        )

        # Update treeview
        self._rc_tree.delete(*self._rc_tree.get_children())
        for mid, entry_data in entries.items():
            status    = entry_data.get("status", "UNKNOWN")
            fail_info = ""
            if status == _STATUS_FAIL:
                fail_info = f"{entry_data.get('fail_reg', '')} - {entry_data.get('fail_code', '')}"
            collected_str = "✓" if entry_data.get("collected", False) else ""
            error_str     = entry_data.get("error", "")
            tag = status.lower() if status.lower() in ("pass", "fail", "running") else "unknown"
            if error_str:
                tag = "error"
            self._rc_tree.insert("", tk.END, values=(
                entry_data.get("mid", mid),
                entry_data.get("location", ""),
                entry_data.get("name", ""),
                status,
                fail_info,
                collected_str,
                error_str,
            ), tags=(tag,))

    def on_rc_collection_complete(self, summary: dict):
        """Called by ResultController when monitoring ends."""
        self._rc_start_btn.config(state=tk.NORMAL)
        self._rc_stop_btn.config(state=tk.DISABLED)

        all_done = summary.get("all_done", False)
        failed   = summary.get("failed", 0)

        if all_done and failed == 0:
            self._rc_status_indicator.config(text="✅ ALL PASSED", foreground="green")
        elif all_done and failed > 0:
            self._rc_status_indicator.config(
                text="⚠ COMPLETED (with failures)", foreground="orange")
        else:
            self._rc_status_indicator.config(text="⏹ STOPPED", foreground="gray")

    def get_profile_table_data(self) -> List[Dict]:
        return self._profile_data.copy()
