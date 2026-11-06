from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    PortfolioCheckStatus,
    PortfolioConstraintCheck,
    PortfolioFitAssessment,
    PortfolioFitDecision,
)
from app.cio.storage import Stage3Store


def _assessment(
    *,
    assessment_id: str,
    opportunity_id: str = "OPP-1",
    snapshot_id: str = "SNAP-1",
    risk_state_id: str | None = "RISK-1",
    account_state_id: str | None = "ACC-2",
    created_at: datetime | None = None,
    ticker: str = "QCOM",
    direction: str = "LONG",
    score: float = 80.0,
) -> PortfolioFitAssessment:
    created_at = created_at or datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)

    return PortfolioFitAssessment(
        assessment_id=assessment_id,
        opportunity_id=opportunity_id,
        snapshot_id=snapshot_id,
        risk_state_id=risk_state_id,
        account_state_id=account_state_id,
        created_at=created_at,
        ticker=ticker,
        direction=direction,
        target_exposure_eur=10_000.0,
        decision=PortfolioFitDecision.PASS,
        constraint_checks=[
            PortfolioConstraintCheck(
                code="TEST_LIMIT",
                status=PortfolioCheckStatus.PASS,
                current_value=1.0,
                projected_value=2.0,
                limit_value=3.0,
                unit="EUR",
                reason="Within configured test limit.",
            )
        ],
        warnings=[],
        portfolio_fit_score=score,
        rationale="Deterministic persistence acceptance fixture.",
    )


def _bind_valid_provenance(store: Stage3Store, monkeypatch) -> None:
    opportunity = SimpleNamespace(
        opportunity_id="OPP-1",
        snapshot_id="SNAP-1",
        ticker="QCOM",
        direction="LONG",
    )
    snapshot = SimpleNamespace(
        snapshot_id="SNAP-1",
        # Deliberately historical/different from assessment account state.
        account_state_id="ACC-1",
    )
    risk_state = SimpleNamespace(
        risk_state_id="RISK-1",
        snapshot_id="SNAP-1",
    )
    account_state = SimpleNamespace(account_state_id="ACC-2")

    monkeypatch.setattr(store, "get_trade_opportunity", lambda _id: opportunity)
    monkeypatch.setattr(store, "get_portfolio_snapshot", lambda _id: snapshot)
    monkeypatch.setattr(store, "get_portfolio_risk_state", lambda _id: risk_state)
    monkeypatch.setattr(store, "get_account_state", lambda _id: account_state)


def test_round_trip_preserves_canonical_assessment(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    assessment = _assessment(assessment_id="PFIT-ROUNDTRIP")
    store.save_portfolio_fit_assessment(assessment)

    loaded = store.get_portfolio_fit_assessment("PFIT-ROUNDTRIP")

    assert loaded == assessment
    assert loaded.model_dump(mode="json") == assessment.model_dump(mode="json")


def test_relational_projection_is_queryable(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    assessment = _assessment(assessment_id="PFIT-PROJECTION", score=81.25)
    store.save_portfolio_fit_assessment(assessment)

    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT opportunity_id, snapshot_id, risk_state_id, account_state_id,
                   ticker, direction, decision, portfolio_fit_score,
                   scoring_coverage_pct, analytical_coverage_pct
            FROM portfolio_fit_assessments
            WHERE assessment_id = ?
            """,
            ("PFIT-PROJECTION",),
        ).fetchone()

    assert row["opportunity_id"] == "OPP-1"
    assert row["snapshot_id"] == "SNAP-1"
    assert row["risk_state_id"] == "RISK-1"
    assert row["account_state_id"] == "ACC-2"
    assert row["ticker"] == "QCOM"
    assert row["direction"] == "LONG"
    assert row["decision"] == "PASS"
    assert row["portfolio_fit_score"] == pytest.approx(81.25)
    assert row["scoring_coverage_pct"] is None
    assert row["analytical_coverage_pct"] is None


def test_upsert_replaces_same_assessment_id(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    first = _assessment(assessment_id="PFIT-UPSERT", score=70.0)
    second = _assessment(assessment_id="PFIT-UPSERT", score=90.0)

    store.save_portfolio_fit_assessment(first)
    store.save_portfolio_fit_assessment(second)

    loaded = store.get_portfolio_fit_assessment("PFIT-UPSERT")
    assert loaded.portfolio_fit_score == pytest.approx(90.0)

    with store._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM portfolio_fit_assessments WHERE assessment_id = ?",
            ("PFIT-UPSERT",),
        ).fetchone()[0]
    assert count == 1


def test_latest_and_list_use_deterministic_ordering(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    t0 = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    older = _assessment(assessment_id="PFIT-A", created_at=t0)
    tie_low = _assessment(assessment_id="PFIT-B", created_at=t0 + timedelta(minutes=1))
    tie_high = _assessment(assessment_id="PFIT-C", created_at=t0 + timedelta(minutes=1))

    for item in (tie_low, older, tie_high):
        store.save_portfolio_fit_assessment(item)

    latest = store.get_latest_portfolio_fit_assessment("OPP-1")
    listed = store.list_portfolio_fit_assessments("OPP-1")

    assert latest.assessment_id == "PFIT-C"
    assert [x.assessment_id for x in listed] == ["PFIT-C", "PFIT-B", "PFIT-A"]


def test_missing_opportunity_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    monkeypatch.setattr(store, "get_trade_opportunity", lambda _id: None)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-NO-OPP")
        )


def test_missing_snapshot_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    opportunity = SimpleNamespace(
        opportunity_id="OPP-1",
        snapshot_id="SNAP-1",
        ticker="QCOM",
        direction="LONG",
    )
    monkeypatch.setattr(store, "get_trade_opportunity", lambda _id: opportunity)
    monkeypatch.setattr(store, "get_portfolio_snapshot", lambda _id: None)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-NO-SNAP")
        )


def test_opportunity_snapshot_mismatch_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    opportunity = SimpleNamespace(
        opportunity_id="OPP-1",
        snapshot_id="SNAP-OTHER",
        ticker="QCOM",
        direction="LONG",
    )
    snapshot = SimpleNamespace(snapshot_id="SNAP-1", account_state_id="ACC-1")

    monkeypatch.setattr(store, "get_trade_opportunity", lambda _id: opportunity)
    monkeypatch.setattr(store, "get_portfolio_snapshot", lambda _id: snapshot)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-SNAP-MISMATCH")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ticker", "NVDA"),
        ("direction", "SHORT"),
    ],
)
def test_opportunity_identity_mismatch_is_rejected(
    tmp_path, monkeypatch, field, value
):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    kwargs = {field: value}
    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(
                assessment_id=f"PFIT-{field.upper()}-MISMATCH",
                **kwargs,
            )
        )


def test_missing_risk_state_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)
    monkeypatch.setattr(store, "get_portfolio_risk_state", lambda _id: None)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-NO-RISK")
        )


def test_risk_state_snapshot_mismatch_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)
    wrong_risk = SimpleNamespace(
        risk_state_id="RISK-1",
        snapshot_id="SNAP-OTHER",
    )
    monkeypatch.setattr(store, "get_portfolio_risk_state", lambda _id: wrong_risk)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-RISK-SNAP-MISMATCH")
        )


def test_missing_account_state_is_rejected(tmp_path, monkeypatch):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)
    monkeypatch.setattr(store, "get_account_state", lambda _id: None)

    with pytest.raises(ValueError):
        store.save_portfolio_fit_assessment(
            _assessment(assessment_id="PFIT-NO-ACCOUNT")
        )


def test_assessment_account_may_differ_from_snapshot_historical_account(
    tmp_path, monkeypatch
):
    store = Stage3Store(tmp_path / "stage3.sqlite")
    _bind_valid_provenance(store, monkeypatch)

    # Snapshot provenance points to ACC-1, but the assessment correctly uses
    # the newer decision-time AccountState ACC-2.
    assessment = _assessment(
        assessment_id="PFIT-LATEST-ACCOUNT",
        account_state_id="ACC-2",
    )

    store.save_portfolio_fit_assessment(assessment)
    loaded = store.get_portfolio_fit_assessment("PFIT-LATEST-ACCOUNT")

    assert loaded.account_state_id == "ACC-2"
