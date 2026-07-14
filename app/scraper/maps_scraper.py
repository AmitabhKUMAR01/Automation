from app.utils.logger import logger
# maps_scraper.py
from playwright.sync_api import sync_playwright
import time
import re
from urllib.parse import urlparse, urlunparse

MIN_RESULTS = 10
MAX_SCROLL_ROUNDS = 20


def clean_url(url: str) -> str:
    ## Strip UTM and tracking params from a URL before visiting.
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def scrape_social_links(context, website_url: str) -> dict:
    ## Open a separate tab to scrape social links so it won't interrupt other navigations
    social = {
        "facebook": None,
        "instagram": None,
        "linkedin": None,
        "twitter": None,
        "whatsapp": None,
        "youtube": None,
        "tiktok": None,
    }

    SOCIAL_PATTERNS = {
        "facebook": r"https?://(www\.)?facebook\.com/(?!sharer|share|dialog|plugins|2008|tr\?|v\d)[a-zA-Z0-9._\-]+/?",
        "instagram": r"https?://(www\.)?instagram\.com/(?!p/|reel/|explore/)[a-zA-Z0-9._\-]+/?",
        "linkedin": r'https?://(www\.)?linkedin\.com/(company|in)/[^\s"\'<>/?]+/?',
        "twitter": r"https?://(www\.)?(twitter|x)\.com/(?!share|intent|home)[a-zA-Z0-9_]+/?",
        "whatsapp": r'https?://(wa\.me|api\.whatsapp\.com/send)[^\s"\'<>]*',
        "youtube": r'https?://(www\.)?youtube\.com/(channel|c|user|@)[^\s"\'<>/?]+/?',
        "tiktok": r"https?://(www\.)?tiktok\.com/@[a-zA-Z0-9._\-]+/?",
    }

    tab = context.new_page()
    try:
        clean = clean_url(website_url)
        tab.goto(clean, timeout=10000, wait_until="domcontentloaded")
        html = tab.content()

        for platform, pattern in SOCIAL_PATTERNS.items():
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                link = match.group(0).rstrip("/?")
                path = urlparse(link).path.strip("/")
                if path and len(path) > 2:
                    social[platform] = link

    except Exception as e:
        logger.info(f"Could not scrape social links from {website_url}: {e}")
    finally:
        tab.close()

    return social


# Noisy / non-business email patterns to skip
_EMAIL_BLACKLIST = re.compile(
    r"(example\.com|sentry\.io|wixpress\.com|schema\.org|\.png|\.(jpg|jpeg|gif|webp|svg|css|js)@)",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_email(context, website_url: str) -> str | None:
    """Scrape the business website for a contact email address.
    Checks mailto: links first (most reliable), then falls back to a
    plain-text regex scan of the page HTML.
    Returns the first clean, non-blacklisted email found, or None.
    """
    tab = context.new_page()
    try:
        clean = clean_url(website_url)
        tab.goto(clean, timeout=10000, wait_until="domcontentloaded")
        html = tab.content()

        # 1. Prefer explicit mailto: links — highest confidence
        for match in re.finditer(r'mailto:([^"\'>\s]+)', html, re.IGNORECASE):
            email = match.group(1).split("?")[0].strip()  # strip ?subject= etc.
            if _EMAIL_PATTERN.match(email) and not _EMAIL_BLACKLIST.search(email):
                return email

        # 2. Fallback: bare email addresses anywhere in the HTML
        for match in _EMAIL_PATTERN.finditer(html):
            email = match.group(0)
            if not _EMAIL_BLACKLIST.search(email):
                return email

    except Exception as e:
        logger.info(f"Could not scrape email from {website_url}: {e}")
    finally:
        tab.close()

    return None


def _extract_phone(page) -> str:
    ## Try multiple strategies to extract a phone number from the detail page
    phone_btn = page.query_selector('button[data-item-id*="phone"]')
    if phone_btn:
        phone_text = phone_btn.inner_text().strip()
        return phone_text.split("\n")[-1].strip() if "\n" in phone_text else phone_text

    phone_btn = page.query_selector('button[aria-label*="phone" i]')
    if phone_btn:
        label = phone_btn.get_attribute("aria-label") or ""
        return label.split(":", 1)[-1].strip() if ":" in label else label.strip()

    phone_pattern = re.compile(r"[\+\(]?[\d\s\-\(\)]{7,15}")
    for btn in page.query_selector_all("button"):
        try:
            data_id = btn.get_attribute("data-item-id") or ""
            aria = btn.get_attribute("aria-label") or ""
            if "phone" in data_id.lower() or "phone" in aria.lower():
                text = btn.inner_text().strip()
                match = phone_pattern.search(text)
                if match:
                    return match.group().strip()
        except Exception:
            pass
    return None


def _clean_maps_url(url: str) -> str:
    cleaned = re.sub(r"/@[\d\.\-]+,[\d\.\-]+,\d+z", "", url)
    return cleaned


def _extract_detail(page, context, place: dict) -> dict:
    ## Extract phone, address, website, rating, reviews, about, and social links from a place's Maps page
    try:
        detail_url = _clean_maps_url(place["maps_url"])
        page.goto(detail_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                'h1.DUwDvf, button[data-item-id*="phone"], button[data-item-id="address"], a[data-item-id="authority"], div.F7nice',
                timeout=10000,
            )
        except Exception:
            pass  # some places may not have all, continue anyway

        phone = _extract_phone(page)

        address = None
        address_btn = page.query_selector('button[data-item-id="address"]')
        if address_btn:
            a_text = address_btn.inner_text().strip()
            address = a_text.split("\n")[-1].strip() if "\n" in a_text else a_text

        website_link = page.query_selector('a[data-item-id="authority"]')
        website = website_link.get_attribute("href") if website_link else None
        
        rating = None

        f7nice = page.query_selector("div.F7nice")
        if f7nice:
            f7nice_text = f7nice.inner_text().strip()
            rating_match = re.search(r"(\d\.\d)", f7nice_text)
            if rating_match:
                rating = rating_match.group(1)

        ## About tab: extract business description
        about_info = {}
        try:
            buttons = page.query_selector_all("button[role='tab'], div[role='tab']")
            for btn in buttons:
                if 'about' in btn.inner_text().lower():
                    btn.click()
                    # Wait for the About panel content to fully render after tab click
                    page.wait_for_timeout(1500)

                    # Locate the scrollable About panel container
                    container = page.query_selector("div.m6QErb[aria-label*='About' i]") or \
                                page.query_selector("div[role='main'] .m6QErb")

                    if container:
                        # Scroll the panel to ensure description is loaded
                        for _ in range(8):
                            container.evaluate("el => el.scrollBy(0, 400)")
                            page.wait_for_timeout(350)

                        # Google renders the description in span.HlvSq
                        desc_el = container.query_selector("span.HlvSq")
                        if desc_el:
                            desc_text = desc_el.inner_text().strip()
                            if desc_text:
                                about_info["description"] = desc_text
                    break
        except Exception as e:
            logger.info(f"Error extracting about tab for {place['name']}: {e}")

        # Use separate tabs for email and social links so they won't interrupt this page
        email = _extract_email(context, website) if website else None
        social_links = scrape_social_links(context, website) if website else {}

        return {
            "name": place["name"],
            "url": website,
            "phone": phone,
            "email": email,
            "address": address,
            "rating": rating,
            "about": about_info,
            "social_links": social_links,
        }

    except Exception as e:
        logger.info(f"Error extracting details for {place['name']}: {e}")
        return {
            "name": place["name"],
            "url": None,
            "phone": None,
            "email": None,
            "address": None,
            "rating": None,
            "about": [],
            "social_links": {},
        }


def scrape_google_maps(job: dict, existing: set = None):
    query = job.get("query")
    if not query:
        # Fallback if query isn't pre-constructed
        location_str = ", ".join(filter(None, [job.get("city"), job.get("state"), job.get("country")]))
        query = f"{job.get('category')} in {location_str}"
    results = []
    existing = existing or set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        if job.get("lat") and job.get("long"):
            # Use @lat,long,zoom format for centered search
            url = f"https://www.google.com/maps/search/{query}/@{job['lat']},{job['long']},13z"
        else:
            url = f"https://www.google.com/maps/search/{query}"
        
        page.goto(url, wait_until="domcontentloaded")

        # Smart wait: wait for the listing feed to load instead of fixed 5s sleep
        try:
            page.wait_for_selector('div[role="feed"]', timeout=10000)
        except Exception:
            time.sleep(2)

        scrollable = page.query_selector('div[role="feed"]')

        ## Smart scrolling: stop early once we have enough listings
        needed = MIN_RESULTS + 5  # buffer for skipped duplicates
        prev_count = 0
        stale_rounds = 0

        for rnd in range(MAX_SCROLL_ROUNDS):
            if scrollable:
                scrollable.evaluate("el => el.scrollBy(0, 3000)")
            else:
                page.mouse.wheel(0, 5000)
            time.sleep(0.8)

            cur_count = len(page.query_selector_all('div[role="article"]'))
            if cur_count >= needed:
                break
            if cur_count == prev_count:
                stale_rounds += 1
                if stale_rounds >= 3:
                    break  # no new results loading, stop scrolling
            else:
                stale_rounds = 0
            prev_count = cur_count

        # short settle after scrolling
        page.wait_for_timeout(1000)

        listings = page.query_selector_all('div[role="article"]')
        logger.info(f"Total listings found: {len(listings)}")

        places = []
        for listing in listings:
            name_el = listing.query_selector("div.fontHeadlineSmall")
            link_el = listing.query_selector("a.hfpxzc")
            if name_el and link_el:
                name = name_el.inner_text()
                if name in existing:
                    logger.info(f"Skipping already scraped: {name}")
                    continue
                places.append({"name": name, "maps_url": link_el.get_attribute("href")})

        # --- Extract details for each place ---
        for place in places:
            if len(results) >= MIN_RESULTS:
                break
            result = _extract_detail(page, context, place)
            results.append(result)

        browser.close()

    return results