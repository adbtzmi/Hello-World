#!/usr/bin/env python3
"""
view/tabs/analyze_jira_tab.py
==============================
Analyze JIRA Tab (View) — matches gui/app.py create_analyze_jira_tab()
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class AnalyzeJiraTab(BaseTab):
    """
    Analyze JIRA Tab — analyzes JIRA issue with AI.

    Layout matches gui/app.py create_analyze_jira_tab():
      Row 0: Bold title
      Row 1: JIRA Issue Key label + entry
      Row 2: Analyze button (centred, colspan 2)
      Row 3: LabelFrame "AI Analysis Result" with ScrolledText
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🤖 Analyze JIRA")
        self._build_ui()

    def _build_ui(self):
        self.configure(padding="10")

        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')

        # Row 0 — title
        ttk.Label(self, text="Analyze JIRA Request with AI",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)

        # Row 1 — issue key
        ttk.Label(self, text="JIRA Issue Key:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('issue_var'),
                  width=40).grid(row=1, column=1, pady=5)

        # Row 2 — analyze button
        self.analyze_btn = ttk.Button(self, text="Analyze with AI",
                                      command=self._analyze_issue)
        self.analyze_btn.grid(row=2, column=0, columnspan=2, pady=10)
        self.context.lockable_buttons.append(self.analyze_btn)

        # Row 3 — result display
        result_frame = ttk.LabelFrame(self, text="AI Analysis Result",
                                      padding="10")
        result_frame.grid(row=3, column=0, columnspan=2,
                          sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)

        self.analyze_result_text = scrolledtext.ScrolledText(
            result_frame, height=18, width=70, wrap=tk.WORD)
        self.analyze_result_text.pack(fill=tk.BOTH, expand=True)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    # ── User actions ──────────────────────────────────────────────────────

    def _analyze_issue(self):
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Error", "Please enter a valid JIRA issue key")
            return

        # ── Workflow Caching ──
        ctrl = getattr(self.context.controller, 'jira_controller', None)
        if ctrl and hasattr(ctrl, 'get_workflow_step'):
            existing = ctrl.get_workflow_step(issue_key, "JIRA_ANALYSIS")
            if existing:
                self.log(f"✓ Found existing JIRA analysis in workflow file")
                self.analyze_result_text.delete('1.0', tk.END)
                self.analyze_result_text.insert('1.0', existing)
                
                # Open chat with existing analysis
                if self.context.controller.chat_controller:
                    self.context.controller.chat_controller.open_interactive_chat(
                        issue_key=issue_key,
                        step_name="JIRA Analysis",
                        initial_content=existing,
                        finalize_callback=lambda: self._on_chat_finalized(issue_key, existing)
                    )
                return

        self.lock_gui()
        self.context.controller.jira_controller.analyze_issue(
            issue_key, callback=self._on_analyze_completed)

    def _on_chat_finalized(self, issue_key, content):
        """Called when user approves analysis in chat."""
        # Update text area
        self.analyze_result_text.delete('1.0', tk.END)
        self.analyze_result_text.insert('1.0', content)
        # Controller handles saving to workflow file usually, or we can do it here
        if self.context.controller.jira_controller:
            self.context.controller.jira_controller.save_workflow_step(issue_key, "JIRA_ANALYSIS", content)
        self.log(f"✓ JIRA analysis finalized for {issue_key}")

    def _on_analyze_completed(self, result):
        self.unlock_gui()
        if result.get('success'):
            analysis_text = result.get('analysis', '')
            issue_key = self.context.get_var('issue_var').get().strip().upper()

            # Open chat for initial refinement
            if self.context.controller.chat_controller:
                self.log("✓ Initial analysis complete - opening interactive chat...")
                self.context.controller.chat_controller.open_interactive_chat(
                    issue_key=issue_key,
                    step_name="JIRA Analysis",
                    initial_content=analysis_text,
                    finalize_callback=lambda: self._on_chat_finalized(issue_key, analysis_text)
                )
            else:
                self.analyze_result_text.delete('1.0', tk.END)
                self.analyze_result_text.insert('1.0', analysis_text)
                self.log("✓ Analysis complete")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to analyze issue:\n{error_msg}")
            self.log(f"✗ Failed to analyze issue: {error_msg}")
