"""YouTube -> Shorts clipping pipeline.

Download a source video with yt-dlp, pull its word-level auto-captions, let an
LLM pick the most engaging segments, then cut + crop to 9:16 + burn captions.
Produces authentic (non-AI-looking) clips that flow into the same approval and
upload queue as the faceless engine.
"""
