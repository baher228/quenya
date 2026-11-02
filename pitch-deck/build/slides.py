"""Quenya investor deck: slide content and layout (1920x1080 grid)."""

from __future__ import annotations

from pathlib import Path

from engine import MONO, SANS, SERIF, Img, Line, Rect, Run, Slide, Text, text_width
from phone import frame_box

ROOT = Path(__file__).resolve().parents[1]
PIECES = ROOT / "assets" / "pieces"
PHONES = ROOT / "assets" / "phones"

# Palette: warm off-white page; ink is the app's phone colour; accents from frontend/src/styles.css.
BG = "#FAF8F5"
INK = "#171411"
BODY = "#4A433B"
MUTE = "#7D7468"
HAIR = "#E4DED4"
SAGE = "#B9C4A8"
SAGE_D = "#66734F"
SAGE_T = "#EDF0E6"
CORAL = "#D9897A"
CORAL_D = "#B1584A"
CORAL_T = "#FAEDE9"

# Grid: 128px margins, 12 columns, 32px gutters.
ML, MR = 128, 1792
CW = MR - ML
GUT = 32
COL = (CW - 11 * GUT) / 12
TOTAL = 14


def cx(col: int) -> float:
    return ML + col * (COL + GUT)


def span(n: int) -> float:
    return n * COL + (n - 1) * GUT


# ---------------------------------------------------------------- primitives


def T(x, y, w, h, paras, **kw) -> Text:
    return Text(x, y, w, h, paras if isinstance(paras, list) else [paras], **kw)


def wordmark(x, y, size=26, align="left", w=200):
    return T(x, y, w, size * 1.3, [[Run("quenya", color=INK), Run(".", color=CORAL)]],
             size=size, weight=600, ls=-0.02, align=align, lh=1.2, name="wordmark")


def eyebrow(x, y, text, color=MUTE, w=None, size=15):
    w = w or min(600, MR - x)
    return T(x, y, w, 24, text, font=MONO, weight=500, size=size, ls=0.16, upper=True,
             color=color, lh=1.4)


def headline(text, y=168, w=1240, size=80, x=ML, h=None):
    return T(x, y, w, h or size * 1.06 * 2 + 8, text, font=SERIF, size=size, lh=1.06,
             ls=-0.012, color=INK, name="headline")


def sub(text, y, w=1000, size=28, x=ML, h=None, color=BODY):
    return T(x, y, w, h or size * 1.45 * 2 + 6, text, size=size, lh=1.45, color=color, name="sub")


def body(x, y, w, h, text, size=22, color=BODY, **kw):
    return T(x, y, w, h, text, size=size, lh=kw.pop("lh", 1.45), color=color, **kw)


def label(x, y, w, text, color=INK, size=30, weight=600, h=None):
    return T(x, y, w, h or size * 1.25 + 4, text, size=size, weight=weight, color=color, lh=1.2,
             ls=-0.01)


def sup(n) -> Run:
    return Run(str(n), sup=True, color=MUTE)


def chrome(slide: Slide, num: int, section: str, sources: list[str] | None = None,
           note_h: float = 60) -> Slide:
    slide.add(
        T(ML, 92, 900, 24, [[Run(f"{num:02d}", color=CORAL_D), Run("   " + section, color=MUTE)]],
          font=MONO, weight=500, size=15, ls=0.16, upper=True, lh=1.4, name="eyebrow"),
        wordmark(MR - 200, 84, size=26, align="right"),
        T(MR - 120, 1010, 120, 24, f"{num:02d} / {TOTAL}", font=MONO, size=14, color=MUTE,
          align="right", ls=0.08, lh=1.4, name="page"),
    )
    if sources:
        slide.add(T(ML, 1034 - note_h, 1450, note_h, ["   ".join(sources)], size=14, color=MUTE,
                    lh=1.45, valign="bottom", name="sources"))
    return slide


def placeholder(x, y, text, size=20, h=None, w=None, center=True) -> tuple[list, float]:
    """Founder-fill placeholder: coral dashed pill with mono [brackets] text."""
    h = h or size * 2.1
    tw = text_width(text, size, MONO, 500, 0.02)
    w = w or tw + size * 1.6
    els = [
        Rect(x, y, w, h, fill=CORAL_T, stroke=CORAL, sw=2, radius=min(h / 2, 14), dash=True,
             name="placeholder"),
        T(x, y, w, h, text, font=MONO, weight=500, size=size, color=CORAL_D, ls=0.02,
          align="center" if center else "left", valign="middle", lh=1.3, name="placeholder-text"),
    ]
    return els, w


def tag(x, y, text, color=SAGE_D, fill=SAGE_T, size=13) -> tuple[list, float]:
    """Small label for modelled or assumed numbers (not a placeholder)."""
    h = size * 2
    w = text_width(text.upper(), size, MONO, 500, 0.14) + size * 1.6
    return [
        Rect(x, y, w, h, fill=fill, radius=h / 2),
        T(x, y, w, h, text, font=MONO, weight=500, size=size, color=color, upper=True, ls=0.14,
          align="center", valign="middle", lh=1.2),
    ], w


def chip(x, y, text, positive=True, size=18) -> tuple[list, float]:
    h = size * 1.9
    w = text_width(text, size, SANS, 500) + size * 1.3
    color, fill = (SAGE_D, SAGE_T) if positive else (CORAL_D, CORAL_T)
    return [
        Rect(x, y, w, h, fill=fill, radius=h / 2),
        T(x, y, w, h, text, size=size, weight=500, color=color, align="center", valign="middle",
          lh=1.2),
    ], w


def phone(name: str, x: float, y: float, w: float) -> tuple[Img, float, float]:
    """Framed phone PNG drawn w px wide. Returns (image, visible device height, side padding)."""
    h, _, pad = frame_box(w)
    return Img(x - pad, y - pad, w, h, PHONES / f"{name}.png", fit="contain", name=f"phone-{name}"), \
        h - 2 * pad, pad


def piece(name: str, x, y, w, h, radius=18, pos=(0.5, 0.5)) -> Img:
    return Img(x, y, w, h, PIECES / f"{name}.jpg", radius=radius, pos=pos, name=f"piece-{name}")


def caption(x, y, w, brand, detail, source_new=True):
    return T(x, y, w, 46, [
        [Run(brand, weight=600, color=INK)],
        [Run(detail, color=MUTE)],
    ], size=15, lh=1.45)


# ---------------------------------------------------------------- slides


def s01_title() -> Slide:
    s = Slide()
    s.add(
        wordmark(ML, 96, size=34, w=260),
        T(ML, 300, 980, 400, [
            "Describe a style.",
            [Run("Swipe "), Run("real", italic=True), Run(" UK fashion.")],
        ], font=SERIF, size=118, lh=1.0, ls=-0.015, name="title"),
        sub("Quenya learns your taste from keep-or-pass swipes on real pieces from 130 UK shops "
            "and live Vinted listings, then builds the outfit.", y=580, w=820, size=28, h=130),
        T(ML, 984, 900, 50, [[Run("Investor presentation", color=INK), Run("   ·   November 2026   ·   quenya.co.uk", color=MUTE)]],
          font=MONO, weight=500, size=15, ls=0.12, upper=True, lh=1.4, valign="bottom"),
    )
    ax, bx = 1150, 1478
    s.add(
        piece("x-drakes-03fead", ax, 236, 300, 430, pos=(0.5, 0.3)),
        caption(ax, 682, 300, "Drake's", "Artist chore jacket · £695 · new"),
        piece("x-oliverspencer-78dee1", bx, 120, 314, 360),
        caption(bx, 496, 314, "Oliver Spencer", "Blenheim crew neck · £249 · new"),
        piece("x-vinted-689003", bx, 590, 314, 300, pos=(0.5, 0.55)),
        caption(bx, 906, 314, "Paraboot on Vinted", "Coraux loafers · £110 · second-hand"),
    )
    s.notes = ("Quenya: describe a style, swipe real UK fashion. All pieces shown are real listings from "
               "the local catalogue used for the app screenshots (Drake's, Oliver Spencer via their "
               "Shopify feeds; Paraboot via live Vinted UK search).")
    return s


def s02_problem() -> Slide:
    s = chrome(Slide(), 2, "Problem → solution", sources=[
        "1  Ipsos for Google, n=2,008 US adults, Feb 2022.",
        "Screen: the Quenya app running on real listings (web build, iPhone 15 Pro size).",
    ], note_h=24)
    s.add(
        headline("You can picture the look. Search can’t find it.", y=168, w=900, size=80,
                 h=260),
        sub([[Run("60% of US adults have tried to find an item they could picture but had no words for."),
              sup(1), Run(" And new and second-hand stock live in separate apps, so the right piece "
                          "rarely shows up.")]], y=372, w=840, size=26, h=190),
        Line(ML, 620, ML + 72, 620, color=SAGE, w=4),
        eyebrow(ML, 652, "Quenya", color=SAGE_D),
        T(ML, 688, 860, 150, "Describe it in your own words. Swipe real pieces. Quenya learns your "
          "taste and builds the outfit.", size=34, weight=500, lh=1.32, ls=-0.01, color=INK,
          name="solution"),
    )
    img, vis_h, pad = phone("swipe-taste-updated", 1182, 118, 520)
    s.add(img)
    s.notes = "The phone shows a genuine capture of the swipe deck after taste update v4."
    return s


def s03_why_matters() -> Slide:
    s = chrome(Slide(), 3, "Why this matters", sources=[
        "1  Nosto, n=2,000 North American and UK consumers, Feb 2023.",
        "2  BoF–McKinsey, The State of Fashion 2025.",
        "3  Constructor shopper survey, n≈900 UK and US, 2024.",
        "4  Ipsos for Google, n=2,008 US adults, Feb 2022.",
        "Figures are US or mixed UK/US; we found no UK-only survey.",
    ], note_h=44)
    s.add(headline("Search is where fashion shopping breaks.", w=1300, h=100))
    s.add(sub("Shoppers know what they want. Keyword search and endless grids make them work for it.",
              y=286, w=1500, size=28, h=50))
    stats = [
        ("80%", "have left a site because its search fell short.", 1),
        ("74%", "have walked away from an online purchase because there was too much choice.", 2),
        ("42%", "say results are technically correct but don’t match their preferences.", 3),
        ("50%", "have failed to find a piece they tried their best to describe.", 4),
    ]
    w = (CW - 3 * 64) / 4
    for i, (num, text, n) in enumerate(stats):
        x = ML + i * (w + 64)
        s.add(
            Line(x, 470, x + w, 470, color=HAIR, w=2),
            Line(x, 470, x + 56, 470, color=SAGE, w=4),
            T(x, 500, w, 170, num, font=SERIF, size=150, lh=1.0, ls=-0.02, color=INK),
            body(x, 680, w - 10, 140, [[Run(text), sup(n)]], size=24, lh=1.42),
        )
    s.notes = ("Sources: Nosto 15 Feb 2023 nosto.com/blog/new-search-research; BoF–McKinsey State of "
               "Fashion 2025; Constructor 2024 via fashionnetwork.com; Ipsos for Google Feb 2022.")
    return s


def s04_how() -> Slide:
    s = chrome(Slide(), 4, "How it works", sources=[
        "Brief, taste chips and outfit pieces are real outputs from the app (taste v4, outfit “Donegal Soft Minimal”).",
    ], note_h=24)
    s.add(headline("From one sentence to a full outfit.", w=1300, h=100))
    w = (CW - 2 * 72) / 3
    xs = [ML + i * (w + 72) for i in range(3)]
    top, vis_h = 330, 330
    # 1: the brief
    s.add(
        Rect(xs[0], top, w, vis_h, fill="#F3EFE8", radius=28),
        eyebrow(xs[0] + 40, top + 40, "The brief", color=MUTE),
        T(xs[0] + 40, top + 80, w - 80, 220,
          "“Quiet luxury for autumn in London. Muted tones, good knits, relaxed tailoring, "
          "suede shoes. No big logos.”", font=SERIF, italic=True, size=36, lh=1.18, color=INK),
    )
    # 2: swipe and learn
    thumbs = ["x-drakes-93bec5", "x-universalworks-c8ba27", "x-drakes-63714b"]
    tw = (w - 2 * 16) / 3
    for i, name in enumerate(thumbs):
        tx = xs[1] + i * (tw + 16)
        s.add(piece(name, tx, top, tw, 196, radius=18))
        keep = i != 2
        s.add(Rect(tx + tw / 2 - 22, top + 172, 44, 44, fill=SAGE if keep else CORAL, ellipse=True),
              T(tx + tw / 2 - 22, top + 172, 44, 44, "✓" if keep else "✕", size=22, weight=700,
                color=INK if keep else "#FFFFFF", align="center", valign="middle", lh=1.0))
    cy, x = top + 250, xs[1]
    for text, pos in [("+ Quiet luxury", True), ("+ Cashmere", True), ("− Large logos", False)]:
        els, cw_ = chip(x, cy, text, pos, size=18)
        s.add(els)
        x += cw_ + 10
    # 3: the outfit
    ow = (w - 16) / 2
    s.add(piece("o2-knitwear", xs[2], top, ow, vis_h, radius=18, pos=(0.5, 0.4)),
          piece("o2-top", xs[2] + ow + 16, top, ow, (vis_h - 16) / 2, radius=18),
          piece("o2-shoes", xs[2] + ow + 16, top + (vis_h + 16) / 2, ow, (vis_h - 16) / 2, radius=18))
    steps = [
        ("1", "Describe a style", "In your own words, with budget, sizes and how niche. "
         "No wardrobe upload, no setup."),
        ("2", "Swipe and it learns", "Keep or pass on real pieces. Each round of swipes updates the "
         "style’s taste profile: likes, avoids, palette, rules."),
        ("3", "Get the outfit", "An AI stylist builds complete looks from new and second-hand. "
         "Lock, swap or reject; every piece links to its shop."),
    ]
    for x, (n, title, text) in zip(xs, steps):
        s.add(
            T(x, 708, 60, 40, n, font=MONO, weight=500, size=18, color=CORAL_D, lh=1.4),
            label(x + 40, 702, w - 40, title, size=30),
            body(x, 760, w - 8, 140, text, size=22),
        )
    s.notes = ("The outfit shown mixes a second-hand Lochmere cashmere jumper (£25, Vinted), an Oliver "
               "Spencer tee (£85, new), and G.H. Bass loafers (£18.90, Vinted).")
    return s


def s05_product() -> Slide:
    s = chrome(Slide(), 5, "Product", sources=[
        "Real screens from the Quenya app on a local catalogue of live listings (web build at iPhone 15 Pro size).",
    ], note_h=24)
    s.add(
        headline(["The real app.", "Live on TestFlight."], y=168, w=470, size=72, h=170),
        body(ML, 372, 440, 120, "iPhone app on TestFlight, plus a web app. Each style keeps its own "
             "brief, deck and taste.", size=22),
    )
    items = [
        ("Swipe", "A ranked deck of real listings, new and second-hand side by side."),
        ("Outfits", "Complete looks within budget. Lock, swap, or reject with a reason."),
        ("Taste", "What it learned, in plain words, with the evidence behind it."),
    ]
    y = 560
    for i, (title, text) in enumerate(items):
        s.add(T(ML, y, 40, 30, f"{i + 1}", font=MONO, weight=500, size=16, color=CORAL_D, lh=1.6),
              label(ML + 36, y - 2, 400, title, size=24),
              body(ML + 36, y + 34, 400, 70, text, size=19, lh=1.4))
        y += 128
    pw = 392
    names = ["swipe-vinted", "outfits", "taste"]
    for i, name in enumerate(names):
        img, _, _ = phone(name, 660 + i * 388, 150, pw)
        s.add(img)
    return s


def s06_why_now() -> Slide:
    s = chrome(Slide(), 6, "Why now", sources=[
        "1  a16z, “LLMflation”, Nov 2024; Epoch AI measures 9–900× a year by task.",
        "2  GlobalData via The Guardian, 21 Jul 2024.",
        "3  Just Style, 17 Oct 2025.",
        "4  Vinted, 29 Apr 2025.",
        "5  Constructor, 2024.",
    ], note_h=24)
    s.add(headline("Three shifts make this possible now.", w=1300, h=100))
    w = (CW - 2 * 80) / 3
    cols = [
        ("~10×", "cheaper per year", [Run("LLM inference at a fixed capability level falls about 10× a year."),
                                       sup(1), Run(" Our product tagging runs on our own CLIP model, with no per-call cost.")]),
        ("£10.1bn", "UK clothes resale by 2027", [Run("Up 48.9% on 2023. Second-hand is now a mainstream "
                                                     "way to shop."), sup(2)]),
        ("60.2%", "of UK 16–24s bought pre-loved", [Run("…clothing in the past year. Young shoppers start "
                                                       "with second-hand, then fill gaps with new."), sup(3)]),
    ]
    for i, (num, kicker, text) in enumerate(cols):
        x = ML + i * (w + 80)
        s.add(
            Line(x, 360, x + w, 360, color=HAIR, w=2),
            Line(x, 360, x + 56, 360, color=SAGE, w=4),
            T(x, 392, w, 170, num, font=SERIF, size=150, lh=1.0, ls=-0.02),
            label(x, 572, w, kicker, size=28),
            body(x, 622, w - 10, 160, [text], size=22),
        )
    s.add(
        Line(ML, 860, MR, 860, color=HAIR, w=2),
        body(ML, 884, CW, 40, [[Run("Vinted 2024: ", weight=600, color=INK),
                                Run("revenue €813.4m (+36%), net profit €76.7m."), sup(4),
                                Run("     52% of shoppers would use generative AI to find products, up from 42%."),
                                sup(5)]], size=21),
    )
    return s


def s07_market() -> Slide:
    s = chrome(Slide(), 7, "Market", sources=[
        "1  eMarketer, State of UK fashion e-commerce 2025.",
        "2  GlobalData via The Guardian, Jul 2024 (includes offline; Vinted pays no affiliate commission).",
        "Take rate, SAM share and MAU are modelling assumptions, not facts.",
    ], note_h=24)
    s.add(headline("A £21bn UK market. We start with style-led shoppers.", w=1500, h=100, size=72))
    # Nested circles, bottoms aligned.
    base, cxm = 960, 448
    for d, fill, stroke in [(600, None, "#CFC7BB"), (384, SAGE_T, None), (150, SAGE, None)]:
        s.add(Rect(cxm - d / 2, base - d, d, d, fill=fill, stroke=stroke, sw=2, ellipse=True))
    s.add(
        T(cxm - 100, base - 600 + 52, 200, 30, "TAM", font=MONO, weight=500, size=16, ls=0.16,
          color=MUTE, align="center"),
        T(cxm - 100, base - 384 + 46, 200, 30, "SAM", font=MONO, weight=500, size=16, ls=0.16,
          color=SAGE_D, align="center"),
        T(cxm - 100, base - 150 + 58, 200, 30, "SOM", font=MONO, weight=500, size=16, ls=0.16,
          color=INK, align="center"),
    )
    x, w = 880, MR - 880
    rows = [
        ("TAM", "£21.0bn", "GMV", [Run("UK online clothing and footwear sales, 2025."), sup(1),
                                   Run(" At a 7% net take rate: ≈ £1.47bn revenue.")], "Assumption: 7% take rate"),
        ("SAM", "£7.4bn", "GMV", [Run("The share spent by 16–34s on style-led, non-value fashion. "
                                      "≈ £0.51bn revenue.")], "Assumption: 35% share"),
        ("SOM", "£1.36m", "a year", [Run("Year 3, bottom-up: 100k MAU × £1.13 per MAU per month × 12.")],
         "Model"),
    ]
    y = 290
    for key, num, unit, text, note in rows:
        s.add(
            Line(x, y, MR, y, color=HAIR, w=2),
            T(x, y + 26, 120, 30, key, font=MONO, weight=500, size=16, ls=0.16, color=MUTE),
            T(x + 120, y + 8, 420, 90, [[Run(num), Run("  " + unit, font=SANS, size=24, color=MUTE)]],
              font=SERIF, size=76, lh=1.05),
            body(x + 540, y + 22, w - 540, 110, [text], size=21, lh=1.42),
        )
        els, _ = tag(x + 540, y + 140, note)
        s.add(els)
        y += 210
    s.add(body(x, y + 6, w, 60, [[Run("Adjacent, not added: ", weight=600, color=INK),
                                  Run("UK clothes resale, £10.1bn by 2027."), sup(2)]], size=21))
    return s


def s08_model() -> Slide:
    s = chrome(Slide(), 8, "Business model", sources=[
        "1  Awin merchant profiles; AffiliateGraph (Finisterre). New Look: 4% new / 1% existing customers.",
        "2  ASA, CAP Code guidance on affiliate marketing.",
        "3  Fortune, 22 Oct 2026.",
    ], note_h=24)
    s.add(
        headline("Affiliate-first. More layers as we grow.", w=1400, h=100),
        sub("Every piece links to the shop that sells it. Shoppers pay the shop’s price; we earn a commission.",
            y=280, w=1300, size=26, h=44),
    )
    w = (CW - 2 * 72) / 3
    xs = [ML + i * (w + 72) for i in range(3)]
    s.add(Line(ML, 392, MR, 392, color=HAIR, w=2))
    phases = [
        ("Now", "Affiliate commission", [Run("Link-outs through Awin, Impact and AvantLink, with "
                                              "Skimlinks for the long tail.")]),
        ("At 25k+ MAU", "Sponsored placements", [Run("Brand cards in the deck, always labelled #Ad under the "
                                                      "CAP Code."), sup(2)]),
        ("At 50–100k MAU", "Premium and B2B", [Run("Premium at about £4.99 a month: more niches, price-drop "
                                                    "alerts, outfit history. Licensing the taste engine to "
                                                    "retailers: Alta lifted Rent the Runway add-to-bag by 12%."),
                                                sup(3)]),
    ]
    for i, (when, title, text) in enumerate(phases):
        x = xs[i]
        s.add(Rect(x, 383, 18, 18, fill=SAGE if i == 0 else BG, stroke=None if i == 0 else SAGE_D,
                   sw=2, ellipse=True),
              eyebrow(x, 424, when, color=SAGE_D if i == 0 else MUTE),
              label(x, 460, w, title, size=32),
              body(x, 512, w - 16, 140, [text], size=21, lh=1.42))
    rates = [("Finisterre", 12), ("fashionette", 8), ("Dorothy Perkins", 6), ("New Look", 4), ("Debenhams", 3)]
    y = 628
    s.add(T(xs[0], y, w, 24, [[Run("Example UK commission rates"), sup(1)]], size=15, color=MUTE,
            weight=500, lh=1.4))
    y += 36
    for name, rate in rates:
        s.add(T(xs[0], y, 170, 26, name, size=17, color=INK, lh=1.4),
              Rect(xs[0] + 180, y + 8, rate / 12 * 250, 12, fill=SAGE, radius=6),
              T(xs[0] + 180 + rate / 12 * 250 + 12, y, 60, 26, f"{rate}%", size=17, weight=600,
                color=INK, lh=1.4))
        y += 32
    # Model strip
    y0 = 852
    s.add(Line(ML, y0, MR, y0, color=HAIR, w=2),
          T(ML, y0 + 30, 440, 30, "Modelled revenue per MAU per month", size=21, weight=600, color=INK,
            lh=1.35))
    els, _ = tag(ML, y0 + 76, "Model, not actuals", color=CORAL_D, fill=CORAL_T)
    s.add(els)
    for i, (num, name) in enumerate([("£0.24", "conservative"), ("£1.13", "base"), ("£4.20", "optimistic")]):
        x = 640 + i * 240
        s.add(T(x, y0 + 18, 240, 70, num, font=SERIF, size=64, lh=1.05),
              T(x, y0 + 90, 240, 26, name, size=17, color=MUTE, lh=1.4))
    s.add(body(1400, y0 + 30, MR - 1400, 90, "Base case: £100k a month needs about 89k MAU.",
               size=21, color=INK))
    return s


def s09_traction() -> Slide:
    s = chrome(Slide(), 9, "Traction")
    s.add(headline("Built and live. Opening the waitlist next.", w=1400, h=100))
    w = (CW - 3 * 56) / 4
    xs = [ML + i * (w + 56) for i in range(4)]
    s.add(Line(ML, 340, MR, 340, color=HAIR, w=2))
    s.add(
        T(xs[0], 370, w, 170, "130", font=SERIF, size=150, lh=1.0, ls=-0.02),
        body(xs[0], 556, w, 90, "UK shops live in the catalogue, 117 of them on Shopify.", size=22),
        T(xs[1], 370, w, 170, "Live", font=SERIF, size=150, lh=1.0, ls=-0.02),
        body(xs[1], 556, w, 90, "iPhone app on TestFlight, plus a web app.", size=22),
    )
    els, _ = placeholder(xs[2], 412, "[Waitlist size]", size=26, h=84, w=w - 20)
    s.add(els, body(xs[2], 556, w, 90, "people on the waitlist, as of [date].", size=22))
    els, _ = placeholder(xs[3], 412, "[Items indexed]", size=26, h=84, w=w - 20)
    s.add(els, body(xs[3], 556, w, 90, "new + Vinted items in the live catalogue.", size=22))
    s.add(Line(ML, 690, MR, 690, color=HAIR, w=2),
          eyebrow(ML, 716, "Also built"),
          body(ML, 752, 760, 160, [
              "Live Vinted UK feed alongside retail, in one deck",
              "Per-style taste profiles and an AI stylist with lock, swap and reject",
              "Offline evaluation suite: replay and simulation",
          ], size=22, pgap=6),
          eyebrow(1000, 716, "Tester metrics, to fill"))
    x, y = 1000, 756
    for text in ["[Active testers]", "[Swipes / session]", "[Keep rate]", "[Outfits / user]",
                 "[Outbound clicks / MAU]", "[D1 / D7 / D30]"]:
        els, pw = placeholder(x, y, text, size=17)
        if x + pw > MR:
            x, y = 1000, y + 56
            els, pw = placeholder(x, y, text, size=17)
        s.add(els)
        x += pw + 12
    return s


def s10_competition() -> Slide:
    s = chrome(Slide(), 10, "Competition", sources=[
        "Positions from product research, status 1 Nov 2026. Vinted and Depop are second-hand only.",
    ], note_h=24)
    s.add(headline("Only Quenya starts from a style and mixes new with second-hand.", w=1560, h=100, size=68))
    x0, y0, pw, ph = 200, 300, 920, 600
    midx, midy = x0 + pw / 2, y0 + ph / 2
    s.add(
        Rect(midx, y0, pw / 2, ph / 2, fill=SAGE_T),
        Line(x0, midy, x0 + pw, midy, color="#CFC7BB", w=2),
        Line(midx, y0, midx, y0 + ph, color="#CFC7BB", w=2),
        T(x0, y0 + ph + 18, 400, 26, "← Starts from a product", size=17, color=MUTE, weight=500, lh=1.4),
        T(x0 + pw - 400, y0 + ph + 18, 400, 26, "Starts from a style →", size=17, color=MUTE,
          weight=500, align="right", lh=1.4),
    )
    s.add(
        T(x0 - 84, y0 - 6, 72, 120, ["New", "+", "second-", "hand"], size=15, color=MUTE, weight=500,
          lh=1.3, align="right"),
        T(x0 - 84, y0 + ph - 46, 72, 50, ["New", "only"], size=15, color=MUTE, weight=500, lh=1.3,
          align="right"),
    )
    players = [
        ("Lyst", 0.16, 0.18), ("Vinted · Depop", 0.12, 0.88), ("Phia", 0.27, 0.74), ("Beni", 0.36, 0.64),
        ("Daydream", 0.64, 0.24), ("Alta", 0.80, 0.30), ("Doji", 0.72, 0.12), ("Whering", 0.86, 0.43),
        ("Gensmo", 0.60, 0.46),
    ]
    for name, px, py in players:
        x, y = x0 + px * pw, y0 + (1 - py) * ph
        s.add(Rect(x - 7, y - 7, 14, 14, fill="#A79F94", ellipse=True),
              T(x + 16, y - 15, 220, 30, name, size=19, color=BODY, lh=1.4))
    qx, qy = x0 + 0.84 * pw, y0 + (1 - 0.84) * ph
    s.add(Rect(qx - 15, qy - 15, 30, 30, fill=INK, ellipse=True),
          Rect(qx - 26, qy - 26, 52, 52, stroke=INK, sw=2, ellipse=True),
          T(qx - 200, qy + 30, 400, 40, "Quenya", size=26, weight=600, color=INK, align="center", lh=1.3))
    x, w = 1260, MR - 1260
    points = [
        ("Starts from a style", "A described style with its own taste profile, not a product page or a "
         "wardrobe upload."),
        ("New + second-hand, one feed", "130 UK shops alongside live Vinted listings, ranked together."),
        ("UK-first", "The funded AI stylists are US-based. Whering, the UK player, styles clothes you "
         "already own."),
    ]
    y = 320
    for title, text in points:
        s.add(Line(x, y, MR, y, color=HAIR, w=2),
              label(x, y + 22, w, title, size=26),
              body(x, y + 66, w, 110, text, size=20, lh=1.42))
        y += 196
    return s


def s11_investors() -> Slide:
    s = chrome(Slide(), 11, "Investors are backing this space", sources=[
        "Gensmo: WWD, Aug 2025.  Daydream: TechCrunch, 25 Jun 2025.  Phia: BoF and Phia, 27 Jan 2026.  "
        "Alta: Fortune, 22 Oct 2026.  Whering: Tech.eu, 7 Jul 2026.  Doji: TechCrunch, 15 May 2025.  "
        "Lyst: TechCrunch, 9 Apr 2025.  The Yes: Pinterest, Jun 2022.",
    ], note_h=24)
    s.add(headline("Investors are already backing AI fashion discovery.", w=1500, h=100, size=72))
    s.add(eyebrow(ML, 306, "Disclosed funding, $M"))
    rows = [
        ("Gensmo", "US · prompt-to-outfit · seed, Jun 2025", 60, "$60M+"),
        ("Daydream", "US · AI shopping agent · seed, Jun 2024", 50, "$50M"),
        ("Phia", "US · shopping incl. resale · $8M seed + $35M Series A", 43, "$43M"),
        ("Alta", "US · AI stylist · $11M seed + $20M Series A", 31, "$31M"),
        ("Whering", "UK · digital wardrobe · incl. $7M seed, Jul 2026", 14, "~$14M"),
        ("Doji", "US · avatar try-on · seed, May 2025", 14, "$14M"),
    ]
    y, bx, scale = 352, ML + 470, 380 / 60
    for name, detail, value, text in rows:
        s.add(label(ML, y, 460, name, size=24),
              T(ML, y + 34, 460, 24, detail, size=15, color=MUTE, lh=1.4),
              Rect(bx, y + 18, value * scale, 16, fill=INK, radius=8),
              T(bx + value * scale + 16, y + 2, 160, 44, text, font=SERIF, size=38, lh=1.1))
        y += 94
    x, w = 1240, MR - 1240
    s.add(eyebrow(x, 306, "Exits"))
    for i, (num, text) in enumerate([("$154M", "Lyst (UK), acquired by ZOZO, Apr 2025"),
                                      ("$87.6M", "The Yes, a swipe-to-learn-taste fashion app, acquired by Pinterest in cash, 2022")]):
        y = 346 + i * 172
        s.add(T(x, y, w, 80, num, font=SERIF, size=76, lh=1.0),
              body(x, y + 86, w, 64, text, size=19, lh=1.4))
    s.add(Rect(x, 706, 4, 190, fill=SAGE),
          eyebrow(x + 28, 704, "The gap", color=SAGE_D),
          T(x + 28, 740, w - 28, 160, "None of them starts from a described style and mixes live UK "
            "second-hand with new UK retail in one feed.", size=25, weight=500, color=INK, lh=1.36))
    s.notes = ("Phia's follow-on verified: $35M Series A led by Notable Capital at a $185M valuation, "
               "announced 27 Jan 2026 (Phia on LinkedIn; Business of Fashion; Forbes). Total funding "
               "more than $43M. Alta: $11M seed (Menlo, Jun 2025) + $20M Series A (Forerunner, Oct 2026).")
    return s


def s12_gtm() -> Slide:
    s = chrome(Slide(), 12, "Go-to-market", sources=[
        "1  socialbrandmatch.com.  2  tim.blog, 2014.  3  waitlister.me case study.  4  youthmarketing.com.  "
        "5  Fast Company, 2025.",
    ], note_h=24)
    s.add(headline("UK first, through creators and a waitlist.", w=1400, h=100))
    w = (CW - 2 * 72) / 3
    xs = [ML + i * (w + 72) for i in range(3)]
    cols = [
        ("Creators", "25 UK micro-creators shortlisted",
         [Run("Across four niches: quiet luxury, streetwear, gorpcore and vintage. UK micro rates run "
              "£100–£1,500 per Instagram post."), sup(1)]),
        ("Waitlist and referral", "Invite-only, then TestFlight, then the App Store",
         [Run("Referral built into the waitlist. Harry’s collected ~100k emails in a week, 77% by referral;"),
          sup(2), Run(" Monzo’s golden tickets drove ~40% of 2017 sign-ups."), sup(3)]),
        ("Campus and sharing", "Ambassadors and a shareable style card",
         [Run("Depop’s ambassadors drove 428K attributed installs."), sup(4),
          Run(" A “style DNA” card to share, in the spirit of Spotify Wrapped (300M+ users)."), sup(5)]),
    ]
    for i, (k, title, text) in enumerate(cols):
        x = xs[i]
        s.add(Line(x, 360, x + w, 360, color=HAIR, w=2),
              Line(x, 360, x + 56, 360, color=SAGE, w=4),
              eyebrow(x, 392, k, color=SAGE_D),
              label(x, 430, w, title, size=30, h=84),
              body(x, 528, w - 10, 220, [text], size=22))
    els, pw = placeholder(xs[1], 690, "[App Store launch date]", size=18)
    s.add(els)
    s.add(Line(ML, 860, MR, 860, color=HAIR, w=2),
          T(ML, 884, CW, 50, "No paid acquisition until CAC payback is under 3 months.", font=SERIF,
            size=40, lh=1.2))
    return s


def s13_team() -> Slide:
    s = chrome(Slide(), 13, "Team")
    s.add(headline("The team.", w=1000, h=100))
    els, _ = placeholder(ML, 290, "[Why this team: one line on founder–market fit]", size=20)
    s.add(els)
    w = (CW - 2 * 72) / 3
    for i in range(3):
        x = ML + i * (w + 72)
        s.add(Rect(x, 400, 220, 220, fill=CORAL_T, stroke=CORAL, sw=2, dash=True, ellipse=True),
              T(x, 400, 220, 220, "[Photo]", font=MONO, weight=500, size=18, color=CORAL_D,
                align="center", valign="middle"))
        y = 660
        for text, size in [("[Name]", 22), ("[Role]", 18), ("[Previous experience]", 18)]:
            els, _ = placeholder(x, y, text, size=size)
            s.add(els)
            y += size * 2.1 + 14
    els, _ = placeholder(ML, 900, "[Advisers and angels, if any]", size=18)
    s.add(els)
    return s


def s14_ask() -> Slide:
    s = chrome(Slide(), 14, "The ask", sources=[
        "1  gov.uk, Seed Enterprise Investment Scheme.  Milestones are targets from our monetisation model.",
    ], note_h=24)
    s.add(headline("The ask.", w=900, h=100))
    els, _ = placeholder(ML, 290, "Raising [£ round size]", size=30, h=76)
    s.add(els)
    w1 = 470
    y = 420
    s.add(Line(ML, y, ML + w1, y, color=HAIR, w=2), eyebrow(ML, y + 24, "Terms"))
    y += 64
    for k, v in [("Instrument", "[SEIS/EIS equity · ASA · SAFE]"), ("Valuation / cap", "[£ —]"),
                 ("Runway", "[__ months]"), ("SEIS advance assurance", "[Status]")]:
        s.add(T(ML, y, w1, 26, k, size=17, color=MUTE, lh=1.4))
        els, _ = placeholder(ML, y + 30, v, size=17)
        s.add(els)
        y += 92
    s.add(body(ML, y + 4, w1, 70, [[Run("SEIS allows up to £250,000 per company, issued before any "
                                        "EIS shares."), sup(1)]], size=17, lh=1.4))
    x2, w2 = 700, 480
    s.add(Line(x2, 420, x2 + w2, 420, color=HAIR, w=2), eyebrow(x2, 444, "Use of funds"))
    y = 488
    for k in ["Team", "Creators and UGC", "Paid tests", "Infrastructure", "Legal and tools", "Contingency"]:
        s.add(T(x2, y + 4, 240, 28, k, size=21, color=INK, lh=1.4))
        els, _ = placeholder(x2 + w2 - 110, y, "[ %]", size=17, w=110)
        s.add(els, Line(x2, y + 50, x2 + w2, y + 50, color=HAIR, w=1))
        y += 66
    x3, w3 = 1290, MR - 1290
    s.add(Line(x3, 420, MR, 420, color=HAIR, w=2), eyebrow(x3, 444, "What it unlocks"))
    y = 492
    for n, title, text in [("1", "5k MAU", "4+ outbound clicks per MAU and D30 retention of 8% or more."),
                           ("2", "25k MAU", "£0.50+ per MAU. Sponsored placements switch on."),
                           ("3", "50k MAU", "£1+ per MAU. Premium and B2B licensing.")]:
        s.add(T(x3, y + 2, 30, 30, n, font=MONO, weight=500, size=16, color=CORAL_D, lh=1.6),
              label(x3 + 34, y - 2, w3 - 34, title, size=26),
              body(x3 + 34, y + 38, w3 - 34, 70, text, size=19, lh=1.4))
        y += 140
    return s


def build() -> list[Slide]:
    return [s01_title(), s02_problem(), s03_why_matters(), s04_how(), s05_product(), s06_why_now(),
            s07_market(), s08_model(), s09_traction(), s10_competition(), s11_investors(), s12_gtm(),
            s13_team(), s14_ask()]
