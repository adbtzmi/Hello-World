#!/usr/bin/env python3
"""
view/tabs/validation_tab.py
============================
Validation & Risk Tab (View) — matches gui/app.py create_risk_tab()
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class ValidationTab(BaseTab):
    """
    Validation & Risk Tab — generates validation document and risk assessment.

    Layout matches gui/app.py create_risk_tab() exactly:
      Row 0: Bold title (colspan 3)
      Row 1: "JIRA Issue Key:" | entry (w+e) | "(Auto-populated from Home tab)" hint
      Row 2: Info label (colspan 3)
      Row 3: Generate button (colspan 3)
      Row 4: LabelFrame "Validation Document & Risk Assessment" with ScrolledText (height=22)
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📋 Validation & Risk")
        self._build_ui()

    def _build_ui(self):
        self.configure(padding="10")

        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')

        # Row 0 — title
        ttk.Label(self,
                  text="Generate Validation Document & Risk Assessment",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=3, pady=10)

        # Row 1 — issue key (3-column, matches original)
        ttk.Label(self, text="JIRA Issue Key:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self,
                  textvariable=self.context.get_var('issue_var'),
                  width=50).grid(row=1, column=1, pady=5, sticky="we")
        ttk.Label(self, text="(Auto-populated from Home tab)",
                  font=('Arial', 8), foreground='gray').grid(
            row=1, column=2, sticky=tk.W, padx=5)

        # Row 2 — info label (matches original)
        ttk.Label(self,
                  text="Risk assessment will be generated from workflow data",
                  font=('Arial', 9), foreground='gray').grid(
            row=2, column=0, columnspan=3, pady=5)

        # Row 3 — generate button
        self.generate_btn = ttk.Button(
            self, text="Generate Validation & Risk Assessment",
            command=self._generate_validation)
        self.generate_btn.grid(row=3, column=0, columnspan=3, pady=10)
        self.context.lockable_buttons.append(self.generate_btn)

        # Row 4 — result display (height=22 matches original)
        result_frame = ttk.LabelFrame(
            self, text="Validation Document & Risk Assessment", padding="10")
        result_frame.grid(row=4, column=0, columnspan=3,
                          sticky="nesw", pady=5)
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        self.risk_result_text = scrolledtext.ScrolledText(
            result_frame, height=18, width=70, wrap=tk.WORD)
        self.risk_result_text.pack(fill=tk.BOTH, expand=True)

        self.columnconfigure(1, weight=1)

    # ── User actions ──────────────────────────────────────────────────────

    def _generate_validation(self):
        issue_key = self.context.get_var('issue_var').get().strip().upper()
        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Please enter JIRA issue key")
            return

        self.lock_gui()
        self.log(f"\n[Generating Validation Document for {issue_key}]")

        if not self.context.controller.validation_controller:
            self.show_error("Error", "Validation controller not initialized")
            self.unlock_gui()
            return

        self.context.controller.validation_controller.generate_validation(
            issue_key, self._on_validation_generated)

    def _on_validation_generated(self, result):
        self.unlock_gui()

        if not result.get('success'):
            error = result.get('error', 'Unknown error')
            self.show_error("Generation Failed", error)
            self.log(f"✗ Validation generation failed: {error}")
            return

        risk_assessment = result.get('risk_assessment', '')
        template_file = result.get('template_file', '')
        issue_key = self.context.get_var('issue_var').get().strip().upper()

        self.log(f"✓ Risk assessment generated - opening interactive chat...")

        if self.context.controller.chat_controller:
            self.context.controller.chat_controller.open_interactive_chat(
                issue_key=issue_key,
                step_name="Validation & Risk Assessment",
                initial_content=risk_assessment,
                finalize_callback=lambda: self._finalize_assessment(issue_key, risk_assessment)
            )
        else:
            self._display_results(risk_assessment, template_file)

    def _finalize_assessment(self, issue_key, risk_assessment_text):
        if self.context.controller.validation_controller:
            self.context.controller.validation_controller.finalize_assessment(
                issue_key, risk_assessment_text)
        template_file = f"{issue_key}_validation.docx"
        self._display_results(risk_assessment_text, template_file)

    def _display_results(self, risk_assessment, template_file):
        display_text = "Validation document generated successfully!\n\n"
        if template_file:
            display_text += f"Output file: {template_file}\n\n"
            display_text += "Document populated with:\n"
            display_text += "- JIRA Analysis\n"
            display_text += "- Impact Analysis\n"
            display_text += "- Test Scenarios\n"
            display_text += "- Risk Assessment\n"
            display_text += "- Repository Information\n\n"

        display_text += "=" * 60 + "\n"
        display_text += "RISK ASSESSMENT\n"
        display_text += "=" * 60 + "\n\n"
        display_text += risk_assessment

        self.risk_result_text.delete('1.0', tk.END)
        self.risk_result_text.insert('1.0', display_text)

        if template_file:
            self.show_info("Success",
                           f"Validation document generated!\n\nSaved as: {template_file}")

        self.log("✓ Validation & Risk Assessment complete")

    # ── View callbacks ────────────────────────────────────────────────────

    def on_validation_completed(self, result: dict):
        """Called by controller when validation completes"""
        issue_key = result.get("issue_key", "")
        self.log(f"✓ Validation complete: {issue_key}")
