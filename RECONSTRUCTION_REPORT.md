# ANIFLIX reconstruction report

## Project identity

- Main project preserved: `aniflix-file-store-bot-main`
- Reference studied only: `filelinkbot-main`
- The reference repository's MongoDB and multi-database implementation was not imported.

## Preservation boundary

The supplied ANIFLIX ZIP contains source code and tests, but no SQLite database file, no `bot_state_data.json`, and no Telegram archive export. The live archive channel and its pinned state message cannot be inspected from the ZIP. Consequently, this reconstruction does not claim to have verified production archive records or performed a live data migration.

The new state loader reads the existing `#ANIFLIX_STATE_STORE_V1#` pinned archive state and the legacy local JSON cache, imports the newest available snapshot into SQLite, and keeps the archive-backed state message as a recovery copy. It never deletes archive messages or changes existing archive message IDs or link payloads. If no state snapshot is found, startup now fails closed instead of silently creating empty defaults.

## Changes

- Added `helper/sqlite_store.py` with additive schema creation, integrity checks, SQLite state snapshots, verified archive file metadata, link metadata, WAL mode, and a pre-migration backup.
- Updated `helper/state_store.py` to compare state revisions and preserve the newest state across SQLite, legacy JSON, and the existing pinned archive state message.
- Updated `helper/storage.py` with a bounded archive semaphore, repeated FloodWait handling, network retry limits, source-message deduplication, and verified metadata writes.
- Preserved single-file link encoding, legacy range-batch decoding, and ordered batch manifest encoding.
- Corrected Join Request access so a pending request never unlocks a file.
- Added pending/accepted/closed-request reconciliation using Telegram's read-only pending join-request listing. "Closed" means no longer pending and not a confirmed member; Telegram does not distinguish decline from withdrawal in the bot's available data.
- Preserved per-channel count clearing without deleting request evidence, channel configuration, or invite links; added safe FSUB channel display-name editing.
- Added progress updates for `/getlink` and `/batch`.
- Kept the ANIFLIX admin panel, welcome editor, photo/caption support, user listing, broadcast, admin management, and existing callback names.
- Added `.env.example`, `.python-version`, `.gitignore`, and updated deployment/workflow documentation.

## Tests performed

- `python -m compileall -q .` — passed.
- Link codec tests — 5 passed.
- Upload-order tests — 5 passed.
- FSUB workflow static tests — 13 passed.
- FSUB removal tests — 3 passed.
- Custom welcome/FSUB media tests — 8 passed.
- Configuration/deployment tests — 8 passed.
- SQLite schema/backup tests — 5 passed.
- FSUB integration-source tests — 8 passed.
- With a minimal Pyrogram import stub and mocked Telegram clients: storage, batch-manifest, startup-state and FSUB-statistics tests — 11 passed.

Overall, 66 local test cases passed across the dependency-free suites and mocked-Telegram suites.\n\nThe Pyrogram package was not installed in this execution environment. Installing `pyrofork==2.3.69` was attempted but the package index was unreachable, so the full suite that imports the real Telegram library and runtime handler registration could not be completed here. Stubbed tests do not substitute for a real Pyrogram runtime.

## Not performed

- No connection to the live Telegram bot or archive channel.
- No real file upload, retrieval, join request, membership update, or invite-link test.
- No Render build or deployment.
- No test against the user's production SQLite database, because none was included.
- No verification that the production bot's pinned archive state message is still accessible or within Telegram's message size limit.

## Render constraint

Render Free web services have an ephemeral filesystem and do not support persistent disks. SQLite will run locally at `SQLITE_PATH`, but the SQLite file and its file/link metadata index can be lost on a redeploy, restart, or spin-down. The existing Telegram state message is retained as a recovery copy for bot state; old file links continue to resolve from their archive message IDs. Durable SQLite metadata requires a Render plan with a persistent disk.
