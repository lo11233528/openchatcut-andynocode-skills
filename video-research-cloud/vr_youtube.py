from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from typing import Any, Sequence

from vr_core import (
    YOUTUBE_ID_RE,
    Bundle,
    RequestConfig,
    ResearchError,
    Runner,
    Segment,
    dedupe_segments,
    extract_audio,
    extract_frames,
    media_duration_ok,
    parse_json3,
    parse_vtt,
    segments_to_outputs,
)
from vr_transcription import transcribe_audio


class YouTubeResearcher:
    def __init__(self, config: RequestConfig, bundle: Bundle, runner: Runner):
        self.config = config
        self.bundle = bundle
        self.runner = runner
        self.warnings: list[str] = []

    @staticmethod
    def _video_id(url: str) -> str | None:
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "youtu.be":
            candidate = parsed.path.strip("/").split("/")[0]
            return candidate if YOUTUBE_ID_RE.match(candidate) else None
        query = urllib.parse.parse_qs(parsed.query)
        candidate = (query.get("v") or [None])[0]
        if candidate and YOUTUBE_ID_RE.match(candidate):
            return candidate
        match = re.search(r"/(?:embed|shorts)/([0-9A-Za-z_-]{6,20})", parsed.path)
        return match.group(1) if match else None

    def run_yt_dlp(
        self,
        args: Sequence[str],
        work: pathlib.Path,
        log: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            ["yt-dlp", *args],
            cwd=work,
            check=check,
            log_name=log,
            timeout=1800,
        )

    def select_caption(
        self,
        work: pathlib.Path,
        video_id: str,
    ) -> tuple[list[Segment], str, str] | None:
        candidates = list(work.glob(f"{video_id}*.json3")) + list(work.glob(f"{video_id}*.vtt"))
        if not candidates:
            return None
        requested = self.config.language.lower().replace("_", "-")

        def score(path: pathlib.Path) -> tuple[int, int]:
            name = path.name.lower()
            language_score = 0
            if requested not in {"", "auto"} and requested.split("-")[0] in name:
                language_score = 50
            elif ".zh" in name:
                language_score = 40
            elif ".en" in name:
                language_score = 30
            return (language_score, path.stat().st_size)

        for path in sorted(candidates, key=score, reverse=True):
            try:
                rows = parse_json3(path) if path.suffix == ".json3" else parse_vtt(path)
            except Exception as exc:
                self.warnings.append(f"Could not parse caption file {path.name}: {exc}")
                continue
            if rows:
                language_match = re.search(rf"{re.escape(video_id)}\.([^.]+)", path.name)
                language = language_match.group(1) if language_match else "unknown"
                auto = any(token in path.name.lower() for token in ("auto", "orig"))
                source = "youtube-auto-caption" if auto else "youtube-caption"
                shutil.copy2(path, self.bundle.root / f"source-caption{path.suffix}")
                return rows, language, source
        return None

    def run(self) -> dict[str, Any]:
        video_id = self._video_id(self.config.url)
        with tempfile.TemporaryDirectory(prefix="video-research-youtube-") as raw_tmp:
            work = pathlib.Path(raw_tmp)
            metadata_proc = self.run_yt_dlp(
                ["--no-playlist", "--dump-single-json", self.config.url],
                work,
                "youtube-metadata",
                check=False,
            )
            if metadata_proc.returncode != 0 or not metadata_proc.stdout.strip():
                raise ResearchError("yt-dlp could not read public YouTube metadata.")
            info = json.loads(metadata_proc.stdout)
            video_id = str(info.get("id") or video_id or "youtube")
            duration = float(info.get("duration") or 0)
            metadata = {
                "platform": "youtube",
                "url": self.config.url,
                "video_id": video_id,
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "channel_id": info.get("channel_id") or info.get("uploader_id"),
                "duration": duration,
                "upload_date": info.get("upload_date"),
                "timestamp": info.get("timestamp"),
                "description": info.get("description"),
                "chapters": info.get("chapters"),
                "thumbnail": info.get("thumbnail"),
            }
            self.bundle.json("metadata.json", metadata)

            self.run_yt_dlp(
                [
                    "--no-playlist",
                    "--skip-download",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    "zh-Hans.*,zh-Hant.*,zh.*,en.*",
                    "--sub-format",
                    "json3/vtt/best",
                    "-o",
                    "%(id)s.%(ext)s",
                    self.config.url,
                ],
                work,
                "youtube-captions",
                check=False,
            )
            caption = self.select_caption(work, video_id)

            frames: list[dict[str, Any]] = []
            if self.config.include_frames and self.config.max_frames > 0:
                self.run_yt_dlp(
                    [
                        "--no-playlist",
                        "-f",
                        "bv*[height<=480]+ba/b[height<=480]/b",
                        "--merge-output-format",
                        "mp4",
                        "-o",
                        "visual.%(ext)s",
                        self.config.url,
                    ],
                    work,
                    "youtube-video",
                    check=False,
                )
                visual_candidates = list(work.glob("visual.*"))
                if visual_candidates:
                    video_path = max(visual_candidates, key=lambda p: p.stat().st_size)
                    video_duration = self.runner.ffprobe_duration(video_path)
                    if media_duration_ok(video_duration, duration):
                        frames = extract_frames(
                            video_path,
                            video_duration,
                            self.bundle.root / "frames",
                            self.config.max_frames,
                            self.runner,
                            1,
                        )
                    else:
                        self.warnings.append("Downloaded YouTube visual stream failed the duration check.")

            if caption:
                segments, language, source = caption
                processed = (
                    duration
                    if duration and max(s.end for s in segments) >= duration * 0.88
                    else max((s.end for s in segments), default=0.0)
                )
            else:
                audio_path = work / "audio.mp3"
                self.run_yt_dlp(
                    [
                        "--no-playlist",
                        "-f",
                        "ba/b",
                        "-x",
                        "--audio-format",
                        "mp3",
                        "--audio-quality",
                        "9",
                        "-o",
                        str(audio_path),
                        self.config.url,
                    ],
                    work,
                    "youtube-audio",
                    check=False,
                )
                if not audio_path.exists():
                    matches = list(work.glob("audio.*"))
                    if matches:
                        extract_audio(matches[0], audio_path, self.runner)
                if not audio_path.exists():
                    raise ResearchError("YouTube had no usable captions and public audio retrieval failed.")
                audio_duration = self.runner.ffprobe_duration(audio_path)
                if not media_duration_ok(audio_duration, duration):
                    raise ResearchError("Downloaded YouTube audio failed the duration/completeness check.")
                segments, language, source = transcribe_audio(
                    audio_path,
                    1,
                    self.bundle,
                    self.config.language,
                    self.config.transcription_backend,
                    self.warnings,
                )
                processed = audio_duration

            segments = dedupe_segments(segments)
            if not segments:
                raise ResearchError("No usable YouTube transcript was produced.")
            segments_to_outputs(self.bundle, segments, multi_part=False)
            self.bundle.json("frames/index.json", frames)
            expected = duration
            coverage = min(1.0, processed / expected) if expected > 0 else None
            complete = coverage is None or coverage >= 0.95
            return {
                "status": "complete" if complete else "partial",
                "platform": "youtube",
                "job_id": self.config.job_id,
                "url": self.config.url,
                "video_id": video_id,
                "title": info.get("title"),
                "expected_duration_seconds": round(expected, 3),
                "processed_media_seconds": round(processed, 3),
                "coverage_ratio": round(coverage, 4) if coverage is not None else None,
                "transcript_sources": [source],
                "transcript_languages": [language],
                "segment_count": len(segments),
                "transcript_last_timestamp_seconds": round(max(segment.end for segment in segments), 3),
                "frame_count": len(frames),
                "visual_analysis_available": bool(frames),
                "warnings": self.warnings,
            }
