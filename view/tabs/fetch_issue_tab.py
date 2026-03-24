#!/usr/bin/env python3
"""
view/tabs/fetch_issue_tab.py
=============================
Fetch Issue Tab (View) — matches gui/app.py create_fetch_issue_tab()
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class FetchIssueTab(BaseTab):
    """
    Fetch Issue Tab — fetches JIRA issue data without AI analysis.

    Layout matches gui/app.py create_fetch_issue_tab():
      Row 0: Bold title
      Row 1: JIRA Issue Key label + entry
      Row 2: Fetch button (centred, colspan 2)
      Row 3: LabelFrame "Issue Details" with ScrolledText
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📋 Fetch Issue")
        self._build_ui()

    def _build_ui(self):
        # padding="10" matches the original Frame(self.notebook, padding="10")
        self.configure(padding="10")

        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')

        # Row 0 — title
        ttk.Label(self, text="Fetch JIRA Issue",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)

        # Row 1 — issue key
        ttk.Label(self, text="JIRA Issue Key:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('issue_var'),
                  width=40).grid(row=1, column=1, pady=5)

        # Row 2 — fetch button
        self.fetch_btn = ttk.Button(self, text="Fetch Issue",
                                    command=self._fetch_issue)
        self.fetch_btn.grid(row=2, column=0, columnspan=2, pady=10)
        self.context.lockable_buttons.append(self.fetch_btn)

        # Row 3 — result display
        result_frame = ttk.LabelFrame(self, text="Issue Details", padding="10")
        result_frame.grid(row=3, column=0, columnspan=2,
                          sticky="nesw", pady=5)

        self.issue_result_text = scrolledtext.ScrolledText(
            result_frame, height=20, width=70, wrap=tk.WORD)
        self.issue_result_text.pack(fill=tk.BOTH, expand=True)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    # ── User actions ──────────────────────────────────────────────────────

    def _fetch_issue(self):
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Error", "Please enter a valid JIRA issue key")
            return

        self.lock_gui()
        self.context.controller.jira_controller.fetch_issue(
            issue_key, callback=self._on_fetch_completed)

    def _on_fetch_completed(self, result):
        self.unlock_gui()
        if result.get('success'):
            self.issue_result_text.delete('1.0', tk.END)
            self.issue_result_text.insert('1.0', result.get('result', ''))
            self.log("✓ Issue fetched successfully")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to fetch issue:\n{error_msg}")
            self.log(f"✗ Failed to fetch issue: {error_msg}")
