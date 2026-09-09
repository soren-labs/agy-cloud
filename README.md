# agy-cloud

Cloud coding-agent platform on temporary GCP VMs and an Antigravity Pro subscription pool. **Current state: public bootstrap with T0 foundations — this is NOT the completed product.** The control plane, scheduler, worker runner, GCE backend, and GitHub App service do not exist yet; they are built by tasks T1–T13a (see `docs/V1-TASKS.md`).

## What exists today (T0)

| Area | Files | What it is |
| --- | --- | --- |
| Contracts | `docs/CONTRACTS.md`, `contracts/openapi.yaml`, `contracts/schemas/` | Human + machine-readable (OpenAPI 3.1, JSON Schema) V1 contract for all public/internal endpoints, objects, errors, auth realms. Endpoint parity is machine-enforced. |
| Shared typed interfaces | `agy_cloud/` (stdlib-only) | Durable-object models, lifecycle/state machines, generation fencing, error taxonomy, runtime constants, and service `Protocol`s for repository transactions, storage, session backend, GitHub effects, clock, snapshots/manifests, worker callbacks, and account token CAS. Unimplemented by design — protocols, not a product. |
| Test infrastructure | `tests/fake_agy/` | Deterministic `agy` stand-in with P0-grounded json/stream-json shapes and fault injection (nonzero exit, quota, timeout, malformed/truncated/no-result, followup continuity), plus fixtures. |
| Honest checks | `Makefile`, `pyproject.toml` | `make lint` / `make test` / `make contract-check` run for real and fail loudly; `make itest` / `make e2e-local` are **unimplemented and exit nonzero (3) by design** — replaced by T12/T10. |
| CI | `.github/workflows/ci.yml` | Required job name **`lint-unit`** runs on every PR (including forks) with **no cloud credentials**. Read-only cloud verification runs only via the explicit label `run-cloud-verify` on a same-repo PR or an explicit `workflow_dispatch` selection, through GitHub OIDC → `agy-ci@agy-cloud-ci`; it never checks out PR head code. |
| Credential verification | `scripts/verify_creds.py` (`make verify-creds`) | Bounded, redacted, read-only check of the CI identity, Firestore, `gs://agy-cloud-agy-cloud-ci`, Compute listing, and exactly one named secret (`e2e-fake-agy-marker`). Never lists secrets, never prints payloads. |

## What does not exist yet (deferred)

API service (T2/T7), scheduler/reaper/account pool (T8), worker containers and runner (T3/T9), GCE backend (T6), GitHub App effects (T5), CLI (T4), docker local e2e (T10), real-cloud acceptance A1–A6 (T12), docs/CLI finish (T13a). Inbound GitHub webhooks (`POST /webhooks/github`), review/plan modes, auto-fix, SSE, MCP, Spot, and image input are P2 and explicitly rejected (`422 unsupported_field`) in V1.

## Checks

```bash
python3 -m pip install -e '.[dev]'   # PEP 668 systems: use a venv (see Makefile header)
make lint            # byte-compile + ruff (failures fail; nothing is swallowed)
make test            # pytest unit + contract-alignment suite
make contract-check  # machine validation of openapi.yaml <-> CONTRACTS.md <-> agy_cloud
make verify-creds    # read-only CI credential verification (needs authenticated gcloud)
```

`make itest` and `make e2e-local` print what will replace them and exit 3. Exit codes across the repo: 0 ok; 1 check/verification failure; 2 environment failure; 3 not-implemented (T0 deferred targets).

## Facts and boundaries

- **Accounts**: the Antigravity pool currently has **TWO** accounts. T0 does not require a third, and the five-concurrency release acceptance (A4) is **not complete** — it is a later gate, not a claim.
- **Model routing** for development/review is authoritative in `docs/V1-TASKS.md` (Hoplite: GLM 5.3 / DeepSeek; Cursor: Grok review).
- **Credentials**: cloud development credentials are limited to the `agy-cloud-ci` CI project. No production credentials, tokens, or signed URLs belong in this repository, PRs, logs, or test fixtures.
- `DESIGN.md` and `PREP.md` are preserved as **historical references**; `V1-SCOPE.md` is the current release baseline and `docs/CONTRACTS.md` is the contract authority.

## Development rules

See `AGENTS.md`: one task = one branch = one PR (`T<n>: <title>`, created with `gh pr create`), paste actual check results into the PR, never merge, never modify contracts without a written contract-change note. File ownership per task: `docs/T0-OWNERSHIP.md`.
