#!/usr/bin/env python3
"""Normalize a GitHub event into one strict Video Research request JSON."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", stripped):
        try:
            value, _ = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Issue body does not contain a valid JSON object.")


def request_from_event(event: dict[str, Any], event_name: str) -> dict[str, Any]:
    run_id = os.environ.get("GITHUB_RUN_ID", "run")
    if event_name == "issues":
        issue = event.get("issue") or {}
        title = str(issue.get("title") or "")
        if not title.lower().startswith("[video-research]"):
            raise ValueError("Issue title must start with [video-research].")
        request = extract_json_object(str(issue.get("body") or ""))
        request.setdefault("job_id", f"issue-{issue.get('number', 'unknown')}-{run_id}")
        return request
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs") or {}
        request: dict[str, Any] = {
            "url": inputs.get("url"),
            "job_id": inputs.get("job_id") or f"dispatch-{run_id}",
            "language": inputs.get("language") or "auto",
            "include_frames": str(inputs.get("include_frames", "true")).lower() == "true",
            "max_frames": int(inputs.get("max_frames") or 12),
            "all_parts": str(inputs.get("all_parts", "false")).lower() == "true",
            "transcription_backend": inputs.get("transcription_backend") or "auto",
        }
        if inputs.get("part"):
            request["part"] = int(inputs["part"])
        return request
    if event_name == "push":
        before = str(event.get("before") or "")
        after = str(event.get("after") or os.environ.get("GITHUB_SHA") or "")
        changed: list[str] = []
        if before and set(before) != {"0"}:
            proc = subprocess.run(
                ["git", "diff", "--name-only", before, after],
                capture_output=True,
                text=True,
                check=False,
            )
            changed = proc.stdout.splitlines()
        if not changed:
            proc = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", after],
                capture_output=True,
                text=True,
                check=False,
            )
            changed = proc.stdout.splitlines()
        candidates = [
            pathlib.Path(path)
            for path in changed
            if path.startswith("video-research-cloud/test-requests/") and path.endswith(".json")
        ]
        if not candidates:
            raise ValueError("Push test did not include a test request JSON file.")
        request = json.loads(candidates[-1].read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Test request root must be a JSON object.")
        request.setdefault("job_id", f"push-{run_id}")
        return request
    raise ValueError(f"Unsupported GitHub event: {event_name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event_path.read_text(encoding="utf-8"))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    request = request_from_event(event, event_name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    safe_job = re.sub(r"[^0-9A-Za-z._-]+", "-", str(request.get("job_id") or "video-research"))[:96]
    with pathlib.Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        handle.write(f"job_id={safe_job}\n")
        handle.write(f"artifact_name=video-research-{safe_job}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Request parsing failed: {exc}", file=sys.stderr)
        raise
