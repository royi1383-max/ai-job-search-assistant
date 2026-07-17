import json
import os
import html
import streamlit as st
from datetime import datetime
from config import APPLICATIONS_PATH, DATA_DIR, KANBAN_STAGES, ANTHROPIC_API_KEY


def _save_apps(apps: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(APPLICATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)
    st.session_state.applications = apps


def _aid(app: dict) -> str:
    """Stable per-application key — positional indices shift on add/delete and
    leave Streamlit widget state pointing at the wrong row."""
    return str(app.get("id") or f"h{abs(hash((app.get('url', ''), app.get('title', ''))))}")


def render(lang: str):
    apps = st.session_state.get("applications", [])

    title = "🚀 מעקב הגשות" if lang == "he" else "🚀 Application Tracker"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    # ── Stats row ─────────────────────────────────────────────────────────────
    # "Saved" rows are bookmarks, not applications — exclude them from totals
    # and from the response-rate denominator.
    applied = [a for a in apps if a.get("stage") != "Saved"]
    total = len(applied)
    active = len([a for a in applied if a.get("stage") not in ("Offer", "Rejected")])
    interviews = len([a for a in apps if "Interview" in a.get("stage", "")])
    offers = len([a for a in apps if a.get("stage") == "Offer"])

    response_rate = (
        int((interviews + offers) / total * 100) if total > 0 else 0
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, (icon, val, label) in zip(
        [c1, c2, c3, c4, c5],
        [
            ("📋", total, "Total" if lang == "en" else "סה\"כ"),
            ("⚡", active, "Active" if lang == "en" else "פעיל"),
            ("🎤", interviews, "Interviews" if lang == "en" else "ריאיונות"),
            ("🏆", offers, "Offers" if lang == "en" else "הצעות"),
            ("📈", f"{response_rate}%", "Response Rate" if lang == "en" else "שיעור תגובה"),
        ],
    ):
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:1.4rem">{icon}</div>
                <div style="font-size:1.4rem;font-weight:700;color:#e6edf3">{val}</div>
                <div style="font-size:0.75rem;color:#6b7280">{label}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Add new application ───────────────────────────────────────────────────
    with st.expander("➕ " + ("הוסף הגשה ידנית" if lang == "he" else "Add Application Manually")):
        with st.form("add_app_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                new_title = st.text_input("תפקיד" if lang == "he" else "Title")
                new_company = st.text_input("חברה" if lang == "he" else "Company")
            with c2:
                new_url = st.text_input("קישור" if lang == "he" else "URL")
                new_stage = st.selectbox("שלב" if lang == "he" else "Stage", KANBAN_STAGES)
            with c3:
                new_date = st.date_input("תאריך הגשה" if lang == "he" else "Date Applied")
                new_notes = st.text_area("הערות" if lang == "he" else "Notes", height=80)

            submitted = st.form_submit_button(
                "✅ " + ("הוסף" if lang == "he" else "Add"), type="primary")
        if submitted and new_title and new_company:
            new_app = {
                "id": f"manual_{int(datetime.now().timestamp())}",
                "title": new_title,
                "company": new_company,
                "url": new_url,
                "stage": new_stage,
                "date_saved": str(new_date),
                "date_applied": str(new_date) if new_stage != "Saved" else "",
                "notes": new_notes,
                "cv_version": "",
                "source": "Manual",
                "location": "",
            }
            apps.append(new_app)
            _save_apps(apps)
            st.success("✅ " + ("נוסף!" if lang == "he" else "Added!"))
            st.rerun()

    # ── Kanban board ──────────────────────────────────────────────────────────
    st.markdown("---")
    cols = st.columns(len(KANBAN_STAGES))
    stage_colors = {
        "Saved": "#6b7280",
        "Applied": "#2563eb",
        "Phone Screen": "#a855f7",
        "Interview": "#22c55e",
        "Offer": "#f59e0b",
        "Rejected": "#ef4444",
    }

    for col, stage in zip(cols, KANBAN_STAGES):
        with col:
            color = stage_colors.get(stage, "#6b7280")
            stage_apps = [a for a in apps if a.get("stage") == stage]
            st.markdown(f"""
            <div style="color:{color};font-weight:600;font-size:0.9rem;margin-bottom:0.5rem">
                {stage} ({len(stage_apps)})
            </div>""", unsafe_allow_html=True)

            for app in stage_apps:
                st.markdown(f"""
                <div class="kanban-card">
                    <b style="color:#e6edf3;font-size:0.82rem">{html.escape(app.get('title', '')[:30])}</b><br>
                    <span style="color:#6b7280;font-size:0.75rem">{html.escape(app.get('company', ''))}</span>
                    {'<br><span style="color:#8b949e;font-size:0.72rem">' + html.escape(app.get('date_applied', '')) + '</span>' if app.get('date_applied') else ''}
                </div>""", unsafe_allow_html=True)

    # ── Table view + edit ─────────────────────────────────────────────────────
    st.markdown("---")
    table_title = "כל ההגשות" if lang == "he" else "All Applications"
    st.markdown(f"**{table_title}**")

    if not apps:
        st.info("אין הגשות עדיין." if lang == "he" else "No applications yet.")
        return

    has_claude = bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "your_key_here"

    for app in list(apps):
        aid = _aid(app)
        with st.expander(f"{app.get('title','')} @ {app.get('company','')} — {app.get('stage','')}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                new_stage = st.selectbox(
                    "שלב" if lang == "he" else "Stage", KANBAN_STAGES,
                    index=KANBAN_STAGES.index(app.get("stage", "Saved")),
                    key=f"stage_{aid}",
                )
                new_notes = st.text_area("הערות" if lang == "he" else "Notes",
                                          value=app.get("notes", ""),
                                          height=80, key=f"notes_{aid}")
            with c2:
                if app.get("url"):
                    st.link_button("🚀 " + ("פתח" if lang == "he" else "Open"), url=app["url"])
                st.caption(("נשמר: " if lang == "he" else "Saved: ") + app.get("date_saved", ""))
                st.caption(("הוגש: " if lang == "he" else "Applied: ") + app.get("date_applied", ""))

            col_save, col_prep, col_del = st.columns(3)
            with col_save:
                if st.button("💾 " + ("שמור" if lang == "he" else "Save"),
                             key=f"save_{aid}"):
                    for a in apps:
                        if _aid(a) == aid:
                            a["stage"] = new_stage
                            a["notes"] = new_notes
                            if new_stage != "Saved" and not a.get("date_applied"):
                                a["date_applied"] = datetime.now().strftime("%Y-%m-%d")
                            break
                    _save_apps(apps)
                    st.success("✅")
                    st.rerun()
            with col_prep:
                prep_primary = app.get("stage") in ("Phone Screen", "Interview")
                if st.button("🎤 " + ("הכנה לראיון" if lang == "he" else "Interview Prep"),
                             key=f"prep_{aid}",
                             type="primary" if prep_primary else "secondary",
                             disabled=not has_claude):
                    from modules.career_advisor import generate_interview_prep
                    with st.spinner("מכין חומרי הכנה..." if lang == "he" else "Preparing materials..."):
                        md = generate_interview_prep(app, st.session_state.get("profile", {}), lang)
                    st.session_state.setdefault("prep_cache", {})[aid] = md
            with col_del:
                if st.button("🗑️ " + ("מחק" if lang == "he" else "Delete"),
                             key=f"del_{aid}"):
                    _save_apps([a for a in apps if _aid(a) != aid])
                    st.rerun()

            prep_md = st.session_state.get("prep_cache", {}).get(aid)
            if prep_md:
                st.markdown("---")
                st.markdown(prep_md)
                dl1, dl2 = st.columns(2)
                with dl1:
                    st.download_button(
                        "⬇️ Markdown", data=prep_md.encode("utf-8"),
                        file_name=f"interview_prep_{app.get('company', 'company')}.md",
                        mime="text/markdown", key=f"prep_md_{aid}",
                    )
                with dl2:
                    from modules.cover_letter import export_letter_docx
                    docx_bytes = export_letter_docx(prep_md, st.session_state.get("profile", {}))
                    if docx_bytes:
                        st.download_button(
                            "⬇️ DOCX", data=docx_bytes,
                            file_name=f"interview_prep_{app.get('company', 'company')}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"prep_docx_{aid}",
                        )
