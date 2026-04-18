"""
translation_tasks.py — 翻译任务入口（无 Celery，直接运行）

由 api/papers.py 通过 asyncio.create_task 调用。
同一时间最多 MAX_CONCURRENT_JOBS 篇论文并行处理，多余的在信号量处排队等待。
"""
import asyncio
import logging
from app.storage.local_storage import local_storage

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 3
_pipeline_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


async def start_translation(job_id: str, storage_key: str):
    """异步启动翻译流水线（在线程池中运行同步流水线，不阻塞事件循环）"""
    async with _pipeline_sem:
        try:
            pdf_bytes = local_storage.get_object(storage_key)
            from app.services.pipeline import run_phase_a_b
            await asyncio.to_thread(run_phase_a_b, job_id, pdf_bytes)
        except Exception as e:
            logger.error(f"[translation_tasks] job={job_id} 失败: {e}", exc_info=True)


async def start_archiving(job_id: str, storage_key: str):
    """异步启动中文论文存档流水线"""
    async with _pipeline_sem:
        try:
            pdf_bytes = local_storage.get_object(storage_key)
            from app.services.pipeline import run_chinese_pipeline
            await asyncio.to_thread(run_chinese_pipeline, job_id, pdf_bytes)
        except Exception as e:
            logger.error(f"[translation_tasks] archive job={job_id} 失败: {e}", exc_info=True)
