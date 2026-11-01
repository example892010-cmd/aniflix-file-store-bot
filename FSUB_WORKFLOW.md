# ANIFLIX interactive Force Subscription workflow

## Add a channel

1. Open `/admin` → **FSUB Settings** → **Add Channel**.
2. Add the bot to the target channel as an administrator.
3. Grant **Invite Users via Link** permission.
4. Forward a message from that channel to the bot.
5. The bot identifies the channel and verifies its administrator status and invite-link permission.
6. Choose **JOIN REQUEST** or **NORMAL JOIN**.
7. The bot creates the matching Telegram invite link and stores the channel and method in the existing persistent state store.

## User verification

- **NORMAL JOIN:** The user must actually join. `TRY AGAIN` checks Telegram membership and does not unlock the file until membership is confirmed.
- **JOIN REQUEST:** The user opens the bot-generated request link and submits a join request. Telegram sends the bot a `chat_join_request` update; the bot verifies that the request came through this channel's configured FSUB invite link and stores the user/channel pair persistently. When the user presses `TRY AGAIN`, that recorded request is enough to unlock the file, even while the request is still pending. The user does not need to submit the same request again.
- The bot never approves or declines requests automatically. Channel administrators retain control of approval.

A request made before the bot receives the Telegram update, through a different invite link, or while the bot is not receiving updates cannot be verified retroactively. The bot must remain an administrator with **Invite Users via Link** permission so Telegram can deliver join-request updates.

## Deployment

No new environment variable, database, shell command, or manual invite-link entry is required. The join-request records are saved in the existing state store alongside the channel list. Existing channel entries remain readable and removable.
