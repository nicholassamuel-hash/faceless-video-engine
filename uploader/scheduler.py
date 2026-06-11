"""Upload scheduler.

Picks approved queue entries and uploads them to the requested platforms with:
* a randomized jitter delay before each upload (never immediate),
* per-platform daily rate limits enforced from config,
* every attempt logged to ``upload_log`` (success/failed/skipped),
* SAFE defaults inherited from config (TikTok=draft, YouTube=private).

An entry is marked ``uploaded`` once every targeted platform has a successful
upload for it (or already had one). Platform/credential failures are logged and
the entry is left ``approved`` so it can be retried later.
"""
from __future__ import annotations

import logging
import random
import time

from faceless_engine.config import Settings, get_settings
from shared import db
from shared.models import Platform, QueueStatus, UploadResult

log = logging.getLogger(__name__)


def _parse_tags(hashtags: str) -> list[str]:
    raw = hashtags.replace(",", " ").split()
    return [t.lstrip("#").strip() for t in raw if t.strip()]


def _jitter_seconds(settings: Settings) -> float:
    lo = min(settings.jitter_min_minutes, settings.jitter_max_minutes)
    hi = max(settings.jitter_min_minutes, settings.jitter_max_minutes)
    return random.uniform(lo, hi) * 60.0


def _upload_one(
    entry: dict, platform: Platform, settings: Settings, *, dry_run: bool
) -> UploadResult:
    """Upload a single entry to a single platform; log the attempt. Returns result."""
    entry_id = entry["queue_id"]

    # Skip if already uploaded to this platform.
    if db.already_uploaded(entry_id, platform):
        log.info("Entry %d already uploaded to %s — skipping", entry_id, platform.value)
        return UploadResult.success

    # Enforce per-platform daily rate limit.
    max_per_day = {
        Platform.tiktok: settings.tiktok_max_per_day,
        Platform.youtube: settings.youtube_max_per_day,
        Platform.instagram: settings.instagram_max_per_day,
    }[platform]
    done_today = db.count_uploads_today(platform)
    if done_today >= max_per_day:
        log.info(
            "%s daily limit reached (%d/%d) — skipping entry %d",
            platform.value, done_today, max_per_day, entry_id,
        )
        db.log_upload(entry_id, platform, UploadResult.skipped,
                      message=f"daily limit {max_per_day} reached")
        return UploadResult.skipped

    if dry_run:
        log.info("[dry-run] would upload entry %d to %s", entry_id, platform.value)
        db.log_upload(entry_id, platform, UploadResult.skipped, message="dry-run")
        return UploadResult.skipped

    # Randomized jitter BEFORE the upload — never immediate.
    delay = _jitter_seconds(settings)
    log.info("Sleeping %.1f min before uploading entry %d to %s",
             delay / 60.0, entry_id, platform.value)
    time.sleep(delay)

    try:
        if platform == Platform.tiktok:
            from uploader.tiktok import upload_to_tiktok

            outcome = upload_to_tiktok(
                entry["video_path"], title=entry["title"],
                mode=settings.tiktok_mode, settings=settings,
            )
            mode = settings.tiktok_mode
        elif platform == Platform.instagram:
            from uploader.instagram import upload_to_instagram

            outcome = upload_to_instagram(
                entry["video_path"],
                caption=f"{entry['title']}\n\n{entry['hashtags']}".strip(),
                settings=settings,
            )
            mode = "reels"
        else:
            from uploader.youtube import upload_to_youtube

            outcome = upload_to_youtube(
                entry["video_path"],
                title=entry["title"],
                description=f"{entry['description']}\n\n{entry['hashtags']}".strip(),
                tags=_parse_tags(entry["hashtags"]),
                mode=settings.youtube_privacy,
                settings=settings,
            )
            mode = settings.youtube_privacy

        db.log_upload(
            entry_id, platform, UploadResult.success,
            mode=mode, remote_id=outcome.remote_id, message=outcome.message,
        )
        log.info("Uploaded entry %d to %s (remote_id=%s)",
                 entry_id, platform.value, outcome.remote_id)
        return UploadResult.success

    except Exception as exc:
        # Log and DO NOT silently swallow — record the failure, keep going.
        log.exception("Upload of entry %d to %s failed", entry_id, platform.value)
        db.log_upload(entry_id, platform, UploadResult.failed, message=str(exc))
        return UploadResult.failed


def run_scheduler(
    platforms: list[str] | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    settings: Settings | None = None,
) -> dict:
    """Process approved entries. Returns a summary dict.

    Args:
        platforms: subset of ["tiktok", "youtube"]; default both.
        dry_run: log intended actions without uploading or sleeping the full jitter.
        limit: max number of entries to process this run.
    """
    settings = settings or get_settings()
    db.init_db(settings)

    targets = [Platform(p) for p in (platforms or ["tiktok", "youtube"])]
    entries = db.approved_for_upload()
    if limit is not None:
        entries = entries[:limit]

    log.info(
        "Scheduler: %d approved entries, targets=%s, dry_run=%s, modes=[tiktok:%s, youtube:%s]",
        len(entries), [p.value for p in targets], dry_run,
        settings.tiktok_mode, settings.youtube_privacy,
    )

    summary = {"processed": 0, "uploaded": 0, "failed": 0, "skipped": 0}
    first = True
    for entry in entries:
        # Jitter BETWEEN entries too (the first entry still jitters inside _upload_one).
        if not first and not dry_run:
            gap = _jitter_seconds(settings)
            log.info("Inter-entry jitter: sleeping %.1f min", gap / 60.0)
            time.sleep(gap)
        first = False

        results = [_upload_one(entry, p, settings, dry_run=dry_run) for p in targets]
        summary["processed"] += 1
        summary["uploaded"] += sum(r == UploadResult.success for r in results)
        summary["failed"] += sum(r == UploadResult.failed for r in results)
        summary["skipped"] += sum(r == UploadResult.skipped for r in results)

        # Mark uploaded only if every target succeeded (and it wasn't a dry-run).
        if not dry_run and all(r == UploadResult.success for r in results):
            db.set_queue_status(entry["queue_id"], QueueStatus.uploaded)
            log.info("Entry %d marked uploaded", entry["queue_id"])

    log.info("Scheduler done: %s", summary)
    return summary
