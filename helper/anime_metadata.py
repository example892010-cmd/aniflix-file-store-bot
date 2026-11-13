
import re


AUTO_SEASON_RE = re.compile(
    r"\bS(?:EASON)?\s*0*(\d{1,2})\b",
    re.IGNORECASE,
)

AUTO_EPISODE_RE = re.compile(
    r"\b(?:EPISODE|EP|E)\s*0*(\d{1,4})\b",
    re.IGNORECASE,
)


def _message_filename(message):
    """Get the filename or caption/text from a Telegram message."""
    media = getattr(message, "document", None)

    if media is None:
        media = getattr(message, "video", None)

    if media is None:
        media = getattr(message, "audio", None)

    if media is None:
        media = getattr(message, "animation", None)

    if media is not None:
        file_name = getattr(media, "file_name", None)
        if file_name:
            return file_name

    caption = getattr(message, "caption", None)
    if caption:
        return caption

    text = getattr(message, "text", None)
    if text:
        return text

    return ""


def _clean_anime_title(title):
    """Clean channel prefixes and release tags from the anime title."""
    title = re.sub(r"^@\S+\s*-\s*", "", title)
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^)]*\)", " ", title)

    # Remove the release-specific trailing name requested for Dragon Ball Super.
    title = re.sub(
        r"\s+\b(?:BERUS|BEERUS)\b\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(r"[_\.]+", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip().upper()


def detect_auto_batch_metadata(messages):
    """
    Detect anime title, season and episode numbers from a group of messages.

    Returns:
        (title, episodes)

        episodes is a dictionary:
        {
            season_number: {episode_number, ...}
        }
    """
    title = None
    episodes = {}

    for message in messages:
        filename = _message_filename(message)
        if not filename:
            continue

        season_match = AUTO_SEASON_RE.search(filename)
        episode_match = AUTO_EPISODE_RE.search(filename)

        if not season_match or not episode_match:
            continue

        season = int(season_match.group(1))
        episode = int(episode_match.group(1))

        # The title is the part before the season marker.
        raw_title = filename[:season_match.start()]
        cleaned_title = _clean_anime_title(raw_title)

        if cleaned_title and title is None:
            title = cleaned_title

        episodes.setdefault(season, set()).add(episode)

    return title or "ANIME", episodes


def build_auto_batch_result(title, episodes, url):
    """
    Build the final /batch1renamed message.

    Example:
        /batch1renamed
        DRAGON BALL SUPER
        Season 01
        01
        02
        https://t.me/ANIFLIX_FILE_STORE_BOT?start=batch_bSCE
    """
    title = _clean_anime_title(title or "ANIME")

    lines = [
        "/batch1renamed",
        title,
    ]

    if episodes:
        for season in sorted(episodes):
            lines.append(
                f"Season {season:02d}" if season < 10 else f"Season {season}"
            )

            for episode in sorted(episodes[season]):
                lines.append(
                    f"{episode:02d}" if episode < 10 else str(episode)
                )
    else:
        lines.append("Season 01")

    lines.append(url)

    return "\n".join(lines)
