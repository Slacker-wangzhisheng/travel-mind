"""天气查询工具（和风天气）。

- 返回天气、温度、降水、风力和数据更新时间。
- API 失败时返回结构化 WeatherError，绝不让 LLM 猜测天气。
- 超出预报范围时明确说明无法获得可靠预报。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from app.schemas import WeatherCondition, WeatherError, WeatherQuery, WeatherResult

_FORECAST_DAYS = 7


class WeatherProvider:
    """天气提供者抽象。"""

    name: str = "abstract"

    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()

    def get(self, query: WeatherQuery) -> WeatherResult | WeatherError:
        raise NotImplementedError


class QWeatherProvider(WeatherProvider):
    """和风天气 Web API 实现。"""

    name = "和风天气"

    def __init__(
        self,
        api_key: str,
        host: str = "https://m933jt5qdc.re.qweatherapi.com",
        client: httpx.Client | None = None,
        today: date | None = None,
    ) -> None:
        super().__init__(today=today)
        self.api_key = api_key
        self.host = host.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def get(self, query: WeatherQuery) -> WeatherResult | WeatherError:
        if not self.api_key:
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="NO_API_KEY",
                message="未配置和风天气 API Key，无法查询实时天气。",
            )
        return self._fetch(query)

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.1), reraise=False)
    def _fetch(self, query: WeatherQuery) -> WeatherResult | WeatherError:
        # 超出 7 天预报范围 -> 明确告知无法获得可靠预报
        if query.date < self.today or query.date > self.today + timedelta(days=_FORECAST_DAYS - 1):
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="OUT_OF_RANGE",
                message=f"{query.date} 超出 {_FORECAST_DAYS} 天预报范围，无法获得可靠预报。",
            )

        try:
            resp = self._client.get(
                f"{self.host}/v7/weather/7d",
                params={"location": query.location, "key": self.api_key},
            )
        except httpx.HTTPError as exc:  # 网络/超时错误
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="NETWORK_ERROR",
                message=f"天气服务请求失败：{exc}",
            )

        if resp.status_code != 200:
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="API_ERROR",
                message=f"天气服务返回异常状态码 {resp.status_code}",
            )

        payload = resp.json()
        if payload.get("code") != "200":
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="API_ERROR",
                message=f"天气服务错误：{payload.get('code')}",
            )

        daily = next((d for d in payload.get("daily", []) if d.get("fxDate") == query.date.isoformat()), None)
        if daily is None:
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="NO_DATA",
                message=f"未能获取 {query.date} 的天气数据。",
            )

        return WeatherResult(
            location=query.location,
            forecast_date=query.date,
            temp_min=int(daily["tempMin"]) if daily.get("tempMin") is not None else None,
            temp_max=int(daily["tempMax"]) if daily.get("tempMax") is not None else None,
            text_day=daily.get("textDay"),
            precip=float(daily["precip"]) if daily.get("precip") is not None else None,
            wind_dir=daily.get("windDirDay"),
            wind_scale=daily.get("windScaleDay"),
            humidity=int(daily["humidity"]) if daily.get("humidity") is not None else None,
            updated_at=_parse_update_time(payload.get("updateTime")),
            source=self.name,
        )

    def close(self) -> None:
        self._client.close()


class MockWeatherProvider(WeatherProvider):
    """确定性 Mock：不依赖网络，可注入指定日期天气（如降雨）用于测试。"""

    name = "Mock天气"

    def __init__(self, forecasts: dict[str, dict] | None = None, today: date | None = None) -> None:
        super().__init__(today=today)
        self._forecasts = forecasts or {}

    def get(self, query: WeatherQuery) -> WeatherResult | WeatherError:
        if query.date < self.today or query.date > self.today + timedelta(days=_FORECAST_DAYS - 1):
            return WeatherError(
                location=query.location,
                forecast_date=query.date,
                code="OUT_OF_RANGE",
                message=f"{query.date} 超出 {_FORECAST_DAYS} 天预报范围，无法获得可靠预报。",
            )
        data = self._forecasts.get(query.date.isoformat())
        if data is None:
            data = _deterministic_forecast(query.location, query.date)
        return WeatherResult(
            location=query.location,
            forecast_date=query.date,
            updated_at=datetime.now(),
            source=self.name,
            **data,
        )


def _deterministic_forecast(location: str, d: date) -> dict:
    """基于日期哈希的确定性 Mock 天气，默认晴朗。"""
    seed = int(hashlib.md5(f"{location}:{d.isoformat()}".encode()).hexdigest()[:8], 16)
    temps = [14, 18, 22, 25, 27, 28, 26, 20, 16, 12]
    lo = temps[seed % len(temps)]
    return {
        "temp_min": lo,
        "temp_max": lo + 8,
        "text_day": "晴",
        "precip": 0.0,
        "wind_dir": "南风",
        "wind_scale": "2级",
        "humidity": 45,
    }


def _parse_update_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "WeatherProvider",
    "QWeatherProvider",
    "MockWeatherProvider",
    "WeatherCondition",
]
