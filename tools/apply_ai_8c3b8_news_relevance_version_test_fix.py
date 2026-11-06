from pathlib import Path

TARGET = Path("tests/test_ai_news_relevance_v2.py")
if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET.resolve()}")

text = TARGET.read_text(encoding="utf-8")
old = 'assert NEWS_RELEVANCE_PROMPT_VERSION == "news-relevance-v2"'
new = 'assert NEWS_RELEVANCE_PROMPT_VERSION == "news-relevance-v3-canonical-order"'

if old not in text:
    raise SystemExit("Expected legacy version assertion not found")

text = text.replace(old, new, 1)
TARGET.write_text(text, encoding="utf-8")
print("Updated legacy news relevance version assertion.")
