"""预算计算工具。

纯 Python / Decimal 计算，禁止让 LLM 直接算金额。

规则：
- 缺少票价时不得假设为免费：未知票价不计入已知门票合计，而是在 warnings 中显式标注。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from app.schemas import BudgetItem, BudgetResult

_FUEL_PRICE_PER_LITER = Decimal("8.00")
_FUEL_CONSUMPTION_L_PER_100KM = Decimal("8.00")
_MEAL_COST_PER_PERSON = Decimal("40.00")
_RESERVE_RATIO = Decimal("0.10")
_TWO_PLACES = Decimal("0.01")


class BudgetInput(BaseModel):
    people_count: int = Field(ge=1)
    distance_km: Decimal = Field(ge=0)
    toll_fee: Decimal = Field(default=Decimal("0"), ge=0)
    ticket_prices: list[Decimal] = Field(default_factory=list)
    unknown_ticket_names: list[str] = Field(default_factory=list)
    meal_cost_per_person: Decimal = Field(default=_MEAL_COST_PER_PERSON, ge=0)
    fuel_price_per_liter: Decimal = Field(default=_FUEL_PRICE_PER_LITER, ge=0)
    fuel_consumption_l_per_100km: Decimal = Field(default=_FUEL_CONSUMPTION_L_PER_100KM, ge=0)
    reserve_ratio: Decimal = Field(default=_RESERVE_RATIO, ge=0, le=1)


def _q(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_budget(inp: BudgetInput) -> BudgetResult:
    """根据明确输入计算预算，所有金额使用 Decimal 并保留两位小数。"""
    fuel_cost = _q(inp.distance_km * inp.fuel_consumption_l_per_100km / Decimal("100") * inp.fuel_price_per_liter)
    toll_cost = _q(inp.toll_fee)
    ticket_cost = _q(sum(inp.ticket_prices, Decimal("0")))
    meal_cost = _q(inp.meal_cost_per_person * Decimal(inp.people_count))

    known_subtotal = fuel_cost + toll_cost + ticket_cost + meal_cost
    reserve_cost = _q(known_subtotal * inp.reserve_ratio)
    total_cost = _q(known_subtotal + reserve_cost)

    items = [
        BudgetItem(name="油费", amount=fuel_cost),
        BudgetItem(name="过路费", amount=toll_cost),
        BudgetItem(name="门票", amount=ticket_cost),
        BudgetItem(name="餐饮", amount=meal_cost),
        BudgetItem(name="预留费用", amount=reserve_cost),
    ]

    warnings: list[str] = []
    unknown_count = len(inp.unknown_ticket_names)
    if inp.unknown_ticket_names:
        names = "、".join(inp.unknown_ticket_names)
        warnings.append(
            f"以下景点暂无可靠票价，未按免费计入：{names}。"
            f"当前 {total_cost} 元仅为「已知费用下限」，实际可能更高，请以景区官方信息为准。"
        )

    return BudgetResult(
        fuel_cost=fuel_cost,
        toll_cost=toll_cost,
        ticket_cost=ticket_cost,
        meal_cost=meal_cost,
        reserve_cost=reserve_cost,
        total_cost=total_cost,
        items=items,
        warnings=warnings,
        known_subtotal=_q(known_subtotal),
        unknown_ticket_count=unknown_count,
        is_lower_bound=unknown_count > 0,
    )
