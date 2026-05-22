"""
End-to-end pipeline demo.

Proves the architecture's core claim: three different inputs
(Jira story, Excel/CSV sheet, codebase analysis) all converge on ONE IR
TestSuite, which renders to ONE real pytest+Selenium file.

Run:  python demo/run_pipeline.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taas.ir.schema import TestSuite
from taas.ai.generator import StubIRGenerator           # swap -> OllamaIRGenerator
from taas.ingest.adapters import (
    JiraConnector, FileIngestor, CodebaseAnalyzer,
)
from taas.translate.engine import SeleniumTranslator

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
BASE_URL = "https://demo-app.local"


def banner(t):
    print("\n" + "=" * 64 + f"\n  {t}\n" + "=" * 64)


def main():
    ai = StubIRGenerator()
    suite = TestSuite(suite_name="Password Reset & Auth", base_url=BASE_URL)

    # ---- 1. INGEST FROM JIRA (shape-accurate mocked payload) --------
    banner("1. Ingest user story from Jira (REST shape)")
    jira_payload = {
        "key": "PROJ-123",
        "fields": {
            "summary": "User can reset password via email link",
            "description": (
                "- User can enter their email address\n"
                "- User can click the reset button\n"
                "- User should see a confirmation message: Reset link sent\n"
            ),
        },
    }
    story = JiraConnector().fetch_story(jira_payload)
    print(f"  pulled {story.key}: {story.title}")
    print(f"  acceptance criteria parsed: {len(story.acceptance_criteria)}")
    story_cases = ai.story_to_ir(story.title, story.acceptance_criteria, story.source)
    suite.cases += story_cases
    print(f"  -> {len(story_cases)} IR test case(s) from story")

    # ---- 2. INGEST FROM EXCEL/CSV -----------------------------------
    banner("2. Ingest extra requirement from CSV upload")
    csv_text = (
        "key,title,acceptance_criteria\n"
        "CSV-1,Login with valid creds,"
        '"User can enter email; User can enter password; '
        'User can click submit; User should see Dashboard"\n'
    )
    csv_stories = FileIngestor().parse_csv(csv_text)
    for s in csv_stories:
        print(f"  parsed {s.key}: {s.title} ({len(s.acceptance_criteria)} AC)")
        suite.cases += ai.story_to_ir(s.title, s.acceptance_criteria, s.source)

    # ---- 3. CODEBASE ANALYSIS -> edge/negative/security -------------
    banner("3. Analyze codebase -> edge, negative & security tests")
    components = CodebaseAnalyzer().analyze([
        {"name": "LoginForm", "fields": ["email", "password"],
         "endpoint": "/api/auth/login"},
    ])
    for comp in components:
        print(f"  component: {comp.name} fields={comp.fields} -> {comp.endpoint}")
        code_cases = ai.code_to_ir(comp.name, comp.fields, comp.endpoint, comp.source)
        suite.cases += code_cases
        for c in code_cases:
            print(f"     + [{c.category.value}] {c.name}")

    # ---- 4. MERGE + COVERAGE ----------------------------------------
    banner("4. Merged suite coverage (feeds homepage metrics)")
    print("  " + json.dumps(suite.coverage_summary(), indent=2).replace("\n", "\n  "))

    # ---- 5. PERSIST IR (what the DB / low-code UI stores) -----------
    ir_path = os.path.join(OUT, "suite_ir.json")
    with open(ir_path, "w") as f:
        f.write(suite.model_dump_json(indent=2))
    print(f"\n  IR written -> {ir_path}")

    # ---- 6. TRANSLATE IR -> REAL SELENIUM ---------------------------
    banner("5. Translate IR -> pytest + Selenium (deterministic)")
    code = SeleniumTranslator().render_suite(suite)
    test_path = os.path.join(OUT, "test_generated_suite.py")
    with open(test_path, "w") as f:
        f.write(code)
    print(f"  Selenium suite written -> {test_path}")
    print(f"  ({code.count('def test_')} runnable test functions generated)")

    banner("Pipeline complete")
    print("  Jira + CSV + Codebase  ->  one IR  ->  one runnable suite.")


if __name__ == "__main__":
    main()
