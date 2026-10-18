"""
Email notifications for long-running jobs (selfplay_job.py, and any later
batch job) -- so the person who started a two-day job hears about progress
and the finish without watching logs.

Sends through Gmail's SMTP with an App password (myaccount.google.com/
apppasswords), configured entirely by environment variables:

    NOTIFY_SMTP_USER       the Gmail address that sends (e.g. you@gmail.com)
    NOTIFY_SMTP_PASSWORD   that account's 16-letter App password
    NOTIFY_EMAIL_TO        recipient; defaults to NOTIFY_SMTP_USER
    NOTIFY_SMTP_HOST       default smtp.gmail.com
    NOTIFY_SMTP_PORT       default 465 (implicit TLS)

Unconfigured, every call is a no-op. A failed send is logged and swallowed:
a notification must never be the reason a job stops.
"""
import os
import smtplib
from email.message import EmailMessage


def is_configured() -> bool:
    return bool(os.environ.get("NOTIFY_SMTP_USER") and os.environ.get("NOTIFY_SMTP_PASSWORD"))


def send_email(subject: str, body: str) -> bool:
    """True if the message was handed to the mail server."""
    if not is_configured():
        return False
    user = os.environ["NOTIFY_SMTP_USER"]
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = os.environ.get("NOTIFY_EMAIL_TO") or user
    message.set_content(body)
    try:
        host = os.environ.get("NOTIFY_SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("NOTIFY_SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, os.environ["NOTIFY_SMTP_PASSWORD"].replace(" ", ""))
            smtp.send_message(message)
        return True
    except Exception as e:  # noqa: BLE001 -- see module docstring
        print(f"notification email failed ({type(e).__name__}: {e})", flush=True)
        return False
