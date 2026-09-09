"""Unit tests for verify_creds: failure paths, redaction, misconfiguration, allowlist.

These tests NEVER touch real credentials: the verifier runs against an
injected fake CommandRunner. The live read-only run happens via
`make verify-creds` in an authenticated environment.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "verify_creds", ROOT / "scripts" / "verify_creds.py"
)
verify_creds = importlib.util.module_from_spec(_spec)
sys.modules["verify_creds"] = verify_creds
_spec.loader.exec_module(verify_creds)

SECRET_PAYLOAD = "super-secret-marker-payload-do-not-print-1234"


class FakeRunner:
    """Configurable gcloud stand-in; records every executed argv."""

    def __init__(self, *, account=None, project=None, responses=None):
        self.account = account or "agy-ci@agy-cloud-ci.iam.gserviceaccount.com"
        self.project = project or "agy-cloud-ci"
        self.responses = responses or {}
        self.executed: list[list[str]] = []

    def run(self, argv):
        self.executed.append(argv)
        joined = " ".join(argv)
        if joined.startswith("gcloud config get-value account"):
            return verify_creds.Completed(0, self.account + "\n", "")
        if joined.startswith("gcloud config get-value project"):
            return verify_creds.Completed(0, self.project + "\n", "")
        if joined.startswith("gcloud firestore databases list"):
            if "firestore" in self.responses:
                return self.responses["firestore"]
            return verify_creds.Completed(
                0, "name: projects/agy-cloud-ci/databases/(default)\nlocation: nam5\n", ""
            )
        if joined.startswith("gcloud storage ls"):
            if "gcs" in self.responses:
                return self.responses["gcs"]
            return verify_creds.Completed(0, "", "")
        if joined.startswith("gcloud compute instances list"):
            if "compute" in self.responses:
                return self.responses["compute"]
            return verify_creds.Completed(0, "Listed 0 items.\n", "")
        if joined.startswith("gcloud secrets versions access"):
            if "secret" in self.responses:
                return self.responses["secret"]
            return verify_creds.Completed(0, SECRET_PAYLOAD, "")
        raise AssertionError(f"unexpected command: {joined}")


def expected():
    return verify_creds.Expected(
        project="agy-cloud-ci",
        bucket="agy-cloud-agy-cloud-ci",
        account="agy-ci@agy-cloud-ci.iam.gserviceaccount.com",
        secret="e2e-fake-agy-marker",
    )


def run_with(runner):
    return verify_creds.Verifier(runner, expected()).run_all()


def test_all_pass_reports_ok():
    code, report = run_with(FakeRunner())
    assert code == 0
    assert any("identity" in line and "PASS" in line for line in report)
    assert any("firestore" in line and "PASS" in line for line in report)
    assert any("gcs" in line and "PASS" in line for line in report)
    assert any("compute" in line and "PASS" in line for line in report)
    assert any("secret" in line and "PASS" in line for line in report)
    assert any("OK (6/6)" in line for line in report)


def test_storage_failure_exits_nonzero_with_fail_line():
    runner = FakeRunner(responses={"gcs": verify_creds.Completed(1, "", "bucket missing")})
    code, report = run_with(runner)
    assert code == 1
    assert any("gcs" in line and "FAIL" in line for line in report)
    assert any("FAILED (1 check(s) failed)" in line for line in report)


def test_firestore_unreachable_fails():
    runner = FakeRunner(responses={"firestore": verify_creds.Completed(1, "", "denied")})
    code, report = run_with(runner)
    assert code == 1
    assert any("firestore" in line and "FAIL" in line for line in report)


def test_secret_unreadable_fails():
    runner = FakeRunner(responses={"secret": verify_creds.Completed(1, "", "not found")})
    code, report = run_with(runner)
    assert code == 1
    assert any("secret" in line and "FAIL" in line for line in report)


def test_misconfigured_account_fails_identity_check():
    runner = FakeRunner(account="someone-else@example.com")
    code, report = run_with(runner)
    assert code == 1
    assert any("identity" in line and "FAIL" in line for line in report)
    assert any("account mismatch" in line for line in report)


def test_misconfigured_project_fails_project_check():
    runner = FakeRunner(project="agy-cloud-prod")
    code, report = run_with(runner)
    assert code == 1
    assert any("project" in line and "FAIL" in line for line in report)
    assert "agy-cloud-prod" in "".join(report)


def test_secret_payload_never_printed_even_on_success():
    _, report = run_with(FakeRunner())
    joined = "\n".join(report)
    assert SECRET_PAYLOAD not in joined
    assert "payload not printed" in joined
    assert f"({len(SECRET_PAYLOAD)} bytes" in joined


def test_compute_reports_zero_instances_without_contents():
    _, report = run_with(FakeRunner())
    assert any("instances listable (0 listed)" in line for line in report)


def test_allowlist_refuses_secret_listing(tmp_path, monkeypatch):
    """The verifier must never list all secrets — only the named one."""
    runner = FakeRunner()
    verifier = verify_creds.Verifier(runner, expected())
    with pytest.raises(verify_creds.RefusedCommand):
        verifier.gcloud_run("secrets", "list")
    with pytest.raises(verify_creds.RefusedCommand):
        verifier.gcloud_run("secrets", "delete", "some-secret")
    with pytest.raises(verify_creds.RefusedCommand):
        verifier.gcloud_run("compute", "instances", "delete", "x")
    # and the marker read itself is allowed
    verifier.gcloud_run(
        "secrets", "versions", "access", "latest", "--secret=e2e-fake-agy-marker"
    )
    assert any("secrets versions access" in " ".join(argv) for argv in runner.executed)


def test_executed_commands_are_all_read_only():
    runner = FakeRunner()
    run_with(runner)
    for argv in runner.executed:
        verify_creds.assert_allowed(argv)


def test_redaction_replaces_credential_material():
    lines = [
        "identity PASS account=agy-ci@agy-cloud-ci.iam.gserviceaccount.com",
        "bad line -----BEGIN RSA PRIVATE KEY-----",
        'worse "refresh_token": "abc"',
    ]
    redacted = verify_creds.Verifier.redact(lines)
    assert redacted[0] == lines[0]
    assert "PRIVATE KEY" not in redacted[1]
    assert "refresh_token" not in redacted[2]


def test_expected_defaults_match_constants():
    from agy_cloud import constants

    e = verify_creds.Expected.from_env({})
    assert e.project == constants.CI_PROJECT
    assert e.bucket == constants.CI_BUCKET
    assert e.account == constants.CI_SERVICE_ACCOUNT
    assert e.secret == constants.CI_MARKER_SECRET


def test_expected_env_overrides():
    e = verify_creds.Expected.from_env(
        {"GCP_PROJECT": "other-project", "AGY_GCS_BUCKET": "gs-other", "AGY_CI_SECRET": "other-secret"}
    )
    assert e.project == "other-project"
    assert e.bucket == "gs-other"
    assert e.secret == "other-secret"
    assert e.account == "agy-ci@agy-cloud-ci.iam.gserviceaccount.com"
