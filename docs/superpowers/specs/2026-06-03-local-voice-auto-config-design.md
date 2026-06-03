# Local Voice Auto Configuration Design

Date: 2026-06-03

## Goal

Aiszr should ship with a bundled GPT-SoVITS local voice runtime so users can enable AI voice replies without manually configuring voice API fields. The app should default to local voice, let users choose one cloned anchor timbre from the existing voice library, and use that same timbre for anchor-script speech and keyword-triggered speech insertions.

After the app is launched, all normal user operations for this flow must happen inside Aiszr. Users should not need to open a terminal, manually start GPT-SoVITS, edit config files, or paste local voice API parameters.

## Non-Goals

- Do not remove Aliyun Bailian support; keep it available for advanced or fallback use.
- Do not expose GPT-SoVITS endpoint, prompt language, text language, or reference-audio API fields in the normal UI.
- Do not change the LiveTalking lip-sync model, avatar preprocessing, or OBS push flow.
- Do not make per-keyword voice selection in this iteration.

## User-Facing Behavior

The default packaged experience is:

1. User opens the app.
2. User opens the AI voice/digital-human feature.
3. The app checks whether GPT-SoVITS is listening on the default local endpoint.
4. If it is not running, the app starts the bundled GPT-SoVITS service.
5. The user selects one anchor timbre from the cloned voice dropdown.
6. Anchor-script speech and keyword voice insertions both use the selected timbre.

The GPT-SoVITS API configuration fields are hidden in the normal UI. Users should not need to type endpoint, reference-audio path, prompt text, prompt language, or synthesis language.

The complete operational loop must be available in-app:

- Create or import a cloned anchor timbre.
- Select the timbre from the anchor voice dropdown.
- Generate the anchor script audio.
- Start the LiveTalking digital-human stream.
- Enable keyword voice replies.
- Generate and insert keyword reply speech into the running stream.

## Bundled GPT-SoVITS Runtime

The packaged app should include a `GPT-SoVITS` runtime directory beside the executable or under a known bundled dependency directory. Path resolution follows the existing `app_paths.app_dir()` pattern:

- Development: resolve relative to the Aiszr source directory or a configured external dependency root.
- PyInstaller onefile/onedir: resolve relative to the executable directory.

The app should manage the local service through a small runtime manager, tentatively `LocalVoiceRuntime`:

- Detect `http://127.0.0.1:9880` before starting a new process.
- Start `api_v2.py` from the bundled GPT-SoVITS directory when needed.
- Use `GPT_SOVITS_ENDPOINT` only as an advanced override.
- Wait for a health-ready state before synthesis.
- Stop only the child process it started when the app exits.

The default endpoint is `http://127.0.0.1:9880`. The provider still posts synthesis requests to `/tts`.

## Default Voice Settings

Default voice settings change to:

- `provider`: `local_voice`
- `model_id`: `gpt-sovits-v2`
- `api.local_voice.endpoint`: `http://127.0.0.1:9880`
- `api.local_voice.prompt_lang`: `zh`
- `api.local_voice.text_lang`: `zh`

`api.local_voice.reference_audio` should not be manually edited in the normal UI. The app should derive the reference audio from the selected cloned timbre.

Existing user settings should still load safely:

- If a user already has Aliyun configured, the saved provider remains respected.
- New installs and missing voice settings default to GPT-SoVITS.
- Unknown or invalid providers fall back to GPT-SoVITS, not Aliyun.

## Timbre Selection

The app already stores cloned voices in `VoiceSettings.voices`. A cloned voice has:

- Display name.
- Local sample WAV path.
- Provider-specific clone identity.
- Clone status.

For GPT-SoVITS, the clone identity is the resolved local WAV path. That makes the timbre reusable for local synthesis as long as the WAV exists.

The UI should keep one global anchor timbre dropdown:

- It appears in the existing anchor voice section.
- It lists cloned voices usable by the current provider.
- It controls both anchor-script speech and keyword-triggered speech insertion.
- If no timbre is selected, the app may select the most recent ready anchor timbre.
- If no usable timbre exists, synthesis fails with a direct message telling the user to clone or import an anchor voice.

The app should not mix provider-specific voice identities:

- Aliyun voices use cloud `voice_id` values and are not usable by GPT-SoVITS.
- GPT-SoVITS voices use local WAV references and are not usable by Aliyun.
- The voice library may show all records, but provider-specific synthesis should only use compatible voices.

## Keyword Insertion Flow

The existing keyword voice insertion flow remains unchanged at the scheduler level:

1. Generate the full anchor-script WAV using the selected local voice.
2. Split the full WAV into short segments.
3. Send segments to LiveTalking in order.
4. When a keyword rule with voice enabled matches, synthesize the rule reply with the same selected anchor timbre.
5. Enqueue the generated WAV as an insertion.
6. The scheduler waits for the current short anchor segment to finish.
7. LiveTalking receives the inserted WAV and lip-syncs it.
8. The scheduler returns to the normal anchor segment loop.

This keeps the mouth movement tied to the actual inserted audio without rewriting the lip-sync logic.

## UI Simplification

Normal UI should hide local GPT-SoVITS internals:

- Hide local provider API fields in the AI voice API dialog.
- Hide local provider API fields in any inline voice configuration panel.
- Keep DeepSeek copywriting settings visible if they are still needed for script generation.
- Keep a concise status indicator such as `本地语音：启动中 / 已就绪 / 启动失败`.

Advanced fields may remain available behind a developer flag or environment variable later, but they are not part of the normal packaged experience.

## Error Handling

- Bundled runtime missing: show `本地语音运行文件缺失，请检查软件包`.
- Port already in use but healthy: reuse the existing service.
- Port already in use but unhealthy: report the port conflict and do not start a second service.
- GPT-SoVITS startup timeout: show startup failure and keep the app responsive.
- Selected timbre WAV missing: ask the user to re-clone or re-import that timbre.
- Synthesis failure: skip the keyword insertion or fail digital-human startup with the provider message, depending on whether the failure occurs during keyword insertion or anchor preparation.

## Testing

Focused tests should cover:

- New default `VoiceSettings` select `local_voice` and `gpt-sovits-v2`.
- Local provider config receives the default endpoint and Chinese language defaults.
- Saved Aliyun settings still load without being overwritten.
- Invalid provider names fall back to `local_voice`.
- Voice selection resolves GPT-SoVITS timbre from the selected voice WAV.
- Aliyun cloud voice IDs are not treated as GPT-SoVITS-compatible local timbres.
- Local runtime manager reuses an already healthy service.
- Local runtime manager starts the bundled process when no service is running.
- UI field metadata hides normal GPT-SoVITS API fields while keeping DeepSeek copywriting settings visible.

## Acceptance Criteria

- Fresh packaged app launches with GPT-SoVITS as the default voice provider.
- User does not need to fill endpoint, reference audio, prompt language, or text language.
- User does not need to run any command-line service to use local voice in the packaged app.
- User can choose one cloned anchor timbre from a dropdown.
- Anchor script and keyword insertion use the selected timbre.
- Keyword insertion still waits for the current short anchor segment to finish and then lip-syncs through LiveTalking.
- Existing Aliyun users can still load and use saved Aliyun settings.
