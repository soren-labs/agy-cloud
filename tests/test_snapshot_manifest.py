"""Snapshot/completion manifest boundary rules (credential denylist, A6 fields)."""
from __future__ import annotations

import pytest

from agy_cloud.models import AgentId, Generation, RunId, Seq, SessionId, Usage
from agy_cloud.protocols.snapshot import (
    CompletionManifest,
    CompletionOutcome,
    SnapshotFileEntry,
    SnapshotManifest,
    validate_completion_manifest,
    validate_snapshot_manifest,
)
from agy_cloud.protocols.snapshot import (
    TestReport as RunTestReport,
)

CONV = "ac4682f7-fb06-497e-aae9-c44b6c733829"
SHA = "a" * 64
SHA2 = "b" * 64


def snapshot_manifest(files=None, **overrides) -> SnapshotManifest:
    base = {
        "agent_id": AgentId("ag_7HkQab12"),
        "seq": Seq(1),
        "conversation_id": CONV,
        "agy_version": "1.1.27",
        "created_at": "2026-09-09T10:00:00.000Z",
        "files": files
        if files is not None
        else (
            SnapshotFileEntry(f"conversations/{CONV}.db", SHA, 40960),
            SnapshotFileEntry("conversation_summaries.db", SHA2, 8192),
            SnapshotFileEntry(f"brain/{CONV}/state.bin", SHA, 2048),
            SnapshotFileEntry(f"annotations/{CONV}.pbtxt", SHA2, 512),
        ),
    }
    base.update(overrides)
    return SnapshotManifest(**base)


def completion_manifest(**overrides) -> CompletionManifest:
    snap = overrides.pop("snapshot", snapshot_manifest())
    base = {
        "run_id": RunId("run_01cd34ef"),
        "agent_id": AgentId("ag_7HkQab12"),
        "session_id": SessionId("s-0f1e2d3c4b5a"),
        "generation": Generation(1),
        "seq": Seq(1),
        "outcome": CompletionOutcome.SUCCEEDED,
        "response": "done",
        "usage": Usage(100, 40, 0, 0, 140),
        "duration_seconds": 25.0,
        "conversation_id": CONV,
        "commit": "c4a2b03" + "0" * 33,
        "pr_url": "https://github.com/soren-labs/agy-cloud-smoke/pull/4",
        "unpushed": False,
        "tests": RunTestReport("passed", "python3 -m unittest", "agents/a/runs/r/tests.log"),
        "snapshot": snap,
        "error_code": None,
        "uploaded_objects": {"result": "agents/a/runs/r/result.json"},
    }
    base.update(overrides)
    return CompletionManifest(**base)


# --- snapshot manifest ---

def test_p0_four_piece_snapshot_is_valid():
    assert validate_snapshot_manifest(snapshot_manifest()) == []


@pytest.mark.parametrize(
    "path",
    [
        "antigravity-oauth-token",
        "brain/x/antigravity-oauth-token",
        "id_rsa",
        "server.pem",
        "credentials/gcp-sa-key.json",
        "service_account.json",
    ],
)
def test_forbidden_files_rejected(path):
    problems = validate_snapshot_manifest(
        snapshot_manifest(files=(SnapshotFileEntry(path, SHA, 10),))
    )
    assert any("forbidden" in p for p in problems), problems


def test_absolute_and_traversal_paths_rejected():
    for bad in ("/etc/passwd", "../escape.txt", "brain/../../secret"):
        problems = validate_snapshot_manifest(
            snapshot_manifest(files=(SnapshotFileEntry(bad, SHA, 10),))
        )
        assert problems, bad


def test_duplicate_entries_rejected():
    problems = validate_snapshot_manifest(
        snapshot_manifest(
            files=(SnapshotFileEntry("a.db", SHA, 1), SnapshotFileEntry("a.db", SHA, 1))
        )
    )
    assert any("duplicate" in p for p in problems)


def test_bad_sha256_and_negative_size_rejected():
    problems = validate_snapshot_manifest(
        snapshot_manifest(files=(SnapshotFileEntry("a.db", "zz", -1),))
    )
    assert len(problems) >= 2


def test_empty_snapshot_rejected():
    assert validate_snapshot_manifest(snapshot_manifest(files=())) != []


def test_snapshot_object_key():
    assert snapshot_manifest().object_key() == "agents/ag_7HkQab12/snapshot/1.tar.gz"


# --- completion manifest ---

def test_successful_completion_valid():
    assert validate_completion_manifest(completion_manifest()) == []


def test_no_changes_must_not_claim_pr():
    problems = validate_completion_manifest(
        completion_manifest(
            outcome=CompletionOutcome.NO_CHANGES, commit=None, pr_url=None
        )
    )
    assert problems == []


def test_no_changes_with_commit_rejected():
    problems = validate_completion_manifest(completion_manifest(outcome=CompletionOutcome.NO_CHANGES))
    assert any("no_changes" in p for p in problems)


def test_unpushed_wip_cannot_claim_pushed_commit():
    problems = validate_completion_manifest(completion_manifest(unpushed=True, commit=None))
    assert any("unpushed" in p for p in problems)


def test_succeeded_cannot_be_unpushed():
    problems = validate_completion_manifest(completion_manifest(unpushed=True, commit=None))
    assert any("succeeded run cannot be unpushed" in p for p in problems)


def test_missing_snapshot_rejected_unless_interrupted():
    problems = validate_completion_manifest(completion_manifest(snapshot=None))
    assert any("missing snapshot" in p for p in problems)

    a6 = validate_completion_manifest(
        completion_manifest(
            outcome=CompletionOutcome.INTERRUPTED,
            snapshot=None,
            commit=None,
            pr_url=None,
            unpushed=True,
        )
    )
    assert a6 == []  # A6: recovery materials substitute for the snapshot


def test_tests_failed_with_draft_pr_is_shape_valid():
    problems = validate_completion_manifest(
        completion_manifest(outcome=CompletionOutcome.TESTS_FAILED, error_code="tests_failed")
    )
    assert problems == []
