"""模拟定位的下发与保持。

DVT 的 LocationSimulation 通道是有状态的：坐标随通道存在而生效，通道关闭
后设备恢复真实定位。因此下发坐标的进程必须持续运行，不能下发完就退出。
这是使用中最容易踩的一点，pymobiledevice3 自带的 CLI 用一句
``wait_return()`` 阻塞在前台来维持，本模块把同样的语义包装成可编程接口。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from .tunnel import RsdAddress, TunnelError

__all__ = ["hold_location", "play_gpx", "clear_location", "set_location_once"]


@contextlib.asynccontextmanager
async def _rsd_session(rsd: RsdAddress) -> AsyncIterator[object]:
    """连接到隧道后的 RemoteServiceDiscovery 端点。"""
    try:
        from pymobiledevice3.remote.remote_service_discovery import (
            RemoteServiceDiscoveryService,
        )
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise TunnelError(f"pymobiledevice3 导入失败：{exc}") from exc

    service = RemoteServiceDiscoveryService((rsd.address, rsd.port))
    try:
        await service.connect()
    except Exception as exc:
        raise TunnelError(
            f"无法连接隧道 {rsd}。隧道可能已断开，请重新建立并使用新的地址与端口。\n"
            f"原始错误：{exc}"
        ) from exc

    try:
        yield service
    finally:
        with contextlib.suppress(Exception):
            await service.close()


@contextlib.asynccontextmanager
async def _location_service(rsd: RsdAddress) -> AsyncIterator[object]:
    """打开 LocationSimulation 通道。退出时通道关闭，模拟定位随之失效。"""
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.location_simulation import (
        LocationSimulation,
    )

    async with _rsd_session(rsd) as service:
        try:
            async with DvtProvider(service) as dvt, LocationSimulation(dvt) as sim:
                yield sim
        except TunnelError:
            raise
        except Exception as exc:
            raise TunnelError(
                f"打开 DVT 通道失败：{exc}\n"
                "常见原因：开发者模式未开启、设备已锁屏、隧道与当前设备不匹配。"
            ) from exc


async def hold_location(
    rsd: RsdAddress,
    latitude: float,
    longitude: float,
    stop: asyncio.Event | None = None,
) -> None:
    """下发坐标并保持生效，直到 stop 被置位或协程被取消。

    Args:
        rsd: 隧道连接参数。
        latitude: 纬度，必须已是 WGS-84。
        longitude: 经度，必须已是 WGS-84。
        stop: 外部停止信号。为 None 时一直保持，由取消操作结束。

    Raises:
        TunnelError: 隧道不可用或 DVT 通道打开失败。
    """
    async with _location_service(rsd) as sim:
        await sim.set(latitude, longitude)
        waiter = stop or asyncio.Event()
        # 通道必须保持打开，坐标才持续生效
        await waiter.wait()


async def set_location_once(
    rsd: RsdAddress, latitude: float, longitude: float
) -> None:
    """下发坐标后立即关闭通道。

    仅用于连通性验证。由于通道关闭，定位不会持续生效，正常使用请用
    hold_location。
    """
    async with _location_service(rsd) as sim:
        await sim.set(latitude, longitude)


async def clear_location(rsd: RsdAddress) -> None:
    """清除模拟定位，让设备恢复真实位置。"""
    async with _location_service(rsd) as sim:
        await sim.clear()


async def play_gpx(
    rsd: RsdAddress,
    path: Path,
    randomness: int = 0,
    disable_sleep: bool = False,
    stop: asyncio.Event | None = None,
) -> None:
    """按 GPX 轨迹连续移动，用于模拟行进路线。

    Args:
        rsd: 隧道连接参数。
        path: GPX 文件路径。其中坐标应为 WGS-84，GPX 标准本身即要求如此。
        randomness: 时间抖动范围（秒），0 表示严格按轨迹时间。
        disable_sleep: 为真时忽略轨迹点之间的间隔，尽快播完。
        stop: 外部停止信号。
    """
    if not path.is_file():
        raise TunnelError(f"GPX 文件不存在：{path}")

    async with _location_service(rsd) as sim:
        await sim.play_gpx_file(
            str(path),
            disable_sleep=disable_sleep,
            timing_randomness_range=randomness,
        )
        waiter = stop or asyncio.Event()
        await waiter.wait()
