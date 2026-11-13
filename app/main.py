"""TravelMind 石游助手 - FastAPI 入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.memory import router as memory_router
from app.api.routes import router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# 第三方 HTTP 客户端默认会把含 key= 的完整 URL 打进日志，存在密钥泄露风险
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("travelmind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auth_secret_is_default:
        logger.warning(
            "AUTH_SECRET 使用默认值，仅适合本地开发；部署前请通过环境变量设置随机密钥。"
        )
    yield


app = FastAPI(title="TravelMind 石游助手", version="0.2.0", lifespan=lifespan)

# 浏览器需要携带 HttpOnly Cookie，因此必须允许 credentials 并显式列出可信来源。
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(memory_router)
app.include_router(router)
