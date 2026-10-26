import hmac
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.sources import vinted

router = APIRouter()


@router.get("/internal/vinted/search")
def search_vinted(
    q: str = Query(..., min_length=1, max_length=200),
    gender: Literal["m", "w", "u"] = Query(...),
    per_page: int = Query(default=10, ge=1, le=96),
    page: int = Query(default=1, ge=1, le=20),
    proxy_key: str | None = Header(default=None, alias="X-Quenya-Proxy-Key"),
) -> dict[str, list[dict]]:
    expected_key = os.getenv("QUENYA_VINTED_PROXY_KEY", "")
    if not expected_key:
        raise HTTPException(status_code=404, detail="Not found")
    provided_key = (proxy_key or "").encode("utf-8")
    if not hmac.compare_digest(provided_key, expected_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        items = vinted.search_direct(q, gender, per_page, page)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc)[:300]},
        )
    return {"items": items}
