import pytest

from app.scanner.exchange_policy import (
    EnabledExchange,
    EnabledExchangePolicy,
    ExchangeCoverageScope,
    scanner_v1_pilot_policy,
)


def entry(
    exchange="LU",
    *,
    provider="eodhd-exchange-symbols",
    coverage=ExchangeCoverageScope.FULL_EXCHANGE,
    enabled=True,
):
    return EnabledExchange(
        exchange_code=exchange,
        provider_id=provider,
        coverage_scope=coverage,
        enabled=enabled,
    )


def test_entry_canonicalizes_exchange_code():
    value = entry(" lu ")

    assert value.exchange_code == "LU"
    assert value.provider_id == "eodhd-exchange-symbols"


def test_string_coverage_scope_is_canonicalized():
    value = entry(
        coverage="FULL_EXCHANGE",
    )

    assert (
        value.coverage_scope
        is ExchangeCoverageScope.FULL_EXCHANGE
    )


def test_invalid_coverage_scope_is_rejected():
    with pytest.raises(
        ValueError,
        match="unsupported coverage_scope",
    ):
        entry(coverage="UNKNOWN")


def test_policy_orders_entries_deterministically():
    policy = EnabledExchangePolicy(
        entries=(
            entry("LU"),
            entry(
                "BIT",
                provider="borsa-italiana-ftse-mib",
                coverage=(
                    ExchangeCoverageScope.INDEX_FALLBACK
                ),
            ),
        )
    )

    assert policy.enabled_exchange_codes == ("BIT", "LU")


def test_duplicate_exchange_is_rejected():
    with pytest.raises(
        ValueError,
        match="duplicate policy exchange_code",
    ):
        EnabledExchangePolicy(
            entries=(
                entry("LU"),
                entry("lu"),
            )
        )


def test_policy_requires_enabled_exchange():
    with pytest.raises(
        ValueError,
        match="enable at least one",
    ):
        EnabledExchangePolicy(
            entries=(
                entry(enabled=False),
            )
        )


def test_disabled_entries_are_retained_but_not_selected():
    policy = EnabledExchangePolicy(
        entries=(
            entry("LU"),
            entry(
                "LSE",
                enabled=False,
            ),
        )
    )

    assert len(policy.entries) == 2
    assert policy.enabled_exchange_codes == ("LU",)


def test_pilot_policy_is_explicit_and_mixed_coverage():
    policy = scanner_v1_pilot_policy()

    assert policy.policy_id == "scanner-v1-pilot"
    assert policy.enabled_exchange_codes == ("BIT", "LU")

    by_exchange = {
        item.exchange_code: item
        for item in policy.enabled_entries
    }

    assert (
        by_exchange["LU"].coverage_scope
        is ExchangeCoverageScope.FULL_EXCHANGE
    )
    assert (
        by_exchange["BIT"].coverage_scope
        is ExchangeCoverageScope.INDEX_FALLBACK
    )
