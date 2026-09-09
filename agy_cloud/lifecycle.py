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
    RunStatus,
    SessionIdentity,
)

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


def expired_lease_run_outcome(manifest_persisted: bool) -> tuple[RunStatus, str]:
    """Reaper rule for an expired lease (A5 / V1-SCOPE §5.4).

    A run whose completion manifest proves the run finished and its artifacts
    were uploaded must NOT be overwritten as failed; the reaper reconciles it
    as-is. Otherwise the run fails with lost_lease and the account slot and VM
    are released. Failed runs are never automatically replayed.
    """
    if manifest_persisted:
        return RunStatus.SUCCEEDED, "manifest_reconciled"
    return RunStatus.FAILED, "lost_lease"


def interruption_outcome(wip_uploaded: bool) -> tuple[RunStatus, str]:
    """A6 rule: control-plane interruption terminates the run, not the evidence.

    The worker stops agy at local lease expiry, uploads WIP snapshot and
    recovery materials within the shutdown budget, and reports `interrupted`.
    The run ends FAILED(interrupted) even if the model had not errored; the
    WIP commit is preserved as recovery material and explicitly unpushed.
    """
    del wip_uploaded  # both outcomes fail the run; upload state only affects evidence
    return RunStatus.FAILED, "interrupted"
