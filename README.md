# Library Availability Checker

A local Python CLI tool that checks **Jersey City Free Public Library (JCFPL)** and
**Harrison, NJ Public Library** for the availability of every book on your Goodreads
reading list — automatically, asynchronously, and formatted as a color-coded terminal table.

---

## Features

- **Goodreads RSS ingestion** — pulls your reading list directly from your public feed
- **Automatic title cleaning** — strips series/edition markers like `(Secrets and Lies, #1)` before searching
- **Two library catalogs** — JCFPL (BiblioCommons) and Harrison Public Library
- **Physical books only** — filters out eBooks, Audiobooks, and eAudiobooks
- **Availability detail** — returns branch name + "Available Now", "Checked Out (Due: …)", or hold queue depth
- **Rich terminal table** — color-coded status column (green / yellow / red)
- **Plain-text file output** — saves `latest_library_run.txt` alongside the terminal view
- **Polite delays** — random 3–6 second pauses between searches to avoid IP blocks

---

## Project Structure

```
library searcher/
├── library_cli.py              # Main orchestrator, RSS parser, terminal UI
├── scrapers/
│   ├── __init__.py
│   ├── jc_library.py           # JCFPL (BiblioCommons) scraper
│   └── harrison_library.py     # Harrison Public Library scraper
├── requirements.txt
├── .env                        # Your Goodreads RSS URL (not committed)
├── latest_library_run.txt      # Auto-generated after each run
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.11+ (check with `python3 --version`)
- `pip` or a virtual environment tool of your choice

### 2. Create and activate a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Playwright browser binaries

> **This step is mandatory.** Playwright downloads real Chromium binaries to drive
> the catalog websites. Without it, the scrapers will crash.

```bash
playwright install chromium
```

If you'd like all supported browsers (not required):

```bash
playwright install
```

### 5. Configure your Goodreads RSS URL

Open `.env` and replace the placeholder value:

```dotenv
GOODREADS_RSS_URL=https://www.goodreads.com/review/list_rss/YOUR_USER_ID?shelf=to-read
```

**How to find your Goodreads RSS URL:**
1. Go to your Goodreads profile.
2. Navigate to the shelf you want to check (e.g., "Want to Read").
3. Scroll to the bottom of the shelf page and click the **RSS** icon.
4. Copy the URL from your browser's address bar.
5. Paste it into `.env`.

The feed must be **public** (set in your Goodreads privacy settings).

---

## Running the Tool

```bash
python3 library_cli.py
```

The script will:
1. Fetch your Goodreads feed
2. Launch a headless Chromium browser
3. Search both library catalogs for each book (with polite delays)
4. Print a formatted table to your terminal
5. Save a plain-text copy as `latest_library_run.txt`

---

## Reading the Output

| Status | Color | Meaning |
|---|---|---|
| `Available Now` | Green | Copy is on the shelf, ready to check out |
| `Checked Out (Due: MM/DD/YYYY)` | Yellow | All copies are checked out; due date shown |
| `On Hold (N in queue)` | Yellow | Book has a waitlist |
| `Not Found` | Red | No physical copy found in that library's catalog |
| `Timeout` / `Error` | Red | Network or page-load issue during scraping |

---

## Troubleshooting

### "Catalog Not Found — Update URL"
The Harrison Library scraper couldn't find a search form on the library website.
This means the catalog URL or page structure has changed.

**Fix:** Open `scrapers/harrison_library.py` and update `HARRISON_CATALOG` to the
current catalog URL. You can find it by visiting [townofharrison.com](https://www.townofharrison.com)
and navigating to the library section.

### All books return "Not Found" for JCFPL
The BiblioCommons catalog selectors may have changed.

**Fix:** Open `scrapers/jc_library.py`. Visit [jclibrary.bibliocommons.com](https://jclibrary.bibliocommons.com),
perform a manual search, right-click → Inspect, and update the CSS selectors in
`check_jc_library()` and `_extract_availability()` to match the current HTML.

### The browser opens visibly instead of running headless
This is normal during debugging. In `library_cli.py`, the `launch()` call uses
`headless=True`. Change it to `headless=False` if you want to watch the scraper
navigate the pages live — very useful for diagnosing selector issues.

### Rate limiting / IP block
Increase `DELAY_MIN` and `DELAY_MAX` in `library_cli.py` to longer intervals
(e.g., 8–15 seconds).

---

## Notes on the Harrison Library Catalog

Harrison Public Library is a small NJ municipal library. Their ILS (Integrated
Library System) may be:

- **Koha** (open-source, used by many small NJ libraries) — scraped first
- **Polaris / PowerPAC** — the fallback scraper navigates the site's search form

If neither approach works, set `headless=False` in `library_cli.py`, run the script,
observe where the browser gets stuck, and update the selectors in
`scrapers/harrison_library.py` accordingly.
