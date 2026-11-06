from pathlib import Path
import re

TARGET = Path("tools/live_opportunity_scoring_8c2.py")
if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET}")

text = TARGET.read_text(encoding="utf-8")

import_line = (
    "from app.ai.canonical_technical import "
    "build_canonical_technical_input\n"
)
if import_line not in text:
    # Insert before first app.ai import.
    match = re.search(r"^from app\.ai\.", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit("Cannot find app.ai import block in live tool")
    text = text[:match.start()] + import_line + text[match.start():]

if "canonical_technical = build_canonical_technical_input(" not in text:
    marker = "    scoring_result = OpportunityScoringService("
    if marker not in text:
        raise SystemExit("Cannot find scoring_result construction in live tool")
    block = (
        "    canonical_technical = build_canonical_technical_input(ticker)\n\n"
    )
    text = text.replace(marker, block + marker, 1)

if "canonical_technical_input=canonical_technical" not in text:
    # Find the score() call attached to scoring_result and inject after first
    # research positional argument. This is intentionally narrow.
    start = text.find("    scoring_result = OpportunityScoringService(")
    if start < 0:
        raise SystemExit("Cannot locate scoring_result block")
    end = text.find("\n\n", start)
    if end < 0:
        end = min(len(text), start + 1200)
    block = text[start:end]

    score_pos = block.find(").score(")
    if score_pos < 0:
        raise SystemExit("Cannot locate .score( call in scoring_result block")

    after = block[score_pos:]
    lines = after.splitlines()
    inserted = False
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if (
            stripped
            and not stripped.startswith("#")
            and "=" not in stripped
            and stripped.endswith(",")
        ):
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines.insert(
                i + 1,
                indent + "canonical_technical_input=canonical_technical,",
            )
            inserted = True
            break

    if not inserted:
        raise SystemExit(
            "Could not safely identify research positional argument in score()"
        )

    new_after = "\n".join(lines)
    block = block[:score_pos] + new_after
    text = text[:start] + block + text[end:]

TARGET.write_text(text, encoding="utf-8")
print("Applied AI-8C.3b.3 live-tool canonical technical integration.")
