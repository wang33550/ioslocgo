"""命令行接口测试。

只覆盖不依赖真实设备的路径：参数解析、坐标转换输出、错误提示。
需要设备的命令由集成测试手动验证。
"""

from __future__ import annotations

import pytest

from ioslocgo.cli import build_parser, main


class TestParser:
    def test_convert_defaults_to_gcj02(self) -> None:
        args = build_parser().parse_args(["convert", "30.0", "120.0"])
        assert args.source == "gcj02"

    def test_rejects_unknown_source(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["convert", "30.0", "120.0", "--source", "xyz"])

    def test_requires_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_set_parses_coordinates(self) -> None:
        args = build_parser().parse_args(
            ["set", "30.36165", "119.973495", "--rsd", "fd00::1 61051"]
        )
        assert args.latitude == pytest.approx(30.36165)
        assert args.longitude == pytest.approx(119.973495)

    def test_negative_longitude(self) -> None:
        """西半球坐标为负，不应被误判为选项。"""
        args = build_parser().parse_args(["convert", "40.69", "--", "-74.04"])
        assert args.longitude == pytest.approx(-74.04)


class TestConvertCommand:
    def test_prints_offset_for_gcj02(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["convert", "30.36165", "119.973495", "--source", "gcj02"]) == 0
        out = capsys.readouterr().out
        assert "WGS-84" in out
        assert "偏移" in out

    def test_no_offset_line_for_wgs84(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["convert", "30.0", "120.0", "--source", "wgs84"]) == 0
        assert "偏移" not in capsys.readouterr().out


class TestRsdParsing:
    """--rsd 接受多种粘贴格式。"""

    @pytest.mark.parametrize(
        "value",
        [
            "fd13:d8fb:3cd1::1 61051",
            "fd13:d8fb:3cd1::1,61051",
            "RSD Address: fd13:d8fb:3cd1::1\nRSD Port: 61051",
        ],
    )
    def test_accepted_formats(self, value: str, capsys: pytest.CaptureFixture) -> None:
        """格式可解析时不应报参数错误。

        clear 会因无真实隧道而失败，但错误信息应指向连接而非解析。
        """
        code = main(["clear", "--rsd", value])
        assert code == 1
        assert "无法解析" not in capsys.readouterr().err

    def test_garbage_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["clear", "--rsd", "garbage"])
        assert "无法解析" in str(exc.value)

    def test_missing_rsd_is_allowed(self) -> None:
        """省略 --rsd 表示走进程内的免提权隧道，不应报参数错误。"""
        args = build_parser().parse_args(["clear"])
        assert args.rsd is None

    def test_garbage_mentions_optional(self) -> None:
        """解析失败时应提示该参数通常可省略。"""
        with pytest.raises(SystemExit) as exc:
            main(["clear", "--rsd", "garbage"])
        assert "省略" in str(exc.value)
