"""隧道参数解析测试。"""

from __future__ import annotations

from ioslocgo.tunnel import RsdAddress, elevate_hint, parse_tunnel_output

# 一次真实的 start-tunnel 输出，取自 iPhone 15 / iOS 27.0
REAL_OUTPUT = """\
2026-09-30 00:38:03 HOST pymobiledevice3.cli.remote[25640] INFO tunnel created
Identifier: 00001111-000A1111A1A11A1E
Interface: pymobiledevice3-tunnel-00001111-000A1111A1A11A1E
Protocol: tcp
RSD Address: fd13:d8fb:3cd1::1
RSD Port: 61051
Use the follow connection option:
--rsd fd13:d8fb:3cd1::1 61051
"""


class TestParseTunnelOutput:
    def test_parses_real_output(self) -> None:
        rsd = parse_tunnel_output(REAL_OUTPUT)
        assert rsd == RsdAddress("fd13:d8fb:3cd1::1", 61051)

    def test_parses_minimal(self) -> None:
        rsd = parse_tunnel_output("RSD Address: fd00::1\nRSD Port: 1234")
        assert rsd == RsdAddress("fd00::1", 1234)

    def test_parses_ipv4(self) -> None:
        rsd = parse_tunnel_output("RSD Address: 10.0.0.2\nRSD Port: 5555")
        assert rsd == RsdAddress("10.0.0.2", 5555)

    def test_returns_none_on_garbage(self) -> None:
        assert parse_tunnel_output("nothing useful here") is None

    def test_returns_none_when_port_missing(self) -> None:
        assert parse_tunnel_output("RSD Address: fd00::1") is None


class TestRsdAddress:
    def test_cli_args_shape(self) -> None:
        rsd = RsdAddress("fd00::1", 61051)
        assert rsd.cli_args == ["--rsd", "fd00::1", "61051"]

    def test_str_is_space_separated(self) -> None:
        assert str(RsdAddress("fd00::1", 61051)) == "fd00::1 61051"

    def test_is_hashable(self) -> None:
        """frozen dataclass 应可用作字典键。"""
        assert len({RsdAddress("fd00::1", 1), RsdAddress("fd00::1", 1)}) == 1


class TestElevateHint:
    def test_mentions_admin_requirement(self) -> None:
        text = elevate_hint()
        assert "start-tunnel" in text
        # 必须说明隧道断开的后果，这是最容易被忽略的点
        assert "断开" in text
