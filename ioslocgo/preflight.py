"""环境自检。

模拟定位依赖一条较长的链路：Apple 驱动服务 -> usbmuxd -> 设备配对 ->
开发者模式 -> RSD 隧道。任何一环缺失，底层库抛出的都是原始异常栈，
排查成本很高。本模块把前四环各变成一次显式检查，并给出中文修复指引。

隧道不在此处检查：它由 :mod:`ioslocgo.tunnel` 在实际下发坐标时按需建立，
失败时自带针对性的错误说明。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum

__all__ = ["CheckResult", "Status", "run_all_checks", "APPLE_SERVICE_NAME"]

APPLE_SERVICE_NAME = "Apple Mobile Device Service"

# 子进程统一超时，避免 usbmuxd 无响应时卡死
_TIMEOUT = 30


class Status(Enum):
    """单项检查的结论。"""

    OK = "ok"
    FAIL = "fail"
    # 无法判定，且不阻断主流程（例如非 Windows 平台跳过服务检查）
    SKIP = "skip"


@dataclass
class CheckResult:
    """一项检查的结果。

    Attributes:
        name: 检查项名称。
        status: 结论。
        detail: 实际观测到的情况。
        hint: 失败时的修复指引，成功则为空。
    """

    name: str
    status: Status
    detail: str
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not Status.FAIL


def _run(cmd: list[str]) -> tuple[int, str]:
    """执行子进程，返回 (退出码, 合并输出)。异常一律转成非零退出码。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, f"命令不存在: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{_TIMEOUT}s）: {' '.join(cmd)}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_apple_service() -> CheckResult:
    """检查 Apple Mobile Device Service 是否在运行。

    该服务提供 usbmuxd，是 USB 通信的前提。它随 iTunes 或 Apple Devices
    安装，默认为手动启动，重装驱动或重启后常处于停止状态。
    """
    name = "Apple 驱动服务"
    if sys.platform != "win32":
        return CheckResult(name, Status.SKIP, "非 Windows 平台，跳过")

    if shutil.which("sc") is None:
        return CheckResult(name, Status.SKIP, "找不到 sc 命令，无法查询")

    code, out = _run(["sc", "query", APPLE_SERVICE_NAME])
    if code != 0 or "1060" in out:
        return CheckResult(
            name,
            Status.FAIL,
            "服务未安装",
            "安装 Apple Devices（Microsoft Store）或 iTunes，它们会一并装上 USB 驱动。",
        )
    if "RUNNING" in out:
        return CheckResult(name, Status.OK, "运行中")
    return CheckResult(
        name,
        Status.FAIL,
        "已安装但未运行",
        "以管理员身份执行：net start \"Apple Mobile Device Service\"\n"
        "  或按 Win+R 输入 services.msc，找到该服务后右键启动。",
    )


async def check_device() -> tuple[CheckResult, dict | None]:
    """列举 USB 上的 iOS 设备，返回检查结果与第一台设备的信息。"""
    name = "设备连接"
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices
    except ImportError:
        return (
            CheckResult(
                name,
                Status.FAIL,
                "pymobiledevice3 未安装",
                "执行：pip install -U pymobiledevice3",
            ),
            None,
        )

    try:
        devices = await list_devices()
    except Exception as exc:  # usbmuxd 不可用时异常类型不稳定
        return (
            CheckResult(
                name,
                Status.FAIL,
                f"无法连接 usbmuxd：{exc}",
                "确认 Apple 驱动服务已启动，然后重新插拔数据线。",
            ),
            None,
        )

    if not devices:
        return (
            CheckResult(
                name,
                Status.FAIL,
                "未检测到设备",
                "重新插拔数据线，解锁手机，在弹出的「信任此电脑」上点信任并输入密码。\n"
                "  若服务是在插线之后才启动的，务必重新插拔一次让其重新枚举。",
            ),
            None,
        )

    dev = devices[0]
    try:
        lockdown = await create_using_usbmux(serial=dev.serial)
        short = lockdown.short_info
        info = {
            "udid": dev.serial,
            "name": short.get("DeviceName", "?"),
            "product": short.get("ProductType", "?"),
            "version": short.get("ProductVersion", "?"),
        }
    except Exception as exc:
        return (
            CheckResult(
                name,
                Status.FAIL,
                f"设备可见但握手失败：{exc}",
                "手机需处于解锁状态，并已信任本机。可尝试重新插拔。",
            ),
            None,
        )

    extra = f"，共 {len(devices)} 台，使用第一台" if len(devices) > 1 else ""
    return (
        CheckResult(
            name,
            Status.OK,
            f"{info['name']} / {info['product']} / iOS {info['version']}{extra}",
        ),
        info,
    )


async def check_developer_mode(version: str | None = None) -> CheckResult:
    """检查开发者模式开关。

    iOS 16 起，调试类服务必须在开发者模式开启后才可访问。此开关只能在
    设备本地开启，主机端只能触发菜单显示，无法强制打开。

    Args:
        version: 已知的系统版本号，省去一次握手。为 None 时自行读取。
    """
    name = "开发者模式"
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
    except ImportError:
        return CheckResult(name, Status.SKIP, "pymobiledevice3 未安装")

    try:
        lockdown = await create_using_usbmux()
        ver = version or str(lockdown.product_version)
        major = int(ver.split(".")[0])
    except Exception as exc:
        return CheckResult(name, Status.FAIL, f"无法读取设备信息：{exc}")

    if major < 16:
        return CheckResult(name, Status.SKIP, f"iOS {ver} 无此开关")

    try:
        enabled = await lockdown.get_developer_mode_status()
    except Exception as exc:
        return CheckResult(
            name,
            Status.FAIL,
            f"查询失败：{exc}",
            "确认设备已解锁且已信任本机。",
        )

    if enabled:
        return CheckResult(name, Status.OK, "已开启")
    return CheckResult(
        name,
        Status.FAIL,
        "未开启",
        "手机上：设置 → 隐私与安全性 → 拉到底部 → 开发者模式 → 打开 → 重启 → 解锁后再确认一次。\n"
        "  若菜单里没有该项，先执行 ioslocgo enable-devmode 触发其显示。\n"
        "  注意：设备设有锁屏密码时无法由主机端启用，需临时关闭密码（会移除 Apple Pay 卡片）。",
    )


async def run_all_checks_async() -> list[CheckResult]:
    """按依赖顺序执行全部检查。

    前序项失败时跳过后续项，避免把同一个根因报成多条错误。
    """
    skipped = "前置检查未通过，已跳过"
    results: list[CheckResult] = []

    service = check_apple_service()
    results.append(service)
    if not service.ok:
        results.append(CheckResult("设备连接", Status.SKIP, skipped))
        results.append(CheckResult("开发者模式", Status.SKIP, skipped))
        return results

    device, info = await check_device()
    results.append(device)
    if not device.ok:
        results.append(CheckResult("开发者模式", Status.SKIP, skipped))
        return results

    results.append(await check_developer_mode(info["version"] if info else None))
    return results


def run_all_checks() -> list[CheckResult]:
    """run_all_checks_async 的同步封装。"""
    return asyncio.run(run_all_checks_async())
