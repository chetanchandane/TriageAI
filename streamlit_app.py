"""
TriageAI Streamlit app: login/register, patient portal, and staff view.
Messages are tied to patient identity (patient_id, full_name) for personalization and staff identification.
"""
import streamlit as st
from dotenv import load_dotenv

from auth import register, login, get_current_user, is_supabase_configured
from state import get_patient_context, set_patient_context, clear_patient_context
from messages_store import save_message, get_all_messages_for_staff, get_messages_for_patient

load_dotenv()

# Page config
st.set_page_config(page_title="TriageAI Patient Portal", page_icon="🏥", layout="centered")

# Initialize session state
if "patient" not in st.session_state:
    st.session_state.patient = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None


def run_triage(patient_message: str):
    """Run triage on message; returns TriageResult as dict or None on failure."""
    try:
        from triage_test import test_triage
        result = test_triage(patient_message)
        return result.model_dump() if result else None
    except Exception as e:
        st.error(f"Triage failed: {e}")
        return None


def render_login_register():
    """Show login and register forms."""
    st.title("🏥 TriageAI Patient Portal")
    if not is_supabase_configured():
        st.info("Running in **demo mode** (no Supabase). Data is in-memory only.")
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
                triage_result = run_triage(content.strip())
                save_message(
                    user_id=patient.user_id,
                    patient_id=patient.patient_id,
                    full_name=patient.full_name,
                    email=patient.email,
                    content=content.strip(),
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


def render_staff_view():
    """What staff sees: all messages grouped by patient."""
    messages = get_all_messages_for_staff()
    if not messages:
        st.caption("No messages yet.")
        return

    # Group by patient
    by_patient: dict[str, list] = {}
    for m in messages:
        key = f"{m.get('patient_id', '')}|{m.get('full_name', '')}|{m.get('email', '')}"
        if key not in by_patient:
            by_patient[key] = []
        by_patient[key].append(m)

    for key, msgs in by_patient.items():
        parts = key.split("|", 2)
        patient_id = parts[0] or "—"
        full_name = parts[1] if len(parts) > 1 else "—"
        email = parts[2] if len(parts) > 2 else "—"
        st.markdown(f"**{full_name}** · `{patient_id}` · {email}")
        for m in msgs:
            st.markdown(f"- *{m.get('created_at', '')[:19]}* — {m.get('content', '')}")
            if m.get("triage_result"):
                tr = m["triage_result"]
                st.caption(f"  Urgency: {tr.get('urgency')} · {tr.get('recommended_queue')} · {tr.get('summary')}")
        st.divider()


def main():
    # Restore session from auth (Supabase) if needed
    if st.session_state.patient is None and is_supabase_configured():
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
