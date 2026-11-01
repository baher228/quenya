import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

from app import config

TESTFLIGHT_APP_STORE_URL = "https://apps.apple.com/app/testflight/id899247664"


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


def invite_email(
    name: str | None,
    open_url: str,
    testflight_url: str,
) -> tuple[str, str, str]:
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Your Quenya invite: how to get the iPhone app"
    intro = (
        "You're in! Quenya is an iPhone app in beta, so it's installed through "
        "Apple's TestFlight app rather than the App Store. It takes about 2 minutes. "
        "Open this email on your iPhone (iOS 17 or later) and follow these steps:"
    )
    privacy = (
        "Your link is just for you, so please don't forward it. "
        "Questions? Just reply to this email."
    )
    text = (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        "1. Get TestFlight. It's Apple's free app for beta apps. If you don't have it yet, "
        "install it from the App Store: "
        f"{TESTFLIGHT_APP_STORE_URL}\n\n"
        f"2. Install Quenya. Tap this link: {testflight_url}\n"
        '   It opens TestFlight. Tap "Accept", then "Install".\n\n'
        f"3. Sign in. Come back to this email and tap your personal link: {open_url}\n"
        '   Then tap "Open in the app" and Quenya signs you in.\n'
        '   If that doesn\'t work, open Quenya and paste the same link into '
        'the "Invite link" box.\n\n'
        f"{privacy}\n\n"
        "Quenya\n"
        "hello@quenya.co.uk"
    )
    html_greeting = escape(greeting)
    html_intro = escape(intro)
    html_app_store_url = escape(TESTFLIGHT_APP_STORE_URL)
    html_app_store_href = escape(TESTFLIGHT_APP_STORE_URL, quote=True)
    html_testflight_url = escape(testflight_url)
    html_testflight_href = escape(testflight_url, quote=True)
    html_open_url = escape(open_url)
    html_open_href = escape(open_url, quote=True)
    html_privacy = escape(privacy)
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#24211f;line-height:1.6">'
        f'<p style="margin:0 0 16px">{html_greeting}</p>'
        f'<p style="margin:0 0 12px">{html_intro}</p>'
        '<ol style="margin:0 0 16px;padding-left:24px">'
        '<li style="margin:0 0 12px">Get TestFlight. It\'s Apple\'s free app for beta apps. '
        "If you don't have it yet, install it from the App Store: "
        f'<a href="{html_app_store_href}" style="color:#68744f">'
        f"{html_app_store_url}</a></li>"
        '<li style="margin:0 0 12px">Install Quenya. Tap this link: '
        f'<a href="{html_testflight_href}" style="color:#68744f">'
        f'{html_testflight_url}</a><br>'
        'It opens TestFlight. Tap "Accept", then "Install".</li>'
        '<li style="margin:0 0 12px">Sign in. Come back to this email and tap your personal '
        f'link: <a href="{html_open_href}" style="color:#68744f">'
        f'{html_open_url}</a><br>'
        'Then tap "Open in the app" and Quenya signs you in.<br>'
        'If that doesn\'t work, open Quenya and paste the same link into the "Invite link" box.'
        "</li></ol>"
        f'<p style="margin:0 0 16px">{html_privacy}</p>'
        f'<p style="margin:0">{escape("Quenya")}<br>'
        f'{escape("hello@quenya.co.uk")}</p>'
        "</div>"
    )
    return subject, text, html_body
