# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # Required: installs headless browser binary
```

Copy `.env.example` to `.env` and set `GOODREADS_RSS_URL` to the RSS feed URL (must contain `list_rss`, not the HTML shelf page).

```bash
python3 library_cli.py                       # full run, uses 6-hour cache
python3 library_cli.py --available-only      # filter to books with at least one available copy
python3 library_cli.py --no-cache            # force fresh scrapes (skip .library_cache.json)
```

There are no tests or lint commands.

## Architecture

Checks two library catalogs for books on a Goodreads "Want to Read" list and prints a color-coded availability report.

**Data flow:**
1. `library_cli.py` loads `GOODREADS_RSS_URL` from `.env`, fetches the feed via `feedparser`, and cleans titles (strips series/edition markers like `(Book 1)` or `[#1]`)
2. A single Playwright browser + context is shared across all books; each book runs JCFPL and SOPL scrapers concurrently on separate pages, with 3–6s random delays between books
3. `scrapers/jc_library.py` drives the JCFPL Sirsi Enterprise catalog (HTML scraping)
4. `scrapers/so_library.py` drives the BCCLS Vega Discover catalog (intercepts the JSON `format-groups` POST response from the SPA)
5. `collapse_rows` groups by `(title, author, library)`. For each group: if any copy is Available Now, the checked-out rows are dropped; if every copy is checked out, the rows collapse into one with the earliest due date (parsed from the `Due M/D/YY` substring in the status); otherwise the first row is kept
6. Results are sorted Available → Checked Out / Possibly → Not Found, then displayed as a `rich` table and saved to `latest_library_run.txt`
7. Per-book results are cached in `.library_cache.json` for `CACHE_TTL_HOURS` (default 6). The cache file uses `{"version": N, "entries": {...}}` envelope; `load_cache()` discards the file when `version != CACHE_VERSION`, so bumping the constant is the migration mechanism

## Adding another library

Adding a third library is a 3-step change:
1. Drop a new `scrapers/<name>.py` exposing `async def check_<name>(title, author, page) -> list[dict]`, returning rows with `library`, `branch`, `status` keys
2. Append `(label, library_name, checker)` to the `_LIBRARIES` list in `library_cli.py`
3. Bump `CACHE_VERSION` so old cache entries (which don't include the new library's rows) are discarded

## Sirsi Catalog Quirks (JCFPL)

- **Search URL**: Uses `FORMAT%09Format%09BOOK%09Books` parameter to filter physical books only
- **Title links**: `href="#"` — the actual ILS item ID is in the `onclick` attribute, not the href
- **Single-result redirect**: When a search returns exactly one hit, Sirsi skips the results page and redirects directly to the detail page; `check_jc_library()` detects this by checking for absence of `.detailLink` elements
- **AJAX holdings**: The holdings table (`table.detailItemTable`) initially shows "Searching..." placeholders that must resolve before parsing; `_wait_for_holdings_ajax()` polls until they disappear
- **Status interpretation**: A shelf location name (e.g., "Non-Fiction Books") in the status column means the item is on the shelf; only statuses matching known unavailable patterns (Checked Out, On Order, etc.) are treated as unavailable

## Vega Discover Quirks (SOPL)

- **Shared catalog**: `sora.search.bccls.org` is BCCLS-wide (~60+ libraries); SOPL is one branch among many
- **Response interception**: Hand-rolled API calls hit 403; the scraper navigates to the search page and intercepts the SPA's own `POST /api/search-result/search/format-groups` response (which carries the right `iii-*` and `anonymous-user-id` headers automatically)
- **Truncated locations**: Each result's `materialTabs[].locations[]` is sorted alphabetically and capped at 6. South Orange (S) usually doesn't appear. There's no working server-side location filter (`metadataBoolQuery` rejected every variant tried). When SO is absent but the result has copies elsewhere, the scraper emits a soft `Possibly at SO — N BCCLS copies` row instead of guessing
- **Material filter**: Done client-side — only `type === "physical"` tabs whose name contains "book" or "large print" (and not "audio"/"ebook"/etc.) are kept

## Key Configuration Constants

Defined at the top of `library_cli.py`:
- `DELAY_MIN` / `DELAY_MAX` (3–6s) — inter-book delay to avoid rate-limiting; increase if searches timeout (the delay only fires between books that actually need scraping — cached books are skipped silently)
- `CACHE_TTL_HOURS` (6) — how long a per-book result stays valid in `.library_cache.json`
- `CACHE_VERSION` — bump whenever `_LIBRARIES` changes or the cached row schema changes; old caches are auto-discarded
- `_LIBRARIES` — list of `(label, library_name, async_checker)` tuples; the loop in `scrape_book` runs all of them in parallel via `asyncio.gather` per book
- `headless=True` in `run_all_books()` — set to `False` to watch the browser during debugging
