"""ffmpeg / ffprobe discovery and invocation helpers (Windows-friendly).

Every module that shells out to ffmpeg goes through here so we get one place
for binary detection, clear install hints, and consistent subprocess handling.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_INSTALL_HINT = (
    "ffmpeg/ffprobe were not found on PATH.\n"
    "Install the binaries and reopen your terminal:\n"
    "  winget install Gyan.FFmpeg\n"
    "or download a build from https://www.gyan.dev/ffmpeg/builds/ and add its\n"
    "'bin' directory to your PATH."
)


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot be located on PATH."""


@lru_cache(maxsize=1)
def find_ffmpeg() -> tuple[str, str]:
    """Return absolute paths to (ffmpeg, ffprobe) or raise FFmpegNotFoundError."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FFmpegNotFoundError(_INSTALL_HINT)
    log.debug("Using ffmpeg=%s ffprobe=%s", ffmpeg, ffprobe)
    return ffmpeg, ffprobe


def ensure_ffmpeg() -> None:
    """Validate ffmpeg/ffprobe availability early, with a clear error."""
    find_ffmpeg()


def run_ffmpeg(args: list[str], *, desc: str = "ffmpeg") -> None:
    """Run ffmpeg with the given args (excluding the binary itself).

    ``-y -hide_banner -loglevel error`` are prepended. Raises CalledProcessError
    with stderr captured (no silent swallowing) on failure.
    """
    ffmpeg, _ = find_ffmpeg()
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args]
    log.debug("Running %s: %s", desc, " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("%s failed (exit %s): %s", desc, proc.returncode, proc.stderr.strip())
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
        )


def probe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    _, ffprobe = find_ffmpeg()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffprobe failed for %s: %s", path, proc.stderr.strip())
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=proc.stderr)
    data = json.loads(proc.stdout)
    return float(data["format"]["duration"])
