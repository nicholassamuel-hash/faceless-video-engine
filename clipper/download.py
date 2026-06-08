"""yt-dlp wrapper: fetch source video metadata, the video file, and json3 subs.

Uses yt-dlp as a subprocess (via the current interpreter's ``-m yt_dlp``) so we
stay decoupled from its Python API churn. ffmpeg (already required) is used by
yt-dlp for muxing.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


@dataclass
class SourceVideo:
    video_id: str
    title: str
    duration: float
    url: str
    video_path: Path
    subs_path: Path | None  # json3 auto-captions, if available
    language: str | None


def _run(args: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "yt_dlp", *args]
    log.debug("yt-dlp: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc


def fetch_info(url: str) -> dict:
    """Return yt-dlp's metadata JSON for a URL (no download)."""
    proc = _run(["-J", "--skip-download", "--no-warnings", url], timeout=120)
    if proc.returncode != 0:
        raise DownloadError(f"yt-dlp info failed: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


@retry(
    retry=retry_if_exception_type(DownloadError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=20),
    reraise=True,
)
def download(url: str, out_dir: Path, sub_lang: str = "en") -> SourceVideo:
    """Download the video (<=1080p mp4) and its json3 auto-captions."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = fetch_info(url)
    vid = info.get("id", "video")
    title = info.get("title", vid)
    duration = float(info.get("duration") or 0)
    language = info.get("language")

    out_tmpl = str(out_dir / "%(id)s.%(ext)s")
    # Prefer <=1080p mp4; merge bestvideo+bestaudio, fall back to best single.
    fmt = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[ext=mp4]/b"
    args = [
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-auto-subs",
        "--sub-langs", sub_lang,
        "--sub-format", "json3",
        "--no-warnings",
        "-o", out_tmpl,
        url,
    ]
    proc = _run(args, timeout=1800)
    if proc.returncode != 0:
        raise DownloadError(f"yt-dlp download failed: {proc.stderr.strip()[:400]}")

    video_path = out_dir / f"{vid}.mp4"
    if not video_path.exists():
        # Some merges land on .mkv/.webm — pick whatever was produced.
        candidates = [p for p in out_dir.glob(f"{vid}.*") if p.suffix not in (".json3",)]
        candidates = [p for p in candidates if p.suffix in (".mp4", ".mkv", ".webm")]
        if not candidates:
            raise DownloadError(f"no video file produced for {vid}")
        video_path = candidates[0]

    subs = sorted(out_dir.glob(f"{vid}*.json3"))
    subs_path = subs[0] if subs else None
    if subs_path is None:
        log.warning("No auto-captions (%s) for %s — transcript will be empty", sub_lang, vid)

    log.info("Downloaded %r (%.0fs) -> %s (subs=%s)",
             title, duration, video_path.name, bool(subs_path))
    return SourceVideo(
        video_id=vid,
        title=title,
        duration=duration,
        url=url,
        video_path=video_path,
        subs_path=subs_path,
        language=language,
    )
