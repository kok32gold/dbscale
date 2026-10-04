"""Deterministic analysis: turns measurements into evidence-backed findings."""

from dbscale.analysis.analyzers import (
    DEFAULT_ANALYZERS,
    AggregationAnalyzer,
    AnalysisContext,
    Analyzer,
    ExcessiveRowsScannedAnalyzer,
    ExecutionFailureAnalyzer,
    ExpensiveJoinAnalyzer,
    LargeSortAnalyzer,
    ScalingAnalyzer,
    SequentialScanAnalyzer,
    ThresholdAnalyzer,
    analyze_workload,
)
from dbscale.analysis.scaling import compute_scaling, fit_exponent

__all__ = [
    "DEFAULT_ANALYZERS",
    "AggregationAnalyzer",
    "AnalysisContext",
    "Analyzer",
    "ExecutionFailureAnalyzer",
    "ExpensiveJoinAnalyzer",
    "ExcessiveRowsScannedAnalyzer",
    "LargeSortAnalyzer",
    "ScalingAnalyzer",
    "SequentialScanAnalyzer",
    "ThresholdAnalyzer",
    "analyze_workload",
    "compute_scaling",
    "fit_exponent",
]
