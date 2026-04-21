#!/usr/bin/env python3
"""
view/tabs/compilation_tab.py
=============================
Compilation Tab (View) — Standalone tab for TP Compilation & Health

UX Improvements Applied:
  A2  — Proper placeholder entry with flag-based logic
  A3  — Fixed misleading Shift+Click hint → "Click to toggle"
  A4  — Wider path entries (fill available space)
  A5  — Compile button visual feedback during compilation
  A6  — Force Fail moved to its own top-level LabelFrame
  A7  — Double-click instruction label for force-fail cases
  A8  — Auto-refresh timestamp indicator in health monitor
  A9  — Scrollbar + increased limit for recent builds
  A10 — Increased compile history treeview height
  A11 — Click-to-sort column headings in history
  A12 — Added Status column to compile history
  A13 — Reduced Add Tester dialog height, collapsible checklist
  A14 — Hostname format validation with visual feedback
  A15 — Vertical stacking layout instead of side-by-side columns
  A23 — Confirmation dialog before compile with summary
"""

import os
import re
import json
import glob
import time
import datetime
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import logging

from view.tabs.base_tab import BaseTab
from view.widgets.tooltip import ToolTip as _ToolTip, tip as _tip

logger = logging.getLogger("bento_app")


class CompilationTab(BaseTab):
    """
    Standalone Compilation tab for TP Package compilation and health monitoring.

    Layout (vertical stacking — A15):
      1. Compile TP Package
         a. Target Testers (full width)
         b. Configuration & Paths (full width)
         c. Action buttons
      2. Force Fail TGZ (own top-level section — A6)
      3. Watcher Health Monitor
      4. Compile History
    """

    _BADGE_COLOURS = {
        "IDLE":     ("#888888", "white"),
        "PENDING":  ("#0078d4", "white"),
        "RUNNING":  ("#005a9e", "white"),
        "SUCCESS":  ("#107c10", "white"),
        "FAILED":   ("#a80000", "white"),
        "TIMEOUT":  ("#ca5010", "white"),
    }

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📦 TP Compilation")
        self._badge_labels = {}
        self._tester_vars  = {}
        self._history_rows = []
        self._sort_col     = None      # A11: current sort column
        self._sort_reverse = False     # A11: sort direction
        self._placeholder_active = True  # A2: placeholder flag
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Create scrollable canvas (same pattern as checkout_tab)
        canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        v_scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")

        # Inner frame that will be scrollable
        self._inner = ttk.Frame(canvas, padding=(8, 6, 8, 12))
        self._inner.columnconfigure(0, weight=1)
        _win = canvas.create_window((0, 0), window=self._inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(_win, width=event.width)
            # Ensure inner frame is at least as tall as the canvas
            inner_h = self._inner.winfo_reqheight()
            if inner_h < event.height:
                canvas.itemconfig(_win, height=event.height)
            else:
                canvas.itemconfig(_win, height=inner_h)

        self._inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        # Use _inner as the main container instead of main_frame
        main_frame = self._inner

        # ══════════════════════════════════════════════════════════════════
        # 1. Compile TP Package (Top)
        # ══════════════════════════════════════════════════════════════════
        compile_frame = ttk.LabelFrame(main_frame, text="Compile TP Package", padding="6")
        compile_frame.pack(fill=tk.X, padx=10, pady=5)
        # Two-column grid: Target Testers (left) | Configuration & Paths (right)
        compile_frame.columnconfigure(0, weight=1)
        compile_frame.columnconfigure(1, weight=1)

        # ── LEFT: Target Testers ─────────────────────────────────────────
        targets_frame = ttk.LabelFrame(compile_frame, text="1. Target Testers", padding="6")
        targets_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=(0, 5))
        targets_frame.columnconfigure(0, weight=1)

        # A2: Proper placeholder search entry
        search_frame = ttk.Frame(targets_frame)
        search_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_frame, text="Search:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._tester_search_var = tk.StringVar()
        self._tester_search_var.trace_add("write", self._filter_testers)
        self._tester_search_entry = ttk.Entry(search_frame, textvariable=self._tester_search_var)
        self._tester_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._placeholder_active = True
        self._tester_search_entry.insert(0, "Search testers...")
        self._tester_search_entry.config(foreground="gray")
        self._tester_search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self._tester_search_entry.bind("<FocusOut>", self._on_search_focus_out)

        # Listbox with scrollbar
        list_frame = ttk.Frame(targets_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self._tester_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE,
                                          height=7, exportselection=False,
                                          font=("Segoe UI", 9))
        self._tester_listbox.grid(row=0, column=0, sticky="nsew")
        self._tester_listbox.bind("<<ListboxSelect>>", self._on_tester_selected)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tester_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._tester_listbox.config(yscrollcommand=scrollbar.set)
        # A3: Fixed hint
        _tip(self._tester_listbox, "Click to toggle tester selection.\nMultiple testers can be selected.")

        # Buttons row below listbox
        btn_row = ttk.Frame(targets_frame)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        add_btn = ttk.Button(btn_row, text="+ Add", width=8, command=self._add_tester)
        add_btn.pack(side=tk.LEFT, padx=(0, 2))
        _tip(add_btn, "Register a new tester in the shared registry.")
        rem_btn = ttk.Button(btn_row, text="🗑 Remove", width=11, command=self._remove_tester)
        rem_btn.pack(side=tk.LEFT, padx=(0, 2))
        _tip(rem_btn, "Remove selected tester(s) from the registry.")
        sel_all_btn = ttk.Button(btn_row, text="☑ All", width=6, command=self._select_all_testers)
        sel_all_btn.pack(side=tk.LEFT, padx=(0, 2))
        _tip(sel_all_btn, "Select all testers in the list.")
        desel_btn = ttk.Button(btn_row, text="☐ None", width=7, command=self._deselect_all_testers)
        desel_btn.pack(side=tk.LEFT)
        _tip(desel_btn, "Deselect all testers.")

        # Selected indicator
        info_frame = ttk.Frame(targets_frame)
        info_frame.pack(fill=tk.X, pady=(3, 0))
        ttk.Label(info_frame, text="Selected:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self._compile_mode_var = tk.StringVar(value="No tester selected")
        self._compile_mode_lbl = ttk.Label(info_frame, textvariable=self._compile_mode_var,
                                           font=("Segoe UI", 9, "italic"), foreground="#cc0000")
        self._compile_mode_lbl.pack(side=tk.LEFT)

        # ── RIGHT: Configuration & Paths ─────────────────────────────────
        config_frame = ttk.LabelFrame(compile_frame, text="2. Configuration & Paths", padding="6")
        config_frame.grid(row=0, column=1, sticky="nsew", padx=(3, 0), pady=(0, 5))
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="TGZ Label:", font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        self.context.set_var("tgz_label_var", tk.StringVar())
        tgz_label_entry = ttk.Entry(config_frame, textvariable=self.context.get_var("tgz_label_var"))
        tgz_label_entry.grid(row=0, column=1, sticky="we", pady=2)
        _tip(tgz_label_entry, "Custom label appended to the TGZ filename.\nLeave blank for default naming.")
        ttk.Label(config_frame, text="(blank = default)", font=("Segoe UI", 8),
                  foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=2)

        ttk.Label(config_frame, text="RAW_ZIP:", font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        raw_zip_frame = ttk.Frame(config_frame)
        raw_zip_frame.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        raw_zip_frame.columnconfigure(0, weight=1)

        from model.site_paths import get_site_path
        default_raw_zip = get_site_path("RAW_ZIP")
        default_release_tgz = get_site_path("RELEASE_TGZ")

        self.context.set_var("compile_raw_zip", tk.StringVar(value=default_raw_zip))
        self.raw_zip_entry = ttk.Entry(raw_zip_frame, textvariable=self.context.get_var("compile_raw_zip"))
        self.raw_zip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tip(self.raw_zip_entry, "Path to the RAW_ZIP folder where compiled ZIPs are placed\nfor the watcher to process.")
        browse_rz = ttk.Button(raw_zip_frame, text="📁", width=3, command=self._browse_raw_zip)
        browse_rz.pack(side=tk.LEFT, padx=(2, 0))
        _tip(browse_rz, "Browse for RAW_ZIP folder.")

        ttk.Label(config_frame, text="RELEASE_TGZ:", font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        release_tgz_frame = ttk.Frame(config_frame)
        release_tgz_frame.grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        release_tgz_frame.columnconfigure(0, weight=1)
        self.context.set_var("compile_release_tgz", tk.StringVar(value=default_release_tgz))
        self.release_tgz_entry = ttk.Entry(release_tgz_frame, textvariable=self.context.get_var("compile_release_tgz"))
        self.release_tgz_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tip(self.release_tgz_entry, "Path to the RELEASE_TGZ folder where final TGZ files\nare stored after watcher processing.")
        browse_rt = ttk.Button(release_tgz_frame, text="📁", width=3, command=self._browse_release_tgz)
        browse_rt.pack(side=tk.LEFT, padx=(2, 0))
        _tip(browse_rt, "Browse for RELEASE_TGZ folder.")

        # ── Compile Button (spans both columns) ─────────────────────────
        action_frame = ttk.Frame(compile_frame)
        action_frame.grid(row=1, column=0, columnspan=2, pady=(0, 5))

        style = ttk.Style()
        style.configure('Compile.TButton')
        # A5: Button text changes during compilation
        self.compile_btn = ttk.Button(action_frame, text="🚀 Compile on Selected Tester(s)",
                                     style='Compile.TButton', command=self._start_compile, width=35)
        self.compile_btn.pack(pady=(0, 2))
        self.context.lockable_buttons.append(self.compile_btn)
        _tip(self.compile_btn, "Start TP compilation on all selected testers.\nRequires JIRA key and repo path from other tabs.")
        self.compile_status_var = tk.StringVar(value="")
        ttk.Label(action_frame, textvariable=self.compile_status_var,
                  font=("Segoe UI", 9, "bold"), foreground="#0066cc").pack()

        self._refresh_testers()

        # ══════════════════════════════════════════════════════════════════
        # 2. Force Fail TGZ (A6: Own top-level section)
        # ══════════════════════════════════════════════════════════════════
        self._build_force_fail_section(main_frame)

        # ══════════════════════════════════════════════════════════════════
        # 3. Watcher Health Monitor (Middle)
        # ══════════════════════════════════════════════════════════════════
        self.health_wrapper = ttk.LabelFrame(main_frame, text="🔍 Watcher Health Monitor", padding="6")
        self.health_wrapper.pack(fill=tk.X, padx=10, pady=5)

        row_frame1 = ttk.Frame(self.health_wrapper)
        row_frame1.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame1, text="📂 RAW_ZIP Folder:", width=28, anchor="w",
                  font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.health_raw_zip_lbl = ttk.Label(row_frame1, text="Checking...", foreground="gray")
        self.health_raw_zip_lbl.pack(side=tk.LEFT)

        row_frame2 = ttk.Frame(self.health_wrapper)
        row_frame2.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame2, text="📂 RELEASE_TGZ Folder:", width=28, anchor="w",
                  font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.health_release_lbl = ttk.Label(row_frame2, text="Checking...", foreground="gray")
        self.health_release_lbl.pack(side=tk.LEFT)

        row_frame3 = ttk.Frame(self.health_wrapper)
        row_frame3.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame3, text="🤖 Watcher Process:", width=28, anchor="w",
                  font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.health_watcher_lbl = ttk.Label(row_frame3, text="Checking...", foreground="gray")
        self.health_watcher_lbl.pack(side=tk.LEFT)

        recent_frame = ttk.Frame(self.health_wrapper)
        recent_frame.pack(fill=tk.X, pady=(3, 2))
        recent_frame.columnconfigure(1, weight=1)
        ttk.Label(recent_frame, text="📊 Recent Builds:", width=28, anchor="w",
                  font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="nw")

        # A9: Added scrollbar, increased height, added border
        builds_container = ttk.Frame(recent_frame)
        builds_container.grid(row=0, column=1, sticky="nsew")
        builds_container.columnconfigure(0, weight=1)
        self.builds_text = tk.Text(builds_container, height=8, bg="white",
                                   relief="groove", bd=1, font=("Segoe UI", 9))
        self.builds_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        builds_scroll = ttk.Scrollbar(builds_container, orient=tk.VERTICAL,
                                      command=self.builds_text.yview)
        builds_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.builds_text.config(yscrollcommand=builds_scroll.set)

        self.builds_text.tag_config("success",     foreground="#28a745")
        self.builds_text.tag_config("failed",      foreground="#dc3545")
        self.builds_text.tag_config("in_progress", foreground="#fd7e14")
        self.builds_text.tag_config("pending",     foreground="#fd7e14")
        self.builds_text.tag_config("timeout",     foreground="purple")
        self.builds_text.tag_config("unknown",     foreground="gray")
        self.builds_text.tag_config("default",     foreground="black")

        # A8: Refresh button row with last-refreshed timestamp
        refresh_row = ttk.Frame(self.health_wrapper)
        refresh_row.pack(fill=tk.X, padx=5, pady=2)
        self._health_last_refresh_var = tk.StringVar(value="")
        ttk.Label(refresh_row, textvariable=self._health_last_refresh_var,
                  font=("Segoe UI", 8, "italic"), foreground="gray").pack(side=tk.LEFT)
        refresh_btn = ttk.Button(refresh_row, text="🔄 Refresh Now", command=self._refresh_health)
        refresh_btn.pack(side=tk.RIGHT)
        _tip(refresh_btn, "Manually refresh health status.\nAuto-refreshes every 30 seconds.")
        self._refresh_health()

        # ══════════════════════════════════════════════════════════════════
        # 4. Compile History Section (Bottom - Fills remainder)
        # ══════════════════════════════════════════════════════════════════
        history_wrapper = ttk.LabelFrame(main_frame, text="📋 Compile History", padding="6")
        history_wrapper.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        history_wrapper.columnconfigure(0, weight=1)

        # Toolbar
        toolbar = ttk.Frame(history_wrapper)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 2))
        ttk.Label(toolbar, text="Past compilations from RELEASE_TGZ",
                  font=("Segoe UI", 8), foreground="gray").pack(side=tk.LEFT)

        hist_refresh_btn = ttk.Button(toolbar, text="🔄 Refresh",
                                      command=self._refresh_history_from_disk)
        hist_refresh_btn.pack(side=tk.RIGHT)
        _tip(hist_refresh_btn, "Reload compile history from disk.")
        open_folder_btn = ttk.Button(toolbar, text="📁 Open Folder",
                                     command=self._open_release_folder)
        open_folder_btn.pack(side=tk.RIGHT, padx=5)
        _tip(open_folder_btn, "Open the RELEASE_TGZ folder in Explorer.\nDouble-click a history row to open its specific folder.")

        # A12: Added "Status" column; A10: Increased height to 8
        hist_cols = ("Timestamp", "Status", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        self._history_tree = ttk.Treeview(history_wrapper, columns=hist_cols,
                                          show="headings", height=8)

        # A12: Column widths with new Status column
        col_widths = [140, 80, 120, 110, 70, 100, 280]
        for col, width in zip(hist_cols, col_widths):
            # A11: Click-to-sort on column headings
            self._history_tree.heading(col, text=col,
                                       command=lambda c=col: self._sort_history(c))
            self._history_tree.column(col, width=width, anchor="w")

        # Row colors (even/odd tags)
        self._history_tree.tag_configure("even", background="#f5f5f5")
        self._history_tree.tag_configure("odd",  background="#ffffff")
        # A12: Only failures get colored — SUCCESS rows stay default black
        self._history_tree.tag_configure("status_failed",  foreground="#a80000")
        self._history_tree.tag_configure("status_error",   foreground="#a80000")

        hist_scroll = ttk.Scrollbar(history_wrapper, orient=tk.VERTICAL,
                                    command=self._history_tree.yview)
        self._history_tree.configure(yscrollcommand=hist_scroll.set)

        # Add double-click handler to open the specific folder
        self._history_tree.bind("<Double-1>", lambda e: self._open_release_folder())
        # Item 19: Right-click context menu for copy
        self._history_tree.bind("<Button-3>", self._history_context_menu)

        self._history_tree.grid(row=1, column=0, sticky="nsew")
        hist_scroll.grid(row=1, column=1, sticky="ns")

        history_wrapper.rowconfigure(1, weight=1)

        # Initial history load
        self._refresh_history_from_disk()

        # ══════════════════════════════════════════════════════════════════
        # 5. Proceed to Checkout (C2: workflow connection)
        # ══════════════════════════════════════════════════════════════════
        proceed_frame = ttk.Frame(main_frame)
        proceed_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        self._proceed_btn = ttk.Button(
            proceed_frame,
            text="Proceed to Checkout  ➜",
            command=self._proceed_to_checkout,
        )
        self._proceed_btn.pack(side=tk.RIGHT)
        _tip(self._proceed_btn,
             "Switch to the Checkout sub-tab to start checkout after compilation.")

    # ──────────────────────────────────────────────────────────────────────
    # A2: PROPER PLACEHOLDER ENTRY HANDLERS
    # ──────────────────────────────────────────────────────────────────────

    def _on_search_focus_in(self, event):
        """Clear placeholder text on focus."""
        if self._placeholder_active:
            self._tester_search_entry.delete(0, tk.END)
            self._tester_search_entry.config(foreground="black")
            self._placeholder_active = False

    def _on_search_focus_out(self, event):
        """Restore placeholder text if entry is empty."""
        if not self._tester_search_entry.get().strip():
            self._placeholder_active = True
            self._tester_search_entry.insert(0, "Search testers...")
            self._tester_search_entry.config(foreground="gray")

    # ──────────────────────────────────────────────────────────────────────
    # C2: PROCEED TO CHECKOUT — workflow connection
    # ──────────────────────────────────────────────────────────────────────

    def _proceed_to_checkout(self):
        """Switch the parent sub-notebook to the Checkout tab."""
        try:
            nb = self.master  # the sub_notebook in CompileCheckoutTab
            if isinstance(nb, ttk.Notebook):
                for i in range(nb.index("end")):
                    tab_text = nb.tab(i, "text")
                    if "Checkout" in tab_text:
                        nb.select(i)
                        return
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # A6: FORCE FAIL SECTION — Own top-level LabelFrame
    # ──────────────────────────────────────────────────────────────────────

    def _build_force_fail_section(self, parent):
        """Build the force-fail UI section as its own top-level LabelFrame."""
        ff_frame = ttk.LabelFrame(parent, text="❌ Force Fail TGZ", padding="6")
        ff_frame.pack(fill=tk.X, padx=10, pady=5)
        ff_frame.columnconfigure(1, weight=1)

        # Row 0: Enable checkbox + Generate button
        ctrl_row = ttk.Frame(ff_frame)
        ctrl_row.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 4))

        self._ff_enabled_var = tk.BooleanVar(value=False)
        self._ff_check = ttk.Checkbutton(
            ctrl_row, text="Enable Force Fail TGZ",
            variable=self._ff_enabled_var,
            command=self._on_ff_toggle,
        )
        self._ff_check.pack(side=tk.LEFT, padx=(0, 10))
        _tip(self._ff_check, "Enable force-fail TGZ generation.\nWhen enabled, AI generates test cases that intentionally fail.")

        self._ff_generate_btn = ttk.Button(
            ctrl_row, text="🤖 Generate Force Fail Cases",
            command=self._generate_force_fail, state=tk.DISABLED,
        )
        self._ff_generate_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.context.lockable_buttons.append(self._ff_generate_btn)
        _tip(self._ff_generate_btn, "Use AI to generate force-fail test cases\nbased on the JIRA issue and repository code.")

        self._ff_compile_btn = ttk.Button(
            ctrl_row, text="🚀 Compile Force Fail TGZ",
            command=self._compile_force_fail, state=tk.DISABLED,
        )
        self._ff_compile_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.context.lockable_buttons.append(self._ff_compile_btn)
        _tip(self._ff_compile_btn, "Compile a TGZ with force-fail patches applied.")

        self._ff_clear_btn = ttk.Button(
            ctrl_row, text="🗑 Clear", command=self._clear_ff_cases,
            state=tk.DISABLED, width=8,
        )
        self._ff_clear_btn.pack(side=tk.LEFT)
        _tip(self._ff_clear_btn, "Clear all generated force-fail cases.")

        # Row 1: Status label
        self._ff_status_var = tk.StringVar(value="Force fail disabled")
        self._ff_status_lbl = ttk.Label(
            ff_frame, textvariable=self._ff_status_var,
            font=("Segoe UI", 9, "italic"), foreground="gray",
        )
        self._ff_status_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Row 2: Cases list (Treeview for per-case toggles)
        cases_frame = ttk.Frame(ff_frame)
        cases_frame.grid(row=2, column=0, columnspan=2, sticky="nswe", pady=(0, 4))
        cases_frame.columnconfigure(0, weight=1)
        cases_frame.rowconfigure(1, weight=1)

        # A7: Instruction label for double-click toggle
        ttk.Label(cases_frame, text="💡 Double-click a row to enable/disable a case",
                  font=("Segoe UI", 8, "italic"), foreground="#666666").grid(
            row=0, column=0, sticky="w", pady=(0, 2))

        ff_cols = ("Enabled", "Test ID", "Description", "Files")
        self._ff_tree = ttk.Treeview(
            cases_frame, columns=ff_cols, show="headings", height=8,
        )
        ff_col_widths = [60, 120, 300, 200]
        for col, w in zip(ff_cols, ff_col_widths):
            self._ff_tree.heading(col, text=col)
            self._ff_tree.column(col, width=w, anchor="w")

        self._ff_tree.tag_configure("enabled",  foreground="#107c10")
        self._ff_tree.tag_configure("disabled", foreground="#888888")
        self._ff_tree.bind("<Double-1>", self._on_ff_case_double_click)

        ff_scroll = ttk.Scrollbar(cases_frame, orient=tk.VERTICAL, command=self._ff_tree.yview)
        self._ff_tree.configure(yscrollcommand=ff_scroll.set)
        self._ff_tree.grid(row=1, column=0, sticky="nsew")
        ff_scroll.grid(row=1, column=1, sticky="ns")

        # Row 3: Diff preview (collapsed by default)
        self._ff_diff_visible = tk.BooleanVar(value=False)
        diff_toggle = ttk.Checkbutton(
            ff_frame, text="Show Diff Preview",
            variable=self._ff_diff_visible,
            command=self._toggle_ff_diff_preview,
        )
        diff_toggle.grid(row=3, column=0, sticky="w", pady=(0, 2))

        self._ff_diff_text = scrolledtext.ScrolledText(
            ff_frame, height=6, width=70, wrap=tk.WORD,
            font=("Consolas", 9), state=tk.DISABLED,
        )
        # Hidden by default — shown when checkbox is toggled
        self._ff_diff_text.grid(row=4, column=0, columnspan=2, sticky="we", pady=(0, 2))
        self._ff_diff_text.grid_remove()

        self._ff_diff_text.tag_config("add",    foreground="#107c10", font=("Consolas", 9, "bold"))
        self._ff_diff_text.tag_config("remove", foreground="#a80000", font=("Consolas", 9, "bold"))
        self._ff_diff_text.tag_config("header", foreground="#0066cc", font=("Consolas", 9, "bold"))
        self._ff_diff_text.tag_config("normal", foreground="#333333")

    # ── Force Fail UI Callbacks ────────────────────────────────────────────

    def _on_ff_toggle(self):
        """Enable/disable force-fail controls based on checkbox."""
        enabled = self._ff_enabled_var.get()
        state = tk.NORMAL if enabled else tk.DISABLED
        self._ff_generate_btn.config(state=state)
        self._ff_clear_btn.config(state=state if self._ff_tree.get_children() else tk.DISABLED)

        if enabled:
            self._ff_status_var.set("Force fail enabled — generate cases to proceed")
            self._ff_status_lbl.config(foreground="#0066cc")
        else:
            self._ff_status_var.set("Force fail disabled")
            self._ff_status_lbl.config(foreground="gray")
            self._ff_compile_btn.config(state=tk.DISABLED)

    def _generate_force_fail(self):
        """Trigger AI generation of force-fail cases."""
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        repo_path = self.context.get_var("impl_repo_var").get().strip()

        jira_project = self.context.config.get("jira", {}).get("project_key", "TSESSD")
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Enter a valid JIRA issue key.")
            return
        if not repo_path:
            self.show_error("Input Error", "Select the local repository path.")
            return

        ctrl = getattr(self.context.controller, "force_fail_controller", None)
        if ctrl is None:
            self.show_error("Error", "ForceFailController is not initialised.")
            return

        self.lock_gui()
        self._ff_status_var.set("⏳ Generating force-fail cases…")
        self._ff_status_lbl.config(foreground="#fd7e14")
        self.log(f"[Force Fail] Generating cases for {issue_key}")

        ctrl.generate_force_fail(
            issue_key=issue_key,
            repo_path=repo_path,
            callback=self._on_ff_generated,
        )

    def _on_ff_generated(self, result):
        """Callback when force-fail generation completes."""
        self.unlock_gui()

        if not result.get("success"):
            error = result.get("error", "Unknown error")
            self._ff_status_var.set(f"✗ Generation failed")
            self._ff_status_lbl.config(foreground="#a80000")
            self.show_error("Force Fail Error", error)
            return

        cases = result.get("cases", [])
        from_cache = result.get("from_cache", False)
        cache_note = " (from cache)" if from_cache else ""

        self._ff_status_var.set(f"✓ {len(cases)} case(s) generated{cache_note}")
        self._ff_status_lbl.config(foreground="#107c10")

        # Populate the cases treeview
        self._populate_ff_cases(cases)

        # Enable compile button
        self._ff_compile_btn.config(state=tk.NORMAL)
        self._ff_clear_btn.config(state=tk.NORMAL)

        # Update diff preview
        self._update_ff_diff_preview()

    def _populate_ff_cases(self, cases):
        """Populate the force-fail cases treeview."""
        self._ff_tree.delete(*self._ff_tree.get_children())
        for case in cases:
            status = "✓" if case.enabled else "○"
            files = ", ".join(p.file for p in case.patches)
            tag = "enabled" if case.enabled else "disabled"
            self._ff_tree.insert("", tk.END, iid=case.test_id, values=(
                status, case.test_id, case.description, files
            ), tags=(tag,))

    def _on_ff_case_double_click(self, event):
        """Toggle a force-fail case on/off via double-click."""
        item = self._ff_tree.identify_row(event.y)
        if not item:
            return

        ctrl = getattr(self.context.controller, "force_fail_controller", None)
        if ctrl is None:
            return

        # Get current state and toggle
        values = self._ff_tree.item(item, "values")
        currently_enabled = values[0] == "✓"
        new_enabled = not currently_enabled

        ctrl.toggle_case(item, new_enabled)

        # Update display
        new_status = "✓" if new_enabled else "○"
        tag = "enabled" if new_enabled else "disabled"
        self._ff_tree.item(item, values=(new_status, values[1], values[2], values[3]), tags=(tag,))

        # Update status
        total, enabled = ctrl.get_cases_count()
        self._ff_status_var.set(f"✓ {total} case(s), {enabled} enabled")

        # Update diff preview
        self._update_ff_diff_preview()

        # Enable/disable compile button based on enabled count
        self._ff_compile_btn.config(state=tk.NORMAL if enabled > 0 else tk.DISABLED)

    def _toggle_ff_diff_preview(self):
        """Show/hide the diff preview text widget."""
        if self._ff_diff_visible.get():
            self._ff_diff_text.grid()
            self._update_ff_diff_preview()
        else:
            self._ff_diff_text.grid_remove()

    def _update_ff_diff_preview(self):
        """Update the diff preview with current force-fail cases."""
        ctrl = getattr(self.context.controller, "force_fail_controller", None)
        if ctrl is None:
            return

        self._ff_diff_text.config(state=tk.NORMAL)
        self._ff_diff_text.delete("1.0", tk.END)

        display = ctrl.get_cases_display()
        if not display or display.startswith("No force-fail"):
            self._ff_diff_text.insert(tk.END, display or "No cases generated.", "normal")
            self._ff_diff_text.config(state=tk.DISABLED)
            return

        # Syntax-highlight the diff display
        for line in display.split("\n"):
            if line.startswith("+++") or line.startswith("---"):
                self._ff_diff_text.insert(tk.END, line + "\n", "header")
            elif line.startswith("+"):
                self._ff_diff_text.insert(tk.END, line + "\n", "add")
            elif line.startswith("-"):
                self._ff_diff_text.insert(tk.END, line + "\n", "remove")
            elif line.startswith("@@") or line.startswith("=="):
                self._ff_diff_text.insert(tk.END, line + "\n", "header")
            else:
                self._ff_diff_text.insert(tk.END, line + "\n", "normal")

        self._ff_diff_text.config(state=tk.DISABLED)

    def _compile_force_fail(self):
        """Compile the force-fail TGZ using patched repo."""
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        repo_path = self.context.get_var("impl_repo_var").get().strip()
        _raw_zip = self.context.get_var("compile_raw_zip").get().strip()
        shared_folder = os.path.dirname(_raw_zip.rstrip("/\\"))
        hostnames = self._get_selected_hostnames()

        if not hostnames:
            self.show_error("Input Error", "Select at least one tester.")
            return

        ctrl = getattr(self.context.controller, "force_fail_controller", None)
        if ctrl is None:
            self.show_error("Error", "ForceFailController is not initialised.")
            return

        total, enabled = ctrl.get_cases_count()
        if enabled == 0:
            self.show_error("Input Error", "No enabled force-fail cases. Double-click to toggle.")
            return

        # Determine label
        base_label = self.context.get_var("tgz_label_var").get().strip()
        ff_label = f"force_fail_{base_label}" if base_label else "force_fail_1"

        self.lock_gui()
        self._ff_status_var.set("⏳ Compiling force-fail TGZ…")
        self._ff_status_lbl.config(foreground="#fd7e14")
        # A5: Update compile button text
        self.compile_status_var.set("Force Fail Compile Running...")
        self.log(f"[Force Fail] Compiling {enabled} case(s) on {len(hostnames)} tester(s)")

        ctrl.compile_force_fail(
            issue_key=issue_key,
            repo_path=repo_path,
            shared_folder=shared_folder,
            hostnames=hostnames,
            label=ff_label,
            callback=self._on_ff_compiled,
        )

    def _on_ff_compiled(self, result):
        """Callback when force-fail compilation completes."""
        self.unlock_gui()
        self.compile_status_var.set("")

        if result.get("success"):
            label = result.get("label", "force_fail")
            self._ff_status_var.set(f"✓ Force-fail TGZ submitted (label: {label})")
            self._ff_status_lbl.config(foreground="#107c10")
            self.log(f"[Force Fail] ✓ Compilation submitted — label: {label}")
        else:
            error = result.get("error", "Unknown error")
            self._ff_status_var.set("✗ Force-fail compilation failed")
            self._ff_status_lbl.config(foreground="#a80000")
            self.show_error("Force Fail Compile Error", error)

    def _clear_ff_cases(self):
        """Clear all generated force-fail cases."""
        ctrl = getattr(self.context.controller, "force_fail_controller", None)
        if ctrl:
            ctrl.clear_cases()

        self._ff_tree.delete(*self._ff_tree.get_children())
        self._ff_compile_btn.config(state=tk.DISABLED)
        self._ff_clear_btn.config(state=tk.DISABLED)
        self._ff_status_var.set("Force fail enabled — generate cases to proceed")
        self._ff_status_lbl.config(foreground="#0066cc")

        # Clear diff preview
        self._ff_diff_text.config(state=tk.NORMAL)
        self._ff_diff_text.delete("1.0", tk.END)
        self._ff_diff_text.config(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # TP COMPILATION — USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_raw_zip(self):
        path = filedialog.askdirectory(title="Select RAW_ZIP Folder")
        if path:
            self.context.get_var("compile_raw_zip").set(path)

    def _browse_release_tgz(self):
        path = filedialog.askdirectory(title="Select RELEASE_TGZ Folder")
        if path:
            self.context.get_var("compile_release_tgz").set(path)

    def update_paths_from_site(self):
        """Update all path fields based on the current global site selection."""
        from model.site_paths import get_site_path

        # Update RAW_ZIP path
        raw_zip_path = get_site_path("RAW_ZIP")
        self.context.get_var("compile_raw_zip").set(raw_zip_path)

        # Update RELEASE_TGZ path
        release_tgz_path = get_site_path("RELEASE_TGZ")
        self.context.get_var("compile_release_tgz").set(release_tgz_path)

    def _refresh_testers(self):
        self._tester_listbox.delete(0, tk.END)
        ctrl = getattr(self.context.controller, "compile_controller", None)
        testers = ctrl.get_available_testers() if ctrl else []
        for i, (hostname, env) in enumerate(testers):
            self._tester_listbox.insert(tk.END, f"{hostname} ({env})")

    def _filter_testers(self, *_):
        if not hasattr(self, '_tester_listbox'):
            return
        # A2: Use placeholder flag instead of string comparison
        if self._placeholder_active:
            return
        query = self._tester_search_var.get().lower().strip()
        self._tester_listbox.delete(0, tk.END)
        ctrl = getattr(self.context.controller, "compile_controller", None)
        testers = ctrl.get_available_testers() if ctrl else []
        for hostname, env in testers:
            label = f"{hostname} ({env})"
            if not query or query in hostname.lower() or query in env.lower():
                self._tester_listbox.insert(tk.END, label)
        self._on_tester_selected()

    def _on_tester_selected(self, event=None):
        selections = self._tester_listbox.curselection()
        if not selections:
            self._compile_mode_var.set("No tester selected")
            self._compile_mode_lbl.config(foreground="#cc0000")
        elif len(selections) == 1:
            val = self._tester_listbox.get(selections[0])
            self._compile_mode_var.set("Selected: " + val)
            self._compile_mode_lbl.config(foreground="#1a6e1a")
        else:
            names = ", ".join([self._tester_listbox.get(i) for i in selections])
            self._compile_mode_var.set(f"Multi-compile: {len(selections)} testers — {names}")
            self._compile_mode_lbl.config(foreground="#0066cc")

    # A1: Select All / Deselect All
    def _select_all_testers(self):
        """Select all testers in the listbox."""
        self._tester_listbox.select_set(0, tk.END)
        self._on_tester_selected()

    def _deselect_all_testers(self):
        """Deselect all testers in the listbox."""
        self._tester_listbox.select_clear(0, tk.END)
        self._on_tester_selected()

    def _add_tester(self):
        """Open the custom Add Tester dialog."""
        AddTesterDialog(self.root, self)

    def _browse_directory(self, var, title):
        """Open directory browser and set the variable."""
        from tkinter import filedialog
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _centre_dialog(self, dialog, w, h):
        """Position dialog at the centre of the main window."""
        dialog.transient(self.root)
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

    def _remove_tester(self):
        """Remove selected tester(s) with confirmation."""
        from tkinter import messagebox
        selections = self._tester_listbox.curselection()
        if not selections:
            messagebox.showwarning("No Selection", "Please select one or more testers to remove.")
            return

        testers_to_remove = [self._tester_listbox.get(idx) for idx in selections]

        if len(testers_to_remove) == 1:
            msg = f"Remove '{testers_to_remove[0]}' from the registry?"
        else:
            msg = f"Remove {len(testers_to_remove)} testers from the registry?\n\n" + "\n".join(f"  • {t}" for t in testers_to_remove)

        if messagebox.askyesno("Remove Tester(s)", msg):
            # Load current registry to delete correctly
            registry_path = self.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
            registry_data = {}
            if os.path.exists(registry_path):
                try:
                    with open(registry_path, "r") as f:
                        registry_data = json.load(f)
                except Exception: pass

            # Match by display label OR by hostname+env inside the value
            for display_label in testers_to_remove:
                if display_label in registry_data:
                    del registry_data[display_label]
                    self.log(f"[Tester Removed] {display_label}")
                else:
                    # Fallback: parse "HOSTNAME (ENV)" and match against
                    # registry values that store hostname/env fields
                    _h, _e = "", ""
                    if " (" in display_label and display_label.endswith(")"):
                        _h, _e = display_label.split(" (", 1)
                        _e = _e[:-1]
                    matched_key = None
                    for rk, rv in registry_data.items():
                        if isinstance(rv, dict):
                            rh = rv.get("hostname", "")
                            re_ = rv.get("env", "")
                        elif isinstance(rv, list):
                            rh = rv[0] if len(rv) > 0 else ""
                            re_ = rv[1] if len(rv) > 1 else ""
                        else:
                            rh, re_ = "", ""
                        if rh == _h and re_ == _e:
                            matched_key = rk
                            break
                    if matched_key:
                        del registry_data[matched_key]
                        self.log(f"[Tester Removed] {display_label}")

            self._save_tester_registry(registry_data)
            self._refresh_testers()

    def _save_tester_registry(self, registry_data=None):
        """
        Persist tester list to bento_testers.json (both local and shared).

        Item 18: When ``registry_data`` is None, we now read the existing
        registry from disk and preserve all original fields instead of
        reconstructing from the listbox with hardcoded defaults.
        """
        registry_path = self.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
        try:
            os.makedirs(os.path.dirname(registry_path), exist_ok=True)

            if registry_data is None:
                # Read existing registry to preserve original values
                if os.path.exists(registry_path):
                    with open(registry_path, "r") as f:
                        registry_data = json.load(f)
                else:
                    registry_data = {}

            with open(registry_path, "w") as f:
                json.dump(registry_data, f, indent=4)
            self.log("✓ Tester registry updated.")
        except Exception as e:
            self.show_error("Registry Error", f"Could not save registry:\n{e}")

    def _get_selected_hostnames(self):
        selection = self._tester_listbox.curselection()
        hostnames = []
        for idx in selection:
            label = self._tester_listbox.get(idx)
            hostname = label.split(" (")[0].strip()
            hostnames.append(hostname)
        return hostnames

    def _get_selected_testers(self):
        """Return list of (hostname, env) tuples from the selected listbox items."""
        selection = self._tester_listbox.curselection()
        testers = []
        for idx in selection:
            item = self._tester_listbox.get(idx)
            if " (" in item and item.endswith(")"):
                hostname = item.split(" (")[0].strip()
                env = item.split(" (")[1][:-1]
                testers.append((hostname, env))
        return testers

    def _start_compile(self):
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        # Original reads source_dir from impl_repo_var
        source_dir = self.context.get_var("impl_repo_var").get().strip()
        _raw_zip = self.context.get_var("compile_raw_zip").get().strip()
        shared_folder = os.path.dirname(_raw_zip.rstrip("/\\"))
        label = self.context.get_var("tgz_label_var").get().strip()
        hostnames = self._get_selected_hostnames()
        testers = self._get_selected_testers()

        if not source_dir:
            self.show_error("Input Error", "Select a local repo path (Implementation tab).")
            return
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Input Error", "Enter a JIRA key (Home tab).")
            return
        if not hostnames:
            self.show_error("Input Error", "Select at least one tester.")
            return

        ctrl = getattr(self.context.controller, "compile_controller", None)
        if ctrl is None:
            self.show_error("Error", "CompileController is not initialised.")
            return

        # A23: Confirmation dialog before compile
        raw_zip = self.context.get_var("compile_raw_zip").get().strip()
        tester_list = "\n".join(f"  • {h} ({e})" for h, e in testers)
        summary = (
            f"JIRA Key:   {issue_key}\n"
            f"Repo Path:  {source_dir}\n"
            f"RAW_ZIP:    {raw_zip}\n"
            f"TGZ Label:  {label or '(default)'}\n"
            f"\nTarget Tester(s) ({len(testers)}):\n{tester_list}"
        )
        if not messagebox.askyesno(
            "Confirm Compilation",
            f"Start compilation with the following settings?\n\n{summary}",
            parent=self.root,
        ):
            return

        if len(testers) > 1:
            # Multiple testers — open dialog for per-tester TGZ labels
            self._open_multi_label_dialog(testers, ctrl, source_dir, issue_key, shared_folder, label)
        else:
            # Single tester — use the default label directly
            self.lock_gui()
            # A5: Visual feedback on compile button
            self.compile_btn.config(text="⏳ Compiling...")
            self.compile_status_var.set("Running...")
            self.log(f"[Compile] Starting compile for {issue_key} on {len(hostnames)} tester(s)…")

            ctrl.start_compile(
                source_dir=source_dir,
                jira_key=issue_key,
                shared_folder=shared_folder,
                label=label,
                hostnames=hostnames,
            )

    def _open_multi_label_dialog(self, targets, ctrl, source_dir, issue_key, shared_folder, default_label):
        """
        Open a modal dialog to set per-tester TGZ labels before multi-compile.
        Ported from legacy gui/app.py _open_multi_label_dialog().
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Set Custom TGZ Labels")
        h = 180 + len(targets) * 40
        dialog.geometry(f"500x{h}")
        dialog.grab_set()
        self._centre_dialog(dialog, 500, h)

        ttk.Label(dialog, text="Set Custom TGZ Labels",
                  font=("Segoe UI", 12, "bold")).pack(pady=(15, 5))
        ttk.Label(dialog,
                  text="Enter a specific TGZ label for each tester (leave blank for no label):"
                  ).pack(pady=(0, 10))

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True, padx=20)

        label_vars = {}
        for i, (hostname, env) in enumerate(targets):
            ttk.Label(frame, text=f"{hostname} ({env}):", width=25,
                      font=('Segoe UI', 10, 'bold')).grid(row=i, column=0, pady=5, sticky=tk.W)
            var = tk.StringVar(value=default_label)
            label_vars[hostname] = var
            ttk.Entry(frame, textvariable=var, width=30).grid(row=i, column=1, pady=5, sticky=tk.W)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15)

        def on_confirm():
            labels = {h: label_vars[h].get().strip() for h in label_vars}
            dialog.destroy()
            hostnames = [h for h, _ in targets]
            self.lock_gui()
            # A5: Visual feedback on compile button
            self.compile_btn.config(text="⏳ Compiling...")
            self.compile_status_var.set("Running...")
            self.log(f"[Compile] Starting multi-compile for {issue_key} on {len(hostnames)} tester(s)…")
            ctrl.start_compile(
                source_dir=source_dir,
                jira_key=issue_key,
                shared_folder=shared_folder,
                label=default_label,
                hostnames=hostnames,
                labels=labels,
            )

        ttk.Button(btn_frame, text="Confirm & Compile",
                   command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel",
                   command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def _refresh_health(self):
        import os
        raw_zip = self.context.get_var("compile_raw_zip").get().strip()
        release_tgz = self.context.get_var("compile_release_tgz").get().strip()

        # Resolve repo_dir from the selected tester's registry entry.
        # Falls back to "" (no lock-file check) if the registry can't be read
        # or no tester is registered — avoids checking a wrong hardcoded path.
        repo_dir = self.context.config.get("default_repo_dir", "")
        try:
            registry_path = self.context.config.get(
                "registry_path", r"P:\temp\BENTO\bento_testers.json")
            if os.path.exists(registry_path):
                with open(registry_path, "r") as _rf:
                    _reg = json.load(_rf)
                # Try the currently selected tester first
                sel = self._tester_listbox.curselection()
                if sel:
                    sel_label = self._tester_listbox.get(sel[0])
                    entry = _reg.get(sel_label)
                    if entry:
                        if isinstance(entry, dict):
                            repo_dir = entry.get("repo_dir", repo_dir)
                        elif isinstance(entry, list) and len(entry) > 2:
                            repo_dir = entry[2]
                elif _reg:
                    # No selection — use the first entry
                    first = next(iter(_reg.values()))
                    if isinstance(first, dict):
                        repo_dir = first.get("repo_dir", repo_dir)
                    elif isinstance(first, list) and len(first) > 2:
                        repo_dir = first[2]
        except Exception:
            pass  # keep whatever repo_dir resolved to above

        # 1. Folder Reachability
        if os.path.isdir(raw_zip):
            self.health_raw_zip_lbl.config(text="✅ Reachable  " + raw_zip, foreground="#28a745")
        else:
            self.health_raw_zip_lbl.config(text="❌ NOT REACHABLE: " + raw_zip, foreground="#dc3545")

        if os.path.isdir(release_tgz):
            self.health_release_lbl.config(text="✅ Reachable  " + release_tgz, foreground="#28a745")
        else:
            self.health_release_lbl.config(text="❌ NOT REACHABLE: " + release_tgz, foreground="#dc3545")

        # 2. Watcher Process & Locks
        lock_path = os.path.join(repo_dir, ".bento_build_lock") if repo_dir else ""
        local_lock_msg = ""
        if lock_path and os.path.exists(lock_path):
            age = int(time.time() - os.path.getmtime(lock_path))
            local_lock_msg = f"Local lock ({age}s) "

        active_locks = []
        if os.path.isdir(raw_zip):
            try:
                for f in os.listdir(raw_zip):
                    if f.endswith(".bento_lock"):
                        parts = f.split("_")
                        if len(parts) >= 3:
                            active_locks.append(f"{parts[1]}")
                        else:
                            active_locks.append("Active")
            except Exception:
                pass

        if active_locks or local_lock_msg:
            locks_str = ", ".join(active_locks)
            msg = "🟡 Processing: " + locks_str if locks_str else "🟡 Processing..."
            if local_lock_msg:
                msg = "🔒 " + local_lock_msg + "| " + msg
            self.health_watcher_lbl.config(text=msg, foreground="#fd7e14")
        else:
            self.health_watcher_lbl.config(text="✅ Idle (no active builds)", foreground="#28a745")

        # 3. Recent Builds List — A9: increased to 5 entries
        try:
            self.builds_text.config(state="normal")
            self.builds_text.delete("1.0", tk.END)

            all_builds = []
            if os.path.isdir(raw_zip):
                try:
                    for f in os.listdir(raw_zip):
                        if f.endswith(".bento_status"):
                            base_name = f.replace(".bento_status", "")
                            mtime = os.path.getmtime(os.path.join(raw_zip, f))
                            all_builds.append((base_name, f, mtime))
                        elif f.endswith(".zip") and not f.endswith(".bento_lock"):
                            mtime = os.path.getmtime(os.path.join(raw_zip, f))
                            all_builds.append((f, None, mtime))
                except Exception:
                    pass

            # Sort by mtime descending
            all_builds.sort(key=lambda x: x[2], reverse=True)

            # Deduplicate by core name
            seen_bases = set()
            unique_count = 0
            for base_name, status_file, mtime in all_builds:
                core_name = base_name.replace(".zip", "") if base_name.endswith(".zip") else base_name
                if core_name not in seen_bases:
                    seen_bases.add(core_name)
                    unique_count += 1

                    state = "unknown"
                    detail = "No status file yet (Waiting for watcher...)"

                    if status_file:
                        full_status_path = os.path.join(raw_zip, status_file)
                        try:
                            with open(full_status_path, 'r') as sf:
                                sdata = json.load(sf)
                                state = sdata.get("status", "unknown")
                                detail = sdata.get("detail", "")
                        except Exception:
                            detail = "Status parse error"
                    else:
                        state = "pending"

                    display_name = base_name if base_name.endswith(".zip") else base_name + ".zip"
                    self.builds_text.insert(tk.END, f"{display_name}\n", "default")
                    self.builds_text.insert(tk.END, f"↳ {state.upper()} — {detail[:70]}\n", state)

                    # A9: Show up to 5 recent builds instead of 3
                    if unique_count >= 5:
                        break

            if unique_count == 0:
                self.builds_text.insert(tk.END, "(no recent builds found)\n", "unknown")

        except Exception as e:
            self.builds_text.insert(tk.END, f"Refresh error: {e}", "failed")
        finally:
            self.builds_text.config(state="disabled")

        # A8: Update last-refreshed timestamp
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self._health_last_refresh_var.set(f"Auto-refresh active · Last updated: {now_str}")

        # Auto-refresh loop — scheduled in a try/finally so any earlier
        # exception in _refresh_health cannot break the loop.
        try:
            self._schedule_health_refresh()
        except Exception:
            # Last-resort: keep the loop alive even if scheduling fails
            self.after(30000, self._refresh_health)

    def _schedule_health_refresh(self):
        """Schedule next health refresh only if the Compilation sub-tab is
        currently visible.  This avoids wasted I/O when the user is on
        another tab.  The loop re-checks every 30 s regardless, so switching
        back to this tab will resume updates within one cycle."""
        try:
            parent_nb = self.master                       # sub-notebook
            if parent_nb and hasattr(parent_nb, 'select'):
                current_tab = parent_nb.select()          # widget path
                if str(self) == str(current_tab):
                    self.after(30000, self._refresh_health)
                    return
            # Tab not visible — check again in 30 s but skip the refresh
            self.after(30000, self._schedule_health_refresh)
        except Exception:
            # Fallback: always refresh if we can't determine visibility
            self.after(30000, self._refresh_health)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_compile_started(self, hostname: str, env: str):
        # A5: Update button text
        self.compile_btn.config(text="⏳ Compiling...")
        self.log(f"⚙ Compile started → {hostname} ({env})")

    def on_compile_completed(self, hostname: str, env: str, result: dict):
        status = result.get("status", "failed").upper()
        elapsed = result.get("elapsed", 0)
        jira_key = self.context.get_var("issue_var").get().strip()
        # Use tgz_file from result (actual filename), fall back to label var
        tgz_file = result.get("tgz_file", "") or ""
        label = self.context.get_var("tgz_label_var").get().strip()
        if not tgz_file:
            tgz_file = f"{status} ({elapsed}s)"

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        # A12: Columns now include Status: ("Timestamp", "Status", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        # Only failures get a color tag; SUCCESS stays default black
        tags = ("status_failed",) if status != "SUCCESS" else ()
        self._history_tree.insert("", 0, values=(ts, status, jira_key, hostname, env, label, tgz_file),
                                  tags=tags)

        self.unlock_gui()
        # A5: Restore compile button text
        self.compile_btn.config(text="🚀 Compile on Selected Tester(s)")
        self.compile_status_var.set("")

        self.log(f"{'✓' if status == 'SUCCESS' else '✗'} Compile {status} → {hostname} ({env}): {result.get('detail', '')}")

    # ──────────────────────────────────────────────────────────────────────
    # A11: COLUMN SORTING FOR COMPILE HISTORY
    # ──────────────────────────────────────────────────────────────────────

    def _sort_history(self, col):
        """Sort the history treeview by the clicked column heading."""
        # Toggle direction if same column clicked again
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False

        # Get all items
        items = [(str(self._history_tree.set(k, col)), k) for k in self._history_tree.get_children("")]
        items.sort(reverse=self._sort_reverse, key=lambda t: t[0].lower())

        # Rearrange items in sorted order
        for index, (val, k) in enumerate(items):
            self._history_tree.move(k, "", index)
            # Re-apply alternating row colors
            tag = "even" if index % 2 == 0 else "odd"
            # Preserve status color tag
            existing_tags = list(self._history_tree.item(k, "tags"))
            status_tags = [t for t in existing_tags if t.startswith("status_")]
            self._history_tree.item(k, tags=(tag, *status_tags))

        # Update heading to show sort direction
        arrow = " ▼" if self._sort_reverse else " ▲"
        hist_cols = ("Timestamp", "Status", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        for c in hist_cols:
            text = c + (arrow if c == col else "")
            self._history_tree.heading(c, text=text,
                                       command=lambda c=c: self._sort_history(c))

    # ──────────────────────────────────────────────────────────────────────
    # Item 19: RIGHT-CLICK CONTEXT MENU FOR COMPILE HISTORY
    # ──────────────────────────────────────────────────────────────────────

    def _history_context_menu(self, event):
        """Show a right-click context menu with Copy Row option."""
        item_id = self._history_tree.identify_row(event.y)
        if not item_id:
            return
        self._history_tree.selection_set(item_id)

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="📋 Copy Row", command=self._copy_history_row)
        menu.add_command(label="📂 Open Folder", command=self._open_release_folder)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_history_row(self):
        """Copy the selected history row to clipboard as tab-separated text."""
        selected = self._history_tree.selection()
        if not selected:
            return
        values = self._history_tree.item(selected[0], "values")
        cols = ("Timestamp", "Status", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        lines = ["\t".join(cols), "\t".join(str(v) for v in values)]
        text = "\n".join(lines)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log("📋 History row copied to clipboard")

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE HISTORY LOGIC
    # ──────────────────────────────────────────────────────────────────────

    def _open_release_folder(self):
        """Open the release folder for the selected history row.

        Item 13: Uses the Output TGZ path from the tree row to locate the
        actual folder on disk, instead of guessing the folder name from
        tester/jira/env columns (which may not match the real directory).
        Falls back to a fuzzy search when the TGZ path is unavailable.
        """
        import os

        # Get the selected item from the history tree
        selected_items = self._history_tree.selection()
        if not selected_items:
            # If nothing is selected, open the general RELEASE_TGZ folder
            repo_dir = self.context.get_var("compile_release_tgz").get().strip()
            if os.path.isdir(repo_dir):
                os.startfile(repo_dir)
            else:
                self.show_error("Error", f"Release folder not found: {repo_dir}")
            return

        # Get the selected item's values
        item_id = selected_items[0]
        values = self._history_tree.item(item_id, "values")

        # A12: Column indices shifted by 1 due to new Status column
        tester   = values[3]    # Index 3 is the "Tester" column
        env      = values[4]    # Index 4 is the "ENV" column
        jira_key = values[2]    # Index 2 is the "JIRA" column
        output_tgz = values[6]  # Index 6 is the "Output TGZ" column

        # Get the base RELEASE_TGZ directory
        base_dir = self.context.get_var("compile_release_tgz").get().strip()

        target_dir = None

        # Strategy 1: Derive folder from the Output TGZ path stored in the row.
        # The TGZ file lives inside the release subfolder, so its parent is
        # the folder we want to open.
        if output_tgz and output_tgz.strip():
            tgz_path = output_tgz.strip()
            # If it's an absolute path, use its parent directly
            if os.path.isabs(tgz_path):
                candidate = os.path.dirname(tgz_path)
                if os.path.isdir(candidate):
                    target_dir = candidate
            # If it's a relative name, look under base_dir for a folder
            # containing a file with that name
            if not target_dir and os.path.isdir(base_dir):
                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if os.path.isdir(item_path):
                        if os.path.isfile(os.path.join(item_path, tgz_path)):
                            target_dir = item_path
                            break

        # Strategy 2: Fuzzy match by tester + jira_key in folder name
        if not target_dir and os.path.isdir(base_dir):
            for item in os.listdir(base_dir):
                item_path = os.path.join(base_dir, item)
                if os.path.isdir(item_path) and tester in item and jira_key in item:
                    target_dir = item_path
                    break

        # Open the folder if found, otherwise show an error
        if target_dir and os.path.isdir(target_dir):
            os.startfile(target_dir)
        else:
            self.show_error("Folder Not Found",
                            f"Could not find release folder for "
                            f"{tester} / {jira_key} / {env}")
            # Fall back to opening the base directory
            if os.path.isdir(base_dir):
                os.startfile(base_dir)

    def _refresh_history_from_disk(self):
        """Matches original _load() logic in gui/app.py."""
        release_root = self.context.get_var("compile_release_tgz").get().strip()
        if not os.path.isdir(release_root):
            return

        def _fetch():
            pattern = os.path.join(release_root, "**", "build_info_*.txt")
            try:
                files = glob.glob(pattern, recursive=True)
                files.sort(key=os.path.getmtime, reverse=True)
                self.root.after(0, lambda: self._populate_history(files))
            except RuntimeError:
                # Main loop not ready yet (race condition during startup) — ignore
                pass
            except Exception as e:
                try:
                    self.log(f"✗ Failed to load history: {e}")
                except RuntimeError:
                    pass  # Main loop not ready

        threading.Thread(target=_fetch, daemon=True).start()

    def _populate_history(self, files):
        self._history_tree.delete(*self._history_tree.get_children())
        for i, fpath in enumerate(files[:100]):  # Limit to 100 entries
            row = self._parse_build_info(fpath)
            if row:
                # A12: Only failures get a color tag; SUCCESS stays default black
                status_val = row[1].upper() if len(row) > 1 else ""
                tag = "even" if i % 2 == 0 else "odd"
                if status_val in ("FAILED", "ERROR"):
                    self._history_tree.insert("", tk.END, values=row, tags=(tag, "status_failed"))
                else:
                    self._history_tree.insert("", tk.END, values=row, tags=(tag,))

    def _parse_build_info(self, fpath):
        """Matches original _parse() logic in gui/app.py. A12: Now includes Status column."""
        data = {}
        try:
            with open(fpath, "r") as f:
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        data[k.strip()] = v.strip()

            output_tgz = data.get("Output TGZ", "").strip()
            if not output_tgz:
                output_tgz = os.path.basename(fpath).replace("build_info_", "").replace(".txt", ".tgz")
            # A12: Added Status column (derive from data or default to "N/A")
            status = data.get("Status", "SUCCESS").upper()
            return (
                data.get("Timestamp", "N/A"),
                status,
                data.get("JIRA Key", "N/A"),
                data.get("Tester", "N/A"),
                data.get("Env", "N/A"),
                data.get("Label", "N/A"),
                output_tgz,
            )
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────────
# A13 + A14: IMPROVED ADD TESTER DIALOG
# ──────────────────────────────────────────────────────────────────────

class AddTesterDialog:
    """
    Modal dialog to register a new tester.

    A13: Reduced height, collapsible preflight checklist.
    A14: Hostname format validation with visual feedback.
    """

    # A14: Hostname pattern (e.g., IBIR-0999, PION-1234, TEST-0001)
    _HOSTNAME_PATTERN = re.compile(r'^[A-Z]{2,8}-\d{2,6}$')

    def __init__(self, root, parent_tab):
        self.root = root
        self.parent_tab = parent_tab

        self.dialog = tk.Toplevel(root)
        self.dialog.title("Add New Tester")
        # A13: Reduced from 560x640 to 520x520, resizable
        self.dialog.geometry("520x520")
        self.dialog.resizable(True, True)
        self.dialog.minsize(400, 400)
        self.dialog.grab_set()

        self.parent_tab._centre_dialog(self.dialog, 520, 520)

        # Container frame
        self.main_frame = ttk.Frame(self.dialog, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame.columnconfigure(1, weight=1)

        # ── Header ──
        ttk.Label(self.main_frame, text="Register New Tester",
                  font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(2, 6))

        ttk.Separator(self.main_frame, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="we", pady=(0, 6))

        # ── A13: Collapsible Preflight Checklist ──
        self._checklist_visible = tk.BooleanVar(value=False)
        checklist_toggle = ttk.Checkbutton(
            self.main_frame, text="⚠ Show Preflight Checklist",
            variable=self._checklist_visible,
            command=self._toggle_checklist,
        )
        checklist_toggle.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self._checklist_frame = ttk.LabelFrame(self.main_frame, text="⚠ Preflight Checklist", padding=8)
        self._checklist_frame.grid(row=3, column=0, columnspan=3, sticky="we", pady=2)
        self._checklist_frame.grid_remove()  # Hidden by default

        ttk.Label(self._checklist_frame,
                  text="Before registering, confirm the tester is ready:",
                  font=("Segoe UI", 9, "bold"), foreground="#cc6600",
                  wraplength=460).pack(anchor=tk.W, pady=(0, 4))

        self.check_vars = []
        checklist_items = [
            "Watcher files visible at P:\\temp\\BENTO\\watcher\\",
            "Watcher running (Task Scheduler configured on tester)",
            "Shared folder P:\\temp\\BENTO accessible from tester"
        ]
        for text in checklist_items:
            v = tk.BooleanVar(value=False)
            self.check_vars.append(v)
            ttk.Checkbutton(self._checklist_frame, text=text, variable=v).pack(anchor=tk.W, pady=1)

        def open_guide():
            import os, sys, webbrowser
            from tkinter import messagebox
            pdf_name = "BENTO_Watcher_Setup_Guide.pdf"
            candidates = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), pdf_name),
                os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), pdf_name),
                os.path.join(os.getcwd(), pdf_name),
            ]
            guide_path = None
            for path in candidates:
                if os.path.exists(path):
                    guide_path = path
                    break
            if guide_path:
                try:
                    os.startfile(guide_path)
                except Exception:
                    webbrowser.open("file:///" + guide_path.replace("\\", "/"))
            else:
                messagebox.showwarning("Guide Not Found",
                    f"Setup guide PDF not found.\n\nSearched:\n" +
                    "\n".join(candidates))

        ttk.Button(self._checklist_frame, text="📄 Open Setup Guide (PDF)", command=open_guide).pack(anchor=tk.W, pady=(6, 0))

        ttk.Separator(self.main_frame, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="we", pady=8)

        # ── Tester Details ──
        ttk.Label(self.main_frame, text="Tester Hostname:", font=("Segoe UI", 9)).grid(
            row=5, column=0, sticky="w", padx=10, pady=4)
        self.hostname_var = tk.StringVar()
        self.hostname_entry = ttk.Entry(self.main_frame, textvariable=self.hostname_var, width=20)
        self.hostname_entry.grid(row=5, column=1, sticky="we", padx=10, pady=4)
        # A14: Validation indicator label
        self._hostname_valid_lbl = ttk.Label(self.main_frame, text="", width=3)
        self._hostname_valid_lbl.grid(row=5, column=2, padx=(0, 10))
        ttk.Label(self.main_frame, text="Format: XXXX-0000  (e.g. IBIR-0999)",
                  font=("Segoe UI", 8), foreground="gray").grid(
            row=6, column=1, columnspan=2, sticky="w", padx=10, pady=0)

        ttk.Label(self.main_frame, text="Environment:", font=("Segoe UI", 9)).grid(
            row=7, column=0, sticky="w", padx=10, pady=4)
        self.env_var = tk.StringVar(value="ABIT")
        env_combo = ttk.Combobox(self.main_frame, textvariable=self.env_var,
                                 values=["ABIT", "SFN2", "CNFG"], state="readonly", width=12)
        env_combo.grid(row=7, column=1, sticky="w", padx=10, pady=4)

        ttk.Label(self.main_frame, text="Repo Path:", font=("Segoe UI", 9)).grid(
            row=8, column=0, sticky="w", padx=10, pady=4)
        self.repo_dir_var = tk.StringVar(value=r"C:\BENTO\adv_ibir_master")
        repo_frame = ttk.Frame(self.main_frame)
        repo_frame.grid(row=8, column=1, columnspan=2, sticky="we", padx=10, pady=4)
        repo_frame.columnconfigure(0, weight=1)
        ttk.Entry(repo_frame, textvariable=self.repo_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(repo_frame, text="📁", width=3,
                   command=lambda: self.parent_tab._browse_directory(self.repo_dir_var, "Select TP Repository")).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Label(self.main_frame, text="Build Command:", font=("Segoe UI", 9)).grid(
            row=9, column=0, sticky="w", padx=10, pady=4)
        self.build_cmd_var = tk.StringVar(value="make release")
        build_combo = ttk.Combobox(self.main_frame, textvariable=self.build_cmd_var,
                                   values=["make release", "make release_supermicro"], width=28)
        build_combo.grid(row=9, column=1, sticky="w", padx=10, pady=4)

        ttk.Separator(self.main_frame, orient="horizontal").grid(
            row=10, column=0, columnspan=3, sticky="we", pady=8)

        # ── Error label ──
        self.err_var = tk.StringVar(value="")
        self._err_lbl = ttk.Label(self.main_frame, textvariable=self.err_var,
                                  foreground="#cc0000", font=("Segoe UI", 9))
        self._err_lbl.grid(row=11, column=0, columnspan=3, padx=10)

        # ── Buttons ──
        btn_row = ttk.Frame(self.main_frame)
        btn_row.grid(row=12, column=0, columnspan=3, pady=10)

        self.add_btn = ttk.Button(btn_row, text="Add Tester", command=self._confirm, width=14)
        self.add_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.dialog.destroy, width=14).pack(side=tk.LEFT, padx=6)

        # A14: Real-time hostname validation
        self.hostname_var.trace_add("write", self._validate_hostname)

        self.hostname_entry.focus_set()

    def _toggle_checklist(self):
        """A13: Show/hide the preflight checklist."""
        if self._checklist_visible.get():
            self._checklist_frame.grid()
        else:
            self._checklist_frame.grid_remove()

    def _validate_hostname(self, *_):
        """A14: Real-time hostname format validation with visual indicator."""
        hostname = self.hostname_var.get().strip().upper()
        if not hostname:
            self._hostname_valid_lbl.config(text="", foreground="gray")
            return
        if self._HOSTNAME_PATTERN.match(hostname):
            self._hostname_valid_lbl.config(text="✓", foreground="#107c10")
            self.err_var.set("")
        else:
            self._hostname_valid_lbl.config(text="✗", foreground="#cc0000")

    def _confirm(self):
        hostname = self.hostname_var.get().strip().upper()
        env      = self.env_var.get().strip().upper()
        repo_dir = self.repo_dir_var.get().strip()
        build_cmd = self.build_cmd_var.get().strip()

        if not hostname:
            self.err_var.set("⚠ Hostname cannot be empty.")
            return

        # A14: Validate hostname format (warn but don't block)
        if not self._HOSTNAME_PATTERN.match(hostname):
            if not messagebox.askyesno(
                "Non-Standard Hostname",
                f"'{hostname}' doesn't match the expected format (e.g. IBIR-0999).\n\n"
                f"Continue anyway?",
                parent=self.dialog
            ):
                return

        key = f"{hostname} ({env})"

        registry_path = self.parent_tab.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
        registry_data = {}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    registry_data = json.load(f)
            except Exception: pass

        if key in registry_data:
            self.err_var.set(f"⚠ Tester '{key}' is already registered.")
            return

        registry_data[key] = {
            "hostname": hostname,
            "env": env,
            "repo_dir": repo_dir,
            "build_cmd": build_cmd
        }

        self.parent_tab._save_tester_registry(registry_data)
        self.parent_tab._refresh_testers()
        self.parent_tab.log(f"[Tester Added] {key} registered.")
        self.dialog.destroy()
