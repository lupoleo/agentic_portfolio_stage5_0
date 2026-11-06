from app.ai.research_validator import ResearchCoverageValidator
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import ResearchEvidence, ResearchModelOutput

def main():
    evidence = [
        ResearchEvidence(evidence_id="M1", source_type="MARKET",
                         text="20-session return 42.34%, SMA20 supplied, RSI14 72.53."),
        ResearchEvidence(evidence_id="N1", source_type="NEWS",
                         text="UiPath highlighted a Banco Azteca Maestro deployment."),
    ]
    output = ResearchModelOutput(
        research_status=ResearchStatus.COMPLETE,
        market_context=None, fundamental_context=None,
        technical_context=None, event_context=None, catalyst_assessment=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Technical momentum is strong and deployment news is positive.",
        bear_case="RSI is overbought.", key_risks=[],
        contradictory_evidence=["RSI14 is overbought and suggests correction risk."],
        unknowns=["Current valuation", "market context", "technical volatility"],
        evidence_quality=EvidenceQuality.LOW, research_confidence=0.70,
        requires_additional_research=False,
    )
    report = ResearchCoverageValidator().validate(output, evidence)
    print("Valid:", report.is_valid)
    for issue in report.issues:
        print(issue.severity.value, issue.code.value, "-", issue.message)
    print()
    print(report.repair_instructions())

if __name__ == "__main__":
    main()
