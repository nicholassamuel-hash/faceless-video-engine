"""SQLite persistence via SQLAlchemy.

Provides engine/session management plus small helper functions used across the
pipeline, approval CLI, and uploader. Sessions are short-lived; use the
:func:`session_scope` context manager for transactional units of work.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from faceless_engine.config import Settings, get_settings
from shared.models import (
    Base,
    ContentItem,
    Job,
    JobStatus,
    Platform,
    QueueEntry,
    QueueStatus,
    UploadLog,
    UploadResult,
    utcnow,
)

log = logging.getLogger(__name__)

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_db(settings: Settings | None = None, *, echo: bool = False) -> Engine:
    """Create the engine (if needed), create tables, and return the engine."""
    global _engine, _Session
    settings = settings or get_settings()
    if _engine is None:
        url = settings.resolved_db_url()
        log.info("Opening database: %s", url)
        _engine = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(_engine)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    if _Session is None:
        init_db()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back and re-raises on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        log.exception("DB transaction failed; rolled back")
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------
# High-level helpers
# --------------------------------------------------------------------------
def create_job(topic: str, language: str) -> int:
    """Insert a new pending Job and return its id."""
    with session_scope() as s:
        job = Job(topic=topic, language=language, status=JobStatus.pending)
        s.add(job)
        s.flush()
        return job.id


def set_job_status(job_id: int, status: JobStatus, error: str | None = None) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        job.status = status
        if error is not None:
            job.error = error


def record_asset(job_id: int, kind: str, path: str, meta: str | None = None) -> None:
    from shared.models import Asset

    with session_scope() as s:
        s.add(Asset(job_id=job_id, kind=kind, path=path, meta=meta))


def enqueue_content(
    job_id: int,
    *,
    title: str,
    description: str,
    hashtags: str,
    caption: str,
    video_path: str,
) -> int:
    """Create a ContentItem + QueueEntry(status=generated). Returns queue id."""
    with session_scope() as s:
        item = ContentItem(
            job_id=job_id,
            title=title,
            description=description,
            hashtags=hashtags,
            caption=caption,
            video_path=video_path,
        )
        s.add(item)
        s.flush()
        entry = QueueEntry(content_item_id=item.id, status=QueueStatus.generated)
        s.add(entry)
        s.flush()
        return entry.id


def list_queue(status: QueueStatus | None = None) -> list[dict]:
    """Return queue entries (optionally filtered) as plain dicts for display."""
    with session_scope() as s:
        stmt = select(QueueEntry).join(ContentItem)
        if status is not None:
            stmt = stmt.where(QueueEntry.status == status)
        stmt = stmt.order_by(QueueEntry.created_at)
        rows = s.execute(stmt).scalars().all()
        return [_entry_to_dict(e) for e in rows]


def get_queue_entry(entry_id: int) -> dict | None:
    with session_scope() as s:
        e = s.get(QueueEntry, entry_id)
        return _entry_to_dict(e) if e else None


def set_queue_status(
    entry_id: int, status: QueueStatus, reason: str | None = None
) -> None:
    with session_scope() as s:
        e = s.get(QueueEntry, entry_id)
        if e is None:
            raise ValueError(f"Queue entry {entry_id} not found")
        e.status = status
        e.reject_reason = reason
        e.reviewed_at = utcnow()


def approved_for_upload() -> list[dict]:
    """Approved entries that have not yet been successfully uploaded."""
    return list_queue(status=QueueStatus.approved)


def log_upload(
    entry_id: int,
    platform: Platform,
    result: UploadResult,
    *,
    mode: str = "",
    remote_id: str | None = None,
    message: str | None = None,
) -> None:
    with session_scope() as s:
        s.add(
            UploadLog(
                queue_entry_id=entry_id,
                platform=platform,
                result=result,
                mode=mode,
                remote_id=remote_id,
                message=message,
            )
        )


def count_uploads_today(platform: Platform, result: UploadResult = UploadResult.success) -> int:
    """How many uploads of ``result`` happened today (UTC) for a platform."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        stmt = (
            select(func.count())
            .select_from(UploadLog)
            .where(
                UploadLog.platform == platform,
                UploadLog.result == result,
                UploadLog.attempted_at >= start,
            )
        )
        return int(s.execute(stmt).scalar_one())


def already_uploaded(entry_id: int, platform: Platform) -> bool:
    """True if this entry already has a successful upload on the platform."""
    with session_scope() as s:
        stmt = (
            select(func.count())
            .select_from(UploadLog)
            .where(
                UploadLog.queue_entry_id == entry_id,
                UploadLog.platform == platform,
                UploadLog.result == UploadResult.success,
            )
        )
        return int(s.execute(stmt).scalar_one()) > 0


def store_tiktok_stats(rows: list[dict]) -> int:
    """Persist a batch of TikTok video stat snapshots. Returns count stored."""
    from shared.models import TikTokStat

    with session_scope() as s:
        for r in rows:
            s.add(TikTokStat(**r))
        return len(rows)


def previous_account_avg(account: str, before) -> dict | None:
    """Average views/engagement for an account from the most recent EARLIER
    fetch snapshot (for trend comparison). Returns None if no prior data."""
    from shared.models import TikTokStat

    with session_scope() as s:
        prev_time = s.execute(
            select(func.max(TikTokStat.fetched_at)).where(
                TikTokStat.account == account, TikTokStat.fetched_at < before
            )
        ).scalar_one_or_none()
        if prev_time is None:
            return None
        rows = s.execute(
            select(TikTokStat).where(
                TikTokStat.account == account, TikTokStat.fetched_at == prev_time
            )
        ).scalars().all()
        if not rows:
            return None
        n = len(rows)
        return {
            "fetched_at": prev_time,
            "avg_views": sum(r.views for r in rows) / n,
            "avg_engagement": sum(r.engagement_rate for r in rows) / n,
            "videos": n,
        }


def _entry_to_dict(e: QueueEntry) -> dict:
    ci = e.content_item
    return {
        "queue_id": e.id,
        "status": e.status.value,
        "reject_reason": e.reject_reason,
        "title": ci.title,
        "description": ci.description,
        "hashtags": ci.hashtags,
        "caption": ci.caption,
        "video_path": ci.video_path,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
