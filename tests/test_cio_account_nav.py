from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cio.models import (
    AccountState,
    Currency,
    CurrencyCash,
    DataSource,
    RiskConstraints,
)


NOW = datetime(
    2026,
    8,
    21,
    16,
    0,
    tzinfo=timezone.utc,
)


def _cash() -> list[CurrencyCash]:

    return [
        CurrencyCash(
            currency=Currency.EUR,
            available=20_000,
            reserve=5_000,
        ),
        CurrencyCash(
            currency=Currency.USD,
            available=1_000,
            reserve=0,
        ),
    ]


def test_account_state_accepts_canonical_equity():

    state = AccountState(
        account_state_id="ACC-NAV-001",
        timestamp=NOW,
        cash=_cash(),
        account_equity_eur=400_000,
        constraints=RiskConstraints(),
        source=DataSource.OPERATOR,
    )

    assert (
        state.account_equity_eur
        == 400_000
    )

    assert (
        state.canonical_nav_eur
        == 400_000
    )


def test_account_state_nav_is_optional_for_backward_compatibility():

    state = AccountState(
        account_state_id="ACC-NAV-OLD",
        timestamp=NOW,
        cash=_cash(),
        constraints=RiskConstraints(),
        source=DataSource.OPERATOR,
    )

    assert (
        state.account_equity_eur
        is None
    )

    assert (
        state.canonical_nav_eur
        is None
    )


def test_account_state_rejects_zero_nav():

    with pytest.raises(
        ValueError,
    ):

        AccountState(
            account_state_id="ACC-NAV-ZERO",
            timestamp=NOW,
            cash=_cash(),
            account_equity_eur=0,
            constraints=RiskConstraints(),
            source=DataSource.OPERATOR,
        )


def test_account_state_rejects_negative_nav():

    with pytest.raises(
        ValueError,
    ):

        AccountState(
            account_state_id="ACC-NAV-NEG",
            timestamp=NOW,
            cash=_cash(),
            account_equity_eur=-1,
            constraints=RiskConstraints(),
            source=DataSource.OPERATOR,
        )