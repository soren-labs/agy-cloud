.PHONY: lint test itest e2e-local verify-creds

lint:
	python3 -m compileall -q api worker cli tests
	python3 -m ruff check . 2>/dev/null || true

test:
	python3 -m pytest -q

itest:
	@echo "T0 bootstrap: integration tests are not implemented yet"

e2e-local:
	@echo "T0 bootstrap: local e2e backend is not implemented yet"

verify-creds:
	@test -n "$$GCP_PROJECT"
	@test -n "$$AGY_GCS_BUCKET"
	@echo "GCP_PROJECT=$$GCP_PROJECT"
	@echo "AGY_GCS_BUCKET=$$AGY_GCS_BUCKET"
	@echo "Credential values are intentionally not printed"
