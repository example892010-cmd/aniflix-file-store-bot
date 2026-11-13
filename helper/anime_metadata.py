"""Filename-based anime title, season, and episode detection for channel auto batches."""

import re
from typing import Iterable


AUTO_SEASON_RE = re.compile(r"\bS(?:EASON)?\s*0*(\d{1,2})\b", re.IGNORECASE)
AUTO_EPISODE_RE = re.compile(r"\b(?:EPISODE|EP|E)\s*0*(\d{1,4})\b", re.IGNORECASE)
MEDIA_FIELDS = ("document", "video", "audio", "photo", "animation", "voice", "video_note", "sticker")


def _message_filename(message) -> str:
    for field in MEDIA_FIELDS:
        media = getattr(message, field, None)
        filename = getattr(media, "file_name", None) if media else None
        if filename:
            return str(filename)
    return str(getattr(message, "caption", None) or getattr(message, "text", None) or "")


def detect_auto_batch_metadata(messages: Iterable):
    """Return (uppercase title, {season: set(episode_numbers)}) from message names."""
    title = ""
    episodes = {}
    for message in messages:
        raw = _message_filename(message)
        if not raw:
            continue
        name = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", raw).strip()
        season_match = AUTO_SEASON_RE.search(name)
        episode_match = AUTO_EPISODE_RE.search(name)
        season = int(season_match.group(1)) if season_match else 1
        episode = int(episode_match.group(1)) if episode_match else None
        if not title and season_match:
            candidate = name[:season_match.start()]
            candidate = re.sub(r"^\s*@[^-]+\s*-\s*", "", candidate)
            candidate = re.sub(r"\[[^]]*\]|\([^)]*\)", " ", candidate)
            candidate = re.sub(r"[_|]+", " ", candidate)
            candidate = re.sub(r"\s*[-–—]+\s*$", "", candidate)
            title = re.sub(r"\s+", " ", candidate).strip(" ._-–—")
        elif not title and episode_match:
            candidate = name[:episode_match.start()]
            candidate = re.sub(r"^\s*@[^-]+\s*-\s*", "", candidate)
            candidate = re.sub(r"\[[^]]*\]|\([^)]*\)", " ", candidate)
            title = re.sub(r"\s+", " ", candidate).strip(" ._-–—")
        if episode is not None:
            episodes.setdefault(season, set()).add(episode)
    if not title:
        for message in messages:
            raw = _message_filename(message)
            if raw:
                candidate = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", raw)
                candidate = re.sub(r"^\s*@[^-]+\s*-\s*", "", candidate)
                candidate = re.sub(r"\[[^]]*\]|\([^)]*\)", " ", candidate)
                title = re.sub(r"\s+", " ", candidate).strip(" ._-–—")
                if title:
                    break
    title = re.sub(r"\bBERUS\b", "BEERUS", title, flags=re.IGNORECASE)
    return (title or "ANIME FILES").upper(), episodes


def build_auto_batch_result(title: str, episodes: dict, url: str) -> str:
    """Build the requested /batch1renamed summary with one combined link."""
    lines = ["/batch1renamed", title, ""]
    if episodes:
        for season in sorted(episodes):
            season_label = f"{season:02d}" if season < 10 else str(season)
            lines.append(f"Season {season_label}")
            lines.extend(f"{episode:02d}" if episode < 10 else str(episode)
                         for episode in sorted(episodes[season]))
            lines.append("")
    else:
        lines.extend(["Season 01", ""])
    lines.append(url)
    return "\n".join(lines).strip()
