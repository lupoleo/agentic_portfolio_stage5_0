"""ECB daily reference FX for liquidity estimates; exact date, no forward fill."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import math
import re
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from app.scanner.history_quality_contracts import SessionFXRate, aware, day

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
NS = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class FXAcquisition:
    currency: str
    requested_sessions: tuple[date, ...]
    rates: tuple[SessionFXRate, ...]
    missing_sessions: tuple[date, ...]
    fetched_at: datetime
    status: str
    diagnostics: tuple[str, ...]
    source_url: str = ECB_URL
    response_sha256: str | None = None
    raw_xml: str | None = None
    provider_version: str = "ecb-session-fx-v1"


def parse_ecb_rates(payload, *, currency, sessions, fetched_at):
    """Known-at is retrieval time, never an invented historical publication time."""
    aware(fetched_at)
    if len(payload) > MAX_BYTES or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unsafe or oversized XML")
    root = ET.fromstring(payload)
    seen_dates, values = set(), {}
    for block in root.iter(NS + "Cube"):
        if "time" not in block.attrib:
            continue
        label = date.fromisoformat(block.attrib["time"])
        if label in seen_dates or label > fetched_at.date():
            raise ValueError("Duplicate or future FX date")
        seen_dates.add(label)
        codes = set()
        for item in block:
            if item.tag != NS + "Cube":
                raise ValueError("Unexpected FX element")
            code = item.attrib.get("currency", "")
            rate = float(item.attrib["rate"])
            if not re.fullmatch(r"[A-Z]{3}", code) or code in codes or not math.isfinite(rate) or rate <= 0:
                raise ValueError("Invalid FX observation")
            codes.add(code)
            if code == currency:
                values[label] = 1. / rate  # ECB quotes currency units per EUR.
    if not seen_dates:
        raise ValueError("Missing ECB observations")
    digest = hashlib.sha256(payload).hexdigest()
    source = f"ECB:daily-reference:{currency}/EUR:sha256:{digest}"
    rates = tuple(SessionFXRate(d, currency, values[d], source, fetched_at)
                  for d in sessions if d in values)
    missing = tuple(d for d in sessions if d not in values)
    return FXAcquisition(currency, sessions, rates, missing, fetched_at,
                         "PARTIAL" if missing else "SUCCESS",
                         ("EXACT_DATE_FX_MISSING",) if missing else (),
                         response_sha256=digest, raw_xml=payload.decode("utf-8-sig"))


class ECBSessionFXProvider:
    def __init__(self, *, opener=urlopen, timeout_seconds=20., now=lambda: datetime.now(timezone.utc)):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Invalid FX timeout")
        self.opener, self.timeout_seconds, self.now = opener, timeout_seconds, now

    def fetch(self, currency, *, sessions):
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency) or currency in {"EUR", "GBX"}:
            raise ValueError("Expected non-EUR major currency")
        sessions = tuple(sessions)
        for value in sessions:
            day(value)
        if len(set(sessions)) != len(sessions):
            raise ValueError("Duplicate requested sessions")
        sessions = tuple(sorted(sessions))
        if not sessions:
            raise ValueError("Empty FX request")
        try:
            with self.opener(ECB_URL, timeout=self.timeout_seconds) as response:
                payload = response.read(MAX_BYTES + 1)
        except Exception as exc:
            fetched_at = self.now()
            aware(fetched_at)
            return FXAcquisition(currency, sessions, (), sessions, fetched_at,
                                 "ERROR", ("FX_TRANSPORT_ERROR:" + type(exc).__name__,))
        fetched_at = self.now()
        aware(fetched_at)
        try:
            return parse_ecb_rates(payload, currency=currency, sessions=sessions, fetched_at=fetched_at)
        except Exception:
            return FXAcquisition(currency, sessions, (), sessions, fetched_at,
                                 "ERROR", ("FX_PAYLOAD_INVALID",),
                                 response_sha256=hashlib.sha256(payload).hexdigest())
