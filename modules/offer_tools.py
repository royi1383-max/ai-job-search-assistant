"""Offer-stage tools: side-by-side offer comparison (deterministic math) and a
Claude-generated negotiation script tuned to Israeli comp specifics."""
import html as _html
import pandas as pd
import streamlit as st
from config import ANTHROPIC_API_KEY


# ── Pure math (testable without Streamlit) ────────────────────────────────────

def total_comp_year1(base_monthly: float, bonus_pct: float = 0.0,
                     equity_yearly: float = 0.0, pension_pct: float = 6.5,
                     keren_hishtalmut: bool = True) -> float:
    """Gross Year-1 total compensation (ILS). Employer keren hishtalmut = 7.5%."""
    annual = base_monthly * 12
    return (
        annual * (1 + bonus_pct / 100)
        + equity_yearly
        + annual * pension_pct / 100
        + (annual * 0.075 if keren_hishtalmut else 0)
    )


def weighted_scores(scores: dict[str, dict[str, float]],
                    weights: dict[str, float]) -> dict[str, float]:
    """scores: {offer: {factor: 1-10}}, weights: {factor: weight}. Returns
    weight-normalized 1-10 score per offer."""
    tw = sum(weights.values()) or 1
    return {
        offer: round(sum(s.get(f, 0) * w for f, w in weights.items()) / tw, 2)
        for offer, s in scores.items()
    }


# ── UI ────────────────────────────────────────────────────────────────────────

_DEFAULT_ROWS = pd.DataFrame([
    {"Offer": "Offer A", "Base ₪/mo": 25000, "Bonus %": 10.0, "Equity ₪/yr": 0,
     "Pension %": 6.5, "Keren Hishtalmut": True, "Days Off": 22,
     "Remote days/wk": 2, "Commute min": 30},
])

_FACTORS = ["Compensation", "Growth", "Work-Life", "Team & Culture", "Commute"]
_FACTORS_HE = {"Compensation": "תגמול", "Growth": "צמיחה", "Work-Life": "איזון",
               "Team & Culture": "צוות ותרבות", "Commute": "נסיעות"}
_DEFAULT_WEIGHTS = {"Compensation": 25, "Growth": 25, "Work-Life": 20,
                    "Team & Culture": 20, "Commute": 10}


def render(lang: str):
    he = lang == "he"
    title = "💼 כלים להצעה" if he else "💼 Offer Tools"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    tab_cmp, tab_neg = st.tabs([
        "📊 " + ("השוואת הצעות" if he else "Compare Offers"),
        "🗣️ " + ("תסריט משא ומתן" if he else "Negotiation Script"),
    ])

    with tab_cmp:
        _render_compare(lang)
    with tab_neg:
        _render_negotiation(lang)


def _render_compare(lang: str):
    he = lang == "he"
    st.caption("ערוך את הטבלה — סה\"כ תגמול שנה ראשונה מחושב אוטומטית (כולל פנסיה וקרן השתלמות)."
               if he else
               "Edit the table — Year-1 total comp is computed automatically (incl. pension & keren hishtalmut).")

    if "offers_df" not in st.session_state:
        st.session_state.offers_df = _DEFAULT_ROWS.copy()

    df = st.data_editor(
        st.session_state.offers_df,
        num_rows="dynamic",
        use_container_width=True,
        key="offers_editor",
    )
    st.session_state.offers_df = df

    offers = [r for _, r in df.iterrows() if str(r.get("Offer", "")).strip()]
    if not offers:
        return

    totals = {}
    cols = st.columns(max(len(offers), 1))
    for col, row in zip(cols, offers):
        name = str(row["Offer"])
        try:
            total = total_comp_year1(
                float(row.get("Base ₪/mo") or 0),
                float(row.get("Bonus %") or 0),
                float(row.get("Equity ₪/yr") or 0),
                float(row.get("Pension %") or 6.5),
                bool(row.get("Keren Hishtalmut", True)),
            )
        except (TypeError, ValueError):
            total = 0.0
        totals[name] = total
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:0.8rem;color:#6b7280">{_html.escape(name)}</div>
                <div style="font-size:1.3rem;font-weight:700;color:#e6edf3">₪{int(total):,}</div>
                <div style="font-size:0.72rem;color:#6b7280">{'סה"כ שנה 1' if he else 'Total Year-1'}</div>
            </div>""", unsafe_allow_html=True)

    if len(offers) < 2:
        return

    # ── Weighted decision matrix ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**⚖️ " + ("מטריצת החלטה משוקללת" if he else "Weighted Decision Matrix") + "**")

    wcols = st.columns(len(_FACTORS))
    weights = {}
    for col, f in zip(wcols, _FACTORS):
        label = _FACTORS_HE[f] if he else f
        with col:
            weights[f] = st.slider(label, 0, 50, _DEFAULT_WEIGHTS[f], key=f"w_{f}")

    # Compensation score auto-derived from computed totals (best offer = 10);
    # the rest are subjective 1-10 inputs.
    best_total = max(totals.values()) or 1
    scores: dict[str, dict[str, float]] = {}
    for row in offers:
        name = str(row["Offer"])
        scores[name] = {"Compensation": round(totals[name] / best_total * 10, 1)}

    st.caption("דרג 1-10 את הגורמים הסובייקטיביים לכל הצעה:" if he
               else "Rate the subjective factors 1-10 per offer:")
    for name in scores:
        fcols = st.columns(len(_FACTORS) - 1)
        for col, f in zip(fcols, _FACTORS[1:]):
            label = f"{name[:12]} · {(_FACTORS_HE[f] if he else f)}"
            with col:
                scores[name][f] = st.slider(label, 1, 10, 5, key=f"s_{name}_{f}")

    final = weighted_scores(scores, weights)
    winner = max(final, key=final.get)

    st.markdown("---")
    rcols = st.columns(len(final))
    for col, (name, score) in zip(rcols, sorted(final.items(), key=lambda x: -x[1])):
        crown = " 👑" if name == winner else ""
        tag = "tag-green" if name == winner else "tag-red"
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:0.85rem;color:#e6edf3">{_html.escape(name)}{crown}</div>
                <div style="font-size:1.6rem;font-weight:800;color:{'#22c55e' if name == winner else '#e6edf3'}">
                    {score}
                </div>
                <div style="font-size:0.7rem;color:#6b7280">/10</div>
            </div>""", unsafe_allow_html=True)


def _render_negotiation(lang: str):
    he = lang == "he"
    has_claude = bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "your_key_here"
    if not has_claude:
        st.warning("⚠️ " + ("נא להגדיר ANTHROPIC_API_KEY ב-.env" if he
                            else "Set ANTHROPIC_API_KEY in .env"))

    c1, c2 = st.columns(2)
    with c1:
        role = st.text_input("תפקיד" if he else "Role", key="neg_role")
        company = st.text_input("חברה" if he else "Company", key="neg_company")
        offered = st.number_input("שכר שהוצע (₪/חודש)" if he else "Offered base (₪/mo)",
                                  min_value=0, value=25000, step=500, key="neg_offered")
    with c2:
        target = st.number_input("שכר יעד (₪/חודש)" if he else "Target base (₪/mo)",
                                 min_value=0, value=28000, step=500, key="neg_target")
        leverage = st.text_area(
            "נקודות מינוף (הצעות מתחרות, ביקוש לכישורים...)" if he
            else "Leverage notes (competing offers, in-demand skills...)",
            height=100, key="neg_leverage")

    if st.button("🗣️ " + ("צור תסריט" if he else "Generate Script"),
                 type="primary", disabled=not has_claude):
        from modules.career_advisor import _claude
        market = st.session_state.get("salary_result", {})
        market_ctx = (
            f"Market research for this profile: min {market.get('min')}, mid {market.get('mid')}, "
            f"max {market.get('max')} ILS/month. " if market.get("mid") else ""
        )
        lang_name = "Hebrew" if he else "English"
        prompt = f"""You are an Israeli salary-negotiation coach. Write a practical, speakable
negotiation script for this situation:

Role: {role} at {company}
Offered: {offered} ILS/month gross. Candidate's target: {target} ILS/month.
{market_ctx}Candidate leverage: {leverage or "none stated"}

Israeli-market specifics to weave in where relevant:
- Keren hishtalmut (7.5% employer / 2.5% employee) — if absent from the offer, it's the
  single highest-value benefit to ask for.
- Pension: standard 6.5% employer contribution + 8.33% severance.
- Havra'a (recuperation pay) is statutory — not a negotiation chip.
- Non-salary asks that work in Israel: extra vacation days, hybrid/remote days,
  signing bonus, earlier salary review (6 months), title bump.

Write in {lang_name}, markdown, with these sections:
## Opening anchor — one paragraph, anchoring 8-12% above the target salary, warm but firm.
## 3 justification points — from the leverage notes and role scope; each 1-2 sentences.
## If they say "that's above our range" — a graceful response + pivot to total comp.
## If they pressure "we need an answer today" — a polite deferral script.
## Ranked non-salary asks — 4 items, most valuable first, one line each with the ILS value where estimable."""

        with st.spinner("כותב תסריט..." if he else "Writing script..."):
            script = _claude(prompt, max_tokens=1200, fast=False)
        st.session_state["neg_script"] = script

    script = st.session_state.get("neg_script")
    if script and not script.startswith("[Error"):
        st.markdown("---")
        st.markdown(script)
        st.download_button(
            "⬇️ Markdown", data=script.encode("utf-8"),
            file_name="negotiation_script.md", mime="text/markdown",
        )
    elif script:
        st.error(script)
