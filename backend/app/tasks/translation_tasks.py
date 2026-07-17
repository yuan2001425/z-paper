"""
translation_tasks.py - async task entrypoints.

The project does not use Celery. API handlers schedule these functions with
asyncio.create_task, and the synchronous pipelines run in a worker thread.
"""
import asyncio
import logging

from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 3
_pipeline_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


async def _refresh_parent_if_needed(job_id: str):
    try:
        from app.database import SessionLocal
        from app.models.job import TranslationJob
        from app.services.long_document import refresh_parent_job

        with SessionLocal() as db:
            job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            parent_job_id = job.parent_job_id if job else None
        if parent_job_id:
            await asyncio.to_thread(refresh_parent_job, parent_job_id)
    except Exception:
        logger.exception("[translation_tasks] failed to refresh long-document parent")


async def start_translation(job_id: str, storage_key: str):
    """Start the foreign-language translation pipeline."""
    async with _pipeline_sem:
        try:
            pdf_bytes = local_storage.get_object(storage_key)
            from app.services.pipeline import run_phase_a_b
            await asyncio.to_thread(run_phase_a_b, job_id, pdf_bytes)
        except Exception as e:
            logger.error("[translation_tasks] job=%s failed: %s", job_id, e, exc_info=True)
        finally:
            await _refresh_parent_if_needed(job_id)


async def start_archiving(job_id: str, storage_key: str):
    """Start the Chinese-paper archive pipeline."""
    async with _pipeline_sem:
        try:
            pdf_bytes = local_storage.get_object(storage_key)
            from app.services.pipeline import run_chinese_pipeline
            await asyncio.to_thread(run_chinese_pipeline, job_id, pdf_bytes)
        except Exception as e:
            logger.error("[translation_tasks] archive job=%s failed: %s", job_id, e, exc_info=True)
        finally:
            await _refresh_parent_if_needed(job_id)
