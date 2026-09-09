# agy-cloud contracts — T0 bootstrap

This file is the shared interface boundary for parallel implementation. Changes require a contract-change note and review by the owners of all affected tasks.

## Runtime constants

- Region: `us-central1`; default zone: `us-central1-a`; default machine: `e2-standard-2`.
- Runtime project is selected from deployment configuration; CI agents use `agy-cloud-ci` only.
- `AGY_BIN` selects the agy executable. The default is `agy`.
- agy JSON output has `conversation_id`, `status`, `response`, `duration_seconds`, `num_turns`, and `usage`.
- agy stream output is newline-delimited objects with `event=init`, `event=step_update`, and `event=result`; the successful result is nested under `result`.

## Durable objects

`agent` is the durable conversation/branch/PR. `run` is one prompt or follow-up. `session` is one VM lifecycle and may execute multiple sequential runs. A session is fenced by `(agent_id, session_id, generation)`.

Agent states: `QUEUED`, `CREATING`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELLED`, `EXPIRED`, `ARCHIVED`.

Run states: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`.

The Firestore repository interface must provide transactional `enqueue`, `claim`, `renew`, `finish`, `pop`, and `release` operations. Every mutation that can be retried accepts an idempotency key. A late session callback must not change a newer generation.

## Public API v1

All public endpoints use `Authorization: Bearer agyc_<opaque-key>`. Keys are stored as SHA-256 hashes. `/webhooks/github` uses HMAC `X-Hub-Signature-256`. Internal endpoints require a Google-signed ID token and an application-level session capability.

- `GET /v1/me`
- `GET /v1/models`
- `GET /v1/repositories`
- `POST /v1/agents` — creates an agent and its first queued run; accepts an idempotency key.
- `GET /v1/agents`, `GET /v1/agents/{agent_id}`
- `GET /v1/agents/{agent_id}/conversation`
- `POST /v1/agents/{agent_id}/followup`
- `POST /v1/agents/{agent_id}/stop`
- `POST /v1/agents/{agent_id}/archive`, `POST /v1/agents/{agent_id}/unarchive`
- `GET /v1/agents/{agent_id}/runs`, `GET /v1/agents/{agent_id}/runs/{run_id}`
- `GET /v1/agents/{agent_id}/runs/{run_id}/events`, `GET /v1/agents/{agent_id}/runs/{run_id}/logs`
- `GET/POST /v1/accounts`, `POST /v1/accounts/{account_id}/token`
- `POST /webhooks/github`

First-version omissions are explicit: `@agy` inbound comments, auto-fix loops, MCP, SSE, review/plan product modes, Spot, and cross-account snapshot restore.

## Internal API

- `POST /internal/tick`
- `GET /internal/runs/{run_id}/spec`
- `POST /internal/runs/{run_id}/finished`
- `POST /internal/sessions/{session_id}/heartbeat`
- `POST /internal/sessions/{session_id}/finished`
- `POST /internal/agents/{agent_id}/pop-run`
- `POST /internal/accounts/{account_id}/token`

Each internal response is scoped to the presented session capability and generation. Workers never enumerate other runs or retrieve another account's token.

## Storage

Firestore collections: `agents`, nested `runs`, `sessions`, `accounts`, `apikeys`, `repos`, and `webhook_deliveries`.

GCS object prefixes: `agents/<agent>/snapshot/<seq>.tar.gz`, `agents/<agent>/runs/<run>/events.jsonl`, `stderr.log`, `tests.log`, and `result.json`; `sessions/<session>/runner.log`.

Snapshots are uploaded before releasing a session. The snapshot includes the agy conversation state and an integrity manifest; it never includes a GitHub token, GCP credential, or production secret.

## Credential boundary

The CI service account is the only cloud credential available to development agents and belongs to `agy-cloud-ci`. Production secrets are never configured in Cursor or Hoplite. `agy-control` can manage only `agy-*` worker instances/disks and the named storage/secrets resources. `agy-worker` can invoke the API and pull its image; it has no project-wide datastore, storage, or secret access.
