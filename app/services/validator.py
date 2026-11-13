"""确定性行程校验器。

所有规则由 Python 计算，禁止让 LLM 判定冲突。校验结果分为：
- blocking=True 的硬性冲突：触发 Agent 修订；
- blocking=False 的风险提醒：进入输出，但不阻塞。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal

from app.schemas import ItineraryOption, PlanRequest


@dataclass
class ValidationIssue:
    code: str
    message: str
    blocking: bool = True


def _parse_time(value: str) -> time:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return time(23, 59)


def _parse_minutes(value: str) -> int:
    t = _parse_time(value)
    return t.hour * 60 + t.minute


def _option_return_minutes(option: ItineraryOption) -> int | None:
    """优先使用未取模的累计分钟；旧数据回退解析字符串并还原跨天信息。"""
    if option.return_minutes and option.return_minutes > 0:
        return option.return_minutes
    text = (option.return_time or "").strip()
    if not text:
        return None
    day_offset = 0
    if text.startswith("次日"):
        day_offset, text = 1, text[len("次日") :].strip()
    elif text.startswith("第") and "日" in text:
        head, _, rest = text.partition("日")
        try:
            day_offset = max(0, int(head[1:]) - 1)
        except ValueError:
            day_offset = 0
        text = rest.strip()
    return day_offset * 24 * 60 + _parse_minutes(text)


class ItineraryValidator:
    def __init__(
        self,
        max_one_way_drive_minutes: int = 150,
        default_latest_return: str = "21:00",
        lunch_minutes: int = 60,
        allow_high_physical_for_elderly: bool = False,
    ) -> None:
        self.max_one_way_drive_minutes = max_one_way_drive_minutes
        self.default_latest_return = default_latest_return
        self.lunch_minutes = lunch_minutes
        self.allow_high_physical_for_elderly = allow_high_physical_for_elderly

    def validate(self, option: ItineraryOption, request: PlanRequest) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        # 1. 单程驾驶时长
        for drive in option.drives:
            if drive.duration_minutes > self.max_one_way_drive_minutes:
                issues.append(
                    ValidationIssue(
                        code="DRIVE_TOO_LONG",
                        message=(
                            f"{drive.origin} → {drive.destination} 驾车约 "
                            f"{drive.duration_minutes} 分钟，超过单程上限 "
                            f"{self.max_one_way_drive_minutes} 分钟。"
                        ),
                    )
                )

        # 1.5 单程驾驶距离上限（多轮对话「最远不超过 X 公里」约束）
        if request.max_one_way_drive_km is not None:
            cap = Decimal(str(request.max_one_way_drive_km))
            for drive in option.drives:
                if drive.distance_km > cap:
                    issues.append(
                        ValidationIssue(
                            code="DRIVE_TOO_FAR",
                            message=(
                                f"{drive.origin} → {drive.destination} 约 "
                                f"{drive.distance_km} 公里，超过单程上限 {cap} 公里。"
                            ),
                        )
                    )

        # 2. 最晚返回时间（按「从出发起的累计分钟」比较，保留跨天信息）
        latest = _parse_time(request.latest_return_time or self.default_latest_return)
        latest_minutes = latest.hour * 60 + latest.minute
        return_minutes = _option_return_minutes(option)
        if return_minutes is not None and return_minutes > latest_minutes:
            late_by = return_minutes - latest_minutes
            hours, minutes = divmod(late_by, 60)
            issues.append(
                ValidationIssue(
                    code="RETURN_TOO_LATE",
                    message=(
                        f"预计 {option.return_time or f'{return_minutes // 60:02d}:{return_minutes % 60:02d}'} 返回，"
                        f"晚于要求的最晚返回时间 {latest:%H:%M}"
                        f"（超出约 {hours} 小时 {minutes} 分钟）。"
                    ),
                )
            )

        # 3. 老人体力限制
        if request.has_elderly and not self.allow_high_physical_for_elderly:
            for attr in option.attractions:
                if attr.physical_level > 2:
                    issues.append(
                        ValidationIssue(
                            code="PHYSICAL_TOO_HIGH",
                            message=f"同行有老人，{attr.name} 体力强度为 {attr.physical_level}，不建议推荐。",
                        )
                    )

        # 4. 预算
        if option.budget and option.budget.total_cost > request.budget:
            prefix = "已知费用下限" if option.budget.is_lower_bound else "方案总费用"
            issues.append(
                ValidationIssue(
                    code="OVER_BUDGET",
                    message=(
                        f"{prefix} {option.budget.total_cost} 元，超过预算 "
                        f"{request.budget} 元。"
                    ),
                )
            )
        elif option.budget and option.budget.is_lower_bound:
            # 未知门票不能被当作 0 元，只能在通过最低预算时提示风险
            issues.append(
                ValidationIssue(
                    code="BUDGET_LOWER_BOUND",
                    message=(
                        f"预算 {request.budget} 元仅覆盖已知费用下限，仍有 "
                        f"{option.budget.unknown_ticket_count} 个景点门票未知，存在超支风险。"
                    ),
                    blocking=False,
                )
            )

        # 5. 时间段不重叠
        issues.extend(self._check_overlap(option))

        # 6. 午餐预留（风险级）
        if not any(leg.name and "午餐" in leg.name for leg in option.legs):
            issues.append(
                ValidationIssue(
                    code="NO_LUNCH",
                    message="行程未预留午餐时间（建议至少 60 分钟）。",
                    blocking=False,
                )
            )

        # 7. 缺失票价 -> 不得假设免费，输出风险
        unknown = [a.name for a in option.attractions if a.ticket_price is None]
        if unknown:
            issues.append(
                ValidationIssue(
                    code="UNKNOWN_TICKET",
                    message=f"以下景点暂无可靠票价，未按免费计入预算：{'、'.join(unknown)}。请以景区官方信息为准。",
                    blocking=False,
                )
            )

        # 8. 缺失开放时间 -> 风险提醒
        no_open = [a.name for a in option.attractions if not a.open_time]
        if no_open:
            issues.append(
                ValidationIssue(
                    code="NO_OPEN_TIME",
                    message=f"以下景点缺少开放时间数据，请出发前以官方信息确认：{'、'.join(no_open)}。",
                    blocking=False,
                )
            )

        return issues

    def _check_overlap(self, option: ItineraryOption) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        timed = [
            leg for leg in option.legs
            if leg.arrive_time is not None and leg.leave_time is not None
        ]
        timed.sort(key=lambda leg: leg.arrive_time)  # type: ignore[arg-type]
        for prev, cur in zip(timed[:-1], timed[1:], strict=False):
            if cur.arrive_time < prev.leave_time:  # type: ignore[operator]
                issues.append(
                    ValidationIssue(
                        code="TIME_OVERLAP",
                        message=f"时间段重叠：{prev.name}（{prev.leave_time!s}）与 {cur.name}（{cur.arrive_time!s}）。",
                    )
                )
        return issues
