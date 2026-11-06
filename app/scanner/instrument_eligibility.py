from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.scanner.instrument_taxonomy import (
    CanonicalInstrumentType,
    resolve_instrument_type,
)
from app.scanner.universe_models import ListingKey, MarketListing


class InstrumentEligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class InstrumentEligibilityReason(str, Enum):
    TYPE_ELIGIBLE = "TYPE_ELIGIBLE"
    TYPE_DEFERRED = "TYPE_DEFERRED"
    TYPE_INELIGIBLE = "TYPE_INELIGIBLE"
    TYPE_UNKNOWN = "TYPE_UNKNOWN"
    CLASSIFICATION_REVIEW_REQUIRED = (
        "CLASSIFICATION_REVIEW_REQUIRED"
    )


class ClassificationReviewFlag(str, Enum):
    RIGHT = "RIGHT"
    WARRANT = "WARRANT"
    UNIT = "UNIT"
    EXCHANGE_TRADED_PRODUCT = "EXCHANGE_TRADED_PRODUCT"
    DEBT_SECURITY = "DEBT_SECURITY"


@dataclass(frozen=True)
class InstrumentEligibilityPolicy:
    eligible_types: frozenset[CanonicalInstrumentType]
    deferred_types: frozenset[CanonicalInstrumentType]
    policy_id: str = "scanner-v1-instrument-eligibility"
    policy_version: str = "1"

    def __post_init__(self) -> None:
        policy_id = self.policy_id.strip()
        policy_version = self.policy_version.strip()
        eligible = frozenset(self.eligible_types)
        deferred = frozenset(self.deferred_types)

        if not policy_id:
            raise ValueError("policy_id must not be blank")
        if not policy_version:
            raise ValueError("policy_version must not be blank")
        if not eligible:
            raise ValueError("eligible_types must not be empty")
        if eligible & deferred:
            raise ValueError(
                "eligible_types and deferred_types must be disjoint"
            )
        if CanonicalInstrumentType.UNKNOWN in eligible:
            raise ValueError("UNKNOWN cannot be eligible")

        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "eligible_types", eligible)
        object.__setattr__(self, "deferred_types", deferred)


@dataclass(frozen=True)
class InstrumentEligibilityDecision:
    listing_key: ListingKey
    raw_instrument_type: str | None
    canonical_type: CanonicalInstrumentType
    status: InstrumentEligibilityStatus
    reason_code: InstrumentEligibilityReason
    message: str
    review_flags: tuple[ClassificationReviewFlag, ...]
    policy_id: str
    policy_version: str

    @property
    def automatically_admitted(self) -> bool:
        return self.status is InstrumentEligibilityStatus.ELIGIBLE


_RIGHT_END = re.compile(r"\bRIGHTS?\s*$", re.IGNORECASE)
_SUBSCRIPTION_RIGHT = re.compile(
    r"\bSUBSCRIPTION RIGHTS?\b",
    re.IGNORECASE,
)
_WARRANT_END = re.compile(r"\bWARRANTS?\s*$", re.IGNORECASE)
_UNIT_END = re.compile(r"\bUNITS?\s*$", re.IGNORECASE)
_UNIT_STRUCTURE = re.compile(
    r"\bUNITS?\s+(?:EACH\s+)?CONSISTING\b",
    re.IGNORECASE,
)
_ETF_ETP = re.compile(r"\b(?:ETF|ETP)\b", re.IGNORECASE)
_DEBT_FORM = re.compile(
    r"(?:\bCORPORATE BOND\b|"
    r"\b\d+(?:\.\d+)?%\s+NOTES?\b|"
    r"\bNOTES?\s+DUE\b|"
    r"\bDEBENTURES?\s+DUE\b)",
    re.IGNORECASE,
)


def classification_review_flags(
    listing: MarketListing,
) -> tuple[ClassificationReviewFlag, ...]:
    """Return conservative flags; never rewrite provider taxonomy."""
    name = listing.name or ""
    flags = set()

    if _RIGHT_END.search(name) or _SUBSCRIPTION_RIGHT.search(name):
        flags.add(ClassificationReviewFlag.RIGHT)
    if _WARRANT_END.search(name):
        flags.add(ClassificationReviewFlag.WARRANT)
    if _UNIT_END.search(name) or _UNIT_STRUCTURE.search(name):
        flags.add(ClassificationReviewFlag.UNIT)
    if _ETF_ETP.search(name):
        flags.add(ClassificationReviewFlag.EXCHANGE_TRADED_PRODUCT)
    if _DEBT_FORM.search(name):
        flags.add(ClassificationReviewFlag.DEBT_SECURITY)

    return tuple(sorted(flags, key=lambda item: item.value))


def scanner_v1_instrument_eligibility_policy(
) -> InstrumentEligibilityPolicy:
    return InstrumentEligibilityPolicy(
        eligible_types=frozenset(
            {CanonicalInstrumentType.COMMON_STOCK}
        ),
        deferred_types=frozenset(
            {
                CanonicalInstrumentType.ETF,
                CanonicalInstrumentType.ETC,
                CanonicalInstrumentType.PREFERRED_STOCK,
            }
        ),
    )


def evaluate_instrument_eligibility(
    listing: MarketListing,
    *,
    policy: InstrumentEligibilityPolicy | None = None,
) -> InstrumentEligibilityDecision:
    selected_policy = (
        policy
        if policy is not None
        else scanner_v1_instrument_eligibility_policy()
    )
    resolution = resolve_instrument_type(listing.instrument_type)
    canonical_type = resolution.canonical_type
    flags = classification_review_flags(listing)

    if canonical_type is CanonicalInstrumentType.UNKNOWN:
        status = InstrumentEligibilityStatus.INELIGIBLE
        reason = InstrumentEligibilityReason.TYPE_UNKNOWN
        message = "Instrument type is missing or not recognized"
    elif canonical_type in selected_policy.eligible_types and flags:
        status = InstrumentEligibilityStatus.REVIEW_REQUIRED
        reason = (
            InstrumentEligibilityReason.CLASSIFICATION_REVIEW_REQUIRED
        )
        message = (
            "Provider type is eligible but security-form evidence "
            "requires review"
        )
    elif canonical_type in selected_policy.eligible_types:
        status = InstrumentEligibilityStatus.ELIGIBLE
        reason = InstrumentEligibilityReason.TYPE_ELIGIBLE
        message = "Canonical instrument type is eligible"
    elif canonical_type in selected_policy.deferred_types:
        status = InstrumentEligibilityStatus.INELIGIBLE
        reason = InstrumentEligibilityReason.TYPE_DEFERRED
        message = "Canonical instrument type is deferred from Scanner V1"
    else:
        status = InstrumentEligibilityStatus.INELIGIBLE
        reason = InstrumentEligibilityReason.TYPE_INELIGIBLE
        message = "Canonical instrument type is outside Scanner V1"

    return InstrumentEligibilityDecision(
        listing_key=listing.key,
        raw_instrument_type=listing.instrument_type,
        canonical_type=canonical_type,
        status=status,
        reason_code=reason,
        message=message,
        review_flags=flags,
        policy_id=selected_policy.policy_id,
        policy_version=selected_policy.policy_version,
    )
