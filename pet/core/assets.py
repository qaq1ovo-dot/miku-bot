"""立绘资源加载。

每个桌宠目录下有一个 pet.json，描述所有状态用到的图片：

{
  "name": "miku_pixel",
  "size": [220, 320],
  "states": {
    "idle":    {"frames": ["raw/idle.png"], "loop": true},
    "talking": {"frames": ["raw/talking.png"], "loop": true},
    "walk":    {"spritesheet": {"file": "spritesheet.png", "frame_w": 64, "frame_h": 64,
                                "count": 8, "fps": 10}, "loop": true}
  },
  "default": "idle"
}

`frames` 和 `spritesheet` 二选一。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image


@dataclass
class StateAnim:
    """单个状态的一组帧。"""

    name: str
    frames: list[Image.Image] = field(default_factory=list)
    fps: float = 8.0
    loop: bool = True

    @property
    def duration_ms(self) -> int:
        return max(30, int(1000 / self.fps)) if self.fps > 0 else 0


class AssetPack:
    """一个桌宠的全部资源。"""

    def __init__(self, root: Path, scale: float = 1.0) -> None:
        self.root = Path(root)
        self.scale = scale
        self.name = self.root.name
        self.size: tuple[int, int] = (220, 320)
        self.states: dict[str, StateAnim] = {}
        self.default_state = "idle"
        self.meta: dict = {}
        self._load()

    def _load(self) -> None:
        cfg_path = self.root / "pet.json"
        if not cfg_path.is_file():
            raise FileNotFoundError(f"缺少 pet.json：{cfg_path}")

        meta = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.meta = meta
        if isinstance(meta.get("size"), list) and len(meta["size"]) == 2:
            self.size = (int(meta["size"][0]), int(meta["size"][1]))
        self.default_state = meta.get("default") or "idle"

        # 顶层定义的 spritesheet 会被每个状态继承
        shared_sheet = meta.get("spritesheet")
        for name, spec in (meta.get("states") or {}).items():
            spec = dict(spec)
            if shared_sheet and not spec.get("spritesheet"):
                spec["spritesheet"] = shared_sheet
            anim = self._build_state(name, spec)
            if anim.frames:
                self.states[name] = anim

        if not self.states:
            raise ValueError(f"{cfg_path} 里没有可用的状态")

        if self.default_state not in self.states:
            self.default_state = next(iter(self.states))

    def _build_state(self, name: str, spec: dict) -> StateAnim:
        fps = float(spec.get("fps") or 8.0)
        loop = bool(spec.get("loop", True))
        frames: list[Image.Image] = []

        if spec.get("spritesheet"):
            sheet = spec["spritesheet"]
            img = Image.open(self.root / sheet["file"]).convert("RGBA")
            fw, fh = int(sheet["frame_w"]), int(sheet["frame_h"])
            cols = int(sheet.get("cols") or max(1, img.width // fw))
            # 支持「按行取帧」：row + frame_count
            row = int(spec.get("row", 0))
            if "frame_count" in spec or "row" in spec:
                count = int(spec.get("frame_count") or 1)
                for i in range(count):
                    x0, y0 = i * fw, row * fh
                    frames.append(img.crop((x0, y0, x0 + fw, y0 + fh)))
            else:
                count = int(sheet.get("count") or 1)
                for i in range(count):
                    col, r = i % cols, i // cols
                    frames.append(img.crop((col * fw, r * fh, (col + 1) * fw, (r + 1) * fh)))

        for rel in spec.get("frames") or []:
            p = self.root / rel
            if p.is_file():
                frames.append(Image.open(p).convert("RGBA"))

        if self.scale != 1.0 and frames:
            w = max(1, int(frames[0].width * self.scale))
            h = max(1, int(frames[0].height * self.scale))
            frames = [f.resize((w, h), Image.LANCZOS) for f in frames]

        return StateAnim(name=name, frames=frames, fps=fps, loop=loop)

    def has(self, name: str) -> bool:
        return name in self.states

    def get(self, name: str) -> StateAnim:
        return self.states.get(name) or self.states[self.default_state]
