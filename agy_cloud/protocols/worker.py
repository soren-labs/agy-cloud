"""Worker-side contract: run spec, session capability, and callbacks (T9).

RunSpec mirrors GET /internal/runs/{run_id}/spec exactly
(contracts/openapi.yaml RunSpec schema; tests keep them aligned).

Session capability (bootstrap binding, V1-SCOPE §5.3):
- The control plane generates a random capability at session creation and
  binds it to (session_id, generation). It reaches the VM only via instance
  startup metadata, readable once by the host runner — never through the agy
  container, never in logs.
- The worker presents it on every /internal call together with a Google ID
  token; both are checked. A capability is invalidated when the generation
  moves (rebind/reaper), so an old VM cannot act on a newer session.
- Worker ID tokens are short-lived Google OIDC minted from the VM metadata
  server and refresh automatically; they authenticate identity (agy-worker
  SA), NOT authorization. Authorization is the session capability. Scheduler
  identity (agy-sched) can call /internal/tick only; workers cannot, and the
  capability is never accepted on tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agy_cloud.models import (
    AccountId,
    AgentId,
    Generation,
    IdempotencyKey,
    Prompt,
    RunId,
    Seq,
    SessionId,
    SessionIdentity,
)
from agy_cloud.protocols.snapshot import CompletionManifest


@dataclass(frozen=True)
class SessionCapability:
    """Bootstrap-bound capability presented on internal endpoints."""

    session_id: SessionId
    generation: Generation
    capability_token: str  # random; delivered via instance metadata only

    def identity(self, agent_id: AgentId) -> SessionIdentity:
        return SessionIdentity(agent_id, self.session_id, self.generation)


@dataclass(frozen=True)
class AccountLease:
    account_id: AccountId
    token_object: str  # GCS/Secret reference; payload fetched at restore time
    agy_version: str


@dataclass(frozen=True)
class UploadUrls:
    """Pre-issued, session-scoped upload URLs (A6 rule: validity covers the
    session deadline + shutdown window so artifacts survive control-plane loss)."""

    events: str
    stderr: str
    tests_log: str
    result: str
    snapshot: str
    valid_until: str


@dataclass(frozen=True)
class RunSpec:
    run_id: RunId
    agent_id: AgentId
    session_id: SessionId
    generation: Generation
    seq: Seq
    prompt: Prompt
    origin: str
    model: str | None
    source_repository: str
    source_ref: str
    branch_name: str
    pr_url: str | None  # existing PR to update (followup), else null
    pr_number: int | None
    draft: bool
    account: AccountLease
    snapshot_object: str | None  # restore source; null for first run
    conversation_id: str | None  # continue conversation; null for first run
    machine_type: str
    test_command: str | None
    turn_timeout_minutes: int
    lease_expires_at: str
    upload_urls: UploadUrls
    github_token_ref: str | None  # installation token handle (host runner only)
    github_token_expires_at: str | None


class WorkerCallbacks(Protocol):
    """What the runner needs from the control plane (all internal endpoints)."""

    def heartbeat(self, capability: SessionCapability) -> str:
        """Renew lease; returns new lease_expires_at. 409 => stop, no new runs."""
        ...

    def pop_next_run(
        self, capability: SessionCapability, *, idempotency_key: IdempotencyKey
    ) -> RunSpec | None:
        """Fetch the next queued run bound to this session (or None)."""
        ...

    def report_run_finished(
        self,
        capability: SessionCapability,
        manifest: CompletionManifest,
        *,
        idempotency_key: IdempotencyKey,
    ) -> str:
        """Upload-before-finish: artifacts already uploaded; commits the run."""
        ...

    def report_session_finished(
        self,
        capability: SessionCapability,
        *,
        end_reason: str,
        idempotency_key: IdempotencyKey,
    ) -> str:
        """Session teardown; releases the account slot and lease."""
        ...

    def write_account_token(
        self,
        capability: SessionCapability,
        *,
        expected_version: int,
        token_object: str,
        idempotency_key: IdempotencyKey,
    ) -> int:
        """Token refresh writeback (agy refreshes access tokens in place).

        Compare-and-set on expected_version; a stale session generation or an
        older secret version must never overwrite a newer credential.
        Returns the new version.
        """
        ...
