"""石家庄景点数据采集脚本。

数据来源：
- 高德地图 POI 搜索（名称/坐标/区县/类型/开放时间），区域限定石家庄市。
- 人工整理的 A 级景区门票参考表（data/ticket_reference.json，含来源与更新时间）。

输出：data/attractions_sjz.json（供 RAG 入库使用）。

约定：票价仅在确有来源时记录，否则为空并标注「暂无可靠票价数据」；绝不编造价格/坐标/简介。
"""
from __future__ import annotations

import json
import sys
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402

_REST = "https://restapi.amap.com"
_CITY = "石家庄"
_TODAY = date.today().isoformat()

# (高德 types, 关键词) 采集规格；types 为空表示不限定大类。
SEARCH_SPECS: list[tuple[str, str]] = [
    ("风景名胜", ""),
    ("科教文化服务", ""),
]

# 石家庄市下辖 22 个区/县/县级市：按区县分片检索以突破高德单查询 ~225 条翻页上限。
DISTRICTS: list[str] = [
    "长安区", "桥西区", "新华区", "裕华区", "藁城区", "鹿泉区", "栾城区", "井陉矿区",
    "正定县", "深泽县", "无极县", "赵县", "高邑县", "赞皇县", "井陉县", "平山县",
    "元氏县", "行唐县", "灵寿县", "辛集市", "晋州市", "新乐市",
]

# 科教文化服务下仅保留与「景点」相关的子类，其余（学校/科研机构等）丢弃。
_CULTURE_KEEP_KEYWORDS = (
    "博物馆", "展览馆", "纪念馆", "美术馆", "科技馆", "文化宫",
    "故居", "遗址", "陈列馆", "名人", "图书馆",
)


def _category_from_type(ty: str) -> str:
    joined = (ty or "").replace("/", ";")
    if any(k in joined for k in ("寺庙", "道观", "教堂", "清真寺", "祠", "塔刹")):
        return "宗教"
    if any(k in joined for k in ("纪念馆", "故居", "遗址", "红色")):
        return "红色旅游"
    if any(k in joined for k in ("博物馆", "展览馆", "美术馆", "科技馆", "文化宫", "陈列馆")):
        return "博物馆"
    if any(k in joined for k in ("公园", "广场", "城市广场")):
        return "城市公园"
    if any(k in joined for k in ("古城", "古镇", "古村", "古建")):
        return "古城"
    return "自然风光"


def _clean_tags(ty: str) -> list[str]:
    seen: list[str] = []
    for seg in (ty or "").replace("/", ";").split(";"):
        seg = seg.strip()
        if seg and seg not in seen:
            seen.append(seg)
    return seen


def _live_pois(
    client: httpx.Client,
    api_key: str,
    types: str,
    keywords: str,
    city: str = _CITY,
) -> list[dict]:
    """分页拉取高德 text 搜索全部结果（单查询上限约 225 条）。"""
    pois: list[dict] = []
    page = 1
    while page <= 40:
        params = {
            "key": api_key,
            "city": city,
            "citylimit": "true",
            "types": types,
            "offset": 25,
            "page": page,
            "extensions": "all",
        }
        if keywords:
            params["keywords"] = keywords
        payload = None
        for attempt in (1, 2):
            resp = client.get(f"{_REST}/v3/place/text", params=params)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") == "1":
                break
            time.sleep(0.6 * attempt)  # 限流/频率退避
        if payload is None:
            break
        if payload.get("status") != "1":
            break
        batch = payload.get("pois") or []
        if not batch:
            break
        pois.extend(batch)
        if len(batch) < 25:
            break
        page += 1
        time.sleep(0.25)  # 控制 QPS，避免触发高德限流
    return pois


def _to_attraction_dict(p: dict) -> dict | None:
    ty = p.get("type") or ""
    cat = _category_from_type(ty)
    # 科教文化服务仅保留景点子类
    joined = ty.replace("/", ";")
    if ("科教文化" in joined or "文化服务" in joined) and not any(
        k in joined for k in _CULTURE_KEEP_KEYWORDS
    ):
        return None

    lng_str, lat_str = "", ""
    loc = p.get("location") or ""
    if "," in loc:
        lng_str, lat_str = loc.split(",", 1)
    biz = p.get("biz_ext") or {}
    open_time = biz.get("open_time")
    if not isinstance(open_time, str):
        open_time = None
    return {
        "name": p.get("name") or "",
        "district": p.get("adname") or "",
        "categories": [cat],
        "tags": _clean_tags(ty),
        "introduction": "",
        "suggested_duration_minutes": 60,
        "physical_level": 1,
        "indoor_ratio": 0.2,
        "suitable_seasons": [],
        "suitable_for_elderly": False,
        "suitable_for_children": False,
        "source_url": f"https://ditu.amap.com/place/{p.get('id', '')}",
        "updated_at": _TODAY,
        "longitude": float(lng_str) if lng_str else None,
        "latitude": float(lat_str) if lat_str else None,
        "ticket_price": None,
        "ticket_note": None,
        "open_time": open_time,
        "official_ticket_url": None,
    }


def crawl_amap_attractions(api_key: str, client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        raw: OrderedDict[str, dict] = OrderedDict()
        for district in DISTRICTS:
            for types, keywords in SEARCH_SPECS:
                for p in _live_pois(client, api_key, types, keywords, city=district):
                    key = p.get("id") or p.get("name")
                    if not key or key in raw:
                        continue
                    d = _to_attraction_dict(p)
                    if d and d["name"]:
                        raw[key] = d
        return list(raw.values())
    finally:
        if own:
            client.close()


def load_ticket_reference(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in data}


def _match_ticket_reference(name: str, reference: dict[str, dict]) -> dict | None:
    """门票参考表匹配：先精确，再按景区名互为子串（兼容高德「XX景区/风景区」变体）。"""
    if name in reference:
        return reference[name]
    for ref_name, ref in reference.items():
        if ref_name and (ref_name in name or name in ref_name):
            return ref
    return None


def merge_tickets(attractions: list[dict], reference: dict[str, dict]) -> list[dict]:
    for a in attractions:
        ref = _match_ticket_reference(a["name"], reference)
        if ref is None:
            a["ticket_note"] = "暂无可靠票价数据"
        else:
            a["ticket_price"] = ref.get("price")
            a["official_ticket_url"] = ref.get("official_url")
            if ref.get("note"):
                a["ticket_note"] = ref["note"]
            elif ref.get("price") is not None and ref["price"] == 0:
                a["ticket_note"] = "免费（以景区官方为准）"
            elif ref.get("price") is not None:
                a["ticket_note"] = f"参考票价 {ref['price']} 元（以景区官方为准）"
            else:
                a["ticket_note"] = "暂无可靠票价数据"
    return attractions


def main() -> None:
    settings = get_settings()
    if not settings.amap_api_key:
        print("未配置 AMAP_API_KEY，无法采集；请检查 .env。")
        sys.exit(1)

    out_path = ROOT / "data" / "attractions_sjz.json"
    ref_path = ROOT / "data" / "ticket_reference.json"

    attractions = crawl_amap_attractions(settings.amap_api_key)
    attractions = merge_tickets(attractions, load_ticket_reference(ref_path))

    out_path.write_text(
        json.dumps(attractions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    districts: dict[str, int] = {}
    with_ticket = 0
    for a in attractions:
        districts[a["district"]] = districts.get(a["district"], 0) + 1
        if a.get("ticket_price") is not None:
            with_ticket += 1

    print(f"采集景点总数：{len(attractions)}")
    print(f"含参考票价：{with_ticket}，其余标注「暂无可靠票价数据」")
    print("按区县分布：")
    for d, c in sorted(districts.items(), key=lambda kv: -kv[1]):
        print(f"  {d}: {c}")
    print(f"已写入：{out_path}")


if __name__ == "__main__":
    main()
