import os
import asyncio
import logging
import signal
import sys

from pyrogram import Client, idle
from pyrogram.enums import ChatType

try:
    import uvloop
    uvloop.install()
    print("✅ uvloop installed for better performance")
except ImportError:
    print("⚠️ uvloop not available, using default event loop")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PYROGRAM_SESSION = os.getenv("PYROGRAM_SESSION", "")

TARGET_GROUP = os.getenv("TARGET_GROUP", "@ccdumpgroup")

user = None
is_running = True
source_groups = []
last_processed_message_ids = {}

def signal_handler(signum, frame):
    global is_running
    logger.info(f"🛑 Received signal {signum}, shutting down gracefully...")
    is_running = False

async def get_all_groups_and_channels():
    try:
        logger.info("🔍 Discovering all groups and channels...")
        groups_and_channels = []
        dialog_count = 0

        async for dialog in user.get_dialogs():
            dialog_count += 1
            chat = dialog.chat
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                groups_and_channels.append({
                    "id": chat.id,
                    "title": chat.title or f"Chat_{chat.id}",
                    "type": str(chat.type),
                    "username": getattr(chat, "username", None)
                })

        logger.info(f"✅ Processed {dialog_count} dialogs")
        logger.info(f"✅ Discovered {len(groups_and_channels)} groups/channels")
        return groups_and_channels
    except Exception as e:
        logger.error(f"❌ Error getting groups: {e}")
        return []

async def safe_get_chat_history(chat_id, limit=20):
    try:
        messages = []
        async for message in user.get_chat_history(chat_id, limit=limit):
            messages.append(message)
        return messages
    except Exception:
        return []

async def process_single_group(group_info):
    group_id = group_info["id"]
    group_title = group_info["title"]

    try:
        messages = await safe_get_chat_history(group_id, limit=20)
        if not messages:
            return

        last_id = last_processed_message_ids.get(group_id, 0)
        new_messages = [m for m in messages if m.id > last_id]

        for message in reversed(new_messages):
            text = (message.text or message.caption or "").strip().lower()
            logger.info(f"DEBUG chat={group_title} msg={message.id} text={text!r}")

            if text == "hi":
                logger.info(f"✅ HI caught in {group_title} ({group_id})")

        if new_messages:
            last_processed_message_ids[group_id] = max(m.id for m in new_messages)

    except Exception as e:
        logger.error(f"❌ Error processing group {group_title}: {e}")

async def poll_multiple_groups():
    global is_running, source_groups
    logger.info("🔄 Starting test polling...")

    source_groups = await get_all_groups_and_channels()
    if not source_groups:
        logger.error("❌ No groups found!")
        return

    for group_info in source_groups:
        last_processed_message_ids[group_info["id"]] = 0

    while is_running:
        for group_info in source_groups:
            if not is_running:
                break
            await process_single_group(group_info)
            await asyncio.sleep(0.5)
        await asyncio.sleep(5)

async def main():
    global user
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not PYROGRAM_SESSION:
        raise RuntimeError("PYROGRAM_SESSION env var is missing")

    user = Client(
        "my_account",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=PYROGRAM_SESSION
    )

    logger.info("🔄 Starting bot...")
    await user.start()
    logger.info("✅ Client started successfully")

    me = await user.get_me()
    logger.info(f"👤 Logged in as: {me.first_name} (@{me.username}) - ID: {me.id}")

    polling_task = asyncio.create_task(poll_multiple_groups())
    try:
        await idle()
    finally:
        is_running = False
        if not polling_task.done():
            polling_task.cancel()
            try:
                await asyncio.wait_for(polling_task, timeout=5.0)
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"💀 Fatal error: {e}")
        sys.exit(1)
