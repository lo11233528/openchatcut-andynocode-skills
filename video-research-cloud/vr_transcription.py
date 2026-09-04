from __future__ import annotations

import os
import pathlib
import re
import time
from typing import Any

import requests

from vr_core import Bundle, ResearchError, Segment, clean_text, dedupe_segments


def group_assembly_words(words: list[dict[str, Any]], part: int) -> list[Segment]:
    rows: list[Segment] = []
    current: list[str] = []
    start_ms: int | None = None
    end_ms = 0
    for word in words:
        token = clean_text(word.get("text"))
        if not token:
            continue
        word_start = int(word.get("start") or 0)
        word_end = int(word.get("end") or word_start)
        if start_ms is None:
            start_ms = word_start
        current.append(token)
        end_ms = max(end_ms, word_end)
        joined = " ".join(current)
        elapsed = (end_ms - start_ms) / 1000.0
        terminal = bool(re.search(r"[.!?。！？]$", token))
        if terminal or elapsed >= 14 or len(current) >= 40:
            rows.append(
                Segment(
                    start=start_ms / 1000.0,
                    end=end_ms / 1000.0,
                    text=joined,
                    part=part,
                )
            )
            current = []
            start_ms = None
            end_ms = 0
    if current and start_ms is not None:
        rows.append(
            Segment(
                start=start_ms / 1000.0,
                end=end_ms / 1000.0,
                text=" ".join(current),
                part=part,
            )
        )
    return dedupe_segments(rows)


def transcribe_assemblyai(
    audio_path: pathlib.Path,
    part: int,
    bundle: Bundle,
    language: str,
) -> tuple[list[Segment], str]:
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise ResearchError("ASSEMBLYAI_API_KEY is not configured.")
    base = os.environ.get("ASSEMBLYAI_BASE_URL", "https://api.assemblyai.com").rstrip("/")
    headers = {"authorization": api_key}
    with audio_path.open("rb") as handle:
        upload = requests.post(
            f"{base}/v2/upload",
            headers=headers,
            data=handle,
            timeout=(30, 900),
        )
    upload.raise_for_status()
    upload_url = upload.json().get("upload_url")
    if not upload_url:
        raise ResearchError("AssemblyAI upload response did not contain upload_url.")

    payload: dict[str, Any] = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
    }
    normalized_language = language.lower().replace("_", "-")
    if normalized_language in {"auto", "", "unknown"}:
        payload["language_detection"] = True
    elif normalized_language.startswith("zh"):
        payload["language_code"] = "zh"
    elif normalized_language.startswith("en"):
        payload["language_code"] = "en"
    else:
        payload["language_detection"] = True

    submit = requests.post(f"{base}/v2/transcript", headers=headers, json=payload, timeout=60)
    submit.raise_for_status()
    transcript_id = submit.json().get("id")
    if not transcript_id:
        raise ResearchError("AssemblyAI submit response did not contain transcript id.")

    deadline = time.monotonic() + 45 * 60
    result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = requests.get(f"{base}/v2/transcript/{transcript_id}", headers=headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise ResearchError(f"AssemblyAI transcription failed: {result.get('error')}")
        time.sleep(10)
    else:
        raise ResearchError("AssemblyAI transcription timed out after 45 minutes.")

    bundle.json(f"parts/p{part:03d}/assemblyai-result.json", result)
    rows: list[Segment] = []
    for utterance in result.get("utterances") or []:
        text = clean_text(utterance.get("text"))
        if text:
            rows.append(
                Segment(
                    start=float(utterance.get("start") or 0) / 1000.0,
                    end=float(utterance.get("end") or 0) / 1000.0,
                    text=text,
                    part=part,
                    speaker=str(utterance.get("speaker") or "") or None,
                )
            )
    if not rows:
        rows = group_assembly_words(result.get("words") or [], part)
    if not rows:
        text = clean_text(result.get("text"))
        if text:
            rows = [Segment(start=0.0, end=0.0, text=text, part=part)]
    if not rows:
        raise ResearchError("AssemblyAI completed but returned no usable transcript text.")
    detected = str(result.get("language_code") or result.get("language") or language or "unknown")
    try:
        requests.delete(f"{base}/v2/transcript/{transcript_id}", headers=headers, timeout=60)
    except Exception:
        pass
    return dedupe_segments(rows), detected


def transcribe_whisper(
    audio_path: pathlib.Path,
    part: int,
    bundle: Bundle,
    language: str,
) -> tuple[list[Segment], str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ResearchError("faster-whisper is unavailable on the runner.") from exc
    model_name = os.environ.get("WHISPER_MODEL", "small")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    normalized = language.lower().replace("_", "-")
    language_arg: str | None = None
    if normalized.startswith("zh"):
        language_arg = "zh"
    elif normalized.startswith("en"):
        language_arg = "en"
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language_arg,
        vad_filter=True,
        beam_size=3,
        condition_on_previous_text=True,
    )
    rows: list[Segment] = []
    for segment in segments_iter:
        text = clean_text(segment.text)
        if text:
            rows.append(
                Segment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=text,
                    part=part,
                )
            )
    if not rows:
        raise ResearchError("Whisper returned no usable speech segments.")
    detected = str(getattr(info, "language", None) or language or "unknown")
    bundle.text(
        f"parts/p{part:03d}/whisper-info.txt",
        f"model={model_name}\nlanguage={detected}\n"
        f"language_probability={getattr(info, 'language_probability', '')}\n",
    )
    return dedupe_segments(rows), detected


def transcribe_audio(
    audio_path: pathlib.Path,
    part: int,
    bundle: Bundle,
    language: str,
    backend: str,
    warnings: list[str],
) -> tuple[list[Segment], str, str]:
    api_available = bool(os.environ.get("ASSEMBLYAI_API_KEY", "").strip())
    if backend == "assemblyai":
        order = ["assemblyai", "whisper"]
    elif backend == "whisper":
        order = ["whisper"]
    else:
        order = ["assemblyai", "whisper"] if api_available else ["whisper"]
    errors: list[str] = []
    for item in order:
        try:
            if item == "assemblyai":
                segments, detected = transcribe_assemblyai(audio_path, part, bundle, language)
                return segments, detected, "assemblyai-universal-3-5-pro"
            segments, detected = transcribe_whisper(audio_path, part, bundle, language)
            return segments, detected, f"faster-whisper-{os.environ.get('WHISPER_MODEL', 'small')}"
        except Exception as exc:
            errors.append(f"{item}: {exc}")
            warnings.append(f"Transcription backend {item} failed; trying the next permitted fallback.")
    raise ResearchError("All transcription backends failed: " + " | ".join(errors))
