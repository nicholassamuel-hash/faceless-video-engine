"""TikTok Content Posting API uploader.

Two modes:
* ``draft``  — upload to the user's TikTok inbox; they finish/post in the app
               (SAFE default — nothing is published automatically).
* ``direct`` — direct post (auto-publish). Requires your app to have the
               ``video.publish`` scope approved by TikTok.

Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

The HTTP flow below follows TikTok's documented init + chunked-upload steps.
Some values (privacy level, allowed interactions) depend on your app's audit
status and are marked ``TODO`` where you must confirm against your account.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from faceless_engine.config import Settings, get_settings

log = logging.getLogger(__name__)

_API_BASE = "https://open.tiktokapis.com/v2"
# TikTok requires a single chunk for files < 5MB, and recommends chunks of
# 5-64MB otherwise. We use one chunk per request up to this size.
_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


@dataclass
class UploadOutcome:
    ok: bool
    remote_id: str | None = None
    message: str = ""


class TikTokError(RuntimeError):
    pass


class TikTokUploader:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.token = self.settings.tiktok_access_token
        if not self.token:
            raise TikTokError(
                "TIKTOK_ACCESS_TOKEN is not set. Obtain a user access token with "
                "the video.upload (draft) or video.publish (direct) scope."
            )

    # -- public ------------------------------------------------------------
    def upload(self, video_path: Path, *, title: str, mode: str | None = None) -> UploadOutcome:
        mode = (mode or self.settings.tiktok_mode).lower()
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"video not found: {video_path}")
        size = video_path.stat().st_size

        if mode == "direct":
            publish_id, upload_url = self._init_direct(title, size)
        else:
            publish_id, upload_url = self._init_inbox(size)

        self._upload_bytes(upload_url, video_path, size)
        log.info("TikTok %s upload complete: publish_id=%s", mode, publish_id)
        return UploadOutcome(ok=True, remote_id=publish_id, message=f"mode={mode}")

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _source_info(self, size: int) -> dict:
        return {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": min(size, _CHUNK_SIZE),
            "total_chunk_count": max(1, -(-size // _CHUNK_SIZE)),  # ceil div
        }

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _init_inbox(self, size: int) -> tuple[str, str]:
        """Initialize a DRAFT (inbox) upload. Returns (publish_id, upload_url)."""
        url = f"{_API_BASE}/post/publish/inbox/video/init/"
        payload = {"source_info": self._source_info(size)}
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        self._raise_for_api_error(data)
        d = data["data"]
        return d["publish_id"], d["upload_url"]

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _init_direct(self, title: str, size: int) -> tuple[str, str]:
        """Initialize a DIRECT POST. Returns (publish_id, upload_url)."""
        url = f"{_API_BASE}/post/publish/video/init/"
        # TODO: Confirm these post_info fields against your app's audit status.
        #   - privacy_level must be one of the values returned by the
        #     creator_info/query endpoint for THIS user (e.g. "SELF_ONLY",
        #     "MUTUAL_FOLLOW_FRIENDS", "PUBLIC_TO_EVERYONE"). Unaudited apps are
        #     typically restricted to "SELF_ONLY".
        #   - disable_comment / disable_duet / disable_stitch as desired.
        post_info = {
            "title": title[:150],
            "privacy_level": "SELF_ONLY",  # TODO: set per creator_info query
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        }
        payload = {"post_info": post_info, "source_info": self._source_info(size)}
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        self._raise_for_api_error(data)
        d = data["data"]
        return d["publish_id"], d["upload_url"]

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _upload_bytes(self, upload_url: str, video_path: Path, size: int) -> None:
        """PUT the file bytes to the signed upload URL (single or chunked)."""
        with httpx.Client(timeout=300) as client, video_path.open("rb") as fh:
            start = 0
            while start < size:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                end = start + len(chunk) - 1
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                }
                resp = client.put(upload_url, headers=headers, content=chunk)
                resp.raise_for_status()
                start = end + 1

    @staticmethod
    def _raise_for_api_error(data: dict) -> None:
        err = data.get("error") or {}
        code = err.get("code")
        if code and code != "ok":
            raise TikTokError(f"TikTok API error: {code} - {err.get('message')}")


def upload_to_tiktok(video_path: Path, *, title: str, mode: str | None = None,
                     settings: Settings | None = None) -> UploadOutcome:
    """Convenience wrapper."""
    return TikTokUploader(settings).upload(video_path, title=title, mode=mode)
