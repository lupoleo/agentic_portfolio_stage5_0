from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.cio.models import (
    CacheStatus,
    DataSource,
    FinecoInstrument,
)
from app.cio.storage import Stage3Store


# =============================================================
# Import file contracts
# =============================================================


class BulkImportInstrument(BaseModel):
    """
    One FinecoInstrument coming from a bulk import file.

    instrument_id is optional because the importer can generate it.

    last_confirmed/source/cache_status are managed by the importer.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    instrument_id: str | None = None

    payload: dict[str, Any]


class FinecoBulkImportFile(BaseModel):
    """
    Canonical JSON format used by the Fineco Bulk Cache Importer.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    schema_version: str = "1.0"

    batch_name: str = Field(
        min_length=1
    )

    created_at: datetime | None = None

    notes: str | None = None

    instruments: list[BulkImportInstrument]


# =============================================================
# Import result
# =============================================================


class ImportAction(str, Enum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    ERROR = "ERROR"


@dataclass
class ImportItemResult:
    action: ImportAction

    instrument_id: str | None

    underlying: str | None

    fineco_symbol: str | None

    message: str | None = None


@dataclass
@dataclass
class BulkImportResult:
    batch_name: str

    dry_run: bool

    items: list[ImportItemResult] = field(
        default_factory=list
    )

    # Stage 3.1
    #
    # Opportunities that changed state after the real
    # Fineco bulk import.
    #
    # Example:
    #
    # WAITING_FOR_BROKER_INSTRUMENTS
    #     ->
    # READY_FOR_INSTRUMENT_SELECTION
    #
    refreshed_opportunity_ids: list[str] = field(
        default_factory=list
    )

    @property
    def refreshed_opportunity_count(
        self,
    ) -> int:
        return len(
            self.refreshed_opportunity_ids
        )

    @property
    def new_count(self) -> int:
        return sum(
            item.action == ImportAction.NEW
            for item in self.items
        )

    @property
    def update_count(self) -> int:
        return sum(
            item.action == ImportAction.UPDATE
            for item in self.items
        )

    @property
    def unchanged_count(self) -> int:
        return sum(
            item.action == ImportAction.UNCHANGED
            for item in self.items
        )

    @property
    def error_count(self) -> int:
        return sum(
            item.action == ImportAction.ERROR
            for item in self.items
        )


# =============================================================
# Identity / matching
# =============================================================


def instrument_identity(
    instrument: FinecoInstrument,
) -> tuple:
    """
    Natural key used to identify the same Fineco operating instrument.

    IMPORTANT:

    instrument_id is deliberately NOT part of this key.

    This makes repeated imports idempotent.
    """

    return (
        instrument.underlying.upper(),

        instrument.instrument_type.value,

        instrument.trading_mode.value,

        (
            instrument.fineco_symbol.upper()
            if instrument.fineco_symbol
            else None
        ),

        (
            instrument.market.upper()
            if instrument.market
            else None
        ),

        instrument.exposure_relationship.value,

        instrument.broker_leverage,

        instrument.embedded_leverage,
    )


def _new_import_instrument_id(
    instrument: FinecoInstrument,
) -> str:
    """
    Generate an ID only for genuinely new cache records.
    """

    symbol = (
        instrument.fineco_symbol
        or instrument.underlying
    )

    safe_symbol = (
        symbol
        .upper()
        .replace(".", "-")
        .replace("/", "-")
        .replace(" ", "-")
    )

    broker_part = (
        f"X{instrument.broker_leverage:g}"
        if instrument.broker_leverage is not None
        else "XNA"
    )

    return (
        f"FIN-"
        f"{safe_symbol}-"
        f"{instrument.instrument_type.value}-"
        f"{instrument.trading_mode.value}-"
        f"{broker_part}-"
        f"{uuid4().hex[:6]}"
    )


# =============================================================
# Comparison
# =============================================================


def _comparison_payload(
    instrument: FinecoInstrument,
) -> dict:
    """
    Compare economic/static cache information.

    Fields that naturally change during confirmation are excluded.
    """

    payload = instrument.model_dump(
        mode="json"
    )

    payload.pop(
        "instrument_id",
        None,
    )

    payload.pop(
        "last_confirmed",
        None,
    )

    payload.pop(
        "cache_status",
        None,
    )

    payload.pop(
        "source",
        None,
    )

    # Market snapshot is dynamic.
    payload.pop(
        "market_snapshot",
        None,
    )

    return payload


# =============================================================
# Importer
# =============================================================


class FinecoBulkImporter:

    def __init__(
        self,
        store: Stage3Store,
    ):
        self.store = store

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------

    def import_file(
        self,
        file_path: str | Path,
        *,
        dry_run: bool = False,
    ) -> BulkImportResult:

        batch = self._load_file(
            file_path
        )

        result = BulkImportResult(
            batch_name=batch.batch_name,
            dry_run=dry_run,
        )

        existing = (
            self.store.list_fineco_instruments()
        )

        existing_by_identity = {
            instrument_identity(item): item
            for item in existing
        }

        confirmed_at = (
            batch.created_at
            or datetime.now(
                timezone.utc
            )
        )

        for import_item in batch.instruments:

            try:

                candidate = (
                    self._build_instrument(
                        import_item,
                        confirmed_at,
                    )
                )

                identity = (
                    instrument_identity(
                        candidate
                    )
                )

                current = (
                    existing_by_identity.get(
                        identity
                    )
                )

                if current is None:

                    candidate = (
                        candidate.model_copy(
                            update={
                                "instrument_id":
                                    (
                                        import_item.instrument_id
                                        or _new_import_instrument_id(
                                            candidate
                                        )
                                    )
                            }
                        )
                    )

                    action = (
                        ImportAction.NEW
                    )

                    if not dry_run:
                        self.store.save_fineco_instrument(
                            candidate
                        )

                        existing_by_identity[
                            identity
                        ] = candidate

                else:

                    # Preserve the stable ID already in SQLite.
                    candidate = (
                        candidate.model_copy(
                            update={
                                "instrument_id":
                                    current.instrument_id
                            }
                        )
                    )

                    changed = (
                        _comparison_payload(
                            current
                        )
                        !=
                        _comparison_payload(
                            candidate
                        )
                    )

                    if changed:

                        action = (
                            ImportAction.UPDATE
                        )

                    else:

                        action = (
                            ImportAction.UNCHANGED
                        )

                    if not dry_run:

                        # Even an unchanged record receives a refreshed
                        # last_confirmed timestamp.
                        self.store.save_fineco_instrument(
                            candidate
                        )

                        existing_by_identity[
                            identity
                        ] = candidate

                result.items.append(
                    ImportItemResult(
                        action=action,
                        instrument_id=(
                            candidate.instrument_id
                        ),
                        underlying=(
                            candidate.underlying
                        ),
                        fineco_symbol=(
                            candidate.fineco_symbol
                        ),
                    )
                )

            except Exception as exc:

                payload = (
                    import_item.payload
                )

                result.items.append(
                    ImportItemResult(
                        action=(
                            ImportAction.ERROR
                        ),
                        instrument_id=(
                            import_item.instrument_id
                        ),
                        underlying=(
                            payload.get(
                                "underlying"
                            )
                        ),
                        fineco_symbol=(
                            payload.get(
                                "fineco_symbol"
                            )
                        ),
                        message=str(exc),
                    )
                )

        # =====================================================
        # Stage 3.1 Opportunity lifecycle integration
        # =====================================================
        #
        # A REAL Fineco bulk import may satisfy one or more
        # opportunities that were waiting for broker instrument
        # discovery.
        #
        # DRY RUN must never modify opportunity state.
        #

        if not dry_run:

            refreshed = (
                self.store
                .refresh_waiting_trade_opportunities()
            )

            result.refreshed_opportunity_ids = [
                opportunity.opportunity_id
                for opportunity in refreshed
                if (
                    opportunity.status.value
                    == "READY_FOR_INSTRUMENT_SELECTION"
                )
            ]

        return result

    # ---------------------------------------------------------
    # File loading
    # ---------------------------------------------------------

    def _load_file(
        self,
        file_path: str | Path,
    ) -> FinecoBulkImportFile:

        path = Path(
            file_path
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Import file not found: {path}"
            )

        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:

            raw = json.load(
                handle
            )

        return (
            FinecoBulkImportFile.model_validate(
                raw
            )
        )

    # ---------------------------------------------------------
    # Canonical model creation
    # ---------------------------------------------------------

    def _build_instrument(
        self,
        import_item: BulkImportInstrument,
        confirmed_at: datetime,
    ) -> FinecoInstrument:

        payload = dict(
            import_item.payload
        )

        # Temporary ID required by FinecoInstrument validation.
        # It will subsequently be replaced by either the existing
        # stable ID or a newly generated ID.
        payload["instrument_id"] = (
            import_item.instrument_id
            or "TEMP-BULK-IMPORT"
        )

        payload["last_confirmed"] = (
            confirmed_at
        )

        payload["source"] = (
            DataSource.OPERATOR
        )

        payload["cache_status"] = (
            CacheStatus.CURRENT
        )

        return (
            FinecoInstrument.model_validate(
                payload
            )
        )


# =============================================================
# Terminal reporting
# =============================================================


def print_bulk_import_result(
    result: BulkImportResult,
) -> None:

    mode = (
        "DRY RUN"
        if result.dry_run
        else "IMPORT"
    )

    print(
        f"\n=== FINECO BULK CACHE {mode} ===\n"
    )

    print(
        f"Batch: {result.batch_name}\n"
    )

    print(
        f"{'Action':<12}"
        f"{'Underlying':<12}"
        f"{'Fineco symbol':<22}"
        f"{'Instrument ID'}"
    )

    print(
        "-" * 90
    )

    for item in result.items:

        print(
            f"{item.action.value:<12}"
            f"{(item.underlying or '-'): <12}"
            f"{(item.fineco_symbol or '-'): <22}"
            f"{item.instrument_id or '-'}"
        )

        if item.message:

            print(
                f"  ERROR: "
                f"{item.message}"
            )

    print(
        "\nSummary"
    )

    print(
        "-" * 40
    )

    print(
        f"NEW:       "
        f"{result.new_count}"
    )

    print(
        f"UPDATE:    "
        f"{result.update_count}"
    )

    print(
        f"UNCHANGED: "
        f"{result.unchanged_count}"
    )

    print(
        f"ERROR:     "
        f"{result.error_count}"
    )

    if result.dry_run:

        print(
            "\nNo database changes performed."
        )
    else:

        print(
            "\nOpportunity lifecycle"
        )

        print(
            "-" * 40
        )

        print(
            "READY after broker refresh: "
            f"{result.refreshed_opportunity_count}"
        )

        for opportunity_id in (
            result.refreshed_opportunity_ids
        ):

            print(
                f"  {opportunity_id}"
            )