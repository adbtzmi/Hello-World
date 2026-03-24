#!/usr/bin/env python3
"""
view/tabs/fetch_issue_tab.py
=============================
Fetch Issue Tab (View) - Phase 2

Tab for fetching JIRA issue only.
Extracted from gui/app.py lines 549-573.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from view.tabs.base_tab import BaseTab


class FetchIssueTab(BaseTab):
    """
    Fetch Issue Tab - fetches JIRA issue data without AI analysis.
    
    Layout:
      1. Issue key input
      2. Fetch button
      3. Result display (scrolled text)
    """
    
    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📋 Fetch Issue")
        self._build_ui()
    
    def _build_ui(self):
        """Build the UI for fetch issue tab."""
        self.columnconfigure(0, weight=1)
        
        # Title
        ttk.Label(self, text="Fetch JIRA Issue", font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)
        
        # Issue input
        ttk.Label(self, text="JIRA Issue Key:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.context.set_var('fetch_issue_key', tk.StringVar(value="TSESSD-"))
        ttk.Entry(self, textvariable=self.context.get_var('fetch_issue_key'), width=40).grid(
            row=1, column=1, pady=5)
        
        # Fetch button
        self.fetch_btn = ttk.Button(self, text="Fetch Issue", command=self._fetch_issue)
        self.fetch_btn.grid(row=2, column=0, columnspan=2, pady=10)
        
        # Result display
        result_frame = ttk.LabelFrame(self, text="Issue Details", padding="10")
        result_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, height=20, width=70, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
    
    def _fetch_issue(self):
        """Fetch JIRA issue via controller."""
        issue_key = self.context.get_var('fetch_issue_key').get().strip().upper()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Error", "Please enter a valid JIRA issue key")
            return
        
        # Disable button during fetch
        self.fetch_btn.configure(state=tk.DISABLED)
        
        # Call controller
        self.context.controller.jira_controller.fetch_issue(
            issue_key,
            callback=self._on_fetch_completed
        )
    
    def _on_fetch_completed(self, result):
        """Callback when fetch completes."""
        # Re-enable button
        self.fetch_btn.configure(state=tk.NORMAL)
        
        if result.get('success'):
            # Display result
            self.result_text.delete('1.0', tk.END)
            self.result_text.insert('1.0', result.get('result', ''))
            self.log("✓ Issue fetched successfully")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to fetch issue:\n{error_msg}")
            self.log(f"✗ Failed to fetch issue: {error_msg}")
