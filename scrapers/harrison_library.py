"""
Harrison, NJ Public Library scraper.

The Harrison Public Library catalog is hosted via the Essex County Library
Cooperative or a standalone ILS reachable through townofharrison.com.

Catalog system: Polaris ILS (PowerPAC interface), typical for NJ municipal libraries.
Base search URL: https://www.harrisonnjlibrary.org  (verify and update if needed)

NOTE: If the catalog URL has changed or the selectors below stop working, open
the catalog in a browser, perform a manual search, and update:
  - CATALOG_BASE
  - SEARCH_URL_TEMPLATE
  - The CSS selectors inside _find_results() and _extract_availability()
"""

import re
from typing import Optional
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError

# ── Configuration ────────────────────────────────────────────────────────────
# Harrison Public Library uses the Innovative Interfaces / Sierra catalog
# accessible via the NJ state library network. Update this URL to match
# the live catalog (check townofharrison.com → Library link).
CATALOG_BASE = "https://catalog.njelibrary.org"   # NJ e-library cooperative fallback
HARRISON_CATALOG = "https://www.harrisonnjlibrary.org"

# Many small NJ libraries use OPAC systems like Destiny, Koha, or a hosted
# BiblioCommons/Polaris portal. The template below targets a generic Koha
# OPAC which is very common for NJ municipal libraries at this scale.
# Pattern: /cgi-bin/koha/opac-search.pl?q=ti:TITLE+au:AUTHOR&limit=mc-itemtype:BK
KOHA_SEARCH_TEMPLATE = (
    "{base}/cgi-bin/koha/opac-search.pl"
    "?q=ti%3A{title}+au%3A{author}"
    "&limit=mc-itemtype%3ABK"           # BK = physical book, excludes eBook/audiobook
    "&sort_by=relevance"
)

LIBRARY_NAME = "Harrison, NJ"


def _encode(text: str) -> str:
    return text.strip().replace(" ", "+").replace("&", "%26").replace(",", "%2C")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


def _map_status(raw: str, due_date: str = "", holds: str = "") -> str:
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
    if "not available" in lower or "lost" in lower or "missing" in lower:
        return "Unavailable"
    return raw.strip() or "Unknown"


# ── Koha OPAC scraper ────────────────────────────────────────────────────────

async def _try_koha(page: Page, title: str, author: str, base: str) -> Optional[list[dict]]:
    """
    Attempt to scrape a Koha OPAC catalog instance.
    Returns None if the page doesn't look like Koha (so caller can try other approach).
    """
    url = KOHA_SEARCH_TEMPLATE.format(
        base=base, title=_encode(title), author=_encode(author)
    )
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        return None

    # Verify this is a Koha page
    is_koha = await page.query_selector("#doc-head, #koha_url, div.koha-results")
    if not is_koha:
        return None

    # Zero results
    no_results = await page.query_selector("div#noresultsmsg, p.noresults")
    if no_results:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # Result list items
    result_items = await page.query_selector_all("li.search-result-item, div.record")
    if not result_items:
        return None  # Unknown page state; let caller fall back

    normalized_title = _normalize(title)
    target = None
    for item in result_items:
        title_el = await item.query_selector("a.title, span.title, h3.biblionumber a")
        if not title_el:
            continue
        item_title = _normalize(await title_el.inner_text())
        if normalized_title in item_title or item_title in normalized_title:
            target = item
            break

    if target is None and result_items:
        target = result_items[0]
    if target is None:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # Click the title link to go to the bib detail page
    link_el = await target.query_selector("a.title, h3.biblionumber a")
    if link_el:
        try:
            await link_el.click()
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightTimeoutError:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]
    else:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    return await _extract_koha_availability(page)


async def _extract_koha_availability(page: Page) -> list[dict]:
    """Parse the Koha holdings table on a bib detail page."""
    results = []

    try:
        await page.wait_for_selector(
            "table#holdingst, table.holdings-table, div#holdings", timeout=10_000
        )
    except PlaywrightTimeoutError:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    rows = await page.query_selector_all(
        "table#holdingst tbody tr, table.holdings-table tbody tr"
    )

    if not rows:
        # Simplified availability badge (some Koha installs)
        avail_el = await page.query_selector("span.available-items, div.availability")
        if avail_el:
            text = (await avail_el.inner_text()).strip()
            return [{"library": LIBRARY_NAME, "branch": "Harrison Public Library", "status": _map_status(text)}]
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    seen = set()
    for row in rows:
        # Koha holdings columns: Library | Call # | Status | ...
        cells = await row.query_selector_all("td")
        if len(cells) < 3:
            continue

        branch_text = (await cells[0].inner_text()).strip()
        status_text = (await cells[2].inner_text()).strip()

        # Filter to physical books only — skip items whose call number / type
        # contains eBook/audiobook markers
        item_type_el = await row.query_selector("td.item_type, span.item-type")
        if item_type_el:
            item_type = (await item_type_el.inner_text()).lower()
            if any(x in item_type for x in ["ebook", "audio", "digital", "online"]):
                continue

        due_el = await row.query_selector("span.date-due, td.date-due")
        due_text = (await due_el.inner_text()).strip() if due_el else ""

        status = _map_status(status_text, due_date=due_text)
        key = branch_text
        if key not in seen:
            seen.add(key)
            results.append({"library": LIBRARY_NAME, "branch": branch_text, "status": status})

    return results if results else [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]


# ── Generic fallback: direct URL scraper ─────────────────────────────────────

async def _try_harrison_direct(page: Page, title: str, author: str) -> list[dict]:
    """
    Fallback scraper that navigates to the Harrison library website,
    finds the catalog search form, and submits a query.

    This handles catalog systems where the URL isn't easily constructable
    (e.g., Polaris PowerPAC, Destiny, or a proprietary portal).
    """
    try:
        await page.goto(HARRISON_CATALOG, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    # Look for a catalog search input
    search_input = await page.query_selector(
        "input[name='q'], input[name='search'], input[type='search'], "
        "input[placeholder*='search' i], input[placeholder*='title' i], "
        "input#search-query, input.catalog-search-input"
    )

    if search_input is None:
        # Try to find a link to the catalog from the library home page
        catalog_link = await page.query_selector(
            "a[href*='catalog'], a[href*='opac'], a[href*='search'], "
            "a:has-text('Catalog'), a:has-text('Search Our Collection')"
        )
        if catalog_link:
            try:
                await catalog_link.click()
                await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                search_input = await page.query_selector(
                    "input[name='q'], input[name='search'], input[type='search'], "
                    "input[placeholder*='search' i], input[placeholder*='title' i]"
                )
            except PlaywrightTimeoutError:
                pass

    if search_input is None:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Catalog Not Found — Update URL"}]

    # Type the title into the search box and submit
    await search_input.click()
    await search_input.fill(f"{title} {author}")
    await search_input.press("Enter")

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except PlaywrightTimeoutError:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    # After search, look for results in any common OPAC format
    result_items = await page.query_selector_all(
        "li.search-result-item, div.result-item, div.record, tr.search-result, "
        "li[data-testid='bib-list-item'], div.cp-search-result-item"
    )

    if not result_items:
        no_results = await page.query_selector(
            "div.no-results, p.noresults, div#noresultsmsg, span:has-text('No results')"
        )
        if no_results:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]
        # Might have landed directly on a bib page
        return await _extract_generic_availability(page)

    # Find the best title match
    normalized_title = _normalize(title)
    target = None
    for item in result_items:
        title_el = await item.query_selector(
            "a.title, span.title, h3 a, a.cp-search-result-item-title"
        )
        if not title_el:
            continue
        item_title = _normalize(await title_el.inner_text())
        if normalized_title in item_title or item_title in normalized_title:
            target = item
            break

    if target is None and result_items:
        target = result_items[0]

    if target is None:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    link_el = await target.query_selector("a.title, h3 a, a.cp-search-result-item-title")
    if link_el:
        try:
            await link_el.click()
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightTimeoutError:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    return await _extract_generic_availability(page)


async def _extract_generic_availability(page: Page) -> list[dict]:
    """
    Generic availability extractor: tries several common OPAC patterns.
    Works for Koha, Polaris, Sierra, BiblioCommons, and Destiny.
    """
    results = []

    # Pattern 1: Koha / Sierra holdings table
    rows = await page.query_selector_all(
        "table#holdingst tbody tr, table.holdings-table tbody tr, "
        "table.availability tbody tr"
    )
    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 2:
            continue

        branch_text = (await cells[0].inner_text()).strip()
        status_idx = 2 if len(cells) > 2 else 1
        status_text = (await cells[status_idx].inner_text()).strip()

        # Skip digital formats
        row_text = (await row.inner_text()).lower()
        if any(x in row_text for x in ["ebook", "e-book", "audiobook", "audio book", "digital"]):
            continue

        due_el = await row.query_selector("span.date-due, td.date-due")
        due_text = (await due_el.inner_text()).strip() if due_el else ""
        status = _map_status(status_text, due_date=due_text)
        results.append({"library": LIBRARY_NAME, "branch": branch_text, "status": status})

    if results:
        return results

    # Pattern 2: BiblioCommons-style availability items
    bc_items = await page.query_selector_all(
        "li.cp-availability-status__item, div.cp-availability-info"
    )
    for item in bc_items:
        branch_el = await item.query_selector(
            ".cp-availability-status__branch-name, span.branch-name"
        )
        status_el = await item.query_selector(
            ".cp-availability-status__label, span.availability-label"
        )
        branch = (await branch_el.inner_text()).strip() if branch_el else "Harrison Public Library"
        raw_status = (await status_el.inner_text()).strip() if status_el else "Unknown"
        results.append({"library": LIBRARY_NAME, "branch": branch, "status": _map_status(raw_status)})

    if results:
        return results

    # Pattern 3: Simple text badges / summary
    for selector in [
        "span.available, div.availability-status, span.item-status",
        "p.availability, div.holdings-summary",
    ]:
        el = await page.query_selector(selector)
        if el:
            text = (await el.inner_text()).strip()
            if text:
                results.append(
                    {"library": LIBRARY_NAME, "branch": "Harrison Public Library", "status": _map_status(text)}
                )
                return results

    return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]


# ── Public API ───────────────────────────────────────────────────────────────

async def check_harrison_library(title: str, author: str, page: Page) -> list[dict]:
    """
    Main entry point called by library_cli.py.

    Args:
        title:  Cleaned book title (no parenthetical series info).
        author: Author name from Goodreads RSS.
        page:   A Playwright Page object (browser context managed by caller).

    Returns:
        List of result dicts: [{library, branch, status}, ...]
        Returns a single "Not Found" entry if nothing matched.

    Strategy:
        1. Try Koha OPAC at the Harrison library base URL.
        2. Fall back to navigating the library website and finding the search form.
    """
    # First attempt: Koha OPAC (most common for NJ municipal libraries this size)
    koha_result = await _try_koha(page, title, author, HARRISON_CATALOG)
    if koha_result is not None:
        return koha_result

    # Second attempt: Navigate the site and use whatever search form exists
    return await _try_harrison_direct(page, title, author)
