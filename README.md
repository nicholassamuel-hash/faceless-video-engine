# Faceless Short-Form Video Engine

Modular, headless pipeline that turns a topic into a 9:16 (1080×1920) MP4,
30–50s, with AI imagery, Ken Burns motion, narration (Edge-TTS), and burned-in
word-level captions — then queues it for **human approval** before any upload.

Uploads are **SAFE by default**: TikTok goes to your *draft inbox*, YouTube goes
*private*. You have to flip a config flag (and confirm) to publish publicly.

```
topic ─▶ script ─▶ TTS (word timings) ─▶ images ─▶ Ken Burns clips
      ─▶ word-level captions ─▶ assemble MP4 ─▶ queue (status=generated)
      ─▶ YOU review/approve ─▶ scheduler uploads (jitter + rate limits)
```

## Layout

```
faceless_engine/   config, llm, tts, imagery, captions, assemble, pipeline
shared/            db (SQLite/SQLAlchemy), models, logging
approval/          review_cli (list / preview / approve / reject)
uploader/          tiktok, youtube, scheduler
main.py            CLI: generate / review / upload / doctor
```

## Prerequisites

- **Python 3.11+**
- **ffmpeg + ffprobe** on PATH (not a pip package):

  ```cmd
  winget install Gyan.FFmpeg
  ```

  Then **reopen your terminal** and verify: `ffmpeg -version`.

## Install (Windows CMD)

```cmd
cd "C:\Users\palkon\Documents\Marketing tools"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` if you want (it works out of the box with the **placeholder image
provider** and the **stub script generator**, so you can produce a video with no
API keys at all).

Sanity check your environment:

```cmd
python main.py doctor
```

## Generate one video

```cmd
python main.py generate --topic "Sejarah kopi Indonesia" --language id --scenes 5
```

Output lands in `work\output\job_XXXXX.mp4` and is queued as `generated`. After
each run a **per-stage benchmark** is printed (see below).

### Imagery — real photos, keyless by default

By default the engine pulls **real, topic-relevant CC-licensed photos** from
[Openverse](https://openverse.org) — no API key needed. Photos are cover-cropped
to fill the 9:16 frame (no stretching) and given Ken Burns motion.

Switch providers via `FE_IMAGE_PROVIDER`:
- `openverse` (default) — real stock photos by keyword, keyless (free, relevance varies).
- `openrouter` — **AI image generation reusing your `LLM_API_KEY`** (~$0.04/image,
  always relevant, photoreal). Best quality. Model via `FE_IMAGE_OR_MODEL`
  (default `google/gemini-2.5-flash-image`). Falls back to Openverse on error.
- `http` — your own AI image API (set `IMAGE_API_KEY` and implement the marked
  `TODO` in `faceless_engine/imagery.py` → `HTTPImageProvider`).
- `placeholder` — ffmpeg gradients (fast smoke tests).

### Script — plug in your LLM for real storytelling

Without an LLM key you get a generic **template** script (it runs, but the
storytelling is bland). For real, topic-aware viral scripts, point it at any
**OpenAI-compatible** API (OpenRouter / Groq / DeepSeek / OpenAI). In `.env`:

```
FE_LLM_PROVIDER=openai
LLM_API_KEY=sk-or-v1-...                 # your OpenRouter key
FE_LLM_BASE_URL=https://openrouter.ai/api/v1
FE_LLM_MODEL=openai/gpt-4o-mini          # or anthropic/claude-3.5-sonnet for best quality
```

The LLM writes the hook/body/CTA, the title, hashtags, and a short English
`image_query` per scene (which drives the Openverse photo search). Tip: the
template makes short videos — for 30–50s on the template path use `--scenes 7`;
the LLM path hits the duration target on its own.

## Benchmark

Every `generate` prints a wall-clock breakdown per stage. To time several runs
and get min/avg/max per stage:

```cmd
python main.py benchmark --topic "Indonesian coffee" --language en --scenes 4 --runs 3
```

Typical split on a CPU-only Windows box: ffmpeg encoding (`assemble` +
`ken_burns`) dominates (~80%); TTS and image download are network-bound.

## Clipping (YouTube → Shorts)

Turn a long video into authentic, non-AI-looking vertical clips: download the
source, read its word-level auto-captions, let the LLM pick the best segment(s),
then cut + crop to 9:16 + burn TikTok-style captions. Clips land in the same
review/upload queue.

```cmd
python main.py clip "https://www.youtube.com/watch?v=VIDEO_ID"
python main.py clip "URL1" "URL2"                      :: multiple sources
python main.py clip "URL" --lang id --clips 3 --target 55   :: per-run overrides
```

CLI flags (override `.env` for that run): `--lang` (caption language), `--clips`
(clips per video), `--crop` (`blur`/`fill`), `--target`/`--min`/`--max` (seconds).
Short LLM picks are auto-extended toward `--target`, snapped to caption-word
boundaries, so clips land at a consistent length.

- Uses **yt-dlp** (installed via requirements) + ffmpeg. No extra keys for the
  download/caption/cut steps; highlight selection uses your `LLM_API_KEY`.
- Framing `FE_CLIP_CROP_MODE`: `blur` (fit + blurred fill, default) or `fill`
  (center-crop). Length via `FE_CLIP_TARGET/MIN/MAX_SECONDS`; clips per source
  via `FE_CLIPS_PER_VIDEO`; caption language via `FE_CLIP_SUB_LANG`.
- Needs the source to have (auto-)captions in the chosen language; without them
  it falls back to an even split with no burned captions.
- Per-stage benchmark prints after each run (`download` / `highlights` / `cut`).

> Note copyright: clipping third-party videos for public reposting can infringe
> rights. Use sources you're allowed to repurpose.

## Review (approval gate)

```cmd
python main.py review list                 :: list generated entries
python main.py review preview 1            :: open in default player
python main.py review approve 1            :: mark approved (upload-eligible)
python main.py review reject 1 --reason "audio clipped"
python main.py review interactive          :: step through all, preview + decide
```

Only **approved** entries are ever uploaded.

## Benchmark TikTok accounts

Pull public engagement metrics (views / likes / comments / reposts) for any
TikTok account or video via yt-dlp — no API key, no login — to benchmark
performance and compare accounts. Snapshots are stored so later runs show trend.

```cmd
python main.py tiktok-stats "@someaccount"
python main.py tiktok-stats "@acc1" "@acc2" --limit 20      :: compare accounts
python main.py tiktok-stats "https://www.tiktok.com/@acc/video/123"
```

Reports per-account avg/median views, average engagement rate, the top video,
and a comparison table; re-running later prints the change vs the last snapshot.

> Reads only PUBLIC data. Avoid high-frequency runs — heavy automated access can
> conflict with TikTok's Terms of Service.

## Upload (SAFE by default)

Dry run first — shows what it *would* do, no network, no waiting:

```cmd
python main.py upload --dry-run
```

Real run in safe mode (TikTok draft inbox, YouTube private). The scheduler sleeps
a randomized jitter before each upload and enforces per-platform daily limits:

```cmd
python main.py upload
python main.py upload --platforms youtube      :: just one platform
python main.py upload --limit 1                :: process a single entry
```

### Going live (opt-in)

Edit `.env`:

```
FE_TIKTOK_MODE=direct        # auto-post instead of draft
FE_YOUTUBE_PRIVACY=public    # public instead of private
```

When a non-safe mode is set, `upload` asks you to type `yes` to confirm
(skip with `--yes` for automation you trust).

## Credentials

| Platform | What you need | Where |
|----------|---------------|-------|
| TikTok   | User access token w/ `video.upload` (draft) or `video.publish` (direct) | `TIKTOK_ACCESS_TOKEN` |
| YouTube  | OAuth *Desktop app* client-secrets JSON | `YOUTUBE_CLIENT_SECRETS` (token cached to `YOUTUBE_TOKEN_FILE` on first run) |

Nothing is hardcoded; all secrets come from `.env` / the environment. **Never
commit `.env` or your secrets.**

## Config reference

All settings live in `.env` (see `.env.example`). Highlights:

| Var | Default | Meaning |
|-----|---------|---------|
| `FE_LANGUAGE` | `id` | `id`→`id-ID-ArdiNeural`, `en`→`en-US-AndrewNeural` |
| `FE_IMAGE_PROVIDER` | `openverse` | `openverse` (free photos) / `openrouter` (AI, ~$0.04/img) / `http` / `placeholder` |
| `FE_LLM_PROVIDER` | `openai` | `openai` (OpenAI-compatible) / `template` (keyless) |
| `LLM_API_KEY` | — | key for the LLM provider (e.g. OpenRouter) |
| `FE_LLM_BASE_URL` | OpenRouter | OpenAI-compatible base URL |
| `FE_LLM_MODEL` | `openai/gpt-4o-mini` | model slug for your provider |
| `FE_WIDTH`/`FE_HEIGHT`/`FE_FPS` | 1080/1920/30 | output spec |
| `FE_MIN_SECONDS`/`FE_MAX_SECONDS` | 30/50 | target duration window |
| `FE_TIKTOK_MODE` | `draft` | `draft` (safe) / `direct` |
| `FE_YOUTUBE_PRIVACY` | `private` | `private` (safe) / `unlisted` / `public` |
| `FE_JITTER_MIN_MINUTES`/`FE_JITTER_MAX_MINUTES` | 8/45 | randomized delay between uploads |
| `FE_TIKTOK_MAX_PER_DAY`/`FE_YOUTUBE_MAX_PER_DAY` | 3/5 | daily upload caps |

## Notes

- Structured logging to stderr (`--log-level DEBUG` for more).
- Network calls retry with exponential backoff; failures are logged and the job
  is marked `failed` (no silent swallowing).
- All paths are handled via `pathlib`; ffmpeg/ffprobe are detected on PATH with a
  clear install hint if missing.
```
