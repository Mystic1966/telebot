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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")
TARGET_GROUP = int(os.getenv("TARGET_GROUP", "-1003488487424"))
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
processing_semaphore = None

def is_approved_message(text):
    if not text:
        return False

    text_lower = text.lower()
    approved_patterns = [
        r'approved\s*✅',
        r'𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗\s*✅',
        r'𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝\s*✅',
        r'status:\s*approved',
        r'response:\s*approved',
        r'charged\s*💎',
        r'charged\s*✅',
        r'status:\s*charged',
        r'payment\s+successful\s*✅',
        r'response:\s*payment\s+successful',
        r'response:\s*\$[\d.]+\s+charged',
        r'response:\s*order_placed',
        r'response:\s*thank\s+you',
        r'message:\s*charged\s+\$?[\d.]',
        r'response:\s*payment\s+method\s+added\s+successfully',
        r'\$[\d.]+\s+charged!',
        r'Approved',
        r'approved',
        r'Charged',
        r'charged',
        r'LIVE',
        r'Card added',
    ]
    return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in approved_patterns)

async def get_all_groups_and_channels():
    try:
        logger.info("🔍 Discovering all groups and channels...")
        groups_and_channels = []
        dialog_count = 0

        async for dialog in user.get_dialogs():
            dialog_count += 1
            chat = dialog.chat
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                if chat.id != TARGET_GROUP:
                    groups_and_channels.append({
                        "id": chat.id,
                        "title": chat.title or f"Chat_{chat.id}",
                        "type": str(chat.type),
                        "username": getattr(chat, "username", None)
                    })
                else:
                    logger.info(f"⚠️ Skipped target group: {chat.title} - ID: {chat.id}")

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

def extract_credit_cards(text):
    if not text:
        return []

    patterns = [
        r'\b(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})\b',
        r'\b(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})\b',
        r'\b(\d{13,19})\D+(\d{1,2})\D+(\d{2,4})\D+(\d{3,4})\b',
        r'(\d{13,19})\s*[\|\/\-:\s]\s*(\d{1,2})\s*[\|\/\-:\s]\s*(\d{2,4})\s*[\|\/\-:\s]\s*(\d{3,4})',
        r'(\d{4})\s*(\d{4})\s*(\d{4})\s*(\d{4})\s*[\|\/\-:\s]\s*(\d{1,2})\s*[\|\/\-:\s]\s*(\d{2,4})\s*[\|\/\-:\s]\s*(\d{3,4})',
    ]

    credit_cards = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            if len(match) == 4:
                card_number, month, year, cvv = match
                card_number = re.sub(r'[\s\-]', '', card_number)

                if 13 <= len(card_number) <= 19 and 1 <= int(month) <= 12 and len(cvv) >= 3:
                    if len(year) == 4:
                        year = year[-2:]
                    credit_cards.append(f"{card_number}|{month.zfill(2)}|{year}|{cvv}")

    return list(dict.fromkeys(credit_cards))

async def get_bin_info(bin_number):
    global bin_cache

    if bin_number in bin_cache:
        return bin_cache[bin_number]

    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"https://bins.antipublic.cc/bins/{bin_number}") as response:
                if response.status == 200:
                    data = await response.json()
                    if len(bin_cache) > BIN_CACHE_SIZE:
                        bin_cache = dict(list(bin_cache.items())[-BIN_CACHE_SIZE // 2:])
                    bin_cache[bin_number] = data
                    return data
    except Exception:
        return None

    return None

def format_card_message(cc_data, bin_info):
    scheme = "UNKNOWN"
    card_type = "UNKNOWN"
    brand = "UNKNOWN"
    bank_name = "UNKNOWN BANK"
    country_name = "UNKNOWN"
    country_emoji = "🌍"

    if bin_info:
        brand = bin_info.get("brand", "UNKNOWN")
        scheme = brand
        card_type = bin_info.get("type", "UNKNOWN").upper()
        bank_name = bin_info.get("bank", "UNKNOWN BANK")
        country_name = bin_info.get("country_name", "UNKNOWN")
        country_emoji = bin_info.get("country_flag", "🌍")

    return f"""𝘾𝘼𝙍𝘿 ⇾ {cc_data}
𝙎𝙏𝘼𝙏𝙐𝙎 ⇾ 𝘈𝘱𝘱𝘳𝘖𝘝𝘌𝘋 💎
𝙈𝙀𝙎𝙎𝘼𝙂𝙀 ⇾ 𝘚𝘊𝘙𝘈𝘗𝘗𝘌𝘋 𝘚𝘌𝘹'𝘊𝘌𝘚𝘚𝘍𝘜𝘓𝘓𝘺
𝙂𝘼𝙏𝙀𝙒𝘼𝙔 ⇾ 𝘚𝘊𝘙𝘈𝘗𝘗𝘌𝙍 🍑
𝙄𝙉𝙁𝙊 ⇾ {scheme} - {card_type} - {brand}
𝘽𝘼𝙉𝙆 ⇾ {bank_name}
𝘾𝙊𝙐𝙉𝙏𝙍𝙔 ⇾ {country_name} {country_emoji}"""

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

async def process_message_for_approved_ccs(message, source_group_id, group_title="Unknown"):
    global processed_messages
    try:
        if message.id in processed_messages:
            return
        processed_messages.add(message.id)

        if len(processed_messages) > 5000:
            processed_messages = set(list(processed_messages)[-2000:])

        text = message.text or message.caption
        if not text or not is_approved_message(text):
            return

        credit_cards = extract_credit_cards(text)
        if not credit_cards:
            return

        logger.info(f"🎯 {len(credit_cards)} CCs from {group_title[:20]}")
        tasks = [asyncio.create_task(process_single_cc(cc)) for cc in credit_cards]
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"❌ Error processing message: {e}")

async def process_single_cc(cc_data):
    try:
        bin_number = cc_data.split("|")[0][:6]
        bin_info = await get_bin_info(bin_number)
        formatted_message = format_card_message(cc_data, bin_info)
        await send_to_target_group(formatted_message, cc_data)
    except Exception as e:
        logger.error(f"❌ Error processing CC: {e}")

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
                asyncio.create_task(process_message_for_approved_ccs(message, group_id, group_title))
                for message in new_messages
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            last_processed_message_ids[group_id] = max(
                last_processed_message_ids.get(group_id, 0),
                max(msg.id for msg in new_messages)
            )
    except Exception:
        pass

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

async def test_access():
    try:
        async for dialog in user.get_dialogs():
            chat = dialog.chat
            if chat.id == TARGET_GROUP:
                logger.info(f"✅ Target found in dialogs: {chat.title} ({chat.id})")
                return True
        logger.error("❌ Target group not found in dialogs")
        return False
    except Exception as e:
        logger.error(f"❌ Access check failed: {e}")
        return False

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

        if await test_access():
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
        else:
            logger.error("❌ Cannot proceed without proper access")
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
