import streamlit as st
import json
import os
import html
from config import TEXTS, PROFILE_PATH, APPLICATIONS_PATH

st.set_page_config(
    page_title="Job Search Engine",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Dark header (holds the sidebar expand arrow when collapsed) */
header[data-testid="stHeader"] {
    background: #0d1117;
}

/* Dark sidebar */
[data-testid="stSidebar"] {
    background: #0d1117;
    border-right: 1px solid #21262d;
}
[data-testid="stSidebar"] * {
    color: #e6edf3 !important;
}

/* Main background */
.main .block-container {
    background: #0d1117;
    padding: 2rem 2.5rem;
    max-width: 1200px;
}
body, .stApp {
    background: #0d1117;
    color: #e6edf3;
}

/* Cards */
.job-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
    transition: border-color 0.2s;
}
.job-card:hover {
    border-color: #2563eb;
}

.metric-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    text-align: center;
}

/* Match score badge */
.match-high   { color: #22c55e; font-weight: 600; }
.match-medium { color: #f59e0b; font-weight: 600; }
.match-low    { color: #6b7280; font-weight: 600; }

/* Section headers */
.section-title {
    font-size: 1.4rem;
    font-weight: 600;
    color: #e6edf3;
    margin-bottom: 1rem;
    border-bottom: 1px solid #21262d;
    padding-bottom: 0.5rem;
}

/* Kanban columns */
.kanban-col {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 1rem;
    min-height: 300px;
}
.kanban-card {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 0.75rem;
    margin-bottom: 0.5rem;
    font-size: 0.85rem;
}

/* Primary button override */
.stButton > button[kind="primary"] {
    background: #2563eb;
    border: none;
    border-radius: 8px;
    font-weight: 500;
    padding: 0.5rem 1.25rem;
}
.stButton > button[kind="primary"]:hover {
    background: #1d4ed8;
}

/* Input fields */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    color: #e6edf3 !important;
}

/* Sidebar nav */
.nav-item {
    display: block;
    padding: 0.6rem 1rem;
    border-radius: 8px;
    margin-bottom: 0.25rem;
    cursor: pointer;
    font-size: 0.9rem;
    transition: background 0.15s;
}
.nav-item:hover, .nav-item.active {
    background: #21262d;
}

/* RTL wrapper */
.rtl { direction: rtl; text-align: right; }
.ltr { direction: ltr; text-align: left; }

/* Tags */
.skill-tag {
    display: inline-block;
    background: #1e3a5f;
    color: #60a5fa;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 0.78rem;
    margin: 2px;
}
.tag-green {
    background: #14532d;
    color: #4ade80;
}
.tag-red {
    background: #450a0a;
    color: #f87171;
}

/* Progress bar */
.progress-bar-bg {
    background: #21262d;
    border-radius: 999px;
    height: 8px;
    width: 100%;
}
.progress-bar-fill {
    background: #2563eb;
    border-radius: 999px;
    height: 8px;
}

/* Hide streamlit branding only — NOT the whole toolbar, it also contains the
   sidebar expand/collapse arrow (stExpandSidebarButton) which must stay visible */
#MainMenu, footer { visibility: hidden; }
header [data-testid="stToolbarActions"] { visibility: hidden; }
header [data-testid="stAppDeployButton"] { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── Session state defaults ──────────────────────────────────────────────────
def _init_state():
    if "lang" not in st.session_state:
        st.session_state.lang = "en"
    if "page" not in st.session_state:
        st.session_state.page = "home"
    if "profile" not in st.session_state:
        st.session_state.profile = _load_profile()
    if "applications" not in st.session_state:
        st.session_state.applications = _load_applications()
    if "selected_job" not in st.session_state:
        st.session_state.selected_job = None

def _load_profile():
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def _load_applications():
    if os.path.exists(APPLICATIONS_PATH):
        with open(APPLICATIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []

def t(key):
    return TEXTS[st.session_state.lang].get(key, key)

# ── Sidebar navigation ──────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🎯 JobAI")
        st.markdown("---")

        lang = st.radio("Language / שפה", ["en", "he"],
                        format_func=lambda x: "English" if x == "en" else "עברית",
                        horizontal=True,
                        label_visibility="collapsed")
        if lang != st.session_state.lang:
            st.session_state.lang = lang
            st.rerun()

        st.markdown("---")

        pages = [
            ("home",    t("nav_home")),
            ("profile", t("nav_profile")),
            ("jobs",    t("nav_jobs")),
            ("cv",      t("nav_cv")),
            ("cover",   t("nav_cover")),
            ("companies", t("nav_companies")),
            ("tracker", t("nav_tracker")),
            ("advisor", t("nav_advisor")),
            ("offers",  t("nav_offers")),
        ]

        for page_id, label in pages:
            is_active = st.session_state.page == page_id
            if st.button(label, key=f"nav_{page_id}",
                         use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state.page = page_id
                st.rerun()

        st.markdown("---")
        profile = st.session_state.profile
        if profile:
            filled = sum(1 for k in ["name", "email", "experience_text", "skills", "education"]
                         if profile.get(k))
            pct = int(filled / 5 * 100)
            st.markdown(f"**{t('profile_complete')}:** {pct}%")
            st.progress(pct / 100)

# ── Pages ───────────────────────────────────────────────────────────────────

def page_home():
    profile = st.session_state.profile
    apps = st.session_state.applications
    lang = st.session_state.lang
    rtl = "rtl" if lang == "he" else "ltr"
    dir_attr = ' dir="rtl"' if lang == "he" else ""

    name = html.escape(profile.get("name", "אורח" if lang == "he" else "Guest"))
    greeting = f"שלום, {name} 👋" if lang == "he" else f"Hello, {name} 👋"

    st.markdown(f'<div class="{rtl}"><h1>{greeting}</h1></div>', unsafe_allow_html=True)

    subtitle = "מה נעשה היום?" if lang == "he" else "What are we working on today?"
    st.markdown(f'<div class="{rtl}" style="color:#6b7280; margin-bottom:2rem">{subtitle}</div>',
                unsafe_allow_html=True)

    # Quick stats — "Saved" rows are bookmarks, not applications: they count
    # only in the Saved metric, and persist via applications.json.
    applied = [a for a in apps if a.get("stage") != "Saved"]
    active_apps = [a for a in applied if a.get("stage") not in ("Offer", "Rejected")]
    interviews = [a for a in apps if "Interview" in a.get("stage", "")]
    saved_count = len([a for a in apps if a.get("stage") == "Saved"])

    col1, col2, col3, col4 = st.columns(4)
    stats = [
        ("📋", str(len(applied)), "Total Applications" if lang == "en" else "סה\"כ הגשות"),
        ("⚡", str(len(active_apps)), "Active" if lang == "en" else "פעיל"),
        ("🎤", str(len(interviews)), "Interviews" if lang == "en" else "ריאיונות"),
        ("🔖", str(saved_count), "Saved Jobs" if lang == "en" else "משרות שמורות"),
    ]
    for col, (icon, val, label) in zip([col1, col2, col3, col4], stats):
        with col:
            st.markdown(f"""
            <div class="metric-card"{dir_attr}>
                <div style="font-size:1.8rem">{icon}</div>
                <div style="font-size:1.6rem;font-weight:700;color:#e6edf3">{val}</div>
                <div style="font-size:0.8rem;color:#6b7280">{label}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Quick actions
    actions_title = "פעולות מהירות" if lang == "he" else "Quick Actions"
    st.markdown(f'<div class="section-title">{actions_title}</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🔍 " + ("חפש משרות" if lang == "he" else "Search Jobs"),
                     use_container_width=True, type="primary"):
            st.session_state.page = "jobs"
            st.rerun()
    with c2:
        if st.button("📄 " + ("בנה קו\"ח" if lang == "he" else "Build CV"),
                     use_container_width=True):
            st.session_state.page = "cv"
            st.rerun()
    with c3:
        if st.button("🎯 " + ("ייעוץ ריאיון" if lang == "he" else "Interview Prep"),
                     use_container_width=True):
            st.session_state.page = "advisor"
            st.rerun()

    # Profile completeness prompt
    if not profile or not profile.get("name"):
        st.markdown("<br>", unsafe_allow_html=True)
        msg = "⚠️ הפרופיל שלך ריק. " if lang == "he" else "⚠️ Your profile is empty. "
        link = "עבור להגדרת פרופיל ←" if lang == "he" else "Set up your profile →"
        st.info(msg)
        if st.button(link):
            st.session_state.page = "profile"
            st.rerun()

    # Recent applications
    if apps:
        st.markdown("<br>", unsafe_allow_html=True)
        recent_title = "הגשות אחרונות" if lang == "he" else "Recent Applications"
        st.markdown(f'<div class="section-title">{recent_title}</div>', unsafe_allow_html=True)
        for app in apps[-5:][::-1]:
            stage_color = {"Applied": "#2563eb", "Interview": "#22c55e",
                           "Offer": "#f59e0b", "Rejected": "#ef4444"}.get(app.get("stage",""), "#6b7280")
            st.markdown(f"""
            <div class="job-card">
                <b style="color:#e6edf3">{html.escape(app.get('title',''))}</b> —
                <span style="color:#6b7280">{html.escape(app.get('company',''))}</span>
                <span style="float:right;color:{stage_color};font-size:0.8rem">
                    ● {html.escape(app.get('stage',''))}
                </span>
            </div>""", unsafe_allow_html=True)


def page_profile():
    import modules.profile as mod_profile
    mod_profile.render(st.session_state.lang)


def page_jobs():
    import modules.job_search as mod_jobs
    mod_jobs.render(st.session_state.lang)


def page_cv():
    import modules.cv_builder as mod_cv
    mod_cv.render(st.session_state.lang)


def page_cover():
    import modules.cover_letter as mod_cover
    mod_cover.render(st.session_state.lang)


def page_companies():
    import modules.company_db as mod_companies
    mod_companies.render(st.session_state.lang)


def page_tracker():
    import modules.tracker as mod_tracker
    mod_tracker.render(st.session_state.lang)


def page_advisor():
    import modules.career_advisor as mod_advisor
    mod_advisor.render(st.session_state.lang)


def page_offers():
    import modules.offer_tools as mod_offers
    mod_offers.render(st.session_state.lang)


# ── Router ──────────────────────────────────────────────────────────────────
_init_state()
render_sidebar()

page = st.session_state.page
if page == "home":
    page_home()
elif page == "profile":
    page_profile()
elif page == "jobs":
    page_jobs()
elif page == "cv":
    page_cv()
elif page == "cover":
    page_cover()
elif page == "companies":
    page_companies()
elif page == "tracker":
    page_tracker()
elif page == "advisor":
    page_advisor()
elif page == "offers":
    page_offers()
