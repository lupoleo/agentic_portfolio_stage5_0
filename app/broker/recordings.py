"""Replays recorded real IBKR readings (``data/recordings``) in order."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .ibkr_client import RecordedTransport

ACCOUNT_TOOLS = ("get_account_summary", "get_account_balances", "get_account_positions", "get_flow_type", "get_account_orders")


def load_account_recording(path: str | Path) -> tuple[RecordedTransport, list[datetime]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    readings = data["readings"]
    transport = RecordedTransport({tool: [r[tool] for r in readings] for tool in ACCOUNT_TOOLS})
    times = [datetime.fromisoformat(r["observed_at"]) for r in readings]
    return transport, times
