# -*- coding: utf-8 -*-
"""
Spool Summary Generator
Generates human-readable summary documents for checkout test results.
"""

import os
import sys
from typing import Dict, List, Optional
from datetime import datetime

# Handle imports for both standalone and module usage
try:
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer


class SpoolSummaryGenerator:
    """
    Generates spool summary documents for checkout test results.
    
    Creates a text-based summary with:
    - Test execution overview
    - Pass/fail statistics
    - Failed test details
    - Performance metrics summary
    - File references
    """
    
    def __init__(self):
        pass
    
    def generate_summary(
        self,
        trace_analysis: TraceAnalysis,
        jira_key: Optional[str] = None,
        mid: Optional[str] = None,
        sku: Optional[str] = None,
        trace_file_path: Optional[str] = None,
        database_path: Optional[str] = None,
    ) -> str:
        """
        Generate spool summary text.
        
        Args:
            trace_analysis: TraceAnalysis object with test results
            jira_key: JIRA issue key
            mid: Module ID
            sku: SKU identifier
            trace_file_path: Path to trace file
            database_path: Path to database file
            
        Returns:
            Formatted summary text
        """
        lines = []
        
        # Header
        lines.append("=" * 80)
        lines.append("CHECKOUT TEST EXECUTION SUMMARY")
        lines.append("=" * 80)
        lines.append("")
        
        # Metadata
        lines.append("METADATA:")
        if jira_key:
            lines.append(f"  JIRA Issue:      {jira_key}")
        if mid:
            lines.append(f"  Module ID (MID): {mid}")
        if sku:
            lines.append(f"  SKU:             {sku}")
        lines.append(f"  Generated:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # Test Statistics
        lines.append("TEST STATISTICS:")
        lines.append(f"  Total Tests:     {trace_analysis.total_tests}")
        lines.append(f"  Passed:          {trace_analysis.passed_tests}")
        lines.append(f"  Failed:          {trace_analysis.failed_tests}")
        lines.append(f"  Pass Rate:       {trace_analysis.pass_rate:.2f}%")
        lines.append("")
        
        # Timing Information
        if trace_analysis.start_time and trace_analysis.end_time:
            lines.append("TIMING INFORMATION:")
            lines.append(f"  Start Time:      {trace_analysis.start_time}")
            lines.append(f"  End Time:        {trace_analysis.end_time}")
            duration_hours = trace_analysis.duration_seconds / 3600
            lines.append(f"  Duration:        {duration_hours:.2f} hours ({trace_analysis.duration_seconds:.0f} seconds)")
            lines.append("")
        
        # Flows Executed
        if trace_analysis.flows_executed:
            lines.append("FLOWS EXECUTED:")
            for flow in trace_analysis.flows_executed:
                lines.append(f"  - {flow}")
            lines.append("")
        
        # Overall Result
        lines.append("OVERALL RESULT:")
        if trace_analysis.failed_tests == 0:
            lines.append("  [PASS] All tests passed successfully")
        else:
            lines.append(f"  [FAIL] {trace_analysis.failed_tests} test(s) failed")
        lines.append("")
        
        # Failed Tests Detail
        if trace_analysis.failed_tests > 0:
            lines.append("FAILED TESTS DETAIL:")
            lines.append("-" * 80)
            failed_tests = [t for t in trace_analysis.test_results if t.result == 'FAIL']
            
            for i, test in enumerate(failed_tests, 1):
                lines.append(f"{i}. {test.test_name}")
                if test.timestamp:
                    lines.append(f"   Time:        {test.timestamp}")
                if test.flow_file:
                    lines.append(f"   Flow:        {test.flow_file}")
                if test.command_times:
                    avg_time = sum(test.command_times) / len(test.command_times)
                    lines.append(f"   Avg Cmd Time: {avg_time:.6f}s")
                if test.error_message:
                    lines.append(f"   Error:       {test.error_message}")
                lines.append("")
            lines.append("-" * 80)
            lines.append("")
        
        # Performance Metrics Summary
        tests_with_metrics = [t for t in trace_analysis.test_results if t.performance_metrics]
        if tests_with_metrics:
            lines.append("PERFORMANCE METRICS:")
            lines.append(f"  Tests with metrics: {len(tests_with_metrics)}")
            
            # Aggregate metrics
            all_metrics = {}
            for test in tests_with_metrics:
                for key in test.performance_metrics.keys():
                    if key not in all_metrics:
                        all_metrics[key] = 0
                    all_metrics[key] += 1
            
            for metric_name, count in all_metrics.items():
                lines.append(f"  {metric_name}: {count} occurrences")
            lines.append("")
        
        # File References
        lines.append("FILE REFERENCES:")
        if trace_file_path:
            lines.append(f"  Trace File:      {trace_file_path}")
        if database_path:
            lines.append(f"  Database File:   {database_path}")
        lines.append("")
        
        # Footer
        lines.append("=" * 80)
        lines.append("END OF SUMMARY")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def generate_from_trace_file(
        self,
        trace_file_path: str,
        **kwargs
    ) -> str:
        """
        Convenience method to analyze trace file and generate summary in one step.
        
        Args:
            trace_file_path: Path to trace file
            **kwargs: Additional parameters (jira_key, mid, sku, etc.)
            
        Returns:
            Formatted summary text
        """
        # Analyze trace file
        analyzer = TraceAnalyzer()
        trace_analysis = analyzer.analyze_file(trace_file_path)
        
        # Generate summary
        return self.generate_summary(
            trace_analysis=trace_analysis,
            trace_file_path=trace_file_path,
            **kwargs
        )
    
    def save_summary(
        self,
        trace_analysis: TraceAnalysis,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Generate and save summary to file.
        
        Args:
            trace_analysis: TraceAnalysis object
            output_path: Path to save summary file
            **kwargs: Additional parameters
            
        Returns:
            True if successful, False otherwise
        """
        try:
            summary = self.generate_summary(trace_analysis, **kwargs)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(summary)
            return True
        except Exception as e:
            print(f"Error saving summary: {e}")
            return False
    
    def save_from_trace_file(
        self,
        trace_file_path: str,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Analyze trace file and save summary in one step.
        
        Args:
            trace_file_path: Path to trace file
            output_path: Path to save summary file
            **kwargs: Additional parameters
            
        Returns:
            True if successful, False otherwise
        """
        try:
            summary = self.generate_from_trace_file(trace_file_path, **kwargs)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(summary)
            return True
        except Exception as e:
            print(f"Error saving summary: {e}")
            return False


# Convenience function
def generate_spool_summary(
    trace_file_path: str,
    output_path: Optional[str] = None,
    **kwargs
) -> str:
    """
    Quick function to generate spool summary from trace file.
    
    Args:
        trace_file_path: Path to trace file
        output_path: Optional path to save summary (if None, returns text only)
        **kwargs: Additional parameters (jira_key, mid, sku, etc.)
        
    Returns:
        Summary text
    """
    generator = SpoolSummaryGenerator()
    summary = generator.generate_from_trace_file(trace_file_path, **kwargs)
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(summary)
    
    return summary


if __name__ == '__main__':
    # Example usage
    if len(sys.argv) < 2:
        print("Usage: python spool_summary_generator.py <trace_file> [output_file] [jira_key] [mid] [sku]")
        sys.exit(1)
    
    trace_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    jira_key = sys.argv[3] if len(sys.argv) > 3 else None
    mid = sys.argv[4] if len(sys.argv) > 4 else None
    sku = sys.argv[5] if len(sys.argv) > 5 else None
    
    print(f"Generating spool summary...")
    print(f"  Trace file: {trace_file}")
    if output_file:
        print(f"  Output: {output_file}")
    if jira_key:
        print(f"  JIRA: {jira_key}")
    if mid:
        print(f"  MID: {mid}")
    if sku:
        print(f"  SKU: {sku}")
    print()
    
    summary = generate_spool_summary(
        trace_file_path=trace_file,
        output_path=output_file,
        jira_key=jira_key,
        mid=mid,
        sku=sku
    )
    
    if output_file:
        print(f"[SUCCESS] Summary saved to: {output_file}")
    else:
        print(summary)
