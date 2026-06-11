"""Application configuration via pydantic-settings.

All settings load from environment variables / a local ``.env`` file. Nothing
is hardcoded; secrets live only in the environment. See ``.env.example``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration for the whole engine."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",  # explicit aliases below where useful
        extra="ignore",
        case_sensitive=False,
    )

    # --- Paths -------------------------------------------------------------
    work_dir: Path = Field(default=Path("./work"), alias="FE_WORK_DIR")
    db_url: str = Field(default="sqlite:///faceless.db", alias="FE_DB_URL")

    # --- Language / voices -------------------------------------------------
    language: Literal["id", "en"] = Field(default="id", alias="FE_LANGUAGE")
    voice_id: str = Field(default="id-ID-ArdiNeural", alias="FE_VOICE_ID")
    voice_en: str = Field(default="en-US-AndrewNeural", alias="FE_VOICE_EN")

    # --- Video output ------------------------------------------------------
    width: int = Field(default=1080, alias="FE_WIDTH")
    height: int = Field(default=1920, alias="FE_HEIGHT")
    fps: int = Field(default=30, alias="FE_FPS")
    min_seconds: int = Field(default=30, alias="FE_MIN_SECONDS")
    max_seconds: int = Field(default=50, alias="FE_MAX_SECONDS")
    # Caption look: "pop" (TikTok-style, 2-3 big words, active word pops) or
    # "highlight" (calmer phrase with the active word recolored).
    caption_style: Literal["pop", "highlight"] = Field(default="pop", alias="FE_CAPTION_STYLE")
    # Scroll-stopping hook overlay shown at the top for the first N seconds.
    hook_enabled: bool = Field(default=True, alias="FE_HOOK_ENABLED")
    hook_seconds: float = Field(default=3.2, alias="FE_HOOK_SECONDS")

    # --- Clipping (YouTube -> Shorts) --------------------------------------
    # Defaults tuned from research: viral TikTok clips cluster at ~60s.
    clip_target_seconds: int = Field(default=58, alias="FE_CLIP_TARGET_SECONDS")
    clip_min_seconds: int = Field(default=42, alias="FE_CLIP_MIN_SECONDS")
    clip_max_seconds: int = Field(default=75, alias="FE_CLIP_MAX_SECONDS")
    clips_per_video: int = Field(default=1, alias="FE_CLIPS_PER_VIDEO")
    # "blur" = fit width + blurred fill (keeps whole frame, pro look);
    # "fill" = center-crop to 9:16 (zooms in, loses sides);
    # "smart" = like fill but centered on the detected speaker's face
    #           (needs opencv-python; falls back to center when no face found).
    clip_crop_mode: Literal["blur", "fill", "smart"] = Field(
        default="blur", alias="FE_CLIP_CROP_MODE"
    )
    # Auto-caption language to fetch from YouTube (e.g. "en", "id").
    clip_sub_lang: str = Field(default="en", alias="FE_CLIP_SUB_LANG")
    # Candidate pool for virality ranking: ask the LLM for this many clip
    # candidates, score them, keep the best clips_per_video. Set equal to
    # clips_per_video to disable ranking.
    clip_candidates: int = Field(default=4, alias="FE_CLIP_CANDIDATES")
    # Hook A/B testing: render up to N variants of each selected clip, same cut
    # with a different hook overlay (1 = off). Research: test 3 hook styles.
    clip_hook_variants: int = Field(default=2, alias="FE_CLIP_HOOK_VARIANTS")

    # --- Clipping campaigns (paid pay-per-view programs) --------------------
    # Active campaign name (must exist in campaigns_file) — "" disables.
    campaign: str = Field(default="", alias="FE_CAMPAIGN")
    campaigns_file: Path = Field(
        default=Path("./config/campaigns.json"), alias="FE_CAMPAIGNS_FILE"
    )

    # --- LLM ---------------------------------------------------------------
    # provider: "openai" (OpenAI-compatible incl. OpenRouter/Groq/DeepSeek) or
    # "template" (keyless fallback). "openai" auto-falls back to template when
    # no LLM_API_KEY is set.
    llm_provider: str = Field(default="openai", alias="FE_LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="FE_LLM_BASE_URL")
    llm_model: str = Field(default="openai/gpt-4o-mini", alias="FE_LLM_MODEL")
    # OpenRouter-optional attribution headers (harmless for other providers).
    llm_referer: str = Field(default="https://github.com/faceless-engine", alias="FE_LLM_REFERER")
    llm_title: str = Field(default="faceless-engine", alias="FE_LLM_TITLE")

    # --- Imagery -----------------------------------------------------------
    # provider: "openverse" (keyless real CC photos), "openrouter" (AI image gen
    # via your existing LLM_API_KEY), "http" (your own AI image API via
    # IMAGE_API_KEY), or "placeholder" (ffmpeg gradients).
    image_provider: str = Field(default="openverse", alias="FE_IMAGE_PROVIDER")
    # OpenRouter image-generation model (used when image_provider="openrouter").
    image_or_model: str = Field(
        default="google/gemini-2.5-flash-image", alias="FE_IMAGE_OR_MODEL"
    )
    image_api_key: str = Field(default="", alias="IMAGE_API_KEY")
    image_api_url: str = Field(
        default="https://api.example-image-gen.com/v1/images", alias="FE_IMAGE_API_URL"
    )
    image_model: str = Field(default="default", alias="FE_IMAGE_MODEL")

    # --- Uploader safety ---------------------------------------------------
    tiktok_mode: Literal["draft", "direct"] = Field(default="draft", alias="FE_TIKTOK_MODE")
    youtube_privacy: Literal["private", "unlisted", "public"] = Field(
        default="private", alias="FE_YOUTUBE_PRIVACY"
    )

    # --- Uploader scheduling ----------------------------------------------
    jitter_min_minutes: float = Field(default=8, alias="FE_JITTER_MIN_MINUTES")
    jitter_max_minutes: float = Field(default=45, alias="FE_JITTER_MAX_MINUTES")
    tiktok_max_per_day: int = Field(default=3, alias="FE_TIKTOK_MAX_PER_DAY")
    youtube_max_per_day: int = Field(default=5, alias="FE_YOUTUBE_MAX_PER_DAY")
    instagram_max_per_day: int = Field(default=3, alias="FE_INSTAGRAM_MAX_PER_DAY")

    # --- Instagram Reels (Graph API; requires a Business/Creator account) ---
    # The Graph API publishes Reels from a PUBLIC video URL, not a local file:
    # host work/output behind a public base URL (e.g. a tunnel or bucket) and
    # set FE_IG_VIDEO_BASE_URL; filenames are appended to it.
    ig_access_token: str = Field(default="", alias="IG_ACCESS_TOKEN")
    ig_user_id: str = Field(default="", alias="IG_USER_ID")
    ig_video_base_url: str = Field(default="", alias="FE_IG_VIDEO_BASE_URL")

    # --- Own-performance tracking -------------------------------------------
    # Our TikTok handle for benchmark.own_stats (views -> earnings feedback loop).
    own_tiktok_account: str = Field(default="", alias="FE_OWN_TIKTOK_ACCOUNT")

    # --- TikTok benchmark scraping (yt-dlp) --------------------------------
    # TikTok's account-listing is flaky in yt-dlp; using your logged-in session
    # makes it reliable. Set a browser to read cookies from ("edge"/"chrome"/
    # "firefox" — the browser must be CLOSED on Windows so its cookie DB unlocks),
    # OR point to an exported Netscape cookies.txt (works while the browser runs).
    tiktok_cookies_browser: str = Field(default="", alias="FE_TIKTOK_COOKIES_BROWSER")
    tiktok_cookies_file: Path | str = Field(default="", alias="FE_TIKTOK_COOKIES_FILE")

    # --- Platform credentials ---------------------------------------------
    tiktok_access_token: str = Field(default="", alias="TIKTOK_ACCESS_TOKEN")
    youtube_client_secrets: Path = Field(
        default=Path("./secrets/youtube_client_secret.json"), alias="YOUTUBE_CLIENT_SECRETS"
    )
    youtube_token_file: Path = Field(
        default=Path("./secrets/youtube_token.json"), alias="YOUTUBE_TOKEN_FILE"
    )

    # --- Derived helpers ---------------------------------------------------
    @field_validator("work_dir", mode="after")
    @classmethod
    def _resolve_work_dir(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def default_voice(self) -> str:
        """The voice id for the configured default language."""
        return self.voice_id if self.language == "id" else self.voice_en

    @property
    def assets_dir(self) -> Path:
        return self.work_dir / "assets"

    @property
    def output_dir(self) -> Path:
        return self.work_dir / "output"

    @property
    def db_path(self) -> Path | None:
        """Absolute SQLite file path (None for non-sqlite URLs)."""
        prefix = "sqlite:///"
        if not self.db_url.startswith(prefix):
            return None
        raw = self.db_url[len(prefix):]
        p = Path(raw)
        return p if p.is_absolute() else (self.work_dir / p)

    def resolved_db_url(self) -> str:
        """DB URL with relative sqlite paths anchored under work_dir."""
        p = self.db_path
        if p is None:
            return self.db_url
        p.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{p.as_posix()}"

    def ensure_dirs(self) -> None:
        """Create the working directory tree."""
        for d in (self.work_dir, self.assets_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()
