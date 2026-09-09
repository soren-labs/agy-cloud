"""AGY_BIN seam and headless command construction (P0 flag contract)."""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from agy_cloud.agy import (
    DEFAULT_PRINT_TIMEOUT,
    AgyPrintSpec,
    build_print_command,
    render_command,
    resolve_agy_bin,
)


def test_agy_bin_env_selects_binary():
    assert resolve_agy_bin({"AGY_BIN": "/opt/agy-1.1.27/agy"}) == "/opt/agy-1.1.27/agy"


def test_default_only_at_execution_boundary():
    assert resolve_agy_bin({}) == "agy"
    assert resolve_agy_bin({"AGY_BIN": ""}) == "agy"
    assert resolve_agy_bin({"AGY_BIN": "   "}) == "agy"


def test_print_flag_uses_equals_form():
    argv = build_print_command("agy", AgyPrintSpec(prompt="do the task"))
    print_arg = argv[-1]
    assert print_arg.startswith("--print="), "P0: --print MUST be joined with '='"
    assert print_arg == "--print=do the task"
    assert "--print" not in argv[:-1]  # never a separate flag+value pair


def test_prompt_is_one_argv_entry_even_with_shell_metacharacters():
    prompt = "rm -rf /; $(whoami) `echo hi` 'quoted' \"double\""
    argv = build_print_command("agy", AgyPrintSpec(prompt=prompt))
    assert argv[-1] == f"--print={prompt}"
    rendered = render_command(argv)
    assert shlex.split(rendered) == argv  # round-trips: one entry, no expansion


def test_full_command_shape():
    argv = build_print_command(
        "/usr/local/bin/agy",
        AgyPrintSpec(
            prompt="write tests",
            model="gemini-3.1-pro-high",
            conversation_id="conv-42",
            output_format="stream-json",
            add_dir="/home/ubuntu/ws",
            print_timeout="30m",
        ),
    )
    assert argv == [
        "/usr/local/bin/agy",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--model",
        "gemini-3.1-pro-high",
        "--conversation",
        "conv-42",
        "--add-dir",
        "/home/ubuntu/ws",
        "--print-timeout",
        "30m",
        "--print=write tests",
    ]


def test_optional_flags_omitted_when_unset():
    argv = build_print_command("agy", AgyPrintSpec(prompt="p"))
    assert "--model" not in argv
    assert "--conversation" not in argv
    assert "--json-schema" not in argv
    assert "--print-timeout" in argv  # default always explicit
    assert argv[argv.index("--print-timeout") + 1] == DEFAULT_PRINT_TIMEOUT


def test_json_schema_flag_forwarded():
    argv = build_print_command("agy", AgyPrintSpec(prompt="p", json_schema='{"type":"object"}'))
    assert argv[argv.index("--json-schema") + 1] == '{"type":"object"}'


def test_invalid_output_format_rejected():
    import pytest

    with pytest.raises(ValueError, match="output format"):
        build_print_command("agy", AgyPrintSpec(prompt="p", output_format="text"))


def test_seam_end_to_end_with_fake(tmp_path):
    """The same seam drives fake_agy: prove AGY_BIN substitution works live."""
    fake = Path(__file__).parents[1] / "tests" / "fake_agy" / "fake_agy.py"
    argv = build_print_command(str(fake), AgyPrintSpec(prompt="FAKE:ok", add_dir=str(tmp_path)))
    proc = subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert '"status": "SUCCESS"' in proc.stdout
