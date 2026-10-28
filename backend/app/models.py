"""SQLAlchemy models. One table: applications.

(client_id, job_id) is UNIQUE so duplicates are impossible even under races.
Job title/company are snapshotted so history survives dataset regeneration.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

STATUS_EXTERNAL_REQUIRED = "external_application_required"
STATUS_APPLIED_BY_USER = "applied_by_user"
VALID_STATUSES = {STATUS_EXTERNAL_REQUIRED, STATUS_APPLIED_BY_USER}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("client_id", "job_id", name="uq_client_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    job_title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=STATUS_EXTERNAL_REQUIRED)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
