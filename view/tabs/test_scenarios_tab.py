#!/usr/bin/env python3
"""
view/tabs/test_scenarios_tab.py
================================
Test Scenarios Tab (View) - Phase 2

Tab for generating test scenarios with AI.
Extracted from gui/app.py lines 667-696.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from view.tabs.base_tab import BaseTab


class TestScenariosTab(BaseTab):
    """
    Test Scenarios Tab - generates test scenarios with AI.
    
    Layout:
      1. Issue key input
      2. Generate button
      3. Result display (scrolled text)
    """
    
    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Test Scenarios")
        self._build_ui()
    
    def _build_ui(self):
        """Build the UI for test scenarios tab."""
        self.columnconfigure(0, weight=1)
        
        # Title
        ttk.Label(self, text="Generate Test Scenarios", font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)
        
        # Issue input
        ttk.Label(self, text="JIRA Issue Key:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.context.set_var('test_issue_key', tk.StringVar(value="TSESSD-"))
        ttk.Entry(self, textvariable=self.context.get_var('test_issue_key'), width=40).grid(
            row=1, column=1, pady=5)
        
        # Generate button
        self.generate_btn = ttk.Button(self, text="Generate Test Scenarios", command=self._generate_tests)
        self.generate_btn.grid(row=2, column=0, columnspan=2, pady=10)
        
        # Result display
        result_frame = ttk.LabelFrame(self, text="Test Scenarios", padding="10")
        result_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, height=20, width=70, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
    
    def _generate_tests(self):
        """Generate test scenarios via controller."""
        issue_key = self.context.get_var('test_issue_key').get().strip().upper()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Error", "Please enter a valid JIRA issue key")
            return
        
        # Disable button during generation
        self.generate_btn.configure(state=tk.DISABLED)
        
        # Call controller
        self.context.controller.test_controller.generate_tests(
            issue_key,
            callback=self._on_generate_completed
        )
    
    def _on_generate_completed(self, result):
        """Callback when test generation completes."""
        # Re-enable button
        self.generate_btn.configure(state=tk.NORMAL)
        
        if result.get('success'):
            # Display result
            test_scenarios = result.get('test_scenarios', '')
            self.result_text.delete('1.0', tk.END)
            self.result_text.insert('1.0', test_scenarios)
            
            if result.get('from_cache'):
                self.show_info("Success", "Test scenarios loaded from workflow file!")
            else:
                self.log("✓ Test scenarios generated - interactive chat opened")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to generate test scenarios:\n{error_msg}")
            self.log(f"✗ Failed to generate test scenarios: {error_msg}")
