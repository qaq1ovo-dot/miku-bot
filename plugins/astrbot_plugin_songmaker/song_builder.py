"""把模型给出的歌（结构/和弦/歌词）写成 vocaloid-cli 的 song.yaml 并导出工程。"""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

BASE = Path(r"C:\dsh\ws\music")
CLI = BASE / "node_modules" / "@jibaru" / "vocaloid-cli" / "dist" / "cli.js"

NOTE_BASE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
             "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _run(args: list[str]) -> dict:
    p = subprocess.run(["node", str(CLI), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(BASE), timeout=180)
    try:
        return json.loads(p.stdout.strip())
    except Exception:  # noqa: BLE001
        return {"ok": False, "raw": p.stdout[-200:]}


def _parse_chord(name: str) -> tuple[int, bool] | None:
    m = re.match(r"^([A-G][#b]?)(m|maj|min)?", name.strip())
    if not m or m.group(1) not in NOTE_BASE:
        return None
    return NOTE_BASE[m.group(1)], (m.group(2) == "m")


def _chord_tones(name: str) -> list[int]:
    parsed = _parse_chord(name)
    if not parsed:
        return [60, 64, 67]
    root, minor = parsed
    third = 3 if minor else 4
    return [(root + i) % 12 for i in (0, third, 7)]


def _fit(pc: int, low: int = 59, high: int = 73) -> int:
    """把音级放到指定音域里（B3~C#5）。"""
    for octave in range(3, 6):
        v = 12 * (octave + 1) + pc
        if low <= v <= high:
            return v
    return 60


def _pitch_name(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def _is_kana(ch: str) -> bool:
    return "\u3040" <= ch <= "\u30ff"


def _pinyin(ch: str) -> str:
    from pypinyin import lazy_pinyin

    py = lazy_pinyin(ch)
    return py[0] if py else "a"


def build(song_id: str, data: dict) -> dict:
    """data: {title, tempo, chords:[...], sections:[{name,kind,lines:[...]}]}"""
    tempo = int(data.get("tempo") or 90)
    tempo = max(60, min(180, tempo))
    chords = [str(c) for c in (data.get("chords") or []) if str(c).strip()] or ["C", "G", "Am", "F"]
    sections = data.get("sections") or []

    # 计算每段占多少小节
    layout = []  # (name, kind, start_bar, lines)
    bar = 3  # 前 2 小节前奏
    def _norm(line):
        """返回 (展示文本, 演唱读音)。"""
        if isinstance(line, dict):
            text = str(line.get("text") or line.get("kana") or "")
            kana = str(line.get("kana") or "")
            return text.strip(), kana.strip()
        return str(line).strip(), ""

    for sec in sections:
        raw_lines = sec.get("lines") or []
        lines = []
        readings = {}
        for x in raw_lines:
            text, kana = _norm(x)
            if not text:
                continue
            lines.append(text)
            if kana:
                readings[text] = kana
        if not lines:
            continue
        span = sum(max(1, math.ceil(len([c for c in ln if c.strip()]) / 4)) for ln in lines)
        layout.append((str(sec.get("name") or "part"), str(sec.get("kind") or "verse"), bar, lines))
        bar += span
    if not layout:
        return {"ok": False, "err": "没有可用的歌词段落"}

    total_bars = bar + 2  # 尾巴留 1 小节

    # 写 song.yaml
    path = BASE / song_id
    path.mkdir(parents=True, exist_ok=True)
    y = [f'schemaVersion: 1',
         f'title: "{song_id}"',
         f'style: ballad',
         f'key: C major',
         f'tempo: {tempo}',
         f'timeSignature: 4/4',
         f'end: "{total_bars}:1"',
         'sections:',
         '  - { name: "intro", kind: intro, at: "1:1" }']
    for name, kind, start, _ in layout:
        y.append(f'  - {{ name: "{name}", kind: {kind}, at: "{start}:1" }}')
    y.append('chords:')
    for b in range(1, total_bars):
        y.append(f'  - {{ at: "{b}:1", chord: "{chords[(b - 1) % len(chords)]}" }}')
    y.append('tracks:')
    y.append('  - id: lead')
    y.append('    kind: vocal')
    y.append('    voice: hanami')
    y.append('    language: ja')
    y.append('    notes: []')
    (path / "song.yaml").write_text("\n".join(y) + "\n", encoding="utf-8")

    # 生成音符
    notes = []
    lyric_seq: list[str] = []
    for _name, _kind, start, lines in layout:
        bar_i = start
        for ln in lines:
            # 有假名读音就用它发音，展示文本仍用原文
            read = readings.get(ln, "")
            source = read if read else ln
            chars = [c for c in source if c.strip() and c not in " 　"]
            syl = [c if _is_kana(c) else _pinyin(c) for c in chars]
            fast = tempo >= 118
            beat = 1
            row = bar_i
            for i, s in enumerate(syl):
                chord = chords[(row - 1) % len(chords)]
                tones = _chord_tones(chord)
                deg = tones[i % 3]
                # 轻快的歌：穿插上方邻音和八度跳进，避免一条直线
                if fast and i % 4 == 2:
                    deg = (deg + 2) % 12
                elif fast and i % 7 == 5:
                    deg = (deg + 7) % 12
                midi = _fit(deg)
                dur = "1/8" if (fast and i % 3 == 1) else "1/4"
                notes.append({"at": f"{row}:{beat}", "pitch": _pitch_name(midi),
                              "dur": dur, "lyric": s})
                lyric_seq.append(s)
                beat += 0.5 if dur == "1/8" else 1
                if beat > 4:
                    beat = 1
                    row += 1
            bar_i += max(1, math.ceil(len(syl) / 4))

    add = _run(["note", "add", song_id, "--track", "lead",
                "--params", json.dumps(notes, ensure_ascii=False)])
    if not add.get("ok"):
        return {"ok": False, "err": f"写音符失败：{add.get('err') or str(add)[:200]}"}

    _run(["arrange", song_id])
    out = f"build\\{song_id}.vpr"
    exp = _run(["song", "export", song_id, "--format", "vpr", "--out", out])
    vpr = BASE / "build" / f"{song_id}.vpr"
    if vpr.exists() and lyric_seq:
        try:
            import vpr_phonemes

            n_fixed = vpr_phonemes.patch_phonemes(vpr, lyric_seq)
        except Exception:  # noqa: BLE001
            n_fixed = 0
    return {"ok": exp.get("ok") and vpr.exists(), "vpr": vpr, "bars": total_bars,
            "notes": len(notes), "tempo": tempo, "chords": chords,
            "lyrics": " ".join(lyric_seq), "phonemes_fixed": n_fixed}
