# ANIFLIX FILE STORE BOT

A Pyrogram bot that copies submitted messages into a Telegram archive channel and generates Telegram deep links for retrieval. It uses no MongoDB or other database service.

## Commands and current behavior

- `/start` — show the welcome text or retrieve the file/batch named by a deep-link payload. Force-subscription channels configured in the state are checked before delivery.
- `/getlink` — start a private multi-message upload session. Send messages, then use **GENERATE LINK**, **PAUSE**, **RESUME**, or **CANCEL**. New multi-message links point to a verified ordered manifest, so unrelated archive messages between uploaded items are not included. Sessions are limited to 200 messages and are held in memory until link generation.
- `/batch` — admin-only. Provide the first and last source-channel post by forwarding each post or sending its Telegram post link. The bot reads explicit message IDs in that bounded range, skips unavailable messages, archives successful copies, and creates a manifest-backed link. The source range is limited to 200 IDs. Use this only for content you own or have permission to republish.
- `/admin` — show the available admin dashboard and basic counters.
- `/status` — admin-only status and counters.
- `/users` — admin-only registered-user count.
- `/broadcast` — admin-only; reply to a message with `/broadcast` to copy it to registered users.
- `/addadmin <numeric_user_id>` and `/deladmin <numeric_user_id>` — owner-only management of extra stored administrators. The owner and `ADMINS` environment IDs cannot be removed.

The admin dashboard's existing **Edit Welcome** action now stores a new text welcome message. **FSUB Settings** supports adding/removing up to 10 channels and editing the subscription prompt. The bot must be an administrator in every FSUB channel for membership checks. The **Broadcast** dashboard action explains the existing reply-and-command flow; the command currently sends immediately and does not ask for a second confirmation.

The existing single-message deep-link payloads and old inclusive range-based batch payloads remain decodable. New batch links use the existing `?start=batch_...` entry point but refer to a small archive manifest containing the exact ordered archive message IDs. Legacy range links are bounded to 200 IDs; because an old range payload did not record an exact manifest, its IDs may include messages that were not part of the original batch if unrelated archive posts were interleaved.

## Telegram permissions and `/batch`

Make the bot an administrator in the private archive channel with permission to post, edit, and pin messages. For `/batch`, the bot must be able to resolve the source channel and read the selected posts; make it an administrator in that source channel when using the bot account.

State initialization and persistence do **not** call `get_chat_history`. It uses the pinned state message and, when available, a locally cached explicit message ID. `/batch` requests only the user-specified message IDs; it does not scan arbitrary history.

If Telegram returns `BOT_METHOD_INVALID` while `/batch` attempts to retrieve source posts using the bot account, set the optional `BATCH_USER_SESSION_STRING` to a separately authorized MTProto user session that can access that source channel. The bot starts this as a distinct read-only-in-purpose Pyrogram client for `/batch` source reads; archive writes and file delivery continue to use the bot account. Keep that session string private and enter it only in Render's secret environment-variable field. Do not commit it to Git or paste it into logs. This optional setting is not required for normal bot operation or state loading.

## Persistence and Render Free

Persistent settings are stored as a small text record in the archive channel's pinned message. The archive channel must remain private and limited to trusted administrators. If the bot cannot edit or pin the record, settings may not persist after a restart. The local `bot_state_data.json` file is only a cache: Render Free has ephemeral local storage, so it must not be treated as permanent storage. The state is stored in a single Telegram text message and is subject to Telegram's message-size limit; the bot logs an error rather than pretending an oversized state was saved remotely.

Render Free web services can spin down or restart. The `/health` endpoint and UptimeRobot can help with availability checks, but they do not provide guaranteed always-on bot polling or durable local storage. Use Free for testing/best-effort operation and choose an always-on deployment for reliable continuous polling.

## Render deployment

1. Push the contents of this ZIP's project folder to a GitHub repository.
2. In Render, choose **New + → Blueprint** and connect that repository, or create a Python Web Service from the repository.
3. Use the repository's `render.yaml` settings, or enter:
   - Build command: `pip install --no-cache-dir -r requirements.txt`
   - Start command: `python main.py`
   - Health check path: `/health`
4. Set these required environment variables in Render:
   - `BOT_TOKEN` — token from BotFather
   - `API_ID` and `API_HASH` — Telegram API credentials
   - `OWNER_ID` — your numeric Telegram user ID
   - `DB_CHANNEL` — private archive channel ID in `-100…` form; despite the legacy name, this is not a database
5. Optionally set `ADMINS` as comma-separated numeric Telegram user IDs and `PROTECT_CONTENT=true` or `false`.
6. Leave `BATCH_USER_SESSION_STRING` blank unless `/batch` receives `BOT_METHOD_INVALID` for source message retrieval. If required, provide a separately authorized session securely through Render's environment settings.
7. Add the bot as an administrator in the archive channel with posting, editing, and pinning permissions. For source retrieval in `/batch`, also add it as an administrator in the source channel unless using the optional reader session.
8. Deploy, wait for the `/health` check to pass, then test `/getlink` with a few permitted messages. Test `/batch` only with a channel you control or have permission to republish.

Never add real tokens, API hashes, or session strings to `.env.example` or Git. `.env.example` contains variable names only. No live Telegram deployment test was performed as part of these local repairs.

## Local checks

Install declared dependencies and run:

```bash
pip install -r requirements.txt
python -m compileall -q .
python -m unittest discover -s tests -v
```

The tests use mocked Telegram operations; they do not claim live Telegram verification.

## Scope note

The uploaded source provided counters but not upload-history browsing, failed-upload tracking, or a detailed storage inventory; these are not claimed as working features. `/users` reports the registered-user count rather than a paginated user list. The bot also does not include a broadcast confirmation step. Existing deployments should first test configuration changes with a small permitted sample.
