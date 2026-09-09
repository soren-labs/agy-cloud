"""Storage adapters: GCS cloud adapter and the local (docker backend) adapter.

Owner: T2 implements both adapters against this interface; T10 uses the local
adapter for the docker session backend. One interface, two backends
(V1-SCOPE §2 "本地验证": full local test path without GCP).

Object layout is fixed by docs/CONTRACTS.md "Storage":
  agents/<agent>/snapshot/<seq>.tar.gz
  agents/<agent>/runs/<run>/events.jsonl | stderr.log | tests.log | result.json
  sessions/<session>/runner.log

Signed-URL rule (V1-SCOPE §5.3): URLs are minted by the control plane, scoped
to this task's objects only, and their validity must cover the session
deadline plus the shutdown window. Signed URLs never enter the agy container.
"""
from __future__ import annotations

from typing import Protocol

from agy_cloud.models import AgentId, RunId, Seq, SessionId


class StorageAdapter(Protocol):
    def upload_run_artifact(
        self, agent_id: AgentId, run_id: RunId, name: str, data: bytes
    ) -> str:
        """Upload one of events.jsonl / stderr.log / tests.log / result.json.

        Returns the object URI (gs://... or local path). Immutable once
        uploaded: re-upload of identical bytes is idempotent, contradictory
        bytes raise a conflict.
        """
        ...

    def upload_snapshot(
        self, agent_id: AgentId, seq: Seq, tarball: bytes, manifest: str
    ) -> str:
        """Upload snapshot <seq>.tar.gz with its integrity manifest."""
        ...

    def download_snapshot(self, agent_id: AgentId, seq: Seq) -> bytes:
        ...

    def issue_download_url(self, object_uri: str, *, expires_at: str) -> str:
        """Short-lived read URL for logs/result artifacts (public API logs)."""
        ...

    def delete_object(self, object_uri: str) -> None:
        """Reaper/expiry cleanup; only for snapshot prefixes and expired runs."""
        ...

    def issue_session_upload_urls(
        self, agent_id: AgentId, session_id: SessionId, *, valid_until: str
    ) -> dict[str, str]:
        """Pre-issued upload URLs for this session's runs (A6 rule).

        Validity must cover session_max + shutdown budget so a worker that
        loses the control plane can still persist WIP and the manifest.
        """
        ...
