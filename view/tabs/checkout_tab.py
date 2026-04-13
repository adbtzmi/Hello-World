# -*- coding: utf-8 -*-
import os
import json
import logging
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import Any, List, Dict
from view.tabs.base_tab import BaseTab

logger = logging.getLogger("bento_app")

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
      2. Checkout Paths & Test Cases  (3-column grid: Label | Input | Button)
         - Site, Form Factor, Generate TempTraveler, Auto Start  (single left-aligned row)
         - TGZ path + Browse
         - Recipe override + Scan TGZ
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

        # ── Task 2: Result collection state ───────────────────────────
        self._rc_checkout_results: Dict[str, dict] = {}   # hostname → result
        self._rc_checkout_hostnames: List[str]      = []   # testers used
        self._rc_mids_file: str                     = ""   # auto-generated MIDs.txt path

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
        _sv('checkout_excel_path', '')
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
            # Ensure inner frame is at least as tall as the canvas
            # to prevent empty space at the top when scrolling
            inner_h = self._inner.winfo_reqheight()
            if inner_h < event.height:
                canvas.itemconfig(_win, height=event.height)
            else:
                canvas.itemconfig(_win, height=inner_h)

        self._inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _mousewheel(event):
            # Only scroll when the mouse is actually over this canvas/tab
            # to avoid stealing scroll events from other widgets
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        f = self._inner
        self._build_profile_section(f, row=0)
        self._build_paths_section(f, row=1)
        self._build_detection_tester_section(f, row=2)
        self._build_action_buttons(f, row=3)
        self._build_test_progress_section(f, row=5)

        # ── Keyboard shortcuts ───────────────────────────────────────────
        self.bind_all("<Control-Return>", lambda e: self._start_checkout())
        self.bind_all("<Control-i>",      lambda e: self._import_xml())

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

        self.gen_btn = ttk.Button(toolbar, text="Generate Profile",
                   command=self._generate_xml_only)
        self.gen_btn.pack(side=tk.LEFT, padx=(6, 3))
        self.context.lockable_buttons.append(self.gen_btn)
        _tip(self.gen_btn, "Build SLATE XML profile(s) and save to XML_OUTPUT folder.\n"
                      "Does NOT trigger checkout. Inspect XML, then copy to\n"
                      "CHECKOUT_QUEUE manually when ready.\n"
                      "Shortcut: Ctrl+Enter (Start)  |  Ctrl+I (Import XML)")

        imp_btn = ttk.Button(toolbar, text="📥 Import Profile",
                   command=self._import_xml)
        imp_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(imp_btn, "Load an existing SLATE XML profile and auto-fill TGZ path from it.")

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
        # 3-column grid: Label (col 0) | Input (col 1, expands) | Button (col 2)
        frm.columnconfigure(0, weight=0)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(2, weight=0)

        cur_row = 0

        # ── Row 0: Site, Form Factor, Generate TempTraveler, Auto Start ──
        from model.site_config import _DEFAULT_SITES, _DEFAULT_FORM_FACTORS

        ttk.Label(frm, text="Site:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))

        opts_row = ttk.Frame(frm)
        opts_row.grid(row=cur_row, column=1, columnspan=2,
                      sticky=tk.W, pady=(0, 6))

        site_combo = ttk.Combobox(
            opts_row, textvariable=self.context.get_var('checkout_site'),
            values=list(_DEFAULT_SITES), state="readonly", width=15)
        site_combo.pack(side=tk.LEFT, padx=(0, 16))
        _tip(site_combo,
             "Select the manufacturing site.\n"
             "Routes MAM queries and other operations to the correct servers.")

        ttk.Label(opts_row, text="Form Factor:").pack(
            side=tk.LEFT, padx=(0, 4))
        ff_combo = ttk.Combobox(
            opts_row, textvariable=self.context.get_var('checkout_form_factor'),
            values=[""] + list(_DEFAULT_FORM_FACTORS),
            state="readonly", width=10)
        ff_combo.pack(side=tk.LEFT, padx=(0, 16))
        _tip(ff_combo,
             "Default form factor for new profile rows.\n"
             "Per-row Form_Factor in the profile table takes precedence.")

        gen_tt_cb = ttk.Checkbutton(
            opts_row, text="Generate TempTraveler",
            variable=self.context.get_var('checkout_gen_tmptravl'))
        gen_tt_cb.pack(side=tk.LEFT, padx=(0, 16))
        _tip(gen_tt_cb,
             "Generate a TempTraveler .dat file for each MID.\n"
             "Uses the template in model/resources/template_tmptravl.dat.")

        autostart_cb = ttk.Checkbutton(
            opts_row, text="Auto Start",
            variable=self.context.get_var('checkout_autostart'))
        autostart_cb.pack(side=tk.LEFT)
        _tip(autostart_cb,
             "Set AutoStart=True in the generated XML profile.\n"
             "When enabled, SLATE automatically starts the test without\n"
             "requiring a manual 'Run Test' click.\n"
             "Mirrors CAT's Auto Start checkbox.")

        cur_row += 1

        # ── Separator ─────────────────────────────────────────────────
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=cur_row, column=0, columnspan=3, sticky="we", pady=(0, 4))
        cur_row += 1

        # ── Row 3: TGZ path ──────────────────────────────────────────
        ttk.Label(frm, text="TGZ:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        tgz_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_tgz_path'))
        tgz_entry.grid(row=cur_row, column=1, sticky="we", pady=(0, 4))
        _tip(tgz_entry, "Path to the compiled .tgz test program archive.\n"
             "Default browse location: P:\\temp\\BENTO\\RELEASE_TGZ")
        tgz_btn = ttk.Button(frm, text="Browse TGZ", width=12, command=self._browse_tgz)
        tgz_btn.grid(row=cur_row, column=2, padx=(6, 0), pady=(0, 4))
        _tip(tgz_btn, "Browse for a compiled TGZ archive.")
        cur_row += 1

        # ── Row 4: Recipe override ───────────────────────────────────
        ttk.Label(frm, text="Recipe:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        recipe_frame = ttk.Frame(frm)
        recipe_frame.grid(row=cur_row, column=1, columnspan=2,
                          sticky="we", pady=(0, 4))
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
        scan_btn = ttk.Button(recipe_frame, text="Scan TGZ", width=12,
                              command=self._scan_tgz_recipes)
        scan_btn.pack(side=tk.LEFT, padx=(6, 0))
        _tip(scan_btn, "Scan the selected TGZ archive for available recipe files\n"
             "and populate the dropdown list.")
        cur_row += 1

        # ── Separator ─────────────────────────────────────────────────
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=cur_row, column=0, columnspan=3, sticky="we", pady=(0, 4))
        cur_row += 1

        # ── Row 6: Test Cases ────────────────────────────────────────
        ttk.Label(frm, text="TC:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))

        tc_frame = ttk.Frame(frm)
        tc_frame.grid(row=cur_row, column=1, columnspan=2,
                      sticky="we", pady=(0, 4))

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
        cur_row += 1

        # ── Separator ─────────────────────────────────────────────────
        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(
            row=cur_row, column=0, columnspan=3, sticky="we", pady=(0, 4))
        cur_row += 1

        # ── Row 8: Hot Folder ────────────────────────────────────────
        ttk.Label(frm, text="Hot Folder:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8))
        hot_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_hot_folder'))
        hot_entry.grid(row=cur_row, column=1, columnspan=2, sticky="we")
        _tip(hot_entry, "Path to the SLATE hot folder where XML profiles are dropped.\n"
             "Default: C:\\test_program\\playground_queue")

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 3 — SLATE Detection + Tester Selection (side-by-side)
    # ──────────────────────────────────────────────────────────────────────

    def _build_detection_tester_section(self, parent, row):
        side_frame = ttk.Frame(parent)
        side_frame.grid(row=row, column=0, sticky="we", pady=(0, 14))
        side_frame.columnconfigure(0, weight=1)

        # ── Tester Selection ──────────────────────────────────────────────
        tester_outer = ttk.LabelFrame(side_frame, text="  Tester Selection  ",
                                      padding=(8, 6, 8, 8))
        tester_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 0))
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

    def _has_duplicate_mid(self, mid: str) -> bool:
        """Check if a MID already exists in the profile table."""
        if not mid.strip():
            return False
        return sum(1 for r in self._profile_data
                   if r.get("MID", "").strip().upper() == mid.strip().upper()) > 1

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
        # Preserve current selection index so we can restore it after rebuild
        sel = self._profile_grid.selection()
        sel_idx = self._profile_grid.index(sel[0]) if sel else -1

        for row in self._profile_grid.get_children():
            self._profile_grid.delete(row)
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        for row_dict in self._profile_data:
            values = [str(row_dict.get(col, "")) for col in cols]
            self._profile_grid.insert("", tk.END, values=values)
        self._profile_row_count_label.configure(
            text=f"{len(self._profile_data)} row(s)")

        # Restore selection if the row still exists
        children = self._profile_grid.get_children()
        if children and 0 <= sel_idx < len(children):
            self._profile_grid.selection_set(children[sel_idx])
            self._profile_grid.see(children[sel_idx])

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
        # Update the Excel File path to the selected file
        self.context.get_var('checkout_excel_path').set(path)
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
        parent: tk.Widget = self.winfo_toplevel()  # type: ignore[assignment]
        HardwareConfigDialog(parent)

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
                # Warn on duplicate MID
                if self._has_duplicate_mid(new_value):
                    messagebox.showwarning(
                        "Duplicate MID",
                        f"MID '{new_value}' already exists in another row.\n"
                        f"Duplicate MIDs will generate conflicting profiles.")

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
            var = tk.BooleanVar(value=False)
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
        """Update the 'Tester' column in profile rows that haven't been manually edited.

        Only overwrites rows where the Tester value is empty or matches
        a previously auto-synced value (comma-separated hostname list).
        Rows with manually entered Tester values are left untouched.
        """
        selected = [h for h, var in self._tester_vars.items() if var.get()]
        tester_value = ", ".join(selected)

        if not self._profile_data:
            return

        # Build a set of all possible auto-synced values (any combination
        # of known hostnames) so we can detect manual edits.
        all_hostnames = set(self._tester_vars.keys())

        changed = False
        for row in self._profile_data:
            current = row.get("Tester", "").strip()
            # Only overwrite if: empty, or current value is purely
            # a comma-separated list of known hostnames (i.e., auto-synced)
            if not current:
                row["Tester"] = tester_value
                changed = True
            else:
                # Check if current value is auto-synced (all parts are known hostnames)
                parts = {p.strip() for p in current.split(",") if p.strip()}
                if parts and parts.issubset(all_hostnames):
                    if current != tester_value:
                        row["Tester"] = tester_value
                        changed = True
                # else: manually edited — leave it alone

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

        # Show busy cursor while scanning
        self.config(cursor="watch")
        self.update_idletasks()
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
        finally:
            self.config(cursor="")

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

        # ── Pre-flight validation ──────────────────────────────────────
        errors = []
        if not hostnames:
            errors.append("No testers selected — select at least one tester.")
        if not tgz_path:
            errors.append("TGZ path is empty — select a compiled TGZ archive.")
        elif not os.path.isfile(tgz_path):
            errors.append(f"TGZ file not found: {tgz_path}")
        if not tc_passing and not tc_fail:
            errors.append("No test cases selected — enable at least PASS or FAIL.")
        if not self._profile_data:
            errors.append("Profile table is empty — add at least one row.")
        else:
            # Check that at least one row has a MID
            has_mid = any(row.get("MID", "").strip() for row in self._profile_data)
            if not has_mid:
                errors.append("No MID specified in any profile row.")
        if errors:
            self.show_error("Validation Error",
                            "Please fix the following before starting checkout:\n\n"
                            + "\n".join(f"• {e}" for e in errors))
            return None

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
        pass

    def _open_queue_folder(self):
        queue_path = r"P:\temp\BENTO\CHECKOUT_QUEUE"
        if os.path.isdir(queue_path):
            os.startfile(queue_path)
        else:
            self.show_error("Folder Not Found",
                            f"Queue folder not found:\n{queue_path}\n\n"
                            "Ensure P: drive is mapped.")

    def _append_result(self, text: str):
        self.log(text)

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

        # Store result for auto-detect
        self._rc_checkout_results[hostname] = result
        if hostname not in self._rc_checkout_hostnames:
            self._rc_checkout_hostnames.append(hostname)

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

            # ── Task 2: Auto-start result collection ──────────────────
            any_success = any(
                r.get("status", "").lower() in ("success", "partial")
                for r in self._rc_checkout_results.values()
            )
            if any_success and self._rc_checkout_hostnames:
                primary_host = self._rc_checkout_hostnames[0]
                self.log(f"🔍 All testers done — auto-starting result "
                         f"collection for {primary_host}...")
                try:
                    self._rc_auto_start_monitoring(primary_host)
                except Exception as e:
                    self.log(f"⚠ Auto-start failed: {e}")
                    logger.error(f"Auto-start result collection failed: {e}")

                # ── Task 3: Auto-start manifest polling ─────────────────
                # The watcher writes a file_manifest JSON after scanning
                # the workspace.  Poll for it so we can show the file
                # selection dialog to the user.
                try:
                    jira_key = (
                        self.context.get_var('issue_var').get().strip().upper()
                    )
                    if not jira_key or jira_key.endswith("-"):
                        # Fallback: extract from TGZ filename
                        import re as _re
                        tgz = self.context.get_var(
                            'checkout_tgz_path').get().strip()
                        m = _re.search(r'([A-Za-z]+-\d+)',
                                       os.path.basename(tgz))
                        jira_key = m.group(1).upper() if m else ""

                    if jira_key:
                        # Resolve ENV for the primary host
                        ctrl = self.context.controller
                        env = ""
                        if ctrl and hasattr(ctrl, "checkout_controller"):
                            env = (ctrl.checkout_controller
                                   ._get_env_for_hostname(primary_host))

                        # Build base search path:
                        #   CHECKOUT_RESULTS / <host>_<ENV> / <jira_key>
                        CHECKOUT_RESULTS = (
                            r"P:\temp\BENTO\CHECKOUT_RESULTS"
                        )
                        tester_folder = (
                            f"{primary_host}_{env.upper()}"
                            if env else primary_host
                        )
                        results_base = os.path.join(
                            CHECKOUT_RESULTS, tester_folder, jira_key
                        )

                        self._manifest_poll_count = 0
                        self.log(
                            f"📋 Polling for file manifest from "
                            f"{primary_host} ({jira_key})..."
                        )
                        # Start polling after a short delay (watcher
                        # needs time to scan + write the manifest)
                        self.context.root.after(
                            3000,
                            lambda: self._poll_for_manifest(
                                primary_host, jira_key, results_base
                            )
                        )
                    else:
                        self.log("⚠ No JIRA key — skipping manifest poll")
                except Exception as e:
                    self.log(f"⚠ Manifest poll setup failed: {e}")
                    logger.error(f"Manifest poll setup failed: {e}")

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

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Test Progress (Result Collection)
    # ──────────────────────────────────────────────────────────────────────

    def _build_test_progress_section(self, parent, row):
        """Build the auto-detect test progress section with MID status treeview."""
        frm = ttk.LabelFrame(parent, text="  Test Progress (Auto-Detect)  ",
                             padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 14))
        frm.columnconfigure(0, weight=1)

        # ── Summary bar ───────────────────────────────────────────────
        summary_frm = ttk.Frame(frm)
        summary_frm.grid(row=0, column=0, sticky="we", pady=(0, 4))

        self._rc_status_indicator = ttk.Label(
            summary_frm, text="⏸ Idle", font=("Segoe UI", 9, "bold"))
        self._rc_status_indicator.pack(side=tk.LEFT, padx=(0, 12))

        self._rc_summary_label = ttk.Label(
            summary_frm, text="", font=("Segoe UI", 8))
        self._rc_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Toolbar ───────────────────────────────────────────────────
        toolbar = ttk.Frame(frm)
        toolbar.grid(row=1, column=0, sticky="we", pady=(0, 4))

        self._rc_refresh_btn = ttk.Button(
            toolbar, text="🔄 Refresh", width=10,
            command=self._rc_refresh_status)
        self._rc_refresh_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tip(self._rc_refresh_btn, "Force re-check all MID statuses.")

        self._rc_collect_btn = ttk.Button(
            toolbar, text="📁 Collect", width=10,
            command=self._rc_collect_selected)
        self._rc_collect_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tip(self._rc_collect_btn, "Manually collect files for selected MID(s).")

        self._rc_spool_btn = ttk.Button(
            toolbar, text="📊 Spool", width=10,
            command=self._rc_spool_selected)
        self._rc_spool_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tip(self._rc_spool_btn, "Manually spool summary for selected MID(s).")

        self._rc_stop_btn = ttk.Button(
            toolbar, text="⏹ Stop", width=8,
            command=self._rc_stop_monitoring, state=tk.DISABLED)
        self._rc_stop_btn.pack(side=tk.RIGHT)
        _tip(self._rc_stop_btn, "Stop the background result collector.")

        # ── Treeview ──────────────────────────────────────────────────
        tree_frm = ttk.Frame(frm)
        tree_frm.grid(row=2, column=0, sticky="nsew")
        tree_frm.columnconfigure(0, weight=1)

        columns = ("mid", "location", "name", "status", "fail_info",
                   "collected", "error")
        self._rc_tree = ttk.Treeview(
            tree_frm, columns=columns, show="headings", height=5,
            selectmode="extended")

        self._rc_tree.heading("mid",       text="MID")
        self._rc_tree.heading("location",  text="Location")
        self._rc_tree.heading("name",      text="Name")
        self._rc_tree.heading("status",    text="Status")
        self._rc_tree.heading("fail_info", text="Fail Info")
        self._rc_tree.heading("collected", text="Collected")
        self._rc_tree.heading("error",     text="Error")

        self._rc_tree.column("mid",       width=90,  minwidth=70)
        self._rc_tree.column("location",  width=50,  minwidth=40)
        self._rc_tree.column("name",      width=120, minwidth=80)
        self._rc_tree.column("status",    width=70,  minwidth=50)
        self._rc_tree.column("fail_info", width=150, minwidth=80)
        self._rc_tree.column("collected", width=60,  minwidth=40)
        self._rc_tree.column("error",     width=150, minwidth=80)

        rc_scroll = ttk.Scrollbar(tree_frm, orient=tk.VERTICAL,
                                  command=self._rc_tree.yview)
        self._rc_tree.configure(yscrollcommand=rc_scroll.set)
        self._rc_tree.grid(row=0, column=0, sticky="nsew")
        rc_scroll.grid(row=0, column=1, sticky="ns")

        # Row colour tags
        self._rc_tree.tag_configure("pass",    foreground="green")
        self._rc_tree.tag_configure("fail",    foreground="red")
        self._rc_tree.tag_configure("running", foreground="blue")
        self._rc_tree.tag_configure("unknown", foreground="gray")
        self._rc_tree.tag_configure("error",   foreground="orange")

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Toolbar actions
    # ──────────────────────────────────────────────────────────────────────

    def _rc_refresh_status(self):
        """Force a status refresh via ResultController."""
        controller = self.context.controller
        if controller and hasattr(controller, "result_controller"):
            rc = controller.result_controller
            if rc and rc.is_running():
                rc.refresh_status()
            else:
                self.log("⚠ Result collector is not running.")

    def _rc_collect_selected(self):
        """Manually collect files for selected MIDs."""
        selected = self._rc_tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Select one or more MIDs to collect.")
            return
        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            return
        rc = controller.result_controller
        if not rc or not rc.is_running():
            self.log("⚠ Result collector is not running.")
            return
        for item_id in selected:
            values = self._rc_tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                rc.collect_single(mid)

    def _rc_spool_selected(self):
        """Manually spool summary for selected MIDs."""
        selected = self._rc_tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Select one or more MIDs to spool.")
            return
        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            return
        rc = controller.result_controller
        if not rc or not rc.is_running():
            self.log("⚠ Result collector is not running.")
            return
        for item_id in selected:
            values = self._rc_tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                rc.spool_single(mid)

    def _rc_stop_monitoring(self):
        """Stop the background result collector."""
        controller = self.context.controller
        if controller and hasattr(controller, "result_controller"):
            rc = controller.result_controller
            if rc:
                rc.stop_monitoring()
        self._rc_stop_btn.config(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Auto-detect: generate MIDs.txt + start monitoring
    # ──────────────────────────────────────────────────────────────────────

    def _rc_auto_start_monitoring(self, hostname: str):
        """
        Auto-generate MIDs.txt from profile table and start ResultCollector.

        Called when all checkout testers have completed.  Builds a MIDs.txt
        from the profile_data rows (MID + Primitive + Dut) and starts the
        background result collector.
        """
        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            self.log("⚠ ResultController not available — skipping auto-detect.")
            return

        rc = controller.result_controller
        if rc.is_running():
            self.log("⚠ Result collector already running — skipping auto-start.")
            return

        # ── Build MIDs.txt from profile table ─────────────────────────
        if not self._profile_data:
            self.log("⚠ No profile data — cannot auto-start result collection.")
            return

        mids_lines = []
        for row in self._profile_data:
            mid  = row.get("MID", "").strip()
            loc  = row.get("Primitive", row.get("PRIMITIVE", "")).strip()
            name = row.get("Dut", row.get("DUT", "")).strip()
            if not mid:
                continue
            loc_str  = loc if loc else "0"
            name_str = name if name else mid
            mids_lines.append(f"{mid}  {loc_str}  {name_str}  False")

        if not mids_lines:
            self.log("⚠ No MIDs found in profile table — skipping auto-detect.")
            return

        # Write to temp file
        try:
            mids_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "temp"
            )
            os.makedirs(mids_dir, exist_ok=True)
            mids_path = os.path.join(mids_dir, f"MIDs_{hostname}.txt")
            with open(mids_path, "w") as f:
                f.write("# Auto-generated by BENTO Checkout\n")
                f.write(f"# Hostname: {hostname}\n")
                for line in mids_lines:
                    f.write(line + "\n")
            self._rc_mids_file = mids_path
            self.log(f"✓ Auto-generated MIDs.txt: {mids_path}")
        except Exception as e:
            self.log(f"✗ Failed to generate MIDs.txt: {e}")
            logger.error(f"MIDs.txt generation failed: {e}")
            return

        # ── Resolve parameters from context ───────────────────────────
        site         = self.context.get_var('checkout_site').get().strip()
        webhook_url  = self.context.get_var('checkout_webhook_url').get().strip()
        notify_teams = self.context.get_var('checkout_notify_teams').get()

        # ── Start monitoring ──────────────────────────────────────────
        self.log(f"🚀 Auto-starting result collection for {hostname}...")
        self._rc_status_indicator.config(
            text="🔄 MONITORING", foreground="blue")
        self._rc_stop_btn.config(state=tk.NORMAL)

        rc.start_monitoring(
            mids_file       = mids_path,
            target_path     = "",
            site            = site,
            machine_type    = "",
            tester_hostname = hostname,
            poll_interval   = 30,
            auto_collect    = True,
            auto_spool      = False,
            webhook_url     = webhook_url,
            notify_teams    = bool(notify_teams),
        )

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Controller → View callbacks
    # ──────────────────────────────────────────────────────────────────────

    def on_rc_progress_update(self, summary: dict, entries: dict):
        """
        Called by ResultController (via root.after) when progress updates.
        Updates the Section 6 treeview and summary bar.
        """
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
            if status == "FAIL":
                fail_info = (f"{entry_data.get('fail_reg', '')} - "
                             f"{entry_data.get('fail_code', '')}")

            collected_str = "✓" if entry_data.get("collected", False) else ""
            error_str     = entry_data.get("error", "")

            tag = status.lower() if status.lower() in (
                "pass", "fail", "running") else "unknown"
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
        """
        Called by ResultController when monitoring ends.
        Resets button states and updates status indicator.
        """
        self._rc_stop_btn.config(state=tk.DISABLED)

        all_done = summary.get("all_done", False)
        failed   = summary.get("failed", 0)

        if all_done and failed == 0:
            self._rc_status_indicator.config(
                text="✅ ALL PASSED", foreground="green")
        elif all_done and failed > 0:
            self._rc_status_indicator.config(
                text="⚠ COMPLETED (with failures)", foreground="orange")
        else:
            self._rc_status_indicator.config(
                text="⏹ STOPPED", foreground="gray")

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 7 — MANIFEST-BASED FILE SELECTION (BENTO ↔ Watcher)
    # ══════════════════════════════════════════════════════════════════════

    def _poll_for_manifest(self, hostname: str, job_id: str,
                           results_base: str):
        """
        Poll CHECKOUT_RESULTS for a manifest JSON written by the watcher.
        When found, show a file selection dialog to the user.

        Called automatically after checkout completes (from on_checkout_completed).
        Runs as a background poll via root.after() to avoid blocking the GUI.

        The watcher writes the manifest to a workspace-specific subfolder
        whose name is not known to BENTO at poll-start time, so we search
        recursively under ``results_base`` for the manifest file.

        Args:
            hostname     : tester hostname (e.g. "IBIR-0383")
            job_id       : JIRA key (e.g. "TSESSD-14270")
            results_base : base path under CHECKOUT_RESULTS to search
                           (e.g. P:\\temp\\BENTO\\CHECKOUT_RESULTS\\HOST_ENV\\JIRA)
        """
        import glob as _glob

        manifest_pattern = os.path.join(
            results_base, "**",
            f"file_manifest_{hostname}_{job_id}.json",
        )
        matches = _glob.glob(manifest_pattern, recursive=True)

        if matches:
            manifest_path = matches[0]
            results_folder = os.path.dirname(manifest_path)
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                self.log(f"📋 File manifest received from {hostname}")
                self._show_file_selection_dialog(manifest, hostname,
                                                  job_id, results_folder)
            except Exception as e:
                self.log(f"⚠ Error reading manifest: {e}")
                logger.error(f"Manifest read error: {e}")
        else:
            # Keep polling every 5 seconds (up to 5 minutes)
            if not hasattr(self, "_manifest_poll_count"):
                self._manifest_poll_count = 0
            self._manifest_poll_count += 1

            if self._manifest_poll_count < 60:  # 60 * 5s = 5 min
                self.context.root.after(
                    5000,
                    lambda: self._poll_for_manifest(
                        hostname, job_id, results_base
                    )
                )
            else:
                self.log("⏱ Manifest poll timeout — watcher will use "
                         "required-only fallback")
                self._manifest_poll_count = 0

    def _show_file_selection_dialog(self, manifest: dict, hostname: str,
                                     job_id: str, results_folder: str):
        """
        Show a Toplevel dialog listing available files from the manifest.
        User checks which optional files to collect; required files are
        always selected and greyed out.

        Args:
            manifest       : parsed manifest JSON dict
            hostname       : tester hostname
            job_id         : JIRA key
            results_folder : path to write selection JSON back to
        """
        dlg = tk.Toplevel(self)
        dlg.title(f"Select Files to Collect — {hostname}")
        dlg.geometry("550x420")
        dlg.resizable(True, True)
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()

        # Header
        ttk.Label(
            dlg,
            text=f"Files available on {hostname} ({job_id})",
            font=("Segoe UI", 10, "bold"),
        ).pack(padx=10, pady=(10, 5), anchor="w")

        ttk.Label(
            dlg,
            text="Required files are always collected. "
                 "Select optional files below:",
            foreground="gray",
        ).pack(padx=10, pady=(0, 10), anchor="w")

        # Scrollable frame for checkboxes
        canvas_frame = ttk.Frame(dlg)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical",
                                   command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Build checkboxes from manifest
        check_vars: Dict[str, tk.BooleanVar] = {}
        files_info = manifest.get("files", [])

        for i, file_entry in enumerate(files_info):
            key      = file_entry.get("key", "")
            desc     = file_entry.get("desc", key)
            required = file_entry.get("required", False)
            found    = file_entry.get("found", False)
            count    = file_entry.get("count", 0)
            sizes    = file_entry.get("sizes", [])
            names    = file_entry.get("names", [])

            var = tk.BooleanVar(value=required or found)
            check_vars[key] = var

            row_frame = ttk.Frame(inner)
            row_frame.pack(fill=tk.X, pady=2)

            cb = ttk.Checkbutton(row_frame, variable=var)
            cb.pack(side=tk.LEFT)

            # Required files: always checked, disabled
            if required:
                var.set(True)
                cb.config(state="disabled")

            # Not found: unchecked, disabled
            if not found:
                var.set(False)
                cb.config(state="disabled")

            # Label with description
            size_str = ""
            if sizes:
                total = sum(sizes)
                if total > 1024 * 1024:
                    size_str = f" ({total / (1024*1024):.1f} MB)"
                elif total > 1024:
                    size_str = f" ({total / 1024:.1f} KB)"
                else:
                    size_str = f" ({total} B)"

            status_icon = "✅" if found else "❌"
            req_tag     = " [REQUIRED]" if required else ""
            name_str    = ""
            if names:
                name_str = " — " + ", ".join(names[:3])
                if len(names) > 3:
                    name_str += f" (+{len(names)-3} more)"

            label_text = (
                f"{status_icon} {desc}{req_tag}{size_str}{name_str}"
            )
            lbl = ttk.Label(row_frame, text=label_text, wraplength=450)
            lbl.pack(side=tk.LEFT, padx=(5, 0))

        # Buttons
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        def _on_confirm():
            selected = [k for k, v in check_vars.items() if v.get()]
            self._write_file_selection(
                selected, hostname, job_id, results_folder
            )
            dlg.destroy()

        def _on_select_all():
            for key, var in check_vars.items():
                entry = next(
                    (f for f in files_info if f.get("key") == key), {}
                )
                if entry.get("found", False):
                    var.set(True)

        def _on_cancel():
            # Cancel = only required files
            required_keys = [
                f.get("key", "") for f in files_info
                if f.get("required", False)
            ]
            self._write_file_selection(
                required_keys, hostname, job_id, results_folder
            )
            dlg.destroy()

        ttk.Button(
            btn_frame, text="Select All", command=_on_select_all
        ).pack(side=tk.LEFT, padx=(0, 5))

        ttk.Button(
            btn_frame, text="Collect Selected", command=_on_confirm,
            style="Accent.TButton" if "Accent.TButton" in
            ttk.Style().theme_names() else "TButton",
        ).pack(side=tk.RIGHT, padx=(5, 0))

        ttk.Button(
            btn_frame, text="Required Only", command=_on_cancel
        ).pack(side=tk.RIGHT, padx=(5, 0))

    def _write_file_selection(self, selected_keys: list, hostname: str,
                               job_id: str, results_folder: str):
        """
        Write the user's file selection JSON back to the shared folder
        so the watcher can read it and copy the selected files.

        Args:
            selected_keys  : list of file key strings chosen by user
            hostname       : tester hostname
            job_id         : JIRA key
            results_folder : path to write selection JSON
        """
        selection = {
            "job_id":        job_id,
            "hostname":      hostname,
            "selected_keys": selected_keys,
            "timestamp":     datetime.now().isoformat(),
        }

        selection_name = f"file_selection_{hostname}_{job_id}.json"
        selection_path = os.path.join(results_folder, selection_name)

        try:
            os.makedirs(results_folder, exist_ok=True)
            with open(selection_path, "w") as f:
                json.dump(selection, f, indent=2)
            self.log(
                f"📝 File selection written: {len(selected_keys)} file(s) "
                f"selected for {hostname}"
            )
            logger.info(f"File selection written: {selection_path}")
        except Exception as e:
            self.log(f"⚠ Failed to write file selection: {e}")
            logger.error(f"File selection write error: {e}")
