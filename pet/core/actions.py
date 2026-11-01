"""把窗口能力包装成 HTTP action，机器人通过它控制桌宠。"""

from __future__ import annotations

import threading
from typing import Any, Callable

from core import caps
from core.permissions import PermissionStore
from ui.window import PetWindow


def build_actions(win: PetWindow, on_quit: Callable[[], None],
                  perms: PermissionStore | None = None,
                  ask_permission: Callable[[str, str], str] | None = None) -> dict[str, Callable[[dict], dict]]:
    """构造 action 表。

    Args:
        win: 桌宠窗口实例。
        on_quit: 退出回调。
        perms: 权限存储；为空时使用内存版。
        ask_permission: 询问用户的回调 fn(cap_name, source) -> once/session/always/deny。

    Returns:
        action 名到处理函数的映射。
    """
    store = perms or PermissionStore(win.cfg.resolve("permissions.json"))

    def _authorize(name: str, source: str) -> tuple[bool, str]:
        """检查权限；必要时弹框询问。"""
        if store.is_granted(name):
            return True, "granted"
        if ask_permission is None:
            return False, "no-ui"          # 无窗口模式一律拒绝
        decision = ask_permission(name, source)
        if decision == "once":
            return True, "once"
        if decision == "session":
            store.grant_session(name)
            return True, "session"
        if decision == "always":
            store.grant_always(name)
            return True, "always"
        return False, decision or "deny"

    def ping(_body: dict) -> dict:
        return {"ok": True, "app": "miku-pet"}

    def status(_body: dict) -> dict:
        snap = win.machine.snapshot()
        snap.update({"ok": True, "x": win.root.winfo_x(), "y": win.root.winfo_y()})
        return snap

    def say(body: dict) -> dict[str, Any]:
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 为空"}
        voice = bool(body.get("voice", True))
        seconds = body.get("seconds")
        # tkinter 不是线程安全的，统一丢回主线程执行
        win.root.after(0, lambda: win.say(text, voice=voice,
                                          seconds=float(seconds) if seconds else None))
        return {"ok": True, "text": text}

    def bubble(body: dict) -> dict[str, Any]:
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 为空"}
        seconds = body.get("seconds")
        win.root.after(0, lambda: win.show_bubble(text, float(seconds) if seconds else None))
        return {"ok": True}

    def set_state(body: dict) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not win.assets.has(name):
            return {"ok": False, "error": f"没有这个状态：{name}", "states": sorted(win.assets.states)}
        duration = float(body.get("duration") or 0)
        win.root.after(0, lambda: win.machine.transition(name, duration=duration, force=True))
        return {"ok": True, "state": name}

    def move(body: dict) -> dict[str, Any]:
        x, y = int(body.get("x", win.root.winfo_x())), int(body.get("y", win.root.winfo_y()))
        win.root.after(0, lambda: win.root.geometry(f"+{x}+{y}"))
        return {"ok": True, "x": x, "y": y}

    def quit_app(_body: dict) -> dict:
        win.root.after(0, on_quit)
        return {"ok": True, "message": "bye"}

    # ---------- 能力 / 权限 ----------
    def list_caps(_body: dict) -> dict:
        return {"ok": True, "capabilities": caps.list_capabilities(),
                "granted": store.snapshot()}

    def call_cap(body: dict) -> dict:
        """调用一项能力；需要用户当场确认。"""
        name = str(body.get("name") or "").strip()
        source = str(body.get("source") or "API")
        cap = caps.REGISTRY.get(name)
        if cap is None:
            return {"ok": False, "error": f"未知能力：{name}",
                    "capabilities": [c["name"] for c in caps.list_capabilities()]}
        allowed, how = _authorize(name, source)
        if not allowed:
            return {"ok": False, "denied": True, "reason": how, "capability": name}
        result = caps.execute(name)
        return {"ok": True, "capability": name, "decision": how, "result": result}

    def revoke(_body: dict) -> dict:
        store.revoke_all()
        return {"ok": True, "message": "所有授权已清空"}

    return {
        "ping": ping,
        "status": status,
        "say": say,
        "bubble": bubble,
        "state": set_state,
        "move": move,
        "quit": quit_app,
        "caps": list_caps,
        "cap": call_cap,
        "revoke": revoke,
    }
