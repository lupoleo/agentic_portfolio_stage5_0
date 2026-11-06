from pathlib import Path

path = Path("app/ai/research_service.py")
text = path.read_text(encoding="utf-8")

# Fix the canonical AI-4A build_inference_record API.
text = text.replace(
"""build_inference_record(
            request=request,
            response=response,""",
"""build_inference_record(
            request,
            response,""",
)
text = text.replace("            now=now,\n        )", "            timestamp=now,\n        )", 1)

# Preserve the established empty-evidence error contract used by legacy tests.
text = text.replace(
    'raise ValueError("research evidence must not be empty")',
    'raise ValueError("At least one evidence item is required")',
)

path.write_text(text, encoding="utf-8")
compile(text, str(path), "exec")
print("Patched:", path)
print("Syntax check: OK")
