import os
import asyncio
import logging
import signal
import sys

from pyrogram import Client, idle
from pyrogram.errors import FloodWait

try:
    import uvloop
    uvloop.install()
    print("uvloop installed")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")
SOURCE_GROUP = int(os.getenv("SOURCE_GROUP", "0"))
TARGET_GROUP = int(os.getenv("TARGET_GROUP", "0"))

SESSION_NAME = "telegram_forwarder"

is_running = True
user = None
last_processed_message_id = 0

async def forward_message(message):
    global user
    try:
        await user.forward_messages(
            chat_id=TARGET_GROUP,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        logger.info(f"Forwarded message {message.id} from {message.chat.id} to {TARGET_GROUP}")
    except FloodWait as e:
        logger.warning(f"Flood wait {e.value}s")
        await asyncio.sleep(e.value + 1)
        await forward_message(message)
    except Exception as e:
        logger.error(f"Forward failed: {e}")

async def process_source_group():
    global last_processed_message_id, user, is_running
    while is_running:
        try:
            messages = []
            async for msg in user.get_chat_history(SOURCE_GROUP, limit=20):
                messages.append(msg)

            if messages:
                messages.reverse()
                new_messages = [m for m in messages if m.id > last_processed_message_id]

                for msg in new_messages:
                    await forward_message(msg)
                    last_processed_message_id = max(last_processed_message_id, msg.id)

            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(10)

def stop_handler(signum, frame):
    global is_running
    is_running = False

async def main():
    global user, last_processed_message_id
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    user = Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=PHONE_NUMBER
    )

    logger.info("Starting client...")
    await user.start()
    logger.info("Client started")

    try:
        last_msgs = []
        async for msg in user.get_chat_history(SOURCE_GROUP, limit=1):
            last_msgs.append(msg)
        if last_msgs:
            last_processed_message_id = last_msgs[0].id
        logger.info(f"Monitoring source {SOURCE_GROUP} -> target {TARGET_GROUP}")

        polling_task = asyncio.create_task(process_source_group())
        await idle()
        is_running = False
        polling_task.cancel()

    finally:
        if user and user.is_connected:
            await user.stop()
        logger.info("Client stopped")

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
