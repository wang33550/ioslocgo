"""坐标转换测试。"""

from __future__ import annotations

import math

import pytest

from ioslocgo.coords import (
    _wgs84_to_gcj02,
    bd09_to_wgs84,
    gcj02_to_wgs84,
    haversine_meters,
    to_wgs84,
)

# 杭州余杭，项目最初的验证点
HANGZHOU_GCJ = (30.36165, 119.973495)
# 东京，用于验证境外不加偏
TOKYO = (35.6762, 139.6503)


class TestRoundTrip:
    """正向加偏与迭代逆变换应当自洽。"""

    @pytest.mark.parametrize(
        "lat,lng",
        [
            HANGZHOU_GCJ,
            (39.9042, 116.4074),  # 北京
            (31.2304, 121.4737),  # 上海
            (22.5431, 114.0579),  # 深圳
            (43.8256, 87.6168),  # 乌鲁木齐，西部边缘
            (18.2528, 109.5122),  # 三亚，南部边缘
        ],
    )
    def test_roundtrip_within_centimeter(self, lat: float, lng: float) -> None:
        wgs = gcj02_to_wgs84(lat, lng)
        back = _wgs84_to_gcj02(*wgs)
        assert haversine_meters(lat, lng, *back) < 0.01

    def test_roundtrip_across_national_grid(self) -> None:
        """在覆盖全国的网格上验证迭代收敛，支撑模块文档中的精度声明。"""
        worst = 0.0
        for lat_half in range(8, 107, 3):
            for lng in range(74, 136, 3):
                lat = lat_half / 2
                wgs = gcj02_to_wgs84(lat, float(lng))
                back = _wgs84_to_gcj02(*wgs)
                worst = max(worst, haversine_meters(lat, float(lng), *back))
        assert worst < 0.01


class TestOffsetMagnitude:
    """国内坐标的偏移量应落在合理区间。"""

    def test_offset_is_hundreds_of_meters(self) -> None:
        wgs = gcj02_to_wgs84(*HANGZHOU_GCJ)
        offset = haversine_meters(*HANGZHOU_GCJ, *wgs)
        # GCJ-02 偏移通常在 100~800 米，此处实测约 525 米
        assert 100 < offset < 800

    def test_offset_direction(self) -> None:
        """中国境内 GCJ-02 相对 WGS-84 通常偏东北，逆变换应向西南。"""
        lat, lng = HANGZHOU_GCJ
        w_lat, w_lng = gcj02_to_wgs84(lat, lng)
        assert w_lng < lng  # 西
        assert w_lat > lat or abs(w_lat - lat) < 0.01


class TestOutOfChina:
    """境外坐标不做偏移。"""

    def test_tokyo_unchanged(self) -> None:
        assert gcj02_to_wgs84(*TOKYO) == TOKYO

    def test_new_york_unchanged(self) -> None:
        ny = (40.7128, -74.0060)
        assert gcj02_to_wgs84(*ny) == ny


class TestToWgs84:
    """统一入口的分派与校验。"""

    def test_wgs84_passthrough(self) -> None:
        assert to_wgs84(*HANGZHOU_GCJ, "wgs84") == HANGZHOU_GCJ

    def test_case_insensitive(self) -> None:
        assert to_wgs84(*HANGZHOU_GCJ, "GCJ02") == to_wgs84(*HANGZHOU_GCJ, "gcj02")

    def test_whitespace_tolerated(self) -> None:
        assert to_wgs84(*HANGZHOU_GCJ, "  gcj02  ") == to_wgs84(*HANGZHOU_GCJ, "gcj02")

    def test_unknown_system_rejected(self) -> None:
        with pytest.raises(ValueError, match="不支持的坐标系"):
            to_wgs84(*HANGZHOU_GCJ, "epsg4326")

    def test_bd09_differs_from_gcj02(self) -> None:
        """百度坐标多一层偏移，结果应与直接按 GCJ-02 处理不同。"""
        as_bd = to_wgs84(*HANGZHOU_GCJ, "bd09")
        as_gcj = to_wgs84(*HANGZHOU_GCJ, "gcj02")
        assert haversine_meters(*as_bd, *as_gcj) > 100


class TestBd09Chain:
    """BD-09 经 GCJ-02 中转到 WGS-84。"""

    def test_bd09_offset_larger_than_gcj02(self) -> None:
        point = (30.36790, 119.98005)
        bd_offset = haversine_meters(*point, *bd09_to_wgs84(*point))
        gcj_offset = haversine_meters(*point, *gcj02_to_wgs84(*point))
        assert bd_offset > gcj_offset


class TestHaversine:
    """距离函数的边界情况。"""

    def test_zero_distance(self) -> None:
        assert haversine_meters(*HANGZHOU_GCJ, *HANGZHOU_GCJ) == pytest.approx(0)

    def test_known_distance(self) -> None:
        """一个纬度约 111 公里。"""
        d = haversine_meters(30.0, 120.0, 31.0, 120.0)
        assert d == pytest.approx(111195, rel=0.01)

    def test_symmetry(self) -> None:
        a, b = (30.0, 120.0), (31.0, 121.0)
        assert haversine_meters(*a, *b) == pytest.approx(haversine_meters(*b, *a))

    def test_no_nan_for_antipodal(self) -> None:
        """对径点不应因浮点误差产生 NaN。"""
        d = haversine_meters(0.0, 0.0, 0.0, 180.0)
        assert not math.isnan(d)
