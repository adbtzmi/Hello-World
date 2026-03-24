#!/usr/bin/env python3
"""
view/tabs/test_scenarios_tab.py
================================
Test Scenarios Tab (View) — matches gui/app.py create_test_tab()
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class TestScenariosTab(BaseTab):
    """
    Test Scenarios Tab — generates test scenarios with AI.

    Layout matches gui/app.py create_test_tab() exactly:
      Row 0: Bold title (colspan 3)
      Row 1: "JIRA Issue Key:" | entry (w+e) | "(Auto-populated from Home tab)" hint
      Row 2: Info label (colspan 3)
      Row 3: Generate button (colspan 3)
      Row 4: LabelFrame "Test Scenarios" with ScrolledText
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🧪 Test Scenarios")
        self._build_ui()

    def _build_ui(self):
        self.configure(padding="10")

        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')

        # Row 0 — title
        ttk.Label(self, text="Generate Test Scenarios",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=3, pady=10)

        # Row 1 — Issue Key (3-column layout matching original)
        ttk.Label(self, text="JIRA Issue Key:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('issue_var'),
                  width=50).grid(row=1, column=1, pady=5, sticky=(tk.W, tk.E))
        ttk.Label(self, text="(Auto-populated from Home tab)",
                  font=('Arial', 8), foreground='gray').grid(
            row=1, column=2, sticky=tk.W, padx=5)

        # Row 2 — info label (matches original)
        ttk.Label(self,
                  text="Test scenarios will be generated from workflow data",
                  font=('Arial', 9), foreground='gray').grid(
            row=2, column=0, columnspan=3, pady=5)

        # Row 3 — generate button
        self.generate_btn = ttk.Button(self, text="Generate Test Scenarios",
                                       command=self._generate_tests)
        self.generate_btn.grid(row=3, column=0, columnspan=3, pady=10)
        self.context.lockable_buttons.append(self.generate_btn)

        # Row 4 — result display
        result_frame = ttk.LabelFrame(self, text="Test Scenarios", padding="10")
        result_frame.grid(row=4, column=0, columnspan=3,
                          sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)

        self.test_result_text = scrolledtext.ScrolledText(
            result_frame, height=18, width=70, wrap=tk.WORD)
        self.test_result_text.pack(fill=tk.BOTH, expand=True)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)

    # ── User actions ──────────────────────────────────────────────────────

    def _generate_tests(self):
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Error", "Please enter JIRA issue key")
            return

        # ── Workflow Caching ──
        ctrl = getattr(self.context.controller, 'test_controller', None)
        if ctrl and hasattr(ctrl, 'get_workflow_step'):
            existing = ctrl.get_workflow_step(issue_key, "TEST_SCENARIOS")
            if existing:
                self.log(f"✓ Found existing test scenarios in workflow file")
                self.test_result_text.delete('1.0', tk.END)
                self.test_result_text.insert('1.0', existing)
                
                # Open chat with existing scenarios
                if self.context.controller.chat_controller:
                    self.context.controller.chat_controller.open_interactive_chat(
                        issue_key=issue_key,
                        step_name="Test Scenarios",
                        initial_content=existing,
                        finalize_callback=lambda: self._on_chat_finalized(issue_key, existing)
                    )
                return

        self.lock_gui()
        self.context.controller.test_controller.generate_tests(
            issue_key, callback=self._on_generate_completed)

    def _on_chat_finalized(self, issue_key, content):
        """Called when user approves test scenarios in chat."""
        self.test_result_text.delete('1.0', tk.END)
        self.test_result_text.insert('1.0', content)
        if self.context.controller.test_controller:
            self.context.controller.test_controller.save_workflow_step(issue_key, "TEST_SCENARIOS", content)
        self.log(f"✓ Test scenarios finalized for {issue_key}")

    def _on_generate_completed(self, result):
        self.unlock_gui()
        if result.get('success'):
            test_scenarios = result.get('test_scenarios', '')
            issue_key = self.context.get_var('issue_var').get().strip().upper()

            # Open chat for initial refinement
            if self.context.controller.chat_controller:
                self.log("✓ Initial test scenarios generated - opening interactive chat...")
                self.context.controller.chat_controller.open_interactive_chat(
                    issue_key=issue_key,
                    step_name="Test Scenarios",
                    initial_content=test_scenarios,
                    finalize_callback=lambda: self._on_chat_finalized(issue_key, test_scenarios)
                )
            else:
                self.test_result_text.delete('1.0', tk.END)
                self.test_result_text.insert('1.0', test_scenarios)
                self.log("✓ Test scenarios generated")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to generate test scenarios:\n{error_msg}")
            self.log(f"✗ Failed to generate test scenarios: {error_msg}")
