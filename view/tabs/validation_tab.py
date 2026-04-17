#!/usr/bin/env python3
"""
view/tabs/validation_tab.py
============================
Validation & Risk Tab (View) — generates validation document and risk
assessment by consolidating checkout trace files + JIRA workflow data.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class ValidationTab(BaseTab):
    """
    Validation & Risk Tab — one-click generation of:
      • Checkout trace analysis (from CHECKOUT_RESULTS)
      • Validation document (populated from trace data)
      • Spool summary
      • Rule-based + AI risk assessment
      • JIRA-based risk assessment (from workflow)

    Layout:
      Row 0: Bold title (colspan 3)
      Row 1: "JIRA Issue Key:" | entry (w+e) | hint
      Row 2: Info label (colspan 3)
      Row 3: Generate button (colspan 3)
      Row 4: LabelFrame with ScrolledText for results
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📋 Validation & Risk")
        self._build_ui()

    def _build_ui(self):
        self.configure(padding="10")

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

        # Row 2 — info label
        ttk.Label(self,
                  text=("Consolidates checkout trace files and generates "
                        "validation document with risk assessment"),
                  font=('Arial', 9), foreground='gray').grid(
            row=2, column=0, columnspan=3, pady=5)

        # Row 3 — generate button
        self.generate_btn = ttk.Button(
            self, text="Generate Validation & Risk Assessment",
            command=self._generate_validation)
        self.generate_btn.grid(row=3, column=0, columnspan=3, pady=10)
        self.context.lockable_buttons.append(self.generate_btn)

        # Row 4 — result display
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
        jira_project = self.context.config.get(
            'jira', {}).get('project_key', 'TSESSD')
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Please enter JIRA issue key")
            return

        self.lock_gui()
        self.log(f"\n[Generating Validation Document for {issue_key}]")

        if not self.context.controller.validation_controller:
            self.show_error("Error",
                            "Validation controller not initialized")
            self.unlock_gui()
            return

        self.context.controller.validation_controller.assess_risks(
            issue_key, self._on_validation_generated)

    def _on_validation_generated(self, result):
        self.unlock_gui()

        if not result.get('success'):
            error = result.get('error', 'Unknown error')
            self.show_error("Generation Failed", error)
            self.log(f"✗ Validation generation failed: {error}")
            return

        issue_key = self.context.get_var('issue_var').get().strip().upper()

        # Build unified display from consolidation + JIRA results
        display_text = self._format_unified_results(result, issue_key)

        # Show in text area
        self.risk_result_text.delete('1.0', tk.END)
        self.risk_result_text.insert('1.0', display_text)

        # Open interactive chat if available (for JIRA assessment)
        jira_result = result.get('jira_assessment', {})
        assessment = result.get('assessment', '')
        if assessment and self.context.controller.chat_controller:
            self.log("✓ Results generated — opening interactive chat...")
            self.context.controller.chat_controller.open_interactive_chat(
                issue_key=issue_key,
                step_name="Validation & Risk Assessment",
                initial_content=assessment,
                finalize_callback=lambda: self._finalize_assessment(
                    issue_key, assessment)
            )
        else:
            self.log("✓ Validation & Risk Assessment complete")

    def _format_unified_results(self, result, issue_key):
        """Format the unified consolidation + JIRA results for display."""
        lines = []
        lines.append("=" * 64)
        lines.append("  VALIDATION DOCUMENT & RISK ASSESSMENT")
        lines.append(f"  JIRA: {issue_key}")
        lines.append("=" * 64)
        lines.append("")

        # ── Section 1: Checkout Trace Consolidation ────────────────────
        consolidation = result.get('consolidation')
        if consolidation and consolidation.get('success'):
            lines.append("-" * 64)
            lines.append("  CHECKOUT TRACE ANALYSIS")
            lines.append("-" * 64)

            # Trace analysis summary
            ta = consolidation.get('trace_analysis', {})
            if ta:
                lines.append("")
                lines.append(f"  Total Tests  : {ta.get('total_tests', 0)}")
                lines.append(f"  Passed       : {ta.get('passed_tests', 0)}")
                lines.append(f"  Failed       : {ta.get('failed_tests', 0)}")
                pr = ta.get('pass_rate', 0)
                lines.append(f"  Pass Rate    : {pr:.2f}%")
                dh = ta.get('duration_hours', 0)
                lines.append(f"  Duration     : {dh:.2f} hours")

            # Rule-based risk assessment
            ra = consolidation.get('risk_assessment', {})
            if ra:
                lines.append("")
                lines.append("  >> Rule-Based Risk Assessment")
                lines.append(
                    f"     Risk Level : {ra.get('risk_level', 'N/A')}")
                rs = ra.get('risk_score', 0)
                lines.append(f"     Risk Score : {rs:.1f} / 100")

            # AI validation
            ai_val = consolidation.get('ai_validation', {})
            if ai_val:
                lines.append("")
                lines.append("  >> AI Checkout Validation")
                lines.append(
                    f"     Status     : "
                    f"{ai_val.get('validation_status', 'N/A')}")
                lines.append(
                    f"     Confidence : "
                    f"{ai_val.get('confidence', 'N/A')}")
                lines.append(
                    f"     Method     : "
                    f"{ai_val.get('method', 'N/A')}")

            # AI risk assessment
            ai_risk = consolidation.get('ai_risk_assessment', {})
            if ai_risk:
                lines.append("")
                lines.append("  >> AI Risk Assessment")
                lines.append(
                    f"     Risk Level : "
                    f"{ai_risk.get('enhanced_risk_level', 'N/A')}")
                score = ai_risk.get('enhanced_risk_score', 0)
                try:
                    lines.append(
                        f"     Risk Score : {float(score):.1f} / 100")
                except (ValueError, TypeError):
                    lines.append(f"     Risk Score : {score}")
                lines.append(
                    f"     Method     : "
                    f"{ai_risk.get('method', 'N/A')}")

            # Generated files
            outputs = consolidation.get('outputs', {})
            if outputs:
                lines.append("")
                lines.append("  >> Generated Files")
                for key, path in outputs.items():
                    label = key.replace("_", " ").title()
                    lines.append(f"     {label}: {path}")

            # Errors (non-fatal)
            errors = consolidation.get('errors', [])
            if errors:
                lines.append("")
                lines.append("  >> Warnings")
                for err in errors:
                    lines.append(f"     ⚠ {err}")

            lines.append("")
        elif consolidation:
            lines.append("-" * 64)
            lines.append("  CHECKOUT TRACE ANALYSIS")
            lines.append("-" * 64)
            errors = consolidation.get('errors', [])
            if errors:
                for err in errors:
                    lines.append(f"  ⚠ {err}")
            else:
                lines.append("  ⚠ Consolidation failed")
            lines.append("")
        else:
            lines.append("-" * 64)
            lines.append("  CHECKOUT TRACE ANALYSIS")
            lines.append("-" * 64)
            lines.append("  ℹ No checkout trace files found in "
                         "CHECKOUT_RESULTS")
            lines.append("  (Run checkout first, then generate)")
            lines.append("")

        # ── Section 2: JIRA-Based Risk Assessment ──────────────────────
        jira_result = result.get('jira_assessment', {})
        assessment = result.get('assessment', '')
        if assessment:
            lines.append("-" * 64)
            lines.append("  JIRA-BASED RISK ASSESSMENT")
            lines.append("-" * 64)
            lines.append("")
            lines.append(assessment)
            lines.append("")
        elif jira_result and not jira_result.get('success'):
            lines.append("-" * 64)
            lines.append("  JIRA-BASED RISK ASSESSMENT")
            lines.append("-" * 64)
            lines.append(
                f"  ℹ {jira_result.get('error', 'Not available')}")
            lines.append("")

        lines.append("=" * 64)

        return "\n".join(lines)

    def _finalize_assessment(self, issue_key, risk_assessment_text):
        if self.context.controller.validation_controller:
            self.context.controller.validation_controller.finalize_assessment(
                issue_key, risk_assessment_text)
        self.log("✓ Risk assessment finalized and saved to workflow")

    # ── View callbacks ────────────────────────────────────────────────────

    def on_ai_checkout_results(self, ai_consolidation: list, summary: dict):
        """
        Called by ResultController when auto-consolidation completes with
        AI validation and/or risk assessment results.

        Args:
            ai_consolidation: List of dicts, one per MID, each containing
                              'mid', 'ai_validation', 'ai_risk_assessment',
                              'ai_report_paths'.
            summary: Full summary dict from ResultCollector.
        """
        machine = summary.get("machine", "")
        site = summary.get("site", "")
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        total = summary.get("total", 0)

        lines = []
        lines.append("=" * 64)
        lines.append("  AI CHECKOUT VALIDATION & RISK ASSESSMENT")
        lines.append("=" * 64)
        lines.append(f"  Machine : {machine}")
        lines.append(f"  Site    : {site}")
        lines.append(
            f"  Results : {passed} passed, {failed} failed / {total} total")
        lines.append("")

        for entry in ai_consolidation:
            mid = entry.get("mid", "?")
            lines.append("-" * 64)
            lines.append(f"  MID: {mid}")
            lines.append("-" * 64)

            # AI Validation
            val = entry.get("ai_validation")
            if val:
                status = val.get("validation_status", "N/A")
                confidence = val.get("confidence", "N/A")
                method = val.get("method", "N/A")
                lines.append("")
                lines.append("  >> AI Checkout Validation")
                lines.append(f"     Status     : {status}")
                lines.append(f"     Confidence : {confidence}")
                lines.append(f"     Method     : {method}")

            # AI Risk Assessment
            risk = entry.get("ai_risk_assessment")
            if risk:
                level = risk.get("enhanced_risk_level", "N/A")
                score = risk.get("enhanced_risk_score", 0)
                method = risk.get("method", "N/A")
                lines.append("")
                lines.append("  >> AI Risk Assessment")
                lines.append(f"     Risk Level : {level}")
                try:
                    lines.append(
                        f"     Risk Score : {float(score):.1f} / 100")
                except (ValueError, TypeError):
                    lines.append(f"     Risk Score : {score}")
                lines.append(f"     Method     : {method}")

            # Report file paths
            paths = entry.get("ai_report_paths", {})
            if paths:
                lines.append("")
                lines.append("  >> Report Files")
                for key, path in paths.items():
                    label = key.replace("ai_", "").replace(
                        "_", " ").title()
                    lines.append(f"     {label}: {path}")

            lines.append("")

        lines.append("=" * 64)
        lines.append("  Full reports saved alongside trace files "
                     "in collection folder.")
        lines.append("=" * 64)

        display_text = "\n".join(lines)

        # Update the text area
        self.risk_result_text.delete('1.0', 'end')
        self.risk_result_text.insert('1.0', display_text)

        self.log("AI checkout validation & risk assessment results "
                 "displayed in Validation & Risk tab")

    def on_validation_completed(self, result: dict):
        """Called by controller when validation completes"""
        issue_key = result.get("issue_key", "")
        self.log(f"✓ Validation complete: {issue_key}")
