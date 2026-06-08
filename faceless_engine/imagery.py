"""Per-scene image generation + Ken Burns motion clips.

Image generation is hidden behind the :class:`ImageProvider` interface so the
backend is swappable. Two concrete providers ship:

* :class:`HTTPImageProvider` — talks to a real text-to-image HTTP API using
  ``IMAGE_API_KEY``. The actual request is left as a clearly marked ``TODO``
  with the expected request shape.
* :class:`PlaceholderImageProvider` — renders a gradient placeholder via ffmpeg
  so the whole pipeline runs end-to-end with no API key (useful for the first
  video / smoke tests).

``ken_burns(image, duration, out)`` turns a still into a slow pan/zoom clip via
the ffmpeg ``zoompan`` filter.
"""
from __future__ import annotations

import base64
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from faceless_engine.config import Settings, get_settings
from faceless_engine.ffmpeg_utils import run_ffmpeg

log = logging.getLogger(__name__)


@dataclass
class Scene:
    """A single narrated beat: spoken text + visual hints."""

    index: int
    text: str
    image_prompt: str  # vivid description (used by AI image providers)
    image_query: str = ""  # short EN keywords (used for stock photo search)
    duration: float = 0.0  # seconds; filled in from TTS timing


class ImageProvider(ABC):
    """Swappable image-generation backend."""

    @abstractmethod
    def generate(self, prompt: str, out_path: Path) -> Path:
        """Generate one image for ``prompt`` and write it to ``out_path``."""
        ...


class HTTPImageProvider(ImageProvider):
    """Concrete provider for a generic text-to-image HTTP API."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.image_api_key:
            raise ValueError(
                "HTTPImageProvider requires IMAGE_API_KEY to be set in the environment."
            )

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def generate(self, prompt: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        s = self.settings

        # ------------------------------------------------------------------
        # TODO: Implement the real text-to-image HTTP call for your provider.
        #
        # Expected request shape (adjust to your vendor's schema):
        #
        #   POST {s.image_api_url}
        #   Headers:
        #     Authorization: Bearer {s.image_api_key}
        #     Content-Type: application/json
        #   JSON body:
        #     {
        #       "model":  s.image_model,
        #       "prompt": prompt,
        #       "width":  s.width,
        #       "height": s.height,
        #       "n": 1,
        #       "response_format": "b64_json"   # or "url"
        #     }
        #
        # Expected response (one of):
        #   {"data": [{"b64_json": "<base64 PNG>"}]}      -> base64-decode to file
        #   {"data": [{"url": "https://.../image.png"}]}  -> GET url, stream to file
        #
        # Reference implementation skeleton (uncomment + finish):
        #
        #   headers = {
        #       "Authorization": f"Bearer {s.image_api_key}",
        #       "Content-Type": "application/json",
        #   }
        #   payload = {
        #       "model": s.image_model, "prompt": prompt,
        #       "width": s.width, "height": s.height, "n": 1,
        #       "response_format": "b64_json",
        #   }
        #   with httpx.Client(timeout=120) as client:
        #       resp = client.post(s.image_api_url, headers=headers, json=payload)
        #       resp.raise_for_status()
        #       b64 = resp.json()["data"][0]["b64_json"]
        #       out_path.write_bytes(base64.b64decode(b64))
        #   return out_path
        # ------------------------------------------------------------------
        raise NotImplementedError(
            "HTTPImageProvider.generate is a stub. Implement the TODO HTTP call "
            "for your image API, or use PlaceholderImageProvider for a dry run."
        )


class PlaceholderImageProvider(ImageProvider):
    """Renders a gradient placeholder with ffmpeg so the pipeline can run dry."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def generate(self, prompt: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        s = self.settings
        # Generate a slightly oversized gradient so Ken Burns has room to pan.
        w, h = int(s.width * 1.3), int(s.height * 1.3)
        c0 = f"0x{random.randint(0, 0xFFFFFF):06X}"
        c1 = f"0x{random.randint(0, 0xFFFFFF):06X}"
        log.info("Placeholder image for prompt %r -> %s", prompt[:48], out_path.name)
        run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}",
                "-frames:v", "1",
                str(out_path),
            ],
            desc="placeholder-image",
        )
        return out_path


class OpenverseImageProvider(ImageProvider):
    """Keyless provider: real CC-licensed photos from the Openverse API.

    Searches by keyword and downloads a matching photo. Falls back to broader
    queries (dropping trailing words) and finally to a gradient placeholder so
    the pipeline never hard-fails on a missing image.
    """

    _SEARCH_URL = "https://api.openverse.org/v1/images/"
    _HEADERS = {"User-Agent": "faceless-engine/0.1 (+https://github.com/faceless-engine)"}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._used_urls: set[str] = set()  # avoid repeating the same photo

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    def _search(self, query: str, page_size: int = 15) -> list[dict]:
        params = {
            "q": query,
            "page_size": page_size,
            "license_type": "commercial",
            "mature": "false",
        }
        with httpx.Client(timeout=40, headers=self._HEADERS) as client:
            resp = client.get(self._SEARCH_URL, params=params)
            resp.raise_for_status()
            return resp.json().get("results", [])

    def _download(self, url: str, out_path: Path) -> bool:
        try:
            with httpx.Client(timeout=60, headers=self._HEADERS, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                if not resp.headers.get("content-type", "").startswith("image"):
                    return False
                out_path.write_bytes(resp.content)
            return out_path.stat().st_size > 1024
        except httpx.HTTPError:
            log.warning("Openverse download failed: %s", url)
            return False

    _STOPWORDS = {
        "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is",
        "are", "with", "this", "that", "about", "history", "story",
    }

    @classmethod
    def _broaden(cls, query: str) -> list[str]:
        """Query variants, keeping the most meaningful (trailing subject) words.

        Stopwords are stripped so broadening never drops the real subject. We try
        the full keyword query first, then progressively keep the LAST words
        (the subject usually trails, e.g. '... indonesian COFFEE').
        """
        raw = [w for w in query.lower().replace(",", " ").split() if w]
        keywords = [w for w in raw if w not in cls._STOPWORDS] or raw
        variants: list[str] = [" ".join(keywords)]
        # Keep last N words (subject-first fallback), 4 -> 1.
        for keep in (4, 3, 2, 1):
            if keep < len(keywords):
                variants.append(" ".join(keywords[-keep:]))
        # De-dup while preserving order.
        seen: set[str] = set()
        out = []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def generate(self, prompt: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        for query in self._broaden(prompt):
            try:
                results = self._search(query)
            except httpx.HTTPError:
                log.exception("Openverse search failed for %r", query)
                continue
            for item in results:
                url = item.get("url")
                if not url or url in self._used_urls:
                    continue
                if self._download(url, out_path):
                    self._used_urls.add(url)
                    log.info("Openverse image for %r -> %s (%s)",
                             query[:40], out_path.name, (item.get("title") or "")[:40])
                    return out_path
        # Nothing found/downloadable — degrade to a placeholder gradient.
        log.warning("Openverse found no usable image for %r — using placeholder", prompt[:48])
        return PlaceholderImageProvider(self.settings).generate(prompt, out_path)


class OpenRouterImageProvider(ImageProvider):
    """AI image generation through OpenRouter (reuses LLM_API_KEY).

    Calls an image-output model via the chat/completions ``modalities`` API and
    decodes the returned base64 image. Falls back to Openverse on failure so the
    pipeline never hard-stops. NOTE: paid — about $0.04 per image with the
    default model.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.llm_api_key:
            raise ValueError(
                "OpenRouterImageProvider requires LLM_API_KEY (your OpenRouter key)."
            )
        self._fallback = OpenverseImageProvider(self.settings)

    @retry(
        # Also retry the "200 but no image" case — it's transient (often the
        # first call of a run returns text only); without this we fall back too
        # eagerly to Openverse.
        retry=retry_if_exception_type((httpx.HTTPError, RuntimeError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=3, max=30),
        reraise=True,
    )
    def _request_image(self, prompt: str) -> bytes:
        s = self.settings
        url = s.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {s.llm_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": s.llm_referer,
            "X-Title": s.llm_title,
        }
        payload = {
            "model": s.image_or_model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }
        with httpx.Client(timeout=180) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                log.error("OpenRouter image API %s: %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
            data = resp.json()
        images = (data["choices"][0]["message"].get("images") or [])
        if not images:
            raise RuntimeError("OpenRouter returned no image (model may not support image output)")
        url_field = images[0]["image_url"]["url"]
        b64 = url_field.split(",", 1)[1] if "," in url_field else url_field
        return base64.b64decode(b64)

    def generate(self, prompt: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Nudge the model toward vertical, photo-real framing.
        full = (
            f"{prompt}. Vertical 9:16 composition, photorealistic, cinematic "
            f"lighting, high detail, no text, no watermark."
        )
        try:
            out_path.write_bytes(self._request_image(full))
            if out_path.stat().st_size < 1024:
                raise RuntimeError("OpenRouter image too small / empty")
            log.info("OpenRouter image for %r -> %s", prompt[:48], out_path.name)
            return out_path
        except Exception:
            log.exception("OpenRouter image failed for %r — falling back to Openverse", prompt[:48])
            return self._fallback.generate(prompt, out_path)


def get_default_provider(settings: Settings | None = None) -> ImageProvider:
    """Pick an image provider based on config (default: Openverse, keyless)."""
    settings = settings or get_settings()
    choice = (settings.image_provider or "openverse").lower()
    if choice == "openrouter":
        try:
            return OpenRouterImageProvider(settings)
        except Exception:
            log.exception("OpenRouter image provider unavailable; falling back to Openverse")
            return OpenverseImageProvider(settings)
    if choice == "http" or (choice == "auto" and settings.image_api_key):
        try:
            return HTTPImageProvider(settings)
        except Exception:
            log.exception("HTTP image provider unavailable; falling back to Openverse")
            return OpenverseImageProvider(settings)
    if choice == "placeholder":
        return PlaceholderImageProvider(settings)
    return OpenverseImageProvider(settings)


def generate_scene_images(
    scenes: list[Scene],
    out_dir: Path,
    provider: ImageProvider | None = None,
    settings: Settings | None = None,
) -> list[Path]:
    """Generate one image per scene. Returns image paths in scene order."""
    settings = settings or get_settings()
    provider = provider or get_default_provider(settings)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for scene in scenes:
        dest = out_dir / f"scene_{scene.index:03d}.png"
        try:
            provider.generate(scene.image_prompt or scene.text, dest)
        except Exception:
            log.exception("Image generation failed for scene %d", scene.index)
            raise
        paths.append(dest)
    return paths


def ken_burns(
    image: Path,
    duration: float,
    out_path: Path,
    settings: Settings | None = None,
    direction: str | None = None,
) -> Path:
    """Create a slow pan/zoom motion clip from a still image via ``zoompan``.

    Args:
        image: source still.
        duration: clip length in seconds.
        out_path: destination .mp4.
        direction: one of "in", "out", "left", "right", "up", "down".
            Random if None.
    """
    settings = settings or get_settings()
    image, out_path = Path(image), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h, fps = settings.width, settings.height, settings.fps
    duration = max(0.5, float(duration))
    frames = max(1, int(round(duration * fps)))

    direction = direction or random.choice(["in", "out", "left", "right", "up", "down"])

    # Cover the 9:16 frame preserving aspect (no stretching), then crop to a
    # 2x canvas so zoompan has headroom to pan/zoom. This is what makes real
    # photos of arbitrary aspect ratios fill the frame cleanly.
    pre = (
        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
        f"crop={w * 2}:{h * 2}"
    )
    z_in = "min(zoom+0.0010,1.30)"
    z_out = "if(lte(zoom,1.0),1.30,max(1.001,zoom-0.0010))"
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if direction == "in":
        z, x, y = z_in, cx, cy
    elif direction == "out":
        z, x, y = z_out, cx, cy
    elif direction == "left":
        z, x, y = "1.20", f"(iw-iw/zoom)*(1-on/{frames})", cy
    elif direction == "right":
        z, x, y = "1.20", f"(iw-iw/zoom)*(on/{frames})", cy
    elif direction == "up":
        z, x, y = "1.20", cx, f"(ih-ih/zoom)*(1-on/{frames})"
    else:  # down
        z, x, y = "1.20", cx, f"(ih-ih/zoom)*(on/{frames})"

    zoompan = (
        f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}':s={w}x{h}:fps={fps}"
    )
    vf = f"{pre},{zoompan},setsar=1,format=yuv420p"

    log.info("Ken Burns (%s) %.2fs: %s -> %s", direction, duration, image.name, out_path.name)
    run_ffmpeg(
        [
            "-loop", "1",
            "-i", str(image.resolve()),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264",
            "-profile:v", "high",
            # Intermediate clips are re-encoded in assemble, so favor speed here.
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        desc="ken-burns",
    )
    return out_path
