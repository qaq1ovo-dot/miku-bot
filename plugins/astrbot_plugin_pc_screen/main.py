"""让 QQ 私聊里的初音看屏幕、动鼠标、敲键盘。

- 只在私聊生效
- 「看屏幕」除了发图，还会让视觉模型先描述一遍画面，这样她真的"知道"你在干什么
- 动作通过 SSH 隧道打到主人电脑上的 pc_agent（127.0.0.1:19883）
"""

from __future__ import annotations

import os
import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

DEFAULT_BASE = os.environ.get("PC_AGENT_URL", "http://127.0.0.1:19883")
DEFAULT_TOKEN = os.environ.get("PC_AGENT_TOKEN", "")
VISION_URL = "https://api.deepseek.com/chat/completions"
VISION_MODEL = "deepseek-flash"
VISION_PROMPT = (
    "简要描述这张电脑屏幕截图（100 字以内，中文）。"
    "如果画面里有游戏，说明是哪个游戏、当前停留在什么界面；"
    "如果有聊天窗口或文档，概括它们在讲什么。不要逐字复述内容。"
)


@register(
    "astrbot_plugin_pc_screen",
    "dsh",
    "看屏幕 / 点鼠标 / 敲键盘（仅私聊）",
    "3.0.0",
)
class PcScreenPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}

    def _base(self) -> str:
        return (self.config.get("agent_url") or DEFAULT_BASE).rstrip("/")

    def _token(self) -> str:
        return self.config.get("token") or DEFAULT_TOKEN

    def _allowed(self, event: AstrMessageEvent) -> bool:
        owners = self.config.get("owner_ids") or []
        if not owners:
            return True
        return str(event.get_sender_id()) in [str(x) for x in owners]

    def _deny(self, event: AstrMessageEvent):
        if self._allowed(event):
            return None
        return None

    async def _post_action(self, payload: dict) -> dict:
        url = self._base() + "/action"
        timeout = float(self.config.get("timeout_seconds", 15.0))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers={"X-Auth-Token": self._token()},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    return await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pc-screen] action 失败: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def _grab(self) -> dict:
        url = self._base() + "/screenshot"
        timeout = float(self.config.get("timeout_seconds", 15.0))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"X-Auth-Token": self._token()},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    return await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pc-screen] screenshot 失败: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def _describe(self, image_b64: str) -> str:
        """把截图丢给视觉模型，返回一句中文描述。"""
        key = self.config.get("vision_api_key") or ""
        if not key:
            return ""
        payload = {
            "model": self.config.get("vision_model") or VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 300,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.get("vision_url") or VISION_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=30.0),
                ) as resp:
                    data = await resp.json()
            message = data["choices"][0]["message"]
            text = (message.get("content") or "").strip()
            if not text:
                # 有些模型把描述写进 reasoning_content，content 反而是空的
                text = (message.get("reasoning_content") or "").strip()
            return text[:600]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pc-screen] 视觉描述失败: %s", exc)
            return ""

    @filter.llm_tool(name="look_screen")
    async def look_screen_tool(self, event: AstrMessageEvent, reason: str = ""):
        '''看一眼主人电脑当前的屏幕。当主人说"看看我屏幕"、"我在干嘛"、"屏幕上是什么"、
        "帮我看看这个界面"时调用。调用后你会收到一段画面描述，可以据此回答主人。

        Args:
            reason(string): 为什么要看，例如"主人让我看他现在在做什么"
        '''
        if event.get_group_id():
            return "这个功能只在私聊可用，群里不行。"
        if not self._allowed(event):
            event.stop_event()
            return "（跳过）"
        data = await self._grab()
        if not data.get("ok"):
            return f"截图失败：{data.get('error') or '电脑没连上'}。让主人检查电脑上的隧道和 pc_agent 窗口。"
        image_b64 = data.get("image", "")
        desc = await self._describe(image_b64)
        if desc:
            return f"（已经把截图发给主人了）画面内容：{desc}"
        return "（已经把截图发给主人了）但我看不太清画面内容，你可以直接问主人。"

    @filter.command("看屏幕", alias={"截图", "看看屏幕"})
    async def look_screen(self, event: AstrMessageEvent):
        """截图并描述画面（仅私聊）"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        data = await self._grab()
        if not data.get("ok"):
            yield event.plain_result(
                f"连不上你的电脑：{data.get('error') or '未知错误'}\n"
                "检查一下隧道窗口和 pc_agent 是不是都开着。"
            )
            return
        image_b64 = data.get("image", "")
        chain = MessageChain([Image.fromBase64(image_b64)])
        await event.send(chain)
        desc = await self._describe(image_b64)
        if desc:
            yield event.plain_result(f"我看到的是：{desc}")

    @filter.command("点击")
    async def click(self, event: AstrMessageEvent, x: str = "", y: str = "", button: str = "left"):
        """点击屏幕坐标：/点击 800 450 [right]"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        try:
            px, py = int(x), int(y)
        except ValueError:
            yield event.plain_result("用法：/点击 800 450 [right]")
            return
        data = await self._post_action({"action": "click", "x": px, "y": py, "button": button})
        yield event.plain_result("点好了。" if data.get("ok") else f"点失败：{data.get('error')}")

    @filter.command("按键")
    async def press_key(self, event: AstrMessageEvent, key: str = ""):
        """按键：/按键 enter 或 /按键 ctrl+c"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        if not key:
            yield event.plain_result("用法：/按键 enter（支持 ctrl+c、f5 这种组合）")
            return
        data = await self._post_action({"action": "key", "key": key})
        yield event.plain_result("按下去了。" if data.get("ok") else f"失败：{data.get('error')}")

    @filter.command("输入")
    async def type_text(self, event: AstrMessageEvent, text: str = ""):
        """在当前焦点处输入文字：/输入 你好"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        if not text:
            yield event.plain_result("用法：/输入 要输入的内容")
            return
        data = await self._post_action({"action": "type", "text": text})
        yield event.plain_result(
            f"输入了 {data.get('length', 0)} 个字符。" if data.get("ok") else f"失败：{data.get('error')}"
        )

    @filter.command("滚轮")
    async def scroll(self, event: AstrMessageEvent, dy: str = "-3"):
        """滚轮：/滚轮 -3（负数向下）"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        try:
            value = int(dy)
        except ValueError:
            value = -3
        data = await self._post_action({"action": "scroll", "dy": value})
        yield event.plain_result("滚好了。" if data.get("ok") else f"失败：{data.get('error')}")

    @filter.command("鼠标")
    async def move(self, event: AstrMessageEvent, x: str = "", y: str = ""):
        """移动鼠标：/鼠标 800 450"""
        if event.get_group_id():
            yield event.plain_result("这个指令只能在私聊里用哦。")
            return
        if not self._allowed(event):
            event.stop_event()
            return
        try:
            px, py = int(x), int(y)
        except ValueError:
            yield event.plain_result("用法：/鼠标 800 450")
            return
        data = await self._post_action({"action": "move", "x": px, "y": py})
        yield event.plain_result("移过去了。" if data.get("ok") else f"失败：{data.get('error')}")
