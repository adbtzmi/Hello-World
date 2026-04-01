# -*- coding: utf-8 -*-
import os
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import Any, List, Dict
from view.tabs.base_tab import BaseTab


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

        _sv('checkout_site', 'SINGAPORE')
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
        _bv('checkout_gen_tmptravl', False)

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

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 1 — Profile Generation
    # ──────────────────────────────────────────────────────────────────────

    def _build_profile_section(self, parent, row):
        frm = ttk.LabelFrame(parent, text="  Profile Generation  ", padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 6))
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
        crt_btn.pack(side=tk.LEFT, padx=(0, 0))
        _tip(crt_btn, "Read the CRT Excel file and auto-populate Form_Factor, Material_Desc,\n"
             "CFGPN, MCTO_#1, Dummy_Lot. Editable columns (Step, MID, Tester…) remain blank.")

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
        frm.grid(row=row, column=0, sticky="we", pady=(0, 6))
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

        # Separator
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=2, column=0, columnspan=3, sticky="we", pady=(0, 4))

        # Test Cases row
        tc_frame = ttk.Frame(frm)
        tc_frame.grid(row=3, column=0, columnspan=3, sticky="we", pady=(0, 4))

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
            row=4, column=0, columnspan=3, sticky="we", pady=(0, 4))

        # Hot Folder row
        ttk.Label(frm, text="Hot Folder:").grid(
            row=5, column=0, sticky=tk.W, padx=(0, 8))
        hot_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_hot_folder'))
        hot_entry.grid(row=5, column=1, columnspan=2, sticky="we")
        _tip(hot_entry, "Path to the SLATE hot folder where XML profiles are dropped.\n"
             "Default: C:\\test_program\\playground_queue")

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 3 — SLATE Detection + Tester Selection (side-by-side)
    # ──────────────────────────────────────────────────────────────────────

    def _build_detection_tester_section(self, parent, row):
        side_frame = ttk.Frame(parent)
        side_frame.grid(row=row, column=0, sticky="we", pady=(0, 6))
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

        teams_cb = ttk.Checkbutton(detect_frame, text="Teams",
                        variable=self.context.get_var('checkout_notify_teams'))
        teams_cb.grid(row=1, column=2, padx=(8, 0), sticky=tk.W, pady=(0, 4))
        _tip(teams_cb, "Send a Microsoft Teams notification when checkout completes.")

        ttk.Label(detect_frame, text="Webhook:").grid(
            row=2, column=0, sticky=tk.W, pady=(0, 2))
        webhook_entry = ttk.Entry(detect_frame,
                  textvariable=self.context.get_var('checkout_webhook_url'))
        webhook_entry.grid(row=3, column=0, columnspan=3, sticky="we", pady=(0, 2))
        _tip(webhook_entry,
             "Microsoft Teams incoming webhook URL.\nLeave blank to disable Teams alerts.")

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
        btn_frame.grid(row=row, column=0, pady=(0, 6))

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
        frm.grid(row=row, column=0, sticky="we", pady=(0, 0))
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
            self._profile_data[row_idx][col_name] = edit_entry.get().strip()
            self._refresh_profile_grid()
            edit_entry.destroy()

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
        self._update_profile_status()
        self.log(f"✓ Profile table loaded from CRT: {crt_data.get('count', 0)} row(s)")

    def on_autofill_completed(self, mid, cfgpn, fw_ver):
        self.log(f"✓ Auto-filled: MID={mid}  CFGPN={cfgpn}  FW={fw_ver}")

    def on_xml_imported(self, data: dict):
        # ── Auto-fill TGZ path ────────────────────────────────────────
        if data.get("tgz_path"):
            self.context.get_var('checkout_tgz_path').set(data["tgz_path"])

        # ── Populate profile grid from material_rows ──────────────────
        material_rows = data.get("material_rows", [])
        env = data.get("env", "")
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
                    "ATTR_OVERWRITE": "",
                })
            self._refresh_profile_grid()
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

    def get_profile_table_data(self) -> List[Dict]:
        return self._profile_data.copy()
