"""
South Orange Public Library scraper.

Catalog: Vega Discover (Innovative Interfaces) running on the shared
         BCCLS (Bergen County Cooperative Library System) consortium.
Public:  https://sora.search.bccls.org/
API:     https://na4.iiivega.com/api/search-result/

How it works:
  Loading /search?query=<title> triggers the browser to POST to
  /api/search-result/search/format-groups, which returns each matching
  title with its material formats (Book, Large Print, Audio Book on CD,
  eAudiobook, ...) and an alphabetically-truncated list of branches
  carrying each format.

  We intercept that response (no need to call the API ourselves — the
  page already issues the request with the correct iii-* / anonymous-
  user-id headers, which a hand-rolled call would have to reproduce).

  Truncation caveat:
    materialTabs[].locations[] returns up to 6 branches sorted
    alphabetically. South Orange (S) typically falls outside the first
    6, so we cannot always tell "not at SO" from "at SO but not in the
    truncated list". When the SO label is present we report a definitive
    status; otherwise we surface a "Possibly at SO — N BCCLS copies"
    row so the user knows a hold request is viable.
"""

import re
import urllib.parse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

CATALOG_BASE     = "https://sora.search.bccls.org"
LIBRARY_NAME     = "South Orange (SOPL)"
SO_BRANCH_LABEL  = "South Orange Public Library"

# Branch label looks like "South Orange -  South Orange Public Library"
# (with one or two spaces around the dash). Matches loosely.
_SO_RE = re.compile(r"south orange\s*[-—]\s*south orange public library", re.IGNORECASE)

# Material tab name keywords that count as a borrowable physical book.
_PHYSICAL_BOOK_KEYWORDS = ("book", "large print", "large type")
# Excluded even if "book" appears (Audio Book on CD, eBook, etc.)
_EXCLUDE_KEYWORDS       = ("audio", "ebook", "e-book", "dvd", "blu-ray", "blu ray", "video", "music", "kit")


# ── Text helpers ──────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


def _title_matches(catalog_text: str, search_title: str) -> bool:
    STOP = {"a", "an", "the"}
    nc = _normalize(catalog_text)
    nt = _normalize(search_title)
    if nt in nc:
        return True
    tw = [w for w in nt.split() if w not in STOP][:5]
    cw = [w for w in nc.split() if w not in STOP]
    return bool(tw) and cw[:len(tw)] == tw


def _is_physical_book(tab: dict) -> bool:
    if tab.get("type") != "physical":
        return False
    name = (tab.get("name") or "").lower()
    if any(x in name for x in _EXCLUDE_KEYWORDS):
        return False
    return any(k in name for k in _PHYSICAL_BOOK_KEYWORDS)


def _map_status(raw: str) -> str:
    s = (raw or "").lower()
    if "unavailable" in s:
        return "Unavailable"
    if "available" in s:
        return "Available Now"
    if "checked out" in s:
        return "Checked Out"
    if "hold" in s:
        return "On Hold"
    if "transit" in s:
        return "In Transit"
    if "missing" in s or "lost" in s:
        return "Missing / Lost"
    return raw or "Unknown"


# ── Result extraction ────────────────────────────────────────────────────────

def _extract_so_results(search_response: dict, search_title: str) -> list[dict]:
    """Walk the format-groups response and return SO availability rows."""
    so_rows: list[dict] = []
    seen_so: set[tuple] = set()
    bccls_copies: list[int] = []  # SO not in listed locations, but other BCCLS branches have it
    any_physical = False

    for item in search_response.get("data", []):
        if not _title_matches(item.get("title", ""), search_title):
            continue
        for tab in item.get("materialTabs", []):
            if not _is_physical_book(tab):
                continue
            any_physical = True

            locations = tab.get("locations") or []
            so_loc = next((l for l in locations if _SO_RE.search(l.get("label", ""))), None)

            if so_loc:
                status = _map_status(so_loc.get("availabilityStatus", ""))
                key = (status,)
                if key not in seen_so:
                    seen_so.add(key)
                    so_rows.append({
                        "library": LIBRARY_NAME,
                        "branch":  SO_BRANCH_LABEL,
                        "status":  status,
                    })
            else:
                total = tab.get("locationsTotalResults") or len(locations)
                if total > 0:
                    bccls_copies.append(total)

    if so_rows:
        return so_rows

    if any_physical and bccls_copies:
        # Truncation may be hiding SO. Surface a soft "possibly" row.
        return [{
            "library": LIBRARY_NAME,
            "branch":  "BCCLS (request)",
            "status":  f"Possibly at SO — {max(bccls_copies)} BCCLS copies",
        }]

    if any_physical:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not at SO"}]

    return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]


# ── Public API ────────────────────────────────────────────────────────────────

async def check_so_library(title: str, author: str, page: Page) -> list[dict]:
    """Search the BCCLS Vega Discover catalog and report South Orange availability."""

    search_url = (
        f"{CATALOG_BASE}/search?"
        f"query={urllib.parse.quote_plus(title)}&searchType=everything"
    )

    captured: dict = {}

    async def on_response(resp):
        # The page issues several POSTs; we want the main format-groups search.
        if (
            "search/format-groups" in resp.url
            and resp.request.method == "POST"
            and resp.status == 200
            and "data" not in captured
        ):
            post = resp.request.post_data or ""
            if '"resourceType":"FormatGroup"' in post and '"searchText"' in post:
                try:
                    captured["data"] = await resp.json()
                except Exception:
                    pass

    page.on("response", on_response)
    try:
        try:
            await page.goto(search_url, wait_until="networkidle", timeout=40_000)
        except PlaywrightTimeoutError:
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeoutError:
                return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Timeout"}]

        # Search response sometimes lands a beat after networkidle fires.
        for _ in range(20):
            if "data" in captured:
                break
            await page.wait_for_timeout(250)
    finally:
        page.remove_listener("response", on_response)

    if "data" not in captured:
        return [{"library": LIBRARY_NAME, "branch": "N/A", "status": "Not Found"}]

    return _extract_so_results(captured["data"], title)
