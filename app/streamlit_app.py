"""
TriageAI Streamlit app: login/register, patient portal, and staff view.
Messages are tied to patient identity (patient_id, full_name) for personalization and staff identification.
"""
import os
import sys

# Ensure the project root is on sys.path so all package imports resolve correctly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from dotenv import load_dotenv

from app.auth import register, login, get_current_user, is_supabase_configured
from graph.state import get_patient_context, set_patient_context, clear_patient_context
from app.messages_store import (
    save_message,
    get_all_messages_for_staff,
    get_messages_for_patient,
    update_message_triage_result,
)
from mcp.tools.communication import send_resolution_email

load_dotenv()

# Optional: workflow and policy (lazy import so app starts without langgraph/chromadb)
def _run_workflow(msg: str, patient_id: str = ""):
    try:
        from graph.workflow import run_triage_workflow
        return run_triage_workflow(msg, patient_id=patient_id)
    except ImportError:
        from agents.safety_agent import screen_for_emergency
        from agents.triage_agent import test_triage
        safety_result = screen_for_emergency(msg)
        safety_dict = safety_result.model_dump()
        triage = test_triage(msg)
        triage_result = triage.model_dump() if triage else {}
        if safety_result.is_potential_emergency:
            triage_result["urgency"] = "EMERGENCY"
            triage_result["safety_flagged"] = True
            triage_result["safety_reason"] = safety_result.reason
            triage_result["safety_triggered_by"] = safety_result.triggered_by
        return safety_dict, triage_result


def _policy_available():
    try:
        from agents.policy_agent import get_relevant_policy, generate_draft_reply, generate_next_steps
        return get_relevant_policy, generate_draft_reply, generate_next_steps
    except ImportError:
        return None

# Page config
st.set_page_config(page_title="TriageAI Patient Portal", page_icon="🏥", layout="centered")

# Initialize session state
if "patient" not in st.session_state:
    st.session_state.patient = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "selected_message_id" not in st.session_state:
    st.session_state.selected_message_id = None


def render_login_register():
    """Show login and register forms."""
    st.title("🏥 TriageAI Patient Portal")
    if not is_supabase_configured():
        st.info("Running in **demo mode** (no Supabase). Data is in-memory only.")
    else:
        if st.button("Restore my session", key="restore_session"):
            user = get_current_user()
            if user:
                set_patient_context(
                    st.session_state,
                    user_id=user["user_id"],
                    patient_id=user["patient_id"],
                    full_name=user["full_name"],
                    email=user["email"],
                )
                st.session_state.user_id = user["user_id"]
                st.rerun()
            else:
                st.caption("No existing session found. Log in or register below.")
    tab1, tab2 = st.tabs(["Log in", "Register"])
    with tab1:
        with st.form("login_form"):
            st.subheader("Log in")
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Log in"):
                if not email or not password:
                    st.error("Please enter email and password.")
                else:
                    err, user_info = login(email, password)
                    if err:
                        st.error(err)
                    else:
                        set_patient_context(
                            st.session_state,
                            user_id=user_info["user_id"],
                            patient_id=user_info["patient_id"],
                            full_name=user_info["full_name"],
                            email=user_info["email"],
                        )
                        st.session_state.user_id = user_info["user_id"]
                        st.rerun()
    with tab2:
        with st.form("register_form"):
            st.subheader("Create an account")
            full_name = st.text_input("Full name", key="reg_name", placeholder="Jane Doe")
            email = st.text_input("Email", key="reg_email", placeholder="jane@example.com")
            password = st.text_input("Password", type="password", key="reg_password")
            if st.form_submit_button("Register"):
                if not full_name or not email or not password:
                    st.error("Please fill in full name, email, and password.")
                else:
                    err, _ = register(email, password, full_name)
                    if err:
                        st.error(err)
                    else:
                        st.success("Account created. Please log in.")
                        st.info("If using Supabase with email confirmation, check your inbox first.")


def render_patient_portal():
    """What the patient sees: submit message and own history only."""
    patient = get_patient_context(st.session_state)
    if not patient:
        return

    st.subheader("Send a message")
    with st.form("message_form"):
        content = st.text_area("Your message", height=120, placeholder="Describe your concern or request...")
        if st.form_submit_button("Submit"):
            if not (content or "").strip():
                st.error("Please enter a message.")
            else:
                msg = content.strip()
                # LangGraph workflow: Safety -> Triage
                try:
                    safety_result_dict, triage_result = _run_workflow(msg, patient_id=patient.patient_id)
                except Exception as e:
                    st.error(f"Workflow failed: {e}")
                    return
                safety_flagged = safety_result_dict.get("is_potential_emergency", False)
                if safety_flagged:
                    st.warning("⚠️ **Safety screen:** This message was flagged as a potential emergency. Staff will prioritize it. If this is a life-threatening emergency, please call 911 or go to the nearest ER.")
                save_message(
                    user_id=patient.user_id,
                    patient_id=patient.patient_id,
                    full_name=patient.full_name,
                    email=patient.email,
                    content=msg,
                    triage_result=triage_result,
                )
                st.success("Message submitted. Staff will review it.")
                if triage_result:
                    with st.expander("Triage summary"):
                        st.json(triage_result)
                st.rerun()

    st.subheader("Your message history")
    my_messages = get_messages_for_patient(patient.user_id)
    if not my_messages:
        st.caption("No messages yet.")
    else:
        for m in my_messages:
            with st.container():
                st.markdown(f"**{m.get('created_at', '')[:19]}** — {m.get('content', '')[:100]}...")
                if m.get("triage_result"):
                    st.caption(f"Urgency: {m['triage_result'].get('urgency', 'N/A')} · {m['triage_result'].get('summary', '')}")
                st.divider()


def _urgency_emoji(urgency: str) -> str:
    u = (urgency or "").upper()
    if u == "EMERGENCY":
        return "🔴"
    if u == "HIGH":
        return "🟠"
    if u == "LOW":
        return "🟢"
    return "🟡"  # NORMAL or default


def render_staff_view():
    """Staff view: two-pane dashboard (active queue left, detail view right)."""
    messages = get_all_messages_for_staff(active_only=True)
    if not messages:
        st.caption("No active messages. Resolved/routed messages are hidden from the queue.")
        return

    # Default to first (highest urgency) message if none selected
    if st.session_state.selected_message_id is None:
        st.session_state.selected_message_id = messages[0].get("id")
    selected_id = st.session_state.selected_message_id
    selected = next((m for m in messages if m.get("id") == selected_id), None)
    if not selected and messages:
        selected = messages[0]
        st.session_state.selected_message_id = selected.get("id")

    st.caption("Active queue (resolved/routed messages are hidden). Set SUPABASE_SERVICE_ROLE_KEY in .env to see all patients.")
    col_left, col_right = st.columns([4, 8])

    with col_left:
        st.markdown("#### 📋 Active queue")
        for m in messages:
            tr = m.get("triage_result") or {}
            urgency = tr.get("urgency", "N/A")
            emoji = _urgency_emoji(urgency)
            full_name = m.get("full_name") or "—"
            content_snippet = (m.get("content") or "")[:80]
            if len(m.get("content") or "") > 80:
                content_snippet += "…"
            ts = (m.get("created_at") or "")[:19]
            is_selected = m.get("id") == selected_id
            with st.container(border=True):
                st.markdown(f"{emoji} **{urgency}** · **{full_name}**")
                st.caption(content_snippet)
                st.caption(ts)
                if st.button("View", key=f"view_{m.get('id')}", use_container_width=True):
                    st.session_state.selected_message_id = m.get("id")
                    st.rerun()

    with col_right:
        st.markdown("#### 📄 Detail view")
        if not selected:
            st.info("Select a message from the queue.")
            return

        tr = selected.get("triage_result") or {}
        full_name = selected.get("full_name") or "—"
        email = selected.get("email") or "—"
        patient_id = selected.get("patient_id") or "—"
        content = selected.get("content") or ""

        st.markdown(f"**{full_name}** · {email} · `{patient_id}`")
        st.markdown("---")
        st.markdown("**Message**")
        st.text_area("Content", value=content, height=120, disabled=True, key="detail_content")
        st.markdown("**AI analysis**")
        with st.container(border=True):
            st.markdown(f"**Intent / summary:** {tr.get('summary') or tr.get('intent') or '—'}")
            st.markdown(f"**Urgency:** {_urgency_emoji(tr.get('urgency',''))} {tr.get('urgency', 'N/A')}")
            st.markdown(f"**Recommended queue:** {tr.get('recommended_queue', '—')}")
            if tr.get("safety_flagged"):
                st.warning(f"⚠️ **Safety flagged:** {tr.get('safety_reason') or 'Potential emergency'}")
        st.markdown("**Patient history**")
        history = get_messages_for_patient(selected.get("user_id", ""))
        with st.expander(f"Past messages ({len(history)})", expanded=False):
            if not history:
                st.caption("No other messages.")
            for h in history:
                if h.get("id") == selected.get("id"):
                    continue
                st.caption(f"*{(h.get('created_at') or '')[:19]}* — {(h.get('content') or '')[:100]}…")
                st.divider()

        # Action buttons
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            if st.button("Approve & Route to ER", type="primary", key="approve_route_er"):
                new_tr = {**(tr or {}), "status": "Resolved/Routed"}
                if update_message_triage_result(selected.get("id"), new_tr):
                    body = "Your case has been reviewed. Please proceed to the ER as directed by staff."
                    send_resolution_email(email, "Urgent: Proceed to ER", body)
                    st.success("Message resolved and patient emailed!")
                    st.session_state.selected_message_id = None
                    st.rerun()
                else:
                    st.error("Failed to update message.")
        with btn_col2:
            if st.button("Edit draft reply", key="edit_draft"):
                st.info("Edit draft reply — coming soon.")
        with btn_col3:
            if st.button("Request more info", key="request_more_info"):
                st.info("Request more info — coming soon.")

        # Policy / draft reply
        st.markdown("**Policy & draft reply**")
        policy_fns = _policy_available()
        if policy_fns:
            get_relevant_policy, generate_draft_reply, generate_next_steps = policy_fns
            with st.container(border=True):
                policy_chunks = get_relevant_policy(content, tr.get("summary", ""))
                draft = generate_draft_reply(content, tr, policy_chunks)
                steps = generate_next_steps(content, tr, policy_chunks)
                st.markdown("**Draft reply**")
                st.text(draft)
                st.markdown("**Suggested next steps**")
                for s in steps:
                    st.markdown(f"- {s}")
        else:
            st.caption("Install `chromadb` and run `pip install -r requirements.txt` for policy-based draft replies.")


def main():
    # No automatic session restore: Supabase is never touched at startup, so a paused/slow project won't hang the app.

    patient = get_patient_context(st.session_state)
    if patient is None:
        render_login_register()
        return

    # Logged in: two tabs — Patient view and Staff view (for demo)
    st.title("🏥 TriageAI")
    st.write(f"**{patient.full_name}** · {patient.patient_id} · *{patient.email}*")
    if st.button("Log out", key="logout_main"):
        clear_patient_context(st.session_state)
        st.session_state.user_id = None
        st.rerun()
        return

    tab_patient, tab_staff = st.tabs(["Patient view", "Staff view"])
    with tab_patient:
        st.caption("What the patient sees: send messages and view your own history.")
        render_patient_portal()
    with tab_staff:
        st.caption("What staff sees: all patient messages grouped by patient.")
        render_staff_view()


if __name__ == "__main__":
    main()
