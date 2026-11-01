"""桌宠桥接 v2.1：双向。

两个方向：
1. QQ → 桌宠：她回复后推送 /say，并带上当前情绪 → 桌宠换动作
2. 桌宠 → 她：桌宠上的聊天框 POST /chat，这里用同一个 provider 生成回复

情绪来源：读 miku_psyche 插件写下的 state.json（同一台机器，直接读文件）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import web

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DEFAULT_ENDPOINT = "http://127.0.0.1:19890"
PSYCHE_STATE = Path(StarTools.get_data_dir("astrbot_plugin_miku_psyche")) / "state.json"


@register("astrbot_plugin_miku_pet2", "dsh", "桌宠双向桥接（含情绪同步）", "2.1.0")
class PetBridgePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._mood: dict = {}

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        if self.config.get("enable", True):
            await self._start_chat_server()
            self._mood_task = asyncio.create_task(self._mood_loop())

    async def terminate(self) -> None:
        task = getattr(self, "_mood_task", None)
        if task:
            task.cancel()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._session and not self._session.closed:
            await self._session.close()

    async def _mood_loop(self) -> None:
        """定时把情绪推给桌宠，让它的动作一直跟着状态走。"""
        while True:
            await asyncio.sleep(max(60, int(self.config.get("mood_sync_minutes", 5) or 5) * 60))
            try:
                if not self.config.get("sync_mood", True):
                    continue
                state = self._mood_state()
                await self._post("state", {"name": state, "duration": 3})
            except Exception:  # noqa: BLE001
                logger.debug("[pet2] 情绪同步失败")

    # ---------- 桌宠 API ----------
    def _endpoint(self) -> str:
        return str(self.config.get("endpoint") or DEFAULT_ENDPOINT).rstrip("/")

    def _headers(self) -> dict:
        token = str(self.config.get("token") or "")
        return {"X-Auth-Token": token} if token else {}

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, action: str, payload: dict) -> bool:
        if not self.config.get("enable", True):
            return False
        url = f"{self._endpoint()}/{action}"
        timeout = float(self.config.get("timeout", 5.0) or 5.0)
        try:
            session = await self._session_get()
            async with session.post(url, json=payload, headers=self._headers(),
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                return resp.status == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("[pet2] %s 失败: %s", action, exc)
            return False

    # ---------- 情绪 ----------
    def _read_mood(self) -> dict:
        try:
            if PSYCHE_STATE.exists():
                self._mood = json.loads(PSYCHE_STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return self._mood

    def _mood_state(self) -> str:
        """把情绪映射成桌宠的动画状态。"""
        m = self._read_mood()
        if not m:
            return "idle"
        try:
            energy = float(m.get("energy", 0.7))
            valence = float(m.get("valence", 0.1))
            social = float(m.get("social", 0.3))
        except Exception:  # noqa: BLE001
            return "idle"
        if energy < 0.25:
            return "thinking"          # 累了，动作慢一点
        if valence > 0.5:
            return "success"           # 心情好，蹦一下
        if social > 0.75:
            return "notice"            # 很想说话，引起注意
        return "idle"

    def _mood_text(self) -> str:
        m = self._read_mood()
        if not m:
            return ""
        try:
            v = float(m.get("valence", 0))
            e = float(m.get("energy", 0.7))
            s = float(m.get("social", 0.3))
        except Exception:  # noqa: BLE001
            return ""
        mood = "心情很好" if v > 0.5 else "心情不错" if v > 0.15 else "心情平静" if v > -0.15 else "有点低落"
        energy = "精神很好" if e > 0.75 else "还算精神" if e > 0.45 else "有点累"
        social = "很想找人说话" if s > 0.75 else "有点想聊天" if s > 0.5 else "安静着"
        return f"{mood}，{energy}，{social}"

    # ---------- 桌宠 → 她 ----------
    async def _start_chat_server(self) -> None:
        """起一个极小的 HTTP 服务，让桌宠能跟她说话。"""
        port = int(self.config.get("chat_port", 19891) or 19891)
        token = str(self.config.get("chat_token") or "")

        async def handle_chat(request: web.Request) -> web.Response:
            if token and request.headers.get("X-Auth-Token") != token:
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return web.json_response({"ok": False, "error": "bad json"}, status=400)
            text = str(body.get("text") or "").strip()
            if not text:
                return web.json_response({"ok": False, "error": "empty"}, status=400)

            reply = await self._generate(text)
            if not reply:
                return web.json_response({"ok": False, "error": "no reply"}, status=500)

            # 桌宠的聊天框自己显示气泡；这里顺便把情绪状态带回去
            return web.json_response({
                "ok": True,
                "reply": reply,
                "mood": self._mood_text(),
                "state": self._mood_state(),
            })

        async def handle_mood(request: web.Request) -> web.Response:
            return web.json_response({
                "ok": True,
                "mood": self._mood_text(),
                "state": self._mood_state(),
                "raw": self._read_mood(),
            })

        app = web.Application()
        app.router.add_post("/chat", handle_chat)
        app.router.add_get("/mood", handle_mood)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        await site.start()
        logger.info("[pet2] 桌宠聊天接口已启动，端口 %s", port)

    async def _generate(self, text: str) -> str:
        """用当前 provider 生成回复，带上人格与情绪。"""
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        persona_id = str(self.config.get("persona_id") or "miku")
        system_prompt = ""
        try:
            persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
            if isinstance(persona, dict):
                system_prompt = str(persona.get("prompt") or "")
        except Exception:  # noqa: BLE001
            system_prompt = ""
        if not system_prompt:
            try:
                persona = self.context.persona_manager.get_default_persona_v3()
                if isinstance(persona, dict):
                    system_prompt = str(persona.get("prompt") or "")
            except Exception:  # noqa: BLE001
                system_prompt = ""

        mood = self._mood_text()
        if mood:
            system_prompt = (system_prompt + f"\n\n[你现在的状态]\n{mood}").strip()

        try:
            resp = await provider.text_chat(prompt=text, system_prompt=system_prompt or None)
            return (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pet2] 生成失败: %s", exc)
            return ""

    # ---------- QQ → 桌宠 ----------
    @filter.on_llm_request()
    async def on_thinking(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("sync_thinking", True):
            return
        await self._post("state", {"name": "thinking", "duration": 0})

    @filter.on_llm_response()
    async def on_reply(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        text = (getattr(resp, "completion_text", "") or "").strip()
        if not text:
            return
        limit = int(self.config.get("max_chars", 120) or 120)
        shown = text if len(text) <= limit else text[:limit] + "…"
        await self._post("say", {"text": shown, "voice": bool(self.config.get("voice", True))})

        if self.config.get("sync_mood", True):
            state = self._mood_state()
            if state != "idle":
                await self._post("state", {"name": state, "duration": 3})

    # ---------- 命令 ----------
    @filter.command("桌宠")
    async def pet_ctl(self, event: AstrMessageEvent):
        """桌宠 <say 文本|state 状态名|mood|ping>"""
        arg = event.message_str.partition(" ")[2].strip()
        if not arg or arg == "ping":
            ok = await self._post("ping", {})
            yield event.plain_result("桌宠在线" if ok else "桌宠没响应（检查隧道和 config）")
            return
        if arg == "mood":
            yield event.plain_result(self._mood_text() or "还没读到情绪状态")
            return

        action, _, value = arg.partition(" ")
        if action == "say" and value:
            ok = await self._post("say", {"text": value, "voice": True})
            yield event.plain_result("已发送" if ok else "发送失败")
        elif action == "state" and value:
            ok = await self._post("state", {"name": value, "duration": 0})
            yield event.plain_result("已切换" if ok else "切换失败（状态名可能不存在）")
        else:
            yield event.plain_result("用法：/桌宠 ping | mood | say 文本 | state idle")
