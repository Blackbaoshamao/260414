# Keyword Voice Insertion Design

Date: 2026-06-02

## Goal

When a keyword auto-reply rule matches a viewer message, Aiszr should synthesize the rule reply with the anchor voice and insert that speech into the digital-human LiveTalking stream. The insertion should wait until the current short anchor-audio segment finishes, then play the keyword reply, then resume the normal anchor script loop.

## Non-Goals

- Do not rewrite the LiveTalking lip-sync model or avatar preprocessing flow.
- Do not replace the existing Aliyun Bailian voice provider.
- Do not choose or integrate a specific local voice model in this design. Local model selection needs a separate research step.
- Do not expose advanced audio-splitting parameters in the initial UI.

## Current Context

The project already has:

- Keyword auto-reply rules with a per-rule `generate_voice` switch.
- A voice provider abstraction with `create_clone()` and `synthesize()` methods.
- Aliyun Bailian voice cloning and synthesis.
- LiveTalking RTMP output where one WAV is repeatedly sent to `/humanaudio`.
- A digital-human pipeline that starts LiveTalking and configures OBS without needing changes to the lip-sync model itself.

The missing piece is a speech scheduler between voice synthesis and LiveTalking.

## Chosen Approach

Use an audio scheduling layer in front of LiveTalking.

Startup flow:

1. Read the anchor script text.
2. Use the currently selected voice provider to synthesize one full anchor WAV.
3. Split the full WAV into short WAV segments using local silence detection.
4. Loop those short segments through LiveTalking.

Keyword flow:

1. A keyword rule matches and `generate_voice` is enabled.
2. The rule reply is synthesized using the same selected provider and the anchor voice.
3. The generated WAV is pushed into a high-priority insertion queue.
4. The scheduler waits for the current short anchor segment to finish.
5. The scheduler sends the inserted keyword WAV to LiveTalking.
6. Normal anchor script segment playback resumes.

This preserves LiveTalking's existing audio-in to lip-sync behavior and changes only the audio feeding policy.

## Voice Provider Behavior

The voice provider selection applies to both clone and synthesis.

Initial providers:

- `aliyun_bailian`: existing cloud clone and cloud synthesis provider.
- `local_voice`: planned provider slot for a locally deployed free/open-source voice model.

Provider contract:

- `validate_credentials()` checks whether the provider is usable.
- `create_clone()` creates or records an anchor voice identity.
- `synthesize()` turns text into a WAV-compatible audio file.

Local voice provider compatibility:

- If the local model supports persistent voice IDs, `create_clone()` returns a local `clone_voice_id`.
- If the local model is zero-shot and uses a reference WAV on every synthesis call, `create_clone()` stores the uploaded anchor sample path as the local voice identity.

The local model itself must be selected only after a research gate covering license, Chinese quality, clone capability, Windows deployment, HTTP API stability, output format, runtime speed, and hardware requirements.

## Audio Segmentation

The anchor WAV is split locally after full synthesis.

Initial segmentation behavior:

- Detect silence using internal default parameters.
- Emit WAV segments only.
- Keep segmentation settings hidden from the main UI.
- Preserve small boundary padding so sentence endings are not cut too aggressively.

Default parameters should be close to the user's prior splitter workflow:

- Silence threshold: `-25 dB`
- Minimum segment length: `2000 ms`
- Minimum silence interval: `50 ms`
- Scan step: `10 ms`
- Maximum retained silence: `1000 ms`

If segmentation produces no usable segments, the scheduler falls back to using the full anchor WAV as a single segment.

## Scheduler Responsibilities

The new scheduler, tentatively `DigitalHumanSpeechScheduler`, owns the audio playback policy for LiveTalking.

Responsibilities:

- Hold the normal anchor segment loop.
- Hold a bounded insertion queue for keyword replies.
- Send exactly one WAV at a time to LiveTalking.
- Prefer insertion audio after the current normal segment ends.
- Resume normal anchor segments after insertion.
- Report status events for UI/logging.

It should not:

- Know about UI widgets.
- Match keyword rules.
- Clone voices.
- Generate text replies.
- Control OBS scenes.

## Integration Points

`digital_human_pipeline.py`:

- Prepare the full anchor WAV with the selected provider.
- Segment the WAV.
- Start LiveTalking.
- Create and start the scheduler instead of directly looping one WAV forever.

`livetalking_runtime.py`:

- Keep process startup and `/humanaudio` upload behavior.
- Expose a method that sends one WAV to LiveTalking and can be reused by the scheduler.
- Stop owning the fixed single-WAV loop when the scheduler is active.

`voice_manager.py`:

- Keep Aliyun provider behavior.
- Add provider support for a future `local_voice` provider.
- Ensure keyword synthesis can request the anchor role, not the copilot role.

`ui.py` keyword path:

- When a keyword rule matches and `generate_voice` is true, synthesize the rule reply with the anchor voice and enqueue it for digital-human insertion.
- Text injection/comment behavior remains unchanged.

`ui_pages/keyword_reply.py`:

- Keep the existing per-rule voice switch.
- No new advanced segmentation controls in the initial UI.

## Error Handling

- Anchor synthesis fails: existing digital-human start should fail with a clear voice-provider message.
- Segmentation fails: fall back to the full anchor WAV.
- Keyword synthesis fails: skip that insertion and keep text reply/comment behavior.
- Scheduler queue is full: drop the newest insertion and log a rate-limit message.
- LiveTalking upload fails during normal playback: surface digital-human runtime error.
- LiveTalking upload fails during insertion: skip the insertion and resume normal playback if possible.
- Local provider unavailable: provider validation fails without affecting Aliyun provider settings.

## Testing

Focused tests should cover:

- Silence segmentation returns ordered segment paths for a synthetic WAV.
- Segmentation fallback when no silence is detected.
- Scheduler plays normal segments in order.
- Scheduler inserts keyword audio after the current normal segment, not immediately.
- Scheduler resumes normal loop after insertion.
- Keyword rules with `generate_voice=false` do not enqueue insertion audio.
- Keyword synthesis failure does not break text reply flow.
- Voice provider settings preserve Aliyun as default and accept a future local provider entry.

## Acceptance Criteria

- A running LiveTalking stream continues to loop anchor speech.
- Keyword rules with voice enabled synthesize a keyword reply using the anchor voice.
- The keyword audio plays after the current short anchor segment finishes.
- The digital human lip-syncs to the inserted keyword audio.
- After insertion, the stream returns to the normal anchor script loop.
- The app remains usable if segmentation fails, keyword synthesis fails, or the local provider is not configured.

