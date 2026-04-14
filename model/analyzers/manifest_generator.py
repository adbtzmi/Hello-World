# -*- coding: utf-8 -*-
"""
Manifest Generator
Creates manifest/index files for collected checkout results.
"""

import os
import sys
import json
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path

# Handle imports for both standalone and module usage
try:
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalysis, TraceAnalyzer


class ManifestGenerator:
    """
    Generates manifest/index files for collected checkout results.
    
    Creates a JSON manifest that lists:
    - All collected files
    - Test case results summary
    - Quick statistics
    - File locations and metadata
    """
    
    def __init__(self):
        pass
    
    def generate_manifest(
        self,
        collection_dir: str,
        trace_analysis: Optional[TraceAnalysis] = None,
        jira_key: Optional[str] = None,
        mid: Optional[str] = None,
        sku: Optional[str] = None,
        additional_metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Generate manifest data structure.
        
        Args:
            collection_dir: Directory containing collected files
            trace_analysis: Optional TraceAnalysis object
            jira_key: JIRA issue key
            mid: Module ID
            sku: SKU identifier
            additional_metadata: Additional metadata to include
            
        Returns:
            Manifest dictionary
        """
        manifest = {
            "manifest_version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "metadata": {},
            "statistics": {},
            "files": {},
            "test_results": {}
        }
        
        # Add metadata
        if jira_key:
            manifest["metadata"]["jira_key"] = jira_key
        if mid:
            manifest["metadata"]["mid"] = mid
        if sku:
            manifest["metadata"]["sku"] = sku
        if additional_metadata:
            manifest["metadata"].update(additional_metadata)
        
        # Scan collection directory for files
        if os.path.exists(collection_dir):
            manifest["files"] = self._scan_directory(collection_dir)
        
        # Add trace analysis results if provided
        if trace_analysis:
            manifest["statistics"] = {
                "total_tests": trace_analysis.total_tests,
                "passed_tests": trace_analysis.passed_tests,
                "failed_tests": trace_analysis.failed_tests,
                "pass_rate": trace_analysis.pass_rate,
                "duration_seconds": trace_analysis.duration_seconds,
                "start_time": trace_analysis.start_time.isoformat() if trace_analysis.start_time else None,
                "end_time": trace_analysis.end_time.isoformat() if trace_analysis.end_time else None,
            }
            
            manifest["test_results"] = {
                "flows_executed": trace_analysis.flows_executed,
                "failed_tests": [
                    {
                        "test_name": t.test_name,
                        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                        "flow_file": t.flow_file
                    }
                    for t in trace_analysis.test_results if t.result == 'FAIL'
                ]
            }
        
        return manifest
    
    def _scan_directory(self, directory: str) -> Dict:
        """
        Scan directory and catalog all files.
        
        Args:
            directory: Directory to scan
            
        Returns:
            Dictionary of files with metadata
        """
        files = {}
        
        try:
            for root, dirs, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, directory)
                    
                    # Get file metadata
                    try:
                        stat = os.stat(filepath)
                        files[rel_path] = {
                            "size_bytes": stat.st_size,
                            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "file_type": self._get_file_type(filename)
                        }
                    except Exception as e:
                        files[rel_path] = {
                            "error": str(e)
                        }
        except Exception as e:
            print(f"Error scanning directory: {e}")
        
        return files
    
    def _get_file_type(self, filename: str) -> str:
        """Determine file type from extension."""
        ext = os.path.splitext(filename)[1].lower()
        
        type_map = {
            '.txt': 'trace',
            '.db': 'database',
            '.docx': 'validation_document',
            '.json': 'json_data',
            '.xml': 'xml_data',
            '.log': 'log',
            '.zip': 'archive',
        }
        
        return type_map.get(ext, 'unknown')
    
    def save_manifest(
        self,
        manifest: Dict,
        output_path: str
    ) -> bool:
        """
        Save manifest to JSON file.
        
        Args:
            manifest: Manifest dictionary
            output_path: Path to save manifest file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving manifest: {e}")
            return False
    
    def generate_and_save(
        self,
        collection_dir: str,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Generate and save manifest in one step.
        
        Args:
            collection_dir: Directory containing collected files
            output_path: Path to save manifest file
            **kwargs: Additional parameters
            
        Returns:
            True if successful, False otherwise
        """
        manifest = self.generate_manifest(collection_dir, **kwargs)
        return self.save_manifest(manifest, output_path)
    
    def generate_from_trace_file(
        self,
        collection_dir: str,
        trace_file_path: str,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Generate manifest with trace analysis in one step.
        
        Args:
            collection_dir: Directory containing collected files
            trace_file_path: Path to trace file for analysis
            output_path: Path to save manifest file
            **kwargs: Additional parameters
            
        Returns:
            True if successful, False otherwise
        """
        # Analyze trace file
        analyzer = TraceAnalyzer()
        trace_analysis = analyzer.analyze_file(trace_file_path)
        
        # Generate and save manifest
        return self.generate_and_save(
            collection_dir=collection_dir,
            output_path=output_path,
            trace_analysis=trace_analysis,
            **kwargs
        )
    
    def generate_quick_summary(self, manifest: Dict) -> str:
        """
        Generate a quick text summary from manifest.
        
        Args:
            manifest: Manifest dictionary
            
        Returns:
            Formatted summary text
        """
        lines = []
        
        lines.append("=" * 60)
        lines.append("CHECKOUT RESULTS MANIFEST SUMMARY")
        lines.append("=" * 60)
        lines.append("")
        
        # Metadata
        if manifest.get("metadata"):
            lines.append("METADATA:")
            for key, value in manifest["metadata"].items():
                lines.append(f"  {key}: {value}")
            lines.append("")
        
        # Statistics
        if manifest.get("statistics"):
            stats = manifest["statistics"]
            lines.append("TEST STATISTICS:")
            lines.append(f"  Total Tests:  {stats.get('total_tests', 'N/A')}")
            lines.append(f"  Passed:       {stats.get('passed_tests', 'N/A')}")
            lines.append(f"  Failed:       {stats.get('failed_tests', 'N/A')}")
            lines.append(f"  Pass Rate:    {stats.get('pass_rate', 'N/A'):.2f}%")
            lines.append("")
        
        # Files
        if manifest.get("files"):
            lines.append("COLLECTED FILES:")
            lines.append(f"  Total Files: {len(manifest['files'])}")
            
            # Group by type
            by_type = {}
            for filename, metadata in manifest["files"].items():
                file_type = metadata.get("file_type", "unknown")
                if file_type not in by_type:
                    by_type[file_type] = []
                by_type[file_type].append(filename)
            
            for file_type, files in by_type.items():
                lines.append(f"  {file_type}: {len(files)} file(s)")
            lines.append("")
        
        # Failed tests
        if manifest.get("test_results", {}).get("failed_tests"):
            failed = manifest["test_results"]["failed_tests"]
            lines.append(f"FAILED TESTS: {len(failed)}")
            for test in failed[:5]:  # Show first 5
                lines.append(f"  - {test['test_name']}")
            if len(failed) > 5:
                lines.append(f"  ... and {len(failed) - 5} more")
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)


# Convenience function
def generate_manifest(
    collection_dir: str,
    output_path: str,
    trace_file_path: Optional[str] = None,
    **kwargs
) -> bool:
    """
    Quick function to generate manifest.
    
    Args:
        collection_dir: Directory containing collected files
        output_path: Path to save manifest file
        trace_file_path: Optional path to trace file for analysis
        **kwargs: Additional parameters
        
    Returns:
        True if successful, False otherwise
    """
    generator = ManifestGenerator()
    
    if trace_file_path:
        return generator.generate_from_trace_file(
            collection_dir, trace_file_path, output_path, **kwargs
        )
    else:
        return generator.generate_and_save(collection_dir, output_path, **kwargs)


if __name__ == '__main__':
    # Example usage
    if len(sys.argv) < 3:
        print("Usage: python manifest_generator.py <collection_dir> <output_file> [trace_file] [jira_key] [mid] [sku]")
        sys.exit(1)
    
    collection_dir = sys.argv[1]
    output_file = sys.argv[2]
    trace_file = sys.argv[3] if len(sys.argv) > 3 else None
    jira_key = sys.argv[4] if len(sys.argv) > 4 else None
    mid = sys.argv[5] if len(sys.argv) > 5 else None
    sku = sys.argv[6] if len(sys.argv) > 6 else None
    
    print(f"Generating manifest...")
    print(f"  Collection dir: {collection_dir}")
    print(f"  Output: {output_file}")
    if trace_file:
        print(f"  Trace file: {trace_file}")
    if jira_key:
        print(f"  JIRA: {jira_key}")
    if mid:
        print(f"  MID: {mid}")
    if sku:
        print(f"  SKU: {sku}")
    print()
    
    success = generate_manifest(
        collection_dir=collection_dir,
        output_path=output_file,
        trace_file_path=trace_file,
        jira_key=jira_key,
        mid=mid,
        sku=sku
    )
    
    if success:
        print(f"[SUCCESS] Manifest saved to: {output_file}")
        
        # Print quick summary
        with open(output_file, 'r') as f:
            manifest = json.load(f)
        
        generator = ManifestGenerator()
        summary = generator.generate_quick_summary(manifest)
        print()
        print(summary)
    else:
        print(f"[FAILED] Failed to generate manifest")
        sys.exit(1)
