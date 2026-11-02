#!/usr/bin/env python3
"""Open the local app at iPhone size, optionally click a tab, and save one screenshot."""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

from capture_app import SAFE_AREA_CSS

APP = os.getenv("APP_URL", "http://localhost:5173")
tab, out = sys.argv[1], sys.argv[2]
wait_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/local/bin/google-chrome")
    context = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=3,
                                  is_mobile=True, has_touch=True, locale="en-GB")
    context.add_init_script(
        f"localStorage.setItem('quenya.token', {os.environ['SESSION_TOKEN']!r});"
        "sessionStorage.setItem('q-nudged', '1');"
    )
    page = context.new_page()
    page.on("console", lambda m: print("console:", m.text[:300]))
    page.goto(APP)
    page.add_style_tag(content=SAFE_AREA_CSS)
    page.wait_for_selector("nav.tabs")
    if tab != "-":
        page.click(f"nav.tabs >> text={tab}")
    page.wait_for_timeout(wait_ms)
    page.screenshot(path=out)
    print(page.locator("main.body").inner_text()[:1500])
    browser.close()
