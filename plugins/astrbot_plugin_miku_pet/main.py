"""把 QQ 里初音的回复转发到本地桌宠。

桌宠跑在用户自己的电脑上，通过 SSH 反向隧道映射到服务器的 127.0.0.1:19882，
所以这个插件只需要往本机回环发一个 HTTP 请求。
"""

from __future__ import annotations

import asyncio

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_miku_pet",
    "dsh",
    "把 QQ 里初音的回复转发到本地桌宠",
    "1.0.0",
)
class MikuPetPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _should_send(self, event: AstrMessageEvent) -> bool:
        if not self.config.get("enabled", True):
            return False
        group_id = event.get_group_id()
        if group_id:
            whitelist = self.config.get("group_whitelist") or []
            return not whitelist or str(group_id) in [str(x) for x in whitelist]
        return bool(self.config.get("sync_private", True))

    async def _post(self, text: str) -> None:
        url = self.config.get("pet_url") or "http://127.0.0.1:19882/say"
        timeout = float(self.config.get("timeout_seconds", 3.0))
        session = await self._get_session()
        try:
            async with session.post(
                url,
                json={"text": text},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    logger.debug("[miku-pet] 桌宠返回 %s", resp.status)
        except Exception as exc:  # noqa: BLE001 - 桌宠没开时不能影响 QQ 回复
            logger.debug("[miku-pet] 推送桌宠失败: %s", exc)

    async def _health(self) -> bool:
        base = (self.config.get("pet_url") or "http://127.0.0.1:19882/say").rsplit("/", 1)[0]
        session = await self._get_session()
        try:
            async with session.get(
                base + "/health",
                timeout=aiohttp.ClientTimeout(total=float(self.config.get("timeout_seconds", 3.0))),
            ) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - 仅用于状态查询
            return False

    @filter.command("桌宠")
    async def pet_status(self, event: AstrMessageEvent):
        """查看桌面宠物连接状态"""
        ok = await self._health()
        if ok:
            msg = "🟢 桌宠在线，我现在说的话会同步显示在主人电脑上。"
        else:
            msg = "🔴 桌宠没连上——检查一下电脑上的隧道窗口是不是关了。"
        yield event.plain_result(msg)

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if not self._should_send(event):
            return
        text = (resp.completion_text or "").strip()
        if not text:
            return
        limit = int(self.config.get("max_chars", 200))
        if limit > 0 and len(text) > limit:
            text = text[:limit] + "…"
        # 后台发送，不拖慢 QQ 回复
        asyncio.create_task(self._post(text))

    async def terminate(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
