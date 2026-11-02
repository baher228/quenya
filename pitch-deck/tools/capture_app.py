#!/usr/bin/env python3
"""Drive the real Quenya web app (Vite + FastAPI, local SQLite) and capture iPhone-size screenshots.

The viewport is an iPhone 15 Pro (393x852pt @3x). The app's CSS pads the top and the tab bar
with env(safe-area-inset-*), which headless Chrome reports as 0, so the harness sets those two
rules to the iPhone 15 Pro insets (59pt top, 34pt bottom). Nothing else on the page is changed.

Usage: SESSION_TOKEN=... python capture_app.py OUT_DIR
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

APP = os.getenv("APP_URL", "http://localhost:5173")
TOKEN = os.environ["SESSION_TOKEN"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)

BRIEF = (
    "Quiet luxury for autumn in London. Muted tones, good knits, relaxed tailoring, "
    "suede shoes. No big logos."
)
SAFE_AREA_CSS = """
@media (max-width:520px){
  .phone{padding-top:59px!important}
  .phone .tabs{padding-bottom:34px!important}
}
"""
KEEP = re.compile(
    r"cashmere|merino|wool|knit|jumper|cardigan|crew|polo|suede|loafer|chelsea|overcoat|coat|"
    r"trouser|pleat|oxford|shirt|chore|navy|grey|gray|ecru|stone|brown|camel|cream|oat|olive|"
    r"charcoal|tobacco|taupe|sand",
    re.I,
)
PASS = re.compile(r"graphic|logo|print|neon|camo|tie[- ]?dye|shorts|swim|hoodie|track|cap\b", re.I)


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(900)
    page.evaluate(
        "() => Promise.race([Promise.all([...document.images].map(i => i.decode().catch(() => null))),"
        " new Promise(r => setTimeout(r, 6000))])"
    )
    page.wait_for_timeout(300)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    print("saved", path, flush=True)


def wait_for_card(page: Page, timeout_s: float = 240) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if page.locator('.card[data-depth="0"]').count():
            return True
        if page.locator(".deck-done").count():
            return False
        page.wait_for_timeout(500)
    return False


def top_card(page: Page) -> str:
    card = page.locator('.card[data-depth="0"]')
    return card.inner_text() if card.count() else ""


def swipe(page: Page, liked: bool) -> None:
    page.keyboard.press("ArrowRight" if liked else "ArrowLeft")
    page.wait_for_timeout(900)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/local/bin/google-chrome")
        context = browser.new_context(
            viewport={"width": 393, "height": 852},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            locale="en-GB",
        )
        context.add_init_script(
            f"localStorage.setItem('quenya.token', {TOKEN!r}); sessionStorage.setItem('q-nudged', '1');"
        )
        page = context.new_page()
        page.set_default_timeout(240_000)
        page.goto(APP)
        page.add_style_tag(content=SAFE_AREA_CSS)
        page.wait_for_selector("textarea")

        page.fill("textarea", BRIEF)
        page.evaluate(
            """() => {
              const set = (el, v) => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
              };
              const ranges = document.querySelectorAll('input[type=range]');
              set(ranges[0], '55');
              const budget = ranges[1];
              set(budget, "48");
            }"""
        )
        shot(page, "brief")
        page.click("button.btn.primary.wide")

        if not wait_for_card(page):
            shot(page, "deck-empty")
            raise SystemExit("deck did not load")
        page.add_style_tag(content=SAFE_AREA_CSS)
        shot(page, "swipe-00")

        swipes = 0
        for _ in range(26):
            if not wait_for_card(page, 120):
                break
            text = top_card(page)
            liked = bool(KEEP.search(text)) and not PASS.search(text)
            swipe(page, liked)
            swipes += 1
            if swipes == 10:
                page.wait_for_selector(".sig", timeout=240_000)
            if swipes >= 11:
                if wait_for_card(page, 60):
                    shot(page, f"swipe-{swipes:02d}")
        page.click("nav.tabs >> text=Taste")
        page.wait_for_timeout(1500)
        shot(page, "taste")

        page.click("nav.tabs >> text=Outfits")
        page.wait_for_timeout(800)
        build = page.locator("button", has_text="Build outfits")
        if build.count():
            build.first.click()
        page.wait_for_selector(".outfit .piece img", timeout=400_000)
        page.wait_for_timeout(2500)
        shot(page, "outfit")
        page.locator(".outfit").nth(1).scroll_into_view_if_needed()
        shot(page, "outfit-2")
        browser.close()


if __name__ == "__main__":
    main()
