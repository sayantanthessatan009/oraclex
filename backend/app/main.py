"""
app/main.py
FastAPI application entry point — registers routers, middleware, lifespan.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.scheduler import start_scheduler, stop_scheduler

setup_logging()
log = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    log.info("oraclex.startup", env=settings.app_env)

    # Warm up cache
    from app.core.cache import get_cache
    cache = await get_cache()
    log.info("oraclex.cache.ready", backend=type(cache).__name__)

    # Start background scheduler
    start_scheduler()

    yield  # ← app runs here

    # Shutdown
    stop_scheduler()
    log.info("oraclex.shutdown")


app = FastAPI(
    title="OracleX API",
    description="AI-powered sports prediction platform — Groq streaming + Supabase",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("unhandled.exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "path": str(request.url.path)},
    )

# ── Routers ───────────────────────────────────────────────────────────────────
from app.api.routes import games, odds, predictions, sentiment, favorites, health  # noqa: E402

app.include_router(health.router)
app.include_router(games.router, prefix="/api/v1")
app.include_router(odds.router, prefix="/api/v1")
app.include_router(predictions.router, prefix="/api/v1")
app.include_router(sentiment.router, prefix="/api/v1")
app.include_router(favorites.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "name": "OracleX",
        "tagline": "The story before it happens",
        "version": "1.0.0",
        "docs": "/docs",
    }
