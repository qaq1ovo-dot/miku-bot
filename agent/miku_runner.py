"""本地任务执行器：监听 E:\\miku\\tasks 里的 .task 文件，执行命令并写结果。

启动后常驻，不弹窗、不抢焦点。
任务文件格式（JSON）：
    {"id": "abc123", "cmd": "...", "cwd": "E:\\\\miku", "timeout": 600}
结果写到同目录 <id>.result.json，日志写到 <id>.log
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

TASKS = Path(os.environ.get("MIKU_TASKS_DIR", r"E:\miku\tasks"))
DONE = TASKS / "done"
POLL = 2.0


def main() -> None:
    TASKS.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    print(f"[runner] watching {TASKS}")
    while True:
        try:
            for f in sorted(TASKS.glob("*.task")):
                try:
                    spec = json.loads(f.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                task_id = str(spec.get("id") or f.stem)
                cmd = str(spec.get("cmd") or "")
                cwd = str(spec.get("cwd") or str(TASKS))
                timeout = int(spec.get("timeout") or 900)
                if not cmd:
                    f.unlink(missing_ok=True)
                    continue

                log_path = TASKS / f"{task_id}.log"
                res_path = TASKS / f"{task_id}.result.json"
                print(f"[runner] run {task_id}: {cmd[:80]}")
                t0 = time.time()
                try:
                    proc = subprocess.run(
                        cmd, shell=True, cwd=cwd, capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=timeout,
                    )
                    code, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
                except subprocess.TimeoutExpired:
                    code, out, err = -1, "", "timeout"
                except Exception as exc:  # noqa: BLE001
                    code, out, err = -2, "", str(exc)

                log_path.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
                res_path.write_text(json.dumps({
                    "id": task_id, "code": code,
                    "seconds": round(time.time() - t0, 1),
                    "stdout_tail": out[-4000:],
                    "stderr_tail": err[-2000:],
                }, ensure_ascii=False, indent=2), encoding="utf-8")

                f.rename(DONE / f.name)
                print(f"[runner] done {task_id} code={code}")
        except Exception as exc:  # noqa: BLE001
            print("[runner] loop error:", exc)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
