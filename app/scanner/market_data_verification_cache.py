"""Exact-request verification cache; no stale fallback or implicit network."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from app.scanner.market_data_contracts import (
    MarketDataVerification, MarketDataDiagnostic as D,
    MarketDataIdentityStatus as I, MarketDataAvailabilityStatus as A,
    SymbolMappingResult, SymbolMappingStatus,
)
from app.scanner.universe_models import ListingKey
from app.scanner.yahoo_market_data_verifier import VERIFICATION_VERSION


CACHE_VERSION = "verification-cache-v1"
MODES = ("prefer-cache", "cache-only", "force-refresh")


def encode(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError("Unsupported cache value")


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as f:
            temp = Path(f.name)
            json.dump(payload, f, default=encode, sort_keys=True, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def request_identity(listing, mapping, start, end, version):
    # Full source record intentionally invalidates even descriptive changes.
    return json.loads(json.dumps({"schema": CACHE_VERSION, "listing": asdict(listing),
        "mapping": asdict(mapping), "start": start, "end": end,
        "verification_version": version}, default=encode, sort_keys=True))


def decode_result(raw):
    raw = dict(raw)
    mapping = dict(raw["mapping"])
    mapping["listing_key"] = ListingKey(**mapping["listing_key"])
    mapping["status"] = SymbolMappingStatus(mapping["status"])
    mapping["diagnostics"] = tuple(D(**d) for d in mapping["diagnostics"])
    raw["mapping"] = SymbolMappingResult(**mapping)
    raw["identity_status"] = I(raw["identity_status"])
    raw["availability_status"] = A(raw["availability_status"])
    raw["diagnostics"] = tuple(D(**d) for d in raw["diagnostics"])
    for field in ("checked_at", "expires_at", "requested_start", "requested_end", "latest_bar_at"):
        if raw[field] is not None:
            raw[field] = datetime.fromisoformat(raw[field])
    return MarketDataVerification(**raw)


def cache_lifetime(result):
    # Error cooldown is distinct from a confirmed absence of data.
    if result.availability_status in {A.TEMPORARY_ERROR, A.PROVIDER_ERROR} or any(
            d.code.startswith("METADATA_") for d in result.diagnostics):
        return timedelta(minutes=5)
    if result.ready_for_market_data(as_of=result.checked_at):
        return timedelta(hours=24)
    return timedelta(hours=1)


class CachedMarketDataVerifier:
    def __init__(self, verifier, root, *, mode="prefer-cache",
                 verification_version=VERIFICATION_VERSION,
                 now=lambda: datetime.now(timezone.utc)):
        if mode not in MODES:
            raise ValueError("Unknown cache mode")
        self.verifier, self.root, self.mode = verifier, Path(root), mode
        self.version, self.now = verification_version, now
        self.cache_hits = self.network_verifications = self.cache_misses = 0

    def verify(self, listing, mapping, *, start, end):
        now = self.now()
        if any(not isinstance(t, datetime) or t.utcoffset() is None for t in (start, end, now)):
            raise ValueError("Aware timestamps required")
        if not start < end <= now or mapping.listing_key != listing.key or mapping.provider_id != "yahoo":
            raise ValueError("Invalid verification request")
        identity = request_identity(listing, mapping, start, end, self.version)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = self.root / (key + ".json")
        issue = "CACHE_MISS"
        if self.mode != "force-refresh":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload["request"] != identity:
                    raise ValueError("Request mismatch")
                result = decode_result(payload["result"])
                if (result.mapping != mapping or result.verification_version != self.version
                        or result.requested_start != start or result.requested_end != end):
                    raise ValueError("Result mismatch")
                if result.checked_at <= now < min(result.expires_at, result.checked_at + cache_lifetime(result)):
                    self.cache_hits += 1
                    return replace(result, from_cache=True)
                issue = "CACHE_EXPIRED"
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                issue = "CACHE_INVALID"
        self.cache_misses += 1
        if self.mode == "cache-only":
            return MarketDataVerification(mapping, I.UNVERIFIED, A.NOT_CHECKED,
                now, now + timedelta(minutes=5), start, end, self.version,
                diagnostics=(D(issue, "No reusable verification; network disabled"),))
        self.network_verifications += 1
        result = self.verifier.verify(listing, mapping, start=start, end=end)
        if (result.mapping != mapping or result.verification_version != self.version
                or result.requested_start != start or result.requested_end != end):
            raise ValueError("Verifier returned a different request/version")
        result = replace(result, from_cache=False,
                         expires_at=min(result.expires_at, result.checked_at + cache_lifetime(result)))
        diagnostics = [] if issue == "CACHE_MISS" else [D(issue, "Previous cache entry not reused")]
        try:
            atomic_json(path, {"request": identity, "result": asdict(result)})
        except OSError:
            diagnostics.append(D("CACHE_WRITE_FAILED", "Verification completed but cache could not be saved"))
        return replace(result, diagnostics=result.diagnostics + tuple(diagnostics))
