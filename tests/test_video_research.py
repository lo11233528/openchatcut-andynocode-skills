from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vr = load_module("video_research", ROOT / "video-research-cloud" / "video_research.py")
pr = load_module("parse_request", ROOT / "video-research-cloud" / "parse_request.py")


def test_request_config_normalizes_values():
    cfg = vr.RequestConfig.from_json(
        {
            "url": "https://www.bilibili.com/video/BV123abc/?p=2",
            "job_id": "hello / world",
            "max_frames": 999,
            "part": "2",
            "transcription_backend": "AUTO",
        }
    )
    assert cfg.job_id == "hello-world"
    assert cfg.max_frames == 20
    assert cfg.part == 2
    assert cfg.transcription_backend == "auto"


def test_request_config_string_booleans():
    cfg = vr.RequestConfig.from_json(
        {
            "url": "https://www.bilibili.com/video/BV123abc/",
            "include_frames": "false",
            "all_parts": "true",
        }
    )
    assert cfg.include_frames is False
    assert cfg.all_parts is True


def test_request_config_rejects_unknown_host():
    try:
        vr.RequestConfig.from_json({"url": "https://example.com/video"})
    except vr.ResearchError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("unknown host should be rejected")


def test_bilibili_json_subtitle_dedupes(tmp_path: pathlib.Path):
    path = tmp_path / "subtitle.json3"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "你好"}]},
                    {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "你好"}]},
                    {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "世界"}]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = vr.parse_json3(path)
    assert [row.text for row in rows] == ["你好", "世界"]


def test_vtt_parser(tmp_path: pathlib.Path):
    path = tmp_path / "captions.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello <b>world</b>\n\n",
        encoding="utf-8",
    )
    rows = vr.parse_vtt(path)
    assert len(rows) == 1
    assert rows[0].start == 1.0
    assert rows[0].end == 2.5
    assert rows[0].text == "Hello world"


def test_collect_bilibili_candidates():
    payload = {
        "code": 0,
        "data": {
            "durl": [{"url": "https://example.invalid/muxed", "size": 12}],
            "dash": {
                "audio": [
                    {
                        "baseUrl": "https://example.invalid/audio",
                        "bandwidth": 100,
                        "codecs": "mp4a",
                    }
                ],
                "video": [
                    {
                        "base_url": "https://example.invalid/video",
                        "bandwidth": 200,
                        "codecs": "avc1",
                    }
                ],
            },
        },
    }
    rows = vr.BilibiliResearcher._collect_candidates("x", payload)
    assert {row.kind for row in rows} == {"muxed", "audio", "video"}


def test_extract_json_from_fenced_issue_body():
    body = """```json
{"url": "https://www.bilibili.com/video/BV123/"}
```"""
    assert pr.extract_json_object(body)["url"].endswith("BV123/")


def test_issue_event_request():
    event = {
        "issue": {
            "number": 12,
            "title": "[video-research] test",
            "body": '{"url":"https://youtu.be/abc1234"}',
        }
    }
    request = pr.request_from_event(event, "issues")
    assert request["url"] == "https://youtu.be/abc1234"
    assert request["job_id"].startswith("issue-12-")
