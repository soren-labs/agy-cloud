"""GitHub effects boundary (T5 implements github_app.py against this).

The runner never holds a GitHub token directly in the agy container; all
GitHub side effects happen through this boundary on the host runner
(V1-SCOPE §5.3). Duplicate-effect rule (V1-SCOPE §5.1): external GitHub
effects are deduplicated by querying (repository, branch, head SHA, run id)
before creating anything. Exactly-once across GitHub/GCS/Firestore is NOT
claimed — only per-effect dedup + idempotent retry.

Installation tokens are minted per repository with 1h validity; long sessions
refresh by requesting a fresh token on each spec/pop (and heartbeat close to
expiry). Tokens are never persisted in Firestore or logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agy_cloud.models import AgentId, RunId


@dataclass(frozen=True)
class InstallationToken:
    token_ref: str  # handle, never the token value itself
    repository: str
    expires_at: str  # RFC3339 UTC; caller must not cache past this


@dataclass(frozen=True)
class PullRequestRef:
    repository: str
    number: int
    url: str
    head_sha: str


class GithubEffects(Protocol):
    def mint_installation_token(self, repository: str) -> InstallationToken:
        """Fresh single-repo installation token; refresh before expiry."""
        ...

    def ensure_branch(self, repository: str, base_ref: str, branch_name: str) -> str:
        """Create agy/<branch> from base if absent; return base head sha."""
        ...

    def push(
        self,
        repository: str,
        branch_name: str,
        *,
        run_id: RunId,
        agent_id: AgentId,
    ) -> str:
        """Push the prepared commit; duplicate push of the same sha is a no-op.

        Unpushed rule (V1-SCOPE §5.4): if the lease was not re-confirmed, the
        runner MUST NOT push; WIP is packaged as recovery material instead.
        """
        ...

    def ensure_pull_request(
        self,
        repository: str,
        branch_name: str,
        *,
        run_id: RunId,
        draft: bool,
    ) -> PullRequestRef:
        """Deterministically create or reuse the agent's PR (never rely on
        auto-create). Reuse is keyed by (repository, branch); one PR per agent.

        tests_failed rule: a draft PR may be created for failed tests, but the
        run is still FAILED(tests_failed) and the Check Run reports failure —
        a failing test never shows as a successful task.
        no_changes rule: never create an empty commit just to open a PR.
        """
        ...

    def create_check_run(
        self,
        repository: str,
        head_sha: str,
        *,
        run_id: RunId,
        status: str,  # "passed" | "failed" | "skipped"
        summary: str,
        log_url: str | None,
    ) -> str:
        """Publish the agy/tests Check Run; idempotent per (head_sha, run_id)."""
        ...
