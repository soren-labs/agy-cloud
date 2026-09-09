"""Account pool and token store (T8 implements; T2 provides the secret layer).

Account binding rules (V1-SCOPE §4/§5.1):
- Sticky: an agent keeps its account while that account has capacity; when a
  limit is hit the run waits for the SAME account (no cross-account migration).
- max_concurrent starts at 1 per account; raising it requires same-account
  measurement (not a T0 concern: only TWO accounts exist today and T0 never
  requires a third).
- Token writeback is account-serialized compare-and-set: an old session must
  never overwrite a newer credential (CAS on secret version + generation
  fencing from lifecycle.check_session_callback).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agy_cloud.models import (
    AccountId,
    AgentId,
    SessionIdentity,
)


class CasResult:
    OK = "ok"
    VERSION_CONFLICT = "version_conflict"  # expected_version stale
    GENERATION_REJECTED = "generation_rejected"  # late session callback


@dataclass(frozen=True)
class TokenWrite:
    expected_version: int  # version the writer last observed
    token_object: str  # serialized agy oauth token (never logged)


class AccountPool(Protocol):
    def acquire_slot(
        self, agent_id: AgentId, *, sticky: AccountId | None
    ) -> AccountId | None:
        """Reserve one execution slot; None means all eligible accounts are full.

        Eligibility: status ok, active_runs < max_concurrent, and (when quota
        cooldown is active) cooldown expired. Sticky accounts are waited for,
        never migrated away from.
        """
        ...

    def release_slot(self, account_id: AccountId, agent_id: AgentId) -> None:
        """Idempotent slot release at session end (any end reason)."""
        ...

    def mark_cooldown(self, account_id: AccountId, *, until: str, reason: str) -> None:
        """Quota cooldown (stderr/result matched quota patterns)."""
        ...


class AccountTokenStore(Protocol):
    def read_token(self, account_id: AccountId, *, session: SessionIdentity) -> str:
        """Fetch the CURRENT token version for a bound session (scoped read).

        Only the session bound to this account's active run may read it;
        workers never enumerate accounts or other tokens.
        """
        ...

    def compare_and_set_token(
        self,
        account_id: AccountId,
        write: TokenWrite,
        *,
        session: SessionIdentity,
    ) -> tuple[str, int]:
        """Write a refreshed token; returns (CasResult value, new_version).

        VERSION_CONFLICT -> caller must re-read and retry with the observed
        version; an old session writing after a rebind is GENERATION_REJECTED.
        """
        ...
