"""情绪状态：让她有一组会自己变化的内部状态。

四个维度（都是 0~1，valence 是 -1~1）：
    valence   心情：-1 很低落，0 平静，+1 很开心
    energy    精力：0 困得不行，1 元气满满
    social    社交欲：0 不想理人，1 很想说话
    curiosity 好奇：0 没兴趣，1 特别想知道点什么

所有变化都由时间和事件驱动，**不额外调用 LLM**。
注入方式：在 system prompt 末尾加一段「你现在的状态」。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_psyche"))
STATE = DATA / "state.json"

DEFAULT = {"valence": 0.1, "energy": 0.7, "social": 0.3, "curiosity": 0.5, "ts": 0.0}

# 每小时的自然回归速率（往 baseline 靠）
DECAY = {"valence": 0.35, "energy": 0.12, "social": -0.15, "curiosity": 0.3}
BASELINE = {"valence": 0.1, "energy": 0.7, "social": 0.3, "curiosity": 0.5}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@register("astrbot_plugin_miku_psyche", "dsh", "情绪与需求状态", "1.0.0")
class PsychePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)
        self._task: asyncio.Task | None = None

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._decay_loop())
        logger.info("[psyche] 情绪系统已启动")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- 状态读写 ----------
    def _load(self) -> dict:
        if STATE.exists():
            try:
                data = json.loads(STATE.read_text(encoding="utf-8"))
                for k, v in DEFAULT.items():
                    data.setdefault(k, v)
                return data
            except Exception:  # noqa: BLE001
                pass
        return dict(DEFAULT)

    def _save(self, state: dict) -> None:
        state["ts"] = time.time()
        try:
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning("[psyche] 保存失败")

    def _decay(self, state: dict) -> dict:
        """按经过的时间调整状态。

        social（社交欲）单独处理：它只会随时间**上涨**，不向基线回落。
        道理很直白——没人陪着说话，只会越来越想，不会越来越淡。
        """
        now = time.time()
        last = float(state.get("ts") or now)
        hours = max(0.0, (now - last) / 3600.0)
        if hours <= 0:
            return state

        boost = float(self.config.get("social_decay_boost", 1.0) or 1.0)
        rise = float(self.config.get("social_rise_per_hour", 0.18) or 0.18)

        for key, rate in DECAY.items():
            if key == "social":
                continue
            base = BASELINE[key]
            value = float(state.get(key, base)) + rate * hours
            value += (base - value) * min(1.0, 0.15 * hours)
            state[key] = _clamp(value, -1.0 if key == "valence" else 0.0, 1.0)

        social = float(state.get("social", BASELINE["social"]))
        social += rise * hours * boost
        state["social"] = _clamp(social)
        return state

    async def _decay_loop(self) -> None:
        step = max(5, int(self.config.get("decay_minutes", 30) or 30)) * 60
        while True:
            await asyncio.sleep(step)
            try:
                state = self._decay(self._load())
                self._save(state)
            except Exception:  # noqa: BLE001
                logger.exception("[psyche] 衰减失败")

    # ---------- 事件 ----------
    def _on_user_message(self, state: dict) -> dict:
        state["social"] = _clamp(float(state["social"]) - 0.35)
        state["valence"] = _clamp(float(state["valence"]) + 0.05, -1.0, 1.0)
        return state

    def _on_reply(self, state: dict) -> dict:
        state["energy"] = _clamp(float(state["energy"]) - 0.02)
        return state

    def _describe(self, state: dict) -> str:
        v = float(state["valence"])
        e = float(state["energy"])
        s = float(state["social"])
        c = float(state["curiosity"])

        mood = "心情很好" if v > 0.5 else "心情不错" if v > 0.15 else \
               "心情平静" if v > -0.15 else "有点低落" if v > -0.5 else "情绪很低落"
        energy = "精神很好" if e > 0.75 else "还算精神" if e > 0.45 else \
                 "有点累了" if e > 0.2 else "困得不行"
        social = "很无聊，想找人说话" if s > 0.75 else "有点想聊天" if s > 0.5 else \
                 "暂时不想说话" if s < 0.25 else "正常状态"
        return f"{mood}，{energy}，{social}"

    def _should_show(self, state: dict) -> bool:
        return (
            float(state["valence"]) < -0.3
            or float(state["energy"]) < 0.3
            or float(state["social"]) > 0.7
        )

    # ---------- 钩子 ----------
    @filter.on_llm_request()
    async def inject_state(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True):
            return
        try:
            state = self._decay(self._load())
            state = self._on_user_message(state)
            self._save(state)

            if not self.config.get("inject_prompt", True):
                return
            desc = self._describe(state)
            block = (
                f"[你现在的状态]\n{desc}\n"
                "（这是你真实的内部状态：说话的语气要和状态一致，"
                "但不必把数值说出来，也不要解释自己为什么这样。）"
            )
            req.system_prompt = f"{req.system_prompt}\n\n{block}" if getattr(req, "system_prompt", None) else block
        except Exception:  # noqa: BLE001
            logger.exception("[psyche] 注入失败")

    @filter.on_llm_response()
    async def after_reply(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if not self.config.get("enable", True):
            return
        try:
            state = self._on_reply(self._load())
            self._save(state)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 命令 ----------
    @filter.command("状态")
    async def show_state(self, event: AstrMessageEvent):
        state = self._decay(self._load())
        bars = []
        for key, label in (("valence", "心情"), ("energy", "精力"),
                           ("social", "社交欲"), ("curiosity", "好奇")):
            val = float(state.get(key, 0))
            if key == "valence":
                width = int((val + 1) / 2 * 10)
                bars.append(f"{label} [{'█' * width}{'·' * (10 - width)}] {val:+.1f}")
            else:
                width = int(val * 10)
                bars.append(f"{label} [{'█' * width}{'·' * (10 - width)}] {val:.1f}")
        text = "【现在的她】\n" + self._describe(state) + "\n\n" + "\n".join(bars)
        text += f"\n\n更新于 {datetime.now().strftime('%H:%M')}"
        yield event.plain_result(text)
