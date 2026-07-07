import json
import os
import requests
import streamlit as st
from datetime import datetime
from urllib.parse import quote
from itertools import groupby

from config import (
    DATA_DIR, JOB_SEARCH_CACHE_TTL,
    ISRAELI_COMPANIES, RECRUITMENT_AGENCIES,
    PRIMARY_QUERIES, MATCH_KEYWORDS, ROLE_CATEGORIES,
)

COMPANIES_PATH    = os.path.join(DATA_DIR, "companies.json")
CAREER_CACHE_PATH = os.path.join(DATA_DIR, "career_urls.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

CENTER_CITIES_HE = ["תל אביב", "רמת גן", "פתח תקווה", "הרצליה", "גבעתיים"]

# Ordered by likelihood — most common first
CAREER_SUFFIXES = [
    "/careers", "/jobs", "/career", "/careers/jobs",
    "/company/careers", "/about/careers", "/en/careers",
    "/join-us", "/work-with-us", "/open-positions",
    "/careers/open-positions", "/hiring", "/about/jobs",
    "/he/careers", "/he/jobs",
]


# ── Persistence ───────────────────────────────────────────────────────────────

def load_companies() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(COMPANIES_PATH):
        try:
            with open(COMPANIES_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"discovered": [], "followed": []}


def save_companies(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COMPANIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Career URL discovery ──────────────────────────────────────────────────────

def _load_career_cache() -> dict:
    """Persistent cache: {base_url: career_url | None}."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CAREER_CACHE_PATH):
        try:
            with open(CAREER_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_career_cache(cache: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CAREER_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _discover_career_url(base_url: str) -> str | None:
    """
    Probe common career-page suffixes on a company's base URL.
    Returns the first URL that responds 200 and contains job-like content,
    or None if none found. Results are cached to disk.
    """
    cache = _load_career_cache()
    if base_url in cache:
        return cache[base_url]

    # Normalise base: strip trailing slash and paths beyond domain
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    found = None
    for suffix in CAREER_SUFFIXES:
        url = base + suffix
        try:
            r = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 1000:
                # Quick sanity check: page should mention job-related words
                sample = r.text[:5000].lower()
                if any(kw in sample for kw in ("job", "career", "position", "role",
                                                "משרה", "קריירה", "תפקיד", "vacancy")):
                    found = r.url  # follow redirects
                    break
        except Exception:
            continue

    cache[base_url] = found
    _save_career_cache(cache)
    return found


def toggle_follow(company_name: str):
    db = load_companies()
    followed = set(db.get("followed", []))
    if company_name in followed:
        followed.discard(company_name)
    else:
        followed.add(company_name)
    db["followed"] = list(followed)
    save_companies(db)
    st.session_state.company_followed = followed


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_job(job: dict) -> bool:
    """Returns True only if the job has the minimum required fields for display."""
    if not job:
        return False
    title = (job.get("title") or "").strip()
    url   = (job.get("url") or "").strip()
    if not title or len(title) < 3:
        return False
    if not url or not url.startswith("http"):
        return False
    BAD_TOKENS = ["test job", "dummy", "placeholder", "lorem ipsum", "untitled"]
    if any(t in title.lower() for t in BAD_TOKENS):
        return False
    return True


# ── Fetch helpers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_alljobs_by_company(company_name: str) -> list[dict]:
    """Search AllJobs by company name (not keyword). Reliable fallback for any company."""
    try:
        from bs4 import BeautifulSoup
        url = (
            f"https://www.alljobs.co.il/SearchResultsGuest.aspx"
            f"?FromGoogle=True&Company={quote(company_name)}"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for card in soup.select(".cjob-content"):
            title_el = card.select_one(".cjob-content-title a")
            loc_el   = card.select_one(".cjob-content-city")
            desc_el  = card.select_one(".cjob-content-desc")
            if not title_el:
                continue
            href = title_el.get("href", "")
            full_url = ("https://www.alljobs.co.il" + href) if href.startswith("/") else href
            results.append({
                "id": f"aj_co_{abs(hash(title_el.text.strip() + company_name))}",
                "title": title_el.text.strip(),
                "company": company_name,
                "location": loc_el.text.strip() if loc_el else "",
                "source": "AllJobs",
                "url": full_url,
                "description": desc_el.text.strip()[:300] if desc_el else "",
                "date": "",
                "match": 0.0,
            })
        return results
    except Exception:
        return []


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _scrape_schema_jobs(career_url: str, company_name: str) -> list[dict]:
    """Tries to extract schema.org JobPosting structured data from a company career page."""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(career_url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        jobs = []
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "JobPosting":
                        loc = item.get("jobLocation", {})
                        loc_str = ""
                        if isinstance(loc, dict):
                            addr = loc.get("address", {})
                            loc_str = addr.get("addressLocality", "") if isinstance(addr, dict) else str(addr)
                        jobs.append({
                            "id": f"schema_{abs(hash(item.get('title','') + company_name))}",
                            "title": item.get("title", ""),
                            "company": company_name,
                            "location": loc_str,
                            "source": "Career Page",
                            "url": item.get("url", career_url),
                            "description": (item.get("description") or "")[:300],
                            "date": (item.get("datePosted") or "")[:10],
                            "match": 0.0,
                        })
            except Exception:
                continue
        return jobs
    except Exception:
        return []


# ── Per-company job checker ───────────────────────────────────────────────────

def check_company_jobs(company: dict, kws: list[str]) -> list[dict]:
    """
    Fetches jobs for a single company and filters by keyword list.
    Priority: Greenhouse → Lever → schema.org (bonus) → AllJobs fallback.
    Always falls back to AllJobs if the primary source returns nothing.
    """
    from modules.job_search import _fetch_greenhouse, _fetch_lever, _fetch_comeet, _fetch_ashby

    raw: list[dict] = []

    if company.get("greenhouse"):
        raw = _fetch_greenhouse(company["greenhouse"])

    elif company.get("lever"):
        raw = _fetch_lever(company["lever"])

    elif company.get("comeet"):
        kind, a, b = company["comeet"]
        raw = _fetch_comeet(kind, a, b)

    elif company.get("ashby"):
        raw = _fetch_ashby(company["ashby"])

    else:
        # Try schema.org on a known career URL if explicitly set
        if company.get("career_url"):
            raw = _scrape_schema_jobs(company["career_url"], company["name"])

    # Filter by role keywords (match against title)
    if kws:
        raw = [j for j in raw if any(kw in (j.get("title") or "").lower() for kw in kws)]

    # Tag company info
    for j in raw:
        if not j.get("company"):
            j["company"] = company["name"]

    return raw


# ── Company DB scan (AllJobs discovery) ──────────────────────────────────────

def _fetch_alljobs_companies(query: str, city_he: str) -> dict[str, dict]:
    """Returns {company_name: {location, active_jobs, alljobs_url}} from AllJobs."""
    try:
        from bs4 import BeautifulSoup
        url = (
            f"https://www.alljobs.co.il/SearchResultsGuest.aspx"
            f"?FromGoogle=True&Position={quote(query)}&City={quote(city_he)}"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        companies: dict[str, dict] = {}
        for card in soup.select(".cjob-content"):
            co_el  = card.select_one(".cjob-content-company")
            loc_el = card.select_one(".cjob-content-city")
            if not co_el:
                continue
            name = co_el.text.strip()
            if not name or len(name) < 2:
                continue
            loc = loc_el.text.strip() if loc_el else city_he
            if name not in companies:
                companies[name] = {
                    "name": name, "location": loc, "active_jobs": 0,
                    "alljobs_url": f"https://www.alljobs.co.il/SearchResultsGuest.aspx?FromGoogle=True&Company={quote(name)}",
                    "sector": "🔍 Discovered", "source": "AllJobs",
                    "last_updated": datetime.now().strftime("%Y-%m-%d"),
                }
            companies[name]["active_jobs"] += 1
        return companies
    except Exception:
        return {}


def build_discovered_companies(selected_categories: list[str]) -> list[dict]:
    """Scans AllJobs for companies posting in the selected categories across 5 cities."""
    queries = []
    for cat in selected_categories:
        queries.extend(PRIMARY_QUERIES.get(cat, []))
    queries = list(dict.fromkeys(queries))[:6]

    merged: dict[str, dict] = {}
    total = len(queries) * len(CENTER_CITIES_HE)
    progress = st.progress(0, text="סורק AllJobs...")
    step = 0

    for query in queries:
        for city in CENTER_CITIES_HE:
            step += 1
            progress.progress(step / total, text=f"מחפש: {query} ב-{city}")
            for name, info in _fetch_alljobs_companies(query, city).items():
                if name not in merged:
                    merged[name] = info
                else:
                    merged[name]["active_jobs"] += info["active_jobs"]

    progress.empty()
    return sorted(merged.values(), key=lambda x: -x["active_jobs"])


# ── Company-by-company full scan ──────────────────────────────────────────────

def scan_all_companies(selected_categories: list[str], sectors_filter: list[str]) -> list[dict]:
    """
    Iterates every company in ISRAELI_COMPANIES, fetches their jobs,
    filters by selected categories, validates each job,
    and returns a deduplicated flat list.
    """
    kws: list[str] = []
    for cat in selected_categories:
        kws.extend(MATCH_KEYWORDS.get(cat, []))

    companies_to_scan: list[dict] = []
    for sector, cos in ISRAELI_COMPANIES.items():
        if not sectors_filter or sector in sectors_filter:
            companies_to_scan.extend(cos)

    total = len(companies_to_scan)
    progress = st.progress(0, text="סורק חברות...")
    all_jobs: list[dict] = []
    failed: list[str] = []

    for i, company in enumerate(companies_to_scan):
        progress.progress((i + 1) / total, text=f"בודק: {company['name']} ({i+1}/{total})")
        try:
            jobs = check_company_jobs(company, kws)
            all_jobs.extend(j for j in jobs if _validate_job(j))
        except Exception:
            failed.append(company["name"])

    progress.empty()

    if failed:
        st.warning(
            f"⚠️ {len(failed)} חברות לא הגיבו: {', '.join(failed[:5])}"
            + ("..." if len(failed) > 5 else "")
        )

    # Deduplicate by id
    seen: set = set()
    unique: list[dict] = []
    for j in all_jobs:
        jid = j.get("id") or abs(hash(j.get("url","") + j.get("title","")))
        if jid not in seen:
            seen.add(jid)
            unique.append(j)

    # Keep only center-area jobs (Gush Dan + Herzliya)
    from modules.job_search import _filter_by_location
    unique = _filter_by_location(unique, "center")
    return unique


# ── Session state helpers ─────────────────────────────────────────────────────

def _init_state():
    if "company_followed" not in st.session_state:
        db = load_companies()
        st.session_state.company_followed = set(db.get("followed", []))
    if "discovered_companies" not in st.session_state:
        db = load_companies()
        st.session_state.discovered_companies = db.get("discovered", [])
    if "company_scan_results" not in st.session_state:
        st.session_state.company_scan_results = []


# ── UI ────────────────────────────────────────────────────────────────────────

def render(lang: str):
    _init_state()
    from modules.job_search import render_job_card, score_job

    followed: set[str] = st.session_state.company_followed
    profile = st.session_state.get("profile", {})

    # ── Section 1: Scan for jobs ──────────────────────────────────────────────
    scan_header = "🔎 סרוק משרות לפי חברה" if lang == "he" else "🔎 Scan Jobs by Company"
    st.markdown(f"#### {scan_header}")
    # Count companies with API support
    api_cos = sum(1 for s, cos in ISRAELI_COMPANIES.items() for co in cos
                  if co.get("greenhouse") or co.get("lever") or co.get("comeet") or co.get("ashby"))
    total_cos = sum(len(cos) for cos in ISRAELI_COMPANIES.values())
    st.caption(
        f"סריקה ישירה דרך Greenhouse/Lever/Comeet API — {api_cos} חברות מתוך {total_cos} עם גישה ישירה"
        if lang == "he"
        else f"Direct scan via Greenhouse/Lever/Comeet API — {api_cos} of {total_cos} companies have API access"
    )

    c1, c2, c3 = st.columns([3, 3, 1])

    with c1:
        scan_cats = st.multiselect(
            "תחומים" if lang == "he" else "Categories",
            list(ROLE_CATEGORIES.keys()),
            default=["📊 Data & Analytics", "💼 Business & Operations"],
            label_visibility="collapsed",
            placeholder="בחר תחומים..." if lang == "he" else "Select categories...",
        )

    with c2:
        all_sectors = list(ISRAELI_COMPANIES.keys())
        scan_sectors = st.multiselect(
            "סקטורים" if lang == "he" else "Sectors",
            all_sectors,
            default=[],
            label_visibility="collapsed",
            placeholder=("כל הסקטורים" if lang == "he" else "All sectors (default)"),
        )

    with c3:
        do_scan = st.button(
            "🔎 " + ("סרוק" if lang == "he" else "Scan"),
            type="primary",
            use_container_width=True,
        )

    if do_scan:
        if not scan_cats:
            st.warning("בחר לפחות תחום אחד." if lang == "he" else "Select at least one category.")
        else:
            results = scan_all_companies(scan_cats, scan_sectors)
            # Score jobs
            for j in results:
                j["match"] = score_job(j, profile)
            results.sort(key=lambda j: (j.get("company", ""), -j.get("match", 0)))
            st.session_state.company_scan_results = results

    # ── Scan results ──────────────────────────────────────────────────────────
    scan_results: list[dict] = st.session_state.company_scan_results
    if scan_results:
        # Group by company
        grouped = {}
        for j in scan_results:
            co = j.get("company", "Unknown")
            grouped.setdefault(co, []).append(j)

        summary = (
            f"נמצאו **{len(scan_results)}** משרות מתאימות ב-**{len(grouped)}** חברות"
            if lang == "he"
            else f"Found **{len(scan_results)}** matching jobs across **{len(grouped)}** companies"
        )
        st.markdown(summary)

        for company_name, jobs in grouped.items():
            jobs_label = f"{'משרות' if lang == 'he' else 'jobs'} ({len(jobs)})"
            with st.expander(f"**{company_name}** — {jobs_label}", expanded=len(jobs) <= 3):
                for job in jobs:
                    render_job_card(job, lang, key_prefix=f"scan_{company_name}_")

    st.markdown("---")

    # ── Section 2: Browse companies ───────────────────────────────────────────
    browse_header = "🏢 עיון חברות" if lang == "he" else "🏢 Browse Companies"
    st.markdown(f"#### {browse_header}")

    # Browse controls
    bc1, bc2, bc3, bc4 = st.columns([3, 1, 1, 1])

    with bc1:
        disc_cats = st.multiselect(
            "תחומים לגילוי" if lang == "he" else "Discovery categories",
            list(ROLE_CATEGORIES.keys()),
            default=["📊 Data & Analytics", "💼 Business & Operations"],
            label_visibility="collapsed",
            key="browse_cats",
            placeholder="בחר תחומים..." if lang == "he" else "Select categories...",
        )

    with bc2:
        do_discover = st.button(
            "🔄 " + ("גלה" if lang == "he" else "Discover"),
            use_container_width=True,
        )

    with bc3:
        only_followed = st.toggle(
            "⭐ " + ("עוקבים" if lang == "he" else "Following"),
            value=False,
        )

    with bc4:
        only_active = st.toggle(
            "📋 " + ("פעילות" if lang == "he" else "Active"),
            value=False,
        )

    if do_discover:
        if not disc_cats:
            st.warning("בחר תחום.")
        else:
            disc = build_discovered_companies(disc_cats)
            st.session_state.discovered_companies = disc
            db = load_companies()
            db["discovered"] = disc
            save_companies(db)
            st.success(
                f"✅ נמצאו {len(disc)} חברות" if lang == "he"
                else f"✅ Found {len(disc)} companies"
            )

    # ── Company card helper ───────────────────────────────────────────────────
    def _company_card(co: dict, key_suffix: str = ""):
        name       = co.get("name", "")
        he_name    = co.get("he", "")
        location   = co.get("location", "")
        active     = co.get("active_jobs", 0)
        homepage   = co.get("url", "")
        career_url = co.get("career_url", "")  # only explicit career page
        has_api    = bool(co.get("greenhouse") or co.get("lever") or co.get("comeet") or co.get("ashby"))
        is_followed = name in followed

        if only_followed and not is_followed:
            return
        if only_active and active == 0:
            return

        center = ["Tel Aviv", "Givatayim", "Ramat Gan", "Petah Tikva", "Herzliya",
                  "תל אביב", "גבעתיים", "רמת גן", "פתח תקווה", "הרצליה"]
        loc_color  = "#22c55e" if any(c in location for c in center) else "#6b7280"
        jobs_color = "#22c55e" if active > 0 else "#6b7280"
        api_badge  = (" <span style='font-size:0.65rem;color:#2563eb'>● API</span>"
                      if has_api else "")

        st.markdown(f"""
        <div class="job-card" style="padding:0.8rem;margin-bottom:0.4rem">
            <b style="color:#e6edf3;font-size:0.88rem">{name}</b>{api_badge}
            {"<br><span style='color:#8b949e;font-size:0.75rem'>" + he_name + "</span>" if he_name and he_name != name else ""}
            <br>
            <span style="color:{loc_color};font-size:0.75rem">📍 {location or '—'}</span>
            {"&nbsp;·&nbsp;<span style='color:" + jobs_color + ";font-size:0.75rem'>📋 " + str(active) + "</span>" if active else ""}
        </div>""", unsafe_allow_html=True)

        fl_label = ("✓ עוקב" if is_followed else "⭐ עקוב") if lang == "he" else ("✓ Following" if is_followed else "⭐ Follow")

        # Show Career button only when we have a real career page URL (not just homepage)
        if career_url:
            c1, c2, c3 = st.columns(3)
            with c1:
                st.link_button("🚀 " + ("קריירה" if lang == "he" else "Career"),
                               url=career_url, use_container_width=True)
            with c2:
                if homepage:
                    st.link_button("🌐 " + ("אתר" if lang == "he" else "Website"),
                                   url=homepage, use_container_width=True)
            with c3:
                if st.button(fl_label, key=f"follow_{name}_{key_suffix}", use_container_width=True):
                    toggle_follow(name)
                    st.rerun()
        elif homepage:
            c1, c2 = st.columns(2)
            with c1:
                st.link_button("🌐 " + ("אתר" if lang == "he" else "Website"),
                               url=homepage, use_container_width=True)
            with c2:
                if st.button(fl_label, key=f"follow_{name}_{key_suffix}", use_container_width=True):
                    toggle_follow(name)
                    st.rerun()
        else:
            if st.button(fl_label, key=f"follow_{name}_{key_suffix}", use_container_width=True):
                toggle_follow(name)
                st.rerun()

    # ── Smart company match (RAG) ──────────────────────────────────────────────
    from modules.company_search import semantic_search, explain_matches

    st.markdown("---")
    smart_header = "🧭 התאמת חברות חכמה" if lang == "he" else "🧭 Smart Company Match"
    st.markdown(f"#### {smart_header}")
    st.caption(
        "חיפוש סמנטי (embeddings) על פני מאגר החברות, ואז Claude מסביר את ההתאמות — מבוסס רק על מה שאוחזר, לא המצאה."
        if lang == "he"
        else "Semantic search (embeddings) over the company directory, then Claude explains the matches — grounded only in what was retrieved, not invented."
    )
    smart_query = st.text_input(
        "smart_query",
        placeholder=(
            "לדוגמה: 'סטארטאפ פינטק בצמיחה עם תרבות טובה'"
            if lang == "he"
            else "e.g. 'fast-growing fintech startup with good culture'"
        ),
        label_visibility="collapsed",
        key="smart_company_query",
    )
    if st.button("🔍 " + ("חפש התאמות" if lang == "he" else "Find Matches"),
                 disabled=not smart_query.strip(), key="smart_search_btn"):
        with st.spinner("Searching..." if lang == "en" else "מחפש..."):
            matches = semantic_search(smart_query, top_k=8)
            st.session_state["smart_matches"] = matches
            st.session_state["smart_explanation"] = explain_matches(
                smart_query, profile, matches
            )

    smart_matches = st.session_state.get("smart_matches")
    if smart_matches:
        method = smart_matches[0].get("retrieval_method", "embeddings")
        st.caption(f"Retrieval method: {method}")
        explanation = st.session_state.get("smart_explanation", "")
        if explanation:
            st.info(explanation)
        smart_cols = st.columns(2)
        for i, m in enumerate(smart_matches):
            with smart_cols[i % 2]:
                _company_card(m, f"smart_{i}")

    st.markdown("---")

    # ── Sector tabs ───────────────────────────────────────────────────────────
    sectors = list(ISRAELI_COMPANIES.keys())
    discovered: list[dict] = st.session_state.get("discovered_companies", [])
    if discovered:
        sectors = sectors + ["🔍 Discovered"]

    tabs = st.tabs(sectors)

    for tab_obj, sector in zip(tabs, sectors):
        with tab_obj:
            if sector == "🔍 Discovered":
                if not discovered:
                    st.info("לחץ 'גלה' כדי לסרוק AllJobs לחברות." if lang == "he"
                            else "Click 'Discover' to scan AllJobs for companies.")
                else:
                    cols = st.columns(2)
                    for i, co in enumerate(discovered):
                        with cols[i % 2]:
                            _company_card(co, f"disc_{i}")
            else:
                companies = ISRAELI_COMPANIES.get(sector, [])
                cols = st.columns(3)
                for i, co in enumerate(companies):
                    with cols[i % 3]:
                        _company_card(co, f"{sector}_{i}")

    # ── Recruitment agencies ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🤝 " + ("חברות השמה" if lang == "he" else "Recruitment Agencies"))
    st.caption(
        "חיפוש משרות דרך חברות השמה — לוחצים על כרטיסייה לחיפוש ישיר ב-AllJobs"
        if lang == "he"
        else "Search jobs via recruitment agencies — click a card to search AllJobs directly"
    )

    agency_cols = st.columns(5)
    for i, ag in enumerate(RECRUITMENT_AGENCIES):
        with agency_cols[i % 5]:
            specs = " · ".join(ag.get("specialties", []))
            st.markdown(f"""
            <div class="job-card" style="padding:0.7rem;text-align:center;margin-bottom:0.4rem">
                <b style="color:#e6edf3;font-size:0.85rem">{ag['name']}</b><br>
                <span style="color:#8b949e;font-size:0.75rem">{ag.get('he','')}</span><br>
                <span style="color:#2563eb;font-size:0.7rem">{specs}</span>
            </div>""", unsafe_allow_html=True)
            st.link_button(
                "🔍 " + ("חפש" if lang == "he" else "Search"),
                url=ag.get("alljobs_search", ag.get("url", "#")),
                use_container_width=True,
            )
