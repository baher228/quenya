"""Build the deck: HTML -> PNG per slide + PDF (Chrome), and editable PPTX (python-pptx).

Usage: python build/build.py [--skip-pptx]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import H, W, render_html, render_pptx  # noqa: E402
from slides import build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
CHROME = "/usr/local/bin/google-chrome"

CHECKS = """
() => {
  const issues = [];
  const box = (el) => el.getBoundingClientRect();
  document.querySelectorAll('section.slide').forEach((slide, si) => {
    const s = box(slide);
    const texts = [...slide.querySelectorAll('.txt')];
    texts.forEach((t) => {
      if (t.scrollHeight > t.clientHeight + 1 || t.scrollWidth > t.clientWidth + 1) {
        issues.push({slide: si + 1, kind: 'overflow', name: t.dataset.name,
                     text: t.innerText.slice(0, 60), sh: t.scrollHeight, ch: t.clientHeight,
                     sw: t.scrollWidth, cw: t.clientWidth});
      }
    });
    slide.querySelectorAll('.el').forEach((el) => {
      const r = box(el);
      if (r.left < s.left - 1 || r.top < s.top - 1 || r.right > s.right + 1 || r.bottom > s.bottom + 1) {
        if (!(el.tagName === 'IMG' && (el.alt || '').length === 0 && el.style.objectFit === 'contain')) {
          issues.push({slide: si + 1, kind: 'off-canvas', tag: el.tagName, name: el.dataset.name || '',
                       r: [r.left - s.left, r.top - s.top, r.right - s.left, r.bottom - s.top].map(Math.round)});
        }
      }
    });
    // Rendered text extents (line boxes), compared pairwise.
    const ink = texts.map((t) => {
      const rects = [];
      const walker = document.createTreeWalker(t, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        rects.push(...[...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0));
      }
      return {t, rects};
    });
    for (let i = 0; i < ink.length; i++) {
      for (let j = i + 1; j < ink.length; j++) {
        let hit = false;
        for (const a of ink[i].rects) {
          for (const b of ink[j].rects) {
            const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
            const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
            if (ox > 2 && oy > 2) hit = true;
          }
        }
        if (hit) issues.push({slide: si + 1, kind: 'text-overlap',
                              a: ink[i].t.innerText.slice(0, 40), b: ink[j].t.innerText.slice(0, 40)});
      }
    }
    // Text over raster images.
    slide.querySelectorAll('img').forEach((img) => {
      const r = box(img);
      ink.forEach(({t, rects}) => {
        for (const a of rects) {
          const ox = Math.min(a.right, r.right) - Math.max(a.left, r.left);
          const oy = Math.min(a.bottom, r.bottom) - Math.max(a.top, r.top);
          if (ox > 2 && oy > 2) {
            issues.push({slide: si + 1, kind: 'text-on-image', text: t.innerText.slice(0, 40),
                         img: img.style.left + ',' + img.style.top});
            break;
          }
        }
      });
    });
  });
  return issues;
}
"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    slides = build()
    html_path = render_html(slides, OUT / "deck.html")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--font-render-hinting=none"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(html_path.as_uri())
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)
        missing = page.evaluate(
            "() => [...document.fonts].filter(f => f.status !== 'loaded').map(f => f.family)"
        )
        if missing:
            print("fonts not loaded:", missing)
        issues = page.evaluate(CHECKS)
        (OUT / "qa.json").write_text(json.dumps(issues, indent=2))
        for issue in issues:
            print("QA", issue)
        for i in range(1, len(slides) + 1):
            page.locator(f"#s{i}").screenshot(path=str(OUT / f"slide-{i:02d}.png"))
        page.pdf(path=str(OUT / "quenya-pitch-deck.pdf"), width=f"{W}px", height=f"{H}px",
                 print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        browser.close()
    if "--skip-pptx" not in sys.argv:
        render_pptx(slides, OUT / "quenya-pitch-deck.pptx")
    print(f"built {len(slides)} slides, {len(issues)} QA issues")


if __name__ == "__main__":
    main()
