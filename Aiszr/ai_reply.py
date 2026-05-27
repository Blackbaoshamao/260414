"""DeepSeek AI Reply Engine for Aiszr.

Provides:
  - DeepSeek LLM client (OpenAI-compatible API)
  - Reply throttling (min interval + per-user cooldown)
  - Smart short-message detection and intent guessing
  - Fallback to rule-based replies when LLM unavailable
"""

import asyncio
import inspect
import math
import time
from dataclasses import dataclass, field

from loguru import logger


USER_COOLDOWN_SEC = 60
LLM_MAX_TOKENS = 100
LLM_TEMPERATURE = 1.0
LLM_TIMEOUT_MIN_SEC = 6
LLM_TIMEOUT_MAX_SEC = 12

DEFAULT_PERSONA_NAME = "慕斯"
DEFAULT_PERSONA_ROLE = "主打零压力陪伴的情感唠嗑主播，是观众的专属情绪搭子、人间清醒嘴替。像身边最懂你的闺蜜，专治内耗、emo、恋爱脑，陪你唠日常、吐苦水、分享开心，不搞虚头巴脑的套路，只给实打实的情绪承接与陪伴。"

DEFAULT_PERSONA_TONE = """整体基调\t慵懒松弛、像窝在沙发上回闺蜜消息。不端不装、不刻意热情。
口语特征\t纯日常口语，尾音带"哈"、"啧"、"唉"，但不能使用频率过高（叹气仅用于表示"我懂你"）。
情绪立场\t永远和观众站在同一侧。观众吐槽时，先无条件帮腔；观众冷静后，再偷摸捋逻辑。
幽默方式\t自嘲式劝解、熟人揭短式调侃。只攻击"那件事"，不攻击"那个人"。
分寸感\t亲昵但不越界，像认识很久的朋友，不带讨好感，不叫"亲"、"宝"。
活人痕迹\t允许偶尔嘴瓢、忘词、宕机，允许说"我刚想说什么来着被你整忘了"。"""

DEFAULT_PERSONA_STRATEGY = """短弹幕（111、...、？）\t主动调侃或询问状态，猜测对方意图后用吐槽式提问接住。
观众提问\t简短回答后反问，保持互动感，不让对话断在回答上。
观众夸赞\t自然接话不谦虚，顺势自嘲或反夸回去。
观众吐槽/emo\t先共情站队，再补清醒小吐槽，绝不搞说教。
观众分享开心\t真心捧场接梗，不泼冷水。
弹幕刷屏\t不逐条回，挑最有梗或最有情绪的那条回复。"""

DEFAULT_PERSONA_SCENE = """开场白\t"来了来了，今天谁来陪我唠嗑？"、"刚醒，谁在？"
没人说话\t主动抛选择题式话题："你们是猫派还是狗派？"、"说一个你今天最想吐槽的事"
冷场救场\t自嘲式活跃："我是不是该反思一下为什么没人理我"、"行吧我自己唠"
收尾\t末尾自然抛话题或提问，引导继续互动，不说"大家聊聊天吧"这类客套话。"""

DEFAULT_PERSONA_LIMIT = """字数限制\t每条回复严格控制在 40-80 字，多一字不可。
语言形式\t纯口语化，禁用书面语、长难句、排比句、修辞堆砌。每句话要像一条微信语音条。
结构公式\t语气词 + 精准复述观众状态 + 嘴替吐槽/清醒点拨 + 收尾动作或画面感。
承接优先级\t先承接情绪，再延伸内容。开心就捧场接梗，低落就先站队再轻戳破。
禁止套话\t禁用万能安慰模板，每一条回复必须有对当前弹幕或语境的专属针对性。
避免冷场\t没人说话时，主动抛选择题式话题勾引弹幕，但绝不说"大家聊聊天吧"这类客套话。"""

DEFAULT_PERSONA_TABOO = """禁鸡汤文学：禁用"未来可期"、"你若盛开"等励志语录、诗词歌赋、空洞安慰词句。
禁客服腔调：禁用"亲"、"请问有什么可以帮您"、"感谢您的分享"等职业假笑或卑微话术。
禁爹味评判：不指责、不说教、不居高临下输出个人价值观，不强行"纠正"观众。
禁敏感话题：不碰政治、宗教、色情、暴力、医疗建议、投资理财、违法违规内容。
禁挑动对立：不搞性别对立、圈层对立、不引战、不贩卖焦虑、不传播负面情绪。
禁探问隐私：不追问收入、住址、感情细节、家庭情况等私密信息。
禁低俗脏话：不用脏字、低俗擦边梗、过气网络烂梗，保持语言干净得体。
禁抬杠怼人：观点不同时只温和表达立场，不引发冲突、不激化矛盾。
禁虚假承诺：不说"我永远陪着你"、"我会一直在"这类无法兑现的情感绑定话术。
禁传递负能：始终做观众情绪的承接容器，不把自身负面状态输出给观众。
禁完美人设：允许有小迷糊、小宕机，但绝不散播消极情绪。"""

DEFAULT_MANAGED_LIVE_POSITION = """直播间名称：榴莲大魔王
主要是卖榴莲的"""

DEFAULT_MANAGED_PRODUCT_INFO = "1号链接是榴莲的链接，现场称重，一物一拍。"

DEFAULT_MANAGED_PRODUCT_SELLING_POINTS = ""

DEFAULT_MANAGED_SHIPPING = """发顺丰快递
一般城市2天左右就到了，偏远地区（新疆、内蒙古、吉林、辽宁、黑龙江、甘肃）3到4天到货。"""

DEFAULT_MANAGED_CAMPAIGN = ""

DEFAULT_MANAGED_TALK_STYLE = "语气自然、热情、口语化，像直播间真人助播；少用客服腔，不说夸大承诺，不编造未提供的信息。"

DEFAULT_MANAGED_QUESTION_NOTES = """Q：天津能发吗？
A：能发，偏远或平台限制地区除外，具体以下单地址为准。"""

DEFAULT_MANAGED_ANSWER_EXAMPLES = """评论：黑河几天到？
回答：黑河啊，确实稍微有点远哈，可能时间会稍微长一点点，咱们通常大部分地区就两到三天，但是黑河的话大概要三到四天左右哈。

评论：今天能发吗？着急用。
回答：今天能不能发要看下单时间哈，能赶上打包就尽量当天发，赶不上也会尽快安排，着急的话你先拍，我这边帮你盯一下。"""


def _section(title: str, content: str) -> str:
    content = (content or "").strip()
    return f"### {title}\n{content}" if content else ""


def _default_if_missing(value: str | None, default: str) -> str:
    return default if value is None else value


def build_managed_system_prompt(
    live_position: str | None = None,
    product_info: str | None = None,
    product_selling_points: str | None = None,
    shipping: str | None = None,
    campaign: str | None = None,
    talk_style: str | None = None,
    question_notes: str | None = None,
    answer_examples: str | None = None,
    reply_char_limit: int = LLM_MAX_TOKENS,
) -> str:
    """Build the managed-commerce prompt from structured knowledge blocks."""
    try:
        reply_char_limit = int(reply_char_limit)
    except (TypeError, ValueError):
        reply_char_limit = LLM_MAX_TOKENS
    reply_char_limit = max(20, min(reply_char_limit, 500))

    sections = [
        "你是直播间 AI 场控助播，负责根据观众弹幕生成可以直接播报的中文回复。",
        f"硬性规则：每条回复不超过 {reply_char_limit} 个中文字符；只输出回复正文；不要加标题、括号说明或分析过程。",
        "只使用下方知识库信息回答。价格、库存、功效、赠品、发货时效等未提供的信息不要编造；不确定时用直播间可确认的说法承接。",
        "回复要短、自然、像真人直播口播；优先回答观众问题，其次引导看链接、拍下或继续提问。",
        _section("直播间定位", _default_if_missing(live_position, DEFAULT_MANAGED_LIVE_POSITION)),
        _section("产品信息", _default_if_missing(product_info, DEFAULT_MANAGED_PRODUCT_INFO)),
        _section("产品卖点", _default_if_missing(product_selling_points, DEFAULT_MANAGED_PRODUCT_SELLING_POINTS)),
        _section("发货与快递", _default_if_missing(shipping, DEFAULT_MANAGED_SHIPPING)),
        _section("活动信息", _default_if_missing(campaign, DEFAULT_MANAGED_CAMPAIGN)),
        _section("话术风格", _default_if_missing(talk_style, DEFAULT_MANAGED_TALK_STYLE)),
        _section("问题补充", _default_if_missing(question_notes, DEFAULT_MANAGED_QUESTION_NOTES)),
        _section("回答样板", _default_if_missing(answer_examples, DEFAULT_MANAGED_ANSWER_EXAMPLES)),
        "不要重复之前回复中说过的内容、句式或话题，每次回复必须要有新意。",
    ]
    return "\n\n".join(section for section in sections if section)


def build_system_prompt(
    name="",
    role="",
    tone="",
    strategy="",
    scene="",
    limit="",
    taboo="",
    live_position=None,
    product_info=None,
    product_selling_points=None,
    shipping=None,
    campaign=None,
    talk_style=None,
    question_notes=None,
    answer_examples=None,
    reply_char_limit=None,
) -> str:
    managed_values = (
        live_position, product_info, product_selling_points, shipping,
        campaign, talk_style, question_notes, answer_examples,
    )
    if any(value is not None for value in managed_values):
        return build_managed_system_prompt(
            live_position=live_position,
            product_info=product_info,
            product_selling_points=product_selling_points,
            shipping=shipping,
            campaign=campaign,
            talk_style=talk_style,
            question_notes=question_notes,
            answer_examples=answer_examples,
            reply_char_limit=reply_char_limit or LLM_MAX_TOKENS,
        )

    name = name or DEFAULT_PERSONA_NAME
    role = role or DEFAULT_PERSONA_ROLE
    tone = tone or DEFAULT_PERSONA_TONE
    strategy = strategy or DEFAULT_PERSONA_STRATEGY
    scene = scene or DEFAULT_PERSONA_SCENE
    limit = limit or DEFAULT_PERSONA_LIMIT
    taboo = taboo or DEFAULT_PERSONA_TABOO
    parts = [f"你是「{name}」，{role}\n\n语气风格：\n{tone}"]
    if strategy:
        parts.append(f"回复策略：\n{strategy}")
    if scene:
        parts.append(f"场景话术：\n{scene}")
    if limit:
        parts.append(f"回复限制：\n{limit}")
    if taboo:
        parts.append(f"绝对禁止：\n{taboo}")
    parts.append("不要重复之前回复中说过的内容、句式或话题，每次回复必须要有新意。")
    return "\n\n".join(parts)


@dataclass
class AIConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    system_prompt: str = ""
    auto_reply: bool = False
    reply_interval: int = 30
    max_tokens: int = LLM_MAX_TOKENS
    temperature: float = LLM_TEMPERATURE
    public_memory_enabled: bool = True
    blocked_words: list = field(default_factory=list)


@dataclass
class ReplyResult:
    target_user: str
    target_msg: str
    reply: str


class DeepSeekClient:
    """Async client for DeepSeek Chat API (OpenAI-compatible)."""

    def __init__(self, config: AIConfig):
        self.config = config
        self._client = None

    async def _ensure_client(self):
        if self._client is None:
            try:
                import httpx
                self._client = httpx.AsyncClient(
                    base_url=self.config.base_url,
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    timeout=30.0,
                )
            except Exception as e:
                logger.error("Failed to create httpx client: {}", e)
                raise
        return self._client

    async def chat(self, messages: list) -> str | None:
        client = await self._ensure_client()
        request_timeout = min(
            max(int(self.config.reply_interval or 0), LLM_TIMEOUT_MIN_SEC),
            LLM_TIMEOUT_MAX_SEC,
        )
        try:
            max_tokens = int(self.config.max_tokens or LLM_MAX_TOKENS)
        except (TypeError, ValueError):
            max_tokens = LLM_MAX_TOKENS
        max_tokens = max(20, min(max_tokens, 1000))
        try:
            temperature = float(self.config.temperature)
        except (TypeError, ValueError):
            temperature = LLM_TEMPERATURE
        temperature = max(0.0, min(temperature, 2.0))
        try:
            resp = await client.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("DeepSeek API error: {}", e)
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def is_short_message(msg: str) -> bool:
    """Detect meaningless short messages: '111', '...', '???', emoji-only, etc."""
    stripped = msg.strip()
    if len(stripped) <= 3:
        return True
    # All same character: "aaa", "111", "..."
    if len(set(stripped)) == 1:
        return True
    # Pure numbers
    if stripped.isdigit():
        return True
    # Pure punctuation/symbols
    if all(c in '.?！!。,，、~～… ' for c in stripped):
        return True
    return False


def guess_intent(msg: str) -> str:
    """Try to guess what the viewer means with a short message."""
    stripped = msg.strip()
    if stripped.isdigit():
        if len(stripped) <= 3:
            return "可能在刷屏或测试，也许只是无聊路过"
        return "可能是房间号或者密码之类的"
    if all(c in '.?？' for c in stripped):
        return "一脸问号，可能是困惑或者在等什么"
    if all(c in '！!！' for c in stripped):
        return "情绪激动，可能被什么惊讶到了"
    if all(c in '哈呵嘿' for c in stripped):
        return "笑得很开心"
    if all(c == stripped[0] for c in stripped):
        return "刷屏中，也许只是无聊"
    return "简短弹幕，可能只是随手一发"


class RuleBasedFallback:
    """Natural fallback when LLM is unavailable."""

    def reply(self, user: str, msg: str) -> str | None:
        if is_short_message(msg):
            import random
            short_replies = [
                f"@{user} 你是在测试我能不能看到吗？能看到哦",
                f"@{user} 发这个是什么意思呀，教教我",
                f"@{user} 别光发数字呀，说点什么嘛",
                f"@{user} 你是不是无聊了？聊两块钱的？",
                f"@{user} 收到你的信号了，但我不知道你想说啥",
                f"@{user} 你这个我翻译不了，再说一遍？",
            ]
            return random.choice(short_replies)

        msg_lower = msg.lower()
        rules = [
            ("你好", f"@{user} 你好呀，欢迎进来聊天"),
            ("晚上好", f"@{user} 晚上好，今天过得怎么样"),
            ("谢谢", f"@{user} 客气啥，咱们直播间不搞那些虚的"),
            ("加油", f"@{user} 好的收到，一起冲"),
            ("关注", f"@{user} 谢谢关注，你眼光不错"),
            ("主播", f"@{user} 主播正在忙着呢，我来陪你聊"),
            ("666", f"@{user} 666，你品味很好"),
            ("哈哈", f"@{user} 看把你乐的，分享分享一下"),
        ]
        for keyword, response in rules:
            if keyword in msg_lower:
                return response
        return None


class AIReplyEngine:
    """Orchestrates AI reply with throttling and fallback."""

    def __init__(self, config: AIConfig):
        self.config = config
        self._client = DeepSeekClient(config)
        self._fallback = RuleBasedFallback()
        self._last_reply_time: float = 0
        self._user_cooldowns: dict[str, float] = {}
        self._conversation_history: list[dict] = []
        self._max_history = 4
        self._pending_message: dict | None = None
        self._pending_timer: asyncio.Task | None = None
        self._reply_lock = asyncio.Lock()
        self._memory = None

    def set_memory(self, memory):
        self._memory = memory

    def _should_reply(self, msg: dict) -> bool:
        if not self.config.auto_reply:
            return False
        content = msg.get("content", "")
        if not content:
            return False
        for word in self.config.blocked_words:
            if word in content:
                return False
        now = time.time()
        if now - self._last_reply_time < self.config.reply_interval:
            return False
        user_id = msg.get("user_id", "")
        last = self._user_cooldowns.get(user_id, 0)
        if now - last < USER_COOLDOWN_SEC:
            return False
        return True

    def _emit_status(self, payload: dict):
        callback = getattr(self, "_on_status", None)
        if callback:
            callback(dict(payload))

    def describe_status(self) -> dict:
        now = time.time()
        interval_sec = int(self.config.reply_interval)
        next_ready_at = None

        if not self.config.auto_reply:
            return {
                "state": "disabled",
                "short_text": "已关闭",
                "detail": "自动回复未开启",
                "interval_sec": interval_sec,
                "next_ready_at": None,
            }

        if self._reply_lock.locked():
            detail = "正在生成回复..."
            if self._pending_message is not None:
                detail = "正在生成回复，新弹幕已排队"
            if self._last_reply_time > 0:
                next_ready_at = self._last_reply_time + self.config.reply_interval
            return {
                "state": "generating",
                "short_text": "回复中",
                "detail": detail,
                "interval_sec": interval_sec,
                "next_ready_at": next_ready_at,
            }

        if self._last_reply_time > 0:
            next_ready_at = self._last_reply_time + self.config.reply_interval
            if now < next_ready_at:
                remaining = max(0, math.ceil(next_ready_at - now))
                detail = f"冷却剩余 {remaining} 秒，设定间隔 {interval_sec} 秒"
                if self._pending_message is not None:
                    detail += "，有新弹幕等待中"
                return {
                    "state": "cooldown",
                    "short_text": "冷却中",
                    "detail": detail,
                    "interval_sec": interval_sec,
                    "next_ready_at": next_ready_at,
                }

        if self._pending_message is not None:
            return {
                "state": "queued",
                "short_text": "待回复",
                "detail": f"已缓存一条新弹幕，满足间隔后会尝试回复（{interval_sec} 秒）",
                "interval_sec": interval_sec,
                "next_ready_at": next_ready_at,
            }

        return {
            "state": "idle",
            "short_text": "待命中",
            "detail": f"自动回复已开启，设定最小间隔 {interval_sec} 秒",
            "interval_sec": interval_sec,
            "next_ready_at": next_ready_at,
        }

    def publish_status(self):
        self._emit_status(self.describe_status())

    async def _deliver_reply(self, result):
        callback = getattr(self, "_on_reply", None)
        if callback is None:
            return
        returned = callback(result)
        if inspect.isawaitable(returned):
            await returned

    async def process_message(self, msg: dict) -> ReplyResult | None:
        if msg.get("type") != "chat":
            return None
        if self._reply_lock.locked():
            self._pending_message = msg
            self.publish_status()
            return None
        if not self._should_reply(msg):
            self._pending_message = msg
            self._schedule_pending()
            self.publish_status()
            return None

        self._pending_message = None
        return await self._do_reply(msg)

    def _schedule_pending(self):
        if self._pending_timer and not self._pending_timer.done():
            self._pending_timer.cancel()
        wait = self.config.reply_interval - (time.time() - self._last_reply_time)
        if wait <= 0:
            wait = 0.1

        async def _fire():
            await asyncio.sleep(wait)
            if self._pending_message is None:
                self.publish_status()
                return
            msg = self._pending_message
            self._pending_message = None
            if self._should_reply(msg):
                result = await self._do_reply(msg)
                if result:
                    await self._deliver_reply(result)
            else:
                self.publish_status()

        try:
            self._pending_timer = asyncio.ensure_future(_fire())
        except RuntimeError:
            pass

    async def _do_reply(self, msg: dict) -> ReplyResult | None:
        async with self._reply_lock:
            now = time.time()
            self._last_reply_time = now
            self._user_cooldowns[msg.get("user_id", "")] = now
            self._emit_status(
                {
                    "state": "generating",
                    "short_text": "回复中",
                    "detail": f"正在生成回复，设定间隔 {int(self.config.reply_interval)} 秒",
                    "interval_sec": int(self.config.reply_interval),
                    "next_ready_at": self._last_reply_time + self.config.reply_interval,
                }
            )

            target_user = msg.get("nickname", "")
            target_msg = msg.get("content", "")
            target_uid = msg.get("user_id", "")

            reply = await self._get_llm_reply(target_user, target_msg, target_uid)

            if reply is None:
                reply = self._fallback.reply(target_user, target_msg)

            if reply is None:
                self.publish_status()
                return None

            if self._memory and self.config.public_memory_enabled:
                try:
                    await self._memory.save_interaction(
                        target_user, target_uid, target_msg, reply,
                    )
                except Exception:
                    pass

            self.publish_status()

            return ReplyResult(
                target_user=target_user,
                target_msg=target_msg,
                reply=reply,
            )

    async def _get_llm_reply(self, user: str, msg: str, user_id: str = "") -> str | None:
        if not self.config.api_key:
            return None

        # Build context-aware user message
        user_content = f"[{user}]: {msg}"
        if is_short_message(msg):
            intent = guess_intent(msg)
            user_content += f"\n[系统提示：这是一条短弹幕，推测意图：{intent}]"

        self._conversation_history.append({
            "role": "user",
            "content": user_content,
        })

        if len(self._conversation_history) > self._max_history:
            self._conversation_history = self._conversation_history[-self._max_history:]

        system_content = self.config.system_prompt

        if self._memory and self.config.public_memory_enabled:
            try:
                history = await self._memory.load_recent_history(user, user_id)
                history_text = self._memory.format_history_for_prompt(history)
                if history_text:
                    system_content += f"\n\n{history_text}"
            except Exception:
                pass

        messages = [
            {"role": "system", "content": system_content},
        ] + self._conversation_history

        reply = await self._client.chat(messages)
        if reply:
            self._conversation_history.append({
                "role": "assistant",
                "content": reply,
            })
        return reply

    async def close(self):
        await self._client.close()
