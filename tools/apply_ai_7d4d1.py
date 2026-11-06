from pathlib import Path
import shutil

path = Path("app/ai/research_service.py")
if not path.exists():
    raise SystemExit("ERROR: app/ai/research_service.py not found. Run from project root.")

src = path.read_text(encoding="utf-8")
backup = path.with_suffix(".py.7d4d1.bak")
if not backup.exists():
    shutil.copy2(path, backup)

def replace_once(old: str, new: str, label: str) -> None:
    global src
    count = src.count(old)
    if count != 1:
        raise SystemExit(f"ERROR [{label}]: expected 1 match, found {count}. No file written.")
    src = src.replace(old, new, 1)

replace_once(
    "from app.ai.provider import AIModelProvider\n",
    """from app.ai.provider import AIModelProvider
from app.ai.evidence_provider import EvidenceItem
from app.ai.evidence_semantics import (
    EvidenceSemanticAssessment,
    EvidenceSemanticTaggingService,
)
from app.ai.evidence_quality import EvidenceQualityAssessment
from app.ai.evidence_quality_semantic import SemanticEvidenceQualityEvaluator
""",
    "imports",
)

replace_once(
    """    semantic_report: dict = Field(default_factory=dict)
    repair_attempted: bool = False
""",
    """    semantic_report: dict = Field(default_factory=dict)
    evidence_quality_report: dict = Field(default_factory=dict)
    evidence_semantic_assessments: list[dict] = Field(default_factory=list)
    repair_attempted: bool = False
""",
    "ResearchResult",
)

replace_once(
    """    def __init__(
        self,
        provider: AIModelProvider,
        prompt_version: str = RESEARCH_PROMPT_VERSION,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self.coverage_validator = ResearchCoverageValidator()
        self.semantic_evaluator = ResearchSemanticQualityEvaluator()
""",
    """    def __init__(
        self,
        provider: AIModelProvider,
        prompt_version: str = RESEARCH_PROMPT_VERSION,
        *,
        evidence_semantic_tagger: EvidenceSemanticTaggingService | None = None,
        evidence_quality_evaluator: SemanticEvidenceQualityEvaluator | None = None,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self.coverage_validator = ResearchCoverageValidator()
        self.semantic_evaluator = ResearchSemanticQualityEvaluator()
        self.evidence_semantic_tagger = (
            evidence_semantic_tagger
            or EvidenceSemanticTaggingService(provider)
        )
        self.evidence_quality_evaluator = (
            evidence_quality_evaluator
            or SemanticEvidenceQualityEvaluator()
        )
""",
    "constructor",
)

replace_once(
    """        risk_state_id: str | None = None,
        now: datetime | None = None,
    ) -> ResearchResult:
        if not evidence:
            raise ValueError("At least one evidence item is required")
""",
    """        risk_state_id: str | None = None,
        now: datetime | None = None,
        evidence_items: list[EvidenceItem] | None = None,
    ) -> ResearchResult:
        if not evidence:
            raise ValueError("At least one evidence item is required")

        evidence_semantics: list[EvidenceSemanticAssessment] = []
        calibrated_quality: EvidenceQualityAssessment | None = None
        if evidence_items is not None:
            self._validate_evidence_items(candidate, evidence, evidence_items)
            evidence_semantics = self.evidence_semantic_tagger.classify_many(
                evidence_items
            )
            calibrated_quality = self.evidence_quality_evaluator.evaluate(
                evidence_items,
                evidence_semantics,
                now=now,
            )
""",
    "research signature/calibration",
)

replace_once(
    """        output = ResearchModelOutput.model_validate(response.structured_output)

        inference = build_inference_record(
""",
    """        output = ResearchModelOutput.model_validate(response.structured_output)

        # Evidence quality is evidence governance, not an LLM opinion.
        if calibrated_quality is not None:
            output = output.model_copy(
                update={"evidence_quality": calibrated_quality.quality}
            )

        inference = build_inference_record(
""",
    "initial quality override",
)

replace_once(
    """            repaired_output = self._merge_repair_output(
                output,
                repair_candidate_output,
                repairable_fields,
            )
""",
    """            repaired_output = self._merge_repair_output(
                output,
                repair_candidate_output,
                repairable_fields,
            )
            if calibrated_quality is not None:
                repaired_output = repaired_output.model_copy(
                    update={"evidence_quality": calibrated_quality.quality}
                )
""",
    "repair quality override",
)

replace_once(
    """                "semantic_warning_codes": [
                    issue.code.value for issue in semantic.warnings
                ],
            },
""",
    """                "semantic_warning_codes": [
                    issue.code.value for issue in semantic.warnings
                ],
                "evidence_quality_calibrated": calibrated_quality is not None,
                "evidence_quality_policy": (
                    calibrated_quality.metadata.get("policy")
                    if calibrated_quality is not None
                    else None
                ),
                "evidence_coverage_score": (
                    calibrated_quality.coverage_score
                    if calibrated_quality is not None
                    else None
                ),
                "evidence_independent_sources": (
                    calibrated_quality.unique_sources
                    if calibrated_quality is not None
                    else None
                ),
            },
""",
    "research metadata",
)

replace_once(
    """            semantic_report=self._semantic_report_dict(semantic),
            repair_attempted=repair_attempted,
        )
""",
    """            semantic_report=self._semantic_report_dict(semantic),
            evidence_quality_report=(
                self._evidence_quality_report_dict(calibrated_quality)
                if calibrated_quality is not None
                else {}
            ),
            evidence_semantic_assessments=[
                assessment.model_dump(mode="json")
                for assessment in evidence_semantics
            ],
            repair_attempted=repair_attempted,
        )
""",
    "ResearchResult diagnostics",
)

marker = """    @staticmethod
    def _normalize_repaired_status(
"""
helpers = """    @staticmethod
    def _validate_evidence_items(
        candidate: ScanCandidate,
        evidence: list[ResearchEvidence],
        evidence_items: list[EvidenceItem],
    ) -> None:
        research_ids = [item.evidence_id for item in evidence]
        canonical_ids = [item.evidence.evidence_id for item in evidence_items]
        if len(canonical_ids) != len(set(canonical_ids)):
            raise ValueError("evidence_items contain duplicate evidence_id values")
        if set(research_ids) != set(canonical_ids):
            raise ValueError(
                "evidence_items must correspond exactly to supplied research evidence"
            )
        if any(item.ticker != candidate.ticker for item in evidence_items):
            raise ValueError("evidence_items ticker must match candidate ticker")

    @staticmethod
    def _evidence_quality_report_dict(
        assessment: EvidenceQualityAssessment,
    ) -> dict:
        return {
            "quality": assessment.quality.value,
            "coverage_score": assessment.coverage_score,
            "freshness_score": assessment.freshness_score,
            "source_diversity_score": assessment.source_diversity_score,
            "total_items": assessment.total_items,
            "unique_sources": assessment.unique_sources,
            "stale_items": assessment.stale_items,
            "undated_items": assessment.undated_items,
            "warnings": list(assessment.warnings),
            "coverage": [
                {
                    "dimension": entry.dimension.value,
                    "level": entry.level.value,
                    "item_count": entry.item_count,
                }
                for entry in assessment.coverage
            ],
            "metadata": dict(assessment.metadata),
        }

"""
replace_once(marker, helpers + marker, "helper methods")

path.write_text(src, encoding="utf-8")
print("PATCHED:", path)
print("BACKUP :", backup)
