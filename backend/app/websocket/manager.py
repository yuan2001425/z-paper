import asyncio
import json
import logging
from collections import defaultdict
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """管理 WebSocket 连接，按 job_id 分组广播进度"""

    def __init__(self):
        self.connections: Dict[str, List[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, job_id: str):
        await websocket.accept()
        self.connections[job_id].append(websocket)
        logger.info(f"[WS] connected job={job_id}, total={len(self.connections[job_id])}")

    def disconnect(self, websocket: WebSocket, job_id: str):
        if job_id in self.connections:
            self.connections[job_id].discard(websocket) if hasattr(
                self.connections[job_id], "discard"
            ) else None
            try:
                self.connections[job_id].remove(websocket)
            except ValueError:
                pass

    async def broadcast(self, job_id: str, data: dict):
        """异步广播（在 async 上下文中调用）"""
        connections = self.connections.get(job_id, [])
        dead = []
        for ws in connections:
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self.connections[job_id].remove(ws)
            except ValueError:
                pass

    def broadcast_sync(self, job_id: str, data: dict):
        """同步广播（在 Celery Worker / 同步代码中调用）"""
        connections = self.connections.get(job_id, [])
        if not connections:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast(job_id, data))
            else:
                loop.run_until_complete(self.broadcast(job_id, data))
        except Exception as e:
            logger.debug(f"[WS] broadcast_sync failed (non-critical): {e}")


# 单例
ws_manager = WebSocketManager()
