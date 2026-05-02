# Library Availability Checker

A local Python CLI tool that checks two library catalogs for every book on your Goodreads reading list and prints a color-coded terminal table:

- **Jersey City Free Public Library (JCFPL)** — Sirsi Enterprise ([jepl.ent.sirsi.net](https://jepl.ent.sirsi.net))
- **South Orange Public Library (SOPL)** — Vega Discover on the shared BCCLS catalog ([sora.search.bccls.org](https://sora.search.bccls.org))

The two scrapers run in parallel per book.

---

## Features

- **Goodreads RSS ingestion** — pulls your reading list from your public feed
- **Automatic title cleaning** — strips series/edition markers like `(Secrets and Lies, #1)` before searching
- **Physical books only** — eBooks, eAudiobooks, and Audio CDs are filtered out at both libraries
- **Per-branch availability** — branch name plus status (including shelf-location-as-available and due dates)
- **Smart collapse + sort** — `Available Now` first; books that are checked out at every branch collapse to a single row showing the soonest return date
- **`--available-only`** — restrict the report to books with at least one available copy
- **Result cache** — successful per-book lookups are cached for 6 hours; `--no-cache` forces fresh scrapes
- **Rich terminal table** — color-coded status column (green / yellow / red)
- **Plain-text file output** — saves `latest_library_run.txt` alongside the terminal view
- **Polite delays** — random 3–6 second pauses between book searches to reduce rate-limit risk

---

## Project Structure

```
library searcher/
├── library_cli.py              # Main orchestrator, RSS parser, terminal UI
├── scrapers/
│   ├── __init__.py
│   ├── jc_library.py           # JCFPL (Sirsi Enterprise) scraper
│   └── so_library.py           # SOPL (Vega Discover via BCCLS) scraper
├── requirements.txt
├── .env                        # Your Goodreads RSS URL (not committed)
├── .library_cache.json         # Auto-generated 6-hour result cache
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

> **This step is mandatory.** Playwright downloads Chromium to drive the catalog.

```bash
playwright install chromium
```

### 5. Configure your Goodreads RSS URL

Open `.env` (copy from `.env.example` if needed) and set:

```dotenv
GOODREADS_RSS_URL=https://www.goodreads.com/review/list_rss/YOUR_USER_ID?key=...&shelf=to-read
```

Use the **RSS** link at the bottom of your Goodreads shelf page — the URL must contain `list_rss`, not the normal shelf page.

---

## Running the Tool

```bash
python3 library_cli.py                       # full run, uses 6-hour cache
python3 library_cli.py --available-only      # only show books with an available copy
python3 library_cli.py --no-cache            # force fresh scrapes for every book
```

The script fetches your feed, launches headless Chromium, scrapes JCFPL and SOPL in parallel per book, prints the table, and writes `latest_library_run.txt`.

---

## Reading the Output

| Status | Color | Meaning |
|---|---|---|
| `Available Now` | Green | On shelf (Sirsi often uses the shelf area name as the status) |
| `Checked Out (Due …)` | Yellow | Checked out at one branch; due date if exposed |
| `Checked Out — soonest: <date>` | Yellow | Every copy is checked out; row collapsed to earliest return |
| `On Hold` | Yellow | Holds / waitlist |
| `Possibly at SO — N BCCLS copies` | Yellow | SOPL only: South Orange may have it (truncated catalog list); N copies elsewhere in BCCLS are requestable |
| `Not at SO` | Red | SOPL only: confirmed not in any BCCLS branch |
| `Not Found` | Red | No matching physical book in the catalog |
| `Timeout` | Red | Page load timed out |

### SOPL truncation caveat

The BCCLS Vega Discover API returns only the first six branches alphabetically per result, and "South Orange" usually falls outside that. When that happens we surface a `Possibly at SO — N BCCLS copies` row so you know a hold is viable even if SO availability isn't directly confirmed.

---

## Troubleshooting

### SSL errors when fetching Goodreads

The project uses `certifi` for HTTPS. Run `pip install -r requirements.txt` again. On macOS you can also run **Install Certificates.command** from the Python folder in Applications.

### Many books show "Not Found" but they exist on the site

JCFPL: the scraper targets Sirsi’s **book** facet and parses `table.detailItemTable` on the detail page. If JCFPL changes their templates, open `scrapers/jc_library.py` and adjust selectors.

SOPL: the scraper intercepts the Vega Discover `format-groups` POST response. If BCCLS migrates off Vega Discover or the response shape changes, see `scrapers/so_library.py`. For debugging either scraper, set `headless=False` in `library_cli.py` to watch the browser.

### Rate limiting

Increase `DELAY_MIN` and `DELAY_MAX` in `library_cli.py`.
