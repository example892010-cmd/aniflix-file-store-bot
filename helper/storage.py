import logging
import asyncio
from typing import Optional, Union, List
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, client: Client, archive_channel_id: int):
        self.client = client
        self.archive_channel_id = archive_channel_id

    async def store_message(self, message: Message) -> Optional[Message]:
        """
        Copies any incoming user message into the Archive Channel safely.
        Supports text, media, documents, stickers, audio, and albums.
        """
        try:
            copied = await message.copy(chat_id=self.archive_channel_id)
            return copied
        except FloodWait as f:
            logger.warning(f"FloodWait encountered: sleeping for {f.value}s")
            await asyncio.sleep(f.value)
            return await message.copy(chat_id=self.archive_channel_id)
        except RPCError as e:
            logger.error(f"Failed to store message in archive: {e}")
            return None

    async def get_message(self, message_id: int) -> Optional[Message]:
        """
        Retrieves a single message by ID directly from the archive channel.
        Uses get_messages (supported for bot accounts).
        """
        try:
            msg = await self.client.get_messages(self.archive_channel_id, message_id)
            if msg and not msg.empty:
                return msg
            return None
        except RPCError as e:
            logger.error(f"Failed to fetch message {message_id} from archive: {e}")
            return None

    async def get_messages_range(self, start_id: int, end_id: int) -> List[Message]:
        """
        Retrieves a range of messages in batches of up to 200 without using get_chat_history.
        """
        first = min(start_id, end_id)
        last = max(start_id, end_id)
        message_ids = list(range(first, last + 1))
        all_messages: List[Message] = []

        for i in range(0, len(message_ids), 200):
            chunk = message_ids[i:i + 200]
            try:
                msgs = await self.client.get_messages(self.archive_channel_id, chunk)
                if isinstance(msgs, list):
                    all_messages.extend([m for m in msgs if m and not m.empty])
                elif msgs and not msgs.empty:
                    all_messages.append(msgs)
            except FloodWait as f:
                await asyncio.sleep(f.value)
                msgs = await self.client.get_messages(self.archive_channel_id, chunk)
                if isinstance(msgs, list):
                    all_messages.extend([m for m in msgs if m and not m.empty])
            except RPCError as e:
                logger.error(f"Error fetching chunk {chunk[0]}-{chunk[-1]}: {e}")

        return all_messages

    async def deliver_message(self, to_chat_id: int, message_id: int) -> bool:
        """Copies a stored message from archive directly to the user."""
        try:
            await self.client.copy_message(
                chat_id=to_chat_id,
                from_chat_id=self.archive_channel_id,
                message_id=message_id
            )
            return True
        except FloodWait as f:
            await asyncio.sleep(f.value)
            await self.client.copy_message(
                chat_id=to_chat_id,
                from_chat_id=self.archive_channel_id,
                message_id=message_id
            )
            return True
        except Exception as e:
            logger.error(f"Failed to deliver message {message_id} to {to_chat_id}: {e}")
            return False
