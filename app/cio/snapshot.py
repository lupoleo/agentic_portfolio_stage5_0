from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.cio.models import PortfolioSnapshot


def sha256_file(file_path: str | Path) -> str:
    """Return the SHA-256 hash of a file without loading it all into memory."""

    path = Path(file_path)
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def build_portfolio_snapshot(
    source_file: str | Path,
    *,
    quant_engine_version: str,
    analyzed_positions: int,
    gross_exposure_eur: float,
    net_exposure_eur: float,
    account_state_id: str | None = None,
    timestamp: datetime | None = None,
) -> PortfolioSnapshot:
    """
    Create an immutable Stage 3.0 snapshot descriptor for a Fineco export.

    The source file itself remains external. The contract stores the exact
    file path and SHA-256 hash so that a CIO decision can later be traced
    back to the precise broker snapshot used by the quantitative engine.
    """

    path = Path(source_file).resolve()

    if not path.exists():
        raise FileNotFoundError(path)

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        raise ValueError("snapshot timestamp must be timezone-aware")

    snapshot_id = (
        f"SNAP-{timestamp.astimezone(timezone.utc):%Y%m%d-%H%M%S}-"
        f"{uuid4().hex[:6]}"
    )

    return PortfolioSnapshot(
        snapshot_id=snapshot_id,
        timestamp=timestamp.astimezone(timezone.utc),
        source_file=str(path),
        source_file_hash=sha256_file(path),
        quant_engine_version=quant_engine_version,
        analyzed_positions=analyzed_positions,
        gross_exposure_eur=gross_exposure_eur,
        net_exposure_eur=net_exposure_eur,
        account_state_id=account_state_id,
    )
