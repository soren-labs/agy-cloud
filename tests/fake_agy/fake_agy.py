#!/usr/bin/env python3
"""Deterministic agy stand-in for local/CI tests; never reads cloud credentials."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--print", dest="prompt", default="")
    p.add_argument("--output-format", choices=["json", "stream-json"], default="json")
    p.add_argument("--model", default="fake-model")
    p.add_argument("--conversation")
    p.add_argument("--add-dir", default=os.getcwd())
    p.add_argument("--json-schema")
    p.add_argument("--print-timeout")
    p.add_argument("--dangerously-skip-permissions", action="store_true")
    args, _ = p.parse_known_args()
    conv = args.conversation or str(uuid.uuid4())
    prompt = args.prompt
    if "FAKE:fail" in prompt:
        print("FAKE_FAILURE", file=sys.stderr)
        return 17
    if "FAKE:quota" in prompt:
        print("RESOURCE_EXHAUSTED", file=sys.stderr)
        return 29
    if "FAKE:sleep" in prompt:
        try:
            time.sleep(min(int(prompt.split("FAKE:sleep", 1)[1].split()[0]), 3))
        except (IndexError, ValueError):
            pass
    if "FAKE:write" in prompt:
        parts = prompt.split("FAKE:write", 1)[1].strip().split(maxsplit=1)
        if len(parts) == 2:
            target, content = parts
            path = (Path(args.add_dir) / target).resolve()
            root = Path(args.add_dir).resolve()
            if root not in path.parents and path != root:
                print("fake_agy: write outside workspace", file=sys.stderr)
                return 2
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    result = {
        "conversation_id": conv,
        "status": "SUCCESS",
        "response": "FAKE_OK\n",
        "duration_seconds": 0,
        "num_turns": 1,
        "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0},
    }
    if args.json_schema:
        result["structured"] = {"verdict": "approve", "findings": []}
    if args.output_format == "stream-json":
        print(json.dumps({"event": "init", "conversation_id": conv, "init": {"model": args.model}}))
        print(json.dumps({"event": "step_update", "step_update": {"step_index": 0, "state": "DONE"}}))
        print(json.dumps({"event": "result", "result": result}))
    else:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
