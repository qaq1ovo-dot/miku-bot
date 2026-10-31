"""直接改写 .vpr（zip+json）里的音素，避免在 VOCALOID 里手动重新插入歌词。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

# 拼音 → 日语式音素（近似即可，反正耳朵听的是“日语腔中文”）
_INITIALS = [
    ("zh", "j"), ("ch", "ch"), ("sh", "sh"), ("b", "b"), ("p", "p"), ("m", "m"),
    ("f", "f"), ("d", "d"), ("t", "t"), ("n", "n"), ("l", "l"), ("g", "g"),
    ("k", "k"), ("h", "h"), ("j", "j"), ("q", "ch"), ("x", "sh"), ("r", "r"),
    ("z", "z"), ("c", "ts"), ("s", "s"), ("y", "y"), ("w", "w"),
]

_FINALS = {
    "a": "a", "o": "o", "e": "e", "i": "i", "u": "u", "v": "yu", "ü": "yu",
    "ai": "ai", "ei": "ei", "ao": "ao", "ou": "ou",
    "an": "an", "en": "en", "ang": "an", "eng": "en", "ong": "on",
    "ia": "ya", "ie": "ye", "iao": "yao", "iu": "yu", "ian": "yen", "in": "in",
    "iang": "yan", "ing": "in", "iong": "yon", "i": "i",
    "ua": "wa", "uo": "wo", "uai": "wai", "ui": "wei", "uan": "wan", "un": "wen",
    "uang": "wang", "ueng": "wen", "ue": "we", "uan2": "wan",
    "er": "er",
}


def pinyin_to_phoneme(syl: str) -> str:
    s = syl.strip().lower()
    if not s:
        return "a"
    # 已经是假名，直接走假名表
    if any("\u3040" <= ch <= "\u30ff" for ch in s):
        return kana_to_phoneme(s)

    head = ""
    rest = s
    for ini, ph in _INITIALS:
        if s.startswith(ini) and len(s) > len(ini):
            head, rest = ph, s[len(ini):]
            break

    tail = _FINALS.get(rest) or _FINALS.get(rest[:2]) or _FINALS.get(rest[:1]) or rest
    if not head:
        return tail
    if tail in ("i", "u", "yu") and head in ("j", "ch", "sh", "y"):
        tail = "i" if tail == "i" else "u"
    return f"{head} {tail}"


_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "k a", "き": "k i", "く": "k u", "け": "k e", "こ": "k o",
    "が": "g a", "ぎ": "g i", "ぐ": "g u", "げ": "g e", "ご": "g o",
    "さ": "s a", "し": "sh i", "す": "s u", "せ": "s e", "そ": "s o",
    "ざ": "z a", "じ": "j i", "ず": "z u", "ぜ": "z e", "ぞ": "z o",
    "た": "t a", "ち": "ch i", "つ": "ts u", "て": "t e", "と": "t o",
    "だ": "d a", "ぢ": "j i", "づ": "z u", "で": "d e", "ど": "d o",
    "な": "n a", "に": "n i", "ぬ": "n u", "ね": "n e", "の": "n o",
    "は": "h a", "ひ": "h i", "ふ": "f u", "へ": "h e", "ほ": "h o",
    "ば": "b a", "び": "b i", "ぶ": "b u", "べ": "b e", "ぼ": "b o",
    "ぱ": "p a", "ぴ": "p i", "ぷ": "p u", "ぺ": "p e", "ぽ": "p o",
    "ま": "m a", "み": "m i", "む": "m u", "め": "m e", "も": "m o",
    "や": "y a", "ゆ": "y u", "よ": "y o",
    "ら": "r a", "り": "r i", "る": "r u", "れ": "r e", "ろ": "r o",
    "わ": "w a", "を": "o", "ん": "N",
    "ゃ": "y a", "ゅ": "y u", "ょ": "y o", "ー": "-",
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "k a", "キ": "k i", "ク": "k u", "ケ": "k e", "コ": "k o",
    "サ": "s a", "シ": "sh i", "ス": "s u", "セ": "s e", "ソ": "s o",
    "タ": "t a", "チ": "ch i", "ツ": "ts u", "テ": "t e", "ト": "t o",
    "ナ": "n a", "ニ": "n i", "ヌ": "n u", "ネ": "n e", "ノ": "n o",
    "ハ": "h a", "ヒ": "h i", "フ": "f u", "ヘ": "h e", "ホ": "h o",
    "マ": "m a", "ミ": "m i", "ム": "m u", "メ": "m e", "モ": "m o",
    "ヤ": "y a", "ユ": "y u", "ヨ": "y o",
    "ラ": "r a", "リ": "r i", "ル": "r u", "レ": "r e", "ロ": "r o",
    "ワ": "w a", "ヲ": "o", "ン": "N",
}


def kana_to_phoneme(text: str) -> str:
    parts = []
    for ch in text:
        if ch in _KANA:
            parts.append(_KANA[ch])
    return " ".join(parts) if parts else "a"


def patch_phonemes(vpr_path: str | Path, syllables: list[str]) -> int:
    """按顺序给 vocal 轨的音符写入音素，返回修改数量。"""
    vpr = Path(vpr_path)
    tmp = vpr.with_suffix(".patched")
    changed = 0

    with zipfile.ZipFile(vpr, "r") as zin:
        items = {n: zin.read(n) for n in zin.namelist()}

    key = next((n for n in items if n.endswith("sequence.json")), None)
    if key is None:
        return 0

    data = json.loads(items[key].decode("utf-8"))
    idx = 0
    for track in data.get("tracks", []):
        for part in track.get("parts", []):
            for note in part.get("notes", []) or []:
                if idx >= len(syllables):
                    break
                ph = pinyin_to_phoneme(syllables[idx])
                if ph:
                    note["phoneme"] = ph
                    changed += 1
                idx += 1

    items[key] = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in items.items():
            zout.writestr(name, blob)

    shutil.move(str(tmp), str(vpr))
    return changed
