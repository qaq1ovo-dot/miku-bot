"""掉线补偿：重连后把漏掉的私聊消息补回复。

思路：
1. 每收到一条消息就记下时间戳（state.json）；
2. 启动/重连时，拉取最近的历史消息，找出「比上次记录更新、且不是自己发的」那些；
3. 逐条让模型生成回复并发送（有条数上限，避免刷屏）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

DATA = Path(StarTools.get_data_dir("astrbot_plugin_catchup"))
STATE = DATA / "state.json"


@register(
    "astrbot_plugin_catchup",
    "dsh",
    "掉线重连后补历史消息",
    "1.0.0",
)
class CatchupPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)

    # ---------- 状态 ----------
    def _load_state(self) -> dict:
        if not STATE.exists():
            return {}
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning("[catchup] 保存状态失败")

    def _touch(self, umo: str, ts: float | None = None) -> None:
        state = self._load_state()
        state[umo] = ts or time.time()
        self._save_state(state)

    # ---------- 平台 ----------
    def _get_bot(self):
        try:
            platform = self.context.get_platform("aiocqhttp")
        except Exception:  # noqa: BLE001
            return None
        return getattr(platform, "bot", None)

    async def _call(self, action: str, **params):
        bot = self._get_bot()
        if bot is None:
            logger.warning("[catchup] 没拿到 bot 实例")
            return None
        try:
            return await bot.api.call_action(action, **params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[catchup] %s 调用失败: %s", action, exc)
            return None

    # ---------- 记录在线时收到的消息 ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def remember_seen(self, event: AstrMessageEvent):
        try:
            if event.get_self_id() == event.get_sender_id():
                return
            self._touch(event.unified_msg_origin)
        except Exception:  # noqa: BLE001
            pass
        return

    # ---------- 重连补偿 ----------
    @filter.on_astrbot_loaded()
    async def catch_up(self):
        if not self.config.get("enable", True):
            return
        await self._do_catchup()

    async def _do_catchup(self) -> None:
        await _sleep(3)  # 等适配器连接完
        sessions = self.config.get("session_list") or []
        owner = str(self.config.get("owner_id") or "")
        if not sessions and owner:
            sessions = [f"napcat:FriendMessage:{owner}"]
        state = self._load_state()
        max_replies = int(self.config.get("max_replies", 2) or 2)
        gap_limit = int(self.config.get("max_gap_minutes", 720) or 720) * 60
        count = int(self.config.get("history_count", 30) or 30)

        for umo in sessions:
            try:
                parts = str(umo).split(":")
                if len(parts) < 3:
                    continue
                kind, target = parts[1], parts[2]
                if "Group" in kind:
                    data = await self._call("get_group_msg_history", group_id=int(target), count=count)
                else:
                    data = await self._call("get_friend_msg_history", user_id=int(target), count=count)
                if not isinstance(data, dict):
                    continue
                msgs = data.get("messages") or []
                last_seen = float(state.get(str(umo), 0) or 0)
                now = time.time()

                pending = []
                for m in msgs:
                    ts = float(m.get("time") or 0)
                    sender = str((m.get("sender") or {}).get("user_id") or (m.get("user_id") or ""))
                    self_id = str(m.get("self_id") or "")
                    if ts <= last_seen:
                        continue
                    if sender and self_id and sender == self_id:
                        continue
                    text = _extract_text(m)
                    if not text.strip():
                        continue
                    pending.append((ts, text))

                if not pending:
                    continue
                if last_seen and (now - pending[-1][0]) > gap_limit:
                    self._touch(str(umo), now)
                    continue

                pending = pending[-max_replies:]
                logger.info("[catchup] %s 有 %s 条漏掉的消息，开始补回复", umo, len(pending))
                provider = self.context.get_using_provider()
                if provider is None:
                    logger.warning("[catchup] 没有可用的 LLM")
                    continue

                for ts, text in pending:
                    when = datetime.fromtimestamp(ts).strftime("%H:%M")
                    prompt = (
                        "你刚才掉线了一会儿，现在刚回来。对方在这期间给你发了消息，"
                        "请自然地回复（可以顺手道个歉说刚才没看到，但不要反复强调）。\n"
                        f"对方在 {when} 说：{text}"
                    )
                    try:
                        resp = await provider.text_chat(prompt=prompt)
                        reply = (resp.completion_text or "").strip()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[catchup] 生成回复失败: %s", exc)
                        continue
                    if not reply:
                        continue
                    if "Group" in kind:
                        await self._call("send_group_msg", group_id=int(target), message=reply)
                    else:
                        await self._call("send_private_msg", user_id=int(target), message=reply)
                    await _sleep(2)

                self._touch(str(umo), pending[-1][0])
                logger.info("[catchup] %s 补偿完成", umo)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[catchup] 补偿 %s 出错: %s", umo, exc)


def _extract_text(message: dict) -> str:
    segs = message.get("message") or []
    parts = []
    for seg in segs:
        if isinstance(seg, dict) and seg.get("type") == "text":
            parts.append((seg.get("data") or {}).get("text", ""))
    return "".join(parts)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
