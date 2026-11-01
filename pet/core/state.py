"""状态机。

一个桌宠同一时刻只有一个状态。状态可以：
- 常驻（idle）
- 临时（talking 播完自动回 idle）
- 定时切换（长时间无操作 → sleepy）

扩展方式：在 STATES 里加规则，或调用 transition()。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from core.assets import AssetPack


class StateMachine:
    def __init__(self, assets: AssetPack, behavior: dict | None = None) -> None:
        self.assets = assets
        self.behavior = behavior or {}
        self.current = assets.default_state
        self._since = time.time()
        self._temp_until = 0.0
        self._last_interaction = time.time()
        self._listeners: list[Callable[[str, str], None]] = []

    # ---------- 订阅 ----------
    def on_change(self, fn: Callable[[str, str], None]) -> None:
        """注册状态变化回调 fn(old, new)。"""
        self._listeners.append(fn)

    # ---------- 查询 ----------
    @property
    def idle_seconds(self) -> float:
        return time.time() - self._last_interaction

    def snapshot(self) -> dict:
        return {
            "state": self.current,
            "idle_seconds": round(self.idle_seconds, 1),
            "states": sorted(self.assets.states),
        }

    # ---------- 变更 ----------
    def touch(self) -> None:
        """任何用户交互都会重置空闲计时。"""
        self._last_interaction = time.time()

    def transition(self, name: str, *, duration: float = 0.0, force: bool = False) -> bool:
        """切换到某个状态。

        Args:
            name: 目标状态名。
            duration: 大于 0 时表示临时状态，到点自动回默认状态。
            force: 为 True 时即使同名也重新开始播放。

        Returns:
            是否切换成功。
        """
        if not self.assets.has(name):
            return False
        if name == self.current and not force and duration <= 0:
            return False
        old, self.current = self.current, name
        self._since = time.time()
        self._temp_until = (time.time() + duration) if duration > 0 else 0.0
        for fn in self._listeners:
            try:
                fn(old, name)
            except Exception:  # noqa: BLE001
                pass
        return True

    def tick(self) -> str | None:
        """定时调用，处理超时逻辑。返回需要切换到的状态（或 None）。"""
        now = time.time()
        if self._temp_until and now >= self._temp_until:
            self._temp_until = 0.0
            return self.assets.default_state

        sleep_after = float(self.behavior.get("idle_to_sleep_sec") or 0)
        if sleep_after > 0 and self.current == self.assets.default_state:
            if self.idle_seconds >= sleep_after and self.assets.has("sleep"):
                return "sleep"
        return None
