#!/usr/bin/env python3
"""Shared models and media helpers for cloud video research."""
from __future__ import annotations

import dataclasses
import html
import json
import pathlib
import re
import subprocess
import time
import urllib.parse
from typing import Any, Iterable, Sequence

import requests

ALLOWED_HOSTS = {
    "www.bilibili.com",
    "bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "www.youtube.com",
    "youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
    "youtube-nocookie.com",
}
BVID_RE = re.compile(r"BV[0-9A-Za-z]+")
YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{6,20}$")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
WBI_MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class ResearchError(RuntimeError):
    """Expected, user-facing extraction failure."""


@dataclasses.dataclass(slots=True)
class RequestConfig:
    url: str
    job_id: str
    language: str = "auto"
    include_frames: bool = True
    max_frames: int = 12
    part: int | None = None
    all_parts: bool = False
    transcription_backend: str = "auto"

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RequestConfig":
        raw_url = str(payload.get("url") or "").strip()
        if not raw_url:
            raise ResearchError("Request is missing a non-empty 'url'.")
        parsed = urllib.parse.urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise ResearchError(f"Unsupported or non-public video host: {host or raw_url}")

        requested_id = str(payload.get("job_id") or "").strip()
        if not requested_id:
            requested_id = f"video-{int(time.time())}"
        job_id = re.sub(r"[^0-9A-Za-z._-]+", "-", requested_id).strip("-._")[:96]
        if not job_id:
            job_id = f"video-{int(time.time())}"

        language = str(payload.get("language") or "auto").strip() or "auto"
        raw_include_frames = payload.get("include_frames", True)
        if isinstance(raw_include_frames, str):
            include_frames = raw_include_frames.strip().lower() not in {"0", "false", "no", "off"}
        else:
            include_frames = bool(raw_include_frames)
        try:
            max_frames = int(payload.get("max_frames", 12))
        except (TypeError, ValueError):
            max_frames = 12
        max_frames = max(0, min(max_frames, 20))
        part_raw = payload.get("part")
        part: int | None
        if part_raw in (None, "", "auto"):
            part = None
        else:
            try:
                part = max(1, int(part_raw))
            except (TypeError, ValueError) as exc:
                raise ResearchError("'part' must be a positive integer or omitted.") from exc
        raw_all_parts = payload.get("all_parts", False)
        if isinstance(raw_all_parts, str):
            all_parts = raw_all_parts.strip().lower() in {"1", "true", "yes", "on"}
        else:
            all_parts = bool(raw_all_parts)
        backend = str(payload.get("transcription_backend") or "auto").lower().strip()
        if backend not in {"auto", "assemblyai", "whisper"}:
            raise ResearchError("transcription_backend must be auto, assemblyai, or whisper.")
        return cls(
            url=raw_url,
            job_id=job_id,
            language=language,
            include_frames=include_frames,
            max_frames=max_frames,
            part=part,
            all_parts=all_parts,
            transcription_backend=backend,
        )


@dataclasses.dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str
    part: int = 1
    speaker: str | None = None

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "start": round(max(0.0, self.start), 3),
            "end": round(max(self.start, self.end), 3),
            "text": self.text,
            "part": self.part,
        }
        if self.speaker:
            row["speaker"] = self.speaker
        return row


@dataclasses.dataclass(slots=True)
class MediaCandidate:
    url: str
    kind: str
    label: str
    bandwidth: int = 0
    codecs: str | None = None


@dataclasses.dataclass(slots=True)
class PartResult:
    part: int
    cid: int | str
    title: str
    expected_duration_seconds: float
    processed_media_seconds: float
    transcript_source: str
    transcript_language: str
    segments: list[Segment]
    frame_entries: list[dict[str, Any]]
    warnings: list[str]


class Bundle:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs = self.root / "logs"
        self.logs.mkdir(exist_ok=True)

    def json(self, relative: str, value: Any) -> pathlib.Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def text(self, relative: str, value: str) -> pathlib.Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def log_http(self, name: str, response: requests.Response) -> None:
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", name)[:120]
        meta = {
            "status": response.status_code,
            "url": response.url,
            "headers": dict(response.headers),
            "content_length": len(response.content),
            "text_head": response.text[:1000],
        }
        self.json(f"logs/{safe}.json", meta)


class Runner:
    def __init__(self, bundle: Bundle):
        self.bundle = bundle

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        check: bool = True,
        log_name: str | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if log_name:
            self.bundle.text(
                f"logs/{log_name}.log",
                f"$ {' '.join(args)}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}\n",
            )
        if check and proc.returncode != 0:
            raise ResearchError(
                f"Command failed ({proc.returncode}): {' '.join(args[:4])}. "
                f"See logs/{log_name or 'command'}.log"
            )
        return proc

    def ffprobe_duration(self, path: pathlib.Path) -> float:
        proc = self.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=False,
            log_name=f"ffprobe-{path.name}",
            timeout=60,
        )
        try:
            value = float(proc.stdout.strip())
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, value)


def hhmmss(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def dedupe_segments(segments: Iterable[Segment]) -> list[Segment]:
    result: list[Segment] = []
    previous = ""
    for seg in sorted(segments, key=lambda x: (x.part, x.start, x.end)):
        text = clean_text(seg.text)
        if not text:
            continue
        normalized = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()
        if normalized and normalized == previous:
            continue
        previous = normalized
        result.append(
            Segment(
                start=max(0.0, float(seg.start)),
                end=max(float(seg.start), float(seg.end)),
                text=text,
                part=seg.part,
                speaker=seg.speaker,
            )
        )
    return result


def segments_to_outputs(bundle: Bundle, segments: list[Segment], multi_part: bool) -> None:
    bundle.json("transcript.json", [s.as_dict() for s in segments])
    plain_lines: list[str] = []
    timed_lines: list[str] = []
    last_part: int | None = None
    for seg in segments:
        if multi_part and seg.part != last_part:
            timed_lines.append(f"\n## Part {seg.part}\n")
            plain_lines.append(f"\n[Part {seg.part}]\n")
            last_part = seg.part
        prefix = f"P{seg.part} " if multi_part else ""
        speaker = f"{seg.speaker}: " if seg.speaker else ""
        timed_lines.append(f"[{prefix}{hhmmss(seg.start)}] {speaker}{seg.text}")
        plain_lines.append(f"{speaker}{seg.text}")
    bundle.text("transcript.txt", "\n".join(plain_lines).strip() + "\n")
    bundle.text("transcript_timed.md", "\n".join(timed_lines).strip() + "\n")


def parse_json3(path: pathlib.Path, part: int = 1) -> list[Segment]:
    obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    rows: list[Segment] = []
    for event in obj.get("events") or []:
        text = clean_text("".join(str(seg.get("utf8") or "") for seg in event.get("segs") or []))
        if not text:
            continue
        start = float(event.get("tStartMs") or 0) / 1000.0
        duration = float(event.get("dDurationMs") or 0) / 1000.0
        rows.append(Segment(start=start, end=start + max(duration, 0.05), text=text, part=part))
    return dedupe_segments(rows)


def parse_vtt(path: pathlib.Path, part: int = 1) -> list[Segment]:
    text = path.read_text(encoding="utf-8", errors="replace")
    timestamp_re = re.compile(
        r"(?P<a>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
        r"(?P<b>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
    )

    def seconds(value: str) -> float:
        bits = value.replace(",", ".").split(":")
        if len(bits) == 3:
            h, m, s = bits
        else:
            h, m, s = "0", bits[0], bits[1]
        return int(h) * 3600 + int(m) * 60 + float(s)

    rows: list[Segment] = []
    block: list[str] = []
    for line in text.splitlines() + [""]:
        if line.strip():
            block.append(line)
            continue
        if not block:
            continue
        index = next((i for i, item in enumerate(block) if "-->" in item), None)
        if index is not None:
            match = timestamp_re.search(block[index])
            if match:
                body = clean_text(" ".join(block[index + 1 :]))
                if body:
                    rows.append(
                        Segment(
                            start=seconds(match.group("a")),
                            end=seconds(match.group("b")),
                            text=body,
                            part=part,
                        )
                    )
        block = []
    return dedupe_segments(rows)


def media_duration_ok(actual: float, expected: float) -> bool:
    if actual <= 0:
        return False
    if expected <= 0:
        return actual >= 10
    return actual >= max(10.0, expected * 0.95) or abs(actual - expected) <= 20.0


def download_media(
    session: requests.Session,
    candidates: list[MediaCandidate],
    destination_dir: pathlib.Path,
    runner: Runner,
    expected_duration: float,
    purpose: str,
    warnings: list[str],
) -> tuple[pathlib.Path, float, MediaCandidate] | None:
    if not candidates:
        return None
    unique: dict[str, MediaCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.url, candidate)
    ranked = list(unique.values())
    ranked.sort(key=lambda item: (item.bandwidth <= 0, item.bandwidth or 10**18, item.label))
    attempts: list[dict[str, Any]] = []
    for index, candidate in enumerate(ranked[:16]):
        suffix = ".m4s" if candidate.kind in {"audio", "video"} else ".flv"
        path = destination_dir / f"{purpose}-{index:02d}{suffix}"
        try:
            with session.get(candidate.url, stream=True, timeout=(30, 240)) as response:
                response.raise_for_status()
                length = int(response.headers.get("content-length") or 0)
                if length > 1_500_000_000:
                    raise ResearchError(f"Refusing unexpectedly large media object ({length} bytes).")
                received = 0
                with path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > 1_500_000_000:
                            raise ResearchError("Media object exceeded 1.5 GB safety limit.")
                        handle.write(chunk)
            actual = runner.ffprobe_duration(path)
            attempts.append(
                {
                    "label": candidate.label,
                    "kind": candidate.kind,
                    "bandwidth": candidate.bandwidth,
                    "bytes": path.stat().st_size,
                    "duration": actual,
                    "accepted": media_duration_ok(actual, expected_duration),
                }
            )
            if media_duration_ok(actual, expected_duration):
                runner.bundle.json(f"logs/{purpose}-download-attempts.json", attempts)
                return path, actual, candidate
            path.unlink(missing_ok=True)
        except Exception as exc:
            attempts.append(
                {
                    "label": candidate.label,
                    "kind": candidate.kind,
                    "bandwidth": candidate.bandwidth,
                    "error": repr(exc),
                    "accepted": False,
                }
            )
            path.unlink(missing_ok=True)
    runner.bundle.json(f"logs/{purpose}-download-attempts.json", attempts)
    warnings.append(f"No {purpose} candidate passed the duration/completeness check.")
    return None


def extract_audio(source: pathlib.Path, destination: pathlib.Path, runner: Runner) -> float:
    runner.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "48k",
            str(destination),
        ],
        log_name=f"extract-audio-{destination.parent.name}",
        timeout=900,
    )
    duration = runner.ffprobe_duration(destination)
    if duration <= 0:
        raise ResearchError("FFmpeg created an audio file with no readable duration.")
    return duration


def extract_frames(
    source: pathlib.Path,
    duration: float,
    destination: pathlib.Path,
    count: int,
    runner: Runner,
    part: int,
) -> list[dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True)
    if count <= 0 or duration <= 0:
        return []
    if count == 1:
        times = [duration / 2]
    else:
        start = min(10.0, duration * 0.04)
        end = max(start, duration - min(10.0, duration * 0.04))
        step = (end - start) / max(1, count - 1)
        times = [start + step * i for i in range(count)]
    entries: list[dict[str, Any]] = []
    for index, timestamp in enumerate(times, start=1):
        name = f"p{part:03d}-{index:03d}-{int(timestamp):06d}.jpg"
        path = destination / name
        proc = runner.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-q:v",
                "3",
                str(path),
            ],
            check=False,
            log_name=f"frame-p{part}-{index}",
            timeout=120,
        )
        if proc.returncode == 0 and path.exists() and path.stat().st_size > 1000:
            entries.append(
                {
                    "part": part,
                    "timestamp_seconds": round(timestamp, 3),
                    "timestamp": hhmmss(timestamp),
                    "path": str(path.relative_to(runner.bundle.root)),
                }
            )
        else:
            path.unlink(missing_ok=True)
    return entries
