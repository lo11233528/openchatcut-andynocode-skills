---
name: video-research
description: Consistently retrieve, summarize, analyze, fact-check, and extract predictions from public YouTube and Bilibili videos using ChatGPT cloud tools only. Use whenever the user supplies a youtube.com, youtu.be, youtube-nocookie.com, bilibili.com, or b23.tv URL and asks to understand the video. Never use Codex or the user's computer. For complete Bilibili understanding, trigger the permanent GitHub-hosted Video Research Cloud workflow in lo11233528/openchatcut-andynocode-skills, validate its manifest, read the full transcript, and inspect sampled frames before answering.
metadata:
  execution: chatgpt-cloud-plus-github-actions
  local_execution: forbidden
  repository: lo11233528/openchatcut-andynocode-skills
---

# Video Research — ChatGPT Cloud

The user's non-negotiable preference is **ChatGPT cloud only**. The user should only paste a video URL and ask for analysis.

## Hard constraints

- Never use Codex, Codex CLI/Desktop, the user's Mac, Computer Use on the user's device, a local terminal, local Python, local FFmpeg, local MCP, local browser cookies, Railway, or a self-hosted server.
- ChatGPT may use its connected GitHub app, a GitHub-hosted Actions runner, and its own cloud file/code workspace to retrieve and inspect the result artifact.
- Never ask the user to execute commands, keep a computer awake, download the video, or manually transcribe it.
- Process only public videos. Do not bypass login, paywalls, CAPTCHA, region controls, DRM, or private/unlisted access restrictions.
- Never place credentials, cookies, API keys, or private links in a GitHub issue.
- Complete all polling and analysis in the current response. Do not promise background delivery.

## Default routing

For Bilibili, or for YouTube when native transcript access is incomplete, use the permanent cloud workflow in:

```text
lo11233528/openchatcut-andynocode-skills
.github/workflows/video-research-cloud.yml
```

The workflow is triggered by a GitHub issue. It prefers creator subtitles, then platform automatic subtitles, then AssemblyAI when configured, then faster-whisper in the GitHub cloud runner. It samples representative frames and deletes raw media before uploading the result.

## Start a cloud job

1. Normalize the supplied URL and preserve a Bilibili `p=` part number.
2. Generate a unique readable job ID, for example:

```text
20260904-143055-BV19Ttg6xEzu-p1
```

3. Create one issue in `lo11233528/openchatcut-andynocode-skills`.
4. The title must start exactly with:

```text
[video-research]
```

5. Put only this JSON object in the body:

```json
{
  "url": "<public video URL>",
  "job_id": "<unique job ID>",
  "language": "auto",
  "include_frames": true,
  "max_frames": 12,
  "all_parts": false,
  "transcription_backend": "auto"
}
```

Use the user's requested language as the language hint when clear. Set `all_parts` to true only when the user explicitly asks for every part. Otherwise preserve `p=` or use the requested `part`; the workflow defaults to part 1 and reports any additional unprocessed parts.

## Find and monitor the run

1. Read the created issue's comments until the workflow posts `run_id` and artifact name.
2. Poll that run's jobs using the GitHub connector until it reaches a terminal state.
3. Do not treat a successful upload step as proof that extraction succeeded; the final job conclusion and `manifest.json` determine success.
4. Fetch the run artifact whose name matches `video-research-<job_id>` and download it into ChatGPT's cloud workspace.
5. Unzip it in ChatGPT's cloud workspace. This is cloud processing, not user-device execution.

## Validate before summarizing

Read `manifest.json` first. Require all of the following for a confident full-video summary:

- `status` is `complete`;
- `coverage_ratio` is at least `0.95`, or the manifest clearly explains why duration is unavailable;
- `segment_count` is greater than zero;
- the platform/video ID matches the supplied URL;
- the selected Bilibili part(s) match the request;
- `raw_media_retained` is false.

If the manifest is partial or failed, state the exact failure stage and actual coverage near the top. Never fill missing content from assumptions, a similar upload, search snippets, the title, or comments.

## Read the evidence

1. Read `metadata.json`.
2. Read the complete `transcript_timed.md` from beginning to end. For a long transcript, page through the file; do not summarize from scattered snippets alone.
3. Use `transcript.json` when structured timestamps or part boundaries are needed.
4. Read `frames/index.json`.
5. Inspect relevant sampled JPEGs, especially around central claims, charts, demonstrations, citations, before/after examples, and prediction timestamps.
6. State visual coverage precisely, for example: `based on the complete available transcript and 12 sampled frames`; never imply frame-by-frame viewing.

## Analysis and fact-checking

Separate these layers:

- **What the creator explicitly says**
- **What the creator implies**
- **Evidence shown or cited in the video**
- **Independent verification**
- **Your assessment**

Use current authoritative web sources for material scientific, medical, legal, financial, political, product, company, price, public-figure, or other time-sensitive claims. Prefer primary sources. A persuasive video claim is not automatically a fact.

For prediction-heavy content, extract:

- exact predicted outcome;
- target or affected group;
- explicit versus implied status;
- timeframe or deadline;
- stated conditions and mechanism;
- creator confidence;
- supporting timestamp;
- evidence actually offered;
- observable confirmation/falsification criteria;
- your independent probability or judgment, with uncertainty.

## Recommended response structure

Adapt to the user's request:

1. **一句話結論 / One-sentence conclusion**
2. **處理覆蓋範圍 / Coverage**
3. **完整摘要 / Full summary**
4. **重要時間線 / Key timeline**
5. **他到底預測了甚麼 / Predictions**
6. **我的分析 / Critical analysis**
7. **事實查核 / Fact-check**
8. **對使用者的實際啟示 / Practical implications**
9. **限制與不確定性 / Limitations**

Match the user's language. For Chinese, use Traditional Chinese unless the user explicitly asks for Simplified Chinese.

Apply `references/analysis-rubric.md` throughout.
