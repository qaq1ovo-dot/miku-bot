"""入口。

用法：
    python main.py                    # 正常启动（带窗口）
    python main.py --headless         # 只开 API，不开窗口（服务器上测试用）
    python main.py --config my.json   # 指定配置
"""

from __future__ import annotations

import argparse
import sys
import time

from core import caps, caps_device  # noqa: F401  （注册设备能力）
from core.actions import build_actions
from core.assets import AssetPack
from core.config import Config
from core.llm import LLM
from core.server import ApiServer
from core.state import StateMachine
from core.tts import TTS


def main() -> int:
    parser = argparse.ArgumentParser(description="Miku desktop pet")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--headless", action="store_true", help="不开窗口，只跑 API")
    args = parser.parse_args()

    cfg = Config.load(args.config or None)
    asset_dir = cfg.resolve(cfg.get("pet.asset_dir", "assets/pets/miku_pixel"))
    scale = float(cfg.get("pet.scale", 1.0))

    print(f"[pet] 资源目录: {asset_dir}", flush=True)
    assets = AssetPack(asset_dir, scale=scale)
    print(f"[pet] 状态: {sorted(assets.states)}", flush=True)

    machine = StateMachine(assets, cfg.get("behavior", {}))
    tts = TTS(cfg.get("tts", {}))

    if args.headless:
        # 无窗口模式：只能查询能力清单，任何实际调用都会被拒绝
        def _deny(body: dict) -> dict:
            name = str(body.get("name") or "")
            cap = caps.REGISTRY.get(name)
            if cap is None:
                return {"ok": False, "error": f"未知能力：{name}"}
            return {"ok": False, "denied": True, "reason": "headless 模式没有确认界面",
                    "capability": name, "consequence": cap.consequence}

        actions = {
            "ping": lambda _b: {"ok": True, "app": "miku-pet", "headless": True},
            "status": lambda _b: {"ok": True, **machine.snapshot()},
            "caps": lambda _b: {"ok": True, "capabilities": caps.list_capabilities()},
            "cap": _deny,
        }
        server = ApiServer(cfg.get("api.host", "127.0.0.1"), int(cfg.get("api.port", 9890)),
                           cfg.get("api.token", ""), actions)
        server.start()
        print("[pet] headless 模式，Ctrl+C 退出", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
        return 0

    # 有窗口模式需要 tkinter
    try:
        from ui.window import PetWindow
    except Exception as exc:  # noqa: BLE001
        print(f"[pet] 无法加载窗口模块（可能没有图形界面）：{exc}", flush=True)
        return 2

    win = PetWindow(cfg, assets, machine, tts)

    # 聊天入口：优先连服务器上的她，其次用本机 LLM
    backend = str(cfg.get("chat.backend", "bot") or "bot").lower()
    box = None

    if backend == "bot":
        from core.bot_client import BotClient

        bot = BotClient(cfg.get("chat", {}))
        if bot.available():
            from ui.chat import ChatBox

            def _send_via_bot(text: str) -> tuple[str, str]:
                return bot.chat(text)

            def _on_reply(reply: str, state: str) -> None:
                win.say(reply, voice=True)
                if state and state not in ("idle", "error") and win.assets.has(state):
                    win.machine.transition(state, duration=2.5)

            box = ChatBox(win.root, _send_via_bot, _on_reply, title="和她说话（同一份记忆）")
            print("[pet] 聊天后端：服务器上的她", flush=True)

    if box is None:
        llm = LLM(cfg.get("llm", {}))
        if llm.available():
            from ui.chat import ChatBox

            def _send_local(text: str) -> tuple[str, str]:
                return llm.chat(text), "idle"

            box = ChatBox(win.root, _send_local,
                          lambda reply, _state: win.say(reply, voice=True),
                          title="和她说句话")
            print("[pet] 聊天后端：本机 LLM", flush=True)

    if box is not None:
        win.chat_opener = box.open

    # 权限：每次调用系统/设备能力都要用户当场确认
    from core.permissions import PermissionStore
    from ui.confirm import ConfirmDialog

    store = PermissionStore(cfg.resolve("permissions.json"))
    dialog = ConfirmDialog(win.root)

    def ask_permission(cap_name: str, source: str) -> str:
        """在 UI 线程里弹确认框，HTTP 线程等待结果。"""
        import threading

        cap = caps.REGISTRY.get(cap_name)
        if cap is None:
            return "deny"
        box: dict = {}
        done = threading.Event()

        def show() -> None:
            try:
                box["v"] = dialog.ask(cap, source)
            finally:
                done.set()

        win.root.after(0, show)
        done.wait(timeout=70)
        return box.get("v", "deny")

    actions = build_actions(win, on_quit=win.close, perms=store, ask_permission=ask_permission)
    server = ApiServer(cfg.get("api.host", "127.0.0.1"), int(cfg.get("api.port", 9890)),
                       cfg.get("api.token", ""), actions)
    server.start()

    try:
        win.run()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
