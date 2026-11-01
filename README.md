# ANIFLIX FILE BOT

This patch adds the requested message-link workflows and admin tools to the existing Telegram-only ANIFLIX build. Extract this patch over the existing project folder, keeping the folder paths shown in the ZIP.

## Commands

- `/getlink` — start a private upload session. Send one or more supported messages (text, photos, videos, documents, stickers, audio, voice, animations, captions, and other copyable messages). The bot updates the count and provides **GENERATE LINK**, **CANCEL**, and **PAUSE/RESUME** buttons. On generation, messages are copied and verified one at a time in original order. Sessions support up to 200 messages. If Telegram temporarily rate-limits a save, use **GENERATE LINK** again to resume from the last verified message.
- `/batch` — forward the first and then last message from a source channel. The bot checks the range (up to 200 message IDs), retrieves available messages in ascending message-ID order, and archives supported non-service messages. To keep channel history access and sharing permission explicit, the bot must be an administrator in the source channel. Use only a channel you own or have permission to republish.
- `/admin` — open the admin panel.
- `/users` — admin-only paginated list of registered users and total count.
- `/broadcast` — reply to a message with `/broadcast`, confirm, and the bot copies it to registered users.
- `/about`, `/help`, `/status`, `/cancel` — information, status, or cancellation.

Single-message links and ordered batch links use Telegram deep-link payloads. Batch links point to a manifest message in the archive channel containing the ordered archive message IDs.

## Admin panel

- Set the welcome message as text or a photo with caption.
- Set the force-subscription prompt as text or a photo with caption.
- Add/remove up to 10 force-subscription channels. Send an entry as `@channel-or-channel-id | https://t.me/invite-link | button label`. The bot must be an administrator in every force-subscription channel so it can check membership.
- View users, link counts, basic bot statistics, and archive-channel information.
- Add/remove extra admins (the owner and `ADMINS` environment IDs cannot be removed in the panel).
- Pause/resume new upload sessions globally.

The `/about` command uses the ANIFLIX credits and a Telegram blockquote style. It no longer shows the previous MD BOTZ / MD GROUP credits.

## Persistence without MongoDB

The archive channel is also used to persist the bot's small settings record and registered-user records as internal text messages. This is necessary because Render Free local storage is not durable across restarts. These internal records are stored in the same private archive channel as media and may be visible to that channel's administrators. Keep the archive channel private and limit access to trusted admins. Do not delete messages beginning with `ANIFLIX_STATE_V1:` or `ANIFLIX_USER_V1:`; the bot uses them to restore its settings and user list. The bot maintains only one current state record when possible; older copies are removed when it has permission.

## Important limitations

- Telegram may reject copying protected, deleted, restricted, or otherwise non-copyable messages. The bot does not bypass those restrictions.
- `/batch` requires the bot to be an administrator in the source channel and should only be used for content you own or have permission to republish.
- A batch range may contain deleted message IDs or service messages. Those are skipped; the bot reports the number saved. Other supported messages are kept in original message-ID order.
- The `/getlink` session queue is held in memory until link generation. An unfinished session may be lost if Render restarts before you generate its link. Messages already copied to the archive remain there.
- Batch retrieval sends the archived messages one by one in order. Telegram does not necessarily recreate the original album grouping.
- Links work only while the bot account, its username, archive channel, and referenced archive messages remain available.
- The bot was tested locally with mocked Telegram operations. Test with a small, permitted sample before storing important messages; a live Telegram test still requires your own bot token and channel setup.

## Render

Keep the same environment variables as the Telegram-only build:

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash
OWNER_ID=your_telegram_user_id
DB_CHANNEL=-1001234567890
```

`DB_CHANNEL` is the Telegram archive channel ID; it is not a database. The existing build/start commands remain:

```bash
pip install --no-cache-dir -r requirements.txt
python main.py
```

## Apply this patch

1. Keep a backup of your current project.
2. Extract this ZIP into the existing project root and allow it to replace matching files.
3. Deploy the updated code.
4. Make the bot an administrator in the archive channel and the channels you configure for force subscription.
5. First test `/getlink` with a few text/photo messages, then `/batch` only with a channel you control or have permission to republish.
