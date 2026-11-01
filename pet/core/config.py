"""配置加载。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT = {
    "pet": {
        "asset_dir": "assets/pets/miku_pixel",
        "scale": 1.0,
        "start_pos": [1400, 700],
        "always_on_top": True,
        "click_through": False,
    },
    "api": {"host": "127.0.0.1", "port": 9890, "token": ""},
    "tts": {
        "enable": False,
        "endpoint": "http://127.0.0.1:9880/tts",
        "ref_audio": "",
        "ref_text": "",
        "text_lang": "ja",
    },
    "behavior": {"idle_to_sleep_sec": 1800, "bubble_sec": 6, "talking_sec": 0},
}

ROOT = Path(__file__).resolve().parent.parent


class Config:
    """一个很薄的配置包装，支持点号取值和默认值合并。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = _merge(DEFAULT, data or {})

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """从文件加载配置。

        Args:
            path: 配置文件路径；为空时依次尝试 config.json、config.example.json。

        Returns:
            Config 实例。
        """
        candidates = [Path(path)] if path else [ROOT / "config.json", ROOT / "config.example.json"]
        for p in candidates:
            if p and p.is_file():
                try:
                    return cls(json.loads(p.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    continue
        return cls()

    def get(self, dotted: str, default: Any = None) -> Any:
        """按 "a.b.c" 的路径取值。"""
        cur: Any = self._data
        for key in dotted.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def resolve(self, rel: str) -> Path:
        """把配置里的相对路径解析成绝对路径。"""
        p = Path(os.path.expanduser(rel))
        return p if p.is_absolute() else (ROOT / p)


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out
