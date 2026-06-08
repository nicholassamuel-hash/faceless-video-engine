"""Fetch + aggregate public TikTok metrics via yt-dlp."""
from __future__ import annotations

import json
import logging
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class StatsError(RuntimeError):
    pass


@dataclass
class VideoStat:
    account: str
    video_id: str
    title: str
    views: int
    likes: int
    comments: int
    reposts: int
    duration: int
    posted_at: datetime | None
    engagement_rate: float
    url: str = ""

    def as_row(self) -> dict:
        """Mapping for shared.db.store_tiktok_stats (DB column names)."""
        return {
            "account": self.account,
            "video_id": self.video_id,
            "title": self.title,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "reposts": self.reposts,
            "duration": self.duration,
            "engagement_rate": self.engagement_rate,
            "posted_at": self.posted_at,
        }


def normalize_target(target: str) -> tuple[str, str]:
    """Return (account_label, url) for a handle, @handle, or full URL."""
    t = target.strip()
    if t.startswith("http"):
        # Extract @account from a video/profile URL if present.
        acct = t
        if "/@" in t:
            acct = "@" + t.split("/@", 1)[1].split("/", 1)[0]
        return acct, t
    handle = t if t.startswith("@") else f"@{t}"
    return handle, f"https://www.tiktok.com/{handle}"


@retry(
    retry=retry_if_exception_type(StatsError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=20),
    reraise=True,
)
def _dump_json(url: str, limit: int) -> dict:
    cmd = [
        sys.executable, "-m", "yt_dlp", "-J", "--no-warnings",
        "--playlist-end", str(limit), url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise StatsError(f"yt-dlp failed for {url}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def _to_stat(account: str, e: dict) -> VideoStat:
    views = int(e.get("view_count") or 0)
    likes = int(e.get("like_count") or 0)
    comments = int(e.get("comment_count") or 0)
    reposts = int(e.get("repost_count") or 0)
    ts = e.get("timestamp")
    posted = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
    eng = (likes + comments + reposts) / views if views else 0.0
    return VideoStat(
        account=account,
        video_id=str(e.get("id", "")),
        title=(e.get("title") or "")[:200],
        views=views, likes=likes, comments=comments, reposts=reposts,
        duration=int(e.get("duration") or 0),
        posted_at=posted,
        engagement_rate=round(eng, 4),
        url=e.get("webpage_url") or e.get("url") or "",
    )


def fetch_account(target: str, limit: int = 20) -> list[VideoStat]:
    """Fetch recent video stats for a TikTok account or single video URL."""
    account, url = normalize_target(target)
    log.info("Fetching TikTok stats: %s (limit %d)", account, limit)
    data = _dump_json(url, limit)
    entries = data.get("entries") if data.get("_type") == "playlist" else [data]
    stats = [_to_stat(account, e) for e in (entries or []) if e]
    stats = [s for s in stats if s.video_id]
    log.info("Got %d videos for %s", len(stats), account)
    return stats


def summarize(stats: list[VideoStat]) -> dict:
    """Aggregate metrics for a set of a single account's videos."""
    if not stats:
        return {}
    views = [s.views for s in stats]
    eng = [s.engagement_rate for s in stats]
    top = max(stats, key=lambda s: s.views)
    return {
        "account": stats[0].account,
        "videos": len(stats),
        "total_views": sum(views),
        "avg_views": int(statistics.mean(views)),
        "median_views": int(statistics.median(views)),
        "avg_engagement": round(statistics.mean(eng), 4),
        "best_video": {"title": top.title, "views": top.views, "url": top.url},
    }
