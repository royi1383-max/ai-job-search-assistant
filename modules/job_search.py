import re
import json
import os
import html as _html
import concurrent.futures
import requests
import streamlit as st
from datetime import datetime, timedelta
from config import (
    JOB_SEARCH_CACHE_TTL, DATA_DIR, APPLICATIONS_PATH,
    ROLE_CATEGORIES, PRIMARY_QUERIES, MATCH_KEYWORDS,
    PRIMARY_QUERIES_HE, MATCH_KEYWORDS_HE,
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
    "SmartRecruiters": "#00b074",
    "JobMaster":  "#d946ef",
    "Comeet":     "#f97316",
    "Ashby":      "#7c3aed",
}

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
    if "היום" in s or "שעה" in s or "שעות" in s or "דקה" in s or "דקות" in s or "ago" in s.lower():
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

# Hebrew job titles use inconsistent gender-suffix notation ("אנליסט/ית",
# "אנליסט.ית", "אנליסטית") and final-letter (sofit) forms shift when a suffix
# is attached (e.g. "כלכלן" -> "כלכלנ.ית"). Plain substring matching (used for
# the English keywords) misses these, so Hebrew keywords are matched via a
# normalized, suffix-tolerant regex instead: each word-stem may appear
# anywhere in the title (order-independent, to also catch compound titles
# like "אנליסט/ית מומחה/ית אשראי"), with an optional trailing suffix.
_HE_SOFIT_MAP = str.maketrans("ךםןףץ", "כמנפצ")
_he_pattern_cache: dict[str, re.Pattern] = {}


def _normalize_he(s: str) -> str:
    return s.translate(_HE_SOFIT_MAP)


def _he_keyword_pattern(phrase: str) -> re.Pattern:
    if phrase not in _he_pattern_cache:
        words = _normalize_he(phrase).split(" ")
        lookaheads = "".join(f"(?=.*{re.escape(w)}\\S{{0,3}})" for w in words)
        _he_pattern_cache[phrase] = re.compile(lookaheads)
    return _he_pattern_cache[phrase]


def _matches_categories(job: dict, selected_categories: list[str]) -> bool:
    """Return True if job title contains at least one keyword from any selected category."""
    if not selected_categories:
        return True
    title = job.get("title", "").lower()
    title_he = _normalize_he(job.get("title", ""))
    for cat in selected_categories:
        if any(kw in title for kw in MATCH_KEYWORDS.get(cat, [])):
            return True
        if any(_he_keyword_pattern(kw).search(title_he) for kw in MATCH_KEYWORDS_HE.get(cat, [])):
            return True
    return False


# ── Build broad query list ────────────────────────────────────────────────────

def get_search_queries(selected_categories: list[str], custom: str = "") -> list[str]:
    """Return a deduplicated list of broad search queries for the selected categories."""
    queries = []
    for cat in selected_categories:
        queries.extend(PRIMARY_QUERIES.get(cat, []))
        queries.extend(PRIMARY_QUERIES_HE.get(cat, []))
    if custom.strip():
        queries.append(custom.strip())
    return list(dict.fromkeys(queries))


# ── Greenhouse public API ─────────────────────────────────────────────────────

# Only boards confirmed live (tested 2026-07). Others return 404, or resolve
# to an unrelated company squatting the same slug (verified by checking actual
# job content/locations, not just HTTP 200 — e.g. "orca", "bcg", "iai", "corp"
# all 200'd but were unrelated companies and were excluded).
GREENHOUSE_BOARDS = [
    # Israeli tech / SaaS
    "nice", "taboola", "appsflyer", "similarweb", "axonius", "pendo",
    "lightricks", "sisense", "myheritage", "cybereason", "sealights",
    "riskified", "transmitsecurity", "catonetworks", "innovid",
    "jfrog", "liveperson", "optimove", "bigid", "yotpo", "forter",
    "orioninnovation", "bringg", "torq", "doubleverify",
    "connecteam", "orcasecurity", "openweb",
    # Israeli-founded, global
    "wolt", "payoneer", "melio", "pagaya",
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

# Only boards confirmed live (tested 2026-07).
LEVER_BOARDS = [
    "walkme", "cloudinary", "houzz", "logz",
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


# ── SmartRecruiters public API ────────────────────────────────────────────────

# Only slugs confirmed live (tested 2026-07). Others 404 or resolve to an
# unrelated company squatting the same slug.
SMARTRECRUITERS_BOARDS = ["atera"]


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_smartrecruiters(company: str) -> list[dict]:
    """Fetch ALL jobs from one SmartRecruiters company board (cached by company only)."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json().get("content", [])
        results = []
        for job in data:
            loc = job.get("location", {}) or {}
            city = loc.get("city", "") or ("Remote" if loc.get("remote") else "")
            company_name = (job.get("company", {}) or {}).get("name", "") or company.replace("-", " ").title()
            results.append({
                "id": f"sr_{company}_{job.get('id')}",
                "title": job.get("name", ""),
                "company": company_name,
                "location": city,
                "source": "SmartRecruiters",
                "url": f"https://jobs.smartrecruiters.com/{company_name.replace(' ', '')}/{job.get('id')}",
                "description": "",
                "date": (job.get("releasedDate") or "")[:10],
            })
        return results
    except Exception:
        return []


# ── Ashby public API ──────────────────────────────────────────────────────────

# Only slugs confirmed live (tested 2026-07) by inspecting actual job content —
# a same-name slug can belong to an unrelated global company (e.g. "aleph"
# resolves to a US FP&A startup, not the Israeli Aleph VC — excluded).
ASHBY_BOARDS = ["lemonade", "honeybook", "redis", "nexxen", "aquant"]


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_ashby(board: str) -> list[dict]:
    """Fetch ALL jobs from one Ashby job board (cached by board only)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json().get("jobs", [])
        results = []
        for job in data:
            results.append({
                "id": f"ab_{board}_{job.get('id')}",
                "title": job.get("title", ""),
                "company": board.replace("-", " ").title(),
                "location": job.get("location", ""),
                "source": "Ashby",
                "url": job.get("jobUrl", ""),
                "description": _strip_html(job.get("descriptionHtml", ""))[:1500],
                "date": (job.get("publishedAt") or "")[:10],
            })
        return results
    except Exception:
        return []


# ── Comeet public API ─────────────────────────────────────────────────────────

# Each entry is ("url", slug, uid) for the hosted-page pattern (jobs embedded as
# inline JS on comeet.com/jobs/{slug}/{uid}) or ("widget", uid, token) for the
# self-hosted-widget pattern (calls the careers-api directly). Only entries
# confirmed live (tested 2026-07) by inspecting actual job content — Comeet
# returns 400 for a bad token/uid, but a stale placeholder token from their own
# docs (seen copy-pasted on some company sites) also 400s, so absence here can
# mean "found the embed but the token was fake," not just "no Comeet usage."
COMEET_BOARDS = [
    ("url", "cyera", "17.008"),
    ("url", "riverside-fm", "66.009"),
    ("url", "bizzabo", "A5.000"),
    ("url", "explorium", "B4.00E"),
    ("url", "immunai", "37.009"),
    ("url", "cognyte", "F2.009"),
    ("url", "infinidat", "D6.003"),
    ("url", "team8", "61.003"),
    ("url", "joinattil", "38.00A"),          # AT&T Israel
    ("widget", "A2.00C", "2ACD5C02AC10081008AB01560180C804"),  # Moon Active
    ("widget", "41.009", "14952452466D3DB7B61495240B91"),      # eToro
    ("widget", "B1.001", "1B16C4BD7005131B1A26F391B1"),        # Overwolf
    ("url", "fiverr", "60.002"),
    ("url", "buyme", "B2.008"),
    ("url", "pango", "59.002"),
    ("widget", "62.002", "262988131015727264C41572E4C10AE10AE"),  # Global-e
    ("widget", "28.003", "82330D228AF208C208C493B30D2208C38F538F5"),  # SuperPlay
    # These 6 were extracted directly from each company's own rendered career
    # page (via a headless browser, since the widget loads jobs client-side
    # with no static HTML footprint) rather than guessed — so identity is
    # certain even where 0 jobs are currently open (Gloat, Elementor).
    ("widget", "D3.00A", "3DA1342F683DA22AAF683DAF68F68F68"),      # OurCrowd
    ("widget", "67.00A", "76A2C7C163E33E625122C7C1DA8ED476A33E6"), # Medison Pharma
    ("widget", "A1.00C", "1AC6B0A08F0C6B085C85CD60F0C1AC"),        # Lumenis
    ("widget", "94.009", "49916FD16FD24C89320DCB296116FD2961"),   # Tailor Brands
    ("widget", "E5.000", "5E02340002F0017800234011A01780"),       # Gloat
    ("widget", "A3.00F", "3AF126BB0D161A3AF161A00B0D126B"),       # Elementor
    ("widget", "10.000", "1030901050040401080"),                 # Guesty
    ("widget", "32.00E", "23E8F811F0B360FB20142E8F8142E"),        # Crazy Labs
    ("url", "earnix", "93.00B"),
    ("widget", "63.00B", "36B11176D614826D66D611171482DAC17ED"),  # Atera
    ("url", "blinkops", "C7.004"),
    ("url", "evinced", "28.000"),
    ("url", "ironscales", "1A.007"),
    ("url", "reco", "3A.00D"),
    ("widget", "71.000", "1705C07307305C04508A02E07302E0"),       # SundaySky
    ("widget", "56.007", "657260A657195C1FB332B8390F195C6571305"), # Swimm
    ("url", "bigabid", "A4.003"),
    ("widget", "22.00A", "22A8A81150454CFC115022AAD2F26137A"),    # Skai
    ("url", "trigo", "A6.005"),
    ("url", "papayaglobal", "16.005"),
]


def _parse_comeet_positions(data: list, uid: str) -> list[dict]:
    results = []
    for job in data:
        loc = job.get("location", {}) or {}
        city = loc.get("name") or loc.get("city") or ("Remote" if loc.get("is_remote") else "")
        desc = ""
        details = (job.get("custom_fields") or {}).get("details") or []
        for d in details:
            if d.get("name") == "Description" and d.get("value"):
                desc = _strip_html(d["value"])[:1500]
                break
        results.append({
            "id": f"cm_{uid}_{job.get('uid')}",
            "title": job.get("name", ""),
            "company": job.get("company_name", ""),
            "location": city,
            "source": "Comeet",
            "url": job.get("url_active_page") or job.get("url_comeet_hosted_page", ""),
            "description": desc,
            "date": (job.get("time_updated") or "")[:10],
        })
    return results


@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_comeet(kind: str, a: str, b: str) -> list[dict]:
    """Fetch ALL jobs from one Comeet board (cached by board only)."""
    try:
        if kind == "url":
            slug, uid = a, b
            r = requests.get(f"https://www.comeet.com/jobs/{slug}/{uid}", headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return []
            m = _re.search(r'COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\]);', r.text, re.S)
            if not m:
                return []
            data = json.loads(m.group(1))
        else:
            uid, token = a, b
            r = requests.get(
                f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions",
                params={"token": token}, headers=HEADERS, timeout=10,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            if not isinstance(data, list):
                return []
        return _parse_comeet_positions(data, uid)
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
    """Fetch jobs from Drushim (tested 2026-07). Site migrated to a Vue/Nuxt SPA
    with a path-based search URL (`/jobs/search/{query}/`) — the old
    `?q=`/`?city=` query-param scheme now 404s server-side and silently
    returns 0 jobs. No location param is sent server-side (Drushim now
    requires an opaque geolexid per city instead of a plain name); city
    narrowing is left to the existing client-side _filter_by_location()
    post-filter in search_jobs(), which already runs for every source.
    """
    try:
        from bs4 import BeautifulSoup
        base = "https://www.drushim.co.il"
        q = requests.utils.quote(query, safe="")
        url = f"{base}/jobs/search/{q}/?ssaen=1"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for card in soup.select(".job-item")[:25]:
            title_el = card.select_one(".job-url")
            company_el = card.select_one(".job-details-top span.font-weight-medium")
            loc_el = card.select_one(".job-details-sub .ml-2")
            date_el = card.select_one("span.display-18.inline-flex")
            href_el = card.select_one("a[href^='/job/']")
            desc_el = card.select_one(".job-intro p")

            if not title_el:
                continue
            href = href_el.get("href", "") if href_el else ""
            full_url = (base + href) if href.startswith("/") else href
            loc_text = loc_el.get_text(strip=True).rstrip("|").strip() if loc_el else ""
            results.append({
                "id": f"dr_{abs(hash(title_el.get_text(strip=True) + href))}",
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": loc_text or location,
                "source": "Drushim",
                "url": full_url or url,
                "description": desc_el.get_text(strip=True)[:300] if desc_el else "",
                "date": _parse_date_str(date_el.get_text(strip=True) if date_el else ""),
            })
        return results
    except Exception:
        return []


# ── JobMaster scraper ─────────────────────────────────────────────────────────

@st.cache_data(ttl=JOB_SEARCH_CACHE_TTL, show_spinner=False)
def _fetch_jobmaster(query: str, location: str = "") -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        base = "https://www.jobmaster.co.il"
        q = requests.utils.quote(query)
        loc_param = f"&l={requests.utils.quote(location)}" if location and location.lower() not in ("all israel", "") else ""
        url = f"{base}/jobs/?q={q}{loc_param}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for card in soup.select("article.JobItem")[:25]:
            title_el   = card.select_one(".CardHeader")
            company_el = card.select_one(".CompanyNameLink span") or card.select_one(".ByTitle")
            loc_el     = card.select_one(".jobLocation")
            desc_el    = card.select_one(".jobShortDescription")
            date_el    = card.select_one(".paddingTop10px .Gray")
            if not title_el:
                continue

            href = title_el.get("href", "") if title_el.name == "a" else ""
            link_el = card.select_one("a.CardHeader")
            if link_el:
                href = link_el.get("href", "")
            if not href:
                m = _re.search(r'(\d+)$', card.get("id", ""))
                if m:
                    href = f"/jobs/checknum.asp?key={m.group(1)}"
            full_url = (base + href) if href.startswith("/") else href

            title = title_el.text.strip()
            results.append({
                "id": f"jm_{abs(hash(title + href))}",
                "title": title,
                "company": company_el.text.strip() if company_el else "",
                "location": loc_el.text.strip() if loc_el else location,
                "source": "JobMaster",
                "url": full_url or url,
                "description": desc_el.text.strip()[:300] if desc_el else "",
                "date": _parse_date_str(date_el.text.strip() if date_el else ""),
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

    # Build one zero-arg fetch task per board/query across every selected
    # source, then run them all concurrently. Sequentially, ~30 Greenhouse +
    # 23 Comeet + 5 Ashby boards alone meant 50+ round trips before a single
    # AllJobs/Drushim/JobMaster/LinkedIn query even started — that's what was
    # making searches take minutes. Filtering is source-agnostic (it only
    # looks at each job's own fields), so it's applied once on the merged
    # results instead of per-source.
    fetch_tasks: list = []

    if "Greenhouse" in sources:
        fetch_tasks += [lambda b=board: _fetch_greenhouse(b) for board in GREENHOUSE_BOARDS]
    if "Lever" in sources:
        fetch_tasks += [lambda b=board: _fetch_lever(b) for board in LEVER_BOARDS]
    if "SmartRecruiters" in sources:
        fetch_tasks += [lambda b=board: _fetch_smartrecruiters(b) for board in SMARTRECRUITERS_BOARDS]
    if "Comeet" in sources:
        fetch_tasks += [lambda k=kind, a=a, b=b: _fetch_comeet(k, a, b) for kind, a, b in COMEET_BOARDS]
    if "Ashby" in sources:
        fetch_tasks += [lambda b=board: _fetch_ashby(b) for board in ASHBY_BOARDS]
    if "LinkedIn" in sources:
        fetch_tasks += [lambda q=query: _fetch_linkedin(q, li_loc) for query in queries]
    if "AllJobs" in sources:
        fetch_tasks += [lambda q=query: _fetch_alljobs(q, alljobs_loc, alljobs_exp) for query in queries]
    if "Drushim" in sources:
        fetch_tasks += [lambda q=query: _fetch_drushim(q, drushim_loc, drushim_exp) for query in queries]
    if "JobMaster" in sources:
        fetch_tasks += [lambda q=query: _fetch_jobmaster(q, alljobs_loc) for query in queries]

    results = []
    if fetch_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            for jobs in executor.map(lambda fn: fn(), fetch_tasks):
                results.extend(jobs)

    # LinkedIn never got the experience filter (no reliable client-side
    # signal for it) — preserve that distinction from before parallelization.
    li_jobs = _filter_by_location([j for j in results if j.get("source") == "LinkedIn"], loc_clean)
    other_jobs = _filter_by_location([j for j in results if j.get("source") != "LinkedIn"], loc_clean)
    other_jobs = _filter_by_experience(other_jobs, exp_level)
    results = other_jobs + li_jobs

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

# Plain substring matching made short skills match everything ("R" is inside
# almost any description, "Excel" inside "excellent") and parenthesized skills
# like "SQL (Advanced)" match nothing. Normalize to the core term and require
# word boundaries instead.
_skill_pat_cache: dict[str, "re.Pattern | None"] = {}


def _skill_pattern(skill: str):
    if skill not in _skill_pat_cache:
        core = re.sub(r"\s*\(.*?\)\s*", " ", skill).strip().lower()
        _skill_pat_cache[skill] = (
            re.compile(r"(?<!\w)" + re.escape(core) + r"(?!\w)", re.IGNORECASE)
            if core else None
        )
    return _skill_pat_cache[skill]


def _skill_in(skill: str, text: str) -> bool:
    pat = _skill_pattern(skill)
    return bool(pat and pat.search(text))


def score_job(job: dict, profile: dict) -> float:
    text = job.get("title", "") + " " + job.get("description", "")
    skills = profile.get("skills", [])
    roles = profile.get("target_roles", [])

    if not skills and not roles:
        return 0.5

    skill_hits = sum(1 for s in skills if _skill_in(s, text))
    role_hits = sum(1 for r in roles if _skill_in(r, text))

    skill_score = min(skill_hits / max(len(skills), 1), 1.0)
    role_score = min(role_hits * 2 / max(len(roles), 1), 1.0)

    return round(0.6 * skill_score + 0.4 * role_score, 2)


def explain_match(job: dict, profile: dict) -> tuple[list[str], list[str]]:
    text = job.get("title", "") + " " + job.get("description", "")
    skills = profile.get("skills", [])
    strengths = [s for s in skills if _skill_in(s, text)]
    gaps = [s for s in skills if not _skill_in(s, text)]
    return strengths[:8], gaps[:5]


# ── Smart fit analysis (Claude) ───────────────────────────────────────────────

def analyze_fit(job: dict, profile: dict, lang: str) -> dict:
    """Deep fit analysis for one job. Rubric: dealbreakers → required (70%) →
    preferred (30%) → red-flag scan. Returns {} on failure."""
    from modules.career_advisor import _claude, _extract_json

    lang_name = "Hebrew" if lang == "he" else "English"
    skills = ", ".join(profile.get("skills", [])[:15]) or "not specified"
    roles = ", ".join(profile.get("target_roles", [])) or "not specified"
    exp = profile.get("experience_text", "")[:1500]
    salary_min = profile.get("salary_min") or "not specified"

    prompt = f"""You are a job-fit analyst. Evaluate the candidate against the job using this rubric, in order:

1. DEALBREAKERS first → verdict "Skip": location clearly incompatible with the Tel Aviv metro area
   (unless remote); salary stated and clearly below {salary_min} ILS/month; seniority mismatch of 2+
   levels (e.g. Director role for a mid-level candidate); required license/clearance the candidate lacks.
2. Extract Required vs Preferred requirements from the description
   ("must", "required", listed under Requirements, or mentioned 3+ times → Required;
   "nice to have", "bonus", "preferred", "advantage" → Preferred).
3. match_pct = round(70 * fraction_of_required_met + 30 * fraction_of_preferred_met).
4. Verdict: "High" = no dealbreakers, all required met, 2+ preferred met;
   "Medium" = most required met; "Low" = significant required gaps; "Skip" = dealbreaker hit.
5. Red flags — scan the description for: "wear many hats", "fast-paced environment",
   "hit the ground running", "rockstar/ninja/guru", "work hard play hard", "like a family",
   "unlimited vacation", "competitive salary" with no actual range, commission-heavy pay.

JOB: {job.get('title', '')} at {job.get('company', '')}
LOCATION: {job.get('location', '')}
DESCRIPTION: {(job.get('description') or '')[:2500]}

CANDIDATE — target roles: {roles}; skills: {skills}
EXPERIENCE: {exp}

Write all string values in {lang_name}; keep JSON keys in English. Return ONLY this JSON:
{{"verdict": "High|Medium|Low|Skip", "match_pct": <int 0-100>, "strengths": ["...", "...", "..."],
"gaps": ["..."], "red_flags": ["..."], "advice": "<1-2 sentences: how to apply / what to emphasize>"}}"""

    raw = _claude(prompt, max_tokens=900, fast=False)
    return _extract_json(raw)


_VERDICT_STYLE = {
    "High":   ("match-high",   "🟢"),
    "Medium": ("match-medium", "🟡"),
    "Low":    ("match-low",    "🟠"),
    "Skip":   ("match-low",    "🔴"),
}


def _render_fit_result(fit: dict, lang: str) -> None:
    cls, dot = _VERDICT_STYLE.get(fit.get("verdict", ""), ("match-low", "⚪"))
    verdict = _html.escape(str(fit.get("verdict", "?")))
    pct = int(fit.get("match_pct") or 0)
    st.markdown(
        f'<div style="margin-bottom:0.5rem">{dot} '
        f'<span class="{cls}" style="font-size:1.2rem">{verdict}</span>'
        f'&nbsp;·&nbsp;<span style="color:#e6edf3;font-weight:600">{pct}%</span></div>',
        unsafe_allow_html=True,
    )
    if fit.get("strengths"):
        st.markdown(
            ("💪 " if lang == "he" else "💪 ")
            + " ".join(f'<span class="skill-tag tag-green">{_html.escape(str(s))}</span>'
                       for s in fit["strengths"][:5]),
            unsafe_allow_html=True,
        )
    if fit.get("gaps"):
        st.markdown(
            ("פערים: " if lang == "he" else "Gaps: ")
            + " ".join(f'<span class="skill-tag tag-red">{_html.escape(str(g))}</span>'
                       for g in fit["gaps"][:5]),
            unsafe_allow_html=True,
        )
    for rf in (fit.get("red_flags") or [])[:4]:
        st.markdown(f"⚠️ {rf}")
    if fit.get("advice"):
        st.info("💡 " + str(fit["advice"]))


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
        from config import ANTHROPIC_API_KEY
        has_claude = bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "your_key_here"

        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        with c1:
            if st.button("📄 " + ("קו\"ח" if lang == "he" else "Build CV"),
                         key=f"{key_prefix}cv_{uid}"):
                st.session_state.selected_job = job
                st.session_state.page = "cv"
                st.rerun()
        with c2:
            if st.button("🔖 " + ("שמור" if lang == "he" else "Save"),
                         key=f"{key_prefix}save_{uid}"):
                # Persisted in applications.json (stage="Saved") — the single
                # source of truth; no separate session-only list.
                _add_to_tracker(job)
                st.toast("✅ " + ("נשמר!" if lang == "he" else "Saved!"))
        with c3:
            if st.button("🎯 " + ("נתח התאמה" if lang == "he" else "Analyze Fit"),
                         key=f"{key_prefix}fit_{uid}", disabled=not has_claude):
                fit_cache = st.session_state.setdefault("fit_cache", {})
                if not fit_cache.get(uid):  # empty/failed results retryable
                    with st.spinner("מנתח התאמה..." if lang == "he" else "Analyzing fit..."):
                        result = analyze_fit(job, st.session_state.get("profile", {}), lang)
                        if result:
                            fit_cache[uid] = result
                        else:
                            st.warning("⚠️ " + ("הניתוח נכשל — נסה שוב."
                                                if lang == "he" else "Analysis failed — try again."))
        with c4:
            if job.get("url"):
                st.link_button(
                    "🚀 " + ("הגש מועמדות" if lang == "he" else "Apply Now"),
                    url=job["url"],
                )

        fit = st.session_state.get("fit_cache", {}).get(uid)
        if fit:
            with st.expander("🎯 " + ("ניתוח התאמה" if lang == "he" else "Fit Analysis"),
                             expanded=True):
                _render_fit_result(fit, lang)


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
            ["Greenhouse", "Lever", "SmartRecruiters", "Comeet", "Ashby", "LinkedIn", "AllJobs", "Drushim", "JobMaster"],
            default=["Greenhouse", "Lever", "SmartRecruiters", "Comeet", "Ashby", "LinkedIn", "AllJobs", "Drushim", "JobMaster"],
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
            # Match quality first — the card leads with match%, so ordering
            # should too; date breaks ties.
            jobs.sort(key=lambda j: (j["match"], j.get("date") or ""), reverse=True)
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
        "description": (job.get("description") or "")[:2000],  # for interview prep
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
