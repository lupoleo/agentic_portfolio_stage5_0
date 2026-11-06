from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import ResearchModelOutput, ResearchService

def base():
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        technical_context=None,event_context=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        evidence_quality=EvidenceQuality.HIGH,research_confidence=.7,
        requires_additional_research=True)

def test_schema_only_authorized_fields():
    m=ResearchService._build_repair_output_schema({"technical_context","event_context"})
    assert set(m.model_fields)=={"technical_context","event_context"}

def test_unknown_field_fails_closed():
    try: ResearchService._build_repair_output_schema({"bad_field"})
    except ValueError: pass
    else: raise AssertionError("ValueError expected")

def test_merge_only_scoped_fields():
    old=base().model_copy(update={"bull_case":"preserve me"})
    m=ResearchService._build_repair_output_schema({"technical_context","event_context"})
    repair=m.model_validate({"technical_context":"RSI 63","event_context":"earnings Thursday"})
    new=ResearchService._merge_repair_output(old,repair,{"technical_context","event_context"})
    assert new.technical_context=="RSI 63"
    assert new.event_context=="earnings Thursday"
    assert new.bull_case=="preserve me"

def test_omitted_repair_field_preserves_old_value():
    old=base().model_copy(update={"technical_context":"existing"})
    m=ResearchService._build_repair_output_schema({"technical_context","event_context"})
    repair=m.model_validate({"event_context":"new event"})
    new=ResearchService._merge_repair_output(old,repair,{"technical_context","event_context"})
    assert new.technical_context=="existing"
    assert new.event_context=="new event"

def test_status_fields_supported():
    m=ResearchService._build_repair_output_schema({"research_status","requires_additional_research"})
    x=m.model_validate({"research_status":"PARTIAL","requires_additional_research":True})
    assert x.research_status==ResearchStatus.PARTIAL
    assert x.requires_additional_research is True
