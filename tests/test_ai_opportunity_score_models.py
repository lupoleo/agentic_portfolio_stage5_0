from datetime import datetime,timezone
import pytest
from pydantic import ValidationError
from app.ai.opportunity_score_models import OpportunityScore,OpportunityScoringProfile,OpportunityScoringStatus
from app.ai.research_models import EvidenceQuality
NOW=datetime(2026,9,2,20,0,tzinfo=timezone.utc)
def payload(**o):
 d=dict(opportunity_score_id="OS1",candidate_id="C1",research_id="R1",scan_id="S1",created_at=NOW,ticker="path",scoring_status=OpportunityScoringStatus.SCORED,thesis_score=75,catalyst_score=80,fundamental_score=65,technical_score=78,expectations_score=60,raw_score=72.5,score_confidence=.75,confidence_adjusted_score=66.875,evidence_quality=EvidenceQuality.HIGH,evidence_coverage_score=.8333,research_confidence=.7,positive_factors=["Momentum"],negative_factors=["Valuation"],uncertainty_factors=["Consensus"],requires_additional_research=False,evidence_ids=["E1"],inference_ids=["I1"]); d.update(o); return d
def test_complete(): assert OpportunityScore(**payload()).ticker=="PATH"
def test_bounds():
 with pytest.raises(ValidationError): OpportunityScore(**payload(technical_score=101))
def test_partial():
 s=OpportunityScore(**payload(scoring_status=OpportunityScoringStatus.PARTIAL,fundamental_score=None,requires_additional_research=True)); assert s.fundamental_score is None
def test_scored_requires_all():
 with pytest.raises(ValidationError): OpportunityScore(**payload(fundamental_score=None))
def test_partial_requires_more():
 with pytest.raises(ValidationError): OpportunityScore(**payload(scoring_status=OpportunityScoringStatus.PARTIAL,fundamental_score=None,requires_additional_research=False))
def test_not_scorable():
 s=OpportunityScore(**payload(scoring_status=OpportunityScoringStatus.NOT_SCORABLE,thesis_score=None,catalyst_score=None,fundamental_score=None,technical_score=None,expectations_score=None,raw_score=None,confidence_adjusted_score=None,requires_additional_research=True)); assert s.raw_score is None
def test_not_scorable_rejects_aggregate():
 with pytest.raises(ValidationError): OpportunityScore(**payload(scoring_status=OpportunityScoringStatus.NOT_SCORABLE,raw_score=50,confidence_adjusted_score=None,requires_additional_research=True))
def test_extra_forbidden():
 with pytest.raises(ValidationError): OpportunityScore(**payload(portfolio_fit_score=90))
def test_event_profile(): assert OpportunityScore(**payload(scoring_profile=OpportunityScoringProfile.EVENT_DRIVEN)).scoring_profile==OpportunityScoringProfile.EVENT_DRIVEN
def test_blank_factor():
 with pytest.raises(ValidationError): OpportunityScore(**payload(positive_factors=[" "]))
