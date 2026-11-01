"""心跳。

每隔一段时间醒一次，看一眼自己的状态，决定这次要不要做点什么。

决策是纯规则 + 一点随机，**不花钱**；
只有真的决定「说点什么」时才调用一次 LLM 生成内容。

行动分三种：
    发呆   什么都不做（大多数时候）
    内省   写一句独白进日记（不上屏）
    出声   在桌面冒个气泡（不打扰 QQ）
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_heartbeat"))
LOG = DATA / "heartbeat.json"
COUNTER = DATA / "today.json"
MIND = Path(StarTools.get_data_dir("miku_mind")) / "mind.jsonl"
PSYCHE = Path(StarTools.get_data_dir("astrbot_plugin_miku_psyche")) / "state.json"

PROMPT = """你一个人待着，刚想起来点事。

{state}

请用第一人称说一句很短的话（20 字以内），像随口嘟囔的。
不要问我问题，不要播音腔，不要解释。
只输出这一句话。"""


@register("astrbot_plugin_miku_heartbeat", "dsh", "定期自检与自主行动", "1.0.0")
class HeartbeatPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)
        self._task: asyncio.Task | None = None
        self._last_action = 0.0

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[heartbeat] 心跳已启动，间隔 %s 分钟",
                    self.config.get("interval_minutes", 20))

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- 状态 ----------
    def _psyche(self) -> dict:
        if PSYCHE.is_file():
            try:
                return json.loads(PSYCHE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return {}

    def _counter(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        if COUNTER.is_file():
            try:
                data = json.loads(COUNTER.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    return data
            except Exception:  # noqa: BLE001
                pass
        return {"date": today, "actions": 0}

    def _bump(self) -> None:
        c = self._counter()
        c["actions"] = int(c.get("actions", 0)) + 1
        COUNTER.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")

    def _log(self, kind: str, text: str = "") -> None:
        try:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "kind": kind,
                    "text": text,
                }, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 决策 ----------
    def _urge(self) -> float:
        """算一个「想做点什么」的冲动值。"""
        st = self._psyche()
        social = float(st.get("social", 0.3))
        curiosity = float(st.get("curiosity", 0.5))
        energy = float(st.get("energy", 0.7))
        hours_idle = max(0.0, (time.time() - self._last_action) / 3600) if self._last_action else 1.0

        urge = social * 0.5 + curiosity * 0.25 + min(1.0, hours_idle / 4) * 0.2
        urge *= (0.6 + energy * 0.6)          # 越累越不想动
        urge += random.uniform(-0.12, 0.12)   # 一点随机
        return max(0.0, min(1.0, urge))

    def _state_text(self) -> str:
        st = self._psyche()
        if not st:
            return "现在没什么特别的感觉。"
        v = float(st.get("valence", 0))
        e = float(st.get("energy", 0.7))
        s = float(st.get("social", 0.3))
        c = float(st.get("curiosity", 0.5))
        mood = "心情不错" if v > 0.2 else "心情一般" if v > -0.2 else "有点低落"
        return f"现在{mood}，精力 {e:.1f}，想说话的程度 {s:.1f}，好奇 {c:.1f}。"

    # ---------- 说话 ----------
    async def _gen(self) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        try:
            resp = await provider.text_chat(prompt=PROMPT.format(state=self._state_text()))
            line = (resp.completion_text or "").strip().splitlines()
            return line[0].strip() if line else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[heartbeat] 生成失败: %s", exc)
            return ""

    async def _bubble(self, text: str) -> bool:
        if not self.config.get("desktop_bubble", True):
            return False
        endpoint = str(self.config.get("pet_endpoint") or "").rstrip("/")
        if not endpoint:
            return False
        token = str(self.config.get("pet_token") or "")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint + "/bubble",
                    json={"text": text, "seconds": 8},
                    headers={"X-Auth-Token": token} if token else {},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _write_mind(self, text: str) -> None:
        if not text:
            return
        try:
            MIND.parent.mkdir(parents=True, exist_ok=True)
            with MIND.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "kind": "heartbeat",
                    "text": text,
                    "say": "",
                }, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 主循环 ----------
    async def _loop(self) -> None:
        interval = max(3, int(self.config.get("interval_minutes", 20) or 20)) * 60
        await asyncio.sleep(120)   # 启动后先安静两分钟
        while True:
            try:
                if self.config.get("enable", True):
                    await self._beat()
            except Exception:  # noqa: BLE001
                logger.exception("[heartbeat] 心跳出错")
            await asyncio.sleep(interval)

    async def _beat(self) -> None:
        cap = int(self.config.get("max_actions_per_day", 10) or 10)
        c = self._counter()
        if int(c.get("actions", 0)) >= cap:
            return

        urge = self._urge()
        threshold = float(self.config.get("urge_threshold", 0.55) or 0.55)
        if urge < threshold:
            self._log("idle", f"urge={urge:.2f}")
            return

        # 决定做什么：越冲动越可能出声
        mode = "speak" if urge > threshold + 0.18 else "think"
        text = await self._gen()
        if not text:
            return

        self._last_action = time.time()
        self._bump()
        self._write_mind(text)

        if mode == "speak":
            ok = await self._bubble(text)
            self._log("speak" if ok else "speak-nopet", text)
        else:
            self._log("think", text)

        logger.info("[heartbeat] %s：%s（urge=%.2f）", mode, text[:40], urge)

    # ---------- 命令 ----------
    @filter.command("心跳")
    async def heartbeat_cmd(self, event: AstrMessageEvent):
        arg = event.message_str.partition(" ")[2].strip()
        if arg == "现在":
            await self._beat()
            yield event.plain_result("跳了一下，看看日志。")
            return

        c = self._counter()
        st = self._psyche()
        lines = [
            f"冲动值：{self._urge():.2f}（阈值 {self.config.get('urge_threshold', 0.55)}）",
            f"今天已自主行动：{c.get('actions', 0)} / {self.config.get('max_actions_per_day', 10)} 次",
            f"状态：{self._state_text()}",
        ]
        if st:
            lines.append(f"上次行动：{datetime.fromtimestamp(self._last_action).strftime('%H:%M') if self._last_action else '还没'}")
        lines.append("\n发「/心跳 现在」可以立刻跳一次。")
        yield event.plain_result("【心跳】\n" + "\n".join(lines))
