"""
Egypt Place Rating Aggregator - Web UI
========================================
Usage: streamlit run app.py
"""

import sys
import re
import os
import subprocess
import time
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

# ── Ensure Scrapling browsers are installed (needed on Streamlit Cloud) ──
try:
    subprocess.run(
        [sys.executable, "-m", "scrapling", "install"],
        capture_output=True, timeout=120,
    )
except Exception:
    pass  # if it fails, Scrapling will give a clear error

# ── Inject PWA manifest (enables "Add to Home Screen" + PWABuilder APK) ──
components.html("""
<script>
(function(){
  if(document.querySelector('link[rel="manifest"]')) return;
  var m = {"name":"Egypt Place Aggregator","short_name":"Places","start_url":".","display":"standalone","background_color":"#0e1117","theme_color":"#f63366","icons":[{"src":"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📍</text></svg>","sizes":"256x256","type":"image/svg+xml"}]};
  var b = new Blob([JSON.stringify(m)], {type:"application/json"});
  var l = document.createElement("link"); l.rel="manifest"; l.href=URL.createObjectURL(b);
  document.head.appendChild(l);
})();
</script>
""", height=0)

# Import scraper from main module
sys.path.insert(0, ".")
from place_rating_aggregator import scrape_place, search_places_by_governorate, GOVERNORATES, CATEGORIES
from advisor import GREETING, answer_question

st.set_page_config(
    page_title="Egypt Place Rating Aggregator",
    page_icon="🇪🇬",
    layout="wide",
)

st.title("🇪🇬 Egypt Place Rating Aggregator")
st.markdown("Scrape ratings, reviews, and addresses for Egyptian places from Google Search.")

# Input
if "places_text_value" not in st.session_state:
    st.session_state.places_text_value = ""

with st.sidebar:
    st.header("Places to look up")
    places_text = st.text_area(
        "One place per line",
        value=st.session_state.places_text_value,
        height=200,
        placeholder="Zooba, Zamalek, Cairo\nKoshari Abou Tarek, Cairo\n...",
        help="Format: Place Name, Area, City — or just Place Name if known.",
    )
    st.session_state.places_text_value = places_text
    default_location = st.text_input(
        "Default location",
        placeholder="e.g. Cairo, Egypt",
        help="Used for any line that doesn't include a location.",
    )
    run = st.button("🚀 Scrape", type="primary", use_container_width=True)

    st.divider()
    st.header("🏛️ Governorate Search")
    st.caption("Generate a place list for a governorate to paste above.")
    gov = st.selectbox("Governorate", list(GOVERNORATES.keys()), index=0)
    gov_cats = st.multiselect(
        "Categories",
        CATEGORIES,
        default=["restaurants", "hotels", "attractions"],
        max_selections=6,
    )
    gov_max = st.slider("Places per category", min_value=3, max_value=15, value=5)
    if st.button("🔍 Generate place list", use_container_width=True):
        if not gov_cats:
            st.error("Select at least one category.")
        else:
            with st.spinner(f"Searching {gov}..."):
                places = search_places_by_governorate(gov, gov_cats, gov_max)
            if places:
                st.session_state.gov_places = places
                st.session_state.gov_name = gov
            else:
                st.error(f"No places found in {gov} for: {', '.join(gov_cats)}. "
                         "Google may be rate-limiting or the page didn't load properly. "
                         "Try again in a few minutes.")

    if st.session_state.get("gov_places"):
        st.caption(f"Found {len(st.session_state.gov_places)} places in {st.session_state.gov_name}")
        text_to_copy = "\n".join(st.session_state.gov_places)
        st.text_area("Copy this list 👇", value=text_to_copy, height=200)
        if st.button("📋 Paste into scraper input", use_container_width=True):
            existing = st.session_state.places_text_value
            st.session_state.places_text_value = existing + ("\n" if existing else "") + text_to_copy
            st.rerun()

# Parse input
if places_text.strip():
    raw_lines = [l.strip() for l in places_text.strip().split("\n") if l.strip()]
    queries = []
    for line in raw_lines:
        parts = [p.strip() for p in line.split(",")]
        place = parts[0]
        loc = ", ".join(parts[1:]).strip() if len(parts) > 1 else default_location
        queries.append((place, loc or None))
else:
    queries = []

if run and not queries:
    st.error("Enter at least one place to look up.")

# ── Scrape (only when button clicked) ───────────────────────────────────────
if run and queries:
    results = []
    progress = st.progress(0, text="Starting...")
    status = st.empty()

    for i, (place, loc) in enumerate(queries):
        label = f"{place}" + (f" ({loc})" if loc else "")
        status.info(f"🔍 Scraping {label}...")
        progress.progress((i) / len(queries), text=f"Scraping {i+1}/{len(queries)}: {label}")

        try:
            data = scrape_place(place, loc)
            results.append(data)
        except Exception as e:
            results.append({
                "query": place,
                "location": loc or "",
                "name": "",
                "address": "",
                "rating": None,
                "review_count": 0,
                "reviews": [],
                "sources": [],
                "error": str(e),
            })

        time.sleep(0.5)

    progress.progress(1.0, text="Done!")
    status.success(f"Scraped {len(results)} place(s)")
    st.session_state.results = results
    st.session_state.results_ts = time.time()

    # Show any errors
    errors = [r for r in results if r.get("error")]
    for r in errors:
        st.error(f"**{r['query']}**: {r['error']}")
    if not errors:
        # Check for empty results (page loaded but no data found)
        empty = [r for r in results if not r.get("rating") and not r.get("address") and not r.get("name")]
        if empty:
            names = ", ".join(r["query"] for r in empty[:3])
            st.warning(f"No data found for: {names}. "
                       "Google may have returned a captcha or different page format. "
                       "Try running locally or use a different network.")

# ── Display results (use cached) ────────────────────────────────────────────
results = st.session_state.get("results")

if results is not None:
    # Collect all unique source names across results
    all_sources = ["Google"]
    for r in results:
        for s in r["sources"]:
            if s["source"] not in all_sources:
                all_sources.append(s["source"])

    # Build display rows
    rows = []
    for r in results:
        # Build a dict of source -> rating for this result
        source_ratings = {}
        source_counts = {}
        if r["rating"]:
            source_ratings["Google"] = r["rating"]
            source_counts["Google"] = r["review_count"]
        for s in r["sources"]:
            source_ratings[s["source"]] = s["rating"]
            source_counts[s["source"]] = s["review_count"]

        # Simple average of ALL platform ratings
        all_vals = [v for v in source_ratings.values() if v]
        avg = round(sum(all_vals) / len(all_vals), 2) if all_vals else None

        # Bayesian weighted average
        bayes_total_w = 10
        bayes_sum = 3.5 * 10
        if r["rating"]:
            bayes_sum += r["rating"] * r["review_count"]
            bayes_total_w += r["review_count"]
        for s in r["sources"]:
            bayes_sum += s["rating"] * s["review_count"]
            bayes_total_w += s["review_count"]
        weighted = round(bayes_sum / bayes_total_w, 2) if bayes_total_w > 10 else None

        name = r["name"] or r["query"]

        row = {
            "Place": name,
            "Address": r["address"] or "—",
            "Simple Avg": f"{avg}/5" if avg else "—",
            "Weighted Avg": f"{weighted}/5" if weighted else "—",
        }
        for src in all_sources:
            val = source_ratings.get(src)
            cnt = source_counts.get(src, "")
            row[src] = f"{val}" if val else "—"

        row["_reviews"] = r["reviews"]
        row["_error"] = r["error"]
        rows.append(row)

    df = pd.DataFrame(rows)

    # Build display columns: Place, Address, then per-source columns, then averages
    display_cols = ["Place", "Address"] + all_sources + ["Simple Avg", "Weighted Avg"]
    displayable = [c for c in display_cols if c in df.columns]

    col_config = {
        "Place": st.column_config.TextColumn("Place", width="medium"),
        "Address": st.column_config.TextColumn("Address", width="medium"),
        "Simple Avg": st.column_config.TextColumn("Simple Avg", width="small"),
        "Weighted Avg": st.column_config.TextColumn("Weighted Avg", width="small"),
    }
    for src in all_sources:
        col_config[src] = st.column_config.TextColumn(src, width="small")

    st.dataframe(
        df[displayable],
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
    )

    # Expandable review cards
    st.divider()
    st.subheader("📝 Latest Reviews")
    for r in results:
        name = r["name"] or r["query"]
        if r["reviews"]:
            with st.expander(f"{name} — {len(r['reviews'])} reviews"):
                for j, rev in enumerate(r["reviews"], 1):
                    st.write(f"**{j}.** {rev}")
        else:
            st.caption(f"_{name}_ — no reviews found")

    # ── Location floating squares with map popup ──────────────────────────────
    st.divider()
    st.subheader("🌍 Locations")

    def _make_locations_component(places):
        cols = min(3, len(places))
        cards = []
        for r in places:
            name = r["name"] or r["query"]
            addr = r["address"] or "No address found"
            js_name = name.replace("'", "\\'").replace('"', '&quot;')
            js_addr = addr.replace("'", "\\'").replace('"', '&quot;')
            html_name = name.replace("&", "&amp;").replace("<", "&lt;")
            html_addr = addr.replace("&", "&amp;").replace("<", "&lt;")
            cards.append(f"""<div class="lc" onclick="sm('{js_name}','{js_addr}')"><div class="ln">📍 {html_name}</div><div class="la">{html_addr}</div></div>""")
        return f"""<style>
.lg{{display:grid;grid-template-columns:repeat({cols},1fr);gap:18px}}
.lc{{cursor:pointer;border:1px solid #ddd;border-radius:14px;padding:18px;background:var(--bg,rgba(255,255,255,0.08));box-shadow:0 2px 8px rgba(0,0,0,0.1);font-size:14px;transition:box-shadow .2s}}
.lc:hover{{box-shadow:0 4px 16px rgba(0,0,0,0.25)}}
.ln{{font-weight:700;font-size:16px;margin-bottom:8px}}
.la{{color:#888}}
#mo{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:99999;justify-content:center;align-items:center;font-family:system-ui,sans-serif}}
#mb{{background:var(--modal-bg,#1e1e1e);border-radius:14px;padding:20px;width:90%;max-width:900px;max-height:90vh;position:relative;box-shadow:0 8px 32px rgba(0,0,0,0.5)}}
#mc{{position:absolute;top:10px;right:14px;background:none;border:none;font-size:28px;cursor:pointer;color:#999;z-index:1}}
#mc:hover{{color:#fff}}
#mn{{font-weight:700;font-size:18px;margin-bottom:10px;padding-right:28px;color:var(--text,#fff)}}
#mf{{width:100%;height:500px;border:0;border-radius:10px}}
</style>
<div class="lg">{"".join(cards)}</div>
<div id="mo"><div id="mb"><button id="mc" onclick="cm()">&times;</button><div id="mn"></div><iframe id="mf" allowfullscreen loading="lazy"></iframe></div></div>
<script>
function sm(n,a){{document.getElementById('mn').textContent=n;document.getElementById('mf').src='https://www.google.com/maps?q='+encodeURIComponent(n+' '+a)+'&output=embed';document.getElementById('mo').style.display='flex'}}
function cm(){{document.getElementById('mo').style.display='none';document.getElementById('mf').src=''}}
document.getElementById('mo')?.addEventListener('click',function(e){{if(e.target===this)cm()}})
</script>"""

    rows = (len(results) + 2) // 3
    components.html(_make_locations_component(results), height=rows * 130)

    # ── AI Trip Advisor (free-form Q&A) ─────────────────────────────────────
    st.divider()
    st.subheader("🤖 AI Trip Advisor")

    if "advisor_msgs" not in st.session_state:
        st.session_state.advisor_msgs = []

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💬 Start chat", type="primary", use_container_width=True):
            st.session_state.advisor_msgs = [
                {"role": "assistant", "content": GREETING},
            ]

    if st.session_state.advisor_msgs:
        for msg in st.session_state.advisor_msgs:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        user_input = st.chat_input("Ask me anything about these places...")
        if user_input:
            st.session_state.advisor_msgs.append({"role": "user", "content": user_input})
            with st.spinner("Thinking..."):
                answer = answer_question(user_input, results)
            st.session_state.advisor_msgs.append({"role": "assistant", "content": answer})
            st.rerun()

elif not run:
    st.info("Enter places in the sidebar and click **Scrape** to begin.")
    st.markdown("""
    ### Example
  
    Paste into the sidebar:
    ```
    Zooba, Zamalek, Cairo
    Koshari Abou Tarek, Cairo
    ```
    """)
