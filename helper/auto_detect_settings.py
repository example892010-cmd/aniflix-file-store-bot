
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
    return deepcopy(DEFAULT_AUTO_DETECT_SETTINGS)


def normalize_auto_detect_settings(settings=None):
    result = get_default_auto_detect_settings()

    if isinstance(settings, dict):
        result.update(settings)

    result["enabled"] = bool(result.get("enabled", True))

    if result.get("format") not in SUPPORTED_FORMATS and result.get("format") != "CUSTOM":
        result["format"] = DEFAULT_AUTO_DETECT_SETTINGS["format"]

    try:
        result["episode_padding"] = max(
            1, min(5, int(result.get("episode_padding", 2)))
        )
    except (TypeError, ValueError):
        result["episode_padding"] = 2

    if not isinstance(result.get("remove_words"), list):
        result["remove_words"] = []

    result["manual_title"] = str(result.get("manual_title") or "").strip()
    result["custom_regex"] = str(result.get("custom_regex") or "").strip()

    return result


def get_auto_detect_pattern(settings=None):
    settings = normalize_auto_detect_settings(settings)

    if settings["format"] == "CUSTOM":
        pattern = settings["custom_regex"]

        if not pattern:
            raise ValueError("Custom regex is empty.")

        compiled = re.compile(pattern, re.IGNORECASE)

        if "season" not in compiled.groupindex:
            raise ValueError("Custom regex needs a named season group.")

        if "episode" not in compiled.groupindex:
            raise ValueError("Custom regex needs a named episode group.")

        return compiled

    return re.compile(
        SUPPORTED_FORMATS[settings["format"]],
        re.IGNORECASE,
    )


def format_auto_episode(episode, settings=None):
    settings = normalize_auto_detect_settings(settings)
    return str(int(episode)).zfill(settings["episode_padding"])


def clean_auto_detect_title(title, settings=None):
    settings = normalize_auto_detect_settings(settings)

    title = str(title or "").strip()

    for word in settings.get("remove_words", []):
        if word:
            title = re.sub(
                re.escape(str(word)),
                "",
                title,
                flags=re.IGNORECASE,
            )

    title = re.sub(r"[\[\](){}]", " ", title)
    title = re.sub(r"[_|]+", " ", title)
    title = re.sub(r"\s+", " ", title)

    return title.strip(" ._-–—")


def get_auto_detect_settings_from_store(state_store):
    saved = state_store.get(
        "auto_detect_settings",
        get_default_auto_detect_settings(),
    )
    return normalize_auto_detect_settings(saved)


async def save_auto_detect_settings_to_store(state_store, settings):
    normalized = normalize_auto_detect_settings(settings)
    await state_store.set("auto_detect_settings", normalized)
    return normalized
