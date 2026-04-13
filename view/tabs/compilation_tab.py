#!/usr/bin/env python3
"""
view/tabs/compilation_tab.py
=============================
Compilation Tab (View) — Standalone tab for TP Compilation & Health
Extracted from implementation_tab.py to be a main-level tab.
"""

import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import logging

from view.tabs.base_tab import BaseTab

logger = logging.getLogger("bento_app")


class CompilationTab(BaseTab):
    """
    Standalone Compilation tab for TP Package compilation and health monitoring.
    
    Layout:
      1. Compile TP Package (Target Testers, Configuration, Action)
      2. Force Fail TGZ Section
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
        
        # 1. Compile TP Package (Top)
        compile_frame = ttk.LabelFrame(main_frame, text="Compile TP Package", padding="6")
        compile_frame.pack(fill=tk.X, padx=10, pady=5)
        compile_frame.columnconfigure(0, weight=1)
        compile_frame.columnconfigure(1, weight=1)

        # 1. Target Testers
        targets_frame = ttk.LabelFrame(compile_frame, text="1. Target Testers", padding="6")
        targets_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 5))
        targets_frame.columnconfigure(0, weight=1)

        search_frame = ttk.Frame(targets_frame)
        search_frame.grid(row=0, column=0, sticky="we", pady=(0, 5))
        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        self._tester_search_var = tk.StringVar()
        self._tester_search_var.trace_add("write", self._filter_testers)
        self._tester_search_entry = ttk.Entry(search_frame, textvariable=self._tester_search_var, width=15)
        self._tester_search_entry.pack(side=tk.LEFT)
        self._tester_search_entry.insert(0, "Search...")
        self._tester_search_entry.config(foreground="gray")
        self._tester_search_entry.bind("<FocusIn>", lambda e: self._tester_search_entry.delete(0, tk.END) if self._tester_search_entry.get() == "Search..." else None)
        self._tester_search_entry.bind("<FocusOut>", lambda e: (self._tester_search_entry.insert(0, "Search..."), self._tester_search_entry.config(foreground="gray")) if not self._tester_search_entry.get() else None)
        ttk.Label(search_frame, text="(Shift+Click for multi)", font=("Arial", 8, "italic"), foreground="gray").pack(side=tk.LEFT, padx=(5, 0))

        list_frame = ttk.Frame(targets_frame)
        list_frame.grid(row=1, column=0, sticky="we")
        self._tester_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, height=3, width=28, exportselection=False)
        self._tester_listbox.pack(side=tk.LEFT, fill=tk.Y)
        self._tester_listbox.bind("<<ListboxSelect>>", self._on_tester_selected)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tester_listbox.yview)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self._tester_listbox.config(yscrollcommand=scrollbar.set)
        
        btn_frame_list = ttk.Frame(list_frame)
        btn_frame_list.pack(side=tk.LEFT, padx=(5, 0), anchor=tk.N)
        ttk.Button(btn_frame_list, text="+ Add Tester", width=12, command=self._add_tester).pack(pady=(0, 5))
        ttk.Button(btn_frame_list, text="🗑 Remove", width=12, command=self._remove_tester).pack(pady=(0, 5))

        info_frame = ttk.Frame(targets_frame)
        info_frame.grid(row=2, column=0, sticky="we", pady=(5, 0))
        ttk.Label(info_frame, text="Selected:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        self._compile_mode_var = tk.StringVar(value="No tester selected")
        self._compile_mode_lbl = ttk.Label(info_frame, textvariable=self._compile_mode_var, font=("Arial", 9, "italic"), foreground="#cc0000")
        self._compile_mode_lbl.pack(side=tk.LEFT)

        # 2. Configuration & Paths
        config_frame = ttk.LabelFrame(compile_frame, text="2. Configuration & Paths", padding="6")
        config_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 5))
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="TGZ Label:").grid(row=0, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        self.context.set_var("tgz_label_var", tk.StringVar())
        ttk.Entry(config_frame, textvariable=self.context.get_var("tgz_label_var"), width=22).grid(row=0, column=1, sticky=tk.W, pady=2)
        ttk.Label(config_frame, text="(blank = default)", font=("Arial", 8), foreground="gray").grid(row=0, column=2, sticky=tk.W, padx=2)

        ttk.Label(config_frame, text="RAW_ZIP:").grid(row=1, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        raw_zip_frame = ttk.Frame(config_frame)
        raw_zip_frame.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        self.context.set_var("compile_raw_zip", tk.StringVar(value=r"P:\temp\BENTO\RAW_ZIP"))
        ttk.Entry(raw_zip_frame, textvariable=self.context.get_var("compile_raw_zip"), width=28).pack(side=tk.LEFT)
        ttk.Button(raw_zip_frame, text="📁", width=3, command=self._browse_raw_zip).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Label(config_frame, text="RELEASE_TGZ:").grid(row=2, column=0, sticky=tk.W, pady=2, padx=(0, 5))
        release_tgz_frame = ttk.Frame(config_frame)
        release_tgz_frame.grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        self.context.set_var("compile_release_tgz", tk.StringVar(value=r"P:\temp\BENTO\RELEASE_TGZ"))
        ttk.Entry(release_tgz_frame, textvariable=self.context.get_var("compile_release_tgz"), width=28).pack(side=tk.LEFT)
        ttk.Button(release_tgz_frame, text="📁", width=3, command=self._browse_release_tgz).pack(side=tk.LEFT, padx=(2, 0))

        # 3. Action Frame
        action_frame = ttk.Frame(compile_frame)
        action_frame.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 5))
        btn_container = ttk.Frame(action_frame)
        btn_container.pack(expand=True)
        
        style = ttk.Style()
        style.configure('Compile.TButton')
        self.compile_btn = ttk.Button(btn_container, text="🚀 Compile on Selected Tester(s)", style='Compile.TButton', command=self._start_compile, width=35)
        self.compile_btn.pack(pady=(0, 2))
        self.context.lockable_buttons.append(self.compile_btn)
        self.compile_status_var = tk.StringVar(value="")
        ttk.Label(btn_container, textvariable=self.compile_status_var, font=("Arial", 9, "bold"), foreground="#0066cc").pack()

        self._refresh_testers()

        # 4. Force Fail Section (between action and health monitor)
        self._build_force_fail_section(compile_frame)

        # 2. Watcher Health Monitor (Middle)
        self.health_wrapper = ttk.LabelFrame(main_frame, text="🔍 Watcher Health Monitor", padding="6")
        self.health_wrapper.pack(fill=tk.X, padx=10, pady=5)
        
        row_frame1 = ttk.Frame(self.health_wrapper)
        row_frame1.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame1, text="📂 RAW_ZIP Folder:", width=28, anchor="w", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.health_raw_zip_lbl = ttk.Label(row_frame1, text="Checking...", foreground="gray")
        self.health_raw_zip_lbl.pack(side=tk.LEFT)

        row_frame2 = ttk.Frame(self.health_wrapper)
        row_frame2.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame2, text="📂 RELEASE_TGZ Folder:", width=28, anchor="w", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.health_release_lbl = ttk.Label(row_frame2, text="Checking...", foreground="gray")
        self.health_release_lbl.pack(side=tk.LEFT)

        row_frame3 = ttk.Frame(self.health_wrapper)
        row_frame3.pack(fill=tk.X, pady=2)
        ttk.Label(row_frame3, text="🤖 Watcher Process:", width=28, anchor="w", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.health_watcher_lbl = ttk.Label(row_frame3, text="Checking...", foreground="gray")
        self.health_watcher_lbl.pack(side=tk.LEFT)

        recent_frame = ttk.Frame(self.health_wrapper)
        recent_frame.pack(fill=tk.X, pady=(3, 2))
        ttk.Label(recent_frame, text="📊 Recent Builds:", width=28, anchor="w", font=("Arial", 9, "bold")).pack(side=tk.LEFT, anchor=tk.N)
        
        self.builds_text = tk.Text(recent_frame, height=6, width=65, bg="white", relief="flat", font=("Segoe UI", 9))
        self.builds_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.builds_text.tag_config("success",     foreground="#28a745")
        self.builds_text.tag_config("failed",      foreground="#dc3545")
        self.builds_text.tag_config("in_progress", foreground="#fd7e14")
        self.builds_text.tag_config("pending",     foreground="#fd7e14")
        self.builds_text.tag_config("timeout",     foreground="purple")
        self.builds_text.tag_config("unknown",     foreground="gray")
        self.builds_text.tag_config("default",     foreground="black")

        ttk.Button(self.health_wrapper, text="🔄 Refresh Now", command=self._refresh_health).pack(anchor="e", padx=5, pady=2)
        self._refresh_health()

        # 3. Compile History Section (Bottom - Fills remainder)
        history_wrapper = ttk.LabelFrame(main_frame, text="📋 Compile History", padding="6")
        history_wrapper.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        history_wrapper.columnconfigure(0, weight=1)

        # Toolbar (matches original)
        toolbar = ttk.Frame(history_wrapper)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 2))
        ttk.Label(toolbar, text="Past compilations from RELEASE_TGZ",
                  font=("Arial", 8), foreground="gray").pack(side=tk.LEFT)
        
        ttk.Button(toolbar, text="🔄 Refresh", 
                   command=self._refresh_history_from_disk).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="📁 Open Folder",
                   command=self._open_release_folder).pack(side=tk.RIGHT, padx=5)

        # Treeview (height=6 to save space, columns match original)
        hist_cols = ("Timestamp", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        self._history_tree = ttk.Treeview(history_wrapper, columns=hist_cols, show="headings", height=4)
        
        # Column widths matching original
        col_widths = [140, 120, 110, 70, 100, 300]
        for col, width in zip(hist_cols, col_widths):
            self._history_tree.heading(col, text=col)
            self._history_tree.column(col, width=width, anchor="w")

        # Row colors (even/odd tags match original)
        self._history_tree.tag_configure("even", background="#f5f5f5")
        self._history_tree.tag_configure("odd",  background="#ffffff")

        hist_scroll = ttk.Scrollbar(history_wrapper, orient=tk.VERTICAL, command=self._history_tree.yview)
        self._history_tree.configure(yscrollcommand=hist_scroll.set)
        
        # Add double-click handler to open the specific folder
        self._history_tree.bind("<Double-1>", lambda e: self._open_release_folder())
        
        self._history_tree.grid(row=1, column=0, sticky="nsew")
        hist_scroll.grid(row=1, column=1, sticky="ns")
        
        history_wrapper.rowconfigure(1, weight=1)
        
        # Initial history load
        self._refresh_history_from_disk()

    # ──────────────────────────────────────────────────────────────────────
    # FORCE FAIL SECTION
    # ──────────────────────────────────────────────────────────────────────

    def _build_force_fail_section(self, parent):
        """Build the force-fail UI section inside the compile LabelFrame."""
        ff_frame = ttk.LabelFrame(parent, text="❌ Force Fail TGZ", padding="6")
        ff_frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=(0, 5))
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

        self._ff_generate_btn = ttk.Button(
            ctrl_row, text="🤖 Generate Force Fail Cases",
            command=self._generate_force_fail, state=tk.DISABLED,
        )
        self._ff_generate_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.context.lockable_buttons.append(self._ff_generate_btn)

        self._ff_compile_btn = ttk.Button(
            ctrl_row, text="🚀 Compile Force Fail TGZ",
            command=self._compile_force_fail, state=tk.DISABLED,
        )
        self._ff_compile_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.context.lockable_buttons.append(self._ff_compile_btn)

        self._ff_clear_btn = ttk.Button(
            ctrl_row, text="🗑 Clear", command=self._clear_ff_cases,
            state=tk.DISABLED, width=8,
        )
        self._ff_clear_btn.pack(side=tk.LEFT)

        # Row 1: Status label
        self._ff_status_var = tk.StringVar(value="Force fail disabled")
        self._ff_status_lbl = ttk.Label(
            ff_frame, textvariable=self._ff_status_var,
            font=("Arial", 9, "italic"), foreground="gray",
        )
        self._ff_status_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Row 2: Cases list (Treeview for per-case toggles)
        cases_frame = ttk.Frame(ff_frame)
        cases_frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=(0, 4))
        cases_frame.columnconfigure(0, weight=1)

        ff_cols = ("Enabled", "Test ID", "Description", "Files")
        self._ff_tree = ttk.Treeview(
            cases_frame, columns=ff_cols, show="headings", height=3,
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
        self._ff_tree.grid(row=0, column=0, sticky="we")
        ff_scroll.grid(row=0, column=1, sticky="ns")

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
        shared_folder = self.context.get_var("compile_raw_zip").get().strip().replace("\\RAW_ZIP", "")
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

    def _refresh_testers(self):
        self._tester_listbox.delete(0, tk.END)
        ctrl = getattr(self.context.controller, "compile_controller", None)
        testers = ctrl.get_available_testers() if ctrl else []
        for i, (hostname, env) in enumerate(testers):
            self._tester_listbox.insert(tk.END, f"{hostname} ({env})")

    def _filter_testers(self, *_):
        if not hasattr(self, '_tester_listbox'):
            return
        query = self._tester_search_var.get().lower()
        if query == "search...":
            query = ""
        self._tester_listbox.delete(0, tk.END)
        ctrl = getattr(self.context.controller, "compile_controller", None)
        testers = ctrl.get_available_testers() if ctrl else []
        for hostname, env in testers:
            label = f"{hostname} ({env})"
            if query in hostname.lower() or query in env.lower():
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
            self._compile_mode_var.set(f"Multi-compile: {len(selections)} testers - " + ", ".join([self._tester_listbox.get(i) for i in selections]))
            self._compile_mode_lbl.config(foreground="#0066cc")

    def _add_tester(self):
        """Open the custom Add Tester dialog (matches legacy app.py)."""
        AddTesterDialog(self.root, self)

    def _browse_directory(self, var, title):
        """Open directory browser and set the variable."""
        from tkinter import filedialog
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _centre_dialog(self, dialog, w, h):
        """Position dialog at the centre of the main window (matches legacy app.py)."""
        dialog.transient(self.root)
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

    def _remove_tester(self):
        """Remove selected tester(s) with confirmation (matches legacy app.py)."""
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
            import json, os
            registry_path = self.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
            registry_data = {}
            if os.path.exists(registry_path):
                try:
                    with open(registry_path, "r") as f:
                        registry_data = json.load(f)
                except Exception: pass
                
            for key in testers_to_remove:
                if key in registry_data:
                    del registry_data[key]
                    self.log(f"[Tester Removed] {key}")
            
            self._save_tester_registry(registry_data)
            self._refresh_testers()

    def _save_tester_registry(self, registry_data=None):
        """
        Persist tester list to bento_testers.json (both local and shared).
        Directly ports the logic from legacy app.py.
        """
        import json, os
        registry_path = self.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
        try:
            os.makedirs(os.path.dirname(registry_path), exist_ok=True)
            
            # If we didn't get explicit data, we reconstruct from listbox (legacy behavior for removals)
            if registry_data is None:
                registry_data = {}
                for i in range(self._tester_listbox.size()):
                    item = self._tester_listbox.get(i)
                    if " (" in item and item.endswith(")"):
                        hostname, env = item.split(" (", 1)
                        env = env[:-1]
                        # Use defaults for legacy fields if reconstructing from listbox
                        registry_data[item] = {
                            "hostname": hostname,
                            "env": env,
                            "repo_dir": r"C:\BENTO\adv_ibir_master",
                            "build_cmd": "make release"
                        }

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
        shared_folder = self.context.get_var("compile_raw_zip").get().strip().replace("\\RAW_ZIP", "") # Hack to get parent
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

        if len(testers) > 1:
            # Multiple testers — open dialog for per-tester TGZ labels
            self._open_multi_label_dialog(testers, ctrl, source_dir, issue_key, shared_folder, label)
        else:
            # Single tester — use the default label directly
            self.lock_gui()
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
                  font=("Arial", 12, "bold")).pack(pady=(15, 5))
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
        import time
        import json
        
        raw_zip = self.context.get_var("compile_raw_zip").get().strip()
        release_tgz = self.context.get_var("compile_release_tgz").get().strip()
        repo_dir = r"C:\BENTO\adv_ibir_master" # Default as per original
        
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
        lock_path = os.path.join(repo_dir, ".bento_build_lock")
        local_lock_msg = ""
        if os.path.exists(lock_path):
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
            
        # 3. Recent Builds List
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
                    
                    if unique_count >= 3:
                        break
            
            if unique_count == 0:
                self.builds_text.insert(tk.END, "(no recent builds found)\n", "unknown")
                
        except Exception as e:
            self.builds_text.insert(tk.END, f"Refresh error: {e}", "failed")
        finally:
            self.builds_text.config(state="disabled")

        # Auto-refresh loop
        self.after(30000, self._refresh_health)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_compile_started(self, hostname: str, env: str):
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

        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        # Columns: ("Timestamp", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
        self._history_tree.insert("", 0, values=(ts, jira_key, hostname, env, label, tgz_file))

        self.unlock_gui()
        self.compile_status_var.set("")

        self.log(f"{'✓' if status == 'SUCCESS' else '✗'} Compile {status} → {hostname} ({env}): {result.get('detail', '')}")

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE HISTORY LOGIC
    # ──────────────────────────────────────────────────────────────────────

    def _open_release_folder(self):
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
        
        # Extract the tester, env, and output TGZ filename
        tester = values[2]  # Index 2 is the "Tester" column
        env = values[3]      # Index 3 is the "ENV" column
        output_tgz = values[5]  # Index 5 is the "Output TGZ" column
        
        # Construct the folder name based on the naming pattern in the screenshot
        # The pattern appears to be: TESTER_TSESSD-XXXX_ENV
        jira_key = values[1]  # Index 1 is the "JIRA" column
        folder_name = f"{tester}_{jira_key}_{env}"
        
        # Get the base RELEASE_TGZ directory
        base_dir = self.context.get_var("compile_release_tgz").get().strip()
        
        # Look for a matching folder
        target_dir = None
        if os.path.isdir(base_dir):
            # First try the exact folder name
            potential_dir = os.path.join(base_dir, folder_name)
            if os.path.isdir(potential_dir):
                target_dir = potential_dir
            else:
                # Try to find a folder that starts with the tester name
                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if os.path.isdir(item_path) and item.startswith(f"{tester}_"):
                        if jira_key in item:
                            target_dir = item_path
                            break
        
        # Open the folder if found, otherwise show an error
        if target_dir and os.path.isdir(target_dir):
            os.startfile(target_dir)
        else:
            self.show_error("Folder Not Found", f"Could not find specific folder for {tester} {jira_key} {env}")
            # Fall back to opening the base directory
            if os.path.isdir(base_dir):
                os.startfile(base_dir)

    def _refresh_history_from_disk(self):
        """Matches original _load() logic in gui/app.py."""
        import glob
        import os
        import threading
        
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
        for i, fpath in enumerate(files[:100]): # Limit to 100 entries
            row = self._parse_build_info(fpath)
            if row:
                tag = "even" if i % 2 == 0 else "odd"
                self._history_tree.insert("", tk.END, values=row, tags=(tag,))

    def _parse_build_info(self, fpath):
        """Matches original _parse() logic in gui/app.py."""
        data = {}
        try:
            with open(fpath, "r") as f:
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        data[k.strip()] = v.strip()
            
            # Key names written by watcher_copier.py write_build_info():
            #   "Tester"    -> hostname
            #   "JIRA Key"  -> jira_key   (NOT "JIRA")
            #   "Env"       -> env        (NOT "ENV")
            #   "Label"     -> label
            #   "Timestamp" -> timestamp
            # Original cols: ("Timestamp", "JIRA", "Tester", "ENV", "Label", "Output TGZ")
            # Prefer the "Output TGZ" field written by watcher_copier (full filename),
            # fall back to deriving from the build_info filename (label only).
            output_tgz = data.get("Output TGZ", "").strip()
            if not output_tgz:
                output_tgz = os.path.basename(fpath).replace("build_info_", "").replace(".txt", ".tgz")
            return (
                data.get("Timestamp", "N/A"),
                data.get("JIRA Key", "N/A"),
                data.get("Tester", "N/A"),
                data.get("Env", "N/A"),
                data.get("Label", "N/A"),
                output_tgz,
            )
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────────

class AddTesterDialog:
    """
    Modal dialog to register a new tester.
    Final adjustment for exact 1:1 parity and visibility.
    """
    def __init__(self, root, parent_tab):
        self.root = root
        self.parent_tab = parent_tab
        
        self.dialog = tk.Toplevel(root)
        self.dialog.title("Add New Tester")
        self.dialog.geometry("560x640")
        self.dialog.resizable(False, False)
        self.dialog.grab_set()
        
        self.parent_tab._centre_dialog(self.dialog, 560, 640)

        # Container frame
        self.main_frame = ttk.Frame(self.dialog, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame.columnconfigure(1, weight=1)

        # ── Header ──
        ttk.Label(self.main_frame, text="Register New Tester",
                  font=("Arial", 12, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(2, 6))

        ttk.Separator(self.main_frame, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(0, 6))

        # ── Preflight Checklist ──
        checklist_frame = ttk.LabelFrame(self.main_frame, text="⚠ Preflight Checklist", padding=10)
        checklist_frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=2)

        ttk.Label(checklist_frame,
                  text="Before registering, confirm the tester is ready to receive compile jobs:",
                  font=("Arial", 9, "bold"), foreground="#cc6600",
                  wraplength=500).pack(anchor=tk.W, pady=(0, 4))

        self.check_vars = []
        checklist_items = [
            "Watcher files visible at P:\\temp\\BENTO\\watcher\\",
            "Watcher running (Task Scheduler configured on tester)",
            "Shared folder P:\\temp\\BENTO accessible from tester"
        ]
        for text in checklist_items:
            v = tk.BooleanVar(value=False)
            self.check_vars.append(v)
            ttk.Checkbutton(checklist_frame, text=text, variable=v).pack(anchor=tk.W, pady=1)

        def open_guide():
            import os, sys, webbrowser
            from tkinter import messagebox
            pdf_name = "BENTO_Watcher_Setup_Guide.pdf"
            # Try multiple strategies to locate the PDF
            candidates = [
                # 1. Relative to this source file (view/tabs/ -> project root)
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), pdf_name),
                # 2. Relative to main script (e.g. main.py in project root)
                os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), pdf_name),
                # 3. Current working directory
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
        
        ttk.Button(checklist_frame, text="📄 Open Setup Guide (PDF)", command=open_guide).pack(anchor=tk.W, pady=(6, 0))

        ttk.Separator(self.main_frame, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="we", pady=8)

        # ── Tester Details ──
        ttk.Label(self.main_frame, text="Tester Hostname:").grid(row=4, column=0, sticky="w", padx=10, pady=4)
        self.hostname_var = tk.StringVar()
        self.hostname_entry = ttk.Entry(self.main_frame, textvariable=self.hostname_var, width=30)
        self.hostname_entry.grid(row=4, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(self.main_frame, text="e.g.  IBIR-0999", font=("Arial", 8), foreground="gray").grid(row=5, column=1, sticky="w", padx=10, pady=0)

        ttk.Label(self.main_frame, text="Environment:").grid(row=6, column=0, sticky="w", padx=10, pady=4)
        self.env_var = tk.StringVar(value="ABIT")
        env_combo = ttk.Combobox(self.main_frame, textvariable=self.env_var, values=["ABIT", "SFN2", "CNFG"], state="readonly", width=12)
        env_combo.grid(row=6, column=1, sticky="w", padx=10, pady=4)

        ttk.Label(self.main_frame, text="Repo Path:").grid(row=7, column=0, sticky="w", padx=10, pady=4)
        self.repo_dir_var = tk.StringVar(value=r"C:\BENTO\adv_ibir_master")
        repo_frame = ttk.Frame(self.main_frame)
        repo_frame.grid(row=7, column=1, sticky="w", padx=10, pady=4)
        ttk.Entry(repo_frame, textvariable=self.repo_dir_var, width=32).pack(side=tk.LEFT)
        ttk.Button(repo_frame, text="📁", width=3, command=lambda: self.parent_tab._browse_directory(self.repo_dir_var, "Select TP Repository")).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Label(self.main_frame, text="Build Command:").grid(row=8, column=0, sticky="w", padx=10, pady=4)
        self.build_cmd_var = tk.StringVar(value="make release")
        build_combo = ttk.Combobox(self.main_frame, textvariable=self.build_cmd_var, values=["make release", "make release_supermicro"], width=28)
        build_combo.grid(row=8, column=1, sticky="w", padx=10, pady=4)

        ttk.Separator(self.main_frame, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="we", pady=8)

        # ── Error label ──
        self.err_var = tk.StringVar(value="")
        ttk.Label(self.main_frame, textvariable=self.err_var, foreground="#cc0000", font=("Arial", 8)).grid(row=10, column=0, columnspan=2)

        # ── Buttons ──
        btn_row = ttk.Frame(self.main_frame)
        btn_row.grid(row=11, column=0, columnspan=2, pady=10)
        
        self.add_btn = ttk.Button(btn_row, text="Add Tester", command=self._confirm, state="disabled", width=14)
        self.add_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.dialog.destroy, width=14).pack(side=tk.LEFT, padx=6)

        # ── Validation ──
        def _update_add_btn(*_):
            all_checked = all(v.get() for v in self.check_vars)
            self.add_btn.config(state="normal" if all_checked else "disabled")
            if all_checked:
                self.err_var.set("")
        
        for v in self.check_vars:
            v.trace_add("write", _update_add_btn)

        self.hostname_entry.focus_set()

    def _confirm(self):
        hostname = self.hostname_var.get().strip().upper()
        env      = self.env_var.get().strip().upper()
        repo_dir = self.repo_dir_var.get().strip()
        build_cmd = self.build_cmd_var.get().strip()
        
        if not hostname:
            self.err_var.set("Hostname cannot be empty.")
            return
        
        key = f"{hostname} ({env})"
        
        import json, os
        registry_path = self.parent_tab.context.config.get("registry_path", r"P:\temp\BENTO\bento_testers.json")
        registry_data = {}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    registry_data = json.load(f)
            except Exception: pass

        if key in registry_data:
            self.err_var.set(f"Tester '{key}' is already registered.")
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
