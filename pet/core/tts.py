"""TTS：把要说的句子变成音频并播放。

默认对接本机的 GPT-SoVITS api_v2（/tts），也可以换成任意 OpenAI 兼容接口。

播放依赖系统能力：
- Windows：winsound（只支持 wav）
- 其他：调用 ffplay / aplay
"""

from __future__ import annotations

import os
import platform
import tempfile
import threading

import requests


class TTS:
    def __init__(self, cfg: dict) -> None:
        self.enable = bool(cfg.get("enable"))
        self.endpoint = str(cfg.get("endpoint") or "")
        self.ref_audio = str(cfg.get("ref_audio") or "")
        self.ref_text = str(cfg.get("ref_text") or "")
        self.text_lang = str(cfg.get("text_lang") or "ja")
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.enable and self.endpoint and self.ref_audio)

    def speak(self, text: str) -> bool:
        """后台生成并播放语音，不阻塞调用方。"""
        if not self.available() or not text.strip():
            return False
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()
        return True

    def _worker(self, text: str) -> None:
        with self._lock:
            try:
                audio = self._synthesize(text)
                if audio:
                    _play_wav(audio)
            except Exception as exc:  # noqa: BLE001
                print(f"[tts] 失败: {exc}", flush=True)

    def _synthesize(self, text: str) -> str | None:
        payload = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio,
            "prompt_text": self.ref_text,
            "prompt_lang": self.text_lang,
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
        }
        resp = requests.post(self.endpoint, json=payload, timeout=120)
        if resp.status_code != 200 or not resp.content:
            print(f"[tts] 合成失败 {resp.status_code}", flush=True)
            return None
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="miku_pet_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(resp.content)
        return path


def _play_wav(path: str) -> None:
    """播放一个 wav 文件，播完清理。"""
    try:
        if platform.system() == "Windows":
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME)
        else:
            player = "afplay" if platform.system() == "Darwin" else "aplay"
            os.system(f'{player} "{path}" >/dev/null 2>&1')
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
