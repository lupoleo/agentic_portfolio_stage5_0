"""Primary/secondary evidence precedence (architecture §5D.3).

``PrecedenceEvidenceProvider`` wraps a primary provider (IBKR) and a declared
secondary (Yahoo). The secondary runs only when the primary has nothing usable
for the instrument (``NO_DATA``) or is unavailable (provider error); it never
overrides or merges with primary evidence. Every secondary item is marked
``secondary_source=True`` with the primary's reason, so prompts and run
manifests show which channels were filled by a secondary source.
"""

from __future__ import annotations

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceProvider,
    EvidenceProviderError,
    EvidenceRequest,
)


class PrecedenceEvidenceProvider(EvidenceProvider):
    def __init__(self, primary: EvidenceProvider, secondary: EvidenceProvider | None) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def provider_name(self) -> str:
        return self._primary.provider_name

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        try:
            primary = self._primary.fetch(request)
            reason = None if primary.status is not EvidenceFetchStatus.NO_DATA else "; ".join(primary.warnings) or "no data"
            primary_error = None
        except EvidenceProviderError as exc:
            primary, reason, primary_error = None, f"{type(exc).__name__}: {exc}", exc
        if reason is None:
            return primary
        if self._secondary is None:
            if primary_error is not None:
                raise primary_error
            return primary

        secondary = self._secondary.fetch(request)
        note = f"primary {self._primary.provider_name} unavailable ({reason}); secondary {self._secondary.provider_name} used"
        items = [
            item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "secondary_source": True,
                        "primary_provider": self._primary.provider_name,
                        "primary_reason": reason,
                    }
                }
            )
            for item in secondary.items
        ]
        return secondary.model_copy(
            update={
                "items": items,
                "warnings": [note, *secondary.warnings],
                "metadata": {
                    **secondary.metadata,
                    "secondary_source": True,
                    "primary_provider": self._primary.provider_name,
                    "primary_reason": reason,
                },
            }
        )
