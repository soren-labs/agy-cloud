"""Snapshot and completion manifests (T0 contract objects; T9 produces, T8 reconciles).

Snapshot rule (CONTRACTS "Storage"): snapshots contain the agy conversation
state (the P0 four-piece set) plus an integrity manifest — never a GitHub
token, GCP credential, service-account key, or any production secret. The
denylist below is enforced by validate_snapshot_manifest() before upload and
by the reaper after recovery.

Completion manifest rule (V1-SCOPE §5.4): the worker uploads run artifacts
FIRST (pre-issued session upload URLs), then reports /finished referencing
the manifest. The reaper, on recovery, reads manifests before deciding the
fate of an interrupted session: a persisted completed run is never overwritten
as failed.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from agy_cloud.models import (
    AgentId,
    Generation,
    RunId,
    Seq,
    SessionId,
    Usage,
)

SNAPSHOT_STATE_FILES = (
    "conversation_summaries.db",
)  # plus brain/<conv>/, conversations/<conv>.db, annotations/<conv>.pbtxt
SNAPSHOT_KEEP = 3  # most recent versions retained (constants.SNAPSHOT_KEEP)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Forbidden anywhere inside a snapshot archive or its manifest.
FORBIDDEN_SNAPSHOT_NAMES = (
    "antigravity-oauth-token",
    "settings.json",  # may embed local trusted paths; re-templated at restore
    "id_rsa",
    "service_account.json",
    "gcp-sa-key.json",
    "github-token",
    "webhook-secret",
)
FORBIDDEN_SNAPSHOT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\.pem$"),
    re.compile(r"^credentials/", ),
)


@dataclass(frozen=True)
class SnapshotFileEntry:
    path: str  # archive-relative posix path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SnapshotManifest:
    agent_id: AgentId
    seq: Seq
    conversation_id: str
    agy_version: str  # pinned version; restore across versions is rejected
    created_at: str  # RFC3339 UTC
    files: tuple[SnapshotFileEntry, ...]
    unpushed_commit: str | None = None  # A6 recovery material (not pushed)

    def object_key(self) -> str:
        return f"agents/{self.agent_id}/snapshot/{self.seq}.tar.gz"


@dataclass(frozen=True)
class TestReport:
    status: str  # "passed" | "failed" | "skipped"
    command: str | None
    log_object: str | None


class CompletionOutcome:
    """Outcome codes for POST /internal/runs/{id}/finished (V1-SCOPE §5.2)."""

    SUCCEEDED = "succeeded"
    NO_CHANGES = "no_changes"  # completed, pr_url stays null; no empty commit
    TESTS_FAILED = "tests_failed"  # model ok, tests failed; Check Run failure
    FAILED = "failed"  # model/classified failure (quota, timeout, malformed...)
    INTERRUPTED = "interrupted"  # A6: control plane unreachable at finish time


@dataclass(frozen=True)
class CompletionManifest:
    run_id: RunId
    agent_id: AgentId
    session_id: SessionId
    generation: Generation
    seq: Seq
    outcome: str  # CompletionOutcome value
    response: str | None
    usage: Usage | None
    duration_seconds: float | None
    conversation_id: str | None  # new or continued conversation
    commit: str | None
    pr_url: str | None
    unpushed: bool  # True when WIP could not be pushed (lease lost / A6)
    tests: TestReport
    snapshot: SnapshotManifest | None  # required unless outcome==INTERRUPTED
    error_code: str | None = None
    uploaded_objects: dict[str, str] = field(default_factory=dict)  # name -> object URI


def validate_snapshot_manifest(manifest: SnapshotManifest) -> list[str]:
    """Return a list of contract violations (empty list = valid).

    Checks: relative posix paths only, no traversal, no duplicates, sha256
    format, non-negative sizes, and the credential denylist. Both the archive
    builder (T9) and the restore path (T2/T8) must call this.
    """
    problems: list[str] = []
    seen: set[str] = set()
    if not manifest.files:
        problems.append("snapshot manifest lists no files")
    for entry in manifest.files:
        if entry.path in seen:
            problems.append(f"duplicate snapshot entry: {entry.path}")
        seen.add(entry.path)
        if posixpath.isabs(entry.path):
            problems.append(f"non-relative snapshot path: {entry.path}")
        elif posixpath.normpath(entry.path).startswith(".."):
            problems.append(f"traversal in snapshot path: {entry.path}")
        if "\\" in entry.path:
            problems.append(f"non-posix snapshot path: {entry.path}")
        lowered = posixpath.basename(entry.path).lower()
        if lowered in FORBIDDEN_SNAPSHOT_NAMES:
            problems.append(f"forbidden file in snapshot: {entry.path}")
        for pattern in FORBIDDEN_SNAPSHOT_PATTERNS:
            if pattern.search(entry.path) or pattern.search(lowered):
                problems.append(f"forbidden snapshot path pattern: {entry.path}")
        if not SHA256_RE.match(entry.sha256):
            problems.append(f"bad sha256 for {entry.path}")
        if entry.size_bytes < 0:
            problems.append(f"negative size for {entry.path}")
    return problems


def validate_completion_manifest(manifest: CompletionManifest) -> list[str]:
    """Cross-field rules for the finish report."""
    problems: list[str] = validate_snapshot_manifest(manifest.snapshot) if manifest.snapshot else []
    if manifest.outcome == CompletionOutcome.INTERRUPTED and manifest.snapshot is None:
        pass  # A6: snapshot best-effort; recovery materials may substitute
    elif manifest.snapshot is None:
        problems.append("completion manifest missing snapshot manifest")
    if manifest.outcome == CompletionOutcome.NO_CHANGES and (manifest.commit or manifest.pr_url):
        problems.append("no_changes must not carry a commit or pr_url")
    if manifest.unpushed and manifest.commit:
        problems.append("unpushed report must not claim a pushed commit")
    if manifest.outcome == CompletionOutcome.SUCCEEDED and manifest.unpushed:
        problems.append("succeeded run cannot be unpushed")
    return problems
