# -*- coding: utf-8 -*-
"""
model/analyzers — Analysis modules for BENTO checkout result processing.

Modules:
    trace_analyzer          — Parse DispatcherDebug trace files for test results
    validation_doc_populator — Populate validation document templates
    spool_summary_generator — Generate spool summary reports
    manifest_generator      — Generate file manifests for collected results
    risk_assessor           — Rule-based risk assessment
    auto_consolidator       — Orchestrate all analysis modules
    ai_checkout_validator   — AI-powered checkout validation framework
    ai_risk_assessor        — AI-enhanced risk assessment
    force_fail_generator    — Generate force-fail configurations
"""

from model.analyzers.trace_analyzer import TraceAnalyzer, TraceAnalysis, TestResult
from model.analyzers.validation_doc_populator import ValidationDocumentPopulator
from model.analyzers.spool_summary_generator import SpoolSummaryGenerator
from model.analyzers.manifest_generator import ManifestGenerator
from model.analyzers.risk_assessor import RiskAssessor, RiskAssessment, RiskLevel
from model.analyzers.auto_consolidator import AutoConsolidator
from model.analyzers.ai_checkout_validator import AICheckoutValidator
from model.analyzers.ai_risk_assessor import AIRiskAssessor

__all__ = [
    "TraceAnalyzer",
    "TraceAnalysis",
    "TestResult",
    "ValidationDocumentPopulator",
    "SpoolSummaryGenerator",
    "ManifestGenerator",
    "RiskAssessor",
    "RiskAssessment",
    "RiskLevel",
    "AutoConsolidator",
    "AICheckoutValidator",
    "AIRiskAssessor",
]
