# scraper

A single-file Python tool that crawls a website, lists every **image, video, audio file and PDF** it finds, and lets you download them now or later.

## Install

```bash
pip install requests beautifulsoup4
```

Python 3.8+. Before first use, set your contact email in `USER_AGENT` at the top of `media_scraper.py`.

## Usage

**Interactive wizard** (scan, review the list, choose what to download):

```bash
python media_scraper.py
```

**Commands:**

```bash
python media_scraper.py scan https://example.com      # crawl and list, saves media.json
python media_scraper.py list --types image,pdf        # re-show saved results
python media_scraper.py download                      # download everything
python media_scraper.py download --types video,audio  # only certain types
python media_scraper.py download --select 1,4,10-20   # only these list numbers
```

Because `scan` saves `media.json`, you can run `download` later without crawling again.

## Options

| Command | Option | Description |
|---|---|---|
| `scan` | `--max-pages N` | Pages to crawl (default 50) |
| | `--delay S` | Seconds between page requests (default 1.0) |
| | `--types` | `image,video,audio,pdf` or `all` (default) |
| | `--same-site-only` | Ignore media hosted on other domains/CDNs |
| | `--probe` | Look up file sizes (slower) |
| | `-o FILE` | Output JSON (default `media.json`) |
| `download` | `-i FILE` | Input JSON (default `media.json`) |
| | `--types`, `--select` | Filter by type or by list number |
| | `-d DIR` | Output folder (default `downloads`) |
| | `--workers N` | Parallel downloads (default 4) |

## What it finds

- **Images:** `<img>`, `srcset`, lazy-load attributes (`data-src`), `<picture>`, video posters, CSS `url(...)` backgrounds, `og:image`
- **Video / audio:** `<video>`, `<audio>`, `<source>`, `og:video`, links to media files
- **PDFs:** links, `<embed>`, `<iframe>`, `<object>`

## Output

```
downloads/
├── image/
├── video/
├── audio/
└── pdf/
```

Existing files are skipped, so re-running only fetches what's missing. Name collisions get a short hash suffix instead of overwriting.

## Limitations

- **JavaScript-rendered pages:** media added by JS won't be seen. Use Playwright, or look for a JSON API in your browser's Network tab.
- **Embedded players** (YouTube, Vimeo) and **streams** (`.m3u8`) aren't downloadable as plain files. Use a tool like `yt-dlp`.
- Only crawls the starting site's domain (media on other domains is still listed unless `--same-site-only` is set).

## be nice!

The scraper respects `robots.txt`, rate-limits requests and identifies itself. Check a site's terms of service before bulk-downloading, as much online media is copyrighted.
