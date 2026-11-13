"""RealtimeAgent：按候选景点查天气、地图路线、POI、票务。

- 只查候选集合内的景点；新增候选（RESEARCH 补查）必须重新查询。
- 外部接口统一超时/重试/失败处理，结果附来源与查询时间；失败明确 unknown。
- 不编造票价 / 开放时间 / 路线 / 距离。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.base import Agent
from app.agents.schemas import RealtimeResult
from app.schemas import RouteResult
from app.tools.amap import POISearchInput
from app.tools.tickets import TicketQuery
from app.tools.weather import WeatherError, WeatherQuery

_LOCATION_ID = "101090101"  # 石家庄


class RealtimeAgent(Agent):
    name = "RealtimeAgent"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        candidates = state.get("candidate_attractions") or []
        queried_at = datetime.now()

        # ---- 天气（一次按目的地查询）----
        weather = None
        weather_error = None
        if req is not None:
            query = WeatherQuery(location=_LOCATION_ID, date=req.travel_date, mode="live")
            try:
                res = deps.weather.get(query)
            except Exception:  # noqa: BLE001
                res = WeatherError(
                    location=_LOCATION_ID,
                    forecast_date=getattr(req, "travel_date", None) or datetime.now().date(),
                    code="QUERY_FAILED",
                    message="天气查询失败",
                )
            if isinstance(res, WeatherError):
                weather_error = res
            else:
                weather = res

        # ---- 逐候选景点：路线 / POI / 票价 ----
        route_map: dict[str, RouteResult] = {}
        ticket_map: dict[str, Any] = {}
        poi_map: dict[str, Any] = {}
        failed: list[str] = []
        sources: list[str] = []

        for attr in candidates:
            name = attr.name
            # POI 坐标
            try:
                poi = deps.amap.search_poi(POISearchInput(name=name))
            except Exception:  # noqa: BLE001
                poi = None
            if poi is not None and poi.longitude is not None and poi.latitude is not None:
                attr.longitude = poi.longitude
                attr.latitude = poi.latitude
                poi_map[name] = poi
            else:
                failed.append(f"POI:{name}")

            # 单程驾车路线（出发地 -> 景点）
            try:
                route = _one_way(state, deps, attr)
            except Exception:  # noqa: BLE001
                route = None
                failed.append(f"路线:{name}")
            if route is not None:
                route_map[name] = route
                if route.source not in sources:
                    sources.append(route.source)
            else:
                failed.append(f"路线:{name}")

            # 票价
            try:
                ticket = deps.tickets.get_ticket_info(TicketQuery(attraction=name, district=attr.district))
            except Exception:  # noqa: BLE001
                ticket = None
            if ticket is not None:
                attr.ticket_price = ticket.price
                attr.official_ticket_url = ticket.official_url
                ticket_map[name] = ticket
                if ticket.source and ticket.source not in sources:
                    sources.append(ticket.source)
            else:
                failed.append(f"票价:{name}")

        if weather_error is not None:
            failed.append(f"天气:{weather_error.code if hasattr(weather_error, 'code') else 'unknown'}")
        elif weather is not None and weather.source not in sources:
            sources.append(weather.source)

        result = RealtimeResult(
            weather=weather,
            weather_error=weather_error,
            route_results=list(route_map.values()),
            ticket_results=list(ticket_map.values()),
            route_map=route_map,
            ticket_map=ticket_map,
            poi_map=poi_map,
            failed_tools=sorted(set(failed)),
            sources=sources,
            queried_at=queried_at,
        )

        return {
            "realtime_result": result,
            "weather": weather,
            "weather_error": weather_error,
            "route_results": list(route_map.values()),
            "ticket_results": list(ticket_map.values()),
            "candidate_attractions": candidates,
            "current_agent": self.name,
            "next_agent": "PlannerAgent",
            "route_reason": "实时数据就绪",
        }


def _one_way(state: dict, deps: Any, attr: Any) -> RouteResult | None:
    from app.services.itinerary import _coord, _departure_coord, _route

    dep = state.get("departure") or ""
    dep_lng, dep_lat = _departure_coord(dep)
    lng, lat = _coord(attr)
    ctx = _ctx(state, deps, attr)
    return _route(ctx, dep, attr.name, dep_lng, dep_lat, lng, lat)


def _ctx(state: dict, deps: Any, attr: Any):
    from datetime import date
    from decimal import Decimal

    from app.services.itinerary import ItineraryContext

    req = state.get("requirement_result")
    weather = state.get("weather")
    return ItineraryContext(
        departure=state.get("departure") or "",
        travel_date=req.travel_date if req else state.get("travel_date") or date.today(),
        people_count=req.people_count if req else state.get("people_count") or 1,
        budget=req.budget if req and req.budget is not None else Decimal("500"),
        interests=req.interests if req else [],
        has_elderly=bool(req.has_elderly) if req else False,
        has_children=bool(req.has_children) if req else False,
        latest_return_time=req.latest_return_time if req else "21:00",
        weather=weather,
        amap=deps.amap,
        lunch_minutes=deps.settings.lunch_minutes,
        buffer_ratio=deps.settings.time_buffer_ratio,
        max_one_way_drive_minutes=deps.settings.max_one_way_drive_minutes,
    )
