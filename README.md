# TravelMind 石游助手

> 石家庄周边一日游智能规划系统 —— 基于 **Supervisor 多 Agent + RAG + 确定性计算** 的对话式行程规划器。

用户输入出发位置、日期、人数、预算、兴趣等信息后，系统输出**两个候选行程方案**，包含推荐理由、天气建议、时间轴、路线距离/耗时、票价、预算明细、风险提醒、雨天备选与数据来源，并支持像豆包一样继续对话调整（排除景点、不去某区县、限制单程距离、查询沿途充电桩/加油站等）。

---

## 核心特性

- **Supervisor 多 Agent 架构**：需求理解、知识检索、实时信息、行程规划、方案审查五个 Agent 职责独立，由确定性 Supervisor 路由（ACCEPT / REPLAN / RESEARCH），所有循环都有上限。
- **RAG 知识库**：pgvector 向量检索 + 本地规则检索合并；按兴趣做确定性相关性过滤（同义词扩展，零相关景点不得进入候选），支持区县/类别过滤、查询改写与结果去重。
- **真实语义 Embedding**：支持远程 OpenAI-compatible `/embeddings`，也支持 `fastembed` 本地模型（`BAAI/bge-small-zh-v1.5`，512 维），无需 API Key 即可获得真实语义召回。
- **确定性优先**：路线、时间轴（含 20% 缓冲与午餐）、预算（Decimal）、硬约束校验全部由 Python 计算，LLM 不会覆盖这些数字。
- **诚实原则**：天气/路线/票价/开放时间为实时查询；查不到标注 `unknown`，绝不编造票价、坐标或营业时间。
- **硬约束门禁**：预算、最晚返程、单程车程、体力/老人儿童、午餐、未知票价、路线可行性统一校验；所有生成路径（LLM / REPLAN / 兜底）在出口前复检，不合格方案直接剔除，无可行方案时返回明确解释。
- **多用户与持久化**：注册/登录/退出（PBKDF2 密码哈希 + HttpOnly 签名 Cookie）；会话、消息、行程写入 PostgreSQL，按 `user_id` 隔离。
- **短期记忆**：每个会话保存结构化约束、最近行程与对话摘要（摘要与原始消息分列，互不覆盖）。
- **长期记忆**：只保存跨旅行的稳定偏好，必须用户确认后才生效，当前输入优先。
- **RAG 可量化**：`evals/` 提供人工标注评测集与脚本，输出 Recall@5/10、无结果率、错误区县率、排除命中率、耗时；降级结果会被明确标记。
- **对话式调整**：多轮会话，Vue 前端提供对话页、历史会话与偏好管理。
- **可降级**：外部依赖全部可注入 Mock；未配置 Key 时全 Mock 跑通；LLM 欠费/超时自动切确定性解析与规划；数据库不可用时降级内存存储并告警。
- **一键启动**：Docker Compose（PostgreSQL + FastAPI + Vue/Nginx）。

---

## 技术栈

Python 3.12 · LangGraph · FastAPI · Pydantic v2 · SQLAlchemy · PostgreSQL + pgvector · DeepSeek（OpenAI-compatible）· fastembed · 高德地图 · 和风天气 · Vue 3 + Vite · uv · Docker Compose

---

## 系统架构

```
START
  ↓
RequirementAgent ──信息不完整──► FinalResponse(追问)
  ↓信息完整
Supervisor（确定性路由 + research/planning 计数）
  ├─ 首轮        → KnowledgeAgent → RealtimeAgent → PlannerAgent
  ├─ RESEARCH(≤2) → KnowledgeAgent → RealtimeAgent → PlannerAgent
  └─ REPLAN(≤3)  → PlannerAgent（保留知识 + 实时数据，只重规划）
                            ↓
                   DeterministicExecutor（路线 / 时间轴 / 预算）
                            ↓
                    CriticAgent（只建议，硬约束由 Python 校验器判定）
                            ↓
                    Supervisor ── ACCEPT → FinalResponse
                      └─ 超限 → DeterministicFallback → FinalResponse
```

- **ACCEPT**：硬约束通过且无严重事实问题，输出方案。
- **REPLAN**：顺序/组合/时长不合理，或出现不相关景点，只让 Planner 重排。
- **RESEARCH**：知识不足、相关度过低或疑似编造，改写查询重新检索并补查实时数据。
- 最多重新检索 2 次、重新规划 3 次；超限后由确定性调度器兜底，绝不死循环。

**兴趣相关性闭环**：检索后先按兴趣（含同义词）剔除零相关候选 → Planner 只能从相关候选选点 → Critic 逐方案复核，发现不相关景点即触发 REPLAN → FinalResponse 出口再次拦截。用户偏好「自然风光/山岳/森林」时，正定古城这类历史文化景点不会进入任何方案。

每个 Agent 有独立 System Prompt、Pydantic 输入/输出 Schema 与专属工具权限，通过共享状态传递结构化结果。运行轨迹写入 `agent_trace`（Agent、起止时间、状态、下一 Agent、路由原因），不记录 API Key、敏感 Prompt 或思维链。

---

## 目录结构

```
app/
├─ main.py                    FastAPI 入口
├─ config.py                  pydantic-settings 配置（禁止硬编码密钥）
├─ schemas.py                 领域 Pydantic 模型
├─ agents/                    多 Agent（requirement/knowledge/realtime/planner/critic + supervisor + executor）
├─ auth/security.py           PBKDF2 密码哈希 + HMAC 签名 Cookie
├─ api/
│  ├─ routes.py               行程 / 对话 / 会话 / 知识导入
│  ├─ auth.py                 注册 / 登录 / 退出 / 当前用户
│  ├─ memory.py               长期记忆 CRUD（按 user_id 隔离）
│  └─ dependencies.py         依赖注入（get_graph 按 MULTI_AGENT_ENABLED 切换）
├─ agent/
│  ├─ multi_agent_graph.py    Supervisor 多 Agent 图
│  ├─ graph.py                单 Agent 图（开关回退用）
│  └─ state.py / prompts.py / llm.py
├─ tools/                     实时工具（weather/amap/tickets/budget/attraction_search）
├─ rag/                       向量检索（embeddings/splitter/retriever/ingest）
├─ db/                        SQLAlchemy + pgvector 模型与会话
└─ services/
   ├─ chat.py                 多轮对话（持久化 + 短期/长期记忆）
   ├─ storage.py              Store 抽象：PostgreSQL / 内存
   ├─ summary.py              对话摘要与上下文裁剪
   ├─ memory.py               长期偏好提取与注入
   ├─ relevance.py            兴趣相关性判定（同义词 + 确定性打分）
   ├─ feasibility.py          硬约束门禁与确定性兜底
   ├─ itinerary.py            确定性调度器
   └─ validator.py            确定性校验器
ui/
├─ web/                       Vue 3 前端（登录 + 会话 + 偏好管理）
└─ streamlit_app.py           旧 Streamlit 前端（可选）
data/                         景点种子数据 / 门票参考数据
evals/                        RAG 评测集、评测脚本与历史结果
tests/                        pytest 用例（全 Mock，不联网）
alembic/                      数据库迁移
```

### 数据分层与隔离

| 存储 | 表 | 用途 | 隔离方式 |
|---|---|---|---|
| 景点知识库 | `document_chunks` | RAG 向量与来源 | 公共只读 |
| 用户 | `users` | 账号与密码哈希 | — |
| 会话 | `chat_sessions` | 结构化约束、摘要、最近方案 | `user_id` |
| 消息 | `chat_messages` | 原始对话（摘要不覆盖原始记录） | `user_id` |
| 行程 | `saved_plans` | 已生成方案 | `user_id` |
| 长期记忆 | `user_memories` | 已确认的稳定偏好 | `user_id` |

数据分工：

- **实时数据**（天气/路线/票价/开放状态）→ 工具查询，带来源与查询时间，失败标注 `unknown`。
- **静态知识**（景点介绍/历史文化/游玩特色/适合人群）→ RAG，返回来源链接。
- **距离/时间/预算/冲突校验** → Python 确定性计算；LLM 只负责理解需求、检索改写、选点与推荐理由。

---

## 快速开始

### 方式一：Docker Compose

```bash
cp .env.example .env     # 填入 AUTH_SECRET 与各类 Key（Key 可留空走 Mock）
docker compose up --build
```

- Vue 前端：http://localhost:8080
- API：http://localhost:8000（`/health` 含 `prompt_version`）
- 旧 Streamlit 前端（可选）：`docker compose --profile streamlit up`

> 如果 Docker Hub 拉取 `node:20-alpine` / `nginx:1.27-alpine` 超时，请先在 Docker Desktop 配置国内镜像加速，或手动 pull 后 `docker tag` 成官方名称再构建。

### 方式二：本地运行

```bash
uv sync

# 后端
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另开终端）
cd ui/web
npm install
npm run dev              # http://localhost:5173
```

默认端口：后端 8000，前端 5173，Vite 已把 `/api` 代理到后端。后端换端口时设置 `VITE_PROXY_TARGET`。

### 初始化数据库与向量库

```bash
uv run alembic upgrade head
curl -X POST http://localhost:8000/api/v1/knowledge/ingest   # 导入 data/attractions.json
```

认证需要 `AUTH_SECRET`；未设置时使用开发默认值并打印告警。`STORAGE_BACKEND=auto`（默认）在数据库可用时写入 PostgreSQL，否则降级内存。


---

## 配置

复制 `.env.example` 为 `.env` 并填写（`.env` 已加入 `.gitignore`，请勿提交）：

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 大模型（默认接 DeepSeek：`https://api.deepseek.com` + `deepseek-flash`） |
| `EMBEDDING_BACKEND` | `openai`（远程 `/embeddings`）/ `local`（fastembed 本地语义向量） |
| `EMBEDDING_MODEL` | 远程 Embedding 模型名（`openai` 后端） |
| `LOCAL_EMBEDDING_MODEL` | 本地模型，默认 `BAAI/bge-small-zh-v1.5`（512 维） |
| `HF_ENDPOINT` | HuggingFace 下载镜像（可选，国内建议 `https://hf-mirror.com`） |
| `AMAP_API_KEY` | 高德地图（POI + 路径） |
| `QWEATHER_API_KEY` / `QWEATHER_API_HOST` | 和风天气 |
| `DATABASE_URL` / `VECTOR_DIM` | PostgreSQL + pgvector（`local` 时设 `512`） |
| `ENABLE_MOCK` | 未配置外部 Key 时自动回退 Mock |
| `MULTI_AGENT_ENABLED` | 是否启用多 Agent 图（默认 `true`） |
| `MAX_RESEARCH_ITERATIONS` | 最大重新检索次数（默认 2） |
| `MAX_PLANNING_ITERATIONS` | 最大重新规划次数（默认 3） |
| `ENABLE_LLM_CRITIC` | 是否启用 LLM 方案审查（默认 `true`） |
| `AUTH_SECRET` | 签名 HttpOnly 会话 Cookie 的密钥（生产必须设置为随机长字符串） |
| `COOKIE_SECURE` / `COOKIE_SAMESITE` | Cookie 安全属性（HTTPS 时设 `COOKIE_SECURE=true`） |
| `STORAGE_BACKEND` | `auto`（默认）/ `db` / `memory` |
| `AUTO_CREATE_TABLES` | 启动时自动创建用户/会话等表（默认 `true`；生产建议 Alembic） |
| `CORS_ORIGINS` | 允许携带 Cookie 的前端来源（逗号分隔） |
| `RECENT_MESSAGE_LIMIT` | 发送给 LLM 的最近消息条数（默认 8） |
| `ENABLE_LLM_SUMMARY` | 是否用 LLM 生成摘要（默认 `false`） |

所有外部依赖均走可注入接口：未配置 API Key 时运行在 Mock 模式，无需任何 Key 即可本地演示与测试。


---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（含 `prompt_version`） |
| POST | `/api/v1/auth/register` | 注册并登录（设置 HttpOnly Cookie） |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/logout` | 退出 |
| GET | `/api/v1/auth/me` | 当前用户（未登录 401） |
| POST | `/api/v1/plan` | 生成行程（登录后写入 `saved_plans`） |
| POST | `/api/v1/chat` | 多轮对话式规划（首轮可带 `form`，后续带 `session_id`） |
| GET | `/api/v1/chat/sessions` | 当前用户的历史会话 |
| GET | `/api/v1/chat/sessions/{id}` | 会话消息与最近方案（非本人 404） |
| DELETE | `/api/v1/chat/sessions/{id}` | 删除会话（非本人 404） |
| GET/POST | `/api/v1/memories` | 长期偏好列表 / 手动新增 |
| PATCH/DELETE | `/api/v1/memories/{id}` | 确认、编辑、删除偏好（非本人 404） |
| POST | `/api/v1/knowledge/ingest` | 导入景点到向量库 |
| GET | `/api/v1/attractions` | 景点列表 |
| GET | `/api/v1/plans/{plan_id}` | 按 ID 查询已生成方案 |

前端请求统一带 `credentials: 'include'`；开发由 Vite 代理 `/api`，生产由 Nginx 反代，Cookie 同站。交互式文档：http://localhost:8000/docs。


## 安全

- API Key 只能从环境变量读取，禁止硬编码或提交仓库；`.env.example` 仅含空占位符。
- 密码只存 PBKDF2-HMAC-SHA256 哈希（260k 迭代 + 随机盐）；会话凭据为 HMAC 签名的 HttpOnly Cookie（`SameSite=Lax`，HTTPS 下应设 `COOKIE_SECURE=true`）。
- 所有会话、消息、行程、长期记忆查询都带 `user_id` 条件，仅凭 `session_id` 无法跨用户访问（统一返回 404）。
- 日志不输出 Authorization Header、API Key、数据库密码或完整敏感输入；`httpx` 日志降级，异常信息经 `app/utils/redact.py` 脱敏。
- 外部工具结果视为不可信输入，进入 Prompt 前做长度与字段限制。
- 若 Key 曾出现在源码、日志或历史提交中，请立即到服务商后台撤销并轮换。

---

## 验收案例

Mock 模式：

1. 「本周六两人从裕华区出发，自驾，预算 600 元，喜欢历史文化，晚 8 点前回来。」
2. 「带一位老人从石家庄出发，不爬山，预算 500 元，当天往返。」
3. 「喜欢自然风景，但当天预报有雨，请生成室内或低强度替代方案。」
4. 「这一版定了，但我不想看到赵州桥、不去平山县、最远单程不超过 120 公里。」
5. 「从裕华区 08:00 出发，2 人，预算 600 元，偏好自然风光、山岳、森林，最晚 19:00 返回。」→ 候选只包含五岳寨/抱犊寨/苍岩山等自然景点，不含正定古城。

多用户 / 记忆：

6. 用户 A 注册并规划 → 系统提出「常住裕华区 / 喜欢历史文化」等待确认 → A 确认 → 新会话自动注入该偏好。
7. 用户 B 看不到 A 的会话与偏好；拿 A 的 `session_id` 访问返回 404。
8. 重启服务后，A 用原 `session_id` 继续对话（需 PostgreSQL，`STORAGE_BACKEND=db`）。

输出保证：不超过预算、不晚于最晚返回、实时信息带来源与时间、缺失数据明确标记。

---

## 已知限制

- 仅支持「石家庄出发、当天往返、自驾」，暂不做酒店/机票/支付/自动购票。
- 高德抓取的 POI 数据存在粒度与脏标问题（同一景区大量子条目、个别类别误标），已通过兴趣相关性过滤与出口门禁兜底，但仍会影响细粒度召回。
- 实时数据依赖外部服务；缺失时返回 `unknown`，不做猜测补全。
- `STORAGE_BACKEND=auto` 在数据库不可用时降级为进程内存，重启不保留会话；需要持久化请设为 `db`。
- 长期记忆目前使用结构化字段 + SQL 查询；自由文本规模变大后再考虑向量检索（仍按 `user_id` 过滤）。
- 未接入 LangGraph Postgres checkpointer：会话状态由 `chat_sessions` + `chat_messages` 负责，避免与图状态重复写入。

---
## License

本项目仅供学习与演示使用。所有实时数据版权归对应数据源所有，票价与开放时间以景区官方为准。
