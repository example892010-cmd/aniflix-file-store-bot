"""Telegram-only archive operations. No local media or database is used."""


async def archive_and_verify(client, source_message, archive_channel_id: int) -> int:
    stored = await source_message.copy(
        chat_id=archive_channel_id,
        disable_notification=True,
    )
    stored_id = getattr(stored, "id", None)
    if not stored_id:
        raise RuntimeError("Telegram did not return an archive message ID")

    verified = await client.get_messages(
        chat_id=archive_channel_id,
        message_ids=stored_id,
    )
    if not verified or getattr(verified, "empty", False) or getattr(verified, "id", None) != stored_id:
        raise RuntimeError("The copied archive message could not be verified")
    return int(stored_id)


async def deliver_archived_message(
    client,
    archive_channel_id: int,
    message_id: int,
    destination_chat_id: int,
    protect_content: bool = True,
):
    archived = await client.get_messages(
        chat_id=archive_channel_id,
        message_ids=message_id,
    )
    if not archived or getattr(archived, "empty", False) or getattr(archived, "id", None) != message_id:
        raise LookupError("That file is no longer available in the archive")
    return await archived.copy(
        chat_id=destination_chat_id,
        protect_content=protect_content,
    )
