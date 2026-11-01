# ANIFLIX FILE STORE BOT

A minimal Telegram-only file store for a Render Free Web Service. Admins send a file to the bot; the bot copies that Telegram message into one private archive channel, reads it back to verify the copy, and returns a direct Telegram start link. Anyone with the link can ask the bot to copy the archived message to them.

## What this build includes

- Admin-only private media uploads (the owner is always an admin).
- One configured Telegram archive channel.
- One-file-at-a-time archive operations to reduce concurrent load.
- Read-back verification before a link is issued.
- Stateless links: archive channel ID and message ID are encoded in the Telegram `/start` payload.
- Retrieval after a Render restart does not need a database lookup.
- `/start`, `/help`, `/about`, and admin-only `/status` commands.
- Render web service with `/health` endpoint.
- No MongoDB, SQL database, local media storage, auto-delete task, user database, premium system, shortener, or settings database.

## Before deployment

1. Create a **private Telegram channel** for archived files.
2. Add your bot to that channel as an **administrator** with permission to post messages and read channel history.
3. Get the numeric channel ID. It normally looks like `-1001234567890`.
4. Create a Render Blueprint from this repository and set the environment variables listed below.
5. Deploy, then confirm that `/health` returns HTTP 200.
6. Send a small test file from an admin account, check that it appears in the archive channel, and open the returned link from another Telegram account.

## Required environment variables

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Token from BotFather |
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `OWNER_ID` | Your numeric Telegram user ID |
| `DB_CHANNEL` | Telegram archive channel ID beginning with `-100` (this is **not** a database) |

`ADMINS` is optional and accepts additional numeric Telegram user IDs separated by commas. `OWNER_ID` is always included. `PORT` is provided by Render. The `ARCHIVE_CHANNEL_ID` alias is also accepted instead of `DB_CHANNEL`.

## Important storage notes

The archive copy is a Telegram channel message. The bot does not download files to Render, and the bot has no code that deletes archive messages. A file link continues to work only while the bot account, bot username, archive channel, and referenced Telegram message remain available and the bot can access the channel. Do not delete archived messages, remove the bot from the archive channel, or change the bot username if you need old links to keep working. No service can promise lifetime availability.

The retrieval link contains the archive channel and message ID. It does not depend on MongoDB or a local file/database on Render. The bot deliberately rejects payloads for channels other than the one configured in `DB_CHANNEL`.

## Render and UptimeRobot

This is configured as a Render **Web Service**, not a background worker. Its local filesystem is not used for archived media. Render Free services can have availability and usage limitations, so use UptimeRobot to monitor the `/health` endpoint rather than treating it as a guarantee of uninterrupted uptime.

## Local checks

Run the unit tests with:

```bash
python -m unittest discover -s tests -v
```

The tests exercise payload handling, mocked archive-and-verify behavior, mocked retrieval, environment validation, and deployment configuration. They do not replace a live test with your bot token and real Telegram archive channel.

## Source license

The original upstream license is retained in `LICENSE`. See `NOTICE.md` for source attribution and modification details.
