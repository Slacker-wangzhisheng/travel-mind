"""票价查询工具（Provider 抽象）。

第一版仅提供 ManualTicketProvider 与 MockTicketProvider：
- 不抓取携程、美团等商业网站。
- 没有可靠票价时返回 price=None、官方购票链接，以及“请以景区官方信息为准”。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from pydantic import BaseModel

from app.schemas import TicketInfo


class TicketQuery(BaseModel):
    attraction: str
    district: str | None = None


class TicketProvider(ABC):
    """票价提供者抽象。"""

    name: str = "abstract"

    @abstractmethod
    def get_ticket_info(self, query: TicketQuery) -> TicketInfo:
        """返回票价信息；无法获得可靠票价时 price 必须为 None。"""


class ManualTicketProvider(TicketProvider):
    """基于人工维护数据的票价提供者。未收录时诚实返回 price=None。"""

    name = "manual"

    def __init__(self, entries: Iterable[dict] | None = None) -> None:
        self._entries: dict[str, dict] = {}
        for e in entries or []:
            self._entries[e["attraction"]] = e

    def get_ticket_info(self, query: TicketQuery) -> TicketInfo:
        entry = self._entries.get(query.attraction)
        if entry is None:
            return TicketInfo(
                attraction=query.attraction,
                price=None,
                official_url=None,
                note="暂无可靠票价数据，请以景区官方信息为准",
                source="manual",
                updated_at=None,
            )
        return TicketInfo(**{"attraction": query.attraction, **entry})


class MockTicketProvider(TicketProvider):
    """测试/演示用 Provider：永远返回“无可靠数据”，避免编造票价。"""

    name = "mock"

    def get_ticket_info(self, query: TicketQuery) -> TicketInfo:
        return TicketInfo(
            attraction=query.attraction,
            price=None,
            official_url=f"https://example.com/ticket/{query.attraction}",
            note="暂无可靠票价数据，请以景区官方信息为准",
            source="mock",
            updated_at=None,
        )


def get_ticket_provider(mock: bool = True) -> TicketProvider:
    """按 mock 开关选择 Provider。默认 Mock，避免在无数据源时编造票价。"""
    if mock:
        return MockTicketProvider()
    return ManualTicketProvider()
