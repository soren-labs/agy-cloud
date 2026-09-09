#!/usr/bin/env python3
"""Bounded, redacted, READ-ONLY verification of the agy-cloud-ci credentials.

What it does (per docs/CONTRACTS.md "Credential boundary"):
  - identity/project: active gcloud account and project match the expected
    CI identity (agy-cloud-ci / agy-ci@agy-cloud-ci.iam.gserviceaccount.com)
  - Firestore: the (default) database is reachable (read-only list)
  - GCS: gs://agy-cloud-agy-cloud-ci is listable (read-only)
  - Compute: instances are listable (read-only; count reported, not contents)
  - Secret Manager: reads EXACTLY ONE named secret (e2e-fake-agy-marker);
    it never lists secrets and never prints the payload (byte length only)

Guarantees:
  - every executed subcommand is checked against a fixed allowlist of
    read-only gcloud verbs; anything else is refused before execution
  - no writes, no secret listing, no token/signed-URL printing; a final
    redaction filter scans the report for credential material
  - authentication is whatever the platform provides: an activated service
    account (Hoplite/Cursor sandboxes) or GitHub Actions WIF via
    google-github-actions/auth + setup-gcloud. ADC-only setups without an
    active gcloud credential are reported as an environment failure.

Exit codes: 0 = all checks passed; 1 = a check failed (verification
failure); 2 = environment failure (gcloud missing/not authenticated).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol

DEFAULTS = {
    "project": "agy-cloud-ci",
    "bucket": "agy-cloud-agy-cloud-ci",
    "account": "agy-ci@agy-cloud-ci.iam.gserviceaccount.com",
    "secret": "e2e-fake-agy-marker",
}

# Exact read-only command prefixes allowed to execute. Anything else raises.
ALLOWED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("gcloud", "config", "get-value"),
    ("gcloud", "auth", "list"),
    ("gcloud", "firestore", "databases", "list"),
    ("gcloud", "storage", "ls"),
    ("gcloud", "compute", "instances", "list"),
    ("gcloud", "secrets", "versions", "access"),
)

# Credential material that must never appear in a report line.
FORBIDDEN_REPORT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'"private_key"'),
    re.compile(r"ya29\.[A-Za-z0-9._-]+"),
    re.compile(r'"refresh_token"'),
    re.compile(r'"client_email"'),
    re.compile(r"https://storage\.googleapis\.com/[^/?]+/[^?]+\?[^ ]*signature=", ),
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ENVIRONMENT = 2


class Completed:
    def __init__(self, rc: int, stdout: str, stderr: str) -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr


class CommandRunner(Protocol):
    def run(self, argv: list[str]) -> Completed: ...


class SubprocessRunner:
    def run(self, argv: list[str]) -> Completed:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, check=False
        )
        return Completed(proc.returncode, proc.stdout, proc.stderr)


class RefusedCommand(RuntimeError):
    """A check attempted to run a command outside the read-only allowlist."""


def assert_allowed(argv: list[str]) -> None:
    if not any(tuple(argv[: len(prefix)]) == prefix for prefix in ALLOWED_PREFIXES):
        raise RefusedCommand(f"command not in read-only allowlist: {' '.join(argv)}")


@dataclass(frozen=True)
class Expected:
    project: str
    bucket: str
    account: str
    secret: str

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Expected:
        return cls(
            project=(env.get("GCP_PROJECT") or DEFAULTS["project"]),
            bucket=(env.get("AGY_GCS_BUCKET") or DEFAULTS["bucket"]),
            account=(env.get("AGY_CI_ACCOUNT") or DEFAULTS["account"]),
            secret=(env.get("AGY_CI_SECRET") or DEFAULTS["secret"]),
        )


class Verifier:
    def __init__(
        self,
        runner: CommandRunner,
        expected: Expected,
        *,
        gcloud_binary: str = "gcloud",
    ) -> None:
        self.runner = runner
        self.expected = expected
        self.gcloud = gcloud_binary

    # --- helpers ---

    def gcloud_run(self, *args: str) -> Completed:
        argv = [self.gcloud, *args]
        assert_allowed(argv)
        return self.runner.run(argv)

    def gcloud_value(self, key: str) -> str:
        return self.gcloud_run("config", "get-value", key).stdout.strip()

    # --- checks; each returns (name, passed, detail) ---

    def check_identity(self) -> tuple[str, bool, str]:
        account = self.gcloud_value("account")
        if account != self.expected.account:
            return (
                "identity",
                False,
                f"account mismatch: expected {self.expected.account}, active {account or '<none>'}",
            )
        return ("identity", True, f"account={account}")

    def check_project(self) -> tuple[str, bool, str]:
        project = self.gcloud_value("project")
        if project != self.expected.project:
            return (
                "project",
                False,
                f"project mismatch: expected {self.expected.project}, active {project or '<none>'}",
            )
        return ("project", True, f"project={project}")

    def check_firestore(self) -> tuple[str, bool, str]:
        done = self.gcloud_run(
            "firestore", "databases", "list", "--project", self.expected.project
        )
        if done.rc != 0 or "(default)" not in done.stdout:
            return ("firestore", False, "default database not reachable (read-only list failed)")
        return ("firestore", True, "default database reachable")

    def check_gcs(self) -> tuple[str, bool, str]:
        done = self.gcloud_run("storage", "ls", f"gs://{self.expected.bucket}")
        if done.rc != 0:
            return ("gcs", False, f"gs://{self.expected.bucket} not listable")
        return ("gcs", True, f"gs://{self.expected.bucket} listable")

    def check_compute(self) -> tuple[str, bool, str]:
        done = self.gcloud_run(
            "compute", "instances", "list", "--project", self.expected.project
        )
        if done.rc != 0:
            return ("compute", False, "instances list failed (read-only)")
        listed = done.stdout.strip()
        count = 0 if not listed or listed.startswith("Listed 0") else len(listed.splitlines())
        return ("compute", True, f"instances listable ({count} listed)")

    def check_secret(self) -> tuple[str, bool, str]:
        done = self.gcloud_run(
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={self.expected.secret}",
            f"--project={self.expected.project}",
        )
        if done.rc != 0 or not done.stdout:
            return ("secret", False, f"named secret {self.expected.secret} not readable")
        # payload is intentionally captured but NEVER printed; report length only
        return ("secret", True, f"{self.expected.secret} read ({len(done.stdout)} bytes, payload not printed)")

    # --- driver ---

    def run_all(self) -> tuple[int, list[str]]:
        report: list[str] = ["verify-creds: read-only credential verification (redacted)"]
        checks = [
            self.check_identity,
            self.check_project,
            self.check_firestore,
            self.check_gcs,
            self.check_compute,
            self.check_secret,
        ]
        results = []
        for check in checks:
            name, passed, detail = check()
            results.append(passed)
            report.append(f"{name:<12} {'PASS' if passed else 'FAIL'} {detail}")
        redacted = self.redact(report)
        if all(results):
            redacted.append(f"verify-creds: OK ({len(results)}/{len(results)})")
            return EXIT_OK, redacted
        redacted.append(f"verify-creds: FAILED ({sum(1 for r in results if not r)} check(s) failed)")
        return EXIT_FAILED, redacted

    @staticmethod
    def redact(lines: list[str]) -> list[str]:
        safe: list[str] = []
        for line in lines:
            if any(pattern.search(line) for pattern in FORBIDDEN_REPORT_PATTERNS):
                safe.append("[redacted: report line contained credential material]")
            else:
                safe.append(line)
        return safe


def main(argv: list[str] | None = None) -> int:
    del argv  # no flags by design; configuration comes from the environment
    env = dict(os.environ)
    if shutil.which("gcloud") is None:
        print("verify-creds: ENVIRONMENT FAILURE — gcloud not found on PATH", file=sys.stderr)
        return EXIT_ENVIRONMENT
    verifier = Verifier(SubprocessRunner(), Expected.from_env(env))
    try:
        code, report = verifier.run_all()
    except RefusedCommand as exc:  # safety net: never execute outside the allowlist
        print(f"verify-creds: refused — {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    for line in report:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
