"""用 faster-whisper 转写参考音频片段。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

CLIPS = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\dsh\ws\music\voices\lijianxi_clips")
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0

from faster_whisper import WhisperModel

t0 = time.time()
model = WhisperModel("small", device="cpu", compute_type="int8")
print("模型加载耗时 %.1fs" % (time.time() - t0))

files = sorted(CLIPS.glob("*.wav"))
if LIMIT:
    files = files[:LIMIT]

out_lines = []
for f in files:
    segs, info = model.transcribe(str(f), language="ja", beam_size=1)
    text = "".join(s.text for s in segs).strip()
    print("%-24s %.1fs -> %s" % (f.name, info.duration, text))
    out_lines.append(f"{f.name}\t{text}")

txt = CLIPS / "transcript.tsv"
txt.write_text("\n".join(out_lines), encoding="utf-8")
print("转写完成，共 %d 条，写入 %s，总耗时 %.1fs" % (len(out_lines), txt, time.time() - t0))
