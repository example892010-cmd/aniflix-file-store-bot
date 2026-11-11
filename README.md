# ANIFLIX FILE STORE BOT

ANIFLIX is the primary project. It uses Pyrofork, the existing Telegram archive channel, ordered batch manifests, and SQLite for local persistence. The reference FileLinkBot repository is not included as runtime code.

## Preserved behavior

- `/start` — ANIFLIX welcome screen or retrieval through an existing deep-link payload.
- `/getlink` — bounded multi-message upload session, original private-chat order preserved, with generate/pause/resume/cancel controls.
- `/batch` — admin-only source-channel range archiver (maximum 200 source message IDs per batch).
- `/admin` — existing ANIFLIX admin dashboard.
- `/status` — admin counters.
- `/users` — registered users with available names, usernames and IDs, paginated in groups of 35.
- `/broadcast` — reply to a message with `/broadcast` to copy it to registered users.
- `/addadmin <numeric_user_id>` and `/deladmin <numeric_user_id>` — owner-only extra administrator management.
- Existing welcome and FSUB custom text/photo+caption support.
- FSUB channel removal and display-name editing.
- Existing callback names, ANIFLIX buttons, archive channel and existing file-link formats.

## File and link compatibility

- The existing Telegram archive channel remains the actual file store.
- Single-file deep links keep their established encoded message-ID format.
- Old inclusive range batch payloads remain readable (bounded to 200 IDs).
- New batches use a verified `ANIFLIX_BATCH_V1` manifest containing exact ordered archive message IDs.
- Archive message IDs are not renumbered or rewritten.
- The new SQLite `file_records` table records source/archive metadata only after the archive copy has been verified. If a previously recorded source message is retried and its archive copy still exists, the bot reuses it.
- Missing archive messages produce a clear unavailable-file response; the bot does not fabricate a replacement message.

The bot does not delete Telegram archive messages. Do not manually delete or replace the pinned ANIFLIX state message unless you have a separate verified backup.

## SQLite and state recovery

`SQLITE_PATH` selects the SQLite database file. The default is `data/aniflix.sqlite3`.

The database contains:
- A versioned state snapshot for ANIFLIX users, settings, FSUB configuration/statistics and counters.
- Verified archive file metadata.
- Generated-link metadata.

Migrations are additive. The database checks its integrity, refuses a schema newer than the code supports, and creates a `.pre-migration.bak` backup before upgrading an existing recognized database. Unknown or incompatible existing schemas are backed up and rejected without modification so they can be inspected first. It does not drop or truncate tables.

The original `#ANIFLIX_STATE_STORE_V1#` message in the existing archive channel is retained as a recovery/compatibility snapshot. Existing `bot_state_data.json` is also read as a legacy local cache. When both SQLite and the Telegram state message exist, the snapshot revision is compared so a newer state is not silently overwritten by an older one. If no snapshot can be found anywhere, startup fails closed instead of pinning empty defaults that could hide existing settings.

**Important:** Render Free has an ephemeral filesystem. SQLite on Render Free is not guaranteed to survive redeploys or instance replacement. The archive-backed state snapshot can restore bot settings and counters, and old file links still point directly to Telegram archive message IDs, but the SQLite file metadata/link index is not a durable backup on Free. For durable SQLite metadata, attach a persistent disk on a Render plan that supports it and set `SQLITE_PATH` to the disk mount path (for example `/var/data/aniflix.sqlite3`). The Telegram archive channel remains the primary file store in either case.

## Force subscription (FSUB)

- Normal Join requires actual Telegram membership.
- Join Request records a unique request only when Telegram reports it through the configured invite link.
- A pending request does **not** unlock files.
- A request becomes accepted only after Telegram confirms actual membership.
- The admin statistics view refreshes the pending-request list using Pyrogram's read-only `get_chat_join_requests` method. A request that is no longer pending and is not a confirmed member is shown as closed (declined or withdrawn); Telegram does not expose which of those actions occurred to the bot.
- The bot never automatically approves or declines requests.
- Statistics are unique-user counts. Clearing one channel's displayed counts leaves its configuration, invite link and request evidence intact.
- Removing a channel removes it from active FSUB checks and clears only that channel's request/stat records.

The bot must be an administrator in every configured channel. Join Request setup additionally requires permission to create invite links. Membership/API errors fail closed.

## Environment variables

Required:
- `BOT_TOKEN` — Telegram bot token.
- `API_ID` — Telegram API ID.
- `API_HASH` — Telegram API hash.
- `OWNER_ID` — numeric Telegram owner ID.
- `DB_CHANNEL` — existing private archive channel ID in `-100…` form. Despite the legacy name, this is **not** a database.

Optional:
- `SQLITE_PATH` — SQLite file path; default `data/aniflix.sqlite3`.
- `ADMINS` — comma/semicolon-separated numeric admin IDs.
- `BATCH_USER_SESSION_STRING` — optional authorized MTProto reader session for `/batch` source reads only. Keep it in Render secret settings; never commit or log it.
- `PORT` — Render web health server port; Render supplies this automatically.
- `SESSION_NAME` — Pyrogram session name; default `aniflix_file_store`.
- `PROTECT_CONTENT` — `true`/`false`; default `true`.
- `BOT_DISPLAY_NAME`, `OWNER_CREDIT`, `DEVELOPER_CREDIT`, `UPDATES_URL`, `ONGOING_URL`, `SUPPORT_URL` — ANIFLIX display settings.
- `ARCHIVE_CHANNEL_ID` — optional alias for `DB_CHANNEL`.

See `.env.example` for names and non-secret defaults. Do not put real tokens, hashes, or session strings in the repository.

## Render deployment

The included `render.yaml` uses:
- Build: `pip install --no-cache-dir -r requirements.txt`
- Start: `python main.py`
- Health check: `/health`
- Plan: `free`

Set the five required variables above in Render. Leave `SQLITE_PATH` at its default on Free (with the persistence limitation described above). If you later attach a persistent disk, use its mount path for `SQLITE_PATH`.

Add the bot as an administrator in the existing archive channel with permission to post, edit and pin the state record. Do not create a replacement storage channel. For `/batch`, the bot must be able to access the source channel; use `BATCH_USER_SESSION_STRING` only if the bot account receives `BOT_METHOD_INVALID` when reading the explicitly selected source message IDs.

Render Free can spin down and restart; the health endpoint does not guarantee always-on Telegram polling. A live Telegram or Render deployment test has not been performed by local tests.

## Local checks

```bash
pip install --no-cache-dir -r requirements.txt
python -m compileall -q .
python -m unittest discover -s tests -v
```

Tests using fake Telegram clients are local checks only. They do not verify a real bot token, Telegram permissions, channel membership updates, live archive access, or Render deployment.
