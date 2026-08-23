"""Alias module kept for the layout named in the project brief.

The implementation lives in manual_import.py, because the browser-export and
CSV/clipboard paths share all of their normalization logic.
"""
from src.market.collectors.manual_import import (  # noqa: F401
    ClipboardListingCollector,
    JsonExportCollector,
    ManualCsvCollector,
    csv_template,
)

__all__ = [
    "JsonExportCollector",
    "ManualCsvCollector",
    "ClipboardListingCollector",
    "csv_template",
]
