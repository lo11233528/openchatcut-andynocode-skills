# Video Research Cloud

A cloud-only retrieval pipeline for public YouTube and Bilibili videos. The user interacts only with ChatGPT. ChatGPT creates a short-lived GitHub issue in this repository; GitHub Actions retrieves public subtitles or media, transcribes when necessary, samples representative frames, validates duration coverage, uploads a three-day artifact, and closes the issue.

## What it does

- Confirms the exact platform/video ID and Bilibili part number.
- Prefers creator subtitles, then platform automatic subtitles.
- Uses AssemblyAI when `ASSEMBLYAI_API_KEY` exists; otherwise uses `faster-whisper` in the GitHub-hosted cloud runner.
- Samples up to 20 representative frames when requested.
- Rejects incomplete media when the retrieved duration is materially shorter than the expected duration.
- Emits `manifest.json`, `metadata.json`, `transcript.txt`, `transcript_timed.md`, `transcript.json`, `frames/index.json`, sampled JPEGs, and redacted diagnostic logs.
- Deletes raw audio and video before artifact upload.

## What it does not do

- No Codex execution.
- No user Mac, local terminal, local browser, local MCP, or local cookies.
- No login bypass, CAPTCHA bypass, DRM circumvention, paid/private media retrieval, or user credential handling.
- No claim of frame-by-frame viewing: visual analysis is based on sampled frames.

## ChatGPT trigger

The workflow accepts issue-triggered jobs only when the issue was opened by the repository owner. Create a GitHub issue whose title starts with:

```text
[video-research]
```

Use a JSON body:

```json
{
  "url": "https://www.bilibili.com/video/BV.../?p=1",
  "job_id": "unique-readable-id",
  "language": "zh",
  "include_frames": true,
  "max_frames": 12,
  "all_parts": false,
  "transcription_backend": "auto"
}
```

The workflow posts its `run_id`, uploads an artifact named `video-research-<job_id>`, posts a completion summary, and closes the issue.

## Optional AssemblyAI setup

Add a repository Actions secret named `ASSEMBLYAI_API_KEY`. When absent, the workflow automatically uses `faster-whisper-small`. Never put the key in an issue, request JSON, repository file, transcript, or chat message.

## Completion gate

A result is marked complete only when a usable transcript exists and processed media coverage is at least 95% of the expected selected-part duration. Partial or failed jobs still upload their manifest and logs, but the workflow ends in failure so ChatGPT cannot silently treat them as complete.

## Artifact retention

Artifacts are retained for three days. They contain text, metadata, sampled frames, and redacted logs—not raw audio or video.
