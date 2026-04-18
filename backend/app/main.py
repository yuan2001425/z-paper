import asyncio
import json
from pathlib import Path

from app.logging_config import setup_logging
setup_logging("api")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings, load_db_config
from app.api import papers, jobs, results, glossary, domain_glossary, chat
from app.api import settings as settings_api

app = FastAPI(title="z-paper API", version="0.2.0")

# 开发模式（npm run dev）时前端跑在 :3000，需要允许跨域
# 生产模式（前端已 build）时同源，CORS 无关紧要但保留不影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
os.makedirs(settings.LOCAL_UPLOAD_PATH, exist_ok=True)

# 确保新表（如 chat_sessions/chat_messages）在启动时自动创建
from app.database import Base, engine
from app import models as _models  # noqa: F401 — 触发所有模型注册
Base.metadata.create_all(bind=engine)
load_db_config()   # 用 DB 里保存的 key 覆盖内存配置（.env 缺失时依然生效）
app.mount("/uploads", StaticFiles(directory=settings.LOCAL_UPLOAD_PATH), name="uploads")

app.include_router(papers.router, prefix="/api/v1/papers", tags=["papers"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(results.router, prefix="/api/v1/results", tags=["results"])
app.include_router(glossary.router, prefix="/api/v1/glossary", tags=["glossary"])
app.include_router(domain_glossary.router, prefix="/api/v1/domain-glossary", tags=["domain-glossary"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(settings_api.router, prefix="/api/v1/settings", tags=["settings"])


_TERMINAL_STATUSES = {"completed", "failed", "waiting_term_review"}


@app.websocket("/ws/jobs/{job_id}")
async def websocket_job_progress(websocket: WebSocket, job_id: str):
    from app.database import SessionLocal
    from app.models.job import TranslationJob

    await websocket.accept()
    last_snapshot = None
    try:
        while True:
            with SessionLocal() as db:
                job = db.query(TranslationJob).filter(TranslationJob.id == job_id).first()
            if job is None:
                break

            snapshot = (job.status, job.progress, job.current_stage)
            if snapshot != last_snapshot:
                payload = {
                    "stage": job.status,
                    "progress": job.progress,
                    "message": job.current_stage or job.status,
                }
                if job.status == "completed":
                    from app.models.result import TranslationResult
                    with SessionLocal() as db:
                        r = db.query(TranslationResult).filter(
                            TranslationResult.job_id == job_id
                        ).first()
                    if r:
                        payload["result_id"] = r.id
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                last_snapshot = snapshot

            if job.status in _TERMINAL_STATUSES:
                break

            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ── 前端静态文件（生产模式）────────────────────────────────────────────────────
# 只在 frontend/dist/ 存在时挂载（开发模式下用 npm run dev，无需此步）
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
