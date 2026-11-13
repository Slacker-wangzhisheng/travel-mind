"""硬约束可行性门禁与兜底规划。

为什么需要这一层：
- PlannerAgent / LLM / REPLAN / DeterministicFallback 都可能产出「看起来合理」但
  违反预算或返程时间的方案；
- FinalResponse 是全链路唯一出口，必须在出口前用同一个确定性校验器再筛一次，
  不合格方案直接剔除，绝不包装成推荐结果。
- 兜底规划不能无条件取前 N 个景点，必须从多到少尝试，全部不满足时诚实返回空。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas import ItineraryOption, PlanRequest, TicketInfo
from app.services.itinerary import Candidate, ItineraryContext, _build_option
from app.services.validator import ItineraryValidator, _option_return_minutes


@dataclass
class FeasibilityOutcome:
    options: list[ItineraryOption] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    message: str | None = None


def filter_feasible(
    options: list[ItineraryOption],
    request: PlanRequest,
    validator: ItineraryValidator,
) -> FeasibilityOutcome:
    """只保留通过全部 blocking 硬约束的方案；软风险写入 option.risks。"""
    kept: list[ItineraryOption] = []
    rejected: list[dict] = []
    for option in options:
        issues = validator.validate(option, request)
        blocking = [i for i in issues if i.blocking]
        if blocking:
            rejected.append(
                {
                    "title": option.title,
                    "reasons": [i.message for i in blocking],
                    "codes": [i.code for i in blocking],
                    "total_cost": option.budget.total_cost if option.budget else None,
                    "return_time": option.return_time,
                    "return_minutes": _option_return_minutes(option),
                }
            )
            continue
        for issue in issues:
            if issue.message and issue.message not in option.risks:
                option.risks.append(issue.message)
        kept.append(option)

    return FeasibilityOutcome(
        options=kept,
        rejected=rejected,
        # 输入本身就是空时，不覆盖上游（如兜底节点）给出的具体解释
        message=None if kept else (explain_no_options(rejected, request) if options else None),
    )


def explain_no_options(rejected: list[dict], request: PlanRequest) -> str:
    """把被剔除方案的原因翻译成可执行的调整建议。"""
    if not rejected:
        return "当前没有找到满足条件的候选景点，请放宽兴趣或出行条件后重试。"

    codes = {code for item in rejected for code in item.get("codes", [])}
    costs = [c for item in rejected if (c := item.get("total_cost")) is not None]
    min_cost: Decimal | None = min(costs) if costs else None

    if "OVER_BUDGET" in codes and min_cost is not None:
        text = (
            f"{request.budget} 元预算不足以完成当前自驾路线"
            f"（已知最低费用约 {min_cost} 元，已含油费、餐饮、门票与预留）。"
            "请提高预算或选择更近的景点。"
        )
        return text

    if "RETURN_TOO_LATE" in codes:
        earliest = min(
            (item["return_time"] for item in rejected if item.get("return_time")),
            default=None,
        )
        text = f"按当前路线与游览时长，预计最早 {earliest} 返回，" if earliest else "按当前路线与游览时长，"
        text += (
            f"晚于要求的最晚返回时间 {request.latest_return_time}。"
            "请减少景点、缩短单程车程或放宽返程时间。"
        )
        return text

    first = rejected[0]["reasons"][0] if rejected[0].get("reasons") else "方案不满足硬约束。"
    return f"没有可行方案：{first} 请调整预算、同行人员或出行条件后重试。"


def build_feasible_options(
    ranked: list[Candidate],
    ctx: ItineraryContext,
    tickets: dict[str, TicketInfo],
    request: PlanRequest,
    validator: ItineraryValidator,
    max_attractions: int = 3,
    max_options: int = 2,
    max_attempts: int = 6,
) -> tuple[list[ItineraryOption], list, list[dict]]:
    """从多到少尝试景点组合，只返回通过硬约束的方案。

    返回 (options, routes, rejected)。全部不合格时 options 为空。
    """
    attempts: list[tuple[str, list[Candidate]]] = []
    total = min(max_attractions, len(ranked))
    for size in range(total, 0, -1):
        attempts.append((_title_for(size), ranked[:size]))

    easy_pool = [c for c in ranked if c.attraction.physical_level <= 2]
    if easy_pool:
        attempts.append(("方案 · 轻松备选", easy_pool[:1]))
        if len(easy_pool) >= 2:
            attempts.append(("方案 · 轻松备选（两点）", easy_pool[:2]))

    options: list[ItineraryOption] = []
    routes: list = []
    rejected: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    def attempt(title: str, subset: list[Candidate]) -> bool:
        names = tuple(c.attraction.name for c in subset)
        if not names or names in seen:
            return False
        seen.add(names)

        option, option_routes = _build_option(title, subset, ctx, tickets, ranked)
        issues = validator.validate(option, request)
        blocking = [i for i in issues if i.blocking]
        if blocking:
            rejected.append(
                {
                    "title": title,
                    "reasons": [i.message for i in blocking],
                    "codes": [i.code for i in blocking],
                    "total_cost": option.budget.total_cost if option.budget else None,
                    "return_time": option.return_time,
                    "return_minutes": _option_return_minutes(option),
                }
            )
            return False

        for issue in issues:
            if issue.message and issue.message not in option.risks:
                option.risks.append(issue.message)
        options.append(option)
        routes.extend(option_routes)
        return True

    for title, subset in attempts[:max_attempts]:
        attempt(title, subset)
        if len(options) >= max_options:
            break

    # 前缀组合全部超时/超预算时，逐个尝试单点：排名第一的景点可能太远，
    # 但第二名（如鹿泉区抱犊寨）也许能在截止时间前返回，不能一次失败就放弃。
    if not options:
        for cand in ranked[:max_attempts]:
            attempt(f"方案 · 单点 {cand.attraction.name}", [cand])
            if len(options) >= max_options:
                break

    return options, routes, rejected


def _title_for(size: int) -> str:
    return f"方案 · {size} 个景点"
