# ANIFLIX interactive Force Subscription workflow

## Add a channel

1. Open `/admin` → **FSUB Settings** → **Add Channel**.
2. Add the bot to the target channel as an administrator.
3. Grant **Invite Users via Link** permission.
4. Forward a message from that channel to the bot.
5. The bot identifies the channel and verifies its administrator status and invite-link permission.
6. Choose **JOIN REQUEST** or **NORMAL JOIN**.
7. The bot creates the matching invite link and saves the configuration in SQLite and the archive-backed recovery state.

## Channel management

Administrators can remove a channel or edit its display name from the FSUB settings panel. Editing the display name does not change its channel ID, invite link, method, or statistics.

## User verification

- **NORMAL JOIN:** The user must actually join. **TRY AGAIN** checks Telegram membership; only a confirmed member passes.
- **JOIN REQUEST:** Telegram's `chat_join_request` update records a unique pending request made through the configured invite link. A pending request does **not** unlock files. The user must wait for a channel administrator to approve the request. The bot then verifies actual membership through Telegram membership checks/updates before granting access.
- The bot never automatically approves or declines requests.
- API errors, missing permissions, unknown status, and unconfirmed membership fail closed.

## Statistics

- Normal Join: unique confirmed members.
- Join Request: unique requests, unique accepted requests confirmed by membership, and pending requests (`requests - accepted`).
- Clearing a channel's displayed statistics does not remove its channel configuration, invite link, or saved request evidence used to correlate a later membership acceptance.
- Statistics cannot reconstruct updates Telegram never delivered while the bot was offline or lacked channel administrator permissions.

## Deployment

The bot must remain an administrator in configured channels. Join Request setup also requires permission to create invite links. No request is automatically approved or declined.
