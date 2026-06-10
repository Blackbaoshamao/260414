# Live Copilot AI Control Refactor Plan

> **For agentic workers:** Use this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Aiszr AI configuration from emotional-companion persona settings into live-stream copilot/control settings backed by DeepSeek, per-session user memory, cooldown policy, and template packs.

**Architecture:** Keep the existing DeepSeek model configuration and `AIReplyEngine` message path. Replace persona fields with structured live-control template settings, add an ephemeral session-memory SQLite store, and let the application decide reply eligibility while DeepSeek only generates bounded reply text. Keyword replies remain higher priority and unchanged.

**Tech Stack:** Python 3.10, PyQt5/PyQt-SiliconUI, asyncio, sqlite3, pytest.

---

## File Structure

- Modify `Aiszr/ai_reply.py`: structured live-control config, prompt builder, cooldowns, memory-aware generation.
- Create `Aiszr/live_control_config.py`: template/default config normalization.
- Create `Aiszr/live_session_memory.py`: per-session SQLite memory store with clear-on-end support.
- Modify `Aiszr/ui_pages/aiconfigpage.py`: live-control template UI and settings.
- Modify `Aiszr/ui.py`: load/save new settings, pass config to worker, clear memory on live end.
- Add tests under `Aiszr/tests/`.

---

### Task 1: Config Model

- [x] Add live-control template dataclass and settings normalization.
- [x] Preserve backward compatibility with legacy persona settings where possible.
- [x] Add tests for defaults, template switching data, and persisted active template.

### Task 2: Session Memory

- [x] Implement SQLite-backed per-session user memory fields.
- [x] Track username, platform, first seen, last message, message count, last reply, preferences/issues, welcomed flag, explained topics.
- [x] Add explicit clear method for ending/stopping live sessions.
- [x] Add tests for upsert, reply update, prompt formatting, and clear.

### Task 3: AI Control Logic

- [x] Extend AIConfig with live-control settings: reply char limit, per-user cooldown, global cooldown, tone style, @ behavior, voice reply.
- [x] Use the session memory in prompt construction and after replies.
- [x] Keep reply decision/cooldown in application code, not DeepSeek.
- [x] Make WeChat text replies include @ while voice replies do not.

### Task 4: UI

- [x] Replace persona section with live copilot/control template management.
- [x] Add template create/delete/switch/save actions.
- [x] Add fields for product info, host/copilot persona, after-sales policy, forbidden commitments, reply boundaries, platform rules, FAQ.
- [x] Add adjustable reply settings.
- [x] Keep model configuration and overall visual style.

### Task 5: Integration And Tests

- [x] Wire saved settings into `AIReplyEngine`.
- [x] Clear session memory when live capture, WeChat capture, digital-human stream, or session are stopped.
- [x] Add targeted unit tests.
- [x] Run relevant test suite.
