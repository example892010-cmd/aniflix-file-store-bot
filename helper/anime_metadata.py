
"""ANIFLIX automatic anime metadata detection."""

import re
from collections import defaultdict

from helper.auto_detect_settings import (
    get_auto_detect_pattern,
    get_default_auto_detect_settings,
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
    r"(?<!\d)(144|240|360|480|540|576|720|1080|1440|2160)\s*[pP](?!\w)",
    re.IGNORECASE,
)

EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")

# Removes uploader tags such as:
# @ANIFLIXANIMETAMIL
# @ANIFLIXANIMETAMIL -
UPLOADER_PREFIX_RE = re.compile(
    r"^\s*@\S+\s*(?:[-–—]\s*)?"
)


def _message_values(message):
    """Extract filenames, captions, and text from Telegram messages."""

    if message is None:
        return []

    if isinstance(message, str):
        return [message]

    if isinstance(message, bytes):
        return [message.decode(errors="ignore")]

    values = []

    # Pyrogram media fields.
    for field in MEDIA_FIELDS:
        media = getattr(message, field, None)

        filename = (
            getattr(media, "file_name", None)
            if media else None
        )

        if filename:
            values.append(str(filename))

    # Alternative document attribute.
    media = getattr(message, "media", None)
    document = (
        getattr(media, "document", None)
        if media else None
    )

    filename = (
        getattr(document, "file_name", None)
        if document else None
    )

    if filename:
        values.append(str(filename))

    # Captions and ordinary text.
    for field in ("caption", "text"):
        value = getattr(message, field, None)

        if value:
            values.append(str(value))

    # Remove duplicate values while preserving order.
    return list(
        dict.fromkeys(
            value for value in values if value.strip()
        )
    )


def _message_filename(message):
    """Compatibility helper for existing bot code."""

    values = _message_values(message)

    return values[0] if values else ""


def _iter_candidate_lines(messages):
    """Yield individual filename, caption, and text lines."""

    if messages is None:
        return

    if isinstance(messages, (str, bytes)):
        messages = [messages]

    else:
        try:
            iter(messages)
        except TypeError:
            messages = [messages]

    for message in messages:
        for value in _message_values(message):

            for line in value.splitlines() or [value]:
                line = line.strip()

                if line:
                    yield line


def _get_detection_pattern(settings=None):
    """Get the selected format, with a safe default fallback."""

    try:
        return get_auto_detect_pattern(settings)

    except (re.error, ValueError, TypeError, KeyError):
        return get_auto_detect_pattern(
            get_default_auto_detect_settings()
        )


def _extract_title_candidate(filename, match):
    """Extract the anime title before the season/episode marker."""

    candidate = filename[:match.start()]

    # Remove uploader/channel prefix.
    candidate = UPLOADER_PREFIX_RE.sub(
        "",
        candidate,
        count=1,
    )

    # Remove bracketed release information.
    candidate = re.sub(
        r"\[[^]]*\]|\([^)]*\)|\{[^}]*\}",
        " ",
        candidate,
    )

    # Normalize separators.
    candidate = re.sub(r"[_|]+", " ", candidate)

    candidate = re.sub(
        r"\s*[-–—]+\s*$",
        "",
        candidate,
    )

    candidate = re.sub(r"\s+", " ", candidate)

    return candidate.strip(" ._-–—")


def _fallback_title_candidate(line):
    """Extract a possible title when no episode marker is detected."""

    candidate = EXTENSION_RE.sub("", line.strip())

    candidate = UPLOADER_PREFIX_RE.sub(
        "",
        candidate,
        count=1,
    )

    # Remove URLs.
    candidate = re.sub(
        r"https?://\S+",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )

    # Remove common resolution labels.
    candidate = re.sub(
        r"\b\d{3,4}\s*[pP]\b",
        " ",
        candidate,
    )

    # Remove common language labels.
    candidate = re.sub(
        r"\b(TAMIL|HINDI|TELUGU|ENGLISH|JAPANESE)\b",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )

    candidate = re.sub(r"\s+", " ", candidate)

    return candidate.strip(" ._-–—")


def detect_auto_batch_metadata(messages, settings=None):
    """
    Detect anime title, season numbers, and unique episode numbers.

    Returns:
        (
            "ANIME TITLE",
            {
                1: {1, 2, 3},
                2: {1, 2}
            }
        )
    """

    settings = normalize_auto_detect_settings(settings)

    candidates = list(_iter_candidate_lines(messages))

    manual_title = str(
        settings.get("manual_title") or ""
    ).strip()

    # Respect the admin's enable/disable setting.
    if not settings.get("enabled", True):

        title = manual_title or "ANIME FILES"

        title = clean_auto_detect_title(
            title,
            settings,
        )

        return title.upper(), {}

    pattern = _get_detection_pattern(settings)

    episodes = defaultdict(set)
    title = ""

    for line in candidates:

        filename = EXTENSION_RE.sub(
            "",
            line,
        ).strip()

        match = pattern.search(filename)

        if not match:
            continue

        try:
            season = int(match.group("season"))
            episode = int(match.group("episode"))

        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        if season < 0 or episode < 0:
            continue

        # Sets prevent duplicate episodes when multiple
        # video qualities are uploaded.
        episodes[season].add(episode)

        # Find the title from the first valid filename.
        if not title:
            title = _extract_title_candidate(
                filename,
                match,
            )

    # Admin-provided title takes priority.
    if manual_title:
        title = manual_title

    # Fallback when no valid episode filename was found.
    if not title:

        for line in candidates:

            # Ignore commands and URLs.
            if line.startswith("/"):
                continue

            if re.match(r"https?://", line, re.IGNORECASE):
                continue

            fallback = _fallback_title_candidate(line)

            if fallback:
                title = fallback
                break

    # Apply admin-configured title cleanup.
    title = clean_auto_detect_title(
        title or "ANIME FILES",
        settings,
    )

    title = re.sub(r"\s+", " ", title).strip()

    return title.upper(), dict(episodes)


def get_auto_batch_qualities(messages):
    """Detect unique video qualities and sort them numerically."""

    qualities = set()

    for line in _iter_candidate_lines(messages):

        for match in QUALITY_RE.finditer(line):

            quality = f"{match.group(1)}p"

            qualities.add(quality)

    return sorted(
        qualities,
        key=lambda value: int(value[:-1]),
    )


def build_auto_detection_preview(
    title,
    episodes,
    qualities=None,
    settings=None,
):
    """Build a readable auto-detection preview."""

    settings = normalize_auto_detect_settings(settings)

    lines = [
        f"Anime: {title or 'ANIME FILES'}"
    ]

    for season in sorted(episodes):

        lines.append(
            f"Season: {int(season):02d}"
        )

        formatted_episodes = [
            format_auto_episode(episode, settings)
            for episode in sorted(episodes[season])
        ]

        lines.append(
            "Episodes: " + (
                ", ".join(formatted_episodes)
                if formatted_episodes
                else "None"
            )
        )

    if qualities:
        lines.append(
            "Qualities: " + ", ".join(qualities)
        )

    return "\n".join(lines)


def build_auto_batch_result(
    title,
    episodes,
    url,
    settings=None,
):
    """Build the /batch1renamed command output."""

    settings = normalize_auto_detect_settings(settings)

    clean_title = clean_auto_detect_title(
        title or "ANIME FILES",
        settings,
    ).upper()

    lines = [
        "/batch1renamed",
        clean_title or "ANIME FILES",
    ]

    for season in sorted(episodes):

        lines.append(
            f"Season {int(season):02d}"
        )

        for episode in sorted(episodes[season]):

            lines.append(
                format_auto_episode(
                    episode,
                    settings,
                )
            )

    lines.append(str(url).strip())

    return "\n".join(lines)
