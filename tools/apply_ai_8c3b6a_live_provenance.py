from pathlib import Path

TARGET = Path("tools/live_opportunity_scoring_8c2.py")
if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET.resolve()}")

text = TARGET.read_text(encoding="utf-8")

if "=== RESEARCH PRESENCE / LATENCY PROVENANCE ===" in text:
    print("Research presence/latency provenance already installed.")
    raise SystemExit(0)

marker = '    print("\\n=== AI-8C.2 LIVE OPPORTUNITY SCORING ===")\n'
if marker not in text:
    raise SystemExit("Could not find live opportunity scoring header print.")

block = (
'    print("\\n=== RESEARCH PRESENCE / LATENCY PROVENANCE ===")\n'
'    research_diag = research_result.diagnostics\n'
'    print("Required contexts:          " f"{research_diag.get(\'required_context_fields\', [])}")\n'
'    print("Initial missing contexts:   " f"{research_diag.get(\'initial_missing_required_contexts\', [])}")\n'
'    print("Final missing contexts:     " f"{research_diag.get(\'final_missing_required_contexts\', [])}")\n'
'    print("Presence repair attempted:  " f"{research_diag.get(\'context_presence_repair_attempted\')}")\n'
'    print("Semantic tagging wall ms:   " f"{research_diag.get(\'semantic_tagging_wall_ms\')}")\n'
'    print("Evidence quality wall ms:   " f"{research_diag.get(\'evidence_quality_wall_ms\')}")\n'
'    print("Initial research latency:   " f"{research_diag.get(\'initial_research_latency_ms\')}")\n'
'    print("Repair research latency:    " f"{research_diag.get(\'repair_research_latency_ms\')}")\n'
'    print("Research provider total ms: " f"{research_diag.get(\'research_provider_latency_total_ms\')}")\n'
'    print("Research inference count:   " f"{research_diag.get(\'research_inference_count\')}")\n'
'    print("Evidence semantic dimensions:")\n'
'    for evidence_id, dimensions in research_diag.get("evidence_semantic_dimensions", {}).items():\n'
'        print(f"  {evidence_id}: {dimensions}")\n'
'    print("=== END RESEARCH PRESENCE / LATENCY PROVENANCE ===")\n\n'
)

text = text.replace(marker, block + marker, 1)
TARGET.write_text(text, encoding="utf-8")
print("Installed AI-8C.3b.6a research provenance diagnostics.")
