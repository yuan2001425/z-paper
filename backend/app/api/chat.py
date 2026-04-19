"""
chat.py — 知识库对话 API

端点：
  POST   /api/v1/chat/sessions                        — 发消息（自动创建或续接会话）
  GET    /api/v1/chat/sessions                        — 列出所有会话
  GET    /api/v1/chat/sessions/{session_id}/messages  — 获取会话消息
  DELETE /api/v1/chat/sessions/{session_id}           — 删除会话
"""

import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.chat import ChatSession, ChatMessage
from app.services.chat_agent import run_chat_turn, run_chat_turn_stream

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


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

    # 运行 agent（可能耗时较长）
    result = run_chat_turn(
        user_message=req.message,
        history=session.history_json or [],
        compaction_summary=session.compaction_summary,
        session_id=session.id,
        compact_failures=session.compact_failures or 0,
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

    def sse_gen():
        final: dict = {}
        try:
            for event in run_chat_turn_stream(req.message, history, compaction_summary, session_id, compact_failures):
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
        {"id": s.id, "title": s.title, "updated_at": s.updated_at}
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
        "session":  {"id": session.id, "title": session.title},
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
