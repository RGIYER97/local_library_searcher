"""
Jersey City Free Public Library scraper.

Catalog: Sirsi Enterprise (SirsiDynix Symphony) v5.2
Base:    https://jepl.ent.sirsi.net/client/en_US/default/

Sirsi Enterprise quirks discovered via live Playwright DOM inspection:

  Search results page (multiple hits):
    - Title links: <a id="detailLink0" class="hideIE" href="#"
                      onclick="checkBeforeLoadingDetail(... 'SD_ILS:NNNNN' ...);">
    - href is always "#"; the ILS record ID is inside the onclick attr.
    - Results count: <div class="resultsToolbar_num_results">5 Results Found</div>

  Single-hit auto-redirect:
    - When a search matches exactly ONE physical book, Sirsi skips the results
      page and renders the DETAIL page directly. The page title becomes the
      book title, and the holdings table is already present.

  Detail page:
    - Holdings table: <table class="detailItemTable sortable0 sortable">
      Columns: Library | Shelf Location | Material Type | Shelf Number | Status
    - Table of Contents: <table class="tocTable fullwidth"> (must be skipped)
    - Holdings are loaded by a secondary AJAX call — cells initially show
      "Searching..." placeholders that get replaced once the AJAX resolves.

  Detail URL pattern:
    /search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:{ID}/one
"""

import re
import urllib.parse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

CATALOG_BASE  = "https://jepl.ent.sirsi.net"
SEARCH_PATH   = "/client/en_US/default/search/results"
DETAIL_PATH   = "/client/en_US/default/search/detailnonmodal/ent:$002f$002fSD_ILS$002f0$002fSD_ILS:{ils_id}/one"
FORMAT_FILTER = "qf=FORMAT%09Format%09BOOK%09Books"

LIBRARY_NAME = "Jersey City (JCFPL)"

_ILS_ID_RE = re.compile(r"SD_ILS[:%]3A(\d+)")

# Columns in the detailItemTable (verified by DOM inspection)
COL_LIBRARY       = 0
COL_SHELF_LOC     = 1
COL_MATERIAL_TYPE = 2
COL_CALL_NUMBER   = 3
COL_STATUS        = 4


# ── URL helpers ───────────────────────────────────────────────────────────────

def _build_search_url(title: str) -> str:
    encoded = urllib.parse.quote_plus(title)
    return f"{CATALOG_BASE}{SEARCH_PATH}?qu={encoded}&{FORMAT_FILTER}"


def _build_detail_url(ils_id: str) -> str:
    return f"{CATALOG_BASE}{DETAIL_PATH.format(ils_id=ils_id)}"


# ── Text helpers ──────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


def _strip_searching(text: str) -> str:
    return re.sub(r"^Searching\.\.\.", "", text).strip()


def _clean_status(raw: str) -> str:
    """
    Map raw Sirsi status text to a canonical label.

    Sirsi quirk: when a book is on the shelf, the Status column shows the
    shelf location name (e.g. "Non-Fiction Books", "New Books") instead of
    a literal "Available". So any status that doesn't match a known
    unavailable pattern is treated as Available Now.
    """
    s = _strip_searching(raw).lower()
    if not s or s == "unknown":
        return "Unknown"
    if "due" in s:
        date_part = _strip_searching(raw).strip()
        return f"Checked Out ({date_part})" if date_part else "Checked Out"
    if "checked out" in s:
        return "Checked Out"
    if "hold" in s:
        return "On Hold"
    if "on order" in s:
        return "On Order"
    if "in transit" in s:
        return "In Transit"
    if "missing" in s or "lost" in s:
        return "Missing / Lost"
    # Everything else — including shelf location names like "Non-Fiction Books",
    # "New Books", "Fiction Books" — means the item is on the shelf.
    return "Available Now"


def _title_matches(catalog_text: str, search_title: str) -> bool:
    STOP = {"a", "an", "the"}
    nc = _normalize(catalog_text)
    nt = _normalize(search_title)
    if nt in nc:
        return True
    tw = [w for w in nt.split() if w not in STOP][:5]
    cw = [w for w in nc.split() if w not in STOP]
    return bool(tw) and cw[:len(tw)] == tw


# ── Holdings extraction (detail page) ────────────────────────────────────────

async def _wait_for_holdings_ajax(page: Page) -> None:
    """Block until the 'Searching...' placeholders in the holdings table clear."""
    try:
        await page.wait_for_function(
            """() => {
                const table = document.querySelector('table.detailItemTable');
                if (!table) return false;
                const cells = table.querySelectorAll('td');
                if (cells.length === 0) return false;
                return !Array.from(cells).some(
                    td => td.textContent.trim().startsWith('Searching...')
                );
            }""",
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        pass


async def _extract_holdings(page: Page) -> list[dict]:
    """
    Parse the holdings table (table.detailItemTable) on a Sirsi detail page.
    Explicitly ignores table.tocTable (Table of Contents).
    """
    try:
        await page.wait_for_selector("table.detailItemTable", timeout=15_000)
    except PlaywrightTimeoutError:
        body = await page.inner_text("body")
        if "available" in body.lower():
            return [{"library": LIBRARY_NAME, "branch": "See catalog", "status": "Available Now"}]
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    await _wait_for_holdings_ajax(page)

    # Only grab rows from the holdings table, never the ToC table
    rows = await page.query_selector_all("table.detailItemTable tr")
    results = []
    seen: set[str] = set()

    for row in rows:
        cells = await row.query_selector_all("td")
        # Holdings table has 5 columns; skip the header row (which uses <th>)
        if len(cells) < 5:
            continue

        branch = _strip_searching((await cells[COL_LIBRARY].inner_text()).strip())
        if not branch or branch.lower() == "library":
            continue

        material = (await cells[COL_MATERIAL_TYPE].inner_text()).strip().lower()
        if any(x in material for x in ["ebook", "e-book", "eaudio", "audiobook"]):
            continue

        raw_status = (await cells[COL_STATUS].inner_text()).strip()
        status = _clean_status(raw_status)

        if status == "Unknown" and "searching" in raw_status.lower():
            continue

        if branch not in seen:
            seen.add(branch)
            results.append({"library": LIBRARY_NAME, "branch": branch, "status": status})

    return results if results else [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]


def _is_detail_page(page: Page, title: str) -> bool:
    """
    Check whether Sirsi auto-redirected to a detail page (single-result case).
    The URL won't contain 'results' anymore, or the page title will match the book.
    """
    url = page.url.lower()
    return "detailnonmodal" in url or "detail" in url


# ── Public API ────────────────────────────────────────────────────────────────

async def check_jc_library(title: str, author: str, page: Page) -> list[dict]:
    """
    Search the JCFPL Sirsi Enterprise catalog for a physical book.

    Strategy:
      1. Navigate to the search URL (title-only, Books format pre-filtered).
      2. Check if Sirsi auto-redirected to a detail page (single-match case).
         If so, jump straight to holdings extraction.
      3. Otherwise, find result title links (a[id^='detailLink']), match ours,
         extract the ILS ID from onclick, and navigate to the detail URL.
      4. Wait for the holdings AJAX and scrape table.detailItemTable.
    """
    url = _build_search_url(title)

    try:
        await page.goto(url, wait_until="networkidle", timeout=40_000)
    except PlaywrightTimeoutError:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    # ── Case 1: Sirsi auto-redirected to a detail page (single result) ───────
    if _is_detail_page(page, title):
        return await _extract_holdings(page)

    # Check if the detail table is already on the page (some single-result
    # layouts render the detail inline without changing the URL)
    detail_table = await page.query_selector("table.detailItemTable")
    if detail_table:
        await _wait_for_holdings_ajax(page)
        return await _extract_holdings(page)

    # ── Case 2: Search results page ──────────────────────────────────────────
    try:
        await page.wait_for_selector(
            "a[id^='detailLink'], div.resultsToolbar_num_results",
            timeout=20_000,
        )
    except PlaywrightTimeoutError:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # Check for "0 Results Found"
    num_el = await page.query_selector("div.resultsToolbar_num_results")
    if num_el:
        num_text = (await num_el.inner_text()).strip()
        if num_text.startswith("0 "):
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # ── Collect title links ──────────────────────────────────────────────────
    detail_links = await page.query_selector_all("a[id^='detailLink']")
    if not detail_links:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # ── Match title and extract ILS ID ───────────────────────────────────────
    best_ils_id = None
    for link in detail_links:
        link_text = await link.inner_text()
        onclick = await link.get_attribute("onclick") or ""
        if _title_matches(link_text, title):
            m = _ILS_ID_RE.search(onclick)
            if m:
                best_ils_id = m.group(1)
                break

    if best_ils_id is None and detail_links:
        onclick = await detail_links[0].get_attribute("onclick") or ""
        m = _ILS_ID_RE.search(onclick)
        if m:
            best_ils_id = m.group(1)

    if best_ils_id is None:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    # ── Navigate to detail page ──────────────────────────────────────────────
    detail_url = _build_detail_url(best_ils_id)
    try:
        await page.goto(detail_url, wait_until="networkidle", timeout=40_000)
    except PlaywrightTimeoutError:
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

    return await _extract_holdings(page)
