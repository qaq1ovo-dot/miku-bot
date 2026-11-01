"""经验学习。

和「记忆」不同：
    记忆存事实（主人喜欢什么）
    经验存规则（我该怎么说话）

流程：
    你说「不是这样 / 别这么长」 → 识别为纠正
        ↓
    让模型把这句纠正总结成一条规则
        ↓
    存进 lessons.jsonl（同一条规则累加次数）
        ↓
    次数 ≥ confirm_times 才算「确认」，优先注入
        ↓
    每次生成前，按关键词相关性取前几条注入
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_learn"))
LESSONS = DATA / "lessons.jsonl"

# 明显的纠正信号
CORRECTION = re.compile(
    r"(不是这样|不是这个|别这么|别说|不要这么|别老|太长了|太短了|太啰嗦|"
    r"重说|重写|换个说法|不对|错了|谁让你|别用这种|太正式|太机械|"
    r"不用解释|别说教|不用道歉|别加|别问)"
)

EXTRACT_PROMPT = """主人刚才对你的回复表达了不满，原话是：
「{text}」

请用一句话总结出「以后该怎么做」的规则，不超过 25 字，用第一人称。
只输出这句话，不要解释。

例：主人嫌我太啰嗦 → 「回复超过三句就要收住」"""


def _keywords(text: str) -> set[str]:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text or "")
    chars = [c for c in re.sub(r"[a-zA-Z0-9_\s]", "", text)]
    return {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


@register("astrbot_plugin_miku_learn", "dsh", "从反馈里学规则", "1.0.0")
class LearnPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)

    # ---------- 存储 ----------
    def _load(self) -> list[dict]:
        if not LESSONS.exists():
            return []
        out = []
        for line in LESSONS.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _save(self, rows: list[dict]) -> None:
        try:
            LESSONS.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.warning("[learn] 写入失败")

    def _add(self, rule: str) -> dict | None:
        rule = " ".join(rule.split()).strip("。 ")
        if not rule or len(rule) > 60:
            return None
        rows = self._load()
        now = time.time()
        for r in rows:
            if r.get("rule") == rule:
                r["hits"] = int(r.get("hits", 1)) + 1
                r["ts"] = now
                self._save(rows)
                return r
        rec = {"rule": rule, "hits": 1, "ts": now,
               "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
        rows.append(rec)
        self._save(rows)
        return rec

    # ---------- 提取规则 ----------
    async def _extract(self, text: str) -> str:
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        try:
            resp = await provider.text_chat(prompt=EXTRACT_PROMPT.format(text=text[:200]))
            rule = (resp.completion_text or "").strip().splitlines()[0]
            return rule.strip("「」\"' ")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[learn] 总结失败: %s", exc)
            return ""

    # ---------- 注入 ----------
    def _relevant(self, query: str, limit: int) -> list[str]:
        rows = self._load()
        if not rows:
            return []
        q = _keywords(query)
        confirm = max(1, int(self.config.get("confirm_times", 3) or 3))
        scored = []
        for r in rows:
            rule = str(r.get("rule") or "")
            hits = int(r.get("hits", 1))
            base = 1.0 if hits >= confirm else 0.5
            overlap = len(q & _keywords(rule))
            age_days = max(0.0, (time.time() - float(r.get("ts", time.time()))) / 86400)
            decay = 1.0 / (1.0 + age_days / 60.0)
            scored.append(((base + overlap * 0.5) * decay, rule))
        scored.sort(reverse=True)
        return [rule for _s, rule in scored[:limit]]

    @filter.on_llm_request()
    async def learn_and_inject(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True):
            return
        try:
            text = event.message_str or ""

            # 1) 有纠正信号 → 学一条
            if self.config.get("auto_learn", True) and CORRECTION.search(text):
                rule = await self._extract(text)
                rec = self._add(rule) if rule else None
                if rec:
                    logger.info("[learn] 学到规则（第 %s 次）：%s", rec.get("hits"), rec.get("rule"))

            # 2) 注入相关经验
            limit = max(1, int(self.config.get("inject_limit", 3) or 3))
            rules = self._relevant(text, limit)
            if rules:
                block = "[你学到的经验]\n" + "\n".join(f"- {r}" for r in rules)
                prompt = getattr(req, "system_prompt", None)
                req.system_prompt = f"{prompt}\n\n{block}" if prompt else block
        except Exception:  # noqa: BLE001
            logger.exception("[learn] 处理失败")

    # ---------- 命令 ----------
    @filter.command("经验")
    async def show_lessons(self, event: AstrMessageEvent):
        arg = event.message_str.partition(" ")[2].strip()
        rows = self._load()
        if arg == "清空":
            self._save([])
            yield event.plain_result("经验已清空。")
            return
        if not rows:
            yield event.plain_result("还没学到什么。你纠正我的时候，我就会记一条。")
            return
        confirm = max(1, int(self.config.get("confirm_times", 3) or 3))
        lines = []
        for r in rows[-15:]:
            hits = int(r.get("hits", 1))
            mark = "★" if hits >= confirm else "·"
            lines.append(f"{mark} [{hits}次] {r.get('rule')}")
        yield event.plain_result("【我学到的经验】\n" + "\n".join(lines)
                                 + f"\n\n（★ = 已确认，{confirm} 次以上）")
