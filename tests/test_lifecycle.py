"""Lifecycle rules: state machines, generation fencing, race policy, formats."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agy_cloud import lifecycle
from agy_cloud.models import (
    AgentId,
    AgentStatus,
    ContractError,
    Generation,
    RunStatus,
    SessionId,
    SessionIdentity,
    format_timestamp,
    instance_name_for_session,
    parse_timestamp,
    validate_account_id,
    validate_agent_id,
    validate_idempotency_key,
    validate_run_id,
    validate_session_id,
)
from agy_cloud.protocols.snapshot import CompletionOutcome

A = AgentStatus


# --- agent state machine ---

@pytest.mark.parametrize(
    ("current", "to", "allowed"),
    [
        (A.QUEUED, A.CREATING, True),
        (A.CREATING, A.RUNNING, True),
        (A.RUNNING, A.FINISHED, True),
        (A.RUNNING, A.ERROR, True),
        (A.RUNNING, A.CANCELLED, True),
        (A.FINISHED, A.QUEUED, True),  # followup re-activates
        (A.ERROR, A.QUEUED, True),  # user followup on preserved snapshot; no auto replay
        (A.FINISHED, A.ARCHIVED, True),
        (A.FINISHED, A.EXPIRED, True),
        (A.EXPIRED, A.ARCHIVED, True),
        (A.ARCHIVED, A.FINISHED, True),  # unarchive restores archived_from
        # invalid
        (A.FINISHED, A.RUNNING, False),
        (A.QUEUED, A.RUNNING, False),  # must pass through CREATING
        (A.ARCHIVED, A.QUEUED, False),  # unarchive only; followup needs unarchive first
        (A.EXPIRED, A.QUEUED, False),  # snapshots deleted; followup rejected
        (A.CANCELLED, A.RUNNING, False),
    ],
)
def test_agent_transitions(current, to, allowed):
    assert lifecycle.is_valid_agent_transition(current, to) is allowed


def test_every_agent_status_has_transition_entry():
    for status in AgentStatus:
        assert status in lifecycle.AGENT_TRANSITIONS


@pytest.mark.parametrize(
    ("current", "to", "allowed"),
    [
        (RunStatus.QUEUED, RunStatus.RUNNING, True),
        (RunStatus.QUEUED, RunStatus.CANCELLED, True),
        (RunStatus.RUNNING, RunStatus.SUCCEEDED, True),
        (RunStatus.RUNNING, RunStatus.FAILED, True),
        (RunStatus.RUNNING, RunStatus.CANCELLED, True),
        (RunStatus.SUCCEEDED, RunStatus.RUNNING, False),  # terminal
        (RunStatus.FAILED, RunStatus.RUNNING, False),
        (RunStatus.CANCELLED, RunStatus.RUNNING, False),
        (RunStatus.QUEUED, RunStatus.SUCCEEDED, False),
    ],
)
def test_run_transitions(current, to, allowed):
    assert lifecycle.is_valid_run_transition(current, to) is allowed


def test_archive_and_followup_gating():
    assert lifecycle.can_archive(A.FINISHED)
    assert lifecycle.can_archive(A.ERROR)
    assert lifecycle.can_archive(A.CANCELLED)
    assert lifecycle.can_archive(A.EXPIRED)
    assert not lifecycle.can_archive(A.QUEUED)
    assert not lifecycle.can_archive(A.CREATING)
    assert not lifecycle.can_archive(A.RUNNING)
    assert not lifecycle.can_archive(A.ARCHIVED)

    assert lifecycle.can_accept_followup(A.FINISHED)
    assert lifecycle.can_accept_followup(A.RUNNING)  # queues on current session
    assert not lifecycle.can_accept_followup(A.ARCHIVED)
    assert not lifecycle.can_accept_followup(A.EXPIRED)


# --- generation fencing ---

def identity(agent="ag_7HkQab12", session="s-0f1e2d3c4b5a", gen=1):
    return SessionIdentity(
        AgentId(agent), SessionId(session), Generation(gen)
    )


def test_fencing_accepts_matching_generation():
    assert lifecycle.check_session_callback(identity(), identity()) == lifecycle.FencingVerdict.ACCEPT


def test_fencing_rejects_stale_generation():
    verdict = lifecycle.check_session_callback(identity(gen=1), identity(gen=2))
    assert verdict == lifecycle.FencingVerdict.STALE_GENERATION


def test_fencing_rejects_future_generation():
    verdict = lifecycle.check_session_callback(identity(gen=3), identity(gen=2))
    assert verdict == lifecycle.FencingVerdict.FUTURE_GENERATION


def test_fencing_rejects_unknown_session():
    verdict = lifecycle.check_session_callback(identity(session="s-aaaa11112222"), identity())
    assert verdict == lifecycle.FencingVerdict.UNKNOWN_SESSION


# --- followup-vs-release race ---

def test_followup_before_release_goes_to_current_session():
    assert lifecycle.resolve_followup_target(True) == "current_session"


def test_followup_after_release_goes_to_new_session():
    assert lifecycle.resolve_followup_target(False) == "new_session"


# --- pop authorization (lease-held precondition, negative/race contract) ---

def test_pop_allowed_only_for_current_lease_holder():
    assert lifecycle.check_pop_authorization(identity(), identity()) == (
        lifecycle.PopAuthorization.POP_ALLOWED
    )


def test_pop_rejected_when_lease_was_released():
    # released lease: runs are unbound and must not be stolen by an old session
    assert lifecycle.check_pop_authorization(identity(), None) == (
        lifecycle.PopAuthorization.LEASE_NOT_HELD
    )


def test_pop_rejected_when_lease_rebound_to_new_session():
    holder = identity(session="s-9999aaaabbbb", gen=1)  # scheduler re-bound the agent
    old_session = identity(session="s-0f1e2d3c4b5a", gen=1)
    assert lifecycle.check_pop_authorization(old_session, holder) == (
        lifecycle.PopAuthorization.LEASE_NOT_HELD
    )


@pytest.mark.parametrize(("presented_gen", "holder_gen"), [(1, 2), (3, 2)])
def test_pop_rejected_on_stale_or_never_issued_generation(presented_gen, holder_gen):
    presented = identity(gen=presented_gen)
    holder = identity(gen=holder_gen)
    assert lifecycle.check_pop_authorization(presented, holder) == (
        lifecycle.PopAuthorization.GENERATION_MISMATCH
    )


# --- expired lease / interruption outcomes ---

@pytest.mark.parametrize(
    ("outcome", "error_code", "expected_status", "expected_detail"),
    [
        (CompletionOutcome.SUCCEEDED, None, RunStatus.SUCCEEDED, "manifest_reconciled"),
        (CompletionOutcome.NO_CHANGES, None, RunStatus.SUCCEEDED, "manifest_reconciled"),
        (CompletionOutcome.TESTS_FAILED, "tests_failed", RunStatus.FAILED, "tests_failed"),
        (CompletionOutcome.FAILED, "quota_exhausted", RunStatus.FAILED, "quota_exhausted"),
        (CompletionOutcome.FAILED, None, RunStatus.FAILED, "internal"),
        (CompletionOutcome.INTERRUPTED, "interrupted", RunStatus.FAILED, "interrupted"),
        (CompletionOutcome.CANCELLED, None, RunStatus.CANCELLED, "cancelled"),
        (None, None, RunStatus.FAILED, "lost_lease"),
    ],
)
def test_expired_lease_reconciles_manifest_outcome_as_is(
    outcome, error_code, expected_status, expected_detail
):
    assert lifecycle.expired_lease_run_outcome(outcome, error_code=error_code) == (
        expected_status,
        expected_detail,
    )


@pytest.mark.parametrize(
    "outcome",
    [CompletionOutcome.TESTS_FAILED, CompletionOutcome.INTERRUPTED, CompletionOutcome.CANCELLED],
)
def test_expired_lease_never_reconciles_bad_outcomes_to_succeeded(outcome):
    status, _ = lifecycle.expired_lease_run_outcome(outcome)
    assert status is not RunStatus.SUCCEEDED


def test_expired_lease_rejects_unknown_outcome():
    with pytest.raises(ContractError):
        lifecycle.expired_lease_run_outcome("mysterious")


@pytest.mark.parametrize("wip_uploaded", [True, False])
def test_interruption_always_fails_the_run(wip_uploaded):
    status, detail = lifecycle.interruption_outcome(wip_uploaded)
    assert status == RunStatus.FAILED
    assert detail == "interrupted"


# --- identifier formats ---

@pytest.mark.parametrize(
    ("validator", "good", "bad"),
    [
        (validate_agent_id, "ag_7HkQab12", "agent-1"),
        (validate_run_id, "run_01cd34ef", "run/1"),
        (validate_session_id, "s-0f1e2d3c4b5a", "S-Upper"),
        (validate_account_id, "pro-1", "Pro 1"),
        (validate_idempotency_key, "launch-0001", "short"),
    ],
)
def test_id_validation(validator, good, bad):
    assert validator(good) == good
    with pytest.raises(ContractError):
        validator(bad)


def test_instance_name_derives_from_session():
    assert instance_name_for_session("s-0f1e2d3c4b5a") == "agy-s-0f1e2d3c4b5a"


def test_instance_name_rejects_invalid_session():
    with pytest.raises(ContractError):
        instance_name_for_session("not-a-session")


def test_instance_name_length_guard():
    with pytest.raises(ContractError):
        instance_name_for_session("s-" + "a" * 60)


# --- timestamps ---

def test_timestamp_roundtrip():
    dt = datetime(2026, 9, 9, 12, 30, 0, 123000, tzinfo=UTC)
    stamp = format_timestamp(dt)
    assert stamp == "2026-09-09T12:30:00.123Z"
    assert parse_timestamp(stamp) == dt


def test_timestamp_rejects_naive_datetime():
    with pytest.raises(ContractError):
        format_timestamp(datetime(2026, 9, 9, 12, 30))  # noqa: DTZ001 - naive on purpose


def test_timestamp_rejects_non_utc_offset():
    with pytest.raises(ContractError):
        parse_timestamp("2026-09-09T14:30:00+02:00")


def test_timestamp_requires_z_suffix():
    with pytest.raises(ContractError):
        parse_timestamp("2026-09-09T12:30:00")


def test_timestamp_normalizes_to_utc():
    plus2 = datetime(2026, 9, 9, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    assert format_timestamp(plus2) == "2026-09-09T12:30:00.000Z"
