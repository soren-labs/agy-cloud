# T0 ownership and evidence

T0 deliverable record: file ownership for downstream tasks, the contract-change
note, the deferred-work map, and verification evidence. Keep this current when
ownership boundaries change.

## Contract-change note

T0 modified `docs/CONTRACTS.md` (the shared contract). The written note lives
in `docs/CONTRACTS.md` under "Contract-change note (T0)": removal of
`POST /webhooks/github` from V1 (P2), snake_case field vocabulary, explicit
scheduler/worker auth separation with bootstrap binding and token refresh,
machine-checkable error taxonomy, fixed ID/timestamp formats, and explicit
deferred-field rejection. Downstream tasks that need contract changes must add
their own note there and pause affected tasks (V1-TASKS rule).

## File ownership (T1–T13a)

| Owner | Files / directories | Notes |
| --- | --- | --- |
| **T0 (baseline, this PR)** | `docs/CONTRACTS.md`, `contracts/**`, `agy_cloud/**`, `tests/fake_agy/**`, `tests/test_fake_agy.py`, `tests/test_agy_command.py`, `tests/test_lifecycle.py`, `tests/test_snapshot_manifest.py`, `tests/test_contract_alignment.py`, `tests/test_verify_creds.py`, `Makefile`, `pyproject.toml`, `.github/workflows/ci.yml`, `scripts/verify_creds.py`, `README.md`, `docs/T0-OWNERSHIP.md`, `api/__init__.py`, `worker/__init__.py`, `cli/__init__.py`, `.gitignore` | Shared contract surface. Changes to `agy_cloud/**` or `contracts/**` require a contract-change note. |
| T1 | `infra/**` (deploy, iam, image-build, scheduler scripts) | May propose CI workflow additions; `ci.yml` edits coordinate with T0 owner. |
| T2 | `api/models.py`, `api/auth.py`, `api/services/{firestore,storage,secrets}.py` + local adapters | Implements `agy_cloud.protocols.repository/storage`; negative tests for internal identity and session scoping. |
| T3 | `worker/Dockerfile.agent`, `worker/settings.template.json`, `worker/iptables.sh`, container hardening | Credential-leak negative tests; runs fake_agy in-container. |
| T4 | `cli/agyctl.py` | Mock-API command tests; explicit unsupported-field errors. |
| T5 | `api/services/github_app.py` | Implements `agy_cloud.protocols.github`; token mint/refresh, idempotent PR, Check Run. |
| T6 | `api/services/gce.py` | Implements `agy_cloud.protocols.session_backend`; idempotent create by session name, delete with disks. |
| T7 | `api/routes/**`, `api/main.py` | Route assembly; T7/T8 both touch assembly — each wave designates the integration owner. |
| T8 | `api/services/{scheduler,reaper,accounts}.py` | Implements `agy_cloud.protocols.accounts` + repository queue ops; must satisfy every lifecycle rule. |
| T9 | `worker/runner.py`, `worker/startup.sh` | Uses `agy_cloud.agy` seam (AGY_BIN) and `agy_cloud.protocols.worker` callbacks; never handles tokens in the agy container. |
| T10 | `tests/e2e_local.sh`, docker session backend under `tests/` | Replaces the `e2e-local` exit-3 guard with a real suite. |
| T12 | `tests/e2e_gcp.sh`, fault injection | Replaces the `itest` exit-3 guard; real A1–A6 evidence. |
| T13a | README final pass, CLI polish | Merges into T0-owned README at that point. |

Shared integration rules: `agy_cloud/**`, `contracts/**`, `Makefile`,
`pyproject.toml`, `.github/workflows/ci.yml`, `README.md` are T0-owned;
downstream PRs may consume them and must propose changes through the owner
plus a contract-change note when semantics shift. `tests/fake_agy/**` is
shared test infrastructure: extend it (new fault modes) rather than fork it.

## Deferred-work map (honest checks)

| Make target | Status | Replaced by |
| --- | --- | --- |
| `lint` | runs (compileall + ruff; no swallowed failures) | — |
| `test` | runs (122 tests at T0) | grows with each task |
| `contract-check` | runs (openapi ↔ CONTRACTS.md ↔ agy_cloud ↔ fixtures) | — |
| `verify-creds` | runs read-only when gcloud is authenticated | — |
| `itest` | exits 3: NOT IMPLEMENTED | T12 |
| `e2e-local` | exits 3: NOT IMPLEMENTED | T10 |
| CI `lint-unit` | required check, runs on all PRs, no cloud creds | — |
| CI `cloud-verify` | label `run-cloud-verify` (same-repo) or dispatch selection | — |

## T0 verification evidence (actual runs, redacted)

Commands run in the development sandbox (Python 3.12.3; PEP 668 system, so
dev tools from `pyproject.toml` dev extras were installed into a `.venv` and
make was invoked as `make <target> PY=.venv/bin/python` — the `PY` override
is documented in the Makefile header; CI uses plain `make` on setup-python):

| Command | Result |
| --- | --- |
| `make lint PY=.venv/bin/python` | PASS, exit 0 (compileall + `ruff check .`) |
| `make test PY=.venv/bin/python` | PASS, exit 0 — 147 passed (review follow-up: 25 new tests for the six blocking findings) |
| `make contract-check PY=.venv/bin/python` | PASS, exit 0 — `OK (25 operations, 63 schemas)` |
| `make verify-creds PY=.venv/bin/python` (agy-ci identity active) | PASS, exit 0 — 6/6 checks: identity `agy-ci@agy-cloud-ci.iam.gserviceaccount.com`, project `agy-cloud-ci`, Firestore default database reachable, `gs://agy-cloud-agy-cloud-ci` listable, Compute listing (0 instances), named secret `e2e-fake-agy-marker` read (28 bytes; payload not printed) |

Failure-path demonstrations (disposable copies in `/tmp`, nothing committed):

| Demonstration | Result |
| --- | --- |
| `make itest` / `make e2e-local` | both print NOT IMPLEMENTED and exit nonzero (recipe exit 3; make reports `Error 3`, wrapper exit 2) |
| ruff on a deliberately broken temp file | FAIL, exit 1 |
| pytest on a deliberately failing temp test | FAIL, exit 1 |
| contract-check on a copy with a renamed endpoint (parity broken) | FAIL, exit 1, precise diff reported |

Not performed in T0 (truthful omissions): real-agy runs (no subscription use
in T0), docker/local e2e (T10), real-cloud integration/fault injection (T12),
GitHub App effects (T5), actual Actions WIF run (needs an authorized GitHub
identity to dispatch; command recorded in the PR description).
