"""HTTP 控制接口。

桌子上的程序只监听 127.0.0.1，由本机的 AstrBot 插件（或 SSH 隧道）调进来。

所有写操作都要带 token：请求头 X-Auth-Token，或 query 参数 token。

新增能力的方式：写一个 handler 注册进 ACTIONS 即可，不用改路由代码。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class ApiServer:
    """极简 HTTP 服务，把请求分发到注册的 action。"""

    def __init__(self, host: str, port: int, token: str, actions: dict[str, Handler]) -> None:
        self.host = host
        self.port = port
        self.token = token or ""
        self.actions = actions
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
                print("[api] " + (fmt % args), flush=True)

            def _authorized(self, params: dict) -> bool:
                if not outer.token:
                    return True
                given = self.headers.get("X-Auth-Token") or (params.get("token") or [""])[0]
                return given == outer.token

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                params = parse_qs(url.query)
                if not self._authorized(params):
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                action = params.get("action", [""])[0] or url.path.strip("/").replace("/", ".")
                fn = outer.actions.get(action)
                if not fn:
                    self._send(404, {"ok": False, "error": f"unknown action: {action}"})
                    return
                try:
                    self._send(200, fn({k: v[0] for k, v in params.items()}))
                except Exception as exc:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(exc)})

            def do_POST(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                params = parse_qs(url.query)
                if not self._authorized(params):
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except Exception:  # noqa: BLE001
                    body = {}
                action = url.path.strip("/").replace("/", ".") or "noop"
                fn = outer.actions.get(action)
                if not fn:
                    self._send(404, {"ok": False, "error": f"unknown action: {action}"})
                    return
                try:
                    self._send(200, fn(body))
                except Exception as exc:  # noqa: BLE001
                    self._send(500, {"ok": False, "error": str(exc)})

        self._server = ThreadingHTTPServer((self.host, self.port), _H)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[api] listening on http://{self.host}:{self.port}", flush=True)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
