"""CodeAtlas API entry point. Run with: uvicorn backend.main:app --reload"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.analyze import router as analyze_router
from backend.api.chat import router as chat_router
from backend.api.search import router as search_router
from backend.api.security import router as security_router
from backend.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="CodeAtlas API",
    version="0.1.0",
    description="Local-first AI-powered code intelligence. Understand. Secure. Onboard.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze_router, prefix="/api", tags=["analysis"])
app.include_router(search_router, prefix="/api", tags=["retrieval"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(security_router, prefix="/api", tags=["security"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "codeatlas"}
