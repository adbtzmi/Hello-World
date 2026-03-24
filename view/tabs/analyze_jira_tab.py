#!/usr/bin/env python3
"""
view/tabs/analyze_jira_tab.py
==============================
Analyze JIRA Tab (View) - Phase 2

Tab for analyzing JIRA with AI.
Extracted from gui/app.py lines 574-598.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from view.tabs.base_tab import BaseTab


class AnalyzeJiraTab(BaseTab):
    """
    Analyze JIRA Tab - analyzes JIRA issue with AI.
    
    Layout:
      1. Issue key input
      2. Analyze button
      3. Result display (scrolled text)
    """
    
    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🤖 Analyze JIRA")
        self._build_ui()
    
    def _build_ui(self):
        """Build the UI for analyze JIRA tab."""
        self.columnconfigure(0, weight=1)
        
        # Title
        ttk.Label(self, text="Analyze JIRA Request with AI", font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)
        
        # Issue input
        ttk.Label(self, text="JIRA Issue Key:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.context.set_var('analyze_issue_key', tk.StringVar(value="TSESSD-"))
        ttk.Entry(self, textvariable=self.context.get_var('analyze_issue_key'), width=40).grid(
            row=1, column=1, pady=5)
        
        # Analyze button
        self.analyze_btn = ttk.Button(self, text="Analyze with AI", command=self._analyze_issue)
        self.analyze_btn.grid(row=2, column=0, columnspan=2, pady=10)
        
        # Result display
        result_frame = ttk.LabelFrame(self, text="AI Analysis Result", padding="10")
        result_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, height=20, width=70, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
    
    def _analyze_issue(self):
        """Analyze JIRA issue via controller."""
        issue_key = self.context.get_var('analyze_issue_key').get().strip().upper()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Error", "Please enter a valid JIRA issue key")
            return
        
        # Disable button during analysis
        self.analyze_btn.configure(state=tk.DISABLED)
        
        # Call controller
        self.context.controller.jira_controller.analyze_issue(
            issue_key,
            callback=self._on_analyze_completed
        )
    
    def _on_analyze_completed(self, result):
        """Callback when analysis completes."""
        # Re-enable button
        self.analyze_btn.configure(state=tk.NORMAL)
        
        if result.get('success'):
            # Display result (will be updated when user approves in chat)
            analysis_text = result.get('analysis', '')
            self.result_text.delete('1.0', tk.END)
            self.result_text.insert('1.0', analysis_text)
            self.log("✓ Analysis complete - interactive chat opened")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to analyze issue:\n{error_msg}")
            self.log(f"✗ Failed to analyze issue: {error_msg}")
