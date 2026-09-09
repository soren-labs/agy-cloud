"""Runtime constants fixed by docs/V1-SCOPE.md §4–§5 and docs/CONTRACTS.md.

Sources are cited per constant; do not change a value without a
contract-change note in docs/CONTRACTS.md.
"""
from __future__ import annotations

# --- Runtime constants (CONTRACTS.md "Runtime constants") ---
REGION = "us-central1"  # V1-SCOPE §4: region
ZONE = "us-central1-a"  # V1-SCOPE §4: default zone
DEFAULT_MACHINE_TYPE = "e2-standard-2"  # V1-SCOPE §4: default machine
AGY_BIN_DEFAULT = "agy"  # AGENTS.md: AGY_BIN selects the binary; default at execution boundary only

# --- Timeouts and leases (V1-SCOPE §4 "固定的运行决策") ---
TURN_TIMEOUT_MINUTES = 30  # per-turn agy --print-timeout
SESSION_MAX_MINUTES = 120  # runner self-imposed session limit
PLATFORM_BUDGET_MINUTES = 135  # platform backstop (session max + 15 min)
HEARTBEAT_INTERVAL_SECONDS = 30
LEASE_SECONDS = 120  # renewed by heartbeat
LATE_LEASE_GRACE_SECONDS = 180  # detection window from last successful heartbeat
FINAL_REPORT_RETRY_BUDGET_SECONDS = 60  # A6: total retry budget for final report
SHUTDOWN_BUDGET_SECONDS = 120  # A6: stop + shutdown total budget

# --- Queue and scheduling ---
MAX_CONCURRENT_RUNS_SMOKE = 1  # first smoke; five is the release acceptance (A4)
MAX_CONCURRENT_RUNS_RELEASE = 5
ACCOUNT_CONCURRENCY_DEFAULT = 1  # may rise to 2 only after same-account measurement

# --- Snapshots (V1-SCOPE §4) ---
SNAPSHOT_KEEP = 3  # most recent snapshot versions retained
SNAPSHOT_INACTIVITY_DAYS = 30  # snapshots expire after 30 days without activity

# --- Pagination (CONTRACTS.md "Pagination") ---
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100

# --- CI credential verification (CONTRACTS.md "Credential boundary") ---
CI_PROJECT = "agy-cloud-ci"
CI_BUCKET = "agy-cloud-agy-cloud-ci"
CI_SERVICE_ACCOUNT = "agy-ci@agy-cloud-ci.iam.gserviceaccount.com"
CI_MARKER_SECRET = "e2e-fake-agy-marker"  # the only secret verify-creds may read
CI_WIF_PROVIDER = (
    "projects/931055573859/locations/global/workloadIdentityPools/github/providers/github"
)
