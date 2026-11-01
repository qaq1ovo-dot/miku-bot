"""独立聊天窗口：可选「本机 LLM」或「服务器上的她」。

- backend = "local"：用本机配置的 LLM（不联网到服务器）
- backend = "bot"  ：转发给服务器上的 AstrBot，用同一份人格与记忆
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable

Sender = Callable[[str], tuple[str, str]]


class ChatBox:
    """一个小输入框：输入 → 发送 → 她回答。"""

    def __init__(self, root: tk.Tk, send: Sender, on_reply: Callable[[str, str], None],
                 title: str = "和她说句话") -> None:
        """
        Args:
            root: 主窗口。
            send: 发送函数 fn(text) -> (回复, 状态)。
            on_reply: 拿到回复后的回调 fn(reply, state)。
            title: 窗口标题。
        """
        self.root = root
        self.send = send
        self.on_reply = on_reply
        self.title = title
        self.win: tk.Toplevel | None = None

    def open(self) -> None:
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title(self.title)
        self.win.geometry("340x120+%d+%d" % (self.root.winfo_x() + 40, self.root.winfo_y() + 40))
        self.win.attributes("-topmost", True)

        self.entry = tk.Entry(self.win, font=("Microsoft YaHei", 11))
        self.entry.pack(fill="x", padx=10, pady=(12, 6))
        self.entry.bind("<Return>", lambda _e: self._send())
        self.entry.focus_set()

        bar = tk.Frame(self.win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(bar, text="发送", width=8, command=self._send).pack(side="right")
        self.status = tk.Label(bar, text="", fg="#888888", font=("Microsoft YaHei", 9))
        self.status.pack(side="left")

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.status.configure(text="她想一想…")
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, text: str) -> None:
        reply, state = self.send(text)

        def _apply() -> None:
            self.status.configure(text="" if reply else "没回上，检查配置")
            if reply:
                self.on_reply(reply, state)
        try:
            self.root.after(0, _apply)
        except tk.TclError:
            pass
