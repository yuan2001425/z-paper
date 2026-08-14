import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.config import load_db_config, settings
from app.database import Base, SessionLocal, engine
from app.models.job import JobStatus, TranslationJob

router = APIRouter()

SYNC_APP_VERSION = "2.2.0"
SYNC_TTL_MINUTES = 15
DISCOVERY_PORT = 37621
_pending_requests: dict[str, dict] = {}
_export_tokens: dict[str, dict] = {}
_sync_operations: dict[str, dict] = {}
_discoverable_until: Optional[datetime] = None
_discovery_name = socket.gethostname()
_device_id = uuid.uuid4().hex
_discovery_started = False
_discovery_lock = threading.Lock()
logger = logging.getLogger(__name__)


class OutboundSyncRequest(BaseModel):
    target_base_url: str
    source_base_url: Optional[str] = None
    source_name: Optional[str] = None


class IncomingSyncRequest(BaseModel):
    request_id: str
    source_base_url: str
    source_name: str
    source_version: str = SYNC_APP_VERSION
    token: str
    created_at: str
    expires_at: str


class EnableDiscoveryRequest(BaseModel):
    device_name: Optional[str] = None
    minutes: int = 10


class ScanDiscoveryRequest(BaseModel):
    seconds: float = 3.0


class ExportSelection(BaseModel):
    rows: dict[str, list[list]] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.utcnow()


def _expires_at() -> datetime:
    return _now() + timedelta(minutes=SYNC_TTL_MINUTES)


def _cleanup_expired() -> None:
    now = _now()
    for store in (_pending_requests, _export_tokens):
        for key, value in list(store.items()):
            if value["expires_at"] < now:
                store.pop(key, None)
    for key, value in list(_sync_operations.items()):
        created_at = value.get("created_at") or now
        if created_at < now - timedelta(hours=2):
            _sync_operations.pop(key, None)


def _normalize_base_url(value: str) -> str:
    base = (value or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="请输入对方设备地址")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}"
    return base


def _db_path() -> Path:
    return Path(settings.DATABASE_URL.replace("sqlite:///", "")).resolve()


def _data_dir() -> Path:
    return _db_path().parent


def _uploads_dir() -> Path:
    return Path(settings.LOCAL_UPLOAD_PATH).resolve()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_upload_target(rel_path: str) -> Path:
    uploads_root = _uploads_dir()
    target = (uploads_root / rel_path).resolve()
    try:
        target.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="同步文件路径非法") from exc
    return target


def _short_paths(paths: list[str], limit: int = 5) -> str:
    shown = paths[:limit]
    suffix = "" if len(paths) <= limit else f" 等 {len(paths)} 个"
    return "、".join(shown) + suffix


def _active_job_count() -> int:
    active = {
        JobStatus.PENDING,
        JobStatus.PARSING,
        JobStatus.POLISHING,
        JobStatus.TRANSLATING,
        "image_translating",
    }
    with SessionLocal() as db:
        return db.query(TranslationJob).filter(TranslationJob.status.in_(active)).count()


def _operation(operation_id: str) -> dict:
    item = _sync_operations[operation_id]
    return {
        **item,
        "created_at": item["created_at"].isoformat() + "Z",
        "updated_at": item["updated_at"].isoformat() + "Z",
    }


def _new_operation(title: str) -> str:
    _cleanup_expired()
    operation_id = str(uuid.uuid4())
    now = _now()
    _sync_operations[operation_id] = {
        "id": operation_id,
        "title": title,
        "status": "running",
        "progress": 1,
        "logs": [],
        "error": None,
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    return operation_id


def _set_operation(operation_id: str, *, progress: Optional[int] = None, status: Optional[str] = None,
                   message: Optional[str] = None, error: Optional[str] = None, result: Optional[dict] = None) -> None:
    item = _sync_operations.get(operation_id)
    if not item:
        return
    if progress is not None:
        item["progress"] = max(0, min(100, progress))
    if status is not None:
        item["status"] = status
    if error is not None:
        item["error"] = error
    if result is not None:
        item["result"] = result
    if message:
        item["logs"].append({"time": _now().isoformat() + "Z", "message": message})
        item["logs"] = item["logs"][-120:]
    item["updated_at"] = _now()


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(row[0] for row in rows)


def _pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk_rows = [(row[5], row[1]) for row in rows if row[5]]
    return [name for _, name in sorted(pk_rows)]


def _identity(values: list) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _source_manifest() -> dict:
    db_path = _db_path()
    tables: dict[str, dict] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _table_names(conn):
            pk_cols = _pk_columns(conn, table)
            if not pk_cols:
                continue
            cols = ", ".join(f'"{col}"' for col in pk_cols)
            rows = [list(row) for row in conn.execute(f'SELECT {cols} FROM "{table}"').fetchall()]
            tables[table] = {"pk": pk_cols, "rows": rows}
    finally:
        conn.close()

    files = []
    uploads_root = _uploads_dir()
    if uploads_root.exists():
        for file_path in uploads_root.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(uploads_root).as_posix()
                stat = file_path.stat()
                files.append({
                    "path": rel_path,
                    "size": stat.st_size,
                    "sha256": _file_sha256(file_path),
                })

    return {
        "app": "z-paper",
        "version": SYNC_APP_VERSION,
        "generated_at": _now().isoformat() + "Z",
        "device_name": socket.gethostname(),
        "tables": tables,
        "files": files,
    }


def _target_delta_selection(source_manifest: dict) -> tuple[ExportSelection, dict]:
    if source_manifest.get("version") != SYNC_APP_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"版本不一致：来源为 {source_manifest.get('version') or '未知'}，本机为 {SYNC_APP_VERSION}",
        )

    requested_rows: dict[str, list[list]] = {}
    conn = sqlite3.connect(str(_db_path()))
    try:
        target_tables = set(_table_names(conn))
        for table, meta in (source_manifest.get("tables") or {}).items():
            pk_cols = meta.get("pk") or []
            source_rows = meta.get("rows") or []
            if not pk_cols or not source_rows:
                continue
            if table == "app_config":
                requested_rows[table] = source_rows
                continue
            if table not in target_tables:
                requested_rows[table] = source_rows
                continue
            cols = ", ".join(f'"{col}"' for col in pk_cols)
            target_keys = {
                _identity(list(row))
                for row in conn.execute(f'SELECT {cols} FROM "{table}"').fetchall()
            }
            missing = [row for row in source_rows if _identity(row) not in target_keys]
            if missing:
                requested_rows[table] = missing
    finally:
        conn.close()

    requested_files = []
    conflict_files: list[str] = []
    for item in source_manifest.get("files") or []:
        rel_path = item.get("path")
        size = item.get("size")
        if not rel_path:
            continue
        target = _safe_upload_target(rel_path)
        source_hash = item.get("sha256") or ""
        if not target.exists():
            requested_files.append(rel_path)
            continue
        if not target.is_file():
            conflict_files.append(rel_path)
            continue
        if target.stat().st_size != size:
            conflict_files.append(rel_path)
            continue
        if source_hash and _file_sha256(target) != source_hash:
            conflict_files.append(rel_path)

    selection = ExportSelection(rows=requested_rows, files=requested_files)
    row_count = sum(len(rows) for rows in requested_rows.values())
    requested_file_set = set(requested_files)
    file_size = sum((item.get("size") or 0) for item in source_manifest.get("files") or [] if item.get("path") in requested_file_set)
    return selection, {
        "row_count": row_count,
        "file_count": len(requested_files),
        "file_size": file_size,
        "table_count": len(requested_rows),
        "conflict_count": len(conflict_files),
        "conflicts": conflict_files[:20],
    }


def _is_usable_lan_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.version == 4
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def _add_lan_ip(ips: set[str], value: str) -> None:
    if value and _is_usable_lan_ip(value):
        ips.add(value)


def _windows_adapter_ips() -> list[str]:
    if not sys.platform.startswith("win"):
        return []
    ips: set[str] = set()
    commands = [
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -ExpandProperty IPAddress",
        ],
        ["ipconfig"],
    ]
    for command in commands:
        try:
            output_bytes = subprocess.check_output(command, stderr=subprocess.DEVNULL, timeout=3)
        except Exception:
            continue

        # Windows command output follows the active code page on many Chinese
        # systems. Decode defensively because we only need ASCII IPv4 numbers.
        output = output_bytes.decode("ascii", errors="ignore")
        for line in output.splitlines():
            if command[0].lower() == "ipconfig" and "IPv4" not in line:
                continue
            for match in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
                _add_lan_ip(ips, match)
    return sorted(ips, key=lambda value: ipaddress.ip_address(value))


def _lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            _add_lan_ip(ips, info[4][0])
    except Exception:
        pass
    for remote in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((remote, 80))
                _add_lan_ip(ips, sock.getsockname()[0])
        except Exception:
            pass
    for ip in _windows_adapter_ips():
        _add_lan_ip(ips, ip)
    return sorted(ips, key=lambda value: ipaddress.ip_address(value))


def _is_loopback_base_url(base_url: str) -> bool:
    try:
        hostname = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _broadcast_targets() -> list[str]:
    targets = {"255.255.255.255"}
    for ip in _lan_ips():
        parts = ip.split(".")
        if len(parts) == 4:
            parts[-1] = "255"
            targets.add(".".join(parts))
    return sorted(targets)


def _discovery_payload() -> bytes:
    payload = {
        "type": "z-paper-device",
        "device_id": _device_id,
        "device_name": _discovery_name,
        "version": SYNC_APP_VERSION,
        "lan_ips": _lan_ips(),
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _discovery_loop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(1.0)
    except OSError as exc:
        logger.warning("sync discovery listener failed: %s", exc)
        return

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            message = json.loads(data.decode("utf-8", errors="ignore"))
            if message.get("type") != "z-paper-discover":
                continue
            if message.get("device_id") == _device_id:
                continue
            if not _discoverable_until or _discoverable_until < _now():
                continue
            sock.sendto(_discovery_payload(), addr)
        except socket.timeout:
            continue
        except Exception as exc:
            logger.debug("sync discovery packet ignored: %s", exc)


def _ensure_discovery_listener() -> None:
    global _discovery_started
    with _discovery_lock:
        if _discovery_started:
            return
        thread = threading.Thread(target=_discovery_loop, name="zpaper-sync-discovery", daemon=True)
        thread.start()
        _discovery_started = True


def _scan_devices(seconds: float) -> list[dict]:
    _ensure_discovery_listener()
    payload = json.dumps({
        "type": "z-paper-discover",
        "device_id": _device_id,
        "version": SYNC_APP_VERSION,
        "nonce": secrets.token_urlsafe(8),
    }).encode("utf-8")
    devices: dict[str, dict] = {}
    deadline = time.time() + max(1.0, min(seconds, 10.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        for target in _broadcast_targets():
            try:
                sock.sendto(payload, (target, DISCOVERY_PORT))
            except OSError:
                pass
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                item = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if item.get("type") != "z-paper-device":
                continue
            if item.get("device_id") == _device_id:
                continue
            base_url = f"http://{addr[0]}:8000"
            devices[base_url] = {
                "device_id": item.get("device_id") or "",
                "device_name": item.get("device_name") or addr[0],
                "base_url": base_url,
                "version": item.get("version") or "",
                "same_version": item.get("version") == SYNC_APP_VERSION,
                "ip": addr[0],
            }
    finally:
        sock.close()
    return sorted(devices.values(), key=lambda x: (not x["same_version"], x["device_name"]))


def _safe_extract(zip_path: Path, target_dir: Path) -> None:
    root = target_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            destination = (root / member.filename).resolve()
            try:
                destination.relative_to(root)
            except ValueError:
                raise HTTPException(status_code=400, detail="同步包包含非法路径，已拒绝导入")
        archive.extractall(root)


def _create_db_snapshot(snapshot_path: Path) -> None:
    source = sqlite3.connect(str(_db_path()))
    target = sqlite3.connect(str(snapshot_path))
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()


def _copy_selected_rows(delta_db: Path, selection: ExportSelection) -> int:
    source = sqlite3.connect(str(_db_path()))
    target = sqlite3.connect(str(delta_db))
    copied = 0
    try:
        for table, identities in selection.rows.items():
            if not identities:
                continue
            table_row = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not table_row:
                continue
            target.execute(table_row[0])
            pk_cols = _pk_columns(source, table)
            if not pk_cols:
                continue
            columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()]
            select_cols = ", ".join(f'"{col}"' for col in columns)
            placeholders = " AND ".join(f'"{col}" = ?' for col in pk_cols)
            insert_sql = (
                f'INSERT INTO "{table}" ({select_cols}) '
                f'VALUES ({", ".join(["?"] * len(columns))})'
            )
            for values in identities:
                row = source.execute(
                    f'SELECT {select_cols} FROM "{table}" WHERE {placeholders}',
                    tuple(values),
                ).fetchone()
                if row:
                    target.execute(insert_sql, tuple(row))
                    copied += 1
        target.commit()
    finally:
        target.close()
        source.close()
    return copied


def _safe_upload_source(rel_path: str) -> Path:
    uploads_root = _uploads_dir()
    target = (uploads_root / rel_path).resolve()
    try:
        target.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="同步文件路径非法") from exc
    return target


def _create_bundle(request_id: str, selection: ExportSelection) -> tuple[Path, Path]:
    if _active_job_count() > 0:
        raise HTTPException(status_code=409, detail="当前有识别或翻译任务正在运行，请完成后再同步")

    temp_dir = Path(tempfile.mkdtemp(prefix="zpaper_sync_export_"))
    delta_db_path = temp_dir / "zpaper-delta.db"
    zip_path = temp_dir / f"zpaper-sync-{request_id}.zip"
    copied_rows = _copy_selected_rows(delta_db_path, selection)

    upload_files = []
    seen_files: set[str] = set()
    for rel_path in selection.files:
        if rel_path in seen_files:
            continue
        seen_files.add(rel_path)
        source = _safe_upload_source(rel_path)
        if source.exists() and source.is_file():
            stat = source.stat()
            upload_files.append({
                "path": rel_path,
                "source": source,
                "size": stat.st_size,
                "sha256": _file_sha256(source),
            })

    manifest = {
        "app": "z-paper",
        "kind": "wlan-sync-bundle",
        "bundle_version": 2,
        "app_version": SYNC_APP_VERSION,
        "request_id": request_id,
        "generated_at": _now().isoformat() + "Z",
        "hostname": socket.gethostname(),
        "row_count": copied_rows,
        "upload_count": len(upload_files),
        "upload_bytes": sum(item["size"] for item in upload_files),
        "uploads": [
            {"path": item["path"], "size": item["size"], "sha256": item["sha256"]}
            for item in upload_files
        ],
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.write(delta_db_path, "data/zpaper-delta.db")
        for item in upload_files:
            archive.write(item["source"], f"uploads/{item['path']}")

    return zip_path, temp_dir


def _replace_current_data(extract_dir: Path) -> dict:
    if _active_job_count() > 0:
        raise HTTPException(status_code=409, detail="当前有识别或翻译任务正在运行，请完成后再导入")

    manifest_path = extract_dir / "manifest.json"
    incoming_db = extract_dir / "data" / "zpaper-delta.db"
    incoming_uploads = extract_dir / "uploads"
    if not manifest_path.exists() or not incoming_db.exists():
        raise HTTPException(status_code=400, detail="同步包不完整，无法导入")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("app") != "z-paper" or manifest.get("kind") != "wlan-sync-bundle":
        raise HTTPException(status_code=400, detail="这不是 z-paper 同步包")
    if manifest.get("app_version") != SYNC_APP_VERSION:
        raise HTTPException(status_code=409, detail="同步包版本与本机版本不一致")

    backup_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    backup_root = _data_dir() / "sync_backups" / backup_name
    backup_root.mkdir(parents=True, exist_ok=True)

    db_path = _db_path()
    uploads_path = _uploads_dir()
    backup_db = backup_root / "zpaper.db"
    upload_manifest = {
        item.get("path"): item
        for item in manifest.get("uploads", [])
        if item.get("path")
    }

    engine.dispose()
    copied_files: list[Path] = []
    skipped_existing_files = 0
    try:
        if db_path.exists():
            _create_db_snapshot(backup_db)

        current = sqlite3.connect(str(db_path))
        delta = sqlite3.connect(str(incoming_db))
        try:
            for table in _table_names(delta):
                table_sql_row = delta.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not table_sql_row:
                    continue
                create_sql = table_sql_row[0].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
                current.execute(create_sql)
                columns = [row[1] for row in delta.execute(f'PRAGMA table_info("{table}")').fetchall()]
                if not columns:
                    continue
                select_cols = ", ".join(f'"{col}"' for col in columns)
                rows = delta.execute(f'SELECT {select_cols} FROM "{table}"').fetchall()
                if not rows:
                    continue
                insert_mode = "INSERT OR REPLACE" if table == "app_config" else "INSERT OR IGNORE"
                insert_sql = (
                    f'{insert_mode} INTO "{table}" ({select_cols}) '
                    f'VALUES ({", ".join(["?"] * len(columns))})'
                )
                current.executemany(insert_sql, rows)
            current.commit()
        finally:
            delta.close()
            current.close()

        uploads_path.mkdir(parents=True, exist_ok=True)
        if incoming_uploads.exists():
            for source in incoming_uploads.rglob("*"):
                if not source.is_file():
                    continue
                rel_path = source.relative_to(incoming_uploads).as_posix()
                destination = _safe_upload_target(rel_path)
                meta = upload_manifest.get(rel_path) or {}
                expected_size = meta.get("size")
                expected_hash = meta.get("sha256")
                source_hash = _file_sha256(source)
                if expected_size is not None and source.stat().st_size != expected_size:
                    raise HTTPException(status_code=400, detail=f"同步包文件大小校验失败：{rel_path}")
                if expected_hash and source_hash != expected_hash:
                    raise HTTPException(status_code=400, detail=f"同步包文件哈希校验失败：{rel_path}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if (
                        destination.is_file()
                        and destination.stat().st_size == source.stat().st_size
                        and _file_sha256(destination) == source_hash
                    ):
                        skipped_existing_files += 1
                        continue
                    raise HTTPException(
                        status_code=409,
                        detail=f"同步包中的文件与本机已有文件路径冲突，已停止导入且不会覆盖目标端：{rel_path}",
                    )
                copied_files.append(destination)
                shutil.copy2(source, destination)

        engine.dispose()
        Base.metadata.create_all(bind=engine)
        load_db_config()
        return {
            "ok": True,
            "backup_path": str(backup_root),
            "source": manifest.get("hostname") or "",
            "row_count": manifest.get("row_count", 0),
            "upload_count": manifest.get("upload_count", 0),
            "upload_bytes": manifest.get("upload_bytes", 0),
            "skipped_existing_files": skipped_existing_files,
        }
    except Exception:
        engine.dispose()
        if backup_db.exists():
            for suffix in ("", "-wal", "-shm"):
                current_db = Path(str(db_path) + suffix)
                if current_db.exists():
                    current_db.unlink()
            shutil.copy2(backup_db, db_path)
        for path in copied_files:
            if path.exists():
                path.unlink()
        engine.dispose()
        raise


async def _fetch_manifest(source_base_url: str, request_id: str, token: str) -> dict:
    url = f"{source_base_url}/api/v1/sync/manifest/{quote(request_id)}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params={"token": token})
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text or "拉取来源清单失败")
        return response.json()


async def _download_bundle(
    source_base_url: str,
    request_id: str,
    token: str,
    selection: ExportSelection,
    target_path: Path,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> None:
    url = f"{source_base_url}/api/v1/sync/export/{quote(request_id)}"
    timeout = httpx.Timeout(30.0, read=None, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, params={"token": token}, json=selection.model_dump()) as response:
            if response.status_code != 200:
                detail = await response.aread()
                message = detail.decode("utf-8", errors="ignore") or "拉取同步包失败"
                raise HTTPException(status_code=response.status_code, detail=message)
            total = int(response.headers.get("content-length") or "0")
            received = 0
            with target_path.open("wb") as fh:
                async for chunk in response.aiter_bytes():
                    fh.write(chunk)
                    received += len(chunk)
                    if total and progress_cb:
                        progress_cb(min(100, int(received / total * 100)))


@router.get("/device")
def get_device_info():
    _cleanup_expired()
    ips = _lan_ips()
    return {
        "device_id": _device_id,
        "device_name": socket.gethostname(),
        "version": SYNC_APP_VERSION,
        "discovery_port": DISCOVERY_PORT,
        "discoverable": bool(_discoverable_until and _discoverable_until > _now()),
        "discoverable_until": _discoverable_until.isoformat() + "Z" if _discoverable_until else None,
        "lan_ips": ips,
        "suggested_urls": [f"http://{ip}:8000" for ip in ips],
        "pending_count": len(_pending_requests),
    }


@router.post("/discovery/enable")
def enable_discovery(payload: EnableDiscoveryRequest):
    global _discoverable_until, _discovery_name
    _ensure_discovery_listener()
    minutes = max(1, min(payload.minutes or 10, 60))
    _discovery_name = (payload.device_name or socket.gethostname()).strip() or socket.gethostname()
    _discoverable_until = _now() + timedelta(minutes=minutes)
    ips = _lan_ips()
    return {
        "ok": True,
        "device_name": _discovery_name,
        "version": SYNC_APP_VERSION,
        "discovery_port": DISCOVERY_PORT,
        "discoverable_until": _discoverable_until.isoformat() + "Z",
        "lan_ips": ips,
        "suggested_urls": [f"http://{ip}:8000" for ip in ips],
    }


@router.post("/discovery/scan")
async def scan_discovery(payload: ScanDiscoveryRequest):
    devices = await asyncio.to_thread(_scan_devices, payload.seconds)
    return {
        "ok": True,
        "version": SYNC_APP_VERSION,
        "devices": devices,
    }


@router.post("/outbound-requests")
async def send_sync_request(payload: OutboundSyncRequest):
    _cleanup_expired()
    target_base_url = _normalize_base_url(payload.target_base_url)
    lan_ips = _lan_ips()
    default_source = f"http://{lan_ips[0]}:8000" if lan_ips else ""
    raw_source_base_url = payload.source_base_url or default_source
    if not raw_source_base_url:
        raise HTTPException(
            status_code=400,
            detail="未检测到本机局域网地址，请确认已连接 WLAN，并允许 TCP 8000 的局域网访问",
        )
    source_base_url = _normalize_base_url(raw_source_base_url)
    if _is_loopback_base_url(source_base_url) and not _is_loopback_base_url(target_base_url):
        raise HTTPException(
            status_code=400,
            detail="本机同步地址不能是 127.0.0.1 或 localhost，请选择 192.168.x.x 这类局域网地址",
        )
    request_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    expires_at = _expires_at()
    source_name = (payload.source_name or socket.gethostname()).strip() or socket.gethostname()

    _export_tokens[request_id] = {
        "token": token,
        "expires_at": expires_at,
        "target_base_url": target_base_url,
    }

    request_payload = {
        "request_id": request_id,
        "source_base_url": source_base_url,
        "source_name": source_name,
        "source_version": SYNC_APP_VERSION,
        "token": token,
        "created_at": _now().isoformat() + "Z",
        "expires_at": expires_at.isoformat() + "Z",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{target_base_url}/api/v1/sync/requests", json=request_payload)
            response.raise_for_status()
    except Exception as exc:
        _export_tokens.pop(request_id, None)
        raise HTTPException(status_code=400, detail=f"发送同步申请失败：{exc}") from exc

    return {"ok": True, "request_id": request_id, "expires_at": request_payload["expires_at"]}


@router.post("/requests")
def receive_sync_request(payload: IncomingSyncRequest):
    _cleanup_expired()
    if payload.source_version != SYNC_APP_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"版本不一致：来源为 {payload.source_version or '未知'}，本机为 {SYNC_APP_VERSION}",
        )
    try:
        expires_at = datetime.fromisoformat(payload.expires_at.replace("Z", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="同步申请时间格式无效") from exc
    if expires_at < _now():
        raise HTTPException(status_code=400, detail="同步申请已过期")

    _pending_requests[payload.request_id] = {
        "request_id": payload.request_id,
        "source_base_url": _normalize_base_url(payload.source_base_url),
        "source_name": payload.source_name,
        "source_version": payload.source_version,
        "token": payload.token,
        "created_at": payload.created_at,
        "expires_at": expires_at,
    }
    return {"ok": True}


@router.get("/requests")
def list_sync_requests():
    _cleanup_expired()
    return {
        "requests": [
            {
                **item,
                "expires_at": item["expires_at"].isoformat() + "Z",
            }
            for item in _pending_requests.values()
        ]
    }


@router.delete("/requests/{request_id}")
def decline_sync_request(request_id: str):
    _pending_requests.pop(request_id, None)
    return {"ok": True}


async def _run_accept_operation(operation_id: str, request_id: str) -> None:
    item = _pending_requests.get(request_id)
    if not item:
        _set_operation(operation_id, status="failed", progress=100, error="同步申请不存在或已过期")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="zpaper_sync_import_"))
    bundle_path = temp_dir / "bundle.zip"
    try:
        _set_operation(operation_id, progress=5, message="检查本机任务状态")
        if _active_job_count() > 0:
            raise HTTPException(status_code=409, detail="当前有识别或翻译任务正在运行，请完成后再导入")

        _set_operation(operation_id, progress=12, message="拉取来源设备数据清单")
        manifest = await _fetch_manifest(item["source_base_url"], request_id, item["token"])

        _set_operation(operation_id, progress=24, message="校验版本并计算差异")
        selection, stats = await asyncio.to_thread(_target_delta_selection, manifest)
        if stats.get("conflict_count"):
            _set_operation(
                operation_id,
                progress=30,
                message=f"发现 {stats['conflict_count']} 个同路径但内容不同的文件，已跳过且不会覆盖：{_short_paths(stats.get('conflicts') or [])}",
            )
        _set_operation(
            operation_id,
            progress=34,
            message=f"需要传输 {stats['row_count']} 条数据、{stats['file_count']} 个文件，约 {stats['file_size'] / 1024 / 1024:.1f} MB",
        )

        def on_download(percent: int) -> None:
            _set_operation(operation_id, progress=35 + int(percent * 0.4))

        _set_operation(operation_id, progress=36, message="下载差异同步包")
        await _download_bundle(item["source_base_url"], request_id, item["token"], selection, bundle_path, on_download)

        extract_dir = temp_dir / "bundle"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _set_operation(operation_id, progress=78, message="校验并解压同步包")
        _safe_extract(bundle_path, extract_dir)

        _set_operation(operation_id, progress=88, message="导入缺失数据并复制缺失文件")
        result = await asyncio.to_thread(_replace_current_data, extract_dir)
        result["delta"] = stats
        _pending_requests.pop(request_id, None)
        _set_operation(operation_id, status="completed", progress=100, message="同步完成", result=result)
    except HTTPException as exc:
        _set_operation(operation_id, status="failed", progress=100, error=str(exc.detail), message=f"同步失败：{exc.detail}")
    except Exception as exc:
        logger.exception("sync import failed")
        _set_operation(operation_id, status="failed", progress=100, error=str(exc), message=f"同步失败：{exc}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.post("/requests/{request_id}/accept")
async def accept_sync_request(request_id: str):
    _cleanup_expired()
    item = _pending_requests.get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="同步申请不存在或已过期")
    if _active_job_count() > 0:
        raise HTTPException(status_code=409, detail="当前有识别或翻译任务正在运行，请完成后再导入")

    operation_id = _new_operation(f"接收来自 {item.get('source_name') or '来源设备'} 的同步")
    asyncio.create_task(_run_accept_operation(operation_id, request_id))
    return {"ok": True, "operation_id": operation_id}


@router.get("/operations/{operation_id}")
def get_sync_operation(operation_id: str):
    _cleanup_expired()
    if operation_id not in _sync_operations:
        raise HTTPException(status_code=404, detail="同步操作不存在")
    return _operation(operation_id)


@router.get("/manifest/{request_id}")
def get_sync_manifest(request_id: str, token: str = Query(...)):
    _cleanup_expired()
    item = _export_tokens.get(request_id)
    if not item or not secrets.compare_digest(item["token"], token):
        raise HTTPException(status_code=403, detail="同步令牌无效或已过期")
    if _active_job_count() > 0:
        raise HTTPException(status_code=409, detail="当前有识别或翻译任务正在运行，请完成后再同步")
    return _source_manifest()


@router.post("/export/{request_id}")
def export_sync_bundle(request_id: str, selection: ExportSelection, token: str = Query(...)):
    _cleanup_expired()
    item = _export_tokens.get(request_id)
    if not item or not secrets.compare_digest(item["token"], token):
        raise HTTPException(status_code=403, detail="同步令牌无效或已过期")

    zip_path, temp_dir = _create_bundle(request_id, selection)
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"zpaper-sync-{request_id}.zip",
        background=BackgroundTask(lambda: shutil.rmtree(temp_dir, ignore_errors=True)),
    )
