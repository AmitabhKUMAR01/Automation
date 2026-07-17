"""
browser_utils.py
================
Shared browser helpers used by all LinkedIn scrapers.

Provides:
  - pick_fingerprint()    : random OS-platform + viewport (Chrome/124 version kept)
  - new_stealth_page()    : creates a page with playwright-stealth applied (if available)
"""
import random
from playwright.sync_api import BrowserContext, Page

try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# Chrome/124 version intentionally kept — matches the Playwright-bundled Chromium binary.
# Rotating the UA version without upgrading the binary creates a TLS fingerprint mismatch.
# Only the OS platform and viewport vary to break the constant Linux / 1400x900 fingerprint.
_FINGERPRINT_POOL = [
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1920, "height": 1080},
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1366, "height": 768},
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1440, "height": 900},
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1536, "height": 864},
    },
]


def pick_fingerprint() -> dict:
    """
    Return a randomly chosen fingerprint dict with 'user_agent' and 'viewport' keys.
    Chrome/124 version is fixed; OS platform and viewport dimensions vary per session.
    """
    return random.choice(_FINGERPRINT_POOL)


def new_stealth_page(context: BrowserContext) -> Page:
    """
    Create a new page and apply playwright-stealth patches if the library is installed.
    Patches navigator.webdriver, WebGL renderer, Canvas fingerprint, and other
    automation signals that LinkedIn's JS probes actively check.

    Falls back to a plain context.new_page() if playwright-stealth is not available —
    all other anti-ban fixes still apply in that case.
    """
    page = context.new_page()
    if STEALTH_AVAILABLE:
        stealth_sync(page)
    return page
