"""Session backend: GCE VM lifecycle (T6 implements; T10 provides docker local).

Idempotency rules (V1-SCOPE §5.1):
- The instance name is derived from the session id
  (models.instance_name_for_session) and is deterministic.
- create_session is idempotent by name: a retry after a partial failure must
  first look up the same-name instance and only insert when absent.
- delete_session removes the instance AND its disks; it is idempotent
  (deleting an absent instance succeeds) and must never touch non-agy
  resources (label selector agy-role=worker, name prefix agy-s-).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from agy_cloud.models import Generation, SessionId


@dataclass(frozen=True)
class SessionSpec:
    session_id: SessionId
    agent_id: str
    instance_name: str
    zone: str
    machine_type: str
    image: str  # pinned version tag, e.g. agy-worker-v1
    startup_task_file: str  # GCS path of the first run's spec pointer


@dataclass(frozen=True)
class SessionHandle:
    session_id: SessionId
    instance_name: str
    zone: str
    generation: Generation


@dataclass(frozen=True)
class OrphanInstance:
    instance_name: str
    labels: dict[str, str]
    created_at: str


class SessionBackend(Protocol):
    def create_session(self, spec: SessionSpec) -> SessionHandle:
        """Create the worker VM for one session; idempotent by instance name."""
        ...

    def delete_session(self, session_id: SessionId, *, generation: Generation) -> None:
        """Delete instance + disks for this session; idempotent; fenced by generation."""
        ...

    def list_orphans(self) -> Iterator[OrphanInstance]:
        """List agy worker instances (label agy-role=worker) for the reaper.

        The reaper deletes only instances whose run is missing/terminal or
        whose age exceeds the platform budget; a VM executing follow-ups is
        never reaped merely because its first run finished.
        """
        ...

    def was_preempted(self, instance_name: str) -> bool:
        """Spot preemption check (P2 feature; protocol reserved for compat)."""
        ...
