"""API error taxonomy: stable `error.code` values and their HTTP statuses.

OpenAPI alignment: contracts/openapi.yaml error responses must use exactly
these codes with these statuses; tests/test_contract_alignment.py enforces it.
"""
from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    # auth
    UNAUTHENTICATED = "unauthenticated"  # 401: missing/invalid API key or ID token
    INVALID_SIGNATURE = "invalid_signature"  # 401: webhook HMAC invalid (P2 inbound; reserved)
    RATE_LIMITED = "rate_limited"  # 429
    # request validation
    INVALID_REQUEST = "invalid_request"  # 400: malformed body/query
    UNSUPPORTED_FIELD = "unsupported_field"  # 422: known-but-deferred field or value (P2 scope)
    # resources
    NOT_FOUND = "not_found"  # 404
    # state conflicts
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"  # 409: same key, different payload
    INVALID_STATE = "invalid_state"  # 409: e.g. followup on ARCHIVED/EXPIRED agent
    AGENT_ACTIVE = "agent_active"  # 409: archive requires stop first
    GENERATION_MISMATCH = "generation_mismatch"  # 409: late callback from an older generation
    LEASE_NOT_HELD = "lease_not_held"  # 409: session lost the agent lease (release race)
    TOKEN_VERSION_CONFLICT = "token_version_conflict"  # 409: account token CAS failed
    # internal authz
    FORBIDDEN_SESSION = "forbidden_session"  # 403: capability not bound to this run/session
    FORBIDDEN_SCHEDULER = "forbidden_scheduler"  # 403: worker identity used on /internal/tick
    FORBIDDEN_WORKER = "forbidden_worker"  # 403: scheduler/other identity used on worker routes
    # server
    INTERNAL = "internal"  # 500


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.INVALID_SIGNATURE: 401,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNSUPPORTED_FIELD: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.AGENT_ACTIVE: 409,
    ErrorCode.GENERATION_MISMATCH: 409,
    ErrorCode.LEASE_NOT_HELD: 409,
    ErrorCode.TOKEN_VERSION_CONFLICT: 409,
    ErrorCode.FORBIDDEN_SESSION: 403,
    ErrorCode.FORBIDDEN_SCHEDULER: 403,
    ErrorCode.FORBIDDEN_WORKER: 403,
    ErrorCode.INTERNAL: 500,
}


def http_status(code: ErrorCode) -> int:
    return ERROR_HTTP_STATUS[code]
