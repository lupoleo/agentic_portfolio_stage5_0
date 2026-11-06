from pathlib import Path
import re

TARGET = Path("app/ai/opportunity_scoring_service.py")

if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET.resolve()}")

text = TARGET.read_text(encoding="utf-8")

pattern = re.compile(
    r'(?ms)^    (?:#.*\n)*    _SCORING_CONTEXT_BUDGETS = \{\n'
    r'        "market_context": \d+,\n'
    r'        "fundamental_context": \d+,\n'
    r'        "technical_context": \d+,\n'
    r'        "event_context": \d+,\n'
    r'        "catalyst_assessment": \d+,\n'
    r'        "bull_case": \d+,\n'
    r'        "bear_case": \d+,\n'
    r'        "key_risks": \d+,\n'
    r'        "contradictory_evidence": \d+,\n'
    r'        "unknowns": \d+,\n'
    r'    \}\n'
)

match = pattern.search(text)
if not match:
    raise SystemExit(
        "Could not locate _SCORING_CONTEXT_BUDGETS in the current project file."
    )

print("Current budget block:")
print(match.group(0))

replacement = '''    # Keep the complete scoring prompt below the global 14k character guard
    # after semantic rubrics and canonical technical features are included.
    # Total research-field budget: 5,350 characters.
    _SCORING_CONTEXT_BUDGETS = {
        "market_context": 500,
        "fundamental_context": 800,
        "technical_context": 650,
        "event_context": 650,
        "catalyst_assessment": 650,
        "bull_case": 450,
        "bear_case": 450,
        "key_risks": 400,
        "contradictory_evidence": 400,
        "unknowns": 400,
    }
'''

text = text[:match.start()] + replacement + text[match.end():]
TARGET.write_text(text, encoding="utf-8")

print("Applied bounded prompt budgets to:")
print(TARGET.resolve())
print()
print("New total research-field budget: 5350")
