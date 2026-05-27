import asyncio
from collections import deque

import pytest

from obs_actions import ObsActionController, ObsActionSettings


class FakeObsClient:
    def __init__(self):
        self.connect_calls = []
        self.request_calls = []
        self.scene_items = {}
        self.scene_names = set()
        self.media_status = {}
        self.closed = False

    async def connect(self, host, port, password):
        self.connect_calls.append((host, port, password))

    async def request(self, request_type, request_data=None):
        request_data = request_data or {}
        self.request_calls.append((request_type, dict(request_data)))

        if request_type == "GetSceneList":
            names = sorted(self.scene_names or set(self.scene_items))
            return {"scenes": [{"sceneName": name} for name in names]}
        if request_type == "GetSceneItemList":
            return {"sceneItems": list(self.scene_items.get(request_data["sceneName"], []))}
        if request_type == "GetMediaInputStatus":
            input_name = request_data["inputName"]
            queue = self.media_status.setdefault(
                input_name,
                deque(
                    [
                        {
                            "mediaState": "OBS_MEDIA_STATE_ENDED",
                            "mediaDuration": 1000,
                            "mediaCursor": 1000,
                        }
                    ]
                ),
            )
            if len(queue) > 1:
                return queue.popleft()
            return queue[0]
        return {}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_action_triggers_after_required_hits_and_returns_to_main_scene():
    controller = ObsActionController()
    controller.MEDIA_STARTUP_GRACE_SEC = 0.01
    controller.MEDIA_POLL_INTERVAL_SEC = 0
    fake_client = FakeObsClient()
    fake_client.scene_items["售后场景"] = [{"sourceName": "售后媒体"}]
    fake_client.media_status["售后媒体"] = deque(
        [
            {
                "mediaState": "OBS_MEDIA_STATE_STOPPED",
                "mediaDuration": 100,
                "mediaCursor": 0,
            },
            {
                "mediaState": "OBS_MEDIA_STATE_PLAYING",
                "mediaDuration": 100,
                "mediaCursor": 10,
            },
            {
                "mediaState": "OBS_MEDIA_STATE_ENDED",
                "mediaDuration": 100,
                "mediaCursor": 100,
            },
        ]
    )
    controller._client = fake_client

    await controller.configure(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 4455,
            "main_scene": "主场景",
            "global_cooldown_sec": 0,
            "match_window_sec": 10,
            "min_hits": 2,
            "rules": [
                {
                    "enabled": True,
                    "name": "售后动作",
                    "keywords": ["售后", "退货"],
                    "target_scene": "售后场景",
                    "cooldown_sec": 0,
                }
            ],
        }
    )

    await controller.handle_chat_message("请问售后怎么处理")
    await asyncio.sleep(0)
    assert not any(
        request_type == "SetCurrentProgramScene"
        for request_type, _ in fake_client.request_calls
    )

    await controller.handle_chat_message("这个产品坏了可以退货吗")
    assert controller._playback_task is not None
    await controller._playback_task

    scene_switches = [
        request_data["sceneName"]
        for request_type, request_data in fake_client.request_calls
        if request_type == "SetCurrentProgramScene"
    ]
    assert scene_switches == ["售后场景", "主场景"]
    assert (
        "TriggerMediaInputAction",
        {
            "inputName": "售后媒体",
            "mediaAction": controller.MEDIA_RESTART_ACTION,
        },
    ) in fake_client.request_calls


@pytest.mark.asyncio
async def test_playback_ignores_new_triggers_while_busy():
    controller = ObsActionController()
    fake_client = FakeObsClient()
    fake_client.scene_items["物流场景"] = [{"sourceName": "物流媒体"}]
    fake_client.media_status["物流媒体"] = deque(
        [
            {
                "mediaState": "OBS_MEDIA_STATE_PLAYING",
                "mediaDuration": 5000,
                "mediaCursor": 100,
            },
            {
                "mediaState": "OBS_MEDIA_STATE_PLAYING",
                "mediaDuration": 5000,
                "mediaCursor": 200,
            },
            {
                "mediaState": "OBS_MEDIA_STATE_ENDED",
                "mediaDuration": 5000,
                "mediaCursor": 5000,
            },
        ]
    )
    controller._client = fake_client

    await controller.configure(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 4455,
            "main_scene": "主场景",
            "global_cooldown_sec": 0,
            "match_window_sec": 10,
            "min_hits": 1,
            "ignore_during_playback": True,
            "rules": [
                {
                    "enabled": True,
                    "name": "物流动作",
                    "keywords": ["发货"],
                    "target_scene": "物流场景",
                    "cooldown_sec": 0,
                }
            ],
        }
    )

    await controller.handle_chat_message("多久发货")
    await asyncio.sleep(0.1)
    await controller.handle_chat_message("什么时候发货")
    assert controller._playback_task is not None
    await controller._playback_task

    target_switch_count = sum(
        1
        for request_type, request_data in fake_client.request_calls
        if request_type == "SetCurrentProgramScene"
        and request_data.get("sceneName") == "物流场景"
    )
    assert target_switch_count == 1


@pytest.mark.asyncio
async def test_probe_reports_connected_when_configuration_is_valid():
    controller = ObsActionController()
    fake_client = FakeObsClient()
    fake_client.scene_names = {"主场景", "售后场景"}
    fake_client.scene_items["售后场景"] = [{"sourceName": "售后媒体"}]

    result = await controller.probe(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 4455,
            "main_scene": "主场景",
            "rules": [
                {
                    "enabled": True,
                    "name": "售后动作",
                    "keywords": ["售后"],
                    "target_scene": "售后场景",
                    "cooldown_sec": 0,
                }
            ],
        },
        client=fake_client,
    )

    assert result["state"] == "connected"
    assert result["connected"] is True
    assert result["short_text"] == "已连接"


@pytest.mark.asyncio
async def test_probe_reports_warning_when_target_scene_is_missing():
    controller = ObsActionController()
    fake_client = FakeObsClient()
    fake_client.scene_names = {"主场景"}

    result = await controller.probe(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 4455,
            "main_scene": "主场景",
            "rules": [
                {
                    "enabled": True,
                    "name": "售后动作",
                    "keywords": ["售后"],
                    "target_scene": "售后场景",
                    "cooldown_sec": 0,
                }
            ],
        },
        client=fake_client,
    )

    assert result["state"] == "warning"
    assert result["connected"] is True
    assert "目标场景不存在" in result["message"]


def test_settings_normalize_obs_rules():
    settings = ObsActionSettings.from_dict(
        {
            "enabled": True,
            "host": "",
            "port": "abc",
            "match_window_sec": 0,
            "min_hits": 99,
            "rules": [
                {
                    "enabled": "true",
                    "name": "售后动作",
                    "keywords": "售后，退货,售后",
                    "target_scene": "售后场景",
                    "cooldown_sec": "-1",
                }
            ],
        }
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 4455
    assert settings.match_window_sec == 1
    assert settings.min_hits == 20
    assert settings.rules[0].keywords == ("售后", "退货")
    assert settings.rules[0].cooldown_sec == 0
