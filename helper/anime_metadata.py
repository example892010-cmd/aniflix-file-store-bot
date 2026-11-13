
"""ANIFLIX automatic anime metadata detection."""

import re
from collections import defaultdict
from typing import Iterable

from helper.auto_detect_settings import (
    get_auto_detect_pattern,
    format_auto_episode,
    clean_auto_detect_title,
    normalize_auto_detect_settings,
)


MEDIA_FIELDS = (
    "document",
    "video",
    "audio",
    "photo",
    "animation",
    "voice",
    "video_note",
    "sticker",
)

QUALITY_RE = re.compile(
    r"(?<!\d)(144|240|360|480|540|576|720|1080|1440|2160)p(?!\w)",
    re.IGNORECASE,
)

EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def _message_filename(message):
    """Get a filename or usable text from a Telegram message."""

    for field in MEDIA_FIELDS:
        media = getattr(message, field, None)
        filename = getattr(media, "file_name", None) if media else None

        if filename:
            return str(filename)

    # Some message libraries expose the filename through a file object.
    media = getattr(message, "media", None)
    document = getattr(media, "document", None) if media else None

    filename = getattr(document, "file_name", None) if document else None
    if filename:
        return str(filename)

    return str(
        getattr(message, "caption", None)
        or getattr(message, "text", None)
        or ""
    )


def _extract_title_candidate(filename, match):
    """Get the title portion before the detected season/episode pattern."""

    candidate = filename[:match.start()]

    # Remove common channel/uploader prefixes.
    candidate = re.sub(
        r"^\s*@[^-]+\s*[-–—]\s*",
        "",
        candidate,
    )

    # Remove release tags and bracketed quality/language information.
    candidate = re.sub(r"\[[^]]*\]|\([^)]*\)|\{[^}]*\}", " ", candidate)

    candidate = re.sub(r"[_|]+", " ", candidate)
    candidate = re.sub(r"\s*[-–—]+\s*$", "", candidate)

    return re.sub(r"\s+", " ", candidate).strip(" ._-–—")


def _get_detection_pattern(settings=None):
    """Safely obtain the configured season/episode pattern."""

    try:
        return get_auto_detect_pattern(settings)
    except Exception:
        # Keep detection usable if a saved custom pattern is invalid.
        return get_auto_detect_pattern(
            normalize_auto_detect_settings(None)
        )


def detect_auto_batch_metadata(messages, settings=None):
    """
    Detect anime title and unique episodes.

    Returns:
        (title, {season_number: set(episode_numbers)})
    """

    settings = normalize_auto_detect_settings(settings)

    if not settings.get("enabled", True):
        return (
            clean_auto_detect_title(
                settings.get("manual_title") or "ANIME FILES",
                settings,
            ).upper(),
            {},
        )

    pattern = _get_detection_pattern(settings)

    title = ""
    episodes = defaultdict(set)

    for message in messages:
        raw_name = _message_filename(message)

        if not raw_name:
            continue

        filename = EXTENSION_RE.sub("", raw_name).strip()
        match = pattern.search(filename)

        if not match:
            continue

        try:
            season = int(match.group("season"))
            episode = int(match.group("episode"))
        except (IndexError, KeyError, TypeError, ValueError):
            continue

        episodes[season].add(episode)

        if not title:
            title = _extract_title_candidate(filename, match)

    # A manually configured title always takes priority.
    manual_title = str(settings.get("manual_title") or "").strip()

    if manual_title:
        title = manual_title

    if not title:
        for message in messages:
            raw_name = _message_filename(message)
            if not raw_name:
                continue

            filename = EXTENSION_RE.sub("", raw_name).strip()
            filename = re.sub(
                r"^\s*@[^-]+\s*[-–—]\s*",
                "",
                filename,
            )
            filename = re.sub(
                r"\[[^]]*\]|\([^)]*\)|\{[^}]*\}",
                " ",
                filename,
            )
            filename = re.sub(r"[_|]+", " ", filename)
            filename = re.sub(r"\s+", " ", filename).strip(" ._-–—")

            if filename:
                title = filename
                break

    title = clean_auto_detect_title(
        title or "ANIME FILES",
        settings,
    )

    return title.upper(), dict(episodes)


def get_auto_batch_qualities(messages):
    """Return unique video qualities found in the batch filenames."""

    qualities = set()

    for message in messages:
        filename = _message_filename(message)

        for match in QUALITY_RE.finditer(filename):
            qualities.add(f"{match.group(1)}p")

    return sorted(qualities, key=lambda value: int(value[:-1]))


def build_auto_detection_preview(
    title,
    episodes,
    qualities=None,
    settings=None,
):
    """Build a readable preview for the admin or channel workflow."""

    settings = normalize_auto_detect_settings(settings)

    lines = [
        f"Anime: {title or 'ANIME FILES'}",
    ]

    for season in sorted(episodes):
        lines.append(f"Season: {season:02d}")

        episode_numbers = sorted(episodes[season])

        formatted = [
            format_auto_episode(episode, settings)
            for episode in episode_numbers
        ]

        lines.append("Episodes: " + ", ".join(formatted))

    if qualities:
        lines.append("Qualities: " + ", ".join(qualities))

    return "\n".join(lines)


def build_auto_batch_result(title, episodes, url, settings=None):
    """Build the /batch1renamed command output."""

    settings = normalize_auto_detect_settings(settings)

    lines = [
        "/batch1renamed",
        title or "ANIME FILES",
    ]

    for season in sorted(episodes):
        lines.append(f"Season {season:02d}")

        for episode in sorted(episodes[season]):
            lines.append(format_auto_episode(episode, settings))

    lines.append(str(url))

    return "\n".join(lines)
