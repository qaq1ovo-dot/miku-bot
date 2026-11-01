"""给第三方插件补「写入长期记忆」的逻辑。

背景：QQ空间说说和主动消息这两个插件本身不会把内容写进长期记忆，
        导致她"说过就忘"。这个脚本在两处代码里插入记忆写入逻辑。

用法：
    python tools/patch_thirdparty.py            # 打补丁（幂等）
    python tools/patch_thirdparty.py --check    # 只检查有没有打上

注意：插件升级后代码可能变化，脚本会检测锚点是否存在，找不到就跳过并提示。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------- QQ 空间：发说说后写记忆 ----------

QZONE_LLM_METHOD = '''
    async def generate_memory_note(self, text: str) -> tuple[str, float]:
        """为已发布的说说补一条「当时的想法 + 重要性」，供长期记忆使用。

        Args:
            text: 刚发布的说说正文。

        Returns:
            (想法, 重要性 0~1)；失败时返回 ("", 0.5)。
        """
        provider = self._get_provider(self.cfg.llm.post_provider_id, None)
        if not isinstance(provider, Provider):
            return "", 0.5
        prompt = (
            f"你刚在 QQ 空间发了一条说说：\\n{text}\\n\\n"
            "用一句话（20 字以内）写出你发这条时脑子里的真实想法，"
            "并给出它对长期记忆的重要性（0 到 1 的小数，越小越不重要）。\\n"
            '只输出 JSON：{"thought": "...", "imp": 0.4}'
        )
        try:
            resp = await provider.text_chat(prompt=prompt)
            raw = resp.completion_text or ""
            match = re.search(r"\\{.*\\}", raw, re.S)
            if not match:
                return "", 0.5
            data = json.loads(match.group(0))
            thought = str(data.get("thought", ""))[:120]
            imp = float(data.get("imp", 0.5))
            return thought, max(0.0, min(1.0, imp))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"生成记忆备注失败：{e}")
            return "", 0.5
'''

QZONE_SCHED_METHOD = '''
    async def _write_memory(self, text: str) -> None:
        """把这条说说和当时想法写进长期记忆的待处理队列。

        Args:
            text: 刚发布的说说正文。
        """
        try:
            thought, imp = await self.service.llm.generate_memory_note(text)
            data_dir = StarTools.get_data_dir("miku_memory")
            data_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "text": f"在 QQ 空间发了条说说：{text}",
                "thought": thought,
                "imp": imp,
                "kind": "qzone",
            }
            with (data_dir / "pending.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\\n")
            logger.info(f"[AutoPublish] 已写入记忆：{text[:20]} imp={imp}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AutoPublish] 写入记忆失败：{e}")
'''

PROACTIVE_METHOD = '''
    @staticmethod
    def _remember_proactive(text: str) -> None:
        """把主动发出的这句话写进长期记忆的待处理队列。

        Args:
            text: 实际发出去的主动消息正文。
        """
        text = (text or "").strip()
        if not text:
            return
        try:
            from astrbot.core.star.star_tools import StarTools

            data_dir = StarTools.get_data_dir("miku_memory")
        except Exception:  # noqa: BLE001
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "text": f"我主动找主人说过：{text}",
            "thought": "想主动和他说句话",
            "imp": 0.5,
            "kind": "proactive",
        }
        with (data_dir / "pending.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\\n")
        logger.info(f"[主动消息] 已写入记忆：{text[:20]}")
'''

PROACTIVE_CALL = '''        # 写入长期记忆，让她之后记得自己主动说过什么。
        try:
            self._remember_proactive(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[主动消息] 写入记忆失败喵: {e}")

'''


def patch_qzone(root: Path, check_only: bool) -> list[str]:
    """给 QQ空间插件打补丁。

    Args:
        root: 插件目录。
        check_only: 只检查不修改。

    Returns:
        操作日志。
    """
    logs: list[str] = []
    llm = root / "core" / "llm_action.py"
    sched = root / "core" / "scheduler.py"

    if llm.is_file():
        text = llm.read_text(encoding="utf-8")
        if "generate_memory_note" in text:
            logs.append("qzone/llm_action.py 已有补丁")
        elif check_only:
            logs.append("qzone/llm_action.py 缺少补丁")
        elif "    async def generate_comment(" in text:
            text = text.replace("    async def generate_comment(", QZONE_LLM_METHOD + "\n    async def generate_comment(", 1)
            if "\nimport json" not in text:
                text = text.replace("import copy\n", "import copy\nimport json\n", 1)
            llm.write_text(text, encoding="utf-8")
            logs.append("qzone/llm_action.py 已打补丁")
        else:
            logs.append("qzone/llm_action.py 找不到锚点，跳过")

    if sched.is_file():
        text = sched.read_text(encoding="utf-8")
        if "_write_memory" in text:
            logs.append("qzone/scheduler.py 已有补丁")
        elif check_only:
            logs.append("qzone/scheduler.py 缺少补丁")
        elif 'await self.sender.send_admin_post(post, message="定时发说说")' in text:
            anchor = 'await self.sender.send_admin_post(post, message="定时发说说")'
            text = text.replace(anchor, anchor + "\n        await self._write_memory(text)", 1)
            # 方法插到文件末尾的类里
            text = text.rstrip() + "\n" + QZONE_SCHED_METHOD
            if "\nimport json" not in text:
                text = text.replace("import random\n", "import json\nimport random\n", 1)
            if "\nimport time" not in text:
                text = text.replace("import json\nimport random\n", "import json\nimport random\nimport time\n", 1)
            if "from astrbot.core.star.star_tools import StarTools" not in text:
                text = text.replace("from astrbot.api import logger\n",
                                    "from astrbot.api import logger\nfrom astrbot.core.star.star_tools import StarTools\n", 1)
            sched.write_text(text, encoding="utf-8")
            logs.append("qzone/scheduler.py 已打补丁")
        else:
            logs.append("qzone/scheduler.py 找不到锚点，跳过")

    return logs


def patch_proactive(root: Path, check_only: bool) -> list[str]:
    """给主动消息插件打补丁。

    Args:
        root: 插件目录。
        check_only: 只检查不修改。

    Returns:
        操作日志。
    """
    logs: list[str] = []
    sender = root / "core" / "message_sender.py"
    if not sender.is_file():
        return ["proactive/message_sender.py 不存在"]

    text = sender.read_text(encoding="utf-8")
    if "_remember_proactive" in text:
        return ["proactive/message_sender.py 已有补丁"]
    if check_only:
        return ["proactive/message_sender.py 缺少补丁"]

    anchor = "            return False\n\n        # Bot 在群聊发言后需要重置沉默计时。"
    if anchor not in text:
        return ["proactive 找不到锚点，跳过"]

    text = text.replace(anchor, "            return False\n\n" + PROACTIVE_CALL
                        + "        # Bot 在群聊发言后需要重置沉默计时。", 1)
    text = text.rstrip() + "\n" + PROACTIVE_METHOD
    for imp, after in (("import json", "import re\n"), ("import time", "import traceback\n"),
                       ("from datetime import datetime", "from pathlib import Path\n")):
        if f"\n{imp}" not in text:
            text = text.replace(after, imp + "\n" + after, 1)
    sender.write_text(text, encoding="utf-8")
    logs.append("proactive/message_sender.py 已打补丁")
    return logs


def main() -> int:
    parser = argparse.ArgumentParser(description="给第三方插件补记忆写入")
    parser.add_argument("--check", action="store_true", help="只检查不修改")
    parser.add_argument("--plugin-dir", default="", help="插件根目录（<AstrBot>/data/plugins）")
    args = parser.parse_args()

    if args.plugin_dir:
        root = Path(args.plugin_dir)
    else:
        candidates = [
            Path(r"C:\dsh\ws\astrbot\AstrBot-master\data\plugins"),
            Path.cwd() / "data" / "plugins",
        ]
        root = next((p for p in candidates if p.is_dir()), candidates[0])
    if not root.is_dir():
        print(f"插件目录不存在：{root}")
        return 1

    logs: list[str] = []
    logs += patch_qzone(root / "astrbot_plugin_qzone_ai", args.check)
    logs += patch_proactive(root / "astrbot_plugin_proactive_chat_ai", args.check)

    for line in logs:
        print(" -", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
