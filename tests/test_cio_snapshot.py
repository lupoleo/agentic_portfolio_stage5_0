from datetime import datetime, timezone

import pytest

from app.cio.snapshot import build_portfolio_snapshot, sha256_file


def test_sha256_file_is_deterministic(tmp_path):
    file_path = tmp_path / "portfolio.xlsx"
    file_path.write_bytes(b"fineco snapshot content")

    first = sha256_file(file_path)
    second = sha256_file(file_path)

    assert first == second
    assert len(first) == 64


def test_build_snapshot_uses_file_hash_and_utc_timestamp(tmp_path):
    file_path = tmp_path / "portfolio.xlsx"
    file_path.write_bytes(b"fineco snapshot content")

    timestamp = datetime(
        2026, 8, 17, 9, 15,
        tzinfo=timezone.utc,
    )

    snapshot = build_portfolio_snapshot(
        file_path,
        quant_engine_version="2.5.0",
        analyzed_positions=31,
        gross_exposure_eur=326_774.28,
        net_exposure_eur=326_774.28,
        account_state_id="ACC-1",
        timestamp=timestamp,
    )

    assert snapshot.snapshot_id.startswith("SNAP-20260817-091500-")
    assert snapshot.source_file_hash == sha256_file(file_path)
    assert snapshot.timestamp == timestamp
    assert snapshot.account_state_id == "ACC-1"


def test_build_snapshot_rejects_naive_timestamp(tmp_path):
    file_path = tmp_path / "portfolio.xlsx"
    file_path.write_bytes(b"x")

    with pytest.raises(ValueError):
        build_portfolio_snapshot(
            file_path,
            quant_engine_version="2.5.0",
            analyzed_positions=1,
            gross_exposure_eur=100,
            net_exposure_eur=100,
            timestamp=datetime(2026, 8, 17, 9, 15),
        )
