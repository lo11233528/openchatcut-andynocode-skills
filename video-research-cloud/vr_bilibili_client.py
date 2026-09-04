from __future__ import annotations

import hashlib
import pathlib
import re
import time
import urllib.parse
import uuid
from typing import Any

import requests

from vr_core import (
    BVID_RE,
    USER_AGENT,
    WBI_MIXIN_TABLE,
    Bundle,
    MediaCandidate,
    RequestConfig,
    ResearchError,
    Runner,
    Segment,
    clean_text,
    dedupe_segments,
    redact_for_logs,
)


class BilibiliClientMixin:
    def __init__(self, config: RequestConfig, bundle: Bundle, runner: Runner):
        self.config = config
        self.bundle = bundle
        self.runner = runner
        self.session = requests.Session()
        self.bvid = self._extract_bvid(config.url)
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": f"https://www.bilibili.com/video/{self.bvid}/",
                "Origin": "https://www.bilibili.com",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self._mixin_key: str | None = None
        self.warnings: list[str] = []
        self._bootstrap_cookies()

    @staticmethod
    def _extract_bvid(url: str) -> str:
        match = BVID_RE.search(url)
        if match:
            return match.group(0)
        parsed = urllib.parse.urlparse(url)
        if (parsed.hostname or "").lower() == "b23.tv":
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=45,
                    allow_redirects=True,
                )
                match = BVID_RE.search(response.url) or BVID_RE.search(response.text[:500000])
                if match:
                    return match.group(0)
            except requests.RequestException as exc:
                raise ResearchError(f"Could not resolve Bilibili short link: {exc}") from exc
        raise ResearchError("Bilibili URL does not contain a resolvable BV identifier.")

    def _bootstrap_cookies(self) -> None:
        now = str(int(time.time()))
        self.session.cookies.set("b_nut", now, domain=".bilibili.com")
        self.session.cookies.set("CURRENT_FNVAL", "4048", domain=".bilibili.com")
        self.session.cookies.set("CURRENT_QUALITY", "16", domain=".bilibili.com")
        self.session.cookies.set(
            "b_lsid",
            uuid.uuid4().hex[:8].upper() + "_" + hex(int(time.time() * 1000))[2:].upper(),
            domain=".bilibili.com",
        )
        self.session.cookies.set("_uuid", str(uuid.uuid4()).upper() + "infoc", domain=".bilibili.com")
        self.session.cookies.set(
            "buvid_fp",
            hashlib.md5((USER_AGENT + now).encode()).hexdigest(),
            domain=".bilibili.com",
        )
        try:
            payload = self.get_json(
                "bootstrap-spi",
                "https://api.bilibili.com/x/frontend/finger/spi",
                required_code=False,
            )
            data = payload.get("data") or {}
            if data.get("b_3"):
                self.session.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
            if data.get("b_4"):
                self.session.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except Exception as exc:
            self.warnings.append(f"Bilibili device-cookie bootstrap failed: {exc}")
        try:
            self.session.get("https://www.bilibili.com/", timeout=30)
        except Exception:
            pass

    def get_json(
        self,
        name: str,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        required_code: bool = True,
    ) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=45)
        self.bundle.log_http(f"bili-{name}", response)
        response.raise_for_status()
        payload = response.json()
        self.bundle.json(f"logs/bili-{name}-payload.json", redact_for_logs(payload))
        if required_code and payload.get("code") != 0:
            raise ResearchError(
                f"Bilibili API {name} returned code={payload.get('code')}: "
                f"{payload.get('message') or payload.get('msg')}"
            )
        return payload

    def get_view(self) -> dict[str, Any]:
        attempts = [
            ("wbi-view", "https://api.bilibili.com/x/web-interface/wbi/view", {"bvid": self.bvid}),
            ("view", "https://api.bilibili.com/x/web-interface/view", {"bvid": self.bvid}),
            ("app-view", "https://app.bilibili.com/x/v2/view", {"bvid": self.bvid}),
        ]
        errors: list[str] = []
        for name, url, params in attempts:
            try:
                payload = self.get_json(name, url, params)
                data = payload.get("data") or payload.get("result")
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self.warnings.append("Primary Bilibili metadata endpoints failed; using pagelist-only metadata.")
        self.bundle.text("logs/bili-view-errors.txt", "\n".join(errors) + "\n")
        return {}

    def get_pages(self, view: dict[str, Any]) -> list[dict[str, Any]]:
        pages = view.get("pages")
        if isinstance(pages, list) and pages:
            return pages
        payload = self.get_json(
            "pagelist",
            "https://api.bilibili.com/x/player/pagelist",
            {"bvid": self.bvid, "jsonp": "jsonp"},
        )
        pages = payload.get("data")
        if not isinstance(pages, list) or not pages:
            raise ResearchError("Bilibili did not return a usable page/part list.")
        return pages

    def _get_mixin_key(self) -> str:
        if self._mixin_key is not None:
            return self._mixin_key
        payload = self.get_json("nav", "https://api.bilibili.com/x/web-interface/nav", required_code=False)
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img = pathlib.PurePosixPath(urllib.parse.urlparse(wbi.get("img_url") or "").path).stem
        sub = pathlib.PurePosixPath(urllib.parse.urlparse(wbi.get("sub_url") or "").path).stem
        raw = img + sub
        if len(raw) < 64:
            raise ResearchError("Bilibili nav API did not return usable WBI keys.")
        self._mixin_key = "".join(raw[i] for i in WBI_MIXIN_TABLE if i < len(raw))[:32]
        return self._mixin_key

    def sign(self, params: dict[str, Any]) -> dict[str, str]:
        cleaned = {
            str(key): re.sub(r"[!'()*]", "", str(value))
            for key, value in params.items()
            if value is not None
        }
        cleaned["wts"] = str(int(time.time()))
        query = urllib.parse.urlencode(sorted(cleaned.items()))
        cleaned["w_rid"] = hashlib.md5((query + self._get_mixin_key()).encode()).hexdigest()
        return cleaned

    def choose_subtitle(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        requested = self.config.language.lower().replace("_", "-")

        def score(item: dict[str, Any]) -> tuple[int, int, int]:
            lan = str(item.get("lan") or item.get("lan_doc") or "").lower()
            is_ai = int(item.get("type") or 0) != 0 or "ai" in lan
            language_score = 0
            if requested not in {"", "auto"} and requested.split("-")[0] in lan:
                language_score = 50
            elif lan.startswith("zh") or "中文" in lan:
                language_score = 40
            elif lan.startswith("en") or "english" in lan:
                language_score = 30
            return (language_score, 0 if is_ai else 10, -int(item.get("id") or 0))

        return max(rows, key=score)

    def subtitles(
        self,
        cid: int | str,
        part: int,
        part_dir: pathlib.Path,
    ) -> tuple[list[Segment], str, str] | None:
        payload = self.get_json(
            f"player-v2-p{part}",
            "https://api.bilibili.com/x/player/v2",
            {"bvid": self.bvid, "cid": cid},
        )
        rows = (((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
        self.bundle.json(f"parts/p{part:03d}/subtitle-index.json", rows)
        selected = self.choose_subtitle(rows)
        if not selected:
            return None
        url = selected.get("subtitle_url") or selected.get("subtitle_url_v2")
        if not url:
            return None
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://api.bilibili.com" + url
        response = self.session.get(url, timeout=45)
        self.bundle.log_http(f"subtitle-p{part}", response)
        response.raise_for_status()
        obj = response.json()
        self.bundle.json(f"parts/p{part:03d}/subtitle-selected.json", obj)
        segments: list[Segment] = []
        for row in obj.get("body") or []:
            text = clean_text(row.get("content"))
            if text:
                segments.append(
                    Segment(
                        start=float(row.get("from") or 0),
                        end=float(row.get("to") or row.get("from") or 0),
                        text=text,
                        part=part,
                    )
                )
        segments = dedupe_segments(segments)
        if not segments:
            return None
        language = str(selected.get("lan") or selected.get("lan_doc") or "unknown")
        source = (
            "bilibili-auto-subtitle"
            if int(selected.get("type") or 0) != 0
            else "bilibili-creator-subtitle"
        )
        return segments, language, source

    @staticmethod
    def _collect_candidates(label: str, payload: dict[str, Any] | None) -> list[MediaCandidate]:
        candidates: list[MediaCandidate] = []
        if not isinstance(payload, dict) or payload.get("code") != 0:
            return candidates
        data = payload.get("data") or payload.get("result") or {}
        for index, row in enumerate(data.get("durl") or []):
            urls = [row.get("url")] + list(row.get("backup_url") or [])
            for backup, url in enumerate(urls):
                if url:
                    candidates.append(
                        MediaCandidate(
                            url=str(url),
                            kind="muxed",
                            label=f"{label}-durl-{index}-{backup}",
                            bandwidth=int(row.get("size") or 0),
                        )
                    )
        dash = data.get("dash") or {}
        for kind, rows in (("audio", dash.get("audio") or []), ("video", dash.get("video") or [])):
            for index, row in enumerate(rows):
                urls = [row.get("baseUrl") or row.get("base_url")] + list(
                    row.get("backupUrl") or row.get("backup_url") or []
                )
                for backup, url in enumerate(urls):
                    if url:
                        candidates.append(
                            MediaCandidate(
                                url=str(url),
                                kind=kind,
                                label=f"{label}-{kind}-{index}-{backup}",
                                bandwidth=int(row.get("bandwidth") or 0),
                                codecs=str(row.get("codecs") or "") or None,
                            )
                        )
        for family in ("dolby", "flac"):
            section = dash.get(family) or {}
            rows = section.get("audio") if isinstance(section, dict) else None
            if isinstance(rows, dict):
                rows = [rows]
            for index, row in enumerate(rows or []):
                urls = [row.get("baseUrl") or row.get("base_url")] + list(
                    row.get("backupUrl") or row.get("backup_url") or []
                )
                for backup, url in enumerate(urls):
                    if url:
                        candidates.append(
                            MediaCandidate(
                                url=str(url),
                                kind="audio",
                                label=f"{label}-{family}-{index}-{backup}",
                                bandwidth=int(row.get("bandwidth") or 0),
                                codecs=str(row.get("codecs") or "") or None,
                            )
                        )
        return candidates

    def media_candidates(self, cid: int | str, part: int) -> list[MediaCandidate]:
        base_params = {"bvid": self.bvid, "cid": cid, "qn": 16, "fnver": 0, "fourk": 0}
        calls: list[tuple[str, str, dict[str, Any]]] = [
            (
                f"playurl-progressive-p{part}",
                "https://api.bilibili.com/x/player/playurl",
                {**base_params, "fnval": 0, "platform": "html5", "high_quality": 0},
            ),
            (
                f"playurl-progressive2-p{part}",
                "https://api.bilibili.com/x/player/playurl",
                {**base_params, "fnval": 0},
            ),
            (
                f"playurl-dash-p{part}",
                "https://api.bilibili.com/x/player/playurl",
                {**base_params, "fnval": 16},
            ),
        ]
        payloads: list[tuple[str, dict[str, Any]]] = []
        for name, url, params in calls:
            try:
                payloads.append((name, self.get_json(name, url, params, required_code=False)))
            except Exception as exc:
                self.warnings.append(f"{name} failed: {exc}")
        try:
            payloads.append(
                (
                    f"wbi-playurl-dash-p{part}",
                    self.get_json(
                        f"wbi-playurl-dash-p{part}",
                        "https://api.bilibili.com/x/player/wbi/playurl",
                        self.sign({**base_params, "fnval": 16}),
                        required_code=False,
                    ),
                )
            )
        except Exception as exc:
            self.warnings.append(f"Signed Bilibili player request failed: {exc}")
        candidates: list[MediaCandidate] = []
        for label, payload in payloads:
            candidates.extend(self._collect_candidates(label, payload))
        self.bundle.json(
            f"parts/p{part:03d}/media-candidates.json",
            [
                {
                    "label": item.label,
                    "kind": item.kind,
                    "bandwidth": item.bandwidth,
                    "codecs": item.codecs,
                    "host": urllib.parse.urlparse(item.url).hostname,
                    "url_sha256": hashlib.sha256(item.url.encode()).hexdigest(),
                }
                for item in candidates
            ],
        )
        return candidates

    def official_conclusion(self, cid: int | str, up_mid: Any, part: int) -> None:
        if not up_mid:
            return
        try:
            payload = self.get_json(
                f"official-conclusion-p{part}",
                "https://api.bilibili.com/x/web-interface/view/conclusion/get",
                self.sign(
                    {
                        "bvid": self.bvid,
                        "cid": cid,
                        "up_mid": up_mid,
                        "web_location": 1315873,
                    }
                ),
                required_code=False,
            )
            self.bundle.json(f"parts/p{part:03d}/official-conclusion.json", payload)
        except Exception as exc:
            self.warnings.append(f"Bilibili official AI conclusion was unavailable for part {part}: {exc}")
