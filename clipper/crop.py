"""Cut a highlight, crop/fit to 9:16, and burn word-level captions in one pass."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from faceless_engine import captions as captions_mod
from faceless_engine.assemble import _escape_filter_path
from faceless_engine.config import Settings, get_settings
from faceless_engine.ffmpeg_utils import probe_duration, run_ffmpeg
from faceless_engine.tts import WordTiming

log = logging.getLogger(__name__)


def _vertical_chain(w: int, h: int, mode: str, center_frac: float | None = None) -> str:
    """Filter chain producing a WxH [base] label from input [0:v].

    ``center_frac`` (0..1, smart mode) horizontally centers the crop window on
    the detected speaker; the expression clamps it inside the frame.
    """
    if mode == "smart" and center_frac is not None:
        cf = max(0.0, min(1.0, center_frac))
        # x = clamp(cf*iw - ow/2, 0, iw-ow); commas escaped inside filter args.
        x_expr = f"max(0\\,min(iw-ow\\,{cf:.4f}*iw-ow/2))"
        return (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:{x_expr}:(ih-oh)/2,setsar=1[base]"
        )
    if mode in ("fill", "smart"):  # smart without a face = plain center fill
        return (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[base]"
        )
    # "blur": fit the whole frame on a blurred, darkened, filled background.
    # The background is blurred at LOW resolution then upscaled — a smooth blur
    # for a fraction of the cost of gblur at full 1080x1920.
    bw, bh = w // 4, h // 4
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"gblur=sigma=6,scale={w}:{h},eq=brightness=-0.08[bgb];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )


def make_clip(
    source_path: Path,
    start: float,
    end: float,
    words: list[WordTiming],
    out_path: Path,
    settings: Settings | None = None,
    hook: str | None = None,
) -> Path:
    """Produce one 9:16 captioned clip from [start, end] of the source video."""
    settings = settings or get_settings()
    source_path, out_path = Path(source_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h, fps = settings.width, settings.height, settings.fps
    duration = max(1.0, end - start)
    mode = settings.clip_crop_mode

    center_frac = None
    if mode == "smart":
        from clipper.facetrack import face_center_fraction

        center_frac = face_center_fraction(source_path, start, end)
    chain = _vertical_chain(w, h, mode, center_frac)

    with tempfile.TemporaryDirectory(prefix="fe_clip_") as tmp:
        # Build the video filter graph, optionally appending the caption burn.
        use_hook = hook if settings.hook_enabled else None
        if words or use_hook:
            subs_path = Path(tmp) / "clip.ass"
            captions_mod.build_ass(words, subs_path, settings=settings, hook=use_hook)
            esc = _escape_filter_path(subs_path)
            filtergraph = f"{chain};[base]ass='{esc}'[v]"
        else:
            filtergraph = f"{chain}[v]"
            log.warning("Clip has no caption words; burning none")

        args = [
            "-ss", f"{start:.3f}",
            "-i", str(source_path.resolve()),
            "-t", f"{duration:.3f}",
            "-filter_complex", filtergraph,
            "-map", "[v]",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            str(out_path),
        ]
        log.info("Cutting clip %.1f-%.1fs (%s) -> %s", start, end, mode, out_path.name)
        run_ffmpeg(args, desc="clip")

    log.info("Clip done: %s (%.1fs)", out_path.name, probe_duration(out_path))
    return out_path
