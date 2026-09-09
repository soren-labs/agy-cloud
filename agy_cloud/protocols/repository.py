"""Firestore repository interface: transactional queue + lease operations.

Owner: T2 (interface consumers: T7 launch/followup, T8 scheduler/reaper, T9 pop).
Semantics (V1-SCOPE §5.1) are normative; implementers must satisfy every rule.

Transaction boundaries:
- `enqueue` is a single transaction that increments the agent `seq`, inserts
  the run, and reads the agent lease atomically (followup-vs-release race).
- `claim` (launch) reserves the session and account slot inside ONE
  transaction BEFORE any VM is created. VM name derives from session id;
  a retried claim must look up the same-name resource instead of creating a
  second VM ("cannot rely on lease-absence alone to create multiple VMs").
- `pop`, `renew`, `finish`, `release` all verify the presented SessionIdentity
  (session id + generation) against the current one; a late callback from an
  older generation is rejected without mutation (lifecycle.check_session_callback).

Idempotency: every mutating operation accepts an idempotency key. Same key +
same payload replays the stored result; same key + different payload raises
IDEMPOTENCY_CONFLICT (409). HTTP retries therefore never double-enqueue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agy_cloud.errors import ErrorCode
from agy_cloud.models import (
    AccountId,
    AgentId,
    Generation,
    IdempotencyKey,
    Prompt,
    RunId,
    RunStatus,
    Seq,
    SessionId,
    SessionIdentity,
)


@dataclass(frozen=True)
class SessionLease:
    """The (session, generation) pair currently bound to an agent, plus expiry."""

    session_id: SessionId
    generation: Generation
    instance_name: str
    expires_at: str  # RFC3339 UTC


@dataclass(frozen=True)
class EnqueueResult:
    run_id: RunId
    seq: Seq
    status: RunStatus  # QUEUED
    replayed: bool  # True when an idempotency key returned an existing run


@dataclass(frozen=True)
class ClaimResult:
    lease: SessionLease
    account_id: AccountId
    created: bool  # False when a retried claim found the existing lease/VM


@dataclass(frozen=True)
class PopResult:
    """The next queued run for THIS agent, bound to THIS session (or none)."""

    run_id: RunId
    seq: Seq
    prompt: Prompt
    generation: Generation


@dataclass(frozen=True)
class FinishOutcome:
    run_status: RunStatus
    agent_status: str  # AgentStatus after the finish transaction
    next_run_id: RunId | None  # present when a queued run stayed in this session


class RunRepository(Protocol):
    def enqueue(
        self,
        agent_id: AgentId,
        prompt: Prompt,
        *,
        origin: str,
        idempotency_key: IdempotencyKey,
    ) -> EnqueueResult:
        """Append one run with a transaction-incremented seq.

        Must resolve the concurrent followup-vs-release race atomically:
        lease still held -> run bound to current session (claimable via pop);
        lease released -> scheduler will bind it to a new session.
        Raises ErrorCode.INVALID_STATE for ARCHIVED/EXPIRED agents.
        """
        ...

    def claim(
        self,
        agent_id: AgentId,
        *,
        instance_name: str,
        machine_type: str,
        idempotency_key: IdempotencyKey,
    ) -> ClaimResult:
        """Reserve session + account slot in one transaction BEFORE VM create.

        Sticky account: prefer the agent's bound account if it has capacity;
        never exceed max_concurrent. Returns the fresh lease whose generation
        starts at 1 and increments on every rebind.
        """
        ...

    def renew(
        self,
        session: SessionIdentity,
        *,
        idempotency_key: IdempotencyKey,
    ) -> str:
        """Extend the lease (heartbeat). Expired/lost lease -> LEASE_NOT_HELD."""
        ...

    def pop(
        self,
        session: SessionIdentity,
        *,
        idempotency_key: IdempotencyKey,
    ) -> PopResult | None:
        """Atomically claim the next QUEUED run for this agent into this session.

        Only runs bound to the presented session (or unbound after release)
        are eligible. Stale generation -> GENERATION_MISMATCH (409).
        """
        ...

    def finish(
        self,
        session: SessionIdentity,
        run_id: RunId,
        outcome: Any,  # protocols.snapshot.CompletionManifest
        *,
        idempotency_key: IdempotencyKey,
    ) -> FinishOutcome:
        """Commit a run result; must be preceded by artifact upload.

        Upload-before-finish: artifacts (snapshot manifest, events, logs,
        result.json) are uploaded first; `finish` only references them. The
        manifest must prove uploads (see protocols.snapshot). A duplicate
        finish with identical manifest replays idempotently; a manifest that
        contradicts stored state raises IDEMPOTENCY_CONFLICT.
        """
        ...

    def release(
        self,
        session: SessionIdentity,
        *,
        end_reason: str,
        idempotency_key: IdempotencyKey,
    ) -> str:
        """End the session, release the account slot, and return agent status.

        Must atomically: end the lease, bump nothing on newer generations
        (late release is a no-op), and hand over any runs enqueued during the
        release transaction to the scheduler. Duplicate release is idempotent.
        """
        ...


class ContractFailure(Exception):
    """Raised by implementations with a stable ErrorCode from agy_cloud.errors."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
