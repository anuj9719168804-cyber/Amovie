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
BASE = os.getenv("MOVIEZWAP_BASE", "https://www.moviezwap.codes").rstrip("/")
# Comma-separated backup domains, tried when the main one keeps answering 403/429/503
MIRRORS = [m.strip().rstrip("/") for m in os.getenv("MOVIEZWAP_MIRRORS", "").split(",") if m.strip()]
# Optional proxy (http://user:pass@host:port). Needed if the site blocks your server's IP.
PROXY_URL = os.getenv("PROXY_URL", "").strip()
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

# Transport. The site blocks Python's default TLS fingerprint (that is a 403 even with perfect
# headers), so curl_cffi is used when installed: it talks TLS exactly like real Chrome.
# Without it the bot falls back to plain `requests` + browser headers (works on some hosts only).
try:
    from curl_cffi import requests as cffi_requests
    _NET_ERRORS = (requests.RequestException, cffi_requests.exceptions.RequestException)
except ImportError:  # pragma: no cover
    cffi_requests = None
    _NET_ERRORS = (requests.RequestException,)

_IMPERSONATE = ["chrome", "safari"]  # curl_cffi browser profiles, rotated after a block
_USER_AGENTS = [  # only used by the plain-requests fallback
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
_tls = threading.local()  # one session per thread: the CDN resolver runs in a thread pool


def _browser_headers(ua: str) -> dict:
    # No "br" in Accept-Encoding: requests can't decode Brotli unless the brotli package is installed.
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _new_session(n: int = 0):
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
    if cffi_requests is not None:
        # no custom User-Agent here: it must match the impersonated browser's TLS fingerprint
        return cffi_requests.Session(impersonate=_IMPERSONATE[n % len(_IMPERSONATE)], proxies=proxies)
    sess_ = requests.Session()
    sess_.headers.update(_browser_headers(_USER_AGENTS[n % len(_USER_AGENTS)]))
    if proxies:
        sess_.proxies.update(proxies)
    adapter = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
    sess_.mount("https://", adapter)
    sess_.mount("http://", adapter)
    return sess_


def _warm_up(session, base: str) -> None:
    """Visit the home page first so the site hands out its cookies, like a real browser."""
    try:
        session.get(base + "/", timeout=15)
    except _NET_ERRORS:
        pass
    session.headers["Referer"] = base + "/"


def sess():
    """This thread's session (created and warmed up on first use)."""
    session = getattr(_tls, "session", None)
    if session is None:
        session = _tls.session = _new_session(0)
        _warm_up(session, BASE)
    return session


def _rotate_identity(base: str) -> None:
    """After a block: brand-new session (other browser profile, no cookies), warmed up again."""
    _tls.n = getattr(_tls, "n", 0) + 1
    _tls.session = _new_session(_tls.n)
    _warm_up(_tls.session, base)


_BLOCK_CODES = (403, 429, 503)


def get_html(url: str, timeout: int = 15) -> requests.Response:
    """GET with retries. On 403/429/503 it changes identity and retries, then tries MIRRORS."""
    global BASE
    suffix = url[len(BASE):] if url.startswith(BASE) else None
    bases = ([BASE] + [m for m in MIRRORS if m != BASE]) if suffix is not None else [None]
    last_error: Exception = RuntimeError("no request was made")

    for base in bases:
        target = url if base is None else base + suffix
        for attempt in range(3):
            try:
                r = sess().get(target, timeout=timeout)
            except _NET_ERRORS as e:
                last_error = e
                time.sleep(1 + attempt)
                continue
            if r.status_code < 400:
                if base is not None and base != BASE:
                    logger.warning(f"Main domain is blocked, switching to {base}")
                    BASE = base
                return r
            hint = " (Cloudflare)" if "cloudflare" in r.headers.get("server", "").lower() else ""
            last_error = requests.HTTPError(f"{r.status_code} for {target}{hint}", response=r)
            if r.status_code not in _BLOCK_CODES:
                raise last_error  # 404 and friends: retries and mirrors won't help
            logger.warning(f"{r.status_code}{hint} from {target}, changing identity (try {attempt + 1}/3)")
            _rotate_identity(base or BASE)
            time.sleep(1.5 * (attempt + 1))
    raise last_error


def clean_query(text: str) -> str:
    """'/search@bot pushpa' or '@some_bot pushpa' -> 'pushpa' (the site must only see the movie name)."""
    text = re.sub(r"^/\w+(@\w+)?\s*", "", text.strip())
    text = re.sub(r"@\w+", " ", text)
    return " ".join(text.split())


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

        sess().get(dw_url, timeout=14)
        r2 = sess().get(dl_url, timeout=14)
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


def _site_search(query: str) -> list[dict]:
    """The site's own search.php. Raises when the site blocks the request."""
    r = get_html(f"{BASE}/search.php?q={quote_plus(query)}")
    s = BeautifulSoup(r.text, "lxml")
    seen = set()
    results = []
    for a in s.find_all("a", href=True):
        href = a["href"]
        if "/movie/" not in href or "/movie//" in href or href in seen:
            continue
        seen.add(href)
        title_text = re.sub(r'^[»\s]+', '', a.get_text(strip=True)).strip()
        if title_text and len(title_text) > 3:
            results.append({"title": title_text, "url": urljoin(BASE, href)})
    return results[:15]


# ── Own movie index ───────────────────────────────────────────────────────────
# search.php sits behind bot protection, but the category pages (and the movie pages) do not.
# When the site search is blocked, the bot crawls the category listings once, keeps every
# movie title + link, and searches that list itself. The crawl only starts when the site search
# fails, so a working setup never pays for it. The index is cached in CATALOG_FILE.

CATALOG_FILE = Path(os.getenv("CATALOG_FILE", "logs/catalog.json"))
CATALOG_TTL = int(os.getenv("CATALOG_TTL_HOURS", 6)) * 3600
CATALOG_MAX_PAGES = int(os.getenv("CATALOG_MAX_PAGES", 25))   # pages crawled per category
CATALOG_WORKERS = 6

_catalog = {"items": {}, "built": 0.0, "building": False}
_catalog_lock = threading.Lock()


def _movie_links(soup) -> list[tuple[str, str]]:
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/movie/" not in href or "/movie//" in href:  # the site has a few broken '/movie//movie/' links
            continue
        title = re.sub(r'^[»\s]+', '', a.get_text(strip=True)).strip()
        if len(title) > 3:
            found.append((title, urljoin(BASE, href)))
    return found


def _crawl_category(cat_url: str) -> None:
    """Add every movie of one category (all its pages, up to CATALOG_MAX_PAGES) to the index."""
    base_no_ext = cat_url[:-5] if cat_url.endswith(".html") else cat_url
    pages, page, failures = 1, 1, 0
    while page <= min(pages, CATALOG_MAX_PAGES):
        url = cat_url if page == 1 else f"{base_no_ext}/{page}.html"
        try:
            html = get_html(url).text
        except Exception as e:
            failures += 1
            logger.warning(f"Catalog: {url} failed ({e})")
            if failures >= 3:
                return
            page += 1
            continue
        if page == 1:
            m = re.search(r'Page\s*1\s*of\s*(\d+)', html)
            pages = int(m.group(1)) if m else 1
        with _catalog_lock:
            for title, link in _movie_links(BeautifulSoup(html, "lxml")):
                _catalog["items"][link] = title
        page += 1
        time.sleep(0.2)


def _build_catalog() -> None:
    try:
        home = BeautifulSoup(get_html(BASE + "/").text, "lxml")
        cats = []
        for a in home.find_all("a", href=True):
            href = urljoin(BASE, a["href"])
            if "/category/" in href and href not in cats:
                cats.append(href)
        logger.info(f"Catalog: crawling {len(cats)} categories")
        with ThreadPoolExecutor(max_workers=CATALOG_WORKERS) as pool:
            list(pool.map(_crawl_category, cats))
        with _catalog_lock:
            _catalog["built"] = time.time()
            snapshot = dict(_catalog["items"])
        try:
            CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CATALOG_FILE.write_text(json.dumps({"built": _catalog["built"], "items": snapshot}), encoding="utf-8")
        except OSError as e:
            logger.warning(f"Catalog: could not save {CATALOG_FILE}: {e}")
        logger.info(f"Catalog: ready, {len(snapshot)} movies")
    except Exception as e:
        logger.error(f"Catalog build failed: {e}")
    finally:
        _catalog["building"] = False


def _ensure_catalog() -> None:
    """Load the cached index, or start building a new one in the background if it is missing/old."""
    with _catalog_lock:
        if not _catalog["items"] and CATALOG_FILE.exists():
            try:
                data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
                _catalog["items"], _catalog["built"] = data["items"], float(data["built"])
            except (OSError, ValueError, KeyError):
                pass
        stale = time.time() - _catalog["built"] > CATALOG_TTL
        if stale and not _catalog["building"]:
            _catalog["building"] = True
            threading.Thread(target=_build_catalog, daemon=True, name="catalog").start()


def catalog_building() -> bool:
    return bool(_catalog["building"]) and not _catalog["items"]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _catalog_search(query: str) -> list[dict]:
    _ensure_catalog()
    words = _tokens(query)
    if not words:
        return []
    with _catalog_lock:
        items = list(_catalog["items"].items())
    hits = []
    for link, title in items:
        have = _tokens(title)
        # every query word must start some word of the title ("pushp" finds "Pushpa")
        if all(any(t.startswith(w) for t in have) for w in words):
            hits.append({"title": title, "url": link})
    hits.sort(key=lambda h: len(h["title"]))  # closest (shortest) titles first
    return hits[:15]


def search_movies(query: str) -> list[dict]:
    query = clean_query(query)
    if not query:
        return []
    try:
        return _site_search(query)
    except Exception as e:
        logger.error(f"Search error: {e}")
    logger.info("Site search failed, using the bot's own movie index")
    return _catalog_search(query)


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
    
    query = clean_query(message.text)  # also handles "/search@bot name" in groups
    if not query:
        await message.reply("❌ Please provide a movie name\n<code>/search Pushpa</code>", parse_mode=ParseMode.HTML)
        return
    
    msg = await message.reply("🔍 Searching...")
    
    results = await asyncio.to_thread(search_movies, query)
    
    if not results:
        if catalog_building():
            await msg.edit_text("⏳ The site's search is blocked, so I'm building my own movie list. Try again in 1-2 minutes.")
        else:
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
        cdns = await asyncio.to_thread(get_movie_cdns, text)
        if cdns:
            buttons = [[InlineKeyboardButton(f"{c['quality']} ({c['size']})", callback_data=f"dl_{hash(c['cdn_url']) % 10000}")] for c in cdns]
            user_data[user_id]["last_cdns"] = cdns
            await msg.edit_text("<b>🎬 Select Quality:</b>", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await msg.edit_text("❌ Could not fetch download links")
    else:
        # Search for movie
        results = await asyncio.to_thread(search_movies, text)
        
        if not results:
            if catalog_building():
                await msg.edit_text("⏳ The site's search is blocked, so I'm building my own movie list. Try again in 1-2 minutes.")
            else:
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
            
            cdns = await asyncio.to_thread(get_movie_cdns, movie["url"])
            
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
