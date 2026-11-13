"""确定性行程调度器。

职责（全部由 Python 计算，不依赖 LLM）：
- 从排序候选中选择景点，生成两个候选方案；
- 计算驾车路线、时间轴（含 20% 缓冲与午餐）；
- 计算预算（油费/过路费/门票/餐饮/预留）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from app.schemas import (
    Attraction,
    DriveLeg,
    ItineraryOption,
    Leg,
    RouteResult,
    TicketInfo,
    WeatherResult,
)
from app.tools.amap import AMapTool, DrivingRouteInput, mock_coord_for_district
from app.tools.budget import BudgetInput, calculate_budget

DEPARTURE_TIME = time(8, 0)
LUNCH_TIME = time(11, 30)
AVG_SPEED_KMH = 60


@dataclass
class Candidate:
    attraction: Attraction
    score: float = 0.0
    reason: str = ""


@dataclass
class ItineraryContext:
    departure: str
    travel_date: date
    people_count: int
    budget: Decimal
    interests: list[str]
    has_elderly: bool
    has_children: bool
    latest_return_time: str
    weather: WeatherResult | None
    amap: AMapTool
    lunch_minutes: int = 60
    buffer_ratio: float = 0.20
    max_one_way_drive_minutes: int = 150
    start_time: str = "08:00"


def is_raining(weather: WeatherResult | None) -> bool:
    if weather is None:
        return False
    if weather.precip is not None and weather.precip > 0:
        return True
    if weather.text_day and "雨" in weather.text_day:
        return True
    return False


def build_options(
    candidates: list[Candidate],
    ctx: ItineraryContext,
    tickets: dict[str, TicketInfo],
    max_attractions: int = 3,
) -> tuple[list[ItineraryOption], list[RouteResult]]:
    """生成两个候选方案并返回所有路线结果。"""
    raining = is_raining(ctx.weather)

    themed = _select(candidates, ctx, raining, strategy="themed", max_n=max_attractions)
    easy = _select(candidates, ctx, raining, strategy="easy", max_n=2)

    # 保证两个方案有差异：轻松线最多 2 个点，主题线最多 max_n 个点
    if len(easy) == 0:
        easy = themed[:1]

    routes: list[RouteResult] = []
    opt_a, routes_a = _build_option("方案一 · 主题精选", themed, ctx, tickets, candidates)
    opt_b, routes_b = _build_option("方案二 · 轻松备选", easy, ctx, tickets, candidates)
    routes.extend(routes_a)
    routes.extend(routes_b)

    return [opt_a, opt_b], routes


def _select(
    candidates: list[Candidate],
    ctx: ItineraryContext,
    raining: bool,
    strategy: str,
    max_n: int,
) -> list[Candidate]:
    pool = list(candidates)
    if strategy == "easy":
        if ctx.has_elderly:
            pool = [c for c in pool if c.attraction.physical_level <= 2]
        if raining:
            pool.sort(key=lambda c: c.attraction.indoor_ratio, reverse=True)
        return pool[: max(1, max_n)]
    return pool[:max_n]


def _build_option(
    title: str,
    selected: list[Candidate],
    ctx: ItineraryContext,
    tickets: dict[str, TicketInfo],
    all_candidates: list[Candidate],
) -> tuple[ItineraryOption, list[RouteResult]]:
    attractions = [c.attraction for c in selected]
    dep_lng, dep_lat = _departure_coord(ctx.departure)

    points_lng = [dep_lng] + [_coord(a)[0] for a in attractions]
    points_lat = [dep_lat] + [_coord(a)[1] for a in attractions]
    names = [ctx.departure] + [a.name for a in attractions]

    drives: list[RouteResult | None] = []
    for i in range(len(names) - 1):
        drives.append(
            _route(
                ctx, names[i], names[i + 1], points_lng[i], points_lat[i], points_lng[i + 1], points_lat[i + 1]
            )
        )
    # 返程：最后一个景点 -> 出发地
    drives.append(
        _route(
            ctx, names[-1], ctx.departure, points_lng[-1], points_lat[-1], dep_lng, dep_lat
        )
    )

    routes = [d for d in drives if d is not None]
    legs, return_epoch = _schedule(attractions, drives, ctx)

    drive_legs = [
        DriveLeg(
            origin=d.origin,
            destination=d.destination,
            distance_km=d.distance_km,
            duration_minutes=d.duration_minutes,
            toll_fee=d.toll_fee,
        )
        for d in routes
    ]

    total_distance = sum((d.distance_km for d in routes), Decimal("0"))
    total_toll = sum((d.toll_fee for d in routes), Decimal("0"))
    known_prices = [
        a.ticket_price for a in attractions if a.ticket_price is not None
    ]
    unknown_names = [a.name for a in attractions if a.ticket_price is None]

    budget = calculate_budget(
        BudgetInput(
            people_count=ctx.people_count,
            distance_km=total_distance,
            toll_fee=total_toll,
            ticket_prices=known_prices,
            unknown_ticket_names=unknown_names,
        )
    )

    reason = "；".join(c.reason for c in selected if c.reason) or "结合兴趣与体力条件综合推荐。"
    weather_advice = _weather_advice(ctx.weather)
    rain_alternative = _rain_alternative(all_candidates, ctx) if is_raining(ctx.weather) else ""

    return_time, return_day_offset = _format_time_label(return_epoch)
    total_drive_minutes = sum(d.duration_minutes for d in routes)

    option = ItineraryOption(
        title=title,
        reason=reason,
        weather=ctx.weather,
        weather_advice=weather_advice,
        legs=legs,
        drives=drive_legs,
        attractions=attractions,
        budget=budget,
        risks=list(budget.warnings),
        rain_alternative=rain_alternative,
        citations=[],
        departure=ctx.departure,
        return_time=return_time,
        return_minutes=return_epoch,
        return_day_offset=return_day_offset,
        total_drive_minutes=total_drive_minutes,
    )
    return option, routes


def _schedule(
    attractions: list[Attraction],
    drives: list[RouteResult | None],
    ctx: ItineraryContext,
) -> tuple[list[Leg], int]:
    """返回 (legs, 返回时刻相对出发的分钟数)。

    返回的分钟数**不取模**：32:20（次日 03:20）会保留为 1940 分钟，
    由校验器据此判断是否晚于最晚返回时间。
    """
    legs: list[Leg] = []
    current = _parse_minutes(ctx.start_time)
    lunch_taken = False

    for i, attr in enumerate(attractions):
        drive = drives[i]
        current += _buffered_minutes(drive, ctx.buffer_ratio)
        arrive_abs = current

        if not lunch_taken and _to_time(current) >= LUNCH_TIME:
            lunch_leave_abs = current + ctx.lunch_minutes
            legs.append(
                Leg(
                    name="午餐休息",
                    arrive_time=_to_time(current),
                    leave_time=_to_time(lunch_leave_abs),
                    duration_minutes=ctx.lunch_minutes,
                    day_offset=_day_offset(current),
                )
            )
            current = lunch_leave_abs
            lunch_taken = True
            arrive_abs = current

        leave_abs = current + attr.suggested_duration_minutes
        legs.append(
            Leg(
                name=attr.name,
                district=attr.district,
                arrive_time=_to_time(arrive_abs),
                leave_time=_to_time(leave_abs),
                duration_minutes=attr.suggested_duration_minutes,
                day_offset=_day_offset(arrive_abs),
            )
        )
        current = leave_abs

    if not lunch_taken:
        lunch_leave_abs = current + ctx.lunch_minutes
        legs.append(
            Leg(
                name="午餐休息",
                arrive_time=_to_time(current),
                leave_time=_to_time(lunch_leave_abs),
                duration_minutes=ctx.lunch_minutes,
                day_offset=_day_offset(current),
            )
        )
        current = lunch_leave_abs

    current += _buffered_minutes(drives[-1], ctx.buffer_ratio)
    return legs, current


def _route(
    ctx: ItineraryContext,
    origin: str,
    destination: str,
    o_lng: float,
    o_lat: float,
    d_lng: float,
    d_lat: float,
) -> RouteResult | None:
    route = ctx.amap.plan_driving_route(
        DrivingRouteInput(
            origin=origin,
            destination=destination,
            origin_lng=o_lng,
            origin_lat=o_lat,
            dest_lng=d_lng,
            dest_lat=d_lat,
        )
    )
    if route is not None:
        return route
    return _estimate_route(origin, destination, o_lng, o_lat, d_lng, d_lat)


def _estimate_route(
    origin: str, destination: str, o_lng: float, o_lat: float, d_lng: float, d_lat: float
) -> RouteResult:
    """路线服务不可用时的确定性估算（明确标注为估算，避免混淆实时数据）。"""
    distance_km = _haversine_km(o_lat, o_lng, d_lat, d_lng)
    duration = max(10, int(distance_km * 60 / AVG_SPEED_KMH))
    return RouteResult(
        origin=origin,
        destination=destination,
        distance_km=Decimal(str(round(distance_km, 2))),
        duration_minutes=duration,
        toll_fee=Decimal("0"),
        route_summary="路线服务不可用，按直线距离估算（非实时数据）",
        origin_lng=o_lng,
        origin_lat=o_lat,
        dest_lng=d_lng,
        dest_lat=d_lat,
        source="估算",
        queried_at=datetime.now(),
    )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _buffered_minutes(drive: RouteResult | None, ratio: float) -> int:
    if drive is None:
        return 0
    return int(round(drive.duration_minutes * (1 + ratio)))


def _departure_coord(departure: str) -> tuple[float, float]:
    for district in (
        "裕华区", "长安区", "桥西区", "新华区", "正定县", "鹿泉区",
        "井陉县", "平山县", "灵寿县", "赞皇县", "赵县",
    ):
        if district in departure:
            return mock_coord_for_district(district)
    return mock_coord_for_district("裕华区")


def _coord(attr: Attraction) -> tuple[float, float]:
    if attr.longitude is not None and attr.latitude is not None:
        return attr.longitude, attr.latitude
    return mock_coord_for_district(attr.district)


def _parse_minutes(value: str) -> int:
    """把 'HH:MM' 解析为当日分钟数，非法输入回退到 08:00。"""
    try:
        h, m = value.split(":", 1)
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 8 * 60


def _to_time(minutes: int) -> time:
    minutes %= 24 * 60
    return time(minutes // 60, minutes % 60)


def _day_offset(minutes: int) -> int:
    """相对出发日的天数偏移（0=当天，1=次日）。"""
    return max(0, minutes // (24 * 60))


def _format_time_label(minutes: int) -> tuple[str, int]:
    """把累计分钟格式化为带跨天标识的时间标签，如 '次日03:20'。"""
    day = _day_offset(minutes)
    clock = _to_time(minutes)
    if day == 0:
        return f"{clock:%H:%M}", 0
    if day == 1:
        return f"次日{clock:%H:%M}", 1
    return f"第{day + 1}日 {clock:%H:%M}", day


def _weather_advice(weather: WeatherResult | None) -> str:
    if weather is None:
        return "天气数据不可用，请出行前以官方预报为准。"
    parts = []
    if weather.temp_max is not None and weather.temp_min is not None:
        parts.append(f"气温 {weather.temp_min}~{weather.temp_max}°C")
    if weather.text_day:
        parts.append(weather.text_day)
    if weather.wind_scale:
        parts.append(f"风力 {weather.wind_scale}")
    if is_raining(weather):
        parts.append("有降雨，建议携带雨具，优先室内或低强度景点")
    return "，".join(parts) + "。"


def _rain_alternative(candidates: list[Candidate], ctx: ItineraryContext) -> str:
    indoor = [c.attraction.name for c in candidates if c.attraction.indoor_ratio >= 0.4]
    if indoor:
        return "雨天替代建议：优先选择室内景点如 " + "、".join(indoor) + "。"
    return "雨天替代建议：建议以室内景点为主，出行前确认实时天气与景区开放状态。"
