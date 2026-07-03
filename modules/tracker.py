import json
import os
import streamlit as st
from datetime import datetime
from config import APPLICATIONS_PATH, DATA_DIR, KANBAN_STAGES


def _save_apps(apps: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(APPLICATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)
    st.session_state.applications = apps


def render(lang: str):
    apps = st.session_state.get("applications", [])

    title = "🚀 מעקב הגשות" if lang == "he" else "🚀 Application Tracker"
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    # ── Stats row ─────────────────────────────────────────────────────────────
    total = len(apps)
    active = len([a for a in apps if a.get("stage") not in ("Offer", "Rejected")])
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
        c1, c2, c3 = st.columns(3)
        with c1:
            new_title = st.text_input("Title / תפקיד", key="new_title")
            new_company = st.text_input("Company / חברה", key="new_company")
        with c2:
            new_url = st.text_input("URL", key="new_url")
            new_stage = st.selectbox("Stage", KANBAN_STAGES, key="new_stage")
        with c3:
            new_date = st.date_input("Date Applied", key="new_date")
            new_notes = st.text_area("Notes / הערות", height=80, key="new_notes")

        if st.button("✅ " + ("הוסף" if lang == "he" else "Add"), type="primary"):
            if new_title and new_company:
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
                    <b style="color:#e6edf3;font-size:0.82rem">{app.get('title','')[:30]}</b><br>
                    <span style="color:#6b7280;font-size:0.75rem">{app.get('company','')}</span>
                    {'<br><span style="color:#8b949e;font-size:0.72rem">' + app.get('date_applied','') + '</span>' if app.get('date_applied') else ''}
                </div>""", unsafe_allow_html=True)

    # ── Table view + edit ─────────────────────────────────────────────────────
    st.markdown("---")
    table_title = "כל ההגשות" if lang == "he" else "All Applications"
    st.markdown(f"**{table_title}**")

    if not apps:
        st.info("אין הגשות עדיין." if lang == "he" else "No applications yet.")
        return

    for i, app in enumerate(apps):
        with st.expander(f"{app.get('title','')} @ {app.get('company','')} — {app.get('stage','')}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                new_stage = st.selectbox(
                    "Stage", KANBAN_STAGES,
                    index=KANBAN_STAGES.index(app.get("stage", "Saved")),
                    key=f"stage_{i}",
                )
                new_notes = st.text_area("Notes", value=app.get("notes", ""),
                                          height=80, key=f"notes_{i}")
            with c2:
                if app.get("url"):
                    st.link_button("🚀 " + ("פתח" if lang == "he" else "Open"), url=app["url"])
                st.caption(f"Saved: {app.get('date_saved','')}")
                st.caption(f"Applied: {app.get('date_applied','')}")

            col_save, col_del = st.columns(2)
            with col_save:
                if st.button("💾 " + ("שמור" if lang == "he" else "Save"),
                             key=f"save_{i}"):
                    apps[i]["stage"] = new_stage
                    apps[i]["notes"] = new_notes
                    if new_stage not in ("Saved",) and not apps[i].get("date_applied"):
                        apps[i]["date_applied"] = datetime.now().strftime("%Y-%m-%d")
                    _save_apps(apps)
                    st.success("✅")
                    st.rerun()
            with col_del:
                if st.button("🗑️ " + ("מחק" if lang == "he" else "Delete"),
                             key=f"del_{i}"):
                    apps.pop(i)
                    _save_apps(apps)
                    st.rerun()
