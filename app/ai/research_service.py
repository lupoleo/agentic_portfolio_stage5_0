from __future__ import annotations

from datetime import datetime
import time

from pydantic import Field, create_model, field_validator

from app.ai.inference import AIInferenceRecord, build_inference_record
from app.ai.models import (
    AIModel,
    AIRequest,
    AITask,
    DataSensitivity,
    ReasoningMode,
    ResponseFormat,
)
from app.ai.provider import AIModelProvider
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.evidence_provider import EvidenceItem
    from app.ai.evidence_semantics import EvidenceSemanticAssessment
    from app.ai.evidence_quality import EvidenceQualityAssessment
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
from app.ai.scan_models import ScanCandidate
from app.ai.research_validator import ResearchCoverageReport, ResearchCoverageValidator
from app.ai.research_semantics import (
    ResearchSemanticQualityEvaluator,
    ResearchSemanticReport,
)


RESEARCH_PROMPT_VERSION = "opportunity-research-v1.5-context-gaps-confidence"
RESEARCH_CONTRACT_VERSION = "ai-8c2-research-v4-forward-framing"

# AI-7D.4D.3d: hard deterministic budgets for the repair input.
# These protect the local 4096-token model from pathological initial outputs.
REPAIR_EVIDENCE_TOTAL_CHAR_BUDGET = 6000
REPAIR_EVIDENCE_ITEM_CHAR_BUDGET = 1600
REPAIR_PREVIOUS_TOTAL_CHAR_BUDGET = 4000
REPAIR_PREVIOUS_FIELD_CHAR_BUDGET = 1200


class ResearchEvidence(AIModel):
    evidence_id: str
    source_type: str
    text: str
    published_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class ResearchModelOutput(AIModel):
    research_status: ResearchStatus
    market_context: str | None = None
    fundamental_context: str | None = None
    technical_context: str | None = None
    event_context: str | None = None
    catalyst_assessment: str | None = None
    expectations_assessment: ExpectationsAssessment = ExpectationsAssessment.UNKNOWN
    bull_case: str | None = None
    bear_case: str | None = None
    key_risks: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    forward_uncertainties: list[str] = Field(default_factory=list)
    evidence_quality: EvidenceQuality
    research_confidence: float = Field(ge=0.0, le=1.0)
    requires_additional_research: bool

    @field_validator("research_confidence", mode="before")
    @classmethod
    def _percent_scale_confidence(cls, value):
        # Live 2026-10-01 (FMC LONG): the model returned 30 for 0.30 and the
        # whole research failed schema validation. A value in (1, 100] is a
        # percentage on a unit scale; anything else is left to validation.
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 1 < value <= 100:
            return value / 100.0
        return value


class ResearchResult(AIModel):
    research: OpportunityResearch
    inference: AIInferenceRecord
    inferences: list[AIInferenceRecord] = Field(default_factory=list)
    coverage_report: dict = Field(default_factory=dict)
    semantic_report: dict = Field(default_factory=dict)
    evidence_quality_report: dict = Field(default_factory=dict)
    evidence_semantic_assessments: list[dict] = Field(default_factory=list)
    repair_attempted: bool = False
    diagnostics: dict = Field(default_factory=dict)


class ResearchCoverageValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        initial_report: ResearchCoverageReport,
        final_report: ResearchCoverageReport,
        inference_ids: list[str],
        initial_semantic_report: ResearchSemanticReport | None = None,
        final_semantic_report: ResearchSemanticReport | None = None,
        initial_output: ResearchModelOutput | None = None,
        final_output: ResearchModelOutput | None = None,
    ) -> None:
        super().__init__(message)
        self.initial_report = initial_report
        self.final_report = final_report
        self.inference_ids = inference_ids
        self.initial_semantic_report = initial_semantic_report
        self.final_semantic_report = final_semantic_report
        self.initial_output = initial_output
        self.final_output = final_output


class ResearchService:
    def __init__(
        self,
        provider: AIModelProvider,
        prompt_version: str = RESEARCH_PROMPT_VERSION,
        *,
        evidence_semantic_tagger=None,
        evidence_quality_evaluator=None,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self.coverage_validator = ResearchCoverageValidator()
        self.semantic_evaluator = ResearchSemanticQualityEvaluator()
        # Lazy imports are required here: evidence_provider owns the canonical
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

    def research(
        self,
        candidate: ScanCandidate,
        evidence: list[ResearchEvidence],
        *,
        sensitivity: DataSensitivity = DataSensitivity.PUBLIC,
        reasoning_mode: ReasoningMode = ReasoningMode.REASONING,
        portfolio_snapshot_id: str | None = None,
        risk_state_id: str | None = None,
        now: datetime | None = None,
        evidence_items: list["EvidenceItem"] | None = None,
    ) -> ResearchResult:
        if not evidence:
            raise ValueError("At least one evidence item is required")

        evidence_semantics: list["EvidenceSemanticAssessment"] = []
        calibrated_quality: "EvidenceQualityAssessment | None" = None
        semantic_tagging_wall_ms: float | None = None
        evidence_quality_wall_ms: float | None = None
        initial_research_latency_ms: float | None = None
        repair_research_latency_ms: float | None = None

        if evidence_items is not None:
            self._validate_evidence_items(candidate, evidence, evidence_items)
            semantic_started = time.perf_counter()
            evidence_semantics = self.evidence_semantic_tagger.classify_many(
                evidence_items
            )
            semantic_tagging_wall_ms = (
                time.perf_counter() - semantic_started
            ) * 1000.0

            quality_started = time.perf_counter()
            calibrated_quality = self.evidence_quality_evaluator.evaluate(
                evidence_items,
                evidence_semantics,
                now=now,
            )
            evidence_quality_wall_ms = (
                time.perf_counter() - quality_started
            ) * 1000.0

        # AI-8C.3b.6: derive context-presence requirements from the canonical
        # evidence semantic tags, not from stochastic research prose.
        required_context_fields = self._required_context_fields(
            evidence_semantics,
            evidence_items=evidence_items,
        )

        snapshot_id = (
            portfolio_snapshot_id
            if portfolio_snapshot_id is not None
            else candidate.portfolio_snapshot_id
        )
        risk_id = (
            risk_state_id
            if risk_state_id is not None
            else candidate.risk_state_id
        )

        if (
            candidate.portfolio_snapshot_id is not None
            and snapshot_id != candidate.portfolio_snapshot_id
        ):
            raise ValueError("portfolio_snapshot_id must match candidate")
        if (
            candidate.risk_state_id is not None
            and risk_id != candidate.risk_state_id
        ):
            raise ValueError("risk_state_id must match candidate")

        request = AIRequest(
            task=AITask.RESEARCH,
            prompt=self._build_prompt(candidate, evidence),
            sensitivity=sensitivity,
            reasoning_mode=reasoning_mode,
            response_format=ResponseFormat.JSON,
            output_schema=ResearchModelOutput,
            metadata={
                "prompt_version": self.prompt_version,
                "candidate_id": candidate.candidate_id,
                "scan_id": candidate.scan_id,
                "ticker": candidate.ticker,
                "evidence_ids": [x.evidence_id for x in evidence],
            },
        )

        response = self.provider.infer(request)
        initial_research_latency_ms = response.latency_ms
        if response.structured_output is None:
            raise ValueError("research provider returned no structured output")

        output = ResearchModelOutput.model_validate(response.structured_output)
        output, initial_list_canonicalization = (
            self._canonicalize_research_list_fields(output)
        )

        # Evidence quality is evidence governance, not an LLM opinion.
        if calibrated_quality is not None:
            output = output.model_copy(
                update={"evidence_quality": calibrated_quality.quality}
            )

        inference = build_inference_record(
            request,
            response,
            prompt_version=self.prompt_version,
            evidence_ids=[x.evidence_id for x in evidence],
            portfolio_snapshot_id=snapshot_id,
            risk_state_id=risk_id,
            timestamp=now,
        )

        inferences = [inference]
        initial_research_confidence = output.research_confidence
        initial_research_status = output.research_status.value
        coverage = self.coverage_validator.validate(output, evidence)
        semantic = self.semantic_evaluator.evaluate(output, evidence)
        missing_required_contexts = self._missing_required_context_fields(
            output,
            required_context_fields,
        )
        confidence_repair_requested = self._confidence_incoherent(coverage)
        forward_repair_requested = self._forward_items_in_unknowns(coverage)
        repair_attempted = False
        deterministic_status_normalized = False
        deterministic_unknowns_canonicalized = False
        repair_list_canonicalization = self._empty_list_canonicalization_report()

        if (
            not coverage.is_valid
            or not semantic.is_valid
            or missing_required_contexts
            or confidence_repair_requested
            or forward_repair_requested
        ):
            repair_attempted = True
            repairable_fields = self._repairable_fields(coverage, semantic)
            if confidence_repair_requested:
                repairable_fields.add("research_confidence")
            if forward_repair_requested:
                repairable_fields.update({
                    "unknowns",
                    "forward_uncertainties",
                    "research_status",
                    "requires_additional_research",
                })
            repairable_fields.update(
                self._context_presence_repairable_fields(
                    missing_required_contexts
                )
            )
            repair_output_schema = self._build_repair_output_schema(repairable_fields)
            repair_request = AIRequest(
                task=AITask.RESEARCH,
                prompt=self._build_repair_prompt(
                    candidate=candidate,
                    evidence=evidence,
                    previous_output=output,
                    coverage=coverage,
                    semantic=semantic,
                    allowed_fields=repairable_fields,
                    evidence_semantics=evidence_semantics,
                ),
                sensitivity=sensitivity,
                reasoning_mode=ReasoningMode.FAST,
                response_format=ResponseFormat.JSON,
                output_schema=repair_output_schema,
                metadata={
                    "prompt_version": self.prompt_version,
                    "candidate_id": candidate.candidate_id,
                    "scan_id": candidate.scan_id,
                    "ticker": candidate.ticker,
                    "evidence_ids": [x.evidence_id for x in evidence],
                    "repair": True,
                    "repair_pass": 1,
                    "parent_inference_id": inference.inference_id,
                    "coverage_error_codes": [
                        issue.code.value for issue in coverage.errors
                    ],
                    "semantic_error_codes": [
                        issue.code.value for issue in semantic.errors
                    ],
                    "repairable_fields": sorted(repairable_fields),
                    "repair_strategy": "FIELD_SCOPED_TARGETED_MERGE",
                    "repair_reasoning_mode": ReasoningMode.FAST.value,
                    "repair_schema_fields": sorted(repairable_fields),
                    "required_context_fields": sorted(required_context_fields),
                    "missing_required_contexts": sorted(missing_required_contexts),
                },
            )
            repair_response = self.provider.infer(repair_request)
            repair_research_latency_ms = repair_response.latency_ms
            if repair_response.structured_output is None:
                raise ValueError(
                    "research repair provider returned no structured output"
                )

            repair_candidate_output = repair_output_schema.model_validate(
                repair_response.structured_output
            )
            repaired_output = self._merge_repair_output(
                output,
                repair_candidate_output,
                repairable_fields,
            )
            repaired_output, repair_list_canonicalization = (
                self._canonicalize_research_list_fields(repaired_output)
            )
            if calibrated_quality is not None:
                repaired_output = repaired_output.model_copy(
                    update={"evidence_quality": calibrated_quality.quality}
                )
            repair_inference = build_inference_record(
                repair_request,
                repair_response,
                prompt_version=f"{self.prompt_version}-repair1",
                evidence_ids=[x.evidence_id for x in evidence],
                portfolio_snapshot_id=snapshot_id,
                risk_state_id=risk_id,
                timestamp=now,
            )
            inferences.append(repair_inference)

            repaired_coverage = self.coverage_validator.validate(
                repaired_output, evidence
            )
            repaired_semantic = self.semantic_evaluator.evaluate(
                repaired_output, evidence
            )

            (
                repaired_output,
                repaired_coverage,
                deterministic_unknowns_canonicalized,
            ) = self._canonicalize_conflicting_unknowns(
                repaired_output,
                evidence,
                repaired_coverage,
            )
            if deterministic_unknowns_canonicalized:
                repaired_semantic = self.semantic_evaluator.evaluate(
                    repaired_output, evidence
                )

            repaired_output, deterministic_status_normalized = (
                self._normalize_repaired_status(
                    repaired_output,
                    repaired_coverage,
                    repaired_semantic,
                )
            )
            if deterministic_status_normalized:
                repaired_coverage = self.coverage_validator.validate(
                    repaired_output, evidence
                )
                repaired_semantic = self.semantic_evaluator.evaluate(
                    repaired_output, evidence
                )

            if not repaired_coverage.is_valid or not repaired_semantic.is_valid:
                codes = ", ".join(
                    [issue.code.value for issue in repaired_coverage.errors]
                    + [issue.code.value for issue in repaired_semantic.errors]
                )
                raise ResearchCoverageValidationError(
                    "Research output remains invalid after one repair pass: "
                    + codes,
                    initial_report=coverage,
                    final_report=repaired_coverage,
                    inference_ids=[x.inference_id for x in inferences],
                    initial_semantic_report=semantic,
                    final_semantic_report=repaired_semantic,
                    initial_output=output,
                    final_output=repaired_output,
                )

            output = repaired_output
            coverage = repaired_coverage
            semantic = repaired_semantic
            response = repair_response

        final_inference = inferences[-1]

        final_missing_required_contexts = self._missing_required_context_fields(
            output,
            required_context_fields,
        )
        research_diagnostics = {
            "required_context_fields": sorted(required_context_fields),
            "initial_missing_required_contexts": sorted(
                missing_required_contexts
            ),
            "final_missing_required_contexts": sorted(
                final_missing_required_contexts
            ),
            "context_presence_repair_attempted": bool(
                missing_required_contexts
            ),
            "semantic_tagging_wall_ms": semantic_tagging_wall_ms,
            "evidence_quality_wall_ms": evidence_quality_wall_ms,
            "initial_research_latency_ms": initial_research_latency_ms,
            "repair_research_latency_ms": repair_research_latency_ms,
            "research_provider_latency_total_ms": (
                (initial_research_latency_ms or 0.0)
                + (repair_research_latency_ms or 0.0)
            ),
            "research_inference_count": len(inferences),
            "research_list_canonicalization": {
                "initial": initial_list_canonicalization,
                "repair": repair_list_canonicalization,
                "total_removed_blank_items": (
                    initial_list_canonicalization["total_removed_blank_items"]
                    + repair_list_canonicalization["total_removed_blank_items"]
                ),
            },
            "evidence_semantic_dimensions": {
                assessment.evidence_id: sorted(
                    str(getattr(dimension, "value", dimension))
                    for dimension in assessment.dimensions
                )
                for assessment in evidence_semantics
            },
        }

        research = OpportunityResearch(
            research_id=self._research_id(final_inference.inference_id),
            candidate_id=candidate.candidate_id,
            scan_id=candidate.scan_id,
            created_at=final_inference.timestamp,
            ticker=candidate.ticker,
            portfolio_snapshot_id=snapshot_id,
            risk_state_id=risk_id,
            research_status=output.research_status,
            market_context=output.market_context,
            fundamental_context=output.fundamental_context,
            technical_context=output.technical_context,
            event_context=output.event_context,
            catalyst_assessment=output.catalyst_assessment,
            expectations_assessment=output.expectations_assessment,
            bull_case=output.bull_case,
            bear_case=output.bear_case,
            key_risks=output.key_risks,
            contradictory_evidence=output.contradictory_evidence,
            unknowns=output.unknowns,
            forward_uncertainties=output.forward_uncertainties,
            evidence_quality=output.evidence_quality,
            research_confidence=output.research_confidence,
            evidence_ids=[x.evidence_id for x in evidence],
            inference_ids=[x.inference_id for x in inferences],
            requires_additional_research=output.requires_additional_research,
            metadata={
                "provider": response.provider,
                "model": response.model,
                "prompt_version": self.prompt_version,
                "research_contract": RESEARCH_CONTRACT_VERSION,
                "material_gaps": self.coverage_validator.material_gaps(
                    output, self.coverage_validator.evidence_blob(evidence)
                ),
                "context_gaps": self.coverage_validator.context_gaps(
                    self.coverage_validator.material_unknowns(output)
                    + self.coverage_validator.reclassified_forward_uncertainties(output),
                    self.coverage_validator.evidence_blob(evidence),
                ),
                "initial_research_confidence": initial_research_confidence,
                "forward_reclassification_requested": forward_repair_requested,
                "initial_research_status": initial_research_status,
                "forward_framed_unknowns": (
                    self.coverage_validator.forward_framed_unknowns(output)
                ),
                "research_confidence_repaired": (
                    confidence_repair_requested
                    and output.research_confidence != initial_research_confidence
                ),
                "forward_items_reclassified_as_gaps": (
                    self.coverage_validator.reclassified_forward_uncertainties(output)
                ),
                "forward_uncertainty_count": len(output.forward_uncertainties),
                "repair_attempted": repair_attempted,
                "deterministic_status_normalized": deterministic_status_normalized,
                "deterministic_unknowns_canonicalized": (
                    deterministic_unknowns_canonicalized
                ),
                "research_list_canonicalization": research_diagnostics[
                    "research_list_canonicalization"
                ],
                "coverage_valid": coverage.is_valid,
                "coverage_warning_codes": [
                    issue.code.value for issue in coverage.warnings
                ],
                "semantic_valid": semantic.is_valid,
                "semantic_warning_codes": [
                    issue.code.value for issue in semantic.warnings
                ],
                "required_context_fields": research_diagnostics[
                    "required_context_fields"
                ],
                "initial_missing_required_contexts": research_diagnostics[
                    "initial_missing_required_contexts"
                ],
                "final_missing_required_contexts": research_diagnostics[
                    "final_missing_required_contexts"
                ],
                "context_presence_repair_attempted": research_diagnostics[
                    "context_presence_repair_attempted"
                ],
                "semantic_tagging_wall_ms": semantic_tagging_wall_ms,
                "evidence_quality_wall_ms": evidence_quality_wall_ms,
                "initial_research_latency_ms": initial_research_latency_ms,
                "repair_research_latency_ms": repair_research_latency_ms,
                "research_provider_latency_total_ms": research_diagnostics[
                    "research_provider_latency_total_ms"
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
        )
        return ResearchResult(
            research=research,
            inference=final_inference,
            inferences=inferences,
            coverage_report=self._coverage_report_dict(coverage),
            semantic_report=self._semantic_report_dict(semantic),
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
            diagnostics=research_diagnostics,
        )

    @classmethod
    def _required_context_fields(
        cls,
        evidence_semantics: list["EvidenceSemanticAssessment"],
        *,
        evidence_items: list["EvidenceItem"] | None = None,
    ) -> set[str]:
        """Map canonical evidence dimensions to deterministic context presence.

        This gate controls whether a context should be attempted. It never
        synthesizes research content and therefore cannot invent facts.
        """
        required: set[str] = set()
        for assessment in evidence_semantics:
            dimensions = {
                str(getattr(dimension, "value", dimension))
                for dimension in assessment.dimensions
            }
            if "FUNDAMENTAL" in dimensions:
                required.add("fundamental_context")
            if "CATALYST_EVENT" in dimensions:
                required.update({"event_context", "catalyst_assessment"})
            if "ANALYST_EXPECTATIONS" in dimensions:
                required.add("event_context")
                if cls._has_scorable_analyst_expectations(evidence_items):
                    required.add("expectations_assessment")
            if "MACRO" in dimensions:
                required.add("market_context")
            if "PRICE_TECHNICAL" in dimensions:
                required.add("technical_context")
        return required

    @staticmethod
    def _has_scorable_analyst_expectations(
        evidence_items: list["EvidenceItem"] | None,
    ) -> bool:
        if not evidence_items:
            return False
        for item in evidence_items:
            metadata = getattr(item.evidence, "metadata", {}) or {}
            if metadata.get("expectations_scorable") is True:
                return True
        return False

    @staticmethod
    def _missing_required_context_fields(
        output: ResearchModelOutput,
        required_fields: set[str],
    ) -> set[str]:
        missing: set[str] = set()
        for name in required_fields:
            value = getattr(output, name, None)
            if value is None:
                missing.add(name)
            elif (
                name == "expectations_assessment"
                and value == ExpectationsAssessment.UNKNOWN
            ):
                missing.add(name)
        return missing

    @staticmethod
    def _context_presence_repairable_fields(
        missing_required_contexts: set[str],
    ) -> set[str]:
        """Authorize only the missing context fields themselves.

        Context presence never independently authorizes rewriting research
        governance. Status fields remain repairable only when the canonical
        coverage or semantic validators report their own status invariant.
        """
        return set(missing_required_contexts)

    @staticmethod
    def _context_presence_repair_instructions(
        allowed_fields: set[str],
    ) -> str:
        context_fields = {
            "market_context",
            "fundamental_context",
            "technical_context",
            "event_context",
            "catalyst_assessment",
            "expectations_assessment",
        }
        targets = sorted(context_fields.intersection(allowed_fields))
        if not targets:
            return "CONTEXT PRESENCE GATE: no context-presence repair required."
        instructions = (
            "CONTEXT PRESENCE GATE:\n"
            "- Canonical evidence semantics deterministically indicate that "
            "these context categories are represented: "
            + ", ".join(targets)
            + ".\n"
            "- Populate each listed field only with facts supported by the "
            "supplied evidence selected for that field.\n"
            "- If the evidence still does not support a truthful summary, "
            "omit/null the field rather than inventing content."
        )
        if "expectations_assessment" in targets:
            instructions += (
                "\n- For expectations_assessment, select NOT_PRICED_IN, "
                "PARTIALLY_PRICED_IN or LARGELY_PRICED_IN only from explicit "
                "analyst/current-price comparisons in the supplied evidence. "
                "Do not infer a classification from price performance alone."
                "\n- Repairing expectations_assessment alone does not authorize "
                "promotion of research_status or clearing "
                "requires_additional_research. Those governance fields remain "
                "protected unless an independent deterministic validator "
                "explicitly included them in the repair schema."
            )
        return instructions

    _RESEARCH_LIST_FIELDS = (
        "key_risks",
        "contradictory_evidence",
        "unknowns",
        "forward_uncertainties",
    )

    @classmethod
    def _empty_list_canonicalization_report(cls) -> dict:
        return {
            "fields": {
                name: {
                    "input_count": 0,
                    "output_count": 0,
                    "removed_blank_items": 0,
                    "normalized_items": 0,
                }
                for name in cls._RESEARCH_LIST_FIELDS
            },
            "total_removed_blank_items": 0,
            "total_normalized_items": 0,
        }

    @classmethod
    def _canonicalize_research_list_fields(
        cls,
        output: ResearchModelOutput,
    ) -> tuple[ResearchModelOutput, dict]:
        """Normalize structural list noise without inventing research content.

        Each list item is stripped. Empty/whitespace-only strings are removed.
        Order and non-blank content are preserved; no semantic deduplication or
        rewriting is performed at this boundary.
        """
        updates: dict[str, list[str]] = {}
        report = cls._empty_list_canonicalization_report()

        for name in cls._RESEARCH_LIST_FIELDS:
            original = list(getattr(output, name, []) or [])
            normalized: list[str] = []
            removed_blank = 0
            normalized_items = 0

            for value in original:
                text = str(value).strip()
                if not text:
                    removed_blank += 1
                    continue
                if text != value:
                    normalized_items += 1
                normalized.append(text)

            updates[name] = normalized
            report["fields"][name] = {
                "input_count": len(original),
                "output_count": len(normalized),
                "removed_blank_items": removed_blank,
                "normalized_items": normalized_items,
            }
            report["total_removed_blank_items"] += removed_blank
            report["total_normalized_items"] += normalized_items

        if any(
            updates[name] != list(getattr(output, name, []) or [])
            for name in cls._RESEARCH_LIST_FIELDS
        ):
            output = output.model_copy(update=updates)

        return output, report

    @staticmethod
    def _validate_evidence_items(
        candidate: ScanCandidate,
        evidence: list[ResearchEvidence],
        evidence_items: list["EvidenceItem"],
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
        assessment: "EvidenceQualityAssessment",
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

    def _canonicalize_conflicting_unknowns(
        self,
        output: ResearchModelOutput,
        evidence: list[ResearchEvidence],
        coverage: ResearchCoverageReport,
    ) -> tuple[ResearchModelOutput, ResearchCoverageReport, bool]:
        """Deterministically remove only validator-proven contradictory unknowns.

        This runs only after the single LLM repair pass. It does not infer facts,
        add evidence, or perform a second model repair. Each candidate unknown is
        removed only when re-validation strictly reduces
        UNKNOWN_CONTRADICTS_SUPPLIED_FACT errors and introduces no new coverage
        error codes. All unresolved invalid states remain fail-closed.
        """

        target_code = "UNKNOWN_CONTRADICTS_SUPPLIED_FACT"

        def error_codes(report: ResearchCoverageReport) -> list[str]:
            return [issue.code.value for issue in report.errors]

        def target_count(report: ResearchCoverageReport) -> int:
            return sum(
                1
                for code in error_codes(report)
                if code == target_code
            )

        current_output = output
        current_report = coverage
        current_target_count = target_count(current_report)

        if current_target_count == 0 or not current_output.unknowns:
            return current_output, current_report, False

        changed = False
        index = 0

        while (
            index < len(current_output.unknowns)
            and current_target_count > 0
        ):
            candidate_unknowns = list(current_output.unknowns)
            candidate_unknowns.pop(index)
            trial_output = current_output.model_copy(
                update={"unknowns": candidate_unknowns}
            )
            trial_report = self.coverage_validator.validate(
                trial_output,
                evidence,
            )

            trial_target_count = target_count(trial_report)
            current_non_target = {
                code
                for code in error_codes(current_report)
                if code != target_code
            }
            trial_non_target = {
                code
                for code in error_codes(trial_report)
                if code != target_code
            }

            if (
                trial_target_count < current_target_count
                and trial_non_target.issubset(current_non_target)
            ):
                current_output = trial_output
                current_report = trial_report
                current_target_count = trial_target_count
                changed = True
                continue

            index += 1

        return current_output, current_report, changed

    @staticmethod
    def _normalize_repaired_status(
        output: ResearchModelOutput,
        coverage: ResearchCoverageReport,
        semantic: ResearchSemanticReport,
    ) -> tuple[ResearchModelOutput, bool]:
        """
        Enforce deterministic research-status invariants after the single LLM
        repair and conservative merge.

        This is not a second LLM repair. Normalization is deliberately narrow:
        it only converts a status to PARTIAL when the semantic evaluator says
        useful multi-sided analysis exists and the remaining validation state
        proves that material research is still missing.

        All other invalid states remain fail-closed.
        """
        coverage_codes = {issue.code.value for issue in coverage.errors}
        semantic_codes = {issue.code.value for issue in semantic.errors}

        if not semantic.useful_analysis:
            return output, False

        # Case A (7D.3.4a): useful analysis was incorrectly downgraded to
        # INSUFFICIENT_EVIDENCE. Coverage must otherwise already be valid.
        if (
            coverage.is_valid
            and semantic_codes == {"USEFUL_ANALYSIS_MARKED_INSUFFICIENT"}
            and output.research_status == ResearchStatus.INSUFFICIENT_EVIDENCE
        ):
            return (
                output.model_copy(
                    update={
                        "research_status": ResearchStatus.PARTIAL,
                        "requires_additional_research": True,
                    }
                ),
                True,
            )

        # Case B (7D.3.4b): the repair left COMPLETE even though material
        # evidence is explicitly missing. COMPLETE_REQUIRES_MORE and
        # COMPLETE_WITH_MORE_RESEARCH are derivative status-invariant errors
        # and are allowed alongside COMPLETE_WITH_MATERIAL_UNKNOWNS.
        allowed_coverage = {
            "COMPLETE_WITH_MATERIAL_UNKNOWNS",
            "COMPLETE_REQUIRES_MORE",
        }
        allowed_semantic = {"COMPLETE_WITH_MORE_RESEARCH"}
        if (
            output.research_status == ResearchStatus.COMPLETE
            and "COMPLETE_WITH_MATERIAL_UNKNOWNS" in coverage_codes
            and coverage_codes.issubset(allowed_coverage)
            and semantic_codes.issubset(allowed_semantic)
        ):
            return (
                output.model_copy(
                    update={
                        "research_status": ResearchStatus.PARTIAL,
                        "requires_additional_research": True,
                    }
                ),
                True,
            )

        return output, False

    @staticmethod
    def _repairable_fields(
        coverage: ResearchCoverageReport,
        semantic: ResearchSemanticReport,
    ) -> set[str]:
        """Return the smallest field set the repair pass is allowed to change."""
        fields: set[str] = set()

        coverage_map = {
            "TECHNICAL_CONTEXT_MISSING": {"technical_context"},
            "EVENT_CONTEXT_MISSING": {"event_context"},
            "COMPLETE_WITH_LOW_EVIDENCE": {
                "research_status",
                "requires_additional_research",
            },
            "COMPLETE_WITH_MATERIAL_UNKNOWNS": {
                "research_status",
                "requires_additional_research",
            },
            "COMPLETE_REQUIRES_MORE": {
                "research_status",
                "requires_additional_research",
            },
            "INSUFFICIENT_WITHOUT_MORE_RESEARCH": {
                "research_status",
                "requires_additional_research",
            },
            "UNKNOWN_CONTRADICTS_SUPPLIED_FACT": {"unknowns"},
        }
        semantic_map = {
            "USEFUL_ANALYSIS_MARKED_INSUFFICIENT": {
                "research_status",
                "requires_additional_research",
            },
            "PARTIAL_WITHOUT_MORE_RESEARCH": {
                "research_status",
                "requires_additional_research",
            },
            "COMPLETE_WITH_MORE_RESEARCH": {
                "research_status",
                "requires_additional_research",
            },
            "LOW_QUALITY_COMPLETE": {
                "research_status",
                "requires_additional_research",
            },
        }

        for issue in coverage.errors:
            fields.update(coverage_map.get(issue.code.value, set()))
        for issue in semantic.errors:
            fields.update(semantic_map.get(issue.code.value, set()))
        return fields

    @staticmethod
    def _build_repair_output_schema(allowed_fields: set[str]):
        """Build the smallest response schema authorized by deterministic validation."""
        if not allowed_fields:
            raise ValueError("Cannot build field-scoped repair schema without repairable fields")
        unknown = allowed_fields.difference(ResearchModelOutput.model_fields)
        if unknown:
            raise ValueError("Unknown ResearchModelOutput repair fields: " + ", ".join(sorted(unknown)))
        definitions = {}
        for name in sorted(allowed_fields):
            annotation = ResearchModelOutput.model_fields[name].annotation
            definitions[name] = (annotation | None, None)
        return create_model(
            "ResearchFieldScopedRepairOutput",
            __base__=AIModel,
            **definitions,
        )

    @staticmethod
    def _merge_repair_output(
        previous_output: ResearchModelOutput,
        repair_output: AIModel,
        allowed_fields: set[str],
    ) -> ResearchModelOutput:
        """Merge only fields actually returned by the field-scoped repair."""
        merged = previous_output.model_dump()
        repaired = repair_output.model_dump(exclude_unset=True)
        for name in allowed_fields:
            if name not in repaired:
                continue

            value = repaired[name]

            # The field-scoped repair schema deliberately makes every allowed
            # field optional so the model can omit fields it cannot repair.
            # Therefore an explicit JSON null is semantically equivalent to
            # "no repair value supplied" for canonical non-nullable fields.
            field = ResearchModelOutput.model_fields[name]
            if value is None and not ResearchService._field_allows_none(field.annotation):
                continue

            merged[name] = value
        return ResearchModelOutput.model_validate(merged)

    @staticmethod
    def _field_allows_none(annotation) -> bool:
        """Return True when the canonical ResearchModelOutput field accepts None."""
        from types import UnionType
        from typing import Union, get_args, get_origin

        if annotation is type(None):
            return True
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            return type(None) in get_args(annotation)
        return False

    def _build_repair_prompt(
        self,
        *,
        candidate: ScanCandidate,
        evidence: list[ResearchEvidence],
        previous_output: ResearchModelOutput,
        coverage: ResearchCoverageReport,
        semantic: ResearchSemanticReport,
        allowed_fields: set[str],
        evidence_semantics: list["EvidenceSemanticAssessment"] | None = None,
    ) -> str:
        scoped_evidence = self._scope_repair_evidence(
            evidence,
            allowed_fields,
            evidence_semantics=evidence_semantics,
        )
        evidence_block = self._bounded_repair_evidence_block(scoped_evidence)
        previous_block = self._scoped_previous_output(previous_output, allowed_fields)
        allowed = ", ".join(sorted(allowed_fields)) or "NONE"
        return (
            "You are repairing a structured research result that failed a "
            "deterministic coverage validator.\n\n"
            "FIELD-SCOPED TARGETED REPAIR\n"
            f"Return ONLY these fields: {allowed}.\n"
            "Protected fields are not part of the repair response schema.\n"
            "Do not recalculate the full research result.\n"
            "Fix the validator errors with the smallest possible change.\n\n"
            f"Candidate ticker: {candidate.ticker}\n"
            f"Candidate thesis: {candidate.thesis_summary}\n\n"
            f"{coverage.repair_instructions()}\n\n"
            f"{self._semantic_repair_instructions(semantic)}\n\n"
            f"{self._context_presence_repair_instructions(allowed_fields)}\n\n"
            f"{self._confidence_repair_instructions(coverage, allowed_fields)}\n\n"
            f"{self._forward_repair_instructions(coverage, allowed_fields)}\n\n"
            "SUPPLIED EVIDENCE\n"
            f"{evidence_block}\n\n"
            "PREVIOUS STRUCTURED OUTPUT\n"
            f"{previous_block}\n"
        )

    @staticmethod
    def _confidence_incoherent(coverage: ResearchCoverageReport) -> bool:
        return any(
            issue.code.value == "RESEARCH_CONFIDENCE_INCOHERENT"
            for issue in coverage.issues
        )

    @staticmethod
    def _forward_items_in_unknowns(coverage: ResearchCoverageReport) -> bool:
        return any(
            issue.code.value == "FORWARD_ITEMS_IN_UNKNOWNS"
            for issue in coverage.issues
        )

    @classmethod
    def _forward_repair_instructions(
        cls,
        coverage: ResearchCoverageReport,
        allowed_fields: set[str],
    ) -> str:
        if "forward_uncertainties" not in allowed_fields or not cls._forward_items_in_unknowns(coverage):
            return ""
        messages = [
            issue.message for issue in coverage.issues
            if issue.code.value == "FORWARD_ITEMS_IN_UNKNOWNS"
        ]
        return (
            "FORWARD UNCERTAINTY RECLASSIFICATION\n"
            + "\n".join(f"- {message}" for message in messages)
            + "\n- Move any future outcome from unknowns to forward_uncertainties;"
            " keep in unknowns only facts knowable at the evidence date.\n"
            "- Then reassess research_status and requires_additional_research"
            " under rules U4, 16 and 17: forward uncertainties never by"
            " themselves require additional research or prevent COMPLETE;"
            " keep PARTIAL only if a material as-of fact remains missing."
        )

    @classmethod
    def _confidence_repair_instructions(
        cls,
        coverage: ResearchCoverageReport,
        allowed_fields: set[str],
    ) -> str:
        if "research_confidence" not in allowed_fields or not cls._confidence_incoherent(coverage):
            return ""
        messages = [
            issue.message for issue in coverage.issues
            if issue.code.value == "RESEARCH_CONFIDENCE_INCOHERENT"
        ]
        return (
            "RESEARCH CONFIDENCE REPAIR\n"
            + "\n".join(f"- {message}" for message in messages)
            + "\n- Return research_confidence using the anchors: 0.2 weak or "
            "conflicting evidence; 0.5 mixed evidence; 0.7 clear evidence with "
            "material uncertainty; 0.85 or more broad, consistent evidence.\n"
            "- Do not change any other field."
        )

    @staticmethod
    def _scope_repair_evidence(
        evidence: list[ResearchEvidence],
        allowed_fields: set[str],
        *,
        evidence_semantics: list["EvidenceSemanticAssessment"] | None = None,
    ) -> list[ResearchEvidence]:
        """Scope content repair using semantic dimensions already computed upstream."""
        all_dimensions = {
            "PRICE_TECHNICAL",
            "FUNDAMENTAL",
            "CATALYST_EVENT",
            "ANALYST_EXPECTATIONS",
            "MACRO",
            "NEWS_CONTEXT",
        }
        field_dimensions = {
            "market_context": {"PRICE_TECHNICAL", "MACRO", "NEWS_CONTEXT"},
            "fundamental_context": {
                "FUNDAMENTAL", "CATALYST_EVENT", "NEWS_CONTEXT"
            },
            "technical_context": {"PRICE_TECHNICAL"},
            "event_context": {
                "CATALYST_EVENT", "ANALYST_EXPECTATIONS",
                "MACRO", "NEWS_CONTEXT"
            },
            "catalyst_assessment": {
                "CATALYST_EVENT", "FUNDAMENTAL",
                "ANALYST_EXPECTATIONS", "MACRO", "NEWS_CONTEXT"
            },
            "expectations_assessment": {
                "ANALYST_EXPECTATIONS", "CATALYST_EVENT",
                "PRICE_TECHNICAL", "NEWS_CONTEXT"
            },
            "bull_case": all_dimensions,
            "bear_case": all_dimensions,
            "key_risks": all_dimensions,
            "contradictory_evidence": all_dimensions,
            "unknowns": all_dimensions,
            "forward_uncertainties": all_dimensions,
        }

        requested: set[str] = set()
        content_requested = False
        for field_name in allowed_fields:
            dimensions = field_dimensions.get(field_name)
            if dimensions is not None:
                content_requested = True
                requested.update(dimensions)

        if not content_requested:
            return []

        # Backward-compatible/fail-safe input behavior: never hide evidence
        # when canonical semantic assessments are unavailable.
        if not evidence_semantics:
            return evidence

        by_id = {
            assessment.evidence_id: assessment
            for assessment in evidence_semantics
        }
        selected: list[ResearchEvidence] = []
        for item in evidence:
            assessment = by_id.get(item.evidence_id)
            if assessment is None:
                continue
            dimensions = {
                str(getattr(dimension, "value", dimension))
                for dimension in assessment.dimensions
            }
            if dimensions.intersection(requested):
                selected.append(item)

        # A zero-result semantic filter is suspicious for a content repair.
        # Preserve the existing fail-safe behavior instead of starving repair.
        return selected or evidence

    @classmethod
    def _scoped_previous_output(
        cls,
        previous_output: ResearchModelOutput,
        allowed_fields: set[str],
    ) -> str:
        """Keep repair targets plus compact governance/dependency state.

        Target values are deterministically bounded so an over-verbose initial
        LLM response cannot make the repair prompt grow without limit.
        """
        import json

        dumped = previous_output.model_dump(mode="json")
        target_values = {
            name: cls._bounded_json_value(
                dumped.get(name),
                REPAIR_PREVIOUS_FIELD_CHAR_BUDGET,
            )
            for name in sorted(allowed_fields)
            if name in dumped
        }
        compact = {
            "repair_target_previous_values": target_values,
            "governance_state": {
                "research_status": dumped["research_status"],
                "evidence_quality": dumped["evidence_quality"],
                "research_confidence": dumped["research_confidence"],
                "requires_additional_research": dumped[
                    "requires_additional_research"
                ],
            },
            "protected_field_presence": {
                "has_market_context": previous_output.market_context is not None,
                "has_fundamental_context": (
                    previous_output.fundamental_context is not None
                ),
                "has_technical_context": previous_output.technical_context is not None,
                "has_event_context": previous_output.event_context is not None,
                "has_catalyst_assessment": (
                    previous_output.catalyst_assessment is not None
                ),
                "has_bull_case": previous_output.bull_case is not None,
                "has_bear_case": previous_output.bear_case is not None,
            },
            "protected_list_counts": {
                "key_risks": len(previous_output.key_risks),
                "contradictory_evidence": len(
                    previous_output.contradictory_evidence
                ),
                "unknowns": len(previous_output.unknowns),
                "forward_uncertainties": len(
                    previous_output.forward_uncertainties
                ),
            },
        }
        serialized = json.dumps(compact, indent=2, ensure_ascii=False)
        return cls._hard_bound_text(
            serialized,
            REPAIR_PREVIOUS_TOTAL_CHAR_BUDGET,
            marker="...[PREVIOUS_OUTPUT_TRUNCATED]...",
        )

    @classmethod
    def _bounded_repair_evidence_block(
        cls,
        evidence: list[ResearchEvidence],
    ) -> str:
        """Format repair evidence under both per-item and total hard budgets."""
        if not evidence:
            return "NONE"

        parts: list[str] = []
        remaining = REPAIR_EVIDENCE_TOTAL_CHAR_BUDGET
        for index, item in enumerate(evidence, start=1):
            formatted = cls._format_evidence(index, item)
            formatted = cls._hard_bound_text(
                formatted,
                REPAIR_EVIDENCE_ITEM_CHAR_BUDGET,
                marker="...[EVIDENCE_ITEM_TRUNCATED]...",
            )
            separator_cost = 2 if parts else 0
            if remaining <= separator_cost:
                break
            available = remaining - separator_cost
            if len(formatted) > available:
                formatted = cls._hard_bound_text(
                    formatted,
                    available,
                    marker="...[EVIDENCE_BLOCK_TRUNCATED]...",
                )
            if formatted:
                parts.append(formatted)
                remaining -= len(formatted) + separator_cost
            if remaining <= 0:
                break
        return "\n\n".join(parts) or "NONE"

    @staticmethod
    def _bounded_json_value(value, budget: int):
        """Bound arbitrary JSON-compatible values while preserving type shape."""
        import json

        if value is None:
            return None
        if isinstance(value, str):
            return ResearchService._hard_bound_text(
                value, budget, marker="...[FIELD_TRUNCATED]..."
            )
        if isinstance(value, list):
            result = []
            used = 2
            for item in value:
                item_budget = max(64, min(400, budget - used))
                bounded = ResearchService._bounded_json_value(item, item_budget)
                encoded = json.dumps(bounded, ensure_ascii=False)
                if used + len(encoded) + 1 > budget:
                    break
                result.append(bounded)
                used += len(encoded) + 1
            return result
        if isinstance(value, dict):
            result = {}
            used = 2
            for key, item in value.items():
                item_budget = max(64, min(400, budget - used))
                bounded = ResearchService._bounded_json_value(item, item_budget)
                encoded = json.dumps({str(key): bounded}, ensure_ascii=False)
                if used + len(encoded) + 1 > budget:
                    break
                result[str(key)] = bounded
                used += len(encoded) + 1
            return result
        return value

    @staticmethod
    def _hard_bound_text(text: str, budget: int, *, marker: str) -> str:
        """Deterministic head/tail compaction with an absolute char ceiling."""
        text = str(text)
        if budget <= 0:
            return ""
        if len(text) <= budget:
            return text
        if len(marker) >= budget:
            return marker[:budget]
        remaining = budget - len(marker)
        head = (remaining + 1) // 2
        tail = remaining // 2
        return text[:head] + marker + (text[-tail:] if tail else "")

    @staticmethod
    def _coverage_report_dict(
        report: ResearchCoverageReport,
    ) -> dict:
        return {
            "is_valid": report.is_valid,
            "errors": [
                {
                    "code": issue.code.value,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
                for issue in report.errors
            ],
            "warnings": [
                {
                    "code": issue.code.value,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
                for issue in report.warnings
            ],
        }

    @staticmethod
    def _semantic_report_dict(report: ResearchSemanticReport) -> dict:
        return {
            "is_valid": report.is_valid,
            "useful_analysis": report.useful_analysis,
            "supported_dimensions": list(report.supported_dimensions),
            "errors": [
                {
                    "code": issue.code.value,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
                for issue in report.errors
            ],
            "warnings": [
                {
                    "code": issue.code.value,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
                for issue in report.warnings
            ],
        }

    @staticmethod
    def _semantic_repair_instructions(report: ResearchSemanticReport) -> str:
        if report.is_valid:
            return "SEMANTIC QUALITY VALIDATOR: no semantic errors."
        lines = [
            "SEMANTIC QUALITY VALIDATOR ERRORS:",
            *[
                f"- {issue.code.value}: {issue.message}"
                for issue in report.errors
            ],
            "STATUS REPAIR RULES:",
            "- If useful multi-sided analysis exists but material evidence is "
            "still missing, use PARTIAL and set requires_additional_research=true.",
            "- Use INSUFFICIENT_EVIDENCE only when the supplied evidence is too "
            "weak to form a useful research assessment.",
            "- Do not downgrade useful supported analysis to "
            "INSUFFICIENT_EVIDENCE merely because material unknowns remain.",
            "- COMPLETE requires sufficient evidence and no material additional "
            "research requirement.",
            "Repair only what the supplied evidence supports. Do not invent "
            "facts merely to satisfy the validator.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _research_id(inference_id: str) -> str:
        suffix = inference_id.removeprefix("AI-")
        return f"RES-{suffix}"

    def _build_prompt(
        self,
        candidate: ScanCandidate,
        evidence: list[ResearchEvidence],
    ) -> str:
        evidence_block = "\n\n".join(
            self._format_evidence(index, item)
            for index, item in enumerate(evidence, start=1)
        )

        return f"""You are the research layer of a portfolio CIO system.

Your job is to transform supplied evidence into a disciplined, structured
research assessment. You are NOT making a trade decision and you must not
invent facts.

Candidate:
- ticker: {candidate.ticker}
- origin: {candidate.origin.value}
- scanner action hypothesis: {candidate.action.value}
- signal type: {candidate.signal_type.value}
- scanner confidence: {candidate.scanner_confidence}
- scanner thesis: {candidate.thesis_summary}

SUPPLIED EVIDENCE
{evidence_block}

EVIDENCE-ONLY RULES
1. Use only facts supported by SUPPLIED EVIDENCE.
2. Do not browse, infer missing numerical facts, invent consensus, valuation,
   analyst targets, technical indicators, events, guidance, or market data.
3. Material as-of information missing from the evidence belongs in unknowns.
4. UNKNOWN means "not established by supplied evidence"; it does NOT mean
   that supported evidence cannot be analysed.
5. Analyse every materially supported claim even when other important fields
   are missing.
6. Distinguish evidence from interpretation. State uncertainty explicitly.

UNKNOWNS VERSUS FORWARD UNCERTAINTIES
U1. unknowns: facts that exist or should be knowable at the evidence date but
    are not in the supplied evidence (e.g. latest quarterly margins, consensus
    estimates, peer valuation). Phrase each as missing data.
U2. forward_uncertainties: future outcomes that no evidence available today
    can establish (future earnings path, sustainability of margins or
    valuation, market reaction, deal or regulatory outcomes, timing).
U3. Never put a missing as-of fact in forward_uncertainties and never put a
    future outcome in unknowns.
U4. Every investment has forward uncertainties. They never by themselves
    require additional research or prevent COMPLETE.
U5. A comparison or finer granularity of a measure the evidence supplies
    (peer or sector valuation when P/E or analyst targets are supplied,
    company guidance when analyst estimates are supplied, the latest quarter
    when trailing margins are supplied) may be listed in unknowns but does not
    by itself require additional research or prevent COMPLETE.

OUTPUT COMPACTION RULES
16. The structured result must be concise. Do not reproduce or summarize full
    articles and do not repeat the same evidence across multiple fields.
17. market_context, fundamental_context, technical_context, event_context and
    catalyst_assessment: maximum 3 short sentences each.
18. bull_case and bear_case: maximum 2 short sentences each.
19. key_risks, contradictory_evidence, unknowns and forward_uncertainties:
    maximum 5 items each;
    every item must be one short sentence.
20. Prefer null or an empty list to filler prose when evidence is absent.
21. The entire JSON response should normally fit well below 6,000 output
    tokens. Return ONLY the requested structured JSON.

STRUCTURED EVIDENCE UTILIZATION RULES
7. The structured context fields are canonical research outputs, not optional
   prose decoration. If supplied evidence materially supports a category,
   summarize that evidence in its matching field:
   - market_context: broad market/sector/peer price or positioning context.
   - fundamental_context: revenue, earnings, margins, cash flow, balance
     sheet, valuation or other company fundamentals actually supplied.
   - technical_context: supplied price returns, trend, moving averages, RSI,
     volume/RVOL, volatility or other technical measurements.
   - event_context: supplied earnings, guidance, analyst action, product,
     customer deployment, investor day, regulatory, M&A, macro or other
     identifiable event/news context.
   - catalyst_assessment: evidence-supported mechanism that could change
     expectations, positioning or future fundamentals.
8. NEVER leave a structured context field null merely because the same
   supported information is discussed in bull_case, bear_case, risks,
   contradictions or unknowns.
9. Conversely, leave a field null when its category is genuinely unsupported.
   Do not fill fields merely for completeness.
10. Do not list an item as unknown if the supplied evidence already establishes
    it. When RSI, moving averages, relative volume or volatility are supplied,
    never list any of them, or "technical indicators", as unknown.
11. bull_case and bear_case must synthesize implications from the structured
    evidence; they do not replace structured context fields.
12. contradictory_evidence is for genuine tension among supplied facts or
    interpretations, not simply for repeating every bear-case risk.
13. key_risks should contain material downside/uncertainty risks supported by
    the evidence or directly arising from clearly identified missing data.

EXPECTATIONS / PRICED-IN
14. Use NOT_PRICED_IN, PARTIALLY_PRICED_IN, or LARGELY_PRICED_IN only when
    supplied evidence supports that judgment. Otherwise use UNKNOWN.
15. Strong recent price performance alone does not prove how much a catalyst
    is priced in.

RESEARCH STATUS / QUALITY
16. COMPLETE means the supplied evidence is sufficient for the requested
    research assessment and no material as-of fact is missing from unknowns.
    forward_uncertainties do not prevent COMPLETE.
17. PARTIAL means useful analysis is possible but a material as-of fact
    listed in unknowns remains missing.
18. INSUFFICIENT_EVIDENCE means evidence is too weak to form a useful research
    assessment.
19. LOW evidence quality plus material unknowns should normally be PARTIAL or
    INSUFFICIENT_EVIDENCE, not COMPLETE.
20. requires_additional_research must be true for INSUFFICIENT_EVIDENCE.
    COMPLETE must not require additional research.
20b. research_confidence (0.0-1.0) is your confidence that this assessment
    correctly characterises the opportunity from the supplied evidence, not
    the probability that a trade succeeds. Anchors: 0.2 weak or conflicting
    evidence; 0.5 mixed evidence; 0.7 clear evidence with material
    uncertainty; 0.85 or more broad, consistent evidence. Never 0.0 when the
    evidence supports a useful assessment.

DECISION BOUNDARY
21. Do not recommend LONG, SHORT, BUY, SELL, position size, entry, stop or
    target. The scanner action is a hypothesis to research, not a conclusion.
22. Return only the requested structured output.

Before returning, perform this silent coverage check:
- If evidence contains technical measurements, technical_context must be
  populated and must mention the material supplied measurements.
- If evidence contains identifiable company/event/news developments,
  event_context must be populated.
- If those developments provide a plausible evidence-supported mechanism for
  changing expectations or fundamentals, catalyst_assessment must be
  populated.
- If analyst evidence supplies current price and explicit price targets,
  expectations_assessment must not be UNKNOWN; classify priced-in state only
  from those supplied comparisons.
- Preserve supplied numeric magnitudes and units exactly. Never shift a
  decimal place or silently rescale a fundamental value.
- Remove unknowns that contradict facts already present in supplied evidence.
- Every unknowns item is missing as-of data; every forward_uncertainties item
  is a future outcome.
"""

    @staticmethod
    def _format_evidence(index: int, item: ResearchEvidence) -> str:
        published = (
            item.published_at.isoformat()
            if item.published_at is not None
            else "UNKNOWN"
        )
        return (
            f"[Evidence {index}]\n"
            f"id: {item.evidence_id}\n"
            f"source_type: {item.source_type}\n"
            f"published_at: {published}\n"
            f"text: {item.text}"
        )
