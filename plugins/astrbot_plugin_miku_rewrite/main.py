"""自我改写。

她可以修改自己的「行为准则」和「性格参数」，但必须经过主人批准。

三类东西：
    overlay.md   当前生效的行为准则（注入到 system prompt）
    traits.json  性格参数（话量 / 主动 / 语气）
    pending.jsonl 待批准的申请

流程：
    她自己觉得需要改（或从经验里升级）
        ↓ 提交申请（附理由）
    主人看到 /改写，批准或拒绝
        ↓ 批准
    写入 overlay / traits，并备份旧版本
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

DATA = Path(StarTools.get_data_dir("astrbot_plugin_miku_rewrite"))
OVERLAY = DATA / "overlay.md"
TRAITS = DATA / "traits.json"
PENDING = DATA / "pending.jsonl"
BACKUP = DATA / "backup"

DEFAULT_TRAITS = {
    "verbosity": 0.5,   # 0 极简 ~ 1 话痨
    "initiative": 0.5,  # 0 被动 ~ 1 主动
    "warmth": 0.6,      # 0 冷静 ~ 1 亲昵
    "formality": 0.3,   # 0 随意 ~ 1 正式
}

TRAIT_HINTS = {
    "verbosity": ("话量", "极简", "话痨"),
    "initiative": ("主动程度", "被动", "主动"),
    "warmth": ("亲昵度", "冷静", "黏人"),
    "formality": ("正式度", "随意", "正式"),
}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


@register("astrbot_plugin_miku_rewrite", "dsh", "受审批的自我改写", "1.0.0")
class RewritePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        DATA.mkdir(parents=True, exist_ok=True)
        BACKUP.mkdir(parents=True, exist_ok=True)

    # ---------- 读取 ----------
    def _traits(self) -> dict:
        if TRAITS.is_file():
            try:
                data = json.loads(TRAITS.read_text(encoding="utf-8"))
                return {k: _clamp(data.get(k, v)) for k, v in DEFAULT_TRAITS.items()}
            except Exception:  # noqa: BLE001
                pass
        return dict(DEFAULT_TRAITS)

    def _overlay(self) -> str:
        if OVERLAY.is_file():
            try:
                return OVERLAY.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                pass
        return ""

    def _pending(self) -> list[dict]:
        if not PENDING.is_file():
            return []
        rows = []
        for line in PENDING.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
        return rows

    def _save_pending(self, rows: list[dict]) -> None:
        PENDING.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )

    # ---------- 申请 ----------
    def _propose(self, kind: str, content: str, reason: str, source: str = "self") -> dict | None:
        if not content.strip():
            return None
        rows = self._pending()
        for r in rows:
            if r.get("kind") == kind and r.get("content") == content.strip():
                return r
        rec = {
            "id": f"{int(time.time())}{len(rows)}",
            "kind": kind,            # overlay / trait
            "content": content.strip(),
            "reason": reason.strip()[:120],
            "source": source,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        rows.append(rec)
        limit = max(1, int(self.config.get("max_pending", 10) or 10))
        if len(rows) > limit:
            rows = rows[-limit:]
        self._save_pending(rows)
        logger.info("[rewrite] 新申请 %s：%s", kind, content[:40])
        return rec

    # ---------- 生效 ----------
    def _apply(self, rec: dict) -> tuple[bool, str]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if OVERLAY.is_file():
            shutil.copy2(OVERLAY, BACKUP / f"overlay_{stamp}.md")
        if TRAITS.is_file():
            shutil.copy2(TRAITS, BACKUP / f"traits_{stamp}.json")

        kind = rec.get("kind")
        if kind == "overlay":
            current = self._overlay()
            line = str(rec.get("content") or "").strip()
            new = (current + "\n- " + line).strip() if current else "- " + line
            if len(new) > int(self.config.get("max_overlay_chars", 1200) or 1200):
                return False, "行为准则已经太长了，先清理一些再加"
            OVERLAY.write_text(new + "\n", encoding="utf-8")
            return True, "准则已加入"

        if kind == "trait":
            try:
                payload = json.loads(str(rec.get("content") or "{}"))
            except Exception:  # noqa: BLE001
                return False, "参数格式不对"
            traits = self._traits()
            changed = []
            for k, v in payload.items():
                if k in traits:
                    traits[k] = _clamp(v)
                    changed.append(f"{TRAIT_HINTS.get(k, (k,))[0]}→{traits[k]:.1f}")
            TRAITS.write_text(json.dumps(traits, ensure_ascii=False, indent=1), encoding="utf-8")
            return True, "参数已更新：" + "，".join(changed)
        return False, f"不认识的类型：{kind}"

    # ---------- 注入 ----------
    def _trait_text(self) -> str:
        t = self._traits()
        def band(v: float, lo: str, hi: str) -> str:
            return hi if v > 0.66 else (lo if v < 0.34 else "适中")
        return (
            f"话量：{band(t['verbosity'], '很简短', '愿意多说')}；"
            f"主动程度：{band(t['initiative'], '偏被动', '偏主动')}；"
            f"亲昵度：{band(t['warmth'], '保持距离', '很亲昵')}；"
            f"正式度：{band(t['formality'], '很随意', '很正式')}"
        )

    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enable", True):
            return
        try:
            parts = [f"[你现在说话的样子]\n{self._trait_text()}"]
            overlay = self._overlay()
            if overlay:
                parts.append(f"[你给自己定下的准则]\n{overlay}")
            block = "\n\n".join(parts)
            prompt = getattr(req, "system_prompt", None)
            req.system_prompt = f"{prompt}\n\n{block}" if prompt else block
        except Exception:  # noqa: BLE001
            logger.exception("[rewrite] 注入失败")

    # ---------- 她的工具 ----------
    @filter.llm_tool()
    async def propose_self_change(self, event: AstrMessageEvent, kind: str = "", content: str = "", reason: str = ""):
        """当你觉得自己需要改变某种行为方式时，用它提交申请（需要主人批准才生效）。

        Args:
            kind(string): overlay 表示加一条行为准则；trait 表示调整性格参数
            content(string): overlay 时填一句话准则；trait 时填 JSON，如 {"verbosity":0.7}
            reason(string): 为什么想这么改
        """
        if not self.config.get("allow_tools", True):
            return "主人没开这个权限"
        kind = (kind or "").strip()
        if kind not in ("overlay", "trait"):
            return "kind 只能是 overlay 或 trait"
        rec = self._propose(kind, content, reason, source="self")
        if not rec:
            return "内容为空，没提交"
        return f"申请已提交（编号 {rec['id']}），等主人批准。理由：{rec.get('reason', '')}"

    # ---------- 命令 ----------
    @filter.command("改写")
    async def rewrite_cmd(self, event: AstrMessageEvent):
        arg = event.message_str.partition(" ")[2].strip()

        if not arg:
            rows = self._pending()
            if not rows:
                yield event.plain_result("没有待批准的申请。\n\n当前参数：\n" + self._trait_text()
                                         + "\n\n准则：\n" + (self._overlay() or "（还没有）"))
                return
            lines = [f"【待批准 {len(rows)} 条】"]
            for r in rows:
                lines.append(f"{r['id']} [{r['kind']}] {r['content']}\n   理由：{r.get('reason', '')}")
            lines.append("\n批准：/改写 批准 编号    拒绝：/改写 拒绝 编号")
            yield event.plain_result("\n".join(lines))
            return

        parts = arg.split()
        cmd = parts[0]

        if cmd in ("批准", "拒绝") and len(parts) >= 2:
            target = parts[1]
            rows = self._pending()
            rec = next((r for r in rows if str(r.get("id")) == target), None)
            if rec is None:
                yield event.plain_result(f"没找到编号 {target}")
                return
            rows = [r for r in rows if str(r.get("id")) != target]
            self._save_pending(rows)
            if cmd == "拒绝":
                yield event.plain_result(f"已拒绝：{rec.get('content', '')[:60]}")
                return
            ok, msg = self._apply(rec)
            yield event.plain_result(("已生效：" if ok else "没生效：") + msg)
            return

        if cmd == "现状":
            yield event.plain_result("【当前状态】\n" + self._trait_text()
                                     + "\n\n准则：\n" + (self._overlay() or "（还没有）"))
            return

        if cmd == "回滚":
            files = sorted(BACKUP.glob("overlay_*.md"))
            if not files:
                yield event.plain_result("没有可回滚的备份")
                return
            shutil.copy2(files[-1], OVERLAY)
            yield event.plain_result(f"已回滚到 {files[-1].name}")
            return

        yield event.plain_result(
            "用法：\n/改写 —— 看待批\n/改写 批准 <编号>\n/改写 拒绝 <编号>\n"
            "/改写 现状\n/改写 回滚"
        )
