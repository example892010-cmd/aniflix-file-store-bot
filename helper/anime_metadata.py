import re


# Matches S01 / Season 01 even when surrounded by underscores, dots, or followed directly by E01
AUTO_SEASON_RE = re.compile(
    r"(?:^|[\s._\-\[\(])S(?:EASON)?[\s._\-]*0*(\d{1,2})(?=[\s._\-\]\)]|E(?:P(?:ISODE)?)?\s*\d|$)",
    re.IGNORECASE,
)

# Matches E01 / EP01 / Episode 01 even when preceded by S01 or underscores
AUTO_EPISODE_RE = re.compile(
    r"(?:^|[\s._\-\[\(]|S\d{1,2})E(?:PISODE|P)?[\s._\-]*0*(\d{1,4})(?=[\s._\-\]\)]|$)",
    re.IGNORECASE,
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
    title = re.sub(r"^@[A-Za-z0-9]+(?:[\s._\-]+|$)", "", title)

    # Remove bracketed release information
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

        season_match = AUTO_SEASON_RE.search(filename)
        episode_match = AUTO_EPISODE_RE.search(filename)

        if not season_match or not episode_match:
            continue

        season = int(season_match.group(1))
        episode = int(episode_match.group(1))

        # Get title from text before S01/S02/etc.
        raw_title = filename[:season_match.start()]
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
