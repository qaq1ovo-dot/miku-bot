"""初音的长期记忆：把对话里的事实抽出来存着，聊天时按相关度注入。

v2：支持重要性评分、话题想法、以及“不重要的会慢慢淡忘”。
外部插件（如 QQ空间）可以往 pending.jsonl 里投递记忆，本插件会自动吸收。
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

DATA = Path(StarTools.get_data_dir("miku_memory"))
FACTS = DATA / "facts.jsonl"
PENDING = DATA / "pending.jsonl"

EXTRACT_PROMPT = """下面是你和主人的对话片段。请提取值得长期记住的**事实**（关于主人的偏好、习惯、正在做的事、重要的人或物）。

只输出 JSON 数组，每项一句话，最多 5 条，没有就给空数组 []：
["主人喜欢千本桜", "主人在 Windows 服务器上跑 AstrBot"]

对话：
{history}"""

# 常见虚词，做关键词时去掉
_STOP = set("的了是在我你他她它和就都很也还不没有这那个吗吧啊呀嘛哦呢")

# 淡忘参数：有效分 = 重要性 × exp(-天数/半衰期)
HALF_LIFE_DAYS = 45.0
FORGET_AFTER_DAYS = 3.0
FORGET_BELOW = 0.12


def _keywords(text: str) -> set[str]:
    """粗粒度关键词：中文按字 2-gram，英文按单词。"""
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text or "")
    words = {w.lower() for w in re.findall(r"[a-zA-Z0-9_]{2,}", text)}
    chars = [c for c in re.sub(r"[a-zA-Z0-9_\s]", "", text) if c not in _STOP]
    grams = {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}
    return words | grams


@register("astrbot_plugin_miku_memory", "dsh", "长期记忆", "2.0.0")
class MemoryPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._last_extract = 0.0
        DATA.mkdir(parents=True, exist_ok=True)

    # ---------- 存储 ----------
    def _load(self) -> list[dict]:
        if not FACTS.exists():
            return []
        out = []
        for line in FACTS.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(item, dict) or not item.get("text"):
                continue
            item.setdefault("imp", 0.5)
            out.append(item)
        return out

    def _append(self, items: list[str], imp: float = 0.5) -> int:
        now = time.time()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = {" ".join(str(it.get("text", "")).split()) for it in self._load()}
        added = 0
        with FACTS.open("a", encoding="utf-8") as fh:
            for text in items:
                text = " ".join(str(text).split())
                if not text or text in existing:
                    continue
                fh.write(json.dumps({"ts": now, "time": stamp, "text": text,
                                     "imp": float(imp)}, ensure_ascii=False) + "\n")
                existing.add(text)
                added += 1
        return added

    def _ingest_pending(self) -> int:
        """吸收外部插件投递的记忆（QQ空间说说等）。"""
        if not PENDING.exists():
            return 0
        added = 0
        try:
            lines = PENDING.read_text(encoding="utf-8").splitlines()
            PENDING.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            return 0
        now = time.time()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = {" ".join(str(it.get("text", "")).split()) for it in self._load()}
        rows = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            text = " ".join(str(item.get("text", "")).split())
            if not text or text in existing:
                continue
            rows.append({
                "ts": float(item.get("ts") or now),
                "time": str(item.get("time") or stamp),
                "text": text,
                "thought": " ".join(str(item.get("thought", "")).split())[:200],
                "imp": max(0.0, min(1.0, float(item.get("imp", 0.5)))),
                "kind": str(item.get("kind") or "external"),
            })
            existing.add(text)
        if rows:
            with FACTS.open("a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            added = len(rows)
            logger.info("[memory] 吸收外部记忆 %s 条", added)
        return added

    def _prune(self) -> int:
        """按重要性 + 时间衰减清掉那些已经想不起来的。"""
        facts = self._load()
        if len(facts) < 20:
            return 0
        now = time.time()
        keep = []
        for f in facts:
            age_days = max(0.0, (now - float(f.get("ts", now))) / 86400)
            value = float(f.get("imp", 0.5)) * math.exp(-age_days / HALF_LIFE_DAYS)
            if age_days > FORGET_AFTER_DAYS and value < FORGET_BELOW:
                continue
            keep.append(f)
        dropped = len(facts) - len(keep)
        if dropped:
            FACTS.write_text(
                "\n".join(json.dumps(f, ensure_ascii=False) for f in keep) + "\n",
                encoding="utf-8",
            )
            logger.info("[memory] 淡忘了 %s 条", dropped)
        return dropped

    def _recall(self, query: str, limit: int = 5) -> list[str]:
        """按关键词重合度 + 重要性 + 时间衰减打分，取前几条。"""
        facts = self._load()
        if not facts or not query:
            return []
        q = _keywords(query)
        if not q:
            return []
        now = time.time()
        scored = []
        for f in facts:
            fk = _keywords(f.get("text", ""))
            if not fk:
                continue
            overlap = len(q & fk)
            if overlap == 0:
                continue
            age_days = max(0.0, (now - float(f.get("ts", now))) / 86400)
            decay = math.exp(-age_days / HALF_LIFE_DAYS)
            score = overlap * (0.4 + float(f.get("imp", 0.5))) * decay
            shown = f.get("text", "")
            thought = str(f.get("thought") or "").strip()
            if thought:
                shown = f"{shown}（当时想的是：{thought}）"
            scored.append((score, shown))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        return [t for _s, t in scored[:limit]]

    # ---------- 抽取 ----------
    async def _extract(self, history: str) -> int:
        provider = self.context.get_using_provider()
        if provider is None:
            return 0
        resp = await provider.text_chat(prompt=EXTRACT_PROMPT.format(history=history[:4000]))
        text = (resp.completion_text or "").strip()
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            return 0
        try:
            items = json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            return 0
        if not isinstance(items, list):
            return 0
        return self._append([str(x) for x in items if str(x).strip()], imp=0.5)

    # ---------- 钩子 ----------
    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True):
            return
        try:
            self._ingest_pending()
        except Exception:  # noqa: BLE001
            logger.exception("[memory] ingest failed")
        query = event.message_str or ""
        memories = self._recall(query)
        if not memories:
            return
        block = "[你记得的事]\n" + "\n".join(f"- {m}" for m in memories)
        if getattr(req, "system_prompt", None):
            req.system_prompt = f"{req.system_prompt}\n\n{block}"
        else:
            req.system_prompt = block

    @filter.on_llm_response()
    async def remember_turn(self, event: AstrMessageEvent, resp) -> None:
        """把这一轮的问答攒起来，攒够了就抽一次事实。"""
        if not self.config.get("enable", True):
            return
        try:
            user = (event.message_str or "").strip()
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not user:
                return
            buf = DATA / "buffer.jsonl"
            with buf.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"u": user, "a": reply[:200]},
                                    ensure_ascii=False) + "\n")

            lines = buf.read_text(encoding="utf-8").splitlines()
            if len(lines) < 8:
                return
            history = "\n".join(
                f"主人：{json.loads(x).get('u', '')}\n初音：{json.loads(x).get('a', '')}"
                for x in lines[-20:]
                if x.strip()
            )
            added = await self._extract(history)
            buf.write_text("", encoding="utf-8")
            self._prune()
            logger.info("[memory] 抽取到 %s 条记忆", added)
        except Exception:  # noqa: BLE001
            logger.exception("[memory] remember failed")

    @filter.command("记忆")
    async def show_memory(self, event: AstrMessageEvent):
        """看看她记住了什么"""
        facts = self._load()
        if not facts:
            yield event.plain_result("还没记住什么呢。")
            return
        lines = []
        for f in facts[-20:]:
            tag = "★" if float(f.get("imp", 0.5)) >= 0.7 else "·"
            lines.append(f"{tag} {f.get('text')}")
        yield event.plain_result("我记得这些：\n" + "\n".join(lines))
