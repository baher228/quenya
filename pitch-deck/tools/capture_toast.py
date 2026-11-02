#!/usr/bin/env python3
"""Swipe until the next taste update and capture the deck while the "Taste vN updated" pill shows.

Usage: SESSION_TOKEN=... python capture_toast.py OUT_DIR
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_app import KEEP, PASS, SAFE_AREA_CSS, top_card, wait_for_card

APP = os.getenv("APP_URL", "http://localhost:5173")
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/local/bin/google-chrome")
    context = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=3,
                                  is_mobile=True, has_touch=True, locale="en-GB")
    context.add_init_script(
        f"localStorage.setItem('quenya.token', {os.environ['SESSION_TOKEN']!r});"
        "sessionStorage.setItem('q-nudged', '1');"
    )
    page = context.new_page()
    page.set_default_timeout(300_000)
    page.goto(APP)
    page.add_style_tag(content=SAFE_AREA_CSS)
    page.wait_for_selector("nav.tabs")
    page.click("nav.tabs >> text=Swipe")
    page.wait_for_timeout(1200)
    if page.locator(".pick-row.on").count():
        page.locator(".pick-row.on").first.click()
    page.wait_for_timeout(1500)
    if page.locator(".deck-done").count():
        page.locator(".deck-done button", has_text="10 more cards").click()
    wait_for_card(page, 120)
    note = page.locator(".route button.x")
    if note.count():
        note.first.click()

    def catch_toast(label: str) -> bool:
        for _ in range(3000):
            if page.locator(".toast.pill").count():
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / f"toast-{label}.png"))
                print("toast shot", label, flush=True)
                return True
            page.wait_for_timeout(100)
        return False

    caught = 0
    for index in range(16):
        if not wait_for_card(page, 60) and page.locator(".deck-done").count():
            page.locator(".deck-done button", has_text="10 more cards").click()
            wait_for_card(page, 60)
        text = top_card(page)
        liked = bool(KEEP.search(text)) and not PASS.search(text)
        page.keyboard.press("ArrowRight" if liked else "ArrowLeft")
        page.wait_for_timeout(1200)
        if page.locator("header.top", has_text="Updating taste").count():
            print("updating after swipe", index, flush=True)
            if catch_toast(f"{index:02d}"):
                caught += 1
        elif caught:
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / f"after-{index:02d}.png"))
            print("after shot", index, flush=True)
            if caught and index > 12:
                break
    browser.close()
