from __future__ import annotations

import pathlib
import urllib.parse
from typing import Any

from vr_bilibili_client import BilibiliClientMixin
from vr_core import (
    MediaCandidate,
    PartResult,
    ResearchError,
    Segment,
    clean_text,
    dedupe_segments,
    download_media,
    extract_audio,
    extract_frames,
    segments_to_outputs,
)
from vr_transcription import transcribe_audio


class BilibiliResearcher(BilibiliClientMixin):
    def process_part(
        self,
        page: dict[str, Any],
        part: int,
        owner_mid: Any,
        frame_budget: int,
    ) -> PartResult:
        cid = page.get("cid")
        if cid is None:
            raise ResearchError(f"Bilibili part {part} has no cid.")
        expected = float(page.get("duration") or 0)
        title = clean_text(page.get("part") or page.get("title") or f"Part {part}")
        part_dir = self.bundle.root / "parts" / f"p{part:03d}"
        part_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        self.official_conclusion(cid, owner_mid, part)

        subtitle_result: tuple[list[Segment], str, str] | None = None
        try:
            subtitle_result = self.subtitles(cid, part, part_dir)
        except Exception as exc:
            warnings.append(f"Subtitle lookup failed: {exc}")

        candidates = self.media_candidates(cid, part)
        video_result: tuple[pathlib.Path, float, MediaCandidate] | None = None
        frame_entries: list[dict[str, Any]] = []
        if self.config.include_frames and frame_budget > 0:
            visual_candidates = [item for item in candidates if item.kind in {"video", "muxed"}]
            video_result = download_media(
                self.session,
                visual_candidates,
                part_dir,
                self.runner,
                expected,
                f"video-p{part}",
                warnings,
            )
            if video_result:
                video_path, video_duration, _ = video_result
                frame_entries = extract_frames(
                    video_path,
                    video_duration,
                    self.bundle.root / "frames",
                    frame_budget,
                    self.runner,
                    part,
                )

        processed = video_result[1] if video_result else 0.0
        if subtitle_result:
            segments, detected_language, source = subtitle_result
            if processed <= 0:
                last_end = max((segment.end for segment in segments), default=0.0)
                processed = expected if expected and last_end >= expected * 0.88 else last_end
        else:
            audio_candidates = [item for item in candidates if item.kind in {"audio", "muxed"}]
            audio_source: pathlib.Path | None = None
            audio_source_duration = 0.0
            if video_result and video_result[2].kind == "muxed":
                audio_source = video_result[0]
                audio_source_duration = video_result[1]
            if audio_source is None:
                audio_result = download_media(
                    self.session,
                    audio_candidates,
                    part_dir,
                    self.runner,
                    expected,
                    f"audio-p{part}",
                    warnings,
                )
                if audio_result:
                    audio_source, audio_source_duration, _ = audio_result
            if audio_source is None:
                raise ResearchError(f"Part {part}: no subtitle or complete public audio stream was available.")
            audio_path = part_dir / "transcription-audio.mp3"
            audio_duration = extract_audio(audio_source, audio_path, self.runner)
            processed = max(processed, audio_source_duration, audio_duration)
            segments, detected_language, source = transcribe_audio(
                audio_path,
                part,
                self.bundle,
                self.config.language,
                self.config.transcription_backend,
                warnings,
            )
            audio_path.unlink(missing_ok=True)

        for raw in part_dir.glob("audio-p*.*"):
            raw.unlink(missing_ok=True)
        for raw in part_dir.glob("video-p*.*"):
            raw.unlink(missing_ok=True)

        self.bundle.json(
            f"parts/p{part:03d}/part-result.json",
            {
                "part": part,
                "cid": cid,
                "title": title,
                "expected_duration_seconds": expected,
                "processed_media_seconds": processed,
                "transcript_source": source,
                "transcript_language": detected_language,
                "segment_count": len(segments),
                "frame_count": len(frame_entries),
                "warnings": warnings,
            },
        )
        return PartResult(
            part=part,
            cid=cid,
            title=title,
            expected_duration_seconds=expected,
            processed_media_seconds=processed,
            transcript_source=source,
            transcript_language=detected_language,
            segments=segments,
            frame_entries=frame_entries,
            warnings=warnings,
        )

    def run(self) -> dict[str, Any]:
        view = self.get_view()
        pages = self.get_pages(view)
        url_query = urllib.parse.parse_qs(urllib.parse.urlparse(self.config.url).query)
        url_part = None
        try:
            if url_query.get("p"):
                url_part = max(1, int(url_query["p"][0]))
        except (TypeError, ValueError):
            pass
        selected_part = self.config.part or url_part or 1
        if self.config.all_parts:
            selected_pages = list(enumerate(pages, start=1))
        else:
            if selected_part > len(pages):
                raise ResearchError(
                    f"Requested Bilibili part {selected_part}, but the video has {len(pages)} part(s)."
                )
            selected_pages = [(selected_part, pages[selected_part - 1])]

        owner = view.get("owner") or {}
        title = clean_text(view.get("title") or f"Bilibili {self.bvid}")
        metadata = {
            "platform": "bilibili",
            "url": self.config.url,
            "bvid": self.bvid,
            "aid": view.get("aid"),
            "title": title,
            "description": view.get("desc"),
            "owner": owner,
            "publish_timestamp": view.get("pubdate"),
            "total_parts": len(pages),
            "selected_parts": [part for part, _ in selected_pages],
            "pages": pages,
            "stat": view.get("stat"),
            "thumbnail": view.get("pic"),
        }
        self.bundle.json("metadata.json", metadata)

        total_frames = self.config.max_frames if self.config.include_frames else 0
        per_part = max(1, total_frames // len(selected_pages)) if total_frames else 0
        results: list[PartResult] = []
        for index, (part, page) in enumerate(selected_pages):
            remaining = max(0, total_frames - sum(len(item.frame_entries) for item in results))
            budget = min(remaining, per_part if index < len(selected_pages) - 1 else remaining)
            results.append(self.process_part(page, part, owner.get("mid"), budget))

        all_segments = dedupe_segments(seg for result in results for seg in result.segments)
        if not all_segments:
            raise ResearchError("No usable transcript was produced.")
        segments_to_outputs(self.bundle, all_segments, multi_part=len(results) > 1)
        frames = [row for result in results for row in result.frame_entries]
        self.bundle.json("frames/index.json", frames)

        expected_total = sum(item.expected_duration_seconds for item in results)
        processed_total = sum(item.processed_media_seconds for item in results)
        coverage = min(1.0, processed_total / expected_total) if expected_total > 0 else None
        warnings = self.warnings + [warning for result in results for warning in result.warnings]
        complete = bool(all_segments) and (coverage is None or coverage >= 0.95)
        if len(pages) > len(results):
            warnings.append(
                f"This Bilibili item has {len(pages)} parts; only part(s) "
                f"{', '.join(str(item.part) for item in results)} were requested and processed."
            )
        return {
            "status": "complete" if complete else "partial",
            "platform": "bilibili",
            "job_id": self.config.job_id,
            "url": self.config.url,
            "video_id": self.bvid,
            "title": title,
            "total_parts": len(pages),
            "processed_parts": [item.part for item in results],
            "expected_duration_seconds": round(expected_total, 3),
            "processed_media_seconds": round(processed_total, 3),
            "coverage_ratio": round(coverage, 4) if coverage is not None else None,
            "transcript_sources": sorted({item.transcript_source for item in results}),
            "transcript_languages": sorted({item.transcript_language for item in results}),
            "segment_count": len(all_segments),
            "transcript_last_timestamp_seconds": round(max(segment.end for segment in all_segments), 3),
            "frame_count": len(frames),
            "visual_analysis_available": bool(frames),
            "warnings": warnings,
        }
