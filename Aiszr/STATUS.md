# Aiszr 项目状态面板

> **一站式入口**：项目当前状态、进行中的工作、关键文档索引。
> 详细 GSD 状态见 [.planning/STATE.md](.planning/STATE.md)；Phase 规划见 [.planning/ROADMAP.md](.planning/ROADMAP.md)。
> **持续更新**：会话里有重要变化（决策、分支切换、里程碑、踩坑教训）就在「更新日志」最上方追加一行。

---

## 项目定位（2026-05-12 Pivot 后）

**Aiszr — AI 数字人直播带货平台**（视频号方向）

主路径：
```
AI 话术  →  TTS 音频  →  绿幕视频合成  →  ffmpeg HLS 推流  →  OBS  →  视频号
```

> 抖音弹幕助手 + OBS 关键词联动 + AI 回复引擎 **全部保留**，作为侧栏功能，**不再是主流程**。

---

## 当前进度

| 项 | 状态 |
|----|------|
| 总体进度 | **85%**（5/8 phases 完成） |
| Phase 6 — 稳定性与观测 | 🔨 计划已完成，等待端到端验证 |
| Phase 7 — 数字人 RTMP | 📌 调研完成（[07-RESEARCH.md](.planning/phases/07-digital-human-rtmp/07-RESEARCH.md)），待规划 |
| Phase 8 — 二期能力扩展 | ⏳ 计划中（云语音 / 百应场控 / 多房间） |
| 当前分支 | `feat/ui-sonoma-redesign` |
| Sonoma 重设计 | ✅ P5 完成（6 个对话框迁移到 Mac* 组件） |

---

## 进行中的工作（feat/ui-sonoma-redesign）

### 已完成
- P1：themes 7→3 + 设计 tokens + Inter 字体级联
- P2：新 [ui_components.py](ui_components.py)（Mac* 系列组件）
- P3：HomePage 迁移 MacCard / MacButton
- P3.1：滚动条 / spin 箭头 / theme 热切换 / 按钮对比度修复
- P4a-d：4 个二级页面迁移
- P4e：voiceconfigpage 按钮 + thumb-card 修复
- P4.1：返回按钮 / OBS 卡片高度 / combo 宽度 / 次级对比度 / gallery 对齐
- P5：6 个对话框迁移到 Mac* 组件

### 最近会话（2026-05-19）的零散打磨
- 首页音频设备：WASAPI host API 优先 + WM_DEVICECHANGE 热插拔自动刷新
- LiveRoomPage：返回按钮删除、"启用直播抓取"改"启动"
- LiveRoomPage：直播间 ID 输入框前加抖音/微信图标切换器（**仅 UI placeholder**，后端未接）

### 待处理（按优先级）
- P6（如有规划）— UI 重设计收尾
- Phase 6 端到端验证 — 长时运行稳定性、漏抓率观测
- Phase 7 规划 — 数字人 RTMP 推流路径
- 抓取源切换器接通 wechat_capture 后端 — **Phase 9 已规划** (.planning/phases/09-wechat-source-wiring/), 4 plans / 4 waves, 待执行

---

## 关键决策与教训

### 硬性禁区
- **不可修改** [capture.py](capture.py) / [decoder.py](decoder.py) / [proto_defs.py](proto_defs.py) — 抓取层冻结，所有过滤逻辑放 AI / UI 层
- AI 回复**不加 conversation history** — 每次只发当前消息，否则越写越长
- "神秘观众"过滤放在 [ai_reply.py](ai_reply.py) `process_message()`，不能回退到 capture 层

### 技术决策
- **PyAudio host API 偏好顺序**：WASAPI > DirectSound > MME（MME 会列虚拟 Sound Mapper 误导用户）→ [homepage.py `_pick_preferred_host`](ui_pages/homepage.py)
- **音频热插拔**：QAbstractNativeEventFilter 监听 WM_DEVICECHANGE + 250ms 防抖 + 按设备名重选
- **关闭程序停音频**：winsound `SND_ASYNC` 播放 + `stop_all_audio()` on close
- **OBS 联动设置 ≠ 推流 OBS 设置** — 已解耦，避免推流设置被自动同步
- **HLS 端口固定 8780** — 推流稳定性所需

### 产品妥协
- 数字人**无真对口型**：预渲染绿幕循环 + 助播插话时数字人不动嘴
- 阿里百炼是唯一可用 provider — 火山 / 腾讯空壳保留

---

## 已知未修

1. **AnchorSettingsDialog 自起 event loop 线程** — 关闭对话框时合成中的线程可能挂着（潜伏 bug）
2. **settings.json 无写锁** — 多并发保存可能写入丢失
3. **视频号弹幕抓取技术不确定** — 无开源参考实现，用户已知风险

---

## 文档索引

| 路径 | 用途 | 何时更新 |
|------|------|----------|
| [STATUS.md](STATUS.md)（本文件） | 一站式状态入口，给人看 | 每次会话有重要变化 |
| [CLAUDE.md](CLAUDE.md) | Claude 项目配置（GSD、Karpathy 原则、auto-skill 触发） | 极少 |
| [.planning/PROJECT.md](.planning/PROJECT.md) | 原始项目定位（pivot 前版本，已过时） | milestone 边界 |
| [.planning/ROADMAP.md](.planning/ROADMAP.md) | Phase 1-8 目标 / 产出物 / 状态 | phase 转换时 |
| [.planning/STATE.md](.planning/STATE.md) | GSD 工作流详细状态 + Pivot 详情 + 22 commits 摘要 | GSD 工具自动 |
| [.planning/REQUIREMENTS.md](.planning/REQUIREMENTS.md) | 需求清单 | milestone 边界 |
| [.planning/phases/](.planning/phases/) | 各 phase 的 PLAN / RESEARCH / VALIDATION 等 | phase 执行时 |
| [.planning/notes/digital-human-rtmp-architecture.md](.planning/notes/digital-human-rtmp-architecture.md) | Phase 7 架构笔记 | 手动 |
| `~/.claude/projects/d--Pjt/memory/` | Claude 跨会话长期记忆（用户偏好、踩坑教训、placeholder 跟踪） | 会话中持续 |

> **注**：[.planning/PROJECT.md](.planning/PROJECT.md) 的"What This Is"段还停留在 pivot 前的"抖音弹幕助手"描述。需要更新时，新方向以 [STATE.md](.planning/STATE.md) 为准。

---

## 更新日志

> 新条目追加在**最上方**。每条一行：`日期 — 摘要（关联文件 / commit / memory）`。

- **2026-05-19** — Phase 9 已规划：多平台抓取源 — 视频号弹幕接通（4 plans / 4 waves, .planning/phases/09-wechat-source-wiring/）
- **2026-05-19** — 创建 STATUS.md 作为主面板；清理外层 .planning/ 残留与重复 skills-reference.md
- **2026-05-19** — 首页音频检测：WASAPI 偏好 + 热插拔自动刷新（[homepage.py](ui_pages/homepage.py)）；LiveRoomPage 删返回按钮 + 文案 + 抖音/微信切换器（仅 UI）
- **2026-05-12** — **重大 Pivot**：从抖音弹幕助手转向视频号数字人直播带货平台（详见 [STATE.md Pivot 段](.planning/STATE.md)）
- **2026-04-30** — Phase 5-SUP 完成：语音克隆修复 + 回归测试
- **2026-04-17** — fetcher 切换 launch_persistent_context、ui DOM 观察器、decoder 噪音过滤增强
