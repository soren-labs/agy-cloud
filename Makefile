# agy-cloud T0 honest checks.
#
# All targets below either run for real or fail loudly. The two deferred
# targets exit with code 3 (distinct from pytest failure 1 and environment
# failure 2) so an unimplemented suite can never look green:
#   - `itest`      -> replaced by T12 (tests/e2e_gcp.sh + fault injection)
#   - `e2e-local`  -> replaced by T10 (docker session backend + e2e_local.sh)
# The replacing tasks must remove the exit-3 guards and run real suites.
#
# Dev tools are NOT installed by this Makefile. Install once per environment:
#   python3 -m pip install -e '.[dev]'
# (on PEP 668 systems use a venv: `uv venv .venv && uv pip install --python
# .venv/bin/python -e '.[dev]'`, then run make with PY=.venv/bin/python)

PY ?= python3

.PHONY: lint test contract-check check verify-creds itest e2e-local

lint:
	$(PY) -m compileall -q api worker cli tests agy_cloud contracts scripts
	$(PY) -m ruff check .

test:
	$(PY) -m pytest -q

contract-check:
	$(PY) contracts/validate_contracts.py

check: lint test contract-check

# Read-only, redacted verification of the agy-cloud-ci identity (see
# scripts/verify_creds.py). Requires an authenticated gcloud (platform SA
# key or GitHub Actions WIF). Exits 0/1/2 = ok / verification failed / env.
verify-creds:
	$(PY) scripts/verify_creds.py

# --- Deferred (T0): intentionally unimplemented, never green ---

itest:
	@echo "itest: NOT IMPLEMENTED in T0 — deferred to T12 (real-cloud integration + fault injection)." >&2
	@echo "itest: exiting nonzero by design so unimplemented work never looks passed." >&2
	@exit 3

e2e-local:
	@echo "e2e-local: NOT IMPLEMENTED in T0 — deferred to T10 (docker session backend local e2e)." >&2
	@echo "e2e-local: exiting nonzero by design so unimplemented work never looks passed." >&2
	@exit 3
