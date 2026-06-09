"""Script generation.

Produces a :class:`Script` (title, description, hashtags, and ordered scenes)
from a topic. The LLM is behind :class:`LLMProvider` so it is swappable.

* :class:`OpenAICompatLLMProvider` — talks to any OpenAI-compatible
  chat-completions endpoint (OpenRouter / Groq / DeepSeek / OpenAI / ...).
  This is the default and produces real, topic-aware viral storytelling.
* :class:`TemplateLLMProvider` — keyless deterministic fallback so the pipeline
  still runs (with generic copy) when no ``LLM_API_KEY`` is configured.
"""
from __future__ import annotations

import json
import logging
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from faceless_engine.config import Settings, get_settings
from faceless_engine.imagery import Scene

log = logging.getLogger(__name__)


@dataclass
class Script:
    title: str
    description: str
    hashtags: list[str]
    scenes: list[Scene]
    language: str = "id"
    hook: str = ""  # scroll-stopping on-screen opener for the first seconds

    @property
    def full_text(self) -> str:
        """Complete narration (all scene texts joined)."""
        return " ".join(s.text.strip() for s in self.scenes if s.text.strip())

    @property
    def hashtags_str(self) -> str:
        return " ".join(h if h.startswith("#") else f"#{h}" for h in self.hashtags)


class LLMProvider(ABC):
    @abstractmethod
    def generate_script(self, topic: str, language: str, num_scenes: int) -> Script:
        ...


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------
_SYSTEM = (
    "You are a world-class short-form video scriptwriter for TikTok, Instagram "
    "Reels, and YouTube Shorts. You write tight, retention-optimized narration "
    "that hooks in the first 2 seconds and keeps viewers to the end. You write "
    "in the requested language, use concrete specifics (names, numbers, vivid "
    "details) and NEVER generic filler like 'this is more interesting than it "
    "looks'. You always reply with a single valid JSON object and nothing else."
)


def _user_prompt(topic: str, language: str, num_scenes: int) -> str:
    lang_name = {"id": "Indonesian (Bahasa Indonesia)", "en": "English"}.get(language, language)
    total_words = "90-140"
    return f"""Write a faceless short-form video script.

TOPIC: {topic}
LANGUAGE (for the spoken narration): {lang_name}
SCENES: exactly {num_scenes}
TARGET: 30-50 seconds total (about {total_words} spoken words across all scenes).

Rules:
- Scene 1 is the HOOK: a pattern-interrupt, bold claim, or burning curiosity gap. No "hi guys".
- Middle scenes deliver concrete, surprising value about the topic (story, cause/effect, steps).
- The LAST scene is a punchy call-to-action (follow / comment / save).
- Each scene's "text" is 1-2 spoken sentences, conversational, easy to say out loud.
- DO NOT invent specific statistics, percentages, dates, prices, or named figures
  you are not sure are true. If unsure, speak in general terms ("pajak yang makin berat",
  "beberapa tahun terakhir") instead of fake precise numbers. Accuracy over fake specifics.
- "image_query": 2-4 ENGLISH keywords to find a matching stock PHOTO (always English).
- "image_prompt": a vivid ENGLISH description of a SINGLE photographable, cinematic SCENE
  (people, places, objects, action, mood). NEVER describe charts, graphs, documents, maps,
  infographics, screenshots, slides, or anything with text/numbers — AI image models render
  those badly. Think "what real photo captures this idea", e.g. a frustrated businessman
  leaving a glass office, an empty factory floor, suitcases at an airport gate.
- "title": <= 80 chars, scroll-stopping but honest.
- "hashtags": 5-8 relevant tags WITHOUT the # symbol.
- "hook": a 3-8 word ON-SCREEN text overlay for the first 3 seconds, in {lang_name}.
  Thematically related but framed dramatically (bold claim / alarming question /
  curiosity gap) — NOT a literal line from the narration.

Return ONLY this JSON shape:
{{
  "title": "...",
  "description": "...",
  "hook": "3-8 word dramatic hook",
  "hashtags": ["...", "..."],
  "scenes": [
    {{"text": "spoken line in {lang_name}", "image_query": "english keywords", "image_prompt": "english visual"}}
  ]
}}"""


def _script_from_json(raw: str, language: str, fallback_topic: str) -> Script:
    """Parse a model's JSON output into a Script (tolerant of code fences)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data = json.loads(match.group(0) if match else raw)
    scenes_raw = data.get("scenes", [])
    scenes = [
        Scene(
            index=i,
            text=(sc.get("text") or "").strip(),
            image_prompt=(sc.get("image_prompt") or sc.get("text") or fallback_topic).strip(),
            image_query=(sc.get("image_query") or sc.get("image_prompt") or fallback_topic).strip(),
        )
        for i, sc in enumerate(scenes_raw)
    ]
    scenes = [s for s in scenes if s.text]
    if not scenes:
        raise ValueError("model returned no usable scenes")
    return Script(
        title=(data.get("title") or fallback_topic).strip()[:120],
        description=(data.get("description") or "").strip(),
        hashtags=[str(h).lstrip("#").strip() for h in data.get("hashtags", []) if str(h).strip()],
        scenes=scenes,
        language=language,
        hook=str(data.get("hook", "")).strip()[:80],
    )


class OpenAICompatLLMProvider(LLMProvider):
    """Chat-completions provider for any OpenAI-compatible API (e.g. OpenRouter)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.llm_api_key:
            raise ValueError("OpenAICompatLLMProvider requires LLM_API_KEY in the environment.")

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _chat(self, messages: list[dict]) -> str:
        s = self.settings
        url = s.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {s.llm_api_key}",
            "Content-Type": "application/json",
            # OpenRouter attribution (ignored by other providers).
            "HTTP-Referer": s.llm_referer,
            "X-Title": s.llm_title,
        }
        payload = {
            "model": s.llm_model,
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 1400,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                log.error("LLM API %s: %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def generate_script(self, topic: str, language: str, num_scenes: int) -> Script:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _user_prompt(topic, language, num_scenes)},
        ]
        log.info("LLM generating script via %s (model=%s)", self.settings.llm_base_url,
                 self.settings.llm_model)
        content = self._chat(messages)
        script = _script_from_json(content, language, topic)
        log.info("LLM script: %d scenes, title=%r", len(script.scenes), script.title)
        return script


class TemplateLLMProvider(LLMProvider):
    """Keyless deterministic fallback (generic copy, but varied & structured)."""

    _HOOKS = {
        "en": ["Most people get {topic} completely wrong.",
               "Here are 3 things about {topic} nobody tells you.",
               "{topic} is way wilder than you think."],
        "id": ["Kebanyakan orang salah paham soal {topic}.",
               "Ini 3 hal soal {topic} yang jarang dibahas.",
               "{topic} ternyata jauh lebih gila dari yang kamu kira."],
    }
    _BODY = {
        "en": ["First, {topic} has a backstory most people never hear about.",
               "Second, the details around {topic} are surprisingly specific.",
               "And the part about {topic} that actually matters is this."],
        "id": ["Pertama, {topic} punya cerita asal yang jarang diketahui.",
               "Kedua, detail soal {topic} ternyata sangat spesifik.",
               "Dan bagian {topic} yang paling penting adalah ini."],
    }
    _CTA = {
        "en": ["Follow for more on {topic} — you won't regret it.",
               "Save this and follow for part 2 about {topic}."],
        "id": ["Follow buat bahas {topic} lainnya — dijamin gak nyesel.",
               "Save dulu, follow buat part 2 soal {topic}."],
    }
    _TAGS = {"en": ["shorts", "facts", "viral", "fyp", "learnontiktok"],
             "id": ["shorts", "fakta", "viral", "fyp", "edukasi"]}

    def generate_script(self, topic: str, language: str, num_scenes: int) -> Script:
        lang = language if language in self._HOOKS else "en"
        topic = topic.strip()
        num_scenes = max(3, min(8, num_scenes))

        # Keyless: keep the stock-photo query = the topic itself so every scene
        # stays on-topic (variety comes from de-duped results, not from drifting
        # keywords). The image_prompt still varies for AI image providers.
        scenes: list[Scene] = []
        scenes.append(Scene(0, random.choice(self._HOOKS[lang]).format(topic=topic),
                            f"cinematic establishing shot of {topic}", topic))
        body = self._BODY[lang]
        for n in range(1, num_scenes - 1):
            scenes.append(Scene(n, body[(n - 1) % len(body)].format(topic=topic),
                                f"detailed illustration of {topic}", topic))
        scenes.append(Scene(num_scenes - 1, random.choice(self._CTA[lang]).format(topic=topic),
                            f"bold closing visual about {topic}", topic))

        hook = {"en": f"The truth about {topic}",
                "id": f"Fakta mengejutkan soal {topic}"}.get(lang, topic)
        log.info("TemplateLLM generated %d scenes for %r (%s, keyless fallback)",
                 len(scenes), topic, lang)
        return Script(title=topic[:80], description=scenes[0].text,
                      hashtags=self._TAGS[lang], scenes=scenes, language=lang, hook=hook[:80])


# Backwards-compatible alias.
StubLLMProvider = TemplateLLMProvider


@retry(
    retry=retry_if_exception_type((httpx.HTTPError,)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def chat(
    messages: list[dict],
    settings: Settings | None = None,
    *,
    temperature: float = 0.7,
    max_tokens: int = 1500,
) -> str:
    """Low-level OpenAI-compatible chat call returning the message content.

    Reused by features beyond script generation (e.g. clip highlight selection).
    Requires LLM_API_KEY.
    """
    s = settings or get_settings()
    if not s.llm_api_key:
        raise ValueError("chat() requires LLM_API_KEY in the environment.")
    url = s.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {s.llm_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": s.llm_referer,
        "X-Title": s.llm_title,
    }
    payload = {
        "model": s.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            log.error("LLM chat %s: %s", resp.status_code, resp.text[:400])
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    prov = settings.llm_provider.lower()
    if prov in ("template", "stub"):
        return TemplateLLMProvider()
    if settings.llm_api_key:
        try:
            return OpenAICompatLLMProvider(settings)
        except Exception:
            log.exception("LLM provider init failed; using template fallback")
            return TemplateLLMProvider()
    log.warning("No LLM_API_KEY set — using TemplateLLMProvider (generic copy). "
                "Set LLM_API_KEY (OpenRouter etc.) for real storytelling.")
    return TemplateLLMProvider()


def generate_script(
    topic: str,
    language: str | None = None,
    num_scenes: int = 5,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> Script:
    settings = settings or get_settings()
    language = language or settings.language
    provider = provider or get_provider(settings)
    return provider.generate_script(topic, language, num_scenes)
