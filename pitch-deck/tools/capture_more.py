#!/usr/bin/env python3
"""Second pass on an existing board: swipe more cards and capture the deck, outfits and taste.

Usage: SESSION_TOKEN=... python capture_more.py OUT_DIR [SWIPES]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_app import KEEP, PASS, SAFE_AREA_CSS, shot, top_card, wait_for_card

APP = os.getenv("APP_URL", "http://localhost:5173")
OUT = Path(sys.argv[1])
SWIPES = int(sys.argv[2]) if len(sys.argv) > 2 else 6
OUT.mkdir(parents=True, exist_ok=True)


def dismiss_route_note(page) -> None:
    note = page.locator(".route button.x")
    if note.count():
        note.first.click()
        page.wait_for_timeout(300)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/local/bin/google-chrome")
    context = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=3,
                                  is_mobile=True, has_touch=True, locale="en-GB")
    context.add_init_script(
        f"localStorage.setItem('quenya.token', {os.environ['SESSION_TOKEN']!r});"
        "sessionStorage.setItem('q-nudged', '1');"
    )
    page = context.new_page()
    page.set_default_timeout(420_000)
    page.goto(APP)
    page.add_style_tag(content=SAFE_AREA_CSS)
    page.wait_for_selector("nav.tabs")
    dismiss_route_note(page)

    if SWIPES < 0:
        page.click("nav.tabs >> text=Outfits")
        page.wait_for_timeout(1000)
        page.locator("main button", has_text="Build outfits").first.click()
        page.wait_for_selector(".outfit", timeout=420_000)
        page.wait_for_timeout(4000)
        dismiss_route_note(page)
        shot(page, "outfit")
        for index in range(page.locator(".outfit").count()):
            page.locator(".outfit").nth(index).scroll_into_view_if_needed()
            shot(page, f"outfit-{index + 1}")
        browser.close()
        raise SystemExit(0)

    page.click("nav.tabs >> text=Swipe")
    page.wait_for_timeout(1200)
    if page.locator(".pick-row.on").count():
        page.locator(".pick-row.on").first.click()
    elif page.locator(".pick-row").count():
        page.locator(".pick-row").first.click()
    page.wait_for_timeout(1500)
    if page.locator(".deck-done").count():
        page.locator(".deck-done button", has_text="10 more cards").click()
    if not wait_for_card(page, 120):
        shot(page, "deck-problem")
        raise SystemExit("no cards")
    dismiss_route_note(page)
    shot(page, "more-00")
    for index in range(1, SWIPES + 1):
        text = top_card(page)
        liked = bool(KEEP.search(text)) and not PASS.search(text)
        page.keyboard.press("ArrowRight" if liked else "ArrowLeft")
        page.wait_for_timeout(1000)
        if not wait_for_card(page, 30):
            break
        while page.locator(".toast").count():
            page.wait_for_timeout(500)
        shot(page, f"more-{index:02d}")

    page.click("nav.tabs >> text=Taste")
    page.wait_for_timeout(1500)
    shot(page, "taste")

    page.click("nav.tabs >> text=Outfits")
    page.wait_for_timeout(1000)
    build = page.locator("main button", has_text="Build outfits")
    if build.count():
        build.first.click()
    started = time.time()
    page.wait_for_selector(".outfit", timeout=420_000)
    print(f"outfits after {time.time() - started:.0f}s", flush=True)
    page.wait_for_timeout(3000)
    dismiss_route_note(page)
    shot(page, "outfit")
    page.locator(".outfit").nth(0).locator("footer").scroll_into_view_if_needed()
    shot(page, "outfit-footer")
    if page.locator(".outfit").count() > 1:
        page.locator(".outfit").nth(1).scroll_into_view_if_needed()
        shot(page, "outfit-2")
    browser.close()
