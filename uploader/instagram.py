"""Instagram Reels upload via the official Graph API (Business/Creator only).

The Content Publishing API cannot take a local file: it pulls the video from a
PUBLIC URL. Host ``work/output`` somewhere reachable (object storage, a tunnel)
and set ``FE_IG_VIDEO_BASE_URL``; the output filename is appended to it.

Flow: create a REELS media container -> poll until processed -> publish.
Raises RuntimeError with a clear message when credentials/hosting are missing,
so the scheduler logs a failed attempt instead of crashing the run.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from faceless_engine.config import Settings, get_settings

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
_POLL_SECONDS = 10
_POLL_TIMEOUT = 300  # Reels processing regularly takes a few minutes


@dataclass
class UploadOutcome:
    remote_id: str | None
    message: str


def _check(resp: httpx.Response) -> dict:
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise RuntimeError(
            f"Graph API {resp.status_code}: {err.get('message', resp.text[:200])}"
        )
    return data


def upload_to_instagram(
    video_path: str,
    *,
    caption: str,
    settings: Settings | None = None,
) -> UploadOutcome:
    """Publish one local output video as a Reel. Returns the IG media id."""
    settings = settings or get_settings()
    if not settings.ig_access_token or not settings.ig_user_id:
        raise RuntimeError(
            "Instagram not configured: set IG_ACCESS_TOKEN and IG_USER_ID "
            "(requires an Instagram Business/Creator account)"
        )
    if not settings.ig_video_base_url:
        raise RuntimeError(
            "FE_IG_VIDEO_BASE_URL not set: the Graph API needs the video at a "
            "public URL; host the output dir and point this at its base URL"
        )

    name = Path(video_path).name
    video_url = settings.ig_video_base_url.rstrip("/") + "/" + name
    params = {"access_token": settings.ig_access_token}

    with httpx.Client(timeout=60) as client:
        # 1. Create the media container.
        data = _check(client.post(
            f"{GRAPH}/{settings.ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption[:2200],
                "share_to_feed": "true",
                **params,
            },
        ))
        container_id = data["id"]
        log.info("IG container %s created for %s", container_id, name)

        # 2. Wait for server-side processing.
        deadline = time.time() + _POLL_TIMEOUT
        while True:
            status = _check(client.get(
                f"{GRAPH}/{container_id}", params={"fields": "status_code", **params}
            )).get("status_code", "IN_PROGRESS")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise RuntimeError(f"IG processing failed for container {container_id}")
            if time.time() > deadline:
                raise RuntimeError(f"IG processing timed out for container {container_id}")
            time.sleep(_POLL_SECONDS)

        # 3. Publish.
        published = _check(client.post(
            f"{GRAPH}/{settings.ig_user_id}/media_publish",
            data={"creation_id": container_id, **params},
        ))

    media_id = published.get("id")
    log.info("IG Reel published: media_id=%s", media_id)
    return UploadOutcome(remote_id=media_id, message=f"reel published from {video_url}")
