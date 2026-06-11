"""Clipping-campaign profiles: per-campaign metadata requirements.

Paid clipping programs (konten.com, Whop Content Rewards, ...) each demand
specific hashtags, mentions, CTAs, and disclosure lines — submissions that miss
them get rejected. A profile captures those rules once; the clip pipeline
injects them into every enqueued clip while the active campaign is set
(``FE_CAMPAIGN`` -> name in ``config/campaigns.json``).

The file holds a JSON list of profiles; unknown keys are ignored so the file
can carry human notes (budget, deadlines) without breaking parsing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path

from faceless_engine.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CampaignProfile:
    name: str
    # Payout: IDR per 1k views (konten.com style). 0 = unknown/not set.
    cpm_idr: float = 0.0
    # Views below this don't count toward payout (konten.com: 1000).
    min_views_payout: int = 1000
    required_hashtags: list[str] = field(default_factory=list)
    mention: str = ""  # e.g. "@brandaccount"
    cta: str = ""  # campaign-required call to action line
    disclaimer: str = ""  # e.g. "#ad" or "Edukasi, bukan ajakan investasi"
    platforms: list[str] = field(default_factory=lambda: ["tiktok", "youtube", "instagram"])
    notes: str = ""

    def estimate_idr(self, views: int) -> float:
        """Estimated payout for one video's view count under this campaign."""
        if views < self.min_views_payout:
            return 0.0
        return views / 1000.0 * self.cpm_idr


def load_campaigns(settings: Settings | None = None) -> dict[str, CampaignProfile]:
    """All profiles from campaigns_file keyed by name ({} if file missing)."""
    settings = settings or get_settings()
    path = Path(settings.campaigns_file)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Cannot read campaigns file %s: %s", path, exc)
        return {}
    known = {f.name for f in fields(CampaignProfile)}
    out: dict[str, CampaignProfile] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        profile = CampaignProfile(**{k: v for k, v in entry.items() if k in known})
        out[profile.name] = profile
    return out


def get_active(settings: Settings | None = None) -> CampaignProfile | None:
    """The profile named by settings.campaign, or None (warn if misconfigured)."""
    settings = settings or get_settings()
    if not settings.campaign.strip():
        return None
    profile = load_campaigns(settings).get(settings.campaign.strip())
    if profile is None:
        log.warning(
            "FE_CAMPAIGN=%r not found in %s — clips will NOT carry campaign metadata",
            settings.campaign, settings.campaigns_file,
        )
    else:
        log.info("Active clipping campaign: %s", profile.name)
    return profile


def apply(
    profile: CampaignProfile | None,
    *,
    title: str,
    description: str,
    hashtags: list[str],
) -> tuple[str, str, list[str]]:
    """Inject campaign requirements into clip metadata (no-op without profile).

    Required hashtags go first (platforms truncate long tag lists); mention,
    CTA, and disclaimer are appended to the description on their own lines.
    """
    if profile is None:
        return title, description, hashtags

    required = [t.lstrip("#") for t in profile.required_hashtags]
    seen = {t.lower() for t in required}
    merged = required + [t for t in hashtags if t.lower() not in seen]

    extra = [s for s in (profile.mention, profile.cta, profile.disclaimer) if s]
    if extra:
        description = (description + "\n\n" + "\n".join(extra)).strip()
    return title, description, merged
