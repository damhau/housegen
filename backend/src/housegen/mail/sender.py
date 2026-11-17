"""Email through Resend (#107), with its official SDK. The SDK is synchronous: each send runs in a
worker thread, off the event loop."""

from __future__ import annotations

from dataclasses import dataclass

import anyio
import resend

from housegen.core.config import get_settings


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str
    reply_to: str | None = None


class EmailError(Exception):
    """Resend did not take the email; the message says why, without the recipient's address."""


def _reason(error: Exception, recipient: str) -> str:
    # the HTTP client's own errors spell out the pool, the retries and the socket
    if not isinstance(error, resend.exceptions.ResendError):
        return "the email service could not be reached"
    if error.error_type == "HttpClientError":
        return "the email service could not be reached"
    return str(error.message or type(error).__name__).replace(recipient, "the recipient")


async def send(email: Email) -> str:
    """Hand the email to Resend. Returns its message id; raises EmailError."""
    settings = get_settings()
    params: resend.Emails.SendParams = {
        "from": settings.EMAIL_FROM.strip(),
        "to": [email.to],
        "subject": email.subject,
        "text": email.text,
        "html": email.html,
    }
    if email.reply_to:
        params["reply_to"] = email.reply_to

    def call() -> resend.Emails.SendResponse:
        resend.api_key = settings.RESEND_API_KEY.strip()
        return resend.Emails.send(params)

    try:
        response = await anyio.to_thread.run_sync(call)
    except Exception as e:
        raise EmailError(_reason(e, email.to)) from e
    return str(response["id"])
