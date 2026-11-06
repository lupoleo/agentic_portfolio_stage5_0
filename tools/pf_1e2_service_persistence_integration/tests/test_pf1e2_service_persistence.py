from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.cio.portfolio_filter_service import PortfolioFilterService


class FakeStore:
    def __init__(self):
        self.saved = []

    def save_portfolio_fit_assessment(self, assessment):
        self.saved.append(assessment)


def build_service(fake_assessment):
    service = object.__new__(PortfolioFilterService)
    service.store = FakeStore()

    calls = []

    def fake_assess(opportunity_id, *, as_of=None, assessment_id=None):
        calls.append(
            {
                "opportunity_id": opportunity_id,
                "as_of": as_of,
                "assessment_id": assessment_id,
            }
        )
        return fake_assessment

    service.assess = fake_assess
    return service, calls


def test_assess_and_persist_saves_exact_returned_assessment():
    assessment = SimpleNamespace(assessment_id="PFIT-1")
    service, calls = build_service(assessment)
    now = datetime(2026, 9, 10, 21, 30, tzinfo=timezone.utc)

    result = service.assess_and_persist(
        "OPP-1",
        as_of=now,
        assessment_id="PFIT-1",
    )

    assert result is assessment
    assert service.store.saved == [assessment]
    assert calls == [
        {
            "opportunity_id": "OPP-1",
            "as_of": now,
            "assessment_id": "PFIT-1",
        }
    ]


def test_plain_assess_remains_side_effect_free():
    assessment = SimpleNamespace(assessment_id="PFIT-2")
    service, _ = build_service(assessment)

    result = service.assess("OPP-2")

    assert result is assessment
    assert service.store.saved == []


def test_persistence_failure_is_propagated():
    assessment = SimpleNamespace(assessment_id="PFIT-3")
    service, _ = build_service(assessment)

    def fail(_assessment):
        raise RuntimeError("persist failed")

    service.store.save_portfolio_fit_assessment = fail

    with pytest.raises(RuntimeError, match="persist failed"):
        service.assess_and_persist("OPP-3")
