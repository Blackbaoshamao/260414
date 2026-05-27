<!-- GSD:project-start source:PROJECT.md -->
## Project

**Aiszr — AI 数字人直播助手**

Aiszr 是一个本地运行的抖音直播助手，围绕“稳定抓弹幕 -> AI 文本回复 -> OBS 动作联动 -> AI 语音播报”构建完整闭环，服务数字人直播与卖货直播场景。

**Core Value:** 稳定、低漏抓地获取直播互动信息，并在不打断直播节奏的前提下完成智能互动与动作联动。

### 当前目标（2026-05-11）

1. Phase 6 端到端验证——长时运行稳定性压测、漏抓率观测。
2. Phase 7 数字人 RTMP 推流——绿幕视频 + TTS 合成 + RTMP 推流到 OBS（调研完成，待规划）。
3. 语音链路生产化——缓存策略、失败重试、成本控制。

### 当前进度（估算）

- **核心链路完成度**（抓取/解码/UI/AI/OBS）：约 **90%**
- **整体产品路线完成度**（含真实云 TTS、场控、多房间）：约 **72%**

### 软件功能全览

#### 已可用

- 抖音登录与会话持久化（Playwright persistent context、扫码登录、失效检测、重登清理）。
- 弹幕主链路抓取（WebSocket 拦截、ACK、断线重连、DOM 补偿、WS/DOM 去重、噪声过滤）。
- 协议解码补强（`chat/gift/like/follow/enter/stats` + 方法统计 + 未知方法观测 + 失败样本日志）。
- 直播页能力（连接状态、弹幕显示、消息类型勾选过滤、AI 回复区与状态栏）。
- AI 回复引擎（DeepSeek 兼容接口、冷却节流、用户冷却、短历史记忆、待回复队列、规则降级）。
- OBS 联动（连接检测、规则库、关键词命中阈值/窗口、播放期忽略、全局冷却、播完切回主场景）。
- AI 语音页（供应商与模型切换、凭据字段动态显示、15s WAV 校验上传、主播/助播角色管理、自动播报开关）。
- 语音任务管线（`AIReplyEngine -> TTSWorker -> VoiceManager -> 本地播放` 已串通）。
- 主题系统与设置持久化（多主题切换、控件联动刷新、配置保存/重置）。

#### 进行中

- Phase 6 稳定性观测——plans 已完成，端到端验证进行中（漏抓率、重连抖动、异常噪声）。
- Phase 7 数字人 RTMP 推流——调研完成（07-RESEARCH.md），待规划与执行。

#### 计划中

- 数字人 RTMP 推流（绿幕视频 + TTS 合成 → RTMP → OBS，Phase 7）。
- 巨量百应登录与文字场控发送（Phase 8）。
- 多房间管理与并发策略（Phase 8）。
- 三家语音供应商真实 API 合成与克隆（Phase 8）。

### Constraints

- **Tech Stack**: Python 3.10 + Playwright — 用户明确指定
- **Desktop UI**: PyQt5 + PyQt-SiliconUI（当前主界面）
- **Runtime**: 本地运行优先 — 不依赖云服务器
- **Login**: 手动扫码 — 抖音登录方式决定，无法自动化
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Framework
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Python | 3.10.x | Runtime | Project constraint. 3.10 has stable pattern matching, `ParamSpec`, union types via `\|`. Wide library support. | HIGH (user-specified) |
| Playwright | 1.58.0 | Browser automation + WebSocket interception | User-specified. Async-first (`async_api`), native WebSocket frame interception via `page.on("websocket")` + `ws.on("framereceived")`. No JS reverse-engineering needed. | HIGH (verified PyPI Jan 30, 2026) |
| FastAPI | 0.135.x | Web server + REST API + WebSocket push | Async-native, native WebSocket support, automatic OpenAPI docs, type validation via Pydantic. The standard Python async web framework in 2025-2026. | HIGH (verified PyPI Apr 1, 2026) |
| Uvicorn | 0.34.x | ASGI server for FastAPI | Default ASGI server for FastAPI. Included in `fastapi[standard]`. Production-ready with `uvloop` on supported platforms. | HIGH |
### Data Layer
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| protobuf (google) | 5.29.x | Deserialize Douyin WebSocket binary frames | Douyin live streams use Protobuf-encoded messages (`PushFrame` -> `Response` -> `ChatMessage` etc). Official google protobuf library. Use 5.x series (not 7.x which is the new upb-based runtime and may have compat issues). | HIGH (verified PyPI) |
| gzip (stdlib) | stdlib | Decompress WebSocket payloads | Douyin's PushFrame.payload is gzip-compressed. No external dependency needed. | HIGH |
| SQLite via aiosqlite | 0.21.x | Persist danmaku history for search/filter | Zero-config embedded database. Perfect for local tool. `aiosqlite` provides async interface matching the async architecture. | HIGH |
### Frontend
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| Vue 3 | 3.5.x | UI framework | Smallest bundle for a reactive dashboard (33KB gzipped). Composition API is clean and well-typed. Easier learning curve than React for a small project. Better ecosystem for Chinese-language docs and community support. | HIGH (verified npm 3.5.32) |
| Vite | 6.x | Build tool + dev server | Instant HMR, fast builds. Standard for Vue 3 projects. `npm create vue@latest` scaffolds everything. | HIGH |
| Native WebSocket API | browser | Receive real-time danmaku from FastAPI | No library needed. Browser-native `new WebSocket(url)`. Keeps frontend minimal. | HIGH |
### Supporting Libraries
| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| Pydantic | 2.x (bundled with FastAPI) | Data validation, settings management | Define config models, WebSocket message schemas. | HIGH |
| asyncio | stdlib | Event loop coordination | Core of the entire async architecture. Playwright async_api and FastAPI both run on asyncio. | HIGH |
| loguru | 0.7.x | Structured logging | Better DX than stdlib logging. Rotation, retention, colorized output. Use for all application logging. | MEDIUM |
| httpx | 0.28.x | Async HTTP client | If needed for fetching Douyin room info (room_id, ttwid) before Playwright opens the page. FastAPI already depends on httpx. | HIGH |
## Critical Architecture Decision: Playwright Interception vs Direct WebSocket
### Recommended: Playwright WebSocket Interception
### What NOT to use: Direct WebSocket (websocket-client)
- Requires maintaining JS reverse-engineering of Douyin's `sign()` function
- Douyin updates their obfuscation every few months, breaking direct connections
- Projects like `DySpider`, `douyinLive` that use this approach frequently have issues opened about "connection fails after update"
- Login/cookie management becomes manual HTTP-level work
- The user chose Playwright specifically to avoid this pain
## What NOT to Use
| Category | Recommended | Do NOT Use | Why Not |
|----------|-------------|------------|---------|
| Browser automation | Playwright | Selenium | User already decided. Selenium has worse async support, no native WebSocket interception API. |
| Web framework | FastAPI | Flask | Flask is synchronous. This project needs async WebSocket support throughout. Flask-SocketIO adds complexity without benefit. |
| Web framework | FastAPI | Django | Massive overkill for a simple WebSocket + REST server. Django's async support is bolted on. |
| Frontend | Vue 3 | React | Smaller bundle, simpler mental model for a dashboard. No need for React's complexity. |
| Frontend | Vue 3 | Svelte | While Svelte is lighter, Vue has much better Chinese-language ecosystem/docs which matters for this Chinese-market tool. Vue's Composition API is nearly as clean. |
| Frontend | Vue 3 | Vanilla JS | The search/filter state management, tab system, and real-time updates warrant a reactive framework. Vanilla would become spaghetti quickly. |
| Protobuf | protobuf 5.x | protobuf 4.x | 4.25.x reached end-of-life Q1 2025. Use 5.x or later. |
| Protobuf | protobuf 5.x | protobuf 7.x | 7.x (released Mar 2026) uses the new upb kernel and may have edge-case issues. 5.x is battle-tested for this exact use case. |
| Database | SQLite | PostgreSQL/MySQL | Local tool, single user. No need for a database server. SQLite handles 100K+ rows of danmaku easily. |
| Database | SQLite | Redis | Redis is for caching/pub-sub, not primary storage. Unnecessary complexity for a local tool. |
| WebSocket client | Playwright interception | websocket-client | websocket-client is synchronous. Requires reverse-engineering Douyin's auth. |
| WebSocket client | Playwright interception | websockets (async lib) | Would need to reverse-engineer Douyin's WSS URL + signature. Playwright avoids this entirely. |
## Installation
# Core
# Playwright browser binaries ( Chromium only, to save space)
# Frontend (run in frontend/ directory)
## Dev Dependencies
# Backend
# Frontend (via npm)
# Vite, Vue TSC, ESLint -- all scaffolded by create-vue
## Sources
- [Playwright PyPI](https://pypi.org/project/playwright/) -- version 1.58.0, Jan 30, 2026
- [Playwright WebSocket API](https://playwright.dev/python/docs/api/class-websocket) -- official docs for frame interception
- [Playwright WebSocketRoute API](https://playwright.dev/python/docs/api/class-websocketroute) -- route_web_socket() for mocking/routing
- [FastAPI PyPI](https://pypi.org/project/fastapi/) -- version 0.135.3, Apr 1, 2026
- [protobuf PyPI](https://pypi.org/project/protobuf/) -- version 7.34.1 (latest), recommend 5.x series
- [Vue.js npm](https://www.npmjs.com/package/vue) -- version 3.5.32
- [zhonghangAlex/DySpider](https://github.com/zhonghangAlex/DySpider) -- reference for Protobuf message structures (PushFrame, Response, ChatMessage, User)
- [HaoDong108/DouyinBarrageGrab](https://github.com/HaoDong108/DouyinBarrageGrab) -- reference for Douyin WSS protocol, polling fallback, message types
- [saermart/DouyinLiveWebFetcher](https://github.com/saermart/DouyinLiveWebFetcher) -- 2025 active project, direct WSS approach
- [Hans Anderson -- Intercepting Sockets with Playwright](https://hansanderson.com/blog/playwright-intercept-sockets/) -- practical 2025 guide
- [Artiely -- WebSocket interception approaches](http://artiely.com/posts/2022/2022-12-20-ws/index.html) -- comprehensive comparison of browser automation vs script replacement vs direct protocol
- [protobuf version support](https://protobuf.dev/support/version-support/) -- EOL dates for protobuf versions
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

## Auto Skill Triggers

When the following conditions are detected, automatically invoke the corresponding skill without waiting for the user to type the command.

| Condition | Skill | Notes |
|-----------|-------|-------|
| User asks to debug or investigate a bug | `/gsd-debug` | Structured debugging with persistent state |
| Non-trivial code edits completed (not typo/single-line) | `/simplify` | Auto-review for reuse, quality, efficiency |
| User asks to ship / create PR / prepare for merge | `/gsd-ship` | PR creation + review + merge prep |
| Touching auth, cookie, credential, or security-sensitive code | `/security-review` | OWASP-aligned security audit |
| User wants TDD, says "red-green-refactor", test-first development | `tdd` | Test-driven development loop |
| User reports a bug / issue, wants to investigate root cause | `triage-issue` | Root cause analysis + GitHub issue with TDD fix plan |
| User wants to file bugs conversationally, do QA session | `qa` | Interactive QA → GitHub issues |
| User wants to design an API, compare module interfaces | `design-an-interface` | Parallel sub-agents generate radically different designs |
| User wants to stress-test a plan, says "grill me" | `grill-me` | Relentless design interview |
| User wants to improve architecture, find refactoring candidates | `improve-codebase-architecture` | Deepening opportunities via module analysis |
| User wants to plan a refactor, break into tiny commits | `request-refactor-plan` | Detailed refactor plan → GitHub issue |
| User wants to convert plan/spec into GitHub issues | `to-issues` | Vertical slice issue breakdown |
| User wants to create a PRD from conversation context | `to-prd` | Synthesize PRD → GitHub issue |
| User wants to edit, revise, improve an article draft | `edit-article` | Section restructuring + prose tightening |
| User wants to set up pre-commit hooks (Husky/lint-staged) | `setup-pre-commit` | Automated hook configuration |
| User wants to set up git safety guardrails in Claude Code | `git-guardrails-claude-code` | Block destructive git commands |
| User wants to create/write/build a new skill | `write-a-skill` | Skill scaffolding + review |
| User wants to scaffold exercises, create course sections | `scaffold-exercises` | Exercise directory structures |
| User wants to search/create notes in Obsidian vault | `obsidian-vault` | Obsidian vault management |
| User mentions shoehorn, replacing `as` in tests | `migrate-to-shoehorn` | TypeScript test migration |
| User says "caveman mode", "less tokens", "be brief" | `caveman` | Ultra-compressed communication |

### Explicit-only Skills (no auto-trigger)

These skills have `disable-model-invocation: true` and must be invoked explicitly by the user:

| Skill | Trigger |
|-------|---------|
| `domain-model` | User explicitly invokes to stress-test plan against domain language |
| `ubiquitous-language` | User explicitly invokes to build DDD glossary |
| `zoom-out` | User explicitly invokes when unfamiliar with code and needs high-level map |

### GitHub Triage (interactive only)

`github-triage` — invoked via `/github-triage` for interactive issue triage via label state machine.

**Exceptions — do NOT auto-trigger:**
- Trivial changes (typo, single-line, comment-only)
- User explicitly said to bypass or skip review
- Already inside a GSD workflow that covers the same scope

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

---
## Karpathy-Inspired Coding Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. **Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---
**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

*Source: [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills)*
