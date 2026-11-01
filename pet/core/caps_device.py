"""设备类能力：音量、亮度、截图、剪贴板、锁屏、播放控制。

全部显式声明风险与后果，走统一的权限确认流程。
"""

from __future__ import annotations

import ctypes
import os
import tempfile
import time

from core.caps import Capability, register

VK_VOLUME_MUTE = 0xAD
VK_MEDIA_PLAY = 0xB3
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1


def _key(vk: int, times: int = 1) -> None:
    """模拟按键（这里只用于音量/媒体键，属于低风险操作）。"""
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.05)


# ---------------------------------------------------------------- 音量

def _volume(delta: int = 0, mute: bool = False) -> dict:
    """调整音量。

    Args:
        delta: 正数调大，负数调小，单位是 2% 一档。
        mute: 是否静音切换。
    """
    if mute:
        _key(VK_VOLUME_MUTE)
        return {"ok": True, "action": "toggle_mute"}
    if delta == 0:
        return {"ok": True, "action": "noop", "hint": "传 delta 或 mute"}
    _key(VK_VOLUME_MUTE if delta < 0 and False else 0xAF if delta > 0 else 0xAE, abs(delta))
    return {"ok": True, "action": "volume_up" if delta > 0 else "volume_down", "steps": abs(delta)}


register(Capability(
    name="device.volume",
    title="调节音量",
    risk="low",
    description="调大/调小/静音系统音量",
    consequence="会改变你当前的音量，随时可以调回来，不会损坏设备。",
    args=["delta", "mute"],
    func=_volume,
))

register(Capability(
    name="device.media",
    title="播放控制",
    risk="low",
    description="播放/暂停、上一首、下一首",
    consequence="会切换你正在播放的音乐，可再点一次恢复。",
    args=["action"],
    func=lambda action="play": _media(action),
))


def _media(action: str = "play") -> dict:
    table = {"play": VK_MEDIA_PLAY, "pause": VK_MEDIA_PLAY,
             "next": VK_MEDIA_NEXT, "prev": VK_MEDIA_PREV}
    vk = table.get(str(action))
    if not vk:
        return {"error": f"不支持的操作：{action}"}
    _key(vk)
    return {"ok": True, "action": action}


# ---------------------------------------------------------------- 亮度

def _brightness(level: int | None = None) -> dict:
    """读取或设置屏幕亮度（仅笔记本/支持 WMI 的屏）。"""
    try:
        out = ctypes.windll.shell32.ShellExecuteW  # noqa: F841  (占位，避免 lint)
        ps_get = (
            "powershell -NoProfile -Command \"(Get-CimInstance -Namespace root/wmi "
            "-ClassName WmiMonitorBrightness).CurrentBrightness\""
        )
        cur = os.popen(ps_get).read().strip()
        if level is None:
            return {"ok": True, "current": cur}
        ps_set = (
            "powershell -NoProfile -Command \"(Get-CimInstance -Namespace root/wmi "
            f"-ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1,{int(level)})\""
        )
        os.system(ps_set)
        return {"ok": True, "set": level}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


register(Capability(
    name="device.brightness",
    title="调节亮度",
    risk="low",
    description="读取或设置屏幕亮度（台式外接屏可能不支持）",
    consequence="会改变屏幕亮度，可以再调回来。",
    args=["level"],
    func=_brightness,
))

# ---------------------------------------------------------------- 截图

def _screenshot(folder: str = "") -> dict:
    """截一张全屏图，保存到临时目录。"""
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(all_screens=True)
        out_dir = folder or os.path.join(tempfile.gettempdir(), "miku_pet_shots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"shot_{int(time.time())}.png")
        img.save(path)
        return {"ok": True, "path": path, "size": img.size}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


register(Capability(
    name="device.screenshot",
    title="截屏",
    risk="medium",
    description="截取当前屏幕并保存成图片",
    consequence="会把你屏幕上的**全部内容**（包括聊天记录、密码框、隐私）截下来。"
                "虽然只存在本机，但如果被发出去就可能是隐私泄露。",
    args=["folder"],
    func=_screenshot,
))

# ---------------------------------------------------------------- 剪贴板

def _clipboard(text: str | None = None) -> dict:
    """读取或写入剪贴板。"""
    try:
        if text is None:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            try:
                data = root.clipboard_get()
            finally:
                root.destroy()
            return {"ok": True, "text": data[:500]}
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
        finally:
            root.destroy()
        return {"ok": True, "written": len(text)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


register(Capability(
    name="device.clipboard",
    title="读写剪贴板",
    risk="medium",
    description="读取你复制的内容，或往剪贴板里写东西",
    consequence="读取会暴露你最近复制的东西（可能是密码、验证码）；写入会覆盖你剪贴板里的内容。",
    args=["text"],
    func=_clipboard,
))

# ---------------------------------------------------------------- 锁屏

def _lock() -> dict:
    try:
        ctypes.windll.user32.LockWorkStation()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


register(Capability(
    name="device.lock",
    title="锁屏",
    risk="high",
    description="立即锁定电脑",
    consequence="会立刻锁屏，你正在全屏的东西（游戏、视频、未保存的文档）会被打断，"
                "需要重新输入密码解锁。",
    func=_lock,
))
