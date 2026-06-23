"""
Place Rating Aggregator
=======================

Scrapes ratings, reviews, and location for Egyptian places from Google Search.

No API keys needed. Uses Scrapling (StealthyFetcher) for browser automation.

SETUP
-----
pip install -r requirements.txt
scrapling install

USAGE
-----
    python place_rating_aggregator.py "Zooba" --location "Zamalek, Cairo"
"""

import sys
import re
import json
import argparse
from scrapling.fetchers import StealthyFetcher

PRIOR_RATING = 3.5
PRIOR_WEIGHT = 10

SOURCE_MAP = {
    "tripadvisor": "TripAdvisor",
    "elmenus": "Elmenus",
    "google": "Google",
    "talabat": "Talabat",
    "yellowpages": "YellowPages",
    "menuegypt": "MenuEgypt",
    "facebook": "Facebook",
}


GOOGLE_UI_LABELS = frozenset({
    "search results", "ai overview", "people also ask", "people also search for",
    "see photos", "page navigation", "sponsored", "ad", "ads", "featured snippet",
    "top stories", "videos", "images", "news", "shopping results",
    "share", "feedback", "thanks for your feedback", "thanks!",
    "save", "saved", "nearby", "book a table", "order online",
    "call", "website", "direction", "directions", "menu",
    # Common Google Maps / Knowledge Panel UI strings
    "thank you for sharing!", "thank you for sharing", "thanks for sharing",
    "suggest an edit", "claim this business", "add a photo", "add a review",
    "write a review", "see all reviews", "more reviews", "all reviews",
    "open now", "open 24 hours", "closed", "temporarily closed",
    "permanently closed", "send to phone", "send to your phone",
    "get directions", "reserve a table", "check availability",
    "edit this place", "add missing information", "send feedback",
    "report a problem", "view larger map", "see on google maps",
    "place details", "more info", "overview", "about",
    "reviews", "photos", "updates", "q&a",
    # Captcha / privacy-interstitial page text
    "about this page", "why did this happen", "terms of service",
    "skip to main content", "please click", "here",
})

# Substrings that disqualify a name — checked via `in`
_BAD_SUBSTRINGS = (
    "thank you",
    "sharing",
    "suggest an edit",
    "claim this",
    "add a photo",
    "write a review",
    "see all",
    "more reviews",
    "report a problem",
    "send feedback",
    "get directions",
    "معلومات",          # Arabic "information" — often a tooltip, not a name
    "لأول",             # Arabic "for the first time"
    "اضغط",             # Arabic "click/press"
    "انقر",             # Arabic "click"
    "footer links",     # Google footer nav label
    "footer",
    # Captcha / privacy-interstitial page headings
    "about this page",
    "why did this happen",
    "terms of service",
    "skip to main content",
    "please click",
    # Single-word captcha links
    "captcha",
)

def is_valid_place_name(text):
    if not text or len(text) < 2 or len(text) > 80:
        return False
    lower = text.lower().strip()
    stripped = lower.rstrip(".,!?;:")
    if stripped in GOOGLE_UI_LABELS or lower in GOOGLE_UI_LABELS:
        return False
    # Reject strings containing known bad substrings
    for bad in _BAD_SUBSTRINGS:
        if bad in lower or bad in text:
            return False
    if re.match(r"^[\d.,()/:;!@#$%^&*_+=~`\[\]{}<>|\\\"'?]+$", text):
        return False
    if text.startswith("http://") or text.startswith("https://") or "://" in text:
        return False
    if "..." in text or ".." in text or text.endswith("…"):
        return False
    if " | " in text:
        return False
    if len(text.split()) >= 7:
        return False
    # Reject if the text ends with "!" (UI confirmation messages, not place names)
    if text.strip().endswith("!"):
        return False
    # Reject if it's a long Arabic sentence (likely a tooltip/caption, not a name).
    # Place names in Arabic are typically short; count Arabic chars.
    arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    if arabic_chars > 0:
        # If more than half the characters are Arabic AND the text is long, skip it
        if arabic_chars / len(text) > 0.4 and len(text) > 20:
            return False
    return True


def _extract_name_from_jsonld(response) -> str | None:
    """Extract place name from JSON-LD structured data on the Search page (no extra request)."""
    try:
        scripts = response.css('script[type="application/ld+json"]')
        for script in scripts:
            if not script.text:
                continue
            data = json.loads(script.text)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                atype = item.get("@type", "")
                if isinstance(atype, list):
                    atype = atype[0] if atype else ""
                if atype in (
                    "LocalBusiness", "Restaurant", "Hotel", "TouristAttraction",
                    "Place", "FoodEstablishment", "LodgingBusiness",
                    "Museum", "Park", "Store", "CafeOrCoffeeShop",
                ):
                    name = item.get("name", "").strip()
                    if name and is_valid_place_name(name):
                        return name
                for key in ("@graph", "@set"):
                    if key in item:
                        for sub in item[key]:
                            if isinstance(sub, dict):
                                sub_type = sub.get("@type", "")
                                if isinstance(sub_type, list):
                                    sub_type = sub_type[0] if sub_type else ""
                                if sub_type in (
                                    "LocalBusiness", "Restaurant", "Hotel",
                                    "TouristAttraction", "Place",
                                    "FoodEstablishment", "LodgingBusiness",
                                ):
                                    name = sub.get("name", "").strip()
                                    if name and is_valid_place_name(name):
                                        return name
    except Exception:
        pass
    return None


def _extract_name_from_maps(query: str, location: str | None = None) -> str | None:
    """Fetch place name from Google Maps search (same approach as governorate search).

    Maps search result articles have aria-label like:
    "La Terrace Restaurant & Lounge · 4.6 ★ · $$$ · Mediterranean"
    Split on "·" and take the first part for the clean name.
    """
    maps_query = f"{query} {location}" if location else query
    maps_url = f"https://www.google.com/maps/search/{maps_query.replace(' ', '+')}/"
    try:
        resp = StealthyFetcher.fetch(
            maps_url,
            headless=True,
            network_idle=True,
            wait=5000,
            timeout=30000,
        )
        for article in resp.css('[role="article"]'):
            aria = article.attrib.get("aria-label", "")
            if aria:
                name = aria.split("·")[0].strip().rstrip(" ·★")
                if name and is_valid_place_name(name):
                    return name
            # Fallback: extract from article text
            txt = article.get_all_text(strip=True)
            first_line = txt.split("\n")[0].strip()
            if first_line and is_valid_place_name(first_line):
                return first_line
    except Exception:
        pass
    return None


def scrape_place(query: str, location: str | None = None) -> dict:
    """Scrape rating, reviews, and location for a place from Google Search."""
    search_query = f"{query} {location}" if location else query
    url = f"https://www.google.com/search?q={search_query.replace(' ', '+')}&hl=en"

    result = {
        "query": query,
        "location": location or "",
        "name": "",
        "address": "",
        "rating": None,
        "review_count": 0,
        "reviews": [],
        "sources": [],
        "error": None,
    }

    try:
        response = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            wait=8000,
            locale="en-US",
            google_search=True,
            timeout=30000,
        )
    except Exception as e:
        result["error"] = f"Page load failed: {e}"
        # Try Maps for the name even when Search fails
        result["name"] = _extract_name_from_maps(query, location) or ""
        return result

    text = response.get_all_text()

    # Detect captcha/blocked pages — still try Maps for the name
    captcha_detected = False
    if not text or len(text.strip()) < 200:
        result["error"] = "Empty or very short response — Google may be blocking the request"
        captcha_detected = True
    lower_text = text.lower() if text else ""
    if any(p in lower_text for p in ("unusual traffic", "captcha", "please confirm you're not a robot",
                                      "our systems have detected unusual traffic")):
        result["error"] = "Google blocked the request (unusual traffic detected). Try a different network or reduce request frequency."
        captcha_detected = True
    if "this page can't be loaded" in lower_text or "google search is temporarily unavailable" in lower_text:
        result["error"] = "Google Search is temporarily unavailable from this IP."
        captcha_detected = True

    if captcha_detected:
        result["name"] = _extract_name_from_maps(query, location) or ""
        return result

    lines = text.split("\n")
    clean = [l.strip() for l in lines]

    # Extract Google rating and review count (now on separate lines)
    google_review_line = None
    for i, line in enumerate(clean):
        m = re.match(r"([\d,]+)\s*Google reviews?", line)
        if m:
            try:
                result["review_count"] = int(m.group(1).replace(",", ""))
                google_review_line = i
            except ValueError:
                pass
            break

    # Rating is on the line before "Google reviews" as a simple decimal
    if google_review_line is not None and google_review_line > 0:
        prev = clean[google_review_line - 1].strip()
        if re.match(r"^\d+(\.\d+)?$", prev):
            try:
                result["rating"] = float(prev)
            except ValueError:
                pass

    # Also try combined format (old Google layout)
    if result["rating"] is None:
        for line in clean:
            m = re.match(r"([\d.]+)\s+([\d,]+)\s*Google reviews?", line)
            if m:
                try:
                    result["rating"] = float(m.group(1))
                    if not result["review_count"]:
                        result["review_count"] = int(m.group(2).replace(",", ""))
                except ValueError:
                    pass
                break

    # ── Name extraction (primary: Google Maps aria-label; fallbacks: Search page) ──
    result["name"] = ""

    # 1. Google Maps — same approach as governorate search
    maps_name = _extract_name_from_maps(query, location)

    # 2. CSS selectors from Search page (free — no extra request)
    if not maps_name:
        for sel in [
            '[data-attrid="title"]',
            'h2[data-attrid="title"]',
            'div[data-attrid="title"]',
            '.kno-ecr-pt',
            '[role="heading"][aria-level="2"]',
        ]:
            els = response.css(sel)
            if els:
                t = els[0].text.strip() if els[0].text else ""
                if is_valid_place_name(t):
                    maps_name = t
                    break

    # 3. JSON-LD structured data from Search page
    if not maps_name:
        maps_name = _extract_name_from_jsonld(response)

    # 4. Heading tags outside nav/header/footer
    if not maps_name:
        for tag in ["h1", "h2", "h3"]:
            els = response.css(tag)
            for el in els:
                t = el.text.strip() if el.text else ""
                if not is_valid_place_name(t):
                    continue
                parent = el.parent
                in_bad = False
                while parent is not None:
                    if parent.tag in ("nav", "header", "footer"):
                        in_bad = True
                        break
                    parent = parent.parent
                if not in_bad:
                    maps_name = t
                    break
            if maps_name:
                break

    if maps_name and is_valid_place_name(maps_name):
        result["name"] = maps_name
    elif google_review_line is not None:
        skip_phrases = (
            "see ", "add ", "write ", "rate ", "review ", "suggest ", "google ",
            "photos", "overview", "share", "feedback", "thanks", "thank",
            "claim", "report", "send ", "get ", "book ", "order ", "reserve ",
            "check ", "edit ", "more ", "all reviews", "open ", "closed",
        )
        # Walk upward from google_review_line, skipping the rating line
        start = google_review_line - 2  # skip rating line too
        for j in range(start, -1, -1):
            candidate = clean[j]
            if not candidate:
                continue
            lower = candidate.lower()
            if any(lower.startswith(p) for p in skip_phrases):
                continue
            if any(p in lower for p in ("reviews", "rating", "google", "sharing", "معلومات")):
                continue
            if not is_valid_place_name(candidate):
                continue
            if re.match(r"^[\d.,()]+$", candidate):
                continue
            result["name"] = candidate
            break

    # If fallback name looks nothing like the query, prefer the page <title>
    if result.get("name"):
        q_words = set(query.lower().split())
        n_words = set(result["name"].lower().split())
        overlap = q_words & n_words
        # If zero overlap and the name contains obvious non-name patterns, discard it
        if not overlap and any(bad in result["name"].lower() for bad in ("thank", "معلومات", "sharing", "suggest", "footer", "feedback")):
            result["name"] = ""

    if not result.get("name"):
        try:
            title_el = response.css("title")
            if title_el:
                title_text = title_el[0].text.strip()
                title_text = re.sub(r"\s*[-–|]\s*Google\s+Search", "", title_text).strip()
                if is_valid_place_name(title_text):
                    result["name"] = title_text
        except Exception:
            pass

    # Extract address (may span multiple lines: "Address\n:\n<value>")
    for i, line in enumerate(clean):
        if line.strip() == "Address" and i + 2 < len(clean) and clean[i + 1].strip() == ":":
            result["address"] = clean[i + 2].strip()
            break
    if not result["address"]:
        for line in clean:
            m = re.match(r"Address:\s*(.+)", line)
            if m:
                result["address"] = m.group(1).strip()

    # Extract source ratings from organic results
    seen_sources = set()
    for i, line in enumerate(clean):
        m = re.match(r"([\d.]+)\(([\d,]+)\)", line)
        if m:
            try:
                rating = float(m.group(1))
                count = int(m.group(2).replace(",", ""))
                source_name = "Unknown"
                for j in range(max(0, i - 4), min(len(clean), i + 2)):
                    lower = clean[j].lower()
                    for key, name in SOURCE_MAP.items():
                        if key in lower:
                            source_name = name
                            break
                if source_name != "Unknown":
                    key = (source_name, rating, count)
                    if key not in seen_sources:
                        seen_sources.add(key)
                        result["sources"].append({
                            "source": source_name,
                            "rating": rating,
                            "review_count": count,
                        })
            except ValueError:
                pass

    # Extract review snippets visible on initial page (Google shows some inline)
    review_lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in review_lines:
        if len(line) > 60 and not line.startswith("http") and not line.startswith("@"):
            is_date = bool(re.match(r"\w{3,9}\s+\d{1,2},?\s+\d{4}", line))
            if not is_date and not line.startswith(("$", "E£", "EGP", "•")):
                result["reviews"].append(line)

    # Final name validation
    if result["name"] and not is_valid_place_name(result["name"]):
        result["name"] = ""
    if not result["name"]:
        result["name"] = query

    # Deduplicate and limit reviews
    seen_reviews = set()
    unique_reviews = []
    for r in result["reviews"]:
        key = r[:50]
        if key not in seen_reviews:
            seen_reviews.add(key)
            unique_reviews.append(r)
    result["reviews"] = unique_reviews[:10]

    return result


def bayesian_weighted_average(sources: list[dict]) -> float:
    total_weight = PRIOR_WEIGHT
    weighted_sum = PRIOR_RATING * PRIOR_WEIGHT
    for s in sources:
        normalized = s["rating"] * (5 / 5)
        weighted_sum += normalized * s["review_count"]
        total_weight += s["review_count"]
    return round(weighted_sum / total_weight, 2) if total_weight > PRIOR_WEIGHT else PRIOR_RATING


GOVERNORATES = {
    "Cairo": "Cairo Governorate",
    "Giza": "Giza Governorate",
    "Alexandria": "Alexandria Governorate",
    "Fayoum": "Fayoum Governorate",
    "Luxor": "Luxor Governorate",
    "Aswan": "Aswan Governorate",
    "Port Said": "Port Said Governorate",
    "Suez": "Suez Governorate",
    "Ismailia": "Ismailia Governorate",
    "Damietta": "Damietta Governorate",
    "Dakahlia": "Dakahlia Governorate",
    "Sharqia": "Sharqia Governorate",
    "Qalyubia": "Qalyubia Governorate",
    "Kafr El Sheikh": "Kafr El Sheikh Governorate",
    "Gharbia": "Gharbia Governorate",
    "Monufia": "Monufia Governorate",
    "Beheira": "Beheira Governorate",
    "Marsa Matruh": "Marsa Matruh Governorate",
    "North Sinai": "North Sinai Governorate",
    "South Sinai": "South Sinai Governorate",
    "Beni Suef": "Beni Suef Governorate",
    "Minya": "Minya Governorate",
    "Asyut": "Asyut Governorate",
    "Sohag": "Sohag Governorate",
    "Qena": "Qena Governorate",
    "Red Sea": "Red Sea Governorate",
    "New Valley": "New Valley Governorate",
}

CATEGORIES = [
    "restaurants",
    "hotels",
    "cafes",
    "attractions",
    "shopping malls",
    "museums",
    "parks",
    "historical sites",
    "nightlife",
    "spas",
]


def search_places_by_governorate(
    governorate: str,
    categories: list[str] | None = None,
    max_per_category: int = 5,
) -> list[str]:
    """Search Google for places in a governorate and return formatted lines.

    Uses Google Search (same engine as scrape_place) — extracts place names
    from organic result headings (<h3> elements).
    """
    if categories is None:
        categories = ["restaurants"]

    city = governorate
    results = []

    for cat in categories:
        query = f"{cat} in {city}"
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}&hl=en"
        try:
            resp = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                wait=5000,
                locale="en-US",
                google_search=True,
                timeout=30000,
            )

            # Check for captcha/blocked page
            text = resp.get_all_text()
            lower_text = text.lower() if text else ""
            if any(p in lower_text for p in ("unusual traffic", "captcha", "please confirm you're not a robot",
                                              "our systems have detected unusual traffic", "about this page",
                                              "why did this happen", "terms of service")):
                continue  # skip this category — blocked

            seen = set()
            cat_count = 0
            # Extract from organic result headings (h3) — these are place names
            for h3 in resp.css("h3"):
                t = h3.text.strip() if h3.text else ""
                if not t or len(t) < 2 or len(t) > 60:
                    continue
                if not re.search(r"[A-Za-z\u0600-\u06FF]", t):
                    continue
                if not is_valid_place_name(t):
                    continue
                # Skip if it's entirely digits/symbols
                if re.match(r"^[\d\s()/:;,!?.\-]+$", t):
                    continue
                key = t.lower().strip()
                if key not in seen:
                    seen.add(key)
                    results.append(f"{t}, {city}")
                    cat_count += 1
                if cat_count >= max_per_category:
                    break

            # If h3 extraction got nothing, try raw text fallback
            if cat_count == 0:
                text = resp.get_all_text()
                for line in text.split("\n"):
                    line = line.strip()
                    if not line or len(line) < 3 or len(line) > 60:
                        continue
                    if not re.search(r"[A-Za-z\u0600-\u06FF]", line):
                        continue
                    lower = line.lower()
                    # Skip common non-place lines
                    if any(p in lower for p in ("reviews", "rating", "google", "subscribe", "cookie", ".com", "http")):
                        continue
                    if is_valid_place_name(line):
                        key = line.lower().strip()
                        if key not in seen:
                            seen.add(key)
                            results.append(f"{line}, {city}")
                            cat_count += 1
                        if cat_count >= max_per_category:
                            break
        except Exception:
            pass

    # Deduplicate and limit
    unique = []
    seen_lines = set()
    for r in results:
        if r not in seen_lines:
            seen_lines.add(r)
            unique.append(r)
    return unique[:max_per_category * len(categories)]


def cli():
    parser = argparse.ArgumentParser(description="Scrape ratings for a place from Google Search.")
    parser.add_argument("place", help='Place name, e.g. "Zooba"')
    parser.add_argument("--location", help='City/area, e.g. "Zamalek, Cairo"', default=None)
    args = parser.parse_args()

    result = scrape_place(args.place, args.location)

    console_enc = sys.stdout.encoding or "utf-8"
    def safe(text):
        if not isinstance(text, str):
            return str(text)
        return text.encode(console_enc, errors="replace").decode(console_enc)

    print(f"\n{'='*50}")
    print(safe(result["name"] or result["query"]))
    if result["address"]:
        print(f"Location: {safe(result['address'])}")
    print(f"{'='*50}")

    if result["rating"]:
        print(f"Google rating: {result['rating']}/5 ({result['review_count']} reviews)")

    for s in result["sources"]:
        print(f"  [{s['source']}] {s['rating']}/5 ({s['review_count']} reviews)")

    if result["reviews"]:
        print(f"\nLatest reviews:")
        for i, r in enumerate(result["reviews"], 1):
            print(f"  {i}. {safe(r)[:150]}")

    if result["error"]:
        print(f"\nError: {result['error']}")


if __name__ == "__main__":
    cli()
