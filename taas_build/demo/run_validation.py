"""
Validation demo: feed the validator the exact garbage a real LLM emits,
prove it's caught BEFORE codegen, and prove clean IR still passes.

Run:  python demo/run_validation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taas.ir.validator import IRValidator, Severity


def show(title, cases):
    res = IRValidator().validate_suite(cases)
    status = "PASS (codegen allowed)" if res.ok else "BLOCKED (codegen refused)"
    print(f"\n{'='*64}\n  {title}\n  -> {status}\n{'='*64}")
    for i in res.issues:
        mark = "✗" if i.severity == Severity.ERROR else "!"
        print(f"  {mark} [{i.severity.value:7}] {i.path:18} {i.code}")
        print(f"      {i.message}")
    if not res.issues:
        print("  (no issues)")
    return res


def main():
    # ---- A. The failure modes a real LLM actually produces ----------
    llm_garbage = [
        {  # invented action type — "clcik" typo, classic model slip
            "name": "Broken login",
            "source": "story:PROJ-9",
            "steps": [
                {"action": "navigate", "value": "/login"},
                {"action": "clcik",  # <-- hallucinated action
                 "locator": {"strategy": "css", "value": "button"}},
            ],
        },
        {  # assert with no locator, and no value to assert
            "name": "Check dashboard",
            "source": "story:PROJ-9",
            "steps": [
                {"action": "navigate", "value": "/"},
                {"action": "assert_text"},  # <-- missing locator AND value
            ],
        },
        {  # fill with a malformed locator (LLM emitted strategy 'xpaht')
            "name": "Enter email",
            "source": "code:LoginForm",
            "steps": [
                {"action": "fill",
                 "locator": {"strategy": "xpaht", "value": "//input"},
                 "value": "a@b.com"},
            ],
        },
        {  # a test that does nothing but click — never verifies anything
            "name": "Pointless test",
            "source": "manual",
            "steps": [
                {"action": "navigate", "value": "/"},
                {"action": "click",
                 "locator": {"strategy": "id", "value": "go"}},
            ],
        },
    ]
    res_bad = show("A. Raw LLM output with typical errors", llm_garbage)

    # ---- B. Clean IR (what the stub/well-behaved LLM produces) -------
    good = [
        {
            "name": "Login happy path",
            "source": "story:PROJ-9",
            "category": "happy_path",
            "steps": [
                {"action": "navigate", "value": "/login"},
                {"action": "fill",
                 "locator": {"strategy": "name", "value": "email"},
                 "value": "user@example.com"},
                {"action": "fill",
                 "locator": {"strategy": "name", "value": "password"},
                 "value": "hunter2"},
                {"action": "click",
                 "locator": {"strategy": "css", "value": "button[type=submit]"}},
                {"action": "assert_text",
                 "locator": {"strategy": "css", "value": "h1"},
                 "value": "Dashboard"},
            ],
        }
    ]
    res_good = show("B. Clean IR", good)

    # ---- C. Empty field on FILL is legitimate (not an error) --------
    edge = [
        {
            "name": "Submit empty form",
            "source": "code:LoginForm",
            "steps": [
                {"action": "navigate", "value": "/"},
                {"action": "fill",
                 "locator": {"strategy": "name", "value": "email"},
                 "value": ""},  # intentional empty -> edge case, allowed
                {"action": "click",
                 "locator": {"strategy": "css", "value": "button"}},
                {"action": "assert_visible",
                 "locator": {"strategy": "css", "value": ".error"}},
            ],
        }
    ]
    res_edge = show("C. Empty FILL is a valid edge case", edge)

    # ---- Scorecard ---------------------------------------------------
    print(f"\n{'='*64}\n  SCORECARD\n{'='*64}")
    print(f"  A (garbage):    {len(res_bad.errors)} errors, "
          f"{len(res_bad.warnings)} warnings -> blocked: {not res_bad.ok}")
    print(f"  B (clean):      {len(res_good.errors)} errors, "
          f"{len(res_good.warnings)} warnings -> passed: {res_good.ok}")
    print(f"  C (empty edge): {len(res_edge.errors)} errors, "
          f"{len(res_edge.warnings)} warnings -> passed: {res_edge.ok}")
    expected = (not res_bad.ok) and res_good.ok and res_edge.ok
    print(f"\n  Validator behaves correctly: {expected}")


if __name__ == "__main__":
    main()
