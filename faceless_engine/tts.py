"""Text-to-speech via Edge-TTS, with word-boundary timestamps for captions.

The public surface is intentionally small and provider-agnostic so the engine
can be swapped:

    tts(text, voice, out_path) -> TTSResult(audio_path, words)

``TTSProvider`` is the swap point; ``EdgeTTSProvider`` is the default concrete
implementation. ``words`` carries per-word start/end times (seconds) suitable
for driving word-level caption highlighting.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from faceless_engine.config import get_settings

log = logging.getLogger(__name__)

# Edge-TTS reports offsets/durations in 100-nanosecond ticks.
_TICKS_PER_SECOND = 10_000_000


@dataclass
class WordTiming:
    """A single spoken word with its time window in the audio."""

    word: str
    start: float  # seconds
    end: float  # seconds

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class TTSResult:
    audio_path: Path
    words: list[WordTiming] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.words[-1].end if self.words else 0.0


class TTSProvider(ABC):
    """Swappable TTS backend interface."""

    @abstractmethod
    async def synthesize(self, text: str, voice: str, out_path: Path) -> TTSResult:
        ...


class EdgeTTSProvider(TTSProvider):
    """Default backend using Microsoft Edge online neural voices (free, no key)."""

    def __init__(self, rate: str = "+0%", volume: str = "+0%", pitch: str = "+0Hz"):
        self.rate = rate
        self.volume = volume
        self.pitch = pitch

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def synthesize(self, text: str, voice: str, out_path: Path) -> TTSResult:
        text = (text or "").strip()
        if not text:
            raise ValueError("tts: empty text")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
            # edge-tts defaults to SentenceBoundary; we need per-word timings.
            boundary="WordBoundary",
        )

        words: list[WordTiming] = []
        log.info("TTS synthesizing %d chars with voice=%s", len(text), voice)
        with out_path.open("wb") as fh:
            async for chunk in communicate.stream():
                ctype = chunk.get("type")
                if ctype == "audio":
                    fh.write(chunk["data"])
                elif ctype == "WordBoundary":
                    start = chunk["offset"] / _TICKS_PER_SECOND
                    dur = chunk["duration"] / _TICKS_PER_SECOND
                    words.append(
                        WordTiming(word=chunk["text"], start=start, end=start + dur)
                    )

        if out_path.stat().st_size == 0:
            raise RuntimeError("tts: edge-tts produced no audio (check network / voice id)")

        log.info("TTS done: %s (%d words, %.2fs)", out_path.name, len(words),
                 words[-1].end if words else 0.0)
        return TTSResult(audio_path=out_path, words=words)


# Module-level default provider (swap by assigning a different TTSProvider).
default_provider: TTSProvider = EdgeTTSProvider()


async def tts_async(
    text: str,
    voice: str | None = None,
    out_path: Path | str = "narration.mp3",
    provider: TTSProvider | None = None,
) -> TTSResult:
    """Async TTS. Returns audio path + word timings."""
    settings = get_settings()
    voice = voice or settings.default_voice
    provider = provider or default_provider
    return await provider.synthesize(text, voice, Path(out_path))


def tts(
    text: str,
    voice: str | None = None,
    out_path: Path | str = "narration.mp3",
    provider: TTSProvider | None = None,
) -> TTSResult:
    """Synchronous convenience wrapper around :func:`tts_async`."""
    return asyncio.run(tts_async(text, voice, out_path, provider))


async def list_voices(language: str | None = None) -> list[dict]:
    """Return available Edge-TTS voices, optionally filtered by language prefix."""
    voices = await edge_tts.list_voices()
    if language:
        voices = [v for v in voices if v.get("Locale", "").lower().startswith(language.lower())]
    return voices
