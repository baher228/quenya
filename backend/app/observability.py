import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk


def before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    request = event.get("request")
    if not isinstance(request, dict):
        return event

    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"authorization", "cookie"}
        }
    elif isinstance(headers, list):
        request["headers"] = [
            pair
            for pair in headers
            if not (
                isinstance(pair, (list, tuple))
                and pair
                and str(pair[0]).lower() in {"authorization", "cookie"}
            )
        ]

    request.pop("query_string", None)
    url = request.get("url")
    if isinstance(url, str):
        request["url"] = urlunsplit(urlsplit(url)._replace(query="", fragment=""))
    return event


def init_sentry(component: str) -> bool:
    dsn = os.getenv("QUENYA_SENTRY_DSN")
    if not dsn or not dsn.strip():
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("QUENYA_ENV", "production"),
        release=os.getenv("QUENYA_RELEASE") or None,
        traces_sample_rate=float(os.getenv("QUENYA_SENTRY_TRACES", "0.05")),
        send_default_pii=False,
        before_send=before_send,
    )
    sentry_sdk.set_tag("component", component)
    return True
