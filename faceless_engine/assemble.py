"""Final assembly: concat motion clips + audio + burned captions -> MP4.

Output spec: 9:16, normalized to ``settings.width x settings.height`` (default
1080x1920), H.264 / yuv420p video, AAC audio, +faststart for streaming.

The heavy lifting is one ffmpeg invocation using the concat demuxer for the
scene clips, the second input for narration audio, and an ``ass``/``subtitles``
filter for the burned-in word-level captions.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from faceless_engine.config import Settings, get_settings
from faceless_engine.ffmpeg_utils import probe_duration, run_ffmpeg

log = logging.getLogger(__name__)


def _escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an ffmpeg filter argument.

    ffmpeg filter syntax treats ``\\`` and ``:`` specially. On Windows we convert
    backslashes to forward slashes and escape the drive-letter colon, which is
    the combination that reliably works for the ass/subtitles filters.
    """
    p = str(Path(path).resolve()).replace("\\", "/")
    p = p.replace(":", r"\:")
    # Escape single quotes too, just in case they appear in the path.
    p = p.replace("'", r"\'")
    return p


def _write_concat_file(clips: list[Path], dest: Path) -> Path:
    """Write an ffmpeg concat-demuxer list file referencing each clip."""
    lines = []
    for c in clips:
        abs_path = str(Path(c).resolve()).replace("\\", "/")
        # concat demuxer: single-quote the path and escape embedded quotes.
        abs_path = abs_path.replace("'", r"'\''")
        lines.append(f"file '{abs_path}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def assemble_video(
    clips: list[Path],
    audio_path: Path,
    out_path: Path,
    subtitles_path: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """Assemble the final vertical MP4 and return its path.

    Args:
        clips: ordered Ken Burns motion clips (same codec/size/fps).
        audio_path: narration audio track.
        out_path: destination .mp4.
        subtitles_path: optional .ass (preferred) or .srt to burn in.
        settings: override settings (defaults to global).
    """
    settings = settings or get_settings()
    if not clips:
        raise ValueError("assemble_video: no clips provided")
    for c in clips:
        if not Path(c).exists():
            raise FileNotFoundError(f"clip missing: {c}")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"audio missing: {audio_path}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h, fps = settings.width, settings.height, settings.fps

    # Build the video filter chain: normalize to WxH (letterbox), set SAR,
    # then optionally burn subtitles.
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"fps={fps}",
    ]
    if subtitles_path is not None:
        sp = Path(subtitles_path)
        if not sp.exists():
            raise FileNotFoundError(f"subtitles missing: {sp}")
        esc = _escape_filter_path(sp)
        if sp.suffix.lower() == ".ass":
            vf_parts.append(f"ass='{esc}'")
        else:
            vf_parts.append(f"subtitles='{esc}'")
    vf = ",".join(vf_parts)

    with tempfile.TemporaryDirectory(prefix="fe_assemble_") as tmp:
        concat_file = _write_concat_file(clips, Path(tmp) / "clips.txt")
        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-i", str(Path(audio_path).resolve()),
            "-vf", vf,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-shortest",
            "-movflags", "+faststart",
            str(out_path),
        ]
        log.info("Assembling %d clips -> %s", len(clips), out_path.name)
        run_ffmpeg(args, desc="assemble")

    dur = probe_duration(out_path)
    log.info("Assembled %s (%.2fs)", out_path, dur)
    if not (settings.min_seconds - 5 <= dur <= settings.max_seconds + 10):
        log.warning(
            "Final duration %.1fs is outside target %d-%ds",
            dur, settings.min_seconds, settings.max_seconds,
        )
    return out_path
