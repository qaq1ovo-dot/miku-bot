"""确认对话框：每次调用设备/系统能力前都要弹。

显示内容：
- 谁在请求（QQ / API / 本地）
- 要做什么（能力名称 + 说明）
- 会有什么后果（人话）
- 风险等级与是否需要管理员
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.caps import Capability

RISK_COLOR = {"low": "#2e7d32", "medium": "#ef6c00", "high": "#c62828"}


class ConfirmDialog:
    """模态确认框，返回 "once" / "session" / "always" / "deny"。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root

    def ask(self, cap: Capability, source: str, timeout: float = 60.0) -> str:
        """弹框并等待用户选择。

        Args:
            cap: 请求的能力。
            source: 请求来源描述，例如 "QQ:123456789"。
            timeout: 超时时间（秒），超时按拒绝处理。

        Returns:
            用户的决定。
        """
        result = {"value": "deny"}
        win = tk.Toplevel(self.root)
        win.title("请求权限")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.geometry("420x300")

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"「{cap.title}」需要你的同意",
                  font=("Microsoft YaHei", 12, "bold")).pack(anchor="w")

        risk = tk.Label(frame, text=f"  {cap.risk_label}  ", bg=RISK_COLOR.get(cap.risk, "#555"),
                        fg="white", font=("Microsoft YaHei", 9))
        risk.pack(anchor="w", pady=(6, 0))

        info = f"""
请求来源：{source}
操作说明：{cap.description or cap.title}
可能后果：{cap.consequence}
{"⚠ 需要管理员权限" if cap.needs_admin else ""}
""".strip()
        text = tk.Text(frame, height=6, wrap="word", relief="flat", bg=win.cget("bg"),
                       font=("Microsoft YaHei", 10))
        text.insert("1.0", info)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, pady=(8, 10))

        bar = ttk.Frame(frame)
        bar.pack(fill="x")

        def choose(value: str) -> None:
            result["value"] = value
            win.destroy()

        ttk.Button(bar, text="拒绝", command=lambda: choose("deny")).pack(side="left")
        ttk.Button(bar, text="允许一次", command=lambda: choose("once")).pack(side="right")
        if cap.risk != "high":
            ttk.Button(bar, text="本会话允许", command=lambda: choose("session")).pack(side="right", padx=6)
        if cap.risk == "low":
            ttk.Button(bar, text="以后都允许", command=lambda: choose("always")).pack(side="right", padx=6)

        win.protocol("WM_DELETE_WINDOW", lambda: choose("deny"))
        win.after(int(timeout * 1000), lambda: choose("timeout") if win.winfo_exists() else None)

        win.update_idletasks()
        x = self.root.winfo_x() + self.root.winfo_width() // 2 - 210
        y = self.root.winfo_y() - 320
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

        win.grab_set()
        self.root.wait_window(win)
        return result["value"]
