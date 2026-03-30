# Library Availability Checker

A local Python CLI tool that checks the **Jersey City Free Public Library (JCFPL)** online catalog for the availability of every book on your Goodreads reading list — formatted as a color-coded terminal table.

The JCFPL catalog is **Sirsi Enterprise** ([jepl.ent.sirsi.net](https://jepl.ent.sirsi.net)).

---

## Features

- **Goodreads RSS ingestion** — pulls your reading list from your public feed
- **Automatic title cleaning** — strips series/edition markers like `(Secrets and Lies, #1)` before searching
- **Physical books only** — search is restricted to the Books format in the catalog
- **Per-branch availability** — branch name plus status (including shelf-location-as-available and due dates)
- **Rich terminal table** — color-coded status column (green / yellow / red)
- **Plain-text file output** — saves `latest_library_run.txt` alongside the terminal view
- **Polite delays** — random 3–6 second pauses between book searches to reduce rate-limit risk
- **Live progress** — prints a one-line JCFPL result for each book as it finishes

---

## Project Structure

```
library searcher/
├── library_cli.py              # Main orchestrator, RSS parser, terminal UI
├── scrapers/
│   ├── __init__.py
│   └── jc_library.py           # JCFPL (Sirsi Enterprise) scraper
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
python3 library_cli.py
```

The script will fetch your feed, launch headless Chromium, search JCFPL for each book, print the table, and write `latest_library_run.txt`.

---

## Reading the Output

| Status | Color | Meaning |
|---|---|---|
| `Available Now` | Green | On shelf (Sirsi often shows the shelf area name in the status column) |
| `Checked Out (Due …)` | Yellow | Checked out; due date if the catalog exposes it |
| `On Hold` | Yellow | Holds / waitlist |
| `Not Found` | Red | No matching physical book in the filtered search |
| `Timeout` | Red | Page load timed out |

---

## Troubleshooting

### SSL errors when fetching Goodreads

The project uses `certifi` for HTTPS. Run `pip install -r requirements.txt` again. On macOS you can also run **Install Certificates.command** from the Python folder in Applications.

### Many books show "Not Found" but they exist on the site

The scraper targets Sirsi’s **book** facet and parses `table.detailItemTable` on the detail page. If JCFPL changes their templates, open `scrapers/jc_library.py` and adjust selectors. For debugging, set `headless=False` in `library_cli.py` so you can watch the browser.

### Rate limiting

Increase `DELAY_MIN` and `DELAY_MAX` in `library_cli.py`.
