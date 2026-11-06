from types import SimpleNamespace

from app.scanner.research_integration_contracts import (
    HypothesisOutcomeStatus,
)
from app.scanner.research_integration_service import (
    _select_pending_hypotheses,
)


def hypothesis(identifier):
    return SimpleNamespace(
        hypothesis_id=identifier,
    )


def outcome(identifier, status):
    return SimpleNamespace(
        hypothesis_id=identifier,
        status=status,
    )


def test_resume_budget_follows_terminal_work():
    hypotheses = tuple(
        hypothesis(f"hyp-{index}")
        for index in range(1, 9)
    )
    outcomes = (
        outcome(
            "hyp-1",
            HypothesisOutcomeStatus.RESEARCHED,
        ),
        outcome(
            "hyp-2",
            HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
        ),
        outcome(
            "hyp-3",
            HypothesisOutcomeStatus.EXCLUDED,
        ),
        outcome(
            "hyp-4",
            HypothesisOutcomeStatus.PENDING,
        ),
    )

    selected = _select_pending_hypotheses(
        hypotheses,
        outcomes,
        max_hypotheses=4,
    )

    assert tuple(
        item.hypothesis_id
        for item in selected
    ) == (
        "hyp-4",
        "hyp-5",
        "hyp-6",
        "hyp-7",
    )


def test_unbounded_resume_keeps_nonterminal_work():
    hypotheses = tuple(
        hypothesis(f"hyp-{index}")
        for index in range(1, 6)
    )
    outcomes = (
        outcome(
            "hyp-1",
            HypothesisOutcomeStatus.RESEARCHED,
        ),
        outcome(
            "hyp-2",
            HypothesisOutcomeStatus.PENDING,
        ),
    )

    selected = _select_pending_hypotheses(
        hypotheses,
        outcomes,
        max_hypotheses=None,
    )

    assert tuple(
        item.hypothesis_id
        for item in selected
    ) == (
        "hyp-2",
        "hyp-3",
        "hyp-4",
        "hyp-5",
    )

def test_bounded_resume_prioritizes_directional_candidates():
    from types import SimpleNamespace

    from app.scanner.research_integration_contracts import (
        ResearchHypothesisKind,
    )
    from app.scanner.research_integration_service import (
        _select_pending_hypotheses,
    )

    hypotheses = (
        SimpleNamespace(
            hypothesis_id="monitor-1",
            kind=ResearchHypothesisKind.PORTFOLIO_MONITOR,
        ),
        SimpleNamespace(
            hypothesis_id="monitor-2",
            kind=ResearchHypothesisKind.PORTFOLIO_MONITOR,
        ),
        SimpleNamespace(
            hypothesis_id="candidate-long",
            kind=ResearchHypothesisKind.NEW_LONG,
        ),
        SimpleNamespace(
            hypothesis_id="candidate-short",
            kind=ResearchHypothesisKind.NEW_SHORT,
        ),
        SimpleNamespace(
            hypothesis_id="monitor-3",
            kind=ResearchHypothesisKind.PORTFOLIO_MONITOR,
        ),
    )

    selected = _select_pending_hypotheses(
        hypotheses,
        outcomes=(),
        max_hypotheses=2,
    )

    assert tuple(
        value.hypothesis_id
        for value in selected
    ) == (
        "candidate-long",
        "candidate-short",
    )


def test_unbounded_resume_preserves_canonical_order():
    from types import SimpleNamespace

    from app.scanner.research_integration_contracts import (
        ResearchHypothesisKind,
    )
    from app.scanner.research_integration_service import (
        _select_pending_hypotheses,
    )

    hypotheses = (
        SimpleNamespace(
            hypothesis_id="monitor-first",
            kind=ResearchHypothesisKind.PORTFOLIO_MONITOR,
        ),
        SimpleNamespace(
            hypothesis_id="candidate-second",
            kind=ResearchHypothesisKind.NEW_LONG,
        ),
    )

    selected = _select_pending_hypotheses(
        hypotheses,
        outcomes=(),
        max_hypotheses=None,
    )

    assert tuple(
        value.hypothesis_id
        for value in selected
    ) == (
        "monitor-first",
        "candidate-second",
    )
