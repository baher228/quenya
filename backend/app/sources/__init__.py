import re
from urllib.parse import urlsplit

STOCK_PHRASE = re.compile(
    r"^(?:out\s+of\s+stock|sold\s+out|this\s+item\s+is\s+sold\s+out|"
    r"no\s+longer\s+available|currently\s+unavailable)$",
    re.IGNORECASE,
)
_UNAVAILABLE_STATUSES = {"outofstock", "soldout", "discontinued"}


def is_unavailable(availability: str | None) -> bool:
    if not availability:
        return False
    path = urlsplit(availability.strip()).path.rstrip("/")
    return path.rsplit("/", 1)[-1].casefold() in _UNAVAILABLE_STATUSES
