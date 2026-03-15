"""
app/services/scheduler.py
APScheduler jobs — odds ingestion, sentiment, predictions, accuracy updates.
"""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler()


async def _job_ingest_odds():
    from app.services.odds_service import odds_service
    log.info("scheduler.odds.start")
    result = await odds_service.run_ingestion_cycle()
    log.info("scheduler.odds.done", result=result)


async def _job_update_accuracy():
    from app.services.prediction_service import prediction_service
    log.info("scheduler.accuracy.start")
    count = await prediction_service.update_accuracy()
    log.info("scheduler.accuracy.done", updated=count)


async def _job_generate_predictions():
    from app.services.prediction_service import prediction_service
    log.info("scheduler.predictions.start")
    result = await prediction_service.generate_batch_predictions()
    log.info("scheduler.predictions.done", result=result)


def start_scheduler():
    scheduler.add_job(
        _job_ingest_odds,
        trigger=IntervalTrigger(minutes=settings.odds_fetch_interval_minutes),
        id="ingest_odds",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        _job_update_accuracy,
        trigger=IntervalTrigger(minutes=15),
        id="update_accuracy",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        _job_generate_predictions,
        trigger=IntervalTrigger(minutes=settings.prediction_generate_interval_minutes),
        id="generate_predictions",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("scheduler.started", jobs=len(scheduler.get_jobs()))


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
