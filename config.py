"""Environment-backed settings for ANIFLIX FILE STORE BOT."""
from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    owner_id: int
    archive_channel_id: int
    admins: tuple[int, ...]
    port: int = 10000
    protect_content: bool = True
    bot_display_name: str = "ANIFLIX FILE STORE BOT"
    owner_credit: str = "@TANJIROKAMADO404"
    developer_credit: str = "@TANJIROKAMADO404"
    updates_url: str = "https://t.me/+98iAagsDsjI5NDk1"
    ongoing_url: str = "https://t.me/+98iAagsDsjI5NDk1"
    support_url: str = "https://t.me/+1jDuhUQ41hA1YmVl"
    session_name: str = "aniflix_file_store"


def _required(env: dict[str, str], name: str, alias: str | None = None) -> str:
    value = env.get(name, "").strip()
    if not value and alias:
        value = env.get(alias, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _integer(value: str, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _boolean(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _https_or_empty(env: dict[str, str], name: str, default: str) -> str:
    value = env.get(name, default).strip()
    if value and urlparse(value).scheme != "https":
        raise ValueError(f"{name} must be an HTTPS URL")
    return value


def load_settings(source: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if source is None else source)
    token = _required(env, "BOT_TOKEN", "TOKEN")
    if ":" not in token:
        raise ValueError("BOT_TOKEN does not look like a Telegram bot token")

    api_id = _integer(_required(env, "API_ID"), "API_ID")
    owner_id = _integer(_required(env, "OWNER_ID"), "OWNER_ID")
    # DB_CHANNEL is retained as the environment name for compatibility with
    # the original setup; it is a Telegram channel ID, not a database.
    archive_raw = env.get("ARCHIVE_CHANNEL_ID", "").strip() or _required(env, "DB_CHANNEL")
    archive_channel_id = _integer(archive_raw, "DB_CHANNEL/ARCHIVE_CHANNEL_ID")
    if api_id <= 0 or owner_id <= 0:
        raise ValueError("API_ID and OWNER_ID must be positive integers")
    port = _integer(env.get("PORT", "10000"), "PORT")
    if not 0 < port < 65536:
        raise ValueError("PORT must be between 1 and 65535")
    if archive_channel_id >= 0 or not str(archive_channel_id).startswith("-100"):
        raise ValueError("DB_CHANNEL must be a Telegram channel ID beginning with -100")

    admins = {owner_id}
    for item in env.get("ADMINS", "").replace(";", ",").split(","):
        item = item.strip()
        if item:
            admin_id = _integer(item, "ADMINS entry")
            if admin_id > 0:
                admins.add(admin_id)

    return Settings(
        bot_token=token,
        api_id=api_id,
        api_hash=_required(env, "API_HASH"),
        owner_id=owner_id,
        archive_channel_id=archive_channel_id,
        admins=tuple(sorted(admins)),
        port=port,
        protect_content=_boolean(env.get("PROTECT_CONTENT"), True),
        bot_display_name=env.get("BOT_DISPLAY_NAME", "ANIFLIX FILE STORE BOT").strip() or "ANIFLIX FILE STORE BOT",
        owner_credit=env.get("OWNER_CREDIT", "@TANJIROKAMADO404").strip(),
        developer_credit=env.get("DEVELOPER_CREDIT", "@TANJIROKAMADO404").strip(),
        updates_url=_https_or_empty(env, "UPDATES_URL", "https://t.me/+98iAagsDsjI5NDk1"),
        ongoing_url=_https_or_empty(env, "ONGOING_URL", "https://t.me/+98iAagsDsjI5NDk1"),
        support_url=_https_or_empty(env, "SUPPORT_URL", "https://t.me/+1jDuhUQ41hA1YmVl"),
        session_name=env.get("SESSION_NAME", "aniflix_file_store").strip() or "aniflix_file_store",
    )
