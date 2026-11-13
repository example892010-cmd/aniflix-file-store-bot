
"""Persistent configuration helpers for ANIFLIX auto detection."""

import re
from copy import deepcopy


DEFAULT_AUTO_DETECT_SETTINGS = {
    "enabled": True,
    "format": "S{season} Ep{episode}",
    "custom_regex": "",
    "manual_title": "",
    "remove_words": ["BERUS", "BEERUS"],
    "episode_padding": 2,
}


SUPPORTED_FORMATS = {
    "S{season} Ep{episode}": (
        r"\bS\s*0*(?P<season>\d{1,2})\s*"
        r"EP\s*0*(?P<episode>\d{1,4})\b"
    ),
    "S{season}E{episode}": (
        r"\bS\s*0*(?P<season>\d{1,2})\s*"
        r"E\s*0*(?P<episode>\d{1,4})\b"
    ),
    "Season {season} Episode {episode}": (
        r"\bSEASON\s*0*(?P<season>\d{1,2})\s*"
        r"EPISODE\s*0*(?P<episode>\d{1,4})\b"
    ),
    "{season}x{episode}": (
        r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,4})\b"
    ),
}


def get_default_auto_detect_settings():
    """Return a fresh copy of the default settings."""
    return deepcopy(DEFAULT_AUTO_DETECT_SETTINGS)


def normalize_auto_detect_settings(settings=None):
    """Merge saved settings with defaults and validate their values."""

    result = get_default_auto_detect_settings()

    if isinstance(settings, dict):
        result.update(settings)

    result["enabled"] = bool(result.get("enabled", True))

    selected_format = str(result.get("format", "")).strip()
    if selected_format not in SUPPORTED_FORMATS and selected_format != "CUSTOM":
        selected_format = DEFAULT_AUTO_DETECT_SETTINGS["format"]

    result["format"] = selected_format

    result["custom_regex"] = str(
        result.get("custom_regex", "")
    ).strip()

    result["manual_title"] = str(
        result.get("manual_title", "")
    ).strip()[:150]

    words = result.get("remove_words", [])
    if not isinstance(words, list):
        words = []

    result["remove_words"] = [
        str(word).strip()
        for word in words
        if str(word).strip()
    ][:30]

    try:
        padding = int(result.get("episode_padding", 2))
    except (TypeError, ValueError):
        padding = 2

    result["episode_padding"] = max(1, min(padding, 4))

    # Validate a custom regex before accepting it.
    if result["format"] == "CUSTOM":
        pattern = result["custom_regex"]

        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            if (
                "season" not in compiled.groupindex
                or "episode" not in compiled.groupindex
            ):
                raise ValueError(
                    "Custom regex must contain named groups "
                    "?P<season> and ?P<episode>."
                )
        except re.error as exc:
            raise ValueError(f"Invalid custom regex: {exc}") from exc

    return result


def get_auto_detect_pattern(settings=None):
    """Return the compiled episode filename detection pattern."""

    config = normalize_auto_detect_settings(settings)

    if config["format"] == "CUSTOM":
        pattern = config["custom_regex"]
    else:
        pattern = SUPPORTED_FORMATS[config["format"]]

    return re.compile(pattern, re.IGNORECASE)


def format_auto_episode(episode, settings=None):
    """Format episode numbers using the configured padding."""

    config = normalize_auto_detect_settings(settings)

    try:
        number = int(episode)
    except (TypeError, ValueError):
        return str(episode)

    return str(number).zfill(config["episode_padding"])


def clean_auto_detect_title(title, settings=None):
    """Remove configured words and channel prefixes from an anime title."""

    config = normalize_auto_detect_settings(settings)

    title = str(title or "").strip()

    # Remove a channel prefix such as @ANIFLIXANIMETAMIL -
    title = re.sub(r"^\s*@[^-]+\s*-\s*", "", title)

    # Remove bracketed release information.
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^)]*\)", " ", title)

    # Remove configured unwanted words as whole words.
    for word in config["remove_words"]:
        title = re.sub(
            rf"\b{re.escape(word)}\b",
            " ",
            title,
            flags=re.IGNORECASE,
        )

    title = re.sub(r"[_\.]+", " ", title)
    title = re.sub(r"\s+", " ", title)

    return title.strip(" -_").upper()


def get_auto_detect_settings_from_store(state_store):
    """Read auto-detection settings from the existing StateStore."""

    saved = state_store.get(
        "auto_detect_settings",
        get_default_auto_detect_settings(),
    )

    return normalize_auto_detect_settings(saved)


async def save_auto_detect_settings_to_store(state_store, settings):
    """Validate and persist auto-detection settings."""

    normalized = normalize_auto_detect_settings(settings)

    await state_store.set(
        "auto_detect_settings",
        normalized,
    )

    return normalized
