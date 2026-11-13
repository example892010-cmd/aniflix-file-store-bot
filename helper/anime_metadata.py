import re


# Matches S01, S18, Season 01, Season 1, etc.
AUTO_SEASON_RE = re.compile(
    r"(?:^|[\s._\-\[\(])S(?:EASON)?[\s._\-]*0*(\d{1,2})(?=[\s._\-\]\)]|E(?:P(?:ISODE)?)?\s*\d|$)",
    re.IGNORECASE,
)

# Matches E01, EP01, Ep383, Episode 01 (searched AFTER the season tag)
AUTO_EPISODE_RE = re.compile(
    r"(?:^|[\s._\-\[\(])E(?:PISODE|P)?[\s._\-]*0*(\d{1,4})(?=[\s._\-\]\)]|$)",
    re.IGNORECASE,
)

# Fallback for formats like "S18 - 383" or "S18 383" where "Ep" is omitted
AUTO_BARE_EPISODE_RE = re.compile(
    r"^\s*[\-._\s]+\s*0*(\d{1,4})(?=[\s._\-\]\)]|$)",
)


def _message_filename(message):
    """Extract a filename from common Telegram message formats."""

    if isinstance(message, str):
        return message

    # Pyrogram and similar message media fields
    for media_name in ("document", "video", "audio", "animation"):
        media = getattr(message, media_name, None)

        if media:
            file_name = getattr(media, "file_name", None)
            if file_name:
                return file_name

            # Some wrappers store the filename inside media.document
            nested_document = getattr(media, "document", None)
            if nested_document:
                for attribute in getattr(nested_document, "attributes", []):
                    file_name = getattr(attribute, "file_name", None)
                    if file_name:
                        return file_name

    # Telethon-style file object
    file_obj = getattr(message, "file", None)
    if file_obj:
        file_name = getattr(file_obj, "name", None)
        if file_name:
            return file_name

    # Document attributes
    document = getattr(message, "document", None)
    if document:
        for attribute in getattr(document, "attributes", []):
            file_name = getattr(attribute, "file_name", None)
            if file_name:
                return file_name

    # Fallback to caption or text
    caption = getattr(message, "caption", None)
    if caption:
        return caption

    text = getattr(message, "text", None)
    if text:
        return text

    return ""


def _clean_anime_title(title):
    """Remove @channel prefixes, release tags, and underscores from the anime title."""

    title = title.strip()

    # Remove leading @channel_name separated by underscores, spaces, dots, or hyphens
    title = re.sub(r"^@[A-Za-z0-9_]+?(?:[\s._\-]+|$)", "", title)

    # Specifically catch @ANIFLIXANIMETAMIL even if jammed directly against the title
    title = re.sub(r"^@?ANIFLIXANIMETAMIL[\s._\-]*", "", title, flags=re.IGNORECASE)

    # Remove bracketed or parenthesized release tags like [1080p] or (Dual Audio)
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^)]*\)", " ", title)

    # Replace underscores, dots, hyphens, and multiple spaces with a single space
    title = re.sub(r"[\s.\-_]+", " ", title)

    return title.strip().upper()


def detect_auto_batch_metadata(messages):
    """
    Detect anime title, season and episode numbers.

    Supports a list of filenames or Telegram message objects.

    Returns:
        title: Anime title
        episodes: {season_number: {episode_numbers}}
    """

    title = None
    episodes = {}

    for message in messages:
        filename = _message_filename(message)

        if not filename:
            continue

        # Strip file extension (.mkv, .mp4, etc.) before parsing
        clean_filename = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename.strip())

        season_match = AUTO_SEASON_RE.search(clean_filename)
        if not season_match:
            continue

        # Search for episode strictly AFTER the season tag so S18 never collides with Ep383
        after_season = clean_filename[season_match.end():]
        episode_match = AUTO_EPISODE_RE.search(after_season) or AUTO_BARE_EPISODE_RE.search(after_season)

        if not episode_match:
            continue

        season = int(season_match.group(1))
        episode = int(episode_match.group(1))

        # Get title from text before S01/S18/Season 01
        raw_title = clean_filename[:season_match.start()]
        cleaned_title = _clean_anime_title(raw_title)

        if cleaned_title and (title is None or title == "ANIME"):
            title = cleaned_title

        # Sets prevent duplicate episode numbers when multiple qualities exist.
        episodes.setdefault(season, set()).add(episode)

    return title or "ANIME", episodes


def build_auto_batch_result(title, episodes, url):
    """
    Build the /batch1renamed output without blank lines or /FORMAT.
    """

    title = _clean_anime_title(title or "ANIME")

    lines = [
        "/batch1renamed",
        title,
    ]

    if episodes:
        for season in sorted(episodes):
            if season < 10:
                lines.append(f"Season {season:02d}")
            else:
                lines.append(f"Season {season}")

            for episode in sorted(episodes[season]):
                if episode < 10:
                    lines.append(f"{episode:02d}")
                else:
                    lines.append(str(episode))
    else:
        lines.append("Season 01")

    lines.append(url)

    return "\n".join(lines)
