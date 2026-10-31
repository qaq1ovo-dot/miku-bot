"""让机器人自己写歌：自己定长度、结构、和弦、速度，导出成品并发给主人。"""

from __future__ import annotations

import os
import asyncio
import json
import re
import subprocess
import uuid
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Record
from astrbot.api.star import Context, Star, register

BASE = Path(os.environ.get("MIKU_MUSIC_DIR", "music")).expanduser()

SONG_PROMPT = """你在写一首完整的歌，全部由你决定：长度、段落结构、和弦、速度。

只输出 JSON，不要解释：
{
  "title": "歌名（2~8字）",
  "tempo": 90,
  "chords": ["C", "G", "Am", "F"],
  "sections": [
    {"name": "A", "kind": "verse", "lines": ["第一句", "第二句"]},
    {"name": "chorus", "kind": "chorus", "lines": ["第一句", "第二句"]}
  ]
}

规则：
1. 3~6 个段落，verse/chorus/bridge 由你安排，长度随你
2. 每句 5~12 个字，口语化，有画面感
3. chords 用标准写法（C G Am F Em Dm 等），4~8 个循环
4. tempo 60~180
5. 不要重复同样的句子

语言：中文或日文都行。
- 中文：lines 直接写中文句子
- 日文：lines 写日文原文，并额外给 "kana" 字段，填整句的平假名读音（不许留汉字）

JSON 示例（日文）：
{"title": "例", "tempo": 90, "chords": ["C","G","Am","F"],
 "sections": [{"name":"A","kind":"verse",
   "lines":[
     {"text":"夜がまだ明けないから","kana":"よるがまだあけないから"}
   ]}]}
"""


def _safe_name(title: str) -> str:
    from pypinyin import lazy_pinyin

    slug = "".join(lazy_pinyin(title))[:24]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug)
    return slug or "song"


@register("astrbot_plugin_songmaker", "dsh", "自己写歌编曲并导出成品", "2.0.0")
class SongMakerPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._auto_task = None

    async def initialize(self) -> None:
        """插件加载后启动自发写歌循环。"""
        if not self.config.get("auto_song", True):
            return
        self._auto_task = asyncio.create_task(self._auto_loop())

    async def _auto_loop(self) -> None:
        import random as _r

        lo = float(self.config.get("auto_min_hours", 6))
        hi = float(self.config.get("auto_max_hours", 14))
        target = str(self.config.get("target_umo") or "")
        await asyncio.sleep(120)  # 启动后先等两分钟
        while True:
            try:
                wait = _r.uniform(lo, hi) * 3600
                await asyncio.sleep(wait)
                ok = await self._voc_wakoi_ready()
                if not ok:
                    logger.info("[song] 自动写歌跳过：VOCALOID 没开")
                    continue
                logger.info("[song] 自动写歌开始")
                await self._full_song_job(target)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("[song] 自动写歌异常")
                await asyncio.sleep(1800)

    async def _voc_wakoi_ready(self) -> bool:
        from . import pc_macro

        try:
            wins = await pc_macro.list_windows()
        except Exception:  # noqa: BLE001
            return False
        return any("VOCALOID" in str(w.get("title") or "").upper() for w in wins)

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        owners = self.config.get("owner_ids") or []
        return str(event.get_sender_id()) in [str(x) for x in owners]

    async def _compose(self, lang: str = "", title_hint: str = "", tempo_hint: int = 0) -> dict:
        provider = self.context.get_using_provider()
        if provider is None:
            raise RuntimeError("没有可用的模型")
        prompt = SONG_PROMPT
        if title_hint:
            prompt += f"\n\n【歌名要求】这首歌的歌名就叫《{title_hint}》，歌词要围绕这个题目写。"
        if tempo_hint:
            prompt += f"\n\n【速度要求】tempo 必须设为 {tempo_hint} 左右（±8以内），不要更低。"
        if lang == "ja":
            prompt += (
                "\n\n【本次必须写日文歌】所有 lines 的 text 必须是日文，"
                "并且每行都要给出对应的 kana（整句平假名，不许留汉字）。歌词里不许出现中文汉字词语。"
            )
        resp = await provider.text_chat(prompt=prompt)
        text = (resp.completion_text or "").strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise RuntimeError(f"模型没给出 JSON：{text[:80]}")
        data = json.loads(match.group(0))
        if not data.get("sections"):
            raise RuntimeError("没有段落")
        return data

    # ---------- 后台任务 ----------
    async def _full_song_job(self, origin: str, lang: str = "", title_hint: str = "", tempo_hint: int = 0) -> None:
        from . import pc_macro, song_builder

        plugin = self

        async def say(text: str) -> None:
            await plugin.context.send_message(origin, MessageChain().message(text))

        logger.info("[song] job start")
        try:
            await say("嗯……我写一首，写完直接导出来。")
            data = await plugin._compose(lang, title_hint, tempo_hint)
            title = str(data.get("title") or "无题")
            song_id = f"{_safe_name(title)}_{uuid.uuid4().hex[:4]}"
            res = song_builder.build(song_id, data)
            if not res.get("ok"):
                await say(f"生成工程失败：{res.get('err')}")
                return

            vpr = res["vpr"]
            remote_vpr = rf"G:\miku\in\{song_id}.vpr"
            remote_wav = rf"G:\miku\out\{song_id}.wav"

            if not await pc_macro.push_file(remote_vpr, Path(vpr).read_bytes()):
                await say("工程传不到你电脑上，检查隧道和 pc_agent。")
                return

            await say(f"《{title}》写好，开始导出。")
            await pc_macro.compose_open_and_export(remote_vpr, remote_wav, res.get("lyrics", ""))

            found = await pc_macro.wait_for_file(remote_wav, 45)
            if not found:
                # 导出目录会跟随工程所在目录，in/out 都找一遍
                for folder in (r"G:\miku\in", r"G:\miku\out", r"G:\miku"):
                    alt = await pc_macro.newest_file(folder, ".wav")
                    if alt:
                        remote_wav = alt
                        found = True
                        break
            if not found:
                await say("导出的音频没找到，可能 VOCALOID 没开着或没保存成功。")
                return

            blob = await pc_macro.fetch_file(remote_wav)
            if not blob:
                await say("音频取不回来。")
                return

            local = BASE / "build" / f"{song_id}.wav"
            local.write_bytes(blob)
            def _text_of(x):
                if isinstance(x, dict):
                    return str(x.get("text") or x.get("kana") or "")
                return str(x)

            lines = "\n".join(
                _text_of(ln) for sec in data.get("sections", []) for ln in sec.get("lines", [])
            )
            await say(f"《{title}》好了。\n\n{lines}")

            # wav → mp3（体积小十倍，QQ 里能直接播放）
            mp3 = local.with_suffix(".mp3")
            ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
            try:
                subprocess.run(
                    [ffmpeg, "-y", "-i", str(local), "-codec:a", "libmp3lame",
                     "-q:a", "4", str(mp3)],
                    capture_output=True, timeout=300, check=False,
                )
            except Exception:  # noqa: BLE001
                logger.exception("[song] mp3 convert failed")

            if mp3.exists():
                await plugin.context.send_message(
                    origin, MessageChain([File(name=mp3.name, file=str(mp3))])
                )
                clip = BASE / "build" / f"{song_id}.voice.mp3"
                try:
                    subprocess.run(
                        [ffmpeg, "-y", "-ss", "25", "-t", "55", "-i", str(mp3),
                         "-codec:a", "libmp3lame", "-q:a", "6", str(clip)],
                        capture_output=True, timeout=180, check=False,
                    )
                    if clip.exists():
                        await plugin.context.send_message(
                            origin, MessageChain([Record(file=str(clip))])
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("[song] voice clip failed")
            else:
                await plugin.context.send_message(
                    origin, MessageChain([File(name=local.name, file=str(local))])
                )

            # 写进内心日志，让她“记得”自己写过这首歌
            try:
                import time as _t

                journal = Path(
                    str(Path(os.environ.get("MIKU_MIND_DIR", "miku_mind")) / "mind.jsonl")
                )
                if journal.parent.exists():
                    with journal.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "ts": _t.time(),
                            "time": _t.strftime("%Y-%m-%d %H:%M"),
                            "kind": "song",
                            "text": f"我刚写完一首歌，叫《{title}》，已经发给主人了。"
                                    f"和弦是 {' '.join(res.get('chords') or [])}，{res.get('tempo')} BPM。",
                            "say": "",
                        }, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001
                logger.exception("[song] journal append failed")
        except pc_macro.Aborted:
            await say("已中断。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[song] job failed")
            await say(f"出错了：{exc}")

    def _find_local_song(self, name: str) -> Path | None:
        """在曲库里按名字找音频（支持拼音/包含匹配）。"""
        from pypinyin import lazy_pinyin

        if not name:
            return None
        key = re.sub(r"[《》\s]", "", name)
        py = "".join(lazy_pinyin(key)).lower()
        folders = [BASE / "library", BASE / "build"]
        best = None
        for folder in folders:
            if not folder.exists():
                continue
            for p in folder.iterdir():
                if p.suffix.lower() not in (".mp3", ".wav", ".flac", ".m4a"):
                    continue
                stem = p.stem.lower()
                if key in stem or (py and py in stem):
                    if best is None or p.stat().st_size > best.stat().st_size:
                        best = p
        return best

    async def _send_audio(self, origin: str, path: Path) -> None:
        if path.suffix.lower() != ".mp3":
            mp3 = path.with_suffix(".mp3")
            if not mp3.exists():
                subprocess.run(
                    [ffmpeg, "-y", "-i", str(path),
                     "-codec:a", "libmp3lame", "-q:a", "4", str(mp3)],
                    capture_output=True, timeout=300, check=False,
                )
            if mp3.exists():
                path = mp3
        await self.context.send_message(
            origin, MessageChain([File(name=path.name, file=str(path))])
        )

    async def _play_or_make(self, origin: str, name: str) -> None:
        """库里有就发，没有就现编一首。"""
        hit = self._find_local_song(name)
        if hit:
            await self.context.send_message(
                origin, MessageChain().message(f"找到了《{name}》，给你。")
            )
            await self._send_audio(origin, hit)
            return
        await self.context.send_message(
            origin,
            MessageChain().message(f"没找到《{name}》，我自己写一首同名的。"),
        )
        await self._full_song_job(origin, "", name)
    async def _cover_job(self, origin: str, query: str) -> None:
        """翻唱：找现成 VSQX 工程 → 导入 → 导出音频。"""
        from . import cover, pc_macro

        plugin = self

        async def say(text: str) -> None:
            await plugin.context.send_message(origin, MessageChain().message(text))

        try:
            hit = cover.find_song(query)
            if not hit:
                await say(f"我的工程库里没有《{query}》这首，要不我写首原创？说\"写首歌\"就行。")
                return

            await say(f"找到《{hit['name']}》的工程，正在导入……")
            song_id = f"cover_{uuid.uuid4().hex[:6]}"
            res = cover.build_cover(song_id, hit["url"])
            if not res.get("ok"):
                await say(f"导入失败：{res.get('err')}")
                return

            vpr = res["vpr"]
            remote_vpr = rf"G:\miku\in\{song_id}.vpr"
            remote_wav = rf"G:\miku\out\{song_id}.wav"

            if not await pc_macro.push_file(remote_vpr, Path(vpr).read_bytes()):
                await say("工程传不到你电脑上，检查隧道和 pc_agent。")
                return

            await say(f"《{hit['name']}》准备好（{res.get('notes')} 个音符），开始导出。")
            await pc_macro.compose_open_and_export(remote_vpr, remote_wav)

            found = await pc_macro.wait_for_file(remote_wav, 45)
            if not found:
                for folder in (r"G:\miku\in", r"G:\miku\out", r"G:\miku"):
                    alt = await pc_macro.newest_file(folder, ".wav")
                    if alt:
                        remote_wav = alt
                        found = True
                        break
            if not found:
                await say("导出的音频没找到。")
                return

            blob = await pc_macro.fetch_file(remote_wav)
            if not blob:
                await say("音频取不回来。")
                return

            local = BASE / "build" / f"{song_id}.wav"
            local.write_bytes(blob)
            mp3 = local.with_suffix(".mp3")
            subprocess.run(
                [ffmpeg, "-y", "-i", str(local),
                 "-codec:a", "libmp3lame", "-q:a", "4", str(mp3)],
                capture_output=True, timeout=300, check=False,
            )
            if mp3.exists():
                await plugin.context.send_message(
                    origin, MessageChain([File(name=mp3.name, file=str(mp3))])
                )
                clip = BASE / "build" / f"{song_id}.voice.mp3"
                subprocess.run(
                    [ffmpeg, "-y", "-ss", "20", "-t", "55",
                     "-i", str(mp3), "-codec:a", "libmp3lame", "-q:a", "6", str(clip)],
                    capture_output=True, timeout=180, check=False,
                )
                if clip.exists():
                    await plugin.context.send_message(
                        origin, MessageChain([Record(file=str(clip))])
                    )
            else:
                await plugin.context.send_message(
                    origin, MessageChain([File(name=local.name, file=str(local))])
                )
        except pc_macro.Aborted:
            await say("已中断。")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[cover] failed")
            await say(f"翻唱出错：{exc}")

    @filter.command("翻唱")
    async def cover_cmd(self, event: AstrMessageEvent, name: str = ""):
        """翻唱某首歌（用现成 VSQX 工程）"""
        if not self._is_owner(event):
            event.stop_event()
            return
        if not name:
            yield event.plain_result("翻唱哪首？/翻唱 曲名")
            return
        asyncio.create_task(self._cover_job(event.unified_msg_origin, name))
        yield event.plain_result(f"找找《{name}》的工程……")
        return
    # ---------- 触发方式 ----------

    @filter.on_llm_request()
    async def catch_keyword(self, event: AstrMessageEvent, req) -> None:
        """不带斜杠说"写歌"也能触发。"""
        if not self._is_owner(event):
            return
        text = (event.message_str or "").strip().replace(" ", "")
        text = re.sub(r"[，。！？,.!?~～]", "", text)

        # 「唱XXX」：先从曲库找，找不到就用这个名字现写一首
        m = re.match(r"^唱(?:一首|首|一个|段)?(.+)$", text)
        if m and m.group(1) and m.group(1).strip("《》") not in ("歌", "首歌", "一首歌", "曲"):
            name = m.group(1).strip("《》")
            event.stop_event()
            logger.info("[song] 唱：%s", name)
            asyncio.create_task(self._play_or_make(event.unified_msg_origin, name))
            try:
                await event.send(MessageChain().message(f"找找《{name}》……"))
            except Exception:  # noqa: BLE001
                logger.exception("[song] send ack failed")
            return

        # 光说“唱歌/唱首歌”＝随便写一首
        if text in ("唱歌", "唱首歌", "唱一曲", "唱一个"):
            event.stop_event()
            asyncio.create_task(self._full_song_job(event.unified_msg_origin))
            try:
                await event.send(MessageChain().message("好，我唱一首自己写的。"))
            except Exception:  # noqa: BLE001
                logger.exception("[song] send ack failed")
            return

        # 写歌类：短句里带“歌”且带“写/做/来/生成/再来”
        if len(text) > 14 or "歌" not in text:
            return
        if not re.search(r"(写|做|来|生成|再来|整).{0,8}歌", text):
            return
        event.stop_event()
        logger.info("[song] keyword hit: %s", text)
        lang = "ja" if re.search(r"日语|日文|日本|にほんご", text) else ""
        tempo = 150 if re.search(r"轻快|欢快|快速|快一点|快些|摇滚|rock|元气|活泼", text) else 0
        asyncio.create_task(self._full_song_job(event.unified_msg_origin, lang, "", tempo))
        try:
            await event.send(MessageChain().message("收到，开始写。"))
        except Exception:  # noqa: BLE001
            logger.exception("[song] send ack failed")
    @filter.command("写歌")
    async def full_song(self, event: AstrMessageEvent):
        """一条龙：她写歌 → 自己导出 → 把成品发给你"""
        if not self._is_owner(event):
            event.stop_event()
            return
        text = event.message_str or ""
        lang = "ja" if re.search(r"日语|日文|日本|にほんご", text) else ""
        tempo = 150 if re.search(r"轻快|欢快|快速|快一点|快些|摇滚|rock|元气|活泼", text) else 0
        asyncio.create_task(self._full_song_job(event.unified_msg_origin, lang, "", tempo))
        yield event.plain_result("开始写歌 + 导出，想停发 /中断")
        return

    @filter.command("唱")
    async def sing(self, event: AstrMessageEvent, name: str = ""):
        """唱一首：库里有就发，没有就现编"""
        if not self._is_owner(event):
            event.stop_event()
            return
        if not name:
            yield event.plain_result("唱哪首？发 /唱 歌名")
            return
        asyncio.create_task(self._play_or_make(event.unified_msg_origin, name))
        yield event.plain_result(f"找找《{name}》……")
        return

    @filter.command("中断")
    async def abort_now(self, event: AstrMessageEvent):
        """立刻停止正在跑的自动操作"""
        if not self._is_owner(event):
            event.stop_event()
            return
        from . import pc_macro

        pc_macro.ABORT = True
        yield event.plain_result("收到，正在停。")

    @filter.command("预览")
    async def preview(self, event: AstrMessageEvent):
        """看一眼电脑现在的画面"""
        if not self._is_owner(event):
            event.stop_event()
            return
        from . import pc_macro

        data = await pc_macro.screenshot()
        if not data.get("ok"):
            yield event.plain_result(f"截图失败：{data.get('error')}")
            return
        yield event.make_result().base64_image(data.get("image", ""))
        yield event.plain_result("确认没问题就发 /导出。")

    @filter.command("导出")
    async def export_now(self, event: AstrMessageEvent, out: str = ""):
        """只导出当前已打开的工程"""
        if not self._is_owner(event):
            event.stop_event()
            return

        import importlib
        import time as _t

        from . import pc_macro

        importlib.reload(pc_macro)

        if not out:
            out = rf"G:\miku\out\song_{_t.strftime('%m%d_%H%M')}.wav"

        origin = event.unified_msg_origin

        async def _run_export() -> None:
            try:
                await pc_macro.export_only(out)
            except pc_macro.Aborted:
                await self.context.send_message(origin, MessageChain().message("已中断。"))
                return
            except Exception as exc:  # noqa: BLE001
                await self.context.send_message(origin, MessageChain().message(f"出错：{exc}"))
                return
            data = await pc_macro.screenshot()
            if data.get("ok") and data.get("image"):
                await self.context.send_message(origin, MessageChain().base64_image(data["image"]))
            await self.context.send_message(
                origin, MessageChain().message(f"跑完了，看看 {out} 有没有生成。")
            )

        asyncio.create_task(_run_export())
        yield event.plain_result(
            f"开始导出到 {out}\n先确认 VOCALOID 在前台。想停发 /中断"
        )
        return
