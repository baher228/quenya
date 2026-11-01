import smtplib

import pytest
from app import accounts, config, db, mailer, main
from app.waitlist import reset_rate_limit
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_invite_email_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "invite-email.sqlite")
    monkeypatch.setattr(config, "QUENYA_SMTP_PASSWORD", "x")
    monkeypatch.setattr(config, "QUENYA_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(config, "QUENYA_SMTP_PORT", 587)
    monkeypatch.setattr(config, "QUENYA_SMTP_USER", "hello@quenya.co.uk")
    monkeypatch.setattr(
        config,
        "QUENYA_MAIL_FROM",
        "Quenya <hello@quenya.co.uk>",
    )
    db._INITIALIZED_DATABASES.clear()
    reset_rate_limit()
    yield
    reset_rate_limit()
    db._INITIALIZED_DATABASES.clear()


def _admin_headers():
    _user, invite = accounts.create_user("Admin", "admin")
    token = accounts.redeem_invite(invite)
    return {"Authorization": f"Bearer {token}"}


def _signup(client, email="ada@example.com", name="Ada Lovelace"):
    response = client.post(
        "/api/waitlist",
        json={"email": email, "name": name, "consent": True},
    )
    assert response.status_code == 200, response.text
    with db.db_connection() as connection:
        return connection.execute(
            "SELECT id FROM waitlist WHERE email=?",
            (email,),
        ).fetchone()["id"]


def _approve(client, headers, email="ada@example.com", name="Ada Lovelace"):
    waitlist_id = _signup(client, email=email, name=name)
    response = client.post(
        f"/api/admin/waitlist/{waitlist_id}/approve",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return waitlist_id, response.json()["invite_url"]


def _install_smtp(monkeypatch, *, login_error=None):
    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self, context):
            events.append(("starttls", context))

        def login(self, user, password):
            events.append(("login", user, password))
            if login_error is not None:
                raise login_error

        def send_message(self, message):
            events.append(("send_message", message))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    return events


def test_approved_invite_is_sent_over_smtp(isolated_invite_email_db, monkeypatch):
    headers = _admin_headers()
    events = _install_smtp(monkeypatch)

    with TestClient(main.app) as client:
        waitlist_id, invite_url = _approve(client, headers)
        response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": invite_url},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "sent_to": "ada@example.com"}
    assert [event[0] for event in events] == [
        "connect",
        "starttls",
        "login",
        "send_message",
    ]
    assert events[2][1:] == ("hello@quenya.co.uk", "x")
    message = events[3][1]
    assert message["To"] == "ada@example.com"
    assert "hello@quenya.co.uk" in message["From"]
    assert message["Subject"] == "Your Quenya invite: how to get the iPhone app"
    token = invite_url.removeprefix(f"{config.QUENYA_APP_URL}/?invite=")
    open_url = f"{config.QUENYA_APP_URL}/open?invite={token}"
    text = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert (
        "You're in! Quenya is an iPhone app in beta, so it's installed through "
        "Apple's TestFlight app rather than the App Store. It takes about 2 minutes. "
        "Open this email on your iPhone (iOS 17 or later) and follow these steps:"
    ) in text
    for body in (text, html_body):
        assert mailer.TESTFLIGHT_APP_STORE_URL in body
        assert config.QUENYA_TESTFLIGHT_URL in body
        assert open_url in body
        assert "Accept" in body
        assert "Install" in body
    assert "<ol" in html_body
    assert invite_url not in text
    assert invite_url not in html_body


def test_missing_smtp_password_returns_503_without_connecting(
    isolated_invite_email_db,
    monkeypatch,
):
    headers = _admin_headers()
    monkeypatch.setattr(config, "QUENYA_SMTP_PASSWORD", "")
    constructed = []

    def unexpected_smtp(*_args, **_kwargs):
        constructed.append(True)
        raise AssertionError("SMTP must not be constructed")

    monkeypatch.setattr(mailer.smtplib, "SMTP", unexpected_smtp)
    with TestClient(main.app) as client:
        waitlist_id, invite_url = _approve(client, headers)
        response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": invite_url},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Email isn't set up on the server"}
    assert constructed == []


def test_another_approved_testers_invite_is_rejected(
    isolated_invite_email_db,
    monkeypatch,
):
    headers = _admin_headers()
    _install_smtp(monkeypatch)
    with TestClient(main.app) as client:
        first_id, first_url = _approve(client, headers)
        second_id, _second_url = _approve(
            client,
            headers,
            email="grace@example.com",
            name="Grace Hopper",
        )
        response = client.post(
            f"/api/admin/waitlist/{second_id}/email-invite",
            headers=headers,
            json={"invite_url": first_url},
        )

    assert first_id != second_id
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invite link doesn't belong to this tester"
    }


def test_foreign_host_invite_url_is_rejected(isolated_invite_email_db):
    headers = _admin_headers()
    with TestClient(main.app) as client:
        waitlist_id, invite_url = _approve(client, headers)
        response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": invite_url.replace("app.quenya.co.uk", "example.com")},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid invite link"}


def test_pending_entry_cannot_be_emailed(isolated_invite_email_db):
    headers = _admin_headers()
    with TestClient(main.app) as client:
        waitlist_id = _signup(client)
        response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": "https://app.quenya.co.uk/?invite=abc"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Waitlist entry is not approved"}


def test_email_invite_requires_admin(isolated_invite_email_db):
    admin_headers = _admin_headers()
    tester, invite = accounts.create_user("Tester", "tester")
    tester_token = accounts.redeem_invite(invite)
    with TestClient(main.app) as client:
        waitlist_id, invite_url = _approve(client, admin_headers)
        no_auth = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            json={"invite_url": invite_url},
        )
        forbidden = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers={"Authorization": f"Bearer {tester_token}"},
            json={"invite_url": invite_url},
        )

    assert tester["role"] == "tester"
    assert no_auth.status_code == 401
    assert forbidden.status_code == 403


def test_smtp_authentication_error_returns_502(isolated_invite_email_db, monkeypatch):
    headers = _admin_headers()
    events = _install_smtp(
        monkeypatch,
        login_error=smtplib.SMTPAuthenticationError(535, b"Authentication failed"),
    )
    with TestClient(main.app) as client:
        waitlist_id, invite_url = _approve(client, headers)
        response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": invite_url},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Couldn't send the email"}
    assert [event[0] for event in events] == ["connect", "starttls", "login"]


def test_invite_rotation_invalidates_the_old_email_link(
    isolated_invite_email_db,
    monkeypatch,
):
    headers = _admin_headers()
    events = _install_smtp(monkeypatch)
    with TestClient(main.app) as client:
        waitlist_id, old_url = _approve(client, headers)
        with db.db_connection() as connection:
            tester_id = connection.execute(
                "SELECT user_id FROM waitlist WHERE id=?",
                (waitlist_id,),
            ).fetchone()["user_id"]
        rotated = client.post(
            f"/api/admin/users/{tester_id}/rotate-invite",
            headers=headers,
        )
        assert rotated.status_code == 200, rotated.text
        new_url = rotated.json()["invite_url"]
        old_response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": old_url},
        )
        new_response = client.post(
            f"/api/admin/waitlist/{waitlist_id}/email-invite",
            headers=headers,
            json={"invite_url": new_url},
        )

    assert old_response.status_code == 400
    assert new_response.status_code == 200, new_response.text
    assert sum(event[0] == "send_message" for event in events) == 1


def test_invite_email_escapes_name_in_html():
    subject, text, html_body = mailer.invite_email(
        "<Ada & Co>",
        "https://app.quenya.co.uk/open?invite=abc123",
        "https://testflight.apple.com/join/DNNd7Naj",
    )

    assert subject == "Your Quenya invite: how to get the iPhone app"
    assert "You're in! Quenya is an iPhone app in beta" in text
    assert "Hi <Ada & Co>," in text
    assert "Hi &lt;Ada &amp; Co&gt;," in html_body
    assert "<Ada & Co>" not in html_body
    for body in (text, html_body):
        assert mailer.TESTFLIGHT_APP_STORE_URL in body
        assert "https://testflight.apple.com/join/DNNd7Naj" in body
        assert "https://app.quenya.co.uk/open?invite=abc123" in body
        assert "Accept" in body
        assert "Install" in body
    assert "<ol" in html_body
    assert (
        f'href="{mailer.TESTFLIGHT_APP_STORE_URL}"'
        in html_body
    )
    assert 'href="https://app.quenya.co.uk/open?invite=abc123"' in html_body
