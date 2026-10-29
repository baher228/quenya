import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .common import ShopSession, make_row, request_succeeded, unique_rows

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.sources.listing_readers import parse_listing  # noqa: E402


def _tavily_rows(shop, session, page, gender):
    key = os.environ.get("TAVILY_API_KEY")
    if not key or shop.get("key") != "ralphlauren":
        return []
    session._pace()
    if session.count >= session.max_requests:
        return []
    session.count += 1
    session.last_request = time.monotonic()
    endpoint = "https://api.tavily.com/extract"
    response = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        timeout=90,
        json={"urls": [page], "extract_depth": "advanced", "format": "markdown"},
    )
    session.requests.append(
        {"url": endpoint, "status": response.status_code, "purpose": "tavily_extract"}
    )
    response.raise_for_status()
    content = (response.json().get("results") or [{}])[0].get("raw_content") or ""
    rows, seen = [], set()
    for match in re.finditer(r'\]\((/en/[^)\s]*?-\d{6,9}\.html)[^)\s]*\s+"([^"]+)"\)', content):
        path, title = match.groups()
        if path in seen:
            continue
        image_matches = re.findall(
            r"https://dtcralphlauren\.scene7\.com/[^)\s]+",
            content[max(0, match.start() - 2500) : match.start()],
        )
        price_match = re.search(r"£([\d,]+\.\d\d)", content[match.end() : match.end() + 2500])
        if not image_matches or not price_match:
            continue
        seen.add(path)
        image = image_matches[-1].split("?")[0] + "?$rl_4x5_pdp$"
        row = make_row(
            shop,
            title,
            float(price_match.group(1).replace(",", "")),
            image,
            urljoin("https://www.ralphlauren.co.uk", path),
            gender,
            page,
            {"availability": "InStock", "price_source": "rl_category_tavily_gbp"},
        )
        if row:
            rows.append(row)
    return rows


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    rows = []
    page_key = (
        "tee_pages"
        if shop.get("_category_set") == "tees" and shop.get("tee_pages")
        else "pages"
    )
    for page in shop.get(page_key, [])[: 1 if probe else None]:
        gender, category_url = page["gender"], page["url"]
        page_limit = (
            1 if probe or shop.get("key") != "ralphlauren" else int(shop.get("max_pages", 3))
        )
        for page_number in range(page_limit):
            request_url = category_url
            if shop.get("key") == "ralphlauren":
                parts = urlsplit(category_url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query.update({"start": str(page_number * 30), "sz": "30"})
                request_url = urlunsplit(
                    (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
                )
            fetched = session.get(request_url, browser=True)
            if fetched is None:
                shop["_status"] = "robots"
                shop["_reason"] = f"disallowed by robots.txt: {request_url}"
                break
            body, final_url, _ = fetched
            if not request_succeeded(session):
                continue
            records = parse_listing(body, final_url, shop["domain"], gender)
            page_rows = []
            for record in records:
                row = make_row(
                    shop,
                    record.get("title", ""),
                    record.get("price_gbp", 0),
                    record.get("image_url", ""),
                    record.get("source_url", ""),
                    gender,
                    request_url,
                    {
                        "availability": record.get("availability") or "InStock",
                        "tile_record": record,
                    },
                )
                if row:
                    page_rows.append(row)
            if not page_rows:
                page_rows.extend(_tavily_rows(shop, session, request_url, gender))
            rows.extend(page_rows)
            session.emit(page_rows)
            if probe:
                shop["_probe_rows"] = len(page_rows)
                break
            if shop.get("key") == "ralphlauren" and not page_rows:
                break
    return unique_rows(rows)
