"""
TriageAI Streamlit app: login/register, patient chat, staff view, and HITL approvals.
Messages are tied to patient identity (patient_id, full_name) for personalization and staff identification.
Sprint 5: Streaming chat interface with multimodal vision and conversational interrupts.
"""
import base64
import os
import sys

# Ensure the project root is on sys.path so all package imports resolve correctly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nest_asyncio
try:
    nest_asyncio.apply()
except ValueError as e:
    if "uvloop" in str(e) or "patch" in str(e).lower():
        pass  # Streamlit uses uvloop; nest_asyncio can't patch it — app runs without nested async
    else:
        raise

import streamlit as st
from dotenv import load_dotenv

from app.auth import register, login, get_current_user, is_supabase_configured
from app.streaming import stream_graph
from graph.state import get_patient_context, set_patient_context, clear_patient_context
from app.messages_store import (
    save_message,
    get_all_messages_for_staff,
    get_messages_for_patient,
    update_message_triage_result,
)
from mcp_tools.tools.communication import send_resolution_email

load_dotenv()

# Optional: workflow and policy (lazy import so app starts without langgraph/chromadb)
def _run_workflow(msg: str, patient_id: str = "", patient_email: str = ""):
    try:
        from graph.workflow import run_triage_workflow
        return run_triage_workflow(msg, patient_id=patient_id, patient_email=patient_email)
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
# Sprint 5: streaming chat state
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
if "chat_thread_id" not in st.session_state:
    st.session_state.chat_thread_id = None
if "pending_interrupt" not in st.session_state:
    st.session_state.pending_interrupt = None
if "uploaded_file_data" not in st.session_state:
    st.session_state.uploaded_file_data = None


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


def _process_uploaded_file(uploaded_file):
    """Encode an uploaded file as a base64 data URI."""
    raw = uploaded_file.read()
    b64 = base64.b64encode(raw).decode("utf-8")
    mime = uploaded_file.type or "application/octet-stream"
    return {
        "uri": f"data:{mime};base64,{b64}",
        "mime": mime,
        "name": uploaded_file.name,
    }


def _stream_and_display(app, inputs, config, patient):
    """Drive stream_graph() and render tokens progressively."""
    from graph.workflow import get_workflow_state

    full_response = ""
    msg_placeholder = st.chat_message("assistant")
    text_area = msg_placeholder.empty()
    status_area = msg_placeholder.empty()

    for event in stream_graph(app, inputs, config):
        if event["type"] == "token":
            full_response += event["content"]
            text_area.markdown(full_response + " |")
        elif event["type"] == "status":
            status_area.caption(event["content"])
        elif event["type"] == "interrupt":
            # Render final text so far (without cursor)
            if full_response:
                text_area.markdown(full_response)
            status_area.empty()
            # Store the interrupt question and rerun so chat_input re-renders
            st.session_state.pending_interrupt = event["content"]
            st.session_state.chat_messages.append(
                {"role": "assistant", "content": event["content"]}
            )
            st.rerun()
            return
        elif event["type"] == "error":
            text_area.markdown(full_response or "")
            status_area.error(event["content"])
            return
        elif event["type"] == "done":
            pass

    # Render final text (without cursor)
    if full_response:
        text_area.markdown(full_response)
    status_area.empty()

    # Extract final results from the workflow state
    thread_id = config["configurable"]["thread_id"]
    state = get_workflow_state(thread_id)
    if state:
        triage_result = state.get("triage_result") or {}
        safety_result = state.get("safety_result") or {}

        # Embed thread_id and hitl_status
        triage_result["thread_id"] = thread_id
        hitl_status = state.get("hitl_status")
        if hitl_status:
            triage_result["hitl_status"] = hitl_status
        else:
            triage_result["hitl_status"] = "pending_review"
            triage_result["draft_reply"] = state.get("draft_reply", "")

        # Safety warning
        if safety_result.get("is_potential_emergency"):
            st.warning(
                "This message was flagged as a potential emergency. "
                "Staff will prioritize it. If this is a life-threatening "
                "emergency, please call 911 or go to the nearest ER."
            )

        # Save to message store
        save_message(
            user_id=patient.user_id,
            patient_id=patient.patient_id,
            full_name=patient.full_name,
            email=patient.email,
            content=st.session_state.chat_messages[0]["content"] if st.session_state.chat_messages else "",
            triage_result=triage_result,
        )

        # Show triage summary
        summary = triage_result.get("summary", "")
        urgency = triage_result.get("urgency", "")
        if summary or urgency:
            result_text = f"**Triage complete.** Urgency: {urgency}. {summary}"
            st.session_state.chat_messages.append(
                {"role": "assistant", "content": result_text}
            )

    # Reset chat thread for next conversation
    st.session_state.chat_thread_id = None
    st.session_state.pending_interrupt = None
    st.session_state.uploaded_file_data = None


def render_patient_portal():
    """Streaming chat interface for the patient (Sprint 5)."""
    patient = get_patient_context(st.session_state)
    if not patient:
        return

    # --- Sidebar: file upload ---
    with st.sidebar:
        st.markdown("#### Attach a file")
        uploaded = st.file_uploader(
            "Upload an image or PDF",
            type=["jpg", "jpeg", "png", "pdf"],
            key="patient_file_upload",
        )
        if uploaded:
            file_data = _process_uploaded_file(uploaded)
            st.session_state.uploaded_file_data = file_data
            if file_data["mime"].startswith("image/"):
                st.image(uploaded, caption=file_data["name"], use_container_width=True)
            else:
                st.caption(f"Attached: {file_data['name']}")
            if st.button("Clear attachment", key="clear_attachment"):
                st.session_state.uploaded_file_data = None
                st.rerun()

    # --- Chat history ---
    for msg in st.session_state.chat_messages:
        st.chat_message(msg["role"]).markdown(msg["content"])

    # --- Show pending interrupt as a prompt ---
    if st.session_state.pending_interrupt:
        st.info("The AI needs more information. Please reply below.")

    # --- Chat input ---
    user_input = st.chat_input("Describe your concern or ask a question...")

    if user_input:
        # Display user message
        st.chat_message("user").markdown(user_input)
        st.session_state.chat_messages.append({"role": "user", "content": user_input})

        if st.session_state.pending_interrupt:
            # Resume from checklist interrupt
            thread_id = st.session_state.chat_thread_id
            st.session_state.pending_interrupt = None
            if thread_id:
                try:
                    from graph.workflow import resume_chat
                    app, command, config = resume_chat(thread_id, user_input)
                    _stream_and_display(app, command, config, patient)
                except Exception as e:
                    st.error(f"Resume failed: {e}")
        else:
            # New workflow
            try:
                from graph.workflow import stream_triage_workflow
                file_data = st.session_state.uploaded_file_data or {}
                app, initial, config, thread_id = stream_triage_workflow(
                    patient_message=user_input,
                    patient_id=patient.patient_id,
                    patient_email=patient.email,
                    file_uri=file_data.get("uri", ""),
                    file_mime_type=file_data.get("mime", ""),
                    file_name=file_data.get("name", ""),
                )
                st.session_state.chat_thread_id = thread_id
                _stream_and_display(app, initial, config, patient)
            except Exception as e:
                st.error(f"Workflow failed: {e}")

    # --- Message history (collapsible) ---
    with st.expander("Your message history"):
        my_messages = get_messages_for_patient(patient.user_id)
        if not my_messages:
            st.caption("No messages yet.")
        else:
            for m in my_messages:
                with st.container():
                    st.markdown(f"**{m.get('created_at', '')[:19]}** -- {m.get('content', '')[:100]}...")
                    if m.get("triage_result"):
                        st.caption(f"Urgency: {m['triage_result'].get('urgency', 'N/A')} | {m['triage_result'].get('summary', '')}")
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
            hitl = tr.get("hitl_status", "")
            with st.container(border=True):
                status_badge = ""
                if hitl == "pending_review":
                    status_badge = " ⏸️ *Pending*"
                elif hitl == "approved":
                    status_badge = " ✅ *Sent*"
                elif hitl == "auto_completed":
                    status_badge = " ⚡ *Auto*"
                st.markdown(f"{emoji} **{urgency}** · **{full_name}**{status_badge}")
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

        # --- Draft reply: editable + action buttons ---
        st.markdown("**Draft reply**")
        thread_id = tr.get("thread_id", "")
        hitl_status = tr.get("hitl_status", "")

        # Get the draft — from triage_result (HITL), or generate via policy agent
        existing_draft = tr.get("draft_reply", "")
        if not existing_draft:
            policy_fns = _policy_available()
            if policy_fns:
                get_relevant_policy, generate_draft_reply, _gen_steps = policy_fns
                policy_chunks = get_relevant_policy(content, tr.get("summary", ""))
                existing_draft = generate_draft_reply(content, tr, policy_chunks)

        edited_draft = st.text_area(
            "Edit the draft before sending",
            value=existing_draft or f"Thank you for contacting us regarding: {tr.get('summary', 'your concern')}. A staff member will review your message shortly.",
            height=150,
            key="staff_draft_edit",
        )

        # Suggested next steps (if policy agent available)
        policy_fns = _policy_available()
        if policy_fns:
            _grp, _gdr, generate_next_steps = policy_fns
            policy_chunks = get_relevant_policy(content, tr.get("summary", "")) if _policy_available() else []
            steps = generate_next_steps(content, tr, policy_chunks)
            if steps:
                st.markdown("**Suggested next steps**")
                for s in steps:
                    st.markdown(f"- {s}")

        # Action buttons
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            if st.button("Approve & Send", type="primary", key="staff_approve_send"):
                subject = f"[TriageAI] Re: {tr.get('summary', 'Your message')}"
                # Try to resume the HITL workflow if thread_id exists
                if thread_id and hitl_status == "pending_review":
                    try:
                        from graph.workflow import resume_workflow
                        _safety, updated_triage = resume_workflow(
                            thread_id=thread_id,
                            edited_draft=edited_draft if edited_draft != existing_draft else None,
                        )
                        updated_triage["status"] = "Resolved/Routed"
                        update_message_triage_result(selected.get("id"), updated_triage)
                        st.success(f"Approved and sent to {email}!")
                        st.session_state.selected_message_id = None
                        st.rerun()
                    except Exception as e:
                        st.error(f"Resume failed: {e}")
                else:
                    # No HITL thread — send directly
                    send_resolution_email(email, subject, edited_draft)
                    new_tr = {**(tr or {}), "status": "Resolved/Routed", "hitl_status": "approved"}
                    update_message_triage_result(selected.get("id"), new_tr)
                    st.success(f"Sent to {email}!")
                    st.session_state.selected_message_id = None
                    st.rerun()
        with btn_col2:
            if st.button("Route to ER", key="approve_route_er"):
                new_tr = {**(tr or {}), "status": "Resolved/Routed", "hitl_status": "approved"}
                if update_message_triage_result(selected.get("id"), new_tr):
                    body = "Your case has been reviewed. Please proceed to the ER as directed by staff."
                    send_resolution_email(email, "Urgent: Proceed to ER", body)
                    st.success("Routed to ER and patient emailed!")
                    st.session_state.selected_message_id = None
                    st.rerun()
                else:
                    st.error("Failed to update message.")
        with btn_col3:
            if st.button("Dismiss", key="staff_dismiss"):
                dismissed_tr = {**tr, "hitl_status": "dismissed", "status": "Resolved/Routed"}
                update_message_triage_result(selected.get("id"), dismissed_tr)
                st.info("Message dismissed.")
                st.session_state.selected_message_id = None
                st.rerun()


def render_pending_approvals():
    """HITL Pending Approvals: messages awaiting staff review before communication."""
    messages = get_all_messages_for_staff(active_only=True)
    pending = [m for m in messages if (m.get("triage_result") or {}).get("hitl_status") == "pending_review"]

    if not pending:
        st.caption("No messages pending approval. All workflows are either auto-completed or already approved.")
        return

    st.markdown(f"**{len(pending)} message(s) awaiting review**")

    for idx, m in enumerate(pending):
        tr = m.get("triage_result") or {}
        full_name = m.get("full_name") or "—"
        email = m.get("email") or "—"
        patient_id = m.get("patient_id") or "—"
        content = m.get("content") or ""
        urgency = tr.get("urgency", "NORMAL")
        thread_id = tr.get("thread_id", "")
        draft = tr.get("draft_reply", "")

        with st.container(border=True):
            st.markdown(f"{_urgency_emoji(urgency)} **{urgency}** — **{full_name}** · {email} · `{patient_id}`")
            st.caption(f"Message: {content[:200]}{'…' if len(content) > 200 else ''}")

            # AI analysis summary
            with st.expander("AI Analysis", expanded=False):
                st.markdown(f"**Intent:** {tr.get('intent', '—')}")
                st.markdown(f"**Summary:** {tr.get('summary', '—')}")
                st.markdown(f"**Queue:** {tr.get('recommended_queue', '—')}")
                st.markdown(f"**Confidence:** {tr.get('confidence', '—')}")
                if tr.get("checklist"):
                    st.markdown("**Checklist:**")
                    for item in tr["checklist"]:
                        st.markdown(f"- {item}")
                if tr.get("safety_flagged"):
                    st.warning(f"⚠️ Safety flagged: {tr.get('safety_reason', '')}")

            # Editable draft reply
            edited_draft = st.text_area(
                "Draft reply (edit before approving)",
                value=draft,
                height=150,
                key=f"draft_{m.get('id', idx)}",
            )

            # Action buttons
            col_approve, col_reject = st.columns(2)
            with col_approve:
                if st.button("Approve & Send", type="primary", key=f"approve_{m.get('id', idx)}"):
                    if not thread_id:
                        st.error("No thread_id found — cannot resume workflow.")
                    else:
                        try:
                            from graph.workflow import resume_workflow
                            _safety, updated_triage = resume_workflow(
                                thread_id=thread_id,
                                edited_draft=edited_draft if edited_draft != draft else None,
                            )
                            # Update the message store with the approved result
                            updated_triage["status"] = "Resolved/Routed"
                            update_message_triage_result(m.get("id"), updated_triage)
                            st.success(f"Approved and sent to {email}!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Resume failed: {e}")

            with col_reject:
                if st.button("Dismiss", key=f"dismiss_{m.get('id', idx)}"):
                    dismissed_tr = {**tr, "hitl_status": "dismissed", "status": "Resolved/Routed"}
                    update_message_triage_result(m.get("id"), dismissed_tr)
                    st.info("Message dismissed.")
                    st.rerun()


def main():
    patient = get_patient_context(st.session_state)
    if patient is None:
        render_login_register()
        return

    # Logged in: three tabs — Patient view, Staff view, Pending Approvals
    st.title("🏥 TriageAI")
    st.write(f"**{patient.full_name}** · {patient.patient_id} · *{patient.email}*")
    if st.button("Log out", key="logout_main"):
        clear_patient_context(st.session_state)
        st.session_state.user_id = None
        st.rerun()
        return

    tab_patient, tab_staff, tab_approvals = st.tabs(["Patient Chat", "Staff view", "Pending Approvals"])
    with tab_patient:
        st.caption("Chat with the AI triage assistant. Attach images for visual assessment.")
        render_patient_portal()
    with tab_staff:
        st.caption("What staff sees: all patient messages grouped by patient.")
        render_staff_view()
    with tab_approvals:
        st.caption("Messages awaiting staff review before communication is sent.")
        render_pending_approvals()


if __name__ == "__main__":
    main()
