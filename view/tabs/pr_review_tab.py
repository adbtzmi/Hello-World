#!/usr/bin/env python3
"""
view/tabs/pr_review_tab.py
============================
✅ PR Review Tab (View) — Automates the JIRA / Pull Request Approval pipeline.

Layout:
  Section 1: Pipeline Configuration (issue key, target branch, reviewers, options)
  Section 2: Pipeline Progress (phase tracker + status log)
  Section 3: Quick Actions (individual step buttons)

Deployment solution only — full end-to-end pipeline from validation doc
attachment through PR merge and JIRA closure.
"""

import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from view.tabs.base_tab import BaseTab


class PRReviewTab(BaseTab):
    """
    ✅ PR Review Tab — automated JIRA / PR Approval pipeline.

    Phases:
      1. Attach validation doc to JIRA & tag requestor
      2. Commit & push code changes to remote
      3. Create pull request to TSE leads/seniors
      4. Poll for PR approval (with stale notifications)
      5. Merge PR on approval
      6. Close/transition release JIRA
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "✅ PR Review")
        self._build_ui()

    def _build_ui(self):
        self.configure(padding="10")

        # Make the tab scrollable for smaller screens
        self._canvas = canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._inner_frame = ttk.Frame(canvas, padding="10")

        self._inner_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        self._canvas_window = canvas.create_window(
            (0, 0), window=self._inner_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Stretch inner frame to match canvas width so content fills the tab
        def _on_canvas_resize(event):
            canvas.itemconfig(self._canvas_window, width=event.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        parent = self._inner_frame

        # ── Title ──────────────────────────────────────────────────────────
        ttk.Label(parent,
                  text="PR Review & Approval Pipeline",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=4, pady=(5, 10), sticky=tk.W)

        ttk.Label(parent,
                  text="Automate: Validation Doc → Commit/Push → PR Creation → Approval → Merge → Close JIRA",
                  font=('Arial', 9), foreground='gray').grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=(0, 10))

        # ══════════════════════════════════════════════════════════════════
        # SECTION 1: Pipeline Configuration
        # ══════════════════════════════════════════════════════════════════
        config_frame = ttk.LabelFrame(parent, text="Pipeline Configuration", padding="10")
        config_frame.grid(row=2, column=0, columnspan=4, sticky="nesw", pady=5)
        config_frame.columnconfigure(1, weight=1)

        row = 0

        # JIRA Issue Key
        ttk.Label(config_frame, text="JIRA Issue Key:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(config_frame,
                  textvariable=self.context.get_var('issue_var'),
                  width=30).grid(row=row, column=1, sticky=tk.W, pady=3, padx=5)
        ttk.Label(config_frame, text="(Auto-populated from Home tab)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=2, sticky=tk.W, padx=5)
        row += 1

        # Target Branch
        ttk.Label(config_frame, text="Target Branch:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._target_branch_var = tk.StringVar(value="master")
        ttk.Entry(config_frame, textvariable=self._target_branch_var,
                  width=30).grid(row=row, column=1, sticky=tk.W, pady=3, padx=5)
        ttk.Label(config_frame, text="(Branch to merge into)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=2, sticky=tk.W, padx=5)
        row += 1

        # Reviewers
        ttk.Label(config_frame, text="Reviewers:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._reviewers_var = tk.StringVar(value="")
        ttk.Entry(config_frame, textvariable=self._reviewers_var,
                  width=50).grid(row=row, column=1, columnspan=2, sticky="we", pady=3, padx=5)
        ttk.Label(config_frame, text="(Comma-separated Bitbucket usernames)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=3, sticky=tk.W, padx=5)
        row += 1

        # Commit Message
        ttk.Label(config_frame, text="Commit Message:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._commit_msg_var = tk.StringVar(value="")
        ttk.Entry(config_frame, textvariable=self._commit_msg_var,
                  width=50).grid(row=row, column=1, columnspan=2, sticky="we", pady=3, padx=5)
        row += 1

        # Validation Doc Path
        ttk.Label(config_frame, text="Validation Doc:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._validation_doc_var = tk.StringVar(value="")
        doc_entry = ttk.Entry(config_frame, textvariable=self._validation_doc_var,
                              width=40)
        doc_entry.grid(row=row, column=1, sticky="we", pady=3, padx=5)
        ttk.Button(config_frame, text="Browse...",
                   command=self._browse_validation_doc).grid(
            row=row, column=2, sticky=tk.W, padx=5)
        ttk.Button(config_frame, text="Auto-detect",
                   command=self._auto_detect_validation_doc).grid(
            row=row, column=3, sticky=tk.W, padx=2)
        row += 1

        # JIRA Transition Name
        ttk.Label(config_frame, text="JIRA Close Status:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._transition_var = tk.StringVar(value="Done")
        ttk.Combobox(config_frame, textvariable=self._transition_var,
                     values=["Done", "Closed", "Resolved", "Released"],
                     width=20, state='readonly').grid(
            row=row, column=1, sticky=tk.W, pady=3, padx=5)
        row += 1

        # ── Options Row ────────────────────────────────────────────────────
        options_frame = ttk.Frame(config_frame)
        options_frame.grid(row=row, column=0, columnspan=4, sticky=tk.W, pady=5)

        self._auto_merge_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Auto-merge on approval",
                        variable=self._auto_merge_var).pack(side=tk.LEFT, padx=(0, 15))

        self._auto_close_jira_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Auto-close JIRA after merge",
                        variable=self._auto_close_jira_var).pack(side=tk.LEFT, padx=(0, 15))
        row += 1

        # ══════════════════════════════════════════════════════════════════
        # SECTION 2: Pipeline Actions
        # ══════════════════════════════════════════════════════════════════
        action_frame = ttk.LabelFrame(parent, text="Pipeline Actions", padding="10")
        action_frame.grid(row=3, column=0, columnspan=4, sticky="nesw", pady=5)

        btn_row = ttk.Frame(action_frame)
        btn_row.pack(fill=tk.X, pady=5)

        self._run_pipeline_btn = ttk.Button(
            btn_row, text="▶ Run Full Pipeline",
            command=self._run_full_pipeline)
        self._run_pipeline_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(self._run_pipeline_btn)

        self._cancel_btn = ttk.Button(
            btn_row, text="⏹ Cancel",
            command=self._cancel_pipeline, state=tk.DISABLED)
        self._cancel_btn.pack(side=tk.LEFT, padx=5)

        ttk.Separator(btn_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        # Quick action buttons
        ttk.Label(btn_row, text="Quick Actions:",
                  font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)

        self._diff_btn = ttk.Button(
            btn_row, text="📊 View Diff",
            command=self._view_diff)
        self._diff_btn.pack(side=tk.LEFT, padx=3)

        self._push_btn = ttk.Button(
            btn_row, text="📤 Commit & Push",
            command=self._commit_push_only)
        self._push_btn.pack(side=tk.LEFT, padx=3)
        self.context.lockable_buttons.append(self._push_btn)

        self._create_pr_btn = ttk.Button(
            btn_row, text="🔀 Create PR",
            command=self._create_pr_only)
        self._create_pr_btn.pack(side=tk.LEFT, padx=3)
        self.context.lockable_buttons.append(self._create_pr_btn)

        self._check_pr_btn = ttk.Button(
            btn_row, text="🔍 Check PR Status",
            command=self._check_pr_status)
        self._check_pr_btn.pack(side=tk.LEFT, padx=3)

        # ══════════════════════════════════════════════════════════════════
        # SECTION 3: Pipeline Progress
        # ══════════════════════════════════════════════════════════════════
        progress_frame = ttk.LabelFrame(parent, text="Pipeline Progress", padding="10")
        progress_frame.grid(row=4, column=0, columnspan=4, sticky="nesw", pady=5)
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.rowconfigure(1, weight=1)

        # Phase indicator
        phase_row = ttk.Frame(progress_frame)
        phase_row.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(phase_row, text="Current Phase:",
                  font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        self._phase_label = ttk.Label(phase_row, text="Idle",
                                       font=('Arial', 9), foreground='gray')
        self._phase_label.pack(side=tk.LEFT, padx=10)

        # Phase progress bar (6 phases)
        self._phase_steps = []
        steps_frame = ttk.Frame(progress_frame)
        steps_frame.pack(fill=tk.X, pady=5)

        phase_names = [
            "1. Attach Doc",
            "2. Commit/Push",
            "3. Create PR",
            "4. PR Approval",
            "5. Merge",
            "6. Close JIRA",
        ]
        for i, name in enumerate(phase_names):
            lbl = ttk.Label(steps_frame, text=f"⬜ {name}",
                           font=('Arial', 8), foreground='gray')
            lbl.pack(side=tk.LEFT, padx=5)
            self._phase_steps.append(lbl)

        # Status log
        self._status_text = scrolledtext.ScrolledText(
            progress_frame, height=14, width=80, wrap=tk.WORD,
            state=tk.DISABLED)
        self._status_text.pack(fill=tk.BOTH, expand=True, pady=5)

        # PR Info row
        pr_info_frame = ttk.Frame(progress_frame)
        pr_info_frame.pack(fill=tk.X, pady=3)

        ttk.Label(pr_info_frame, text="PR URL:",
                  font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        self._pr_url_var = tk.StringVar(value="(none)")
        self._pr_url_entry = ttk.Entry(pr_info_frame,
                                        textvariable=self._pr_url_var,
                                        state='readonly', width=60)
        self._pr_url_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        ttk.Button(pr_info_frame, text="📋 Copy URL",
                   command=self._copy_pr_url).pack(side=tk.LEFT, padx=5)

        # Configure grid weights
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)

    # ══════════════════════════════════════════════════════════════════════
    # USER ACTIONS
    # ══════════════════════════════════════════════════════════════════════

    def _run_full_pipeline(self):
        """Launch the full PR review pipeline."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        target_branch = self._target_branch_var.get().strip()
        if not target_branch:
            self.show_error("Input Error", "Please enter a target branch")
            return

        reviewers_str = self._reviewers_var.get().strip()
        if not reviewers_str:
            self.show_error("Input Error",
                           "Please enter at least one reviewer (Bitbucket username)")
            return

        reviewers = [r.strip() for r in reviewers_str.split(",") if r.strip()]
        commit_msg = self._commit_msg_var.get().strip()
        if not commit_msg:
            commit_msg = f"[{issue_key}] Code changes"

        validation_doc = self._validation_doc_var.get().strip() or None

        ctrl = self._get_controller()
        if not ctrl:
            return

        # Confirm
        msg = (
            f"Run full PR pipeline?\n\n"
            f"JIRA: {issue_key}\n"
            f"Target: {target_branch}\n"
            f"Reviewers: {', '.join(reviewers)}\n"
            f"Auto-merge: {'Yes' if self._auto_merge_var.get() else 'No'}\n"
            f"Auto-close JIRA: {'Yes' if self._auto_close_jira_var.get() else 'No'}"
        )
        if not messagebox.askyesno("Confirm Pipeline", msg):
            return

        self._set_running(True)
        self._reset_phase_indicators()
        self._append_status(f"\n{'='*60}")
        self._append_status(f"  STARTING PR REVIEW PIPELINE")
        self._append_status(f"  JIRA: {issue_key} | Target: {target_branch}")
        self._append_status(f"{'='*60}\n")

        ctrl.run_full_pipeline(
            issue_key=issue_key,
            target_branch=target_branch,
            reviewers=reviewers,
            commit_message=commit_msg,
            validation_doc_path=validation_doc,
            auto_merge=self._auto_merge_var.get(),
            auto_close_jira=self._auto_close_jira_var.get(),
            transition_name=self._transition_var.get(),
            callback=self._on_pipeline_complete,
            phase_callback=self._on_phase_update,
        )

    def _cancel_pipeline(self):
        """Cancel the running pipeline."""
        ctrl = self._get_controller()
        if ctrl:
            ctrl.cancel()
            self._append_status("⚠ Cancel requested...")

    def _view_diff(self):
        """Show git diff summary."""
        ctrl = self._get_controller()
        if not ctrl:
            return

        ctrl.get_diff_summary(self._on_diff_result)

    def _commit_push_only(self):
        """Commit & push only (standalone)."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        commit_msg = self._commit_msg_var.get().strip()
        if not commit_msg:
            commit_msg = f"[{issue_key}] Code changes"

        ctrl = self._get_controller()
        if not ctrl:
            return

        self.lock_gui()
        self._append_status(f"\n📤 Committing & pushing: {commit_msg}")
        ctrl.commit_and_push(commit_msg, self._on_push_result)

    def _create_pr_only(self):
        """Create PR only (standalone)."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        target_branch = self._target_branch_var.get().strip()
        if not target_branch:
            self.show_error("Input Error", "Please enter a target branch")
            return

        reviewers_str = self._reviewers_var.get().strip()
        if not reviewers_str:
            self.show_error("Input Error", "Please enter at least one reviewer")
            return

        reviewers = [r.strip() for r in reviewers_str.split(",") if r.strip()]

        ctrl = self._get_controller()
        if not ctrl:
            return

        self.lock_gui()
        self._append_status(f"\n🔀 Creating PR: → {target_branch}")
        ctrl.create_pr_only(issue_key, target_branch, reviewers, self._on_pr_created)

    def _check_pr_status(self):
        """Check current PR approval status."""
        ctrl = self._get_controller()
        if not ctrl:
            return

        self._append_status("\n🔍 Checking PR status...")
        ctrl.check_pr_status(self._on_pr_status)

    def _browse_validation_doc(self):
        """Open file dialog to select validation document."""
        filepath = filedialog.askopenfilename(
            title="Select Validation Document",
            filetypes=[
                ("Word Documents", "*.docx"),
                ("All files", "*.*"),
            ],
            initialdir=os.getcwd(),
        )
        if filepath:
            self._validation_doc_var.set(filepath)

    def _auto_detect_validation_doc(self):
        """Auto-detect validation doc from workflow or current directory."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        # Check common patterns
        candidates = [
            f"Validation_{issue_key}.docx",
            f"{issue_key}_validation.docx",
            "template_validation.docx",
        ]

        for candidate in candidates:
            if os.path.exists(candidate):
                self._validation_doc_var.set(candidate)
                self._append_status(f"✓ Found validation doc: {candidate}")
                return

        self._append_status("⚠ No validation document found automatically")
        self.show_info("Not Found",
                      "No validation document found.\n"
                      "Generate one from the Validation & Risk tab first,\n"
                      "or browse to select manually.")

    def _copy_pr_url(self):
        """Copy PR URL to clipboard."""
        url = self._pr_url_var.get()
        if url and url != "(none)":
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.show_info("Copied", f"PR URL copied to clipboard:\n{url}")

    # ══════════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ══════════════════════════════════════════════════════════════════════

    def _on_pipeline_complete(self, result):
        """Called when the full pipeline finishes."""
        self._set_running(False)

        if result.get("success"):
            phases = result.get("phases_completed", [])
            pr_url = result.get("pr_url", "")
            if pr_url:
                self._pr_url_var.set(pr_url)

            self._append_status(f"\n{'='*60}")
            self._append_status(f"  ✅ PIPELINE COMPLETE")
            self._append_status(f"  Phases: {', '.join(phases)}")
            if pr_url:
                self._append_status(f"  PR URL: {pr_url}")
            self._append_status(f"{'='*60}")

            self.show_info("Pipeline Complete",
                          f"PR Review pipeline completed successfully!\n\n"
                          f"Phases completed: {len(phases)}\n"
                          f"PR URL: {pr_url or 'N/A'}")
        else:
            error = result.get("error", "Unknown error")
            phases = result.get("phases_completed", [])

            self._append_status(f"\n{'='*60}")
            self._append_status(f"  ❌ PIPELINE FAILED")
            self._append_status(f"  Error: {error}")
            self._append_status(f"  Phases completed: {', '.join(phases)}")
            self._append_status(f"{'='*60}")

            self.show_error("Pipeline Failed",
                           f"Error: {error}\n\n"
                           f"Phases completed: {', '.join(phases)}")

    def _on_phase_update(self, phase_name, detail=""):
        """Called during pipeline execution to update phase indicator."""
        self._phase_label.config(text=f"{phase_name} — {detail}" if detail else phase_name,
                                  foreground='blue')

        # Update phase step indicators
        phase_num = self._extract_phase_number(phase_name)
        if phase_num:
            for i, lbl in enumerate(self._phase_steps):
                if i < phase_num - 1:
                    # Completed
                    text = lbl.cget("text")
                    if text.startswith("⬜"):
                        lbl.config(text=text.replace("⬜", "✅"), foreground='green')
                elif i == phase_num - 1:
                    # Current
                    text = lbl.cget("text")
                    if text.startswith("⬜"):
                        lbl.config(text=text.replace("⬜", "🔄"), foreground='blue')

        self._append_status(f"  [{phase_name}] {detail}")

    def _on_diff_result(self, result):
        """Display diff summary."""
        if result.get("success"):
            branch = result.get("branch", "?")
            diff = result.get("diff", "(no changes)")
            self._append_status(f"\n📊 Branch: {branch}")
            self._append_status(f"{'─'*40}")
            self._append_status(diff if diff else "(no uncommitted changes)")
            self._append_status(f"{'─'*40}")
        else:
            self._append_status(f"✗ {result.get('error', 'Unknown error')}")

    def _on_push_result(self, result):
        """Handle commit & push result."""
        self.unlock_gui()
        if result.get("success"):
            commit = result.get("commit_hash", "")[:8]
            if result.get("no_changes"):
                self._append_status(f"ℹ No changes to commit (HEAD: {commit})")
            else:
                self._append_status(f"✓ Pushed commit {commit}")
                self.show_info("Push Success", f"Code pushed successfully!\nCommit: {commit}")
        else:
            self._append_status(f"✗ Push failed: {result.get('error')}")
            self.show_error("Push Failed", result.get("error", "Unknown error"))

    def _on_pr_created(self, result):
        """Handle PR creation result."""
        self.unlock_gui()
        if result.get("success"):
            pr_id = result.get("pr_id", "")
            pr_url = result.get("pr_url", "")
            self._pr_url_var.set(pr_url)
            self._append_status(f"✓ PR #{pr_id} created: {pr_url}")
            self.show_info("PR Created",
                          f"Pull Request #{pr_id} created!\n\n{pr_url}")
        else:
            self._append_status(f"✗ PR creation failed: {result.get('error')}")
            self.show_error("PR Failed", result.get("error", "Unknown error"))

    def _on_pr_status(self, result):
        """Display PR status check result."""
        if result.get("success"):
            state = result.get("state", "UNKNOWN")
            approved = result.get("approved", False)
            reviewers = result.get("reviewers", [])

            icon = "✅" if approved else ("🔀" if state == "MERGED" else "⏳")
            self._append_status(f"\n{icon} PR #{result.get('pr_id', '?')} — State: {state}")

            for r in reviewers:
                status_icon = "✅" if r["approved"] else "⏳"
                self._append_status(f"  {status_icon} {r['user']}: {r['status']}")

            if approved:
                self._append_status("  → All reviewers approved!")
            elif state == "MERGED":
                self._append_status("  → PR already merged")
            elif state == "DECLINED":
                self._append_status("  → PR was declined")
        else:
            self._append_status(f"✗ Status check failed: {result.get('error')}")

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _get_issue_key(self) -> str:
        """Get and validate the JIRA issue key."""
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Please enter a JIRA issue key")
            return ""
        return issue_key

    def _get_controller(self):
        """Get the PRReviewController from the master controller."""
        ctrl = getattr(self.context.controller, 'pr_review_controller', None)
        if not ctrl:
            self.show_error("Error", "PR Review controller not initialized")
        return ctrl

    def _set_running(self, running: bool):
        """Toggle UI state for pipeline running/idle."""
        if running:
            self.lock_gui()
            self._cancel_btn.config(state=tk.NORMAL)
            self._phase_label.config(text="Starting...", foreground='blue')
        else:
            self.unlock_gui()
            self._cancel_btn.config(state=tk.DISABLED)
            self._phase_label.config(text="Idle", foreground='gray')

    def _reset_phase_indicators(self):
        """Reset all phase step indicators to pending."""
        phase_names = [
            "1. Attach Doc",
            "2. Commit/Push",
            "3. Create PR",
            "4. PR Approval",
            "5. Merge",
            "6. Close JIRA",
        ]
        for i, lbl in enumerate(self._phase_steps):
            lbl.config(text=f"⬜ {phase_names[i]}", foreground='gray')

    def _extract_phase_number(self, phase_name: str) -> int:
        """Extract phase number from phase name like '3/6 — Create PR'."""
        try:
            if "/" in phase_name:
                return int(phase_name.split("/")[0])
        except (ValueError, IndexError):
            pass
        return 0

    def _append_status(self, text: str):
        """Append text to the status log (thread-safe via root.after)."""
        def _do():
            self._status_text.configure(state=tk.NORMAL)
            self._status_text.insert(tk.END, text + "\n")
            self._status_text.see(tk.END)
            self._status_text.configure(state=tk.DISABLED)

        self.root.after(0, _do)
