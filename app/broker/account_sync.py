"""Account sync service (architecture §5A).

``sync()`` reads the account through one source, compares it with the latest
persisted version of the same lineage (mode), and:

* stores a new immutable ``StateVersion`` when anything material changed,
  with the parent link and the list of classified changes;
* records a ``NO_CHANGE`` sync run otherwise, so the age of the last confirmed
  state is always known.

It never writes to the broker: the safety counters are always zero.
"""

from __future__ import annotations

import hashlib

from .fingerprint import account_fingerprint, profile_fingerprint, structural_fingerprint
from .models import ChangeType, StateChange, StateVersion, SyncOutcome, SyncResult
from .sources import AccountSource
from .state_diff import DiffTolerances, diff_readings
from .sync_store import SyncStore


def _version_id(mode: str, observed_at: str, structural: str, account: str) -> str:
    digest = hashlib.sha256(f"{mode}|{observed_at}|{structural}|{account}".encode()).hexdigest()
    return f"ACCT-{mode[:4]}-{observed_at[:19].replace(':', '').replace('-', '')}-{digest[:10]}"


class AccountSyncService:
    def __init__(self, source: AccountSource, store: SyncStore, tolerances: DiffTolerances = DiffTolerances()) -> None:
        self._source = source
        self._store = store
        self._tolerances = tolerances

    def sync(self) -> SyncResult:
        reading = self._source.read()
        if reading.mode is not self._source.mode:
            raise ValueError("source returned a reading of another mode")

        structural = structural_fingerprint(reading)
        account = account_fingerprint(reading)
        profile = profile_fingerprint(reading)
        observed = reading.observed_at.isoformat()
        previous = self._store.latest_version(reading.mode)

        if previous is None:
            changes: tuple[StateChange, ...] = (StateChange(change_type=ChangeType.INITIAL, subject="ACCOUNT"),)
            outcome = SyncOutcome.INITIAL
        else:
            changes = diff_readings(previous.reading, reading, self._tolerances)
            if not changes:
                outcome = SyncOutcome.NO_CHANGE
            elif all(c.change_type is ChangeType.VALUATION_DRIFT for c in changes):
                outcome = SyncOutcome.VALUATION_ONLY
            else:
                outcome = SyncOutcome.CHANGED

        if outcome is SyncOutcome.NO_CHANGE:
            version = previous
            new_version = False
        else:
            version = StateVersion(
                version_id=_version_id(reading.mode.value, observed, structural, account),
                parent_version_id=None if previous is None else previous.version_id,
                mode=reading.mode,
                origin=reading.origin,
                created_at=reading.observed_at,
                structural_fingerprint=structural,
                account_fingerprint=account,
                profile_fingerprint=profile,
                reading=reading,
            )
            new_version = True

        result = SyncResult(
            sync_id=f"SYNC-{hashlib.sha256(f'{reading.mode.value}|{observed}'.encode()).hexdigest()[:16]}",
            observed_at=reading.observed_at,
            outcome=outcome,
            version=version,
            changes=changes,
        )
        self._store.record(result, new_version=new_version)
        return result
