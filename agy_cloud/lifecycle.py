"""Lifecycle rules: state machines, generation fencing, and release-race policy.

These pure functions encode docs/V1-SCOPE.md §5.1/§5.2/§5.4 so the scheduler
(T8), reaper (T8), and runner (T9) share one interpretation. Tests pin the
rules; changing behavior requires a contract-change note.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from agy_cloud.models import (
    AgentStatus,
    ContractError,
    RunFailureCode,
    RunStatus,
    SessionIdentity,
)
from agy_cloud.protocols.snapshot import CompletionOutcome

# --- Agent state machine (V1-SCOPE §5.1) ---
AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.QUEUED: frozenset({AgentStatus.CREATING, AgentStatus.CANCELLED, AgentStatus.ERROR}),
    AgentStatus.CREATING: frozenset({AgentStatus.RUNNING, AgentStatus.CANCELLED, AgentStatus.ERROR}),
    AgentStatus.RUNNING: frozenset({AgentStatus.FINISHED, AgentStatus.CANCELLED, AgentStatus.ERROR}),
    # Follow-up re-activates terminal agents (new session); no automatic replay of failed runs.
    AgentStatus.FINISHED: frozenset(
        {AgentStatus.QUEUED, AgentStatus.EXPIRED, AgentStatus.ARCHIVED}
    ),
    AgentStatus.ERROR: frozenset({AgentStatus.QUEUED, AgentStatus.EXPIRED, AgentStatus.ARCHIVED}),
    AgentStatus.CANCELLED: frozenset({AgentStatus.QUEUED, AgentStatus.EXPIRED, AgentStatus.ARCHIVED}),
    # EXPIRED: snapshots deleted; only archive remains. Follow-up is rejected (invalid_state).
    AgentStatus.EXPIRED: frozenset({AgentStatus.ARCHIVED}),
    # ARCHIVED returns to the recorded pre-archive status via unarchive.
    AgentStatus.ARCHIVED: frozenset(
        {AgentStatus.FINISHED, AgentStatus.ERROR, AgentStatus.CANCELLED, AgentStatus.EXPIRED}
    ),
}

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

# Archive only applies to agents with no active work; active agents must stop first.
ACTIVE_AGENT_STATUSES = frozenset({AgentStatus.QUEUED, AgentStatus.CREATING, AgentStatus.RUNNING})
ARCHIVABLE_AGENT_STATUSES = frozenset(
    {AgentStatus.FINISHED, AgentStatus.ERROR, AgentStatus.CANCELLED, AgentStatus.EXPIRED}
)
TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


def is_valid_agent_transition(current: AgentStatus, to: AgentStatus) -> bool:
    return to in AGENT_TRANSITIONS[current]


def is_valid_run_transition(current: RunStatus, to: RunStatus) -> bool:
    return to in RUN_TRANSITIONS[current]


def can_archive(agent_status: AgentStatus) -> bool:
    return agent_status in ARCHIVABLE_AGENT_STATUSES


def can_accept_followup(agent_status: AgentStatus) -> bool:
    """Follow-up is allowed unless archived/expired; active agents queue it."""
    return agent_status not in frozenset({AgentStatus.ARCHIVED, AgentStatus.EXPIRED})


# --- Generation fencing (V1-SCOPE §5.1) ---
class FencingVerdict(StrEnum):
    ACCEPT = "accept"
    STALE_GENERATION = "stale_generation"  # late callback from an expired session
    UNKNOWN_SESSION = "unknown_session"  # callback names a different session
    FUTURE_GENERATION = "future_generation"  # never issued; reject as invalid


def check_session_callback(presented: SessionIdentity, current: SessionIdentity) -> FencingVerdict:
    """Rule: a late session callback must not change a newer generation.

    All mutating internal endpoints (renew/pop/finish/release/token writeback)
    evaluate this before touching state. STALE and UNKNOWN callbacks are
    rejected without side effects; the caller returns 409 generation_mismatch
    (or 403 forbidden_session for UNKNOWN_SESSION).
    """
    if presented.session_id != current.session_id:
        return FencingVerdict.UNKNOWN_SESSION
    if presented.generation < current.generation:
        return FencingVerdict.STALE_GENERATION
    if presented.generation > current.generation:
        return FencingVerdict.FUTURE_GENERATION
    return FencingVerdict.ACCEPT


FollowupTarget = Literal["current_session", "new_session"]


def resolve_followup_target(lease_held_by_session: bool) -> FollowupTarget:
    """Concurrent followup-vs-release rule (V1-SCOPE §5.1).

    Enqueue happens inside one transaction that also reads the agent lease:
    - enqueue before release  -> the run is bound to the CURRENT session,
      which picks it up with pop-run before finishing;
    - enqueue after release   -> the scheduler starts a NEW session (fresh VM).
    There is no interleaving: the transaction serializes the decision.
    """
    return "current_session" if lease_held_by_session else "new_session"


def expired_lease_run_outcome(
    manifest_outcome: str | None,
    *,
    error_code: str | None = None,
) -> tuple[RunStatus, str]:
    """Reaper rule for an expired lease (A5 / V1-SCOPE §5.4).

    The reaper first reads the persisted completion manifest and reconciles
    its outcome AS-IS; a persisted outcome is never upgraded:
    - succeeded / no_changes              -> SUCCEEDED (manifest_reconciled)
    - tests_failed / failed / interrupted -> FAILED, preserving the
      manifest's original error code (tests_failed is never SUCCEEDED)
    - cancelled                          -> CANCELLED
    - no manifest                        -> FAILED(lost_lease); slot/VM/disk
      released and nothing replayed.
    """
    if manifest_outcome is None:
        return RunStatus.FAILED, RunFailureCode.LOST_LEASE.value
    if manifest_outcome in (CompletionOutcome.SUCCEEDED, CompletionOutcome.NO_CHANGES):
        return RunStatus.SUCCEEDED, "manifest_reconciled"
    if manifest_outcome == CompletionOutcome.TESTS_FAILED:
        return RunStatus.FAILED, RunFailureCode.TESTS_FAILED.value
    if manifest_outcome == CompletionOutcome.FAILED:
        return RunStatus.FAILED, error_code or RunFailureCode.INTERNAL.value
    if manifest_outcome == CompletionOutcome.INTERRUPTED:
        return RunStatus.FAILED, RunFailureCode.INTERRUPTED.value
    if manifest_outcome == CompletionOutcome.CANCELLED:
        return RunStatus.CANCELLED, "cancelled"
    raise ContractError(f"unknown completion outcome: {manifest_outcome!r}")


def interruption_outcome(wip_uploaded: bool) -> tuple[RunStatus, str]:
    """A6 rule: control-plane interruption terminates the run, not the evidence.

    The worker stops agy at local lease expiry, uploads WIP snapshot and
    recovery materials within the shutdown budget, and reports `interrupted`.
    The run ends FAILED(interrupted) even if the model had not errored; the
    WIP commit is preserved as recovery material and explicitly unpushed.
    """
    del wip_uploaded  # both outcomes fail the run; upload state only affects evidence
    return RunStatus.FAILED, "interrupted"


# --- pop-run authorization (V1-SCOPE §5.1: lease-held precondition) ---
class PopAuthorization(StrEnum):
    POP_ALLOWED = "accept"
    # released or rebound lease: an old session must never steal an unbound run
    LEASE_NOT_HELD = "lease_not_held"
    # same session but stale or never-issued generation
    GENERATION_MISMATCH = "generation_mismatch"


def check_pop_authorization(
    presented: SessionIdentity, lease_holder: SessionIdentity | None
) -> PopAuthorization:
    """pop-run rule: only the CURRENT lease holder may claim the next run.

    A released lease (runs left unbound) or one rebound to a different
    session is LEASE_NOT_HELD — the unbound run waits for the scheduler to
    bind it to a NEW session; an old session must never steal it. A matching
    session with a stale or never-issued generation is GENERATION_MISMATCH.
    Rejected pops mutate nothing (RunRepository.pop contract).
    """
    if lease_holder is None or lease_holder.session_id != presented.session_id:
        return PopAuthorization.LEASE_NOT_HELD
    if check_session_callback(presented, lease_holder) is not FencingVerdict.ACCEPT:
        return PopAuthorization.GENERATION_MISMATCH
    return PopAuthorization.POP_ALLOWED
