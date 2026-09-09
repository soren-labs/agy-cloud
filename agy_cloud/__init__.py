"""agy-cloud shared contract layer (T0).

Typed schemas, lifecycle rules, and service protocols shared by the control
plane (api), the worker runner, and the CLI. This package is stdlib-only by
design: it is the machine-readable contract surface, not the product.

Ownership: T0 owns this package; changes require a contract-change note in
docs/CONTRACTS.md (see docs/T0-OWNERSHIP.md).
"""
from agy_cloud import constants, errors, lifecycle, models
from agy_cloud.agy import build_print_command, resolve_agy_bin

__all__ = [
    "build_print_command",
    "constants",
    "errors",
    "lifecycle",
    "models",
    "resolve_agy_bin",
]
