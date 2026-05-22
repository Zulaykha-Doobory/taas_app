"""
AI provider abstraction.

The whole point of this module: the rest of the system depends on the
`IRGenerator` *interface*, never on a concrete model. Today we ship a
deterministic stub (zero dependencies, runs anywhere, fully testable).
Tomorrow you drop in `OllamaIRGenerator` -- same methods, same return type --
and nothing else in the codebase changes.

This is how the prototype proves the PIPELINE without needing a GPU.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import List

from taas.ir.schema import (
    TestCase, TestStep, TestCategory, ActionType, Locator,
)


class IRGenerator(ABC):
    """Contract every AI backend must satisfy."""

    @abstractmethod
    def story_to_ir(self, story_title: str, acceptance_criteria: List[str],
                    source: str) -> List[TestCase]:
        ...

    @abstractmethod
    def code_to_ir(self, component_name: str, fields: List[str],
                   endpoint: str, source: str) -> List[TestCase]:
        ...


class StubIRGenerator(IRGenerator):
    """
    Deterministic, rule-based stand-in for the LLM.

    It mimics the *shape* of what Qwen2.5-Coder will eventually produce:
    happy paths from acceptance criteria, plus edge / negative / security
    cases inferred from form structure. No model, no network, no cost.
    """

    # ---- STORY -> IR -------------------------------------------------
    def story_to_ir(self, story_title, acceptance_criteria, source):
        steps: List[TestStep] = [
            TestStep(action=ActionType.NAVIGATE, value="/",
                     description=f"Open app for: {story_title}")
        ]
        # Naive AC parsing: turn "user can X" criteria into click/assert pairs.
        for ac in acceptance_criteria:
            ac_low = ac.lower()
            if "enter" in ac_low or "input" in ac_low or "fill" in ac_low:
                field = self._guess_field(ac)
                steps.append(TestStep(
                    action=ActionType.FILL,
                    locator=Locator(strategy="name", value=field),
                    value="sample_value",
                    description=f"AC: {ac}",
                ))
            elif "click" in ac_low or "submit" in ac_low or "press" in ac_low:
                steps.append(TestStep(
                    action=ActionType.CLICK,
                    locator=Locator(strategy="css", value="button[type=submit]"),
                    description=f"AC: {ac}",
                ))
            else:
                # default: treat as an expected outcome -> assertion
                steps.append(TestStep(
                    action=ActionType.ASSERT_TEXT,
                    locator=Locator(strategy="css", value="body"),
                    value=self._guess_expected_text(ac),
                    description=f"AC (verify): {ac}",
                ))
        steps.append(TestStep(action=ActionType.SCREENSHOT,
                              description="Capture final state"))
        return [TestCase(
            name=f"Story happy path - {story_title}",
            category=TestCategory.HAPPY_PATH,
            source=source,
            tags=["story", "happy_path"],
            steps=steps,
        )]

    # ---- CODE -> IR --------------------------------------------------
    def code_to_ir(self, component_name, fields, endpoint, source):
        cases: List[TestCase] = []

        # Edge case: boundary / empty inputs
        edge_steps = [TestStep(action=ActionType.NAVIGATE, value="/",
                               description="Open component")]
        for f in fields:
            edge_steps.append(TestStep(
                action=ActionType.FILL,
                locator=Locator(strategy="name", value=f),
                value="",  # empty submission
                description=f"Edge: leave '{f}' empty",
            ))
        edge_steps.append(TestStep(
            action=ActionType.CLICK,
            locator=Locator(strategy="css", value="button[type=submit]"),
            description="Submit with empty fields",
        ))
        edge_steps.append(TestStep(
            action=ActionType.ASSERT_VISIBLE,
            locator=Locator(strategy="css", value=".error, [role=alert]"),
            description="Expect validation error",
        ))
        cases.append(TestCase(
            name=f"Edge - empty fields on {component_name}",
            category=TestCategory.EDGE_CASE, source=source,
            tags=["code", "edge"], steps=edge_steps,
        ))

        # Negative path: invalid input
        neg_steps = [TestStep(action=ActionType.NAVIGATE, value="/")]
        for f in fields:
            neg_steps.append(TestStep(
                action=ActionType.FILL,
                locator=Locator(strategy="name", value=f),
                value="invalid!!!",
                description=f"Negative: invalid data in '{f}'",
            ))
        neg_steps.append(TestStep(
            action=ActionType.CLICK,
            locator=Locator(strategy="css", value="button[type=submit]"),
        ))
        neg_steps.append(TestStep(
            action=ActionType.ASSERT_URL, value=endpoint,
            description="Should not navigate away on invalid input",
        ))
        cases.append(TestCase(
            name=f"Negative - invalid input on {component_name}",
            category=TestCategory.NEGATIVE, source=source,
            tags=["code", "negative"], steps=neg_steps,
        ))

        # Security: injection probe + DAST handoff
        sec_steps = [TestStep(action=ActionType.NAVIGATE, value="/")]
        for f in fields:
            sec_steps.append(TestStep(
                action=ActionType.FILL,
                locator=Locator(strategy="name", value=f),
                value="' OR '1'='1",  # classic SQLi probe
                description=f"Security: injection probe in '{f}'",
            ))
        sec_steps.append(TestStep(
            action=ActionType.CLICK,
            locator=Locator(strategy="css", value="button[type=submit]"),
        ))
        sec_steps.append(TestStep(
            action=ActionType.SECURITY_SCAN, value=endpoint,
            description="Hand off endpoint to OWASP ZAP for DAST scan",
        ))
        cases.append(TestCase(
            name=f"Security - injection probe on {component_name}",
            category=TestCategory.SECURITY, source=source,
            tags=["code", "security", "sast", "dast"], steps=sec_steps,
        ))
        return cases

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def _guess_field(text: str) -> str:
        for kw in ("email", "password", "username", "name", "search"):
            if kw in text.lower():
                return kw
        return "input_field"

    @staticmethod
    def _guess_expected_text(text: str) -> str:
        m = re.search(r"(?:see|shown|displayed|message)[\s:]+['\"]?([^'\".]+)",
                      text, re.IGNORECASE)
        return m.group(1).strip() if m else "Success"


class OllamaIRGenerator(IRGenerator):
    """
    Real LLM backend. NOT exercised in this prototype (needs a running
    Ollama + GPU), but included to show the swap is a one-class change.
    The prompts instruct the model to return IR JSON, which we validate
    against the Pydantic schema -- so a hallucinated/malformed response
    fails loudly instead of producing bad Selenium code.
    """

    def __init__(self, model: str = "qwen2.5-coder:14b",
                 host: str = "http://localhost:11434"):
        self.model = model
        self.host = host

    def _call(self, prompt: str) -> str:
        import json, urllib.request
        payload = json.dumps({
            "model": self.model, "prompt": prompt,
            "format": "json", "stream": False,
        }).encode()
        req = urllib.request.Request(f"{self.host}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["response"]

    def story_to_ir(self, story_title, acceptance_criteria, source):
        prompt = self._story_prompt(story_title, acceptance_criteria)
        return self._parse(self._call(prompt), source)

    def code_to_ir(self, component_name, fields, endpoint, source):
        prompt = self._code_prompt(component_name, fields, endpoint)
        return self._parse(self._call(prompt), source)

    @staticmethod
    def _story_prompt(title, acs):
        crit = "\n".join(f"- {a}" for a in acs)
        return (
            "You are a QA engineer. Convert this user story into test cases.\n"
            "Return ONLY valid JSON: a list of objects matching the TestCase "
            "schema (name, category, source, tags, steps[]). No prose.\n\n"
            f"Story: {title}\nAcceptance criteria:\n{crit}\n"
        )

    @staticmethod
    def _code_prompt(component, fields, endpoint):
        return (
            "You are a security-aware QA engineer. Given this component, emit "
            "edge-case, negative-path, and security test cases as JSON only "
            "(list of TestCase objects). No prose.\n\n"
            f"Component: {component}\nFields: {fields}\nEndpoint: {endpoint}\n"
        )

    @staticmethod
    def _parse(raw: str, source: str) -> List[TestCase]:
        import json
        data = json.loads(raw)
        items = data if isinstance(data, list) else data.get("cases", [])
        out = []
        for item in items:
            item.setdefault("source", source)
            out.append(TestCase(**item))  # Pydantic validates -> fails loud
        return out
