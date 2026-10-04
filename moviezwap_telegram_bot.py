"""
MoviezWap Telegram Downloader Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Send a movie name  →  pick a quality  →  get the file in chat.
Built with Pyrogram + MoviezWap scraper. Force-subscribe, admin panel, referral system.
"""

import asyncio
import logging
import os
import re
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, quote_plus

from dotenv import load_dotenv
from pyrogram import Client, filters, StopPropagation
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from pyrogram.enums import ParseMode, ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChannelInvalid, PeerIdInvalid, BotMethodInvalid, RPCError
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

load_dotenv()

# Bot config
API_ID = int(os.getenv("API_ID", 20432885))
API_HASH = os.getenv("API_HASH", "4fdcfab1c7f5e24ae69f3ce6bb234dec")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8372384598:AAHzv7NS1j2bsZ3yOlxy65NkbiRIpu5Th0k")
OWNER_ID = int(os.getenv("OWNER_ID", 8729304171))

# Force subscribe channels (space-separated)
def _normalize_channel(value: str):
    value = value.strip()
    # Telegram supergroup/channel IDs must be passed as -100xxxxxxxxxx.
    # Also accept a mistakenly configured positive 100xxxxxxxxxx ID.
    if re.fullmatch(r"-100\d+", value):
        return int(value)
    if re.fullmatch(r"100\d+", value):
        return int("-" + value)
    return value

FORCE_SUB_CHANNELS = [
    _normalize_channel(x)
    for x in os.getenv("FORCE_SUB_CHANNELS", "").split()
    if x.strip()
]
# Log channel
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", -1004396123873)) if os.getenv("LOG_CHANNEL") else None

# Limits
MAX_PARALLEL_DOWNLOADS = int(os.getenv("MAX_PARALLEL_DOWNLOADS", 5))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2000))  # MB

# MovieZWap config
BASE = "https://www.moviezwap.codes"
CDN_PAT = re.compile(r'https://\d+g\d+\.moviezzwaphd\.xyz[^\s"\'<>\)]+', re.I)
WORKERS = 20

# Create directories
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)

# Logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("moviezwap_bot")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SESSION & DOWNLOADER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})
adapter = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
S.mount("https://", adapter)
S.mount("http://", adapter)

try:
    S.get(BASE + "/", timeout=15)
except:
    pass


def get_html(url: str, timeout: int = 15) -> requests.Response:
    r = S.get(url, timeout=timeout)
    r.raise_for_status()
    return r


def extract_quality(text: str, filename: str = "") -> str:
    for src in (text, filename):
        m = re.search(r'(2160p|1080p|720p|480p|360p|320p|240p)', src, re.I)
        if m:
            return m.group(1).upper()
    m = re.search(r'\b(HD|SD|HQ|4K|FHD)\b', text, re.I)
    return m.group(1).upper() if m else "?"


def _size_from_text(text: str) -> str:
    m = re.search(r'(\d+(?:\.\d+)?)\s*(MB|GB)', text, re.I)
    return f"{m.group(1)} {m.group(2).upper()}" if m else "?"


def resolve_cdn(dwload_url: str, label: str = "") -> dict | None:
    try:
        file_id = re.search(r'file=([^&]+)', dwload_url)
        file_id = file_id.group(1) if file_id else dwload_url.split("file=")[-1]

        dw_url = f"{BASE}/dwload.php?file={file_id}"
        dl_url = f"{BASE}/download.php?file={file_id}"

        S.get(dw_url, timeout=14)
        r2 = S.get(dl_url, timeout=14)
        text = r2.text
        soup2 = BeautifulSoup(text, "lxml")

        cdn = ""
        for a in soup2.find_all("a", href=True):
            if "moviezzwaphd.xyz" in a["href"]:
                cdn = a["href"]
                break

        if not cdn:
            m = re.search(r'(?:window\.location|location\.href)\s*=\s*["\']([^"\']+moviezzwaphd[^"\']+)["\']', text)
            if m:
                cdn = m.group(1)

        if not cdn:
            m = CDN_PAT.search(text)
            if m:
                cdn = m.group(0).rstrip("\\")

        if not cdn:
            return None

        filename = cdn.split("?")[0].rstrip("/").split("/")[-1]
        size = _size_from_text(text)
        return {
            "quality": extract_quality(label, filename),
            "filename": filename,
            "size": size,
            "cdn_url": cdn
        }
    except:
        return None


def get_movie_cdns(movie_url: str) -> list[dict]:
    try:
        full_url = movie_url if movie_url.startswith("http") else urljoin(BASE, movie_url)
        r = get_html(full_url)
        s = BeautifulSoup(r.text, "lxml")

        dwload_links = []
        for a in s.find_all("a", href=True):
            href = a["href"]
            if "dwload.php" in href or "download.php" in href:
                full_href = urljoin(BASE, href) if not href.startswith("http") else href
                label = a.get_text(strip=True) or "Download"
                dwload_links.append((label, full_href))

        if not dwload_links:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(resolve_cdn, href, label): label for label, href in dwload_links}
            for f in futures:
                cdn = f.result()
                if cdn:
                    results.append(cdn)

        results.sort(key=lambda x: int(re.search(r'(\d+)p', x.get("quality", ""), re.I).group(1) or 0) if re.search(r'(\d+)p', x.get("quality", ""), re.I) else 0)
        return results
    except Exception as e:
        logger.error(f"Error getting CDNs: {e}")
        return []


def search_movies(query: str) -> list[dict]:
    try:
        search_url = f"{BASE}/search.php?q={quote_plus(query)}"
        r = get_html(search_url)
        s = BeautifulSoup(r.text, "lxml")

        seen = set()
        results = []
        for a in s.find_all("a", href=True):
            href = a["href"]
            if "/movie/" not in href or href in seen:
                continue
            seen.add(href)
            title_text = re.sub(r'^[»\s]+', '', a.get_text(strip=True)).strip()
            if title_text and len(title_text) > 3:
                results.append({"title": title_text, "url": urljoin(BASE, href)})

        return results[:15]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if not all([API_ID, API_HASH, BOT_TOKEN]):
    logger.error("Missing required credentials in .env file")
    exit(1)

app = Client(
    "moviezwap_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=Path.cwd(),
)

# User data storage (in-memory for now, use MongoDB for production)
user_data = {}
download_queue = {}


async def check_force_sub(user_id: int) -> bool:
    """Check if user has joined all force-subscribe channels"""
    if not FORCE_SUB_CHANNELS:
        return True
    
    for channel in FORCE_SUB_CHANNELS:
        try:
            member = await app.get_chat_member(channel, user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                return False
        except (UserNotParticipant, ChannelInvalid, PeerIdInvalid):
            return False
        except BotMethodInvalid as e:
            logger.error(
                "Force-sub channel %r is invalid for bot access. "
                "Use @channelusername or the correct -100xxxxxxxxxx ID. Error: %s",
                channel,
                e,
            )
            return False
        except RPCError as e:
            logger.warning(f"Force-sub check failed for {channel!r}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected force-sub check error for {channel!r}: {e}")
            return False
    
    return True


async def log_message(text: str):
    """Send message to log channel"""
    if LOG_CHANNEL:
        try:
            await app.send_message(LOG_CHANNEL, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Log error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    # Check force subscribe
    if not await check_force_sub(user_id):
        buttons = []
        for channel in FORCE_SUB_CHANNELS:
            if isinstance(channel, str) and channel.startswith("@"): 
                buttons.append([InlineKeyboardButton("🔗 Join Channel", url=f"https://t.me/{channel[1:]}")])
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
        
        await message.reply(
            f"<b>👋 Welcome {user_name}!</b>\n\n"
            "<b>Please join our channels first:</b>",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return
    
    # Save user
    user_data[user_id] = {
        "name": user_name,
        "joined_at": datetime.now().isoformat(),
        "downloads": 0
    }
    
    await log_message(f"<b>New user:</b> {user_name} (ID: {user_id})")
    
    buttons = [[
        InlineKeyboardButton("🔍 Search Movie", switch_inline_query_current_chat=""),
        InlineKeyboardButton("❓ Help", callback_data="help"),
    ]]
    
    await message.reply(
        f"<b>🎬 Welcome to MoviezWap Bot!</b>\n\n"
        f"<b>Send movie name or link:</b>\n"
        f"• <code>Pushpa</code> (search)\n"
        f"• <code>movie URL</code> (direct link)\n\n"
        f"<b>Commands:</b>\n"
        f"/search &lt;name&gt; - Search movie\n"
        f"/help - Show help\n"
        f"/stats - Your stats\n"
        f"/cancel - Cancel downloads",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not await check_force_sub(user_id):
        await message.reply("❌ Please join channels first using /start")
        return
    
    await message.reply(
        "<b>📖 How to use:</b>\n\n"
        "<b>1️⃣ Search:</b>\n"
        "Just type movie name:\n"
        "<code>Pushpa</code>\n\n"
        "<b>2️⃣ Pick quality:</b>\n"
        "Click on the quality button\n"
        "Options: 320p, 480p, 720p\n\n"
        "<b>3️⃣ Download:</b>\n"
        "File will be sent to chat\n"
        "See progress in real-time\n\n"
        "<b>4️⃣ Commands:</b>\n"
        "/search &lt;name&gt; - Advanced search\n"
        "/cancel - Stop all downloads\n"
        "/stats - View your stats\n"
        "/plans - Premium plans\n\n"
        "<b>ℹ️ Info:</b>\n"
        "Max file size: 2GB\n"
        "Languages: Hindi, Telugu, Tamil...\n"
        "Quality: 320p → 720p",
        parse_mode=ParseMode.HTML
    )


@app.on_message(filters.command("search") & filters.text)
async def search_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not await check_force_sub(user_id):
        await message.reply("❌ Please join channels first using /start")
        return
    
    query = message.text.replace("/search ", "", 1).strip()
    if not query:
        await message.reply("❌ Please provide a movie name\n<code>/search Pushpa</code>", parse_mode=ParseMode.HTML)
        return
    
    msg = await message.reply("🔍 Searching...")
    
    results = search_movies(query)
    
    if not results:
        await msg.edit_text("❌ No movies found. Try another name.")
        return
    
    # Build buttons
    buttons = []
    for i, r in enumerate(results[:10], 1):
        title = r["title"][:50]
        buttons.append([InlineKeyboardButton(f"{i}. {title}", callback_data=f"select_{i}_{hash(r['url']) % 10000}")])
    
    # Store search results
    user_data[user_id]["last_search"] = results
    
    await msg.edit_text(
        f"<b>🎬 Found {len(results)} results:</b>\n\n"
        "Click to select:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_message(filters.command("cancel"))
async def cancel_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in download_queue:
        download_queue[user_id].clear()
        await message.reply("✅ All downloads cancelled")
    else:
        await message.reply("❌ No active downloads")


@app.on_message(filters.command("stats"))
async def stats_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not await check_force_sub(user_id):
        await message.reply("❌ Please join channels first using /start")
        return
    
    user = user_data.get(user_id, {})
    downloads = user.get("downloads", 0)
    joined_at = user.get("joined_at", "Unknown")
    
    await message.reply(
        f"<b>📊 Your Stats:</b>\n\n"
        f"<b>ID:</b> {user_id}\n"
        f"<b>Downloads:</b> {downloads}\n"
        f"<b>Joined:</b> {joined_at[:10]}\n"
        f"<b>Status:</b> Free User",
        parse_mode=ParseMode.HTML
    )


@app.on_message(filters.text & ~filters.regex(r"^/"))
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if not await check_force_sub(user_id):
        await message.reply("❌ Please join channels first using /start")
        return
    
    msg = await message.reply("🔍 Processing...")
    
    # Check if it's a URL
    if text.startswith("http"):
        cdns = get_movie_cdns(text)
        if cdns:
            buttons = [[InlineKeyboardButton(f"{c['quality']} ({c['size']})", callback_data=f"dl_{hash(c['cdn_url']) % 10000}")] for c in cdns]
            user_data[user_id]["last_cdns"] = cdns
            await msg.edit_text("<b>🎬 Select Quality:</b>", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await msg.edit_text("❌ Could not fetch download links")
    else:
        # Search for movie
        results = search_movies(text)
        
        if not results:
            await msg.edit_text("❌ No movies found")
            return
        
        buttons = [[InlineKeyboardButton(f"{r['title'][:40]}", callback_data=f"pick_{i}")] for i, r in enumerate(results[:8])]
        user_data[user_id]["last_search"] = results
        
        await msg.edit_text(
            "<b>🎬 Select Movie:</b>",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


@app.on_callback_query()
async def callback_handler(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data
    
    if data == "verify":
        if await check_force_sub(user_id):
            await query.answer("✅ Verified!", show_alert=True)
            await query.message.delete()
        else:
            await query.answer("❌ You haven't joined all channels", show_alert=True)
    
    elif data == "help":
        await query.message.edit_text(
            "<b>📖 Help & Features:</b>\n\n"
            "🎬 <b>Search:</b> Type movie name\n"
            "📥 <b>Download:</b> Pick quality\n"
            "⚡ <b>Fast:</b> Direct CDN links\n"
            "🎞️ <b>Quality:</b> 320p - 720p\n"
            "📊 <b>Progress:</b> Real-time\n\n"
            "Type movie name to start!",
            parse_mode=ParseMode.HTML
        )
        await query.answer()
    
    elif data.startswith("pick_"):
        idx = int(data.split("_")[1])
        results = user_data.get(user_id, {}).get("last_search", [])
        
        if idx < len(results):
            movie = results[idx]
            msg = await query.message.edit_text("⏳ Getting download links...")
            
            cdns = get_movie_cdns(movie["url"])
            
            if cdns:
                buttons = [[InlineKeyboardButton(f"{c['quality']} ({c['size']})", callback_data=f"dl_{idx}_{i}")] for i, c in enumerate(cdns)]
                user_data[user_id]["last_movie"] = movie
                user_data[user_id]["last_cdns"] = cdns
                
                await msg.edit_text(
                    f"<b>🎬 {movie['title']}</b>\n\n"
                    "<b>Select Quality:</b>",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.HTML
                )
            else:
                await msg.edit_text("❌ Could not get download links")
    
    await query.answer()


# Render Web Service health/port server.
PORT = int(os.getenv("PORT", "10000"))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","service":"moviezwap-telegram-bot"}')

    def log_message(self, format, *args):
        return


def start_health_server():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"Health server listening on 0.0.0.0:{PORT}")
        return server
    except Exception as e:
        logger.exception(f"Health server failed to start: {e}")
        return None


async def main():
    logger.info("🚀 MoviezWap Telegram Bot starting...")
    health_server = start_health_server()

    await app.start()
    logger.info("✅ Bot is running")

    try:
        await log_message("<b>✅ MoviezWap Bot started</b>")
    except Exception as e:
        logger.warning(f"Startup log failed: {e}")

    # Pyrogram 2.2.26 has no Client.idle().
    # Keep the same event loop alive until Render stops the process.
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        if app.is_connected:
            await app.stop()
        if health_server:
            health_server.shutdown()
            health_server.server_close()


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
    except Exception:
        logger.exception("Fatal bot error")
        raise
