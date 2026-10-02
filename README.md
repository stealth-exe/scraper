# scraper

A single-file Python tool that crawls a website, lists every image, video, audio file and PDF it finds, and lets you download them immediately or later.

## Install

```bash
git clone https://github.com/stealth-exe/scraper.git
cd scraper
pip3 install -r requirements.txt
```

Use python 3.8+ for best performance. Before first use, set your contact email in `USER_AGENT` at the top of `media_scraper.py`.

## Usage

**via wizard**

```bash
python3 media_scraper.py
```

**via commands**

```bash
python3 media_scraper.py scan https://example.com      # crawl and list, saves media.json
python3 media_scraper.py list --types image,pdf        # re-show saved results
python3 media_scraper.py download                      # download everything
python3 media_scraper.py download --types video,audio  # only certain types
python3 media_scraper.py download --select 1,4,10-20   # only these list numbers
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

- Media added by JS won't be seen. Use Playwright, or look for a JSON API in your browser's Network tab.
- Embedded players (YouTube, Vimeo etc) and streams (`.m3u8`) aren't downloadable as plain files. Something like `yt-dlp` would probably be more apt. 
- Crawl scope: only pages on the starting domain are visited (`www.` is ignored; other subdomains like `blog.example.com` are not followed).
- Media scope: files hosted elsewhere, such as a CDN, are still listed. Use `--same-site-only` to keep only files on the starting domain.

## be nice!

The scraper respects `robots.txt`, rate-limits requests and identifies itself. Check a site's terms of service before bulk-downloading, as much online media is copyrighted.

## Contributing

Bug reports and pull requests are welcome at [github.com/stealth-exe/scraper](https://github.com/stealth-exe/scraper). Please [open an issue](https://github.com/stealth-exe/scraper/issues) first for larger changes.

## License

See [LICENSE](https://github.com/stealth-exe/scraper/blob/main/LICENSE).
