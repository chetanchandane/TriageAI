"""
MCP communication tools: Email and notification logic.

Uses Resend for transactional email when RESEND_API_KEY is set.
Falls back to console print (mock) when the key is missing, so the app
works in demo mode without email configuration.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# Sender address — Resend requires a verified domain or their onboarding address.
# Update this once you have a verified domain in your Resend dashboard.
_DEFAULT_FROM = os.environ.get("RESEND_FROM_EMAIL", "TriageAI <onboarding@resend.dev>")


def send_resolution_email(patient_email: str, subject: str, body: str) -> bool:
    """
    Send a resolution/notification email to the patient via Resend.
    Falls back to console mock if RESEND_API_KEY is not configured.
    """
    api_key = os.environ.get("RESEND_API_KEY")

    if not api_key:
        print(f"[Email mock — set RESEND_API_KEY to send real emails]")
        print(f"  To: {patient_email} | Subject: {subject}")
        print(f"  Body: {body[:200]}{'...' if len(body) > 200 else ''}")
        return True

    try:
        import resend

        resend.api_key = api_key
        resend.Emails.send({
            "from": _DEFAULT_FROM,
            "to": [patient_email],
            "subject": subject,
            "text": body,
        })
        print(f"[Email sent] To: {patient_email} | Subject: {subject}")
        return True
    except Exception as e:
        print(f"[Email failed] To: {patient_email} | Error: {e}")
        return False


def send_notification(recipient: str, message: str, channel: str = "email") -> bool:
    """
    Generic notification sender.
    Routes to send_resolution_email for the email channel.
    Other channels remain mock for now.
    """
    if channel == "email":
        return send_resolution_email(recipient, "TriageAI Notification", message)

    print(f"[Notification mock] Channel: {channel} | To: {recipient}")
    print(f"  Message: {message[:200]}{'...' if len(message) > 200 else ''}")
    return True
