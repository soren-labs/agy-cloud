# Agent rules

- Read `docs/DESIGN.md`, `docs/V1-SCOPE.md`, `docs/V1-TASKS.md`, and the relevant section of `docs/CONTRACTS.md` before editing.
- Work only in the directory assigned by the task. Do not modify contracts or another task's files without a written contract-change note in the PR.
- Never use production credentials. Cloud development credentials are limited to `agy-cloud-ci`.
- Do not put any service-account JSON, GitHub token, Antigravity token, private key, or signed URL in the repository, PR, logs, or test fixtures.
- The runtime binary is selected through `AGY_BIN`; defaulting to `agy` is allowed only at the execution boundary.
- The agy container must not receive GitHub or GCP credentials. GitHub push, PR, Check Run, and GCS writes belong to the host runner.
- Run the checks listed by the task and paste their results into the PR. Do not claim a cloud or real-agy check was run unless its output is attached.
- Create the PR with `gh pr create`; do not rely on an auto-create option.
- PR title format: `T<n>: <short title>`. Include summary, tests, and any DESIGN deviation.
- Never merge a PR. Keep changes reviewable and avoid unrelated formatting churn.
