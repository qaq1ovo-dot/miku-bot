"""桌宠 → 服务器上的她。

桌宠自己不会说话，它只是把你说的话转发给服务器上的 AstrBot 插件，
那边用同一份人格 + 记忆 + 情绪生成回复。

好处：桌宠上的聊天和 QQ 里是**同一个她**。
"""

from __future__ import annotations

import requests


class BotClient:
    def __init__(self, cfg: dict) -> None:
        self.endpoint = str(cfg.get("endpoint") or "http://127.0.0.1:19891").rstrip("/")
        self.token = str(cfg.get("token") or "")
        self.timeout = float(cfg.get("timeout", 90))

    def available(self) -> bool:
        return bool(self.endpoint)

    def chat(self, text: str) -> tuple[str, str]:
        """把一句话发给服务器上的她。

        Args:
            text: 用户说的话。

        Returns:
            (回复文本, 她的状态名)；失败时返回 ("", "error")。
        """
        headers = {"X-Auth-Token": self.token} if self.token else {}
        try:
            resp = requests.post(f"{self.endpoint}/chat", json={"text": text},
                                 headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                return "", "error"
            data = resp.json()
            return str(data.get("reply") or ""), str(data.get("state") or "idle")
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] 请求失败: {exc}", flush=True)
            return "", "error"

    def mood(self) -> dict:
        headers = {"X-Auth-Token": self.token} if self.token else {}
        try:
            resp = requests.get(f"{self.endpoint}/mood", headers=headers, timeout=10)
            return resp.json() if resp.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            return {}
