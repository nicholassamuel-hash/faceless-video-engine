"""Faceless video engine — top-level CLI.

Commands:
    generate   Generate one video and queue it for review.
    review     Review queued videos (list / preview / approve / reject).
    upload     Run the upload scheduler over approved entries (SAFE by default).
    doctor     Check the environment (ffmpeg, config, DB).

Examples (Windows CMD):
    python main.py generate --topic "Sejarah kopi Indonesia" --language id --scenes 5
    python main.py review interactive
    python main.py upload --dry-run
    python main.py upload --platforms youtube
"""
from __future__ import annotations

import argparse
import logging
import sys

from faceless_engine.config import get_settings
from faceless_engine.ffmpeg_utils import FFmpegNotFoundError, find_ffmpeg
from shared import db
from shared.logging_setup import setup_logging

log = logging.getLogger(__name__)


_STAGE_ORDER = ["script", "tts", "images", "ken_burns", "captions", "audio_concat", "assemble"]


def _print_timings(timings: dict, video_seconds: float | None = None) -> None:
    total = timings.get("total", 0) or 1e-9
    print("\n  --- benchmark (wall-clock) ---")
    for stage in _STAGE_ORDER:
        if stage in timings:
            secs = timings[stage]
            print(f"  {stage:<13} {secs:>7.2f}s  {secs / total * 100:>5.1f}%")
    print(f"  {'TOTAL':<13} {timings.get('total', 0):>7.2f}s")
    if video_seconds:
        rt = timings.get("total", 0) / video_seconds if video_seconds else 0
        print(f"  ({rt:.1f}s of compute per 1s of finished video)")


def cmd_generate(args: argparse.Namespace) -> int:
    from faceless_engine.pipeline import run_pipeline

    result = run_pipeline(
        args.topic, language=args.language, num_scenes=args.scenes
    )
    print("\n=== Video generated ===")
    print(f"  job_id    : {result['job_id']}")
    print(f"  queue_id  : {result['queue_id']}")
    print(f"  title     : {result['title']}")
    print(f"  scenes    : {result['scenes']}")
    print(f"  duration  : {result['duration']:.1f}s")
    print(f"  file      : {result['video_path']}")
    _print_timings(result.get("timings", {}), result.get("duration"))
    print("\nNext: review it ->  python main.py review interactive")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    from clipper.pipeline import run_clip_pipeline

    # Apply CLI overrides onto the shared settings (no env vars needed).
    settings = get_settings()
    if args.lang:
        settings.clip_sub_lang = args.lang
    if args.clips:
        settings.clips_per_video = args.clips
    if args.crop:
        settings.clip_crop_mode = args.crop
    if args.target:
        settings.clip_target_seconds = args.target
    if args.min is not None:
        settings.clip_min_seconds = args.min
    if args.max is not None:
        settings.clip_max_seconds = args.max

    for url in args.urls:
        print(f"\n=== Clipping: {url} ===")
        result = run_clip_pipeline(url, settings=settings)
        print(f"  source : {result['source_title']} ({result['source_duration']}s)")
        for c in result["clips"]:
            print(f"  clip   : queue {c['queue_id']} | {c['start']}-{c['end']}s "
                  f"({c['duration']}s) | {c['title']}")
            print(f"           {c['video_path']}")
        _print_timings_clip(result.get("timings", {}))
    print("\nNext: review ->  python main.py review interactive")
    return 0


def _print_timings_clip(timings: dict) -> None:
    if not timings:
        return
    order = ["download", "highlights", "cut", "total"]
    print("\n  --- benchmark (wall-clock) ---")
    for stage in order:
        if stage in timings:
            print(f"  {stage:<11} {timings[stage]:>7.2f}s")


def cmd_benchmark(args: argparse.Namespace) -> int:
    from faceless_engine.pipeline import run_pipeline

    runs: list[dict] = []
    for i in range(args.runs):
        print(f"\n[benchmark] run {i + 1}/{args.runs} ...")
        result = run_pipeline(args.topic, language=args.language, num_scenes=args.scenes)
        runs.append(result)
        _print_timings(result.get("timings", {}), result.get("duration"))

    # Aggregate per-stage stats across runs.
    print("\n=== Benchmark summary (%d runs) ===" % len(runs))
    stages = _STAGE_ORDER + ["total"]
    print(f"  {'stage':<13} {'min':>8} {'avg':>8} {'max':>8}")
    for stage in stages:
        vals = [r["timings"][stage] for r in runs if stage in r.get("timings", {})]
        if not vals:
            continue
        print(f"  {stage:<13} {min(vals):>7.2f}s {sum(vals) / len(vals):>7.2f}s {max(vals):>7.2f}s")
    vid = [r["duration"] for r in runs]
    print(f"\n  video length: min {min(vid):.1f}s / avg {sum(vid) / len(vid):.1f}s / max {max(vid):.1f}s")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from approval.review_cli import build_parser as review_parser

    parser = review_parser()
    sub_args = parser.parse_args(args.review_args)
    return sub_args.func(sub_args)


def cmd_upload(args: argparse.Namespace) -> int:
    from uploader.scheduler import run_scheduler

    settings = get_settings()
    if not args.dry_run and not args.yes:
        print("Upload modes: TikTok=%s, YouTube=%s" % (
            settings.tiktok_mode, settings.youtube_privacy))
        if settings.tiktok_mode == "direct" or settings.youtube_privacy == "public":
            confirm = input(
                "WARNING: a non-safe (public/direct) mode is enabled. Type 'yes' to proceed: "
            ).strip().lower()
            if confirm != "yes":
                print("Aborted.")
                return 1

    summary = run_scheduler(
        platforms=args.platforms, dry_run=args.dry_run, limit=args.limit
    )
    print(f"\nScheduler summary: {summary}")
    return 0


def cmd_tiktok_stats(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from benchmark import tiktok_stats as tt
    from shared import db

    db.init_db(get_settings())
    now = datetime.now(timezone.utc)
    summaries = []
    for target in args.accounts:
        try:
            stats = tt.fetch_account(target, limit=args.limit)
        except Exception as exc:
            print(f"  ! failed to fetch {target}: {exc}")
            continue
        if not stats:
            print(f"  ! no public videos found for {target}")
            continue
        db.store_tiktok_stats([s.as_row() for s in stats])
        summary = tt.summarize(stats)
        summaries.append(summary)

        acct = summary["account"]
        print(f"\n=== {acct} ===  ({summary['videos']} videos)")
        print(f"  avg views      : {summary['avg_views']:,}")
        print(f"  median views   : {summary['median_views']:,}")
        print(f"  avg engagement : {summary['avg_engagement'] * 100:.2f}%")
        print(f"  total views    : {summary['total_views']:,}")
        bv = summary["best_video"]
        print(f"  top video      : {bv['views']:,} views | {bv['title'][:60]}")

        prev = db.previous_account_avg(acct, now)
        if prev:
            dv = summary["avg_views"] - prev["avg_views"]
            pct = (dv / prev["avg_views"] * 100) if prev["avg_views"] else 0
            print(f"  vs last snapshot: avg views {dv:+,.0f} ({pct:+.1f}%) "
                  f"since {prev['fetched_at']:%Y-%m-%d %H:%M}")

    if len(summaries) > 1:
        print("\n=== Comparison (by avg views) ===")
        print(f"  {'account':<24} {'avg views':>12} {'engagement':>11} {'videos':>7}")
        for s in sorted(summaries, key=lambda x: x["avg_views"], reverse=True):
            print(f"  {s['account']:<24} {s['avg_views']:>12,} "
                  f"{s['avg_engagement'] * 100:>10.2f}% {s['videos']:>7}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = get_settings()
    print("=== Doctor ===")
    try:
        ffmpeg, ffprobe = find_ffmpeg()
        print(f"  ffmpeg  : {ffmpeg}")
        print(f"  ffprobe : {ffprobe}")
    except FFmpegNotFoundError as exc:
        print(f"  ffmpeg  : NOT FOUND\n{exc}")
        return 1
    settings.ensure_dirs()
    db.init_db(settings)
    print(f"  work_dir: {settings.work_dir}")
    print(f"  db      : {settings.resolved_db_url()}")
    print(f"  language: {settings.language}  voice={settings.default_voice}")
    print(f"  output  : {settings.width}x{settings.height} @ {settings.fps}fps")
    if settings.llm_provider.lower() in ("template", "stub"):
        llm_desc = "template (keyless, generic copy)"
    elif settings.llm_api_key:
        llm_desc = f"{settings.llm_base_url} (model={settings.llm_model})"
    else:
        llm_desc = "template fallback — set LLM_API_KEY for real scripts"
    print(f"  llm     : {llm_desc}")
    img = settings.image_provider.lower()
    if img == "http" or (img == "auto" and settings.image_api_key):
        img_desc = "HTTP AI image API (key set)"
    elif img == "placeholder":
        img_desc = "placeholder gradients"
    else:
        img_desc = "openverse (keyless real photos)"
    print(f"  image   : {img_desc}")
    print(f"  upload  : tiktok={settings.tiktok_mode}  youtube={settings.youtube_privacy}")
    print("  status  : OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="faceless", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="generate one video")
    p_gen.add_argument("--topic", required=True)
    p_gen.add_argument("--language", choices=["id", "en"], default=None)
    p_gen.add_argument("--scenes", type=int, default=5)
    p_gen.set_defaults(func=cmd_generate)

    p_clip = sub.add_parser("clip", help="clip YouTube URL(s) into 9:16 Shorts")
    p_clip.add_argument("urls", nargs="+", help="one or more YouTube URLs")
    p_clip.add_argument("--lang", default=None, help="caption language (e.g. id, en)")
    p_clip.add_argument("--clips", type=int, default=None, help="clips per source video")
    p_clip.add_argument("--crop", choices=["blur", "fill"], default=None, help="9:16 framing")
    p_clip.add_argument("--target", type=int, default=None, help="target clip seconds")
    p_clip.add_argument("--min", type=int, default=None, help="min clip seconds")
    p_clip.add_argument("--max", type=int, default=None, help="max clip seconds")
    p_clip.set_defaults(func=cmd_clip)

    p_bench = sub.add_parser("benchmark", help="time the pipeline over N runs")
    p_bench.add_argument("--topic", required=True)
    p_bench.add_argument("--language", choices=["id", "en"], default=None)
    p_bench.add_argument("--scenes", type=int, default=5)
    p_bench.add_argument("--runs", type=int, default=3)
    p_bench.set_defaults(func=cmd_benchmark)

    p_rev = sub.add_parser("review", help="review queued videos (delegates to review_cli)")
    p_rev.add_argument("review_args", nargs=argparse.REMAINDER,
                       help="e.g. 'list', 'preview 3', 'approve 3', 'interactive'")
    p_rev.set_defaults(func=cmd_review)

    p_up = sub.add_parser("upload", help="run the upload scheduler (SAFE by default)")
    p_up.add_argument("--platforms", nargs="*", choices=["tiktok", "youtube"], default=None)
    p_up.add_argument("--dry-run", action="store_true")
    p_up.add_argument("--limit", type=int, default=None)
    p_up.add_argument("--yes", action="store_true", help="skip the non-safe-mode confirmation")
    p_up.set_defaults(func=cmd_upload)

    p_tt = sub.add_parser("tiktok-stats", help="benchmark public TikTok account metrics")
    p_tt.add_argument("accounts", nargs="+", help="@handles or TikTok URLs to benchmark")
    p_tt.add_argument("--limit", type=int, default=20, help="videos per account (default 20)")
    p_tt.set_defaults(func=cmd_tiktok_stats)

    p_doc = sub.add_parser("doctor", help="check environment & config")
    p_doc.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252 and crash on emoji / non-Latin text in
    # titles and captions; make all output UTF-8 and never crash on a glyph.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)
    try:
        return args.func(args)
    except FFmpegNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
