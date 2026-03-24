#!/usr/bin/env python3
"""
view/tabs/repository_tab.py
============================
Repository Tab (View) — matches gui/app.py create_repo_tab()
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from view.tabs.base_tab import BaseTab


class RepositoryTab(BaseTab):
    """
    Repository Tab — clone repository and create feature branch.

    Layout matches gui/app.py create_repo_tab() exactly:
      Row 0: Bold title (colspan 2)
      Row 1: Repository combobox (searchable, width=37)
      Row 2: Base Branch entry
      Row 3: Issue Key entry
      Row 4: btn_frame — "1. Clone Repository" | "2. Create Feature Branch"
      Row 5: LabelFrame "Repository Status" with ScrolledText (height=15)
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📦 Repository")
        self._build_ui()
        # Auto-fetch repos on tab creation (guard inside _fetch_repos)
        self._fetch_repos()

    def _build_ui(self):
        self.configure(padding="10")

        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')

        # Row 0 — title
        ttk.Label(self, text="Clone Repository & Create Branch",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)

        # Row 1 — Repository (searchable combobox, width=37 matches original)
        ttk.Label(self, text="Repository:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.repo_combo = ttk.Combobox(
            self, textvariable=self.context.get_var('repo_var'), width=37)
        self.repo_combo.grid(row=1, column=1, pady=5)
        self.repo_combo.bind('<KeyRelease>', self._filter_repos)

        # Row 2 — Base Branch
        ttk.Label(self, text="Base Branch:").grid(
            row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('branch_var'),
                  width=40).grid(row=2, column=1, pady=5)

        # Row 3 — Issue Key (label text matches original: "Issue Key:" not "JIRA Issue Key:")
        ttk.Label(self, text="Issue Key:").grid(
            row=3, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('issue_var'),
                  width=40).grid(row=3, column=1, pady=5)

        # Row 4 — Buttons (numbered to match original)
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)

        self.clone_btn = ttk.Button(
            btn_frame, text="1. Clone Repository",
            command=self._clone_repo)
        self.clone_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(self.clone_btn)

        self.branch_btn = ttk.Button(
            btn_frame, text="2. Create Feature Branch",
            command=self._create_branch)
        self.branch_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(self.branch_btn)

        # Row 5 — Result display (height=15 matches original)
        result_frame = ttk.LabelFrame(self, text="Repository Status", padding="10")
        result_frame.grid(row=5, column=0, columnspan=2,
                          sticky="nesw", pady=5)

        self.repo_result_text = scrolledtext.ScrolledText(
            result_frame, height=15, width=70, wrap=tk.WORD)
        self.repo_result_text.pack(fill=tk.BOTH, expand=True)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(5, weight=1)

    # ── Controller interaction ─────────────────────────────────────────────

    def _fetch_repos(self):
        """Fetch repository list from Bitbucket."""
        # Guard: repo_controller is only available after set_view()
        repo_ctrl = getattr(self.context.controller, 'repo_controller', None)
        if repo_ctrl is None:
            return
        self.log("Fetching repositories...")
        repo_ctrl.fetch_repos(callback=self._on_repos_fetched)

    def _on_repos_fetched(self, repos):
        if repos:
            self.repo_combo['values'] = repos
            self.log(f"✓ Loaded {len(repos)} repositories")
        else:
            self.log("✗ No repositories found")

    def _filter_repos(self, event):
        query = self.context.get_var('repo_var').get()
        repo_ctrl = getattr(self.context.controller, 'repo_controller', None)
        if repo_ctrl is None:
            return
        filtered = repo_ctrl.filter_repos(query)
        self.repo_combo['values'] = filtered

    def _clone_repo(self):
        repo = self.context.get_var('repo_var').get().strip()
        branch = self.context.get_var('branch_var').get().strip()
        issue_key = self.context.get_var('issue_var').get().strip().upper()

        if not all([repo, branch, issue_key]) or issue_key.endswith("-"):
            self.show_error("Error", "All fields required (Repository, Base Branch, Issue Key)")
            return

        self.lock_gui()

        self.context.controller.repo_controller.clone_repo(
            repo, branch, issue_key,
            callback=self._on_clone_completed)

    def _on_clone_completed(self, result):
        self.unlock_gui()

        if result.get('success'):
            self.repo_result_text.delete('1.0', tk.END)
            self.repo_result_text.insert('1.0', result.get('result', ''))
            self.log("✓ Repository cloned successfully")
        elif result.get('warning'):
            messagebox.showwarning("Repository Already Exists", result.get('warning'))
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to clone repository:\n{error_msg}")
            self.log(f"✗ Failed to clone repository: {error_msg}")

    def _create_branch(self):
        repo = self.context.get_var('repo_var').get().strip()
        branch = self.context.get_var('branch_var').get().strip()
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        feature_branch = self.context.get_var('feature_branch_var').get().strip()

        if not all([repo, branch, issue_key]) or issue_key.endswith("-"):
            self.show_error("Error", "All fields required (Repository, Base Branch, Issue Key)")
            return

        self.lock_gui()

        self.context.controller.repo_controller.create_feature_branch(
            repo, branch, issue_key, feature_branch,
            callback=self._on_branch_completed)

    def _on_branch_completed(self, result):
        self.unlock_gui()

        if result.get('success'):
            self.repo_result_text.delete('1.0', tk.END)
            self.repo_result_text.insert('1.0', result.get('result', ''))
            feature_branch = result.get('feature_branch', '')
            if feature_branch and self.context.get_var('feature_branch_var'):
                self.context.get_var('feature_branch_var').set(feature_branch)
            self.log("✓ Feature branch created successfully")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to create feature branch:\n{error_msg}")
            self.log(f"✗ Failed to create feature branch: {error_msg}")
