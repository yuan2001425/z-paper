"""
chat.py — 知识库对话 API

端点：
  POST   /api/v1/chat/sessions                        — 发消息（自动创建或续接会话）
  GET    /api/v1/chat/sessions                        — 列出所有会话
  GET    /api/v1/chat/sessions/{session_id}/messages  — 获取会话消息
  DELETE /api/v1/chat/sessions/{session_id}           — 删除会话
"""

import io
import json
import logging
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, SessionLocal
from app.models.chat import ChatSession, ChatMessage
from app.services.chat_agent import run_chat_turn, run_chat_turn_stream, update_session_memory, extract_durable_memories
from app.services.chat_tools import resolve_scope_paper_ids

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    scope_folder_ids: Optional[list[int]] = None  # 搜索范围：选中的文件夹 id（None=沿用会话已有范围）


class ScopeRequest(BaseModel):
    scope_folder_ids: list[int]  # 空列表=全部论文


class RenameRequest(BaseModel):
    title: str


# ── 发送消息 ──────────────────────────────────────────────────────────────────

@router.post("/sessions")
def send_message(req: ChatRequest, db: Session = Depends(get_db)):
    # 获取或新建会话
    session = None
    if req.session_id:
        session = db.query(ChatSession).filter(ChatSession.id == req.session_id).first()
        if not session:
            raise HTTPException(404, "会话不存在")

    if not session:
        title = req.message[:40] + ("…" if len(req.message) > 40 else "")
        session = ChatSession(
            id=str(uuid.uuid4()),
            title=title,
            history_json=[],
            compaction_summary=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(session)
        db.flush()

    # 更新搜索范围（请求里带了才更新；否则沿用会话已有范围）
    if req.scope_folder_ids is not None:
        session.scope_folder_ids = req.scope_folder_ids

    # 保存用户消息
    db.add(ChatMessage(
        id=str(uuid.uuid4()),
        session_id=session.id,
        role="user",
        content=req.message,
        tool_calls_json=[],
        citations_json=[],
        created_at=datetime.utcnow(),
    ))

    # 解析搜索范围（文件夹 → 论文 ID 集合）
    scope_paper_ids = resolve_scope_paper_ids(session.scope_folder_ids or [])

    # 运行 agent（可能耗时较长）
    result = run_chat_turn(
        user_message=req.message,
        history=session.history_json or [],
        compaction_summary=session.compaction_summary,
        session_id=session.id,
        compact_failures=session.compact_failures or 0,
        session_memory=session.session_memory,
        scope_paper_ids=scope_paper_ids,
    )

    # 保存 AI 回复
    assistant_id = str(uuid.uuid4())
    db.add(ChatMessage(
        id=assistant_id,
        session_id=session.id,
        role="assistant",
        content=result["answer"],
        tool_calls_json=result["tool_calls"],
        citations_json=result["citations"],
        created_at=datetime.utcnow(),
    ))

    # 更新会话历史
    session.history_json    = result["new_history"]
    session.updated_at      = datetime.utcnow()
    session.compact_failures = result.get("compact_failures", 0)
    if result["compaction_summary"]:
        session.compaction_summary = result["compaction_summary"]
        session.auto_summary       = result["compaction_summary"]

    db.commit()

    # 增量更新会话研究笔记（事务外，避免长事务阻塞），解决「多问几句就忘」
    answer_text = result.get("answer", "")
    if answer_text:
        try:
            new_memory = update_session_memory(session.session_memory, req.message, answer_text, result.get("tool_calls"))
            if new_memory and new_memory != session.session_memory:
                with SessionLocal() as db2:
                    s = db2.query(ChatSession).filter(ChatSession.id == session.id).first()
                    if s:
                        s.session_memory = new_memory
                        s.updated_at = datetime.utcnow()
                        db2.commit()
            # 轮末提取跨会话持久记忆（事务外，best-effort；若本轮已 save_memory 则跳过）
            extract_durable_memories(req.message, answer_text, result.get("tool_calls"))
        except Exception as e:
            logger.warning(f"更新会话研究笔记失败: {e}")

    return {
        "session_id":  session.id,
        "message_id":  assistant_id,
        "answer":      result["answer"],
        "tool_calls":  result["tool_calls"],
        "citations":   result["citations"],
    }


# ── 流式发消息 ────────────────────────────────────────────────────────────────

@router.post("/sessions/stream")
def send_message_stream(req: ChatRequest):
    """SSE 流式接口：逐步推送工具调用事件和 answer 文本块"""

    # 预先同步：查找/创建会话，保存用户消息
    with SessionLocal() as db:
        session = None
        if req.session_id:
            session = db.query(ChatSession).filter(ChatSession.id == req.session_id).first()
            if not session:
                raise HTTPException(404, "会话不存在")

        if not session:
            title = req.message[:40] + ("…" if len(req.message) > 40 else "")
            session = ChatSession(
                id=str(uuid.uuid4()),
                title=title,
                history_json=[],
                compaction_summary=None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(session)
            db.flush()

        # 更新搜索范围（请求里带了才更新；否则沿用会话已有范围）
        if req.scope_folder_ids is not None:
            session.scope_folder_ids = req.scope_folder_ids

        db.add(ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session.id,
            role="user",
            content=req.message,
            tool_calls_json=[],
            citations_json=[],
            created_at=datetime.utcnow(),
        ))
        db.commit()

        session_id         = session.id
        history            = list(session.history_json or [])
        compaction_summary = session.compaction_summary
        compact_failures   = session.compact_failures or 0
        session_memory     = session.session_memory
        scope_paper_ids    = resolve_scope_paper_ids(session.scope_folder_ids or [])

    def sse_gen():
        final: dict = {}
        try:
            for event in run_chat_turn_stream(req.message, history, compaction_summary, session_id, compact_failures, session_memory, scope_paper_ids):
                if event["type"] == "done":
                    final = event
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return

        # 流结束后保存到 DB
        assistant_id = str(uuid.uuid4())
        try:
            with SessionLocal() as db:
                s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
                if s:
                    db.add(ChatMessage(
                        id=assistant_id,
                        session_id=session_id,
                        role="assistant",
                        content=final.get("answer", ""),
                        tool_calls_json=final.get("tool_calls", []),
                        citations_json=final.get("citations", []),
                        created_at=datetime.utcnow(),
                    ))
                    s.history_json       = final.get("new_history", history)
                    s.updated_at         = datetime.utcnow()
                    s.compact_failures   = final.get("compact_failures", 0)
                    if final.get("compaction_summary"):
                        s.compaction_summary = final["compaction_summary"]
                        s.auto_summary       = final["compaction_summary"]
                    db.commit()
        except Exception as e:
            pass  # DB 保存失败不影响客户端已收到的内容

        # 增量更新会话研究笔记（事务外，避免长事务阻塞），解决「多问几句就忘」
        answer_text = final.get("answer", "")
        if answer_text:
            try:
                new_memory = update_session_memory(session_memory, req.message, answer_text, final.get("tool_calls"))
                if new_memory and new_memory != session_memory:
                    with SessionLocal() as db:
                        s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
                        if s:
                            s.session_memory = new_memory
                            s.updated_at = datetime.utcnow()
                            db.commit()
                # 轮末提取跨会话持久记忆（事务外，best-effort；若本轮已 save_memory 则跳过）
                extract_durable_memories(req.message, answer_text, final.get("tool_calls"))
            except Exception as e:
                logger.warning(f"更新会话研究笔记失败: {e}")

        done_event = {
            "type":       "done",
            "session_id": session_id,
            "message_id": assistant_id,
            "citations":  final.get("citations", []),
        }
        yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── 会话列表 ──────────────────────────────────────────────────────────────────

@router.get("/sessions/list")
def list_sessions(db: Session = Depends(get_db)):
    sessions = (
        db.query(ChatSession)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
        .all()
    )
    return [
        {"id": s.id, "title": s.title, "updated_at": s.updated_at, "scope_folder_ids": s.scope_folder_ids or []}
        for s in sessions
    ]


# ── 获取消息历史 ──────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "会话不存在")

    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return {
        "session":  {"id": session.id, "title": session.title, "scope_folder_ids": session.scope_folder_ids or []},
        "messages": [
            {
                "id":         m.id,
                "role":       m.role,
                "content":    m.content,
                "tool_calls": m.tool_calls_json,
                "citations":  m.citations_json,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
    }


# ── 删除会话 ──────────────────────────────────────────────────────────────────

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "会话不存在")
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"ok": True}


# ── 导出会话（zip：chat.json + 关联图片）──────────────────────────────────────

_IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


@router.get("/sessions/{session_id}/export")
def export_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "会话不存在")

    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )

    uploads_root = Path(settings.LOCAL_UPLOAD_PATH)
    img_map = {}  # 原始相对路径 -> zip 内 arcname

    def _rewrite(content: str) -> str:
        def _repl(match):
            alt = match.group(1)
            url = match.group(2)
            if '/uploads/' not in url:
                return match.group(0)
            rel = url.split('/uploads/', 1)[1].split('?', 1)[0]
            if rel not in img_map:
                ext = Path(rel).suffix or '.jpg'
                img_map[rel] = f"images/img_{len(img_map) + 1:03d}{ext}"
            return f"![{alt}]({img_map[rel]})"
        return _IMG_RE.sub(_repl, content)

    data = {
        "title": session.title,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "messages": [
            {
                "role":       m.role,
                "content":    _rewrite(m.content or ""),
                "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
            }
            for m in msgs
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("chat.json", json.dumps(data, ensure_ascii=False, indent=2))
        for rel, arcname in img_map.items():
            src = uploads_root / rel
            if src.is_file():
                zf.write(src, arcname)
    buf.seek(0)

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', session.title or '新对话')
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')
    filename = f"{ts}_{safe_title}.zip"

    # 中文文件名需用 RFC 5987 的 filename* 编码，否则 latin-1 编码报错
    ascii_name = f"chat_{session_id[:8]}.zip"
    utf8_name = quote(filename)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{utf8_name}"
            )
        },
    )


# ── 重命名会话 ──────────────────────────────────────────────────────────────────

@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, payload: RenameRequest, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "会话不存在")
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    session.title = title
    session.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "title": title}


# ── 更新会话搜索范围 ────────────────────────────────────────────────────────────

@router.patch("/sessions/{session_id}/scope")
def update_scope(session_id: str, payload: ScopeRequest, db: Session = Depends(get_db)):
    """设置会话的搜索范围（选中的文件夹 id 列表，空列表=全部论文）。"""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "会话不存在")
    session.scope_folder_ids = payload.scope_folder_ids
    session.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "scope_folder_ids": payload.scope_folder_ids}
