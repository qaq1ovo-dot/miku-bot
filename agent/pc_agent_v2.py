"""PC 动作代理：给服务器端的 AstrBot 提供「看她屏幕」和「替她操作电脑」的能力。

能力范围（全部只监听 127.0.0.1，靠 SSH 隧道进来，token 鉴权）：
    GET  /health
    POST /screenshot                        截图
    POST /action {"action":"move","x":..,"y":..}
    POST /action {"action":"click","x":..,"y":..,"button":"left|right","clicks":1}
    POST /action {"action":"type","text":"..."}
    POST /action {"action":"key","key":"enter|esc|tab|space|f1|ctrl+c|..."}
    POST /action {"action":"scroll","dy":-3}
    POST /action {"action":"size"}          屏幕尺寸

用法（桌宠目录下）：
    uv run python pc_agent.py
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("PC_AGENT_TOKEN", "")
HOST = "127.0.0.1"
PORT = int(os.environ.get("PC_AGENT_PORT", "9883"))
MAX_WIDTH = 1280

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send_mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    extra = ctypes.c_ulong(0)
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(dx, dy, data, flags, 0, ctypes.pointer(extra))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_unicode_char(ch: str) -> None:
    for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
        extra = ctypes.c_ulong(0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, ctypes.pointer(extra))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


VK_MAP = {
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "menu": 0x5D, "apps": 0x5D,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[_c] = ord(_c.upper())
for _d in "0123456789":
    VK_MAP[_d] = ord(_d)


def _press_key(key: str) -> None:
    """支持 'enter' 以及 'ctrl+c' 这类组合键。"""
    parts = [p.strip().lower() for p in key.replace("+", " ").split() if p.strip()]
    if not parts:
        return
    if len(parts) == 1 and len(parts[0]) == 1 and parts[0] not in VK_MAP:
        _send_unicode_char(parts[0])
        return
    codes = [VK_MAP[p] for p in parts if p in VK_MAP]
    if not codes:
        return
    for code in codes:
        extra = ctypes.c_ulong(0)
        scan = user32.MapVirtualKeyW(code, 0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(code, scan, 0, 0, ctypes.pointer(extra))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.02)
    for code in reversed(codes):
        extra = ctypes.c_ulong(0)
        scan = user32.MapVirtualKeyW(code, 0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(code, scan, KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.02)


def capture_png_b64() -> tuple[str, int, int]:
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)

    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), img.width, img.height


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print("[pc-agent] " + (fmt % args), flush=True)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("X-Auth-Token") == TOKEN

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/health", ""):
            self._send(200, {"ok": True})
            return
        if path.startswith("/file"):
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(self.path).query)
            target = (qs.get("path") or [""])[0]
            if not target or not os.path.exists(target):
                self._send(404, {"error": "file not found", "path": target})
                return
            size = os.path.getsize(target)
            if size > 40 * 1024 * 1024:
                self._send(413, {"error": "file too large", "size": size})
                return
            with open(target, "rb") as fh:
                data = base64.b64encode(fh.read()).decode("ascii")
            self._send(200, {"ok": True, "name": os.path.basename(target),
                             "size": size, "data": data})
            return
        if path == "/list":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(self.path).query)
            folder = (qs.get("dir") or [""])[0]
            if not folder or not os.path.isdir(folder):
                self._send(404, {"error": "dir not found", "dir": folder})
                return
            items = []
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full):
                    items.append({"name": name, "size": os.path.getsize(full),
                                  "mtime": os.path.getmtime(full)})
            items.sort(key=lambda x: x["mtime"], reverse=True)
            self._send(200, {"ok": True, "dir": folder, "files": items[:50]})
            return
        if path == "/windows":
            items = []
            EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _cb(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                cbuf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cbuf, 256)
                texts = []
                if cbuf.value == "#32770":
                    EnumChild = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

                    def _child(chwnd, _lp):
                        ln = user32.GetWindowTextLengthW(chwnd)
                        if ln > 0:
                            b = ctypes.create_unicode_buffer(ln + 1)
                            user32.GetWindowTextW(chwnd, b, ln + 1)
                            texts.append(b.value)
                        return True

                    user32.EnumChildWindows(hwnd, EnumChild(_child), 0)
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                r = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                items.append({"title": buf.value, "class": cbuf.value,
                              "hwnd": int(hwnd), "texts": texts,
                              "rect": [r.left, r.top, r.right, r.bottom]})
                return True

            user32.EnumWindows(EnumProc(_cb), 0)
            self._send(200, {"ok": True, "windows": items})
            return
        if path == "/window":
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            self._send(200, {"ok": True, "title": buf.value, "hwnd": int(hwnd)})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return

        path = self.path.split("?")[0].rstrip("/")
        if path == "/file":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                info = json.loads(self.rfile.read(length).decode("utf-8"))
                target = info.get("path") or ""
                payload = info.get("data") or ""
                if not target or not payload:
                    raise ValueError("path/data required")
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(target, "wb") as fh:
                    fh.write(base64.b64decode(payload))
                self._send(200, {"ok": True, "path": target, "size": os.path.getsize(target)})
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": str(exc)})
            return

        if path == "/screenshot":
            try:
                b64, width, height = capture_png_b64()
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": f"capture failed: {exc}"})
                return
            self._send(200, {"ok": True, "image": b64, "width": width, "height": height})
            return

        if path == "/activate":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                info = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                info = {}
            needle = str(info.get("title") or "").upper()
            found = []

            EnumProc2 = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _cb2(hwnd, _lp):
                if not user32.IsWindowVisible(hwnd):
                    return True
                ln = user32.GetWindowTextLengthW(hwnd)
                if ln <= 0:
                    return True
                b = ctypes.create_unicode_buffer(ln + 1)
                user32.GetWindowTextW(hwnd, b, ln + 1)
                if needle and needle in b.value.upper():
                    found.append((hwnd, b.value))
                return True

            user32.EnumWindows(EnumProc2(_cb2), 0)
            if not found:
                self._send(404, {"error": "window not found", "title": needle})
                return
            hwnd, title = found[0]
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            self._send(200, {"ok": True, "title": title})
            return

        if path == "/action":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": f"bad request: {exc}"})
                return
            result = handle_action(data)
            self._send(200 if result.get("ok") else 400, result)
            return

        self._send(404, {"error": "not found"})


def handle_action(data: dict) -> dict:
    action = str(data.get("action") or "").lower()
    try:
        if action == "size":
            w, h = screen_size()
            return {"ok": True, "width": w, "height": h}

        if action == "move":
            user32.SetCursorPos(int(data["x"]), int(data["y"]))
            return {"ok": True}

        if action == "click":
            x, y = int(data["x"]), int(data["y"])
            button = str(data.get("button") or "left").lower()
            clicks = max(1, min(3, int(data.get("clicks") or 1)))
            user32.SetCursorPos(x, y)
            time.sleep(0.03)
            down, up = (
                (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
                if button == "right"
                else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
            )
            for _ in range(clicks):
                _send_mouse(down)
                time.sleep(0.02)
                _send_mouse(up)
                time.sleep(0.05)
            return {"ok": True}

        if action == "type":
            text = str(data.get("text") or "")
            for ch in text:
                if ch in "\n\r":
                    _press_key("enter")
                else:
                    _send_unicode_char(ch)
                    time.sleep(0.01)
            return {"ok": True, "length": len(text)}

        if action == "key":
            _press_key(str(data.get("key") or ""))
            return {"ok": True}

        if action == "scroll":
            _send_mouse(MOUSEEVENTF_WHEEL, data=int(data.get("dy") or -3) * 120)
            return {"ok": True}

        return {"ok": False, "error": f"unknown action: {action}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[pc-agent] listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
