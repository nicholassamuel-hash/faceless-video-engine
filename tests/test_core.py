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


# --------------------------------------------------------------------------
# campaign profiles
# --------------------------------------------------------------------------
def _campaign_settings(tmp_path: Path, name: str = "test-camp") -> Settings:
    cfg = tmp_path / "campaigns.json"
    cfg.write_text(json.dumps([{
        "name": name, "cpm_idr": 7500, "min_views_payout": 1000,
        "required_hashtags": ["BrandX", "fyp"], "mention": "@brandx",
        "disclaimer": "Konten promosi berbayar.",
        "ignored_extra_key": "ok",
    }]), encoding="utf-8")
    return Settings(FE_CAMPAIGNS_FILE=str(cfg), FE_CAMPAIGN=name)


def test_campaign_apply_injects_metadata(tmp_path: Path):
    from shared import campaigns as camp

    profile = camp.get_active(_campaign_settings(tmp_path))
    assert profile is not None
    title, desc, tags = camp.apply(
        profile, title="T", description="D", hashtags=["fyp", "crypto"]
    )
    assert tags[:2] == ["BrandX", "fyp"]      # required first
    assert tags.count("fyp") == 1             # deduped (case-insensitive)
    assert "crypto" in tags
    assert "@brandx" in desc and "promosi" in desc


def test_campaign_apply_noop_without_profile():
    from shared import campaigns as camp

    out = camp.apply(None, title="T", description="D", hashtags=["a"])
    assert out == ("T", "D", ["a"])


def test_campaign_missing_file_and_earnings(tmp_path: Path):
    from shared import campaigns as camp

    s = Settings(FE_CAMPAIGNS_FILE=str(tmp_path / "nope.json"), FE_CAMPAIGN="x")
    assert camp.load_campaigns(s) == {}
    assert camp.get_active(s) is None

    profile = camp.get_active(_campaign_settings(tmp_path))
    assert profile.estimate_idr(500) == 0          # below payout floor
    assert profile.estimate_idr(10_000) == 75_000  # 10k views * Rp7500/1k


# --------------------------------------------------------------------------
# virality ranking + hook variants
# --------------------------------------------------------------------------
def test_rank_highlights_passthrough_without_llm():
    from clipper.highlights import Highlight
    from clipper.score import rank_highlights

    hls = [Highlight(0, 30, "a"), Highlight(40, 70, "b")]
    out = rank_highlights(hls, ["x", "y"], settings=Settings(LLM_API_KEY=""))
    assert [h.title for h in out] == ["a", "b"]
    assert all(h.score is None for h in out)


def test_hook_variants_dedupe_and_floor():
    from clipper.highlights import Highlight

    hl = Highlight(0, 30, "t", hook="A", hooks=["A", "A", "B", "C"])
    assert hl.hook_variants(3) == ["A", "B", "C"]
    assert hl.hook_variants(1) == ["A"]
    assert Highlight(0, 30, "t").hook_variants(3) == [""]  # no hooks -> one pass


# --------------------------------------------------------------------------
# smart crop chain
# --------------------------------------------------------------------------
def test_vertical_chain_smart_centers_on_face():
    from clipper.crop import _vertical_chain

    chain = _vertical_chain(1080, 1920, "smart", center_frac=0.25)
    assert "0.2500*iw-ow/2" in chain and "max(0\\," in chain
    # without a detected face, smart behaves like a plain center fill
    assert _vertical_chain(1080, 1920, "smart", None) == _vertical_chain(1080, 1920, "fill")
