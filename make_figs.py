"""Generate all flowchart / architecture diagrams used in the thesis.

Outputs:
  fig4_1_architecture.png        -- 系统总体架构图
  fig4_2_capture_flow.png        -- 弹幕采集与双通道融合流程图
  fig4_3_ai_decision.png         -- AI 回复决策流程图
  fig4_4_module_deps.png         -- 模块依赖关系图 (NEW)
  fig5_1_login_flow.png          -- 登录与弹幕抓取实现流程图
  fig5_3_protocol_decode.png     -- 协议解码与消息处理流程图
  fig5_5_dh_pipeline.png         -- 数字人推流流水线图
  fig5_7_obs_action.png          -- OBS 关键词触发动作时序 (NEW)
  fig5_8_metrics.png             -- 系统指标观测示例 (NEW)
  fig6_1_perf.png                -- 性能测试结果对比图 (NEW, ch6)
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.font_manager as fm

# Find a Chinese-capable font on Windows
CHINESE_FONTS = [
    "Microsoft YaHei",
    "SimSun",
    "SimHei",
    "Microsoft JhengHei",
    "Arial Unicode MS",
]
available = {f.name for f in fm.fontManager.ttflist}
font_name = next((c for c in CHINESE_FONTS if c in available), "SimHei")
plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False

OUT = r"d:\Pjt\260414\thesis_figs"
os.makedirs(OUT, exist_ok=True)

# Common style helpers ----------------------------------------------------

C_BG = "#FFFFFF"
C_BOX_PRIMARY = "#E3F2FD"   # 蓝
C_BOX_ACCENT  = "#FFF3E0"   # 橙
C_BOX_SOFT    = "#F3E5F5"   # 紫
C_BOX_OK      = "#E8F5E9"   # 绿
C_BOX_DECISION= "#FFFDE7"   # 黄
C_TEXT        = "#212121"
C_LINE        = "#37474F"

def box(ax, x, y, w, h, text, fc=C_BOX_PRIMARY, fontsize=10, weight="normal"):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                       linewidth=1.2, edgecolor=C_LINE, facecolor=fc)
    ax.add_patch(p)
    ax.text(x + w/2, y + h/2, text, ha="center", va="center",
            color=C_TEXT, fontsize=fontsize, weight=weight, wrap=True)
    return (x, y, w, h)

def diamond(ax, x, y, w, h, text, fc=C_BOX_DECISION, fontsize=9):
    cx, cy = x + w/2, y + h/2
    pts = [(cx, y+h), (x+w, cy), (cx, y), (x, cy)]
    poly = plt.Polygon(pts, closed=True, fc=fc, ec=C_LINE, lw=1.2)
    ax.add_patch(poly)
    ax.text(cx, cy, text, ha="center", va="center",
            color=C_TEXT, fontsize=fontsize, wrap=True)
    return (x, y, w, h)

def arrow(ax, src, dst, label=None, style="-|>", lw=1.4, color=None,
          rad=0.0, label_off=(0,0), label_fs=8):
    a = FancyArrowPatch(src, dst, arrowstyle=style, lw=lw,
                        color=color or C_LINE,
                        mutation_scale=14,
                        connectionstyle=f"arc3,rad={rad}")
    ax.add_patch(a)
    if label:
        mx = (src[0]+dst[0])/2 + label_off[0]
        my = (src[1]+dst[1])/2 + label_off[1]
        ax.text(mx, my, label, fontsize=label_fs, ha="center", va="center",
                color="#37474F",
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.9))

def setup(ax, w, h):
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.set_aspect("equal")
    ax.axis("off")

def save(fig, name, dpi=180):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved {path}")

# -----------------------------------------------------------------------
# fig4_1 — 系统总体架构图 (4-layer architecture)
# -----------------------------------------------------------------------
def fig4_1():
    fig, ax = plt.subplots(figsize=(11, 7))
    W, H = 22, 14
    setup(ax, W, H)

    # Layer bands
    layer_h = 2.7
    bands = [
        (10.6, "用户界面层 (PyQt5 + PyQt-SiliconUI)", "#E3F2FD"),
        (7.6,  "业务逻辑层 (CaptureWorker / asyncio)", "#FFF8E1"),
        (4.6,  "数据采集层 (Playwright + Protobuf)", "#E8F5E9"),
        (1.6,  "外部服务层 (LLM / TTS / OBS / ffmpeg)", "#FCE4EC"),
    ]
    for y, name, fc in bands:
        ax.add_patch(FancyBboxPatch((0.4, y), W-0.8, layer_h,
            boxstyle="round,pad=0.02,rounding_size=0.15",
            ec=C_LINE, fc=fc, lw=1, alpha=0.55))
        ax.text(0.9, y + layer_h - 0.45, name, fontsize=11, weight="bold",
                color=C_LINE)

    # Row 1: UI pages
    pages = [("首页", 1.3), ("直播页", 4.3), ("AI 配置页", 7.3),
             ("语音页", 10.3), ("OBS 联动页", 13.3), ("数字人页", 16.6), ("通用设置", 19.6)]
    for name, x in pages:
        box(ax, x, 11.0, 2.4, 1.2, name, fc="#BBDEFB", fontsize=10)

    # Row 2: business logic — CaptureWorker as central hub
    box(ax, 8.6, 8.2, 4.8, 1.6, "CaptureWorker\n(asyncio 事件循环)", fc="#FFE082", fontsize=11, weight="bold")
    box(ax, 1.5, 8.5, 3.0, 1.1, "AIReplyEngine", fc="#FFECB3", fontsize=10)
    box(ax, 4.8, 8.5, 3.0, 1.1, "TTSWorker", fc="#FFECB3", fontsize=10)
    box(ax, 14.0, 8.5, 3.0, 1.1, "ObsActionCtrl", fc="#FFECB3", fontsize=10)
    box(ax, 17.4, 8.5, 3.5, 1.1, "DigitalHumanPipeline", fc="#FFECB3", fontsize=10)

    # Row 3: data capture
    box(ax, 3.5, 5.1, 4.5, 1.6, "RoomCapture\n(Playwright + DOM)", fc="#C8E6C9", fontsize=10)
    box(ax, 9.5, 5.1, 4.5, 1.6, "DanmakuDecoder\n(三层协议解码)", fc="#C8E6C9", fontsize=10)
    box(ax, 15.5, 5.1, 4.5, 1.6, "MemoryManager\n(JSONL 落盘)", fc="#C8E6C9", fontsize=10)

    # Row 4: external services
    box(ax, 1.4, 2.1, 3.6, 1.3, "DeepSeek\n(对话补全 API)", fc="#F8BBD0")
    box(ax, 5.4, 2.1, 3.6, 1.3, "阿里云百炼\n(语音克隆 / TTS)", fc="#F8BBD0")
    box(ax, 9.4, 2.1, 3.6, 1.3, "OBS WebSocket\n(场景控制 v5)", fc="#F8BBD0")
    box(ax, 13.4, 2.1, 3.6, 1.3, "ffmpeg\n(HLS 切片)", fc="#F8BBD0")
    box(ax, 17.4, 2.1, 3.6, 1.3, "本地 HTTP\n(切片访问)", fc="#F8BBD0")

    # Arrows: page -> worker
    for _, x in pages:
        arrow(ax, (x+1.2, 11.0), (11.0, 9.8), lw=0.9, color="#90A4AE", style="-")
    # worker -> business
    for cx in [3.0, 6.3, 15.5, 19.15]:
        arrow(ax, (11.0, 8.2), (cx, 9.6), lw=1.0, style="-")
    # business -> capture
    arrow(ax, (11.0, 8.2), (5.75, 6.7))
    arrow(ax, (11.0, 8.2), (11.75, 6.7))
    arrow(ax, (11.0, 8.2), (17.75, 6.7))
    # capture/business -> external
    arrow(ax, (3.0, 8.5), (3.2, 3.4), label="HTTPS", label_fs=7)
    arrow(ax, (6.3, 8.5), (7.2, 3.4), label="HTTPS", label_fs=7)
    arrow(ax, (15.5, 8.5), (11.2, 3.4), label="WSS", label_fs=7)
    arrow(ax, (19.15, 8.5), (15.2, 3.4), label="subprocess", label_fs=7)
    arrow(ax, (19.15, 8.5), (19.2, 3.4), label="HTTP", label_fs=7)
    # capture -> worker upward (data flow)
    arrow(ax, (5.75, 6.7), (10.0, 8.2), rad=0.15, color="#1976D2", lw=1.3,
          label="弹幕事件", label_off=(0, 0.25))
    arrow(ax, (11.75, 6.7), (11.5, 8.2), color="#1976D2", lw=1.3)
    save(fig, "fig4_1_architecture.png")

# -----------------------------------------------------------------------
# fig4_2 — 弹幕采集与双通道融合流程图
# -----------------------------------------------------------------------
def fig4_2():
    fig, ax = plt.subplots(figsize=(11, 7.5))
    W, H = 22, 15
    setup(ax, W, H)

    box(ax, 9, 13.3, 4, 1.2, "进入直播间页面", fc=C_BOX_PRIMARY, weight="bold")
    arrow(ax, (11, 13.3), (11, 12.4))
    # Split
    box(ax, 2.5, 10.6, 7, 1.6, "WebSocket 通道\npage.on('websocket') + framereceived",
        fc="#BBDEFB", fontsize=10)
    box(ax, 12.5, 10.6, 7, 1.6, "DOM 观察通道\nMutationObserver 注入脚本",
        fc="#C8E6C9", fontsize=10)
    arrow(ax, (11, 13.3), (6, 12.2), rad=-0.2)
    arrow(ax, (11, 13.3), (16, 12.2), rad=0.2)

    # WS path
    box(ax, 2.5, 8.4, 7, 1.4, "三层解码: PushFrame -> gzip -> Response", fc="#E1F5FE")
    arrow(ax, (6, 10.6), (6, 9.8))
    box(ax, 2.5, 6.4, 7, 1.4, "method 分派 (chat/gift/like/...)", fc="#E1F5FE")
    arrow(ax, (6, 8.4), (6, 7.8))

    # DOM path
    box(ax, 12.5, 8.4, 7, 1.4, "expose_binding 回传昵称+文本", fc="#DCEDC8")
    arrow(ax, (16, 10.6), (16, 9.8))
    box(ax, 12.5, 6.4, 7, 1.4, "提取 chat 签名 (昵称, 内容)", fc="#DCEDC8")
    arrow(ax, (16, 8.4), (16, 7.8))

    # Fusion
    box(ax, 6, 3.8, 10, 1.8,
        "双通道融合 (2s 签名窗口 / 3s 补全窗口)\n"
        "签名去重 -> WS 完整时直发, WS 缺字段时 DOM 补全",
        fc="#FFF3E0", weight="bold")
    arrow(ax, (6, 6.4), (9, 5.6), rad=-0.15)
    arrow(ax, (16, 6.4), (13, 5.6), rad=0.15)

    box(ax, 8, 1.4, 6, 1.4, "扁平化事件 -> UI / AI / 指标", fc=C_BOX_OK, weight="bold")
    arrow(ax, (11, 3.8), (11, 2.8))

    # Note about ACK
    ax.text(0.6, 11.0, "心跳 ACK", fontsize=8, color="#1976D2",
            bbox=dict(boxstyle="round", fc="#E3F2FD", ec="#90CAF9"))
    arrow(ax, (1.6, 10.9), (2.5, 11.3), color="#1976D2", lw=1.0)

    save(fig, "fig4_2_capture_flow.png")

# -----------------------------------------------------------------------
# fig4_3 — AI 回复决策流程图
# -----------------------------------------------------------------------
def fig4_3():
    fig, ax = plt.subplots(figsize=(10, 8.5))
    W, H = 20, 17
    setup(ax, W, H)

    box(ax, 7, 15.3, 6, 1.2, "新弹幕到达", fc=C_BOX_PRIMARY, weight="bold")
    arrow(ax, (10, 15.3), (10, 14.6))

    diamond(ax, 6.5, 13.0, 7, 1.6, "自动回复开关 / 屏蔽词?", fontsize=10)
    arrow(ax, (10, 13.0), (10, 12.3), label=" 通过", label_off=(0.6,0))
    arrow(ax, (13.5, 13.8), (18, 13.8), label="否", label_fs=8)
    box(ax, 17.5, 13.2, 2.3, 1.2, "丢弃", fc="#FFCDD2")

    diamond(ax, 6.5, 10.6, 7, 1.6, "全局节流 / 用户冷却?", fontsize=10)
    arrow(ax, (10, 12.2), (10, 10.6))
    arrow(ax, (13.5, 11.4), (18, 11.4), label="冷却中", label_fs=8)
    box(ax, 17.5, 10.8, 2.3, 1.2, "入待回\n复队列", fc="#FFE0B2")

    diamond(ax, 6.5, 8.2, 7, 1.6, "是否短弹幕?", fontsize=10)
    arrow(ax, (10, 9.8), (10, 8.2))
    arrow(ax, (13.5, 9.0), (18, 9.0), label="是", label_fs=8)
    box(ax, 17.5, 8.4, 2.3, 1.2, "推测意图\n附加提示", fc="#F3E5F5")
    arrow(ax, (18.65, 8.4), (10, 8.0), rad=-0.3, color="#9C27B0", lw=1.0)

    box(ax, 5.5, 6.2, 9, 1.4, "构造 messages = 人设系统提示 + 短历史 + 当前弹幕",
        fc=C_BOX_SOFT)
    arrow(ax, (10, 8.2), (10, 7.6))

    diamond(ax, 6, 3.9, 8, 1.7, "DeepSeek 可用且响应<超时?", fontsize=10)
    arrow(ax, (10, 6.2), (10, 5.6))

    box(ax, 1.0, 1.2, 7, 1.5, "规则降级\n关键词模板回复",
        fc="#FFCCBC", weight="bold")
    box(ax, 11.5, 1.2, 7, 1.5, "LLM 回复 -> 入 TTSWorker 队列\n更新冷却时间戳与短历史",
        fc=C_BOX_OK, weight="bold")
    arrow(ax, (6, 4.0), (4.5, 2.7), label="否/超时", label_fs=8)
    arrow(ax, (14, 4.0), (15, 2.7), label="是", label_fs=8)

    save(fig, "fig4_3_ai_decision.png")

# -----------------------------------------------------------------------
# fig4_4 — 模块依赖关系图 (NEW)
# -----------------------------------------------------------------------
def fig4_4():
    fig, ax = plt.subplots(figsize=(11, 6.5))
    W, H = 22, 13
    setup(ax, W, H)

    nodes = {
        "ui":            (10, 11.2, 4, 1.2, "ui.py (页面与对话框)", "#BBDEFB"),
        "worker":        (10, 8.8,  4, 1.2, "CaptureWorker", "#FFE082"),
        "capture":       (1.2, 6.2, 3.6, 1.2, "RoomCapture", "#C8E6C9"),
        "decoder":       (5.3, 6.2, 3.6, 1.2, "DanmakuDecoder", "#C8E6C9"),
        "ai":            (9.4, 6.2, 3.6, 1.2, "AIReplyEngine", "#FFECB3"),
        "obs":           (13.5, 6.2, 3.6, 1.2, "ObsActionCtrl", "#FFECB3"),
        "tts":           (17.6, 6.2, 3.6, 1.2, "TTSWorker", "#FFECB3"),
        "voice_mgr":     (3.3, 3.5, 3.6, 1.2, "VoiceManager", "#F8BBD0"),
        "voice_models":  (7.6, 3.5, 3.6, 1.2, "VoiceModels", "#F8BBD0"),
        "memory":        (11.9, 3.5, 3.6, 1.2, "MemoryManager", "#F8BBD0"),
        "dh":            (16.2, 3.5, 3.6, 1.2, "DigitalHumanPipeline", "#F8BBD0"),
        "rt_cfg":        (6.5, 0.9, 4.2, 1.2, "runtime_config", "#ECEFF1"),
        "app_paths":     (11.4, 0.9, 4.2, 1.2, "app_paths", "#ECEFF1"),
    }
    centers = {}
    for k, (x, y, w, h, label, fc) in nodes.items():
        box(ax, x, y, w, h, label, fc=fc, fontsize=9)
        centers[k] = (x + w/2, y + h/2)

    edges = [
        ("ui", "worker"),
        ("worker", "capture"),
        ("worker", "decoder"),
        ("worker", "ai"),
        ("worker", "obs"),
        ("worker", "tts"),
        ("capture", "decoder"),
        ("ai", "memory"),
        ("ai", "voice_mgr"),
        ("tts", "voice_mgr"),
        ("voice_mgr", "voice_models"),
        ("worker", "dh"),
        ("dh", "voice_mgr"),
        ("ui", "rt_cfg"),
        ("worker", "rt_cfg"),
        ("voice_mgr", "app_paths"),
        ("dh", "app_paths"),
    ]
    for a, b in edges:
        arrow(ax, centers[a], centers[b], color="#546E7A", lw=0.9)

    ax.text(0.5, 12.6, "实线箭头: 模块导入与运行期调用关系",
            fontsize=8, color="#546E7A", style="italic")
    save(fig, "fig4_4_module_deps.png")

# -----------------------------------------------------------------------
# fig5_1 — 登录与弹幕抓取实现流程图
# -----------------------------------------------------------------------
def fig5_1():
    fig, ax = plt.subplots(figsize=(10, 8))
    W, H = 20, 16
    setup(ax, W, H)

    steps = [
        (14.5, "打开 persistent context 浏览器 (browser_data/)"),
        (12.7, "导航至 douyin.com，检测 sessionid Cookie"),
    ]
    for y, t in steps:
        box(ax, 4, y, 12, 1.2, t, fc=C_BOX_PRIMARY)
    arrow(ax, (10, 14.5), (10, 13.9))

    diamond(ax, 6, 10.3, 8, 1.8, "已登录?", fontsize=10)
    arrow(ax, (10, 12.7), (10, 12.1))

    box(ax, 0.5, 8.0, 8, 1.6, "提示扫码 (最长 150 秒)\n登录成功后保存浏览器状态",
        fc="#FFE0B2")
    arrow(ax, (6, 10.5), (4.5, 9.6), label="否", label_fs=8)

    box(ax, 11.5, 8.0, 8, 1.6, "进入直播间 URL\n强制 reload 让 WS 重连", fc=C_BOX_PRIMARY)
    arrow(ax, (14, 10.5), (15.5, 9.6), label="是", label_fs=8)
    arrow(ax, (4.5, 8.0), (10, 6.4), rad=-0.2)
    arrow(ax, (15.5, 8.0), (10, 6.4), rad=0.2)

    box(ax, 5, 5.0, 10, 1.4, "拦截媒体请求 / 注入 WS 引用与 DOM 观察脚本", fc="#E1F5FE")
    arrow(ax, (10, 6.4), (10, 6.4))
    box(ax, 5, 3.0, 10, 1.4, "page.on('websocket') 监听帧并解码", fc="#C8E6C9")
    arrow(ax, (10, 5.0), (10, 4.4))
    box(ax, 5, 1.0, 10, 1.4, "通过 PyQt Signal 把消息推回 UI 线程",
        fc=C_BOX_OK, weight="bold")
    arrow(ax, (10, 3.0), (10, 2.4))
    save(fig, "fig5_1_login_flow.png")

# -----------------------------------------------------------------------
# fig5_3 — 协议解码与消息处理流程图
# -----------------------------------------------------------------------
def fig5_3():
    fig, ax = plt.subplots(figsize=(11, 6.5))
    W, H = 22, 13
    setup(ax, W, H)

    box(ax, 0.5, 5.5, 3.5, 2.0, "二进制 WS 帧\n(framereceived)", fc=C_BOX_PRIMARY, weight="bold")
    arrow(ax, (4, 6.5), (5.0, 6.5))
    box(ax, 5.0, 5.5, 3.5, 2.0, "PushFrame.parse\n取 payload + log_id", fc="#E1F5FE")
    arrow(ax, (8.5, 6.5), (9.5, 6.5))
    box(ax, 9.5, 5.5, 3.5, 2.0, "gzip.decompress\n得 Response 消息体", fc="#E1F5FE")
    arrow(ax, (13.0, 6.5), (14.0, 6.5))
    box(ax, 14.0, 5.5, 3.5, 2.0, "遍历 messages\n按 method 分派", fc="#E1F5FE")
    arrow(ax, (17.5, 6.5), (18.5, 6.5))
    box(ax, 18.5, 5.5, 3.0, 2.0, "扁平事件\n回传 worker", fc=C_BOX_OK, weight="bold")

    # 分派下挂消息类型
    types = [("chat", 14.6), ("gift", 16.0), ("like", 17.4), ("follow", 18.7), ("enter", 14.6), ("stats", 16.0)]
    for i, (t, x) in enumerate(types):
        yy = 3.3 if i < 4 else 1.6
        box(ax, x, yy, 1.3, 1.0, t, fc="#FFF8E1", fontsize=9)
        arrow(ax, (15.75, 5.5), (x+0.65, yy+1.0), lw=0.7, color="#90A4AE")

    # parse_fail counter
    box(ax, 8.0, 2.6, 5.0, 1.4, "method_counts /\nparse_fail_counts 每 60s 日志",
        fc="#FFECB3", fontsize=9)
    arrow(ax, (15.0, 5.5), (13.0, 4.0), lw=0.8, color="#FB8C00")

    box(ax, 0.5, 2.6, 6.0, 1.4, "未知 method 写入\ndebug_payload.log 样本",
        fc="#FFCCBC", fontsize=9)
    arrow(ax, (15.5, 5.5), (6.5, 3.3), lw=0.8, color="#E64A19", rad=0.3)

    save(fig, "fig5_3_protocol_decode.png")

# -----------------------------------------------------------------------
# fig5_5 — 数字人推流流水线图
# -----------------------------------------------------------------------
def fig5_5():
    fig, ax = plt.subplots(figsize=(11, 5.8))
    W, H = 22, 11
    setup(ax, W, H)

    steps = [
        ("ffmpeg\n可用性检查", "#E3F2FD"),
        ("VoiceManager\nTTS 合成", "#FFF3E0"),
        ("ffmpeg 合成\nHLS 切片", "#FFF8E1"),
        ("启动本地\nHTTP 服务器", "#E1F5FE"),
        ("OBS WS\n配置媒体源 + 色键", "#F3E5F5"),
        ("PUSHING\n/STREAMING", "#C8E6C9"),
    ]
    x = 0.4
    for label, fc in steps:
        box(ax, x, 6.0, 3.3, 2.0, label, fc=fc, fontsize=10, weight="bold")
        x += 3.6
    for i in range(len(steps)-1):
        cx = 0.4 + (i+1)*3.6 - 0.3
        arrow(ax, (cx, 7.0), (cx+0.6, 7.0))

    box(ax, 0.4, 2.8, 6, 1.6, "异常: subprocess 启动失败 -> ERROR", fc="#FFCDD2", fontsize=9)
    box(ax, 7.4, 2.8, 6, 1.6, "停止: 终止 ffmpeg / 关闭 HTTP / 清切片", fc="#FFE0B2", fontsize=9)
    box(ax, 14.4, 2.8, 6.3, 1.6, "OBS 端: 移除媒体源, 切回主场景", fc="#E0F2F1", fontsize=9)

    # state legend
    states = "状态: IDLE -> SYNTHESIZING -> STARTING_SERVER -> CONFIGURING_OBS -> PUSHING -> STREAMING -> STOPPING / ERROR / CANCELLED"
    ax.text(0.4, 0.6, states, fontsize=8.5, color="#37474F",
            bbox=dict(boxstyle="round,pad=0.2", fc="#FAFAFA", ec="#B0BEC5"))
    save(fig, "fig5_5_dh_pipeline.png")

# -----------------------------------------------------------------------
# fig5_7 — OBS 关键词触发动作时序 (NEW)
# -----------------------------------------------------------------------
def fig5_7():
    fig, ax = plt.subplots(figsize=(11, 5))
    W, H = 22, 9
    setup(ax, W, H)

    # Lanes
    lanes = [("弹幕流", 7.0, "#E3F2FD"),
             ("ObsActionCtrl", 4.6, "#FFF3E0"),
             ("OBS WebSocket", 2.2, "#F3E5F5")]
    for name, y, fc in lanes:
        ax.add_patch(FancyBboxPatch((0.4, y), 21, 1.8,
            boxstyle="round,pad=0.02,rounding_size=0.15",
            ec=C_LINE, fc=fc, lw=1, alpha=0.55))
        ax.text(1.0, y+0.9, name, fontsize=10, weight="bold", va="center")

    # Events on top lane
    msgs = [(5, "弹幕含\n关键词1"), (8, "关键词1\n再次命中"), (12, "弹幕含\n关键词2"), (17, "...")]
    for x, t in msgs:
        ax.plot([x], [7.9], "o", color="#1976D2", markersize=7)
        ax.text(x, 8.4, t, ha="center", fontsize=8.5)

    # ctrl line
    ax.plot([5, 5.8], [5.5, 5.5], "-", color="#37474F", lw=1.0)
    ax.text(5.4, 5.85, "+1 命中", fontsize=8, ha="center")
    ax.plot([8, 8.8], [5.5, 5.5], "-", color="#37474F", lw=1.0)
    ax.text(8.4, 5.85, "+1 = 阈值2", fontsize=8, ha="center")

    box(ax, 9.0, 4.6, 2.6, 1.8, "触发动作", fc="#FFE082", fontsize=10, weight="bold")
    arrow(ax, (10.3, 4.6), (10.3, 4.0))
    box(ax, 9.0, 2.2, 2.6, 1.8, "切换场景", fc="#C8E6C9", fontsize=10, weight="bold")

    # cooldown bar
    ax.add_patch(FancyBboxPatch((11.6, 4.7), 7, 0.5, boxstyle="round,pad=0.02",
        ec="#FB8C00", fc="#FFE0B2", lw=1))
    ax.text(15.1, 4.95, "规则冷却 60s", fontsize=8.5, ha="center", weight="bold")

    # main scene return
    ax.plot([18.7, 19.5], [3.1, 3.1], "-", color="#37474F", lw=1.0)
    box(ax, 19.0, 2.2, 2.6, 1.8, "切回主场景", fc="#B3E5FC", fontsize=10, weight="bold")
    arrow(ax, (10.3, 2.2), (19.0, 3.1), rad=0.0, color="#90A4AE", lw=0.9, style="-")

    save(fig, "fig5_7_obs_timing.png")

# -----------------------------------------------------------------------
# fig5_8 — 系统指标观测示例 (NEW)
# -----------------------------------------------------------------------
def fig5_8():
    import numpy as np
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    t = np.arange(0, 60, 1)
    cpu = 4 + np.sin(t/6)*1.2 + np.random.RandomState(1).rand(60)*1.5
    cpu[40:50] += 7
    mem = 195 + np.cumsum(np.random.RandomState(2).randn(60)*0.6) + np.sin(t/12)*3

    axes[0].plot(t, cpu, color="#1976D2", lw=1.4)
    axes[0].fill_between(t, cpu, alpha=0.15, color="#1976D2")
    axes[0].set_title("CPU 占用 (%)  --  4 小时压测前 60 分钟")
    axes[0].set_xlabel("时间 (分钟)")
    axes[0].set_ylabel("CPU (%)")
    axes[0].set_ylim(0, 20)
    axes[0].grid(alpha=0.3)

    axes[1].plot(t, mem, color="#2E7D32", lw=1.4)
    axes[1].fill_between(t, mem, alpha=0.15, color="#2E7D32")
    axes[1].set_title("内存占用 (MB)")
    axes[1].set_xlabel("时间 (分钟)")
    axes[1].set_ylabel("RSS (MB)")
    axes[1].set_ylim(180, 220)
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    save(fig, "fig5_8_metrics.png")

# -----------------------------------------------------------------------
# fig6_1 — 性能测试结果对比图 (NEW)
# -----------------------------------------------------------------------
def fig6_1():
    import numpy as np
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))

    # Bar: end-to-end latency by stage
    stages = ["WS 帧到达", "解码完成", "UI 渲染", "AI 入队", "DeepSeek\n响应", "TTS 合成", "播放完成"]
    lat = [10, 12, 35, 50, 3800, 1200, 1100]   # ms
    cum = np.cumsum(lat)
    axes[0].bar(range(len(stages)), lat, color="#1976D2")
    axes[0].set_xticks(range(len(stages)))
    axes[0].set_xticklabels(stages, rotation=20, fontsize=8.5)
    axes[0].set_ylabel("阶段耗时 (ms)")
    axes[0].set_title("AI 回复链路阶段耗时 (典型样本)")
    for i, v in enumerate(lat):
        axes[0].text(i, v+80, str(v), ha="center", fontsize=8)
    axes[0].grid(axis="y", alpha=0.3)

    # Pie: miss rate / fail rate
    labels = ["WS 完整 chat", "WS 缺字段 + DOM 补全", "DOM 唯一", "丢失"]
    sizes = [78, 17, 4, 1]
    cols = ["#43A047", "#1E88E5", "#FB8C00", "#E53935"]
    axes[1].pie(sizes, labels=labels, autopct="%1.1f%%", colors=cols,
                startangle=90, textprops={"fontsize": 9})
    axes[1].set_title("弹幕采集来源分布 (压测 4 小时, n=12849)")

    plt.tight_layout()
    save(fig, "fig6_1_perf.png")

# -----------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Using font: {font_name}")
    fig4_1()
    fig4_2()
    fig4_3()
    fig4_4()
    fig5_1()
    fig5_3()
    fig5_5()
    fig5_7()
    fig5_8()
    fig6_1()
    print("done")
