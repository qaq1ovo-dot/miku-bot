"""桌宠窗口：透明、置顶、可拖动、带气泡。

只用 tkinter + Pillow，不需要额外依赖。

透明做法：Windows 的 -transparentcolor 把某个颜色当透明；
因为立绘是像素风（硬边），这个方案效果可以接受。
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from core.assets import AssetPack
from core.config import Config
from core.state import StateMachine
from core.tts import TTS

TRANSPARENT_KEY = "#010203"  # 一个几乎不会出现在立绘里的颜色
TICK_MS = 60


class PetWindow:
    def __init__(self, cfg: Config, assets: AssetPack, machine: StateMachine, tts: TTS,
                 chat_opener=None) -> None:
        self.cfg = cfg
        self.assets = assets
        self.machine = machine
        self.tts = tts
        self.chat_opener = chat_opener

        self.anim = assets.get(machine.current)
        self.frame_idx = 0
        self._photo: ImageTk.PhotoImage | None = None
        self._ticks = 0
        self._drag_from = (0, 0)
        self._bubble_after: str | None = None

        self.root = tk.Tk()
        self.root.title("Miku Pet")
        self.root.overrideredirect(True)          # 去掉标题栏
        self.root.configure(bg=TRANSPARENT_KEY)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass                                   # 非 Windows 平台忽略
        if cfg.get("pet.always_on_top", True):
            self.root.attributes("-topmost", True)

        first = self.anim.frames[0]
        w, h = first.width, first.height
        x, y = cfg.get("pet.start_pos", [200, 200])
        self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

        self.label = tk.Label(self.root, bg=TRANSPARENT_KEY, bd=0)
        self.label.pack()

        # 气泡：独立的无边框小窗
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.configure(bg="#ffffff")
        if cfg.get("pet.always_on_top", True):
            self.bubble.attributes("-topmost", True)
        self.bubble_label = tk.Label(
            self.bubble, text="", bg="#ffffff", fg="#222222",
            font=("Microsoft YaHei", 11), wraplength=260, justify="left", padx=10, pady=8,
        )
        self.bubble_label.pack()
        self.bubble.withdraw()

        # 交互
        self.label.bind("<ButtonPress-1>", self._on_press)
        self.label.bind("<B1-Motion>", self._on_drag)
        self.label.bind("<Double-Button-1>", lambda _e: self._on_poke())
        self.label.bind("<Button-3>", self._on_menu)

        self.menu = tk.Menu(self.root, tearoff=0)
        if self.chat_opener is not None:
            self.menu.add_command(label="和我说话…", command=self.chat_opener)
            self.menu.add_separator()
        self.menu.add_command(label="说句话", command=lambda: self.say("こんにちは"))
        self.menu.add_command(label="切换状态", command=self._cycle_state)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.close)

        self.machine.on_change(self._on_state_change)
        self._render_frame()
        self.root.after(TICK_MS, self._tick)

    # ---------- 渲染 ----------
    def _render_frame(self) -> None:
        frames = self.anim.frames
        if not frames:
            return
        frame = frames[self.frame_idx % len(frames)]
        self._photo = ImageTk.PhotoImage(frame)
        self.label.configure(image=self._photo)

    def _tick(self) -> None:
        self._ticks += 1
        if self.anim.duration_ms and (self._ticks * TICK_MS) >= self.anim.duration_ms:
            self._ticks = 0
            self.frame_idx += 1
            if self.frame_idx >= len(self.anim.frames):
                if self.anim.loop:
                    self.frame_idx = 0
                else:
                    self.frame_idx = len(self.anim.frames) - 1
            self._render_frame()

        nxt = self.machine.tick()
        if nxt:
            self.machine.transition(nxt)

        self.root.after(TICK_MS, self._tick)

    def _on_state_change(self, _old: str, new: str) -> None:
        self.anim = self.assets.get(new)
        self.frame_idx = 0
        self._ticks = 0
        first = self.anim.frames[0]
        self.root.geometry(f"{first.width}x{first.height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._render_frame()

    # ---------- 交互 ----------
    def _on_press(self, event: tk.Event) -> None:
        self.machine.touch()
        self._drag_from = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        self.machine.transition("clicked", duration=0.6)

    def _on_drag(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_from[0]
        y = event.y_root - self._drag_from[1]
        self.root.geometry(f"+{x}+{y}")
        self._move_bubble()

    def _on_poke(self) -> None:
        self.machine.touch()
        self.machine.transition("dragged", duration=0.8)
        self.say("なに？")

    def _on_menu(self, event: tk.Event) -> None:
        self.machine.touch()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _cycle_state(self) -> None:
        names = sorted(self.assets.states)
        if not names:
            return
        idx = names.index(self.machine.current) if self.machine.current in names else -1
        self.machine.transition(names[(idx + 1) % len(names)], force=True)

    # ---------- 气泡 ----------
    def show_bubble(self, text: str, seconds: float | None = None) -> None:
        if not text.strip():
            return
        self.bubble_label.configure(text=text)
        self.bubble.update_idletasks()
        self._move_bubble()
        self.bubble.deiconify()
        self.bubble.lift()
        if self._bubble_after:
            self.root.after_cancel(self._bubble_after)
        sec = seconds if seconds is not None else float(self.cfg.get("behavior.bubble_sec", 6))
        self._bubble_after = self.root.after(int(sec * 1000), self.bubble.withdraw)

    def _move_bubble(self) -> None:
        bx = self.root.winfo_x() + 10
        by = self.root.winfo_y() - self.bubble.winfo_reqheight() - 8
        self.bubble.geometry(f"+{bx}+{max(0, by)}")

    # ---------- 说话 ----------
    def say(self, text: str, *, voice: bool = True, seconds: float | None = None) -> None:
        self.machine.touch()
        self.show_bubble(text, seconds)
        self.machine.transition("talking", duration=0.0, force=True)
        if voice and self.tts.available():
            self.tts.speak(text)

    # ---------- 生命周期 ----------
    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass
