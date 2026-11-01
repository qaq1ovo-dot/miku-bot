"""内置 LLM 客户端（OpenAI 兼容接口）。

有了它，桌宠可以完全独立运行：不需要 AstrBot，不需要任何平台。

兼容对象：DeepSeek、OpenAI、任何 OpenAI 兼容网关、本地 vLLM/Ollama。
"""

from __future__ import annotations

import requests


class LLM:
    def __init__(self, cfg: dict) -> None:
        self.base_url = str(cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.model = str(cfg.get("model") or "deepseek-chat")
        self.system_prompt = str(cfg.get("system_prompt") or "")
        self.temperature = float(cfg.get("temperature", 0.8))
        self.max_tokens = int(cfg.get("max_tokens", 300))
        self.history_turns = int(cfg.get("history_turns", 6))
        self._history: list[dict] = []

    def available(self) -> bool:
        return bool(self.base_url and self.model and (self.api_key or "localhost" in self.base_url))

    def chat(self, text: str) -> str:
        """发一句话，返回回复文本。

        Args:
            text: 用户输入。

        Returns:
            模型回复；失败时返回空字符串。
        """
        if not self.available() or not text.strip():
            return ""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self._history[-self.history_turns * 2:])
        messages.append({"role": "user", "content": text})

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                print(f"[llm] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                return ""
            data = resp.json()
            reply = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[llm] 请求失败: {exc}", flush=True)
            return ""

        if reply:
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": reply})
            self._history = self._history[-self.history_turns * 2:]
        return reply

    def reset(self) -> None:
        self._history.clear()
