"""LLM-judge virality scoring: rank candidate highlights, keep the best.

Posting a weak clip into a pay-per-view campaign is free work (videos under
the campaign's view floor earn nothing), so the pipeline over-generates
candidates (clip_candidates) and only cuts the top clips_per_video by score.

Without an LLM key the ranking is a no-op: original order, score=None.
"""
from __future__ import annotations

import json
import logging
import re

from clipper.highlights import Highlight
from faceless_engine import llm as llm_mod
from faceless_engine.config import Settings, get_settings

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are a short-form video strategist who predicts how well clips will "
    "perform on TikTok/Reels/Shorts. You return only valid JSON."
)


def _prompt(items: list[dict]) -> str:
    return f"""Score each candidate clip 0-100 for short-form virality.

Judge four factors (25 points each):
- HOOK: would the first seconds stop a scroll? (problem-first/curiosity beats bland)
- PAYOFF: does it deliver a complete, satisfying takeaway on its own?
- CLARITY: understandable with no outside context, works with captions only?
- SHARE: would a viewer send this to a friend or comment on it?

Be harsh: reserve 80+ for genuinely exceptional moments; average clips get 40-60.

CANDIDATES:
{json.dumps(items, ensure_ascii=False, indent=1)}

Return ONLY JSON:
{{"scores": [{{"index": 0, "score": 72, "reason": "<=15 words why"}}]}}"""


def rank_highlights(
    highlights: list[Highlight],
    excerpts: list[str],
    settings: Settings | None = None,
) -> list[Highlight]:
    """Attach scores and return highlights sorted best-first.

    ``excerpts`` are the spoken words inside each candidate's window (same
    order as ``highlights``). On any LLM failure the input order is returned.
    """
    settings = settings or get_settings()
    if len(highlights) <= 1 or not settings.llm_api_key:
        return highlights

    items = [
        {
            "index": i,
            "title": hl.title,
            "hook": hl.hook,
            "duration_s": round(hl.duration, 1),
            "transcript_excerpt": (excerpts[i] if i < len(excerpts) else "")[:600],
        }
        for i, hl in enumerate(highlights)
    ]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _prompt(items)},
    ]
    try:
        content = llm_mod.chat(messages, settings, temperature=0.2, max_tokens=600)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(match.group(0) if match else content)
        for row in data.get("scores", []):
            i = int(row["index"])
            if 0 <= i < len(highlights):
                highlights[i].score = max(0.0, min(100.0, float(row["score"])))
                highlights[i].score_reason = str(row.get("reason", ""))[:200]
        ranked = sorted(
            highlights, key=lambda h: h.score if h.score is not None else -1, reverse=True
        )
        log.info(
            "Virality ranking: %s",
            [f"{h.score:.0f} {h.title[:40]!r}" for h in ranked if h.score is not None],
        )
        return ranked
    except Exception:
        log.exception("Virality scoring failed — keeping original order")
        return highlights
