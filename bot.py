import re
import asyncio
import logging
import aiohttp
import signal
import sys
import os
from datetime import datetime, timedelta
from pyrogram.enums import ParseMode, ChatType
from pyrogram import Client, filters, idle
from pyrogram.errors import (
    UserAlreadyParticipant,
    InviteHashExpired,
    InviteHashInvalid,
    PeerIdInvalid,
    ChannelPrivate,
    UsernameNotOccupied,
    FloodWait,
    RPCError
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

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
TARGET_GROUP = int(os.getenv("TARGET_GROUP", "-1002732639317"))

MAX_CONCURRENT_GROUPS = 3
MESSAGE_BATCH_SIZE = 20
POLL_INTERVAL = 12
BIN_CACHE_SIZE = 1000
SEND_DELAY = 0.5
REQUEST_DELAY = 1.0
INIT_BATCH_SIZE = 5
INIT_DELAY = 2.0

user = Client(
    "cc_monitor_user",
    api_id=API_ID,
    api_hash=API_HASH,
    phone_number=PHONE_NUMBER,
    workers=50,
    sleep_threshold=45,
    max_concurrent_transmissions=10
)

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
        r'approved\s*✅',
        r'status:\s*approved',
        r'status:\s*𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝',
        r'𝐒𝐭𝐚𝐭𝐮𝐬:\s*𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝',
        r'response:\s*approved',
        r'𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞\s*➳\s*approved',
        r'𝗦𝘁𝗮𝘁𝘂𝘀\s*:\s*approved',
        r'𝗖𝗖\s*:\s*[\d|]+\s*𝗦𝘁𝗮𝘁𝘂𝘀\s*:\s*approved',
        r'𝗦𝘁𝗮𝘁𝘂𝘀\s*:\s*approved',
        r'status\s*:\s*approved',
        r'approved\s*✅',
        r'𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱\s*✅',
        r'charged\s*💎',
        r'charged\s*✅',
        r'status:\s*charged',
        r'𝐬𝐭𝐚𝐭𝐮𝐬:\s*charged',
        r'𝘾𝙃𝘼𝙍𝙂𝙀𝘿\s*💎',
        r'charged\s+\$?[\d.]+\s*✅',
        r'status:\s*charged\s+\$?[\d.]+\s*✅',
        r'⌁\s*status:\s*charged',
        r'charged',
        r'Charged',
        r'Approved',
        r'approved',
        r'order_placed',
        r'thank_you',
        r'hit',
        r'www',
        r'Payment method added successfully',
        r'Thank you for your purchase!',
        r'Charged 💎',
        r'𝘾𝙃𝘼𝙍𝙂𝙀𝘿 💎',
        r'𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 ✅',
        r'𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙 ✅',
        r'𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙',
        r'𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿 ✅',
        r'Approved.!! ✅',
        r'𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ✅',
        r'APPROVED! ✅',
        r'Card added',
        r'LIVE',
        r'𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱! 𝗖𝗩𝗩',
        r'✅ 𝗖𝗵𝗮𝗿𝗴𝗲𝗱',
        r'Thanks for your purchase!',
        r'payment\s+successful\s*✅',
        r'𝐌𝐄𝐒𝐒𝐀𝐆𝐄:\s*payment\s+successful\s*✅',
        r'response:\s*payment\s+successful',
        r'response:\s*\$[\d.]+\s+charged',
        r'response:\s*order_placed',
        r'response:\s*thank\s+you',
        r'message:\s*charged\s+\$?[\d.]',
        r'response:\s*approval\s*-\s*\(00\)',
        r'message:\s*hit\s+\$[\d.]+\s*🔥',
        r'message:\s*insufficient\s+funds\s*🔥',
        r'response:\s*payment\s+method\s+added\s+successfully',
        r'\$[\d.]+\s+charged!',
        r'response:\s*\$[\d.]+\s+charged!',
        r'response:\s*order_placed'
    ]

    for pattern in approved_patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True

    return False

async def get_all_groups_and_channels():
    try:
        logger.info("🔍 Discovering all groups and channels...")
        groups_and_channels = []

        try:
            dialog_count = 0
            async for dialog in user.get_dialogs():
                dialog_count += 1
                chat = dialog.chat

                if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
                    if chat.id != TARGET_GROUP:
                        group_info = {
                            'id': chat.id,
                            'title': chat.title or f"Chat_{chat.id}",
                            'type': str(chat.type),
                            'username': getattr(chat, 'username', None)
                        }
                        groups_and_channels.append(group_info)
                    else:
                        logger.info(f"⚠️ Skipped target group: {chat.title} - ID: {chat.id}")

            logger.info(f"✅ Processed {dialog_count} dialogs")
            logger.info(f"✅ Discovered {len(groups_and_channels)} groups/channels for OPTIMIZED monitoring")

        except Exception as e:
            logger.error(f"❌ Error getting groups: {e}")
            return []

        return groups_and_channels

    except Exception as e:
        logger.error(f"❌ Error getting groups and channels: {e}")
        return []

async def safe_get_chat_history(chat_id, limit=20):
    try:
        await asyncio.sleep(REQUEST_DELAY)

        messages = []
        async for message in user.get_chat_history(chat_id, limit=limit):
            messages.append(message)
        return messages
    except PeerIdInvalid:
        return []
    except ChannelPrivate:
        return []
    except FloodWait as e:
        if e.value <= 60:
            logger.info(f"⏳ Flood wait {e.value}s, waiting...")
            await asyncio.sleep(e.value + 1)
            return await safe_get_chat_history(chat_id, limit)
        else:
            logger.warning(f"⏳ Very long flood wait {e.value}s, skipping")
            return []
    except Exception as e:
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
        r'(?:card|cc|𝗖𝗖|💳)\s*:?\s*(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})',
        r'(?:card|cc|𝗖𝗖|💳)\s*:?\s*(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'⌁\s*card\s*:\s*(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})',
        r'𝗖𝗖\s*[⇾:]?\s*(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})',
        r'𝐂𝐂\s*:?\s*(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})',
        r'(?:card|cc|𝗖𝗖|💳)\s*:?\s*(\d{13,19})\|(\d{1,2})\|(\d{2})\|(\d{3,4})',
        r'⌁\s*card\s*:\s*(\d{13,19})\|(\d{1,2})\|(\d{2})\|(\d{3,4})',
        r'𝗖𝗖\s*[⇾:]?\s*(\d{13,19})\|(\d{1,2})\|(\d{2})\|(\d{3,4})',
        r'𝐂𝐂\s*:?\s*(\d{13,19})\|(\d{1,2})\|(\d{2})\|(\d{3,4})',
    ]

    credit_cards = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            if len(match) == 4:
                card_number, month, year, cvv = match
                card_number = re.sub(r'[\s\-]', '', card_number)

                if (len(card_number) >= 13 and len(card_number) <= 19 and
                    1 <= int(month) <= 12 and len(cvv) >= 3):

                    if len(year) == 2 and int(year) < 50:
                        year = year
                    elif len(year) == 4:
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
                        bin_cache = dict(list(bin_cache.items())[-BIN_CACHE_SIZE//2:])
                    bin_cache[bin_number] = data
                    return data
                return None
    except Exception:
        return None

def format_card_message(cc_data, bin_info):
    scheme = "UNKNOWN"
    card_type = "UNKNOWN"
    brand = "UNKNOWN"
    bank_name = "UNKNOWN BANK"
    country_name = "UNKNOWN"
    country_emoji = "🌍"
    if bin_info:
        brand = bin_info.get('brand', 'UNKNOWN')
        scheme = brand
        card_type = bin_info.get('type', 'UNKNOWN').upper()
        bank_name = bin_info.get('bank', 'UNKNOWN BANK')
        country_name = bin_info.get('country_name', 'UNKNOWN')
        country_emoji = bin_info.get('country_flag', '🌍')
    message = f"""𝘾𝘼𝙍𝘿 ⇾ {cc_data}
𝙎𝙏𝘼𝙏𝙐𝙎 ⇾ 𝘈𝘱𝘱𝘳𝘖𝘝𝘌𝘋 💎
𝙈𝙀𝙎𝙎𝘼𝙂𝙀 ⇾ 𝘚𝘊𝘙𝘈𝘗𝘗𝘌𝘋 𝘚𝘌𝘹'𝘊𝘌𝘚𝘚𝘍𝘜𝘓𝘓𝘺
𝙂𝘼𝙏𝙀𝙒𝘼𝙔 ⇾ 𝘚𝘊𝘙𝘈𝘗𝘗𝘌𝘙 🍑
𝙄𝙉𝙁𝙊 ⇾ {scheme} - {card_type} - {brand}
𝘽𝘼𝙉𝙆 ⇾ {bank_name}
𝘾𝙊𝙐𝙉𝙏𝙍𝙔 ⇾ {country_name} {country_emoji}"""
    return message

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
        else:
            logger.warning(f"⏳ Long flood wait {e.value}s, skipping")
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
        if not text:
            return
        if not is_approved_message(text):
            return
        credit_cards = extract_credit_cards(text)
        if not credit_cards:
            return
        logger.info(f"🎯 {len(credit_cards)} CCs from {group_title[:20]}")

        tasks = []
        for cc_data in credit_cards:
            task = asyncio.create_task(process_single_cc(cc_data))
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"❌ Error processing message: {e}")

async def process_single_cc(cc_data):
    try:
        bin_number = cc_data.split('|')[0][:6]
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
        except Exception as e:
            pass

async def process_single_group(group_info):
    group_id = group_info['id']
    group_title = group_info['title']

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

            message_tasks = []
            for message in new_messages:
                task = asyncio.create_task(
                    process_message_for_approved_ccs(message, group_id, group_title)
                )
                message_tasks.append(task)

            if message_tasks:
                await asyncio.gather(*message_tasks, return_exceptions=True)

            last_processed_message_ids[group_id] = max(
                last_processed_message_ids.get(group_id, 0),
                max(msg.id for msg in new_messages)
            )

            if len(new_messages) > 3:
                logger.info(f"📨 {group_title[:25]}: {len(new_messages)} msgs")
    except Exception as e:
        pass

async def poll_multiple_groups():
    global last_processed_message_ids, is_running, source_groups
    logger.info("🔄 Starting RATE-LIMITED multi-group polling...")

    source_groups = await get_all_groups_and_channels()

    if not source_groups:
        logger.error("❌ No groups found!")
        return
    logger.info(f"📡 Monitoring {len(source_groups)} groups/channels with RATE-LIMITED SPEED (max {MAX_CONCURRENT_GROUPS} concurrent)")
    logger.info("🔍 Sequential initialization to avoid rate limits...")

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
            except Exception as e:
                logger.debug(f"Init error for {group_info.get('title', 'Unknown')}: {e}")

        completed = min(i + INIT_BATCH_SIZE, len(source_groups))
        if completed % (INIT_BATCH_SIZE * 3) == 0 or completed == len(source_groups):
            logger.info(f"🔍 Initialized {completed}/{len(source_groups)} groups")

        if i + INIT_BATCH_SIZE < len(source_groups):
            await asyncio.sleep(INIT_DELAY)
    logger.info("✅ All groups initialized. Starting polling...")

    poll_count = 0
    while is_running:
        try:
            poll_count += 1
            logger.info(f"🔄 Poll {poll_count} - Processing {len(source_groups)} groups (OPTIMIZED)...")

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
    group_id = group_info['id']
    group_title = group_info['title']

    try:
        messages = await safe_get_chat_history(group_id, limit=1)
        if messages:
            last_processed_message_ids[group_id] = messages[0].id
        else:
            last_processed_message_ids[group_id] = 0
    except Exception:
        last_processed_message_ids[group_id] = 0

async def join_target_group():
    try:
        try:
            target_chat = await user.get_chat(TARGET_GROUP)
            logger.info(f"✅ Already have access to target group: {target_chat.title}")
            return True
        except Exception:
            logger.info(f"⚠️ Need to join target group {TARGET_GROUP}")

        invite_link = "https://t.me/+TsJYw8k_KHxjZDc8"
        logger.info(f"🔗 Attempting to join target group via invite link...")

        try:
            await user.join_chat(invite_link)
            logger.info(f"✅ Successfully joined target group!")

            await asyncio.sleep(2)
            target_chat = await user.get_chat(TARGET_GROUP)
            logger.info(f"✅ Confirmed access to: {target_chat.title}")
            return True

        except UserAlreadyParticipant:
            logger.info(f"✅ Already a member of target group")
            return True
        except InviteHashExpired:
            logger.error(f"❌ Invite link has expired")
            return False
        except InviteHashInvalid:
            logger.error(f"❌ Invalid invite link")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to join target group: {e}")
            return False

    except Exception as e:
        logger.error(f"❌ Error in join_target_group: {e}")
        return False

async def test_access():
    try:
        if not await join_target_group():
            return False
        global source_groups
        source_groups = await get_all_groups_and_channels()

        if not source_groups:
            logger.error("❌ No groups found to monitor!")
            return False
        logger.info(f"✅ Found {len(source_groups)} groups/channels to monitor")

        logger.info("📋 Sample groups that will be monitored:")
        for i, group in enumerate(source_groups[:5]):
            logger.info(f"   {i+1}. {group['title']} ({group['type']}) - ID: {group['id']}")

        if len(source_groups) > 5:
            logger.info(f"   ... and {len(source_groups) - 5} more groups")

        return True
    except Exception as e:
        logger.error(f"❌ Error in test_access: {e}")
        return False

def signal_handler(signum, frame):
    global is_running
    logger.info(f"🛑 Received signal {signum}, shutting down gracefully...")
    is_running = False
    try:
        loop = asyncio.get_event_loop()
        for task in asyncio.all_tasks(loop):
            if not task.done():
                task.cancel()
    except Exception:
        pass

async def main():
    global is_running, processing_semaphore
    processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_GROUPS)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        logger.info("🔄 Starting RATE-LIMITED CC Monitor Bot...")
        await user.start()
        logger.info("✅ User client started successfully")
        try:
            me = await user.get_me()
            logger.info(f"👤 Logged in as: {me.first_name} (@{me.username}) - ID: {me.id}")
        except Exception as e:
            logger.warning(f"⚠️ Could not get user info: {e}")
        await asyncio.sleep(1)
        logger.info(f"🤖 RATE-LIMITED Multi-Group CC Monitor Active!")
        logger.info(f"📤 Target group: {TARGET_GROUP}")
        logger.info(f"🔍 Max concurrent groups: {MAX_CONCURRENT_GROUPS}")
        logger.info(f"⏱️ Poll interval: {POLL_INTERVAL}s")
        logger.info(f"📦 Message batch size: {MESSAGE_BATCH_SIZE}")
        logger.info(f"🚀 Send delay: {SEND_DELAY}s")
        logger.info(f"⏱️ Request delay: {REQUEST_DELAY}s")
        logger.info(f"📦 Init batch size: {INIT_BATCH_SIZE}")
        logger.info(f"⚡ Init delay: {INIT_DELAY}s")
        logger.info(f"🔍 Filtering for APPROVED/CHARGED messages only")
        logger.info(f"⚡ RATE-LIMITED for stability and compliance")
        access_ok = await test_access()
        if access_ok:
            logger.info("✅ All access tests passed! Starting RATE-LIMITED monitor...")

            polling_task = asyncio.create_task(poll_multiple_groups())

            try:
                logger.info("🎯 RATE-LIMITED Monitor active! Stable scanning for approved/charged CCs...")
                await idle()
            except KeyboardInterrupt:
                logger.info("🛑 Received keyboard interrupt")
            except Exception as e:
                logger.error(f"❌ Error during idle: {e}")
            finally:
                logger.info("🛑 Shutting down polling...")
                is_running = False

                if not polling_task.done():
                    polling_task.cancel()
                    try:
                        await asyncio.wait_for(polling_task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        logger.info("✅ Polling task cancelled")
                    except Exception as e:
                        logger.error(f"❌ Error cancelling polling task: {e}")
        else:
            logger.error("❌ Cannot proceed without proper access")
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
    finally:
        logger.info("🛑 Stopping client...")
        try:
            if user.is_connected:
                await user.stop(block=False)
            logger.info("✅ Client stopped cleanly")
        except Exception as e:
            logger.error(f"❌ Error stopping client: {e}")

if __name__ == "__main__":
    try:
        if sys.platform.startswith('win'):
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
