"""把长音频按静音切分成若干个 2~10 秒的片段。"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\dsh\ws\music\voices\lijianxi.wav")
OUTDIR = Path(sys.argv[2] if len(sys.argv) > 2 else r"C:\dsh\ws\music\voices\lijianxi_clips")
OUTDIR.mkdir(parents=True, exist_ok=True)

with wave.open(str(SRC), "rb") as w:
    sr = w.getframerate()
    n = w.getnframes()
    data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0

frame = int(sr * 0.03)          # 30ms 一帧
hop = frame
frames = len(data) // hop
rms = np.array([np.sqrt(np.mean(data[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(frames)])
thr = max(0.008, float(np.percentile(rms, 30)) * 1.6)
voiced = rms > thr

# 合并成语音区间
segs = []
start = None
for i, v in enumerate(voiced):
    if v and start is None:
        start = i
    elif not v and start is not None:
        segs.append((start, i))
        start = None
if start is not None:
    segs.append((start, len(voiced)))

# 合并间隔小于 0.25 秒的区间
merged = []
for s, e in segs:
    if merged and (s - merged[-1][1]) * 0.03 < 0.25:
        merged[-1] = (merged[-1][0], e)
    else:
        merged.append((s, e))

kept = 0
total = 0.0
for idx, (s, e) in enumerate(merged):
    dur = (e - s) * 0.03
    if dur < 1.2 or dur > 12:
        continue
    a = int(s * hop)
    b = int(e * hop)
    clip = data[a:b]
    peak = float(np.max(np.abs(clip))) or 1.0
    clip = (clip / peak * 0.9 * 32767).astype(np.int16)
    out = OUTDIR / f"lijianxi_{kept:03d}.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(clip.tobytes())
    kept += 1
    total += dur

print(f"切出 {kept} 段，共 {total:.1f} 秒，目录：{OUTDIR}")
sizes = sorted(p.stat().st_size for p in OUTDIR.glob("*.wav"))
if sizes:
    print("单段大小(KB) 最小/中位/最大:", sizes[0] // 1024, sizes[len(sizes) // 2] // 1024, sizes[-1] // 1024)
