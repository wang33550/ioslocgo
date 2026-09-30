"""RSD 隧道管理。

iOS 17 起，开发者服务（含模拟定位）不再直接挂在 lockdown 上，而是位于
一条 RemoteXPC 隧道之后。建立该隧道有两条路径：

- **用户态隧道**（``userspace_rsd``，默认）。用纯 Python 的 PyTCP 协议栈
  在本进程内建立，不创建系统级虚拟网卡，因此无需管理员权限。要求设备
  系统 iOS 17.4 以上。
- **提权隧道**。由 ``pymobiledevice3 lockdown start-tunnel`` 创建系统级
  虚拟网络接口，需要管理员 / root 权限，并在独立终端中常驻。iOS 17.0
  至 17.3 缺少 CoreDeviceProxy，只能走这条路。

因此本模块既提供前者的上下文管理器，也提供后者的操作指引
（``elevate_hint``）与输出解析（``parse_tunnel_output``）。
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass

__all__ = [
    "RsdAddress",
    "TunnelError",
    "elevate_hint",
    "parse_tunnel_output",
    "userspace_rsd",
]

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
        """转成 pymobiledevice3 命令行所需的 --rsd 参数。

        本项目直接调用 Python API，不经由子进程。此属性供调用方在需要
        拼接 pymobiledevice3 命令行时使用。
        """
        return ["--rsd", self.address, str(self.port)]

    def __str__(self) -> str:
        return f"{self.address} {self.port}"


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


@contextlib.asynccontextmanager
async def userspace_rsd(serial: str | None = None) -> AsyncIterator[object]:
    """在本进程内建立用户态隧道，返回已连接的 RSD 端点。

    使用纯 Python 的 PyTCP 协议栈，不创建系统级虚拟网卡，因此**无需管理员
    权限**。这是首选路径：用户不必手动开提权终端，也不必复制粘贴 RSD 地址。

    代价是主机到设备方向的传输较慢（发送分段被刻意压小以保证可靠性），
    但模拟定位只下发几十字节的坐标，完全不受影响。

    要求 iOS 17.4 以上（需要 CoreDeviceProxy）。更早的版本请退回到手动
    建立提权隧道，再通过 --rsd 传入。
    """
    try:
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    except ImportError as exc:
        raise TunnelError(
            f"无法导入用户态隧道模块：{exc}\n"
            "请确认 pymobiledevice3 版本不低于 11.19，且已安装 pmd-pytcp。"
        ) from exc

    tunnel = UserspaceRsdTunnel(serial=serial)
    try:
        rsd = await tunnel.aopen()
    except Exception as exc:
        raise TunnelError(
            f"建立用户态隧道失败：{exc}\n\n"
            "可能原因与对策：\n"
            "  - 设备系统低于 iOS 17.4：改用手动提权隧道，见 ioslocgo doctor 的提示。\n"
            "  - 设备已锁屏或未信任本机：解锁并重新插拔数据线。\n"
            "  - 开发者模式未开启：执行 ioslocgo doctor 查看。"
        ) from exc

    try:
        yield rsd
    finally:
        with contextlib.suppress(Exception):
            await tunnel.aclose()


