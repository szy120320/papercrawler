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
    Column, DateTime, Integer, String, Text,
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
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DownloadDatabase:
    """封装 SQLite 下载历史操作"""

    def __init__(self, db_path: str):
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self._engine)

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
