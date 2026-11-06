
from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(".")
LOCAL = ROOT / "app" / "ai" / "local_provider.py"
RESEARCH = ROOT / "app" / "ai" / "research_service.py"
SCORING = ROOT / "app" / "ai" / "opportunity_scoring_service.py"
TEST_LOCAL = ROOT / "tests" / "test_ai_local_provider.py"
TEST_SCORING = ROOT / "tests" / "test_ai_opportunity_scoring_service.py"

for path in (LOCAL, RESEARCH, SCORING, TEST_LOCAL, TEST_SCORING):
    if not path.exists():
        raise SystemExit(f"Required project file not found: {path}")

def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".pre_ai8c2_stability.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)

def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"Patch point not found: {label}")
    return text.replace(old, new, 1)

for p in (LOCAL, RESEARCH, SCORING, TEST_LOCAL, TEST_SCORING):
    backup(p)

# 1) LocalProvider: bounded RESEARCH headroom + one compact rescue.
text = LOCAL.read_text(encoding="utf-8")
text = text.replace(
    "research_reasoning_num_predict: int = 8192,",
    "research_reasoning_num_predict: int = 12288,",
    1,
)

old = '''        started = time.perf_counter()
        response_payload = self._transport(
            url,
            body,
            self.timeout_seconds,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0

        return self._normalize_response(
            response_payload=response_payload,
            ai_request=ai_request,
            latency_ms=latency_ms,
        )
'''
new = '''        started = time.perf_counter()
        response_payload = self._transport(
            url,
            body,
            self.timeout_seconds,
        )
        transport_retries = 0

        if self._should_retry_incomplete_structured_response(
            response_payload,
            ai_request,
        ):
            retry_payload = dict(payload)
            retry_payload["prompt"] = (
                ai_request.prompt
                + "\\n\\nCRITICAL COMPACT RETRY: The previous structured "
                "generation was incomplete. Return ONLY the requested JSON. "
                "Be extremely concise: no repeated evidence, no article "
                "summaries, context fields at most 3 short sentences, "
                "bull/bear cases at most 2 short sentences, and list fields "
                "at most 5 short items."
            )
            retry_payload["think"] = False
            retry_options = dict(retry_payload.get("options", {}))
            retry_options["num_predict"] = min(num_predict, 6144)
            retry_payload["options"] = retry_options

            retry_body = json.dumps(retry_payload).encode("utf-8")
            response_payload = self._transport(
                url,
                retry_body,
                self.timeout_seconds,
            )
            transport_retries = 1

        latency_ms = (time.perf_counter() - started) * 1000.0

        normalized = self._normalize_response(
            response_payload=response_payload,
            ai_request=ai_request,
            latency_ms=latency_ms,
        )
        if transport_retries:
            usage = dict(normalized.usage)
            usage["transport_retries"] = transport_retries
            normalized = normalized.model_copy(update={"usage": usage})
        return normalized
'''
text = replace_once(text, old, new, "LocalProvider infer transport block")

marker = '''    @staticmethod
    def _response_diagnostics(
'''
helper = '''    @staticmethod
    def _should_retry_incomplete_structured_response(
        response_payload: dict[str, Any],
        ai_request: AIRequest,
    ) -> bool:
        if ai_request.response_format is not ResponseFormat.JSON:
            return False

        raw_content = response_payload.get("response")
        if not isinstance(raw_content, str):
            return False

        try:
            json.loads(raw_content)
            return False
        except json.JSONDecodeError:
            pass

        return (
            response_payload.get("done") is False
            or response_payload.get("done_reason") == "length"
        )

'''
if "def _should_retry_incomplete_structured_response(" not in text:
    if marker not in text:
        raise SystemExit("Patch point not found: LocalProvider diagnostics marker")
    text = text.replace(marker, helper + marker, 1)

LOCAL.write_text(text, encoding="utf-8")

# 2) Research prompt compaction: solve verbosity rather than only raising ceiling.
text = RESEARCH.read_text(encoding="utf-8")
compaction_rules = '''OUTPUT COMPACTION RULES
16. The structured result must be concise. Do not reproduce or summarize full
    articles and do not repeat the same evidence across multiple fields.
17. market_context, fundamental_context, technical_context, event_context and
    catalyst_assessment: maximum 3 short sentences each.
18. bull_case and bear_case: maximum 2 short sentences each.
19. key_risks, contradictory_evidence and unknowns: maximum 5 items each;
    every item must be one short sentence.
20. Prefer null or an empty list to filler prose when evidence is absent.
21. The entire JSON response should normally fit well below 6,000 output
    tokens. Return ONLY the requested structured JSON.

'''
if "OUTPUT COMPACTION RULES" not in text:
    anchor = "STRUCTURED EVIDENCE UTILIZATION RULES\n"
    if anchor not in text:
        raise SystemExit("Patch point not found: ResearchService prompt rules")
    text = text.replace(anchor, compaction_rules + anchor, 1)
RESEARCH.write_text(text, encoding="utf-8")

# 3) Scoring repair: unresolved grounding -> component NULL, not whole-run crash.
text = SCORING.read_text(encoding="utf-8")
old = '''        for name in invalid_components:
            assessment = repaired.get(name)
            if assessment is None:
                raise ValueError(
                    f"Opportunity scoring grounding repair omitted {name}"
                )
            if hasattr(assessment, "model_dump"):
                assessment = assessment.model_dump()
            if not isinstance(assessment, dict):
                raise ValueError(
                    f"Opportunity scoring grounding repair returned invalid {name}"
                )

            score = assessment.get("score")
            cited = assessment.get("supporting_evidence_ids") or []
            if score is None:
                assessment["rationale"] = None
                assessment["supporting_evidence_ids"] = []
            elif not cited:
                raise ValueError(
                    f"Opportunity scoring grounding repair left {name} "
                    "scored without supporting evidence"
                )

            merged[name] = assessment
'''
new = '''        allowed_alias_set = set(allowed_aliases)

        for name in invalid_components:
            assessment = repaired.get(name)
            if hasattr(assessment, "model_dump"):
                assessment = assessment.model_dump()

            if not isinstance(assessment, dict):
                assessment = {
                    "score": None,
                    "rationale": None,
                    "supporting_evidence_ids": [],
                }

            score = assessment.get("score")
            cited = assessment.get("supporting_evidence_ids") or []
            cited_are_valid = bool(cited) and all(
                value in allowed_alias_set for value in cited
            )

            if score is None or not cited_are_valid:
                assessment["score"] = None
                assessment["rationale"] = None
                assessment["supporting_evidence_ids"] = []

            merged[name] = assessment
'''
text = replace_once(
    text,
    old,
    new,
    "OpportunityScoringService grounding repair block",
)
SCORING.write_text(text, encoding="utf-8")

# 4) Tests.
text = TEST_LOCAL.read_text(encoding="utf-8")
text = text.replace(
    "assert provider.research_reasoning_num_predict == 8192",
    "assert provider.research_reasoning_num_predict == 12288",
    1,
)

test_anchor = "def test_json_request_maps_to_ollama_json_format_and_parses_output():\n"
extra_tests = r'''
def test_incomplete_research_json_done_false_gets_one_compact_retry():
    captured = []

    def transport(url, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        if len(captured) == 1:
            return {
                "response": '{"market_context":"unterminated',
                "done": False,
            }
        return {
            "response": '{"status":"ok"}',
            "done": True,
            "eval_count": 20,
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.RESEARCH,
            prompt="Research.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert len(captured) == 2
    assert captured[0]["think"] is True
    assert captured[0]["options"]["num_predict"] == 12288
    assert captured[1]["think"] is False
    assert captured[1]["options"]["num_predict"] == 6144
    assert "CRITICAL COMPACT RETRY" in captured[1]["prompt"]
    assert response.structured_output == {"status": "ok"}
    assert response.usage["transport_retries"] == 1


def test_length_truncated_research_json_gets_one_compact_retry():
    calls = 0

    def transport(url, body, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "response": '{"market_context":"unterminated',
                "done": True,
                "done_reason": "length",
                "eval_count": 12288,
            }
        return {
            "response": '{"status":"ok"}',
            "done": True,
        }

    provider = LocalProvider(transport=transport)
    response = provider.infer(
        AIRequest(
            task=AITask.RESEARCH,
            prompt="Research.",
            reasoning_mode=ReasoningMode.REASONING,
            response_format=ResponseFormat.JSON,
        )
    )

    assert calls == 2
    assert response.structured_output == {"status": "ok"}


'''
if "test_incomplete_research_json_done_false_gets_one_compact_retry" not in text:
    if test_anchor not in text:
        raise SystemExit("Patch point not found: LocalProvider tests")
    text = text.replace(test_anchor, extra_tests + test_anchor, 1)
TEST_LOCAL.write_text(text, encoding="utf-8")

text = TEST_SCORING.read_text(encoding="utf-8")
old_test = '''def test_grounding_repair_fails_closed_if_component_remains_scored_without_evidence():
    initial = output(
        thesis={
            "score": 75,
            "rationale": "Coherent thesis.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "thesis": {
                    "score": 75,
                    "rationale": "Still unsupported.",
                    "supporting_evidence_ids": [],
                }
            },
        ]
    )

    with pytest.raises(
        ValueError,
        match="left thesis scored without supporting evidence",
    ):
        OpportunityScoringService(provider).score(
            research(), evidence_coverage_score=.80
        )


'''
new_test = '''def test_grounding_repair_fails_closed_at_component_level_if_still_unsupported():
    initial = output(
        thesis={
            "score": 75,
            "rationale": "Coherent thesis.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "thesis": {
                    "score": 75,
                    "rationale": "Still unsupported.",
                    "supporting_evidence_ids": [],
                }
            },
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.thesis.score is None
    assert result.components.thesis.rationale is None
    assert result.components.thesis.supporting_evidence_ids == []
    assert result.components.catalyst.score == 80


def test_grounding_repair_invalid_alias_is_canonicalized_to_null():
    initial = output(
        catalyst={
            "score": 70,
            "rationale": "Catalyst.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "catalyst": {
                    "score": 70,
                    "rationale": "Still invalid.",
                    "supporting_evidence_ids": ["FAKE"],
                }
            },
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.catalyst.score is None
    assert result.components.catalyst.supporting_evidence_ids == []


'''
if "test_grounding_repair_fails_closed_at_component_level_if_still_unsupported" not in text:
    if old_test not in text:
        raise SystemExit("Patch point not found: scoring grounding test")
    text = text.replace(old_test, new_test, 1)
TEST_SCORING.write_text(text, encoding="utf-8")

print("Applied AI-8C.2 consolidated stability patch.")
print("RESEARCH budget: 12288; one compact incomplete-JSON rescue;")
print("research prompt compaction; unresolved scoring grounding -> component NULL.")
