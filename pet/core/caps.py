"""能力（Capability）注册表。

每项能力都要声明：
- risk：风险等级（low / medium / high）
- consequence：对人话解释「这么做会发生什么」
- needs_admin：是否需要管理员权限

新增能力 = 写一个函数 + 注册一条记录，不用改其他地方。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

Result = dict[str, Any]


@dataclass
class Capability:
    name: str
    title: str
    risk: str                     # low / medium / high
    consequence: str              # 给用户看的后果说明
    description: str = ""         # 一句话说明它做什么
    needs_admin: bool = False
    func: Callable[..., Result] | None = None
    args: list[str] = field(default_factory=list)

    @property
    def risk_label(self) -> str:
        return {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(self.risk, self.risk)


# ---------------------------------------------------------------- 只读信息类

def _sys_info() -> Result:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "python": platform.python_version(),
        "uptime_hours": round(time.time() - _boot_time(), 1),
    }


def _boot_time() -> float:
    if platform.system() == "Windows":
        try:
            import ctypes

            return time.time() - (ctypes.windll.kernel32.GetTickCount64() / 1000.0)
        except Exception:  # noqa: BLE001
            return time.time()
    return time.time()


def _memory() -> Result:
    if platform.system() == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                gb = 1024 ** 3
                return {
                    "total_gb": round(stat.ullTotalPhys / gb, 1),
                    "avail_gb": round(stat.ullAvailPhys / gb, 1),
                    "percent": stat.dwMemoryLoad,
                }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    return {"error": "仅支持 Windows"}


def _disk() -> Result:
    out = []
    for drive in "CDEFG":
        path = f"{drive}:\\"
        if os.path.exists(path):
            try:
                usage = shutil.disk_usage(path)
                gb = 1024 ** 3
                out.append({
                    "drive": drive,
                    "total_gb": round(usage.total / gb, 1),
                    "free_gb": round(usage.free / gb, 1),
                    "used_percent": round(usage.used / usage.total * 100, 1),
                })
            except OSError:
                continue
    return {"drives": out}


def _cpu() -> Result:
    return {
        "cores": os.cpu_count(),
        "processor": platform.processor() or platform.machine(),
    }


def _battery() -> Result:
    try:
        import ctypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return {
                "on_ac": status.ACLineStatus == 1,
                "percent": status.BatteryLifePercent,
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {"error": "读取失败"}


# ---------------------------------------------------------------- 注册

REGISTRY: dict[str, Capability] = {}


def register(cap: Capability) -> None:
    REGISTRY[cap.name] = cap


register(Capability(
    name="sys.info",
    title="系统信息",
    risk="low",
    description="读取操作系统版本、主机名、运行时长",
    consequence="只读取信息，不会修改任何东西。但主机名/系统版本属于隐私，发出去别人就知道你用的什么电脑。",
    func=_sys_info,
))

register(Capability(
    name="sys.cpu",
    title="CPU 信息",
    risk="low",
    description="读取核心数与型号",
    consequence="只读，无副作用。",
    func=_cpu,
))

register(Capability(
    name="sys.memory",
    title="内存使用",
    risk="low",
    description="读取总内存与可用内存",
    consequence="只读，无副作用。",
    func=_memory,
))

register(Capability(
    name="sys.disk",
    title="磁盘空间",
    risk="low",
    description="读取各盘符容量与剩余空间",
    consequence="只读，无副作用。",
    func=_disk,
))

register(Capability(
    name="sys.battery",
    title="电池状态",
    risk="low",
    description="读取电量与是否插电",
    consequence="只读，无副作用。",
    func=_battery,
))

# ---------------------------------------------------------------- 高风险示例（默认禁用）

def _run(cmd: list[str]) -> Result:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, errors="replace")
        return {"code": out.returncode, "stdout": out.stdout[-2000:], "stderr": out.stderr[-500:]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


register(Capability(
    name="sys.processes",
    title="进程列表",
    risk="medium",
    description="列出正在运行的程序名",
    consequence="能看出你开了什么软件（游戏、聊天、工作文件）。只读，但属于隐私。",
    func=lambda: _run(["tasklist"]),
))

register(Capability(
    name="device.notify",
    title="弹系统通知",
    risk="medium",
    description="在右下角弹一条通知",
    consequence="会打断你当前的注意力，但不会改动任何数据。",
    func=lambda msg="": {"skipped": True, "reason": "通知功能待接入"},
))


def list_capabilities() -> list[dict]:
    """返回给前端/API 用的能力清单。"""
    return [
        {
            "name": c.name,
            "title": c.title,
            "risk": c.risk,
            "risk_label": c.risk_label,
            "description": c.description,
            "consequence": c.consequence,
            "needs_admin": c.needs_admin,
        }
        for c in REGISTRY.values()
    ]


def execute(name: str, **kwargs: Any) -> Result:
    """执行一项能力。

    Args:
        name: 能力名。
        **kwargs: 传给能力的参数。

    Returns:
        能力执行结果；未知能力或异常时返回 error。
    """
    cap = REGISTRY.get(name)
    if cap is None:
        return {"error": f"未知能力：{name}"}
    if cap.func is None:
        return {"error": f"能力 {name} 未实现"}
    try:
        return cap.func(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
