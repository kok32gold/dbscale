"""Output: human-readable terminal reports and machine-readable JSON."""

from dbscale.reporting.json_report import load_result, write_result
from dbscale.reporting.terminal import render_report, render_schema

__all__ = ["load_result", "render_report", "render_schema", "write_result"]
