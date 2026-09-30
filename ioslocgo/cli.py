"""命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from pathlib import Path

from . import __version__
from .coords import SUPPORTED_SYSTEMS, haversine_meters, to_wgs84
from .location import clear_location, hold_location, play_gpx
from .preflight import Status, run_all_checks
from .tunnel import RsdAddress, TunnelError, elevate_hint, parse_tunnel_output

_MARKS = {Status.OK: "[  OK  ]", Status.FAIL: "[ 失败 ]", Status.SKIP: "[ 跳过 ]"}


def _print_checks(results: list) -> bool:
    """打印自检结果，返回是否全部通过。"""
    print("环境自检")
    print("-" * 60)
    for item in results:
        print(f"{_MARKS[item.status]} {item.name}：{item.detail}")
        if item.hint:
            for line in item.hint.splitlines():
                print(f"         {line}")
    print("-" * 60)
    return all(r.ok for r in results)


def _resolve_rsd(args: argparse.Namespace) -> RsdAddress | None:
    """解析 --rsd 参数。

    返回 None 表示走默认的用户态隧道：在本进程内建立，无需管理员权限，
    用户也不必手动复制 RSD 地址。
    """
    if not args.rsd:
        return None

    # 同时接受 "地址 端口" 与 "地址,端口" 两种写法
    raw = args.rsd.replace(",", " ").split()
    if len(raw) == 2:
        return RsdAddress(raw[0], int(raw[1]))
    parsed = parse_tunnel_output(args.rsd)
    if parsed:
        return parsed
    raise SystemExit(
        f"无法解析 --rsd {args.rsd!r}。\n"
        '正确写法："地址 端口"，例如：--rsd "fd13:d8fb:3cd1::1 61051"\n'
        "提示：通常无需该参数，省略即在本进程内建立免提权隧道。"
    )


def _convert(args: argparse.Namespace) -> tuple[float, float]:
    """按来源坐标系转换成 WGS-84，并打印偏移量。"""
    lat, lng = to_wgs84(args.latitude, args.longitude, args.source)
    if args.source.lower() != "wgs84":
        offset = haversine_meters(args.latitude, args.longitude, lat, lng)
        print(
            f"坐标系转换：{args.source.upper()} "
            f"{args.latitude:.6f}, {args.longitude:.6f}"
            f"  ->  WGS-84 {lat:.6f}, {lng:.6f}（偏移 {offset:.0f} 米）"
        )
    else:
        print(f"坐标（WGS-84）：{lat:.6f}, {lng:.6f}")
    return lat, lng


async def _run_until_interrupt(coro_factory, label: str) -> int:
    """运行保持型任务，等待 Ctrl+C 退出。"""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop.set()

    # Windows 的 ProactorEventLoop 不支持 add_signal_handler，
    # 该平台依赖 KeyboardInterrupt 兜底
    with contextlib.suppress(NotImplementedError, AttributeError):
        loop.add_signal_handler(signal.SIGINT, request_stop)

    task = asyncio.create_task(coro_factory(stop))
    print(f"\n{label}")
    print("按 Ctrl+C 停止。保持本进程运行，退出后设备将恢复真实定位。")
    try:
        await task
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        stop.set()
    except TunnelError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1
    print("\n已停止，设备恢复真实定位。")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    if not _print_checks(run_all_checks()):
        print("请按上方指引修复后重试。")
        return 1

    print("全部通过，可直接下发坐标：")
    print("  ioslocgo set 30.36165 119.973495 --source gcj02")
    print("\n隧道会在进程内自动建立，无需管理员权限。")
    print("若设备系统低于 iOS 17.4，用户态隧道不可用，需手动建立提权隧道：\n")
    print(elevate_hint())
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    if not args.skip_check and not _print_checks(run_all_checks()):
        print("自检未通过。确认无误可加 --skip-check 跳过。")
        return 1
    rsd = _resolve_rsd(args)
    lat, lng = _convert(args)

    return asyncio.run(
        _run_until_interrupt(
            lambda stop: hold_location(rsd, lat, lng, stop),
            f"已下发模拟定位：{lat:.6f}, {lng:.6f}",
        )
    )


def cmd_play(args: argparse.Namespace) -> int:
    rsd = _resolve_rsd(args)
    path = Path(args.gpx)
    return asyncio.run(
        _run_until_interrupt(
            lambda stop: play_gpx(
                rsd, path, args.randomness, args.disable_sleep, stop
            ),
            f"开始播放轨迹：{path.name}",
        )
    )


def cmd_clear(args: argparse.Namespace) -> int:
    rsd = _resolve_rsd(args)
    try:
        asyncio.run(clear_location(rsd))
    except TunnelError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print("已清除模拟定位，设备恢复真实定位。")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    _convert(args)
    return 0


def cmd_enable_devmode(args: argparse.Namespace) -> int:
    """触发开发者模式菜单显示，并尝试启用。"""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.amfi import AmfiService

    async def run() -> int:
        try:
            lockdown = await create_using_usbmux()
            service = AmfiService(lockdown)
            await service.reveal_developer_mode_option_in_ui()
            print("已请求在设置中显示开发者模式选项。")
            await service.enable_developer_mode()
            print("已请求启用开发者模式，设备将重启，重启后请再确认一次。")
            return 0
        except Exception as exc:
            text = str(exc)
            print(f"启用失败：{text}", file=sys.stderr)
            if "passcode" in text.lower():
                print(
                    "\n设备设置了锁屏密码时无法由主机端启用。请选择其一：\n"
                    "  1. 手机上手动开启：设置 → 隐私与安全性 → 开发者模式\n"
                    "     （上面的命令已尝试让该菜单项显示出来）\n"
                    "  2. 临时关闭锁屏密码后重试本命令，成功后再设回密码。\n"
                    "     注意：关闭密码会移除 Apple Pay 中的银行卡，需重新添加。",
                    file=sys.stderr,
                )
            return 1

    return asyncio.run(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ioslocgo",
        description="iOS 模拟定位工具，面向国内坐标系与 Windows 环境。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "典型流程：\n"
            "  1. ioslocgo doctor                             检查环境\n"
            "  2. ioslocgo set 30.36165 119.973495 --source gcj02\n"
            "\n隧道在进程内自动建立，无需管理员权限（要求 iOS 17.4 以上）。\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"ioslocgo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_rsd(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--rsd",
            metavar='"地址 端口"',
            help="连接到已有的提权隧道。通常无需指定，默认在进程内建立免提权隧道；"
            "设备系统低于 iOS 17.4 时才需要此参数",
        )

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("set", help="下发并保持模拟定位")
    p.add_argument("latitude", type=float, help="纬度")
    p.add_argument("longitude", type=float, help="经度")
    p.add_argument(
        "--source",
        default="gcj02",
        choices=SUPPORTED_SYSTEMS,
        help="输入坐标所属坐标系，默认 gcj02（高德、腾讯、Apple 地图中国区）",
    )
    p.add_argument("--skip-check", action="store_true", help="跳过环境自检")
    add_rsd(p)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("play", help="按 GPX 轨迹移动")
    p.add_argument("gpx", help="GPX 文件路径")
    p.add_argument("--randomness", type=int, default=0, help="时间抖动范围（秒）")
    p.add_argument("--disable-sleep", action="store_true", help="忽略轨迹点间隔")
    add_rsd(p)
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("clear", help="清除模拟定位")
    add_rsd(p)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("convert", help="仅做坐标系转换，不连接设备")
    p.add_argument("latitude", type=float)
    p.add_argument("longitude", type=float)
    p.add_argument("--source", default="gcj02", choices=SUPPORTED_SYSTEMS)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("enable-devmode", help="尝试启用开发者模式")
    p.set_defaults(func=cmd_enable_devmode)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except TunnelError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
