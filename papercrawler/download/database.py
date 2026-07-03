"""
SQLite 下载历史数据库
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import (
    Column, DateTime, Integer, String, Text, text,
    create_engine, select, update,
)
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class PaperRecord(Base):
    __tablename__ = "papers"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    doi            = Column(String(256), unique=True, nullable=True)
    hash_id        = Column(String(64), nullable=True)
    title          = Column(Text, nullable=False)
    authors        = Column(Text, nullable=True)   # JSON 字符串
    year           = Column(Integer, nullable=True)
    journal        = Column(Text, nullable=True)
    access_status  = Column(String(32), nullable=True)
    download_status = Column(String(16), nullable=False, default="pending")
    output_dir     = Column(Text, nullable=True)
    error_msg      = Column(Text, nullable=True)
    download_run   = Column(String(128), nullable=True)  # 首次下载所在 run 的名称(用于跨 run 跳过提示)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def _migrate_schema(engine) -> None:
    """
    幂等 schema 迁移:对已存在的表加新列(若不存在)。

    SQLAlchemy 的 create_all() 只创建不存在的表,不会给已存在表加列。
    这里手动处理,保证升级平滑。
    """
    with engine.begin() as conn:
        # 检查 papers 表是否存在
        rs = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
        )).fetchone()
        if not rs:
            return  # 新 DB,create_all 已处理

        # 已存在的列名
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(papers)"))]

        if "download_run" not in cols:
            logger.info("[db-migrate] 添加 download_run 列")
            conn.execute(text("ALTER TABLE papers ADD COLUMN download_run VARCHAR(128)"))


class DownloadDatabase:
    """封装 SQLite 下载历史操作"""

    def __init__(self, db_path: str):
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self._engine)
        _migrate_schema(self._engine)

    def upsert(
        self,
        doi: Optional[str],
        hash_id: str,
        title: str,
        authors: list[str],
        year: Optional[int],
        journal: Optional[str],
        access_status: str,
        download_status: str,
        output_dir: str,
        error_msg: Optional[str] = None,
        download_run: Optional[str] = None,
    ) -> None:
        with Session(self._engine) as session:
            # 先查
            existing = None
            if doi:
                existing = session.execute(
                    select(PaperRecord).where(PaperRecord.doi == doi)
                ).scalar_one_or_none()
            if not existing:
                existing = session.execute(
                    select(PaperRecord).where(PaperRecord.hash_id == hash_id)
                ).scalar_one_or_none()

            if existing:
                existing.download_status = download_status
                existing.access_status = access_status
                existing.output_dir = output_dir
                existing.error_msg = error_msg
                # 首次记录 run name,后续不覆盖
                if download_run and not existing.download_run:
                    existing.download_run = download_run
                existing.updated_at = datetime.utcnow()
            else:
                record = PaperRecord(
                    doi=doi,
                    hash_id=hash_id,
                    title=title,
                    authors=json.dumps(authors, ensure_ascii=False),
                    year=year,
                    journal=journal,
                    access_status=access_status,
                    download_status=download_status,
                    output_dir=output_dir,
                    error_msg=error_msg,
                    download_run=download_run,
                )
                session.add(record)
            session.commit()

    def is_downloaded(self, doi: Optional[str], hash_id: str) -> bool:
        """检查是否已成功下载"""
        with Session(self._engine) as session:
            if doi:
                rec = session.execute(
                    select(PaperRecord).where(
                        PaperRecord.doi == doi,
                        PaperRecord.download_status == "success",
                    )
                ).scalar_one_or_none()
                if rec:
                    return True
            rec = session.execute(
                select(PaperRecord).where(
                    PaperRecord.hash_id == hash_id,
                    PaperRecord.download_status == "success",
                )
            ).scalar_one_or_none()
            return rec is not None

    def find_existing(self, doi: Optional[str], hash_id: str) -> Optional[PaperRecord]:
        """查找是否已存在(返回完整记录,含 download_run / output_dir)"""
        with Session(self._engine) as session:
            if doi:
                rec = session.execute(
                    select(PaperRecord).where(
                        PaperRecord.doi == doi,
                        PaperRecord.download_status == "success",
                    )
                ).scalar_one_or_none()
                if rec:
                    return rec
            return session.execute(
                select(PaperRecord).where(
                    PaperRecord.hash_id == hash_id,
                    PaperRecord.download_status == "success",
                )
            ).scalar_one_or_none()

    def list_records(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        with Session(self._engine) as session:
            stmt = select(PaperRecord)
            if status:
                stmt = stmt.where(PaperRecord.download_status == status)
            stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "doi": r.doi,
                    "title": r.title,
                    "year": r.year,
                    "journal": r.journal,
                    "access_status": r.access_status,
                    "download_status": r.download_status,
                    "output_dir": r.output_dir,
                    "error_msg": r.error_msg,
                    "download_run": r.download_run,
                    "created_at": str(r.created_at),
                }
                for r in rows
            ]

    def stats(self) -> dict:
        """统计各状态数量"""
        from sqlalchemy import func
        with Session(self._engine) as session:
            rows = session.execute(
                select(PaperRecord.download_status, func.count())
                .group_by(PaperRecord.download_status)
            ).all()
            return {status: count for status, count in rows}
