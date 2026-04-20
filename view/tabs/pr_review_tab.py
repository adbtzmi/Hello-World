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
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from view.tabs.base_tab import BaseTab
from view.widgets.tooltip import ToolTip


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
        self._notebook = notebook  # Store for tab-change detection
        self._reviewer_chips: list[str] = []  # list of added reviewer usernames
        self._reviewer_display_names: dict[str, str] = {}  # username → display name (Feature 13)
        # H2: Approval polling state
        self._polling_active = False
        self._poll_after_id = None       # root.after() handle for cancellation
        self._poll_interval = 30         # seconds between polls
        self._poll_countdown = 0         # seconds remaining until next poll
        self._poll_countdown_id = None   # root.after() handle for countdown tick
        # H3: Elapsed timer state
        self._elapsed_start: float = 0.0
        self._elapsed_after_id = None    # root.after() handle for timer tick
        self._build_ui()

        # Item 12: Auto-populate commit message when JIRA key changes
        try:
            issue_var = self.context.get_var('issue_var')
            if issue_var:
                issue_var.trace_add('write', self._auto_populate_commit_message)
                # Populate immediately if a key is already set
                self._auto_populate_commit_message()
        except Exception:
            pass

        # Improvement 1: Auto-populate target branch from workflow
        # Deferred so the controller has time to initialize after app startup
        self.root.after(500, self._auto_populate_target_branch)

        # Feature 12: Check for saved pipeline state to resume
        self.root.after(1500, self._check_resume_pipeline)

        # L3: Restore last used settings from settings.json
        self.root.after(600, self._restore_last_settings)

        # Re-detect source branch when user switches to this tab
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_selected, add=True)

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

        # Mousewheel scrolling (match compilation/checkout tabs)
        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self._inner_frame.bind(
            "<Enter>",
            lambda _: canvas.bind_all("<MouseWheel>", _mousewheel))
        self._inner_frame.bind(
            "<Leave>",
            lambda _: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        parent = self._inner_frame

        # ── Title ──────────────────────────────────────────────────────────
        ttk.Label(parent,
                  text="PR Review & Approval Pipeline",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=4, pady=(5, 10))

        ttk.Label(parent,
                  text="Automate: Validation Doc → Commit/Push → PR Creation → Approval → Merge → Close JIRA",
                  font=('Arial', 9), foreground='gray').grid(
            row=1, column=0, columnspan=4, pady=(0, 10))

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

        # Target Branch (Feature 10: searchable autocomplete from Bitbucket API)
        ttk.Label(config_frame, text="Target Branch:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._target_branch_var = tk.StringVar(value="master")
        self._branch_entry = ttk.Entry(
            config_frame, textvariable=self._target_branch_var, width=30)
        self._branch_entry.grid(row=row, column=1, sticky=tk.W, pady=3, padx=5)

        # Branch autocomplete state
        self._branch_ac_listbox: tk.Listbox | None = None
        self._branch_ac_toplevel: tk.Toplevel | None = None
        self._branch_ac_debounce_id: str | None = None

        self._branch_entry.bind("<KeyRelease>", self._on_branch_key)
        self._branch_entry.bind("<FocusOut>", self._hide_branch_autocomplete)
        self._branch_entry.bind("<Escape>", self._hide_branch_autocomplete)
        self._branch_entry.bind("<Down>", self._branch_ac_focus_listbox)
        self._branch_entry.bind("<Return>", self._branch_ac_select_current)

        ttk.Label(config_frame, text="(Type to search branches from Bitbucket)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=2, sticky=tk.W, padx=5)
        row += 1

        # Reviewers (Item 16: Autocomplete + Improvement 3: Chip/Tag display)
        ttk.Label(config_frame, text="Reviewers:").grid(
            row=row, column=0, sticky=tk.NW, pady=3)
        self._reviewers_var = tk.StringVar(value="")

        # Frame to hold the entry + chip container
        reviewer_outer = ttk.Frame(config_frame)
        reviewer_outer.grid(
            row=row, column=1, columnspan=2, sticky="we", pady=3, padx=5)
        reviewer_outer.columnconfigure(0, weight=1)

        # Chip container (Improvement 3: shows added reviewers as removable tags)
        self._chip_frame = ttk.Frame(reviewer_outer)
        self._chip_frame.grid(row=0, column=0, sticky="we", pady=(0, 3))

        # Search entry for autocomplete (below chips)
        self._reviewers_entry = ttk.Entry(
            reviewer_outer, textvariable=self._reviewers_var, width=48)
        self._reviewers_entry.grid(row=1, column=0, sticky="we")

        # Autocomplete dropdown (Listbox in a Toplevel — floats over the UI)
        self._ac_listbox: tk.Listbox | None = None
        self._ac_toplevel: tk.Toplevel | None = None
        self._ac_debounce_id: str | None = None
        self._ac_display_map: dict = {}
        self._ac_clicking: bool = False  # Flag to prevent FocusOut during click

        # Bind keystrokes for autocomplete
        self._reviewers_entry.bind("<KeyRelease>", self._on_reviewer_key)
        self._reviewers_entry.bind("<FocusOut>", self._hide_autocomplete)
        self._reviewers_entry.bind("<Escape>", self._hide_autocomplete)
        self._reviewers_entry.bind("<Down>", self._ac_focus_listbox)
        self._reviewers_entry.bind("<Return>", self._ac_select_current)
        # L5: Backspace in empty entry removes last reviewer chip
        self._reviewers_entry.bind("<BackSpace>", self._on_reviewer_backspace)

        ttk.Label(config_frame, text="(Type to search, chips added below)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=3, sticky=tk.W, padx=5)
        row += 1

        # Commit Message (Feature 14: multi-line Text widget)
        ttk.Label(config_frame, text="Commit Message:").grid(
            row=row, column=0, sticky=tk.NW, pady=3)
        self._commit_msg_text = tk.Text(
            config_frame, height=3, width=50, wrap=tk.WORD,
            font=('Arial', 9))
        self._commit_msg_text.grid(
            row=row, column=1, sticky="we", pady=3, padx=5)
        self._ai_commit_btn = ttk.Button(
            config_frame, text="✨ AI Generate",
            command=self._generate_ai_commit_message)
        self._ai_commit_btn.grid(row=row, column=2, sticky=tk.NW, padx=5)
        ttk.Label(config_frame, text="(Multi-line supported)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=3, sticky=tk.NW, padx=5)
        # Auto-sync PR title when commit message changes
        self._pr_title_user_edited = False  # Track manual edits
        self._commit_msg_text.bind("<<Modified>>", self._on_commit_msg_changed)
        row += 1

        # M4: PR Title (editable, auto-synced from first line of commit message)
        ttk.Label(config_frame, text="PR Title:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._pr_title_var = tk.StringVar(value="")
        self._pr_title_entry = ttk.Entry(
            config_frame, textvariable=self._pr_title_var, width=50)
        self._pr_title_entry.grid(
            row=row, column=1, columnspan=2, sticky="we", pady=3, padx=5)
        # Mark as user-edited when they type directly in PR title
        self._pr_title_entry.bind(
            "<Key>", lambda _: setattr(self, '_pr_title_user_edited', True))
        ttk.Label(config_frame,
                  text="(Auto-synced from commit msg first line)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=3, sticky=tk.NW, padx=5)
        row += 1

        # PR Description (Improvement 2: editable multi-line description)
        ttk.Label(config_frame, text="PR Description:").grid(
            row=row, column=0, sticky=tk.NW, pady=3)
        self._pr_desc_text = tk.Text(
            config_frame, height=3, width=50, wrap=tk.WORD,
            font=('Arial', 9))
        self._pr_desc_text.grid(
            row=row, column=1, sticky="we", pady=3, padx=5)
        # M1: AI Generate button for PR description
        self._ai_pr_desc_btn = ttk.Button(
            config_frame, text="✨ AI Generate",
            command=self._generate_ai_pr_description)
        self._ai_pr_desc_btn.grid(row=row, column=2, sticky=tk.NW, padx=5)
        ttk.Label(config_frame,
                  text="(Optional — auto-generated if empty)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=3, sticky=tk.NW, padx=5)
        row += 1

        # M3: Source Branch (read-only, auto-populated from git)
        ttk.Label(config_frame, text="Source Branch:").grid(
            row=row, column=0, sticky=tk.W, pady=3)
        self._source_branch_var = tk.StringVar(value="(detecting...)")
        self._source_branch_label = ttk.Label(
            config_frame, textvariable=self._source_branch_var,
            font=('Consolas', 9), foreground='#0066cc')
        self._source_branch_label.grid(
            row=row, column=1, sticky=tk.W, pady=3, padx=5)
        ttk.Label(config_frame, text="(Auto-detected from local repo)",
                  font=('Arial', 8), foreground='gray').grid(
            row=row, column=2, columnspan=2, sticky=tk.W, padx=5)
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

        # Grid layout: Col 0=pipeline controls | Col 1=separator | Col 2=label | Col 3=buttons
        ctrl_frame = ttk.Frame(action_frame)
        ctrl_frame.grid(row=0, column=0, sticky="w", pady=(5, 2))

        self._run_pipeline_btn = ttk.Button(
            ctrl_frame, text="▶ Run Full Pipeline",
            command=self._run_full_pipeline)
        self._run_pipeline_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(self._run_pipeline_btn)

        self._cancel_btn = ttk.Button(
            ctrl_frame, text="⏹ Cancel",
            command=self._cancel_pipeline, state=tk.DISABLED)
        self._cancel_btn.pack(side=tk.LEFT, padx=5)

        ttk.Separator(action_frame, orient=tk.VERTICAL).grid(
            row=0, column=1, rowspan=2, sticky="ns", padx=10, pady=5)

        # "Quick Actions:" label in its own column, spanning both rows
        ttk.Label(action_frame, text="Quick Actions:",
                  font=('Arial', 9, 'bold')).grid(
            row=0, column=2, sticky="nw", pady=(8, 2), padx=(0, 5))

        # Row 1 buttons (col 3) — View Diff starts here
        qa_row1 = ttk.Frame(action_frame)
        qa_row1.grid(row=0, column=3, sticky="w", pady=(5, 2))

        self._diff_btn = ttk.Button(
            qa_row1, text="📊 View Diff",
            command=self._view_diff)
        self._diff_btn.pack(side=tk.LEFT, padx=3)

        self._diff_popup_btn = ttk.Button(
            qa_row1, text="🔍 Diff Preview",
            command=self._open_diff_preview)
        self._diff_popup_btn.pack(side=tk.LEFT, padx=3)

        self._push_btn = ttk.Button(
            qa_row1, text="📤 Commit & Push",
            command=self._commit_push_only)
        self._push_btn.pack(side=tk.LEFT, padx=3)
        self.context.lockable_buttons.append(self._push_btn)

        self._create_pr_btn = ttk.Button(
            qa_row1, text="🔀 Create PR",
            command=self._create_pr_only)
        self._create_pr_btn.pack(side=tk.LEFT, padx=3)
        self.context.lockable_buttons.append(self._create_pr_btn)

        self._check_pr_btn = ttk.Button(
            qa_row1, text="🔍 Check PR Status",
            command=self._check_pr_status)
        self._check_pr_btn.pack(side=tk.LEFT, padx=3)

        # Row 2 buttons (col 3) — Merge PR aligns under View Diff
        qa_row2 = ttk.Frame(action_frame)
        qa_row2.grid(row=1, column=3, sticky="w", pady=(0, 5))

        # H1: Merge PR button (standalone quick action)
        self._merge_pr_btn = ttk.Button(
            qa_row2, text="🔀 Merge PR",
            command=self._merge_pr_only)
        self._merge_pr_btn.pack(side=tk.LEFT, padx=3)

        # H2: Poll Approval button (auto-refresh with countdown)
        self._poll_btn = ttk.Button(
            qa_row2, text="⏳ Poll Approval",
            command=self._toggle_polling)
        self._poll_btn.pack(side=tk.LEFT, padx=3)

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

        # H3: Elapsed time indicator (right-aligned in phase row)
        self._elapsed_label = ttk.Label(phase_row, text="",
                                         font=('Consolas', 9), foreground='gray')
        self._elapsed_label.pack(side=tk.RIGHT, padx=10)

        # H2: Polling countdown label (right-aligned, next to elapsed)
        self._poll_status_label = ttk.Label(phase_row, text="",
                                             font=('Arial', 8), foreground='gray')
        self._poll_status_label.pack(side=tk.RIGHT, padx=5)

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

        # Status log header with Clear button (Item 11)
        log_header = ttk.Frame(progress_frame)
        log_header.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(log_header, text="Status Log",
                  font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        ttk.Button(log_header, text="🗑 Clear Log",
                   command=self._clear_status_log).pack(side=tk.RIGHT)

        # L2: Pipeline progress bar (determinate, 6 steps)
        self._pipeline_progress = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, length=400,
            mode='determinate', maximum=6, value=0)
        self._pipeline_progress.pack(fill=tk.X, pady=(2, 5))

        self._status_text = scrolledtext.ScrolledText(
            progress_frame, height=14, width=80, wrap=tk.WORD,
            state=tk.DISABLED)
        self._status_text.pack(fill=tk.BOTH, expand=True, pady=(2, 5))

        # L1: Configure syntax highlighting tags for status log
        self._status_text.tag_configure("success", foreground="#228B22")
        self._status_text.tag_configure("error", foreground="#DC143C")
        self._status_text.tag_configure("warning", foreground="#FF8C00")
        self._status_text.tag_configure("phase", foreground="#1E90FF", font=('Arial', 9, 'bold'))
        self._status_text.tag_configure("header", foreground="#4B0082", font=('Arial', 10, 'bold'))
        self._status_text.tag_configure("info", foreground="#555555")

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
        ttk.Button(pr_info_frame, text="🌐 Open",
                   command=self._open_pr_in_browser).pack(side=tk.LEFT, padx=2)

        # Configure grid weights
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)

    # ══════════════════════════════════════════════════════════════════════
    # USER ACTIONS
    # ══════════════════════════════════════════════════════════════════════

    def _get_min_reviewers(self) -> int:
        """M5: Read minimum reviewer count from settings.json (default 1)."""
        try:
            import json
            with open("settings.json", "r") as f:
                cfg = json.load(f)
            return int(cfg.get("pr_review", {}).get("min_reviewers", 1))
        except Exception:
            return 1

    def _run_full_pipeline(self):
        """Launch the full PR review pipeline."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        target_branch = self._target_branch_var.get().strip()
        if not target_branch:
            self.show_error("Input Error", "Please enter a target branch")
            return

        # Improvement 3: Use chip-based reviewers
        reviewers = list(self._reviewer_chips)
        # M5: Reviewer minimum count validation
        min_rev = self._get_min_reviewers()
        if len(reviewers) < min_rev:
            self.show_error(
                "Input Error",
                f"Please add at least {min_rev} reviewer(s). "
                f"Currently {len(reviewers)} added.\n\n"
                f"(Configurable via settings.json → pr_review.min_reviewers)"
            )
            return

        # L6: Validate target branch exists via Bitbucket API
        if not self._validate_target_branch(target_branch):
            return

        commit_msg = self._commit_msg_text.get("1.0", tk.END).strip()
        if not commit_msg:
            commit_msg = f"[{issue_key}] Code changes"

        # M4: Read PR title from editable field
        pr_title = self._pr_title_var.get().strip()

        # Read PR description (auto-generated if empty)
        pr_description = self._pr_desc_text.get("1.0", tk.END).strip()

        # M3: Source branch display
        source_branch = self._source_branch_var.get().strip()

        validation_doc = self._validation_doc_var.get().strip() or None

        ctrl = self._get_controller()
        if not ctrl:
            return

        # M2: Confirmation dialog with PR description preview
        desc_preview = pr_description[:200] + "..." if len(pr_description) > 200 else pr_description
        desc_line = f"\nPR Description: {desc_preview}" if desc_preview else "\nPR Description: (auto-generated)"
        title_line = f"\nPR Title: {pr_title}" if pr_title else "\nPR Title: (auto-generated)"
        msg = (
            f"Run full PR pipeline?\n\n"
            f"JIRA: {issue_key}\n"
            f"Source: {source_branch}\n"
            f"Target: {target_branch}\n"
            f"Reviewers: {', '.join(reviewers)}"
            f"{title_line}"
            f"{desc_line}\n\n"
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
            pr_description=pr_description,
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

        commit_msg = self._commit_msg_text.get("1.0", tk.END).strip()
        if not commit_msg:
            commit_msg = f"[{issue_key}] Code changes"

        ctrl = self._get_controller()
        if not ctrl:
            return

        self.lock_gui()
        self._append_status(f"\n📤 Committing & pushing: {commit_msg}")
        ctrl.commit_and_push(commit_msg, self._on_push_result)

    def _create_pr_only(self):
        """Create PR only (standalone). Includes duplicate PR check (Improvement 4)."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        target_branch = self._target_branch_var.get().strip()
        if not target_branch:
            self.show_error("Input Error", "Please enter a target branch")
            return

        # Improvement 3 + M5: Use chip-based reviewers with min count validation
        reviewers = list(self._reviewer_chips)
        min_rev = self._get_min_reviewers()
        if len(reviewers) < min_rev:
            self.show_error(
                "Input Error",
                f"Please add at least {min_rev} reviewer(s). "
                f"Currently {len(reviewers)} added.\n\n"
                f"(Configurable via settings.json → pr_review.min_reviewers)"
            )
            return

        # Improvement 2: Get PR description from text widget
        pr_description = self._pr_desc_text.get("1.0", tk.END).strip()

        # M4: Get PR title from editable field
        pr_title = self._pr_title_var.get().strip()

        ctrl = self._get_controller()
        if not ctrl:
            return

        # Improvement 4: Check for duplicate PR before creating
        self.lock_gui()
        self._append_status(f"\n🔍 Checking for existing PRs: → {target_branch}")
        ctrl.check_existing_pr(
            target_branch,
            lambda result: self._on_dup_check_result(
                result, issue_key, target_branch, reviewers,
                pr_description, pr_title, ctrl
            ),
        )

    def _on_dup_check_result(self, result, issue_key, target_branch, reviewers,
                              pr_description, pr_title, ctrl):
        """Improvement 4: Handle duplicate PR check result before creating PR."""
        if result.get("exists"):
            self.unlock_gui()
            pr_id = result.get("pr_id", "?")
            title = result.get("title", "")
            pr_url = result.get("pr_url", "")
            msg = (
                f"An OPEN pull request already exists!\n\n"
                f"PR #{pr_id}: {title}\n"
                f"URL: {pr_url}\n\n"
                f"Do you still want to create a new PR?"
            )
            if not messagebox.askyesno("Duplicate PR Warning", msg, icon="warning"):
                self._append_status(f"⚠ Skipped — existing PR #{pr_id} found")
                if pr_url:
                    self._pr_url_var.set(pr_url)
                return
            self.lock_gui()

        self._append_status(f"🔀 Creating PR: → {target_branch}")
        ctrl.create_pr_only(
            issue_key, target_branch, reviewers,
            pr_description=pr_description,
            pr_title=pr_title,
            callback=self._on_pr_created,
        )

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
        """Auto-detect validation doc from workflow, repo path, or CWD."""
        issue_key = self._get_issue_key()
        if not issue_key:
            return

        # Build list of base filenames to look for
        basenames = [
            f"Validation_{issue_key}.docx",
            f"{issue_key}_validation.docx",
            "template_validation.docx",
        ]

        # Build list of directories to search (CWD + workflow output + repo)
        search_dirs = [os.getcwd()]
        try:
            wf_ctrl = getattr(self.context.controller, "workflow_controller", None)
            if wf_ctrl:
                repo_path = wf_ctrl.get_workflow_step("REPOSITORY_PATH")
                if repo_path:
                    search_dirs.append(repo_path)
                output_dir = wf_ctrl.get_workflow_step("OUTPUT_DIR")
                if output_dir:
                    search_dirs.append(output_dir)
        except Exception:
            pass

        for base_dir in search_dirs:
            for name in basenames:
                candidate = os.path.join(base_dir, name)
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

    def _open_pr_in_browser(self):
        """Item 20: Open PR URL in the default web browser."""
        url = self._pr_url_var.get()
        if url and url != "(none)":
            webbrowser.open(url)
        else:
            self.show_info("No URL", "No PR URL available yet.\n"
                           "Run the pipeline first to create a PR.")

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

            # Item 16: Save used reviewers to settings.json for future fallback
            self._save_used_reviewers()

            # L3: Save last used settings for next session
            self._save_last_settings()

            # Item 15: Mark all phases ✅ on success
            for lbl in self._phase_steps:
                text = lbl.cget("text")
                clean = text.lstrip("⬜✅🔄❌ ")
                lbl.config(text=f"✅ {clean}", foreground='green')

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

            # Item 15: Mark completed phases ✅ and failed phase ❌
            total_phases = len(self._phase_steps)
            completed_count = len(phases)
            for i, lbl in enumerate(self._phase_steps):
                text = lbl.cget("text")
                # Strip existing indicator prefix
                clean = text.lstrip("⬜✅🔄❌ ")
                if i < completed_count:
                    lbl.config(text=f"✅ {clean}", foreground='green')
                elif i == completed_count and i < total_phases:
                    lbl.config(text=f"❌ {clean}", foreground='red')
                else:
                    lbl.config(text=f"⬜ {clean}", foreground='gray')

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

            # L2: Update progress bar value
            self._pipeline_progress['value'] = phase_num

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

            # L4: Update diff button label with file count
            if diff:
                import re
                m = re.search(r'(\d+)\s+files?\s+changed', diff)
                file_count = int(m.group(1)) if m else 0
                if file_count:
                    self._diff_btn.config(text=f"📊 View Diff ({file_count} files)")
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
            # Show the auto-generated description in the PR Description field
            pr_desc = result.get("pr_description", "")
            if pr_desc and not self._pr_desc_text.get("1.0", tk.END).strip():
                self._pr_desc_text.delete("1.0", tk.END)
                self._pr_desc_text.insert("1.0", pr_desc)
            # Item 16: Save used reviewers to settings.json for future fallback
            self._save_used_reviewers()
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
            self._start_elapsed_timer()   # H3
        else:
            self.unlock_gui()
            self._cancel_btn.config(state=tk.DISABLED)
            self._phase_label.config(text="Idle", foreground='gray')
            self._stop_elapsed_timer()    # H3

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
        # L2: Reset progress bar
        self._pipeline_progress['value'] = 0

    def _extract_phase_number(self, phase_name: str) -> int:
        """Extract phase number from phase name like '3/6 — Create PR'."""
        try:
            if "/" in phase_name:
                return int(phase_name.split("/")[0])
        except (ValueError, IndexError):
            pass
        return 0

    def _clear_status_log(self):
        """Item 11: Clear the status log text widget."""
        self._status_text.configure(state=tk.NORMAL)
        self._status_text.delete("1.0", tk.END)
        self._status_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # L3: REMEMBER LAST USED SETTINGS
    # ──────────────────────────────────────────────────────────────────────

    def _restore_last_settings(self):
        """L3: Restore last used settings (target branch, transition, auto-merge,
        auto-close) from settings.json on tab init."""
        try:
            import json
            with open("settings.json", "r") as f:
                cfg = json.load(f)
            pr_cfg = cfg.get("pr_review", {})

            last_branch = pr_cfg.get("last_target_branch", "")
            if last_branch and not self._target_branch_var.get().strip():
                self._target_branch_var.set(last_branch)

            last_transition = pr_cfg.get("last_transition", "")
            if last_transition:
                self._transition_var.set(last_transition)

            if "auto_merge" in pr_cfg:
                self._auto_merge_var.set(bool(pr_cfg["auto_merge"]))

            if "auto_close_jira" in pr_cfg:
                self._auto_close_jira_var.set(bool(pr_cfg["auto_close_jira"]))
        except Exception:
            pass

    def _save_last_settings(self):
        """L3: Save current settings to settings.json for next session."""
        try:
            import json
            with open("settings.json", "r") as f:
                cfg = json.load(f)

            pr_cfg = cfg.setdefault("pr_review", {})
            pr_cfg["last_target_branch"] = self._target_branch_var.get().strip()
            pr_cfg["last_transition"] = self._transition_var.get().strip()
            pr_cfg["auto_merge"] = self._auto_merge_var.get()
            pr_cfg["auto_close_jira"] = self._auto_close_jira_var.get()

            with open("settings.json", "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # L5: REVIEWER CHIP KEYBOARD DELETE
    # ──────────────────────────────────────────────────────────────────────

    def _on_reviewer_backspace(self, event):
        """L5: When Backspace is pressed in an empty reviewer entry,
        remove the last reviewer chip."""
        current_text = self._reviewers_var.get()
        if current_text:
            # Entry has text — let default Backspace behaviour handle it
            return
        # Entry is empty — remove last chip
        if self._reviewer_chips:
            last_username = self._reviewer_chips[-1]
            children = self._chip_frame.winfo_children()
            if children:
                last_chip: tk.Frame = children[-1]  # type: ignore[assignment]
                self._remove_reviewer_chip(last_username, last_chip)
            return "break"  # Suppress default Backspace

    # ──────────────────────────────────────────────────────────────────────
    # L6: BRANCH VALIDATION BEFORE PIPELINE
    # ──────────────────────────────────────────────────────────────────────

    def _validate_target_branch(self, target_branch: str) -> bool:
        """L6: Validate that the target branch exists on the remote via
        Bitbucket API before starting the pipeline.

        Returns True if valid (or if validation cannot be performed),
        False if the branch definitely does not exist.
        """
        ctrl = self._get_controller()
        if not ctrl:
            return True  # Can't validate without controller — allow anyway

        import threading

        result_holder: list = []
        done_event = threading.Event()

        def _on_branches(branches):
            result_holder.append(branches)
            done_event.set()

        ctrl.fetch_branches(filter_text=target_branch, callback=_on_branches)

        # Wait up to 10 seconds for the API response
        done_event.wait(timeout=10)

        if not result_holder:
            # Timeout or error — allow pipeline to proceed
            return True

        branches = result_holder[0]
        if not branches:
            # API returned empty — could be network issue, allow anyway
            return True

        # Check if exact branch name exists in results
        if target_branch in branches:
            return True

        # Branch not found — ask user
        from tkinter import messagebox
        return messagebox.askyesno(
            "Branch Not Found",
            f"Target branch '{target_branch}' was not found on the remote.\n\n"
            f"Available branches matching '{target_branch}':\n"
            f"  {', '.join(branches[:10]) if branches else '(none)'}\n\n"
            f"Proceed anyway?"
        )

    def _append_status(self, text: str):
        """Append text to the status log (thread-safe via root.after).
        L1: Auto-applies color tags based on content patterns."""
        def _detect_tag(line: str) -> str:
            """Detect the appropriate tag for syntax highlighting."""
            stripped = line.strip()
            if stripped.startswith("✅") or stripped.startswith("✓") or "COMPLETE" in stripped.upper():
                return "success"
            if stripped.startswith("❌") or stripped.startswith("✗") or "FAILED" in stripped.upper():
                return "error"
            if stripped.startswith("⚠") or "warning" in stripped.lower():
                return "warning"
            if stripped.startswith("[") and "/" in stripped[:6]:
                return "phase"
            if stripped.startswith("═") or stripped.startswith("─"):
                return "header"
            if stripped.startswith("🤖") or stripped.startswith("📊") or stripped.startswith("📤"):
                return "info"
            return ""

        def _do():
            self._status_text.configure(state=tk.NORMAL)
            tag = _detect_tag(text)
            if tag:
                start = self._status_text.index(tk.END)
                self._status_text.insert(tk.END, text + "\n")
                end = self._status_text.index(tk.END)
                self._status_text.tag_add(tag, start, end)
            else:
                self._status_text.insert(tk.END, text + "\n")
            self._status_text.see(tk.END)
            self._status_text.configure(state=tk.DISABLED)

        self.root.after(0, _do)

    # ──────────────────────────────────────────────────────────────────────
    # H1: MERGE PR (standalone quick action)
    # ──────────────────────────────────────────────────────────────────────

    def _merge_pr_only(self):
        """H1: Merge the current PR (standalone quick action)."""
        ctrl = self._get_controller()
        if not ctrl:
            return

        if not messagebox.askyesno("Confirm Merge",
                                    "Merge the current PR?\n\n"
                                    "This will merge the PR into the target branch."):
            return

        self._append_status("\n🔀 Merging PR...")
        ctrl.merge_pr(self._on_merge_result)

    def _on_merge_result(self, result):
        """H1: Handle merge PR result."""
        if result.get("success"):
            self._append_status("✅ PR merged successfully!")
            self.show_info("PR Merged", "Pull Request merged successfully!")
            # Stop polling if active (PR is now merged)
            if self._polling_active:
                self._stop_polling()
        else:
            error = result.get("error", "Unknown error")
            self._append_status(f"✗ Merge failed: {error}")
            self.show_error("Merge Failed", error)

    # ──────────────────────────────────────────────────────────────────────
    # H2: PR APPROVAL POLLING (auto-refresh with countdown)
    # ──────────────────────────────────────────────────────────────────────

    def _toggle_polling(self):
        """H2: Toggle approval polling on/off."""
        if self._polling_active:
            self._stop_polling()
        else:
            self._start_polling()

    def _start_polling(self):
        """H2: Start polling for PR approval every N seconds."""
        ctrl = self._get_controller()
        if not ctrl:
            return

        self._polling_active = True
        self._poll_btn.config(text="⏹ Stop Polling")
        self._append_status(f"\n⏳ Polling for PR approval every {self._poll_interval}s...")
        self._poll_status_label.config(text="Polling active", foreground='blue')

        # Fire first check immediately
        self._poll_tick()

    def _stop_polling(self):
        """H2: Stop the approval polling loop."""
        self._polling_active = False
        self._poll_btn.config(text="⏳ Poll Approval")
        self._poll_status_label.config(text="", foreground='gray')

        if self._poll_after_id:
            self.root.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        if self._poll_countdown_id:
            self.root.after_cancel(self._poll_countdown_id)
            self._poll_countdown_id = None

        self._append_status("⏹ Polling stopped")

    def _poll_tick(self):
        """H2: Execute one poll cycle — check status, then schedule next."""
        if not self._polling_active:
            return

        ctrl = self._get_controller()
        if not ctrl:
            self._stop_polling()
            return

        ctrl.check_pr_status(self._on_poll_result)

    def _on_poll_result(self, result):
        """H2: Handle poll result — auto-stop if approved/merged, else schedule next."""
        if not self._polling_active:
            return

        if result.get("success"):
            state = result.get("state", "UNKNOWN")
            approved = result.get("approved", False)
            reviewers = result.get("reviewers", [])

            icon = "✅" if approved else ("🔀" if state == "MERGED" else "⏳")
            self._append_status(f"  {icon} Poll: PR state={state}, approved={approved}")

            for r in reviewers:
                status_icon = "✅" if r["approved"] else "⏳"
                self._append_status(f"    {status_icon} {r['user']}: {r['status']}")

            if approved:
                self._append_status("  🎉 PR is approved! Stopping poll.")
                self._poll_status_label.config(text="✅ Approved!", foreground='green')
                self._stop_polling()
                return
            elif state == "MERGED":
                self._append_status("  🔀 PR already merged. Stopping poll.")
                self._poll_status_label.config(text="🔀 Merged", foreground='green')
                self._stop_polling()
                return
        else:
            self._append_status(f"  ⚠ Poll error: {result.get('error', '?')}")

        # Schedule next poll with countdown
        self._poll_countdown = self._poll_interval
        self._poll_countdown_tick()

    def _poll_countdown_tick(self):
        """H2: Update countdown label and schedule next tick or poll."""
        if not self._polling_active:
            return

        if self._poll_countdown <= 0:
            self._poll_status_label.config(text="Checking...", foreground='blue')
            self._poll_tick()
            return

        self._poll_status_label.config(
            text=f"Next poll in {self._poll_countdown}s", foreground='orange')
        self._poll_countdown -= 1
        self._poll_countdown_id = self.root.after(1000, self._poll_countdown_tick)

    # ──────────────────────────────────────────────────────────────────────
    # H3: ELAPSED TIME INDICATOR (during pipeline execution)
    # ──────────────────────────────────────────────────────────────────────

    def _start_elapsed_timer(self):
        """H3: Start the elapsed time counter."""
        self._elapsed_start = time.monotonic()
        self._update_elapsed()

    def _stop_elapsed_timer(self):
        """H3: Stop the elapsed time counter and show final time."""
        if self._elapsed_after_id:
            self.root.after_cancel(self._elapsed_after_id)
            self._elapsed_after_id = None

        if self._elapsed_start:
            elapsed = time.monotonic() - self._elapsed_start
            h, rem = divmod(int(elapsed), 3600)
            m, s = divmod(rem, 60)
            self._elapsed_label.config(
                text=f"⏱ {h:02d}:{m:02d}:{s:02d} (done)",
                foreground='green')
            self._elapsed_start = 0.0

    def _update_elapsed(self):
        """H3: Update the elapsed time label every second."""
        if not self._elapsed_start:
            return

        elapsed = time.monotonic() - self._elapsed_start
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        self._elapsed_label.config(
            text=f"⏱ {h:02d}:{m:02d}:{s:02d}",
            foreground='blue')
        self._elapsed_after_id = self.root.after(1000, self._update_elapsed)

    def _on_tab_selected(self, event=None):
        """Re-detect source branch when user switches to this tab.
        Only runs if source branch is still showing a placeholder."""
        try:
            # Check if this tab is the currently selected one
            selected = self._notebook.select()
            if str(self) != str(selected):
                return  # Different tab selected, ignore

            current_src = self._source_branch_var.get()
            if current_src in ("(no repo loaded)", "(unknown)", "(error)"):
                self._auto_populate_target_branch()
        except Exception:
            pass

    def _auto_populate_target_branch(self):
        """Improvement 1: Auto-populate target branch from workflow.

        Uses silent controller lookup (no error dialog) since this runs
        during startup when the controller may not be initialized yet.
        Also M3: auto-detect source branch from local git repo.
        """
        try:
            ctrl = getattr(self.context.controller, 'pr_review_controller', None)
            if ctrl:
                branch = ctrl.get_target_branch_from_workflow()
                if branch:
                    self._target_branch_var.set(branch)

                # M3: Auto-detect source branch
                repo_path = ctrl.workflow.get_workflow_step("REPOSITORY_PATH")
                if repo_path:
                    from model.orchestrators.pr_review_orchestrator import git_get_current_branch
                    src = git_get_current_branch(repo_path)
                    if src:
                        self._source_branch_var.set(src)
                    else:
                        self._source_branch_var.set("(unknown)")
                else:
                    self._source_branch_var.set("(no repo loaded)")
        except Exception:
            self._source_branch_var.set("(error)")

    def _auto_populate_commit_message(self, *_args):
        """Item 12: Auto-populate commit message from JIRA key when the
        commit message field is empty or matches the previous auto-generated
        pattern.  Triggered by a trace on the issue_var StringVar.
        Also M4: auto-populate PR title from issue key."""
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        current_msg = self._commit_msg_text.get("1.0", tk.END).strip()

        # Only auto-fill if the field is empty or matches the auto pattern
        if not current_msg or current_msg.startswith("[") and current_msg.endswith("]"):
            if issue_key and not issue_key.endswith("-"):
                self._commit_msg_text.delete("1.0", tk.END)
                self._commit_msg_text.insert("1.0", f"[{issue_key}] Validation updates")

        # M4: Auto-populate PR title from issue key + commit message
        current_title = self._pr_title_var.get().strip()
        if not current_title or current_title.startswith("["):
            if issue_key and not issue_key.endswith("-"):
                msg = self._commit_msg_text.get("1.0", tk.END).strip()
                # Strip existing issue key prefix to avoid duplication
                # e.g. "[TSESSD-99999] Validation updates" → "Validation updates"
                import re
                msg_clean = re.sub(r'^\[' + re.escape(issue_key) + r'\]\s*', '', msg)
                self._pr_title_var.set(f"[{issue_key}] {msg_clean}" if msg_clean else f"[{issue_key}] Feature branch merge")

    def _on_commit_msg_changed(self, _event=None):
        """Auto-sync PR title from the first line of the commit message.

        Only updates if the user hasn't manually edited the PR title field.
        Uses the <<Modified>> virtual event from tk.Text.
        """
        # Reset the modified flag so the event fires again next time
        try:
            self._commit_msg_text.edit_modified(False)
        except Exception:
            pass

        if self._pr_title_user_edited:
            return

        issue_key = self._get_issue_key()
        if not issue_key:
            return

        msg = self._commit_msg_text.get("1.0", tk.END).strip()
        if not msg:
            return

        # Use only the first line of the commit message for the PR title
        first_line = msg.split("\n")[0].strip()
        import re
        # Strip existing [KEY] prefix to avoid duplication
        first_line_clean = re.sub(
            r'^\[' + re.escape(issue_key) + r'\]\s*', '', first_line)
        if first_line_clean:
            self._pr_title_var.set(f"[{issue_key}] {first_line_clean}")

    def _save_used_reviewers(self):
        """Item 16: Save the current reviewers to settings.json via controller.

        Called after successful PR creation so that used reviewers appear
        in the static fallback list for future autocomplete.
        Uses chip-based reviewer list (Improvement 3).
        """
        try:
            reviewers = list(self._reviewer_chips)
            if reviewers:
                ctrl = self._get_controller()
                if ctrl:
                    ctrl.save_recent_reviewers(reviewers)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # AI COMMIT MESSAGE GENERATION
    # ──────────────────────────────────────────────────────────────────────

    def _generate_ai_commit_message(self):
        """Handle the '✨ AI Generate' button click.

        Calls the controller to generate a commit message from the git diff
        using the AI Gateway.  Disables the button while running.
        """
        ctrl = self._get_controller()
        if not ctrl:
            return

        issue_key = self._get_issue_key()
        if not issue_key:
            from tkinter import messagebox
            messagebox.showwarning(
                "Missing Issue Key",
                "Please enter a JIRA Issue Key first.",
                parent=self.root,
            )
            return

        # Disable button and show progress
        self._ai_commit_btn.configure(state="disabled", text="⏳ Generating...")
        self._append_status("🤖 Generating AI commit message...")

        ctrl.generate_ai_commit_message(issue_key, self._on_ai_commit_result)

    def _on_ai_commit_result(self, result: dict):
        """Callback from controller with the AI-generated commit message."""
        # Re-enable button
        self._ai_commit_btn.configure(state="normal", text="✨ AI Generate")

        if result.get("success"):
            message = result["message"]
            # Take only the first line for the commit message field
            # Feature 14: Insert full multi-line message into Text widget
            self._commit_msg_text.delete("1.0", tk.END)
            self._commit_msg_text.insert("1.0", message.strip())
            first_line = message.split("\n")[0].strip()
            self._append_status(f"✅ AI commit message set: {first_line}")
        else:
            error = result.get("error", "Unknown error")
            self._append_status(f"⚠️ AI commit message failed: {error}")
            from tkinter import messagebox
            messagebox.showerror(
                "AI Commit Message",
                f"Failed to generate commit message:\n{error}",
                parent=self.root,
            )

    # ──────────────────────────────────────────────────────────────────────
    # M1: AI PR DESCRIPTION GENERATION
    # ──────────────────────────────────────────────────────────────────────

    def _generate_ai_pr_description(self):
        """M1: Handle the '✨ AI Generate' button click for PR description.

        Calls the controller to generate a PR description from the git diff
        using the AI Gateway.  Disables the button while running.
        """
        ctrl = self._get_controller()
        if not ctrl:
            return

        issue_key = self._get_issue_key()
        if not issue_key:
            from tkinter import messagebox
            messagebox.showwarning(
                "Missing Issue Key",
                "Please enter a JIRA Issue Key first.",
                parent=self.root,
            )
            return

        target_branch = self._target_branch_var.get().strip() or "master"

        # Disable button and show progress
        self._ai_pr_desc_btn.configure(state="disabled", text="⏳ Generating...")
        self._append_status("🤖 Generating AI PR description...")

        ctrl.generate_ai_pr_description(
            issue_key, target_branch, self._on_ai_pr_desc_result
        )

    def _on_ai_pr_desc_result(self, result: dict):
        """M1: Callback from controller with the AI-generated PR description."""
        # Re-enable button
        self._ai_pr_desc_btn.configure(state="normal", text="✨ AI Generate")

        if result.get("success"):
            description = result["description"]
            self._pr_desc_text.delete("1.0", tk.END)
            self._pr_desc_text.insert("1.0", description.strip())
            preview = description.split("\n")[0].strip()[:80]
            self._append_status(f"✅ AI PR description set: {preview}...")
        else:
            error = result.get("error", "Unknown error")
            self._append_status(f"⚠️ AI PR description failed: {error}")
            from tkinter import messagebox
            messagebox.showerror(
                "AI PR Description",
                f"Failed to generate PR description:\n{error}",
                parent=self.root,
            )

    # ──────────────────────────────────────────────────────────────────────
    # REVIEWER AUTOCOMPLETE (Item 16)
    # ──────────────────────────────────────────────────────────────────────

    def _on_reviewer_key(self, event):
        """Handle keystrokes in the reviewers Entry with 1000ms debounce.

        With chip-based UI (Improvement 3), the entire entry text is the
        search query (no comma splitting needed).
        Minimum 4 characters required to trigger API search.
        """
        # Ignore navigation / modifier keys
        if event.keysym in ("Down", "Up", "Left", "Right", "Escape",
                            "Return", "Tab", "Shift_L", "Shift_R",
                            "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return

        # Cancel any pending debounce
        if self._ac_debounce_id is not None:
            self.root.after_cancel(self._ac_debounce_id)
            self._ac_debounce_id = None

        # With chip UI, the full entry text is the search query
        query = self._reviewers_var.get().strip()

        if len(query) < 4:
            self._hide_autocomplete()
            return

        # Schedule the API call after 1000ms debounce (1 second)
        self._ac_debounce_id = self.root.after(
            1000, lambda: self._fire_autocomplete(query)
        )

    def _fire_autocomplete(self, query: str):
        """Send the autocomplete query to the controller."""
        # Use silent controller lookup — don't show error dialog during typing
        ctrl = getattr(self.context.controller, 'pr_review_controller', None)
        if not ctrl:
            return
        ctrl.fetch_reviewer_suggestions(query, self._show_autocomplete)

    def _show_autocomplete(self, results):
        """Callback from controller — display the autocomplete dropdown.

        Args:
            results: list of dicts with 'username', 'display_name', 'email'.
        """
        # Tear down any existing dropdown
        self._destroy_autocomplete()

        if not results:
            return

        # Build display strings; exclude already-added chip reviewers
        self._ac_display_map = {}  # display_text -> username
        display_items: list[str] = []
        for item in results:
            uname = item.get("username", "")
            if uname in self._reviewer_chips:
                continue  # skip already-added reviewers
            dname = item.get("display_name", "")
            email = item.get("email", "")
            display = f"{uname}  —  {dname}"
            if email:
                display += f"  ({email})"
            display_items.append(display)
            self._ac_display_map[display] = uname

        if not display_items:
            return

        # Calculate width: use the longest display string + padding
        max_chars = max((len(d) for d in display_items), default=60)
        listbox_width = max(max_chars + 4, 80)  # minimum 80 chars wide

        # Create a Toplevel that floats over the entry
        entry = self._reviewers_entry
        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()

        top = tk.Toplevel(self.root)
        top.wm_overrideredirect(True)
        top.wm_geometry(f"+{x}+{y}")
        top.lift()

        listbox = tk.Listbox(
            top, width=listbox_width, height=min(len(display_items), 10),
            font=("Consolas", 9), selectmode=tk.SINGLE,
        )
        listbox.pack(fill=tk.BOTH, expand=True)

        for display in display_items:
            listbox.insert(tk.END, display)

        # Use ButtonPress (not ButtonRelease) so click fires before FocusOut
        # destroys the Toplevel on Windows
        listbox.bind("<ButtonPress-1>", self._ac_on_click)
        listbox.bind("<Return>", self._ac_on_listbox_return)
        listbox.bind("<Escape>", self._hide_autocomplete)

        # Prevent FocusOut from destroying dropdown while mouse is over it
        listbox.bind("<Enter>", lambda _: setattr(self, '_ac_clicking', True))
        listbox.bind("<Leave>", lambda _: setattr(self, '_ac_clicking', False))

        if listbox.size() > 0:
            listbox.selection_set(0)

        self._ac_toplevel = top
        self._ac_listbox = listbox

    def _destroy_autocomplete(self):
        """Destroy the autocomplete Toplevel if it exists."""
        if self._ac_toplevel is not None:
            try:
                self._ac_toplevel.destroy()
            except Exception:
                pass
            self._ac_toplevel = None
            self._ac_listbox = None

    def _hide_autocomplete(self, event=None):
        """Hide the autocomplete dropdown (bound to FocusOut / Escape)."""
        # If mouse is over the listbox, don't destroy — user is clicking
        if self._ac_clicking:
            return
        # Small delay so click events on the listbox can fire first
        self.root.after(200, self._destroy_autocomplete)

    def _ac_focus_listbox(self, event=None):
        """Move focus into the autocomplete listbox (Down arrow)."""
        if self._ac_listbox is not None:
            self._ac_listbox.focus_set()
            if not self._ac_listbox.curselection():
                self._ac_listbox.selection_set(0)
            return "break"

    def _ac_select_current(self, event=None):
        """Select the currently highlighted listbox item (Return in entry)."""
        if self._ac_listbox is not None and self._ac_listbox.curselection():
            self._ac_insert_selection(self._ac_listbox.curselection()[0])
            return "break"

    def _ac_on_click(self, event):
        """Handle mouse click on a listbox item."""
        if self._ac_listbox is None:
            return
        sel = self._ac_listbox.nearest(event.y)
        if sel >= 0:
            # Reset flag before inserting (which destroys the dropdown)
            self._ac_clicking = False
            self._ac_insert_selection(sel)

    def _ac_on_listbox_return(self, event):
        """Handle Return key inside the listbox."""
        if self._ac_listbox is None:
            return "break"
        sel = self._ac_listbox.curselection()
        if sel:
            self._ac_insert_selection(sel[0])
        return "break"

    def _ac_insert_selection(self, index: int):
        """Improvement 3: Add the selected username as a chip/tag instead of
        appending to comma-separated text.
        Feature 13: Also extracts display name for tooltip on chip."""
        if self._ac_listbox is None:
            return

        display_text = self._ac_listbox.get(index)
        username = self._ac_display_map.get(display_text, display_text)

        # Feature 13: Extract display name from the display string
        # Format is "username  —  Display Name  (email)"
        display_name = ""
        if "  —  " in display_text:
            after_dash = display_text.split("  —  ", 1)[1]
            # Strip optional email suffix
            if "  (" in after_dash:
                display_name = after_dash.split("  (", 1)[0].strip()
            else:
                display_name = after_dash.strip()

        # Add as chip (skip if already added)
        self._add_reviewer_chip(username, display_name=display_name)

        # Clear the search entry for the next search
        self._reviewers_var.set("")
        self._reviewers_entry.focus_set()

        self._destroy_autocomplete()

    # ──────────────────────────────────────────────────────────────────────
    # REVIEWER CHIP / TAG MANAGEMENT (Improvement 3)
    # ──────────────────────────────────────────────────────────────────────

    def _add_reviewer_chip(self, username: str, display_name: str = ""):
        """Add a reviewer chip/tag to the chip container.

        Each chip is a small Frame containing a Label (username) and a
        '×' Button to remove it.  Duplicate usernames are silently ignored.

        Feature 13: If *display_name* is provided, a hover tooltip shows
        the full display name on the chip.
        """
        username = username.strip()
        if not username or username in self._reviewer_chips:
            return

        self._reviewer_chips.append(username)

        # Feature 13: Store display name mapping
        if display_name:
            self._reviewer_display_names[username] = display_name

        chip = tk.Frame(
            self._chip_frame, bg="#e0e7ff", bd=1, relief=tk.RAISED,
            padx=4, pady=1,
        )
        chip.pack(side=tk.LEFT, padx=2, pady=2)

        chip_label = tk.Label(
            chip, text=username, bg="#e0e7ff", fg="#1e3a5f",
            font=("Arial", 9),
        )
        chip_label.pack(side=tk.LEFT)

        # Feature 13: Add tooltip with display name if available
        tooltip_text = display_name if display_name else username
        ToolTip(chip_label, tooltip_text)

        remove_btn = tk.Button(
            chip, text="×", bg="#e0e7ff", fg="#c0392b",
            font=("Arial", 8, "bold"), bd=0, cursor="hand2",
            activebackground="#fdd", activeforeground="#c0392b",
            command=lambda u=username, c=chip: self._remove_reviewer_chip(u, c),
        )
        remove_btn.pack(side=tk.LEFT, padx=(3, 0))

    def _remove_reviewer_chip(self, username: str, chip_widget: tk.Frame):
        """Remove a reviewer chip from the container and the internal list."""
        if username in self._reviewer_chips:
            self._reviewer_chips.remove(username)
        try:
            chip_widget.destroy()
        except Exception:
            pass

    def _clear_all_chips(self):
        """Remove all reviewer chips."""
        self._reviewer_chips.clear()
        for child in self._chip_frame.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

    def _load_reviewer_suggestions(self):
        """Item 16: Load reviewer suggestions from settings.json.

        Reads 'pr_review.reviewers' list from settings.  Falls back to an
        empty list if the key is missing or the file cannot be read.
        """
        try:
            import json
            settings_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "settings.json"
            )
            with open(settings_path, "r") as f:
                cfg = json.load(f)
            reviewers = cfg.get("pr_review", {}).get("reviewers", [])
            if isinstance(reviewers, list):
                return reviewers
        except Exception:
            pass
        return []

    # ──────────────────────────────────────────────────────────────────────
    # BRANCH AUTOCOMPLETE (Feature 10)
    # ──────────────────────────────────────────────────────────────────────

    def _on_branch_key(self, event):
        """Handle keystrokes in the target branch Entry with 500ms debounce.

        Minimum 2 characters required to trigger API search.
        """
        if event.keysym in ("Down", "Up", "Left", "Right", "Escape",
                            "Return", "Tab", "Shift_L", "Shift_R",
                            "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return

        # Cancel any pending debounce
        if self._branch_ac_debounce_id is not None:
            self.root.after_cancel(self._branch_ac_debounce_id)
            self._branch_ac_debounce_id = None

        query = self._target_branch_var.get().strip()

        if len(query) < 2:
            self._destroy_branch_autocomplete()
            return

        # Schedule the API call after 500ms debounce
        self._branch_ac_debounce_id = self.root.after(
            500, lambda: self._fire_branch_autocomplete(query)
        )

    def _fire_branch_autocomplete(self, query: str):
        """Send the branch autocomplete query to the controller."""
        ctrl = getattr(self.context.controller, 'pr_review_controller', None)
        if not ctrl:
            return
        ctrl.fetch_branches(query, self._show_branch_autocomplete)

    def _show_branch_autocomplete(self, results):
        """Callback from controller — display the branch autocomplete dropdown.

        Args:
            results: list of branch name strings.
        """
        self._destroy_branch_autocomplete()

        if not results:
            return

        # Filter to only branches matching the current query (case-insensitive)
        query = self._target_branch_var.get().strip().lower()
        filtered = [b for b in results if query in b.lower()] if query else results

        if not filtered:
            return

        # Calculate width
        max_chars = max((len(b) for b in filtered), default=30)
        listbox_width = max(max_chars + 4, 40)

        # Create a Toplevel that floats over the entry
        entry = self._branch_entry
        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()

        top = tk.Toplevel(self.root)
        top.wm_overrideredirect(True)
        top.wm_geometry(f"+{x}+{y}")
        top.lift()

        listbox = tk.Listbox(
            top, width=listbox_width, height=min(len(filtered), 10),
            font=("Consolas", 9), selectmode=tk.SINGLE,
        )
        listbox.pack(fill=tk.BOTH, expand=True)

        for branch in filtered:
            listbox.insert(tk.END, branch)

        listbox.bind("<ButtonRelease-1>", self._branch_ac_on_click)
        listbox.bind("<Return>", self._branch_ac_on_listbox_return)
        listbox.bind("<Escape>", self._hide_branch_autocomplete)

        if listbox.size() > 0:
            listbox.selection_set(0)

        self._branch_ac_toplevel = top
        self._branch_ac_listbox = listbox

    def _destroy_branch_autocomplete(self):
        """Destroy the branch autocomplete Toplevel if it exists."""
        if self._branch_ac_toplevel is not None:
            try:
                self._branch_ac_toplevel.destroy()
            except Exception:
                pass
            self._branch_ac_toplevel = None
            self._branch_ac_listbox = None

    def _hide_branch_autocomplete(self, event=None):
        """Hide the branch autocomplete dropdown (bound to FocusOut / Escape)."""
        self.root.after(150, self._destroy_branch_autocomplete)

    def _branch_ac_focus_listbox(self, event=None):
        """Move focus into the branch autocomplete listbox (Down arrow)."""
        if self._branch_ac_listbox is not None:
            self._branch_ac_listbox.focus_set()
            if not self._branch_ac_listbox.curselection():
                self._branch_ac_listbox.selection_set(0)
            return "break"

    def _branch_ac_select_current(self, event=None):
        """Select the currently highlighted branch listbox item (Return in entry)."""
        if self._branch_ac_listbox is not None and self._branch_ac_listbox.curselection():
            self._branch_ac_insert_selection(self._branch_ac_listbox.curselection()[0])
            return "break"

    def _branch_ac_on_click(self, event):
        """Handle mouse click on a branch listbox item."""
        if self._branch_ac_listbox is None:
            return
        sel = self._branch_ac_listbox.nearest(event.y)
        if sel >= 0:
            self._branch_ac_insert_selection(sel)

    def _branch_ac_on_listbox_return(self, event):
        """Handle Return key inside the branch listbox."""
        if self._branch_ac_listbox is None:
            return "break"
        sel = self._branch_ac_listbox.curselection()
        if sel:
            self._branch_ac_insert_selection(sel[0])
        return "break"

    def _branch_ac_insert_selection(self, index: int):
        """Set the target branch Entry to the selected branch name."""
        if self._branch_ac_listbox is None:
            return

        branch_name = self._branch_ac_listbox.get(index)
        self._target_branch_var.set(branch_name)

        # Move cursor to end of entry
        self._branch_entry.icursor(tk.END)
        self._branch_entry.focus_set()

        self._destroy_branch_autocomplete()

    # ──────────────────────────────────────────────────────────────────────
    # DIFF PREVIEW POPUP (Feature 11)
    # ──────────────────────────────────────────────────────────────────────

    def _open_diff_preview(self):
        """Feature 11: Open a dedicated diff preview popup with syntax
        highlighting instead of appending to the status log."""
        ctrl = self._get_controller()
        if not ctrl:
            return

        self._diff_popup_btn.configure(state="disabled", text="⏳ Loading...")
        self._append_status("🔍 Loading full diff preview...")
        ctrl.get_full_diff(self._on_full_diff_result)

    def _on_full_diff_result(self, result):
        """Callback from controller with the full unified diff."""
        self._diff_popup_btn.configure(state="normal", text="🔍 Diff Preview")

        if result.get("success"):
            diff_text = result.get("diff", "")
            branch = result.get("branch", "?")
            if not diff_text:
                self._append_status("ℹ No uncommitted changes to preview")
                self.show_info("Diff Preview", "No uncommitted changes found.")
                return
            self._show_diff_popup(diff_text, branch)
        else:
            error = result.get("error", "Unknown error")
            self._append_status(f"✗ Diff preview failed: {error}")
            self.show_error("Diff Preview", f"Failed to load diff:\n{error}")

    def _show_diff_popup(self, diff_text: str, branch: str = ""):
        """Feature 11: Display the full diff in a popup window with basic
        syntax highlighting (added lines green, removed lines red)."""
        popup = tk.Toplevel(self.root)
        popup.title(f"Diff Preview — {branch}" if branch else "Diff Preview")
        popup.geometry("900x600")
        popup.transient(self.root)

        # Header
        header = ttk.Frame(popup, padding="5")
        header.pack(fill=tk.X)
        ttk.Label(header, text=f"📊 Full Diff — Branch: {branch}",
                  font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Close", command=popup.destroy).pack(side=tk.RIGHT)

        # Diff text widget with syntax highlighting
        text_frame = ttk.Frame(popup)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        diff_widget = tk.Text(
            text_frame, wrap=tk.NONE, font=("Consolas", 10),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
        )
        diff_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbars
        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                                  command=diff_widget.yview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        diff_widget.configure(yscrollcommand=y_scroll.set)

        x_scroll = ttk.Scrollbar(popup, orient=tk.HORIZONTAL,
                                  command=diff_widget.xview)
        x_scroll.pack(fill=tk.X, padx=5)
        diff_widget.configure(xscrollcommand=x_scroll.set)

        # Configure syntax highlighting tags
        diff_widget.tag_configure("added", foreground="#4ec9b0")       # green
        diff_widget.tag_configure("removed", foreground="#f44747")     # red
        diff_widget.tag_configure("header", foreground="#569cd6",      # blue
                                   font=("Consolas", 10, "bold"))
        diff_widget.tag_configure("hunk", foreground="#c586c0")        # purple
        diff_widget.tag_configure("file_sep", foreground="#808080")    # gray

        # Insert diff text with highlighting
        for line in diff_text.splitlines(True):
            stripped = line.rstrip("\n")
            if stripped.startswith("+++") or stripped.startswith("---"):
                diff_widget.insert(tk.END, line, "header")
            elif stripped.startswith("@@"):
                diff_widget.insert(tk.END, line, "hunk")
            elif stripped.startswith("+"):
                diff_widget.insert(tk.END, line, "added")
            elif stripped.startswith("-"):
                diff_widget.insert(tk.END, line, "removed")
            elif stripped.startswith("diff "):
                diff_widget.insert(tk.END, line, "file_sep")
            else:
                diff_widget.insert(tk.END, line)

        diff_widget.configure(state=tk.DISABLED)

        # Status bar
        line_count = diff_text.count("\n")
        added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
        status = ttk.Label(popup,
                           text=f"  {line_count} lines  |  +{added} added  |  -{removed} removed",
                           font=("Arial", 9), foreground="gray")
        status.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._append_status(f"✓ Diff preview opened ({line_count} lines, +{added}/-{removed})")

    # ──────────────────────────────────────────────────────────────────────
    # PIPELINE HISTORY / RESUME (Feature 12)
    # ──────────────────────────────────────────────────────────────────────

    def _check_resume_pipeline(self):
        """Feature 12: Check if there is a saved pipeline state to resume.

        Called during startup (deferred) to offer resuming a crashed pipeline.
        """
        try:
            ctrl = getattr(self.context.controller, 'pr_review_controller', None)
            if not ctrl:
                return

            state = ctrl.load_pipeline_state()
            if not state:
                return

            issue_key = state.get("issue_key", "?")
            target_branch = state.get("target_branch", "?")
            last_phase = state.get("last_phase", "?")

            msg = (
                f"A previous pipeline run was interrupted.\n\n"
                f"JIRA: {issue_key}\n"
                f"Target: {target_branch}\n"
                f"Last completed phase: {last_phase}\n\n"
                f"Would you like to resume from where it left off?"
            )
            if messagebox.askyesno("Resume Pipeline?", msg):
                self._resume_pipeline(state)
            else:
                ctrl.clear_pipeline_state()
                self._append_status("ℹ Cleared saved pipeline state")
        except Exception:
            pass

    def _resume_pipeline(self, state: dict):
        """Feature 12: Resume a pipeline from saved state.

        Restores the UI fields from the saved state and re-runs the pipeline.
        """
        try:
            # Restore UI fields from saved state
            issue_key = state.get("issue_key", "")
            if issue_key:
                self.context.get_var('issue_var').set(issue_key)

            target_branch = state.get("target_branch", "")
            if target_branch:
                self._target_branch_var.set(target_branch)

            reviewers = state.get("reviewers", [])
            self._clear_all_chips()
            for r in reviewers:
                self._add_reviewer_chip(r)

            commit_msg = state.get("commit_message", "")
            if commit_msg:
                self._commit_msg_text.delete("1.0", tk.END)
                self._commit_msg_text.insert("1.0", commit_msg)

            self._append_status(f"\n🔄 Resuming pipeline from: {state.get('last_phase', '?')}")
            self._append_status(f"  JIRA: {issue_key} | Target: {target_branch}")

            # Clear the saved state before re-running
            ctrl = self._get_controller()
            if ctrl:
                ctrl.clear_pipeline_state()

            # Re-run the full pipeline (user can adjust fields before clicking Run)
            self.show_info(
                "Pipeline Restored",
                f"Pipeline state restored.\n\n"
                f"JIRA: {issue_key}\n"
                f"Target: {target_branch}\n"
                f"Reviewers: {', '.join(reviewers)}\n\n"
                f"Click '▶ Run Full Pipeline' to resume."
            )
        except Exception as e:
            self._append_status(f"⚠ Failed to resume pipeline: {e}")
