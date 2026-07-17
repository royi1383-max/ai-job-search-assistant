import re
import json
import streamlit as st
import plotly.graph_objects as go
from config import ANTHROPIC_API_KEY, CLAUDE_SMART, CLAUDE_FAST


def _claude(prompt: str, max_tokens: int = 1000, fast: bool = True) -> str:
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your_key_here":
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        kwargs = {}
        if not fast:
            # Sonnet 5 runs adaptive thinking by default when the param is
            # omitted; disable to keep the old low-latency behavior.
            kwargs["thinking"] = {"type": "disabled"}
        msg = client.messages.create(
            model=CLAUDE_FAST if fast else CLAUDE_SMART,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"[Error: {e}]"


def _extract_json(raw: str, kind: str = "object"):
    """Pull the first JSON object/array out of a Claude reply. {} / [] on failure."""
    empty = [] if kind == "array" else {}
    try:
        m = re.search(r'\[[\s\S]+\]' if kind == "array" else r'\{[\s\S]+\}', raw)
        return json.loads(m.group()) if m else empty
    except Exception:
        return empty


def _to_int(v, default: int = 0) -> int:
    try:
        return int(float(str(v).replace(",", "").replace("₪", "").strip()))
    except Exception:
        return default


def generate_interview_prep(app: dict, profile: dict, lang: str) -> str:
    """Full interview prep guide (markdown) for a tracked application."""
    title = app.get("title", "")
    company = app.get("company", "")
    description = (app.get("description") or "").strip()
    exp = profile.get("experience_text", "")[:1500]
    skills = ", ".join(profile.get("skills", [])[:15])
    lang_name = "Hebrew" if lang == "he" else "English"

    jd_block = (
        f"JOB DESCRIPTION:\n{description[:2500]}"
        if description
        else "JOB DESCRIPTION: (not available — infer typical requirements for this title at this company)"
    )

    prompt = f"""Create an interview preparation guide for the role "{title}" at {company}.

{jd_block}

CANDIDATE EXPERIENCE:
{exp}

CANDIDATE SKILLS: {skills}

Write the entire guide in {lang_name}, as markdown with exactly these sections:

## {'שאלות צפויות בראיון' if lang == 'he' else 'Likely Interview Questions'}
8-10 questions mixing technical (from the job's skill requirements) and behavioral.
After each question add one short line: why the interviewer asks it.

## {'טיוטות תשובה בשיטת STAR' if lang == 'he' else 'STAR Answer Drafts'}
3-4 drafts built ONLY from the candidate's actual experience above.
Each: Situation/Task/Action/Result, max 120 words, quantified where the resume gives numbers.
Never invent facts not present in the experience text.

## {'שאלות לשאול את המראיין' if lang == 'he' else 'Questions to Ask the Interviewer'}
5 questions specific to this company and role."""

    return _claude(prompt, max_tokens=3000, fast=False)


def skill_gap_analysis(profile: dict, job_description: str) -> dict:
    skills = profile.get("skills", [])
    prompt = f"""Analyze the skill gap between this candidate and job description.

Candidate skills: {', '.join(skills)}

Job description:
{job_description[:1500]}

Return JSON:
{{
  "match_strengths": ["skill1", "skill2"],
  "gaps": ["missing_skill1", "missing_skill2"],
  "quick_wins": ["course or action to close gap in <1 month"],
  "overall_fit": "Strong/Good/Moderate/Weak",
  "fit_reason": "one sentence explanation"
}}
JSON only."""

    raw = _claude(prompt, max_tokens=800, fast=True)
    return _extract_json(raw)


COMPANY_SEGMENTS = {
    # ── Tech ──────────────────────────────────────────────────────────────────
    "🚀 VC-backed Startup": "early/growth-stage VC-backed Israeli startup (Series A–C), typically 20–300 employees",
    "📈 Public Tech / Unicorn": "publicly traded or unicorn Israeli tech company (monday.com, Wix, CyberArk, NICE, Fiverr, WalkMe)",
    "🌐 MNC R&D Center": "global tech MNC with Israel R&D center (Google, Microsoft, Amazon, Intel, Meta, Cisco, SAP, Oracle) — highest-paying segment in Israeli market",
    "🔐 Cybersecurity": "Israeli cybersecurity company (Check Point, CyberArk, Wiz, Orca Security, SentinelOne Israel, Armis, Radware)",
    "📺 Media / AdTech": "Israeli media-tech or ad-tech company (Taboola, IronSource, Outbrain, AppsFlyer)",
    # ── Finance ───────────────────────────────────────────────────────────────
    "💳 FinTech": "Israeli FinTech company (Payoneer, Nayax, Papaya Global, Next Insurance, Lemonade Israel)",
    "🏦 Traditional Bank": "Israeli commercial bank (Bank Hapoalim, Leumi, Discount, Mizrahi-Tefahot, First International FIBI)",
    "📊 Investment House / Asset Management": "Israeli investment house or asset manager (Altshuler Shaham, Meitav, IBI, Psagot, Analyst)",
    "🛡️ Insurance Company": "Israeli insurance group (Clal, Harel, Migdal, Phoenix, Menora Mivtachim)",
    # ── Professional Services ─────────────────────────────────────────────────
    "🏢 Big 4 / Consulting": "Big 4 accounting or strategy consulting firm (Deloitte, EY, KPMG, PwC, BCG, McKinsey, Roland Berger Israel)",
    # ── Healthcare ────────────────────────────────────────────────────────────
    "🏥 Healthcare / Pharma": "Israeli healthcare or pharma (Teva, MSD Israel, Pfizer Israel, health funds Maccabi/Clalit/Leumit, hospitals Sheba/Ichilov)",
    "🧬 MedTech / Digital Health": "Israeli medical-tech or digital health startup (Given Imaging, Itamar Medical, Healthy.io, healthtech startups)",
    # ── Telecom ───────────────────────────────────────────────────────────────
    "📡 Telecom": "Israeli telecom operator (Cellcom, Partner Communications, Bezeq, Hot Mobile) — regulated, corporate structure",
    # ── Consumer & Retail ─────────────────────────────────────────────────────
    "🛒 Retail / Consumer / FMCG": "Israeli retail chain or consumer goods company (Shufersal, Rami Levy, Super-Pharm, Fox, Strauss, Tnuva, Castro)",
    # ── Real Estate & Infrastructure ──────────────────────────────────────────
    "🏘️ Real Estate / PropTech": "Israeli real estate developer or PropTech company (Azrieli, Aura, Shikun & Binui, Madlan)",
    "⚡ Energy / Infrastructure": "Israeli energy or utilities company (Israel Electric Corporation, Delek Group, Paz, NewMed Energy)",
    # ── Public & Non-profit ───────────────────────────────────────────────────
    "🏛️ Government / Public Sector": "Israeli government ministry, public authority, or state-owned enterprise (CBS, ITA, Bank of Israel, municipalities)",
    "🎓 Academia / Research Institute": "Israeli university or public research institute (Tel Aviv University, Weizmann, Technion, Hebrew University, Reichman)",
    # ── Industrial ────────────────────────────────────────────────────────────
    "🏗️ Industrial / Defense": "Israeli industrial or defense corporation (IAI, Elbit Systems, Rafael, IMI, manufacturing conglomerates)",
}


def salary_range(role: str, experience_years: int, profile: dict, company_segment: str) -> dict:
    segment_desc = COMPANY_SEGMENTS.get(company_segment, company_segment)

    # Pull candidate context from profile
    skills       = ", ".join((profile.get("skills") or [])[:10]) or "not specified"
    education    = profile.get("education", "B.Sc.")
    current_role = profile.get("current_role", "")
    background   = f"{current_role}, {education}" if current_role else education

    prompt = f"""You are an Israeli compensation expert with deep knowledge of 2024-2025 salary benchmarks.

Candidate profile:
- Role applying for: {role}
- Years of experience: {experience_years}
- Background: {background}
- Key skills: {skills}
- Location: Tel Aviv metropolitan area (center)

Target company type: {segment_desc}

Give a realistic GROSS monthly salary range (ILS) for THIS specific candidate applying to THIS type of company.
Consider:
- Israeli market rates for {company_segment} specifically (not generic market average)
- Candidate's actual background and skills
- Typical compensation structure for this sector (base + bonus norms)

Return ONLY this JSON (no other text):
{{
  "min": <integer>,
  "mid": <integer>,
  "max": <integer>,
  "currency": "ILS",
  "bonus_note": "<typical annual bonus % or structure for this sector>",
  "notes": "<1-2 sentences: why this range, what drives it up/down for this candidate>"
}}"""

    raw = _claude(prompt, max_tokens=600, fast=False)
    return _extract_json(raw)


def _radar_chart(strengths: list[str], gaps: list[str]):
    categories = (strengths + gaps)[:8]
    if not categories:
        return None
    values = [90 if c in strengths else 35 for c in categories] + [90 if categories[0] in strengths else 35]
    categories_closed = categories + [categories[0]]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=categories_closed,
        fill='toself',
        fillcolor='rgba(37,99,235,0.2)',
        line=dict(color='#2563eb', width=2),
        name='Your Skills',
    ))
    fig.add_trace(go.Scatterpolar(
        r=[75] * len(categories_closed),
        theta=categories_closed,
        fill='toself',
        fillcolor='rgba(34,197,94,0.08)',
        line=dict(color='#22c55e', width=1, dash='dot'),
        name='Job Requirements',
    ))
    fig.update_layout(
        polar=dict(
            bgcolor='#161b22',
            radialaxis=dict(visible=True, range=[0, 100], gridcolor='#30363d',
                            tickfont=dict(color='#6b7280', size=9)),
            angularaxis=dict(gridcolor='#30363d', tickfont=dict(color='#e6edf3', size=10)),
        ),
        paper_bgcolor='#0d1117',
        plot_bgcolor='#0d1117',
        font=dict(color='#e6edf3'),
        showlegend=True,
        legend=dict(bgcolor='#161b22', bordercolor='#30363d'),
        margin=dict(t=40, b=40, l=90, r=90),
        height=420,
    )
    return fig


def render(lang: str):
    profile = st.session_state.profile
    rtl = "rtl" if lang == "he" else "ltr"

    title = "🎯 יועץ קריירה" if lang == "he" else "🎯 Career Advisor"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    has_claude = ANTHROPIC_API_KEY and ANTHROPIC_API_KEY != "your_key_here"
    if not has_claude:
        st.warning("⚠️ " + ("נא להגדיר ANTHROPIC_API_KEY ב-.env לשימוש ביועץ הקריירה."
                             if lang == "he"
                             else "Set ANTHROPIC_API_KEY in .env to use the Career Advisor."))

    tabs_labels = (
        ["פערי כישורים", "מחקר שכר", "מה ללמוד"]
        if lang == "he"
        else ["Skill Gap", "Salary Research", "What to Learn"]
    )
    tabs = st.tabs(tabs_labels)

    # ── Tab 0: Skill Gap ──────────────────────────────────────────────────────
    with tabs[0]:
        jd_for_gap = st.text_area(
            "תיאור משרה" if lang == "he" else "Job Description",
            height=150,
            placeholder="הדבק תיאור משרה..." if lang == "he" else "Paste job description...",
        )
        gap_label = "🔍 " + ("נתח פערים" if lang == "he" else "Analyze Gaps")
        if st.button(gap_label, type="primary", disabled=not has_claude):
            if jd_for_gap.strip():
                with st.spinner("מנתח..." if lang == "he" else "Analyzing..."):
                    gap = skill_gap_analysis(profile, jd_for_gap)
                    st.session_state.gap_result = gap

        gap = st.session_state.get("gap_result", {})
        if gap:
            fit = gap.get("overall_fit", "")
            fit_color = {"Strong": "#22c55e", "Good": "#4ade80",
                         "Moderate": "#f59e0b", "Weak": "#ef4444"}.get(fit, "#6b7280")

            fit_label = "התאמה כוללת" if lang == "he" else "Overall Fit"
            st.markdown(f"""
            <div class="metric-card" style="margin-bottom:1rem">
                <div style="color:#6b7280;font-size:0.85rem">{fit_label}</div>
                <div style="color:{fit_color};font-size:1.8rem;font-weight:700">{fit}</div>
                <div style="color:#8b949e;font-size:0.85rem">{gap.get('fit_reason','')}</div>
            </div>""", unsafe_allow_html=True)

            col_l, col_r = st.columns(2)
            with col_l:
                strengths = gap.get("match_strengths", [])
                st.markdown("**✅ " + ("נקודות חוזק" if lang == "he" else "Strengths") + "**")
                for s in strengths:
                    st.markdown(f'<span class="skill-tag tag-green">{s}</span>', unsafe_allow_html=True)

                st.markdown("<br>**❌ " + ("פערים" if lang == "he" else "Gaps") + "**", unsafe_allow_html=True)
                for g in gap.get("gaps", []):
                    st.markdown(f'<span class="skill-tag tag-red">{g}</span>', unsafe_allow_html=True)

            with col_r:
                fig = _radar_chart(strengths, gap.get("gaps", []))
                if fig:
                    st.plotly_chart(fig, use_container_width=True)

            quick_wins = gap.get("quick_wins", [])
            if quick_wins:
                st.markdown("**⚡ " + ("פעולות מהירות לסגירת הפער" if lang == "he" else "Quick Wins") + "**")
                for w in quick_wins:
                    st.markdown(f"• {w}")

    # ── Tab 1: Salary ─────────────────────────────────────────────────────────
    with tabs[1]:
        st.caption(
            "טווחי השכר מחושבים לפי הפרופיל שלך + סוג החברה הספציפי — לא ממוצע שוק גנרי"
            if lang == "he"
            else "Salary ranges are tailored to your profile + specific company type — not a generic market average"
        )

        c1, c2 = st.columns([2, 1])
        with c1:
            sal_role = st.text_input(
                "תפקיד" if lang == "he" else "Role",
                value=(profile.get("target_roles") or ["Data Analyst"])[0],
                key="sal_role",
            )
        with c2:
            sal_exp = st.number_input(
                "שנות ניסיון" if lang == "he" else "Years of experience",
                value=3, min_value=0, max_value=30,
            )

        sal_segment = st.selectbox(
            "סוג חברה" if lang == "he" else "Company type",
            list(COMPANY_SEGMENTS.keys()),
            index=0,
        )
        st.caption(f"_{COMPANY_SEGMENTS[sal_segment]}_")

        sal_label = "💰 " + ("חשב טווח שכר" if lang == "he" else "Calculate Salary Range")
        if st.button(sal_label, type="primary", disabled=not has_claude):
            with st.spinner("מנתח שכר לפי פרופיל + סוג חברה..." if lang == "he"
                            else "Analysing salary for your profile + company type..."):
                sal = salary_range(sal_role, sal_exp, profile, sal_segment)
                st.session_state.salary_result = sal
                st.session_state.salary_segment = sal_segment

        sal = st.session_state.get("salary_result", {})
        if sal and sal.get("mid"):
            seg_label = st.session_state.get("salary_segment", sal_segment)
            st.markdown(f"**{seg_label}**")

            c1, c2, c3 = st.columns(3)
            for col, (label, val) in zip(
                [c1, c2, c3],
                [
                    ("Min" if lang == "en" else "מינימום", _to_int(sal.get("min", 0))),
                    ("Median" if lang == "en" else "חציון", _to_int(sal.get("mid", 0))),
                    ("Max" if lang == "en" else "מקסימום", _to_int(sal.get("max", 0))),
                ],
            ):
                with col:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div style="color:#6b7280;font-size:0.8rem">{label}</div>
                        <div style="color:#e6edf3;font-size:1.5rem;font-weight:700">
                            ₪{val:,}
                        </div>
                    </div>""", unsafe_allow_html=True)

            if sal.get("bonus_note"):
                st.info(f"🎁 **בונוס:** {sal['bonus_note']}" if lang == "he"
                        else f"🎁 **Bonus:** {sal['bonus_note']}")
            if sal.get("notes"):
                st.caption(f"💡 {sal['notes']}")

            my_min = profile.get("salary_min", 0)
            my_max = profile.get("salary_max", 0)
            if my_min and my_max:
                mid = _to_int(sal.get("mid", 0))
                if mid > my_max:
                    st.success("🎉 " + ("השוק מציע יותר ממה שציפית — שקול לדרוש יותר." if lang == "he"
                                        else "Market pays above your expectation — consider asking for more!"))
                elif mid < my_min:
                    st.warning("⚠️ " + ("שכר החציון נמוך מהמינימום שלך לתפקיד זה." if lang == "he"
                                        else "Median market rate is below your minimum for this role."))

    # ── Tab 2: What to Learn ──────────────────────────────────────────────────
    with tabs[2]:
        recent_jobs = st.session_state.get("job_results", [])
        # Some sources (e.g. LinkedIn) never return a description — a title-only
        # entry just dilutes the trend signal, so drop those before combining.
        described_jobs = [j for j in recent_jobs if len((j.get("description") or "")) >= 80]
        learn_label = "📚 " + ("מה כדאי ללמוד?" if lang == "he" else "What Should I Learn?")

        if st.button(learn_label, type="primary", disabled=not has_claude):
            if not recent_jobs:
                st.info("חפש משרות קודם כדי לנתח מגמות." if lang == "he"
                        else "Search for jobs first to analyze trends.")
            elif len(described_jobs) < 3:
                st.warning(
                    "רוב המשרות שנמצאו בלי תיאור מפורט — נסה חיפוש עם יותר תוצאות ממקורות עשירים "
                    "(Greenhouse/Lever/Comeet/Ashby) לפני ניתוח מגמות." if lang == "he" else
                    "Most search results have no real description — try a search that pulls more "
                    "results from richer sources (Greenhouse/Lever/Comeet/Ashby) before analyzing trends."
                )
            else:
                combined_jds = " ".join(j.get("description", "")[:200] for j in described_jobs[:20])
                skills = ", ".join(profile.get("skills", []))

                prompt = f"""Based on these job descriptions (combined):
{combined_jds[:2000]}

Candidate already knows: {skills}

Suggest the top 5 skills/tools to learn next, prioritized by:
1. Frequency in job postings
2. Ease of learning (quick wins first)
3. Career impact

Return JSON array: [{{"skill":"...","why":"...","resource":"free course or resource name","time_to_learn":"..."}}]
JSON only."""

                with st.spinner("מנתח מגמות שוק..." if lang == "he" else "Analyzing market trends..."):
                    raw = _claude(prompt, max_tokens=800, fast=False)
                    st.session_state.learn_result = _extract_json(raw, kind="array")

        items = st.session_state.get("learn_result", [])
        if items:
            for i, item in enumerate(items, 1):
                st.markdown(f"""
                <div class="job-card">
                    <b style="color:#2563eb">#{i} {item.get('skill','')}</b><br>
                    <span style="color:#8b949e;font-size:0.85rem">{item.get('why','')}</span><br>
                    <span style="color:#22c55e;font-size:0.82rem">📚 {item.get('resource','')}</span>
                    &nbsp;·&nbsp;
                    <span style="color:#6b7280;font-size:0.82rem">⏱ {item.get('time_to_learn','')}</span>
                </div>""", unsafe_allow_html=True)
