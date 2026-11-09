"""Cloud portfolio: shared, approved-email access; persistent Supabase records and files."""
import uuid
from datetime import date
import pandas as pd
import plotly.express as px
import streamlit as st
from auth_ui import require_login
from cloud_store import Store, CloudError, inspect_backup

CATEGORIES = ["Engineering / Process", "Python Application", "Research / Article", "Presentation / Training", "Improvement Project", "Academic / Doctoral", "Other"]
STAGES = ["Idea", "Planning", "In progress", "Pilot / Prototype", "Completed", "Implemented", "Published", "Archived"]
DELIVERABLES = ["Python App", "Presentation", "Overleaf Report", "PDF Article", "Technical Report", "Dashboard", "Simulation", "Training Material", "Patent / IP", "Other"]
FILE_TYPES = ["pdf", "ppt", "pptx", "doc", "docx", "py", "ipynb", "zip", "csv", "xlsx", "png", "jpg", "jpeg", "svg", "md", "txt", "json", "html", "js", "css", "tex", "bib"]
MAX_FILE_MB = 50

def rows(sql, params=()): return store.rows(sql, params)
def project_rows(): return rows("SELECT * FROM projects ORDER BY created_on DESC, title")
def project_name(project): return f"{project['title']} · {project['created_on']} · {project['id'][:6]}"
def save_project(data, kinds, project_id=None): return store.save_project(data, kinds, project_id)
def save_files(pid, uploads): return store.save_files(pid, uploads, FILE_TYPES)
def delete_record(kind, rid, pid):
    store.delete_record(kind, rid, pid)
    st.session_state.pop("loaded_file_" + rid, None)
def add_link(pid, label, url):
    import re
    url = url.strip()
    if not re.fullmatch(r"https?://[^\s]+", url, re.I):
        raise ValueError("Links must begin with https:// or http://")
    store.insert("links", dict(id=uuid.uuid4().hex, project_id=pid, label=label.strip() or "Link", url=url))

st.set_page_config(page_title="Accomplishment Portfolio", page_icon="🏆", layout="wide")
try:
    settings = dict(st.secrets["supabase"])
    require_login(settings)
    store = Store(settings["url"], settings["secret_key"],
                  settings.get("bucket", "TomosAccomplishments"),
                  settings.get("max_file_mb", 50))
    MAX_FILE_MB = settings.get("max_file_mb", 50)
except (KeyError, FileNotFoundError):
    st.error("Cloud setup is incomplete. Add the Supabase settings in Streamlit Secrets using secrets.example.toml.")
    st.stop()
except CloudError as error:
    st.error(str(error)); st.stop()
st.title("🏆 Accomplishment Portfolio")
st.caption("Engineering • Innovation • Research • Software • Presentations")

with st.sidebar:
    st.header("Navigation")
    page = st.radio("Go to", ["Dashboard", "Add project", "Project library", "Data & backup"], label_visibility="collapsed")
    st.divider()
    st.caption("Records and attachments are stored in your private Supabase project.")

try:
    projects = project_rows()
except CloudError as error:
    st.error(str(error))
    st.info("If this is a new project, run setup.sql in Supabase SQL Editor, then refresh.")
    st.stop()

if page == "Dashboard":
    year_options = sorted({date.today().year, *(int(p["created_on"][:4]) for p in projects)}, reverse=True)
    year = st.selectbox("Accomplishment year", year_options)
    current = [p for p in projects if p["created_on"].startswith(str(year))]
    ids = [p["id"] for p in current]
    kinds = rows("SELECT project_id, kind FROM deliverables")
    activities = rows("SELECT * FROM activities")
    kind_count = {k: sum(1 for r in kinds if r["kind"] == k and r["project_id"] in ids) for k in DELIVERABLES}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Projects recorded", len(current))
    c2.metric("Presentations", kind_count["Presentation"])
    c3.metric("Python applications", kind_count["Python App"])
    c4.metric("Overleaf reports", kind_count["Overleaf Report"])
    c5.metric("PDF articles", kind_count["PDF Article"])
    st.caption("Each metric counts distinct project records tagged with that deliverable. A project can have multiple deliverable types.")
    if current:
        left, right = st.columns(2)
        frame = pd.DataFrame(current)
        with left:
            monthly = frame.assign(month=frame["created_on"].str[:7]).groupby("month").size().reset_index(name="projects")
            st.plotly_chart(px.bar(monthly, x="month", y="projects", title="Projects created by month", labels={"month": "Month", "projects": "Projects"}), use_container_width=True)
        with right:
            category = frame.groupby("category").size().reset_index(name="projects")
            st.plotly_chart(px.pie(category, names="category", values="projects", title="Portfolio by category"), use_container_width=True)
        deliverable_frame = pd.DataFrame([{"type": name, "count": count} for name, count in kind_count.items() if count])
        if not deliverable_frame.empty:
            st.plotly_chart(px.bar(deliverable_frame, x="type", y="count", title="Deliverables across the portfolio"), use_container_width=True)
        st.metric("Recorded effort (project estimates + activity entries)", f"{sum(p['hours'] for p in current) + sum(a['hours'] for a in activities if a['project_id'] in ids):,.1f} h")
        st.subheader("Recent accomplishments")
        for p in current[:8]:
            st.markdown(f"**{p['title']}** — {p['created_on']} · {p['category']} · {p['status']}")
    else:
        st.info("No projects for this year yet. Use **Add project** to create your first record.")

elif page == "Add project":
    st.subheader("Record a new accomplishment")
    editing = st.session_state.get("edit_id")
    selected = next((p for p in projects if p["id"] == editing), None)
    if editing and not selected:
        st.session_state.pop("edit_id", None)
    with st.form("project_form", clear_on_submit=False):
        title = st.text_input("Project / accomplishment title *", value=selected["title"] if selected else "")
        a, b, c = st.columns(3)
        created_on = a.date_input("Date created / started", value=date.fromisoformat(selected["created_on"]) if selected else date.today())
        category = b.selectbox("Main category", CATEGORIES, index=CATEGORIES.index(selected["category"]) if selected and selected["category"] in CATEGORIES else 0)
        status = c.selectbox("Current stage", STAGES, index=STAGES.index(selected["status"]) if selected and selected["status"] in STAGES else 0)
        work_type = st.text_input("Specific work type", value=selected["work_type"] if selected else "", placeholder="e.g., process simulation, predictive modeling, employee training")
        existing_kinds = [x["kind"] for x in rows("SELECT kind FROM deliverables WHERE project_id=?", (editing,))] if selected else []
        selected_kinds = st.multiselect("Deliverables produced (select all that apply)", DELIVERABLES, default=existing_kinds)
        description = st.text_area("Project overview", value=selected["description"] if selected else "", placeholder="What did you build or prepare?")
        problem = st.text_area("Problem / opportunity", value=selected["problem"] if selected else "", placeholder="What challenge motivated the work?")
        outcome = st.text_area("Results / evidence", value=selected["outcome"] if selected else "", placeholder="What changed? Include before/after metrics if available.")
        impact = st.text_area("Value / impact", value=selected["impact"] if selected else "", placeholder="Time saved, capacity gained, quality improvement, users trained...")
        a, b = st.columns(2)
        audience = a.text_input("Audience / stakeholder", value=selected["audience"] if selected else "")
        collaborators = b.text_input("Collaborators / contributors", value=selected["collaborators"] if selected else "")
        tools = st.text_input("Tools / technology", value=selected["tools"] if selected else "", placeholder="Python, Streamlit, SimPy, Overleaf, Gamma, PowerPoint")
        tags = st.text_input("Tags (comma-separated)", value=selected["tags"] if selected else "")
        hours = st.number_input("Estimated initial effort (hours)", min_value=0.0, step=0.5, value=float(selected["hours"]) if selected else 0.0)
        submitted = st.form_submit_button("Save changes" if selected else "Create project", type="primary")
    if submitted:
        if not title.strip():
            st.error("Please enter a project title.")
        else:
            data = dict(title=title.strip(), created_on=created_on.isoformat(), category=category, status=status, work_type=work_type, description=description, problem=problem, outcome=outcome, impact=impact, audience=audience, collaborators=collaborators, tools=tools, tags=tags, hours=hours)
            pid = save_project(data, selected_kinds, editing if selected else None)
            st.session_state.pop("edit_id", None)
            st.session_state["view_id"] = pid
            st.success("Project saved. Open the Project library to add files, links, and work milestones.")
    if selected and st.button("Cancel editing"):
        st.session_state.pop("edit_id", None)
        st.rerun()

elif page == "Project library":
    st.subheader("Project library")
    if not projects:
        st.info("No projects yet. Add your first project to get started.")
    else:
        search = st.text_input("Search titles, descriptions, tags, tools, or collaborators")
        categories = st.multiselect("Filter categories", CATEGORIES)
        filtered = [p for p in projects if (not categories or p["category"] in categories) and (not search or search.lower() in " ".join(str(v) for v in p.values()).lower())]
        if not filtered:
            st.info("No matching records.")
        else:
            chosen_id = st.selectbox("Open a project", [p["id"] for p in filtered], format_func=lambda x: project_name(next(p for p in filtered if p["id"] == x)), index=next((i for i, p in enumerate(filtered) if p["id"] == st.session_state.get("view_id")), 0))
            st.session_state["view_id"] = chosen_id
            p = next(p for p in filtered if p["id"] == chosen_id)
            st.markdown(f"### {p['title']}")
            st.caption(f"{p['created_on']} · {p['category']} · {p['status']} · {p['work_type']}")
            tags_for_project = [r["kind"] for r in rows("SELECT kind FROM deliverables WHERE project_id=?", (chosen_id,))]
            st.write("**Deliverables:**", ", ".join(tags_for_project) or "None tagged")
            for label, field in [("Overview", "description"), ("Problem / opportunity", "problem"), ("Results / evidence", "outcome"), ("Impact", "impact"), ("Audience", "audience"), ("Collaborators", "collaborators"), ("Tools", "tools"), ("Tags", "tags")]:
                if p[field]:
                    st.markdown(f"**{label}:** {p[field]}")
            if st.button("✏️ Edit this project"):
                st.session_state["edit_id"] = chosen_id
                st.info("Choose **Add project** in the sidebar to edit this record.")
            st.divider()
            left, right = st.columns(2)
            with left:
                st.markdown("#### References and links")
                for link in rows("SELECT * FROM links WHERE project_id=?", (chosen_id,)):
                    c1, c2 = st.columns([5, 1])
                    c1.markdown(f"[{link['label']}]({link['url']})")
                    if c2.button("Remove", key="link"+link["id"]):
                        delete_record("links", link["id"], chosen_id)
                        st.rerun()
                with st.form("add_link"):
                    label = st.text_input("Reference label", placeholder="Overleaf, Gamma deck, GitHub repository...")
                    url = st.text_input("URL", placeholder="https://...")
                    if st.form_submit_button("Add link"):
                        try:
                            add_link(chosen_id, label, url)
                            st.rerun()
                        except (ValueError, CloudError) as error:
                            st.error(str(error))
            with right:
                st.markdown("#### Evidence files")
                for attachment in rows("SELECT * FROM files WHERE project_id=?", (chosen_id,)):
                    c1, c2 = st.columns([5, 1])
                    file_key = "loaded_file_" + attachment["id"]
                    if c1.button("Load download: " + attachment["original_name"], key="load"+attachment["id"]):
                        try:
                            st.session_state[file_key] = store.download(attachment)
                        except CloudError as error:
                            c1.error(str(error))
                    if file_key in st.session_state:
                        c1.download_button(f"⬇ {attachment['original_name']}", data=st.session_state[file_key], file_name=attachment["original_name"], key="download"+attachment["id"])
                    if c2.button("Remove", key="file"+attachment["id"]):
                        delete_record("files", attachment["id"], chosen_id)
                        st.rerun()
                uploads = st.file_uploader("Upload PDFs, presentations, ZIP archives, code, images, datasets", type=FILE_TYPES, accept_multiple_files=True, key="upload"+chosen_id)
                if st.button("Save uploaded files", disabled=not uploads):
                    try:
                        st.success(f"Stored {save_files(chosen_id, uploads)} files.")
                        st.rerun()
                    except (OSError, ValueError, CloudError) as error:
                        st.error(str(error))
            st.divider()
            st.markdown("#### Development timeline / work log")
            for activity in rows("SELECT * FROM activities WHERE project_id=? ORDER BY activity_on DESC", (chosen_id,)):
                c1, c2 = st.columns([6, 1])
                c1.write(f"**{activity['activity_on']}** — {activity['note']} ({activity['hours']:g} h)")
                if c2.button("Remove", key="act"+activity["id"]):
                    delete_record("activities", activity["id"], chosen_id)
                    st.rerun()
            with st.form("activity"):
                activity_on = st.date_input("Date of milestone / work", date.today())
                note = st.text_input("What did you accomplish?")
                time_spent = st.number_input("Additional effort (hours)", min_value=0.0, step=0.5)
                if st.form_submit_button("Add milestone"):
                    if note.strip():
                        store.insert("activities", dict(id=uuid.uuid4().hex, project_id=chosen_id,
                            activity_on=activity_on.isoformat(), note=note.strip(), hours=time_spent))
                        st.rerun()
                    else:
                        st.error("Please describe the milestone.")
            with st.expander("Danger zone — permanently delete project"):
                confirm = st.checkbox("I understand this deletes the project, its timeline, links, and cloud attachments.", key="confirm"+chosen_id)
                if st.button("Delete this project", disabled=not confirm):
                    store.delete_project(chosen_id)
                    st.session_state.pop("view_id", None)
                    st.rerun()

elif page == "Data & backup":
    st.subheader("Export, backup and migration")
    if projects:
        st.download_button("Download project register (CSV)", pd.DataFrame(projects).to_csv(index=False).encode("utf-8-sig"), f"portfolio_projects_{date.today()}.csv", mime="text/csv")
    st.write("Prepare a ZIP containing all project records and registered attachments.")
    st.caption("Avoid editing or deleting records in another session while generating a full backup.")
    if st.button("Prepare full backup"):
        try:
            with st.spinner("Downloading cloud records and attachments..."):
                st.session_state["full_backup"] = store.backup_bytes()
            st.success("Backup ready.")
        except (CloudError, ValueError) as error:
            st.session_state.pop("full_backup", None)
            st.error(str(error))
    if "full_backup" in st.session_state:
        st.download_button("Download full backup (ZIP)", st.session_state["full_backup"], f"accomplishment_portfolio_backup_{date.today()}.zip", mime="application/zip", type="primary")
        st.caption("This is a snapshot from when you clicked Prepare full backup. Prepare again after changes.")
    st.divider()
    st.subheader("Import your existing local backup")
    st.caption("Initial migration only: the cloud portfolio must be empty. Existing records are never overwritten.")
    if projects:
        st.info("Import is disabled because this portfolio already contains projects.")
    else:
        backup = st.file_uploader("Select your full backup ZIP", type=["zip"], key="migration_zip")
        if backup is not None:
            try:
                content = backup.getvalue()
                plan = inspect_backup(content, store.max_file_bytes)
                st.dataframe(pd.DataFrame([{"Record type":k,"Count":v} for k,v in plan["counts"].items()]), hide_index=True)
                st.caption(f"Registered attachment total: {plan['file_bytes']/1024/1024:,.2f} MB")
                confirm = st.checkbox("Import these records and attachments into my empty cloud portfolio.")
                if st.button("Import backup", disabled=not confirm, type="primary"):
                    with st.spinner("Uploading attachments and importing records..."):
                        counts = store.import_backup(content)
                        actual = {t: len(store.select(t)) for t in counts}
                        if actual != counts:
                            raise CloudError("Import returned, but counts differ. Keep your local backup and investigate before adding records.")
                        st.session_state["migration_completed"] = counts
                    st.rerun()
            except (ValueError,CloudError) as error:
                st.error(str(error))
    if "migration_completed" in st.session_state:
        st.success("Migration completed and record counts verified. Open Project library and download several attachments to check them.")
    st.info("Keep a separate backup of both records and attachments. Database-only backups do not include storage file contents.")
