"""
reset_db.py — 清空并重建数据库（SQLite）

用法：
  python reset_db.py

数据库文件位于 data/zpaper.db（自动创建）
"""
import sys
import os

# 确保能找到 app 包
sys.path.insert(0, os.path.dirname(__file__))

from app.database import Base, engine
from app.models import *  # noqa: F401,F403 — 触发所有模型注册

def reset():
    print("正在删除所有表...")
    Base.metadata.drop_all(bind=engine)
    print("正在重建所有表...")
    Base.metadata.create_all(bind=engine)
    tables = sorted(Base.metadata.tables.keys())
    print(f"完成，共 {len(tables)} 张表：")
    for t in tables:
        print(f"  [ok] {t}")

if __name__ == "__main__":
    confirm = input("此操作将清空所有数据，确认继续？(y/N) ").strip().lower()
    if confirm == "y":
        reset()
    else:
        print("已取消。")
