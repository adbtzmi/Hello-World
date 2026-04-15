# -*- coding: utf-8 -*-
"""
AI Checkout Validator
=====================
AI-powered framework to understand test cases and code changes,
then read traces/files to verify if the change and validation is successful.

This module bridges the gap between:
  - JIRA change request context (what was changed and why)
  - Trace file analysis (what actually happened during checkout)
  - Validation verification (did the checkout prove the change works?)

Uses the Model Gateway AI to provide intelligent analysis that goes
beyond rule-based pattern matching.
"""

import os
import sys
import json
import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime

# Handle imports for both standalone and module usage
try:
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer

logger = logging.getLogger("bento_app")


# ══════════════════════════════════════════════════════════════════════════════
# AI CHECKOUT VALIDATOR
# ══════════════════════════════════════════════════════════════════════════════

class AICheckoutValidator:
    """
    AI-powered checkout validation framework.

    Capabilities:
      1. Understands test case context from JIRA/workflow data
      2. Reads trace content and correlates with expected test scenarios
      3. Verifies if code changes are properly validated by checkout results
      4. Provides intelligent pass/fail analysis with reasoning
      5. Identifies gaps between expected and actual test coverage

    Requires an AIGatewayClient instance for AI inference.
    Falls back to rule-based analysis if AI is unavailable.
    """

    # System prompt for the AI validation mode
    SYSTEM_PROMPT = (
        "You are an expert SSD test validation engineer. Your role is to analyze "
        "checkout test results and determine if a code change has been properly "
        "validated.\n\n"
        "You understand:\n"
        "- SLATE test framework trace files (DispatcherDebug format)\n"
        "- SSD test flows (init, main, wrapup phases)\n"
        "- Test case pass/fail patterns and their significance\n"
        "- Performance metrics (tPROG, tBERS, tREAD, tRSNAP, tPBSY)\n"
        "- How code changes map to expected test behavior changes\n\n"
        "When analyzing:\n"
        "- Be specific about which tests validate which aspects of the change\n"
        "- Identify any gaps in test coverage relative to the change scope\n"
        "- Flag unexpected test behaviors that may indicate issues\n"
        "- Consider both direct and indirect impacts of the change\n"
        "- Provide confidence level (HIGH/MEDIUM/LOW) for your assessment\n"
        "- Use structured output with clear sections"
    )

    def __init__(self, ai_client=None):
        """
        Initialize the AI Checkout Validator.

        Args:
            ai_client: AIGatewayClient instance for AI inference.
                       If None, falls back to rule-based analysis.
        """
        self.ai_client = ai_client
        self.trace_analyzer = TraceAnalyzer()

    # ──────────────────────────────────────────────────────────────────────
    # MAIN VALIDATION ENTRY POINT
    # ──────────────────────────────────────────────────────────────────────

    def validate_checkout(
        self,
        trace_analysis: TraceAnalysis,
        jira_context: Optional[Dict] = None,
        test_scenarios: Optional[str] = None,
        code_changes: Optional[str] = None,
        impact_analysis: Optional[str] = None,
        trace_file_path: Optional[str] = None,
        trace_content_sample: Optional[str] = None,
        log_callback: Optional[Callable] = None,
    ) -> Dict:
        """
        Perform AI-powered checkout validation.

        Analyzes trace results against the JIRA change context to determine
        if the checkout properly validates the code change.

        Args:
            trace_analysis: TraceAnalysis object from trace file parsing
            jira_context: Dict with JIRA analysis data (description, change objective, etc.)
            test_scenarios: Expected test scenarios text from workflow
            code_changes: Description of code changes made
            impact_analysis: Impact analysis text from workflow
            trace_file_path: Path to trace file (for reading raw content)
            trace_content_sample: Pre-read sample of trace content (first/last N lines)
            log_callback: Optional callback for progress logging

        Returns:
            Dict with validation results:
              - validation_status: "VALIDATED" | "PARTIALLY_VALIDATED" | "NOT_VALIDATED" | "INCONCLUSIVE"
              - confidence: "HIGH" | "MEDIUM" | "LOW"
              - summary: Human-readable summary
              - test_coverage_analysis: Analysis of test coverage vs change scope
              - findings: List of specific findings
              - recommendations: List of recommendations
              - ai_analysis: Full AI response text (if AI was used)
              - method: "ai" or "rule_based"
        """
        self._log(log_callback, "🤖 Starting AI checkout validation...")

        # Read trace content sample if path provided but no sample given
        if trace_file_path and not trace_content_sample:
            trace_content_sample = self._read_trace_sample(trace_file_path)

        # Try AI-powered validation first
        if self.ai_client:
            try:
                result = self._validate_with_ai(
                    trace_analysis=trace_analysis,
                    jira_context=jira_context,
                    test_scenarios=test_scenarios,
                    code_changes=code_changes,
                    impact_analysis=impact_analysis,
                    trace_content_sample=trace_content_sample,
                    log_callback=log_callback,
                )
                if result.get("success"):
                    self._log(log_callback, "  ✓ AI validation complete")
                    return result
                else:
                    self._log(log_callback,
                              f"  ⚠ AI validation failed: {result.get('error', 'unknown')}, "
                              "falling back to rule-based")
            except Exception as e:
                self._log(log_callback,
                          f"  ⚠ AI validation error: {e}, falling back to rule-based")
                logger.warning(f"AI checkout validation failed: {e}")

        # Fallback to rule-based validation
        self._log(log_callback, "  Using rule-based validation...")
        return self._validate_rule_based(
            trace_analysis=trace_analysis,
            jira_context=jira_context,
            test_scenarios=test_scenarios,
            code_changes=code_changes,
            log_callback=log_callback,
        )

    # ──────────────────────────────────────────────────────────────────────
    # AI-POWERED VALIDATION
    # ──────────────────────────────────────────────────────────────────────

    def _validate_with_ai(
        self,
        trace_analysis: TraceAnalysis,
        jira_context: Optional[Dict],
        test_scenarios: Optional[str],
        code_changes: Optional[str],
        impact_analysis: Optional[str],
        trace_content_sample: Optional[str],
        log_callback: Optional[Callable],
    ) -> Dict:
        """Perform AI-powered validation using Model Gateway."""
        # Build the analysis prompt
        prompt = self._build_validation_prompt(
            trace_analysis=trace_analysis,
            jira_context=jira_context,
            test_scenarios=test_scenarios,
            code_changes=code_changes,
            impact_analysis=impact_analysis,
            trace_content_sample=trace_content_sample,
        )

        self._log(log_callback, "  Sending to AI for analysis...")

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        # Use the AI client to get analysis
        response = self.ai_client.chat_completion(
            messages=messages,
            task_type="analysis",
        )

        if not response.get("success"):
            return {
                "success": False,
                "error": response.get("error", "AI request failed"),
            }

        # Extract AI response text
        ai_text = self._extract_response_text(response)
        if not ai_text:
            return {"success": False, "error": "Empty AI response"}

        # Parse the AI response into structured result
        result = self._parse_ai_validation_response(ai_text, trace_analysis)
        result["ai_analysis"] = ai_text
        result["method"] = "ai"
        result["model_used"] = response.get("model_used", "unknown")
        result["success"] = True
        return result

    def _build_validation_prompt(
        self,
        trace_analysis: TraceAnalysis,
        jira_context: Optional[Dict],
        test_scenarios: Optional[str],
        code_changes: Optional[str],
        impact_analysis: Optional[str],
        trace_content_sample: Optional[str],
    ) -> str:
        """Build the validation analysis prompt for the AI."""
        sections = []

        sections.append(
            "Analyze the following checkout test results and determine if the "
            "code change has been properly validated.\n"
        )

        # ── Trace Analysis Summary ────────────────────────────────────────
        sections.append("## TRACE ANALYSIS RESULTS")
        sections.append(f"- Total Tests: {trace_analysis.total_tests}")
        sections.append(f"- Passed: {trace_analysis.passed_tests}")
        sections.append(f"- Failed: {trace_analysis.failed_tests}")
        sections.append(f"- Pass Rate: {trace_analysis.pass_rate:.2f}%")
        if trace_analysis.duration_seconds:
            hours = trace_analysis.duration_seconds / 3600
            sections.append(f"- Duration: {hours:.2f} hours ({trace_analysis.duration_seconds:.0f}s)")
        if trace_analysis.start_time:
            sections.append(f"- Start: {trace_analysis.start_time}")
        if trace_analysis.end_time:
            sections.append(f"- End: {trace_analysis.end_time}")

        # Flows executed
        if trace_analysis.flows_executed:
            sections.append(f"\n### Flows Executed ({len(trace_analysis.flows_executed)}):")
            for flow in trace_analysis.flows_executed:
                sections.append(f"  - {flow}")

        # Failed tests detail
        failed_tests = [t for t in trace_analysis.test_results if t.result == 'FAIL']
        if failed_tests:
            sections.append(f"\n### Failed Tests ({len(failed_tests)}):")
            for t in failed_tests[:20]:  # Limit to 20 for prompt size
                line = f"  - {t.test_name}"
                if t.flow_file:
                    line += f" (flow: {t.flow_file})"
                if t.error_message:
                    line += f" — {t.error_message}"
                sections.append(line)
            if len(failed_tests) > 20:
                sections.append(f"  ... and {len(failed_tests) - 20} more failed tests")

        # Performance metrics summary
        tests_with_metrics = [t for t in trace_analysis.test_results if t.performance_metrics]
        if tests_with_metrics:
            sections.append(f"\n### Performance Metrics (from {len(tests_with_metrics)} tests):")
            # Aggregate unique metric keys
            all_keys = set()
            for t in tests_with_metrics:
                all_keys.update(t.performance_metrics.keys())
            sections.append(f"  Metrics tracked: {', '.join(sorted(all_keys))}")

        # ── JIRA Context ──────────────────────────────────────────────────
        if jira_context:
            sections.append("\n## JIRA CHANGE CONTEXT")
            if isinstance(jira_context, dict):
                for key in ['change_objective', 'description', 'summary',
                             'acceptance_criteria', 'components', 'work_type']:
                    val = jira_context.get(key)
                    if val:
                        sections.append(f"- **{key.replace('_', ' ').title()}**: {val}")
            elif isinstance(jira_context, str):
                sections.append(jira_context[:3000])

        # ── Code Changes ──────────────────────────────────────────────────
        if code_changes:
            sections.append("\n## CODE CHANGES")
            sections.append(code_changes[:3000])

        # ── Impact Analysis ───────────────────────────────────────────────
        if impact_analysis:
            sections.append("\n## IMPACT ANALYSIS")
            sections.append(impact_analysis[:2000])

        # ── Expected Test Scenarios ───────────────────────────────────────
        if test_scenarios:
            sections.append("\n## EXPECTED TEST SCENARIOS")
            sections.append(test_scenarios[:3000])

        # ── Raw Trace Content Sample ──────────────────────────────────────
        if trace_content_sample:
            sections.append("\n## TRACE FILE CONTENT (sample)")
            sections.append("```")
            sections.append(trace_content_sample[:4000])
            sections.append("```")

        # ── Instructions ──────────────────────────────────────────────────
        sections.append("\n## ANALYSIS REQUIRED")
        sections.append(
            "Based on the above information, provide:\n\n"
            "### 1. VALIDATION STATUS\n"
            "One of: VALIDATED, PARTIALLY_VALIDATED, NOT_VALIDATED, INCONCLUSIVE\n\n"
            "### 2. CONFIDENCE LEVEL\n"
            "One of: HIGH, MEDIUM, LOW\n\n"
            "### 3. VALIDATION SUMMARY\n"
            "Brief summary of whether the checkout validates the code change.\n\n"
            "### 4. TEST COVERAGE ANALYSIS\n"
            "How well do the executed tests cover the scope of the change?\n"
            "- Which aspects of the change are covered by passing tests?\n"
            "- Which aspects are NOT covered or insufficiently tested?\n"
            "- Are there any unexpected test results?\n\n"
            "### 5. FINDINGS\n"
            "Specific observations (bullet points).\n\n"
            "### 6. RECOMMENDATIONS\n"
            "Actionable recommendations (bullet points).\n\n"
            "### 7. VERIFICATION VERDICT\n"
            "Final verdict: Is this checkout sufficient to approve the change? Why or why not?"
        )

        return "\n".join(sections)

    def _parse_ai_validation_response(self, ai_text: str, trace_analysis: TraceAnalysis) -> Dict:
        """Parse the AI response into a structured validation result."""
        result = {
            "validation_status": "INCONCLUSIVE",
            "confidence": "LOW",
            "summary": "",
            "test_coverage_analysis": "",
            "findings": [],
            "recommendations": [],
            "verification_verdict": "",
        }

        text_upper = ai_text.upper()

        # Extract validation status
        for status in ["VALIDATED", "PARTIALLY_VALIDATED", "NOT_VALIDATED", "INCONCLUSIVE"]:
            # Look for the status in the VALIDATION STATUS section
            if f"VALIDATION STATUS" in text_upper:
                status_section = ai_text[text_upper.find("VALIDATION STATUS"):]
                status_section = status_section[:200]  # Look in first 200 chars of section
                if status in status_section.upper():
                    result["validation_status"] = status
                    break

        # Extract confidence level
        for level in ["HIGH", "MEDIUM", "LOW"]:
            if f"CONFIDENCE" in text_upper:
                conf_section = ai_text[text_upper.find("CONFIDENCE"):]
                conf_section = conf_section[:200]
                if level in conf_section.upper():
                    result["confidence"] = level
                    break

        # Extract sections by headers
        result["summary"] = self._extract_section(ai_text, "VALIDATION SUMMARY", "TEST COVERAGE")
        result["test_coverage_analysis"] = self._extract_section(
            ai_text, "TEST COVERAGE ANALYSIS", "FINDINGS"
        )
        result["verification_verdict"] = self._extract_section(
            ai_text, "VERIFICATION VERDICT", None
        )

        # Extract findings as bullet points
        findings_text = self._extract_section(ai_text, "FINDINGS", "RECOMMENDATIONS")
        if findings_text:
            result["findings"] = self._extract_bullet_points(findings_text)

        # Extract recommendations as bullet points
        rec_text = self._extract_section(ai_text, "RECOMMENDATIONS", "VERIFICATION")
        if rec_text:
            result["recommendations"] = self._extract_bullet_points(rec_text)

        # If summary is empty, generate a basic one
        if not result["summary"]:
            if trace_analysis.failed_tests == 0:
                result["summary"] = (
                    f"All {trace_analysis.total_tests} tests passed with "
                    f"{trace_analysis.pass_rate:.1f}% pass rate."
                )
            else:
                result["summary"] = (
                    f"{trace_analysis.failed_tests}/{trace_analysis.total_tests} tests failed "
                    f"({trace_analysis.pass_rate:.1f}% pass rate)."
                )

        return result

    # ──────────────────────────────────────────────────────────────────────
    # RULE-BASED FALLBACK VALIDATION
    # ──────────────────────────────────────────────────────────────────────

    def _validate_rule_based(
        self,
        trace_analysis: TraceAnalysis,
        jira_context: Optional[Dict],
        test_scenarios: Optional[str],
        code_changes: Optional[str],
        log_callback: Optional[Callable],
    ) -> Dict:
        """Rule-based validation fallback when AI is unavailable."""
        findings = []
        recommendations = []

        # ── Determine validation status based on test results ─────────────
        if trace_analysis.total_tests == 0:
            validation_status = "NOT_VALIDATED"
            confidence = "HIGH"
            summary = "No tests were executed. Checkout cannot validate the change."
            findings.append("No test results found in trace file")
            recommendations.append("Ensure test execution completed and trace file is valid")
        elif trace_analysis.failed_tests == 0:
            validation_status = "VALIDATED"
            confidence = "MEDIUM"  # MEDIUM because we can't verify coverage without AI
            summary = (
                f"All {trace_analysis.total_tests} tests passed "
                f"({trace_analysis.pass_rate:.1f}% pass rate). "
                f"Duration: {trace_analysis.duration_seconds / 3600:.2f} hours."
            )
            findings.append(f"All {trace_analysis.total_tests} tests passed successfully")

            # Check flow coverage
            if trace_analysis.flows_executed:
                findings.append(
                    f"{len(trace_analysis.flows_executed)} test flow(s) executed: "
                    f"{', '.join(trace_analysis.flows_executed[:5])}"
                )
            else:
                findings.append("No flow information found in trace")
                recommendations.append("Verify that all required test flows were executed")
                confidence = "LOW"
        else:
            fail_rate = (trace_analysis.failed_tests / trace_analysis.total_tests) * 100
            if fail_rate < 5:
                validation_status = "PARTIALLY_VALIDATED"
                confidence = "MEDIUM"
                summary = (
                    f"{trace_analysis.failed_tests} of {trace_analysis.total_tests} tests failed "
                    f"({trace_analysis.pass_rate:.1f}% pass rate). "
                    f"Low failure rate — review failed tests for relevance."
                )
            elif fail_rate < 20:
                validation_status = "PARTIALLY_VALIDATED"
                confidence = "LOW"
                summary = (
                    f"{trace_analysis.failed_tests} of {trace_analysis.total_tests} tests failed "
                    f"({trace_analysis.pass_rate:.1f}% pass rate). "
                    f"Moderate failure rate — investigation needed."
                )
            else:
                validation_status = "NOT_VALIDATED"
                confidence = "HIGH"
                summary = (
                    f"{trace_analysis.failed_tests} of {trace_analysis.total_tests} tests failed "
                    f"({trace_analysis.pass_rate:.1f}% pass rate). "
                    f"High failure rate — checkout does not validate the change."
                )

            # Detail failed tests
            failed_tests = [t for t in trace_analysis.test_results if t.result == 'FAIL']
            for t in failed_tests[:10]:
                findings.append(f"FAILED: {t.test_name}" + (f" ({t.flow_file})" if t.flow_file else ""))
            if len(failed_tests) > 10:
                findings.append(f"... and {len(failed_tests) - 10} more failed tests")

            recommendations.append("Review all failed tests to determine if failures are related to the change")
            recommendations.append("Check if failed tests are known failures or new regressions")

        # ── Test coverage analysis ────────────────────────────────────────
        test_coverage_analysis = self._analyze_test_coverage_rule_based(
            trace_analysis, jira_context, test_scenarios
        )

        # ── Check for expected test scenarios ─────────────────────────────
        if test_scenarios and jira_context:
            findings.append("JIRA context and test scenarios available for cross-reference")
            if not self.ai_client:
                recommendations.append(
                    "Configure AI client for deeper analysis of test coverage "
                    "against JIRA requirements"
                )

        return {
            "success": True,
            "validation_status": validation_status,
            "confidence": confidence,
            "summary": summary,
            "test_coverage_analysis": test_coverage_analysis,
            "findings": findings,
            "recommendations": recommendations,
            "verification_verdict": (
                f"Validation Status: {validation_status} (Confidence: {confidence}). "
                f"{summary}"
            ),
            "ai_analysis": None,
            "method": "rule_based",
        }

    def _analyze_test_coverage_rule_based(
        self,
        trace_analysis: TraceAnalysis,
        jira_context: Optional[Dict],
        test_scenarios: Optional[str],
    ) -> str:
        """Generate test coverage analysis using rule-based approach."""
        lines = []

        # Flow coverage
        expected_phases = ['init', 'main', 'wrapup']
        executed_lower = [f.lower() for f in trace_analysis.flows_executed]

        lines.append("Test Flow Coverage:")
        for phase in expected_phases:
            found = any(phase in flow for flow in executed_lower)
            status = "✓" if found else "✗ MISSING"
            lines.append(f"  {status} {phase} phase")

        # Test volume assessment
        lines.append(f"\nTest Volume: {trace_analysis.total_tests} tests executed")
        if trace_analysis.total_tests < 10:
            lines.append("  ⚠ Low test count — may indicate incomplete test execution")
        elif trace_analysis.total_tests > 100:
            lines.append("  ✓ Comprehensive test execution")

        # Duration assessment
        if trace_analysis.duration_seconds > 0:
            hours = trace_analysis.duration_seconds / 3600
            lines.append(f"\nTest Duration: {hours:.2f} hours")
            if hours < 0.5:
                lines.append("  ⚠ Short test duration — verify all tests ran to completion")

        # Performance metrics coverage
        tests_with_metrics = [t for t in trace_analysis.test_results if t.performance_metrics]
        if tests_with_metrics:
            lines.append(f"\nPerformance Metrics: {len(tests_with_metrics)} tests with metrics")
        else:
            lines.append("\nPerformance Metrics: No performance metrics captured")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # TRACE CONTENT READING
    # ──────────────────────────────────────────────────────────────────────

    def _read_trace_sample(self, trace_file_path: str, head_lines: int = 50,
                           tail_lines: int = 50) -> str:
        """
        Read a sample of trace file content (head + tail) for AI analysis.

        Args:
            trace_file_path: Path to trace file
            head_lines: Number of lines from the beginning
            tail_lines: Number of lines from the end

        Returns:
            Sample text with head and tail of the trace file
        """
        try:
            with open(trace_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()

            total = len(all_lines)
            if total <= head_lines + tail_lines:
                return ''.join(all_lines)

            head = ''.join(all_lines[:head_lines])
            tail = ''.join(all_lines[-tail_lines:])
            return (
                f"{head}\n"
                f"... [{total - head_lines - tail_lines} lines omitted] ...\n"
                f"{tail}"
            )
        except Exception as e:
            logger.warning(f"Could not read trace sample: {e}")
            return ""

    # ──────────────────────────────────────────────────────────────────────
    # REPORT GENERATION
    # ──────────────────────────────────────────────────────────────────────

    def generate_validation_report(self, validation_result: Dict) -> str:
        """
        Generate a human-readable validation report.

        Args:
            validation_result: Result dict from validate_checkout()

        Returns:
            Formatted report text
        """
        lines = []

        lines.append("=" * 80)
        lines.append("AI CHECKOUT VALIDATION REPORT")
        lines.append("=" * 80)
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Method: {validation_result.get('method', 'unknown').upper()}")
        if validation_result.get('model_used'):
            lines.append(f"AI Model: {validation_result['model_used']}")
        lines.append("")

        # Status
        status = validation_result.get('validation_status', 'UNKNOWN')
        confidence = validation_result.get('confidence', 'UNKNOWN')
        status_icon = {
            "VALIDATED": "✅",
            "PARTIALLY_VALIDATED": "⚠️",
            "NOT_VALIDATED": "❌",
            "INCONCLUSIVE": "❓",
        }.get(status, "❓")

        lines.append(f"VALIDATION STATUS: {status_icon} {status}")
        lines.append(f"CONFIDENCE LEVEL:  {confidence}")
        lines.append("")

        # Summary
        summary = validation_result.get('summary', '')
        if summary:
            lines.append("SUMMARY:")
            lines.append(f"  {summary}")
            lines.append("")

        # Test Coverage Analysis
        coverage = validation_result.get('test_coverage_analysis', '')
        if coverage:
            lines.append("TEST COVERAGE ANALYSIS:")
            for line in coverage.split('\n'):
                lines.append(f"  {line}")
            lines.append("")

        # Findings
        findings = validation_result.get('findings', [])
        if findings:
            lines.append("FINDINGS:")
            for finding in findings:
                lines.append(f"  • {finding}")
            lines.append("")

        # Recommendations
        recommendations = validation_result.get('recommendations', [])
        if recommendations:
            lines.append("RECOMMENDATIONS:")
            for rec in recommendations:
                lines.append(f"  • {rec}")
            lines.append("")

        # Verification Verdict
        verdict = validation_result.get('verification_verdict', '')
        if verdict:
            lines.append("VERIFICATION VERDICT:")
            lines.append(f"  {verdict}")
            lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # UTILITY METHODS
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_section(text: str, start_header: str, end_header: Optional[str]) -> str:
        """Extract text between two markdown headers."""
        text_upper = text.upper()
        start_idx = text_upper.find(start_header.upper())
        if start_idx == -1:
            return ""

        # Move past the header line
        newline_idx = text.find('\n', start_idx)
        if newline_idx == -1:
            return ""
        content_start = newline_idx + 1

        # Find end
        if end_header:
            end_idx = text_upper.find(end_header.upper(), content_start)
            if end_idx == -1:
                content = text[content_start:]
            else:
                content = text[content_start:end_idx]
        else:
            content = text[content_start:]

        return content.strip()

    @staticmethod
    def _extract_bullet_points(text: str) -> List[str]:
        """Extract bullet points from text."""
        points = []
        for line in text.split('\n'):
            stripped = line.strip()
            if stripped.startswith(('- ', '• ', '* ', '· ')):
                points.append(stripped[2:].strip())
            elif stripped.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
                # Numbered list
                dot_idx = stripped.find('.')
                if dot_idx != -1:
                    points.append(stripped[dot_idx + 1:].strip())
        return points

    @staticmethod
    def _extract_response_text(response: Dict) -> str:
        """Extract text content from AI gateway response."""
        try:
            resp_data = response.get("response", {})
            choices = resp_data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
        except (KeyError, IndexError, TypeError):
            pass
        return ""

    @staticmethod
    def _log(callback: Optional[Callable], message: str):
        """Log to callback and logger."""
        logger.info(message)
        if callback:
            try:
                callback(message)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def validate_checkout_result(
    trace_file_path: str,
    ai_client=None,
    jira_context: Optional[Dict] = None,
    test_scenarios: Optional[str] = None,
    code_changes: Optional[str] = None,
    impact_analysis: Optional[str] = None,
) -> Dict:
    """
    Quick function to validate a checkout result.

    Args:
        trace_file_path: Path to trace file
        ai_client: Optional AIGatewayClient instance
        jira_context: Optional JIRA context dict
        test_scenarios: Optional expected test scenarios
        code_changes: Optional code changes description
        impact_analysis: Optional impact analysis text

    Returns:
        Validation result dict
    """
    validator = AICheckoutValidator(ai_client=ai_client)
    analyzer = TraceAnalyzer()
    trace_analysis = analyzer.analyze_file(trace_file_path)

    return validator.validate_checkout(
        trace_analysis=trace_analysis,
        jira_context=jira_context,
        test_scenarios=test_scenarios,
        code_changes=code_changes,
        impact_analysis=impact_analysis,
        trace_file_path=trace_file_path,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python ai_checkout_validator.py <trace_file> [jira_context_json]")
        print()
        print("  trace_file:        Path to DispatcherDebug*.txt trace file")
        print("  jira_context_json: Optional path to JSON file with JIRA context")
        sys.exit(1)

    trace_file = sys.argv[1]
    jira_ctx = None

    if len(sys.argv) > 2:
        try:
            with open(sys.argv[2], 'r') as f:
                jira_ctx = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load JIRA context: {e}")

    print("=" * 80)
    print("AI CHECKOUT VALIDATION")
    print("=" * 80)
    print(f"Trace File: {trace_file}")
    print()

    result = validate_checkout_result(
        trace_file_path=trace_file,
        jira_context=jira_ctx,
    )

    validator = AICheckoutValidator()
    report = validator.generate_validation_report(result)
    print(report)
