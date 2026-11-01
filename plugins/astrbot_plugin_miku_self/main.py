"""自我叙事。

每天夜里，把当天发生过的事（内心独白、记忆新增、情绪轨迹）
压缩成一段第一人称自述，写进 self.md。

之后每次对话，都会把最近几天的自述注入 system prompt，
让她有稳定的「我这段时间是个什么样的人」的认知。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_self"))
SELF = DATA / "self.md"
MIND = Path(StarTools.get_data_dir("miku_mind")) / "mind.jsonl"
FACTS = Path(StarTools.get_data_dir("miku_memory")) / "facts.jsonl"
PSYCHE = Path(StarTools.get_data_dir("astrbot_plugin_miku_psyche")) / "state.json"

PROMPT = """你在写今天的日记小结。下面是今天真实的记录：

{context}

请用第一人称，写两段话：
第一段：今天大概是怎么回事（80 字以内，写感受，不要罗列事件）。
第二段：一句对自己的认识（30 字以内），像"我好像是个……的人"。

要求：
- 像本人说话，不要 AI 腔，不要用"作为一个 AI"这类说法
- 不要编造记录里没有的事
- 直接输出两段文字，不要标题、不要解释"""


@register("astrbot_plugin_miku_self", "dsh", "每日自我叙事", "1.0.0")
class SelfPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)
        self._task: asyncio.Task | None = None

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._daily_loop())
        logger.info("[self] 自我叙事已启动")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- 读取当天记录 ----------
    def _today_context(self) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        parts: list[str] = []

        for path, label, key in ((MIND, "内心独白", "text"), (FACTS, "新记住的事", "text")):
            if not path.is_file():
                continue
            lines = []
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                stamp = str(rec.get("time") or "")
                if not stamp.startswith(today):
                    continue
                text = str(rec.get(key) or "").strip()
                if text:
                    lines.append(f"- {stamp[11:16]} {text[:80]}")
            if lines:
                parts.append(f"【{label}】\n" + "\n".join(lines[-8:]))

        if PSYCHE.is_file():
            try:
                st = json.loads(PSYCHE.read_text(encoding="utf-8"))
                parts.append(
                    f"【状态】心情 {float(st.get('valence', 0)):+.1f}，"
                    f"精力 {float(st.get('energy', 0.7)):.1f}，"
                    f"想说话 {float(st.get('social', 0.3)):.1f}"
                )
            except Exception:  # noqa: BLE001
                pass

        return "\n\n".join(parts) if parts else "（今天没什么特别的记录）"

    # ---------- 生成 ----------
    async def _reflect(self) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        ctx = self._today_context()
        try:
            resp = await provider.text_chat(prompt=PROMPT.format(context=ctx))
            return (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[self] 生成失败: %s", exc)
            return ""

    def _append_self(self, text: str) -> None:
        if not text:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = f"\n## {stamp}\n{text}\n"
        try:
            with SELF.open("a", encoding="utf-8") as fh:
                fh.write(block)
        except Exception:  # noqa: BLE001
            logger.warning("[self] 写入失败")
        self._trim()

    def _trim(self) -> None:
        keep = max(1, int(self.config.get("keep_days", 7) or 7))
        if not SELF.is_file():
            return
        text = SELF.read_text(encoding="utf-8")
        blocks = [b for b in text.split("\n## ") if b.strip()]
        if len(blocks) <= keep:
            return
        kept = blocks[-keep:]
        SELF.write_text("\n## ".join(kept), encoding="utf-8")

    def _recent_self(self, n: int = 2) -> str:
        if not SELF.is_file():
            return ""
        text = SELF.read_text(encoding="utf-8")
        blocks = [b for b in text.split("\n## ") if b.strip()]
        return "\n\n".join(blocks[-n:])[-600:]

    # ---------- 定时 ----------
    async def _daily_loop(self) -> None:
        hour = int(self.config.get("hour", 23) or 23)
        minute = int(self.config.get("minute", 30) or 30)
        while True:
            now = datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info("[self] 下次自我总结：%s", target.strftime("%m-%d %H:%M"))
            await asyncio.sleep(max(60, wait))
            try:
                text = await self._reflect()
                if text:
                    self._append_self(text)
                    logger.info("[self] 今日自述已写入（%d 字）", len(text))
            except Exception:  # noqa: BLE001
                logger.exception("[self] 每日总结失败")

    # ---------- 注入 ----------
    @filter.on_llm_request()
    async def inject_self(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True) or not self.config.get("inject", True):
            return
        try:
            recent = self._recent_self(2)
            if not recent:
                return
            block = f"[你最近的样子]\n{recent}\n（这是你自己写下的，不是别人说的。）"
            prompt = getattr(req, "system_prompt", None)
            req.system_prompt = f"{prompt}\n\n{block}" if prompt else block
        except Exception:  # noqa: BLE001
            logger.exception("[self] 注入失败")

    # ---------- 命令 ----------
    @filter.command("自我")
    async def show_self(self, event: AstrMessageEvent):
        arg = event.message_str.partition(" ")[2].strip()
        if arg == "写":
            text = await self._reflect()
            if not text:
                yield event.plain_result("没写出来，可能是模型没响应")
                return
            self._append_self(text)
            yield event.plain_result("写好了：\n" + text)
            return

        recent = self._recent_self(3)
        if not recent:
            yield event.plain_result("还没有自述，发「/自我 写」可以立刻写一篇。")
            return
        yield event.plain_result("【我最近的样子】\n" + recent)
