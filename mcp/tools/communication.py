"""
MCP communication tools: Email and notification logic.
Currently a mock implementation; replace with a real provider (Resend, SendGrid, smtplib) later.
"""


def send_resolution_email(patient_email: str, subject: str, body: str) -> bool:
    """
    Send a resolution/notification email to the patient.
    Currently a mock that prints to console.
    TODO: Replace with a real email provider (e.g. Resend, SendGrid, or smtplib).
    """
    print(f"[Email] To: {patient_email} | Subject: {subject}")
    print(f"   Body: {body[:200]}{'...' if len(body) > 200 else ''}")
    return True


def send_notification(recipient: str, message: str, channel: str = "email") -> bool:
    """
    Generic notification sender. Supports different channels (email, sms, etc.).
    Currently a mock for all channels.
    """
    print(f"[Notification] Channel: {channel} | To: {recipient}")
    print(f"   Message: {message[:200]}{'...' if len(message) > 200 else ''}")
    return True
