import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

from app import config


class MailNotConfigured(RuntimeError):
    pass


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    if not config.QUENYA_SMTP_PASSWORD:
        raise MailNotConfigured("SMTP password is not configured")

    message = EmailMessage()
    message["From"] = config.QUENYA_MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message["Reply-To"] = config.QUENYA_SMTP_USER
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="quenya.co.uk")
    message.set_content(text)
    if html is not None:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP(
        config.QUENYA_SMTP_HOST,
        config.QUENYA_SMTP_PORT,
        timeout=20,
    ) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(config.QUENYA_SMTP_USER, config.QUENYA_SMTP_PASSWORD)
        server.send_message(message)


def invite_email(name: str | None, invite_url: str) -> tuple[str, str, str]:
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Your Quenya tester invite"
    intro = "You're in. Open this link on your phone to start using Quenya:"
    privacy = "The link is just for you, so please don't forward it."
    text = (
        f"{greeting}\n\n"
        f"{intro}\n"
        f"{invite_url}\n\n"
        f"{privacy}\n\n"
        "Quenya\n"
        "hello@quenya.co.uk"
    )
    html_greeting = escape(greeting)
    html_intro = escape(intro)
    html_privacy = escape(privacy)
    html_url = escape(invite_url)
    html_href = escape(invite_url, quote=True)
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#24211f;line-height:1.6">'
        f'<p style="margin:0 0 16px">{html_greeting}</p>'
        f'<p style="margin:0 0 12px">{html_intro}</p>'
        f'<p style="margin:0 0 16px"><a href="{html_href}" '
        f'style="color:#68744f">{html_url}</a></p>'
        f'<p style="margin:0 0 16px">{html_privacy}</p>'
        f'<p style="margin:0">{escape("Quenya")}<br>'
        f'{escape("hello@quenya.co.uk")}</p>'
        "</div>"
    )
    return subject, text, html_body
