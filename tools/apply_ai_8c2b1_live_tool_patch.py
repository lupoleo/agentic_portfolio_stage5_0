from pathlib import Path

path = Path("tools/live_opportunity_scoring_8c2.py")
text = path.read_text(encoding="utf-8")

old = "scoring_result = OpportunityScoringService(local).score("
new = """scoring_result = OpportunityScoringService(
        local,
        normalize_provider_transport=True,
    ).score("""

if new in text:
    print("Live tool already patched.")
elif old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("Patched tools/live_opportunity_scoring_8c2.py")
else:
    raise SystemExit(
        "Expected OpportunityScoringService(local).score( call not found; "
        "no file was modified."
    )
