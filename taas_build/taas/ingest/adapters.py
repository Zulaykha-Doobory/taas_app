"""
Requirements Ingestion + Codebase Analysis.

Each adapter normalizes its source into the SAME shape:
  story  -> (title, [acceptance_criteria])
  code   -> (component_name, [fields], endpoint)

So downstream (AI generator -> IR) never knows or cares whether the input
came from Jira, an Excel sheet, or a parsed AST. That's the abstraction
that lets all sources merge into one TestSuite.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class Story:
    key: str
    title: str
    acceptance_criteria: List[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return f"story:{self.key}"


@dataclass
class CodeComponent:
    name: str
    fields: List[str]
    endpoint: str

    @property
    def source(self) -> str:
        return f"code:{self.name}"


# --------------------------------------------------------------------------
# ALM connector (Jira / Azure DevOps)
# --------------------------------------------------------------------------
class JiraConnector:
    """
    Shape-accurate stub of a Jira REST pull. In production this hits
    GET /rest/api/3/issue/{key} with a token. Here we accept an in-memory
    payload that matches Jira's JSON shape, so the parsing logic is REAL
    even though the network call is mocked.
    """

    def fetch_story(self, issue_payload: dict) -> Story:
        key = issue_payload.get("key", "UNKNOWN")
        fields = issue_payload.get("fields", {})
        title = fields.get("summary", "Untitled")
        # AC often live in a custom field or the description as a checklist.
        desc = fields.get("description", "") or ""
        acs = self._extract_criteria(desc)
        return Story(key=key, title=title, acceptance_criteria=acs)

    @staticmethod
    def _extract_criteria(text: str) -> List[str]:
        lines = []
        for raw in text.splitlines():
            s = raw.strip(" -*\t")
            if s and (raw.strip().startswith(("-", "*")) or "should" in s.lower()
                      or "must" in s.lower() or "can" in s.lower()):
                lines.append(s)
        return lines


# --------------------------------------------------------------------------
# File parser (Excel / CSV)
# --------------------------------------------------------------------------
class FileIngestor:
    """
    Parses uploaded requirement sheets. Expected columns:
    key | title | acceptance_criteria  (criteria separated by ';' or newlines)
    Uses stdlib csv so the prototype has zero pip dependencies for this path;
    production swaps in pandas to also read .xlsx.
    """

    def parse_csv(self, csv_text: str) -> List[Story]:
        stories: List[Story] = []
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            raw_ac = row.get("acceptance_criteria", "") or ""
            acs = [a.strip() for a in raw_ac.replace("\n", ";").split(";")
                   if a.strip()]
            stories.append(Story(
                key=row.get("key", "CSV"),
                title=row.get("title", "Untitled"),
                acceptance_criteria=acs,
            ))
        return stories


# --------------------------------------------------------------------------
# Codebase analyzer (Tree-sitter stand-in)
# --------------------------------------------------------------------------
class CodebaseAnalyzer:
    """
    In production this walks the repo with Tree-sitter and extracts forms,
    fields, routes, and endpoints structurally. Here we accept a small
    descriptor to demonstrate the contract: structure in, CodeComponents out.
    The KEY POINT preserved from the architecture: extraction is structural
    (deterministic), and only the *test-case authoring* is handed to the LLM.
    """

    def analyze(self, components_descriptor: List[dict]) -> List[CodeComponent]:
        out = []
        for c in components_descriptor:
            out.append(CodeComponent(
                name=c["name"],
                fields=c.get("fields", []),
                endpoint=c.get("endpoint", "/"),
            ))
        return out
