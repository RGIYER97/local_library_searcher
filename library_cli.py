#!/usr/bin/env python3
"""
library_cli.py — Main orchestrator for the Library Availability Checker.

Flow:
  1. Load Goodreads RSS URL from .env
  2. Parse the feed with feedparser → extract title + author per book
  3. Clean titles with regex (strip parenthetical series/edition info)
  4. Launch a Playwright browser and loop through books
  5. For each book, run the Jersey City scraper with random delays
  6. Aggregate all results into a rich terminal table
  7. Save a plain-text copy to latest_library_run.txt
"""

import asyncio
import os
import random
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import certifi
import feedparser
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext
from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

from scrapers.jc_library import check_jc_library

# ── Constants ────────────────────────────────────────────────────────────────
OUTPUT_FILE = Path("latest_library_run.txt")
DELAY_MIN = 3      # seconds between searches (anti-bot courtesy)
DELAY_MAX = 6

STATUS_COLORS = {
    "available": "bold green",
    "checked out": "yellow",
    "on hold": "yellow",
    "not found": "red",
    "timeout": "red",
    "unavailable": "red",
    "catalog not found": "red",
    "unknown": "dim",
}

console = Console()


# ── Step 1: Environment & Feed ───────────────────────────────────────────────

def load_rss_url() -> str:
    # override=True ensures .env always wins over any previously exported shell variable
    load_dotenv(override=True)
    url = os.getenv("GOODREADS_RSS_URL", "").strip()
    if not url or "YOUR_USER_ID" in url:
        console.print(
            "[bold red]Error:[/] GOODREADS_RSS_URL is not set in your .env file.\n"
            "Open [cyan].env[/] and replace the placeholder with your real Goodreads RSS URL.",
            highlight=False,
        )
        sys.exit(1)
    return url


# ── Step 2: Feed Parsing ─────────────────────────────────────────────────────

_GOODREADS_UA = "library_cli/1.0 (+https://github.com/RGIYER97/local_library_searcher)"


def _fetch_feed_bytes(url: str) -> bytes:
    """
    Download feed over HTTPS using certifi's CA bundle.

    macOS Python installs from python.org often lack a working default CA store,
    which causes urllib/feedparser to raise SSL: CERTIFICATE_VERIFY_FAILED.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers={"User-Agent": _GOODREADS_UA})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()

    # Goodreads returns HTML when given a shelf URL instead of an RSS URL.
    # feedparser will then fail with "not well-formed (invalid token)".
    if "text/html" in content_type or raw.lstrip()[:15].lower().startswith(b"<!doctype"):
        console.print(
            "[bold red]Error:[/] The URL in your [cyan].env[/] is pointing to the Goodreads "
            "[bold]shelf page[/] (HTML), not the [bold]RSS feed[/] (XML).\n\n"
            "Your current URL pattern:  [yellow]/review/list/[/]\n"
            "Required URL pattern:      [green]/review/list_rss/[/]\n\n"
            "How to get the correct URL:\n"
            "  1. Go to your Goodreads profile → 'Want to Read' shelf\n"
            "  2. Scroll to the very bottom of the page\n"
            "  3. Click the [cyan]RSS[/] icon\n"
            "  4. Copy the URL from your browser — it will contain [green]list_rss[/]\n"
            "  5. Paste it into [cyan].env[/] as GOODREADS_RSS_URL",
            highlight=False,
        )
        sys.exit(1)

    return raw


def parse_goodreads_feed(url: str) -> list[dict]:
    """
    Fetch and parse the Goodreads RSS feed.

    Returns a list of dicts: [{title: str, author: str}, ...]
    Skips entries that are missing both title and author.
    """
    console.print(f"[cyan]Fetching Goodreads feed…[/] {url}")
    try:
        raw = _fetch_feed_bytes(url)
    except urllib.error.URLError as exc:
        console.print(f"[bold red]Failed to download RSS feed:[/] {exc.reason}")
        if "CERTIFICATE_VERIFY_FAILED" in str(exc.reason):
            console.print(
                "[dim]Tip: On macOS, you can also run "
                "“Install Certificates.command” from your Python folder in Applications.[/]",
                highlight=False,
            )
        sys.exit(1)
    except OSError as exc:
        console.print(f"[bold red]Failed to download RSS feed:[/] {exc}")
        sys.exit(1)

    feed = feedparser.parse(raw)

    if feed.bozo and not feed.entries:
        console.print(f"[bold red]Failed to parse RSS feed:[/] {feed.bozo_exception}")
        sys.exit(1)

    if not feed.entries and "/review/list" in url and "list_rss" not in url:
        console.print(
            "[bold red]No entries in feed.[/] Your URL looks like the Goodreads shelf page, "
            "not the RSS feed. Open your shelf on Goodreads, use the [cyan]RSS[/] link at the "
            "bottom, and copy that URL (it contains [cyan]list_rss[/]).",
            highlight=False,
        )
        sys.exit(1)

    books = []
    for entry in feed.entries:
        raw_title = entry.get("title", "").strip()
        author = entry.get("author_name", entry.get("author", "")).strip()

        if not raw_title:
            continue

        clean = clean_title(raw_title)
        if clean:
            books.append({"title": clean, "author": author, "raw_title": raw_title})

    console.print(f"[green]Found {len(books)} book(s) in your feed.[/]\n")
    return books


# ── Step 3: Title Cleaning ────────────────────────────────────────────────────

# Removes anything in parentheses: "Bad Blood (Secrets and Lies, #1)" → "Bad Blood"
# Also strips brackets: "Dune [Book 1]" → "Dune"
_PARENS_RE = re.compile(r"\s*[\(\[].*?[\)\]]\s*")
# Collapse extra whitespace after removal
_WHITESPACE_RE = re.compile(r"\s{2,}")


def clean_title(title: str) -> str:
    """Strip series/edition markers from a Goodreads RSS title."""
    cleaned = _PARENS_RE.sub(" ", title)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    # Remove trailing punctuation artifacts left after stripping (e.g. trailing colon)
    cleaned = re.sub(r"[:\-–—,]+$", "", cleaned).strip()
    return cleaned


# ── Step 4 & 5: Async Scraping Loop ──────────────────────────────────────────

def _sanitize_status(status: str) -> str:
    """Collapse multi-line Playwright error dumps to a single short line."""
    # Take only the first line, cap at 60 chars
    first_line = status.split("\n")[0].strip()
    if first_line.lower().startswith("error:"):
        # Strip "Error: ElementHandle.click: Timeout …" → "Timeout"
        # Strip "Error: Page.goto: net::ERR_…"         → "Network Error"
        inner = first_line[6:].strip()
        if "timeout" in inner.lower():
            return "Timeout"
        if "net::" in inner.lower() or "err_" in inner.lower():
            return "Network Error"
        return first_line[:60]
    return first_line[:60]


def _print_jc_result(results: list[dict]) -> None:
    """Print a per-book summary line for each JC result as it comes in."""
    for r in results:
        status = _sanitize_status(r.get("status", "Unknown"))
        branch = r.get("branch", "N/A")
        lower = status.lower()

        if "available now" in lower:
            icon = "[bold green]✓[/]"
            detail = f"[green]{status}[/] at [bold]{branch}[/]"
        elif "checked out" in lower or "on hold" in lower or "in transit" in lower:
            icon = "[yellow]~[/]"
            detail = f"[yellow]{status}[/] — [bold]{branch}[/]"
        elif "not found" in lower:
            icon = "[red]✗[/]"
            detail = f"[red]Not found in physical book format[/]"
        else:
            icon = "[dim]?[/]"
            detail = f"[dim]{status}[/]"

        console.print(f"  {icon} JCFPL: {detail}")


async def scrape_book(
    title: str,
    author: str,
    context: BrowserContext,
) -> list[dict]:
    """
    Run the JC scraper for a single book.
    Prints a live result summary to the terminal, then returns the result rows.
    """
    rows = []
    jc_page = await context.new_page()
    try:
        jc_results = await check_jc_library(title, author, jc_page)
        for r in jc_results:
            rows.append({**r, "title": title, "author": author})
    except Exception as exc:
        jc_results = [{
            "library": "Jersey City (JCFPL)", "branch": "N/A",
            "status": _sanitize_status(f"Error: {exc}"),
        }]
        rows.extend({**r, "title": title, "author": author} for r in jc_results)
    finally:
        await jc_page.close()

    _print_jc_result(jc_results)
    return rows


async def run_all_books(books: list[dict]) -> list[dict]:
    """
    Iterate through the book list, calling the JC scraper per book.
    Inserts a random inter-book delay to avoid rate-limiting.
    """
    all_rows = []

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        context: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
        )

        total = len(books)
        for idx, book in enumerate(books, start=1):
            title = book["title"]
            author = book["author"]
            console.rule(f"[bold]Book {idx}/{total}[/]")
            console.print(f"  Searching: [bold]{title}[/] by {author}")

            rows = await scrape_book(title, author, context)
            all_rows.extend(rows)

            # Inter-book delay (skip after the last book)
            if idx < total:
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                console.print(f"  [dim]Waiting {delay:.1f}s before next book…[/]")
                await asyncio.sleep(delay)

        await context.close()
        await browser.close()

    return all_rows


# ── Step 6: Rich Terminal Table ───────────────────────────────────────────────

def _status_style(status: str) -> str:
    lower = status.lower()
    for key, style in STATUS_COLORS.items():
        if key in lower:
            return style
    return "white"


def build_table(rows: list[dict]) -> Table:
    table = Table(
        title=f"[bold]Library Availability Report[/] — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold magenta",
        title_style="bold white on blue",
    )

    table.add_column("Book Title", style="bold", min_width=25, max_width=40, overflow="fold")
    table.add_column("Author", min_width=18, max_width=28, overflow="fold")
    table.add_column("Library System", min_width=20)
    table.add_column("Branch / Location", min_width=20)
    table.add_column("Status / When Available", min_width=22)

    for row in rows:
        status = _sanitize_status(row.get("status", "Unknown"))
        status_text = Text(status, style=_status_style(status))

        table.add_row(
            row.get("title", ""),
            row.get("author", ""),
            row.get("library", ""),
            row.get("branch", ""),
            status_text,
        )

    return table


# ── Step 7: Plain-text file output ───────────────────────────────────────────

def save_plain_text(rows: list[dict]) -> None:
    """Write a plain-text (no ANSI) version of the results to OUTPUT_FILE."""
    lines = [
        "Library Availability Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 90,
        f"{'Title':<38} {'Author':<22} {'Library':<20} {'Branch':<22} {'Status'}",
        "-" * 90,
    ]

    for row in rows:
        lines.append(
            f"{row.get('title',''):<38.38} "
            f"{row.get('author',''):<22.22} "
            f"{row.get('library',''):<20.20} "
            f"{row.get('branch',''):<22.22} "
            f"{_sanitize_status(row.get('status',''))}"
        )

    lines.append("=" * 90)
    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[dim]Plain-text report saved to [bold]{OUTPUT_FILE}[/][/]")


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main() -> None:
    console.rule("[bold blue]Library Availability Checker[/]")

    rss_url = load_rss_url()
    books = parse_goodreads_feed(rss_url)

    if not books:
        console.print("[yellow]No books found in feed. Nothing to search.[/]")
        return

    all_rows = await run_all_books(books)

    console.rule("[bold blue]Results[/]")
    table = build_table(all_rows)
    console.print(table)

    save_plain_text(all_rows)


if __name__ == "__main__":
    asyncio.run(main())
