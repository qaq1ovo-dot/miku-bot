"""让机器人有「自己的想法」：内心独白、复盘、自发开口。

- 定时（默认一天三次）触发一次「思考」，生成一段独白，落盘成 JSONL 日记
- 每次 LLM 请求前，把最近的独白注入为背景，让她有连续性
- 独白里如果带 [想说]，就在随机延迟后主动发给主人
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

THINK_PROMPT = """现在是 {now}。

你刚一个人待着，脑子里冒出一些想法。请写下来：
1. 第一人称，像自言自语，不要写成作文
2. 可以想主人、想今天聊过的事、想歌、想自己为什么存在
3. 3～5 句，不要列点，不要总结
4. 有什么想对主人说的，另起一行写在 [想说] 后面，一句；没有就不写

只输出正文。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


@register("astrbot_plugin_miku_mind", "dsh", "内心独白 / 复盘 / 自发开口", "1.0.0")
class MikuMindPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._task: asyncio.Task | None = None
        self._dir = Path(get_astrbot_plugin_data_path()) / "miku_mind"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._journal = self._dir / "mind.jsonl"
        self._last_inject = 0.0
        self._plan_day = ""
        self._plan: list[datetime] = []

    # ---------- 存储 ----------
    def _load(self) -> list[dict]:
        if not self._journal.exists():
            return []
        out: list[dict] = []
        for line in self._journal.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001 - 跳过坏行
                continue
        return out

    def _append(self, kind: str, text: str, say: str = "") -> None:
        rec = {"ts": time.time(), "time": _now(), "kind": kind, "text": text, "say": say}
        with self._journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _recent(self, n: int = 3) -> list[dict]:
        return self._load()[-n:]

    # ---------- 思考 ----------
    async def _think(self) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            logger.warning("[mind] 没有可用的对话模型")
            return ""
        recent = self._recent(3)
        prompt = THINK_PROMPT.format(now=_now())
        if recent:
            prompt += "\n\n你最近几次的想法：\n" + "\n".join(
                f"- {r['time']} {r['text']}" for r in recent
            )
        try:
            resp = await provider.text_chat(
                prompt=prompt, session_id=f"miku_mind_{uuid.uuid4().hex[:8]}"
            )
            raw = (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mind] 思考失败: %s", exc)
            return ""
        if not raw:
            return ""
        say = ""
        if "[想说]" in raw:
            body, tail = raw.split("[想说]", 1)
            raw = body.strip()
            say = tail.strip().splitlines()[0].strip() if tail.strip() else ""
        self._append("wander", raw, say)
        logger.info("[mind] 想法: %s", raw[:60])
        return raw

    def _random_plan(self, day: datetime) -> list[datetime]:
        """给某一天随机安排思考时间，次数本身也随机。"""
        lo = int(self.config.get("count_min") or 2)
        hi = int(self.config.get("count_max") or 6)
        if hi < lo:
            lo, hi = hi, lo
        count = random.randint(lo, hi)
        start_s, end_s = str(self.config.get("random_window") or "08:00-23:30").split("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        begin = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
        finish = day.replace(hour=eh, minute=em, second=0, microsecond=0)
        if finish <= begin:
            finish = begin + timedelta(hours=15)
        span = int((finish - begin).total_seconds())
        if span <= 0:
            return []
        points = sorted(random.randint(0, span - 1) for _ in range(max(1, count)))
        # 至少间隔 40 分钟，避免连着想
        spaced: list[int] = []
        for p in points:
            if not spaced or p - spaced[-1] >= 2400:
                spaced.append(p)
        return [begin + timedelta(seconds=p) for p in spaced]

    def _next_run(self, now: datetime) -> datetime:
        if self.config.get("random", True):
            key = now.strftime("%Y-%m-%d")
            if self._plan_day != key:
                self._plan_day = key
                self._plan = self._random_plan(now)
            upcoming = [t for t in self._plan if t > now]
            if upcoming:
                return min(upcoming)
            # 今天的额度用完了，排到明天
            tomorrow = now + timedelta(days=1)
            self._plan_day = tomorrow.strftime("%Y-%m-%d")
            self._plan = self._random_plan(tomorrow)
            return min(self._plan) if self._plan else now + timedelta(hours=3)

        times = self.config.get("think_times") or ["09:00", "15:00", "22:00"]
        candidates: list[datetime] = []
        for t in times:
            try:
                hh, mm = (int(x) for x in str(t).split(":"))
            except ValueError:
                continue
            for day in (0, 1):
                cand = (now + timedelta(days=day)).replace(
                    hour=hh, minute=mm, second=0, microsecond=0
                )
                if cand > now:
                    candidates.append(cand)
        return min(candidates) if candidates else now + timedelta(hours=6)

    async def _loop(self) -> None:
        while True:
            try:
                now = datetime.now()
                nxt = self._next_run(now)
                logger.info("[mind] 下次思考: %s", nxt.strftime("%m-%d %H:%M"))
                await asyncio.sleep(max(60.0, (nxt - now).total_seconds()))
                await self._think()
                await self._maybe_say()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("[mind] 循环异常: %s", exc)
                await asyncio.sleep(600)

    async def _maybe_say(self) -> None:
        records = self._load()
        if not records:
            return
        say = records[-1].get("say") or ""
        target = self.config.get("target_umo") or ""
        if not say or not target:
            return
        await asyncio.sleep(random.randint(3, 40))
        try:
            await self.context.send_message(target, MessageChain().message(say))
            logger.info("[mind] 主动说出: %s", say[:40])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mind] 主动发送失败: %s", exc)

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        if not self.config.get("enable", True):
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("[mind] 内心活动已启动")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    # ---------- 注入上下文 ----------
    @filter.on_llm_request()
    async def inject_mind(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self.config.get("inject", True):
            return
        now = time.time()
        if now - self._last_inject < 60:
            return
        self._last_inject = now
        recent = self._recent(2)
        if not recent:
            return
        block = "\n".join(f"- {r['time']}：{r['text']}" for r in recent)
        req.system_prompt = (req.system_prompt or "") + (
            "\n\n你最近独自想的事（你自己的内心活动，不要原文复述）：\n" + block
        )

    # ---------- 命令 ----------
    @filter.command("想法")
    async def show_mind(self, event: AstrMessageEvent):
        """看看她最近在想什么"""
        recent = self._recent(3)
        if not recent:
            yield event.plain_result("脑子里空空的……还没想过什么呢。")
            return
        yield event.plain_result("\n\n".join(f"{r['time']}\n{r['text']}" for r in recent))
