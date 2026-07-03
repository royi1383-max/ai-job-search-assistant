import re
import json
import os
import html as _html
import requests
import streamlit as st
from datetime import datetime, timedelta
from config import (
    JOB_SEARCH_CACHE_TTL, DATA_DIR, APPLICATIONS_PATH,
    ROLE_CATEGORIES, PRIMARY_QUERIES, MATCH_KEYWORDS,
    EXPERIENCE_LEVELS, EXPERIENCE_EXCLUDE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

SOURCE_COLORS = {
    "Greenhouse": "#22c55e",
    "Lever":      "#a855f7",
    "AllJobs":    "#f59e0b",
    "Drushim":    "#3b82f6",
    "LinkedIn":   "#0a66c2",
    "Career Page":"#e11d48",
}

LOCATION_PRESETS = [
    "🏙️ Center (TLV Area)", "All Israel", "Tel Aviv", "Remote",
    "Herzliya", "Ra'anana", "Haifa", "Jerusalem", "Beer Sheva", "Custom...",
]

CENTER_CITIES = [
    "tel aviv", "תל אביב", "tlv",
    "givatayim", "גבעתיים",
    "ramat gan", "רמת גן",
    "petah tikva", "פתח תקווה", "petach tikva", "petah tiqwa",
    "herzliya", "הרצליה", "herzlia", "herzliyya",
    "holon", "חולון",
    "bat yam", "בת ים",
    "bnei brak", "בני ברק", "bnai brak",
    "givat shmuel", "גבעת שמואל",
    "azrieli", "azriel",          # landmarks that appear as locations
    "tel-aviv",                   # hyphenated variant
    "jaffa", "יפו", "yafo",        # Tel Aviv-Yafo alias
]

_LOCATION_ALIASES = {
    "center": CENTER_CITIES,
    "tel aviv": ["tel aviv", "tlv", "תל אביב"],
    "remote": ["remote", "anywhere", "worldwide", "global", "fully remote",
               "work from home", "wfh"],
    "herzliya": ["herzliya", "herzlia", "herzliyya", "הרצליה"],
    "ra'anana": ["ra'anana", "raanana", "רעננה"],
    "haifa": ["haifa", "חיפה", "kirya", "tirat carmel", "krayot"],
    "jerusalem": ["jerusalem", "ירושלים", "har hotzvim"],
    "beer sheva": ["beer sheva", "be'er sheva", "beersheba", "באר שבע"],
}


# ── Location filter ───────────────────────────────────────────────────────────

def _filter_by_location(jobs: list[dict], location: str) -> list[dict]:
    if not location or location.lower() in ("", "all israel", "all"):
        return jobs
    loc_lower = location.lower().strip()
    allowed = _LOCATION_ALIASES.get(loc_lower, [loc_lower])
    result = []
    for j in jobs:
        job_loc = (j.get("location") or "").lower()
        if not job_loc or any(a in job_loc for a in allowed):
            result.append(j)
    return result


# ── Experience filter (for Greenhouse/Lever which don't have URL params) ─────

def _filter_by_experience(jobs: list[dict], exp_level: str) -> list[dict]:
    exclude = EXPERIENCE_EXCLUDE.get(exp_level, [])
    if not exclude:
        return jobs
    out = []
    for j in jobs:
        title = j.get("title", "").lower()
        if not any(w in title for w in exclude):
            out.append(j)
    return out


# ── Date filter ──────────────────────────────────────────────────────────────

import re as _re

def _parse_date_str(s: str) -> str:
    """Convert various date formats to YYYY-MM-DD, or '' if unparseable."""
    s = s.strip()
    if not s:
        return ""
    today = datetime.now()
    # ISO / already clean
    try:
        datetime.strptime(s[:10], "%Y-%m-%d")
        return s[:10]
    except ValueError:
        pass
    # DD/MM/YYYY
    try:
        return datetime.strptime(s, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    # Hebrew relative: "לפני N ימים"
    m = _re.search(r'לפני\s+(\d+)\s+ימי?', s)
    if m:
        return (today - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if "היום" in s or "שעה" in s or "שעות" in s or "ago" in s.lower():
        return today.strftime("%Y-%m-%d")
    if "אתמול" in s or "yesterday" in s.lower():
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if "שבוע" in s or "week" in s.lower():
        return (today - timedelta(days=7)).strftime("%Y-%m-%d")
    return ""


def _filter_by_date(jobs: list[dict], days: int = 7) -> list[dict]:
    """Keep only jobs posted in the last `days` days. Jobs with no date pass through."""
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for j in jobs:
        date_str = _parse_date_str(j.get("date") or "")
        if not date_str:
            result.append(j)  # unknown date → keep
            continue
        try:
            if datetime.strptime(date_str, "%Y-%m-%d") >= cutoff:
                result.append(j)
        except ValueError:
            result.append(j)
    return result


# ── Category post-filter ──────────────────────────────────────────────────────

def _matches_categories(job: dict, selected_categories: list[str]) -> bool:
    """Return True if job title contains at least one keyword from any selected category."""
    if not selected_categories:
        return True
    title = job.get("title", "").lower()
    for cat in selected_categories:
        if any(kw in title for kw in MATCH_KEYWORDS.get(cat, [])):
            return True
    return False


# ── Build broad query list ────────────────────────────────────────────────────

def get_search_queries(selected_categories: list[str], custom: str = "") -> list[str]:
    """Return a deduplicated list of broad search queries for the selected categories."""
    queries = []
    for cat in selected_categories:
        queries.extend(PRIMARY_QUERIES.get(cat, []))
    if custom.strip():
        queries.append(custom.strip())
    return list(dict.fromkeys(queries))


# ── Greenhouse public API ─────────────────────────────────────────────────────

# Only boards confirmed live (tested 2026-06). Others return 404.
GREENHOUSE_BOARDS = [
    # Israeli tech / SaaS
    "nice", "taboola", "appsflyer", "similarweb", "axonius", "pendo",
    "lightricks", "sisense", "myheritage", "cybereason", "sealights",
    "riskified", "transmitsecurity", "catonetworks", "innovid",
    "jfrog", "liveperson", "optimove", "bigid", "yotpo", "forter",
    "orioninnovation", "bringg", "torq", "doubleverify",
    # Israeli-founded, global
    "wolt", "payoneer",
    # Global companies with large Israel R&D
    "samsara", "unity3d", "commvault",
]


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_greenhouse(board: str) -> list[dict]:
    """Fetch ALL jobs from one Greenhouse board (cached by board only — no query duplication)."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json().get("jobs", [])
        results = []
        for job in data:
            title    = job.get("title", "")
            location = (job.get("location") or {}).get("name", "")
            content  = job.get("content", "")
            results.append({
                "id": f"gh_{board}_{job.get('id')}",
                "title": title,
                "company": board.replace("-", " ").title(),
                "location": location,
                "source": "Greenhouse",
                "url": job.get("absolute_url", ""),
                "description": _strip_html(content)[:1500],
                "date": job.get("updated_at", "")[:10],
            })
        return results
    except Exception:
        return []


# ── Lever public API ──────────────────────────────────────────────────────────

# Only boards confirmed live (tested 2025-06).
LEVER_BOARDS = [
    "walkme", "cloudinary", "houzz",
]


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_lever(board: str) -> list[dict]:
    """Fetch ALL jobs from one Lever board (cached by board only — no query duplication)."""
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        results = []
        for job in data:
            title    = job.get("text", "")
            location = job.get("categories", {}).get("location", "")
            desc_blocks = job.get("descriptionBody", {}).get("descriptionBody", "") or ""
            results.append({
                "id": f"lv_{board}_{job.get('id')}",
                "title": title,
                "company": board.replace("-", " ").title(),
                "location": location,
                "source": "Lever",
                "url": job.get("hostedUrl", ""),
                "description": _strip_html(str(desc_blocks))[:1500],
                "date": datetime.fromtimestamp(
                    job.get("createdAt", 0) / 1000
                ).strftime("%Y-%m-%d") if job.get("createdAt") else "",
            })
        return results
    except Exception:
        return []


# ── AllJobs scraper ───────────────────────────────────────────────────────────

@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_alljobs(query: str, location: str = "תל אביב", exp_code: str = "") -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        q = requests.utils.quote(query)
        loc = requests.utils.quote(location) if location and location.lower() not in ("all israel", "") else ""
        exp_param = f"&Experience={exp_code}" if exp_code else ""
        url = (
            f"https://www.alljobs.co.il/SearchResultsGuest.aspx"
            f"?FromGoogle=True&Position={q}&City={loc}{exp_param}&Days=7"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for card in soup.select(".cjob-content")[:25]:
            title_el   = card.select_one(".cjob-content-title a")
            company_el = card.select_one(".cjob-content-company")
            loc_el     = card.select_one(".cjob-content-city")
            desc_el    = card.select_one(".cjob-content-desc")
            date_el    = (card.select_one(".cjob-content-date") or
                          card.select_one(".date") or
                          card.select_one("[class*='date']") or
                          card.select_one(".time"))
            if not title_el:
                continue
            href = title_el.get("href", "")
            full_url = ("https://www.alljobs.co.il" + href) if href.startswith("/") else href
            results.append({
                "id": f"aj_{abs(hash(title_el.text + (company_el.text if company_el else '')))}",
                "title": title_el.text.strip(),
                "company": company_el.text.strip() if company_el else "",
                "location": loc_el.text.strip() if loc_el else location,
                "source": "AllJobs",
                "url": full_url,
                "description": desc_el.text.strip() if desc_el else "",
                "date": _parse_date_str(date_el.text.strip() if date_el else ""),
            })
        return results
    except Exception:
        return []


# ── Drushim scraper ───────────────────────────────────────────────────────────

@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_drushim(query: str, location: str = "", exp_code: str = "0") -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        q = requests.utils.quote(query)
        base = "https://www.drushim.co.il"
        loc_param = ""
        if location and location.lower() not in ("all israel", ""):
            loc_map = {
                "tel aviv": "תל-אביב", "remote": "", "haifa": "חיפה",
                "jerusalem": "ירושלים", "herzliya": "הרצליה",
                "ra'anana": "רעננה", "beer sheva": "באר-שבע",
                "ramat gan": "רמת-גן", "petah tikva": "פתח-תקווה",
                "givatayim": "גבעתיים",
            }
            he_city = loc_map.get(location.lower(), location)
            if he_city:
                loc_param = f"&city={requests.utils.quote(he_city)}"

        exp_param = f"&experience={exp_code}" if exp_code and exp_code != "0" else "&experience=0"
        url = f"{base}/jobs/search/?q={q}{loc_param}&jobCat=0{exp_param}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for card in soup.select(".job-item, .list-item, article.job")[:25]:
            title_el = (card.select_one(".job-title a") or
                        card.select_one("h2 a") or
                        card.select_one(".title a"))
            company_el = (card.select_one(".company-name") or
                          card.select_one(".employer") or
                          card.select_one(".company"))
            loc_el = (card.select_one(".job-location") or
                      card.select_one(".location") or
                      card.select_one(".city"))
            desc_el = (card.select_one(".job-description") or
                       card.select_one(".description") or
                       card.select_one("p"))
            date_el = (card.select_one(".job-date") or
                       card.select_one(".date") or
                       card.select_one("[class*='date']") or
                       card.select_one(".time-since") or
                       card.select_one("time"))

            if not title_el:
                continue
            href = title_el.get("href", "")
            full_url = (base + href) if href.startswith("/") else href
            date_raw = (date_el.get("datetime") or date_el.text) if date_el else ""
            results.append({
                "id": f"dr_{abs(hash(title_el.text.strip() + href))}",
                "title": title_el.text.strip(),
                "company": company_el.text.strip() if company_el else "",
                "location": loc_el.text.strip() if loc_el else location,
                "source": "Drushim",
                "url": full_url or f"{base}/jobs/search/?q={q}",
                "description": desc_el.text.strip()[:300] if desc_el else "",
                "date": _parse_date_str(date_raw.strip()),
            })
        return results
    except Exception:
        return []


# ── LinkedIn public jobs API ──────────────────────────────────────────────────

def _parse_linkedin_cards(soup, loc: str) -> list[dict]:
    """Extract job dicts from a parsed LinkedIn search results page."""
    results = []
    for card in soup.select("li"):
        title_el   = card.select_one(".base-search-card__title, h3")
        company_el = card.select_one(".base-search-card__subtitle, h4")
        loc_el     = card.select_one(".job-search-card__location, .base-search-card__metadata span")
        link_el    = card.select_one("a.base-card__full-link, a[href*='linkedin.com/jobs']")
        time_el    = card.select_one("time[datetime]")
        if not title_el or not link_el:
            continue
        href = link_el.get("href", "").split("?")[0]
        results.append({
            "id": f"li_{abs(hash(href))}",
            "title": title_el.text.strip(),
            "company": company_el.text.strip() if company_el else "",
            "location": loc_el.text.strip() if loc_el else loc,
            "source": "LinkedIn",
            "url": href,
            "description": "",
            "date": time_el.get("datetime", "") if time_el else "",
        })
    return results


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_linkedin(query: str, location: str = "Israel") -> list[dict]:
    """Fetch up to 75 LinkedIn jobs (3 pages × 25), sorted by date. Local date filter applied later."""
    try:
        from bs4 import BeautifulSoup
        loc = location if location and location.lower() not in ("all israel", "") else "Israel"
        base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        results = []
        for start in (0, 25, 50):
            params = {
                "keywords": query,
                "location": loc,
                "start": start,
                "count": 25,
                "sortBy": "DD",    # date descending → freshest first
            }
            r = requests.get(base_url, params=params, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            page = _parse_linkedin_cards(soup, loc)
            results.extend(page)
            if len(page) < 25:
                break
        return results
    except Exception:
        return []


# ── Aggregator ────────────────────────────────────────────────────────────────

def search_jobs(
    queries: list[str],
    sources: list[str],
    location: str = "",
    exp_level: str = "All Levels",
    selected_categories: list[str] | None = None,
) -> list[dict]:
    loc_clean = location.strip() if location else ""
    exp_cfg = EXPERIENCE_LEVELS.get(exp_level, EXPERIENCE_LEVELS["All Levels"])
    alljobs_exp = exp_cfg["alljobs_code"]
    drushim_exp = exp_cfg["drushim_code"]

    # Determine AllJobs / Drushim city string
    if loc_clean.lower() == "center":
        alljobs_loc = "תל אביב"
        drushim_loc = "tel aviv"
        li_loc = "Tel Aviv, Israel"
    elif loc_clean.lower() in ("all israel", ""):
        alljobs_loc = ""
        drushim_loc = ""
        li_loc = "Israel"
    else:
        alljobs_loc = loc_clean
        drushim_loc = loc_clean
        li_loc = loc_clean

    results = []

    # Greenhouse & Lever: fetch each board ONCE (cached), filter locally — avoids N_boards × N_queries requests
    if "Greenhouse" in sources:
        gh_jobs = []
        for board in GREENHOUSE_BOARDS:
            gh_jobs.extend(_fetch_greenhouse(board))
        gh_jobs = _filter_by_location(gh_jobs, loc_clean)
        gh_jobs = _filter_by_experience(gh_jobs, exp_level)
        results.extend(gh_jobs)

    if "Lever" in sources:
        lv_jobs = []
        for board in LEVER_BOARDS:
            lv_jobs.extend(_fetch_lever(board))
        lv_jobs = _filter_by_location(lv_jobs, loc_clean)
        lv_jobs = _filter_by_experience(lv_jobs, exp_level)
        results.extend(lv_jobs)

    # LinkedIn: one request per query; filter location locally too (server-side geo is imprecise)
    if "LinkedIn" in sources:
        li_raw = []
        for query in queries:
            li_raw.extend(_fetch_linkedin(query, li_loc))
        li_raw = _filter_by_location(li_raw, loc_clean)
        results.extend(li_raw)

    # AllJobs: one request per query; source already applies city/experience server-side
    if "AllJobs" in sources:
        aj_jobs = []
        for query in queries:
            aj_jobs.extend(_fetch_alljobs(query, alljobs_loc, alljobs_exp))
        aj_jobs = _filter_by_location(aj_jobs, loc_clean)
        aj_jobs = _filter_by_experience(aj_jobs, exp_level)
        results.extend(aj_jobs)

    # Drushim: one request per query; source already applies city/experience server-side
    if "Drushim" in sources:
        dr_jobs = []
        for query in queries:
            dr_jobs.extend(_fetch_drushim(query, drushim_loc, drushim_exp))
        dr_jobs = _filter_by_location(dr_jobs, loc_clean)
        dr_jobs = _filter_by_experience(dr_jobs, exp_level)
        results.extend(dr_jobs)

    # Deduplicate by id
    seen: set[str] = set()
    unique = []
    for job in results:
        if job["id"] not in seen:
            seen.add(job["id"])
            unique.append(job)

    # Post-filter by selected categories (title matching)
    if selected_categories:
        unique = [j for j in unique if _matches_categories(j, selected_categories)]

    # Date filter: LinkedIn results are stale after 7 days (search ranking drops them).
    # GH/Lever jobs stay active on company boards until filled — no date cutoff needed.
    li_results = [j for j in unique if j.get("source") == "LinkedIn"]
    other_results = [j for j in unique if j.get("source") != "LinkedIn"]
    li_results = _filter_by_date(li_results, days=7)
    unique = other_results + li_results

    return unique


# ── Match scoring ─────────────────────────────────────────────────────────────

def score_job(job: dict, profile: dict) -> float:
    text = (job.get("title", "") + " " + job.get("description", "")).lower()
    skills = [s.lower() for s in profile.get("skills", [])]
    roles = [r.lower() for r in profile.get("target_roles", [])]

    if not skills and not roles:
        return 0.5

    skill_hits = sum(1 for s in skills if s in text)
    role_hits = sum(1 for r in roles if r in text)

    skill_score = min(skill_hits / max(len(skills), 1), 1.0)
    role_score = min(role_hits * 2 / max(len(roles), 1), 1.0)

    return round(0.6 * skill_score + 0.4 * role_score, 2)


def explain_match(job: dict, profile: dict) -> tuple[list[str], list[str]]:
    text = (job.get("title", "") + " " + job.get("description", "")).lower()
    skills = profile.get("skills", [])
    strengths = [s for s in skills if s.lower() in text]
    gaps = [s for s in skills if s.lower() not in text]
    return strengths[:8], gaps[:5]


# ── Helper ────────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    clean = re.sub(r'<[^>]+>', ' ', html)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


# ── UI ─────────────────────────────────────────────────────────────────────────

def render_job_card(job: dict, lang: str, key_prefix: str = "") -> None:
    """Reusable job card with CV / Save / Apply buttons. Importable by other modules."""
    match_pct = int(job.get("match", 0) * 100)
    match_class = (
        "match-high" if match_pct >= 70
        else ("match-medium" if match_pct >= 40 else "match-low")
    )
    src_color = SOURCE_COLORS.get(job.get("source", ""), "#6b7280")
    desc_raw = (job.get("description") or "").strip()

    # Escape all dynamic values to prevent broken HTML rendering
    title    = _html.escape(job.get("title", ""))
    company  = _html.escape(job.get("company", ""))
    location = _html.escape(job.get("location", ""))
    date     = _html.escape(job.get("date", ""))
    source   = _html.escape(job.get("source", ""))
    desc     = _html.escape(desc_raw[:220])

    loc_part  = f'&nbsp;·&nbsp;<span style="color:#6b7280;font-size:0.9rem">{location}</span>' if location else ""
    date_part = f'&nbsp;·&nbsp;<span style="color:#6b7280;font-size:0.78rem">{date}</span>' if date else ""
    desc_part = (
        f'<div style="color:#8b949e;font-size:0.82rem;margin-top:0.5rem">'
        f'{desc}{"..." if len(desc_raw) > 220 else ""}</div>'
    ) if desc else ""

    with st.container():
        st.markdown(
            f'<div class="job-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div>'
            f'<span style="font-size:1.05rem;font-weight:600;color:#e6edf3">{title}</span><br>'
            f'<span style="color:#6b7280;font-size:0.9rem">{company}</span>'
            f'{loc_part}{date_part}'
            f'</div>'
            f'<div style="text-align:right;flex-shrink:0;margin-left:1rem">'
            f'<span class="{match_class}" style="font-size:1.1rem">{match_pct}%</span><br>'
            f'<span style="color:{src_color};font-size:0.75rem">● {source}</span>'
            f'</div>'
            f'</div>'
            f'{desc_part}'
            f'</div>',
            unsafe_allow_html=True,
        )

        profile = st.session_state.get("profile", {})
        if profile.get("skills"):
            strengths, gaps = explain_match(job, profile)
            if strengths or gaps:
                with st.expander("🎯 " + ("התאמת כישורים" if lang == "he" else "Skill Match")):
                    if strengths:
                        st.markdown(
                            ("יש לך: " if lang == "he" else "You have: ")
                            + " ".join(f'<span class="skill-tag tag-green">{_html.escape(s)}</span>' for s in strengths),
                            unsafe_allow_html=True,
                        )
                    if gaps:
                        st.markdown(
                            ("חסר לך: " if lang == "he" else "Missing: ")
                            + " ".join(f'<span class="skill-tag tag-red">{_html.escape(g)}</span>' for g in gaps),
                            unsafe_allow_html=True,
                        )

        uid = job.get("id", abs(hash(job.get("url", "") + job.get("title", ""))))
        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st.button("📄 " + ("קו\"ח" if lang == "he" else "Build CV"),
                         key=f"{key_prefix}cv_{uid}"):
                st.session_state.selected_job = job
                st.session_state.page = "cv"
                st.rerun()
        with c2:
            if st.button("🔖 " + ("שמור" if lang == "he" else "Save"),
                         key=f"{key_prefix}save_{uid}"):
                saved = st.session_state.get("saved_jobs", [])
                if job not in saved:
                    saved.append(job)
                    st.session_state.saved_jobs = saved
                    _add_to_tracker(job)
                st.toast("✅ " + ("נשמר!" if lang == "he" else "Saved!"))
        with c3:
            if job.get("url"):
                st.link_button(
                    "🚀 " + ("הגש מועמדות" if lang == "he" else "Apply Now"),
                    url=job["url"],
                )


def render(lang: str):
    title = "🔍 חיפוש משרות" if lang == "he" else "🔍 Job Search"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    tab_jobs, tab_companies = st.tabs(
        ["📋 משרות", "🏢 חברות"] if lang == "he" else ["📋 Jobs", "🏢 Companies"]
    )

    with tab_jobs:
        _render_jobs(lang)

    with tab_companies:
        from modules.company_db import render as render_companies
        render_companies(lang)


def _render_jobs(lang: str):
    profile = st.session_state.profile

    # ── Row 1: Category + Experience ─────────────────────────────────────────
    col_cat, col_exp = st.columns([5, 2])
    location = "center"  # always filter to TLV area

    with col_cat:
        cat_label = "תחומים" if lang == "he" else "Role Categories"
        selected_categories = st.multiselect(
            cat_label,
            list(ROLE_CATEGORIES.keys()),
            default=["📊 Data & Analytics", "💼 Business & Operations"],
            label_visibility="collapsed",
            placeholder="בחר תחומים..." if lang == "he" else "Select categories...",
        )

    with col_exp:
        exp_levels = list(EXPERIENCE_LEVELS.keys())
        exp_level = st.selectbox(
            "ניסיון" if lang == "he" else "Experience",
            exp_levels,
            index=exp_levels.index("Mid (2-5 שנים)"),
            label_visibility="collapsed",
        )

    # ── Row 2: Custom role + Sources + Search ────────────────────────────────
    col_custom, col_src, col_btn = st.columns([3, 2, 1])

    with col_custom:
        custom_role = st.text_input(
            "תפקיד ספציפי (אופציונלי)" if lang == "he" else "Specific role (optional)",
            placeholder="e.g. Pricing Analyst",
            label_visibility="collapsed",
        )

    with col_src:
        sources = st.multiselect(
            "מקורות" if lang == "he" else "Sources",
            ["Greenhouse", "Lever", "LinkedIn", "AllJobs", "Drushim"],
            default=["Greenhouse", "Lever", "LinkedIn", "AllJobs", "Drushim"],
            label_visibility="collapsed",
        )

    with col_btn:
        search_label = "🔍 " + ("חפש" if lang == "he" else "Search")
        do_search = st.button(search_label, type="primary", use_container_width=True)

    # ── Run search ────────────────────────────────────────────────────────────
    if do_search:
        if not selected_categories and not custom_role.strip():
            st.warning("בחר לפחות תחום אחד או הכנס תפקיד ספציפי." if lang == "he"
                       else "Select at least one category or enter a specific role.")
            return

        queries = get_search_queries(selected_categories, custom_role)

        # Build a human-readable summary of what we're searching
        all_titles = []
        for cat in selected_categories:
            all_titles.extend(ROLE_CATEGORIES.get(cat, []))
        if custom_role.strip():
            all_titles.append(custom_role.strip())

        with st.spinner("מחפש משרות..." if lang == "he" else "Searching jobs..."):
            jobs = search_jobs(queries, sources, location, exp_level, selected_categories)
            for job in jobs:
                job["match"] = score_job(job, profile)
            jobs.sort(key=lambda j: (j.get("date") or "0000-00-00", j["match"]), reverse=True)
            st.session_state.job_results = jobs
            st.session_state.job_query = ", ".join(queries[:3])
            st.session_state.selected_categories = selected_categories

    jobs = st.session_state.get("job_results", [])

    if not jobs:
        st.info("לא נמצאו משרות — לחץ חפש." if lang == "he" else "No results yet — click Search.")
        return

    filtered = jobs

    # Source breakdown chips
    src_counts: dict[str, int] = {}
    for j in filtered:
        src_counts[j["source"]] = src_counts.get(j["source"], 0) + 1

    count_label = (
        f"נמצאו **{len(filtered)}** משרות" if lang == "he"
        else f"Found **{len(filtered)}** jobs"
    )
    chips = "  ".join(
        f'<span style="color:{SOURCE_COLORS.get(s,"#6b7280")};font-size:0.78rem">● {s} ({n})</span>'
        for s, n in src_counts.items()
    )
    st.markdown(
        f'<div style="margin-bottom:0.75rem">{count_label} &nbsp;&nbsp; {chips}</div>',
        unsafe_allow_html=True,
    )

    # ── Job cards ─────────────────────────────────────────────────────────────
    for job in filtered:
        render_job_card(job, lang, key_prefix="js_")


def _add_to_tracker(job: dict):
    apps = st.session_state.get("applications", [])
    if any(a.get("id") == job.get("id") for a in apps):
        return
    apps.append({
        "id": job.get("id"),
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "url": job.get("url"),
        "source": job.get("source"),
        "stage": "Saved",
        "date_saved": datetime.now().strftime("%Y-%m-%d"),
        "date_applied": "",
        "notes": "",
        "cv_version": "",
    })
    st.session_state.applications = apps
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(APPLICATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)
