import os
import re
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "bot")
TARGET_GROUP = os.getenv("TARGET_GROUP", "@ccdumpgroup")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

user = Client(
    name=SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH
)

def extract_cc(text: str):
    patterns = [
        r"\b\d{12,19}\|\d{1,2}\|\d{2,4}\|\d{3,4}\b",
        r"\b\d{12,19}\s+\d{1,2}\s+\d{2,4}\s+\d{3,4}\b"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0)
    return None

async def process_message_for_approved_ccs(message: Message):
    try:
        text = message.text or message.caption or ""
        if not text:
            return

        if "approved" not in text.lower():
            return

        cc = extract_cc(text)
        if not cc:
            logger.info(f"⚠️ Approved keyword found but no CC pattern matched in chat {message.chat.id}")
            return

        logger.info(f"✅ Match found in chat {message.chat.id}: {cc}")
        await user.send_message(TARGET_GROUP, f"Approved CC detected:\n{cc}")
        logger.info(f"📤 Sent to {TARGET_GROUP}")

    except Exception as e:
        logger.error(f"❌ Error processing message: {e}")

async def get_all_groups_and_channels():
    try:
        logger.info("🔍 Discovering all groups and channels...")
        chats = []
        dialog_count = 0
        target_username = TARGET_GROUP.lstrip("@") if isinstance(TARGET_GROUP, str) else None

        async for dialog in user.get_dialogs():
            dialog_count += 1
            chat = dialog.chat

            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                if target_username and getattr(chat, "username", None) == target_username:
                    logger.info(f"⚠️ Skipped target group: {chat.title} - ID: {chat.id}")
                    continue

                chats.append(chat)
                logger.info(f"📌 Added: {chat.title} - ID: {chat.id} - Type: {chat.type}")

        logger.info(f"✅ Processed {dialog_count} dialogs")
        logger.info(f"✅ Discovered {len(chats)} groups/channels")
        return chats

    except Exception as e:
        logger.error(f"❌ Error getting groups: {e}")
        return []

async def poll_chat(chat, delay=1.5):
    try:
        logger.info(f"🔄 Polling: {chat.title} - ID: {chat.id}")
        async for message in user.get_chat_history(chat.id, limit=25):
            await process_message_for_approved_ccs(message)
        await asyncio.sleep(delay)
    except Exception as e:
        logger.error(f"❌ Polling error for {chat.id}: {e}")

async def main():
    await user.start()
    me = await user.get_me()
    logger.info(f"👤 Logged in as: {me.first_name} (@{me.username}) - ID: {me.id}")

    chats = await get_all_groups_and_channels()

    logger.info("🔄 Starting RATE-LIMITED multi-group polling...")
    while True:
        for chat in chats:
            await poll_chat(chat)
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
