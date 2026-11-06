import pytest

from app.scanner.instrument_taxonomy import (
    CanonicalInstrumentType,
    RAW_TYPE_MAP,
    normalize_raw_instrument_type,
    resolve_instrument_type,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("COMMON STOCK", CanonicalInstrumentType.COMMON_STOCK),
        ("COMMON_STOCK", CanonicalInstrumentType.COMMON_STOCK),
        ("PREFERRED STOCK", CanonicalInstrumentType.PREFERRED_STOCK),
        ("ETF", CanonicalInstrumentType.ETF),
        ("ETC", CanonicalInstrumentType.ETC),
        ("FUND", CanonicalInstrumentType.FUND),
        ("MUTUAL FUND", CanonicalInstrumentType.MUTUAL_FUND),
        ("BOND", CanonicalInstrumentType.BOND),
        ("NOTE", CanonicalInstrumentType.NOTE),
        ("NOTES", CanonicalInstrumentType.NOTE),
        ("WARRANT", CanonicalInstrumentType.WARRANT),
        ("UNIT", CanonicalInstrumentType.UNIT),
        ("INDEX", CanonicalInstrumentType.INDEX),
    ],
)
def test_known_raw_values_resolve_to_canonical_type(raw, expected):
    resolution = resolve_instrument_type(raw)
    assert resolution.canonical_type is expected
    assert resolution.recognized is True


def test_provider_common_stock_spellings_are_synonyms():
    spaced = resolve_instrument_type("COMMON STOCK")
    underscored = resolve_instrument_type("COMMON_STOCK")
    assert spaced.canonical_type is CanonicalInstrumentType.COMMON_STOCK
    assert underscored.canonical_type is spaced.canonical_type


def test_normalization_is_case_space_and_whitespace_stable():
    assert normalize_raw_instrument_type(" common   stock ") == "COMMON STOCK"
    assert (
        resolve_instrument_type(" common   stock ").canonical_type
        is CanonicalInstrumentType.COMMON_STOCK
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "RIGHT", "CRYPTO"])
def test_unknown_or_missing_values_fail_closed(raw):
    resolution = resolve_instrument_type(raw)
    assert resolution.canonical_type is CanonicalInstrumentType.UNKNOWN
    assert resolution.recognized is False


def test_resolution_preserves_original_provider_value():
    resolution = resolve_instrument_type(" common   stock ")
    assert resolution.raw_value == " common   stock "
    assert resolution.normalized_raw_value == "COMMON STOCK"


def test_raw_mapping_is_explicit_and_contains_no_name_heuristics():
    assert "RIGHT" not in RAW_TYPE_MAP
    assert "TRUST" not in RAW_TYPE_MAP
    assert "INVESTMENT TRUST" not in RAW_TYPE_MAP
