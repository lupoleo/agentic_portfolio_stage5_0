from pathlib import Path

TARGET = Path("app/ai/research_service.py")

if not TARGET.exists():
    raise SystemExit(f"Target not found: {TARGET}")

text = TARGET.read_text(encoding="utf-8")

if "deterministic_unknowns_canonicalized" in text:
    print("AI-8C.2b.2 already appears to be applied.")
    raise SystemExit(0)

old = '''        repair_attempted = False
        deterministic_status_normalized = False
'''
new = '''        repair_attempted = False
        deterministic_status_normalized = False
        deterministic_unknowns_canonicalized = False
'''
if old not in text:
    raise SystemExit(
        "Patch point 1 not found. research_service.py does not match the expected baseline."
    )
text = text.replace(old, new, 1)

old = '''            repaired_semantic = self.semantic_evaluator.evaluate(
                repaired_output, evidence
            )

            repaired_output, deterministic_status_normalized = (
                self._normalize_repaired_status(
'''
new = '''            repaired_semantic = self.semantic_evaluator.evaluate(
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
'''
if old not in text:
    raise SystemExit("Patch point 2 not found.")
text = text.replace(old, new, 1)

old = '                "deterministic_status_normalized": deterministic_status_normalized,\n'
new = (
    '                "deterministic_status_normalized": deterministic_status_normalized,\n'
    '                "deterministic_unknowns_canonicalized": (\n'
    '                    deterministic_unknowns_canonicalized\n'
    '                ),\n'
)
if old not in text:
    raise SystemExit("Patch point 3 not found.")
text = text.replace(old, new, 1)

marker = '''    @staticmethod
    def _normalize_repaired_status(
'''
helper = '''    def _canonicalize_conflicting_unknowns(
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

'''
if marker not in text:
    raise SystemExit("Patch point 4 not found.")
text = text.replace(marker, helper + marker, 1)

TARGET.write_text(text, encoding="utf-8")
print("Applied AI-8C.2b.2 to app/ai/research_service.py")
