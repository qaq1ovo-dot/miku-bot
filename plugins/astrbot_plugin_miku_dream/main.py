"""睡眠与做梦。

逻辑很简单，但效果挺像那么回事：

    很久没消息  →  睡着（记录睡着的时刻）
    有人说话    →  醒来，用睡着期间的反刍内容拼成一个梦
    之后对话    →  梦会作为隐约的印象出现在上下文里

梦不是随便编的：素材来自她最近的记忆、独白和当天说过的话，
所以梦里的东西往往和你们最近聊的有关。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_dream"))
STATE = DATA / "state.json"
DREAMS = DATA / "dreams.jsonl"
FACTS = Path(StarTools.get_data_dir("miku_memory")) / "facts.jsonl"
MIND = Path(StarTools.get_data_dir("miku_mind")) / "mind.jsonl"

DREAM_PROMPT = """你刚睡醒，做了一个梦。梦的素材来自你最近惦记的事：

{material}

请描述这个梦，用第一人称，80 字以内。
要求：
- 有具体的画面和动作，不要解释象征意义
- 可以荒诞，但语气要平静，像真的经历过
- 不要出现"梦见了""我做了个梦"这类提示语，直接描述
- 不要写"醒来后我明白了……"这种总结

只输出梦的描述本身。"""


def _read_jsonl(path: Path, limit: int = 20) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


@register("astrbot_plugin_miku_dream", "dsh", "睡眠与做梦", "1.0.0")
class DreamPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)

    # ---------- 状态 ----------
    def _state(self) -> dict:
        if STATE.is_file():
            try:
                return json.loads(STATE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return {"asleep": False, "last_seen": time.time(), "sleep_at": 0.0}

    def _save(self, st: dict) -> None:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 素材 ----------
    def _material(self) -> str:
        """挑几条最近的记忆和独白当梦的原料。"""
        parts: list[str] = []
        facts = _read_jsonl(FACTS, 12)
        minds = _read_jsonl(MIND, 8)

        picked_facts = random.sample(facts, min(3, len(facts))) if facts else []
        for f in picked_facts:
            text = str(f.get("text") or "").strip()
            if text:
                parts.append(f"- {text}")

        picked_mind = random.sample(minds, min(2, len(minds))) if minds else []
        for m in picked_mind:
            text = str(m.get("text") or "").strip()
            if text:
                parts.append(f"- {text[:60]}")

        return "\n".join(parts) if parts else "- 什么都没想，脑子里空空的"

    # ---------- 做梦 ----------
    async def _dream(self) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        try:
            resp = await provider.text_chat(prompt=DREAM_PROMPT.format(material=self._material()))
            text = (resp.completion_text or "").strip()
            return text[:200]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dream] 做梦失败: %s", exc)
            return ""

    def _save_dream(self, text: str) -> None:
        if not text:
            return
        rec = {
            "ts": time.time(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "text": text,
        }
        with DREAMS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # 只保留最近若干天
        keep = max(1, int(self.config.get("keep_days", 7) or 7))
        rows = _read_jsonl(DREAMS, 500)
        cutoff = time.time() - keep * 86400
        rows = [r for r in rows if float(r.get("ts", 0)) >= cutoff]
        DREAMS.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        logger.info("[dream] 梦：%s", text[:40])

    def _latest_dream(self) -> str:
        rows = _read_jsonl(DREAMS, 5)
        if not rows:
            return ""
        last = rows[-1]
        stamp = str(last.get("time") or "")
        today = datetime.now().strftime("%Y-%m-%d")
        if not stamp.startswith(today):
            return ""
        return str(last.get("text") or "")

    # ---------- 睡眠检测 ----------
    def _should_sleep(self, idle_minutes: float) -> bool:
        if idle_minutes < float(self.config.get("sleep_after_minutes", 180) or 180):
            return False
        if not self.config.get("night_only", False):
            return True
        hour = datetime.now().hour
        return hour >= 23 or hour < 9

    # ---------- 钩子 ----------
    @filter.on_llm_request()
    async def wake_or_inject(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True):
            return
        try:
            st = self._state()
            now = time.time()
            idle_minutes = (now - float(st.get("last_seen", now))) / 60

            # 1) 醒来：如果有睡眠记录，做一个梦
            if st.get("asleep"):
                slept = float(st.get("sleep_at") or now)
                slept_minutes = (now - slept) / 60
                st["asleep"] = False
                st["last_seen"] = now
                st.pop("sleep_at", None)
                self._save(st)
                if slept_minutes >= 20:
                    text = await self._dream()
                    if text:
                        self._save_dream(text)

            # 2) 判断是否该睡
            elif self._should_sleep(idle_minutes):
                st["asleep"] = True
                st["sleep_at"] = now
                st["last_seen"] = now
                self._save(st)
                logger.info("[dream] 她已经睡着了（空闲 %.0f 分钟）", idle_minutes)

            # 3) 注入当天的梦
            if self.config.get("inject_dream", True):
                dream = self._latest_dream()
                if dream:
                    block = (f"[你昨晚的梦]\n{dream}\n"
                             "（这是模糊的印象，不用主动讲，被打听时可以随口提一句。）")
                    prompt = getattr(req, "system_prompt", None)
                    req.system_prompt = f"{prompt}\n\n{block}" if prompt else block

            st["last_seen"] = now
            self._save(st)
        except Exception:  # noqa: BLE001
            logger.exception("[dream] 处理失败")

    # ---------- 命令 ----------
    @filter.command("梦")
    async def show_dream(self, event: AstrMessageEvent):
        arg = event.message_str.partition(" ")[2].strip()
        st = self._state()
        if arg == "睡":
            st["asleep"] = True
            st["sleep_at"] = time.time()
            self._save(st)
            yield event.plain_result("她睡着了。下次你说话她就醒。")
            return
        if arg == "做":
            text = await self._dream()
            if not text:
                yield event.plain_result("没做出梦来")
                return
            self._save_dream(text)
            yield event.plain_result("梦：\n" + text)
            return

        rows = _read_jsonl(DREAMS, 10)
        if not rows:
            yield event.plain_result("还没做过梦。\n发「/梦 做」可以立刻做一个，或者她长时间没消息后会自动做梦。")
            return
        lines = [f"{r.get('time', '')}  {r.get('text', '')}" for r in rows[-5:]]
        status = "睡着中" if st.get("asleep") else "醒着"
        yield event.plain_result(f"【她的梦】（{status}）\n" + "\n\n".join(lines))
