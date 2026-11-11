# ANIFLIX persistence and FSUB migration notes

This reconstruction retains the existing `#ANIFLIX_STATE_STORE_V1#` pinned archive-channel record and imports it into a versioned SQLite snapshot. The Telegram archive channel remains the file store. Existing archive messages, message IDs, and deep-link payloads are not rewritten.

## SQLite migration

- Set `SQLITE_PATH` to the desired SQLite file path. Default: `data/aniflix.sqlite3`.
- On first schema upgrade, the database module creates a `.pre-migration.bak` backup before applying additive schema changes.
- SQLite migrations use `CREATE TABLE IF NOT EXISTS`; they do not drop or truncate tables.
- The state snapshot is imported from the existing pinned state message or legacy `bot_state_data.json` cache when available.
- SQLite is written first; the existing archive state record is maintained as a recovery copy.
- If SQLite is newer than the archive snapshot, the newer SQLite state is republished to the archive state message. If no state snapshot exists anywhere, startup fails closed rather than creating empty defaults. No archive file messages are touched.

## FSUB behavior

Both Normal Join and Join Request require confirmed Telegram membership to access files. A `chat_join_request` update is recorded as pending evidence only. Acceptance is counted only after membership is confirmed. The bot does not automatically approve or decline requests.

## Important Render limitation

Render Free uses an ephemeral filesystem. `SQLITE_PATH=data/aniflix.sqlite3` works during a running instance but is not durable across every redeploy/restart. The existing Telegram state message provides a recovery copy for settings and counters, but it is not a substitute for a persistent SQLite disk for all normalized metadata. For durable SQLite file metadata, attach a persistent disk on a plan that supports it and set `SQLITE_PATH` to its mount path (for example `/var/data/aniflix.sqlite3`). Do not switch or recreate the Telegram archive channel.
