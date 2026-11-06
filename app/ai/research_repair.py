from __future__ import annotations

from dataclasses import dataclass

from app.ai.models import AIRequest, AITask, DataSensitivity, ReasoningMode, ResponseFormat
from app.ai.provider import AIModelProvider
from app.ai.research_service import ResearchEvidence, ResearchModelOutput
from app.ai.research_validator import ResearchCoverageReport, ResearchCoverageValidator


@dataclass(frozen=True)
class ResearchRepairResult:
    output: ResearchModelOutput
    initial_report: ResearchCoverageReport
    final_report: ResearchCoverageReport
    repair_attempted: bool


class ResearchRepairService:
    def __init__(self, provider: AIModelProvider, validator: ResearchCoverageValidator | None = None):
        self.provider = provider
        self.validator = validator or ResearchCoverageValidator()

    def validate_or_repair(
        self,
        *,
        ticker: str,
        candidate_context: str,
        evidence: list[ResearchEvidence],
        output: ResearchModelOutput,
        sensitivity: DataSensitivity = DataSensitivity.PUBLIC,
    ) -> ResearchRepairResult:
        initial = self.validator.validate(output, evidence)
        if initial.is_valid:
            return ResearchRepairResult(output, initial, initial, False)

        request = AIRequest(
            task=AITask.RESEARCH,
            prompt=self._build_repair_prompt(
                ticker=ticker,
                candidate_context=candidate_context,
                evidence=evidence,
                previous_output=output,
                report=initial,
            ),
            sensitivity=sensitivity,
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
            output_schema=ResearchModelOutput,
            metadata={
                "repair": True,
                "repair_pass": 1,
                "ticker": ticker.upper(),
                "coverage_error_codes": [x.code.value for x in initial.errors],
            },
        )
        response = self.provider.infer(request)
        if response.structured_output is None:
            raise ValueError("research repair provider returned no structured output")

        repaired = ResearchModelOutput.model_validate(response.structured_output)
        final = self.validator.validate(repaired, evidence)
        return ResearchRepairResult(repaired, initial, final, True)

    @staticmethod
    def _build_repair_prompt(*, ticker, candidate_context, evidence, previous_output, report):
        evidence_text = "\n\n".join(
            f"[{item.evidence_id}] source={item.source_type}\n{item.text}"
            for item in evidence
        )
        return (
            f"Ticker: {ticker.upper()}\n"
            f"Candidate context: {candidate_context}\n\n"
            f"{report.repair_instructions()}\n\n"
            "SUPPLIED EVIDENCE:\n"
            f"{evidence_text}\n\n"
            "PREVIOUS STRUCTURED OUTPUT:\n"
            f"{previous_output.model_dump_json(indent=2)}\n"
        )
