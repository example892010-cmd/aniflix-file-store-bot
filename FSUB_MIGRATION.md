# ANIFLIX FSUB migration patch

This patch ports the public repository's force-subscription configuration pattern into
ANIFLIX while retaining ANIFLIX's existing persistent `StateStore`, admin callbacks,
and `/start` deep-link format. It does not add MongoDB.

## Apply

Copy the files in this patch ZIP into the matching paths in your ANIFLIX repository,
overwriting `bot.py` and adding the new helper/test/documentation files.

## Add a channel from `/admin`

Open **FSUB Settings → Add Channel**. The new channel-ID format is:

`-1001234567890 | no | 0`

Fields:
1. Telegram channel ID (must begin with `-100`)
2. `yes` or `no` to create a join-request invite link
3. Invite expiry in minutes; `0` means no expiry

The bot must be an administrator in that channel and have permission to create invite
links. For a timed link, the bot generates a fresh invite when showing the FSUB gate.

The previous ANIFLIX format remains supported:

`@channelusername | https://t.me/join-link | Button label`

Up to 10 channels are still supported and settings remain saved through the existing
archive-channel-backed state store.

## Verification behavior

- `/start` and deep-link `/start PAYLOAD` both check FSUB.
- A deep-link payload is retained through the **Try Again** callback.
- Membership is accepted only for confirmed members, administrators, owners, or
  restricted users whose `is_member` flag is true.
- `LEFT`, `KICKED`, pending/unknown membership, and API errors do not bypass FSUB.
- The bot must be an administrator in every configured private channel to check users.
- Join-request invite links are supported, but a pending request does not count as
  membership; the user must be approved and become a member before access is granted.

No MongoDB, new environment variables, or storage architecture changes are required.

## Tests

Run:

```bash
python -m unittest tests.test_fsub_system -v
python -m compileall -q .
```

The tests cover membership-status interpretation and fail-closed behavior using a fake
Telegram client. They do not constitute live Telegram verification.
