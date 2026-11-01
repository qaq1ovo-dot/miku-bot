"""权限管理。

规则：
- low    ：问一次，可「本会话允许」
- medium ：每次都问
- high   ：每次都问，且永远不能记住（必须当场点）

授权记录存在 permissions.json，只保存 low 风险的长期允许。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.caps import REGISTRY


class PermissionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict = {"session": {}, "always": {}}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        self._data.setdefault("session", {})
        self._data.setdefault("always", {})

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 查询 ----------
    def is_granted(self, name: str) -> bool:
        """当前是否已经允许，不需要再问。"""
        cap = REGISTRY.get(name)
        if cap is None:
            return False
        if cap.risk == "high":
            return False                     # 高风险永不复用授权
        if name in self._data["always"]:
            return True
        if cap.risk == "low" and name in self._data["session"]:
            return True
        return False

    # ---------- 授权 ----------
    def grant_session(self, name: str) -> None:
        self._data["session"][name] = time.time()
        self._save()

    def grant_always(self, name: str) -> None:
        cap = REGISTRY.get(name)
        if cap and cap.risk == "low":
            self._data["always"][name] = time.time()
            self._save()

    def revoke_all(self) -> None:
        self._data = {"session": {}, "always": {}}
        self._save()

    def snapshot(self) -> dict:
        return {
            "session": sorted(self._data["session"]),
            "always": sorted(self._data["always"]),
        }
