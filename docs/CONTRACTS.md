# agy-cloud contracts — V1 (T0 baseline)

This file is the shared interface boundary for parallel implementation. Changes require a contract-change note and review by the owners of all affected tasks. The machine-readable contract is `contracts/openapi.yaml` (OpenAPI 3.1); `make contract-check` enforces that the endpoint lists, enums, error codes, and examples in this file and the OpenAPI document stay aligned. `agy_cloud/` is the Python projection of this contract (models, errors, lifecycle, protocols); tests keep it in sync.

## Contract-change note (T0)

Changes relative to the bootstrap commit, with reasons:

1. **`POST /webhooks/github` removed from the V1 contract.** Inbound GitHub events (`@agy` triggers, review aggregation) are P2 per `docs/V1-SCOPE.md` §3. It is now listed under "Deferred endpoints (P2)" only, and absent from the machine contract. The OUTBOUND status webhook contract is unchanged and remains V1 scope (optional per-agent, persisted retry).
2. **snake_case field vocabulary.** API JSON, Firestore fields, and the worker protocol use one canonical snake_case vocabulary (`created_at`, `pr_url`). This deviates from the camelCase examples in `DESIGN.md` §7.3; V1-SCOPE explicitly does not promise Cursor compatibility, and a single vocabulary removes a mapping layer across api/Firestore/worker. `DESIGN.md` is preserved as historical reference.
3. **`/internal/tick` authentication is scheduler-only and now explicitly separated from the worker session capability** (see "Internal authentication"). The bootstrap also specified Google ID tokens; T0 adds the bootstrap binding (capability issuance at VM creation), the generation fencing rule, and GitHub installation-token refresh semantics.
4. **Error taxonomy made machine-checkable**: stable `error.code` values with fixed HTTP statuses (`agy_cloud/errors.py` ↔ `openapi.yaml`).
5. **ID formats fixed** (`ag_`, `run_`, `s-` lowercase session ids so `agy-<session>` is a valid GCE name; RFC3339 `Z` timestamps).
6. **Deferred-field rejection is explicit**: V1 requests must reject P2 fields with `422 unsupported_field` instead of silently accepting them (per-endpoint list in `openapi.yaml` `x-deferred-fields`).

## Runtime constants

- Region: `us-central1`; default zone: `us-central1-a`; default machine: `e2-standard-2`.
- Runtime project is selected from deployment configuration; CI agents use `agy-cloud-ci` only.
- `AGY_BIN` selects the agy executable. The default is `agy`, and defaulting is allowed only inside `agy_cloud.agy.resolve_agy_bin()` (the execution boundary).
- agy JSON output has `conversation_id`, `status`, `response`, `duration_seconds`, `num_turns`, and `usage` (P0-verified; `contracts/schemas/agy_result.schema.json`).
- agy stream output is newline-delimited objects with `event=init`, `event=step_update`, and `event=result`; each payload is NESTED under the event name; the successful result is nested under `result` (`contracts/schemas/agy_stream_event.schema.json`). Consumers route on `event` and never parse a top-level `status`. Structured output (`--json-schema`) field naming is NOT P0-verified and is deferred to P2; V1 code mode never reads it.
- Timeouts/leases (V1-SCOPE §4): turn 30 min; session 2 h; platform budget 2 h 15 min; heartbeat 30 s; lease 120 s; late-lease detection within 180 s of the last successful heartbeat; final-report retry budget 60 s; shutdown budget 120 s. Numeric constants live in `agy_cloud/constants.py`.

## Durable objects

- `agent` — the durable conversation/branch/PR identity. One agent = one branch = one PR = one bound account.
- `run` — one prompt (launch or follow-up). `seq` is per-agent and incremented inside the enqueue transaction.
- `session` — one VM lifecycle; executes runs sequentially. Fenced by `(agent_id, session_id, generation)`; `generation` starts at 1 and increments on every rebind. A session may hold the agent lease for at most one agent at a time, and an agent has at most one live session.

Object identifiers: agents `ag_<A-Za-z0-9>{8,32}`, runs `run_<A-Za-z0-9>{8,32}`, sessions `s-[a-z0-9]{8,32}` (so the GCE instance name `agy-<session_id>` is RFC1035-valid), accounts `[a-z0-9][a-z0-9-]{1,62}`. Timestamps are RFC3339 UTC strings ending in `Z` (`agy_cloud/models.TIMESTAMP_RE`).

Agent states: `QUEUED`, `CREATING`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELLED`, `EXPIRED`, `ARCHIVED`. Transitions (enforced by `agy_cloud.lifecycle.AGENT_TRANSITIONS`): `QUEUED→CREATING→RUNNING` then `FINISHED|ERROR|CANCELLED`; terminal agents re-activate to `QUEUED` on follow-up; `FINISHED/ERROR/CANCELLED→EXPIRED` (30-day inactivity, snapshots deleted); `ARCHIVED` only from terminal states via `archive`, restored by `unarchive` to the recorded `archived_from`.

Run states: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`. A run never moves out of a terminal state; retries create evidence, not new runs, and failed runs are never automatically replayed.

Stop/archive behavior:

- `stop` is idempotent: cancels the RUNNING run and all QUEUED runs of the agent (each cancellation recorded with its own state change), terminates the session, keeps the latest snapshot for later follow-up. Stopping an already-terminal agent succeeds and cancels nothing.
- `archive` requires no active work (`409 agent_active` otherwise); it records `archived_from` and leaves branch/PR untouched. `unarchive` restores the pre-archive status.
- `followup` on `ARCHIVED`/`EXPIRED` agents is `409 invalid_state`.

## State and idempotency rules (V1-SCOPE §5.1, encoded in `agy_cloud/lifecycle.py`)

- The Firestore repository interface provides transactional `enqueue`, `claim`, `renew`, `pop`, `finish`, and `release` (`agy_cloud.protocols.repository.RunRepository`).
- Every retriable mutation accepts an idempotency key (`Idempotency-Key` header; `^[A-Za-z0-9._-]{8,128}$`). Same key + same payload → the stored result is replayed (no duplicate effect); same key + different payload → `409 idempotency_conflict`. HTTP retries therefore cannot double-enqueue.
- Creating a VM requires first reserving the session and account slot in ONE transaction (`claim`). The VM name derives from the session id; a retried claim must query the same-name resource instead of creating a second VM.
- `pop`, `claim`, `renew`, `finish`, `release` verify the presented `(session_id, generation)`; a late callback from an older generation MUST NOT change newer state (`lifecycle.check_session_callback` → `409 generation_mismatch`).
- Concurrent followup-vs-release is resolved inside the enqueue/release transaction (`lifecycle.resolve_followup_target`): runs enqueued before release stay with the current session (picked up via pop); runs enqueued after release are bound to a new session by the scheduler.
- Upload-before-finish: the worker uploads artifacts through pre-issued session upload URLs (valid for the session deadline + shutdown window) BEFORE calling `/internal/runs/{id}/finished`; the completion manifest references the uploaded objects as proof.
- Expired-lease recovery (reaper): a run whose completion manifest proves completion and upload is reconciled as-is and never overwritten as failed; otherwise the run is `FAILED(lost_lease)`, the slot/VM/disk are released, and nothing is replayed.
- Duplicate external GitHub effects are prevented by querying (repository, branch, head SHA, run id) before creating a PR/comment/Check Run. Exactly-once across GitHub/GCS/Firestore is explicitly NOT claimed.
- `tests_failed`: model SUCCESS with failing tests → run `FAILED(tests_failed)`, Check Run failure, optional draft PR; never shown as a successful task.
- `no_changes`: a run may complete with no diff → run `SUCCEEDED`, `pr_url=null`, `no_changes=true`; never create an empty commit to force a PR.
- A6 interruption: on control-plane unreachability the worker stops agy at its local lease deadline, must NOT push without a re-confirmed lease, uploads WIP snapshot + recovery materials within the budgets, and reports `interrupted`; the reaper reconciles from the manifest.

## Public API v1

All public endpoints use `Authorization: Bearer agyc_<opaque-key>` (stored as SHA-256 hashes), rate-limited 60 req/min/key. Pagination: `limit` (default 20, max 100) and opaque `cursor` for agents/runs/conversation; `offset` (0-based) for events. Deferred fields are rejected `422 unsupported_field` — see `x-deferred-fields` on `POST /v1/agents` and `POST /v1/agents/{agent_id}/followup` in `contracts/openapi.yaml` (mode=review/plan, prompt images, compute.spot, keep_warm_seconds, auto_fix, target.open_as, request_reviewer, SSE).

- `GET /v1/me`
- `GET /v1/models`
- `GET /v1/repositories`
- `POST /v1/agents` — creates an agent and its first queued run; requires an idempotency key; `201` first, `200` replay.
- `GET /v1/agents` — filters: `status`, `repository`; `limit`/`cursor`.
- `GET /v1/agents/{agent_id}`
- `GET /v1/agents/{agent_id}/conversation` — user/assistant messages ordered by run.
- `POST /v1/agents/{agent_id}/followup` — queues on the current session (active agent) or re-activates with a new session (terminal agent); requires an idempotency key.
- `POST /v1/agents/{agent_id}/stop` — idempotent cancel; keeps snapshot.
- `POST /v1/agents/{agent_id}/archive` — terminal agents only (`409 agent_active` otherwise).
- `POST /v1/agents/{agent_id}/unarchive` — restores `archived_from`.
- `GET /v1/agents/{agent_id}/runs` — oldest first; `limit`/`cursor`.
- `GET /v1/agents/{agent_id}/runs/{run_id}`
- `GET /v1/agents/{agent_id}/runs/{run_id}/events` — ordered agy stream events; `offset`/`limit` (JSON paging; SSE is P2).
- `GET /v1/agents/{agent_id}/runs/{run_id}/logs` — short-lived signed URLs for `stderr.log`/`tests.log` with `expires_at`.
- `GET /v1/accounts` — pool status; never token payloads.
- `POST /v1/accounts` — registers an account record (id, name).
- `POST /v1/accounts/{account_id}/token` — writes the agy OAuth token as a new secret version; compare-and-set via `expected_version` (`409 token_version_conflict` on staleness).

First-version omissions are explicit (P2): inbound `@agy` comments and `/webhooks/github`, auto-fix loops, MCP, SSE, review/plan product modes, image input, Spot, cross-account snapshot restore, dependency caching, `.agy/environment.json` dynamic parsing.

## Internal API

- `POST /internal/tick`
- `GET /internal/runs/{run_id}/spec`
- `POST /internal/runs/{run_id}/finished`
- `POST /internal/sessions/{session_id}/heartbeat`
- `POST /internal/sessions/{session_id}/finished`
- `POST /internal/agents/{agent_id}/pop-run`
- `POST /internal/accounts/{account_id}/token`

### Internal authentication (three strictly separated realms)

1. **Scheduler** — `POST /internal/tick` accepts ONLY a Google-signed ID token whose subject is the scheduler service account (`agy-sched@<project>`), with audience = the service URL. A worker identity on tick is `403 forbidden_worker`; a session capability NEVER authorizes tick; API keys cannot reach `/internal` at all.
2. **Worker identity** — all other internal endpoints require a Google ID token for the worker service account (`agy-worker@<project>`), minted from the VM metadata server and auto-refreshed (short-lived). It authenticates identity only.
3. **Session capability (bootstrap binding)** — authorization additionally requires `X-Agy-Session-Capability: <session_id>:<secret>`, a random capability generated at session creation, bound to `(session_id, generation)`, and delivered to the VM exclusively via instance startup metadata (read once by the host runner; never passed to the agy container, never logged). The capability is invalidated when the generation moves, so an old VM cannot act on a newer session. Workers never enumerate other runs or read another account's token (`403 forbidden_session` otherwise).

GitHub installation-token refresh: tokens are minted per repository with 1h validity and delivered as references in the run spec / pop responses; a long session receives a refreshed reference in heartbeat responses near expiry. Token values are never persisted in Firestore or logs. Account agy-token writeback (`POST /internal/accounts/{account_id}/token`) is compare-and-set on `expected_version` AND fenced by generation — an old session must never overwrite a newer credential.

## Outbound state webhook (V1 scope, optional per agent)

`POST <webhook.url>` with headers `X-Webhook-ID`, `X-Webhook-Event: statusChange`, `X-Webhook-Signature: sha256=<hmac-sha256(raw body, secret)>`. Payload schema: `OutboundWebhookEvent` in `contracts/openapi.yaml` (`event`, `timestamp`, agent `id`/`status`, optional `source`, `target`, `run_id`, `run_seq`, `summary`, `tests_status`, `commit`). Emitted for `RUNNING`, `FINISHED`, `ERROR`, `CANCELLED`, `EXPIRED`. Delivery is persisted (`webhook_deliveries`) and retried with exponential backoff, 5 attempts (10 s … 15 min). The secret is stored in Secret Manager; responses and logs never echo it.

## Storage

Firestore collections: `agents` (nested `runs`), `sessions`, `accounts`, `apikeys`, `repos`, `webhook_deliveries`.

GCS object prefixes: `agents/<agent>/snapshot/<seq>.tar.gz` (keep most recent 3, delete on EXPIRE), `agents/<agent>/runs/<run>/events.jsonl`, `stderr.log`, `tests.log`, `result.json`; `sessions/<session>/runner.log`.

Snapshots are uploaded before releasing a session. The snapshot contains the agy conversation state (P0 four-piece set: `brain/<conv>/`, `conversations/<conv>.db`, `annotations/<conv>.pbtxt`, `conversation_summaries.db`) plus an integrity manifest (`agy_cloud.protocols.snapshot.SnapshotManifest`: per-file sha256/size, conversation id, pinned agy version). It never includes a GitHub token, GCP credential, service-account key, or any production secret — enforced by `validate_snapshot_manifest()`'s denylist. Restore across agy versions is rejected explicitly (branch is preserved).

## Error taxonomy

Stable `error.code` values with fixed HTTP statuses (machine-checked against `contracts/openapi.yaml`): `unauthenticated` 401, `invalid_signature` 401 (P2 reserved), `rate_limited` 429, `invalid_request` 400, `unsupported_field` 422, `not_found` 404, `idempotency_conflict` 409, `invalid_state` 409, `agent_active` 409, `generation_mismatch` 409, `lease_not_held` 409, `token_version_conflict` 409, `forbidden_session` 403, `forbidden_scheduler` 403, `forbidden_worker` 403, `internal` 500. Error bodies are `{"error": {"code", "message", "request_id"}}`.

## Credential boundary

The CI service account is the only cloud credential available to development agents and belongs to `agy-cloud-ci` (`agy-ci@agy-cloud-ci.iam.gserviceaccount.com`). `make verify-creds` (scripts/verify_creds.py) performs bounded, redacted, READ-ONLY verification of: identity/project/bucket expectations, Firestore database access, `gs://agy-cloud-agy-cloud-ci` listing, Compute instance listing, and exactly one named secret (`e2e-fake-agy-marker`). It never lists all secrets, never prints token or secret payloads, never writes, and never touches production. Production secrets are never configured in Cursor or Hoplite. `agy-control` can manage only `agy-*` worker instances/disks and the named storage/secrets resources. `agy-worker` can invoke the API and pull its image; it has no project-wide datastore, storage, or secret access.
