"""Place a real 393x852pt app screenshot inside a clean iPhone frame (transparent PNG)."""

from __future__ import annotations

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

SCREEN_W, SCREEN_H = 393, 852
FRAME = 4
BEZEL = 11
INSET = FRAME + BEZEL
DEVICE_W, DEVICE_H = SCREEN_W + 2 * INSET, SCREEN_H + 2 * INSET
PAD = 60

STATUS_ICONS = """
<svg width="78" height="14" viewBox="0 0 78 14" fill="#fff" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="9" width="3.2" height="4.5" rx="1"/><rect x="4.8" y="6.5" width="3.2" height="7" rx="1"/>
  <rect x="9.6" y="3.8" width="3.2" height="9.7" rx="1"/><rect x="14.4" y="1" width="3.2" height="12.5" rx="1"/>
  <path d="M31 3.2c2.6 0 5 1 6.8 2.7l1.3-1.3A11.3 11.3 0 0 0 31 1.3c-3.1 0-6 1.2-8.1 3.3l1.3 1.3A9.5 9.5 0 0 1 31 3.2Z"/>
  <path d="M31 6.9c1.6 0 3 .6 4.2 1.6l1.3-1.3A7.8 7.8 0 0 0 31 5c-2 0-3.9.8-5.5 2.2l1.3 1.3c1.2-1 2.6-1.6 4.2-1.6Z"/>
  <path d="M31 10.5c.6 0 1.1.2 1.5.6L31 13.4l-1.5-2.3c.4-.4.9-.6 1.5-.6Z"/>
  <rect x="47.5" y="1.2" width="25" height="12" rx="3.6" fill="none" stroke="#fff" stroke-opacity=".38" stroke-width="1.1"/>
  <rect x="49.4" y="3.1" width="21.2" height="8.2" rx="2.2"/>
  <path d="M74.2 5v4.4c.9-.3 1.5-1.2 1.5-2.2s-.6-1.9-1.5-2.2Z" fill-opacity=".45"/>
</svg>
"""


def _html(screenshot: Path) -> str:
    data = base64.b64encode(screenshot.read_bytes()).decode()
    return f"""<!doctype html><html><head><style>
    html,body{{margin:0;background:transparent}}
    .wrap{{padding:{PAD}px;width:{DEVICE_W}px;height:{DEVICE_H}px}}
    .device{{position:relative;width:{DEVICE_W}px;height:{DEVICE_H}px;border-radius:64px;
      background:linear-gradient(145deg,#d9d6d1 0%,#a9a59f 18%,#efece7 46%,#9d9993 74%,#cfccc6 100%);
      box-shadow:0 50px 70px -30px rgba(23,20,17,.35),0 18px 30px -18px rgba(23,20,17,.25)}}
    .device::before{{content:"";position:absolute;inset:{FRAME - 1}px;border-radius:61px;background:#0b0a09}}
    .screen{{position:absolute;left:{INSET}px;top:{INSET}px;width:{SCREEN_W}px;height:{SCREEN_H}px;
      border-radius:51px;overflow:hidden;background:#171411}}
    .screen img{{display:block;width:100%;height:100%}}
    .island{{position:absolute;left:50%;top:11px;width:124px;height:36px;margin-left:-62px;border-radius:20px;background:#000}}
    .time{{position:absolute;left:52px;top:18px;font:600 16.5px/20px Inter,system-ui;color:#fff;letter-spacing:-.01em}}
    .icons{{position:absolute;right:30px;top:21px}}
    .home{{position:absolute;left:50%;bottom:8px;width:136px;height:5px;margin-left:-68px;border-radius:3px;background:#fff;opacity:.92}}
    .btn{{position:absolute;width:4px;border-radius:2px;background:linear-gradient(90deg,#8f8b85,#d6d3ce)}}
    </style></head><body><div class="wrap"><div class="device">
      <div class="btn" style="left:-3px;top:150px;height:32px"></div>
      <div class="btn" style="left:-3px;top:210px;height:60px"></div>
      <div class="btn" style="left:-3px;top:284px;height:60px"></div>
      <div class="btn" style="right:-3px;top:230px;height:96px;background:linear-gradient(270deg,#8f8b85,#d6d3ce)"></div>
      <div class="screen"><img src="data:image/png;base64,{data}">
        <div class="time">9:41</div><div class="icons">{STATUS_ICONS}</div>
        <div class="island"></div><div class="home"></div></div>
    </div></div></body></html>"""


def frame(screenshot: Path, out: Path, scale: int = 3) -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/local/bin/google-chrome")
        page = browser.new_page(
            viewport={"width": DEVICE_W + 2 * PAD, "height": DEVICE_H + 2 * PAD},
            device_scale_factor=scale,
        )
        page.set_content(_html(screenshot))
        page.wait_for_timeout(200)
        page.screenshot(path=str(out), omit_background=True)
        browser.close()
    return out


def frame_box(width: float) -> tuple[float, float, float]:
    """For a framed PNG drawn `width` px wide: (height, screen-left offset, padding) in px."""
    k = width / (DEVICE_W + 2 * PAD)
    return (DEVICE_H + 2 * PAD) * k, (PAD + INSET) * k, PAD * k
