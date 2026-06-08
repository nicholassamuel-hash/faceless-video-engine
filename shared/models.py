"""SQLAlchemy ORM models: Job, ContentItem, QueueEntry, UploadLog.

Lifecycle status flow for a QueueEntry:

    generated -> approved  -> uploaded
              -> rejected
    (any)     -> failed

Only ``approved`` entries are eligible for upload.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class QueueStatus(str, enum.Enum):
    generated = "generated"
    approved = "approved"
    rejected = "rejected"
    uploaded = "uploaded"
    failed = "failed"


class Platform(str, enum.Enum):
    tiktok = "tiktok"
    youtube = "youtube"


class UploadResult(str, enum.Enum):
    success = "success"
    failed = "failed"
    skipped = "skipped"


class Job(Base):
    """One end-to-end generation run for a single video."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(8), default="id")
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus), default=JobStatus.pending, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    assets: Mapped[list["Asset"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    content_item: Mapped["ContentItem | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class Asset(Base):
    """An intermediate file produced for a job (audio, image, clip, subs)."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # audio|image|clip|subtitles|video
    path: Mapped[str] = mapped_column(Text)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="assets")


class ContentItem(Base):
    """Publishable metadata for the finished video (title/desc/hashtags/caption)."""

    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[str] = mapped_column(Text, default="")  # space/comma separated
    caption: Mapped[str] = mapped_column(Text, default="")  # full spoken script
    video_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="content_item")
    queue_entry: Mapped["QueueEntry | None"] = relationship(
        back_populates="content_item", uselist=False, cascade="all, delete-orphan"
    )


class QueueEntry(Base):
    """A finished video waiting in the approval/upload queue."""

    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id"), unique=True, index=True
    )
    status: Mapped[QueueStatus] = mapped_column(
        SAEnum(QueueStatus), default=QueueStatus.generated, index=True
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    content_item: Mapped["ContentItem"] = relationship(back_populates="queue_entry")
    uploads: Mapped[list["UploadLog"]] = relationship(
        back_populates="queue_entry", cascade="all, delete-orphan"
    )


class TikTokStat(Base):
    """A snapshot of one TikTok video's public metrics (for benchmarking)."""

    __tablename__ = "tiktok_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(120), index=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.0)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UploadLog(Base):
    """One upload attempt for a queue entry to a specific platform."""

    __tablename__ = "upload_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue_entry_id: Mapped[int] = mapped_column(ForeignKey("queue.id"), index=True)
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform), index=True)
    result: Mapped[UploadResult] = mapped_column(SAEnum(UploadResult))
    mode: Mapped[str] = mapped_column(String(32), default="")  # draft/direct/private/public
    remote_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    queue_entry: Mapped["QueueEntry"] = relationship(back_populates="uploads")
