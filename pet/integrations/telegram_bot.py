"""Telegram 适配器（示例）。

它让桌宠接入 Telegram：收到消息 → 问 LLM → 让桌宠说话 + 回复 Telegram。

这是一个独立的进程，桌宠本体不需要知道 Telegram 的存在。

用法：
    set TG_TOKEN=123456:ABC...
    python -m integrations.telegram_bot

配置（环境变量）：
    TG_TOKEN        Telegram Bot Token（必须）
    PET_URL         桌宠 API，默认 http://127.0.0.1:9890
    PET_TOKEN       桌宠 API token
    LLM_BASE        OpenAI 兼容接口地址
    LLM_KEY         API key
    LLM_MODEL       模型名
"""

from __future__ import annotations

import os
import time

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
PET_URL = os.environ.get("PET_URL", "http://127.0.0.1:9890").rstrip("/")
PET_TOKEN = os.environ.get("PET_TOKEN", "")
LLM_BASE = os.environ.get("LLM_BASE", "https://api.deepseek.com").rstrip("/")
LLM_KEY = os.environ.get("LLM_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "你是一个叫初音未来的虚拟女孩，说话自然、简短。")

API = f"https://api.telegram.org/bot{TG_TOKEN}"


def say_to_pet(text: str) -> None:
    """把一句话推给桌宠，让她冒气泡（失败也不影响聊天）。"""
    try:
        requests.post(
            f"{PET_URL}/say",
            json={"text": text, "voice": True},
            headers={"X-Auth-Token": PET_TOKEN} if PET_TOKEN else {},
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def ask_llm(text: str) -> str:
    """问 LLM，返回回复。"""
    headers = {"Content-Type": "application/json"}
    if LLM_KEY:
        headers["Authorization"] = f"Bearer {LLM_KEY}"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.8,
        "max_tokens": 300,
    }
    try:
        r = requests.post(f"{LLM_BASE}/chat/completions", json=payload, headers=headers, timeout=60)
        if r.status_code != 200:
            return ""
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def reply(chat_id: int, text: str) -> None:
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    if not TG_TOKEN:
        print("缺少 TG_TOKEN 环境变量", flush=True)
        return
    print("[tg] 开始轮询", flush=True)
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=60)
            data = r.json()
            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = (msg.get("chat") or {}).get("id")
                if not text or chat_id is None:
                    continue
                answer = ask_llm(text)
                if not answer:
                    continue
                say_to_pet(answer)
                reply(chat_id, answer)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[tg] 出错: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
