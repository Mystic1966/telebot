import os
import re
import asyncio
import logging
import aiohttp
import signal
import sys

from pyrogram.enums import ParseMode, ChatType
from pyrogram import Client, idle
from pyrogram.errors import (
    PeerIdInvalid,
    ChannelPrivate,
    UsernameNotOccupied,
    FloodWait,
)

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
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")
TARGET_GROUP = os.getenv("TARGET_GROUP", "@ccdumpgroup")
PYROGRAM_SESSION = os.getenv("PYROGRAM_SESSION", "")

MAX_CONCURRENT_GROUPS = 3
MESSAGE_BATCH_SIZE = 20
POLL_INTERVAL = 12
BIN_CACHE_SIZE = 1000
SEND_DELAY = 0.5
REQUEST_DELAY = 1.0
INIT_BATCH_SIZE = 5
INIT_DELAY = 2.0

user = None
is_running = True
last_processed_message_ids = {}
processed_messages = set()
source_groups = []
bin_cache = {}

def normalize_text(text):
    return (text or "").strip()

def is_approved_message(text):
    if not text:
        return False

    text_lower = text.lower()
    approved_patterns = [
        r"\bapproved\b",
        r"\bstatus:\s*approved\b",
        r"\bresponse:\s*card\s+added\b",
        r"\bcard\s+added\b",
        r"\bcharged\b",
        r"\blive\b",
    ]
    return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in approved_patterns)

def extract_message_fields(text):
    if not text:
        return {}

    fields = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()

    return fields

def extract_credit_cards(text):
    if not text:
        return []

    patterns = [
        r"\b(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})\b",
        r"\b(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})\b",
        r"\b(\d{13,19})\D+(\d{1,2})\D+(\d{2,4})\D+(\d{3,4})\b",
        r"(\d{13,19})\s*[\|/\-:\s]\s*(\d{1,2})\s*[\|/\-:\s]\s*(\d{2,4})\s*[\|/\-:\s]\s*(\d{3,4})",
        r"(\d{4})\s*(\d{4})\s*(\d{4})\s*(\d{4})\s*[\|/\-:\s]\s*(\d{1,2})\s*[\|/\-:\s]\s*(\d{2,4})\s*[\|/\-:\s]\s*(\d{3,4})",
    ]

    credit_cards = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            if len(match) == 4:
                card_number, month, year, cvv = match
                card_number = re.sub(r"[\s\-]", "", card_number)
                if 13 <= len(card_number) <= 19 and 1 <= int(month) <= 12 and len(cvv) >= 3:
                    if len(year) == 4:
                        year = year[-2:]
                    credit_cards.append(f"{card_number}|{month.zfill(2)}|{year}|{cvv}")

    return list(dict.fromkeys(credit_cards))

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
        await asyncio.sleep(REQUEST_DELAY)
        messages = []
        async for message in user.get_chat_history(chat_id, limit=limit):
            messages.append(message)
        return messages
    except (PeerIdInvalid, ChannelPrivate, UsernameNotOccupied):
        return []
    except FloodWait as e:
        if e.value <= 60:
            logger.info(f"⏳ Flood wait {e.value}s, waiting...")
            await asyncio.sleep(e.value + 1)
            return await safe_get_chat_history(chat_id, limit)
        return []
    except Exception:
        return []

def format_card_message(fields, cc_data):
    status = fields.get("status", "Unknown")
    response = fields.get("response", "Unknown")
    gateway = fields.get("gateway", "Unknown")
    bank = fields.get("bank", "Unknown")
    card_type = fields.get("type", "Unknown")
    country = fields.get("country", "Unknown")
    checked_by = fields.get("checked by", "Unknown")
    credits_left = fields.get("credits left", "Unknown")

    return (
        f"CC: {cc_data}\n"
        f"Status: {status}\n"
        f"Response: {response}\n"
        f"Gateway: {gateway}\n"
        f"Bank: {bank}\n"
        f"Type: {card_type}\n"
        f"Country: {country}\n"
        f"Checked by: {checked_by}\n"
        f"Credits left: {credits_left}"
    )

async def send_to_target_group(formatted_message, cc_data):
    try:
        await user.send_message(
            chat_id=TARGET_GROUP,
            text=formatted_message,
            parse_mode=ParseMode.DEFAULT
        )
        logger.info(f"✅ Sent CC {cc_data[:12]}***")
        await asyncio.sleep(SEND_DELAY)
    except FloodWait as e:
        if e.value <= 30:
            await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"❌ Send failed: {e}")

async def process_message_for_approved_ccs(message, group_title="Unknown"):
    global processed_messages
    try:
        unique_key = f"{message.chat.id}:{message.id}"
        if unique_key in processed_messages:
            return
        processed_messages.add(unique_key)

        if len(processed_messages) > 5000:
            processed_messages = set(list(processed_messages)[-2000:])

        text = (message.text or message.caption or "").strip()
        logger.info(f"DEBUG chat={message.chat.id} msg={message.id} text={text[:150]!r}")

        if not text:
            return

        if not is_approved_message(text):
            logger.info(f"DEBUG skipped approved=False chat={message.chat.id} msg={message.id}")
            return

        fields = extract_message_fields(text)
        credit_cards = extract_credit_cards(text)

        logger.info(f"DEBUG approved=True cards={credit_cards} fields={fields}")

        if not credit_cards:
            logger.info(f"DEBUG no card match chat={message.chat.id} msg={message.id}")
            return

        logger.info(f"🎯 {len(credit_cards)} CCs from {group_title[:20]}")
        tasks = []
        for cc in credit_cards:
            formatted_message = format_card_message(fields, cc)
            tasks.append(asyncio.create_task(send_to_target_group(formatted_message, cc)))

        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error(f"❌ Error processing message: {e}")

async def process_group_batch(group_batch):
    for group_info in group_batch:
        if not is_running:
            break
        try:
            await process_single_group(group_info)
            await asyncio.sleep(0.3)
        except Exception:
            pass

async def process_single_group(group_info):
    group_id = group_info["id"]
    group_title = group_info["title"]

    try:
        messages = await safe_get_chat_history(group_id, limit=MESSAGE_BATCH_SIZE)
        if not messages:
            return

        new_messages = []
        last_id = last_processed_message_ids.get(group_id, 0)

        for message in messages:
            if message.id > last_id:
                new_messages.append(message)

        if new_messages:
            new_messages.reverse()
            tasks = [
                asyncio.create_task(process_message_for_approved_ccs(message, group_title))
                for message in new_messages
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            last_processed_message_ids[group_id] = max(
                last_processed_message_ids.get(group_id, 0),
                max(msg.id for msg in new_messages)
            )
    except Exception as e:
        logger.error(f"❌ Error processing group {group_title}: {e}")

async def poll_multiple_groups():
    global is_running, source_groups
    logger.info("🔄 Starting RATE-LIMITED multi-group polling...")

    source_groups = await get_all_groups_and_channels()
    if not source_groups:
        logger.error("❌ No groups found!")
        return

    for i in range(0, len(source_groups), INIT_BATCH_SIZE):
        if not is_running:
            break

        batch = source_groups[i:i + INIT_BATCH_SIZE]
        for group_info in batch:
            if not is_running:
                break
            try:
                await init_group(group_info)
                await asyncio.sleep(0.5)
            except Exception:
                pass

        if i + INIT_BATCH_SIZE < len(source_groups):
            await asyncio.sleep(INIT_DELAY)

    poll_count = 0
    while is_running:
        try:
            poll_count += 1
            logger.info(f"🔄 Poll {poll_count} - Processing {len(source_groups)} groups...")

            for i in range(0, len(source_groups), MAX_CONCURRENT_GROUPS):
                if not is_running:
                    break
                batch = source_groups[i:i + MAX_CONCURRENT_GROUPS]
                await process_group_batch(batch)
                await asyncio.sleep(1.0)

            logger.info(f"✅ Completed poll {poll_count}. Waiting {POLL_INTERVAL}s...")
            await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.error(f"❌ Error in polling loop: {e}")
            await asyncio.sleep(60)

async def init_group(group_info):
    group_id = group_info["id"]
    try:
        messages = await safe_get_chat_history(group_id, limit=1)
        if messages:
            last_processed_message_ids[group_id] = messages[0].id
        else:
            last_processed_message_ids[group_id] = 0
    except Exception:
        last_processed_message_ids[group_id] = 0

def signal_handler(signum, frame):
    global is_running
    logger.info(f"🛑 Received signal {signum}, shutting down gracefully...")
    is_running = False

async def main():
    global user
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if not PYROGRAM_SESSION:
            raise RuntimeError("PYROGRAM_SESSION env var is missing")

        user = Client(
            "my_account",
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=PHONE_NUMBER,
            session_string=PYROGRAM_SESSION
        )

        logger.info("🔄 Starting bot...")
        await user.start()
        logger.info("✅ Client started successfully")

        try:
            me = await user.get_me()
            logger.info(f"👤 Logged in as: {me.first_name} (@{me.username}) - ID: {me.id}")
        except Exception as e:
            logger.warning(f"⚠️ Could not get user info: {e}")

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
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
    finally:
        logger.info("🛑 Stopping client...")
        try:
            if user is not None and user.is_connected:
                await user.stop(block=False)
            logger.info("✅ Client stopped cleanly")
        except Exception as e:
            logger.error(f"❌ Error stopping client: {e}")

if __name__ == "__main__":
    try:
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Monitor stopped by user")
    except Exception as e:
        logger.error(f"💀 Fatal error: {e}")
    finally:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                pending = asyncio.all_tasks(loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        sys.exit(0)
