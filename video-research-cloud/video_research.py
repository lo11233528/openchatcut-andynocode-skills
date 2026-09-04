#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
import time
import urllib.parse
from typing import Any, Sequence

MODULE_DIR = pathlib.Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from vr_bilibili import BilibiliResearcher
from vr_core import (
    Bundle,
    RequestConfig,
    ResearchError,
    Runner,
    parse_json3,
    parse_vtt,
    redact_for_logs,
)
from vr_youtube import YouTubeResearcher


def platform_for(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if "bilibili.com" in host or host == "b23.tv":
        return "bilibili"
    if "youtube.com" in host or host == "youtu.be" or "youtube-nocookie.com" in host:
        return "youtube"
    raise ResearchError(f"Unsupported video platform: {host}")


def write_failure_manifest(bundle: Bundle, config: RequestConfig | None, exc: BaseException) -> None:
    existing: dict[str, Any] = {}
    manifest_path = bundle.root / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(
        {
            "status": "failed",
            "job_id": config.job_id if config else None,
            "url": config.url if config else None,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "completed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    bundle.json("manifest.json", existing)
    bundle.text("ERROR.txt", f"{type(exc).__name__}: {exc}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    bundle = Bundle(args.output)
    config: RequestConfig | None = None
    started = time.monotonic()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ResearchError("Request JSON root must be an object.")
        config = RequestConfig.from_json(payload)
        bundle.json("request.json", dataclasses.asdict(config))
        runner = Runner(bundle)
        platform = platform_for(config.url)
        if platform == "bilibili":
            manifest = BilibiliResearcher(config, bundle, runner).run()
        else:
            manifest = YouTubeResearcher(config, bundle, runner).run()
        manifest.update(
            {
                "schema_version": "1.0",
                "started_at_utc": started_at,
                "completed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime_seconds": round(time.monotonic() - started, 3),
                "raw_media_retained": False,
                "completion_gate": "coverage_ratio >= 0.95 and transcript exists",
            }
        )
        bundle.json("manifest.json", manifest)
        return 0 if manifest.get("status") == "complete" else 2
    except BaseException as exc:
        write_failure_manifest(bundle, config, exc)
        print(f"Video research failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # Defense in depth: raw media must never be part of the uploaded artifact.
        for pattern in ("*.mp3", "*.m4a", "*.webm", "*.mp4", "*.flv", "*.m4s", "*.ts"):
            for path in bundle.root.rglob(pattern):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
