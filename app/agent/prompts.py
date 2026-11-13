"""Agent 提示词模板。"""
from __future__ import annotations

# Prompt 版本标识：修改任何 System Prompt 时同步递增，便于对比评测结果。
PROMPT_VERSION = "2026.11.13"

PARSE_SYSTEM = (
    "你是行程信息解析助手。从用户的自然语言描述中提取结构化字段，"
    "只输出 JSON，不要输出任何额外文字。缺失的字段用 null。"
    "日期转换为 YYYY-MM-DD，预算转换为数字，兴趣偏好转换为简短中文词列表。"
)

RANK_SYSTEM = (
    "你是石家庄周边游推荐助手。请为一个景点写一句话推荐理由（不超过 40 字），"
    "贴合用户兴趣。只输出纯文本，不要 JSON。"
)

REFINE_SYSTEM = (
    "你是行程调整理解助手。用户在看过行程方案后，用自然语言提出修改诉求，"
    "例如「我不想去某景点、不想跑太远、不去某个县、最远单程不超过多少公里、路上有没有充电桩/加油站」。"
    "请只输出一个 JSON 对象，不要输出任何额外文字，字段如下："
    '{"exclude_attractions": ["要排除的景点名"], "exclude_districts": ["要排除的区县名"], '
    '"max_one_way_drive_km": 单程最远公里数或 null, "wants_charging": 是否询问充电桩(true/false), '
    '"wants_gas": 是否询问加油站(true/false), "wants_replan": 是否需要重新规划(true/false)}。'
    "无法确定的字段填 null 或 false，不要臆造。"
    "如果用户说「我想去」，则 wants_replan 为 true。"
    "如果用户说「我不想去」，则 wants_replan 为 false。"
    "如果用户问其他的问题，你需要联网搜索相关信息，或者去高德地图查询。比如【某某某景点是收费的吧，某某某景点是否有充电桩/加油站？】"
)


def parse_user(text: str) -> str:
    return f"用户描述：\n{text}"


def rank_user(name: str, interests: list[str]) -> str:
    return f"景点：{name}\n用户兴趣：{'、'.join(interests) or '未指定'}"


def refine_user(text: str, context: str = "") -> str:
    if context:
        return f"【对话上下文】\n{context}\n\n【用户诉求】\n{text}"
    return f"用户诉求：\n{text}"


SUMMARY_SYSTEM = (
    "你是会话摘要助手。请把给定的历史对话压缩成不超过 200 字的中文摘要，"
    "只保留：用户已确认的出行约束、已排除的景点或区县、最终采用的方案要点。"
    "不得编造原文没有的内容，只输出摘要正文。"
)


# ---------------- 行程生成（完整 RAG：知识库 + 实时工具 → LLM 生成结构化行程） ----------------

GEN_SYSTEM = (
    "你是石家庄周边一日游的行程规划助手。你将获得四部分信息：用户需求、知识库检索结果、"
    "实时工具结果、生成要求。请仅基于这些信息规划行程。"
    "只输出一个 JSON 对象，不要输出任何额外文字。JSON 结构如下：\n"
    '{\n'
    '  "options": [\n'
    '    {\n'
    '      "title": "方案名称",\n'
    '      "reason": "一句话推荐理由（不超过40字）",\n'
    '      "attractions": [\n'
    '        {"name": "景点名（必须与提供的名称完全一致）",\n'
    '         "suggested_duration_minutes": 游览分钟数,\n'
    '         "note": "选它的理由，结合知识库介绍与来源"}\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    '}\n'
    "生成要求（必须严格遵守）：\n"
    "- 只能基于上述四部分信息规划，不得编造票价和营业时间；\n"
    "- 每个方案 2~3 个景点，attractions 顺序即游览顺序；\n"
    "- 必须满足预算、时间和单程驾车距离限制（参考实时工具结果中的驾车耗时）；\n"
    "- attractions 中的 name 必须来自「知识库检索结果」或「实时工具结果」里真实出现的景点名；\n"
    "- 有效信息不足时请缩小 scope 规划可行的较小方案，不要臆造。"
)


def gen_user(
    user_section: str,
    knowledge_section: str,
    realtime_section: str,
    feedback: str = "",
) -> str:
    """拼装生成 Prompt 的四部分。"""
    block = (
        "【用户需求】\n"
        f"{user_section}\n\n"
        "【知识库检索结果】\n"
        f"{knowledge_section or '（无检索结果）'}\n\n"
        "【实时工具结果】\n"
        f"{realtime_section or '（无实时数据）'}"
    )
    if feedback:
        block += f"\n\n【上一版校验反馈】\n{feedback}"
    return block


GEN_QUERY_SYSTEM = (
    "你是行程信息检索辅助助手。根据用户需求生成向量检索用的查询词和元数据过滤条件。"
    "只输出一个 JSON 对象："
    '{"query": "检索查询词", "district": "区县或null", "category": "类别或null"}。'
    "query 应是 3~8 个能命中景点介绍的中文关键词；district 只在用户明确想去或不去某区县时填具体区县名，否则 null。"
)


def gen_query_user(user_text: str, interests: list[str]) -> str:
    return f"用户描述：{user_text}\n兴趣偏好：{'、'.join(interests) or '未指定'}"


REQUIREMENT_SYSTEM = (
    "你是行程信息解析助手。从用户的自然语言描述中提取结构化字段，只输出 JSON，缺失字段用 null。\n"
    "JSON 字段如下："
    '{"departure": 出发地, "travel_date": YYYY-MM-DD, "people_count": 人数, "budget": 预算数字, '
    '"interests": [兴趣词], "physical_level": 体力等级1-5, "has_elderly": 是否含老人, '
    '"has_children": 是否含儿童, "latest_return_time": HH:MM, "start_time": HH:MM, '
    '"include_districts": [明确想去的区县], "exclude_districts": [明确说不去/排除的区县], '
    '"exclude_attractions": [明确说不去/排除的景点], "max_one_way_drive_km": 最远单程公里数或null}。'
    "重要区分：用户说「不去或排除某区县」必须放入 exclude_districts，绝不能放入 include_districts；"
    "只有「想去或包含某区县」才能放入 include_districts，二者必须严格分开。无法确定的填 null。"
)


CRITIC_SYSTEM = (
    "你是行程质量审查助手。判断方案是否满足用户需求、推荐理由是否有知识库/实时数据支撑、"
    "是否有无来源景点或疑似编造的信息。只输出 JSON："
    '{"decision": "ACCEPT|REPLAN|RESEARCH", "soft_issues": ["软性建议"], '
    '"unsupported_claims": ["找不到支撑来源的表述"], "feedback_for_planner": "给规划助手的改进建议", '
    '"feedback_for_retriever": "给检索助手的改写建议", "route_reason": "路由原因一句话"}。'
    "注意：你只能提出建议，最终硬约束（预算/时间/路线）由系统判定，不要在你的 JSON 中做数值计算。"
)


def critic_user(
    user_section: str,
    proposal_summary: str,
    knowledge_section: str,
    realtime_section: str,
) -> str:
    """拼装批评审 Prompt。"""
    return (
        "【用户需求】\n"
        f"{user_section}\n\n"
        "【本次方案】\n"
        f"{proposal_summary or '（无方案）'}\n\n"
        "【知识库检索结果】\n"
        f"{knowledge_section or '（无检索结果）'}\n\n"
        "【实时工具结果】\n"
        f"{realtime_section or '（无实时数据）'}"
    )
