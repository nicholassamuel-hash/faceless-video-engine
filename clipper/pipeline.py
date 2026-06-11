"""End-to-end clipping: YouTube URL -> captioned 9:16 Shorts in the queue.

Flow:
    download (yt-dlp) -> parse json3 captions -> LLM highlight selection
    -> per highlight: cut + crop 9:16 + burn captions -> enqueue (generated)

Reuses the shared DB/queue so clips go through the same approval + upload path
as faceless videos. Failures mark the Job failed and re-raise (no swallowing).
"""
from __future__ import annotations

import logging

from clipper import crop as crop_mod
from clipper import download as dl_mod
from clipper import highlights as hl_mod
from clipper import score as score_mod
from clipper import transcript as ts_mod
from faceless_engine.config import Settings, get_settings
from faceless_engine.ffmpeg_utils import ensure_ffmpeg, probe_duration
from faceless_engine.pipeline import _Bench
from shared import campaigns as camp_mod
from shared import db
from shared.models import JobStatus

log = logging.getLogger(__name__)


def run_clip_pipeline(url: str, *, settings: Settings | None = None) -> dict:
    """Clip one source URL into Shorts. Returns a summary dict."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    ensure_ffmpeg()
    db.init_db(settings)
    bench = _Bench()

    language = settings.clip_sub_lang
    # One "source" job tracks the download; each clip gets its own job so the
    # one-content-item-per-job invariant holds (a source yields many clips).
    src_job_id = db.create_job(topic=f"[clip-source] {url}", language=language)
    log.info("Clip source job %d started: %s", src_job_id, url)

    try:
        db.set_job_status(src_job_id, JobStatus.running)
        job_dir = settings.assets_dir / f"clip_{src_job_id:05d}"

        # 1. Download -----------------------------------------------------
        with bench.stage("download"):
            src = dl_mod.download(url, job_dir / "source", sub_lang=language)
        db.record_asset(src_job_id, "video", str(src.video_path))
        if src.subs_path:
            db.record_asset(src_job_id, "subtitles", str(src.subs_path))

        # 2. Transcript ---------------------------------------------------
        words = ts_mod.parse_json3(src.subs_path) if src.subs_path else []
        transcript = ts_mod.transcript_lines(words) if words else ""

        # 3. Highlight selection -----------------------------------------
        with bench.stage("highlights"):
            candidates = hl_mod.select_highlights(
                src.title, transcript, src.duration or probe_duration(src.video_path),
                settings=settings,
            )
            # Extend/trim each pick toward the target length, snapped to words.
            candidates = hl_mod.fit_durations(candidates, words, settings=settings)

        # 3b. Virality ranking: score the candidate pool, keep the best ---
        with bench.stage("scoring"):
            excerpts = [
                " ".join(w.word for w in ts_mod.words_in_range(words, c.start, c.end))
                for c in candidates
            ]
            ranked = score_mod.rank_highlights(candidates, excerpts, settings=settings)
            highlights = ranked[: max(1, settings.clips_per_video)]

        campaign = camp_mod.get_active(settings)

        # 4. Cut + crop + caption per highlight (xN hook variants) --------
        out_dir = settings.output_dir
        results = []
        for i, hl in enumerate(highlights):
            clip_words = ts_mod.words_in_range(words, hl.start, hl.end)
            caption_text = " ".join(w.word for w in clip_words)
            base_desc = f"From: {src.title}\n{hl.reason}".strip()
            if hl.score is not None:
                base_desc += f"\nVirality: {hl.score:.0f}/100 — {hl.score_reason}"

            variants = hl.hook_variants(settings.clip_hook_variants)
            for v, hook in enumerate(variants):
                suffix = f"_{chr(97 + v)}" if len(variants) > 1 else ""
                out_path = out_dir / f"clip_{src_job_id:05d}_{i:02d}{suffix}.mp4"
                with bench.stage("cut"):
                    crop_mod.make_clip(
                        src.video_path, hl.start, hl.end, clip_words, out_path,
                        settings=settings, hook=hook,
                    )
                desc = base_desc
                if len(variants) > 1:
                    desc += f"\nHook variant {chr(65 + v)}: {hook}"
                tags = hl.hashtags or ["shorts", "fyp", "clip"]
                title, desc, tags = camp_mod.apply(
                    campaign, title=hl.title or src.title, description=desc, hashtags=tags
                )
                # Each clip is its own job (one ContentItem per Job).
                clip_job_id = db.create_job(topic=f"[clip] {title}", language=language)
                db.record_asset(clip_job_id, "video", str(out_path))
                queue_id = db.enqueue_content(
                    clip_job_id,
                    title=title,
                    description=desc,
                    hashtags=" ".join(f"#{t}" for t in tags),
                    caption=caption_text,
                    video_path=str(out_path),
                )
                db.set_job_status(clip_job_id, JobStatus.completed)
                results.append({
                    "queue_id": queue_id,
                    "video_path": str(out_path),
                    "start": round(hl.start, 1),
                    "end": round(hl.end, 1),
                    "duration": round(probe_duration(out_path), 1),
                    "title": title,
                    "hook": hook,
                    "score": hl.score,
                })

        db.set_job_status(src_job_id, JobStatus.completed)
        timings = bench.as_dict()
        log.info("Clip job %d complete: %d clip(s) | timings=%s",
                 src_job_id, len(results), timings)
        return {
            "job_id": src_job_id,
            "source_title": src.title,
            "source_duration": round(src.duration, 1),
            "clips": results,
            "timings": timings,
        }

    except Exception as exc:
        log.exception("Clip job %d failed", src_job_id)
        db.set_job_status(src_job_id, JobStatus.failed, error=str(exc))
        raise
