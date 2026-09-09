# agy-cloud

Temporary bootstrap repository for the agy-cloud first version. The authoritative product design is in `docs/DESIGN.md`; the narrowed release boundary is in `docs/V1-SCOPE.md`; the task dependency map is in `docs/V1-TASKS.md`.

The repository is private and is intended to be used by Cursor Cloud Agents and Hoplite. Cloud agents receive only the CI GCP project credentials. Production Antigravity tokens, the production GitHub App private key, and all production credentials remain outside this repository and outside Cursor/Hoplite secrets.

This initial commit establishes the shared context and boundaries. It is not the implementation of the platform. Follow-up work must use one task, one branch, and one PR, and must not silently change `docs/CONTRACTS.md`.
