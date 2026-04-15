# -*- coding: utf-8 -*-
"""
AI-Enhanced Risk Assessor
=========================
Extends the rule-based RiskAssessor with AI-powered analysis
for deeper, context-aware risk assessment of checkout results.

Uses the Model Gateway AI to:
  - Correlate test failures with code change context
  - Identify subtle risk patterns that rule-based logic misses
  - Provide actionable, context-specific recommendations
  - Generate natural-language risk narratives
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
    from model.analyzers.risk_assessor import (
        RiskAssessor, RiskAssessment, RiskLevel
    )
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer
    from model.analyzers.risk_assessor import (
        RiskAssessor, RiskAssessment, RiskLevel
    )

logger = logging.getLogger("bento_app")


class AIRiskAssessor(RiskAssessor):
    """
    AI-enhanced risk assessor that extends the rule-based RiskAssessor.

    When an AI client is available, it augments the rule-based assessment
    with AI-powered analysis that considers:
      - JIRA change context and code change scope
      - Correlation between failed tests and change impact
      - Historical patterns and industry best practices
      - Subtle risk indicators that rules can't capture

    Falls back gracefully to the parent RiskAssessor when AI is unavailable.
    """

    SYSTEM_PROMPT = (
        "You are a senior risk assessment specialist for SSD test engineering "
        "and semiconductor manufacturing. Your role is to evaluate checkout "
        "test results and provide comprehensive risk analysis.\n\n"
        "You understand:\n"
        "- SSD test validation workflows and their significance\n"
        "- How test failures correlate with production risks\n"
        "- Performance degradation patterns and their implications\n"
        "- Test coverage requirements for different types of changes\n"
        "- Industry standards for validation completeness\n\n"
        "When assessing risk:\n"
        "- Consider both direct and indirect impacts\n"
        "- Evaluate if test coverage is sufficient for the change scope\n"
        "- Identify failure patterns that may indicate systemic issues\n"
        "- Provide specific, actionable mitigation strategies\n"
        "- Rate risk with clear justification\n"
        "- Consider worst-case production scenarios"
    )

    def __init__(self, ai_client=None):
        """
        Initialize the AI-enhanced risk assessor.

        Args:
            ai_client: AIGatewayClient instance for AI inference.
                       If None, behaves identically to the base RiskAssessor.
        """
        super().__init__()
        self.ai_client = ai_client

    def assess_risk_enhanced(
        self,
        trace_analysis: TraceAnalysis,
        baseline_analysis: Optional[TraceAnalysis] = None,
        expected_duration_hours: Optional[float] = None,
        jira_context: Optional[Dict] = None,
        code_changes: Optional[str] = None,
        impact_analysis: Optional[str] = None,
        test_scenarios: Optional[str] = None,
        validation_result: Optional[Dict] = None,
        log_callback: Optional[Callable] = None,
    ) -> Dict:
        """
        Perform enhanced risk assessment combining rule-based and AI analysis.

        Args:
            trace_analysis: Current test analysis
            baseline_analysis: Optional baseline for comparison
            expected_duration_hours: Expected test duration
            jira_context: JIRA change context dict
            code_changes: Description of code changes
            impact_analysis: Impact analysis text
            test_scenarios: Expected test scenarios
            validation_result: Result from AICheckoutValidator (if available)
            log_callback: Optional progress callback

        Returns:
            Enhanced risk assessment dict with:
              - rule_based: Original RiskAssessment from parent class
              - ai_analysis: AI-generated risk analysis text
              - enhanced_risk_level: Final risk level (AI-adjusted)
              - enhanced_risk_score: Final risk score (AI-adjusted)
              - ai_findings: Additional findings from AI
              - ai_recommendations: Additional recommendations from AI
              - mitigation_strategies: Specific mitigation strategies
              - production_risk_scenarios: Potential production failure scenarios
              - method: "ai_enhanced" or "rule_based"
        """
        self._log(log_callback, "📊 Performing enhanced risk assessment...")

        # Step 1: Run the base rule-based assessment
        self._log(log_callback, "  Step 1: Rule-based assessment...")
        rule_based = self.assess_risk(
            trace_analysis=trace_analysis,
            baseline_analysis=baseline_analysis,
            expected_duration_hours=expected_duration_hours,
        )

        result = {
            "success": True,
            "rule_based": rule_based.to_dict(),
            "enhanced_risk_level": rule_based.risk_level.value,
            "enhanced_risk_score": rule_based.risk_score,
            "ai_findings": [],
            "ai_recommendations": [],
            "mitigation_strategies": [],
            "production_risk_scenarios": [],
            "ai_analysis": None,
            "method": "rule_based",
        }

        # Step 2: If AI client available, enhance with AI analysis
        if self.ai_client:
            try:
                self._log(log_callback, "  Step 2: AI-enhanced analysis...")
                ai_result = self._assess_with_ai(
                    trace_analysis=trace_analysis,
                    rule_based=rule_based,
                    baseline_analysis=baseline_analysis,
                    expected_duration_hours=expected_duration_hours,
                    jira_context=jira_context,
                    code_changes=code_changes,
                    impact_analysis=impact_analysis,
                    test_scenarios=test_scenarios,
                    validation_result=validation_result,
                    log_callback=log_callback,
                )

                if ai_result.get("success"):
                    # Merge AI results
                    result["ai_analysis"] = ai_result.get("ai_text", "")
                    result["ai_findings"] = ai_result.get("findings", [])
                    result["ai_recommendations"] = ai_result.get("recommendations", [])
                    result["mitigation_strategies"] = ai_result.get("mitigation_strategies", [])
                    result["production_risk_scenarios"] = ai_result.get(
                        "production_risk_scenarios", []
                    )
                    result["method"] = "ai_enhanced"
                    result["model_used"] = ai_result.get("model_used", "unknown")

                    # Adjust risk level based on AI analysis
                    ai_risk_level = ai_result.get("ai_risk_level")
                    ai_risk_score = ai_result.get("ai_risk_score")
                    if ai_risk_level and ai_risk_score is not None:
                        # Use weighted average: 60% rule-based, 40% AI
                        blended_score = (rule_based.risk_score * 0.6) + (ai_risk_score * 0.4)
                        result["enhanced_risk_score"] = round(blended_score, 1)
                        result["enhanced_risk_level"] = self._determine_risk_level(
                            blended_score
                        ).value
                    
                    self._log(log_callback,
                              f"  ✓ AI-enhanced risk: {result['enhanced_risk_level']} "
                              f"(score: {result['enhanced_risk_score']:.1f}/100)")
                else:
                    self._log(log_callback,
                              f"  ⚠ AI analysis failed: {ai_result.get('error', 'unknown')}")
            except Exception as e:
                self._log(log_callback, f"  ⚠ AI enhancement error: {e}")
                logger.warning(f"AI risk assessment failed: {e}")
        else:
            self._log(log_callback, "  Step 2: Skipped (no AI client configured)")

        self._log(log_callback,
                  f"  ✓ Final risk: {result['enhanced_risk_level']} "
                  f"(score: {result['enhanced_risk_score']:.1f}/100)")
        return result

    def _assess_with_ai(
        self,
        trace_analysis: TraceAnalysis,
        rule_based: RiskAssessment,
        baseline_analysis: Optional[TraceAnalysis],
        expected_duration_hours: Optional[float],
        jira_context: Optional[Dict],
        code_changes: Optional[str],
        impact_analysis: Optional[str],
        test_scenarios: Optional[str],
        validation_result: Optional[Dict],
        log_callback: Optional[Callable],
    ) -> Dict:
        """Perform AI-powered risk analysis."""
        prompt = self._build_risk_prompt(
            trace_analysis=trace_analysis,
            rule_based=rule_based,
            baseline_analysis=baseline_analysis,
            expected_duration_hours=expected_duration_hours,
            jira_context=jira_context,
            code_changes=code_changes,
            impact_analysis=impact_analysis,
            test_scenarios=test_scenarios,
            validation_result=validation_result,
        )

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        response = self.ai_client.chat_completion(
            messages=messages,
            task_type="analysis",
        )

        if not response.get("success"):
            return {"success": False, "error": response.get("error", "AI request failed")}

        ai_text = self._extract_response_text(response)
        if not ai_text:
            return {"success": False, "error": "Empty AI response"}

        # Parse AI response
        parsed = self._parse_ai_risk_response(ai_text)
        parsed["ai_text"] = ai_text
        parsed["model_used"] = response.get("model_used", "unknown")
        parsed["success"] = True
        return parsed

    def _build_risk_prompt(
        self,
        trace_analysis: TraceAnalysis,
        rule_based: RiskAssessment,
        baseline_analysis: Optional[TraceAnalysis],
        expected_duration_hours: Optional[float],
        jira_context: Optional[Dict],
        code_changes: Optional[str],
        impact_analysis: Optional[str],
        test_scenarios: Optional[str],
        validation_result: Optional[Dict],
    ) -> str:
        """Build the risk assessment prompt for AI."""
        sections = []

        sections.append(
            "Perform a comprehensive risk assessment for the following "
            "checkout test results. Consider the code change context and "
            "provide specific, actionable analysis.\n"
        )

        # ── Rule-Based Assessment Results ─────────────────────────────────
        sections.append("## RULE-BASED ASSESSMENT (baseline)")
        sections.append(f"- Risk Level: {rule_based.risk_level.value}")
        sections.append(f"- Risk Score: {rule_based.risk_score:.1f}/100")
        if rule_based.findings:
            sections.append("- Findings:")
            for f in rule_based.findings:
                sections.append(f"  • {f}")
        if rule_based.performance_issues:
            sections.append("- Performance Issues:")
            for issue in rule_based.performance_issues:
                sections.append(f"  • {issue.get('type', 'unknown')}: "
                                f"delta {issue.get('delta_percent', 0):+.1f}%")

        # ── Test Results ──────────────────────────────────────────────────
        sections.append("\n## TEST RESULTS")
        sections.append(f"- Total Tests: {trace_analysis.total_tests}")
        sections.append(f"- Passed: {trace_analysis.passed_tests}")
        sections.append(f"- Failed: {trace_analysis.failed_tests}")
        sections.append(f"- Pass Rate: {trace_analysis.pass_rate:.2f}%")
        if trace_analysis.duration_seconds:
            sections.append(
                f"- Duration: {trace_analysis.duration_seconds / 3600:.2f} hours"
            )
        if expected_duration_hours:
            sections.append(f"- Expected Duration: {expected_duration_hours:.2f} hours")

        # Failed tests
        failed = [t for t in trace_analysis.test_results if t.result == 'FAIL']
        if failed:
            sections.append(f"\n### Failed Tests ({len(failed)}):")
            for t in failed[:15]:
                line = f"  - {t.test_name}"
                if t.flow_file:
                    line += f" (flow: {t.flow_file})"
                sections.append(line)

        # Flows
        if trace_analysis.flows_executed:
            sections.append(f"\n### Flows Executed: {', '.join(trace_analysis.flows_executed)}")

        # ── Baseline Comparison ───────────────────────────────────────────
        if baseline_analysis:
            sections.append("\n## BASELINE COMPARISON")
            sections.append(f"- Baseline Pass Rate: {baseline_analysis.pass_rate:.2f}%")
            sections.append(f"- Current Pass Rate: {trace_analysis.pass_rate:.2f}%")
            delta = trace_analysis.pass_rate - baseline_analysis.pass_rate
            sections.append(f"- Delta: {delta:+.2f}%")
            sections.append(f"- Baseline Tests: {baseline_analysis.total_tests}")
            sections.append(f"- Current Tests: {trace_analysis.total_tests}")

        # ── JIRA Context ──────────────────────────────────────────────────
        if jira_context:
            sections.append("\n## CHANGE CONTEXT (from JIRA)")
            if isinstance(jira_context, dict):
                for key in ['change_objective', 'description', 'summary',
                             'components', 'work_type', 'acceptance_criteria']:
                    val = jira_context.get(key)
                    if val:
                        sections.append(f"- {key.replace('_', ' ').title()}: {val}")
            elif isinstance(jira_context, str):
                sections.append(jira_context[:2000])

        # ── Code Changes ──────────────────────────────────────────────────
        if code_changes:
            sections.append("\n## CODE CHANGES")
            sections.append(code_changes[:2000])

        # ── Impact Analysis ───────────────────────────────────────────────
        if impact_analysis:
            sections.append("\n## IMPACT ANALYSIS")
            sections.append(impact_analysis[:2000])

        # ── Validation Result ─────────────────────────────────────────────
        if validation_result:
            sections.append("\n## CHECKOUT VALIDATION RESULT")
            sections.append(
                f"- Status: {validation_result.get('validation_status', 'N/A')}"
            )
            sections.append(
                f"- Confidence: {validation_result.get('confidence', 'N/A')}"
            )
            summary = validation_result.get('summary', '')
            if summary:
                sections.append(f"- Summary: {summary}")

        # ── Analysis Instructions ─────────────────────────────────────────
        sections.append("\n## ANALYSIS REQUIRED")
        sections.append(
            "Provide a comprehensive risk assessment with the following sections:\n\n"
            "### 1. AI RISK LEVEL\n"
            "One of: LOW, MEDIUM, HIGH, CRITICAL\n\n"
            "### 2. AI RISK SCORE\n"
            "Numeric score 0-100\n\n"
            "### 3. RISK SUMMARY\n"
            "Brief narrative of the overall risk posture.\n\n"
            "### 4. ADDITIONAL FINDINGS\n"
            "Findings that the rule-based assessment may have missed (bullet points).\n\n"
            "### 5. PRODUCTION RISK SCENARIOS\n"
            "Specific scenarios where this change could cause issues in production, "
            "even if checkout passed (bullet points).\n\n"
            "### 6. MITIGATION STRATEGIES\n"
            "Specific, actionable mitigation strategies (bullet points).\n\n"
            "### 7. RECOMMENDATIONS\n"
            "Final recommendations for the engineering team (bullet points)."
        )

        return "\n".join(sections)

    def _parse_ai_risk_response(self, ai_text: str) -> Dict:
        """Parse AI risk assessment response into structured data."""
        result = {
            "ai_risk_level": None,
            "ai_risk_score": None,
            "findings": [],
            "recommendations": [],
            "mitigation_strategies": [],
            "production_risk_scenarios": [],
        }

        text_upper = ai_text.upper()

        # Extract AI risk level
        for level in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            if "AI RISK LEVEL" in text_upper:
                section = ai_text[text_upper.find("AI RISK LEVEL"):]
                section = section[:200]
                if level in section.upper():
                    result["ai_risk_level"] = level
                    break

        # Extract AI risk score
        if "AI RISK SCORE" in text_upper:
            import re
            section = ai_text[text_upper.find("AI RISK SCORE"):]
            section = section[:200]
            score_match = re.search(r'(\d{1,3})', section)
            if score_match:
                score = int(score_match.group(1))
                if 0 <= score <= 100:
                    result["ai_risk_score"] = float(score)

        # Extract bullet-point sections
        result["findings"] = self._extract_bullet_section(
            ai_text, "ADDITIONAL FINDINGS", "PRODUCTION RISK"
        )
        result["production_risk_scenarios"] = self._extract_bullet_section(
            ai_text, "PRODUCTION RISK SCENARIOS", "MITIGATION"
        )
        result["mitigation_strategies"] = self._extract_bullet_section(
            ai_text, "MITIGATION STRATEGIES", "RECOMMENDATIONS"
        )
        result["recommendations"] = self._extract_bullet_section(
            ai_text, "RECOMMENDATIONS", None
        )

        return result

    def generate_enhanced_report(self, assessment_result: Dict) -> str:
        """
        Generate a comprehensive enhanced risk assessment report.

        Args:
            assessment_result: Result dict from assess_risk_enhanced()

        Returns:
            Formatted report text
        """
        lines = []

        lines.append("=" * 80)
        lines.append("ENHANCED RISK ASSESSMENT REPORT")
        lines.append("=" * 80)
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Method: {assessment_result.get('method', 'unknown').upper()}")
        if assessment_result.get('model_used'):
            lines.append(f"AI Model: {assessment_result['model_used']}")
        lines.append("")

        # Risk Level
        level = assessment_result.get('enhanced_risk_level', 'UNKNOWN')
        score = assessment_result.get('enhanced_risk_score', 0)
        level_icon = {
            "LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"
        }.get(level, "⚪")

        lines.append(f"RISK LEVEL: {level_icon} {level}")
        lines.append(f"RISK SCORE: {score:.1f}/100")
        lines.append("")

        # Rule-based baseline
        rb = assessment_result.get('rule_based', {})
        if rb:
            lines.append("RULE-BASED BASELINE:")
            lines.append(f"  Level: {rb.get('risk_level', 'N/A')}")
            lines.append(f"  Score: {rb.get('risk_score', 0):.1f}/100")
            rb_findings = rb.get('findings', [])
            if rb_findings:
                lines.append("  Findings:")
                for f in rb_findings:
                    lines.append(f"    • {f}")
            lines.append("")

        # AI Findings
        ai_findings = assessment_result.get('ai_findings', [])
        if ai_findings:
            lines.append("AI-IDENTIFIED FINDINGS:")
            for f in ai_findings:
                lines.append(f"  • {f}")
            lines.append("")

        # Production Risk Scenarios
        scenarios = assessment_result.get('production_risk_scenarios', [])
        if scenarios:
            lines.append("PRODUCTION RISK SCENARIOS:")
            for s in scenarios:
                lines.append(f"  ⚠ {s}")
            lines.append("")

        # Mitigation Strategies
        mitigations = assessment_result.get('mitigation_strategies', [])
        if mitigations:
            lines.append("MITIGATION STRATEGIES:")
            for m in mitigations:
                lines.append(f"  → {m}")
            lines.append("")

        # Recommendations (combined rule-based + AI)
        rb_recs = rb.get('recommendations', []) if rb else []
        ai_recs = assessment_result.get('ai_recommendations', [])
        all_recs = rb_recs + ai_recs
        if all_recs:
            lines.append("RECOMMENDATIONS:")
            for r in all_recs:
                lines.append(f"  • {r}")
            lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # UTILITY METHODS
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_bullet_section(
        text: str, start_header: str, end_header: Optional[str]
    ) -> List[str]:
        """Extract bullet points from a section between two headers."""
        text_upper = text.upper()
        start_idx = text_upper.find(start_header.upper())
        if start_idx == -1:
            return []

        # Move past header line
        newline_idx = text.find('\n', start_idx)
        if newline_idx == -1:
            return []
        content_start = newline_idx + 1

        # Find end
        if end_header:
            end_idx = text_upper.find(end_header.upper(), content_start)
            if end_idx == -1:
                section_text = text[content_start:]
            else:
                section_text = text[content_start:end_idx]
        else:
            section_text = text[content_start:]

        # Extract bullet points
        points = []
        for line in section_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith(('- ', '• ', '* ', '· ')):
                points.append(stripped[2:].strip())
            elif stripped and stripped[0].isdigit() and '.' in stripped[:4]:
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

def assess_risk_enhanced(
    trace_file_path: str,
    ai_client=None,
    baseline_trace_path: Optional[str] = None,
    expected_duration_hours: Optional[float] = None,
    jira_context: Optional[Dict] = None,
    code_changes: Optional[str] = None,
    impact_analysis: Optional[str] = None,
) -> Dict:
    """
    Quick function to perform enhanced risk assessment.

    Args:
        trace_file_path: Path to trace file
        ai_client: Optional AIGatewayClient instance
        baseline_trace_path: Optional baseline trace file
        expected_duration_hours: Expected test duration
        jira_context: Optional JIRA context
        code_changes: Optional code changes description
        impact_analysis: Optional impact analysis text

    Returns:
        Enhanced risk assessment dict
    """
    assessor = AIRiskAssessor(ai_client=ai_client)
    analyzer = TraceAnalyzer()

    trace_analysis = analyzer.analyze_file(trace_file_path)

    baseline_analysis = None
    if baseline_trace_path:
        baseline_analysis = analyzer.analyze_file(baseline_trace_path)

    return assessor.assess_risk_enhanced(
        trace_analysis=trace_analysis,
        baseline_analysis=baseline_analysis,
        expected_duration_hours=expected_duration_hours,
        jira_context=jira_context,
        code_changes=code_changes,
        impact_analysis=impact_analysis,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python ai_risk_assessor.py <trace_file> "
              "[expected_hours] [baseline_trace] [jira_context_json]")
        sys.exit(1)

    trace_file = sys.argv[1]
    expected_hours = float(sys.argv[2]) if len(sys.argv) > 2 else None
    baseline = sys.argv[3] if len(sys.argv) > 3 else None
    jira_ctx = None
    if len(sys.argv) > 4:
        try:
            with open(sys.argv[4], 'r') as f:
                jira_ctx = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load JIRA context: {e}")

    print("=" * 80)
    print("AI-ENHANCED RISK ASSESSMENT")
    print("=" * 80)
    print(f"Trace File: {trace_file}")
    if expected_hours:
        print(f"Expected Duration: {expected_hours} hours")
    if baseline:
        print(f"Baseline: {baseline}")
    print()

    result = assess_risk_enhanced(
        trace_file_path=trace_file,
        baseline_trace_path=baseline,
        expected_duration_hours=expected_hours,
        jira_context=jira_ctx,
    )

    assessor = AIRiskAssessor()
    report = assessor.generate_enhanced_report(result)
    print(report)
