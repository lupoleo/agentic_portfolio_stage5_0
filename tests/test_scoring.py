from app.analysis.scoring import calculate_position_alignment


def test_short_bearish_is_aligned():
    assert calculate_position_alignment("SHORT", "BEARISH") == "ALIGNED"


def test_short_bullish_is_against():
    assert calculate_position_alignment("SHORT", "BULLISH") == "AGAINST"


def test_short_neutral_is_neutral():
    assert calculate_position_alignment("SHORT", "NEUTRAL") == "NEUTRAL"
