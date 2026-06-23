"""
AI Trip Advisor — Free-form Q&A
=================================
Answers natural-language questions about scraped place data
using keyword matching, scoring, and templates (no LLM needed).
"""

import re
import math

GREETING = """Hi! I've studied all the scraped places. Ask me anything:

- *"What's the best place?"*
- *"Show me places in Zamalek"*
- *"Tell me about Zooba"*
- *"What do people say about Koshary?"*
- *"Compare Zooba and Felfela"*
- *"Where is Cairo Tower?"*
- *"Good for dinner"*
- *"cheap options"*
"""


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe(d, key, default=""):
    v = d.get(key)
    return v if v is not None else default


def _name(d):
    return _safe(d, "name") or _safe(d, "query")


def _rating(d):
    return d.get("rating")


def _count(d):
    return d.get("review_count", 0)


def _addr(d):
    return _safe(d, "address")


def _reviews(d):
    return d.get("reviews", [])


def _sources(d):
    return d.get("sources", [])


def _text_pool(d):
    parts = [_name(d), _safe(d, "address"), *_reviews(d)]
    return " ".join(parts).lower()


# ── Question answering ─────────────────────────────────────────────────────

def answer_question(question: str, data: list[dict]) -> str:
    """Answer a natural language question about the scraped data."""
    q = question.strip().rstrip("?").lower()

    if not data:
        return "No place data available yet. Run a scrape first!"

    # ── Intent detection ─────────────────────────────────────────────────
    # (order matters — more specific first)
    if any(kw in q for kw in ["where is", "location of", "address of"]):
        return _where_is(data, q)

    if any(kw in q for kw in ["compare", "vs", "versus", "difference"]):
        return _compare(data, q)

    if any(kw in q for kw in ["say about", "people say", "reviews for", "reviews about", "feedback", "opinion"]):
        return _reviews_about(data, q)

    if any(kw in q for kw in ["tell me about", "tell us about"]):
        return _about_place(data, q)

    if any(kw in q for kw in ["best", "top", "highest", "favorite", "recommend"]):
        return _best_places(data, q)

    if any(kw in q for kw in ["in ", "at ", "area ", "near ", "around "]):
        return _places_in_area(data, q)

    if any(kw in q for kw in ["dinner", "lunch", "breakfast", "meal", "eat"]):
        return _meal_suggestions(data, q)

    if any(kw in q for kw in ["cheap", "budget", "affordable", "inexpensive", "price", "cost"]):
        return _budget_options(data)

    if any(kw in q for kw in ["cuisine", "food", "what kind", "menu", "serve"]):
        return _cuisine_query(data, q)

    if any(kw in q for kw in ["list", "all", "show", "what places", "every"]):
        return _list_all(data)

    # ── Fallback: search reviews for keywords ────────────────────────────
    keywords = [w for w in q.split() if len(w) > 3]
    if keywords:
        results = []
        for d in data:
            pool = _text_pool(d)
            matches = sum(1 for kw in keywords if kw in pool)
            if matches:
                results.append((matches, d))
        if results:
            results.sort(key=lambda x: -x[0])
            lines = [f"Found mentions of **{q}**:"]
            for _, d in results[:5]:
                lines.append(f"- **{_name(d)}**", )
            return "\n".join(lines)

    return f"""I'm not sure how to answer that. Try asking:

- *"Best place?"*
- *"Places in Zamalek"*
- *"Tell me about Zooba"*
- *"Compare Zooba and Felfela"*
- *"Where is Cairo Tower?"*
- *"Reviews for Koshary"*
- *"Budget options"*
- *"Dinner suggestions"*"""


# ── Intent handlers ────────────────────────────────────────────────────────

def _best_places(data, q):
    by_rating = sorted(data, key=lambda d: (_rating(d) or 0), reverse=True)
    top = by_rating[:5]
    lines = ["**Top rated places:**", ""]
    for i, d in enumerate(top, 1):
        r = _rating(d)
        c = _count(d)
        tag = ""
        if "review" in q:
            by_count = sorted(data, key=_count, reverse=True)
            top_count = by_count[:5]
            lines = ["**Most reviewed places:**", ""]
            for j, p in enumerate(top_count, 1):
                lines.append(f"{j}. **{_name(p)}** — {_count(p)} reviews ({_rating(p)}/5)")
            return "\n".join(lines)
        if "cheap" in q or "budget" in q:
            return _budget_options(data)
    lines = ["**Top rated places:**", ""]
    for i, d in enumerate(top, 1):
        r = _rating(d)
        c = _count(d) or 0
        stars = "★" * round(r or 0) if r else ""
        lines.append(f"{i}. **{_name(d)}** — {stars} {r}/5 ({c} reviews)")
    if "cheap" in q or "budget" in q:
        lines.append("")
        lines.append("_Tip: ask `budget options` for cheap eats_")
    return "\n".join(lines)


def _compare(data, q):
    names = [_name(d) for d in data]
    targets = []
    for tok in q.replace("vs", " ").replace("versus", " ").replace(",", " ").split():
        for name in names:
            if tok and len(tok) > 2 and name.lower().startswith(tok) or (tok in name.lower()):
                if name not in targets:
                    targets.append(name)
    targets = targets[:3]
    if len(targets) < 2:
        return "I need two place names to compare. Say *\"Compare Zooba and Felfela\"*."
    items = [d for d in data if _name(d) in targets]
    if len(items) < 2:
        return "Couldn't find enough places to compare."
    lines = [f"**Comparing:** {' vs '.join(targets)}", ""]
    header = f"| {' | '.join(f'{_name(d)}' for d in items)} |"
    sep = f"| {' | '.join('---' for _ in items)} |"
    lines.append(header)
    lines.append(sep)
    for label, getter in [
        ("Google Rating", lambda d: f"{_rating(d) or '-'}/5"),
        ("Reviews", lambda d: str(_count(d) or 0)),
        ("Address", _addr),
        ("Sources", lambda d: str(len(_sources(d)))),
    ]:
        row = f"| **{label}** | {' | '.join(getter(d) for d in items)} |"
        lines.append(row)
    return "\n".join(lines)


def _where_is(data, q):
    for d in data:
        name = _name(d).lower()
        if any(part in name for part in q.split() if len(part) > 3):
            addr = _addr(d)
            if addr:
                return f"**{_name(d)}** is at: {addr}\n\n👉 [Open in Google Maps](https://www.google.com/maps/search/{_name(d).replace(' ', '+')})"
            return f"**{_name(d)}** — no address found in the scrape."
    # Fuzzy match
    for d in data:
        name_lower = _name(d).lower()
        for word in q.split():
            if len(word) > 3 and word in name_lower:
                addr = _addr(d)
                if addr:
                    return f"**{_name(d)}** is at: {addr}"
                return f"**{_name(d)}** — no address found."
    return "Couldn't find that place in the scraped data."


def _about_place(data, q):
    for d in data:
        name_lower = _name(d).lower()
        for word in q.split():
            if len(word) > 3 and word in name_lower:
                r = _rating(d)
                c = _count(d) or 0
                addr = _addr(d)
                revs = _reviews(d)
                lines = [f"**{_name(d)}**"]
                if addr:
                    lines.append(f"📍 {addr}")
                if r:
                    lines.append(f"⭐ {r}/5 ({c} Google reviews)")
                srcs = _sources(d)
                if srcs:
                    for s in srcs[:3]:
                        lines.append(f"  [{s['source']}] {s['rating']}/5 ({s['review_count']} reviews)")
                if revs:
                    lines.append(f"\n**Sample reviews:**")
                    for rev in revs[:3]:
                        lines.append(f"> {rev}")
                return "\n".join(lines)
    return f"Sorry, I don't have data about that. Scraped places: {', '.join(_name(d) for d in data)}"


def _reviews_about(data, q):
    target = None
    for d in data:
        name_lower = _name(d).lower()
        for token in q.split():
            if len(token) > 3 and token in name_lower:
                target = d
                break
    if not target:
        # search reviews for keywords
        kw = [w for w in q.split() if len(w) > 3 and w not in ("what", "people", "about", "they")]
        matches = []
        for d in data:
            for rev in _reviews(d):
                for k in kw:
                    if k in rev.lower():
                        matches.append((d, rev))
                        break
        if matches:
            lines = [f"**What people are saying:**", ""]
            for d, rev in matches[:5]:
                lines.append(f"- **{_name(d)}**: _{rev}_")
            return "\n".join(lines)
        return "No review mentions found for that."
    revs = _reviews(target)
    if not revs:
        return f"No reviews found for **{_name(target)}**."
    lines = [f"**Reviews for {_name(target)}:**", ""]
    for i, rev in enumerate(revs[:5], 1):
        lines.append(f"*{i}.* {rev}")
    return "\n".join(lines)


def _places_in_area(data, q):
    # Extract area name
    area_tokens = q.split()
    area = ""
    for i, tok in enumerate(area_tokens):
        if tok in ("in", "at", "near", "around") and i + 1 < len(area_tokens):
            area = area_tokens[i + 1].strip(",.!?")
            break
    if not area:
        area = area_tokens[-1].strip(",.!?")
    if not area or len(area) < 3:
        return "Which area? Say *\"Places in Zamalek\"*."
    filtered = [d for d in data if area in _addr(d).lower() or area in _name(d).lower()]
    if not filtered:
        return f"No places found in **{area}**."
    lines = [f"**Places in {area.title()} ({len(filtered)}):**", ""]
    for d in filtered:
        r = _rating(d)
        stars = f" {'★' * round(r or 0)} {r}/5" if r else ""
        lines.append(f"- **{_name(d)}**{stars}")
    return "\n".join(lines)


def _meal_suggestions(data, q):
    meal_type = ""
    for m in ["dinner", "lunch", "breakfast"]:
        if m in q:
            meal_type = m
            break
    # Simple heuristic: places with reviews mentioning the meal type
    scored = []
    for d in data:
        pool = _text_pool(d)
        score = 0
        r = _rating(d) or 0
        if meal_type and meal_type in pool:
            score += 3
        if "restaurant" in _name(d).lower():
            score += 1
        score += r * 0.5
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    label = meal_type or "eating out"
    lines = [f"**Suggestions for {label}:**", ""]
    for score, d in scored[:5]:
        r = _rating(d)
        stars = f" {'★' * round(r or 0)} {r}/5" if r else ""
        lines.append(f"- **{_name(d)}**{stars}")
    return "\n".join(lines)


def _budget_options(data):
    # Look for price keywords in reviews
    keywords = ["cheap", "affordable", "budget", "inexpensive", "reasonably priced", "value", "low price"]
    scored = []
    for d in data:
        pool = _text_pool(d)
        kw_matches = sum(1 for kw in keywords if kw in pool)
        r = _rating(d) or 0
        score = kw_matches * 5 + r * 0.3
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    lines = ["**Budget-friendly options:**", ""]
    for score, d in scored[:5]:
        r = _rating(d)
        stars = f" {'★' * round(r or 0)} {r}/5" if r else ""
        lines.append(f"- **{_name(d)}**{stars}")
    return "\n".join(lines)


def _cuisine_query(data, q):
    tokens = [w for w in q.split() if len(w) > 3 and w not in ("what", "kind", "menu", "serve", "cuisine", "food")]
    query_cuisine = " ".join(tokens) if tokens else ""
    scored = []
    for d in data:
        pool = _text_pool(d)
        score = 0
        if query_cuisine and query_cuisine in pool:
            score += 5
        r = _rating(d) or 0
        score += r * 0.3
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    lines = [f"**Places matching your taste:**", ""]
    for score, d in scored[:5]:
        r = _rating(d)
        stars = f" {'★' * round(r or 0)} {r}/5" if r else ""
        lines.append(f"- **{_name(d)}**{stars}")
    return "\n".join(lines)


def _list_all(data):
    lines = [f"**All scraped places ({len(data)}):**", ""]
    for d in sorted(data, key=lambda x: _rating(x) or 0, reverse=True):
        r = _rating(d)
        stars = f" {'★' * round(r or 0)} {r}/5" if r else ""
        lines.append(f"- **{_name(d)}**{stars}")
    return "\n".join(lines)
