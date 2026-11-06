from pathlib import Path

TARGET = Path("tools/live_opportunity_scoring_8c2.py")
if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET.resolve()}")

text = TARGET.read_text(encoding="utf-8")
if "=== SCORABILITY PROVENANCE ===" in text:
    print("Scorability provenance output already installed.")
    raise SystemExit(0)

marker = '    print("\\nDeterministic calculation:")\n'
if marker not in text:
    raise SystemExit(
        "Could not find deterministic calculation output in live tool."
    )

block = '''    print("\n=== SCORABILITY PROVENANCE ===")
    for component_name, provenance in scoring_result.diagnostics["components"].items():
        print(
            f"  {component_name.upper():12} "
            f"context={provenance['context_present']} "
            f"scorable={provenance['deterministic_scorable']} "
            f"initial={provenance['initial_model_score']} "
            f"scorability_repair={provenance['scorability_repair_attempted']} "
            f"grounding_repair={provenance['grounding_repair_attempted']} "
            f"final={provenance['final_score']}"
        )
    print("=== END SCORABILITY PROVENANCE ===")

'''

text = text.replace(marker, block + marker, 1)
TARGET.write_text(text, encoding="utf-8")
print("Installed AI-8C.3b.4a live scorability provenance.")
