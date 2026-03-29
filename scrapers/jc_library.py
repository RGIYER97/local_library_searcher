"""
Jersey City Free Public Library scraper.

Catalog system: BiblioCommons
Base URL: https://jclibrary.bibliocommons.com

BiblioCommons search URL structure:
  /v2/search?query=TITLE+AUTHOR&searchType=keyword&f_FORMAT=BK
  f_FORMAT=BK  → physical books only (filters out eBook, Audiobook, eAudiobook)
"""

import asyncio
import re
from typing import Optional
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError

CATALOG_BASE = "https://jclibrary.bibliocommons.com"
LIBRARY_NAME = "Jersey City (JCFPL)"


def _build_search_url(title: str, author: str) -> str:
    query = f"{title} {author}".strip()
    encoded = query.replace(" ", "+")
    return f"{CATALOG_BASE}/v2/search?query={encoded}&searchType=keyword&f_FORMAT=BK"


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for loose title matching."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


async def _extract_availability(page: Page, title: str) -> list[dict]:
    """
    After navigating to a bib detail page, scrape all branch copies and statuses.
    Returns a list of dicts: {library, branch, status}
    """
    results = []

    # Wait for the availability panel to load
    try:
        await page.wait_for_selector("div.cp-availability-status", timeout=10_000)
    except PlaywrightTimeoutError:
        # Availability panel didn't appear — possibly zero copies at JC
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # Each copy/holding is in a row inside the availability table
    # BiblioCommons uses .cp-availability-status__item or similar structure
    holding_rows = await page.query_selector_all(
        "li.cp-availability-status__item, div.cp-availability-info"
    )

    if not holding_rows:
        # Fall back: try to read a simple status badge
        status_el = await page.query_selector("span.cp-availability-status__label")
        if status_el:
            status_text = (await status_el.inner_text()).strip()
            branch_el = await page.query_selector("span.cp-availability-status__branch-name")
            branch_text = (await branch_el.inner_text()).strip() if branch_el else "Unknown Branch"
            results.append(
                {"library": LIBRARY_NAME, "branch": branch_text, "status": _map_status(status_text)}
            )
        else:
            results.append({"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"})
        return results

    seen_branches = set()
    for row in holding_rows:
        branch_el = await row.query_selector(
            ".cp-availability-status__branch-name, [data-testid='branch-name'], span.branch-name"
        )
        status_el = await row.query_selector(
            ".cp-availability-status__label, [data-testid='availability-label'], span.availability-label"
        )
        due_el = await row.query_selector(
            ".cp-availability-status__due-date, [data-testid='due-date'], span.due-date"
        )
        holds_el = await row.query_selector(
            ".cp-holds-count, [data-testid='holds-count'], span.holds-count"
        )

        branch = (await branch_el.inner_text()).strip() if branch_el else "Unknown Branch"
        raw_status = (await status_el.inner_text()).strip() if status_el else "Unknown"
        due_date = (await due_el.inner_text()).strip() if due_el else ""
        holds_count = (await holds_el.inner_text()).strip() if holds_el else ""

        status = _map_status(raw_status, due_date=due_date, holds=holds_count)

        # Deduplicate: keep best status per branch (Available > Due Date > Holds)
        key = branch
        if key not in seen_branches:
            seen_branches.add(key)
            results.append({"library": LIBRARY_NAME, "branch": branch, "status": status})

    return results if results else [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]


def _map_status(raw: str, due_date: str = "", holds: str = "") -> str:
    """Translate raw BiblioCommons status text into our canonical labels."""
    lower = raw.lower()
    if "available" in lower and "not" not in lower:
        return "Available Now"
    if "checked out" in lower or "due" in lower:
        suffix = f" (Due: {due_date})" if due_date else ""
        return f"Checked Out{suffix}"
    if "hold" in lower or holds:
        count = holds if holds else raw
        return f"On Hold ({count} in queue)"
    if "on order" in lower or "in transit" in lower:
        return raw.title()
    return raw.strip() or "Unknown"


async def check_jc_library(title: str, author: str, page: Page) -> list[dict]:
    """
    Main entry point called by library_cli.py.

    Args:
        title:  Cleaned book title (no parenthetical series info).
        author: Author name from Goodreads RSS.
        page:   A Playwright Page object (browser context managed by caller).

    Returns:
        List of result dicts: [{library, branch, status}, ...]
        Returns a single "Not Found" entry if nothing matched.
    """
    url = _build_search_url(title, author)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    # ── Check for zero-results page ──────────────────────────────────────────
    no_results = await page.query_selector(
        "div.cp-no-results, h2.no-results-message, [data-testid='no-results']"
    )
    if no_results:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # ── Find the first result that matches our title ─────────────────────────
    # BiblioCommons results are <li> elements with class "cp-search-result-item"
    result_items = await page.query_selector_all(
        "li.cp-search-result-item, div.cp-search-result-item, li[data-testid='bib-list-item']"
    )

    if not result_items:
        # Page loaded but no result list found — navigate directly to availability
        # (sometimes BiblioCommons redirects straight to the bib page on exact match)
        current_url = page.url
        if "/item/" in current_url or "/record/" in current_url:
            return await _extract_availability(page, title)
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    target_item = None
    normalized_title = _normalize(title)

    for item in result_items:
        title_el = await item.query_selector(
            "span.title-content, a.cp-search-result-item-title, [data-testid='bib-title']"
        )
        if not title_el:
            continue
        item_title = _normalize(await title_el.inner_text())
        # Accept the item if our cleaned title appears anywhere in the result title
        if normalized_title in item_title or item_title in normalized_title:
            target_item = item
            break

    # If no exact-ish match, fall back to the first result
    if target_item is None and result_items:
        target_item = result_items[0]

    if target_item is None:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # Click through to the bib detail page
    link_el = await target_item.query_selector(
        "a.cp-search-result-item-title, a[data-testid='bib-title-link'], span.title-content a"
    )
    if link_el:
        try:
            await link_el.click()
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightTimeoutError:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]
    else:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    return await _extract_availability(page, title)
