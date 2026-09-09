"""Behavioral tests for fake_agy, driven through the real AGY_BIN seam.

The fake is invoked exactly the way the T9 runner will invoke real agy:
argv built by agy_cloud.agy.build_print_command, binary selected by
resolve_agy_bin with AGY_BIN pointing at the fake. Output shapes are checked
against the P0-grounded JSON schemas in contracts/schemas/, not just against
ad-hoc asserts.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest
import referencing

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from agy_cloud.agy import AgyPrintSpec, build_print_command, resolve_agy_bin

FAKE = ROOT / "tests" / "fake_agy" / "fake_agy.py"
FIXTURES = ROOT / "tests" / "fake_agy" / "fixtures"
RESULT_SCHEMA = json.loads((ROOT / "contracts" / "schemas" / "agy_result.schema.json").read_text())
STREAM_SCHEMA = json.loads(
    (ROOT / "contracts" / "schemas" / "agy_stream_event.schema.json").read_text()
)
# the stream schema $refs the result schema by $id; register it locally so
# validation never needs network access
REGISTRY = referencing.Registry().with_resource(
    RESULT_SCHEMA["$id"], referencing.Resource.from_contents(RESULT_SCHEMA)
)
STREAM_VALIDATOR = jsonschema.Draft202012Validator(STREAM_SCHEMA, registry=REGISTRY)
RESULT_VALIDATOR = jsonschema.Draft202012Validator(RESULT_SCHEMA)


def validate_stream(event: dict) -> None:
    STREAM_VALIDATOR.validate(event)


def validate_result(data: dict) -> None:
    RESULT_VALIDATOR.validate(data)


def run_fake(spec: AgyPrintSpec, *, env: dict | None = None, timeout: float = 30):
    """Run the fake through the AGY_BIN seam (no shell, single --print= arg)."""
    binary = resolve_agy_bin({"AGY_BIN": str(FAKE)})
    argv = build_print_command(binary, spec)
    assert argv[0] == str(FAKE), "AGY_BIN must select the binary at the execution boundary"
    full_env = {k: v for k, v in os.environ.items() if k != "AGY_BIN"}
    if env:
        full_env.update(env)
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=full_env, check=False
    )


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "ws").mkdir()
    return str(tmp_path / "ws")


def make_spec(prompt: str, workspace: str, **kwargs) -> AgyPrintSpec:
    return AgyPrintSpec(prompt=prompt, add_dir=workspace, **kwargs)


# --- success shapes (P0-grounded) ---

def test_json_success_matches_fixture_and_schema(workspace):
    proc = run_fake(make_spec("FAKE:ok", workspace))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    fixture = json.loads((FIXTURES / "result_success.json").read_text())
    assert data == fixture
    validate_result(data)


def test_stream_success_nested_events_and_schema(workspace):
    proc = run_fake(make_spec("FAKE:ok", workspace, output_format="stream-json"))
    assert proc.returncode == 0, proc.stderr
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [e["event"] for e in events] == ["init", "step_update", "result"]
    # nested payloads, never a top-level status (P0 correction)
    assert "status" not in events[-1]
    assert events[-1]["result"]["status"] == "SUCCESS"
    for event in events:
        validate_stream(event)


def test_init_event_shape(workspace):
    proc = run_fake(make_spec("FAKE:ok", workspace, output_format="stream-json"))
    init = json.loads(proc.stdout.splitlines()[0])["init"]
    assert init["model"] == "fake-model"
    assert init["cwd"] == str(Path(workspace).resolve())
    assert isinstance(init["tools"], list) and init["tools"]
    assert "permission_mode" in init


# --- conversation continuity (P0: session is local state) ---

def test_followup_continues_same_conversation_and_increments_turns(workspace):
    first = json.loads(run_fake(make_spec("FAKE:ok", workspace)).stdout)
    second = json.loads(
        run_fake(make_spec("FAKE:ok", workspace, conversation_id=first["conversation_id"])).stdout
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert second["num_turns"] == first["num_turns"] + 1


def test_default_conversation_id_is_deterministic(tmp_path):
    ws1 = tmp_path / "a"
    ws2 = tmp_path / "b"
    ws1.mkdir()
    ws2.mkdir()
    r1 = json.loads(run_fake(make_spec("FAKE:ok", str(ws1))).stdout)
    r2 = json.loads(run_fake(make_spec("FAKE:ok", str(ws2))).stdout)
    assert r1["conversation_id"] == r2["conversation_id"]
    assert r1["conversation_id"] == "00000000-0000-4000-8000-000000000001"


def test_injected_conversation_id(workspace):
    proc = run_fake(make_spec("FAKE:ok", workspace), env={"FAKE_AGY_CONVERSATION_ID": "injected-conv-42"})
    assert json.loads(proc.stdout)["conversation_id"] == "injected-conv-42"


def test_state_file_lives_in_workspace(workspace):
    run_fake(make_spec("FAKE:ok", workspace))
    state = json.loads((Path(workspace) / ".fake-agy-state.json").read_text())
    assert state["conversations"]["00000000-0000-4000-8000-000000000001"] == 1


# --- failure / quota / timeout fault injection ---

def test_fail_exits_nonzero_with_stderr(workspace):
    proc = run_fake(make_spec("FAKE:fail", workspace))
    assert proc.returncode == 17
    assert "FAKE_FAILURE" in proc.stderr


def test_quota_matches_design_classifier(workspace):
    proc = run_fake(make_spec("FAKE:quota", workspace))
    assert proc.returncode != 0
    # DESIGN §5 classifier: /quota|RESOURCE_EXHAUSTED|429|rate limit/i
    assert re.search(r"RESOURCE_EXHAUSTED|quota|429|rate limit", proc.stderr, re.IGNORECASE)


def test_arbitrary_exit_code(workspace):
    proc = run_fake(make_spec("FAKE:exit 7", workspace))
    assert proc.returncode == 7


def test_controllable_timeout_exits_124_after_print_timeout(workspace):
    spec = make_spec("FAKE:sleep 30", workspace, print_timeout="1s")
    started = time.monotonic()
    proc = run_fake(spec, timeout=15)
    elapsed = time.monotonic() - started
    assert proc.returncode == 124
    assert elapsed < 10, "fake must not sleep past --print-timeout"


def test_sleep_under_timeout_completes_normally(workspace):
    proc = run_fake(make_spec("FAKE:sleep 0.1", workspace, print_timeout="30m"))
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == "SUCCESS"


# --- malformed / truncated / no-result outputs ---

def test_malformed_json_output_unparseable(workspace):
    proc = run_fake(make_spec("FAKE:malformed", workspace))
    assert proc.returncode == 0  # agy itself may exit 0 with broken output
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)


def test_malformed_stream_line(workspace):
    proc = run_fake(make_spec("FAKE:malformed", workspace, output_format="stream-json"))
    lines = proc.stdout.splitlines()
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[0])


def test_truncated_stream_has_no_result_event(workspace):
    proc = run_fake(make_spec("FAKE:truncated", workspace, output_format="stream-json"))
    events = []
    for line in proc.stdout.splitlines():
        events.append(json.loads(line))
    assert [e["event"] for e in events] == ["init", "step_update"]
    assert not any(e["event"] == "result" for e in events)  # no_result case


def test_truncated_json_is_incomplete_document(workspace):
    proc = run_fake(make_spec("FAKE:truncated", workspace))
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)


def test_noresult_json_is_empty_object(workspace):
    proc = run_fake(make_spec("FAKE:noresult", workspace))
    assert json.loads(proc.stdout) == {}


def test_noresult_stream_result_event_carries_null(workspace):
    proc = run_fake(make_spec("FAKE:noresult", workspace, output_format="stream-json"))
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert events[-1]["event"] == "result"
    assert events[-1]["result"] is None
    # the schema documents null-result as a *valid but failing* shape: the
    # runner (T9) must classify it as no_result, not crash on it
    validate_stream(events[-1])


# --- workspace writes and boundary ---

def test_write_inside_workspace(workspace):
    run_fake(make_spec("FAKE:write marker.txt hello-fake", workspace))
    assert (Path(workspace) / "marker.txt").read_text() == "hello-fake"


def test_write_escape_rejected(workspace):
    proc = run_fake(make_spec("FAKE:write ../escape.txt nope", workspace))
    assert proc.returncode == 2
    assert not (Path(workspace).parent / "escape.txt").exists()


# --- misc determinism ---

def test_custom_response_and_tokens(workspace):
    proc = run_fake(make_spec("FAKE:response custom-text FAKE:tokens 100 40", workspace))
    data = json.loads(proc.stdout)
    assert data["response"] == "custom-text\n"
    assert data["usage"]["input_tokens"] == 100
    assert data["usage"]["output_tokens"] == 40
    assert data["usage"]["total_tokens"] == 140


def test_json_schema_flag_accepted_with_p2_note(workspace):
    schema = '{"type":"object"}'
    proc = run_fake(make_spec("FAKE:ok", workspace, json_schema=schema))
    assert proc.returncode == 0
    assert "P2" in proc.stderr  # honest: structured output deferred, not invented
    json.loads(proc.stdout)  # output remains the documented result shape
