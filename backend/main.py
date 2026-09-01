"""CodeAtlas API entry point. Run with: uvicorn backend.main:app --reload"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.analyze import router as analyze_router
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "codeatlas"}
