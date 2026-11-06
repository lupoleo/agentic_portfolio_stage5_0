from types import SimpleNamespace

from tools.live_opportunity_scoring_provenance_8c2c import (
    _print_provenance_diagnostic,
)


def _assessment(score, ids):
    return SimpleNamespace(score=score, supporting_evidence_ids=ids)


def test_diagnostic_prints_canonical_and_outside_ids(capsys):
    research = SimpleNamespace(
        ticker="SNPS",
        evidence_ids=["E1", "E2"],
    )
    components = SimpleNamespace(
        thesis=_assessment(60, ["E1"]),
        catalyst=_assessment(70, ["E3"]),
        fundamental=_assessment(None, []),
        technical=_assessment(65, ["E2"]),
        expectations=_assessment(None, []),
    )

    _print_provenance_diagnostic(research, components)
    out = capsys.readouterr().out

    assert "canonical_research_evidence_count: 2" in out
    assert "cited_minus_canonical: ['E3']" in out
    assert "outside_canonical: ['E3']" in out


def test_diagnostic_reports_clean_grounding(capsys):
    research = SimpleNamespace(ticker="PATH", evidence_ids=["E1"])
    components = SimpleNamespace(
        thesis=_assessment(60, ["E1"]),
        catalyst=_assessment(None, []),
        fundamental=_assessment(None, []),
        technical=_assessment(65, ["E1"]),
        expectations=_assessment(None, []),
    )

    _print_provenance_diagnostic(research, components)
    out = capsys.readouterr().out

    assert "cited_minus_canonical: NONE" in out
