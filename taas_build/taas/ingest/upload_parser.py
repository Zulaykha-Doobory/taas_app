"""
File Upload Parser — Excel (.xlsx) and CSV -> IR TestCases.

Clients fill in a simple spreadsheet with their test steps.
This module reads it and converts every row into a proper IR TestCase
that the execution engine can run directly.

Supported column names (case-insensitive, order doesn't matter):
  test_name       | name of the test (rows with the same name = one test)
  category        | happy_path / negative / edge_case / security  (optional)
  url             | the path to navigate to e.g. /login
  action          | navigate / fill / click / assert_text / assert_visible /
                    assert_url / select / wait / screenshot / security_scan
  locator_type    | id / name / css / xpath / link_text
  locator_value   | the actual selector e.g. "username" or ".flash"
  input_value     | what to type (for fill/select actions)
  expected        | what to assert (for assert_* actions)
  description     | optional human note shown in the report

Example rows that form one test:
  test_name    | url    | action      | locator_type | locator_value        | expected
  Valid login  | /login | fill        | id           | username             |
  Valid login  |        | fill        | id           | password             |
  Valid login  |        | click       | css          | button[type=submit]  |
  Valid login  |        | assert_text | css          | .flash               | You logged in
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import List, Dict, Any

from taas.ir.schema import (
    TestCase, TestStep, TestCategory, ActionType, Locator
)


# ---- column name normalisation (handle human typos/variations) --------
_COL_ALIASES = {
    "test_name": ["test_name", "name", "test", "testname", "test name"],
    "category":  ["category", "cat", "type", "test_type"],
    "url":       ["url", "path", "navigate", "page", "navigate_to"],
    "action":    ["action", "step", "action_type"],
    "locator_type":  ["locator_type", "by", "selector_type", "locator type"],
    "locator_value": ["locator_value", "selector", "element", "locator value"],
    "input_value":   ["input_value", "value", "input", "data", "text"],
    "expected":      ["expected", "assert", "expected_text", "expected text"],
    "description":   ["description", "desc", "note", "notes"],
}

def _norm_headers(raw_headers: List[str]) -> Dict[str, str]:
    """Map raw header -> canonical column name."""
    mapping: Dict[str, str] = {}
    for raw in raw_headers:
        key = raw.strip().lower().replace("-", "_").replace(" ", "_")
        for canonical, aliases in _COL_ALIASES.items():
            if key in aliases:
                mapping[raw] = canonical
                break
    return mapping


def _parse_action(raw: str) -> ActionType:
    raw = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        return ActionType(raw)
    except ValueError:
        # common shorthand aliases
        aliases = {
            "nav": ActionType.NAVIGATE, "go": ActionType.NAVIGATE,
            "type": ActionType.FILL, "enter": ActionType.FILL,
            "press": ActionType.CLICK, "tap": ActionType.CLICK,
            "check": ActionType.ASSERT_TEXT, "verify": ActionType.ASSERT_TEXT,
            "assert": ActionType.ASSERT_TEXT,
            "visible": ActionType.ASSERT_VISIBLE,
            "screenshot": ActionType.SCREENSHOT,
            "snap": ActionType.SCREENSHOT,
            "scan": ActionType.SECURITY_SCAN,
        }
        if raw in aliases:
            return aliases[raw]
        raise ValueError(f"Unknown action '{raw}'. "
                         f"Use: {[a.value for a in ActionType]}")


def _parse_category(raw: str) -> TestCategory:
    raw = (raw or "happy_path").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        return TestCategory(raw)
    except ValueError:
        aliases = {
            "happy": TestCategory.HAPPY_PATH, "positive": TestCategory.HAPPY_PATH,
            "neg": TestCategory.NEGATIVE, "bad": TestCategory.NEGATIVE,
            "edge": TestCategory.EDGE_CASE, "boundary": TestCategory.EDGE_CASE,
            "sec": TestCategory.SECURITY, "security": TestCategory.SECURITY,
        }
        return aliases.get(raw, TestCategory.HAPPY_PATH)


def _rows_to_cases(rows: List[Dict[str, str]], source: str) -> List[TestCase]:
    """Group rows by test_name, build one TestCase per group."""
    groups: Dict[str, List[Dict]] = defaultdict(list)
    order: List[str] = []
    for row in rows:
        name = (row.get("test_name") or "").strip()
        if not name:
            continue
        if name not in groups:
            order.append(name)
        groups[name].append(row)

    cases: List[TestCase] = []
    for name in order:
        group = groups[name]
        category = _parse_category(group[0].get("category", ""))
        steps: List[TestStep] = []

        for row in group:
            raw_action = (row.get("action") or "").strip()
            if not raw_action:
                continue
            try:
                action = _parse_action(raw_action)
            except ValueError as e:
                raise ValueError(f"Test '{name}': {e}") from e

            loc_type  = (row.get("locator_type") or "").strip().lower()
            loc_value = (row.get("locator_value") or "").strip()
            locator   = Locator(strategy=loc_type, value=loc_value) \
                        if loc_type and loc_value else None

            # value comes from input_value OR url (for navigate) OR expected
            value = (row.get("input_value") or row.get("url") or
                     row.get("expected") or "").strip() or None

            # For assert actions, expected column wins over input_value
            if action in (ActionType.ASSERT_TEXT, ActionType.ASSERT_URL,
                          ActionType.ASSERT_VISIBLE):
                value = (row.get("expected") or row.get("input_value") or "").strip() or None

            steps.append(TestStep(
                action=action, locator=locator, value=value,
                description=(row.get("description") or "").strip() or None,
            ))

        if steps:
            cases.append(TestCase(
                name=name, category=category, source=source,
                tags=["upload"], steps=steps,
            ))
    return cases


class UploadParser:
    """Parse uploaded Excel or CSV files into IR TestCases."""

    def parse_csv_bytes(self, data: bytes, source: str = "upload:csv") -> List[TestCase]:
        text = data.decode("utf-8-sig")  # handle BOM from Excel-saved CSV
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValueError("CSV has no headers.")
        mapping = _norm_headers(list(reader.fieldnames))
        rows = []
        for raw_row in reader:
            row = {mapping.get(k, k.lower()): v for k, v in raw_row.items()}
            rows.append(row)
        return _rows_to_cases(rows, source)

    def parse_excel_bytes(self, data: bytes, source: str = "upload:excel") -> List[TestCase]:
        import openpyxl, io as _io
        wb = openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            raise ValueError("Excel file is empty.")
        headers = [str(c or "").strip() for c in rows_raw[0]]
        mapping = _norm_headers(headers)
        rows = []
        for raw_row in rows_raw[1:]:
            row = {}
            for i, val in enumerate(raw_row):
                if i < len(headers):
                    canonical = mapping.get(headers[i], headers[i].lower())
                    row[canonical] = str(val or "").strip()
            rows.append(row)
        return _rows_to_cases(rows, source)
