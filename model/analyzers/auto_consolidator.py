# -*- coding: utf-8 -*-
"""
Auto Consolidator
Integrates all analysis modules to automatically consolidate checkout results.
"""

import os
import sys
import json
from typing import Dict, List, Optional
from datetime import datetime

# Handle imports for both standalone and module usage
try:
    from model.analyzers.trace_analyzer import TraceAnalyzer
    from model.analyzers.validation_doc_populator import ValidationDocumentPopulator
    from model.analyzers.spool_summary_generator import SpoolSummaryGenerator
    from model.analyzers.manifest_generator import ManifestGenerator
    from model.analyzers.risk_assessor import RiskAssessor
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from model.analyzers.trace_analyzer import TraceAnalyzer
    from model.analyzers.validation_doc_populator import ValidationDocumentPopulator
    from model.analyzers.spool_summary_generator import SpoolSummaryGenerator
    from model.analyzers.manifest_generator import ManifestGenerator
    from model.analyzers.risk_assessor import RiskAssessor


class AutoConsolidator:
    """
    Automatically consolidates checkout results.
    
    Orchestrates all analysis modules to:
    1. Analyze trace files
    2. Generate validation documents
    3. Create spool summaries
    4. Generate manifests
    5. Perform risk assessment
    """
    
    def __init__(self, template_path: str = "template_validation.docx"):
        """
        Initialize the auto consolidator.
        
        Args:
            template_path: Path to validation document template
        """
        self.trace_analyzer = TraceAnalyzer()
        self.validation_populator = ValidationDocumentPopulator(template_path)
        self.spool_generator = SpoolSummaryGenerator()
        self.manifest_generator = ManifestGenerator()
        self.risk_assessor = RiskAssessor()
    
    def consolidate(
        self,
        collection_dir: str,
        trace_file_path: str,
        database_path: Optional[str] = None,
        jira_key: Optional[str] = None,
        mid: Optional[str] = None,
        sku: Optional[str] = None,
        expected_duration_hours: Optional[float] = None,
        baseline_trace_path: Optional[str] = None,
        generate_validation_doc: bool = True,
        generate_spool_summary: bool = True,
        generate_manifest: bool = True,
        perform_risk_assessment: bool = True
    ) -> Dict:
        """
        Perform complete auto-consolidation.
        
        Args:
            collection_dir: Directory containing collected files
            trace_file_path: Path to trace file
            database_path: Optional path to database file
            jira_key: JIRA issue key
            mid: Module ID
            sku: SKU identifier
            expected_duration_hours: Expected test duration for performance check
            baseline_trace_path: Optional baseline trace for comparison
            generate_validation_doc: Whether to generate validation document
            generate_spool_summary: Whether to generate spool summary
            generate_manifest: Whether to generate manifest
            perform_risk_assessment: Whether to perform risk assessment
            
        Returns:
            Dictionary with consolidation results
        """
        results = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "collection_dir": collection_dir,
            "trace_file": trace_file_path,
            "outputs": {},
            "errors": []
        }
        
        try:
            # Step 1: Analyze trace file
            print("Step 1: Analyzing trace file...")
            trace_analysis = self.trace_analyzer.analyze_file(trace_file_path)
            results["trace_analysis"] = {
                "total_tests": trace_analysis.total_tests,
                "passed_tests": trace_analysis.passed_tests,
                "failed_tests": trace_analysis.failed_tests,
                "pass_rate": trace_analysis.pass_rate,
                "duration_hours": trace_analysis.duration_seconds / 3600
            }
            print(f"  [OK] Analyzed {trace_analysis.total_tests} tests ({trace_analysis.pass_rate:.2f}% pass rate)")
            
            # Step 2: Generate validation document
            if generate_validation_doc:
                print("Step 2: Generating validation document...")
                validation_doc_path = os.path.join(collection_dir, "validation_document.docx")
                success = self.validation_populator.populate_document(
                    trace_analysis=trace_analysis,
                    output_path=validation_doc_path,
                    jira_key=jira_key,
                    mid=mid,
                    sku=sku,
                    trace_file_path=trace_file_path,
                    database_path=database_path
                )
                if success:
                    results["outputs"]["validation_document"] = validation_doc_path
                    print(f"  [OK] Validation document: {validation_doc_path}")
                else:
                    results["errors"].append("Failed to generate validation document")
                    print("  [ERROR] Failed to generate validation document")
            
            # Step 3: Generate spool summary
            if generate_spool_summary:
                print("Step 3: Generating spool summary...")
                spool_summary_path = os.path.join(collection_dir, "spool_summary.txt")
                success = self.spool_generator.save_summary(
                    trace_analysis=trace_analysis,
                    output_path=spool_summary_path,
                    jira_key=jira_key,
                    mid=mid,
                    sku=sku,
                    trace_file_path=trace_file_path,
                    database_path=database_path
                )
                if success:
                    results["outputs"]["spool_summary"] = spool_summary_path
                    print(f"  [OK] Spool summary: {spool_summary_path}")
                else:
                    results["errors"].append("Failed to generate spool summary")
                    print("  [ERROR] Failed to generate spool summary")
            
            # Step 4: Generate manifest
            if generate_manifest:
                print("Step 4: Generating manifest...")
                manifest_path = os.path.join(collection_dir, "manifest.json")
                success = self.manifest_generator.generate_and_save(
                    collection_dir=collection_dir,
                    output_path=manifest_path,
                    trace_analysis=trace_analysis,
                    jira_key=jira_key,
                    mid=mid,
                    sku=sku
                )
                if success:
                    results["outputs"]["manifest"] = manifest_path
                    print(f"  [OK] Manifest: {manifest_path}")
                else:
                    results["errors"].append("Failed to generate manifest")
                    print("  [ERROR] Failed to generate manifest")
            
            # Step 5: Perform risk assessment
            if perform_risk_assessment:
                print("Step 5: Performing risk assessment...")
                
                # Analyze baseline if provided
                baseline_analysis = None
                if baseline_trace_path and os.path.exists(baseline_trace_path):
                    baseline_analysis = self.trace_analyzer.analyze_file(baseline_trace_path)
                
                risk_assessment = self.risk_assessor.assess_risk(
                    trace_analysis=trace_analysis,
                    baseline_analysis=baseline_analysis,
                    expected_duration_hours=expected_duration_hours
                )
                
                # Save risk assessment report
                risk_report_path = os.path.join(collection_dir, "risk_assessment.txt")
                risk_report = self.risk_assessor.generate_report(risk_assessment)
                with open(risk_report_path, 'w', encoding='utf-8') as f:
                    f.write(risk_report)
                
                # Save risk assessment JSON
                risk_json_path = os.path.join(collection_dir, "risk_assessment.json")
                with open(risk_json_path, 'w', encoding='utf-8') as f:
                    json.dump(risk_assessment.to_dict(), f, indent=2)
                
                results["outputs"]["risk_assessment_report"] = risk_report_path
                results["outputs"]["risk_assessment_json"] = risk_json_path
                results["risk_assessment"] = {
                    "risk_level": risk_assessment.risk_level.value,
                    "risk_score": risk_assessment.risk_score
                }
                print(f"  [OK] Risk Level: {risk_assessment.risk_level.value} (Score: {risk_assessment.risk_score:.1f}/100)")
                print(f"  [OK] Risk assessment: {risk_report_path}")
            
            print("\n[SUCCESS] Auto-consolidation completed successfully!")
            
        except Exception as e:
            results["success"] = False
            results["errors"].append(str(e))
            print(f"\n[FAILED] Auto-consolidation failed: {e}")
        
        return results
    
    def consolidate_batch(
        self,
        collection_dirs: List[str],
        **common_kwargs
    ) -> Dict[str, Dict]:
        """
        Consolidate multiple checkout results in batch.
        
        Args:
            collection_dirs: List of collection directories
            **common_kwargs: Common parameters for all consolidations
            
        Returns:
            Dictionary mapping collection dir to results
        """
        batch_results = {}
        
        for collection_dir in collection_dirs:
            print(f"\n{'=' * 80}")
            print(f"Processing: {collection_dir}")
            print('=' * 80)
            
            # Find trace file in collection directory
            trace_files = []
            for root, dirs, files in os.walk(collection_dir):
                for file in files:
                    if file.startswith("DispatcherDebug") and file.endswith(".txt"):
                        trace_files.append(os.path.join(root, file))
            
            if not trace_files:
                print(f"[ERROR] No trace file found in {collection_dir}")
                batch_results[collection_dir] = {
                    "success": False,
                    "errors": ["No trace file found"]
                }
                continue
            
            trace_file = trace_files[0]  # Use first trace file found
            
            # Find database file
            database_file = None
            for root, dirs, files in os.walk(collection_dir):
                for file in files:
                    if file.endswith(".db"):
                        database_file = os.path.join(root, file)
                        break
            
            # Consolidate
            results = self.consolidate(
                collection_dir=collection_dir,
                trace_file_path=trace_file,
                database_path=database_file,
                **common_kwargs
            )
            
            batch_results[collection_dir] = results
        
        return batch_results


# Convenience function
def auto_consolidate(
    collection_dir: str,
    trace_file_path: str,
    **kwargs
) -> Dict:
    """
    Quick function to auto-consolidate checkout results.
    
    Args:
        collection_dir: Directory containing collected files
        trace_file_path: Path to trace file
        **kwargs: Additional parameters
        
    Returns:
        Consolidation results dictionary
    """
    consolidator = AutoConsolidator()
    return consolidator.consolidate(collection_dir, trace_file_path, **kwargs)


if __name__ == '__main__':
    # Example usage
    if len(sys.argv) < 3:
        print("Usage: python auto_consolidator.py <collection_dir> <trace_file> [jira_key] [mid] [sku] [expected_duration_hours]")
        sys.exit(1)
    
    collection_dir = sys.argv[1]
    trace_file = sys.argv[2]
    jira_key = sys.argv[3] if len(sys.argv) > 3 else None
    mid = sys.argv[4] if len(sys.argv) > 4 else None
    sku = sys.argv[5] if len(sys.argv) > 5 else None
    expected_duration = float(sys.argv[6]) if len(sys.argv) > 6 else None
    
    print("=" * 80)
    print("AUTO CONSOLIDATE CHECKOUT RESULT")
    print("=" * 80)
    print(f"Collection Dir: {collection_dir}")
    print(f"Trace File:     {trace_file}")
    if jira_key:
        print(f"JIRA Key:       {jira_key}")
    if mid:
        print(f"MID:            {mid}")
    if sku:
        print(f"SKU:            {sku}")
    if expected_duration:
        print(f"Expected Duration: {expected_duration} hours")
    print("=" * 80)
    print()
    
    results = auto_consolidate(
        collection_dir=collection_dir,
        trace_file_path=trace_file,
        jira_key=jira_key,
        mid=mid,
        sku=sku,
        expected_duration_hours=expected_duration
    )
    
    # Print summary
    print("\n" + "=" * 80)
    print("CONSOLIDATION SUMMARY")
    print("=" * 80)
    print(f"Status: {'SUCCESS' if results['success'] else 'FAILED'}")
    
    if results.get("trace_analysis"):
        ta = results["trace_analysis"]
        print(f"\nTest Results:")
        print(f"  Total Tests:  {ta['total_tests']}")
        print(f"  Passed:       {ta['passed_tests']}")
        print(f"  Failed:       {ta['failed_tests']}")
        print(f"  Pass Rate:    {ta['pass_rate']:.2f}%")
        print(f"  Duration:     {ta['duration_hours']:.2f} hours")
    
    if results.get("risk_assessment"):
        ra = results["risk_assessment"]
        print(f"\nRisk Assessment:")
        print(f"  Risk Level:   {ra['risk_level']}")
        print(f"  Risk Score:   {ra['risk_score']:.1f}/100")
    
    if results.get("outputs"):
        print(f"\nGenerated Files:")
        for key, path in results["outputs"].items():
            print(f"  {key}: {path}")
    
    if results.get("errors"):
        print(f"\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")
    
    print("=" * 80)
