import smtplib
import ssl
from email.message import EmailMessage

from src.core.config import settings


class EmailDeliveryUnavailable(RuntimeError):
    pass


def ensure_email_delivery_configured() -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise EmailDeliveryUnavailable("Email delivery is not configured")


def send_account_email(recipient: str, subject: str, body: str) -> None:
    ensure_email_delivery_configured()
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_USE_STARTTLS:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryUnavailable("Email delivery failed") from exc
