from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.folder import VirtualFolder, PaperFolderMapping
from app.models.paper import Paper

router = APIRouter()


class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None


class FolderRename(BaseModel):
    name: str


class MovePapersRequest(BaseModel):
    paper_ids: List[str]
    folder_id: Optional[int] = None  # None = 移到未分类


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _get_folder_depth(folder_id: int, db: Session) -> int:
    depth = 1
    current_id = folder_id
    while True:
        row = db.query(VirtualFolder.parent_id).filter(VirtualFolder.id == current_id).first()
        if row is None or row[0] is None:
            break
        depth += 1
        current_id = row[0]
    return depth


def _collect_folder_ids(folder_id: int, db: Session) -> set:
    """递归收集一个文件夹及其所有子文件夹的 id。"""
    ids = {folder_id}
    children = db.query(VirtualFolder.id).filter(VirtualFolder.parent_id == folder_id).all()
    for (cid,) in children:
        ids |= _collect_folder_ids(cid, db)
    return ids


# ── 接口 ──────────────────────────────────────────────────────────────────────

@router.get("")
def list_folders(db: Session = Depends(get_db)):
    """返回所有文件夹（平铺，客户端自行构建树），附带每个文件夹的论文数量。"""
    folders = (
        db.query(VirtualFolder)
        .order_by(VirtualFolder.parent_id.nulls_first(), VirtualFolder.name)
        .all()
    )

    # 每个文件夹的论文数（仅直接关联，不包含子文件夹）
    counts = dict(
        db.query(PaperFolderMapping.folder_id, func.count())
        .group_by(PaperFolderMapping.folder_id)
        .all()
    )

    total = db.query(func.count(Paper.id)).scalar() or 0

    mapped_subq = db.query(PaperFolderMapping.paper_id).distinct()
    unclassified = db.query(func.count(Paper.id)).filter(~Paper.id.in_(mapped_subq)).scalar() or 0

    return {
        "folders": [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id,
                "paper_count": counts.get(f.id, 0),
            }
            for f in folders
        ],
        "total_papers": total,
        "unclassified_count": unclassified,
    }


@router.post("")
def create_folder(body: FolderCreate, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "文件夹名称不能为空")
    if body.parent_id is not None and _get_folder_depth(body.parent_id, db) >= 3:
        raise HTTPException(400, "最多支持三级文件夹")
    folder = VirtualFolder(name=name, parent_id=body.parent_id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "paper_count": 0}


@router.put("/{folder_id}")
def rename_folder(folder_id: int, body: FolderRename, db: Session = Depends(get_db)):
    folder = db.query(VirtualFolder).filter(VirtualFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    folder.name = body.name.strip()
    db.commit()
    return {"ok": True}


@router.delete("/{folder_id}")
def delete_folder(
    folder_id: int,
    strategy: str = Query(default="unclassified", description="unclassified | physical"),
    db: Session = Depends(get_db),
):
    """删除文件夹及其所有子文件夹。
    strategy=unclassified：论文保留，仅解除关联（论文变为未分类）。
    strategy=physical：同时从数据库和磁盘删除关联的论文文件。
    """
    folder = db.query(VirtualFolder).filter(VirtualFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")

    folder_ids = _collect_folder_ids(folder_id, db)

    if strategy == "physical":
        mappings = (
            db.query(PaperFolderMapping)
            .filter(PaperFolderMapping.folder_id.in_(folder_ids))
            .all()
        )
        paper_ids = {m.paper_id for m in mappings}
        from app.storage.local_storage import local_storage
        for pid in paper_ids:
            p = db.query(Paper).filter(Paper.id == pid).first()
            if p:
                try:
                    local_storage.delete_object(p.storage_key)
                except Exception:
                    pass
                db.delete(p)
        db.flush()

    # 显式删除映射行（SQLite 默认不启用外键 CASCADE，不能依赖自动级联）
    db.query(PaperFolderMapping).filter(
        PaperFolderMapping.folder_id.in_(folder_ids)
    ).delete(synchronize_session=False)

    db.query(VirtualFolder).filter(VirtualFolder.id.in_(folder_ids)).delete(
        synchronize_session=False
    )
    db.commit()
    return {"ok": True}


@router.put("/papers/move")
def move_papers(body: MovePapersRequest, db: Session = Depends(get_db)):
    """将若干论文移动到指定文件夹（folder_id=None 表示移到未分类）。"""
    if not body.paper_ids:
        return {"ok": True}

    # 先移除这些论文已有的所有文件夹关联
    db.query(PaperFolderMapping).filter(
        PaperFolderMapping.paper_id.in_(body.paper_ids)
    ).delete(synchronize_session=False)

    if body.folder_id is not None:
        folder = db.query(VirtualFolder).filter(VirtualFolder.id == body.folder_id).first()
        if not folder:
            raise HTTPException(404, "目标文件夹不存在")
        for pid in body.paper_ids:
            db.add(PaperFolderMapping(paper_id=pid, folder_id=body.folder_id))

    db.commit()
    return {"ok": True}
