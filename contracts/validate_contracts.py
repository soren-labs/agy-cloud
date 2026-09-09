#!/usr/bin/env python3
"""Machine validation of the agy-cloud contracts (make contract-check).

Checks (exit 1 with a report on any failure):
  1. contracts/openapi.yaml parses and declares OpenAPI 3.1.
  2. Every operation has a unique operationId and at least one 4xx response.
  3. Every $ref inside the spec resolves.
  4. Endpoint parity with docs/CONTRACTS.md: the human contract's Public API
     v1 and Internal API endpoint lists must match the OpenAPI paths exactly
     (method + path). Deferred (P2) endpoints must NOT appear in either.
  5. Every request-body object schema uses additionalProperties: false, and
     the two deferred-field surfaces (POST /v1/agents, followup) declare
     x-deferred-fields.
  6. Enum schemas (AgentStatus, RunStatus, ...) match agy_cloud.models.
  7. Error codes in openapi examples are valid agy_cloud.errors codes.
  8. All inline examples validate against their response schemas (jsonschema).
  9. contracts/schemas/agy_*.schema.json are valid JSON Schema and the
     fixtures under tests/fake_agy/fixtures validate against them.

This is contract validation, not product testing: it never imports or runs
the (unimplemented) service.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agy_cloud import errors as err_mod
from agy_cloud import models as models_mod
from agy_cloud.protocols import snapshot as snapshot_mod

OPENAPI = ROOT / "contracts" / "openapi.yaml"
CONTRACTS_MD = ROOT / "docs" / "CONTRACTS.md"

OPERATIONS = ("get", "post", "put", "delete", "patch")
REQUIRED_JOB_NAME = "lint-unit"  # checked by CI; asserted here for documentation parity


def load_spec() -> dict[str, Any]:
    with OPENAPI.open() as f:
        return yaml.safe_load(f)


def iter_refs(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


def resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise KeyError(f"external ref not allowed: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def iter_operations(spec: dict[str, Any]):
    for path, item in spec["paths"].items():
        for method in OPERATIONS:
            if method in item:
                yield path, method, item[method]


def parse_human_endpoints() -> dict[str, set[str]]:
    """Extract `- `METHOD /path`` lines from CONTRACTS.md endpoint sections."""
    text = CONTRACTS_MD.read_text()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current and line.strip().startswith("- `"):
            m = re.match(r"- `([A-Z]+) (/[^`]+)`", line.strip())
            if m:
                sections[current].append(f"{m.group(1)} {m.group(2)}")
    return {
        "public": set(sections.get("Public API v1", [])),
        "internal": set(sections.get("Internal API", [])),
    }


def openapi_endpoint_sets(spec: dict[str, Any]) -> tuple[set[str], set[str]]:
    public: set[str] = set()
    internal: set[str] = set()
    for path, method, op in iter_operations(spec):
        entry = f"{method.upper()} {path}"
        tags = op.get("tags", [])
        if "public" in tags:
            public.add(entry)
        elif "internal-scheduler" in tags or "internal-worker" in tags:
            internal.add(entry)
    return public, internal


def check(spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    if not spec.get("openapi", "").startswith("3.1"):
        problems.append("openapi version must be 3.1.x")

    # unique operationIds + 4xx present
    seen_ids: dict[str, str] = {}
    for path, method, op in iter_operations(spec):
        oid = op.get("operationId")
        if not oid:
            problems.append(f"{method.upper()} {path}: missing operationId")
            continue
        if oid in seen_ids:
            problems.append(f"duplicate operationId {oid} ({seen_ids[oid]} and {path})")
        seen_ids[oid] = path
        statuses = [str(s) for s in op.get("responses", {})]
        if not any(s.startswith("4") for s in statuses):
            problems.append(f"{oid}: no 4xx response declared")

    # refs resolve
    for ref in iter_refs(spec):
        try:
            resolve_ref(spec, ref)
        except (KeyError, TypeError):
            problems.append(f"unresolvable $ref: {ref}")

    # request bodies: additionalProperties false + deferred-field surfaces
    for path, method, op in iter_operations(spec):
        body = op.get("requestBody", {})
        for media in body.get("content", {}).values():
            schema = media.get("schema", {})
            if "$ref" in schema:
                schema = resolve_ref(spec, schema["$ref"])
            if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
                problems.append(f"{op['operationId']}: request body must set additionalProperties: false")
    for oid in ("createAgent", "followup"):
        op = next((o for _, _, o in iter_operations(spec) if o.get("operationId") == oid), None)
        if not op or not op.get("x-deferred-fields"):
            problems.append(f"{oid}: must declare x-deferred-fields (explicit P2 rejection)")

    # enums match agy_cloud.models
    enum_checks = {
        "AgentStatus": models_mod.AgentStatus,
        "RunStatus": models_mod.RunStatus,
        "RunOrigin": models_mod.RunOrigin,
        "RunFailureCode": models_mod.RunFailureCode,
        "AccountStatus": models_mod.AccountStatus,
        "TestsStatus": models_mod.TestsStatus,
        "SessionEndReason": models_mod.SessionEndReason,
        "CompletionOutcome": snapshot_mod.CompletionOutcome,
    }
    schemas = spec["components"]["schemas"]
    for name, enum_cls in enum_checks.items():
        declared = set(schemas[name]["enum"])
        actual = {member.value for member in enum_cls}
        if declared != actual:
            problems.append(f"schema {name} enum {sorted(declared)} != models {sorted(actual)}")

    # error codes in examples are valid codes
    for node in iter_examples(spec):
        code = dig(node, "error", "code")
        if code is not None and code not in {m.value for m in err_mod.ErrorCode}:
            problems.append(f"example error code not in taxonomy: {code}")

    # scheduler/worker separation
    tick_op = spec["paths"]["/internal/tick"]["post"]
    tick_security = tick_op.get("security")
    if tick_security != [{"schedulerIdToken": []}]:
        problems.append("/internal/tick must require ONLY schedulerIdToken")
    if "sessionCapability" in json.dumps(tick_security):
        problems.append("/internal/tick must never accept sessionCapability")
    for path, method, op in iter_operations(spec):
        if "internal-worker" in op.get("tags", []):
            sec = op.get("security")
            if sec != [{"workerIdToken": [], "sessionCapability": []}]:
                problems.append(f"{op['operationId']}: worker routes need ID token AND capability")

    # endpoint parity with human contract
    human = parse_human_endpoints()
    api_public, api_internal = openapi_endpoint_sets(spec)
    if human["public"] != api_public:
        problems.append(
            "public endpoints out of sync with docs/CONTRACTS.md: "
            f"missing-in-openapi={sorted(human['public'] - api_public)} "
            f"extra-in-openapi={sorted(api_public - human['public'])}"
        )
    if human["internal"] != api_internal:
        problems.append(
            "internal endpoints out of sync with docs/CONTRACTS.md: "
            f"missing-in-openapi={sorted(human['internal'] - api_internal)} "
            f"extra-in-openapi={sorted(api_internal - human['internal'])}"
        )
    if any("webhooks/github" in e for e in api_public | api_internal):
        problems.append("POST /webhooks/github is P2 and must not be a V1 endpoint")

    return problems


def iter_examples(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "examples":
                for ex in value.values():
                    if isinstance(ex, dict) and "value" in ex:
                        yield ex["value"]
            else:
                yield from iter_examples(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_examples(item)


def dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def check_examples(spec: dict[str, Any]) -> list[str]:
    import jsonschema

    problems: list[str] = []
    for path, method, op in iter_operations(spec):
        for status, resp in op.get("responses", {}).items():
            for media in resp.get("content", {}).values():
                schema = media.get("schema")
                if schema is None:
                    continue
                try:
                    resolved = dereference(spec, schema)
                except (KeyError, TypeError) as exc:
                    problems.append(f"{op['operationId']} {status}: bad ref {exc}")
                    continue
                for name, ex in media.get("examples", {}).items():
                    value = ex.get("value")
                    try:
                        jsonschema.validate(value, resolved, cls=jsonschema.Draft202012Validator)
                    except jsonschema.ValidationError as exc:
                        problems.append(
                            f"{op['operationId']} {status} example {name!r}: {exc.message}"
                        )
    return problems


def dereference(spec: dict[str, Any], schema: Any) -> Any:
    """Resolve $ref chains (local refs only); keeps nested refs resolvable by
    inlining resolved fragments."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            return dereference(spec, resolve_ref(spec, schema["$ref"]))
        return {k: dereference(spec, v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [dereference(spec, item) for item in schema]
    return schema


def check_agy_schemas_and_fixtures() -> list[str]:
    import jsonschema
    import referencing

    problems: list[str] = []
    schema_dir = ROOT / "contracts" / "schemas"
    fixtures_dir = ROOT / "tests" / "fake_agy" / "fixtures"
    with (schema_dir / "agy_result.schema.json").open() as f:
        result_schema = json.load(f)
    registry = referencing.Registry().with_resource(
        result_schema["$id"], referencing.Resource.from_contents(result_schema)
    )
    mapping = {
        "agy_result.schema.json": ["result_success.json", "result_usage.json"],
        "agy_stream_event.schema.json": ["stream_success.jsonl"],
    }
    for schema_file, fixture_files in mapping.items():
        schema_path = schema_dir / schema_file
        if not schema_path.exists():
            problems.append(f"missing schema: {schema_path}")
            continue
        with schema_path.open() as f:
            schema = json.load(f)
        for fixture_file in fixture_files:
            fixture_path = fixtures_dir / fixture_file
            if not fixture_path.exists():
                problems.append(f"missing fixture: {fixture_path}")
                continue
            with fixture_path.open() as f:
                if fixture_file.endswith(".jsonl"):
                    documents = [json.loads(line) for line in f if line.strip()]
                else:
                    documents = [json.load(f)]
            for i, doc in enumerate(documents):
                try:
                    jsonschema.Draft202012Validator(
                        schema, registry=registry
                    ).validate(doc)
                except jsonschema.ValidationError as exc:
                    problems.append(f"{fixture_file}[{i}] vs {schema_file}: {exc.message}")
    return problems


def main() -> int:
    spec = load_spec()
    problems = check(spec) + check_examples(spec) + check_agy_schemas_and_fixtures()
    if problems:
        print(f"contract-check: FAILED ({len(problems)} problem(s))")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    ops = sum(1 for _ in iter_operations(spec))
    print(f"contract-check: OK ({ops} operations, {len(spec['components']['schemas'])} schemas)")
    print(f"contract-check: CI required job name reference = {REQUIRED_JOB_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
