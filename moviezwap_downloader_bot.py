#!/usr/bin/env python3
"""
MoviezWap Advanced Downloader Bot
Downloads movies directly from extracted CDN links with resume & progress tracking
"""

import re, time, json, threading, sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus
from pathlib import Path

BASE    = "https://www.moviezwap.codes"
CDN_PAT = re.compile(r'https://\d+g\d+\.moviezzwaphd\.xyz[^\s"\'<>\)]+', re.I)
WORKERS = 20
LOCK    = threading.Lock()
DOWNLOAD_DIR = "downloads"

# Create downloads directory
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})
adapter = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
S.mount("https://", adapter)
S.mount("http://", adapter)

try:
    S.get(BASE + "/", timeout=15)
    S.headers.update({"Referer": BASE + "/", "Sec-Fetch-Site": "same-origin"})
except Exception:
    pass


def get_html(url: str, timeout: int = 15, referer: str = None) -> requests.Response:
    hdrs = {}
    if referer:
        hdrs["Referer"] = referer
    r = S.get(url, timeout=timeout, headers=hdrs)
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

        S.get(dw_url, timeout=14, headers={"Referer": BASE + "/"})
        r2 = S.get(dl_url, timeout=14, headers={"Referer": dw_url})
        text = r2.text
        soup2 = BeautifulSoup(text, "lxml")

        cdn = ""
        for a in soup2.find_all("a", href=True):
            if "moviezzwaphd.xyz" in a["href"]:
                cdn = a["href"]
                break

        if not cdn:
            m = re.search(
                r'(?:window\.location|location\.href)\s*=\s*["\']([^"\']+moviezzwaphd[^"\']+)["\']',
                text)
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
        return {"quality": extract_quality(label, filename),
                "filename": filename, "size": size, "cdn_url": cdn}

    except Exception:
        return None


def get_movie_cdns(movie_url: str, verbose: bool = False) -> list[dict]:
    full_url = movie_url if movie_url.startswith("http") else urljoin(BASE, movie_url)
    r = get_html(full_url, referer=BASE + "/")
    s = BeautifulSoup(r.text, "lxml")

    title_tag = s.find("h1") or s.find("h2")
    title = title_tag.get_text(strip=True) if title_tag else full_url

    dwload_links = []
    for a in s.find_all("a", href=True):
        href = a["href"]
        if "dwload.php" in href or "download.php" in href:
            full_href = urljoin(BASE, href) if not href.startswith("http") else href
            label = a.get_text(strip=True) or "Download"
            dwload_links.append((label, full_href))

    if not dwload_links:
        if verbose:
            print(f"  ⚠️  No download links found on: {full_url}")
        return []

    if verbose:
        print(f"\n  🎬 {title}")
        print(f"  Found {len(dwload_links)} download option(s) — resolving CDN links…\n")

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(resolve_cdn, href, label): label
                      for label, href in dwload_links}
        for f in as_completed(future_map):
            cdn = f.result()
            if cdn:
                results.append(cdn)

    def quality_sort_key(x):
        m = re.search(r'(\d+)p', x.get("quality", ""), re.I)
        return int(m.group(1)) if m else 0

    results.sort(key=quality_sort_key)
    return results


def search_movies(query: str) -> list[dict]:
    search_url = f"{BASE}/search.php?q={quote_plus(query)}"
    r = get_html(search_url, referer=BASE + "/")
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

    url_map = {}
    for item in results:
        url = item["url"]
        if url not in url_map or len(item["title"]) > len(url_map[url]["title"]):
            url_map[url] = item

    final = list(url_map.values())
    words = query.lower().split()
    filtered = [r for r in final
                if any(w in r["title"].lower() or w in r["url"].lower() for w in words)]

    return (filtered if filtered else final)[:20]


def download_file(url: str, filepath: str, chunk_size: int = 8192) -> bool:
    """Download file with resume support"""
    try:
        # Check if file exists and resume
        resume_header = {}
        if os.path.exists(filepath):
            resume_header = {"Range": f"bytes={os.path.getsize(filepath)}-"}
        
        r = S.get(url, headers=resume_header, stream=True, timeout=30)
        
        if r.status_code == 416:  # Range not valid
            os.remove(filepath)
            r = S.get(url, stream=True, timeout=30)
        
        total_size = int(r.headers.get('content-length', 0))
        
        with open(filepath, 'ab') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True, desc=os.path.basename(filepath)) as pbar:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        
        return True
    except Exception as e:
        print(f"  ❌ Download failed: {e}")
        return False


def print_cdn_results(results: list[dict]):
    if not results:
        print("  ❌ Could not resolve any CDN links.")
        return
    print(f"\n  {'QUALITY':<10} {'SIZE':<10} FILENAME")
    print("  " + "─" * 75)
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['quality']:<8} {r['size']:<10} {r['filename']}")
    print()


def download_movie_interactive(cdns: list[dict], title: str):
    """Interactive download selection"""
    if not cdns:
        print("  ❌ No CDN links available for download.")
        return
    
    print_cdn_results(cdns)
    print("  Pick quality to download (number):")
    choice = input("  Enter choice (1-{}): ".format(len(cdns))).strip()
    
    if not choice.isdigit() or not (1 <= int(choice) <= len(cdns)):
        print("  ❌ Invalid choice")
        return
    
    selected = cdns[int(choice) - 1]
    
    # Create filename
    safe_title = re.sub(r'[^\w\s-]', '', title)[:40].strip()
    filename = f"{safe_title}_{selected['quality']}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    print(f"\n  📥 Downloading: {selected['filename']}")
    print(f"  Quality: {selected['quality']} | Size: {selected['size']}")
    print(f"  Saving to: {filepath}\n")
    
    if download_file(selected['cdn_url'], filepath):
        print(f"\n  ✅ Download complete! Saved to {filepath}")
    else:
        print(f"  ❌ Download failed. Check your internet connection.")


def option1_search_download():
    print("\n" + "─" * 60)
    query = input("  🔍 Enter movie name to search: ").strip()
    if not query:
        return

    print(f"  Searching for '{query}'…")
    results = search_movies(query)

    if not results:
        print("  ❌ No results found. Try a different name.")
        return

    lang_order = []
    lang_map = {}
    idx = 1
    for r in results:
        title = r["title"]
        lang = "Other"
        for candidate in ["Telugu", "Tamil", "Hindi", "Malayalam", "Kannada",
                          "Bengali", "Marathi", "Hollywood", "Japanese",
                          "Filipino", "Korean", "Chinese"]:
            if candidate.lower() in title.lower():
                lang = candidate
                break
        if lang not in lang_map:
            lang_map[lang] = []
            lang_order.append(lang)
        lang_map[lang].append((idx, r))
        idx += 1

    print(f"\n  Found {len(results)} result(s):\n")
    for lang in lang_order:
        print(f"  -- {lang} " + "-" * 40)
        for num, r in lang_map[lang]:
            print(f"    [ {num}] {r['title']}")
        print()

    pick = input("  Pick number: ").strip()
    if not pick.isdigit():
        print("  Invalid input.")
        return

    pick_int = int(pick)
    chosen = None
    for lang in lang_order:
        for num, r in lang_map[lang]:
            if num == pick_int:
                chosen = r
                break
        if chosen:
            break

    if not chosen:
        print("  Invalid number.")
        return

    print(f"\n  ⏳ Getting download links for: {chosen['title']}")
    cdns = get_movie_cdns(chosen['url'], verbose=False)
    
    if cdns:
        print_cdn_results(cdns)
        download_movie_interactive(cdns, chosen['title'])
    else:
        print("  ❌ Could not extract any download links.")


def option2_paste_url_download():
    print("\n" + "─" * 60)
    url = input("  🔗 Paste movie page URL: ").strip()
    if not url:
        return

    if not url.startswith("http"):
        url = urljoin(BASE, url)

    print(f"\n  ⏳ Fetching CDN links from: {url}")
    cdns = get_movie_cdns(url, verbose=True)
    
    if cdns:
        print_cdn_results(cdns)
        # Extract title from URL
        title = url.rstrip("/").split("/")[-1].replace(".html", "").replace("-", " ").title()
        download_movie_interactive(cdns, title)
    else:
        print("  ❌ Could not extract any download links.")


def option3_download_latest():
    print("\n" + "─" * 60)
    print("  ⏳ Fetching latest 10 movies from homepage…\n")

    r = get_html(BASE + "/")
    s = BeautifulSoup(r.text, "lxml")

    seen = set()
    latest = []
    for a in s.find_all("a", href=True):
        href = a["href"]
        if "/movie/" not in href or href in seen:
            continue
        seen.add(href)
        title_text = re.sub(r'^[»\s]+', '', a.get_text(strip=True)).strip()
        title_text = re.sub(r'\s*[-–]\s*\[.*?\]', '', title_text).strip()
        if title_text and len(title_text) > 3:
            latest.append({"title": title_text, "url": urljoin(BASE, href)})
        if len(latest) == 10:
            break

    if not latest:
        print("  ❌ Could not fetch latest movies.")
        return

    print(f"  Resolving CDN links for all 10…\n")

    results = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        future_map = {pool.submit(get_movie_cdns, m["url"]): m for m in latest}
        with tqdm(total=len(latest), unit="movie", ncols=60) as pbar:
            for f in as_completed(future_map):
                m = future_map[f]
                cdns = f.result()
                results[m["url"]] = cdns
                pbar.update(1)

    print()
    print("=" * 68)
    for i, m in enumerate(latest, 1):
        cdns = results.get(m["url"], [])
        print(f"\n  {i}. 🎬 {m['title']}")
        if cdns:
            for j, c in enumerate(cdns, 1):
                print(f"     {j}. [{c['quality']}] ({c['size']})")
        else:
            print("     ❌ No CDN links resolved")
    print()

    pick = input("  Pick movie number (1-10): ").strip()
    if pick.isdigit() and 1 <= int(pick) <= len(latest):
        m = latest[int(pick) - 1]
        cdns = results.get(m["url"], [])
        if cdns:
            download_movie_interactive(cdns, m["title"])
        else:
            print("  ❌ No links available for this movie.")


def option4_batch_download():
    """Download multiple movies from a list"""
    print("\n" + "─" * 60)
    print("  📋 Batch Download Mode")
    print("  Enter movie names (one per line, empty line to finish):")
    
    movies = []
    while True:
        name = input("  > ").strip()
        if not name:
            break
        movies.append(name)
    
    if not movies:
        return
    
    print(f"\n  Found {len(movies)} movie(s) to search…\n")
    
    for movie_name in movies:
        print(f"  🔍 Searching: {movie_name}")
        results = search_movies(movie_name)
        
        if not results:
            print(f"    ❌ Not found\n")
            continue
        
        # Get first result
        chosen = results[0]
        print(f"    ✅ Found: {chosen['title']}")
        
        cdns = get_movie_cdns(chosen['url'], verbose=False)
        if cdns:
            # Download best quality
            best = cdns[-1]  # Last one is highest quality
            safe_title = re.sub(r'[^\w\s-]', '', chosen['title'])[:40].strip()
            filename = f"{safe_title}_{best['quality']}.mp4"
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            
            print(f"    ⬇️  Downloading {best['quality']}...")
            if download_file(best['cdn_url'], filepath):
                print(f"    ✅ Done!\n")
            else:
                print(f"    ❌ Failed\n")
        else:
            print(f"    ❌ No CDN links found\n")


def main():
    print()
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║    🎬 MoviezWap Advanced Downloader Bot                   ║")
    print("╠═══════════════════════════════════════════════════════════╣")
    print("║  [1]  🔍 Search & Download                               ║")
    print("║  [2]  🔗 Paste URL & Download                            ║")
    print("║  [3]  ⭐ Latest 10 Movies                                 ║")
    print("║  [4]  📋 Batch Download (multiple movies)                ║")
    print("║  [5]  📁 Open Downloads Folder                           ║")
    print("║  [0]  Exit                                               ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    while True:
        choice = input("  Choose option (0-5): ").strip()
        if choice == "1":
            option1_search_download()
        elif choice == "2":
            option2_paste_url_download()
        elif choice == "3":
            option3_download_latest()
        elif choice == "4":
            option4_batch_download()
        elif choice == "5":
            abs_path = os.path.abspath(DOWNLOAD_DIR)
            print(f"\n  📂 Downloads folder: {abs_path}")
            if os.path.exists(abs_path):
                files = os.listdir(abs_path)
                if files:
                    print(f"  Files ({len(files)}):")
                    for f in sorted(files):
                        size = os.path.getsize(os.path.join(abs_path, f))
                        print(f"    • {f} ({size / (1024*1024):.2f} MB)")
                else:
                    print("  No files downloaded yet.")
            print()
        elif choice == "0":
            print("\n  👋 later bro 👋\n")
            sys.exit(0)
        else:
            print("  ❌ Invalid. Enter 0-5.")

        print()
        again = input("  Back to menu? (y/n): ").strip().lower()
        if again != "y":
            print("\n  👋 later bro 👋\n")
            break
        print()
        print("╔═══════════════════════════════════════════════════════╗")
        print("║  [1] Search  [2] URL  [3] Latest  [4] Batch  [0] Exit ║")
        print("╚═══════════════════════════════════════════════════════╝")
        print()


if __name__ == "__main__":
    main()
