from pathlib import Path
import shutil

FILES = [
    Path("tests/test_ai_research_service_coverage_integration.py"),
    Path("tests/test_ai_research_service_semantic_integration.py"),
]

OLD = '''    def infer(self, request):
        self.requests.append(request)
        output = self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)]
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="{}",
            structured_output=output.model_dump(mode="json"),
            latency_ms=1.0,
            usage={},
        )
'''

NEW = '''    def infer(self, request):
        self.requests.append(request)
        output = self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)]

        payload = output.model_dump(mode="json")

        # AI-7D.4D.3: fake providers must honor the request output schema.
        # Repair requests are field-scoped, so project legacy full-output
        # fixtures onto only the fields accepted by the declared schema.
        schema_fields = set(request.output_schema.model_fields)
        payload = {
            key: value
            for key, value in payload.items()
            if key in schema_fields
        }

        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="{}",
            structured_output=payload,
            latency_ms=1.0,
            usage={},
        )
'''

for path in FILES:
    if not path.exists():
        raise SystemExit(f"ERROR: missing {path}")
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(
            f"ERROR: expected exactly one SequenceProvider.infer block in {path}; "
            f"found {count}. No files were modified."
        )

for path in FILES:
    backup = path.with_suffix(path.suffix + ".7d4d3fix1.bak")
    shutil.copy2(path, backup)
    text = path.read_text(encoding="utf-8").replace(OLD, NEW, 1)
    path.write_text(text, encoding="utf-8")
    print(f"PATCHED: {path}")
    print(f"BACKUP : {backup}")

print("FIX    : legacy SequenceProvider now honors request.output_schema")
print("PRODUCTION FILES MODIFIED: NO")
