"""
media_scraper.py - find every image, video, audio file and PDF on a website,
list them, and (optionally, now or later) download them.

Quick start
    pip install requests beautifulsoup4

    python media_scraper.py  (this launches an interactive wizard)
    python media_scraper.py scan https://example.com
    python media_scraper.py list
    python media_scraper.py download --types image,pdf
    python media_scraper.py download --select 1,4,10-20

`scan` saves its results to media.json, so you can run `download` any time
later without crawling again.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "media-scraper/1.0 (+you@example.com)"  #contact info
DEFAULT_JSON = "media.json"

TYPE_ORDER = ["image", "video", "audio", "pdf"]
EXTENSIONS = {
    "image": ".jpg .jpeg .png .gif .webp .svg .bmp .ico .avif .tif .tiff .heic",
    "video": ".mp4 .webm .ogv .mov .mkv .avi .m4v .flv .wmv .m3u8",
    "audio": ".mp3 .wav .ogg .oga .m4a .aac .flac .opus .wma",
    "pdf": ".pdf",
}
EXT_TYPES = {e: t for t, exts in EXTENSIONS.items() for e in exts.split()}

META_KEYS = {
    "og:image": "image", "og:image:url": "image", "og:image:secure_url": "image",
    "twitter:image": "image", "og:video": "video", "og:video:url": "video",
    "og:video:secure_url": "video", "og:audio": "audio", "og:audio:url": "audio",
    "og:audio:secure_url": "audio",
}
LAZY_ATTRS = ["src", "data-src", "data-original", "data-lazy-src", "data-lazy"]
SRCSET_ATTRS = ["srcset", "data-srcset", "data-lazy-srcset"]
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", re.I)


def normalize(url):
    return urldefrag(url)[0]


def ext_of(url):
    return os.path.splitext(urlparse(url).path)[1].lower()


def site_key(url):
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def human(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def load_robots(start_url):
    p = urlparse(start_url)
    rp = RobotFileParser()
    rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
    try:
        rp.read()
        return rp
    except Exception:
        return None


def fetch(session, url, retries=3, timeout=15, stream=False):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout, stream=stream)
            if r.status_code in (429, 503):
                r.close()
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"  ! failed: {url} ({e})")
            else:
                time.sleep(2 ** attempt)
    return None


def extract_media(soup, base):
    """Return a list of (url, type, source_tag) for every media reference."""
    found = []

    def add(raw, kind, tag):
        raw = (raw or "").strip()
        if not raw or raw.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:", "#")):
            return
        url = normalize(urljoin(base, raw))
        if urlparse(url).scheme not in ("http", "https"):
            return
        kind = kind or EXT_TYPES.get(ext_of(url))
        if kind:
            found.append((url, kind, tag))

    def add_srcset(value, kind, tag):
        for part in re.split(r",\s+", (value or "").strip()):
            if part.strip():
                add(part.split()[0], kind, tag)

    def add_css(text, tag):
        for m in CSS_URL.findall(text or ""):
            add(m, None, tag)

    for img in soup.find_all("img"):
        for a in LAZY_ATTRS:
            add(img.get(a), "image", "img")
        for a in SRCSET_ATTRS:
            add_srcset(img.get(a), "image", "img")

    for src in soup.find_all("source"):
        parent = src.parent.name if src.parent else ""
        kind = {"picture": "image", "video": "video", "audio": "audio"}.get(parent)
        add(src.get("src"), kind, f"source/{parent}")
        for a in SRCSET_ATTRS:
            add_srcset(src.get(a), kind or "image", f"source/{parent}")

    for tag_name, kind in (("video", "video"), ("audio", "audio")):
        for el in soup.find_all(tag_name):
            add(el.get("src"), kind, tag_name)
            if tag_name == "video":
                add(el.get("poster"), "image", "video[poster]")

    for tag in soup.find_all(["embed", "iframe"], src=True):
        add(tag["src"], None, tag.name)
    for tag in soup.find_all("object", data=True):
        add(tag["data"], None, "object")

    for a in soup.find_all("a", href=True):
        add(a["href"], None, "a")

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in META_KEYS:
            add(meta.get("content"), META_KEYS[key], "meta")

    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        as_ = (link.get("as") or "").lower()
        if "image_src" in rel or (rel == "preload" and as_ in ("image", "video", "audio")):
            add(link["href"], as_ if as_ in ("image", "video", "audio") else "image", "link")

    for el in soup.find_all(style=True):
        add_css(el["style"], "css")
    for st in soup.find_all("style"):
        add_css(st.string, "css")

    return found


def extract_links(soup, base):
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = normalize(urljoin(base, href))
        if urlparse(url).scheme in ("http", "https"):
            out.append(url)
    return out


def scan(start_url, max_pages=50, delay=1.0, types=None, same_site_only=False):
    types = set(types or TYPE_ORDER)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = load_robots(start_url)

    root = site_key(start_url)
    start = normalize(start_url)
    queue, seen = deque([start]), {start}
    items = {}  #url -> item
    visited = 0

    while queue and visited < max_pages:
        url = queue.popleft()
        if robots and not robots.can_fetch(USER_AGENT, url):
            print(f"  - blocked by robots.txt: {url}")
            continue

        print(f"[{visited + 1}/{max_pages}] {url}")
        resp = fetch(session, url, stream=True)
        visited += 1
        if resp is None:
            time.sleep(delay)
            continue
        if visited == 1:
            root = site_key(resp.url)  #redirects eg http -> https

        ctype = resp.headers.get("Content-Type", "").lower()
        if "html" not in ctype:
            resp.close()  #file (not page); don't download the body while scanning
            kind = EXT_TYPES.get(ext_of(url))
            if kind in types and url not in items:
                items[url] = {"url": url, "type": kind, "found_on": url, "tag": "direct"}
            time.sleep(delay)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        base = resp.url

        new = 0
        for murl, kind, tag in extract_media(soup, base):
            if kind not in types or murl in items:
                continue
            if same_site_only and site_key(murl) != root:
                continue
            items[murl] = {"url": murl, "type": kind, "found_on": url, "tag": tag}
            new += 1
        if new:
            print(f"  + {new} new media file(s)")

        for link in extract_links(soup, base):
            if ext_of(link) in EXT_TYPES:
                continue  #media
            if site_key(link) == root and link not in seen:
                seen.add(link)
                queue.append(link)
        time.sleep(delay)

    order = {t: i for i, t in enumerate(TYPE_ORDER)}
    result = sorted(items.values(), key=lambda it: order[it["type"]]) #keeps order of discovery
    return result, visited


def probe(items, workers=8):
    """HEAD every file to learn its size and real content type (optional)."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def head(item):
        try:
            r = session.head(item["url"], allow_redirects=True, timeout=10,
                             headers={"Referer": item["found_on"]})
            if r.status_code < 400:
                if "Content-Length" in r.headers:
                    item["size"] = int(r.headers["Content-Length"])
                item["content_type"] = r.headers.get("Content-Type", "").split(";")[0]
        except Exception:
            pass

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(head, items))



def save_json(items, source, pages, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "pages_crawled": pages,
                   "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "items": items}, f, indent=2)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["items"]
    except FileNotFoundError:
        sys.exit(f"{path} not found. Run `scan` first.")


def print_listing(items, types=None):
    """Numbers are global (stable) so they can be used with --select."""
    types = set(types or TYPE_ORDER)
    print()
    for t in TYPE_ORDER:
        group = [(i, it) for i, it in enumerate(items, 1) if it["type"] == t and t in types]
        if not group:
            continue
        print(f"== {t.upper()} ({len(group)}) " + "=" * 40)
        for i, it in group:
            size = f"  [{human(it['size'])}]" if "size" in it else ""
            print(f"{i:>4}. {it['url']}{size}")
        print()
    counts = ", ".join(f"{sum(1 for it in items if it['type'] == t)} {t}" for t in TYPE_ORDER)
    total = sum(it.get("size", 0) for it in items)
    extra = f"  (~{human(total)} known size)" if total else ""
    print(f"Total: {len(items)} files - {counts}{extra}")


def safe_name(url):
    name = os.path.basename(unquote(urlparse(url).path)) or "file"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "file"
    return name[:150]


def assign_names(items):
    """Filenames from URLs; if two different URLs share a name, add a short hash."""
    names = [safe_name(it["url"]) for it in items]
    counts = {}
    for it, n in zip(items, names):
        counts[(it["type"], n)] = counts.get((it["type"], n), 0) + 1
    out = []
    for it, n in zip(items, names):
        if counts[(it["type"], n)] > 1:
            stem, ext = os.path.splitext(n)
            n = f"{stem}_{hashlib.md5(it['url'].encode()).hexdigest()[:8]}{ext}"
        out.append(n)
    return out


def download_one(session, item, name, dest_dir, timeout=30, retries=2):
    ext = os.path.splitext(name)[1]
    if ext and os.path.exists(os.path.join(dest_dir, name)):
        return "skipped", name, 0
    last_err = ""
    for attempt in range(retries):
        tmp = None
        try:
            with session.get(item["url"], stream=True, timeout=timeout,
                             headers={"Referer": item["found_on"]}) as r:
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if ctype == "text/html":
                    return "failed", "server returned an HTML page, not a file", 0
                final = name
                if not ext:
                    final += mimetypes.guess_extension(ctype) or ""
                path = os.path.join(dest_dir, final)
                if os.path.exists(path):
                    return "skipped", final, 0
                tmp, size = path + ".part", 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)
                        size += len(chunk)
                os.replace(tmp, path)
                return "ok", final, size
        except Exception as e:
            last_err = str(e)
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
            time.sleep(1 + attempt)
    return "failed", last_err, 0


def download(items, outdir="downloads", workers=4):
    if not items:
        print("Nothing to download.")
        return
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    names = assign_names(items)
    for t in {it["type"] for it in items}:
        os.makedirs(os.path.join(outdir, t), exist_ok=True)

    print(f"\nDownloading {len(items)} file(s) to ./{outdir}/ ...")
    stats = {"ok": 0, "skipped": 0, "failed": 0}
    total_bytes, failures = 0, []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(download_one, session, it, n, os.path.join(outdir, it["type"])): it
                for it, n in zip(items, names)}
        for done, fut in enumerate(as_completed(futs), 1):
            status, info, size = fut.result()
            stats[status] += 1
            total_bytes += size
            if status == "failed":
                failures.append((futs[fut]["url"], info))
            print(f"[{done}/{len(items)}] {status:<7} {info if status != 'failed' else futs[fut]['url']}")

    print(f"\nDone: {stats['ok']} downloaded ({human(total_bytes)}), "
          f"{stats['skipped']} already existed, {stats['failed']} failed.")
    for url, err in failures:
        print(f"  ! {url}\n    {err}")


#CLI
def parse_types(value):
    if not value or value.lower() == "all":
        return list(TYPE_ORDER)
    types = [t.strip().lower().rstrip("s") for t in value.split(",") if t.strip()]
    bad = [t for t in types if t not in TYPE_ORDER]
    if bad:
        sys.exit(f"Unknown type(s): {', '.join(bad)}. Choose from: {', '.join(TYPE_ORDER)}")
    return types


def parse_selection(spec, n):
    """'1,4,10-20' -> {1, 4, 10, ..., 20}, validated against 1..n."""
    chosen = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                chosen.update(range(int(a), int(b) + 1))
            else:
                chosen.add(int(part))
        except ValueError:
            sys.exit(f"Bad selection: '{part}'")
    out_of_range = [i for i in chosen if not 1 <= i <= n]
    if out_of_range:
        sys.exit(f"Numbers out of range (1-{n}): {sorted(out_of_range)}")
    return chosen


def choose_items(items, types=None, select=None):
    chosen = items
    if select:
        wanted = parse_selection(select, len(items))
        chosen = [it for i, it in enumerate(items, 1) if i in wanted]
    if types:
        chosen = [it for it in chosen if it["type"] in set(types)]
    return chosen


def cmd_scan(a):
    types = parse_types(a.types)
    items, pages = scan(a.url, a.max_pages, a.delay, types, a.same_site_only)
    if a.probe and items:
        print("\nChecking file sizes ...")
        probe(items)
    save_json(items, a.url, pages, a.output)
    print_listing(items)
    print(f"\nCrawled {pages} page(s). Saved to {a.output}. "
          f"Download later with: python {os.path.basename(__file__)} download")


def cmd_list(a):
    print_listing(load_json(a.input), parse_types(a.types))


def cmd_download(a):
    items = load_json(a.input)
    chosen = choose_items(items, parse_types(a.types) if a.types else None, a.select)
    download(chosen, a.dir, a.workers)


def ask(prompt, default=None):
    val = input(f"{prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
    return val or (str(default) if default is not None else "")


def interactive():
    print("=== Media Scraper ===")
    url = ask("Website URL")
    if not url:
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    max_pages = int(ask("Max pages to crawl", 50))
    types = parse_types(ask("Types (image,video,audio,pdf or all)", "all"))
    items, pages = scan(url, max_pages, 1.0, types)
    if not items:
        print("\nNo media found.")
        return
    if ask("Look up file sizes? (slower) y/n", "n").lower().startswith("y"):
        probe(items)
    save_json(items, url, pages, DEFAULT_JSON)
    print_listing(items)
    print(f"\n(Results saved to {DEFAULT_JSON})")

    while True:
        choice = ask("\nDownload?  [a]ll / [t]ypes / [n]umbers / [q]uit", "q").lower()
        if choice.startswith("q"):
            break
        if choice.startswith("a"):
            chosen = items
        elif choice.startswith("t"):
            chosen = choose_items(items, parse_types(ask("Which types (e.g. image,pdf)")))
        elif choice.startswith("n"):
            chosen = choose_items(items, select=ask("Numbers (e.g. 1,4,10-20)"))
        else:
            continue
        download(chosen, ask("Save to folder", "downloads"))
        break


def main():
    p = argparse.ArgumentParser(description="Find and download images, video, audio and PDFs from a website.")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="crawl a site and list its media")
    s.add_argument("url")
    s.add_argument("--max-pages", type=int, default=50)
    s.add_argument("--delay", type=float, default=1.0, help="seconds between page requests")
    s.add_argument("--types", default="all", help="image,video,audio,pdf (default all)")
    s.add_argument("--same-site-only", action="store_true", help="ignore media hosted on other domains/CDNs")
    s.add_argument("--probe", action="store_true", help="look up file sizes (slower)")
    s.add_argument("-o", "--output", default=DEFAULT_JSON)
    s.set_defaults(func=cmd_scan)

    l = sub.add_parser("list", help="show results of a previous scan")
    l.add_argument("-i", "--input", default=DEFAULT_JSON)
    l.add_argument("--types", default="all")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("download", help="download files from a previous scan")
    d.add_argument("-i", "--input", default=DEFAULT_JSON)
    d.add_argument("--types", help="only these types, e.g. image,pdf")
    d.add_argument("--select", help="only these list numbers, e.g. 1,4,10-20")
    d.add_argument("-d", "--dir", default="downloads")
    d.add_argument("--workers", type=int, default=4)
    d.set_defaults(func=cmd_download)

    args = p.parse_args()
    try:
        args.func(args) if args.cmd else interactive()
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()