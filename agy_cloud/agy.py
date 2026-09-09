"""AGY binary selection and command construction (the AGY_BIN injection seam).

AGENTS.md: the runtime binary is selected through AGY_BIN; defaulting to `agy`
is allowed only at the execution boundary — i.e. inside resolve_agy_bin().
Every caller (runner, tests, fake harness) must go through this module.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from agy_cloud.constants import AGY_BIN_DEFAULT

# P0-verified headless flags (docs/P0-INVENTORY.md, DESIGN §1.1/附录 A):
# `--print` MUST be joined with `=`, otherwise later flags are parsed as prompt.
DEFAULT_PRINT_TIMEOUT = "30m"


def resolve_agy_bin(env: dict[str, str] | None = None) -> str:
    """Resolve the agy executable; the `agy` default applies only here."""
    environ = os.environ if env is None else env
    agy_bin = (environ.get("AGY_BIN") or "").strip()
    return agy_bin or AGY_BIN_DEFAULT


@dataclass(frozen=True)
class AgyPrintSpec:
    """One headless agy turn (T9 runner builds these; fake_agy accepts them)."""

    prompt: str
    model: str | None = None
    conversation_id: str | None = None
    output_format: str = "json"  # "json" | "stream-json"
    add_dir: str = "."
    print_timeout: str = DEFAULT_PRINT_TIMEOUT
    json_schema: str | None = None  # accepted for argv compat; structured output is P2


def build_print_command(binary: str, spec: AgyPrintSpec) -> list[str]:
    """Build the argv for one headless turn.

    The prompt is passed as a single `--print=<prompt>` argument (never via
    shell expansion); large prompt bodies live in TASK.md and only a short
    pointer is passed on the command line (V1-SCOPE §5.2).
    """
    if spec.output_format not in ("json", "stream-json"):
        raise ValueError(f"unsupported output format: {spec.output_format!r}")
    if not binary:
        raise ValueError("empty agy binary")
    argv: list[str] = [binary]
    argv += ["--output-format", spec.output_format]
    argv += ["--dangerously-skip-permissions"]
    if spec.model:
        argv += ["--model", spec.model]
    if spec.conversation_id:
        argv += ["--conversation", spec.conversation_id]
    argv += ["--add-dir", spec.add_dir]
    argv += ["--print-timeout", spec.print_timeout]
    if spec.json_schema:
        argv += ["--json-schema", spec.json_schema]
    argv.append(f"--print={spec.prompt}")
    return argv


def render_command(argv: list[str]) -> str:
    """Shell rendering for logs; safe because the prompt is one argv entry."""
    return " ".join(shlex.quote(part) for part in argv)
