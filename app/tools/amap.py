"""高德地图 Web API 工具：POI 查询与驾车路径规划。

坐标体系统一使用高德返回的 GCJ-02，不混用其他坐标系。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

import httpx
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_fixed

from app.schemas import POIResult, RouteResult

_REST = "https://restapi.amap.com"


class AMapError(Exception):
    """高德 API 调用失败。"""


class POISearchInput(BaseModel):
    name: str
    city: str = "石家庄"


class DrivingRouteInput(BaseModel):
    origin: str
    destination: str
    origin_lng: float
    origin_lat: float
    dest_lng: float
    dest_lat: float


# 石家庄市区/各区县确定性基准坐标（仅用于 Mock 演示，非真实偏差点）
_DISTRICT_COORD = {
    "裕华区": (114.53, 38.01),
    "长安区": (114.55, 38.04),
    "桥西区": (114.47, 38.03),
    "新华区": (114.47, 38.05),
    "正定县": (114.57, 38.14),
    "鹿泉区": (114.30, 38.08),
    "井陉县": (114.14, 38.03),
    "平山县": (114.20, 38.25),
    "灵寿县": (114.38, 38.30),
    "赞皇县": (114.40, 37.66),
    "赵县": (114.77, 37.75),
}


class AMapTool:
    """高德地图工具（live 或 mock）。"""

    source = "高德地图"

    def __init__(
        self,
        api_key: str = "",
        client: httpx.Client | None = None,
        mock: bool = True,
    ) -> None:
        self.api_key = api_key
        self.mock = mock
        self._client = client or httpx.Client(timeout=10.0)

    # ------------------------------------------------------------------ POI
    def search_poi(self, inp: POISearchInput) -> POIResult | None:
        if self.mock or not self.api_key:
            return self._mock_search_poi(inp)
        return self._live_search_poi(inp)

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.1), reraise=True)
    def _live_search_poi(self, inp: POISearchInput) -> POIResult | None:
        resp = self._client.get(
            f"{_REST}/v3/place/text",
            params={"keywords": inp.name, "city": inp.city, "key": self.api_key, "offset": 1},
        )
        resp.raise_for_status()
        payload = resp.json()
        pois = payload.get("pois") or []
        if not pois:
            return None
        p = pois[0]
        lng, lat = _parse_location(p.get("location"))
        return POIResult(
            name=p.get("name") or inp.name,
            address=p.get("address"),
            longitude=lng,
            latitude=lat,
            adcode=p.get("adcode"),
            source=self.source,
        )

    def _mock_search_poi(self, inp: POISearchInput) -> POIResult:
        lng, lat = _mock_coord(inp.name)
        return POIResult(
            name=inp.name,
            address=f"{inp.city}{_district_from_name(inp.name)}",
            longitude=lng,
            latitude=lat,
            source=f"{self.source}(Mock)",
        )

    # ------------------------------------------------------------- nearby
    def search_nearby(
        self,
        keyword: str,
        longitude: float,
        latitude: float,
        radius_m: int = 3000,
        limit: int = 5,
    ) -> list[POIResult]:
        """周边 POI 检索（沿途充电站/加油站等）。失败返回空列表，绝不编造。"""
        if self.mock or not self.api_key or longitude is None or latitude is None:
            return []
        try:
            return self._live_search_nearby(keyword, longitude, latitude, radius_m, limit)
        except Exception:  # noqa: BLE001 - POI 检索失败统一降级为空
            return []

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.1), reraise=True)
    def _live_search_nearby(
        self,
        keyword: str,
        longitude: float,
        latitude: float,
        radius_m: int,
        limit: int,
    ) -> list[POIResult]:
        resp = self._client.get(
            f"{_REST}/v3/place/around",
            params={
                "key": self.api_key,
                "location": f"{longitude},{latitude}",
                "keywords": keyword,
                "radius": radius_m,
                "offset": limit,
                "extensions": "all",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        results: list[POIResult] = []
        for p in payload.get("pois") or []:
            lng, lat = _parse_location(p.get("location"))
            results.append(
                POIResult(
                    name=p.get("name") or keyword,
                    address=p.get("address"),
                    longitude=lng,
                    latitude=lat,
                    adcode=p.get("adcode"),
                    source=self.source,
                )
            )
        return results

    # -------------------------------------------------------------- driving
    def plan_driving_route(self, inp: DrivingRouteInput) -> RouteResult | None:
        if self.mock or not self.api_key:
            return self._mock_plan_driving_route(inp)
        try:
            return self._live_plan_driving_route(inp)
        except Exception:  # noqa: BLE001 - 统一转为 None 由调用方记录风险
            return None

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.1), reraise=True)
    def _live_plan_driving_route(self, inp: DrivingRouteInput) -> RouteResult | None:
        origin = f"{round(inp.origin_lng, 6)},{round(inp.origin_lat, 6)}"
        destination = f"{round(inp.dest_lng, 6)},{round(inp.dest_lat, 6)}"
        resp = self._client.get(
            f"{_REST}/v3/direction/driving",
            params={"origin": origin, "destination": destination, "key": self.api_key},
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "1":
            raise AMapError(payload.get("info", "route planning failed"))
        path = (payload.get("route", {}).get("paths") or [None])[0]
        if path is None:
            return None
        return RouteResult(
            origin=inp.origin,
            destination=inp.destination,
            distance_km=Decimal(str(round(int(path.get("distance", 0)) / 1000, 2))),
            duration_minutes=int(int(path.get("duration", 0)) / 60),
            toll_fee=Decimal(str(path.get("tolls", 0) or 0)),
            route_summary="高德驾车路径（实时）",
            origin_lng=inp.origin_lng,
            origin_lat=inp.origin_lat,
            dest_lng=inp.dest_lng,
            dest_lat=inp.dest_lat,
            source=self.source,
            queried_at=datetime.now(),
        )

    @staticmethod
    def _mock_plan_driving_route(inp: DrivingRouteInput) -> RouteResult:
        distance_km = _mock_distance(inp.origin, inp.destination)
        duration_minutes = int(distance_km * 60 / 60) + 10  # 平均时速 ~60km/h
        toll = Decimal("0")
        return RouteResult(
            origin=inp.origin,
            destination=inp.destination,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            toll_fee=toll,
            route_summary="Mock 驾车路径（确定性估算，仅用于演示，非实时数据）",
            origin_lng=inp.origin_lng,
            origin_lat=inp.origin_lat,
            dest_lng=inp.dest_lng,
            dest_lat=inp.dest_lat,
            source=f"{AMapTool.source}(Mock)",
            queried_at=datetime.now(),
        )

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_location(location: str | None) -> tuple[float | None, float | None]:
    if not location:
        return None, None
    parts = location.split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def _mock_coord(name: str) -> tuple[float, float]:
    base_lng = 114.5
    base_lat = 38.1
    seed = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    lng = base_lng + ((seed % 80) - 40) / 100
    lat = base_lat + (((seed // 7) % 60) - 30) / 100
    return round(lng, 6), round(lat, 6)


def _mock_distance(origin: str, destination: str) -> Decimal:
    seed = int(hashlib.md5(f"{origin}:{destination}".encode()).hexdigest()[:8], 16)
    km = 15 + (seed % 95)
    return Decimal(str(km))


def _district_from_name(name: str) -> str:
    for dist in _DISTRICT_COORD:
        if dist in name:
            return dist
    return "市区"


def mock_coord_for_district(district: str) -> tuple[float, float]:
    """按区县返回 Mock 基准坐标，用于路径规划起点。"""
    return _DISTRICT_COORD.get(district, _mock_coord(district))
