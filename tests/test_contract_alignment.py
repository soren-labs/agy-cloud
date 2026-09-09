"""Machine alignment between openapi.yaml, agy_cloud, docs/CONTRACTS.md, and CI.

These are structural/semantic checks, not "file contains word" checks: every
assertion compares two independently-authored sources that must agree.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import fields
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from agy_cloud import constants, models
from agy_cloud import errors as err
from agy_cloud.protocols import worker as worker_proto

_spec = importlib.util.spec_from_file_location(
    "validate_contracts", ROOT / "contracts" / "validate_contracts.py"
)
validate_contracts = importlib.util.module_from_spec(_spec)
sys.modules["validate_contracts"] = validate_contracts
_spec.loader.exec_module(validate_contracts)


def test_full_contract_validation_passes():
    spec = validate_contracts.load_spec()
    problems = (
        validate_contracts.check(spec)
        + validate_contracts.check_examples(spec)
        + validate_contracts.check_agy_schemas_and_fixtures()
    )
    assert problems == []


def test_endpoint_parity_with_human_contract():
    spec = validate_contracts.load_spec()
    human = validate_contracts.parse_human_endpoints()
    api_public, api_internal = validate_contracts.openapi_endpoint_sets(spec)
    assert human["public"] == api_public
    assert human["internal"] == api_internal


def test_run_spec_dataclass_matches_openapi_schema():
    spec = validate_contracts.load_spec()
    schema = spec["components"]["schemas"]["RunSpec"]
    dataclass_props = {f.name for f in fields(worker_proto.RunSpec)}
    openapi_props = set(schema["properties"])
    assert dataclass_props == openapi_props, (
        f"RunSpec drift: dataclass-only={dataclass_props - openapi_props} "
        f"openapi-only={openapi_props - dataclass_props}"
    )


def test_agent_and_run_status_enums_match_openapi():
    spec = validate_contracts.load_spec()
    schemas = spec["components"]["schemas"]
    assert set(schemas["AgentStatus"]["enum"]) == {s.value for s in models.AgentStatus}
    assert set(schemas["RunStatus"]["enum"]) == {s.value for s in models.RunStatus}
    assert set(schemas["RunFailureCode"]["enum"]) == {s.value for s in models.RunFailureCode}
    assert set(schemas["AccountStatus"]["enum"]) == {s.value for s in models.AccountStatus}
    assert set(schemas["SessionEndReason"]["enum"]) == {s.value for s in models.SessionEndReason}


def test_error_code_taxonomy_matches_openapi():
    spec = validate_contracts.load_spec()
    openapi_codes = set(spec["components"]["schemas"]["ErrorCode"]["enum"])
    python_codes = {code.value for code in err.ErrorCode}
    assert openapi_codes == python_codes
    for code in err.ErrorCode:
        assert err.ERROR_HTTP_STATUS[code] >= 400


def test_scheduler_and_worker_security_realms_are_separate():
    spec = validate_contracts.load_spec()
    paths = spec["paths"]
    assert paths["/internal/tick"]["post"]["security"] == [{"schedulerIdToken": []}]
    for path, ops in paths.items():
        if path == "/internal/tick" or not path.startswith("/internal/"):
            continue
        for method, op in ops.items():
            if method in ("get", "post"):
                assert op["security"] == [{"workerIdToken": [], "sessionCapability": []}], (
                    f"{method.upper()} {path} must require ID token AND session capability"
                )


def test_outbound_webhook_contract_present_and_inbound_absent():
    spec = validate_contracts.load_spec()
    assert "OutboundWebhookEvent" in spec["components"]["schemas"]
    assert "/webhooks/github" not in spec["paths"]
    deferred = spec["info"].get("x-deferred-endpoints", {})
    assert "POST /webhooks/github" in deferred


def test_constants_match_ci_workflow_expectations():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    cloud_verify = workflow["jobs"]["cloud-verify"]
    auth_step = next(s for s in cloud_verify["steps"] if "google-github-actions/auth" in str(s.get("uses", "")))
    assert auth_step["with"]["workload_identity_provider"] == constants.CI_WIF_PROVIDER
    assert auth_step["with"]["service_account"] == constants.CI_SERVICE_ACCOUNT


def test_ci_workflow_policy():
    workflow_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    workflow = yaml.safe_load(workflow_text)

    # required check name is exactly lint-unit
    assert workflow["jobs"]["lint-unit"]["name"] == "lint-unit"

    # lint-unit must not receive cloud credentials
    lint_perms = workflow["jobs"]["lint-unit"].get("permissions", {})
    assert "id-token" not in lint_perms, "lint-unit must never mint OIDC tokens"
    lint_text = yaml.dump(workflow["jobs"]["lint-unit"])
    assert "google-github-actions/auth" not in lint_text
    assert "secrets." not in lint_text

    # cloud-verify: least permissions, id-token only for WIF
    cloud_perms = workflow["jobs"]["cloud-verify"]["permissions"]
    assert cloud_perms == {"contents": "read", "id-token": "write"}

    # pull_request_target only for labeled events; never checkout PR head
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers["pull_request_target"]["types"] == ["labeled"]
    assert "pull_request.head.sha" not in workflow_text, "never check out PR head in cloud-verify"
    assert "pull_request.base.sha" in workflow_text

    # workflow_dispatch has an explicit check selection and no PR-field deps
    dispatch = triggers["workflow_dispatch"]
    assert dispatch["inputs"]["checks"]["options"] == ["lint-unit", "verify-creds", "all"]
    assert "pull_request" not in yaml.dump(dispatch)

    # both deferred make targets are absent from CI steps (they exit nonzero by design)
    run_commands = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                run_commands.append(step["run"])
    all_runs = "\n".join(run_commands)
    assert "make itest" not in all_runs
    assert "make e2e-local" not in all_runs
