import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
FAKE = ROOT / "tests" / "fake_agy" / "fake_agy.py"


def run_fake(fmt):
    with tempfile.TemporaryDirectory() as workspace:
        p = subprocess.run(
            [sys.executable, str(FAKE), "--output-format", fmt, "--add-dir", workspace, "--print", "FAKE:write marker.txt ok"],
        check=True,
        capture_output=True,
        text=True,
        )
    return [json.loads(x) for x in p.stdout.splitlines()]


def test_fake_json_shape():
    data = run_fake("json")[0]
    assert data["status"] == "SUCCESS"
    assert data["conversation_id"]


def test_fake_stream_shape():
    data = run_fake("stream-json")
    assert [x["event"] for x in data] == ["init", "step_update", "result"]
    assert data[-1]["result"]["status"] == "SUCCESS"
