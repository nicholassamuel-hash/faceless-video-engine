"""Parse YouTube json3 auto-captions into word-level timings.

json3 structure: ``events[].segs[]`` where each event has ``tStartMs`` and each
seg has ``utf8`` (the text) and ``tOffsetMs`` (offset from the event start).
Word start = (tStartMs + tOffsetMs) / 1000; word end = next word's start.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from faceless_engine.tts import WordTiming

log = logging.getLogger(__name__)


def parse_json3(path: Path) -> list[WordTiming]:
    """Return ordered WordTimings from a json3 caption file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    words: list[WordTiming] = []
    for event in data.get("events", []):
        base = event.get("tStartMs", 0)
        for seg in event.get("segs", []) or []:
            text = (seg.get("utf8") or "").strip()
            if not text or text == "\n":
                continue
            start = (base + seg.get("tOffsetMs", 0)) / 1000.0
            words.append(WordTiming(word=text, start=start, end=start))

    # Fill each word's end with the next word's start (last gets +0.4s).
    for i in range(len(words) - 1):
        words[i].end = max(words[i].start, words[i + 1].start)
    if words:
        words[-1].end = words[-1].start + 0.4

    log.info("Parsed %d caption words from %s", len(words), Path(path).name)
    return words


def words_in_range(words: list[WordTiming], start: float, end: float) -> list[WordTiming]:
    """Words whose start falls within [start, end), re-based so clip starts at 0."""
    out = []
    for w in words:
        if start <= w.start < end:
            out.append(
                WordTiming(word=w.word, start=w.start - start, end=min(w.end, end) - start)
            )
    return out


def transcript_lines(words: list[WordTiming], words_per_line: int = 12) -> str:
    """Render the transcript as timestamped lines for an LLM to scan.

    Each line is prefixed with its start time in seconds, e.g.::

        [18.8] We're no strangers to love you know the rules and so do I
    """
    lines: list[str] = []
    for i in range(0, len(words), words_per_line):
        chunk = words[i : i + words_per_line]
        if not chunk:
            continue
        text = " ".join(w.word for w in chunk)
        lines.append(f"[{chunk[0].start:.1f}] {text}")
    return "\n".join(lines)
