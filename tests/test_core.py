"""Offline unit tests for core logic (no network / no ffmpeg required).

Run:  python -m pytest tests/ -q
"""
from __future__ import annotations

import json
from pathlib import Path

from faceless_engine.config import Settings
from faceless_engine.tts import WordTiming


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def test_default_voice_by_language():
    assert Settings(FE_LANGUAGE="id").default_voice == "id-ID-ArdiNeural"
    assert Settings(FE_LANGUAGE="en").default_voice == "en-US-AndrewNeural"


def test_resolved_db_url_anchors_under_workdir(tmp_path: Path):
    s = Settings(FE_WORK_DIR=str(tmp_path), FE_DB_URL="sqlite:///faceless.db")
    url = s.resolved_db_url()
    assert url.startswith("sqlite:///")
    assert "faceless.db" in url
    # relative sqlite path is anchored under work_dir
    assert tmp_path.as_posix() in url


# --------------------------------------------------------------------------
# captions (ASS generation) — regression guard for the 10-field Format bug
# --------------------------------------------------------------------------
def test_fmt_time():
    from faceless_engine.captions import _fmt_time

    assert _fmt_time(0) == "0:00:00.00"
    assert _fmt_time(61.5) == "0:01:01.50"
    assert _fmt_time(3661.0) == "1:01:01.00"


def test_build_ass_structure(tmp_path: Path):
    from faceless_engine.captions import build_ass

    words = [
        WordTiming("Halo", 0.0, 0.5),
        WordTiming("dunia", 0.5, 1.0),
        WordTiming("ini", 1.0, 1.4),
        WordTiming("tes", 1.4, 1.9),
        WordTiming("caption", 1.9, 2.5),
    ]
    out = build_ass(words, tmp_path / "c.ass", settings=Settings(), max_words_per_line=4)
    text = out.read_text(encoding="utf-8")

    # Format line must be the canonical 10-field layout (the bug we fixed:
    # an 8-field Format leaked "0,," into the rendered caption text).
    fmt = next(l for l in text.splitlines() if l.startswith("Format:") and "Text" in l)
    assert "Name" in fmt and "MarginV" in fmt
    assert fmt.count(",") == 9  # 10 fields -> 9 commas

    # One Dialogue per word; highlight override present.
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(words)
    assert "\\c" in text  # colour override for the active word


def test_build_ass_rejects_empty():
    import pytest

    from faceless_engine.captions import build_ass

    with pytest.raises(ValueError):
        build_ass([], Path("x.ass"), settings=Settings())


# --------------------------------------------------------------------------
# clipper transcript (json3 parsing + range/rebasing)
# --------------------------------------------------------------------------
def _fake_json3(tmp_path: Path) -> Path:
    data = {
        "events": [
            {"tStartMs": 1000, "segs": [{"utf8": "satu", "tOffsetMs": 0},
                                         {"utf8": " dua", "tOffsetMs": 500}]},
            {"tStartMs": 2000, "segs": [{"utf8": "\n"}]},  # ignored
            {"tStartMs": 2200, "segs": [{"utf8": "tiga", "tOffsetMs": 0}]},
        ]
    }
    p = tmp_path / "subs.json3"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_parse_json3_word_timing(tmp_path: Path):
    from clipper.transcript import parse_json3

    words = parse_json3(_fake_json3(tmp_path))
    assert [w.word for w in words] == ["satu", "dua", "tiga"]
    assert words[0].start == 1.0
    assert words[1].start == 1.5          # 1000 + 500 ms
    assert words[0].end == words[1].start  # end filled from next word
    assert words[2].start == 2.2


def test_words_in_range_rebases(tmp_path: Path):
    from clipper.transcript import parse_json3, words_in_range

    words = parse_json3(_fake_json3(tmp_path))
    sub = words_in_range(words, 1.4, 3.0)   # should grab "dua" (1.5) and "tiga" (2.2)
    assert [w.word for w in sub] == ["dua", "tiga"]
    assert sub[0].start == 1.5 - 1.4        # rebased so clip starts at 0


# --------------------------------------------------------------------------
# clipper highlight duration fitting
# --------------------------------------------------------------------------
def test_fit_durations_extends_short_clip():
    from clipper.highlights import Highlight, fit_durations

    words = [WordTiming(f"w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(200)]  # 0..200s
    settings = Settings(FE_CLIP_TARGET_SECONDS=55, FE_CLIP_MIN_SECONDS=40, FE_CLIP_MAX_SECONDS=75)
    hl = Highlight(start=10.0, end=30.0, title="x")  # 20s -> should grow toward 55s
    fit_durations([hl], words, settings=settings)
    assert hl.duration >= 50  # extended near target (snapped to word edges)


def test_fit_durations_trims_long_clip():
    from clipper.highlights import Highlight, fit_durations

    words = [WordTiming(f"w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(200)]
    settings = Settings(FE_CLIP_TARGET_SECONDS=55, FE_CLIP_MIN_SECONDS=40, FE_CLIP_MAX_SECONDS=75)
    hl = Highlight(start=10.0, end=120.0, title="x")  # 110s -> trim to <= 75
    fit_durations([hl], words, settings=settings)
    assert hl.duration <= 75


# --------------------------------------------------------------------------
# tiktok stats parsing
# --------------------------------------------------------------------------
def test_tiktok_engagement_and_normalize():
    from benchmark.tiktok_stats import _to_stat, normalize_target

    label, url = normalize_target("khaby.lame")
    assert label == "@khaby.lame" and url.endswith("/@khaby.lame")
    label2, _ = normalize_target("https://www.tiktok.com/@foo/video/123")
    assert label2 == "@foo"

    stat = _to_stat("@acc", {"id": "1", "title": "hi", "view_count": 1000,
                             "like_count": 80, "comment_count": 15, "repost_count": 5})
    assert stat.engagement_rate == 0.1  # (80+15+5)/1000
