"""通过 pc_agent 远程驱动 VOCALOID 导出音频。"""

from __future__ import annotations

import os
import asyncio

import aiohttp

PC_URL = os.environ.get("PC_AGENT_URL", "http://127.0.0.1:19883")
TOKEN = os.environ.get("PC_AGENT_TOKEN", "")

ABORT = False


class Aborted(Exception):
    """用户主动中断。"""


class UnknownDialog(Exception):
    """遇到没见过的弹窗，停下来等人处理。"""


def _check() -> None:
    if ABORT:
        raise Aborted()


async def _sleep(seconds: float) -> None:
    """可被中断的等待。"""
    step = 0.2
    left = seconds
    while left > 0:
        _check()
        await asyncio.sleep(min(step, left))
        left -= step


async def _act(session: aiohttp.ClientSession, payload: dict) -> dict:
    _check()
    async with session.post(
        PC_URL + "/action",
        json=payload,
        headers={"X-Auth-Token": TOKEN},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        return await resp.json()


async def _key(session: aiohttp.ClientSession, key: str, wait: float = 0.4) -> None:
    await _act(session, {"action": "key", "key": key})
    await _sleep(wait)


async def _type(session: aiohttp.ClientSession, text: str, wait: float = 0.4) -> None:
    await _act(session, {"action": "type", "text": text})
    await _sleep(wait)


async def _windows(session: aiohttp.ClientSession) -> list[dict]:
    _check()
    try:
        async with session.get(
            PC_URL + "/windows",
            headers={"X-Auth-Token": TOKEN},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as r:
            info = await r.json()
        return info.get("windows") or []
    except Aborted:
        raise
    except Exception:  # noqa: BLE001
        return []


async def screenshot() -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            PC_URL + "/screenshot",
            headers={"X-Auth-Token": TOKEN},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            return await resp.json()


async def export_fixed(vpr_path: str, out_wav: str) -> str:
    """固定按键序列：Win+R 打开 → 回车 → N → Esc → 全选 → 导出。"""
    global ABORT
    ABORT = False
    async with aiohttp.ClientSession() as session:
        await _key(session, "win+r", 2.0)
        await _type(session, vpr_path, 3.0)
        await _key(session, "enter", 18.0)   # 打开 + 加载
        await _key(session, "enter", 4.0)    # 第一个弹窗：Free trial
        await _key(session, "n", 4.0)        # 第二个弹窗：否
        await _sleep(10.0)                   # 等第三个弹窗出现
        await _key(session, "enter", 3.0)    # 第三个弹窗：OK
        await _sleep(2.0)
        await _key(session, "enter", 2.0)    # 兜底
        await _key(session, "ctrl+a", 1.0)
        await activate("VOCALOID")
        await _sleep(0.6)
        await _key(session, "ctrl+e", 5.0)
        await _key(session, "enter", 5.0)
        await _type(session, out_wav, 2.0)
        await _key(session, "enter", 3.0)
    return out_wav


async def fetch_file(remote_path: str) -> bytes | None:
    """从用户电脑取回文件内容。"""
    import base64
    from urllib.parse import quote

    async with aiohttp.ClientSession() as session:
        url = f"{PC_URL}/file?path={quote(remote_path)}"
        try:
            async with session.get(url, headers={"X-Auth-Token": TOKEN},
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                info = await r.json()
        except Exception:  # noqa: BLE001
            return None
    if not info.get("ok"):
        return None
    return base64.b64decode(info["data"])


async def push_file(remote_path: str, data: bytes) -> bool:
    """把文件写到用户电脑上。"""
    import base64

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                PC_URL + "/file",
                json={"path": remote_path, "data": base64.b64encode(data).decode("ascii")},
                headers={"X-Auth-Token": TOKEN},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                info = await r.json()
        except Exception:  # noqa: BLE001
            return False
    return bool(info.get("ok"))


async def list_windows() -> list[dict]:
    """列出用户电脑上的顶层窗口。"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                PC_URL + "/windows",
                headers={"X-Auth-Token": TOKEN},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                info = await r.json()
        except Exception:  # noqa: BLE001
            return []
    return info.get("windows") or []


async def newest_file(folder: str, suffix: str = ".wav") -> str | None:
    """目录里最新的指定后缀文件（导出目录不确定时兜底）。"""
    from urllib.parse import quote

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{PC_URL}/list?dir={quote(folder)}",
                headers={"X-Auth-Token": TOKEN},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                info = await r.json()
        except Exception:  # noqa: BLE001
            return None
    for item in info.get("files") or []:
        if str(item.get("name", "")).lower().endswith(suffix):
            return folder + "\\" + str(item["name"])
    return None


async def wait_for_file(remote_path: str, seconds: float = 90) -> bool:
    """等用户电脑上出现指定文件。"""
    while seconds > 0:
        _check()
        if await fetch_file(remote_path) is not None:
            return True
        await _sleep(3)
        seconds -= 3
    return False


async def activate(title: str = "VOCALOID") -> bool:
    """把指定标题的窗口切到前台。"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                PC_URL + "/activate",
                json={"title": title},
                headers={"X-Auth-Token": TOKEN},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                info = await r.json()
        except Exception:  # noqa: BLE001
            return False
    return bool(info.get("ok"))


async def compose_open_and_export(vpr_path: str, out_wav: str, lyrics: str = "") -> str:
    """VOCALOID 已打开时：Ctrl+O 打开工程 → 关弹窗 → 导出。"""
    global ABORT
    ABORT = False
    await activate("VOCALOID")
    async with aiohttp.ClientSession() as session:
        await _key(session, "ctrl+o", 2.5)
        await _type(session, vpr_path, 2.5)
        await _key(session, "enter", 12.0)

        # 最多按 3 次回车关掉“声库未安装”这类提示（这个弹窗按回车就行）
        for _ in range(6):
            wins = await _windows(session)
            dialogs = [w for w in wins if w.get("class") == "#32770"]
            if not dialogs:
                break
            blob = " ".join(" ".join(w.get("texts") or []) for w in dialogs)
            if "否" in blob:
                await _key(session, "n", 2.0)
            else:
                await _key(session, "enter", 2.0)
        await _sleep(2.0)

        await activate("VOCALOID")
        await _sleep(1.0)


        await _key(session, "ctrl+a", 1.0)
        await activate("VOCALOID")
        await _sleep(0.6)
        await _key(session, "ctrl+e", 5.0)
        await _key(session, "enter", 5.0)
        # “另存为”对话框：Alt+N 定位到“文件名”输入框，再输完整路径
        await _key(session, "alt+n", 0.8)
        await _key(session, "ctrl+a", 0.3)
        await _type(session, out_wav, 2.5)
        await _key(session, "enter", 4.0)
    return out_wav


async def export_only(out_wav: str) -> str:
    """VOCALOID 已经打开并加载好工程时，只做全选+导出。"""
    global ABORT
    ABORT = False
    async with aiohttp.ClientSession() as session:
        await _key(session, "ctrl+a", 0.8)
        await activate("VOCALOID")
        await _sleep(0.6)
        await _key(session, "ctrl+e", 5.0)
        await _key(session, "enter", 5.0)   # 混音设置
        await _type(session, out_wav, 2.0)
        await _key(session, "enter", 3.0)
    return out_wav


async def export_audio(vpr_path: str, out_wav: str) -> str:
    """驱动 VOCALOID 打开工程并导出音频。"""
    global ABORT
    ABORT = False
    async with aiohttp.ClientSession() as session:
        # 1. Win+R 打开工程，回车没生效就重试
        await _key(session, "win+r", 2.0)
        await _type(session, vpr_path, 3.0)
        await _key(session, "enter", 2.0)   # 只按一次
        # 确认“运行”窗口已经关闭（不再发回车，避免落到后面的弹窗上）
        for _ in range(10):
            wins = await _windows(session)
            if not any(str(w.get("title") or "") == "运行" for w in wins):
                break
            await _sleep(1.0)

        # 2. 等 VOCALOID 窗口出现
        for _ in range(60):
            await _sleep(2)
            wins = await _windows(session)
            if any("VOCALOID" in str(w.get("title") or "").upper() for w in wins):
                break

        # 3. 逐个处理弹窗
        quiet = 0
        seen_any = False
        handled: set[int] = set()
        for _ in range(150):
            await _sleep(1.5)
            wins = await _windows(session)
            dialogs = [w for w in wins if w.get("class") == "#32770"]
            if not dialogs:
                quiet += 1
                # 至少要见过一次弹窗，并且连续 6 轮安静，才算加载完成
                if seen_any and quiet >= 6:
                    break
                continue
            quiet = 0
            seen_any = True
            fresh = [w for w in dialogs if int(w.get("hwnd") or 0) not in handled]
            if not fresh:
                await _sleep(1.0)
                continue

            # 只挑“有文字”的弹窗处理，跳过没有内容的空壳窗口
            candidates = [w for w in fresh if " ".join(w.get("texts") or []).strip()]
            if not candidates:
                await _sleep(1.0)
                continue

            # 一次只处理一个弹窗，按它自己的文字判断
            target = candidates[0]
            blob = " ".join(target.get("texts") or []).lower()

            # “运行”对话框：按回车确认（这是我们自己打开的）
            run_title = str(target.get("title") or "").strip()
            if "打开" in blob or "运行" in run_title or run_title.lower() == "run":
                await _key(session, "enter", 3.0)
                continue

            known = (
                "trial period" in blob
                or "free trial" in blob
                or "not been installed" in blob
                or "voice banks" in blob
                or ("否" in blob and "authoriz" in blob)
            )
            if not known:
                titles = " / ".join(str(w.get("title") or "") for w in candidates)
                raise UnknownDialog(f"{blob[:120]}（窗口标题：{titles}）")

            hwnd = int(target.get("hwnd") or 0)

            async def _still() -> bool:
                ws = await _windows(session)
                return any(int(x.get("hwnd") or 0) == hwnd for x in ws)

            await activate("VOCALOID")   # 防止别的窗口抢焦点
            await _sleep(0.5)

            if "free trial" in blob or "trial period" in blob:
                # Editor 未授权：默认按钮就是 Free trial，直接回车
                await _key(session, "enter", 2.5)
                # 纠正：如果回车落到了“是/否”弹窗上，立刻补按 N
                ws = await _windows(session)
                for x in ws:
                    if x.get("class") != "#32770":
                        continue
                    if "否" in " ".join(x.get("texts") or []):
                        await _key(session, "n", 1.5)
                        break
            elif "否" in blob:
                rect = target.get("rect") or [0, 0, 0, 0]
                left, top, right, bottom = rect
                w, h = max(1, right - left), max(1, bottom - top)

                # 只按 N（按回车会变成“是”，绝不能发）
                await _key(session, "n", 1.5)
                if await _still():
                    await _key(session, "n", 1.5)
                    if await _still():
                        # 兜底：点“否”按钮
                        await _act(session, {"action": "click",
                                             "x": int(left + w * 0.87),
                                             "y": int(top + h * 0.90)})
                        await _sleep(2.0)
            else:
                await _key(session, "enter", 2.0)

            # 弹窗确实消失了才标记为已处理，否则下一轮重试
            if not await _still():
                handled.add(hwnd)

        # 4. 全选 + 导出
        await _key(session, "ctrl+a", 0.6)
        await activate("VOCALOID")
        await _sleep(0.6)
        await _key(session, "ctrl+e", 5.0)
        await _key(session, "enter", 5.0)   # 混音设置
        await _type(session, out_wav, 2.0)
        await _key(session, "enter", 3.0)
    return out_wav
