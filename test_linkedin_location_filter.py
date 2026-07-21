"""
test_linkedin_location_filter.py

Verify LinkedIn people search with location applied via the UI location filter
(click filter → type location → select → Show results).

This mirrors exactly what you do manually in the browser — no geoUrn IDs needed.

This script does NOT modify linkedin_search_connector.py.

Example:
    venv/bin/python test_linkedin_location_filter.py
    venv/bin/python test_linkedin_location_filter.py --position CEO --location Bahrain

Run from project root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import quote, unquote, urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.config.database import SessionLocal
from app.models.linkedin_search_config import LinkedinSearchConfig
from app.models.profile_setting import ProfileSetting
from app.scraper.linkedin_finder import HEADLESS, LinkedInSessionExpiredError, _check_session, _rand_delay


RESULTS_WAIT_TIMEOUT = 15000


# ── URL helpers (for comparison display only) ──────────────────────────────────

def build_keyword_search_url(position: str, location: str) -> str:
    """Current production approach — location embedded in keywords."""
    return (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote(f'{position} {location}')}"
        f"&origin=GLOBAL_SEARCH_HEADER"
    )


def build_position_only_search_url(position: str) -> str:
    """Starting point before applying the location filter via UI."""
    return (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote(position)}"
        f"&origin=GLOBAL_SEARCH_HEADER"
    )


# ── UI: apply location filter ──────────────────────────────────────────────────

def _dismiss_popups(page) -> None:
    for sel in (
        "button[aria-label='Dismiss']",
        "button.artdeco-modal__dismiss",
        "button:has-text('Dismiss')",
    ):
        try:
            for btn in page.locator(sel).all()[:2]:
                if btn.is_visible(timeout=300):
                    btn.click()
                    time.sleep(0.3)
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def apply_location_filter_via_ui(page, location: str, position: str) -> None:
    """
    Apply location through LinkedIn's filter UI — same steps as a manual search:
      1. Open people search with position keyword only
      2. Click All filters (or Locations pill)
      3. Type location name and pick the typeahead match
      4. Click Show results
    """
    search_url = build_position_only_search_url(position)
    print(f"\n  Step 1 — Open people search (position only):\n  {search_url}\n")

    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    _rand_delay(2, 3)
    _check_session(page)
    _dismiss_popups(page)

    print(f"  Step 2 — Open location filter panel...")
    opened = False
    for sel in (
        "button.search-reusables__all-filters-pill-button",
        "button:has-text('All filters')",
        "button[aria-label*='Locations']",
        "button:has-text('Locations')",
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=3000):
                btn.click(force=True)
                _rand_delay(1, 1.5)
                opened = True
                print(f"    Clicked: {sel}")
                break
        except Exception:
            continue

    if not opened:
        page.screenshot(path="failed_open_filters.png")
        raise RuntimeError("Could not open LinkedIn location filter UI")

    print(f"  Step 3 — Type location '{location}' and select match...")
    
    # Check if there is an "+ Add a location" button we need to click first
    for btn_sel in (
        "button:has-text('Add a location')",
        "button[aria-label*='Add a location']",
        "button.search-reusables__collection-filter-show-more",
    ):
        try:
            add_btn = page.locator(btn_sel).first
            if add_btn.count() > 0 and add_btn.is_visible(timeout=1000):
                add_btn.click(force=True)
                _rand_delay(0.5, 1)
                break
        except Exception:
            pass

    loc_input = None
    for sel in (
        "input[placeholder*='Add a location']",
        "input[placeholder*='location']",
        "input[aria-label*='Location']",
        "input[aria-label*='location']",
        "div[role='dialog'] input[type='text']",
        "div.search-reusables__filters-bar input[type='text']",
    ):
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=2000):
                loc_input = el
                break
        except Exception:
            continue

    if not loc_input:
        raise RuntimeError("Could not find location input in LinkedIn filter UI")

    loc_input.click()
    loc_input.fill("")
    loc_input.type(location, delay=80)
    _rand_delay(1.5, 2)

    selected = False
    selected_label = ""
    # We want to match an option that appeared after typing.
    # It might be a typeahead hit or a checkbox, but we shouldn't just grab ANY checkbox (like '1st' connections).
    for sel in (
        "div.basic-typeahead__selectable:visible",
        "li.basic-typeahead__selectable:visible",
        "div.search-typeahead-v2__hit:visible",
        "div[role='option']:visible",
        f"div[role='checkbox']:has-text('{location}')",
        f"div[role='checkbox']:has-text('{location.title()}')",
    ):
        try:
            # wait a bit for typeahead to appear
            opt = page.locator(sel).first
            if opt.count() > 0:
                opt.wait_for(state="visible", timeout=3000)
                selected_label = (opt.inner_text() or "").strip().split("\n")[0]
                opt.click(force=True)
                selected = True
                _rand_delay(0.5, 1)
                break
        except Exception:
            continue

    if not selected:
        raise RuntimeError(f"No typeahead match found for location '{location}'")

    print(f"    Selected: {selected_label or location}")

    print("  Step 4 — Apply filter (Show results)...")
    clicked_show = page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('button, div[role="button"], a, span[role="button"]'));
            for (const b of btns) {
                const txt = (b.innerText || '').toLowerCase().trim();
                if (txt === 'show results' || txt.includes('show results') || txt.includes('apply current filters')) {
                    if (b.offsetParent !== null) { // visible
                        b.click();
                        return true;
                    }
                }
            }
            return false;
        }
    """)
    if clicked_show:
        _rand_delay(2, 3)
    else:
        # Fallback to pressing Enter
        page.keyboard.press("Enter")
        _rand_delay(2, 3)
        # We don't raise an error here because Enter might have worked, let's just proceed and let the assertions verify
        print("    ⚠️  Could not click 'Show results' via JS, tried pressing Enter")

    # Wait for results to reload with filter applied
    if not _wait_for_results(page):
        print("    ⚠️ Results didn't load immediately, reloading page with applied filter...")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        _wait_for_results(page)
    print("  ✅ Location filter applied via UI")


# ── Verification helpers ─────────────────────────────────────────────────────

def _wait_for_results(page) -> bool:
    try:
        page.wait_for_selector(
            "ul.reusable-search__entity-result-list, "
            "div[role='listitem']:has(a[href*='/in/']), "
            "div.search-no-results",
            timeout=RESULTS_WAIT_TIMEOUT,
        )
        return True
    except PlaywrightTimeout:
        return False


def _get_search_input_value(page) -> str:
    for sel in (
        "input[aria-label*='Search']",
        "input.search-global-typeahead__input",
        "input[placeholder*='Search']",
    ):
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=1000):
                val = el.input_value(timeout=1000) or el.get_attribute("value") or ""
                if val.strip():
                    return val.strip()
        except Exception:
            continue
    return ""


def _get_active_location_filter_text(page) -> str:
    """Read the active location filter chip (e.g. 'Qatar' in the screenshot)."""
    skip_words = (
        "1st", "2nd", "3rd", "seniority", "company", "industry",
        "all filters", "people", "connections", "follower",
        "current company", "past company", "school", "locations",
    )
    try:
        pills = page.locator(
            "div.search-reusables__filter-list button.search-reusables__filter-pill-button"
        ).all()
        for pill in pills:
            if not pill.is_visible(timeout=500):
                continue
            txt = (pill.inner_text() or "").strip()
            aria = (pill.get_attribute("aria-label") or "").strip()
            combined = f"{txt} {aria}".lower()
            if not txt or any(w in combined for w in skip_words):
                continue
            if len(txt) < 60:
                return txt
    except Exception:
        pass
    return ""


def _extract_result_locations(page, max_cards: int = 10) -> list[dict]:
    results: list[dict] = []
    cards = page.locator(
        "li.reusable-search__result-container, "
        "li[class*='result-container'], "
        "div[role='listitem']:has(a[href*='/in/'])"
    )
    count = min(cards.count(), max_cards)

    for i in range(count):
        card = cards.nth(i)
        try:
            name = ""
            name_el = card.locator("a[href*='/in/']").first
            if name_el.count() > 0:
                name = (name_el.inner_text() or "").strip().split("\n")[0]

            location_text = card.evaluate("""
                el => {
                    const selectors = [
                        '.entity-result__secondary-subtitle',
                        '.entity-result__summary',
                        'div[class*="secondary-subtitle"]',
                        'div[class*="primary-subtitle"]',
                    ];
                    for (const sel of selectors) {
                        const nodes = el.querySelectorAll(sel);
                        for (const n of nodes) {
                            const t = (n.innerText || '').trim();
                            if (t) return t;
                        }
                    }
                    const lines = (el.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean);
                    return lines.length > 1 ? lines[lines.length - 1] : '';
                }
            """) or ""
            results.append({"name": name, "location": location_text.strip()})
        except Exception:
            continue
    return results


def _location_matches(result_location: str, expected: str) -> bool:
    if not result_location:
        return False
    rl = result_location.lower()
    el = expected.lower()
    return el in rl or rl in el


def _parse_geo_urn_from_url(url: str) -> list[str]:
    parsed = parse_qs(urlparse(url).query)
    raw = unquote(parsed.get("geoUrn", [""])[0])
    return re.findall(r'"(\d+)"', raw)


def verify_ui_location_filter(
    page,
    position: str,
    location: str,
    *,
    screenshot_path: str | None = None,
) -> dict:
    """
    Apply location filter via UI and verify the page state matches expectations.
    """
    apply_location_filter_via_ui(page, location, position)

    for _ in range(2):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        _rand_delay(0.8, 1.2)

    current_url = page.url
    search_input = _get_search_input_value(page)
    filter_chip = _get_active_location_filter_text(page)
    result_cards = _extract_result_locations(page)
    url_geo_urns = _parse_geo_urn_from_url(current_url)

    if screenshot_path:
        page.screenshot(path=screenshot_path, full_page=False)
        print(f"\n  📸 Screenshot saved: {screenshot_path}")

    checks = {
        "search_input_position_only": (
            location.lower() not in search_input.lower()
            and position.lower() in search_input.lower()
        ),
        "location_filter_chip_visible": bool(filter_chip),
        "location_filter_chip_matches": (
            _location_matches(filter_chip, location) if filter_chip else False
        ),
        "url_has_geo_urn": len(url_geo_urns) > 0,
        "results_loaded": len(result_cards) > 0,
        "results_match_location": (
            sum(1 for r in result_cards if _location_matches(r["location"], location))
            >= max(1, len(result_cards) // 2)
            if result_cards else False
        ),
    }

    return {
        "url": current_url,
        "search_input": search_input,
        "filter_chip": filter_chip,
        "geo_urns": url_geo_urns,
        "result_cards": result_cards,
        "checks": checks,
        "passed": all(checks.values()),
    }


# ── Output ─────────────────────────────────────────────────────────────────────

def print_separator(title: str = "") -> None:
    print("\n" + "=" * 64)
    if title:
        print(f"  {title}")
        print("=" * 64)


def print_approach_comparison(position: str, location: str) -> None:
    print_separator("Approach Comparison")
    print("\n  ❌ Current production (location in search bar):")
    print(f"     {build_keyword_search_url(position, location)}")
    print("\n  ✅ Proposed (UI location filter — what this test does):")
    print(f"     1. Open: {build_position_only_search_url(position)}")
    print(f"     2. Click location filter → type '{location}' → select → Show results")
    print("     3. Search bar keeps position only; location appears as filter chip")


def print_verification_report(result: dict, location: str) -> None:
    print_separator("Verification Report")
    checks = result["checks"]

    labels = {
        "search_input_position_only": "Search bar has position only (no location keyword)",
        "location_filter_chip_visible": "Location filter chip is visible",
        "location_filter_chip_matches": f"Location filter chip matches '{location}'",
        "url_has_geo_urn": "LinkedIn added geoUrn to URL (filter is active)",
        "results_loaded": "Search results loaded",
        "results_match_location": f"Result cards mostly match location '{location}'",
    }

    for key, label in labels.items():
        ok = checks.get(key, False)
        print(f"  {'✅' if ok else '❌'}  {label}")

    print(f"\n  Search bar value : '{result['search_input']}'")
    print(f"  Filter chip text : '{result['filter_chip']}'")
    if result["geo_urns"]:
        print(f"  geoUrn in URL    : {result['geo_urns']} (set automatically by LinkedIn)")
    print(f"  Current URL      : {result['url'][:120]}...")

    if result["result_cards"]:
        print("\n  Sample results:")
        for card in result["result_cards"][:5]:
            match = "✓" if _location_matches(card["location"], location) else "✗"
            print(
                f"    [{match}] {(card['name'] or 'N/A'):<28} "
                f"| {card['location'] or '(no location text)'}"
            )

    print(f"\n  {'✅ ALL CHECKS PASSED' if result['passed'] else '❌ SOME CHECKS FAILED'}")
    print("=" * 64)


def load_defaults_from_config(db) -> tuple[str, str]:
    config = (
        db.query(LinkedinSearchConfig)
        .filter(LinkedinSearchConfig.is_active == True)  # noqa: E712
        .first()
    )
    if not config:
        return "CEO", "Bahrain"
    positions = config.positions or ["CEO"]
    position = positions[0] if positions else "CEO"
    location = config.location or "Bahrain"
    return position, location


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify LinkedIn location filter via UI clicks (not keyword search)."
    )
    parser.add_argument("--profile-id", type=int, default=1, help="ProfileSetting ID")
    parser.add_argument(
        "--position",
        default=None,
        help="Job title keyword (default: first position from active config, else 'CEO')",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Location name (default: from active LinkedinSearchConfig, else 'Bahrain')",
    )
    parser.add_argument(
        "--screenshot",
        default="test_location_filter_screenshot.png",
        help="Path to save a screenshot after applying the filter",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        help="Skip saving screenshot",
    )
    args = parser.parse_args()

    print_separator("LinkedIn Location Filter — UI Verification Test")
    print("  Applies location by clicking the filter UI (no geoUrn IDs needed).")

    db = SessionLocal()
    try:
        default_position, default_location = load_defaults_from_config(db)
        position = args.position or default_position
        location = args.location or default_location

        profile = db.query(ProfileSetting).filter(ProfileSetting.id == args.profile_id).first()
        if not profile or not profile.session_state:
            print(f"\n❌  Profile {args.profile_id} not found or has no session.")
            print("    Run linkedin_login.py first to save a session.")
            sys.exit(1)

        print(f"\n  Profile  : {profile.name} (id={profile.id})")
        print(f"  Position : {position}")
        print(f"  Location : {location}")

        print_approach_comparison(position, location)

        session_dict = json.loads(profile.session_state)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=session_dict,
                viewport={"width": 1400, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
                _rand_delay(2, 3)
                _check_session(page)
                print("\n  ✅ Session valid")

                screenshot = None if args.no_screenshot else args.screenshot
                result = verify_ui_location_filter(
                    page,
                    position=position,
                    location=location,
                    screenshot_path=screenshot,
                )
                print_verification_report(result, location)

                if not result["passed"]:
                    sys.exit(1)

            except LinkedInSessionExpiredError:
                print("\n❌  LinkedIn session expired — re-run linkedin_login.py")
                sys.exit(1)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    except json.JSONDecodeError:
        print(f"\n❌  Profile {args.profile_id} session state is invalid JSON.")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
