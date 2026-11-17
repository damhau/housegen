"""The invitation email (#107): who invites, to which organisation, the link and until when. Plain
text and simple HTML, no images or tracking; replies go to the person who invites."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from housegen.accounts.models import Invitation, Organisation, User
from housegen.mail import sender
from housegen.mail.sender import Email, EmailError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    sent: bool
    error: str | None = None


def _when(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    return f"{moment.day} {moment:%B %Y, %H:%M} UTC"


def invitation_email(
    invitation: Invitation, organisation: Organisation, inviter: User, url: str
) -> Email:
    who = inviter.name.strip() or inviter.email or "Someone"
    by = f"{who} ({inviter.email})" if inviter.email and inviter.email != who else who
    until = _when(invitation.expires_at)
    subject = f"{who} invited you to {organisation.name} on housegen"
    text = (
        f"{by} invited you to join {organisation.name} on housegen, where its members work "
        "on 3D models of houses together.\n\n"
        f"Accept the invitation:\n{url}\n\n"
        f"The link works once, until {until}. Sign in with Google or Microsoft, with any "
        "account: whoever opens the link joins, so keep it to yourself.\n\n"
        f"Not expecting this? Ignore this email, or reply to ask {who}."
    )
    e = html.escape
    page = (
        '<!doctype html><html lang="en"><body style="font-family: sans-serif; line-height: 1.5">'
        f"<p>{e(by)} invited you to join <strong>{e(organisation.name)}</strong> on housegen, "
        "where its members work on 3D models of houses together.</p>"
        f'<p><a href="{e(url, quote=True)}">Accept the invitation</a></p>'
        f"<p>The link works once, until {e(until)}. Sign in with Google or Microsoft, with any "
        "account: whoever opens the link joins, so keep it to yourself.</p>"
        f"<p>Not expecting this? Ignore this email, or reply to ask {e(who)}.</p>"
        '<p style="color: #666; font-size: 12px">If the link does not open, copy this address '
        f"into your browser: {e(url)}</p>"
        "</body></html>"
    )
    return Email(
        to=invitation.email,
        subject=subject,
        text=text,
        html=page,
        reply_to=inviter.email or None,
    )


async def send_invitation(
    invitation: Invitation,
    organisation: Organisation,
    inviter: User,
    url: str,
    now: datetime,
) -> Outcome:
    """Email the invitation; a failure keeps it (its link still works) and says why. Logs the
    invitation and Resend's message id, never the address."""
    try:
        message_id = await sender.send(invitation_email(invitation, organisation, inviter, url))
    except EmailError as e:
        logger.warning(
            "email.failed",
            extra={
                "invitation_id": invitation.id,
                "organisation_id": organisation.id,
                "error": str(e),
            },
        )
        return Outcome(sent=False, error=str(e))
    invitation.emailed_at = now
    logger.info(
        "email.sent",
        extra={
            "invitation_id": invitation.id,
            "organisation_id": organisation.id,
            "message_id": message_id,
        },
    )
    return Outcome(sent=True)
