"""Machine alignment between openapi.yaml, agy_cloud, docs/CONTRACTS.md, and CI.

These are structural/semantic checks, not "file contains word" checks: every
assertion compares two independently-authored sources that must agree.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import types
import typing
from dataclasses import fields
from enum import StrEnum
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from agy_cloud import constants, models
from agy_cloud import errors as err
from agy_cloud.protocols import snapshot as snapshot_proto
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


# --- typed-model vs published-schema parity (Agent, Run, OutboundWebhookEvent) ---

def _resolve_node(spec, node):
    while isinstance(node, dict) and "$ref" in node:
        node = validate_contracts.resolve_ref(spec, node["$ref"])
    return node


def _unwrap_nullable(prop):
    """oneOf: [null, X] -> X (a nullable property's actual schema)."""
    if isinstance(prop, dict) and "oneOf" in prop:
        non_null = [v for v in prop["oneOf"] if not (isinstance(v, dict) and v.get("type") == "null")]
        if len(non_null) == 1:
            return non_null[0]
    return prop


def _unwrap_optional(annotation):
    """X | None -> X."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def assert_model_parity(spec, dc, schema_node, path, *, strict_required):
    """Recursively pin a typed model to its published (closed) schema.

    strict_required=True (top-level objects): schema-required fields are
    exactly the dataclass fields without defaults. Nested objects use the
    one-directional rule (required fields must not carry defaults) because
    delivery payloads (e.g. the webhook source) may omit inner required sets.
    """
    schema = _resolve_node(spec, schema_node)
    assert schema.get("type") == "object", f"{path}: expected object schema"
    assert schema.get("additionalProperties") is False, f"{path}: published schema must be closed"
    hints = typing.get_type_hints(dc)
    dc_fields = {f.name: f for f in dataclasses.fields(dc)}
    props = schema["properties"]
    assert set(dc_fields) == set(props), (
        f"{path} drift: dataclass-only={sorted(set(dc_fields) - set(props))} "
        f"schema-only={sorted(set(props) - set(dc_fields))}"
    )
    required = set(schema.get("required", []))
    for name, f in dc_fields.items():
        has_default = f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
        if strict_required:
            assert (name in required) is not has_default, (
                f"{path}.{name}: schema-required vs dataclass-default drift"
            )
        else:
            assert not (name in required and has_default), (
                f"{path}.{name}: schema-required field must not default"
            )
        annotation = _unwrap_optional(hints[name])
        prop = _resolve_node(spec, _unwrap_nullable(props[name]))
        if dataclasses.is_dataclass(annotation):
            assert_model_parity(spec, annotation, prop, f"{path}.{name}", strict_required=False)
        elif isinstance(annotation, type) and issubclass(annotation, StrEnum):
            declared = set(prop["enum"])
            actual = {member.value for member in annotation}
            assert declared == actual, f"{path}.{name}: enum drift {sorted(declared)} != {sorted(actual)}"


def test_agent_typed_model_matches_openapi_schema():
    spec = validate_contracts.load_spec()
    assert_model_parity(
        spec, models.Agent, spec["components"]["schemas"]["Agent"], "Agent", strict_required=True
    )


def test_run_typed_model_matches_openapi_schema():
    spec = validate_contracts.load_spec()
    assert_model_parity(
        spec, models.Run, spec["components"]["schemas"]["Run"], "Run", strict_required=True
    )


def test_outbound_webhook_event_matches_openapi_schema():
    spec = validate_contracts.load_spec()
    assert_model_parity(
        spec,
        models.OutboundWebhookEvent,
        spec["components"]["schemas"]["OutboundWebhookEvent"],
        "OutboundWebhookEvent",
        strict_required=True,
    )


def test_completion_outcome_enum_matches_openapi():
    spec = validate_contracts.load_spec()
    declared = set(spec["components"]["schemas"]["CompletionOutcome"]["enum"])
    assert declared == {member.value for member in snapshot_proto.CompletionOutcome}


def test_internal_identity_error_codes_match_machine_contract():
    spec = validate_contracts.load_spec()
    # worker identity on scheduler-only tick -> forbidden_worker (machine example)
    tick_example = (
        spec["paths"]["/internal/tick"]["post"]["responses"]["403"]
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
        .get("forbidden_worker", {})
        .get("value", {})
        .get("error", {})
    )
    assert tick_example.get("code") == err.ErrorCode.FORBIDDEN_WORKER.value
    assert "scheduler identity required" in tick_example.get("message", "")
    # scheduler identity on a worker route -> forbidden_scheduler (documented scope)
    worker_scheme = spec["components"]["securitySchemes"]["workerIdToken"]["description"]
    assert "forbidden_scheduler" in worker_scheme
    # both codes are 403 in the taxonomy shared with openapi.yaml
    assert err.ERROR_HTTP_STATUS[err.ErrorCode.FORBIDDEN_SCHEDULER] == 403
    assert err.ERROR_HTTP_STATUS[err.ErrorCode.FORBIDDEN_WORKER] == 403


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
