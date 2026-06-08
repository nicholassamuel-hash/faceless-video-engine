"""Word-level (TikTok-style) caption generation.

Turns TTS word timings into an ASS subtitle file where the currently-spoken
word is highlighted. ASS is preferred over drawtext because it gives precise
styling (outline, bold, color) and per-word timing with one burn-in filter
(see :func:`faceless_engine.assemble.assemble_video`).
"""
from __future__ import annotations

import logging
from pathlib import Path

from faceless_engine.config import Settings, get_settings
from faceless_engine.tts import WordTiming

log = logging.getLogger(__name__)


def _fmt_time(seconds: float) -> str:
    """Format seconds as ASS timestamp H:MM:SS.cs (centiseconds)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:  # rounding overflow
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    """Escape characters special to ASS event text."""
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _chunk(words: list[WordTiming], max_per_line: int) -> list[list[WordTiming]]:
    return [words[i : i + max_per_line] for i in range(0, len(words), max_per_line)]


def build_ass(
    words: list[WordTiming],
    out_path: Path,
    settings: Settings | None = None,
    *,
    max_words_per_line: int = 4,
    font: str = "Arial",
    base_color: str = "&H00FFFFFF",  # white  (AABBGGRR)
    highlight_color: str = "&H0000F0FF",  # bright yellow
    outline_color: str = "&H00000000",  # black
) -> Path:
    """Write a word-highlighted ASS file and return its path.

    Each spoken word produces one Dialogue event over its time window, showing
    its phrase with the active word recolored. Empty/whitespace words are
    skipped. Raises ValueError if there are no usable words.
    """
    settings = settings or get_settings()
    words = [w for w in words if w.word and w.word.strip()]
    if not words:
        raise ValueError("build_ass: no word timings provided")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = settings.width, settings.height
    # Font size and bottom margin scaled to the canvas height.
    font_size = max(40, int(h * 0.045))
    margin_v = int(h * 0.18)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{base_color},{base_color},{outline_color},&H64000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    lines = _chunk(words, max_words_per_line)
    for line in lines:
        line_start = line[0].start
        line_end = line[-1].end
        for i, active in enumerate(line):
            # Active word window: from its start to the next word's start so
            # the highlight advances smoothly; last word holds until line end.
            start = active.start
            end = line[i + 1].start if i + 1 < len(line) else line_end
            if end <= start:
                end = start + 0.05

            rendered = []
            for j, wd in enumerate(line):
                token = _ass_escape(wd.word.strip())
                if j == i:
                    rendered.append(f"{{\\c{highlight_color}\\b1}}{token}{{\\r}}")
                else:
                    rendered.append(token)
            text = " ".join(rendered)
            events.append(
                f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Default,,0,0,0,,{text}"
            )

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log.info("Captions: %d words across %d lines -> %s", len(words), len(lines), out_path.name)
    return out_path
