---
name: transcript-safe-cleanup
description: Precision cleanup of talking-head videos using existing transcript timestamps while preserving timeline synchronization.
---

# Transcript Safe Cleanup

## Purpose

Clean long-form talking-head videos using existing transcript timestamps.

## Core Rules

- Use the existing transcript.
- Never re-transcribe.
- Never add captions.
- Never modify transcript text as a replacement for timeline edits.
- Timeline edits must correspond to actual media timestamps.

## Remove

Remove:

- false starts
- incomplete sentence attempts
- repeated takes

When repeated takes exist:

KEEP:
- the final complete take

REMOVE:
- earlier incomplete attempts

## Timeline Safety

Before every cut identify:

- source clip ID
- start timestamp
- end timestamp

Never cut:

- inside words
- inside sentences
- important emphasis

## Pause Cleanup

Only remove clearly unnecessary pauses.

Preserve:

- natural speaking rhythm
- breathing
- sentence flow

## Verification

After editing confirm:

- timeline audio matches transcript
- no words are accidentally removed
- synchronization is preserved
