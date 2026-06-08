"""FastAPI backend for the local Faceless Studio dashboard.

Wraps the existing pipelines (faceless generate, YouTube clip, review queue,
TikTok benchmark) behind a small JSON API + serves the single-page UI. Long
jobs run in background threads; a rolling log buffer powers the live console.

Run via webapp/launch.py (or: uvicorn webapp.server:app --port 8765).
"""
from __future__ import annotations

import collections
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from faceless_engine.config import get_settings
from shared import db
from shared.logging_setup import setup_logging
from shared.models import QueueStatus

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# --------------------------------------------------------------------------
# Live log buffer (powers the dashboard console)
# --------------------------------------------------------------------------
_LOG_BUF: collections.deque[str] = collections.deque(maxlen=400)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_BUF.append(self.format(record))
        except Exception:
            pass


def _install_log_buffer() -> None:
    h = _BufferHandler()
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                                     datefmt="%H:%M:%S"))
    h.setLevel(logging.INFO)
    logging.getLogger().addHandler(h)


# --------------------------------------------------------------------------
# Background task registry
# --------------------------------------------------------------------------
_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _start_task(kind: str, fn, *args, **kwargs) -> str:
    tid = uuid.uuid4().hex[:8]
    with _LOCK:
        _TASKS[tid] = {"id": tid, "kind": kind, "status": "running",
                       "result": None, "error": None}

    def worker() -> None:
        try:
            result = fn(*args, **kwargs)
            with _LOCK:
                _TASKS[tid].update(status="done", result=result)
            log.info("Task %s (%s) done", tid, kind)
        except Exception as exc:  # logged, surfaced to UI, not swallowed
            logging.getLogger(__name__).exception("Task %s (%s) failed", tid, kind)
            with _LOCK:
                _TASKS[tid].update(status="error", error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    log.info("Task %s (%s) started", tid, kind)
    return tid


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
app = FastAPI(title="Faceless Studio")


@app.on_event("startup")
def _startup() -> None:
    setup_logging()
    _install_log_buffer()
    s = get_settings()
    s.ensure_dirs()
    db.init_db(s)
    log.info("Faceless Studio ready")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


# ---- request models ------------------------------------------------------
class GenerateReq(BaseModel):
    topic: str
    language: str = "id"
    scenes: int = 5
    image_provider: str | None = None


class ClipReq(BaseModel):
    url: str
    lang: str = "id"
    clips: int = 1
    target: int = 55
    crop: str = "blur"


class TikTokReq(BaseModel):
    accounts: list[str]
    limit: int = 15


class RejectReq(BaseModel):
    reason: str = "rejected"


# ---- pipelines -----------------------------------------------------------
@app.post("/api/generate")
def api_generate(req: GenerateReq) -> dict:
    from faceless_engine.pipeline import run_pipeline

    if not req.topic.strip():
        raise HTTPException(400, "topic is required")
    s = get_settings()
    if req.image_provider:
        s.image_provider = req.image_provider

    def job():
        return run_pipeline(req.topic, language=req.language, num_scenes=req.scenes, settings=s)

    return {"task": _start_task("generate", job)}


@app.post("/api/clip")
def api_clip(req: ClipReq) -> dict:
    from clipper.pipeline import run_clip_pipeline

    if not req.url.strip():
        raise HTTPException(400, "url is required")
    s = get_settings()
    s.clip_sub_lang = req.lang
    s.clips_per_video = req.clips
    s.clip_target_seconds = req.target
    s.clip_crop_mode = req.crop

    def job():
        return run_clip_pipeline(req.url, settings=s)

    return {"task": _start_task("clip", job)}


@app.post("/api/tiktok")
def api_tiktok(req: TikTokReq) -> dict:
    from benchmark import tiktok_stats as tt

    def job():
        out = []
        for target in req.accounts:
            stats = tt.fetch_account(target, limit=req.limit)
            if stats:
                db.store_tiktok_stats([x.as_row() for x in stats])
                out.append(tt.summarize(stats))
        return out

    return {"task": _start_task("tiktok", job)}


@app.get("/api/task/{tid}")
def api_task(tid: str) -> dict:
    with _LOCK:
        t = _TASKS.get(tid)
    if not t:
        raise HTTPException(404, "unknown task")
    return t


@app.get("/api/logs")
def api_logs(tail: int = 60) -> dict:
    return {"lines": list(_LOG_BUF)[-tail:]}


# ---- queue / review ------------------------------------------------------
@app.get("/api/queue")
def api_queue(status: str | None = None) -> dict:
    st = QueueStatus(status) if status else None
    return {"items": db.list_queue(status=st)}


@app.post("/api/queue/{qid}/approve")
def api_approve(qid: int) -> dict:
    db.set_queue_status(qid, QueueStatus.approved)
    return {"ok": True}


@app.post("/api/queue/{qid}/reject")
def api_reject(qid: int, req: RejectReq) -> dict:
    db.set_queue_status(qid, QueueStatus.rejected, reason=req.reason)
    return {"ok": True}


@app.get("/api/video/{qid}")
def api_video(qid: int) -> FileResponse:
    entry = db.get_queue_entry(qid)
    if not entry:
        raise HTTPException(404, "not found")
    path = Path(entry["video_path"])
    if not path.exists():
        raise HTTPException(404, "video file missing")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/doctor")
def api_doctor() -> dict:
    from faceless_engine.ffmpeg_utils import FFmpegNotFoundError, find_ffmpeg

    s = get_settings()
    try:
        ffmpeg, _ = find_ffmpeg()
        ff = True
    except FFmpegNotFoundError:
        ff = False
    counts = {st.value: len(db.list_queue(status=st)) for st in QueueStatus}
    return {
        "ffmpeg": ff,
        "llm": s.llm_base_url if s.llm_api_key else "template (no key)",
        "llm_model": s.llm_model if s.llm_api_key else "-",
        "image_provider": s.image_provider,
        "output_spec": f"{s.width}x{s.height}@{s.fps}",
        "tiktok_mode": s.tiktok_mode,
        "youtube_privacy": s.youtube_privacy,
        "queue_counts": counts,
    }
