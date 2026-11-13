"""领域模型：跨模块共享的 Pydantic v2 输入/输出模型。

说明：
- 实时数据（天气、路线、票价、经纬度、开放时间）一律不得在种子数据中编造；
  运行时通过工具查询，查询不到时用 None，并在 citations/risks 中显式标注来源缺失。
- 金额统一使用 Decimal，避免浮点误差。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 景点
# ---------------------------------------------------------------------------


class Attraction(BaseModel):
    """景点（种子数据 + 运行时补充信息合并后的统一视图）。"""

    name: str
    district: str
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    introduction: str = ""
    suggested_duration_minutes: int = 60
    physical_level: int = Field(ge=1, le=5)
    indoor_ratio: float = Field(ge=0.0, le=1.0)
    suitable_seasons: list[str] = Field(default_factory=list)
    suitable_for_elderly: bool = False
    suitable_for_children: bool = False
    source_url: str | None = None
    updated_at: str | None = None

    # 运行时补充信息（缺失为 None，绝不编造）
    longitude: float | None = None
    latitude: float | None = None
    ticket_price: Decimal | None = None
    ticket_note: str | None = None
    open_time: str | None = None
    official_ticket_url: str | None = None


# ---------------------------------------------------------------------------
# 天气
# ---------------------------------------------------------------------------


class WeatherCondition(BaseModel):
    text: str
    code: str | None = None


class WeatherResult(BaseModel):
    """和风天气查询结果。"""

    location: str
    forecast_date: date
    temp_min: int | None = None
    temp_max: int | None = None
    text_day: str | None = None
    precip: float | None = None
    wind_dir: str | None = None
    wind_scale: str | None = None
    humidity: int | None = None
    updated_at: datetime | None = None
    source: str = "和风天气"


class WeatherError(BaseModel):
    """结构化天气错误。"""

    location: str
    forecast_date: date
    code: str
    message: str
    source: str = "和风天气"


class WeatherQuery(BaseModel):
    location: str
    date: date
    mode: Literal["mock", "live"] = "live"


# ---------------------------------------------------------------------------
# 高德 POI / 路线
# ---------------------------------------------------------------------------


class POIResult(BaseModel):
    name: str
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    adcode: str | None = None
    source: str = "高德地图"


class RouteResult(BaseModel):
    origin: str
    destination: str
    distance_km: Decimal
    duration_minutes: int
    toll_fee: Decimal = Decimal("0")
    route_summary: str = ""
    origin_lng: float | None = None
    origin_lat: float | None = None
    dest_lng: float | None = None
    dest_lat: float | None = None
    polyline: list[tuple[float, float]] = Field(default_factory=list)
    source: str = "高德地图"
    queried_at: datetime | None = None


# ---------------------------------------------------------------------------
# 票价
# ---------------------------------------------------------------------------


class TicketInfo(BaseModel):
    attraction: str
    price: Decimal | None = None
    official_url: str | None = None
    note: str | None = None
    source: str = "景区官方"
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------


class BudgetItem(BaseModel):
    name: str
    amount: Decimal


class BudgetResult(BaseModel):
    fuel_cost: Decimal
    toll_cost: Decimal
    ticket_cost: Decimal
    meal_cost: Decimal
    reserve_cost: Decimal
    total_cost: Decimal
    items: list[BudgetItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # 已知费用下限（不含未知门票）；存在未知票价时 total_cost 只是下限
    known_subtotal: Decimal = Decimal("0")
    unknown_ticket_count: int = 0
    is_lower_bound: bool = False


# ---------------------------------------------------------------------------
# 行程
# ---------------------------------------------------------------------------


class Leg(BaseModel):
    """行程中的一个停留节点/景点。"""

    name: str
    district: str | None = None
    arrive_time: time | None = None
    leave_time: time | None = None
    duration_minutes: int | None = None
    # 0=出发日；1=次日；以此类推（跨天信息不能被 24 小时取模丢失）
    day_offset: int = 0


class DriveLeg(BaseModel):
    """两景点之间的驾车段。"""

    origin: str
    destination: str
    distance_km: Decimal
    duration_minutes: int
    toll_fee: Decimal = Decimal("0")


class ItineraryOption(BaseModel):
    """一个候选行程方案。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    reason: str = ""
    weather: WeatherResult | None = None
    weather_advice: str = ""
    legs: list[Leg] = Field(default_factory=list)
    drives: list[DriveLeg] = Field(default_factory=list)
    attractions: list[Attraction] = Field(default_factory=list)
    budget: BudgetResult | None = None
    risks: list[str] = Field(default_factory=list)
    rain_alternative: str = ""
    citations: list[str] = Field(default_factory=list)
    departure: str = ""
    return_time: str = ""
    # 从出发时间起的累计分钟数（可为 24*60 以上），用于确定性校验跨天
    return_minutes: int = 0
    return_day_offset: int = 0
    total_drive_minutes: int = 0


# ---------------------------------------------------------------------------
# 引用 / 来源
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    kind: str  # weather / route / ticket / knowledge
    label: str
    url: str | None = None
    updated_at: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# API 请求 / 响应
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    departure: str
    travel_date: date
    people_count: int = Field(ge=1, le=50)
    budget: Decimal = Field(gt=0)
    interests: list[str] = Field(default_factory=list)
    transport_mode: Literal["driving"] = "driving"
    physical_level: int = Field(default=3, ge=1, le=5)
    has_elderly: bool = False
    has_children: bool = False
    latest_return_time: str = "21:00"
    start_time: str = "08:00"
    # 多轮对话新增约束
    exclude_attractions: list[str] = Field(default_factory=list)
    exclude_districts: list[str] = Field(default_factory=list)
    max_one_way_drive_km: float | None = None


class PlanResponse(BaseModel):
    plan_id: str | None = None
    clarification_required: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    itinerary_options: list[ItineraryOption] = Field(default_factory=list)
    # 没有任何方案通过硬约束时，说明原因与可调整方向
    explanation: str | None = None


# ---------------------------------------------------------------------------
# 对话式接口（多轮行程调整）
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    form: PlanRequest | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    clarification_required: bool = False
    clarification_question: str | None = None
    itinerary_options: list[ItineraryOption] = Field(default_factory=list)
    poi_results: list[POIResult] = Field(default_factory=list)
    # 新增字段为可选项，保持与既有前端协议兼容
    memory_suggestions: list[MemorySuggestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 认证 / 会话 / 长期记忆
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str | None = None


class SessionSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime | None = None
    created_at: datetime | None = None


class SessionDetail(BaseModel):
    session: SessionSummary
    messages: list[dict] = Field(default_factory=list)
    itinerary_options: list[ItineraryOption] = Field(default_factory=list)
    # 本会话创建时注入的已确认长期偏好（便于前端展示与自查）
    applied_preferences: list[dict] = Field(default_factory=list)


class MemorySuggestion(BaseModel):
    id: str
    type: str
    content: str
    status: str = "pending"


class MemoryCreateRequest(BaseModel):
    type: str = "interest"
    content: str


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    status: Literal["pending", "confirmed"] | None = None
