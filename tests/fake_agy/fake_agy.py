#!/usr/bin/env python3
"""Deterministic agy stand-in for local/CI tests.

FAKE CONVENTIONS — not real-agy claims. The shapes that ARE grounded in P0
evidence (docs/P0-INVENTORY.md, DESIGN §1.1) are exactly the JSON result
fields and the nested stream events; they are pinned by
contracts/schemas/agy_*.schema.json and tests/fake_agy/fixtures/. Real agy
failure exit codes are NOT documented by P0; the codes below are test
conventions for the runner's fault handling.

Headless flags mirror the real CLI (accepted for argv compatibility):
  --print=<prompt>            (real agy requires the '=' form)
  --output-format json|stream-json
  --model, --conversation, --add-dir, --print-timeout, --json-schema,
  --dangerously-skip-permissions
Unknown flags are ignored. --json-schema is accepted but structured output is
deferred to P2 (field name unverified); a stderr note is emitted instead.

Determinism:
  FAKE_AGY_CONVERSATION_ID   fixed id for NEW conversations
                              (default 00000000-0000-4000-8000-000000000001)
  FAKE_AGY_DURATION          duration_seconds value (default 0.0)

Local state: <add-dir>/.fake-agy-state.json counts turns per conversation,
so --conversation followups increment num_turns like a restored real session.

Fault injection via prompt directives:
  FAKE:ok                          success (default)
  FAKE:fail                        exit 17, stderr FAKE_FAILURE
  FAKE:quota                       exit 29, stderr RESOURCE_EXHAUSTED (quota
                                   classifier pattern from DESIGN §5)
  FAKE:exit <code>                 arbitrary exit code
  FAKE:sleep <seconds>             sleep up to --print-timeout; exceeding it
                                   exits 124 (fake timeout convention)
  FAKE:malformed                   invalid JSON on stdout, exit 0
  FAKE:truncated                   valid prefix, EOF mid-document
  FAKE:noresult                    json: {} / stream: result event with null
  FAKE:response <text>             custom response text
  FAKE:tokens <input> <output>     deterministic usage numbers
  FAKE:write <relpath> <content>   write inside --add-dir (escape rejected, exit 2)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

DEFAULT_CONVERSATION_ID = "00000000-0000-4000-8000-000000000001"
STATE_FILE = ".fake-agy-state.json"
# DESIGN §5 quota classifier: /quota|RESOURCE_EXHAUSTED|429|rate limit/i
QUOTA_STDERR = "RESOURCE_EXHAUSTED: fake quota injected"
TIMEOUT_EXIT = 124  # fake convention; mirrors GNU timeout for readability


def parse_duration(value: str | None) -> float | None:
    """Parse agy --print-timeout durations like 30m, 45s, 1h."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h)", value.strip())
    if not m:
        return None
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def load_state(add_dir: Path) -> dict:
    path = add_dir / STATE_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
    return {}


def save_state(add_dir: Path, state: dict) -> None:
    (add_dir / STATE_FILE).write_text(json.dumps(state))


def build_result(args, conversation_id: str, num_turns: int, response: str, usage: dict) -> dict:
    return {
        "conversation_id": conversation_id,
        "status": "SUCCESS",
        "response": response,
        "duration_seconds": float(os.environ.get("FAKE_AGY_DURATION", "0.0")),
        "num_turns": num_turns,
        "usage": usage,
    }


def emit(output_format: str, result: dict, model: str, cwd: str) -> None:
    if output_format == "stream-json":
        print(
            json.dumps(
                {
                    "event": "init",
                    "init": {
                        "model": model,
                        "cwd": cwd,
                        "tools": ["run_command", "write_file", "list_directory"],
                        "permission_mode": "always-proceed",
                    },
                }
            )
        )
        print(
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {"step_index": 0, "state": "DONE", "step_type": "tool_use"},
                }
            )
        )
        print(json.dumps({"event": "result", "result": result}))
    else:
        print(json.dumps(result))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--print", dest="prompt", default="")
    parser.add_argument("--output-format", choices=["json", "stream-json"], default="json")
    parser.add_argument("--model", default="fake-model")
    parser.add_argument("--conversation")
    parser.add_argument("--add-dir", default=os.getcwd())
    parser.add_argument("--json-schema")
    parser.add_argument("--print-timeout")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    args, _unknown = parser.parse_known_args()

    add_dir = Path(args.add_dir).resolve()
    prompt = args.prompt

    if args.json_schema:
        print(
            "fake_agy: --json-schema accepted but structured output is deferred to P2 "
            "(field name not P0-verified)",
            file=sys.stderr,
        )

    # --- immediate fault exits ---
    if "FAKE:fail" in prompt:
        print("FAKE_FAILURE", file=sys.stderr)
        return 17
    if "FAKE:quota" in prompt:
        print(QUOTA_STDERR, file=sys.stderr)
        return 29
    m = re.search(r"FAKE:exit\s+(\d+)", prompt)
    if m:
        print(f"FAKE_EXIT:{m.group(1)}", file=sys.stderr)
        return int(m.group(1))

    # --- controllable timeout ---
    m = re.search(r"FAKE:sleep\s+(\d+(?:\.\d+)?)", prompt)
    if m:
        requested = float(m.group(1))
        timeout = parse_duration(args.print_timeout)
        if timeout is not None and requested >= timeout:
            time.sleep(min(requested, timeout))
            print(f"fake_agy: print timeout exceeded after {timeout}s (fake convention)", file=sys.stderr)
            return TIMEOUT_EXIT
        time.sleep(requested)

    # --- workspace write (escape rejected) ---
    m = re.search(r"FAKE:write\s+(\S+)\s+(.+)", prompt)
    if m:
        target, content = m.group(1), m.group(2).rstrip("\\n")
        path = (add_dir / target).resolve()
        if path != add_dir and add_dir not in path.parents:
            print("fake_agy: write outside workspace", file=sys.stderr)
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    # --- conversation state: followup continues the same conversation ---
    state = load_state(add_dir)
    if args.conversation:
        conversation_id = args.conversation
        turns = state.get("conversations", {}).get(conversation_id, 0)
    else:
        conversation_id = os.environ.get("FAKE_AGY_CONVERSATION_ID") or DEFAULT_CONVERSATION_ID
        if os.environ.get("FAKE_AGY_RANDOM_ID") == "1":
            conversation_id = str(uuid.uuid4())
        turns = state.get("conversations", {}).get(conversation_id, 0)
    num_turns = turns + 1
    conversations = state.get("conversations", {})
    conversations[conversation_id] = num_turns
    save_state(add_dir, {"conversations": conversations})

    # --- response / usage ---
    response = "FAKE_OK\n"
    m = re.search(r"FAKE:response\s+(.+?)(?=\s+FAKE:|$)", prompt)
    if m:
        response = m.group(1).rstrip("\\n") + "\n"
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    }
    m = re.search(r"FAKE:tokens\s+(\d+)\s+(\d+)", prompt)
    if m:
        usage["input_tokens"] = int(m.group(1))
        usage["output_tokens"] = int(m.group(2))
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    result = build_result(args, conversation_id, num_turns, response, usage)

    # --- output-shape faults ---
    if "FAKE:malformed" in prompt:
        if args.output_format == "stream-json":
            print('{"event": "init", "init": {')
        else:
            print('{"status": "SUCCESS", "conversation_id": ')
        return 0
    if "FAKE:truncated" in prompt:
        if args.output_format == "stream-json":
            print(json.dumps({"event": "init", "init": {"model": args.model, "cwd": str(add_dir)}}))
            print(
                json.dumps(
                    {"event": "step_update", "step_update": {"step_index": 0}}
                )
            )
        else:
            print(json.dumps(result)[:-12], end="")
        return 0
    if "FAKE:noresult" in prompt:
        if args.output_format == "stream-json":
            print(
                json.dumps(
                    {"event": "init", "init": {"model": args.model, "cwd": str(add_dir)}}
                )
            )
            print(json.dumps({"event": "result", "result": None}))
        else:
            print("{}")
        return 0

    emit(args.output_format, result, args.model, str(add_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
