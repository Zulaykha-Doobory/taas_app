# TaaS Prototype — IR Pipeline Vertical Slice

Proves the core architectural bet: **one Intermediate Representation (IR)**
that Jira stories, Excel/CSV uploads, and codebase analysis all converge on,
which then renders deterministically to real pytest + Selenium code.

## What this proves
- Source-agnostic ingestion (Jira REST shape, CSV) → identical IR shape
- Codebase analysis → edge / negative / security test cases
- All sources merge into ONE TestSuite
- LLM is swappable (StubIRGenerator now → OllamaIRGenerator later, same interface)
- IR → real, compiling, pytest-collectable Selenium code (NOT via LLM — auditable)

## Run it
    pip install pydantic
    python demo/run_pipeline.py

Outputs land in ./output:
  - suite_ir.json              the IR (what the DB + low-code UI store)
  - test_generated_suite.py    real runnable Selenium (5 tests)
  - pytest.ini                 marker registration

## Run the generated tests (needs a browser + the demo app)
    pip install pytest selenium
    cd output && pytest -v        # points Chrome at Selenoid/Grid in prod

## Swap the stub for a real local LLM
In demo/run_pipeline.py:
    from taas.ai.generator import OllamaIRGenerator
    ai = OllamaIRGenerator(model="qwen2.5-coder:14b")
That's the only change. Everything downstream is identical.

## Layout
  taas/ir/schema.py         the IR (Pydantic) — the heart of the system
  taas/ai/generator.py      swappable AI: Stub + Ollama backends
  taas/ingest/adapters.py   Jira / CSV / codebase analyzer
  taas/translate/engine.py  deterministic IR → Selenium codegen
  demo/run_pipeline.py      end-to-end orchestration

## Validation layer (added)
The IR validator (taas/ir/validator.py) is the guard rail between
"something produced IR" and "we generate Selenium." Codegen is now gated:
SeleniumTranslator.render_suite() raises IRValidationError on invalid IR.

Run the validation demo:
    python demo/run_validation.py

It feeds the validator the exact errors a real LLM produces (invented
action types, asserts with no locator, malformed selectors) and proves
each is caught BEFORE codegen, while clean IR passes through.

This matters most when you swap StubIRGenerator for OllamaIRGenerator:
malformed model output fails loudly here instead of producing broken
Selenium that explodes confusingly at runtime.

## Web server + browser UI (added)
A FastAPI server now exposes the engine over REST and serves a built-in
browser UI at the root — so localhost:8080 shows a live interface.

Install + run:
    pip install -r requirements.txt
    python -m uvicorn server.app:app --port 8080 --reload

Then open  http://localhost:8080  in your browser.
(If you saw ERR_CONNECTION_REFUSED before, it was because no server was
running yet — this is the server.)

Click the buttons to: run the full pipeline, ingest a story, analyze a
component, or submit deliberately-broken IR and watch the validation gate
reject it with an HTTP 422 (the browser never receives broken Selenium).

Endpoints: GET /health, POST /ingest/story, /ingest/csv, /analyze/code,
/validate, /generate, /pipeline/demo.

Swap to a real local LLM: in server/app.py replace
    ai = StubIRGenerator()
with
    ai = OllamaIRGenerator(model="qwen2.5-coder:14b")

## Results dashboard (added) — what you actually run
The browser UI is now a RESULTS dashboard, not a code viewer. Open
http://localhost:8080, click "Run tests", and you get:
  - summary cards (total / passed / failed / errored / pass-rate)
  - a pass/fail bar
  - a table: each test, its type, time, and the failure reason if it broke

Execution backend is swappable (taas/execute/runner.py):
  - SimulationRunner (default) — runs NOW, no browser needed, realistic results
  - SeleniumRunner — real browser execution; needs Chrome/Selenoid + a live
    target URL. One-line swap in server/app.py:
        from taas.execute.runner import SeleniumRunner
        runner = SeleniumRunner(remote_url="http://selenoid:4444/wd/hub")

New endpoints: POST /run/demo (execute + store), GET /runs (history).
