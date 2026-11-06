"""Cache-only S2.2E Candidate Set and Portfolio Watch Set audit."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from app.portfolio.fineco_importer import load_fineco_positions
from app.scanner.history_quality_contracts import (
    GateStatus,
    HistoryGate,
    HistoryQualityPolicy,
    HistoryQualityResult,
    HistoryRoute,
    IndicatorCapability,
)
from app.scanner.instrument_eligibility import (
    ClassificationReviewFlag,
    InstrumentEligibilityDecision,
    InstrumentEligibilityReason,
    InstrumentEligibilityStatus,
)
from app.scanner.instrument_taxonomy import CanonicalInstrumentType
from app.scanner.market_data_contracts import (
    MarketDataDiagnostic,
    SymbolMappingResult,
    SymbolMappingStatus,
)
from app.scanner.market_data_verification_cache import atomic_json, decode_result
from app.scanner.universe_models import ListingKey, MarketListing
from app.scanner.watch_universe import (
    assemble_research_watch_universe,
    watch_universe_to_dict,
)
from app.scanner.watch_universe_contracts import CandidateEvidenceBundle


DEFAULT_OUTPUT = Path("data/cache/scanner/watch_universe")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _aware(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field} must be an ISO-8601 timestamp"
        )

    normalized = value.strip()

    if normalized[-1:] in {"Z", "z"}:
        normalized = normalized[:-1] + "+00:00"

    normalized = re.sub(
        r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)",
        r"\1",
        normalized,
    )

    result = datetime.fromisoformat(normalized)

    if result.utcoffset() is None:
        raise ValueError(
            f"{field} must be timezone-aware"
        )

    return result


def _listing(row):
    exchange = row["exchange"]
    region = "US" if exchange in {"NASDAQ", "NYSE", "AMEX", "BATS", "NYSE ARCA"} else "EUROPE"
    return MarketListing(
        row["symbol"], exchange, exchange, region,
        currency=row.get("currency"),
        instrument_type=row.get("raw_instrument_type"),
        isin=row.get("isin"),
        name=row.get("name"),
    )


def _eligibility(row, listing):
    return InstrumentEligibilityDecision(
        listing_key=listing.key,
        raw_instrument_type=row.get("raw_instrument_type"),
        canonical_type=CanonicalInstrumentType(row["canonical_type"]),
        status=InstrumentEligibilityStatus(row["status"]),
        reason_code=InstrumentEligibilityReason(row["reason_code"]),
        message="Restored from accepted S2.2B eligibility report",
        review_flags=tuple(ClassificationReviewFlag(value) for value in row.get("review_flags", ())),
        policy_id=row["policy_id"],
        policy_version=row["policy_version"],
    )


def _mapping(row, *, provider_id, mapping_version):
    return SymbolMappingResult(
        listing_key=ListingKey(row["exchange"], row["symbol"]),
        provider_id=provider_id,
        mapping_version=mapping_version,
        status=SymbolMappingStatus(row["mapping_status"]),
        candidate_symbols=tuple(row.get("candidate_symbols", ())),
        rule_id=row.get("rule_id"),
        diagnostics=tuple(
            MarketDataDiagnostic(value["code"], value["message"])
            for value in row.get("diagnostics", ())
        ),
    )


def _history(raw):
    policy = dict(raw["policy"])
    policy["publication_grace"] = timedelta(seconds=policy["publication_grace"])
    return HistoryQualityResult(
        listing_key=ListingKey(**raw["listing_key"]),
        yahoo_symbol=raw["yahoo_symbol"],
        as_of=_aware(raw["as_of"], "history.as_of"),
        policy=HistoryQualityPolicy(**policy),
        snapshot_fingerprint=raw["snapshot_fingerprint"],
        evidence_fingerprint=raw["evidence_fingerprint"],
        evidence_sources=tuple(tuple(value) for value in raw["evidence_sources"]),
        route=HistoryRoute(raw["route"]),
        gates=tuple(
            HistoryGate(value["name"], GateStatus(value["status"]), value["reason"])
            for value in raw["gates"]
        ),
        indicators=tuple(
            IndicatorCapability(**value) for value in raw["indicators"]
        ),
        metrics=tuple(tuple(value) for value in raw["metrics"]),
        listing_event=raw.get("listing_event"),
    )


def _validate_reports(eligibility, mapping):
    if eligibility.get("audit_id") != "E2E-S2.2B":
        raise ValueError("Expected an S2.2B eligibility report")
    if mapping.get("audit_id") != "E2E-S2.2C-MAPPING":
        raise ValueError("Expected an S2.2C mapping report")
    rows = eligibility.get("all_decisions")
    mappings = mapping.get("mappings")
    if not isinstance(rows, list) or not isinstance(mappings, list):
        raise ValueError("Missing eligibility or mapping rows")
    if eligibility.get("decision_count") != len(rows):
        raise ValueError("Eligibility decision count mismatch")
    if mapping.get("mapping_count") != len(mappings):
        raise ValueError("Mapping count mismatch")


def build_watch_universe_audit(
    eligibility,
    mapping,
    history_reports,
    positions,
    *,
    portfolio_snapshot_id,
    as_of,
    assembled_at,
    source_fingerprints=(),
):
    _validate_reports(eligibility, mapping)
    positions = tuple(positions)
    mapping_by_key = {}
    for row in mapping["mappings"]:
        value = _mapping(
            row,
            provider_id=mapping["provider_id"],
            mapping_version=mapping["mapping_version"],
        )
        if value.listing_key in mapping_by_key:
            raise ValueError("Duplicate listing in mapping report")
        mapping_by_key[value.listing_key] = value

    history_by_key = {}
    for report in history_reports:
        if report.get("audit_id") != "E2E-S2.2D-HISTORY-PILOT":
            raise ValueError("Expected S2.2D history reports")
        for record in report.get("results", ()):
            if record.get("quality") is None:
                continue
            quality = _history(record["quality"])
            if quality.as_of > as_of:
                continue
            verification = decode_result(record["acquisition"]["verification"])
            existing = history_by_key.get(quality.listing_key)
            restored = (quality, verification)
            if existing is None or quality.as_of > existing[0].as_of:
                history_by_key[quality.listing_key] = restored
            elif quality.as_of == existing[0].as_of and restored != existing:
                raise ValueError("Conflicting history results at the same as_of")

    bundles = []
    seen = set()
    for row in eligibility["all_decisions"]:
        item = _listing(row)
        if item.key in seen:
            raise ValueError("Duplicate listing in eligibility report")
        seen.add(item.key)
        decision = _eligibility(row, item)
        mapped = mapping_by_key.get(item.key)
        quality, verification = history_by_key.get(item.key, (None, None))
        if quality is not None:
            if mapped is None:
                raise ValueError("History exists without an S2.2C mapping")
            if verification.mapping != mapped:
                raise ValueError("History verification differs from mapping report")
        bundles.append(CandidateEvidenceBundle(
            item, decision, mapped, verification, quality,
        ))

    universe = assemble_research_watch_universe(
        candidate_inputs=bundles,
        portfolio_positions=positions,
        portfolio_snapshot_id=portfolio_snapshot_id,
        as_of=as_of,
        assembled_at=assembled_at,
        source_fingerprints=source_fingerprints,
    )
    candidate_reasons = Counter(value.reason.value for value in universe.candidate_decisions)
    portfolio_reasons = Counter(value.reason.value for value in universe.portfolio_decisions)
    readiness = Counter(value.readiness.value for value in universe.members)
    provenance = Counter(
        "+".join(value.value for value in member.provenances)
        for member in universe.members
    )
    return {
        "audit_id": "E2E-S2.2E",
        "schema": "scanner-research-watch-universe-v1",
        "network_calls": 0,
        "generated_at": assembled_at.isoformat(),
        "as_of": as_of.isoformat(),
        "portfolio_snapshot_id": portfolio_snapshot_id,
        "summary": {
            "eligibility_input_count": len(bundles),
            "candidate_count": len(universe.candidate_set),
            "portfolio_input_count": len(positions),
            "portfolio_watch_count": len(universe.portfolio_watch_set),
            "research_member_count": len(universe.members),
            "candidate_reason_counts": dict(sorted(candidate_reasons.items())),
            "portfolio_reason_counts": dict(sorted(portfolio_reasons.items())),
            "readiness_counts": dict(sorted(readiness.items())),
            "provenance_counts": dict(sorted(provenance.items())),
        },
        "universe": watch_universe_to_dict(universe),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--mapping-report", type=Path, required=True)
    parser.add_argument("--history-report", type=Path, action="append", default=[])
    parser.add_argument("--portfolio-file", type=Path, required=True)
    parser.add_argument("--portfolio-snapshot-id", required=True)
    parser.add_argument("--as-of", required=True, help="Timezone-aware ISO-8601 timestamp")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    as_of = _aware(args.as_of, "as_of")
    assembled_at = datetime.now(timezone.utc)
    paths = [
        args.eligibility_report,
        args.mapping_report,
        *args.history_report,
        args.portfolio_file,
    ]
    source_fingerprints = tuple(
        (f"source_{index:03d}:{path.name}", _fingerprint(path))
        for index, path in enumerate(paths)
    )
    report = build_watch_universe_audit(
        _load(args.eligibility_report),
        _load(args.mapping_report),
        tuple(_load(path) for path in args.history_report),
        tuple(load_fineco_positions(args.portfolio_file)),
        portfolio_snapshot_id=args.portfolio_snapshot_id,
        as_of=as_of,
        assembled_at=assembled_at,
        source_fingerprints=source_fingerprints,
    )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    stamp = assembled_at.strftime("%Y%m%dT%H%M%S%fZ")
    path = args.output_directory / f"scanner_watch_universe_{stamp}.json"
    atomic_json(path, report)
    print("audit: E2E-S2.2E")
    print("mode: cache-only")
    print("network_calls: 0")
    for key, value in report["summary"].items():
        print(f"{key}: {value}")
    print("fingerprint:", report["universe"]["fingerprint"])
    print("report:", path)
    print("AUDIT_STATUS: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
