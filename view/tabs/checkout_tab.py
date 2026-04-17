# -*- coding: utf-8 -*-
import os
import time
from enum import Enum
import json
import logging
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
import tksheet
from typing import Any, List, Dict
from view.tabs.base_tab import BaseTab
from view.widgets.tooltip import ToolTip as _ToolTip, tip as _tip

logger = logging.getLogger("bento_app")

# Status constants (shared with result_collector model)
_STATUS_PASS    = "PASS"
_STATUS_FAIL    = "FAIL"
_STATUS_RUNNING = "RUNNING"


# ─────────────────────────────────────────────────────────────────────────────
# Checkout State Machine
# ─────────────────────────────────────────────────────────────────────────────

class CheckoutState(Enum):
    """
    Checkout state machine for UI control.
    Single source of truth for button enable/disable logic.
    """
    IDLE = "IDLE"               # No active checkout, ready to start
    RUNNING = "RUNNING"         # Checkout in progress
    COLLECTING = "COLLECTING"   # Checkout done, collecting results
    STOPPING = "STOPPING"       # Stop requested, waiting for cleanup
    COMPLETED = "COMPLETED"     # Checkout finished successfully
    ERROR = "ERROR"             # Checkout finished with errors


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

    B19 — Row validation indicators (incomplete rows highlighted red, complete green)
    B20 — Bulk paste from Excel (Ctrl+V multi-row paste synced to _profile_data)
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

    # Profile Generation Table columns (name, width)
    # Optimized widths to prevent overflow while maintaining readability
    _PROFILE_GEN_COLUMNS = [
        ("Form_Factor",         100),
        ("Material_Desc",       140),
        ("CFGPN",               100),
        ("MCTO_#1",              85),
        ("Dummy_Lot",           100),
        ("Step",                 70),
        ("MID",                 100),
        ("Tester",              100),
        ("DIB_TYPE",             90),
        ("MACHINE_MODEL",       110),
        ("MACHINE_VENDOR",      115),
        ("Primitive",            85),
        ("Dut",                  50),
        ("ATTR_OVERWRITE",      150),
    ]

    _AUTO_POPULATED_COLS = {"Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot"}

    _EDITABLE_COLS = {
        "Form_Factor", "Material_Desc", "CFGPN", "MCTO_#1", "Dummy_Lot",
        "Step", "MID", "Tester", "DIB_TYPE", "MACHINE_MODEL", "MACHINE_VENDOR",
        "Primitive", "Dut", "ATTR_OVERWRITE",
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
        "DIB_TYPE":              "DIB_TYPE",
        "DIB TYPE":              "DIB_TYPE",
        "Dib_Type":              "DIB_TYPE",
        "MACHINE_MODEL":         "MACHINE_MODEL",
        "MACHINE MODEL":         "MACHINE_MODEL",
        "Machine_Model":         "MACHINE_MODEL",
        "MACHINE_VENDOR":        "MACHINE_VENDOR",
        "MACHINE VENDOR":        "MACHINE_VENDOR",
        "Machine_Vendor":        "MACHINE_VENDOR",
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

        # B18: Undo/Redo stack for profile table
        self._undo_stack: List[List[Dict]] = []
        self._redo_stack: List[List[Dict]] = []
        self._max_undo = 30

        # ── Checkout State Machine ────────────────────────────────────
        self._checkout_state = CheckoutState.IDLE

        # ── Task 2: Result collection state ───────────────────────────
        self._rc_checkout_results: Dict[str, dict] = {}   # hostname -> result
        self._rc_checkout_hostnames: List[str]      = []   # testers used
        self._rc_mids_file: str                     = ""   # auto-generated MIDs.txt path
        self._manifest_poll_started: bool           = False # guard: only start once

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

        # Initialize hot folder from global site path
        from model.site_paths import get_site_path
        default_hot_folder = get_site_path("CHECKOUT_QUEUE")
        
        _sv('checkout_tgz_path')
        _sv('checkout_recipe_override')
        _sv('checkout_hot_folder', default_hot_folder)
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
        self._build_test_progress_section(f, row=4)

        # ── Keyboard shortcuts ───────────────────────────────────────────
        self.bind_all("<Control-Return>", lambda e: self._start_checkout())
        self.bind_all("<Control-i>",      lambda e: self._import_xml())
        # B18: Undo/Redo keyboard shortcuts for profile table
        self.bind_all("<Control-z>",      lambda e: self._profile_undo())
        self.bind_all("<Control-y>",      lambda e: self._profile_redo())

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

        self.gen_btn = ttk.Button(toolbar, text="Generate Profile",
                   command=self._generate_xml_only)
        self.gen_btn.pack(side=tk.LEFT, padx=(6, 3))
        self.context.lockable_buttons.append(self.gen_btn)
        _tip(self.gen_btn, "Build SLATE XML profile(s) and save to XML_OUTPUT folder.\n"
                      "Does NOT trigger checkout. Inspect XML, then copy to\n"
                      "CHECKOUT_QUEUE manually when ready.\n"
                      "Shortcut: Ctrl+Enter (Start)  |  Ctrl+I (Import XML)")

        imp_btn = ttk.Button(toolbar, text="Import Profile",
                   command=self._import_xml)
        imp_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(imp_btn, "Load an existing SLATE XML profile and auto-fill TGZ path from it.")

        # ── Profile Grid (tksheet for visible black cell borders) ────────
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        col_widths = [w for _, w in self._PROFILE_GEN_COLUMNS]

        self._profile_grid = tksheet.Sheet(
            frm,
            headers=cols,
            show_x_scrollbar=True,
            show_y_scrollbar=True,
            height=160,
            width=None,
            default_row_height=26,
            font=("Segoe UI", 9, "normal"),
            header_font=("Segoe UI", 9, "bold"),
            table_grid_fg="#000000",
            header_grid_fg="#000000",
            index_grid_fg="#000000",
            header_border_fg="#000000",
            table_bg="#ffffff",
            table_fg="#000000",
            header_bg="#f0f0f0",
            header_fg="#000000",
            top_left_bg="#f0f0f0",
            outline_thickness=1,
            outline_color="#000000",
            show_row_index=False,
            show_top_left=False,
            align="w",
        )
        self._profile_grid.grid(row=2, column=0, sticky="nsew")

        # Set individual column widths
        for i, w in enumerate(col_widths):
            self._profile_grid.column_width(column=i, width=w)

        # Enable cell editing and selection  (B4: column_width_resize + double_click_column_resize)
        self._profile_grid.enable_bindings(
            "single_select",
            "row_select",
            "column_width_resize",
            "double_click_column_resize",
            "arrowkeys",
            "copy",
            "paste",
            "undo",
            "edit_cell",
        )

        # Bind cell edit events to capture changes
        self._profile_grid.extra_bindings("begin_edit_cell", self._on_sheet_begin_edit)
        self._profile_grid.extra_bindings("end_edit_cell", self._on_sheet_cell_edited)
        self._profile_grid.extra_bindings("cell_select", self._on_sheet_cell_select)

        # B2: Right-click context menu
        self._profile_grid.bind("<Button-3>", self._profile_context_menu)

        # B20: Bulk paste from Excel — override Ctrl+V to sync with _profile_data
        self._profile_grid.bind("<Control-v>", self._on_sheet_paste)
        self._profile_grid.bind("<Control-V>", self._on_sheet_paste)

        # B12: Editable cell visual indicator — highlight editable column headers
        # with a subtle blue tint so users know which columns are editable
        editable_indices = [i for i, (c, _) in enumerate(self._PROFILE_GEN_COLUMNS)
                           if c in self._EDITABLE_COLS]
        for col_idx in editable_indices:
            try:
                self._profile_grid.highlight_columns(
                    columns=col_idx, bg="#e8f0fe", fg="#000000")
            except Exception:
                pass  # tksheet version may not support highlight_columns

        _tip(self._profile_grid,
             "Click to select a row.  Right-click for context menu.\n"
             "Double-click any cell to edit it inline.\n"
             "Double-click ATTR_OVERWRITE to open the attribute editor dialog.\n"
             "Blue-tinted columns are editable.  Ctrl+Z/Y for undo/redo.\n"
             "Ctrl+V to paste multiple rows from Excel.")

        # B16: Validation indicator label (shown below grid when issues found)
        self._validation_indicator = ttk.Label(
            frm, text="", foreground="#a80000", font=("Segoe UI", 8))
        self._validation_indicator.grid(row=3, column=0, sticky="w", pady=(2, 0))

        # ── Status bar ────────────────────────────────────────────────────
        status_bar = ttk.Frame(frm)
        status_bar.grid(row=4, column=0, sticky="we", pady=(2, 0))

        self._profile_status_label = ttk.Label(
            status_bar,
            text="No data \u2014 use 'Add Row' to populate",
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
        frm = ttk.LabelFrame(parent, text="  Checkout Paths  ",
                             padding=(8, 6, 8, 8))
        frm.grid(row=row, column=0, sticky="we", pady=(0, 14))
        # 3-column grid: Label (col 0) | Input (col 1, expands) | Button (col 2)
        frm.columnconfigure(0, weight=0)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(2, weight=0)

        cur_row = 0

        # ── Row 0: Generate TempTraveler, Auto Start ──
        opts_row = ttk.Frame(frm)
        opts_row.grid(row=cur_row, column=0, columnspan=3,
                      sticky=tk.W, pady=(0, 6))

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

        # ── TGZ Archive Path ──────────────────────────────────────────
        ttk.Label(frm, text="TGZ:").grid(
            row=cur_row, column=0, sticky=tk.W, padx=(0, 8))
        
        tgz_entry = ttk.Entry(frm, textvariable=self.context.get_var('checkout_tgz_path'))
        tgz_entry.grid(row=cur_row, column=1, sticky="we")
        _tip(tgz_entry, "Path to the compiled TGZ archive containing test programs.")
        
        tgz_browse_btn = ttk.Button(frm, text="Browse...",
                                     command=lambda: self._browse_file('checkout_tgz_path',
                                                                       "Select TGZ Archive",
                                                                       [("TGZ files", "*.tgz"), ("All files", "*.*")]))
        tgz_browse_btn.grid(row=cur_row, column=2, sticky=tk.W, padx=(4, 0))
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
        refresh_btn = ttk.Button(hdr, text="\u21bb Refresh",
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
        """Build the primary action button section.  B9: Single toggle Start/Stop button."""
        action_frame = ttk.Frame(parent)
        action_frame.grid(row=row, column=0, sticky="we", pady=(0, 14))

        # Center the buttons
        action_frame.columnconfigure(0, weight=1)
        action_frame.columnconfigure(1, weight=0)
        action_frame.columnconfigure(2, weight=0)
        action_frame.columnconfigure(3, weight=1)

        # B9: Start Checkout button (toggles to Stop when running)
        self.checkout_btn = ttk.Button(
            action_frame, text="\u25b6 Start Checkout",
            style='Accent.TButton',
            command=self._start_checkout)
        self.checkout_btn.grid(row=0, column=1, padx=(0, 6))
        self.context.lockable_buttons.append(self.checkout_btn)

        # B9: Stop button — hidden by default, shown when running
        self.stop_btn = ttk.Button(
            action_frame, text="\u25a0 Stop Checkout",
            command=self._stop_checkout)
        self.stop_btn.grid(row=0, column=2)
        self.stop_btn.grid_remove()  # Hidden initially

        _tip(self.checkout_btn,
             "Generate XML profiles and start the checkout run on all selected testers.\n"
             "Only enabled when IDLE and preconditions are met.")
        _tip(self.stop_btn,
             "Stop the active checkout run on all testers.\n"
             "Requires confirmation before stopping.")

    # ──────────────────────────────────────────────────────────────────────
    # SITE PATH UPDATE
    # ──────────────────────────────────────────────────────────────────────

    def update_paths_from_site(self):
        """Update checkout paths when global site selection changes."""
        from model.site_paths import get_site_path
        
        # Update hot folder path
        checkout_queue_path = get_site_path("CHECKOUT_QUEUE")
        self.context.get_var("checkout_hot_folder").set(checkout_queue_path)

    def _browse_file(self, var_name, title, filetypes):
        """Browse for a file and update the specified variable."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            self.context.get_var(var_name).set(path)

    def _browse_directory(self, var, title):
        """Browse for a directory and update the specified variable."""
        from tkinter import filedialog
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    # ──────────────────────────────────────────────────────────────────────
    # PROFILE TABLE — DATA MANAGEMENT
    # ──────────────────────────────────────────────────────────────────────

    def _profile_add_default_row(self):
        self._push_undo_snapshot()
        row_data = {
            "Form_Factor": "", "Material_Desc": "", "CFGPN": "",
            "MCTO_#1": "", "Dummy_Lot": "None", "Step": "",
            "MID": "", "Tester": "", "DIB_TYPE": "", "MACHINE_MODEL": "",
            "MACHINE_VENDOR": "", "Primitive": "", "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()
        self._validate_profile_realtime()

    def _profile_add_row(self):
        self._push_undo_snapshot()
        row_data = {
            "Form_Factor": "", "Material_Desc": "", "CFGPN": "",
            "MCTO_#1": "", "Dummy_Lot": "", "Step": "",
            "MID": "", "Tester": "", "DIB_TYPE": "", "MACHINE_MODEL": "",
            "MACHINE_VENDOR": "", "Primitive": "", "Dut": "",
            "ATTR_OVERWRITE": "",
        }
        self._profile_data.append(row_data)
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

    def _has_duplicate_mid(self, mid: str) -> bool:
        """Check if a MID already exists in the profile table."""
        if not mid.strip():
            return False
        return sum(1 for r in self._profile_data
                   if r.get("MID", "").strip().upper() == mid.strip().upper()) > 1

    def _profile_remove_row(self):
        # tksheet selection: try multiple API patterns for compatibility
        idx = self._get_selected_row_idx()
        if idx < 0:
            self.show_error("No Selection", "Select a row to remove.")
            return
        self._push_undo_snapshot()
        if 0 <= idx < len(self._profile_data):
            self._profile_data.pop(idx)
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

    # ── B18: Undo / Redo helpers ──────────────────────────────────────────
    def _push_undo_snapshot(self):
        """Save a deep copy of the current profile data onto the undo stack."""
        import copy
        snapshot = copy.deepcopy(self._profile_data)
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        # Any new mutation clears the redo stack
        self._redo_stack.clear()

    def _profile_undo(self):
        """Restore the previous profile table state."""
        import copy
        if not self._undo_stack:
            return
        # Push current state to redo before restoring
        self._redo_stack.append(copy.deepcopy(self._profile_data))
        self._profile_data = self._undo_stack.pop()
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

    def _profile_redo(self):
        """Re-apply the last undone change."""
        import copy
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._profile_data))
        self._profile_data = self._redo_stack.pop()
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

    # ── B3: Move row up / down ────────────────────────────────────────────
    def _get_selected_row_idx(self) -> int:
        """Return the currently selected row index, or -1 if none."""
        idx = -1
        try:
            selected = self._profile_grid.get_currently_selected()
            if hasattr(selected, 'rows') and selected.rows:  # type: ignore[union-attr]
                idx = int(list(selected.rows)[0])  # type: ignore[union-attr]
            elif hasattr(selected, 'row') and selected.row is not None:  # type: ignore[union-attr]
                idx = int(selected.row)  # type: ignore[union-attr]
        except Exception:
            pass
        if idx < 0:
            try:
                sel_rows = self._profile_grid.get_selected_rows()
                if sel_rows:
                    idx = int(min(sel_rows))  # type: ignore[arg-type]
            except Exception:
                pass
        return idx

    def _profile_move_up(self):
        """Swap the selected row with the one above it."""
        idx = self._get_selected_row_idx()
        if idx <= 0 or idx >= len(self._profile_data):
            return
        self._push_undo_snapshot()
        self._profile_data[idx], self._profile_data[idx - 1] = (
            self._profile_data[idx - 1], self._profile_data[idx]
        )
        self._refresh_profile_grid()
        # Re-select the moved row
        try:
            self._profile_grid.select_row(idx - 1)
        except Exception:
            pass

    def _profile_move_down(self):
        """Swap the selected row with the one below it."""
        idx = self._get_selected_row_idx()
        if idx < 0 or idx >= len(self._profile_data) - 1:
            return
        self._push_undo_snapshot()
        self._profile_data[idx], self._profile_data[idx + 1] = (
            self._profile_data[idx + 1], self._profile_data[idx]
        )
        self._refresh_profile_grid()
        try:
            self._profile_grid.select_row(idx + 1)
        except Exception:
            pass

    # ── B2: Right-click context menu ──────────────────────────────────────
    def _profile_context_menu(self, event):
        """Show a context menu on right-click in the profile grid."""
        idx = self._get_selected_row_idx()
        menu = tk.Menu(self, tearoff=0)

        # Row operations (only if a row is selected)
        has_row = 0 <= idx < len(self._profile_data)
        menu.add_command(
            label="✏️  Edit Cell",
            command=lambda: self._profile_grid.open_cell(event) if has_row else None,
            state="normal" if has_row else "disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="➕  Add Row Below",
            command=self._profile_add_row,
        )
        menu.add_command(
            label="📋  Duplicate Row",
            command=lambda: self._profile_duplicate_row(idx),
            state="normal" if has_row else "disabled",
        )
        menu.add_command(
            label="🗑️  Delete Row",
            command=self._profile_remove_row,
            state="normal" if has_row else "disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="⬆️  Move Up",
            command=self._profile_move_up,
            state="normal" if has_row and idx > 0 else "disabled",
        )
        menu.add_command(
            label="⬇️  Move Down",
            command=self._profile_move_down,
            state="normal" if has_row and idx < len(self._profile_data) - 1 else "disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="↩️  Undo  (Ctrl+Z)",
            command=self._profile_undo,
            state="normal" if self._undo_stack else "disabled",
        )
        menu.add_command(
            label="↪️  Redo  (Ctrl+Y)",
            command=self._profile_redo,
            state="normal" if self._redo_stack else "disabled",
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _profile_duplicate_row(self, idx: int):
        """Duplicate the row at the given index."""
        import copy
        if idx < 0 or idx >= len(self._profile_data):
            return
        self._push_undo_snapshot()
        new_row = copy.deepcopy(self._profile_data[idx])
        # Clear MID to avoid duplicate MID issues
        new_row["MID"] = ""
        self._profile_data.insert(idx + 1, new_row)
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

    # ── B16: Real-time validation ─────────────────────────────────────────
    def _validate_profile_realtime(self):
        """Check profile data for common issues and update the indicator."""
        if not self._profile_data:
            self._validation_indicator.configure(text="")
            return

        issues: List[str] = []

        # Check for empty MIDs
        empty_mids = sum(1 for r in self._profile_data
                         if not r.get("MID", "").strip())
        if empty_mids:
            issues.append(f"{empty_mids} row(s) missing MID")

        # Check for duplicate MIDs
        mids = [r.get("MID", "").strip().upper()
                for r in self._profile_data if r.get("MID", "").strip()]
        seen = set()
        dupes = set()
        for m in mids:
            if m in seen:
                dupes.add(m)
            seen.add(m)
        if dupes:
            issues.append(f"Duplicate MID(s): {', '.join(sorted(dupes))}")

        # Check for empty Steps
        empty_steps = sum(1 for r in self._profile_data
                          if not r.get("Step", "").strip())
        if empty_steps:
            issues.append(f"{empty_steps} row(s) missing Step")

        if issues:
            self._validation_indicator.configure(
                text="⚠ " + " | ".join(issues),
                foreground="#a80000",
            )
        else:
            self._validation_indicator.configure(
                text="✓ All rows valid",
                foreground="#107c10",
            )

    def _refresh_profile_grid(self):
        # Auto-populate hardware configuration based on Step and Form_Factor
        from model.hardware_config import HardwareConfig
        hw_config = HardwareConfig()

        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        sheet_data = []
        for idx, row_dict in enumerate(self._profile_data):
            # Auto-populate hardware config if Step and Form_Factor are present
            step = row_dict.get("Step", "").strip()
            form_factor = row_dict.get("Form_Factor", "").strip()
            if step and form_factor:
                if not row_dict.get("DIB_TYPE", "").strip():
                    row_dict["DIB_TYPE"] = hw_config.get_dib_type(step, form_factor)
                if not row_dict.get("MACHINE_MODEL", "").strip():
                    row_dict["MACHINE_MODEL"] = hw_config.get_machine_model(step)
                if not row_dict.get("MACHINE_VENDOR", "").strip():
                    row_dict["MACHINE_VENDOR"] = hw_config.get_machine_vendor(step)

            values = [str(row_dict.get(col, "")) for col in cols]
            sheet_data.append(values)

        self._profile_grid.set_sheet_data(sheet_data, reset_col_positions=False)
        # Re-enable bindings after set_sheet_data (some tksheet versions reset them)
        self._profile_grid.enable_bindings(
            "single_select", "row_select",
            "column_width_resize", "double_click_column_resize",
            "arrowkeys", "copy", "paste", "undo", "edit_cell",
        )
        # Re-bind extra bindings (cell edit callbacks + cell select callback)
        self._profile_grid.extra_bindings("begin_edit_cell", self._on_sheet_begin_edit)
        self._profile_grid.extra_bindings("end_edit_cell", self._on_sheet_cell_edited)
        self._profile_grid.extra_bindings("cell_select", self._on_sheet_cell_select)
        # B20: Re-bind Ctrl+V for bulk paste sync
        self._profile_grid.bind("<Control-v>", self._on_sheet_paste)
        self._profile_grid.bind("<Control-V>", self._on_sheet_paste)
        self._profile_grid.set_all_column_widths()
        # Re-apply column widths
        for i, (_, w) in enumerate(self._PROFILE_GEN_COLUMNS):
            self._profile_grid.column_width(column=i, width=w)
        # Re-apply editable column highlighting
        editable_indices = [i for i, (c, _) in enumerate(self._PROFILE_GEN_COLUMNS)
                           if c in self._EDITABLE_COLS]
        for col_idx in editable_indices:
            try:
                self._profile_grid.highlight_columns(
                    columns=col_idx, bg="#e8f0fe", fg="#000000")
            except Exception:
                pass
        # B19: Row validation indicators — highlight incomplete rows only.
        # Complete rows use default white bg (column highlights still apply).
        for idx, row_dict in enumerate(self._profile_data):
            mid  = row_dict.get("MID", "").strip()
            step = row_dict.get("Step", "").strip()
            if not mid or not step:
                # Incomplete row — light red background
                try:
                    self._profile_grid.highlight_rows(
                        rows=idx, bg="#fde8e8", fg="#a80000")
                except Exception:
                    pass
            else:
                # Complete row — clear any previous red highlight
                try:
                    self._profile_grid.dehighlight_rows(rows=[idx])
                except Exception:
                    pass
        self._profile_grid.refresh()
        self._profile_row_count_label.configure(
            text=f"{len(self._profile_data)} row(s)")

    def _update_profile_status(self):
        count = len(self._profile_data)
        if count == 0:
            self._profile_status_label.configure(
                text="No data \u2014 use 'Add Row' to populate",
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
        self._push_undo_snapshot()
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

    def _on_sheet_begin_edit(self, event):
        """Called by tksheet BEFORE the text editor opens.

        If the column is ATTR_OVERWRITE, cancel the inline editor and
        open the dialog form instead.  Return ``None`` to cancel,
        or the pre-fill text to allow normal editing.

        If the column is Dummy_Lot and the cell already has a value,
        trigger the lot lookup immediately (auto-fill CFGPN/MCTO/etc.)
        so the user doesn't have to re-type the lot to trigger it.
        """
        try:
            row_idx = int(event.row) if event.row is not None else -1
            col_idx = int(event.column) if event.column is not None else -1
        except (TypeError, ValueError):
            return event.value  # allow edit
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        if 0 <= col_idx < len(cols) and cols[col_idx] == "ATTR_OVERWRITE":
            if 0 <= row_idx < len(self._profile_data):
                self.after(50, lambda r=row_idx: self._show_attr_overwrite_dialog(r))
            return None  # cancel inline text editor

        # Auto-trigger lot lookup when double-clicking a pre-filled Dummy_Lot cell
        if 0 <= col_idx < len(cols) and cols[col_idx] == "Dummy_Lot":
            if 0 <= row_idx < len(self._profile_data):
                existing_lot = self._profile_data[row_idx].get("Dummy_Lot", "").strip()
                existing_cfgpn = self._profile_data[row_idx].get("CFGPN", "").strip()
                # Only auto-lookup if lot exists but CFGPN is still empty
                if existing_lot and existing_lot.upper() not in ("", "NONE") and not existing_cfgpn:
                    self.after(100, lambda r=row_idx, l=existing_lot: self._trigger_lot_lookup(r, l))

        return event.value  # allow normal editing

    def _on_sheet_cell_edited(self, event):
        """Called by tksheet when a cell edit is completed.

        In tksheet 7.6.0 the event is an ``EventDataDict`` with:
        - ``row`` / ``column`` — display row/col indices (int)
        - ``value`` — the new cell value after editing
        - ``text`` is NOT present; use ``value`` instead.
        """
        try:
            row_idx = int(event.row) if event.row is not None else -1
            col_idx = int(event.column) if event.column is not None else -1
        except (TypeError, ValueError):
            return
        # In tksheet 7.x the new value is in event.value
        new_value = str(event.value) if event.value is not None else ""
        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        if row_idx < 0 or row_idx >= len(self._profile_data):
            return
        if col_idx < 0 or col_idx >= len(cols):
            return
        col_name = cols[col_idx]

        # ATTR_OVERWRITE is handled by _on_sheet_begin_edit (dialog opens
        # before the text editor), so this should not fire for that column.
        # Guard just in case:
        if col_name == "ATTR_OVERWRITE":
            return

        # B18: Save undo snapshot before mutation
        self._push_undo_snapshot()
        self._profile_data[row_idx][col_name] = new_value.strip()
        # Auto-lookup CFGPN/MCTO when Dummy_Lot is edited
        if col_name == "Dummy_Lot" and new_value.strip() and new_value.strip().upper() not in ("", "NONE"):
            self._trigger_lot_lookup(row_idx, new_value.strip())
        # Duplicate MID check when MID is edited
        if col_name == "MID" and new_value.strip() and new_value.strip().upper() not in ("", "NONE"):
            if self._has_duplicate_mid(new_value.strip()):
                messagebox.showwarning(
                    "Duplicate MID",
                    f"MID '{new_value.strip()}' already exists in another row.\n"
                    f"Duplicate MIDs will generate conflicting profiles.")
        # B16: Update validation indicator after edit
        self._validate_profile_realtime()

    # ── B20: Bulk paste from Excel ───────────────────────────────────────
    def _on_sheet_paste(self, event=None):
        """Intercept Ctrl+V to handle multi-row paste from Excel clipboard.

        Excel copies cells as tab-separated columns and newline-separated
        rows.  This handler:
        1. Reads the clipboard text.
        2. Parses it into rows × columns.
        3. Writes values into ``_profile_data`` starting at the currently
           selected cell, adding new rows if the paste extends beyond the
           existing data.
        4. Refreshes the grid and triggers validation.

        Returns ``"break"`` to prevent tksheet's built-in paste from
        also firing.
        """
        try:
            clipboard_text = self._profile_grid.clipboard_get()
        except tk.TclError:
            return "break"  # nothing on clipboard

        if not clipboard_text or not clipboard_text.strip():
            return "break"

        # ── Determine paste origin (selected cell) ───────────────────
        try:
            sel = self._profile_grid.get_currently_selected()
            if sel and hasattr(sel, 'row') and sel.row is not None:
                start_row = int(sel.row)
                start_col = int(sel.column) if sel.column is not None else 0
            else:
                start_row, start_col = 0, 0
        except Exception:
            start_row, start_col = 0, 0

        cols = [c for c, _ in self._PROFILE_GEN_COLUMNS]
        num_cols = len(cols)

        # ── Parse clipboard (tab-separated columns, newline-separated rows)
        lines = clipboard_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = lines.rstrip("\n")  # remove trailing blank line
        rows_data = [line.split("\t") for line in lines.split("\n")]

        if not rows_data:
            return "break"

        # ── Save undo snapshot before bulk mutation ───────────────────
        self._push_undo_snapshot()

        # ── Write pasted data into _profile_data ─────────────────────
        for r_offset, row_values in enumerate(rows_data):
            target_row = start_row + r_offset

            # Add new rows if paste extends beyond existing data
            while target_row >= len(self._profile_data):
                new_row = {c: "" for c, _ in self._PROFILE_GEN_COLUMNS}
                self._profile_data.append(new_row)

            for c_offset, cell_value in enumerate(row_values):
                target_col = start_col + c_offset
                if target_col >= num_cols:
                    break  # ignore columns beyond grid width

                col_name = cols[target_col]
                # Only paste into editable columns
                if col_name not in self._EDITABLE_COLS:
                    continue
                # Skip ATTR_OVERWRITE — managed via dialog
                if col_name == "ATTR_OVERWRITE":
                    continue

                self._profile_data[target_row][col_name] = cell_value.strip()

        # ── Refresh grid and validate ────────────────────────────────
        self._refresh_profile_grid()
        self._update_profile_status()
        self._validate_profile_realtime()

        # Show feedback
        n_rows = len(rows_data)
        n_cols_pasted = max(len(r) for r in rows_data) if rows_data else 0
        self._profile_status_label.configure(
            text=f"Pasted {n_rows} row(s) × {n_cols_pasted} column(s) "
                 f"starting at row {start_row + 1}",
            foreground="#107c10",
        )

        return "break"  # prevent tksheet built-in paste

    def _on_sheet_cell_select(self, event):
        """Called by tksheet when a cell is selected — update status bar.

        In tksheet 7.6.0 the *cell_select* event does NOT populate
        ``event.row`` / ``event.column`` (they are ``None``).  The
        selected cell coordinates live in ``event.being_selected``
        which is a ``Box_t(r1, c1, r2, c2, type)`` tuple, or an
        empty tuple when nothing is selected.
        """
        try:
            # Try being_selected first (tksheet 7.x)
            bs = event.being_selected  # type: ignore[union-attr]
            if bs and len(bs) >= 2:
                row_idx = int(bs[0])
            elif event.row is not None:
                row_idx = int(event.row)
            else:
                row_idx = -1
        except Exception:
            row_idx = -1

        if row_idx < 0 or row_idx >= len(self._profile_data):
            self._profile_status_label.configure(
                text="No row selected", foreground="#666666")
            return
        row_dict = self._profile_data[row_idx]
        cfgpn     = row_dict.get("CFGPN", "")
        dummy_lot = row_dict.get("Dummy_Lot", "")
        mid       = row_dict.get("MID", "")
        step      = row_dict.get("Step", "")
        tester    = row_dict.get("Tester", "")
        self._profile_status_label.configure(
            text=(f"Selected: CFGPN={cfgpn}  Lot={dummy_lot}  "
                  f"MID={mid}  Step={step}  Tester={tester}"),
            foreground="#0078d4")

    def _get_default_temptraveler_attrs(self, row_idx):
        """Build default TempTraveler attributes for a profile row.

        These mirror the attributes that generate_slate_xml() would create
        automatically (MAM/STEP, CFGPN/STEP_ID, EQUIPMENT/DIB_TYPE,
        EQUIPMENT/DIB_TYPE_NAME, RECIPE_SELECTION/RECIPE_SEL_TEST_PROGRAM_PATH).
        Pre-populating them in the ATTR_OVERWRITE dialog lets the user
        inspect, edit, or remove any of them before XML generation.
        """
        from model.hardware_config import get_hardware_config

        row = self._profile_data[row_idx]
        step = str(row.get("Step", row.get("STEP", "ABIT"))).strip().upper() or "ABIT"
        form_factor = str(row.get("Form_Factor", row.get("FORM_FACTOR", ""))).strip()

        hw = get_hardware_config()
        mam_step, cfgpn_step_id = hw.get_step_names(step)
        dib_type = row.get("DIB_TYPE", "").strip()
        if not dib_type:
            dib_type = hw.get_dib_type(step, form_factor) if form_factor else ""

        # TGZ path for RECIPE_SEL_TEST_PROGRAM_PATH
        tgz_path = ""
        try:
            tgz_path = self.context.get_var('checkout_tgz_path').get().strip()
        except Exception:
            pass

        defaults = [
            ("MAM",              "STEP",                          mam_step),
            ("CFGPN",            "STEP_ID",                       cfgpn_step_id),
            ("EQUIPMENT",        "DIB_TYPE",                      dib_type),
            ("EQUIPMENT",        "DIB_TYPE_NAME",                 dib_type),
        ]
        if tgz_path:
            # Normalize to UNC-style uppercase path (matches orchestrator)
            norm_tgz = tgz_path.replace("/", "\\").upper()
            defaults.append(
                ("RECIPE_SELECTION", "RECIPE_SEL_TEST_PROGRAM_PATH", norm_tgz)
            )
        return defaults

    def _show_attr_overwrite_dialog(self, row_idx):
        current_value = self._profile_data[row_idx].get("ATTR_OVERWRITE", "")

        dialog = tk.Toplevel(self.winfo_toplevel())
        dialog.title("TempTraveler Attribute Editor")
        dialog.geometry("700x580")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        # Help text
        help_frm = ttk.Frame(dialog)
        help_frm.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(help_frm,
                  text="Edit TempTraveler attributes for this profile row.  "
                       "Default attributes are auto-filled from the row's Step "
                       "and Form Factor.\n"
                       "• Double-click a row to edit its value inline.\n"
                       "• Remove optional attributes you don't need.\n"
                       "• Add custom attributes with the fields below.",
                  foreground="#555555", font=("Segoe UI", 8),
                  wraplength=660, justify=tk.LEFT).pack(anchor="w")

        # Input fields for adding new entries
        input_frame = ttk.LabelFrame(dialog, text="Add / Edit Attribute", padding="8")
        input_frame.pack(fill=tk.X, padx=10, pady=(4, 4))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Section:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        section_var = tk.StringVar()
        section_cb = ttk.Combobox(input_frame, textvariable=section_var,
                     values=["MAM", "MCTO", "CFGPN", "EQUIPMENT",
                             "RECIPE_SELECTION", "DRIVE_INFO", "RAW_VALUES"],
                     width=22)
        section_cb.grid(row=0, column=1, sticky="we", pady=2)
        section_cb.set("")
        _tip(section_cb, "TempTraveler section name.\n"
                         "Common: MAM, CFGPN, EQUIPMENT, RECIPE_SELECTION.")

        ttk.Label(input_frame, text="Attr Name:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        attr_name_var = tk.StringVar()
        attr_name_entry = ttk.Entry(input_frame, textvariable=attr_name_var)
        attr_name_entry.grid(row=1, column=1, sticky="we", pady=2)
        _tip(attr_name_entry, "Attribute name (e.g. STEP, DIB_TYPE, NAND_OPTION).")

        ttk.Label(input_frame, text="Attr Value:").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        attr_value_var = tk.StringVar()
        attr_value_entry = ttk.Entry(input_frame, textvariable=attr_value_var)
        attr_value_entry.grid(row=2, column=1, sticky="we", pady=2)
        _tip(attr_value_entry, "The value for the attribute.")

        # Entries grid
        entries_frame = ttk.LabelFrame(dialog, text="TempTraveler Attributes", padding="8")
        entries_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        entry_cols = ["Section", "Attr Name", "Attr Value"]
        entries_tree = ttk.Treeview(entries_frame, columns=entry_cols,
                                    show="headings", height=9, selectmode="browse")
        for col in entry_cols:
            entries_tree.heading(col, text=col, anchor=tk.W)
        entries_tree.column("Section",    width=140, stretch=False, anchor=tk.W)
        entries_tree.column("Attr Name",  width=200, stretch=True,  anchor=tk.W)
        entries_tree.column("Attr Value", width=300, stretch=True,  anchor=tk.W)

        # Scrollbar
        tree_scroll = ttk.Scrollbar(entries_frame, orient=tk.VERTICAL,
                                    command=entries_tree.yview)
        entries_tree.configure(yscrollcommand=tree_scroll.set)
        entries_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ── Populate entries ─────────────────────────────────────────────
        # Parse existing ATTR_OVERWRITE value into a dict for quick lookup
        existing_attrs = {}  # (section, name) -> value
        if current_value:
            parts = current_value.split(";")
            for i in range(0, len(parts), 3):
                if i + 2 < len(parts):
                    s, n, v = parts[i].strip(), parts[i+1].strip(), parts[i+2].strip()
                    existing_attrs[(s, n)] = v

        # Get default TempTraveler attributes
        defaults = self._get_default_temptraveler_attrs(row_idx)

        # Merge: user values override defaults
        merged = []
        seen_keys = set()
        for sect, name, default_val in defaults:
            key = (sect, name)
            val = existing_attrs.pop(key, default_val)
            merged.append((sect, name, val))
            seen_keys.add(key)

        # Append any remaining user-added attributes not in defaults
        for (sect, name), val in existing_attrs.items():
            merged.append((sect, name, val))

        for sect, name, val in merged:
            entries_tree.insert("", tk.END, values=(sect, name, val))

        # ── Double-click to edit value inline ────────────────────────────
        def _on_double_click(event):
            item = entries_tree.identify_row(event.y)
            col = entries_tree.identify_column(event.x)
            if not item:
                return
            # Only allow editing the "Attr Value" column (#3)
            col_idx = int(col.replace("#", "")) - 1
            if col_idx != 2:
                return
            vals = entries_tree.item(item, "values")
            if not vals or len(vals) < 3:
                return

            # Get cell bounding box
            bbox = entries_tree.bbox(item, col)
            if not bbox:
                return
            x, y, w, h = bbox

            # Create inline edit entry
            edit_var = tk.StringVar(value=vals[2])
            edit_entry = ttk.Entry(entries_tree, textvariable=edit_var)
            edit_entry.place(x=x, y=y, width=w, height=h)
            edit_entry.focus_set()
            edit_entry.select_range(0, tk.END)

            def _commit(e=None):
                new_val = edit_var.get().strip()
                entries_tree.item(item, values=(vals[0], vals[1], new_val))
                edit_entry.destroy()

            def _cancel_edit(e=None):
                edit_entry.destroy()

            edit_entry.bind("<Return>", _commit)
            edit_entry.bind("<FocusOut>", _commit)
            edit_entry.bind("<Escape>", _cancel_edit)

        entries_tree.bind("<Double-1>", _on_double_click)

        # ── Select entry → populate input fields for reference ───────────
        def _on_tree_select(event):
            sel = entries_tree.selection()
            if sel:
                vals = entries_tree.item(sel[0], "values")
                if vals and len(vals) >= 3:
                    section_var.set(vals[0])
                    attr_name_var.set(vals[1])
                    attr_value_var.set(vals[2])

        entries_tree.bind("<<TreeviewSelect>>", _on_tree_select)

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=(4, 10))

        def _add_or_update():
            section = section_var.get().strip()
            attr = attr_name_var.get().strip()
            value = attr_value_var.get().strip()

            if not section:
                messagebox.showwarning("Validation", "Section name is required", parent=dialog)
                return
            if not attr:
                messagebox.showwarning("Validation", "Attribute name is required", parent=dialog)
                return

            # Check if entry with same section+attr already exists → update it
            for child in entries_tree.get_children():
                vals = entries_tree.item(child, "values")
                if vals[0] == section and vals[1] == attr:
                    entries_tree.item(child, values=(section, attr, value))
                    section_var.set("")
                    attr_name_var.set("")
                    attr_value_var.set("")
                    return

            entries_tree.insert("", tk.END, values=(section, attr, value))
            section_var.set("")
            attr_name_var.set("")
            attr_value_var.set("")

        def _remove_entry():
            sel = entries_tree.selection()
            if sel:
                entries_tree.delete(sel[0])

        def _reset_defaults():
            """Clear all and re-populate with defaults."""
            for child in entries_tree.get_children():
                entries_tree.delete(child)
            fresh_defaults = self._get_default_temptraveler_attrs(row_idx)
            for sect, name, val in fresh_defaults:
                entries_tree.insert("", tk.END, values=(sect, name, val))

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

        ttk.Button(btn_frame, text="Add / Update", command=_add_or_update).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Remove",       command=_remove_entry).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Reset Defaults", command=_reset_defaults).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn_frame, text="Save",         command=_save).pack(side=tk.RIGHT, padx=(3, 0))
        ttk.Button(btn_frame, text="Cancel",       command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 3))

    # _on_profile_single_click and _on_profile_row_select are replaced by
    # _on_sheet_cell_select (tksheet handles selection natively)

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

        # B8: Confirmation dialog before checkout start
        hostnames = [h for h, v in self._tester_vars.items() if v.get()]
        row_count = len(self._profile_data)
        tgz_name = os.path.basename(
            self.context.get_var('checkout_tgz_path').get().strip())
        summary = (
            f"Ready to start checkout?\n\n"
            f"  Testers:  {', '.join(hostnames)}\n"
            f"  Profiles: {row_count} row(s)\n"
            f"  TGZ:      {tgz_name}\n\n"
            f"This will generate XML profiles and deploy to all selected testers."
        )
        if not messagebox.askyesno("Confirm Checkout", summary, icon='question'):
            return

        self._manifest_poll_started = False   # reset for new run
        self._set_checkout_state(CheckoutState.RUNNING)
        self.context.controller.checkout_controller.start_checkout(params)

    def _stop_checkout(self):
        """
        Stop the active checkout run with confirmation dialog.
        Prevents accidental stops and logs the user action.
        Works during both RUNNING and COLLECTING states.
        """
        # Confirmation dialog for safety — allow stop during RUNNING or COLLECTING
        if self._checkout_state not in (CheckoutState.RUNNING, CheckoutState.COLLECTING):
            return
            
        response = messagebox.askyesno(
            "Stop Checkout",
            "Stop the active checkout run?\n\n"
            "This will terminate the checkout process on all testers.\n"
            "Are you sure you want to continue?",
            icon='warning'
        )
        
        if not response:
            return
        
        # Log the stop action
        self.log("⏹ User requested checkout stop")
        
        # Set state to STOPPING to disable buttons during cleanup
        self._set_checkout_state(CheckoutState.STOPPING)
        
        # Trigger the actual stop on the checkout orchestrator
        self.context.controller.checkout_controller.stop_checkout()

        # Also stop the result-collector monitoring loop (if running)
        self._rc_stop_monitoring()

        # Item 9: Use a delayed fallback instead of instant IDLE transition.
        # The completion callback (on_checkout_completed) will set COMPLETED
        # when the orchestrator finishes cleanly.  If the callback never
        # fires (e.g. user cancels mid-monitoring), this timeout ensures
        # the UI doesn't stay stuck in STOPPING forever.
        self.after(5000, self._stop_fallback_to_idle)

    def _stop_fallback_to_idle(self):
        """Transition STOPPING → IDLE after a timeout, but only if the
        completion callback hasn't already moved us to COMPLETED/ERROR."""
        if self._checkout_state == CheckoutState.STOPPING:
            self.log("⏹ Stop timeout — resetting to IDLE")
            self._set_checkout_state(CheckoutState.IDLE)

    def _collect_params(self):
        from model.checkout_params import (
            CheckoutParams, TestCaseConfig,
            parse_attr_overwrite_string, ProfileRowParams,
        )
        from pydantic import ValidationError

        # Get site from global site selection
        from model.site_paths import get_site_resolver
        site = get_site_resolver().current_site
        
        tgz_path    = self.context.get_var('checkout_tgz_path').get().strip()
        hot_folder  = self.context.get_var('checkout_hot_folder').get().strip()
        method      = self.context.get_var('checkout_detect_method').get()
        timeout_m   = self.context.get_var('checkout_timeout_min').get()
        notify      = self.context.get_var('checkout_notify_teams').get()
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
                dib_type=row.get("DIB_TYPE", ""),
                machine_model=row.get("MACHINE_MODEL", ""),
                machine_vendor=row.get("MACHINE_VENDOR", ""),
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
        self._validate_profile_realtime()
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
                "DIB_TYPE": "", "MACHINE_MODEL": "", "MACHINE_VENDOR": "",
                "Primitive": "", "Dut": "", "ATTR_OVERWRITE": "",
            })
        if not self._profile_data:
            self._profile_add_default_row()
        self._refresh_profile_grid()
        self._sync_tester_column()
        self._update_profile_status()
        self._validate_profile_realtime()
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
            # Get site from global site selection
            from model.site_paths import get_site_resolver
            site = get_site_resolver().current_site
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
                    "DIB_TYPE":       "",
                    "MACHINE_MODEL":  "",
                    "MACHINE_VENDOR": "",
                    "Primitive":      mrow.get("primitive", ""),
                    "Dut":            mrow.get("dut", ""),
                    "ATTR_OVERWRITE": attr_overwrite,
                })
            self._refresh_profile_grid()
            self._sync_tester_column()
            self._update_profile_status()
            self._validate_profile_realtime()

            # Auto-trigger lot lookup for rows with lot but no CFGPN
            for idx, row in enumerate(self._profile_data):
                lot = row.get("Dummy_Lot", "").strip()
                cfgpn = row.get("CFGPN", "").strip()
                if lot and lot.upper() not in ("", "NONE") and not cfgpn:
                    # Stagger lookups slightly to avoid flooding MAM
                    self.after(200 * (idx + 1),
                               lambda r=idx, l=lot: self._trigger_lot_lookup(r, l))

        # ── Log summary ───────────────────────────────────────────────
        filled = []
        if data.get("tgz_path"):   filled.append("TGZ")
        if data.get("env"):        filled.append(f"ENV={data['env']}")
        if material_rows:
            filled.append(f"{len(material_rows)} MID(s)")
            # Show full lot from material_rows (lot_prefix may strip digits)
            lots = list({r.get("lot", "") for r in material_rows if r.get("lot", "")})
            if lots:
                filled.append(f"Lot={'|'.join(lots)}")
            elif data.get("lot_prefix"):
                filled.append(f"Lot={data['lot_prefix']}")
        else:
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
            # Transition state machine so _poll_for_manifest() guard
            # allows continued polling (it rejects RUNNING state).
            if self._checkout_state == CheckoutState.RUNNING:
                self._set_checkout_state(CheckoutState.COLLECTING)
            # Start manifest polling as soon as we know the watcher is
            # collecting — don't wait for on_checkout_completed() because
            # wait_for_checkout() now keeps polling until "success"/"failed"
            # (which only happens AFTER user selects files).
            if not getattr(self, '_manifest_poll_started', False):
                self._manifest_poll_started = True
                self._start_manifest_polling(hostname)
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
            # Determine final state based on results
            # "collecting" means SLATE succeeded and watcher is gathering
            # workspace files — treat it as success for UI purposes so
            # manifest polling can start immediately.
            any_success = any(
                r.get("status", "").lower() in (
                    "success", "partial", "collecting"
                )
                for r in self._rc_checkout_results.values()
            )
            if any_success:
                self._set_checkout_state(CheckoutState.COMPLETED)
            else:
                self._set_checkout_state(CheckoutState.ERROR)

            # ── Task 2: Auto-start result collection ──────────────────
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
                # Guard: if on_checkout_progress() already started polling
                # (via the "collecting" phase callback), skip here.
                if not self._manifest_poll_started:
                    self._start_manifest_polling(primary_host)

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
            # Determine final state based on XML generation result
            if status in ("SUCCESS", "XML_SUCCESS"):
                self._set_checkout_state(CheckoutState.COMPLETED)
            else:
                self._set_checkout_state(CheckoutState.ERROR)

    def get_profile_table_data(self) -> List[Dict]:
        return self._profile_data.copy()

    # ──────────────────────────────────────────────────────────────────────
    # SECTION 6 — Test Progress (Result Collection)
    # ──────────────────────────────────────────────────────────────────────

    def _build_test_progress_section(self, parent, row):
        """Build the auto-detect test progress section with MID status treeview."""
        # ── Header Frame with Title and Runtime Controls ─────────────────
        header_container = ttk.Frame(parent)
        header_container.grid(row=row, column=0, sticky="we", pady=(0, 0))
        header_container.columnconfigure(0, weight=1)
        
        # Create the LabelFrame with custom header
        frm = ttk.LabelFrame(header_container, text="", padding=(8, 6, 8, 8))
        frm.grid(row=0, column=0, sticky="we")
        frm.columnconfigure(0, weight=1)
        
        # ── Custom Header: Title + Status + Runtime Buttons ──────────────
        header_frm = ttk.Frame(frm)
        header_frm.grid(row=0, column=0, sticky="we", pady=(0, 8))
        header_frm.columnconfigure(1, weight=1)
        
        # Left: Title
        ttk.Label(header_frm, text="Test Progress (Auto-Detect)",
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 15))
        
        # Middle: Status indicator
        self._checkout_status_label = ttk.Label(
            header_frm, text="Status: IDLE",
            font=("Segoe UI", 9), foreground="#888888")
        self._checkout_status_label.pack(side=tk.LEFT, padx=(0, 20))
        
        # Right: Runtime control buttons
        btn_container = ttk.Frame(header_frm)
        btn_container.pack(side=tk.RIGHT)
        
        self._rc_refresh_btn = ttk.Button(
            btn_container, text="🔄 Refresh", width=10,
            command=self._rc_refresh_status)
        self._rc_refresh_btn.pack(side=tk.LEFT, padx=(0, 3))
        _tip(self._rc_refresh_btn, "Refresh tester status")
        
        self._rc_spool_btn = ttk.Button(
            btn_container, text="📊 Spool", width=10,
            command=self._rc_spool_selected)
        self._rc_spool_btn.pack(side=tk.LEFT, padx=(0, 10))
        _tip(self._rc_spool_btn, "Spool/prepare logs or artifacts")
        
        # B9: Stop button removed from here — now in action buttons section

        # ── Summary bar (MID status summary) ──────────────────────────────
        summary_frm = ttk.Frame(frm)
        summary_frm.grid(row=1, column=0, sticky="we", pady=(0, 4))

        self._rc_status_indicator = ttk.Label(
            summary_frm, text="\u23f8 Idle", font=("Segoe UI", 9, "bold"))
        self._rc_status_indicator.pack(side=tk.LEFT, padx=(0, 12))

        self._rc_summary_label = ttk.Label(
            summary_frm, text="", font=("Segoe UI", 8))
        self._rc_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # B10: Progress bar for test progress
        self._checkout_progress = ttk.Progressbar(
            frm, orient=tk.HORIZONTAL, mode='determinate', length=400)
        self._checkout_progress.grid(row=2, column=0, sticky="we", pady=(0, 6))
        self._checkout_progress['value'] = 0

        # ── Result Collector Grid ─────────────────────────────────────
        frm.rowconfigure(3, weight=1)

        columns = ("mid", "location", "name", "status", "fail_info",
                   "collected", "error")
        # B11: Increased treeview height from 5 to 8 for better visibility
        self._rc_tree = ttk.Treeview(
            frm, columns=columns, show="headings",
            selectmode="extended", height=8)

        self._rc_tree.heading("mid",       text="MID")
        self._rc_tree.heading("location",  text="Location")
        self._rc_tree.heading("name",      text="Name")
        self._rc_tree.heading("status",    text="Status")
        self._rc_tree.heading("fail_info", text="Fail Info")
        self._rc_tree.heading("collected", text="Collected")
        self._rc_tree.heading("error",     text="Error")

        self._rc_tree.column("mid",       width=90,  minwidth=60)
        self._rc_tree.column("location",  width=50,  minwidth=40)
        self._rc_tree.column("name",      width=120, minwidth=80)
        self._rc_tree.column("status",    width=70,  minwidth=50)
        self._rc_tree.column("fail_info", width=150, minwidth=80)
        self._rc_tree.column("collected", width=60,  minwidth=40)
        self._rc_tree.column("error",     width=150, minwidth=80)

        rc_vsb = ttk.Scrollbar(frm, orient=tk.VERTICAL, command=self._rc_tree.yview)
        self._rc_tree.configure(yscrollcommand=rc_vsb.set)

        self._rc_tree.grid(row=3, column=0, sticky="nsew")
        rc_vsb.grid(row=3, column=1, sticky="ns")

        # Row colour tags
        self._rc_tree.tag_configure("pass",    foreground="green")
        self._rc_tree.tag_configure("fail",    foreground="red")
        self._rc_tree.tag_configure("running", foreground="blue")
        self._rc_tree.tag_configure("unknown", foreground="gray")
        self._rc_tree.tag_configure("error",   foreground="orange")

    # ──────────────────────────────────────────────────────────────────────
    # STATE MACHINE — Centralized UI State Management
    # ──────────────────────────────────────────────────────────────────────

    def _set_checkout_state(self, new_state: CheckoutState):
        """
        Set the checkout state and update all UI elements accordingly.
        Single source of truth for button enable/disable logic.
        """
        self._checkout_state = new_state
        self.update_checkout_ui_state()

    def update_checkout_ui_state(self):
        """
        Update all button states and status labels based on current checkout state.

        State Machine Rules (B9: toggle Start/Stop visibility):
        - IDLE: Start visible+enabled, Stop hidden, runtime buttons enabled
        - RUNNING: Start hidden, Stop visible+enabled, runtime buttons enabled
        - COLLECTING: Start hidden, Stop visible+enabled, progress bar reset
        - STOPPING: Start hidden, Stop visible+disabled, Refresh enabled
        - COMPLETED/ERROR: Start visible+enabled, Stop hidden, runtime buttons enabled
        """
        state = self._checkout_state

        # Update status label
        status_colors = {
            CheckoutState.IDLE: ("#888888", "IDLE"),
            CheckoutState.RUNNING: ("#0078d4", "RUNNING"),
            CheckoutState.COLLECTING: ("#0078d4", "COLLECTING"),
            CheckoutState.STOPPING: ("#ca5010", "STOPPING"),
            CheckoutState.COMPLETED: ("#107c10", "COMPLETED"),
            CheckoutState.ERROR: ("#a80000", "ERROR"),
        }
        color, text = status_colors.get(state, ("#888888", "UNKNOWN"))
        self._checkout_status_label.config(text=f"Status: {text}", foreground=color)

        # B9: Toggle Start/Stop button visibility
        if state == CheckoutState.IDLE:
            has_testers = any(var.get() for var in self._tester_vars.values())
            has_profile = len(self._profile_data) > 0
            self.checkout_btn.grid()  # Show Start
            self.checkout_btn.state(["!disabled"] if (has_testers and has_profile) else ["disabled"])
            self.stop_btn.grid_remove()  # Hide Stop
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["!disabled"])
            # B10: Reset progress bar
            self._checkout_progress['value'] = 0

        elif state == CheckoutState.RUNNING:
            self.checkout_btn.grid_remove()  # Hide Start
            self.stop_btn.grid()  # Show Stop
            self.stop_btn.state(["!disabled"])
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["!disabled"])

        elif state == CheckoutState.COLLECTING:
            # Checkout done, now collecting results — reset progress bar
            self.checkout_btn.grid_remove()  # Hide Start
            self.stop_btn.grid()  # Show Stop (to allow stopping collection)
            self.stop_btn.state(["!disabled"])
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["!disabled"])
            # Reset progress bar — will be driven by on_rc_progress_update()
            self._checkout_progress['value'] = 0

        elif state == CheckoutState.STOPPING:
            self.checkout_btn.grid_remove()  # Hide Start
            self.stop_btn.grid()  # Show Stop (disabled)
            self.stop_btn.state(["disabled"])
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["disabled"])

        elif state == CheckoutState.COMPLETED:
            self.checkout_btn.grid()  # Show Start
            self.checkout_btn.state(["!disabled"])
            self.stop_btn.grid_remove()  # Hide Stop
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["!disabled"])
            # B10: Fill progress bar to 100% on success
            self._checkout_progress['value'] = 100

        elif state == CheckoutState.ERROR:
            self.checkout_btn.grid()  # Show Start
            self.checkout_btn.state(["!disabled"])
            self.stop_btn.grid_remove()  # Hide Stop
            self._rc_refresh_btn.state(["!disabled"])
            self._rc_spool_btn.state(["!disabled"])
            # Reset progress bar on error — no misleading green bar
            self._checkout_progress['value'] = 0

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
        # Stop button state is now managed by update_checkout_ui_state()

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
            prim = row.get("Primitive", row.get("PRIMITIVE", "")).strip()
            dut  = row.get("Dut", row.get("DUT", "")).strip()
            if not mid:
                continue
            # Location must be in PxDy format for resolve_adv_workspaces()
            if prim and dut:
                loc_str = f"P{prim}D{dut}"
            elif prim:
                loc_str = f"P{prim}D0"
            else:
                loc_str = "P0D0"
            name_str = dut if dut else mid
            mids_lines.append(f"{mid}  {loc_str}  {name_str}  True")

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
        from model.site_paths import get_site_resolver
        site         = get_site_resolver().current_site
        webhook_url  = self.context.get_var('checkout_webhook_url').get().strip()
        notify_teams = self.context.get_var('checkout_notify_teams').get()

        # ── Start monitoring ──────────────────────────────────────────
        self.log(f"🚀 Auto-starting result collection for {hostname}...")
        self._rc_status_indicator.config(
            text="🔄 MONITORING", foreground="blue")
        # Stop button state managed by update_checkout_ui_state()
        self._set_checkout_state(CheckoutState.COLLECTING)

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
        Updates the Section 6 treeview, summary bar, and B10 progress bar.
        """
        total      = summary.get("total", 0)
        passed     = summary.get("passed", 0)
        failed     = summary.get("failed", 0)
        running    = summary.get("running", 0)
        collected  = summary.get("collected", 0)
        unresolved = summary.get("unresolved", 0)
        machine    = summary.get("machine", "")
        site       = summary.get("site", "")

        # B10: Update progress bar
        if total > 0:
            done = passed + failed
            pct = int((done / total) * 100)
            self._checkout_progress['value'] = pct
        else:
            self._checkout_progress['value'] = 0

        unresolved_str = f"  |  \u26a0 Unresolved: {unresolved}" if unresolved else ""
        self._rc_summary_label.config(
            text=(
                f"Machine: {machine}  |  Site: {site}  |  "
                f"Total: {total}  |  "
                f"\u2705 Pass: {passed}  |  \u274c Fail: {failed}  |  "
                f"\ud83d\udd04 Running: {running}  |  \ud83d\udcc1 Collected: {collected}"
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
        Note: treeview is populated by on_rc_progress_update() which the
        controller calls BEFORE this method.
        """
        # Stop button state managed by update_checkout_ui_state()
        self._set_checkout_state(CheckoutState.COMPLETED)

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

    def _start_manifest_polling(self, hostname: str):
        """
        Resolve JIRA key + ENV, build the results_base path, and kick off
        ``_poll_for_manifest()`` after a short delay.

        Called from:
          • ``on_checkout_progress()`` — as soon as the "collecting" phase
            fires (earliest possible moment).
          • ``on_checkout_completed()`` — fallback if the phase callback
            was never received.

        Guarded by ``self._manifest_poll_started`` so it runs at most once
        per checkout run.
        """
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

            if not jira_key:
                self.log("⚠ No JIRA key — skipping manifest poll")
                return

            # Resolve ENV for the hostname
            ctrl = self.context.controller
            env = ""
            if ctrl and hasattr(ctrl, "checkout_controller"):
                env = (ctrl.checkout_controller
                       ._get_env_for_hostname(hostname))

            # Build base search path:
            #   CHECKOUT_RESULTS / <host>_<ENV> / <jira_key>
            CHECKOUT_RESULTS = r"P:\temp\BENTO\CHECKOUT_RESULTS"
            tester_folder = (
                f"{hostname}_{env.upper()}" if env else hostname
            )
            results_base = os.path.join(
                CHECKOUT_RESULTS, tester_folder, jira_key
            )

            self._manifest_poll_count = 0
            self._manifest_poll_started = True
            self._manifest_poll_start_ts = time.time()
            self.log(
                f"📋 Polling for file manifest from "
                f"{hostname} ({jira_key})..."
            )
            # Start polling after a very short delay — the watcher
            # writes the manifest almost immediately after scanning.
            self.context.root.after(
                500,
                lambda: self._poll_for_manifest(
                    hostname, jira_key, results_base
                )
            )
        except Exception as e:
            self.log(f"⚠ Manifest poll setup failed: {e}")
            logger.error(f"Manifest poll setup failed: {e}")

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

        Polls **indefinitely** (every 10 s) because the watcher may need
        up to 30 minutes to collect required files before it writes the
        manifest.  Polling stops automatically when the user clicks Stop
        (checkout state leaves COMPLETED/COLLECTING).

        Args:
            hostname     : tester hostname (e.g. "IBIR-0383")
            job_id       : JIRA key (e.g. "TSESSD-14270")
            results_base : base path under CHECKOUT_RESULTS to search
                           (e.g. P:\\temp\\BENTO\\CHECKOUT_RESULTS\\HOST_ENV\\JIRA)
        """
        import glob as _glob

        # ── Guard: stop polling if user clicked Stop or started a new run ──
        # Item 10: Removed IDLE from allowed states — manifest polling should
        # only continue while checkout is COMPLETED or COLLECTING.  IDLE means
        # the user stopped or a new run hasn't started yet.
        if self._checkout_state not in (
            CheckoutState.COMPLETED, CheckoutState.COLLECTING,
        ):
            self.log("📋 Manifest poll cancelled (checkout state changed)")
            self._manifest_poll_count = 0
            return

        manifest_pattern = os.path.join(
            results_base, "**",
            f"file_manifest_{hostname}_{job_id}.json",
        )
        matches = _glob.glob(manifest_pattern, recursive=True)

        if matches:
            # When multiple manifests exist (from previous runs), pick the
            # one written AFTER polling started.  Fall back to newest by
            # mtime if none qualifies.
            poll_start = getattr(self, "_manifest_poll_start_ts", 0)
            fresh = []
            for m in matches:
                try:
                    mt = os.path.getmtime(m)
                    if mt >= poll_start:
                        fresh.append((mt, m))
                except OSError:
                    pass

            if fresh:
                # Newest among files written after poll started
                fresh.sort(key=lambda x: x[0], reverse=True)
                manifest_path = os.path.normpath(fresh[0][1])
            else:
                # Fallback: newest by mtime overall
                timed = []
                for m in matches:
                    try:
                        timed.append((os.path.getmtime(m), m))
                    except OSError:
                        pass
                if timed:
                    timed.sort(key=lambda x: x[0], reverse=True)
                    manifest_path = os.path.normpath(timed[0][1])
                else:
                    manifest_path = os.path.normpath(matches[0])

            results_folder = os.path.normpath(os.path.dirname(manifest_path))
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                self.log(f"📋 File manifest received from {hostname}")
                logger.info(
                    f"Manifest found: {manifest_path} "
                    f"(out of {len(matches)} match(es)) — "
                    f"selection will be written to: {results_folder}"
                )
                self._manifest_poll_count = 0
                self._show_file_selection_dialog(manifest, hostname,
                                                  job_id, results_folder)
            except Exception as e:
                self.log(f"⚠ Error reading manifest: {e}")
                logger.error(f"Manifest read error: {e}")
                self._manifest_poll_count = 0
        else:
            # Keep polling every 2 seconds — indefinitely.
            # The watcher may need up to 30 min to collect required files
            # before writing the manifest, so a short timeout would cause
            # the popup to never appear.
            if not hasattr(self, "_manifest_poll_count"):
                self._manifest_poll_count = 0
            self._manifest_poll_count += 1

            # Log a status message every ~60 s (every 30th poll at 2 s)
            if self._manifest_poll_count % 30 == 0:
                elapsed_min = (self._manifest_poll_count * 2) // 60
                self.log(
                    f"📋 Still waiting for file manifest from "
                    f"{hostname}… ({elapsed_min} min elapsed)"
                )

            self.context.root.after(
                2_000,
                lambda: self._poll_for_manifest(
                    hostname, job_id, results_base
                )
            )

    # Known file categories from the watcher's FILE_ALLOW_LIST.
    # Only these keys get individual checkboxes in the dialog;
    # everything else is grouped under "Other workspace files".
    _KNOWN_FILE_KEYS = {
        "results_db", "dispatcher_debug", "tracefile",
        "error_log", "test_log", "summary_txt", "summary_zip",
        "update_xml",
    }

    # File extensions to exclude from the "other files" group
    _EXCLUDED_EXTENSIONS = {".py", ".pyc", ".pyo"}

    def _show_file_selection_dialog(self, manifest: dict, hostname: str,
                                     job_id: str, results_folder: str):
        """
        Show a Toplevel dialog listing available files from the manifest.
        User checks which optional files to collect; required files are
        always selected and greyed out.

        Large manifests (thousands of workspace files) are handled by
        grouping unknown file keys into a single "Other workspace files"
        checkbox.  Only known categories from FILE_ALLOW_LIST get
        individual checkboxes.  Files with .py/.pyc extensions are
        excluded entirely.

        Args:
            manifest       : parsed manifest JSON dict
            hostname       : tester hostname
            job_id         : JIRA key
            results_folder : path to write selection JSON back to
        """
        files_info = manifest.get("files", [])

        # ── Partition files into known categories vs. "other" ──────────
        known_entries: list = []      # individual checkboxes
        other_entries: list = []      # grouped under one checkbox
        excluded_count = 0

        for fe in files_info:
            key = fe.get("key", "")
            # Skip excluded extensions (.py, .pyc, .pyo)
            ext = os.path.splitext(key)[1].lower()
            if ext in self._EXCLUDED_EXTENSIONS:
                excluded_count += 1
                continue
            # Also check names for excluded extensions
            names = fe.get("names", [])
            if names and all(
                os.path.splitext(n)[1].lower() in self._EXCLUDED_EXTENSIONS
                for n in names
            ):
                excluded_count += 1
                continue

            if key in self._KNOWN_FILE_KEYS or fe.get("required", False):
                known_entries.append(fe)
            else:
                other_entries.append(fe)

        logger.info(
            f"File selection dialog: {len(known_entries)} known, "
            f"{len(other_entries)} other, {excluded_count} excluded "
            f"(.py/.pyc) — total manifest entries: {len(files_info)}"
        )

        # ── Build dialog ──────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title(f"Select Files to Collect — {hostname}")
        dlg.geometry("600x480")
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

        # ── Known-category checkboxes (individual) ────────────────────
        check_vars: Dict[str, tk.BooleanVar] = {}

        for file_entry in known_entries:
            key      = file_entry.get("key", "")
            desc     = file_entry.get("desc", key)
            required = file_entry.get("required", False)
            found    = file_entry.get("found", False)
            sizes    = file_entry.get("sizes", [])
            names    = file_entry.get("names", [])

            var = tk.BooleanVar(value=required or found)
            check_vars[key] = var

            row_frame = ttk.Frame(inner)
            row_frame.pack(fill=tk.X, pady=2)

            cb = ttk.Checkbutton(row_frame, variable=var)
            cb.pack(side=tk.LEFT)

            if required:
                var.set(True)
                cb.config(state="disabled")
            if not found:
                var.set(False)
                cb.config(state="disabled")

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
            lbl = ttk.Label(row_frame, text=label_text, wraplength=500)
            lbl.pack(side=tk.LEFT, padx=(5, 0))

        # ── "Other workspace files" grouped checkbox ──────────────────
        # Collect keys for the "other" group so we can include/exclude
        # them all at once when the user toggles the checkbox.
        other_keys: list = []
        other_found_count = 0
        other_total_size  = 0
        other_total_files = 0
        for fe in other_entries:
            k = fe.get("key", "")
            other_keys.append(k)
            if fe.get("found", False):
                other_found_count += 1
                other_total_size += sum(fe.get("sizes", []))
                other_total_files += fe.get("count", 0)

        if other_keys:
            # Separator
            ttk.Separator(inner, orient="horizontal").pack(
                fill=tk.X, pady=(8, 4)
            )

            other_var = tk.BooleanVar(value=False)
            # Store other_keys on the var for retrieval in callbacks
            other_var._other_keys = other_keys  # type: ignore[attr-defined]

            row_frame = ttk.Frame(inner)
            row_frame.pack(fill=tk.X, pady=2)

            cb = ttk.Checkbutton(row_frame, variable=other_var)
            cb.pack(side=tk.LEFT)

            if other_total_size > 1024 * 1024:
                o_size = f"{other_total_size / (1024*1024):.1f} MB"
            elif other_total_size > 1024:
                o_size = f"{other_total_size / 1024:.1f} KB"
            else:
                o_size = f"{other_total_size} B"

            other_label = (
                f"📁 Other workspace files — "
                f"{other_found_count} file(s), {o_size}"
            )
            if excluded_count:
                other_label += (
                    f"  (excluded {excluded_count} .py/.pyc files)"
                )
            ttk.Label(
                row_frame, text=other_label, wraplength=500,
                foreground="#555",
            ).pack(side=tk.LEFT, padx=(5, 0))
        else:
            other_var = None

        # ── Total file size summary ───────────────────────────────────
        total_size = 0
        total_files = 0
        for file_entry in known_entries + other_entries:
            if file_entry.get("found", False):
                total_size += sum(file_entry.get("sizes", []))
                total_files += file_entry.get("count", 0)
        if total_size > 1024 * 1024 * 1024:
            size_summary = f"{total_size / (1024**3):.2f} GB"
        elif total_size > 1024 * 1024:
            size_summary = f"{total_size / (1024**2):.1f} MB"
        elif total_size > 1024:
            size_summary = f"{total_size / 1024:.1f} KB"
        else:
            size_summary = f"{total_size} B"
        ttk.Label(
            dlg,
            text=f"📦 Total: {total_files} file(s), {size_summary}",
            font=("Segoe UI", 9, "bold"),
            foreground="#0078d4",
        ).pack(padx=10, pady=(5, 0), anchor="w")

        # ── Buttons ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        def _gather_selected() -> list:
            """Collect all selected keys including 'other' group."""
            selected = [k for k, v in check_vars.items() if v.get()]
            if other_var is not None and other_var.get():
                selected.extend(
                    getattr(other_var, "_other_keys", [])
                )
            return selected

        def _on_confirm():
            self._write_file_selection(
                _gather_selected(), hostname, job_id, results_folder
            )
            dlg.destroy()

        def _on_select_all():
            for key, var in check_vars.items():
                entry = next(
                    (f for f in known_entries if f.get("key") == key), {}
                )
                if entry.get("found", False):
                    var.set(True)
            if other_var is not None:
                other_var.set(True)

        def _on_cancel():
            required_keys = [
                f.get("key", "") for f in known_entries
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

        # Normalize path separators for cross-platform consistency
        results_folder = os.path.normpath(results_folder)

        selection_name = f"file_selection_{hostname}_{job_id}.json"
        selection_path = os.path.join(results_folder, selection_name)

        logger.info(
            f"File selection: writing to {selection_path} "
            f"(folder exists={os.path.isdir(results_folder)})"
        )

        try:
            os.makedirs(results_folder, exist_ok=True)
            with open(selection_path, "w") as f:
                json.dump(selection, f, indent=2)

            # Verify the file was actually written
            if os.path.isfile(selection_path):
                fsize = os.path.getsize(selection_path)
                self.log(
                    f"📝 File selection written: {len(selected_keys)} file(s) "
                    f"selected for {hostname}"
                )
                logger.info(
                    f"File selection written OK: {selection_path} "
                    f"({fsize} bytes)"
                )
            else:
                self.log(
                    f"⚠ File selection write succeeded but file not found "
                    f"at {selection_path}"
                )
                logger.error(
                    f"File selection write anomaly: open() succeeded but "
                    f"isfile() returned False for {selection_path}"
                )
        except Exception as e:
            self.log(f"⚠ Failed to write file selection: {e}")
            logger.error(
                f"File selection write error: {e} "
                f"(path={selection_path})"
            )
