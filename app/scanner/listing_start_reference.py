"""Reviewed primary-source listing references.

Never infer a listing-start date from the first available price bar.
This module validates an operator-reviewed manifest. It does not
independently authenticate the source or certify the review.
"""

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.scanner.history_quality_contracts import ListingStartEvidence, aware
from app.scanner.universe_models import ListingKey


SCHEMA = "scanner-listing-start-references-v1"


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonempty reference text required")
    return value.strip()


def _timestamp(value):
    result = datetime.fromisoformat(_text(value))
    aware(result)
    return result


def _url(value):
    value = _text(value)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Public HTTPS primary-source URL required")
    return value


@dataclass(frozen=True)
class ReviewedListingReference:
    evidence: ListingStartEvidence
    issuer_name: str
    isin: str | None
    source_kind: str
    source_url: str
    source_title: str
    source_excerpt: str
    observed_at: datetime
    reviewed_at: datetime
    reviewed_by: str
    record_sha256: str


@dataclass(frozen=True)
class ReferenceResolution:
    status: str
    evidence: ListingStartEvidence | None
    reference: ReviewedListingReference | None
    diagnostics: tuple[str, ...]


class ListingStartReferenceRegistry:
    def __init__(self, document):
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ValueError("Unsupported listing-reference schema")

        rows = document.get("references")
        if not isinstance(rows, list):
            raise ValueError("references must be an array")

        self._references = {}

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid reference record")

            exchange = _text(row.get("exchange"))
            symbol = _text(row.get("symbol"))
            key = ListingKey(exchange, symbol)

            if key.exchange != exchange or key.symbol != symbol:
                raise ValueError("Canonical exchange/symbol required")

            if key in self._references:
                raise ValueError("Duplicate/conflicting listing reference")

            if (
                row.get("review_status") != "APPROVED"
                or row.get("date_status") != "CONFIRMED_TRADING_START"
            ):
                raise ValueError(
                    "A forecast or unreviewed date is not listing-start evidence"
                )

            kind = row.get("source_kind")
            if kind not in {"EXCHANGE", "ISSUER", "REGULATOR"}:
                raise ValueError("Reviewed primary source required")

            first = date.fromisoformat(_text(row.get("first_session")))
            observed = _timestamp(row.get("observed_at"))
            reviewed = _timestamp(row.get("reviewed_at"))

            if reviewed < observed or first > observed.date():
                raise ValueError("Invalid observation/review chronology")

            event = row.get("event_kind")
            if event not in {"IPO", "LISTING_START"}:
                raise ValueError("Unsupported listing event")

            if event == "IPO" and row.get("ipo_confirmed") is not True:
                raise ValueError(
                    "IPO needs explicit primary-source confirmation"
                )

            if event == "LISTING_START" and row.get("ipo_confirmed") is not False:
                raise ValueError("Listing start does not establish an IPO")

            isin = row.get("isin")
            if isin is not None:
                isin = _text(isin)

            source_url = _url(row.get("source_url"))
            title = _text(row.get("source_title"))
            excerpt = _text(row.get("source_excerpt"))
            issuer = _text(row.get("issuer_name"))
            reviewer = _text(row.get("reviewed_by"))

            canonical = {
                "exchange": exchange,
                "symbol": symbol,
                "first_session": first.isoformat(),
                "event_kind": event,
                "ipo_confirmed": row["ipo_confirmed"],
                "source_kind": kind,
                "source_url": source_url,
                "source_title": title,
                "source_excerpt": excerpt,
                "issuer_name": issuer,
                "isin": isin,
                "observed_at": observed.isoformat(),
                "reviewed_at": reviewed.isoformat(),
                "reviewed_by": reviewer,
                "date_status": row["date_status"],
                "review_status": row["review_status"],
            }

            digest = hashlib.sha256(
                json.dumps(
                    canonical,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()

            evidence = ListingStartEvidence(
                key,
                first,
                (
                    f"reviewed-primary-reference:{source_url}"
                    f"#record-sha256={digest}"
                ),
                reviewed,
                event,
            )

            self._references[key] = ReviewedListingReference(
                evidence,
                issuer,
                isin,
                kind,
                source_url,
                title,
                excerpt,
                observed,
                reviewed,
                reviewer,
                digest,
            )

    @classmethod
    def load(cls, path):
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON field")
                result[key] = value
            return result

        document = json.loads(
            Path(path).read_text(encoding="utf-8-sig"),
            object_pairs_hook=unique_object,
        )
        return cls(document)

    def resolve(self, listing, *, as_of):
        aware(as_of)
        reference = self._references.get(listing.key)

        if reference is None:
            return ReferenceResolution(
                "MISSING",
                None,
                None,
                ("NO_LISTING_START_REFERENCE",),
            )

        if reference.evidence.known_at > as_of:
            return ReferenceResolution(
                "NOT_YET_KNOWN",
                None,
                reference,
                ("REFERENCE_NOT_KNOWN_AS_OF",),
            )

        if (
            reference.isin
            and listing.isin
            and reference.isin != listing.isin
        ):
            return ReferenceResolution(
                "CONFLICT",
                None,
                reference,
                ("REFERENCE_ISIN_MISMATCH",),
            )

        return ReferenceResolution(
            "MATCHED",
            reference.evidence,
            reference,
            (),
        )
