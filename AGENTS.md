# AGENTS.md

## Project overview

This repository contains a Python CLI that reads a Goodreads RSS shelf, checks physical-book availability at South Orange Public Library through the shared BCCLS Vega Discover catalog, displays a Rich terminal report, and saves a plain-text copy.

## Setup and commands

Use the existing virtual environment when it is available:

```bash
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python3 library_cli.py
python3 library_cli.py --available-only
python3 library_cli.py --no-cache
```

Copy `.env.example` to `.env` and set `GOODREADS_RSS_URL` to a Goodreads `list_rss` URL. Never commit `.env` or expose its URL because it contains a private feed key.

There is currently no automated test or lint command. At minimum, compile the Python files after code changes:

```bash
python3 -m compileall -q library_cli.py scrapers
```

## Architecture

- `library_cli.py` fetches and parses the Goodreads feed, cleans titles, coordinates Playwright, caches per-book results, collapses and sorts rows, and renders output.
- `scrapers/so_library.py` drives the BCCLS Vega Discover UI and extracts the SPA's `format-groups` JSON response.
- `scrapers/__init__.py` exposes configured scraper functions.
- `.library_cache.json` and `latest_library_run.txt` are generated files and remain untracked.

The scraper contract is `async def check_<name>(title, author, page) -> list[dict]`. Every returned row must include `library`, `branch`, and `status`. Add the checker to `_LIBRARIES` in `library_cli.py`, and bump `CACHE_VERSION` whenever the configured libraries or cached row schema changes.

## Implementation notes

- Keep browser pages isolated per scraper and always close them in `finally` blocks.
- Preserve the physical-book filter; electronic and audio formats must not appear as borrowable books.
- Treat the BCCLS location list as truncated. If South Orange is absent but other BCCLS copies exist, retain the cautious `Possibly at SO` result instead of claiming the title is unavailable locally.
- Keep network failures and Playwright errors short enough for both terminal and plain-text reports by routing statuses through `_sanitize_status`.
- Do not weaken the random inter-book delay without considering catalog rate limits.
- Update `README.md`, this file, and `CLAUDE.md` when commands, architecture, supported libraries, or catalog behavior changes.
