"""坐标系转换。

国内地图服务使用加密偏移后的坐标系，而 iOS 的模拟定位接口接受的是
WGS-84（GPS 原始坐标）。直接把高德/腾讯/百度上复制的坐标喂给设备，
会产生数百米的偏移，足以让打卡、围栏一类的判定失败。

支持的坐标系：

- WGS-84: GPS 原始坐标。Google Maps 国际版、GPX 文件、GNSS 接收机。
- GCJ-02: 国测局坐标，俗称"火星坐标"。高德、腾讯、Apple 地图中国区。
- BD-09:  百度坐标，在 GCJ-02 之上再做一次偏移。百度地图。

转换公式为社区通行实现，WGS-84 与 GCJ-02 之间的逆变换没有解析解，
此处用迭代法逼近，精度约 0.1 米，远优于坐标系本身的偏移量级。
"""

from __future__ import annotations

import math

__all__ = [
    "SUPPORTED_SYSTEMS",
    "bd09_to_wgs84",
    "gcj02_to_wgs84",
    "haversine_meters",
    "to_wgs84",
]

# 克拉索夫斯基椭球长半轴，GCJ-02 加偏算法沿用此参数
_A = 6378245.0
# 第一偏心率平方
_EE = 0.00669342162296594323
_PI = math.pi
# 百度坐标偏移使用的圆周率变体
_X_PI = _PI * 3000.0 / 180.0

SUPPORTED_SYSTEMS = ("wgs84", "gcj02", "bd09")


def _out_of_china(lng: float, lat: float) -> bool:
    """判断坐标是否落在中国境外。

    境外不做加偏，GCJ-02 与 WGS-84 一致。判定范围略大于国界，
    与主流实现保持一致，边境地区可能有误判，但影响仅是少量偏移。
    """
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (
        -100.0
        + 2.0 * lng
        + 3.0 * lat
        + 0.2 * lat * lat
        + 0.1 * lng * lat
        + 0.2 * math.sqrt(abs(lng))
    )
    ret += (20.0 * math.sin(6.0 * lng * _PI) + 20.0 * math.sin(2.0 * lng * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * _PI) + 40.0 * math.sin(lat / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * _PI) + 320.0 * math.sin(lat * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (
        300.0
        + lng
        + 2.0 * lat
        + 0.1 * lng * lng
        + 0.1 * lng * lat
        + 0.1 * math.sqrt(abs(lng))
    )
    ret += (20.0 * math.sin(6.0 * lng * _PI) + 20.0 * math.sin(2.0 * lng * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * _PI) + 40.0 * math.sin(lng / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * _PI) + 300.0 * math.sin(lng / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def _wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lat, lng
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * _PI
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * _PI)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lat: float, lng: float, iterations: int = 8) -> tuple[float, float]:
    """GCJ-02 转 WGS-84。

    加偏函数不可逆，用迭代逼近：先假定结果等于输入，正向加偏后与目标
    比对，将残差反馈回估计值。8 轮后误差进入亚米级别。
    """
    if _out_of_china(lng, lat):
        return lat, lng

    est_lat, est_lng = lat, lng
    for _ in range(iterations):
        fwd_lat, fwd_lng = _wgs84_to_gcj02(est_lat, est_lng)
        est_lat += lat - fwd_lat
        est_lng += lng - fwd_lng
    return est_lat, est_lng


def bd09_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """BD-09 转 GCJ-02。百度在 GCJ-02 之上叠加了一层三角函数偏移。"""
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def bd09_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """BD-09 转 WGS-84，经 GCJ-02 中转。"""
    gcj_lat, gcj_lng = bd09_to_gcj02(lat, lng)
    return gcj02_to_wgs84(gcj_lat, gcj_lng)


def to_wgs84(lat: float, lng: float, source: str) -> tuple[float, float]:
    """把任意支持的坐标系转成 WGS-84。

    Args:
        lat: 纬度。
        lng: 经度。
        source: 源坐标系，取值见 SUPPORTED_SYSTEMS。

    Raises:
        ValueError: 坐标系名称不支持。
    """
    key = source.strip().lower()
    if key == "wgs84":
        return lat, lng
    if key == "gcj02":
        return gcj02_to_wgs84(lat, lng)
    if key == "bd09":
        return bd09_to_wgs84(lat, lng)
    raise ValueError(
        f"不支持的坐标系 {source!r}，可选：{', '.join(SUPPORTED_SYSTEMS)}"
    )


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点间的大圆距离，单位米。用于展示转换前后的偏移量。"""
    r = 6371008.8  # 地球平均半径
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
