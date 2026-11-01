# ANIFLIX interactive Force Subscription workflow

This change replaces manual `channel ID | invite URL | label` entry with the
interactive channel setup requested for the admin panel.

## Add a channel

1. Open `/admin` → **FSUB Settings** → **Add Channel**.
2. Add the bot to the target channel as an administrator.
3. Grant **Invite Users via Link** permission.
4. Forward a message from that channel to the bot.
5. The bot resolves the forwarded channel and verifies its administrator
   status and invite-link permission.
6. Choose **JOIN REQUEST** or **NORMAL JOIN**.
7. The bot creates the corresponding Telegram invite link and saves the
   channel, title, ID, invite link and selected method in the existing
   persistent state store.

The existing maximum of 10 FSUB channels remains in force. The FSUB menu
lists each channel and its selected method. Existing stored channel entries
are still readable and removable.

## Membership and manual approval

The bot does **not** approve join requests. For a JOIN REQUEST link, Telegram
places the user in a pending-request state until a channel administrator
approves or declines it. A pending request is not treated as membership.

When the user presses **TRY AGAIN**, the bot calls Telegram's
`get_chat_member` API again. File delivery is allowed only when Telegram
reports `MEMBER`, `ADMINISTRATOR`, or `OWNER`. A pending request, a user who
left, or an API/permission error remains blocked.

## Deployment

No new environment variable, database, shell command, or storage architecture
is introduced. Replace the edited `bot.py` and add this document and the
regression test file. Existing FSUB entries remain in the current state store.
