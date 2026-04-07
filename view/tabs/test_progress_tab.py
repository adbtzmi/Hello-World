#!/usr/bin/env python3
"""
view/tabs/test_progress_tab.py
================================
Test Progress Tab (View) — Task 2

Provides a GUI for monitoring test progress per drive/DUT:
  - Load MIDs.txt file
  - Configure target shared folder, site, machine type
  - Start/stop auto-detect polling
  - View real-time test status per MID in a Treeview
  - Manually collect files or spool summary for individual MIDs
  - Summary bar showing pass/fail/running counts
  - Additional file patterns to extract
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from view.tabs.base_tab import BaseTab


class TestProgressTab(BaseTab):
    """
    Test Progress & Result Collection Tab.

    Displays per-drive/DUT test status and allows:
      - Auto-detect test completion
      - Collect tracefile.txt + resultsManager.db
      - Spool summary (optional)
      - Teams notification on all-complete
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📊 Test Progress")
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Configuration Section ─────────────────────────────────────────
        config_frame = ttk.LabelFrame(self, text="Result Collection Configuration", padding="8")
        config_frame.grid(row=0, column=0, sticky="we", pady=(0, 5))
        config_frame.columnconfigure(1, weight=1)

        # MIDs File
        ttk.Label(config_frame, text="MIDs File:").grid(
            row=0, column=0, sticky=tk.W, pady=2)
        self._mids_file_var = tk.StringVar()
        mids_entry = ttk.Entry(config_frame, textvariable=self._mids_file_var, width=60)
        mids_entry.grid(row=0, column=1, sticky=tk.W, pady=2, padx=(0, 5))
        ttk.Button(config_frame, text="Browse...", width=10,
                   command=self._browse_mids_file).grid(
            row=0, column=2, pady=2)

        # Target Shared Folder
        ttk.Label(config_frame, text="Target Folder:").grid(
            row=1, column=0, sticky=tk.W, pady=2)
        self._target_path_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self._target_path_var, width=60).grid(
            row=1, column=1, sticky=tk.W, pady=2, padx=(0, 5))
        ttk.Button(config_frame, text="Browse...", width=10,
                   command=self._browse_target_folder).grid(
            row=1, column=2, pady=2)

        # Site + Machine Type row
        row2_frame = ttk.Frame(config_frame)
        row2_frame.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=2)

        ttk.Label(row2_frame, text="Site:").pack(side=tk.LEFT, padx=(0, 5))
        self._site_var = tk.StringVar(value="SINGAPORE")
        site_combo = ttk.Combobox(
            row2_frame, textvariable=self._site_var, width=15,
            values=["SINGAPORE", "PENANG", "BOISE", "XIAN"],
            state="readonly",
        )
        site_combo.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row2_frame, text="Machine Type:").pack(side=tk.LEFT, padx=(0, 5))
        self._machine_type_var = tk.StringVar(value="Auto-Detect")
        machine_combo = ttk.Combobox(
            row2_frame, textvariable=self._machine_type_var, width=15,
            values=["Auto-Detect", "IBIR", "MPT"],
            state="readonly",
        )
        machine_combo.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row2_frame, text="Poll Interval (s):").pack(side=tk.LEFT, padx=(0, 5))
        self._poll_interval_var = tk.IntVar(value=30)
        ttk.Spinbox(
            row2_frame, textvariable=self._poll_interval_var,
            from_=10, to=300, width=6,
        ).pack(side=tk.LEFT)

        # Options row
        row3_frame = ttk.Frame(config_frame)
        row3_frame.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=2)

        self._auto_collect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3_frame, text="Auto-Collect Files",
                        variable=self._auto_collect_var).pack(side=tk.LEFT, padx=(0, 15))

        self._auto_spool_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3_frame, text="Auto-Spool Summary",
                        variable=self._auto_spool_var).pack(side=tk.LEFT, padx=(0, 15))

        self._notify_teams_var = tk.BooleanVar(value=True)
        self._notify_teams_var.trace_add('write', self._on_teams_notify_toggled)
        ttk.Checkbutton(row3_frame, text="Teams Notification",
                        variable=self._notify_teams_var).pack(side=tk.LEFT, padx=(0, 15))

        # Additional file patterns
        ttk.Label(row3_frame, text="Extra Patterns:").pack(side=tk.LEFT, padx=(10, 5))
        self._extra_patterns_var = tk.StringVar()
        ttk.Entry(row3_frame, textvariable=self._extra_patterns_var, width=25).pack(
            side=tk.LEFT)
        ttk.Label(row3_frame, text="(comma-separated globs)",
                  foreground="gray").pack(side=tk.LEFT, padx=(5, 0))

        # ── Action Buttons ────────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=1, column=0, sticky=tk.W, pady=5)

        self._start_btn = ttk.Button(
            btn_frame, text="▶ Start Monitoring",
            command=self._start_monitoring,
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.context.lockable_buttons.append(self._start_btn)

        self._stop_btn = ttk.Button(
            btn_frame, text="⏹ Stop",
            command=self._stop_monitoring, state=tk.DISABLED,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        self._refresh_btn = ttk.Button(
            btn_frame, text="🔄 Refresh Status",
            command=self._refresh_status, state=tk.DISABLED,
        )
        self._refresh_btn.pack(side=tk.LEFT, padx=(0, 5))

        self._collect_btn = ttk.Button(
            btn_frame, text="📁 Collect Selected",
            command=self._collect_selected, state=tk.DISABLED,
        )
        self._collect_btn.pack(side=tk.LEFT, padx=(0, 5))

        self._spool_btn = ttk.Button(
            btn_frame, text="📊 Spool Selected",
            command=self._spool_selected, state=tk.DISABLED,
        )
        self._spool_btn.pack(side=tk.LEFT, padx=(0, 5))

        # ── Summary Bar ──────────────────────────────────────────────────
        summary_frame = ttk.Frame(self)
        summary_frame.grid(row=2, column=0, sticky="we", pady=(0, 5))

        self._summary_label = ttk.Label(
            summary_frame,
            text="No monitoring active",
            font=("Arial", 10),
        )
        self._summary_label.pack(side=tk.LEFT)

        self._status_indicator = ttk.Label(
            summary_frame, text="", font=("Arial", 10, "bold"),
        )
        self._status_indicator.pack(side=tk.RIGHT, padx=10)

        # ── Treeview — Per-DUT Status ────────────────────────────────────
        tree_frame = ttk.LabelFrame(self, text="Drive / DUT Status", padding="5")
        tree_frame.grid(row=3, column=0, sticky="nswe", pady=(0, 5))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        columns = ("mid", "location", "name", "status", "fail_info", "collected", "error")
        self._tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="extended", height=12,
        )

        self._tree.heading("mid",       text="MID")
        self._tree.heading("location",  text="Location")
        self._tree.heading("name",      text="File Name")
        self._tree.heading("status",    text="Status")
        self._tree.heading("fail_info", text="Fail Info")
        self._tree.heading("collected", text="Collected")
        self._tree.heading("error",     text="Error")

        self._tree.column("mid",       width=110, minwidth=80)
        self._tree.column("location",  width=70,  minwidth=50)
        self._tree.column("name",      width=120, minwidth=80)
        self._tree.column("status",    width=80,  minwidth=60)
        self._tree.column("fail_info", width=200, minwidth=100)
        self._tree.column("collected", width=80,  minwidth=60)
        self._tree.column("error",     width=200, minwidth=100)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nswe")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="we")

        # Tag styles for status coloring
        self._tree.tag_configure("pass",    foreground="green")
        self._tree.tag_configure("fail",    foreground="red")
        self._tree.tag_configure("running", foreground="orange")
        self._tree.tag_configure("unknown", foreground="gray")
        self._tree.tag_configure("error",   foreground="red", background="#fff0f0")

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_mids_file(self):
        path = filedialog.askopenfilename(
            title="Select MIDs.txt File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._mids_file_var.set(path)

    def _browse_target_folder(self):
        path = filedialog.askdirectory(title="Select Target Shared Folder")
        if path:
            self._target_path_var.set(path)

    def _on_teams_notify_toggled(self, *args):
        """Warn user if Teams Notification is enabled but no webhook URL is configured."""
        if self._notify_teams_var.get():
            webhook_url = ""
            webhook_var = self.context.get_var("checkout_webhook_url")
            if webhook_var:
                webhook_url = webhook_var.get().strip()
            if not webhook_url:
                messagebox.showwarning(
                    "Webhook URL Missing",
                    "Teams Notification is enabled but no Webhook URL is configured.\n\n"
                    "Please go to the 🏠 Home tab → Notifications section\n"
                    "and enter your Teams Workflow Webhook URL.\n\n"
                    "Without a valid Webhook URL, Teams notifications will not be sent.",
                    parent=self.winfo_toplevel(),
                )

    def _start_monitoring(self):
        """Start the result collection monitoring."""
        mids_file = self._mids_file_var.get().strip()
        if not mids_file:
            self.show_error("Error", "Please select a MIDs.txt file.")
            return
        if not os.path.exists(mids_file):
            self.show_error("Error", f"MIDs file not found:\n{mids_file}")
            return

        target_path = self._target_path_var.get().strip()
        site        = self._site_var.get().strip()
        machine     = self._machine_type_var.get().strip()
        if machine == "Auto-Detect":
            machine = ""

        poll_interval = self._poll_interval_var.get()
        auto_collect  = self._auto_collect_var.get()
        auto_spool    = self._auto_spool_var.get()
        notify_teams  = self._notify_teams_var.get()

        # Parse additional patterns
        extra = self._extra_patterns_var.get().strip()
        additional_patterns = [p.strip() for p in extra.split(",") if p.strip()] if extra else []

        # Resolve webhook URL from shared context
        webhook_url = ""
        webhook_var = self.context.get_var("checkout_webhook_url")
        if webhook_var:
            webhook_url = webhook_var.get().strip()

        # Validate webhook URL when Teams notification is enabled
        if notify_teams and not webhook_url:
            response = messagebox.askyesno(
                "Webhook URL Missing",
                "Teams Notification is enabled but no Webhook URL is configured.\n\n"
                "Please go to the 🏠 Home tab → Notifications section\n"
                "and enter your Teams Workflow Webhook URL.\n\n"
                "Do you want to continue without Teams notification?",
                parent=self.winfo_toplevel(),
            )
            if not response:
                return
            # User chose to continue — disable Teams notify for this run
            notify_teams = False

        # Get controller
        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            self.show_error("Error", "Result controller not available.")
            return

        # Update UI state
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._refresh_btn.config(state=tk.NORMAL)
        self._collect_btn.config(state=tk.NORMAL)
        self._spool_btn.config(state=tk.NORMAL)
        self._status_indicator.config(text="🔄 MONITORING", foreground="blue")

        controller.result_controller.start_monitoring(
            mids_file           = mids_file,
            target_path         = target_path,
            site                = site,
            machine_type        = machine,
            poll_interval       = poll_interval,
            auto_collect        = auto_collect,
            auto_spool          = auto_spool,
            additional_patterns = additional_patterns,
            webhook_url         = webhook_url,
            notify_teams        = notify_teams,
        )

    def _stop_monitoring(self):
        """Stop the result collection monitoring."""
        controller = self.context.controller
        if controller and hasattr(controller, "result_controller"):
            controller.result_controller.stop_monitoring()

    def _refresh_status(self):
        """Force a status refresh."""
        controller = self.context.controller
        if controller and hasattr(controller, "result_controller"):
            controller.result_controller.refresh_status()

    def _collect_selected(self):
        """Manually collect files for selected MIDs."""
        selected = self._tree.selection()
        if not selected:
            self.show_info("Info", "Please select one or more MIDs to collect.")
            return

        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            return

        for item_id in selected:
            values = self._tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                controller.result_controller.collect_single(mid)

    def _spool_selected(self):
        """Manually spool summary for selected MIDs."""
        selected = self._tree.selection()
        if not selected:
            self.show_info("Info", "Please select one or more MIDs to spool.")
            return

        controller = self.context.controller
        if not controller or not hasattr(controller, "result_controller"):
            return

        for item_id in selected:
            values = self._tree.item(item_id, "values")
            mid = values[0] if values else ""
            if mid:
                controller.result_controller.spool_single(mid)

    # ──────────────────────────────────────────────────────────────────────
    # CONTROLLER → VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_progress_update(self, summary: dict, entries: dict):
        """
        Called by ResultController (via root.after) when progress updates.
        Updates the Treeview and summary bar.
        """
        # Update summary bar
        total      = summary.get("total", 0)
        passed     = summary.get("passed", 0)
        failed     = summary.get("failed", 0)
        running    = summary.get("running", 0)
        collected  = summary.get("collected", 0)
        unresolved = summary.get("unresolved", 0)
        machine    = summary.get("machine", "")
        site       = summary.get("site", "")

        unresolved_str = f"  |  ⚠ Unresolved: {unresolved}" if unresolved else ""
        self._summary_label.config(
            text=(
                f"Machine: {machine}  |  Site: {site}  |  "
                f"Total: {total}  |  "
                f"✅ Pass: {passed}  |  ❌ Fail: {failed}  |  "
                f"🔄 Running: {running}  |  📁 Collected: {collected}"
                f"{unresolved_str}"
            )
        )

        # Update treeview
        self._tree.delete(*self._tree.get_children())

        for mid, entry_data in entries.items():
            status    = entry_data.get("status", "UNKNOWN")
            fail_info = ""
            if status == "FAIL":
                fail_info = f"{entry_data.get('fail_reg', '')} - {entry_data.get('fail_code', '')}"

            collected_str = "✓" if entry_data.get("collected", False) else ""
            error_str     = entry_data.get("error", "")

            # Determine tag for coloring
            tag = status.lower() if status.lower() in ("pass", "fail", "running") else "unknown"
            if error_str:
                tag = "error"

            self._tree.insert("", tk.END, values=(
                entry_data.get("mid", mid),
                entry_data.get("location", ""),
                entry_data.get("name", ""),
                status,
                fail_info,
                collected_str,
                error_str,
            ), tags=(tag,))

    def on_collection_complete(self, summary: dict):
        """
        Called by ResultController when monitoring ends.
        Resets button states.
        """
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)

        all_done = summary.get("all_done", False)
        failed   = summary.get("failed", 0)

        if all_done and failed == 0:
            self._status_indicator.config(text="✅ ALL PASSED", foreground="green")
        elif all_done and failed > 0:
            self._status_indicator.config(text="⚠ COMPLETED (with failures)", foreground="orange")
        else:
            self._status_indicator.config(text="⏹ STOPPED", foreground="gray")
