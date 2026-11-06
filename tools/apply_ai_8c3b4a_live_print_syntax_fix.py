from pathlib import Path

TARGET = Path("tools/live_opportunity_scoring_8c2.py")
if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET.resolve()}")

text = TARGET.read_text(encoding="utf-8")

# Fix the exact malformed diagnostic print introduced by AI-8C.3b.4a.
bad = '''    print("
=== SCORABILITY PROVENANCE ===")
'''
good = '''    print("\\n=== SCORABILITY PROVENANCE ===")
'''

if bad not in text:
    raise SystemExit(
        "Malformed provenance print block not found. "
        "Open tools/live_opportunity_scoring_8c2.py around line 155."
    )

text = text.replace(bad, good, 1)
TARGET.write_text(text, encoding="utf-8")

print("Fixed AI-8C.3b.4a live provenance print syntax.")
