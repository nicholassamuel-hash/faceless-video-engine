"""LLM-driven highlight selection from a timestamped transcript.

Given the transcript lines (with start times) the model returns the most
shareable self-contained segments as start/end seconds plus a title + hashtags.
Falls back to a simple even split if no LLM key is configured or parsing fails.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from faceless_engine import llm as llm_mod
from faceless_engine.config import Settings, get_settings
from faceless_engine.tts import WordTiming

log = logging.getLogger(__name__)


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    hashtags: list[str] = field(default_factory=list)
    reason: str = ""
    hook: str = ""  # scroll-stopping on-screen opener (not a literal quote)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


_SYSTEM = (
    "You are an expert short-form video editor who finds the most viral, "
    "self-contained moments inside a longer video's transcript. You return only "
    "valid JSON."
)


def _prompt(title: str, transcript: str, n: int, target: int, lo: int, hi: int) -> str:
    return f"""Source video title: {title}

Below is the timestamped transcript ([seconds] text). Pick the {n} BEST clip(s)
for TikTok/Reels/Shorts: moments that are self-contained, hook fast, and make
someone stop scrolling (strong claim, story beat, punchline, surprising fact).

Each clip must:
- last between {lo} and {hi} seconds (aim ~{target}s),
- start at a natural sentence start and end at a natural stop,
- use timestamps that exist in the transcript.

Also write a "hook": a 3-8 word scroll-stopping ON-SCREEN text overlay for the
first 3 seconds. It must be thematically related to the clip but framed more
dramatically than the literal content — a bold claim, alarming question, or
curiosity gap. It does NOT need to be a quote from the video. Same language as
the transcript. Example: a clip about the economy slowing -> "Indonesia di ambang
krisis?" or "Ini baru awal dari kehancuran".

Return ONLY JSON:
{{
  "clips": [
    {{"start": <sec>, "end": <sec>, "title": "punchy <=80 char title",
      "hook": "3-8 word dramatic hook", "hashtags": ["tag1","tag2"], "reason": "why it pops"}}
  ]
}}

TRANSCRIPT:
{transcript}"""


def _fallback(total: float, n: int, target: int) -> list[Highlight]:
    """Even split when no LLM is available."""
    n = max(1, n)
    clips = []
    for i in range(n):
        start = min(total - target, i * (total / n)) if total > target else 0.0
        start = max(0.0, start)
        clips.append(Highlight(start=start, end=min(total, start + target),
                               title="Clip", hashtags=["shorts", "fyp"]))
    return clips


def _snap_start(words: list[WordTiming], t: float) -> float:
    """Snap to the start of the word nearest to t (don't cut mid-word)."""
    if not words:
        return max(0.0, t)
    return min(words, key=lambda w: abs(w.start - t)).start


def _snap_end(words: list[WordTiming], t: float) -> float:
    if not words:
        return t
    return min(words, key=lambda w: abs(w.end - t)).end


def fit_durations(
    highlights: list[Highlight],
    words: list[WordTiming],
    settings: Settings | None = None,
) -> list[Highlight]:
    """Grow/trim each highlight toward the target length, snapped to word edges.

    The LLM often returns tight 20-30s moments; we extend them (end first, then
    pull the start back near the end of the source) so clips land around the
    configured target, then snap both edges to caption-word boundaries.
    """
    settings = settings or get_settings()
    target, hi = settings.clip_target_seconds, settings.clip_max_seconds
    total = words[-1].end if words else None

    for hl in highlights:
        dur = hl.duration
        if dur > hi:  # too long -> trim the tail
            hl.end = hl.start + hi
        elif dur < target:  # too short -> extend toward target
            new_start, new_end = hl.start, hl.start + target
            if total is not None and new_end > total:
                new_end = total
                new_start = max(0.0, new_end - target)
            if words:
                new_start = _snap_start(words, new_start)
                new_end = _snap_end(words, new_end)
            hl.start, hl.end = new_start, max(new_end, new_start + 1.0)
        log.info("Clip window: %.1f-%.1fs (%.1fs) — %s", hl.start, hl.end, hl.duration, hl.title)
    return highlights


def select_highlights(
    title: str,
    transcript: str,
    total_duration: float,
    settings: Settings | None = None,
) -> list[Highlight]:
    settings = settings or get_settings()
    n = max(1, settings.clips_per_video)
    target = settings.clip_target_seconds
    lo, hi = settings.clip_min_seconds, settings.clip_max_seconds

    if not settings.llm_api_key or not transcript.strip():
        log.warning("No LLM key or empty transcript — using even-split fallback")
        return _fallback(total_duration, n, target)

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _prompt(title, transcript, n, target, lo, hi)},
    ]
    try:
        content = llm_mod.chat(messages, settings, temperature=0.5, max_tokens=1200)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(match.group(0) if match else content)
        clips: list[Highlight] = []
        for c in data.get("clips", []):
            start = float(c["start"])
            end = float(c["end"])
            if end <= start:
                continue
            # Clamp duration into [lo, hi].
            dur = end - start
            if dur > hi:
                end = start + hi
            if end - start < min(lo, total_duration):
                end = min(total_duration, start + lo)
            clips.append(
                Highlight(
                    start=max(0.0, start),
                    end=min(total_duration, end),
                    title=str(c.get("title", "Clip"))[:120],
                    hashtags=[str(h).lstrip("#") for h in c.get("hashtags", [])],
                    reason=str(c.get("reason", "")),
                    hook=str(c.get("hook", ""))[:80],
                )
            )
        clips = [c for c in clips if c.duration >= min(lo, total_duration) * 0.5]
        if not clips:
            raise ValueError("LLM returned no usable clips")
        log.info("LLM selected %d highlight(s)", len(clips))
        return clips[:n]
    except Exception:
        log.exception("Highlight selection failed — using even-split fallback")
        return _fallback(total_duration, n, target)
