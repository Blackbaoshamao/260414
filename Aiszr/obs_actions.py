"""OBS action orchestration for keyword-triggered scene playback."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import websockets


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _split_keywords(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        parts = value
    elif isinstance(value, str):
        parts = value.replace("，", ",").split(",")
    else:
        parts = ()

    keywords: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = str(part).strip()
        if not keyword:
            continue
        lowered = keyword.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(keyword)
    return tuple(keywords)


@dataclass(slots=True)
class ObsActionRule:
    enabled: bool = True
    name: str = ""
    keywords: tuple[str, ...] = ()
    target_scene: str = ""
    cooldown_sec: int = 60
    _keywords_folded: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self):
        self.name = self.name.strip()
        self.target_scene = self.target_scene.strip()
        self.cooldown_sec = max(0, int(self.cooldown_sec))
        self._keywords_folded = tuple(keyword.casefold() for keyword in self.keywords)

    @classmethod
    def from_dict(cls, value: object) -> "ObsActionRule":
        if not isinstance(value, dict):
            return cls()
        return cls(
            enabled=_coerce_bool(value.get("enabled"), True),
            name=str(value.get("name", "")).strip(),
            keywords=_split_keywords(value.get("keywords")),
            target_scene=str(value.get("target_scene", "")).strip(),
            cooldown_sec=_coerce_int(value.get("cooldown_sec"), 60, 0, 3600),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "name": self.name,
            "keywords": list(self.keywords),
            "target_scene": self.target_scene,
            "cooldown_sec": self.cooldown_sec,
        }

    def matches(self, content_folded: str) -> bool:
        return any(keyword in content_folded for keyword in self._keywords_folded)

    @property
    def key(self) -> str:
        return self.name or self.target_scene

    @property
    def display_name(self) -> str:
        return self.name or self.target_scene or "未命名动作"

    @property
    def is_valid(self) -> bool:
        return bool(self.keywords and self.target_scene)


@dataclass(slots=True)
class ObsActionSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    main_scene: str = ""
    ignore_during_playback: bool = True
    global_cooldown_sec: int = 20
    match_window_sec: int = 10
    min_hits: int = 2
    rules: tuple[ObsActionRule, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> "ObsActionSettings":
        if not isinstance(value, dict):
            return cls()
        rules_raw = value.get("rules")
        if not isinstance(rules_raw, list):
            rules_raw = []
        return cls(
            enabled=_coerce_bool(value.get("enabled"), False),
            host=str(value.get("host", "127.0.0.1")).strip() or "127.0.0.1",
            port=_coerce_int(value.get("port"), 4455, 1, 65535),
            password=str(value.get("password", "")),
            main_scene=str(value.get("main_scene", "")).strip(),
            ignore_during_playback=_coerce_bool(value.get("ignore_during_playback"), True),
            global_cooldown_sec=_coerce_int(value.get("global_cooldown_sec"), 20, 0, 3600),
            match_window_sec=_coerce_int(value.get("match_window_sec"), 10, 1, 3600),
            min_hits=_coerce_int(value.get("min_hits"), 2, 1, 20),
            rules=tuple(ObsActionRule.from_dict(rule) for rule in rules_raw),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "password": self.password,
            "main_scene": self.main_scene,
            "ignore_during_playback": self.ignore_during_playback,
            "global_cooldown_sec": self.global_cooldown_sec,
            "match_window_sec": self.match_window_sec,
            "min_hits": self.min_hits,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @property
    def active_rules(self) -> tuple[ObsActionRule, ...]:
        return tuple(rule for rule in self.rules if rule.enabled and rule.is_valid)


DEFAULT_OBS_ACTION_SETTINGS = ObsActionSettings()


class ObsRequestError(RuntimeError):
    """Raised when OBS rejects a request."""


class ObsWebSocketClient:
    """Minimal OBS WebSocket v5 client for scene and media requests."""

    def __init__(self):
        self._ws = None
        self._endpoint: tuple[str, int, str] | None = None
        self._request_counter = 0

    async def connect(self, host: str, port: int, password: str):
        endpoint = (host, port, password)
        if self._ws is not None and self._endpoint == endpoint:
            return

        await self.close()

        uri = f"ws://{host}:{port}"
        self._ws = await websockets.connect(
            uri,
            subprotocols=["obswebsocket.json"],
            open_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        )
        try:
            hello = await self._recv_json()
            if hello.get("op") != 0:
                raise ObsRequestError("OBS 未返回握手消息")

            identify = {
                "rpcVersion": min(int(hello.get("d", {}).get("rpcVersion", 1) or 1), 1),
                "eventSubscriptions": 0,
            }
            auth_payload = hello.get("d", {}).get("authentication")
            if isinstance(auth_payload, dict):
                if not password:
                    raise ObsRequestError("OBS 需要密码，但当前未配置密码")
                identify["authentication"] = self._build_auth_string(
                    password=password,
                    challenge=str(auth_payload.get("challenge", "")),
                    salt=str(auth_payload.get("salt", "")),
                )

            await self._send_json({"op": 1, "d": identify})
            identified = await self._recv_json()
            if identified.get("op") != 2:
                raise ObsRequestError("OBS 身份验证失败")
            self._endpoint = endpoint
        except Exception:
            await self.close()
            raise

    async def request(self, request_type: str, request_data: dict | None = None) -> dict:
        if self._ws is None:
            raise ObsRequestError("OBS 尚未连接")

        self._request_counter += 1
        request_id = str(self._request_counter)
        payload = {
            "op": 6,
            "d": {
                "requestType": request_type,
                "requestId": request_id,
            },
        }
        if request_data:
            payload["d"]["requestData"] = request_data

        try:
            await self._send_json(payload)
            while True:
                message = await self._recv_json()
                if message.get("op") != 7:
                    continue
                data = message.get("d", {})
                if data.get("requestId") != request_id:
                    continue
                status = data.get("requestStatus", {})
                if not status.get("result"):
                    comment = status.get("comment") or f"code={status.get('code')}"
                    raise ObsRequestError(f"{request_type} 失败: {comment}")
                return data.get("responseData", {})
        except ObsRequestError:
            raise
        except Exception:
            await self.close()
            raise

    async def close(self):
        ws = self._ws
        self._ws = None
        self._endpoint = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def create_input(
        self,
        scene_name: str,
        input_name: str,
        input_kind: str,
        input_settings: dict | None = None,
        scene_item_enabled: bool = True,
    ) -> dict:
        data = {
            "sceneName": scene_name,
            "inputName": input_name,
            "inputKind": input_kind,
            "sceneItemEnabled": scene_item_enabled,
        }
        if input_settings:
            data["inputSettings"] = input_settings
        return await self.request("CreateInput", data)

    async def set_input_settings(
        self,
        input_name: str,
        input_settings: dict,
        overlay: bool = True,
    ) -> dict:
        return await self.request("SetInputSettings", {
            "inputName": input_name,
            "inputSettings": input_settings,
            "overlay": overlay,
        })

    async def remove_input(self, input_name: str) -> dict:
        return await self.request("RemoveInput", {"inputName": input_name})

    async def create_source_filter(
        self,
        source_name: str,
        filter_name: str,
        filter_kind: str,
        filter_settings: dict | None = None,
    ) -> dict:
        data = {
            "sourceName": source_name,
            "filterName": filter_name,
            "filterKind": filter_kind,
        }
        if filter_settings:
            data["filterSettings"] = filter_settings
        return await self.request("CreateSourceFilter", data)

    async def set_source_filter_settings(
        self,
        source_name: str,
        filter_name: str,
        filter_settings: dict,
        overlay: bool = True,
    ) -> dict:
        return await self.request("SetSourceFilterSettings", {
            "sourceName": source_name,
            "filterName": filter_name,
            "filterSettings": filter_settings,
            "overlay": overlay,
        })

    async def get_source_filter_list(self, source_name: str) -> dict:
        return await self.request("GetSourceFilterList", {"sourceName": source_name})

    async def _send_json(self, payload: dict):
        if self._ws is None:
            raise ObsRequestError("OBS 尚未连接")
        await asyncio.wait_for(self._ws.send(json.dumps(payload)), timeout=5)

    async def _recv_json(self) -> dict:
        if self._ws is None:
            raise ObsRequestError("OBS 尚未连接")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=5)
        return json.loads(raw)

    @staticmethod
    def _build_auth_string(password: str, challenge: str, salt: str) -> str:
        secret = base64.b64encode(
            hashlib.sha256(f"{password}{salt}".encode("utf-8")).digest()
        ).decode("utf-8")
        return base64.b64encode(
            hashlib.sha256(f"{secret}{challenge}".encode("utf-8")).digest()
        ).decode("utf-8")
class ObsDigitalHumanConfigurator:
    """Auto-configures OBS with a Media Source pointing at an HLS stream."""

    MEDIA_SOURCE_KIND = "ffmpeg_source"

    def __init__(
        self,
        client: ObsWebSocketClient,
        log_callback: Callable[[str], None] | None = None,
    ):
        self._client = client
        self._log = log_callback or (lambda msg: None)
        self._created_input_name: str | None = None

    async def setup(
        self,
        scene_name: str,
        input_name: str,
        stream_path: str,
    ) -> dict:
        input_settings = {
            "input": stream_path,
            "is_local_file": False,
            "hw_decode": True,
        }
        try:
            # Try create fresh; if source already exists, update it in place
            await self._client.create_input(
                scene_name=scene_name,
                input_name=input_name,
                input_kind=self.MEDIA_SOURCE_KIND,
                input_settings=input_settings,
            )
        except ObsRequestError:
            await self._client.set_input_settings(input_name, input_settings)
            await self._client.request(
                "TriggerMediaInputAction",
                {"inputName": input_name, "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"},
            )
        self._created_input_name = input_name
        self._log(f"OBS 媒体源「{input_name}」已配置")
        return {"ok": True, "input_name": input_name, "message": "OBS 配置完成"}

    async def teardown(self) -> None:
        if self._created_input_name:
            with contextlib.suppress(ObsRequestError):
                await self._client.remove_input(self._created_input_name)
            self._created_input_name = None


class ObsActionController:
    """Keyword-driven OBS scene playback controller."""

    MEDIA_RESTART_ACTION = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
    MEDIA_ACTIVE_STATES = {
        "OBS_MEDIA_STATE_OPENING",
        "OBS_MEDIA_STATE_BUFFERING",
        "OBS_MEDIA_STATE_PLAYING",
        "OBS_MEDIA_STATE_PAUSED",
    }
    MEDIA_END_STATES = {
        "OBS_MEDIA_STATE_ENDED",
        "OBS_MEDIA_STATE_STOPPED",
        "OBS_MEDIA_STATE_ERROR",
    }
    MEDIA_STARTUP_GRACE_SEC = 1.5
    MEDIA_POLL_INTERVAL_SEC = 0.5

    def __init__(self, log_callback: Callable[[str], None] | None = None):
        self._log_callback = log_callback or (lambda message: None)
        self._client = ObsWebSocketClient()
        self._settings = DEFAULT_OBS_ACTION_SETTINGS
        self._hit_buckets: dict[str, deque[float]] = {}
        self._rule_cooldowns: dict[str, float] = {}
        self._global_cooldown_until = 0.0
        self._playback_task: asyncio.Task | None = None
        self._playback_token = 0
        self._lock = asyncio.Lock()
        self._scene_media_cache: dict[str, str] = {}

    def cooldown_remaining(self) -> float:
        """Seconds until global cooldown clears. 0 if no cooldown active.

        Why: HomePage OBS card polls this on a 1s QTimer to show countdown.
        Lock-free atomic float read — safe to call from the Qt main thread
        while the controller runs on the asyncio loop thread.
        """
        remaining = self._global_cooldown_until - time.monotonic()
        return remaining if remaining > 0 else 0.0

    async def probe(self, value: object | None = None, client: ObsWebSocketClient | None = None) -> dict:
        settings_source = value if value is not None else self._settings.to_dict()
        settings = ObsActionSettings.from_dict(settings_source)
        probe_client = client or ObsWebSocketClient()
        owns_client = client is None
        try:
            return await self._probe_with_client(probe_client, settings)
        finally:
            if owns_client:
                await probe_client.close()

    async def configure(self, value: object):
        settings = ObsActionSettings.from_dict(value)
        task_to_cancel = None
        close_client = False

        async with self._lock:
            endpoint_changed = (
                self._settings.host,
                self._settings.port,
                self._settings.password,
            ) != (settings.host, settings.port, settings.password)
            should_stop = (
                self._playback_task is not None
                and not self._playback_task.done()
                and (
                    not settings.enabled
                    or endpoint_changed
                    or settings.main_scene != self._settings.main_scene
                )
            )
            if should_stop:
                self._playback_token += 1
                task_to_cancel = self._playback_task
                self._playback_task = None
            self._settings = settings
            self._hit_buckets.clear()
            self._rule_cooldowns.clear()
            self._global_cooldown_until = 0.0
            self._scene_media_cache.clear()
            close_client = endpoint_changed or not settings.enabled

        if task_to_cancel is not None:
            task_to_cancel.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_to_cancel

        if close_client:
            await self._client.close()

        if settings.enabled:
            self._log(
                f"OBS 联动已启用，主场景: {settings.main_scene or '未设置'}，规则数: {len(settings.active_rules)}"
            )
        else:
            self._log("OBS 联动已关闭")

    async def close(self):
        task_to_cancel = None
        async with self._lock:
            self._playback_token += 1
            task_to_cancel = self._playback_task
            self._playback_task = None
        if task_to_cancel is not None:
            task_to_cancel.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_to_cancel
        await self._client.close()

    async def handle_chat_message(self, content: str):
        content_folded = " ".join((content or "").strip().split()).casefold()
        if not content_folded:
            return

        task_to_cancel = None
        playback_task = None

        async with self._lock:
            now = time.monotonic()
            settings = self._settings
            if not settings.enabled or not settings.main_scene or not settings.active_rules:
                self._log(
                    f"gate [no-config]: enabled={settings.enabled} "
                    f"main_scene={settings.main_scene!r} rules={len(settings.active_rules)}"
                )
                return

            is_playing = self._playback_task is not None and not self._playback_task.done()
            if is_playing and settings.ignore_during_playback:
                self._log("gate [playing]: 正在播放 + ignore_during_playback=True")
                return
            if not is_playing and now < self._global_cooldown_until:
                self._log(
                    f"gate [global-cooldown]: 还需 {self._global_cooldown_until - now:.1f}s"
                )
                return

            rule = self._register_hit(content_folded, now)
            if rule is None:
                return

            if is_playing and self._playback_task is not None:
                self._playback_token += 1
                task_to_cancel = self._playback_task
                self._playback_task = None

            self._playback_token += 1
            token = self._playback_token
            playback_task = asyncio.create_task(self._play_rule(rule, token))
            self._playback_task = playback_task
            self._hit_buckets.clear()

        if task_to_cancel is not None:
            task_to_cancel.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_to_cancel

        if playback_task is not None:
            playback_task.add_done_callback(self._consume_task_exception)

    async def _probe_with_client(self, client: ObsWebSocketClient, settings: ObsActionSettings) -> dict:
        try:
            await client.connect(settings.host, settings.port, settings.password)
            scene_response = await client.request("GetSceneList")
        except Exception as exc:
            return self._build_probe_result(
                state="disconnected" if settings.enabled else "disabled",
                connected=False,
                short_text="未连接" if settings.enabled else "未启用",
                message="OBS 联动未启用" if not settings.enabled else self._format_obs_exception(exc),
            )

        if not settings.enabled:
            return self._build_probe_result(
                state="warning",
                connected=True,
                short_text="已连接",
                message=f"OBS 已连接 ({settings.host}:{settings.port})，但联动未启用",
            )

        scenes = {
            str(scene.get("sceneName", "")).strip()
            for scene in scene_response.get("scenes", [])
            if str(scene.get("sceneName", "")).strip()
        }
        issues: list[str] = []

        if not settings.main_scene:
            issues.append("未填写主场景")
        elif settings.main_scene not in scenes:
            issues.append(f"主场景不存在: {settings.main_scene}")

        active_rules = settings.active_rules
        if not active_rules:
            issues.append("未配置启用中的动作规则")

        scene_media_cache: dict[str, str] = {}
        for rule in active_rules:
            if rule.target_scene not in scenes:
                issues.append(f"目标场景不存在: {rule.target_scene}")
                continue

            media_input = await self._resolve_media_input_for_client(
                client,
                rule.target_scene,
                scene_media_cache,
            )
            if not media_input:
                issues.append(f"场景 {rule.target_scene} 中未找到可播放媒体源")

        if issues:
            preview = "；".join(issues[:3])
            if len(issues) > 3:
                preview += f"；另有 {len(issues) - 3} 项问题"
            return self._build_probe_result(
                state="warning",
                connected=True,
                short_text="已连接",
                message=f"OBS 已连接，但配置仍需处理：{preview}",
            )

        return self._build_probe_result(
            state="connected",
            connected=True,
            short_text="已连接",
            message=f"OBS 已连接，主场景和 {len(active_rules)} 条规则均通过检查",
        )

    def _register_hit(self, content_folded: str, now: float) -> ObsActionRule | None:
        for rule in self._settings.active_rules:
            if not rule.matches(content_folded):
                continue
            cooldown_until = self._rule_cooldowns.get(rule.key, 0.0)
            if now < cooldown_until:
                self._log(
                    f"gate [rule-cooldown] {rule.display_name}: 还需 {cooldown_until - now:.1f}s"
                )
                continue

            bucket = self._hit_buckets.setdefault(rule.key, deque())
            while bucket and now - bucket[0] > self._settings.match_window_sec:
                bucket.popleft()
            bucket.append(now)
            if len(bucket) >= self._settings.min_hits:
                self._log(
                    f"hit: rule={rule.display_name} 命中 {len(bucket)}/{self._settings.min_hits} 次 → 触发"
                )
                bucket.clear()
                return rule
            self._log(
                f"hit-pending: rule={rule.display_name} "
                f"{len(bucket)}/{self._settings.min_hits} 次 (窗口 {self._settings.match_window_sec}s)"
            )
            return None
        return None

    async def _play_rule(self, rule: ObsActionRule, token: int):
        playback_started = False
        current_task = asyncio.current_task()
        try:
            await self._client.connect(
                self._settings.host,
                self._settings.port,
                self._settings.password,
            )
            media_input = await self._resolve_media_input(rule.target_scene)
            if not media_input:
                self._log(f"场景“{rule.target_scene}”未找到媒体源，动作已跳过")
                return

            await self._client.request(
                "SetCurrentProgramScene",
                {"sceneName": rule.target_scene},
            )
            await self._client.request(
                "TriggerMediaInputAction",
                {
                    "inputName": media_input,
                    "mediaAction": self.MEDIA_RESTART_ACTION,
                },
            )
            playback_started = True
            async with self._lock:
                if token == self._playback_token:
                    self._rule_cooldowns[rule.key] = time.monotonic() + rule.cooldown_sec
            self._log(f"触发 OBS 动作: {rule.display_name} -> {rule.target_scene}")
            await self._poll_media_until_end(media_input)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"OBS 动作执行失败: {self._format_obs_exception(exc)}")
        finally:
            async with self._lock:
                is_current = token == self._playback_token and self._playback_task is current_task

            if playback_started and is_current:
                try:
                    await self._client.request(
                        "SetCurrentProgramScene",
                        {"sceneName": self._settings.main_scene},
                    )
                    self._log(f"动作结束，切回主场景: {self._settings.main_scene}")
                except Exception as exc:
                    self._log(f"切回主场景失败: {self._format_obs_exception(exc)}")

            async with self._lock:
                if token == self._playback_token and self._playback_task is current_task:
                    self._playback_task = None
                    if playback_started:
                        self._global_cooldown_until = (
                            time.monotonic() + self._settings.global_cooldown_sec
                        )

    async def _resolve_media_input(self, scene_name: str) -> str | None:
        return await self._resolve_media_input_for_client(
            self._client,
            scene_name,
            self._scene_media_cache,
        )

    async def _resolve_media_input_for_client(
        self,
        client: ObsWebSocketClient,
        scene_name: str,
        cache: dict[str, str],
    ) -> str | None:
        cached = cache.get(scene_name)
        if cached:
            try:
                await client.request("GetMediaInputStatus", {"inputName": cached})
                return cached
            except Exception:
                cache.pop(scene_name, None)

        response = await client.request("GetSceneItemList", {"sceneName": scene_name})
        seen: set[str] = set()
        for item in response.get("sceneItems", []):
            source_name = str(item.get("sourceName", "")).strip()
            if not source_name or source_name in seen:
                continue
            seen.add(source_name)
            try:
                await client.request("GetMediaInputStatus", {"inputName": source_name})
                cache[scene_name] = source_name
                return source_name
            except Exception:
                continue
        return None

    async def _poll_media_until_end(self, media_input: str):
        deadline = time.monotonic() + 900.0
        startup_deadline = time.monotonic() + self.MEDIA_STARTUP_GRACE_SEC
        playback_seen = False

        while time.monotonic() < deadline:
            status = await self._client.request(
                "GetMediaInputStatus",
                {"inputName": media_input},
            )
            media_state = str(status.get("mediaState", ""))
            media_duration = status.get("mediaDuration")
            media_cursor = status.get("mediaCursor")

            if (
                media_state in self.MEDIA_ACTIVE_STATES
                or isinstance(media_cursor, (int, float)) and media_cursor > 0
            ):
                playback_seen = True

            if not playback_seen:
                if time.monotonic() < startup_deadline:
                    await asyncio.sleep(self.MEDIA_POLL_INTERVAL_SEC)
                    continue
                if media_state in self.MEDIA_END_STATES:
                    self._log(
                        f"媒体源 {media_input} 未进入播放态，当前状态: {media_state or 'UNKNOWN'}"
                    )
                    return

            if media_state in self.MEDIA_END_STATES:
                return
            if (
                isinstance(media_duration, (int, float))
                and isinstance(media_cursor, (int, float))
                and media_duration > 0
                and media_cursor >= media_duration - 50
            ):
                return
            await asyncio.sleep(self.MEDIA_POLL_INTERVAL_SEC)

    def _log(self, message: str):
        self._log_callback(message)

    @staticmethod
    def _build_probe_result(state: str, connected: bool, short_text: str, message: str) -> dict:
        return {
            "state": state,
            "connected": connected,
            "short_text": short_text,
            "message": message,
        }

    @staticmethod
    def _format_obs_exception(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        lowered = text.lower()
        if "password" in lowered or "authentication" in lowered or "身份验证" in text:
            return "OBS 连接失败：密码不正确或未填写密码"
        if "timed out" in lowered or "timeout" in lowered:
            return "OBS 连接超时，请检查地址、端口和局域网连通性"
        if "refused" in lowered or "10061" in lowered or "cannot connect" in lowered:
            return "OBS 未启动或 WebSocket 服务未开启"
        if "404" in lowered or "handshake" in lowered:
            return "OBS WebSocket 握手失败，请确认端口和插件版本"
        if "nodename nor servname" in lowered or "name or service not known" in lowered:
            return "OBS 地址无法解析，请检查主机地址"
        return f"OBS 连接失败：{text}"

    @staticmethod
    def _consume_task_exception(task: asyncio.Task):
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()
