"""YouTube Data API v3 uploader with resumable upload.

Defaults to ``private`` visibility (SAFE). Set ``FE_YOUTUBE_PRIVACY=public``
(or pass mode="public") to publish openly.

First run performs an OAuth installed-app flow using your client-secrets JSON
and caches the token to ``YOUTUBE_TOKEN_FILE`` for subsequent runs.

Docs: https://developers.google.com/youtube/v3/guides/uploading_a_video
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from faceless_engine.config import Settings, get_settings

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_API_NAME = "youtube"
_API_VERSION = "v3"


@dataclass
class UploadOutcome:
    ok: bool
    remote_id: str | None = None
    message: str = ""


class YouTubeError(RuntimeError):
    pass


def _load_credentials(settings: Settings):
    """Load cached creds or run the OAuth installed-app flow; refresh as needed."""
    # Imported lazily so the rest of the package doesn't hard-depend on google libs.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_file = Path(settings.youtube_token_file)
    secrets_file = Path(settings.youtube_client_secrets)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing YouTube OAuth token")
            creds.refresh(Request())
        else:
            if not secrets_file.exists():
                raise YouTubeError(
                    f"YouTube client-secrets file not found: {secrets_file}. "
                    "Create OAuth 'Desktop app' credentials in Google Cloud Console "
                    "and point YOUTUBE_CLIENT_SECRETS at the downloaded JSON."
                )
            log.info("Starting YouTube OAuth flow (a browser window will open)")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), _SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build_service(settings: Settings):
    from googleapiclient.discovery import build

    creds = _load_credentials(settings)
    return build(_API_NAME, _API_VERSION, credentials=creds, cache_discovery=False)


class YouTubeUploader:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=60),
        reraise=True,
    )
    def upload(
        self,
        video_path: Path,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        mode: str | None = None,
        category_id: str = "22",  # "People & Blogs"
    ) -> UploadOutcome:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"video not found: {video_path}")

        privacy = (mode or self.settings.youtube_privacy).lower()
        if privacy not in ("private", "unlisted", "public"):
            raise ValueError(f"invalid youtube privacy: {privacy}")

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags or [],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        service = _build_service(self.settings)
        media = MediaFileUpload(
            str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4"
        )
        log.info("YouTube upload starting (privacy=%s): %s", privacy, video_path.name)

        try:
            request = service.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    log.info("YouTube upload progress: %d%%", int(status.progress() * 100))
        except HttpError as exc:
            log.error("YouTube API error: %s", exc)
            raise YouTubeError(str(exc)) from exc

        video_id = response.get("id")
        log.info("YouTube upload complete: id=%s privacy=%s", video_id, privacy)
        return UploadOutcome(ok=True, remote_id=video_id, message=f"privacy={privacy}")


def upload_to_youtube(
    video_path: Path,
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    mode: str | None = None,
    settings: Settings | None = None,
) -> UploadOutcome:
    """Convenience wrapper."""
    return YouTubeUploader(settings).upload(
        video_path, title=title, description=description, tags=tags, mode=mode
    )
