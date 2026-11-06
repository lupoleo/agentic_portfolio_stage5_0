import pytest

from app.scanner.instrument_eligibility import (
    ClassificationReviewFlag,
    InstrumentEligibilityPolicy,
    InstrumentEligibilityReason,
    InstrumentEligibilityStatus,
    classification_review_flags,
    evaluate_instrument_eligibility,
    scanner_v1_instrument_eligibility_policy,
)
from app.scanner.instrument_taxonomy import CanonicalInstrumentType
from app.scanner.universe_models import MarketListing


def listing(
    *,
    symbol="ABC",
    exchange="NASDAQ",
    instrument_type="COMMON STOCK",
    name="Example Corporation",
    isin=None,
):
    return MarketListing(
        symbol=symbol,
        exchange=exchange,
        market=exchange,
        region="US",
        currency="USD",
        instrument_type=instrument_type,
        name=name,
        isin=isin,
    )


def test_v1_policy_admits_only_common_stock():
    policy = scanner_v1_instrument_eligibility_policy()
    assert policy.eligible_types == {
        CanonicalInstrumentType.COMMON_STOCK
    }
    assert policy.deferred_types == {
        CanonicalInstrumentType.ETF,
        CanonicalInstrumentType.ETC,
        CanonicalInstrumentType.PREFERRED_STOCK,
    }


def test_ordinary_common_stock_is_eligible_without_isin():
    decision = evaluate_instrument_eligibility(listing(isin=None))
    assert decision.status is InstrumentEligibilityStatus.ELIGIBLE
    assert decision.reason_code is InstrumentEligibilityReason.TYPE_ELIGIBLE
    assert decision.automatically_admitted is True
    assert decision.review_flags == ()


def test_bit_common_stock_alias_is_eligible():
    decision = evaluate_instrument_eligibility(
        listing(
            symbol="A2A",
            exchange="BIT",
            instrument_type="COMMON_STOCK",
            name="A2a",
        )
    )
    assert decision.status is InstrumentEligibilityStatus.ELIGIBLE
    assert decision.canonical_type is CanonicalInstrumentType.COMMON_STOCK


@pytest.mark.parametrize(
    ("instrument_type", "canonical"),
    [
        ("ETF", CanonicalInstrumentType.ETF),
        ("ETC", CanonicalInstrumentType.ETC),
        ("PREFERRED STOCK", CanonicalInstrumentType.PREFERRED_STOCK),
    ],
)
def test_deferred_types_are_not_automatically_admitted(
    instrument_type,
    canonical,
):
    decision = evaluate_instrument_eligibility(
        listing(instrument_type=instrument_type)
    )
    assert decision.canonical_type is canonical
    assert decision.status is InstrumentEligibilityStatus.INELIGIBLE
    assert decision.reason_code is InstrumentEligibilityReason.TYPE_DEFERRED


@pytest.mark.parametrize(
    "instrument_type",
    ["FUND", "MUTUAL FUND", "BOND", "NOTES", "WARRANT", "UNIT", "INDEX"],
)
def test_out_of_scope_types_are_ineligible(instrument_type):
    decision = evaluate_instrument_eligibility(
        listing(instrument_type=instrument_type)
    )
    assert decision.status is InstrumentEligibilityStatus.INELIGIBLE
    assert decision.reason_code is (
        InstrumentEligibilityReason.TYPE_INELIGIBLE
    )


@pytest.mark.parametrize("instrument_type", [None, "", "RIGHT", "CRYPTO"])
def test_unknown_types_fail_closed(instrument_type):
    decision = evaluate_instrument_eligibility(
        listing(instrument_type=instrument_type)
    )
    assert decision.status is InstrumentEligibilityStatus.INELIGIBLE
    assert decision.reason_code is InstrumentEligibilityReason.TYPE_UNKNOWN


@pytest.mark.parametrize(
    ("symbol", "name", "flag"),
    [
        (
            "AACPR",
            "Apogee Acquisition Corp Rights",
            ClassificationReviewFlag.RIGHT,
        ),
        (
            "BKKT-WT",
            "Bakkt Holdings Inc. Warrant",
            ClassificationReviewFlag.WARRANT,
        ),
        (
            "NMM",
            "Navios Maritime Partners LP Unit",
            ClassificationReviewFlag.UNIT,
        ),
        (
            "HYPE",
            "21Shares Hyperliquid ETP",
            ClassificationReviewFlag.EXCHANGE_TRADED_PRODUCT,
        ),
        (
            "SCCC",
            "Sachem Capital Corp. 7.75% Note",
            ClassificationReviewFlag.DEBT_SECURITY,
        ),
    ],
)
def test_high_confidence_conflict_requires_review(symbol, name, flag):
    decision = evaluate_instrument_eligibility(
        listing(symbol=symbol, name=name)
    )
    assert decision.status is InstrumentEligibilityStatus.REVIEW_REQUIRED
    assert decision.reason_code is (
        InstrumentEligibilityReason.CLASSIFICATION_REVIEW_REQUIRED
    )
    assert flag in decision.review_flags
    assert decision.automatically_admitted is False


@pytest.mark.parametrize(
    "name",
    [
        "NOTE AB",
        "Law Debenture Corp",
        "Rights and Issues Investment Trust Public Limited Company",
        "United Airlines Holdings Inc.",
        "Netflix Inc.",
        "Funding Circle Holdings PLC",
    ],
)
def test_known_name_false_positives_are_not_flagged(name):
    assert classification_review_flags(listing(name=name)) == ()


def test_collective_vehicle_is_not_silently_reclassified():
    value = listing(name="Bancroft Fund Limited")
    decision = evaluate_instrument_eligibility(value)
    assert decision.status is InstrumentEligibilityStatus.ELIGIBLE
    assert decision.canonical_type is CanonicalInstrumentType.COMMON_STOCK
    assert decision.review_flags == ()


def test_decision_is_auditable():
    decision = evaluate_instrument_eligibility(listing(symbol="NVDA"))
    assert decision.listing_key.exchange == "NASDAQ"
    assert decision.listing_key.symbol == "NVDA"
    assert decision.raw_instrument_type == "COMMON STOCK"
    assert decision.policy_id == "scanner-v1-instrument-eligibility"
    assert decision.policy_version == "1"
    assert decision.message


def test_policy_rejects_overlapping_type_sets():
    with pytest.raises(ValueError, match="disjoint"):
        InstrumentEligibilityPolicy(
            eligible_types=frozenset({CanonicalInstrumentType.ETF}),
            deferred_types=frozenset({CanonicalInstrumentType.ETF}),
        )


def test_policy_rejects_unknown_as_eligible():
    with pytest.raises(ValueError, match="UNKNOWN"):
        InstrumentEligibilityPolicy(
            eligible_types=frozenset({CanonicalInstrumentType.UNKNOWN}),
            deferred_types=frozenset(),
        )
