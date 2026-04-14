# -*- coding: utf-8 -*-
"""
Risk Assessor
AI-powered risk assessment for checkout test results.
"""

import os
import sys
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum

# Handle imports for both standalone and module usage
try:
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer


class RiskLevel(Enum):
    """Risk level enumeration."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskAssessment:
    """Risk assessment result."""
    
    def __init__(
        self,
        risk_level: RiskLevel,
        risk_score: float,
        findings: List[str],
        recommendations: List[str],
        performance_issues: List[Dict],
        test_coverage_gaps: List[str]
    ):
        self.risk_level = risk_level
        self.risk_score = risk_score  # 0-100
        self.findings = findings
        self.recommendations = recommendations
        self.performance_issues = performance_issues
        self.test_coverage_gaps = test_coverage_gaps
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "risk_level": self.risk_level.value,
            "risk_score": self.risk_score,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "performance_issues": self.performance_issues,
            "test_coverage_gaps": self.test_coverage_gaps
        }


class RiskAssessor:
    """
    AI-powered risk assessment for checkout test results.
    
    Analyzes:
    - Failed test patterns and impact
    - Performance degradation
    - Test coverage gaps
    - Historical comparison (if baseline provided)
    """
    
    # Risk scoring weights
    WEIGHT_FAIL_RATE = 0.4
    WEIGHT_CRITICAL_TESTS = 0.3
    WEIGHT_PERFORMANCE = 0.2
    WEIGHT_COVERAGE = 0.1
    
    # Critical test patterns (tests that are high-risk if they fail)
    CRITICAL_TEST_PATTERNS = [
        'init',
        'startup',
        'security',
        'authentication',
        'power',
        'firmware',
        'boot'
    ]
    
    def __init__(self):
        pass
    
    def assess_risk(
        self,
        trace_analysis: TraceAnalysis,
        baseline_analysis: Optional[TraceAnalysis] = None,
        expected_duration_hours: Optional[float] = None
    ) -> RiskAssessment:
        """
        Perform comprehensive risk assessment.
        
        Args:
            trace_analysis: Current test analysis
            baseline_analysis: Optional baseline for comparison
            expected_duration_hours: Expected test duration for performance check
            
        Returns:
            RiskAssessment object
        """
        findings = []
        recommendations = []
        performance_issues = []
        test_coverage_gaps = []
        
        # Calculate risk score components
        fail_rate_score = self._assess_fail_rate(trace_analysis, findings, recommendations)
        critical_test_score = self._assess_critical_tests(trace_analysis, findings, recommendations)
        performance_score = self._assess_performance(
            trace_analysis, expected_duration_hours, performance_issues, findings, recommendations
        )
        coverage_score = self._assess_coverage(trace_analysis, test_coverage_gaps, findings, recommendations)
        
        # Calculate weighted risk score
        risk_score = (
            fail_rate_score * self.WEIGHT_FAIL_RATE +
            critical_test_score * self.WEIGHT_CRITICAL_TESTS +
            performance_score * self.WEIGHT_PERFORMANCE +
            coverage_score * self.WEIGHT_COVERAGE
        )
        
        # Determine risk level
        risk_level = self._determine_risk_level(risk_score)
        
        # Add baseline comparison if available
        if baseline_analysis:
            self._compare_with_baseline(trace_analysis, baseline_analysis, findings, recommendations)
        
        return RiskAssessment(
            risk_level=risk_level,
            risk_score=risk_score,
            findings=findings,
            recommendations=recommendations,
            performance_issues=performance_issues,
            test_coverage_gaps=test_coverage_gaps
        )
    
    def _assess_fail_rate(
        self,
        analysis: TraceAnalysis,
        findings: List[str],
        recommendations: List[str]
    ) -> float:
        """Assess risk based on test failure rate."""
        if analysis.total_tests == 0:
            return 0.0
        
        fail_rate = (analysis.failed_tests / analysis.total_tests) * 100
        
        if fail_rate == 0:
            findings.append("All tests passed successfully")
            return 0.0
        elif fail_rate < 5:
            findings.append(f"Low failure rate: {fail_rate:.2f}% ({analysis.failed_tests}/{analysis.total_tests} tests)")
            recommendations.append("Review failed tests to ensure they are not critical")
            return 20.0
        elif fail_rate < 10:
            findings.append(f"Moderate failure rate: {fail_rate:.2f}% ({analysis.failed_tests}/{analysis.total_tests} tests)")
            recommendations.append("Investigate failed tests and fix issues before deployment")
            return 50.0
        elif fail_rate < 20:
            findings.append(f"High failure rate: {fail_rate:.2f}% ({analysis.failed_tests}/{analysis.total_tests} tests)")
            recommendations.append("CRITICAL: Significant test failures detected - do not deploy")
            return 80.0
        else:
            findings.append(f"CRITICAL failure rate: {fail_rate:.2f}% ({analysis.failed_tests}/{analysis.total_tests} tests)")
            recommendations.append("CRITICAL: Major test failures - immediate investigation required")
            return 100.0
    
    def _assess_critical_tests(
        self,
        analysis: TraceAnalysis,
        findings: List[str],
        recommendations: List[str]
    ) -> float:
        """Assess risk based on critical test failures."""
        failed_tests = [t for t in analysis.test_results if t.result == 'FAIL']
        
        critical_failures = []
        for test in failed_tests:
            test_name_lower = test.test_name.lower()
            for pattern in self.CRITICAL_TEST_PATTERNS:
                if pattern in test_name_lower:
                    critical_failures.append(test.test_name)
                    break
        
        if critical_failures:
            findings.append(f"CRITICAL: {len(critical_failures)} critical test(s) failed")
            findings.append(f"Critical failures: {', '.join(critical_failures[:3])}")
            recommendations.append("CRITICAL: Critical tests failed - do not deploy until resolved")
            return 100.0
        elif failed_tests:
            findings.append(f"{len(failed_tests)} non-critical test(s) failed")
            return 30.0
        else:
            return 0.0
    
    def _assess_performance(
        self,
        analysis: TraceAnalysis,
        expected_duration_hours: Optional[float],
        performance_issues: List[Dict],
        findings: List[str],
        recommendations: List[str]
    ) -> float:
        """Assess risk based on performance metrics."""
        if not expected_duration_hours:
            return 0.0
        
        actual_duration_hours = analysis.duration_seconds / 3600
        delta_hours = actual_duration_hours - expected_duration_hours
        delta_percent = (delta_hours / expected_duration_hours) * 100 if expected_duration_hours > 0 else 0
        
        if abs(delta_percent) < 5:
            findings.append(f"Performance within expected range ({delta_percent:+.2f}%)")
            return 0.0
        elif delta_percent > 5:
            issue = {
                "type": "performance_degradation",
                "expected_hours": expected_duration_hours,
                "actual_hours": actual_duration_hours,
                "delta_percent": delta_percent
            }
            performance_issues.append(issue)
            
            if delta_percent > 20:
                findings.append(f"CRITICAL: Significant performance degradation ({delta_percent:+.2f}%)")
                recommendations.append("Investigate performance regression before deployment")
                return 80.0
            elif delta_percent > 10:
                findings.append(f"WARNING: Performance degradation detected ({delta_percent:+.2f}%)")
                recommendations.append("Review performance impact")
                return 50.0
            else:
                findings.append(f"Minor performance degradation ({delta_percent:+.2f}%)")
                return 20.0
        else:
            findings.append(f"Performance improvement detected ({delta_percent:+.2f}%)")
            return 0.0
    
    def _assess_coverage(
        self,
        analysis: TraceAnalysis,
        test_coverage_gaps: List[str],
        findings: List[str],
        recommendations: List[str]
    ) -> float:
        """Assess risk based on test coverage."""
        # Check if all expected flows were executed
        expected_flows = ['init', 'main', 'wrapup']
        executed_flows = [f.lower() for f in analysis.flows_executed]
        
        missing_flows = []
        for expected in expected_flows:
            if not any(expected in flow for flow in executed_flows):
                missing_flows.append(expected)
        
        if missing_flows:
            test_coverage_gaps.extend(missing_flows)
            findings.append(f"WARNING: Missing expected flows: {', '.join(missing_flows)}")
            recommendations.append("Ensure all required test flows are executed")
            return 50.0
        
        return 0.0
    
    def _compare_with_baseline(
        self,
        current: TraceAnalysis,
        baseline: TraceAnalysis,
        findings: List[str],
        recommendations: List[str]
    ):
        """Compare current results with baseline."""
        # Compare pass rates
        pass_rate_delta = current.pass_rate - baseline.pass_rate
        
        if pass_rate_delta < -5:
            findings.append(f"WARNING: Pass rate decreased by {abs(pass_rate_delta):.2f}% vs baseline")
            recommendations.append("Investigate regression in test pass rate")
        elif pass_rate_delta > 5:
            findings.append(f"IMPROVEMENT: Pass rate increased by {pass_rate_delta:.2f}% vs baseline")
        
        # Compare test counts
        if current.total_tests < baseline.total_tests:
            findings.append(f"WARNING: Fewer tests executed ({current.total_tests} vs {baseline.total_tests})")
            recommendations.append("Verify all tests are being executed")
    
    def _determine_risk_level(self, risk_score: float) -> RiskLevel:
        """Determine risk level from score."""
        if risk_score >= 75:
            return RiskLevel.CRITICAL
        elif risk_score >= 50:
            return RiskLevel.HIGH
        elif risk_score >= 25:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def generate_report(self, assessment: RiskAssessment) -> str:
        """Generate human-readable risk assessment report."""
        lines = []
        
        lines.append("=" * 80)
        lines.append("RISK ASSESSMENT REPORT")
        lines.append("=" * 80)
        lines.append("")
        
        # Risk level and score
        lines.append(f"RISK LEVEL: {assessment.risk_level.value}")
        lines.append(f"RISK SCORE: {assessment.risk_score:.1f}/100")
        lines.append("")
        
        # Findings
        if assessment.findings:
            lines.append("FINDINGS:")
            for finding in assessment.findings:
                lines.append(f"  - {finding}")
            lines.append("")
        
        # Performance issues
        if assessment.performance_issues:
            lines.append("PERFORMANCE ISSUES:")
            for issue in assessment.performance_issues:
                lines.append(f"  - Type: {issue['type']}")
                lines.append(f"    Expected: {issue['expected_hours']:.2f} hours")
                lines.append(f"    Actual: {issue['actual_hours']:.2f} hours")
                lines.append(f"    Delta: {issue['delta_percent']:+.2f}%")
            lines.append("")
        
        # Test coverage gaps
        if assessment.test_coverage_gaps:
            lines.append("TEST COVERAGE GAPS:")
            for gap in assessment.test_coverage_gaps:
                lines.append(f"  - {gap}")
            lines.append("")
        
        # Recommendations
        if assessment.recommendations:
            lines.append("RECOMMENDATIONS:")
            for rec in assessment.recommendations:
                lines.append(f"  - {rec}")
            lines.append("")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def assess_from_trace_file(
        self,
        trace_file_path: str,
        **kwargs
    ) -> RiskAssessment:
        """
        Convenience method to analyze trace file and assess risk in one step.
        
        Args:
            trace_file_path: Path to trace file
            **kwargs: Additional parameters
            
        Returns:
            RiskAssessment object
        """
        analyzer = TraceAnalyzer()
        trace_analysis = analyzer.analyze_file(trace_file_path)
        
        return self.assess_risk(trace_analysis, **kwargs)


# Convenience function
def assess_risk(
    trace_file_path: str,
    baseline_trace_path: Optional[str] = None,
    expected_duration_hours: Optional[float] = None
) -> RiskAssessment:
    """
    Quick function to assess risk from trace file.
    
    Args:
        trace_file_path: Path to trace file
        baseline_trace_path: Optional path to baseline trace file
        expected_duration_hours: Expected test duration
        
    Returns:
        RiskAssessment object
    """
    assessor = RiskAssessor()
    analyzer = TraceAnalyzer()
    
    # Analyze current trace
    trace_analysis = analyzer.analyze_file(trace_file_path)
    
    # Analyze baseline if provided
    baseline_analysis = None
    if baseline_trace_path:
        baseline_analysis = analyzer.analyze_file(baseline_trace_path)
    
    return assessor.assess_risk(
        trace_analysis,
        baseline_analysis=baseline_analysis,
        expected_duration_hours=expected_duration_hours
    )


if __name__ == '__main__':
    # Example usage
    if len(sys.argv) < 2:
        print("Usage: python risk_assessor.py <trace_file> [expected_duration_hours] [baseline_trace]")
        sys.exit(1)
    
    trace_file = sys.argv[1]
    expected_duration = float(sys.argv[2]) if len(sys.argv) > 2 else None
    baseline_trace = sys.argv[3] if len(sys.argv) > 3 else None
    
    print(f"Performing risk assessment...")
    print(f"  Trace file: {trace_file}")
    if expected_duration:
        print(f"  Expected duration: {expected_duration} hours")
    if baseline_trace:
        print(f"  Baseline: {baseline_trace}")
    print()
    
    assessment = assess_risk(
        trace_file_path=trace_file,
        baseline_trace_path=baseline_trace,
        expected_duration_hours=expected_duration
    )
    
    assessor = RiskAssessor()
    report = assessor.generate_report(assessment)
    print(report)
