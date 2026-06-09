"""End-to-end orchestration of one video.

Flow:
    script -> per-scene TTS -> scene images -> Ken Burns clips
           -> word-level captions -> assemble MP4 -> enqueue (status=generated)

Each scene is voiced separately so its narration length drives its motion-clip
length, keeping audio and visuals in sync. Word timings are concatenated (with
per-scene offsets) to build one caption track. Everything is recorded to the DB;
failures mark the Job ``failed`` and re-raise (no silent swallowing).
"""
from __future__ import annotations

import dataclasses
import logging
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from faceless_engine import assemble as assemble_mod
from faceless_engine import captions as captions_mod
from faceless_engine import imagery as imagery_mod
from faceless_engine import llm as llm_mod
from faceless_engine import tts as tts_mod
from faceless_engine.config import Settings, get_settings
from faceless_engine.ffmpeg_utils import ensure_ffmpeg, probe_duration, run_ffmpeg
from faceless_engine.imagery import OpenverseImageProvider
from faceless_engine.tts import WordTiming
from shared import db
from shared.models import JobStatus

log = logging.getLogger(__name__)


class _Bench:
    """Accumulates wall-clock time per pipeline stage."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = defaultdict(float)
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] += time.perf_counter() - t0

    def total(self) -> float:
        return time.perf_counter() - self._start

    def as_dict(self) -> dict[str, float]:
        d = {k: round(v, 2) for k, v in self.stages.items()}
        d["total"] = round(self.total(), 2)
        return d


def _concat_audio(audio_files: list[Path], out_path: Path) -> Path:
    """Concatenate scene narration files into one track (re-encoded to AAC)."""
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory(prefix="fe_audio_") as tmp:
        listfile = Path(tmp) / "audio.txt"
        listfile.write_text(
            "\n".join(
                f"file '{str(Path(a).resolve()).replace(chr(92), '/')}'" for a in audio_files
            )
            + "\n",
            encoding="utf-8",
        )
        run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(listfile),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                str(out_path),
            ],
            desc="concat-audio",
        )
    return out_path


def run_pipeline(
    topic: str,
    *,
    language: str | None = None,
    num_scenes: int = 5,
    settings: Settings | None = None,
) -> dict:
    """Generate one video for ``topic``. Returns a summary dict.

    The finished video lands in the DB as a QueueEntry with status=generated.
    """
    settings = settings or get_settings()
    settings.ensure_dirs()
    ensure_ffmpeg()
    db.init_db(settings)

    language = language or settings.language
    voice = settings.voice_id if language == "id" else settings.voice_en

    job_id = db.create_job(topic=topic, language=language)
    log.info("Job %d started: topic=%r language=%s", job_id, topic, language)
    bench = _Bench()

    try:
        db.set_job_status(job_id, JobStatus.running)

        # 1. Script -------------------------------------------------------
        with bench.stage("script"):
            script = llm_mod.generate_script(
                topic, language=language, num_scenes=num_scenes, settings=settings
            )
        if not script.scenes:
            raise RuntimeError("script generation produced no scenes")

        job_dir = settings.assets_dir / f"job_{job_id:05d}"
        (job_dir / "audio").mkdir(parents=True, exist_ok=True)
        (job_dir / "images").mkdir(parents=True, exist_ok=True)
        (job_dir / "clips").mkdir(parents=True, exist_ok=True)

        image_provider = imagery_mod.get_default_provider(settings)
        use_query = isinstance(image_provider, OpenverseImageProvider)

        clips: list[Path] = []
        scene_audio_files: list[Path] = []
        all_words: list[WordTiming] = []
        cumulative = 0.0

        # 2-4. Per scene: TTS -> image -> motion clip --------------------
        for scene in script.scenes:
            audio_path = job_dir / "audio" / f"scene_{scene.index:03d}.mp3"
            with bench.stage("tts"):
                result = tts_mod.tts(scene.text, voice=voice, out_path=audio_path)
            db.record_asset(job_id, "audio", str(audio_path))
            scene_audio_files.append(audio_path)

            # Use the probed audio duration as the authoritative scene length.
            dur = probe_duration(audio_path)
            scene = dataclasses.replace(scene, duration=dur)

            # Offset this scene's word timings into the global timeline.
            for w in result.words:
                all_words.append(
                    WordTiming(w.word, w.start + cumulative, w.end + cumulative)
                )
            cumulative += dur

            # Openverse wants short keyword queries; AI providers want the prompt.
            visual = (scene.image_query if use_query else scene.image_prompt) or scene.text
            image_path = job_dir / "images" / f"scene_{scene.index:03d}.png"
            with bench.stage("images"):
                image_provider.generate(visual, image_path)
            db.record_asset(job_id, "image", str(image_path))

            clip_path = job_dir / "clips" / f"scene_{scene.index:03d}.mp4"
            with bench.stage("ken_burns"):
                imagery_mod.ken_burns(image_path, dur, clip_path, settings=settings)
            db.record_asset(job_id, "clip", str(clip_path))
            clips.append(clip_path)

        # 5. Captions -----------------------------------------------------
        subs_path = job_dir / "captions.ass"
        if all_words:
            hook = script.hook if settings.hook_enabled else None
            with bench.stage("captions"):
                captions_mod.build_ass(all_words, subs_path, settings=settings, hook=hook)
            db.record_asset(job_id, "subtitles", str(subs_path))
        else:
            subs_path = None
            log.warning("No word timings produced; assembling without captions")

        # 6. Narration track + assemble ----------------------------------
        narration = job_dir / "narration.m4a"
        with bench.stage("audio_concat"):
            _concat_audio(scene_audio_files, narration)
        db.record_asset(job_id, "audio", str(narration))

        out_path = settings.output_dir / f"job_{job_id:05d}.mp4"
        with bench.stage("assemble"):
            assemble_mod.assemble_video(
                clips, narration, out_path, subtitles_path=subs_path, settings=settings
            )
        db.record_asset(job_id, "video", str(out_path))

        # 7. Enqueue for approval ----------------------------------------
        queue_id = db.enqueue_content(
            job_id,
            title=script.title,
            description=script.description,
            hashtags=script.hashtags_str,
            caption=script.full_text,
            video_path=str(out_path),
        )
        db.set_job_status(job_id, JobStatus.completed)

        duration = probe_duration(out_path)
        timings = bench.as_dict()
        log.info("Job %d complete: %s (%.1fs) queue_id=%d | timings=%s",
                 job_id, out_path, duration, queue_id, timings)
        return {
            "job_id": job_id,
            "queue_id": queue_id,
            "video_path": str(out_path),
            "duration": duration,
            "title": script.title,
            "scenes": len(script.scenes),
            "timings": timings,
        }

    except Exception as exc:
        log.exception("Job %d failed", job_id)
        db.set_job_status(job_id, JobStatus.failed, error=str(exc))
        raise
