"""Track OUR posted videos' views + estimate campaign earnings.

Closes the feedback loop the benchmark scraper doesn't cover: which of our own
clips/hook variants actually win, and what the active campaign would pay.

Sources:
  - TikTok: scrape our own account (FE_OWN_TIKTOK_ACCOUNT or --account) with
    the same yt-dlp machinery as benchmark.tiktok_stats.
  - YouTube: successful upload_log remote ids -> per-video yt-dlp lookups.

Usage:
  python -m benchmark.own_stats                     # both sources
  python -m benchmark.own_stats --account @handle   # override TikTok handle
  python -m benchmark.own_stats --limit 30 --json

Earnings use the active campaign profile (FE_CAMPAIGN): views below
min_views_payout earn 0, the rest pay cpm_idr per 1k views.
"""
from __future__ import annotations

import argparse
import json
import logging

from benchmark import tiktok_stats as tt
from faceless_engine.config import get_settings
from shared import campaigns as camp_mod
from shared import db
from shared.models import Platform

log = logging.getLogger(__name__)


def _youtube_rows(limit: int) -> list[dict]:
    """Fetch view counts for our uploaded YouTube videos via yt-dlp."""
    uploads = db.successful_uploads(Platform.youtube)[-limit:]
    rows = []
    for u in uploads:
        url = f"https://www.youtube.com/watch?v={u['remote_id']}"
        try:
            data = tt._dump_json(url, limit=1)
        except tt.StatsError as exc:
            # Private uploads (the safe default) aren't publicly queryable.
            log.warning("YouTube lookup failed for %s: %s", u["remote_id"], exc)
            continue
        rows.append({
            "platform": Platform.youtube,
            "video_id": str(u["remote_id"]),
            "title": (data.get("title") or "")[:200],
            "url": url,
            "views": int(data.get("view_count") or 0),
            "likes": int(data.get("like_count") or 0),
            "comments": int(data.get("comment_count") or 0),
        })
    return rows


def _tiktok_rows(account: str, limit: int) -> list[dict]:
    stats = tt.fetch_account(account, limit=limit)
    return [{
        "platform": Platform.tiktok,
        "video_id": s.video_id,
        "title": s.title,
        "url": s.url,
        "views": s.views,
        "likes": s.likes,
        "comments": s.comments,
    } for s in stats]


def collect(account: str | None = None, limit: int = 30) -> dict:
    """Fetch + persist snapshots; return a report dict with earnings estimate."""
    settings = get_settings()
    db.init_db(settings)
    campaign = camp_mod.get_active(settings)

    rows: list[dict] = []
    handle = (account or settings.own_tiktok_account).strip()
    if handle:
        rows += _tiktok_rows(handle, limit)
    else:
        log.info("No own TikTok account configured (FE_OWN_TIKTOK_ACCOUNT) — skipping")
    rows += _youtube_rows(limit)

    if rows:
        db.store_own_stats(rows)

    videos = []
    total_views = 0
    total_idr = 0.0
    for r in sorted(rows, key=lambda x: x["views"], reverse=True):
        idr = campaign.estimate_idr(r["views"]) if campaign else 0.0
        total_views += r["views"]
        total_idr += idr
        videos.append({
            "platform": r["platform"].value,
            "views": r["views"],
            "likes": r["likes"],
            "est_idr": round(idr),
            "title": r["title"][:70],
            "url": r["url"],
        })
    return {
        "videos": videos,
        "count": len(videos),
        "total_views": total_views,
        "campaign": campaign.name if campaign else None,
        "est_total_idr": round(total_idr),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default=None, help="own TikTok handle (@...)")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    report = collect(args.account, args.limit)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if not report["videos"]:
        print("No own videos found (set FE_OWN_TIKTOK_ACCOUNT or upload publicly first).")
        return
    print(f"{'PLATFORM':<10} {'VIEWS':>10} {'LIKES':>8} {'EST IDR':>12}  TITLE")
    for v in report["videos"]:
        print(f"{v['platform']:<10} {v['views']:>10,} {v['likes']:>8,} "
              f"{v['est_idr']:>12,}  {v['title']}")
    camp = report["campaign"] or "(none — set FE_CAMPAIGN for earnings)"
    print(f"\n{report['count']} videos | {report['total_views']:,} views | "
          f"campaign: {camp} | est. total: Rp{report['est_total_idr']:,}")


if __name__ == "__main__":
    main()
