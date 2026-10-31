"""翻唱：从 ustvsqx 站找工程 → 导入 → 导出 vpr。"""

from __future__ import annotations

import html as html_lib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import requests

BASE = Path(r"C:\dsh\ws\music")
CLI = BASE / "node_modules" / "@jibaru" / "vocaloid-cli" / "dist" / "cli.js"
INDEX = "https://ustvsqx.github.io/"
UA = {"User-Agent": "Mozilla/5.0"}


def _run(args: list[str]) -> dict:
    p = subprocess.run(["node", str(CLI), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(BASE), timeout=300)
    try:
        return json.loads(p.stdout.strip())
    except Exception:  # noqa: BLE001
        return {"ok": False, "raw": p.stdout[-200:], "err": p.stderr[-200:]}


def list_songs() -> list[dict]:
    """抓站上的歌曲表：name + vsqx 分享链接。"""
    page = requests.get(INDEX, headers=UA, timeout=30).text
    songs: list[dict] = []
    for row in re.split(r"<tr[^>]*>", page)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if not cells:
            continue
        name = html_lib.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
        # 只认锚文本是 vsqx 的链接
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*\.?vsqx', row, re.I)
        if not name or not m:
            continue
        songs.append({"name": name, "url": m.group(1)})
    return songs


def local_projects() -> list[dict]:
    """本地工程库：music/vsqx 下的 .vsqx / .ust / .vpr 文件。"""
    folder = BASE / "vsqx"
    if not folder.exists():
        return []
    return [
        {"name": p.stem, "path": str(p), "local": True}
        for p in folder.iterdir()
        if p.suffix.lower() in (".vsqx", ".ust", ".vpr")
    ]


def find_song(query: str) -> dict | None:
    q = query.strip().lower()
    if not q:
        return None

    # 先查本地工程库
    for s in local_projects():
        if q in s["name"].lower():
            return s

    songs = list_songs()
    # 完全包含优先
    for s in songs:
        if q in s["name"].lower():
            return s
    # 英文/罗马字匹配括号内容
    for s in songs:
        if q in s["name"].lower().replace(" ", ""):
            return s
    return None


def download_vsqx(url: str, dest: Path) -> Path | None:
    """下载分享链接（可能是 zip），取出里面的 .vsqx。"""
    if not url.endswith("/download"):
        url = url.rstrip("/") + "/download"
    r = requests.get(url, headers=UA, timeout=120, allow_redirects=True)
    if r.status_code != 200 or not r.content:
        return None
    data = r.content
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".vsqx")]
            if not names:
                return None
            # 优先 noedit 版本
            names.sort(key=lambda n: ("noedit" not in n.lower(), len(n)))
            dest.write_bytes(z.read(names[0]))
    else:
        dest.write_bytes(data)
    return dest if dest.exists() else None


def build_cover(song_id: str, share_url: str) -> dict:
    """下载 → 导入 → 导出 vpr。"""
    raw = Path(share_url) if Path(share_url).exists() else BASE / "midi" / f"{song_id}.vsqx"
    if not Path(share_url).exists() and not download_vsqx(share_url, raw):
        return {"ok": False, "err": "下载或解压失败"}

    out_dir = BASE / song_id
    imp = _run(["song", "import", str(raw), "--out", str(out_dir)])
    if not imp.get("ok"):
        return {"ok": False, "err": f"导入失败：{str(imp)[:150]}"}

    out = f"build\\{song_id}.vpr"
    exp = _run(["song", "export", song_id, "--format", "vpr", "--out", out])
    vpr = BASE / "build" / f"{song_id}.vpr"
    if not vpr.exists():
        return {"ok": False, "err": f"导出失败：{str(exp)[:150]}"}

    tracks = (imp.get("data") or {}).get("tracks") or []
    notes = sum(int(t.get("notes") or 0) for t in tracks)
    return {"ok": True, "vpr": vpr, "notes": notes, "tracks": len(tracks)}
