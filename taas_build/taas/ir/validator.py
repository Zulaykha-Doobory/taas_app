"""
IR Validation Layer.

The guard rail between "something produced IR" and "we generate Selenium from it".
Runs BEFORE codegen. Two reasons it exists:

  1. When the AI step is a real LLM, malformed IR is routine, not rare:
     missing locators, invented action types, asserts with nothing to assert,
     selectors that are obviously broken. This rejects them with a clear
     message instead of emitting Selenium that explodes at runtime.

  2. It encodes the per-action contract in ONE place, so the translator can
     trust its input and stay simple.

Design choice: validate raw dicts, not just Pydantic objects. The LLM emits
JSON; we want to catch "action='clcik'" with a helpful error, not a Pydantic
enum traceback. So validation runs on the dict, then we construct the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

from taas.ir.schema import ActionType


class Severity(str, Enum):
    ERROR = "error"      # blocks codegen
    WARNING = "warning"  # allowed, but surfaced to the user/UI


@dataclass
class Issue:
    severity: Severity
    path: str            # e.g. "case[2].step[4]" so the UI can highlight it
    code: str            # machine-readable, e.g. "missing_locator"
    message: str         # human-readable


@dataclass
class ValidationResult:
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def add(self, sev, path, code, msg):
        self.issues.append(Issue(sev, path, code, msg))


# --- the per-action contract: what each action REQUIRES ----------------
# (action -> needs_locator, needs_value)
_CONTRACT: Dict[str, Dict[str, bool]] = {
    ActionType.NAVIGATE.value:       {"locator": False, "value": True},
    ActionType.FILL.value:           {"locator": True,  "value": True},
    ActionType.CLICK.value:          {"locator": True,  "value": False},
    ActionType.SELECT.value:         {"locator": True,  "value": True},
    ActionType.WAIT.value:           {"locator": True,  "value": False},
    ActionType.ASSERT_TEXT.value:    {"locator": True,  "value": True},
    ActionType.ASSERT_VISIBLE.value: {"locator": True,  "value": False},
    ActionType.ASSERT_URL.value:     {"locator": False, "value": True},
    ActionType.SCREENSHOT.value:     {"locator": False, "value": False},
    ActionType.SECURITY_SCAN.value:  {"locator": False, "value": True},
}

_VALID_STRATEGIES = {"id", "name", "css", "xpath", "link_text"}
_ASSERT_ACTIONS = {
    ActionType.ASSERT_TEXT.value, ActionType.ASSERT_VISIBLE.value,
    ActionType.ASSERT_URL.value,
}


class IRValidator:
    """Validate raw IR (list of test-case dicts) before model construction."""

    def validate_suite(self, cases: List[Dict[str, Any]]) -> ValidationResult:
        res = ValidationResult()
        if not cases:
            res.add(Severity.ERROR, "suite", "empty_suite",
                    "Suite has no test cases.")
            return res
        seen_names: Dict[str, int] = {}
        for ci, case in enumerate(cases):
            self._validate_case(case, ci, res, seen_names)
        return res

    def _validate_case(self, case, ci, res, seen_names):
        cpath = f"case[{ci}]"

        name = case.get("name")
        if not name or not str(name).strip():
            res.add(Severity.ERROR, cpath, "missing_name",
                    "Test case has no name.")
        else:
            seen_names[name] = seen_names.get(name, 0) + 1
            if seen_names[name] == 2:
                res.add(Severity.WARNING, cpath, "duplicate_name",
                        f"Duplicate test name '{name}' — runners may collide.")

        steps = case.get("steps")
        if not steps:
            res.add(Severity.ERROR, cpath, "no_steps",
                    "Test case has no steps.")
            return

        has_assert = False
        for si, step in enumerate(steps):
            spath = f"{cpath}.step[{si}]"
            action = step.get("action")

            # 1. invented / missing action type (top LLM failure mode)
            if action not in _CONTRACT:
                res.add(Severity.ERROR, spath, "unknown_action",
                        f"Unknown action '{action}'. "
                        f"Valid: {sorted(_CONTRACT)}.")
                continue

            if action in _ASSERT_ACTIONS:
                has_assert = True

            rules = _CONTRACT[action]

            # 2. required locator present and well-formed?
            loc = step.get("locator")
            if rules["locator"]:
                if not loc:
                    res.add(Severity.ERROR, spath, "missing_locator",
                            f"Action '{action}' requires a locator.")
                else:
                    self._validate_locator(loc, spath, res)
            elif loc:
                res.add(Severity.WARNING, spath, "ignored_locator",
                        f"Action '{action}' ignores its locator.")

            # 3. required value present?
            val = step.get("value")
            if rules["value"] and (val is None or str(val) == ""):
                # empty string is legitimate for FILL (clearing a field)
                if not (action == ActionType.FILL.value and val == ""):
                    res.add(Severity.ERROR, spath, "missing_value",
                            f"Action '{action}' requires a value.")

            # 4. timeout sanity
            t = step.get("timeout", 10)
            if not isinstance(t, int) or t <= 0 or t > 300:
                res.add(Severity.WARNING, spath, "odd_timeout",
                        f"Timeout {t!r} is outside the sane 1-300s range.")

        # 5. semantic: a test that never asserts anything is suspect
        if not has_assert:
            res.add(Severity.WARNING, cpath, "no_assertion",
                    "Test case has no assertion — it can pass without "
                    "verifying anything.")

    def _validate_locator(self, loc, spath, res):
        if not isinstance(loc, dict):
            res.add(Severity.ERROR, spath, "bad_locator",
                    "Locator must be an object with strategy + value.")
            return
        strat = loc.get("strategy")
        if strat not in _VALID_STRATEGIES:
            res.add(Severity.ERROR, spath, "bad_strategy",
                    f"Locator strategy '{strat}' invalid. "
                    f"Use one of {sorted(_VALID_STRATEGIES)}.")
        if not loc.get("value", "").strip():
            res.add(Severity.ERROR, spath, "empty_locator_value",
                    "Locator has an empty value.")
