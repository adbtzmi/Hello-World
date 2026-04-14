# -*- coding: utf-8 -*-
"""
Trace File Analyzer for SLATE Test Framework
Parses DispatcherDebug trace files to extract test results and performance metrics.
"""

import re
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class TestResult:
    """Represents a single test case result."""
    test_name: str
    result: str  # PASS, FAIL
    timestamp: Optional[datetime] = None
    command_times: List[float] = field(default_factory=list)
    performance_metrics: Dict = field(default_factory=dict)
    flow_file: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class TraceAnalysis:
    """Complete analysis of a trace file."""
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    test_results: List[TestResult] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    flows_executed: List[str] = field(default_factory=list)
    
    @property
    def pass_rate(self) -> float:
        """Calculate pass rate percentage."""
        if self.total_tests == 0:
            return 0.0
        return (self.passed_tests / self.total_tests) * 100


class TraceAnalyzer:
    """
    Analyzes SLATE test framework trace files (DispatcherDebug*.txt).
    
    Extracts:
    - Test case names and results (PASS/FAIL)
    - Command execution times
    - Performance metrics (tPROG, tBERS, tREAD, tRSNAP, tPBSY)
    - Test flow information
    - Timestamps and duration
    """
    
    # Regex patterns
    TIMESTAMP_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):INFO:STDOUT:(.+)$'
    )
    RUNNING_PATTERN = re.compile(r'^Running:\s+(.+\.flow)$')
    TEST_RESULT_PATTERN = re.compile(r'^Test Result:\s+(PASS|FAIL)$')
    COMMAND_TIME_PATTERN = re.compile(r'^Command time:\s+([\d.]+)$')
    
    # Performance metrics patterns (looking for dictionary-like structures)
    PERF_METRICS_PATTERN = re.compile(
        r'\{[^}]*["\']?(tPROG|tBERS|tREAD|tRSNAP|tPBSY)["\']?\s*:'
    )
    
    def __init__(self):
        self.current_flow = None
        self.pending_command_times = []
        self.last_timestamp = None
    
    def analyze_file(self, trace_file_path: str) -> TraceAnalysis:
        """
        Analyze a trace file and extract all test results and metrics.
        
        Args:
            trace_file_path: Path to the DispatcherDebug*.txt file
            
        Returns:
            TraceAnalysis object with complete analysis
        """
        analysis = TraceAnalysis()
        
        try:
            with open(trace_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    self._process_line(line.strip(), analysis)
            
            # Finalize analysis
            if analysis.start_time and analysis.end_time:
                analysis.duration_seconds = (
                    analysis.end_time - analysis.start_time
                ).total_seconds()
            
            analysis.total_tests = len(analysis.test_results)
            analysis.passed_tests = sum(
                1 for t in analysis.test_results if t.result == 'PASS'
            )
            analysis.failed_tests = sum(
                1 for t in analysis.test_results if t.result == 'FAIL'
            )
            
        except Exception as e:
            print(f"Error analyzing trace file: {e}")
        
        return analysis
    
    def _process_line(self, line: str, analysis: TraceAnalysis):
        """Process a single line from the trace file."""
        # Parse timestamp and message
        match = self.TIMESTAMP_PATTERN.match(line)
        if not match:
            return
        
        timestamp_str, message = match.groups()
        timestamp = self._parse_timestamp(timestamp_str)
        
        # Track first and last timestamps
        if analysis.start_time is None:
            analysis.start_time = timestamp
        analysis.end_time = timestamp
        self.last_timestamp = timestamp
        
        # Check for flow execution
        flow_match = self.RUNNING_PATTERN.match(message)
        if flow_match:
            self.current_flow = flow_match.group(1)
            if self.current_flow not in analysis.flows_executed:
                analysis.flows_executed.append(self.current_flow)
            return
        
        # Check for command time
        cmd_time_match = self.COMMAND_TIME_PATTERN.match(message)
        if cmd_time_match:
            cmd_time = float(cmd_time_match.group(1))
            self.pending_command_times.append(cmd_time)
            return
        
        # Check for test result
        result_match = self.TEST_RESULT_PATTERN.match(message)
        if result_match:
            result = result_match.group(1)
            
            # Create test result entry
            test_result = TestResult(
                test_name=self._extract_test_name(analysis),
                result=result,
                timestamp=timestamp,
                command_times=self.pending_command_times.copy(),
                flow_file=self.current_flow
            )
            
            analysis.test_results.append(test_result)
            
            # Clear pending command times
            self.pending_command_times.clear()
            return
        
        # Check for performance metrics
        if self.PERF_METRICS_PATTERN.search(message):
            self._extract_performance_metrics(message, analysis)
    
    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse timestamp string to datetime object."""
        try:
            return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
        except ValueError:
            return datetime.now()
    
    def _extract_test_name(self, analysis: TraceAnalysis) -> str:
        """
        Extract test name from context.
        Uses flow file and test count as identifier.
        """
        flow_name = self.current_flow or "unknown_flow"
        test_count = len(analysis.test_results) + 1
        return f"{flow_name}:test_{test_count}"
    
    def _extract_performance_metrics(self, message: str, analysis: TraceAnalysis):
        """
        Extract performance metrics from log message.
        Looks for tPROG, tBERS, tREAD, tRSNAP, tPBSY metrics.
        """
        # Try to find dictionary-like structures in the message
        # This is a simplified extraction - may need refinement based on actual format
        try:
            # Look for patterns like {u'tPROG': {u'MAX': 2386, ...}}
            # Convert Python dict notation to JSON
            cleaned = message.replace("u'", '"').replace("'", '"')
            
            # Try to extract JSON-like structures
            start_idx = cleaned.find('{')
            if start_idx != -1:
                # Find matching closing brace
                brace_count = 0
                end_idx = start_idx
                for i in range(start_idx, len(cleaned)):
                    if cleaned[i] == '{':
                        brace_count += 1
                    elif cleaned[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i + 1
                            break
                
                if end_idx > start_idx:
                    json_str = cleaned[start_idx:end_idx]
                    try:
                        metrics = json.loads(json_str)
                        
                        # If we have test results, add metrics to the last one
                        if analysis.test_results and isinstance(metrics, dict):
                            # Check if this contains performance metrics
                            perf_keys = {'tPROG', 'tBERS', 'tREAD', 'tRSNAP', 'tPBSY'}
                            if any(key in metrics for key in perf_keys):
                                analysis.test_results[-1].performance_metrics.update(metrics)
                    except json.JSONDecodeError:
                        pass  # Not valid JSON, skip
        except Exception:
            pass  # Silently skip if extraction fails
    
    def generate_summary_report(self, analysis: TraceAnalysis) -> str:
        """
        Generate a human-readable summary report.
        
        Args:
            analysis: TraceAnalysis object
            
        Returns:
            Formatted summary string
        """
        lines = []
        lines.append("=" * 80)
        lines.append("TRACE FILE ANALYSIS SUMMARY")
        lines.append("=" * 80)
        lines.append("")
        
        # Overall statistics
        lines.append("OVERALL STATISTICS:")
        lines.append(f"  Total Tests:     {analysis.total_tests}")
        lines.append(f"  Passed:          {analysis.passed_tests}")
        lines.append(f"  Failed:          {analysis.failed_tests}")
        lines.append(f"  Pass Rate:       {analysis.pass_rate:.2f}%")
        lines.append("")
        
        # Timing information
        if analysis.start_time and analysis.end_time:
            lines.append("TIMING INFORMATION:")
            lines.append(f"  Start Time:      {analysis.start_time}")
            lines.append(f"  End Time:        {analysis.end_time}")
            lines.append(f"  Duration:        {analysis.duration_seconds:.2f} seconds")
            lines.append("")
        
        # Flows executed
        if analysis.flows_executed:
            lines.append("FLOWS EXECUTED:")
            for flow in analysis.flows_executed:
                lines.append(f"  - {flow}")
            lines.append("")
        
        # Failed tests detail
        failed_tests = [t for t in analysis.test_results if t.result == 'FAIL']
        if failed_tests:
            lines.append("FAILED TESTS:")
            for test in failed_tests:
                lines.append(f"  - {test.test_name}")
                if test.timestamp:
                    lines.append(f"    Time: {test.timestamp}")
                if test.command_times:
                    avg_time = sum(test.command_times) / len(test.command_times)
                    lines.append(f"    Avg Command Time: {avg_time:.6f}s")
            lines.append("")
        
        # Performance metrics summary
        tests_with_metrics = [
            t for t in analysis.test_results if t.performance_metrics
        ]
        if tests_with_metrics:
            lines.append("PERFORMANCE METRICS:")
            lines.append(f"  Tests with metrics: {len(tests_with_metrics)}")
            
            # Aggregate metrics
            all_metrics = {}
            for test in tests_with_metrics:
                for key, value in test.performance_metrics.items():
                    if key not in all_metrics:
                        all_metrics[key] = []
                    all_metrics[key].append(value)
            
            for metric_name, values in all_metrics.items():
                lines.append(f"  {metric_name}: {len(values)} occurrences")
            lines.append("")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def export_to_dict(self, analysis: TraceAnalysis) -> Dict:
        """
        Export analysis to dictionary format for JSON serialization.
        
        Args:
            analysis: TraceAnalysis object
            
        Returns:
            Dictionary representation
        """
        return {
            'summary': {
                'total_tests': analysis.total_tests,
                'passed_tests': analysis.passed_tests,
                'failed_tests': analysis.failed_tests,
                'pass_rate': analysis.pass_rate,
                'duration_seconds': analysis.duration_seconds,
                'start_time': analysis.start_time.isoformat() if analysis.start_time else None,
                'end_time': analysis.end_time.isoformat() if analysis.end_time else None,
            },
            'flows_executed': analysis.flows_executed,
            'test_results': [
                {
                    'test_name': t.test_name,
                    'result': t.result,
                    'timestamp': t.timestamp.isoformat() if t.timestamp else None,
                    'command_times': t.command_times,
                    'performance_metrics': t.performance_metrics,
                    'flow_file': t.flow_file,
                    'error_message': t.error_message,
                }
                for t in analysis.test_results
            ]
        }


# Convenience function for quick analysis
def analyze_trace_file(trace_file_path: str) -> TraceAnalysis:
    """
    Quick analysis of a trace file.
    
    Args:
        trace_file_path: Path to the DispatcherDebug*.txt file
        
    Returns:
        TraceAnalysis object
    """
    analyzer = TraceAnalyzer()
    return analyzer.analyze_file(trace_file_path)


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python trace_analyzer.py <trace_file_path>")
        sys.exit(1)
    
    trace_path = sys.argv[1]
    print(f"Analyzing trace file: {trace_path}")
    print()
    
    analyzer = TraceAnalyzer()
    analysis = analyzer.analyze_file(trace_path)
    
    # Print summary report
    print(analyzer.generate_summary_report(analysis))
    
    # Optionally export to JSON
    if len(sys.argv) > 2 and sys.argv[2] == '--json':
        import json
        output_path = trace_path.replace('.txt', '_analysis.json')
        with open(output_path, 'w') as f:
            json.dump(analyzer.export_to_dict(analysis), f, indent=2)
        print(f"\nJSON export saved to: {output_path}")
