"""下载 B 站视频的音频轨（单 P）。"""

from __future__ import annotations
import os

import subprocess
import sys
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
OUT = Path(os.environ.get("MIKU_VOICE_DIR", "voices"))
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")


def get_cid(bvid: str) -> tuple[int, str, int]:
    r = requests.get(
        "https://api.bilibili.com/x/player/pagelist",
        params={"bvid": bvid},
        headers=H,
        timeout=30,
    )
    p = r.json()["data"][0]
    return p["cid"], p["part"], p["duration"]


def download(bvid: str, name: str) -> Path | None:
    cid, part, dur = get_cid(bvid)
    print("目标:", bvid, part, dur, "秒")
    j = requests.get(
        "https://api.bilibili.com/x/player/playurl",
        params={"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1},
        headers=H,
        timeout=30,
    ).json()
    audio = j["data"]["dash"]["audio"]
    audio.sort(key=lambda a: a.get("bandwidth", 0), reverse=True)
    url = audio[0]["baseUrl"]

    raw = OUT / f"{name}.m4s"
    with requests.get(url, headers=H, stream=True, timeout=300) as r:
        r.raise_for_status()
        with raw.open("wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
    print("下载:", raw.stat().st_size, "bytes")

    wav = OUT / f"{name}.wav"
    subprocess.run(
        [FFMPEG, "-y", "-i", str(raw), "-ac", "1", "-ar", "44100", str(wav)],
        capture_output=True,
        check=False,
    )
    print("转 wav:", wav.exists(), wav.stat().st_size if wav.exists() else 0)
    return wav if wav.exists() else None


if __name__ == "__main__":
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1854y167gg"
    name = sys.argv[2] if len(sys.argv) > 2 else "drama_lijianxi"
    download(bvid, name)
