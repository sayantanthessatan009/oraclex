"""
app/api/routes/health.py
Health check endpoint for Railway/Render uptime monitoring.
"""
from fastapi import APIRouter
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import HealthResponse
from app.services.scheduler import scheduler

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health_check():
    settings = get_settings()

    groq_ok = False
    supabase_ok = False
    cache_backend = "unknown"

    try:
        from app.core.database import get_supabase
        get_supabase().table("games").select("id").limit(1).execute()
        supabase_ok = True
    except Exception as e:
        log.warning("health.supabase.fail", error=str(e))

    try:
        from groq import AsyncGroq
        # Just instantiating the client is enough to validate the key format
        AsyncGroq(api_key=settings.groq_api_key)
        groq_ok = bool(settings.groq_api_key and settings.groq_api_key.startswith("gsk_"))
    except Exception:
        pass

    try:
        from app.core.cache import get_cache
        c = await get_cache()
        cache_backend = type(c).__name__
    except Exception:
        pass

    return HealthResponse(
        status="ok" if (supabase_ok and groq_ok) else "degraded",
        env=settings.app_env,
        groq_connected=groq_ok,
        supabase_connected=supabase_ok,
        cache_backend=cache_backend,
        scheduler_running=scheduler.running,
    )
