"""RSD 隧道管理。

iOS 17 起，开发者服务（含模拟定位）不再直接挂在 lockdown 上，而是位于
一条 RemoteXPC 隧道之后。该隧道需要创建虚拟网络接口，在 Windows 上必须
由管理员权限的进程建立，因此无法在普通权限的主程序内直接开启。

本模块负责：判断当前是否具备管理员权限；在有权限时于本进程内开启隧道；
否则生成一条可供用户在管理员终端里执行的命令。
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from dataclasses import dataclass

__all__ = ["RsdAddress", "TunnelError", "is_admin", "elevate_hint", "parse_tunnel_output"]

# start-tunnel 输出中 RSD 地址与端口所在行
_RSD_RE = re.compile(
    r"RSD\s+Address:\s*(?P<addr>[0-9a-fA-F:.%]+).*?RSD\s+Port:\s*(?P<port>\d+)",
    re.DOTALL,
)


class TunnelError(RuntimeError):
    """隧道相关错误。"""


@dataclass(frozen=True)
class RsdAddress:
    """一条已建立隧道的连接参数。"""

    address: str
    port: int

    @property
    def cli_args(self) -> list[str]:
        """转成 pymobiledevice3 命令行所需的 --rsd 参数。"""
        return ["--rsd", self.address, str(self.port)]

    def __str__(self) -> str:
        return f"{self.address} {self.port}"


def is_admin() -> bool:
    """当前进程是否具备管理员 / root 权限。"""
    if sys.platform != "win32":
        import os

        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def parse_tunnel_output(text: str) -> RsdAddress | None:
    """从 start-tunnel 的输出里提取 RSD 地址与端口。

    供用户把隧道输出粘贴回程序时使用，也用于解析子进程输出。
    """
    match = _RSD_RE.search(text)
    if not match:
        return None
    return RsdAddress(match.group("addr"), int(match.group("port")))


def tunnel_command() -> list[str]:
    """返回启动隧道的命令行。"""
    return [sys.executable, "-m", "pymobiledevice3", "lockdown", "start-tunnel"]


#: 所有平台的指引都必须包含这段说明，隧道断开会静默失效，是最易踩的点
_TUNNEL_CAVEAT = (
    "窗口中出现 RSD Address 与 RSD Port 两行后，把它们通过 --rsd 传给本工具。\n"
    "隧道一旦断开，模拟定位立即失效，设备会恢复真实位置。"
)


def elevate_hint() -> str:
    """生成提权启动隧道的操作指引。"""
    cmd = " ".join(tunnel_command())
    if sys.platform != "win32":
        return (
            "建立隧道需要 root 权限。请在另一个终端中执行，并保持窗口不要关闭：\n\n"
            f"    sudo {cmd}\n\n" + _TUNNEL_CAVEAT
        )
    ps = (
        "Start-Process powershell -Verb RunAs -ArgumentList "
        f"'-NoExit','-Command','{cmd}'"
    )
    return (
        "建立隧道需要管理员权限。请任选一种方式，并保持该窗口不要关闭：\n\n"
        "  方式一，在当前 PowerShell 里执行（会弹出 UAC 授权框）：\n"
        f"    {ps}\n\n"
        "  方式二，手动操作：开始菜单搜索 PowerShell，右键「以管理员身份运行」，然后执行：\n"
        f"    {cmd}\n\n" + _TUNNEL_CAVEAT
    )


def probe(rsd: RsdAddress, timeout: int = 30) -> bool:
    """探测隧道是否可用。

    通过一次轻量的 device-information 调用验证 RSD 端点可达。
    """
    cmd = [
        sys.executable,
        "-m",
        "pymobiledevice3",
        "developer",
        "dvt",
        "device-information",
        *rsd.cli_args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0
