"""Durable-object models for agy-cloud V1 (contract layer).

Property names are the canonical API/Firestore field vocabulary (snake_case).
The JSON API serializes these names verbatim; see the contract-change note in
docs/CONTRACTS.md ("snake_case field vocabulary").

OpenAPI alignment: contracts/openapi.yaml component schemas must declare
exactly these properties; tests/test_contract_alignment.py enforces it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType

AgentId = NewType("AgentId", str)
RunId = NewType("RunId", str)
SessionId = NewType("SessionId", str)
AccountId = NewType("AccountId", str)
Generation = NewType("Generation", int)  # monotonic per session lifecycle
Seq = NewType("Seq", int)  # monotonic per agent, incremented inside enqueue transaction
IdempotencyKey = NewType("IdempotencyKey", str)


class ContractError(ValueError):
    """A value violates the shared contract (bad id, timestamp, or enum)."""


# --- ID formats (CONTRACTS.md "Object identifiers") ---
AGENT_ID_RE = re.compile(r"^ag_[A-Za-z0-9]{8,32}$")
RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9]{8,32}$")
# Session ids feed GCE instance names ("agy-" + session id), so they are
# lowercase RFC1035-safe from the start (V1-SCOPE §5.1: VM name derives from session id).
SESSION_ID_RE = re.compile(r"^s-[a-z0-9]{8,32}$")
ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def _validate(pattern: re.Pattern[str], value: str, what: str) -> None:
    if not isinstance(value, str) or not pattern.match(value):
        raise ContractError(f"invalid {what}: {value!r}")


def validate_agent_id(value: str) -> AgentId:
    _validate(AGENT_ID_RE, value, "agent id")
    return AgentId(value)


def validate_run_id(value: str) -> RunId:
    _validate(RUN_ID_RE, value, "run id")
    return RunId(value)


def validate_session_id(value: str) -> SessionId:
    _validate(SESSION_ID_RE, value, "session id")
    return SessionId(value)


def validate_account_id(value: str) -> AccountId:
    _validate(ACCOUNT_ID_RE, value, "account id")
    return AccountId(value)


def validate_idempotency_key(value: str) -> IdempotencyKey:
    _validate(IDEMPOTENCY_KEY_RE, value, "idempotency key")
    return IdempotencyKey(value)


def instance_name_for_session(session_id: str) -> str:
    """Deterministic GCE instance name for a session (idempotent create)."""
    sid = validate_session_id(session_id)
    name = f"agy-{sid}"
    if len(name) > 63:
        raise ContractError(f"instance name too long: {name!r}")
    return name


# --- Timestamps: RFC3339 UTC, always Z (CONTRACTS.md "Timestamps") ---
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def format_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ContractError("naive datetime not allowed; use UTC")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
        raise ContractError(f"invalid RFC3339 UTC timestamp: {value!r}")
    return datetime.fromisoformat(value)


# --- Enums (V1-SCOPE §5.1) ---
class AgentStatus(StrEnum):
    QUEUED = "QUEUED"
    CREATING = "CREATING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunOrigin(StrEnum):
    """V1 has only API-driven runs; github_comment/review/ci_autofix are P2."""

    API = "api"
    FOLLOWUP = "followup"


class SessionEndReason(StrEnum):
    FINISHED = "finished"
    CANCELLED = "cancelled"
    ERROR = "error"
    LOST_LEASE = "lost_lease"
    PREEMPTED = "preempted"
    TIMEOUT = "timeout"
    REAPED = "reaped"


class RunFailureCode(StrEnum):
    """error.code values for runs (V1-SCOPE §5.2, §5.4)."""

    MODEL_FAILED = "model_failed"  # agy exited nonzero without a classified cause
    QUOTA_EXHAUSTED = "quota_exhausted"  # stderr/result matched quota patterns
    TURN_TIMEOUT = "turn_timeout"  # --print-timeout exceeded
    MALFORMED_OUTPUT = "malformed_output"  # unparseable agy JSON/event stream
    NO_RESULT = "no_result"  # stream ended without a result event/payload
    TESTS_FAILED = "tests_failed"  # model SUCCESS but tests failed
    LOST_LEASE = "lost_lease"  # heartbeat/lease lost (A5)
    INTERRUPTED = "interrupted"  # control plane unreachable (A6)
    INTERNAL = "internal"


class AccountStatus(StrEnum):
    OK = "ok"
    COOLDOWN = "cooldown"
    NEEDS_RELOGIN = "needs_relogin"
    DISABLED = "disabled"


class TestsStatus(StrEnum):
    SKIPPED = "skipped"  # no test command configured
    PASSED = "passed"
    FAILED = "failed"


class SessionState(StrEnum):
    """Lifecycle of the lease/VM holder (internal)."""

    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    ENDED = "ENDED"


# --- Run outcome detail (V1-SCOPE §5.2) ---
NO_CHANGES = "no_changes"  # run succeeded but produced no diff; pr_url stays null


# --- Shared value objects ---
@dataclass(frozen=True)
class SessionIdentity:
    """Fencing token: a callback is only honored for this exact triple."""

    agent_id: AgentId
    session_id: SessionId
    generation: Generation


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cache_read_tokens: int
    total_tokens: int

    def validate(self) -> None:
        for name in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
            if getattr(self, name) < 0:
                raise ContractError(f"negative token count: {name}")


@dataclass(frozen=True)
class Source:
    repository: str  # normalized "owner/name" (github.com URLs accepted on input)
    ref: str


@dataclass(frozen=True)
class Target:
    branch_name: str
    url: str | None  # API resource URL of the agent
    pr_url: str | None
    pr_number: int | None
    auto_create_pr: bool
    draft: bool


@dataclass(frozen=True)
class RunTests:
    command: str | None
    status: TestsStatus | None  # null until the run executed
    log_url: str | None


@dataclass(frozen=True)
class RunError:
    code: RunFailureCode
    message: str


@dataclass(frozen=True)
class RunResult:
    commit: str | None  # hex sha pushed to the branch; null when unpushed or no changes
    pr_url: str | None  # null on no_changes (never fabricate an empty commit)
    no_changes: bool
    unpushed: bool  # A6: WIP committed locally, could not be pushed


@dataclass(frozen=True)
class Prompt:
    text: str
    task_file: str | None = None  # GCS path of TASK.md for large prompts


@dataclass(frozen=True)
class AgentStats:
    runs: int
    total_tokens: int
    total_seconds: float


@dataclass(frozen=True)
class AgentSnapshot:
    """Nested Agent.snapshot object exactly as published (closed schema)."""

    gcs_uri: str
    seq: Seq
    conversation_id: str
    agy_version: str
    updated_at: str


@dataclass(frozen=True)
class Agent:
    # Required-ness mirrors openapi.yaml Agent (parity-tested): schema-required
    # fields are positional; schema-optional fields default to None/false.
    id: AgentId
    status: AgentStatus
    mode: str  # V1: always "code"
    source: Source
    target: Target
    stats: AgentStats
    created_at: str
    updated_at: str
    expires_at: str
    model: str | None = None  # requested model; resolved default at first claim
    account_id: AccountId | None = None  # bound account (sticky); null until first claim
    snapshot: AgentSnapshot | None = None
    archived_from: AgentStatus | None = None  # pre-archive status restored by unarchive
    webhook_url: str | None = None
    webhook_secret_set: bool = False
    last_run_at: str | None = None
    archived_at: str | None = None


@dataclass(frozen=True)
class Run:
    id: RunId
    agent_id: AgentId
    seq: Seq
    prompt: Prompt
    origin: RunOrigin
    status: RunStatus
    tests: RunTests
    retries: int
    created_at: str
    result: RunResult | None = None
    usage: Usage | None = None
    duration_seconds: float | None = None
    response: str | None = None
    session_id: SessionId | None = None
    generation: Generation | None = None
    error: RunError | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class Session:
    id: SessionId
    agent_id: AgentId
    generation: Generation
    instance_name: str | None
    zone: str | None
    state: SessionState
    run_ids: list[RunId] = field(default_factory=list)
    created_at: str | None = None
    ended_at: str | None = None
    end_reason: SessionEndReason | None = None


@dataclass(frozen=True)
class AccountUsage:
    total_tokens: int
    runs: int


@dataclass(frozen=True)
class Account:
    id: AccountId
    name: str
    status: AccountStatus
    max_concurrent: int
    active_runs: int
    cooldown_until: str | None
    last_used_at: str | None
    usage_today: AccountUsage
    token_version: int  # Secret Manager version of the current agy token
    token_updated_at: str | None


@dataclass(frozen=True)
class ConversationMessage:
    text: str
    type: str  # "user_message" | "assistant_message"
    run_id: RunId
    created_at: str


# --- Outbound state webhook (CONTRACTS.md "Outbound state webhook") ---
OUTBOUND_WEBHOOK_EVENT = "statusChange"
OUTBOUND_WEBHOOK_STATUSES: tuple[AgentStatus, ...] = (
    AgentStatus.RUNNING,
    AgentStatus.FINISHED,
    AgentStatus.ERROR,
    AgentStatus.CANCELLED,
    AgentStatus.EXPIRED,
)
OUTBOUND_WEBHOOK_SIGNATURE_HEADER = "X-Webhook-Signature"  # value: sha256=<hmac-sha256(body, secret)>


@dataclass(frozen=True)
class WebhookTarget:
    """Target subset delivered in webhook payloads (closed published schema)."""

    branch_name: str | None = None
    pr_url: str | None = None


@dataclass(frozen=True)
class OutboundWebhookEvent:
    event: str
    timestamp: str
    id: AgentId
    status: AgentStatus
    source: Source | None = None
    target: WebhookTarget | None = None
    run_id: RunId | None = None
    run_seq: Seq | None = None
    summary: str | None = None
    tests_status: TestsStatus | None = None
    commit: str | None = None
