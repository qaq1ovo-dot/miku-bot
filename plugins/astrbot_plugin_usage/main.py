"""用量统计：把每次 LLM 调用的 token 折算成钱，接近预算就提醒。

- 数据按天存：plugin_data/astrbot_plugin_usage/usage.json
- 命令：/用量 看今天和本期累计
- token 优先用 provider 返回的 usage；拿不到就按字数估算
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_usage"))
USAGE = DATA / "usage.json"


@register("astrbot_plugin_usage", "dsh", "LLM 用量与花费统计", "1.0.0")
class UsagePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)
        self._warned: set[float] = set()

    # ---------- 存储 ----------
    def _load(self) -> dict:
        if not USAGE.exists():
            return {}
        try:
            return json.loads(USAGE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, data: dict) -> None:
        try:
            USAGE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning("[usage] 写入失败")

    # ---------- 计量 ----------
    def _record(self, prompt: str, reply: str, usage: dict | None) -> tuple[int, int, float]:
        """记录一次调用，返回 (输入 tokens, 输出 tokens, 本次花费)。"""
        if usage:
            tin = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            tout = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        else:
            # 粗略估算：中文约 1.5 字/token，英文约 4 字/token
            tin = max(1, int(len(prompt) / 2))
            tout = max(1, int(len(reply) / 2))

        price_in = float(self.config.get("price_in", 2.0) or 2.0)
        price_out = float(self.config.get("price_out", 8.0) or 8.0)
        cost = tin / 1_000_000 * price_in + tout / 1_000_000 * price_out

        data = self._load()
        today = date.today().isoformat()
        day = data.setdefault(today, {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
        day["calls"] += 1
        day["in"] += tin
        day["out"] += tout
        day["cost"] = round(float(day.get("cost", 0)) + cost, 6)
        self._save(data)
        return tin, tout, cost

    def _totals(self) -> tuple[int, float]:
        data = self._load()
        calls = sum(int(d.get("calls", 0)) for d in data.values())
        cost = sum(float(d.get("cost", 0)) for d in data.values())
        return calls, round(cost, 2)

    # ---------- 钩子 ----------
    @filter.on_llm_response()
    async def count(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        try:
            prompt = event.message_str or ""
            reply = getattr(resp, "completion_text", "") or ""
            usage = None
            for attr in ("usage", "raw_completion"):
                obj = getattr(resp, attr, None)
                if isinstance(obj, dict):
                    usage = obj.get("usage") if attr == "raw_completion" else obj
                    if usage:
                        break
            self._record(prompt, reply, usage)

            calls, cost = self._totals()
            budget = float(self.config.get("budget", 100.0) or 100.0)
            ratio = float(self.config.get("warn_ratio", 0.6) or 0.6)
            if budget > 0:
                used = cost / budget
                for mark in (ratio, 0.8, 0.95):
                    if used >= mark and mark not in self._warned:
                        self._warned.add(mark)
                        logger.warning(
                            "[usage] 已用 %.0f%% 预算（%.2f / %.2f 元，%d 次调用）",
                            used * 100, cost, budget, calls,
                        )
        except Exception:  # noqa: BLE001
            logger.exception("[usage] 统计失败")

    # ---------- 命令 ----------
    @filter.command("用量")
    async def show_usage(self, event: AstrMessageEvent):
        data = self._load()
        today = date.today().isoformat()
        d = data.get(today, {})
        calls, cost = self._totals()
        budget = float(self.config.get("budget", 100.0) or 100.0)
        left = max(0.0, budget - cost)
        lines = [
            f"今天：{d.get('calls', 0)} 次，{int(d.get('in', 0))} in / {int(d.get('out', 0))} out，"
            f"{float(d.get('cost', 0)):.2f} 元",
            f"累计：{calls} 次，{cost:.2f} 元",
            f"预算：{budget:.0f} 元，剩余 {left:.2f} 元",
        ]
        yield event.plain_result("【用量】\n" + "\n".join(lines))
