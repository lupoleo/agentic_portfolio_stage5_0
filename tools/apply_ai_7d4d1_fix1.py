from pathlib import Path
import shutil

path = Path("app/ai/research_service.py")
if not path.exists():
    raise SystemExit("ERROR: app/ai/research_service.py not found.")

src = path.read_text(encoding="utf-8")
backup = path.with_suffix(".py.7d4d1.circular.bak")
if not backup.exists():
    shutil.copy2(path, backup)

old = """from app.ai.evidence_provider import EvidenceItem
from app.ai.evidence_semantics import (
    EvidenceSemanticAssessment,
    EvidenceSemanticTaggingService,
)
from app.ai.evidence_quality import EvidenceQualityAssessment
from app.ai.evidence_quality_semantic import SemanticEvidenceQualityEvaluator
"""
if old not in src:
    raise SystemExit("ERROR: expected 7D.4D.1 top-level evidence imports not found.")

new = """from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.evidence_provider import EvidenceItem
    from app.ai.evidence_semantics import EvidenceSemanticAssessment
    from app.ai.evidence_quality import EvidenceQualityAssessment
"""
src = src.replace(old, new, 1)

old_ctor = """        evidence_semantic_tagger: EvidenceSemanticTaggingService | None = None,
        evidence_quality_evaluator: SemanticEvidenceQualityEvaluator | None = None,
"""
new_ctor = """        evidence_semantic_tagger=None,
        evidence_quality_evaluator=None,
"""
if old_ctor not in src:
    raise SystemExit("ERROR: expected 7D.4D.1 constructor annotations not found.")
src = src.replace(old_ctor, new_ctor, 1)

old_body = """        self.evidence_semantic_tagger = (
            evidence_semantic_tagger
            or EvidenceSemanticTaggingService(provider)
        )
        self.evidence_quality_evaluator = (
            evidence_quality_evaluator
            or SemanticEvidenceQualityEvaluator()
        )
"""
new_body = """        # Lazy imports are required here: evidence_provider owns the canonical
        # EvidenceItem but imports ResearchEvidence from this module.
        if evidence_semantic_tagger is None:
            from app.ai.evidence_semantics import EvidenceSemanticTaggingService
            evidence_semantic_tagger = EvidenceSemanticTaggingService(provider)
        if evidence_quality_evaluator is None:
            from app.ai.evidence_quality_semantic import (
                SemanticEvidenceQualityEvaluator,
            )
            evidence_quality_evaluator = SemanticEvidenceQualityEvaluator()
        self.evidence_semantic_tagger = evidence_semantic_tagger
        self.evidence_quality_evaluator = evidence_quality_evaluator
"""
if old_body not in src:
    raise SystemExit("ERROR: expected 7D.4D.1 constructor body not found.")
src = src.replace(old_body, new_body, 1)

# Runtime annotations must not resolve EvidenceItem because it is TYPE_CHECKING only.
src = src.replace(
    "        evidence_items: list[EvidenceItem] | None = None,\n",
    '        evidence_items: list["EvidenceItem"] | None = None,\n',
    1,
)
src = src.replace(
    "        evidence_semantics: list[EvidenceSemanticAssessment] = []\n",
    '        evidence_semantics: list["EvidenceSemanticAssessment"] = []\n',
    1,
)
src = src.replace(
    "        calibrated_quality: EvidenceQualityAssessment | None = None\n",
    '        calibrated_quality: "EvidenceQualityAssessment | None" = None\n',
    1,
)
src = src.replace(
    "        evidence_items: list[EvidenceItem],\n",
    '        evidence_items: list["EvidenceItem"],\n',
    1,
)
src = src.replace(
    "        assessment: EvidenceQualityAssessment,\n",
    '        assessment: "EvidenceQualityAssessment",\n',
    1,
)

path.write_text(src, encoding="utf-8")
print("PATCHED:", path)
print("BACKUP :", backup)
print("FIX    : circular import removed via TYPE_CHECKING + lazy imports")
