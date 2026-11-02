"""Tiny slide engine: one element list per slide, rendered to HTML (PNG/PDF) and to editable PPTX.

All coordinates are CSS px on a 1920x1080 canvas. 1px = 6350 EMU = 0.5pt.
"""

from __future__ import annotations

import base64
import html
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image as PILImage
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

W, H = 1920, 1080
EMU = 6350
FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"

SERIF = "serif"
SANS = "sans"
MONO = "mono"


def family(font: str, weight: int) -> tuple[str, bool]:
    """Return (font family name, bold flag) shared by HTML and PPTX."""
    if font == SERIF:
        return "Instrument Serif", False
    if font == MONO:
        return ("IBM Plex Mono Medium", False) if weight >= 500 else ("IBM Plex Mono", False)
    if weight >= 700:
        return "Hanken Grotesk", True
    if weight >= 600:
        return "Hanken Grotesk SemiBold", False
    if weight >= 500:
        return "Hanken Grotesk Medium", False
    return "Hanken Grotesk", False


FONT_FILES = {
    ("Instrument Serif", False, False): "InstrumentSerif-Regular.ttf",
    ("Instrument Serif", False, True): "InstrumentSerif-Italic.ttf",
    ("Hanken Grotesk", False, False): "HankenGrotesk-Regular.ttf",
    ("Hanken Grotesk", True, False): "HankenGrotesk-Bold.ttf",
    ("Hanken Grotesk Medium", False, False): "HankenGrotesk-Medium.ttf",
    ("Hanken Grotesk SemiBold", False, False): "HankenGrotesk-SemiBold.ttf",
    ("IBM Plex Mono", False, False): "IBMPlexMono-Regular.ttf",
    ("IBM Plex Mono Medium", False, False): "IBMPlexMono-Medium.ttf",
}


def text_width(text: str, size: float, font: str = SANS, weight: int = 400, ls: float = 0,
               italic: bool = False) -> float:
    from PIL import ImageFont

    name, bold = family(font, weight)
    path = FONT_DIR / FONT_FILES.get((name, bold, italic), FONT_FILES[(name, bold, False)])
    face = ImageFont.truetype(str(path), size=max(1, round(size * 4)))
    return face.getlength(text) / 4 + ls * size * len(text)


@dataclass
class Run:
    text: str
    size: float | None = None
    font: str | None = None
    weight: int | None = None
    color: str | None = None
    italic: bool | None = None
    sup: bool = False
    ls: float | None = None


@dataclass
class Text:
    x: float
    y: float
    w: float
    h: float
    paras: list
    size: float = 24
    font: str = SANS
    weight: int = 400
    color: str = "#171411"
    lh: float = 1.3
    ls: float = 0.0
    align: str = "left"
    valign: str = "top"
    italic: bool = False
    upper: bool = False
    pgap: float = 0
    name: str = ""


@dataclass
class Img:
    x: float
    y: float
    w: float
    h: float
    src: Path
    fit: str = "cover"
    radius: float = 0
    pos: tuple[float, float] = (0.5, 0.5)
    name: str = ""


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: str | None = None
    stroke: str | None = None
    sw: float = 1
    radius: float = 0
    dash: bool = False
    ellipse: bool = False
    name: str = ""


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    color: str = "#171411"
    w: float = 1.5
    dash: bool = False


@dataclass
class Slide:
    elements: list = field(default_factory=list)
    notes: str = ""
    bg: str = "#FAF8F5"

    def add(self, *items):
        for item in items:
            if isinstance(item, (list, tuple)):
                self.add(*item)
            elif item is not None:
                self.elements.append(item)
        return self


def _paras(t: Text) -> list[list[Run]]:
    out = []
    for para in t.paras:
        if isinstance(para, str):
            out.append([Run(para)])
        elif isinstance(para, Run):
            out.append([para])
        else:
            out.append([Run(r) if isinstance(r, str) else r for r in para])
    return out


def _style(t: Text, r: Run) -> dict:
    return {
        "size": r.size or t.size,
        "font": r.font or t.font,
        "weight": r.weight or t.weight,
        "color": r.color or t.color,
        "italic": t.italic if r.italic is None else r.italic,
        "ls": t.ls if r.ls is None else r.ls,
    }


# ---------------------------------------------------------------- HTML


def _font_faces() -> str:
    rules = []
    for (name, bold, italic), file in FONT_FILES.items():
        url = (FONT_DIR / file).as_uri()
        rules.append(
            f"@font-face{{font-family:'{name}';src:url('{url}');"
            f"font-weight:{700 if bold else 400};font-style:{'italic' if italic else 'normal'}}}"
        )
    return "\n".join(rules)


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _html_el(el) -> str:
    if isinstance(el, Rect):
        css = [f"left:{el.x}px;top:{el.y}px;width:{el.w}px;height:{el.h}px"]
        if el.fill:
            css.append(f"background:{el.fill}")
        if el.stroke:
            css.append(f"border:{el.sw}px {'dashed' if el.dash else 'solid'} {el.stroke}")
        css.append(f"border-radius:{'50%' if el.ellipse else f'{el.radius}px'}")
        return f'<div class="el" style="{";".join(css)}"></div>'
    if isinstance(el, Line):
        import math

        length = math.hypot(el.x2 - el.x1, el.y2 - el.y1)
        angle = math.degrees(math.atan2(el.y2 - el.y1, el.x2 - el.x1))
        style = "dashed" if el.dash else "solid"
        return (
            f'<div class="el" style="left:{el.x1}px;top:{el.y1 - el.w / 2}px;width:{length}px;height:0;'
            f'border-top:{el.w}px {style} {el.color};transform-origin:0 {el.w / 2}px;'
            f'transform:rotate({angle}deg)"></div>'
        )
    if isinstance(el, Img):
        fit = "cover" if el.fit == "cover" else "contain"
        return (
            f'<img class="el" src="{_data_uri(el.src)}" style="left:{el.x}px;top:{el.y}px;'
            f"width:{el.w}px;height:{el.h}px;object-fit:{fit};object-position:{el.pos[0] * 100}% "
            f'{el.pos[1] * 100}%;border-radius:{el.radius}px">'
        )
    if isinstance(el, Text):
        paras = []
        for index, para in enumerate(_paras(el)):
            runs = []
            for r in para:
                s = _style(el, r)
                name, bold = family(s["font"], s["weight"])
                text = html.escape(r.text.upper() if el.upper else r.text)
                css = (
                    f"font-family:'{name}';font-weight:{700 if bold else 400};"
                    f"font-style:{'italic' if s['italic'] else 'normal'};font-size:{s['size']}px;"
                    f"color:{s['color']};letter-spacing:{s['ls']}em"
                )
                if r.sup:
                    css += ";vertical-align:super;font-size:" + f"{s['size'] * 0.6}px;line-height:0"
                runs.append(f'<span style="{css}">{text}</span>')
            gap = f"margin-top:{el.pgap}px;" if index else ""
            paras.append(
                f'<p style="{gap}font-size:{el.size}px;line-height:{el.size * el.lh}px;text-align:{el.align}">{"".join(runs) or "&nbsp;"}</p>'
            )
        justify = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[el.valign]
        return (
            f'<div class="el txt" data-name="{html.escape(el.name)}" style="left:{el.x}px;top:{el.y}px;'
            f'width:{el.w}px;height:{el.h}px;justify-content:{justify}">{"".join(paras)}</div>'
        )
    raise TypeError(el)


def render_html(slides: list[Slide], out: Path) -> Path:
    pages = []
    for index, slide in enumerate(slides, 1):
        body = "".join(_html_el(el) for el in slide.elements)
        pages.append(f'<section class="slide" id="s{index}" style="background:{slide.bg}">{body}</section>')
    out.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        + _font_faces()
        + f"""
        @page{{size:{W}px {H}px;margin:0}}
        html,body{{margin:0;padding:0;background:#ddd}}
        .slide{{position:relative;width:{W}px;height:{H}px;overflow:hidden;page-break-after:always;break-after:page}}
        .el{{position:absolute;box-sizing:border-box;margin:0}}
        .txt{{display:flex;flex-direction:column;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;
              font-kerning:normal;font-feature-settings:"kern","liga"}}
        .txt p{{margin:0;white-space:pre-wrap}}
        </style></head><body>"""
        + "".join(pages)
        + "</body></html>",
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------------------- PPTX


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#")[:6].upper())


def _emu(px: float) -> Emu:
    return Emu(int(round(px * EMU)))


def _set_round(shape, radius: float, w: float, h: float) -> None:
    if radius <= 0:
        return
    shape.adjustments[0] = min(0.5, radius / min(w, h))


def _drop_theme_style(shape) -> None:
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def _pptx_el(slide, el) -> None:
    shapes = slide.shapes
    if isinstance(el, Rect):
        kind = MSO_SHAPE.OVAL if el.ellipse else (
            MSO_SHAPE.ROUNDED_RECTANGLE if el.radius else MSO_SHAPE.RECTANGLE
        )
        shape = shapes.add_shape(kind, _emu(el.x), _emu(el.y), _emu(el.w), _emu(el.h))
        if el.radius and not el.ellipse:
            _set_round(shape, el.radius, el.w, el.h)
        if el.fill:
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb(el.fill)
        else:
            shape.fill.background()
        if el.stroke:
            shape.line.color.rgb = _rgb(el.stroke)
            shape.line.width = Pt(el.sw * 0.5)
            if el.dash:
                from pptx.enum.dml import MSO_LINE_DASH_STYLE

                shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        else:
            shape.line.fill.background()
        _drop_theme_style(shape)
        if el.name:
            shape.name = el.name
        return
    if isinstance(el, Line):
        line = shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, _emu(el.x1), _emu(el.y1), _emu(el.x2), _emu(el.y2)
        )
        line.line.color.rgb = _rgb(el.color)
        line.line.width = Pt(el.w * 0.5)
        if el.dash:
            from pptx.enum.dml import MSO_LINE_DASH_STYLE

            line.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        _drop_theme_style(line)
        return
    if isinstance(el, Img):
        with PILImage.open(el.src) as image:
            iw, ih = image.size
        if el.fit == "contain":
            scale = min(el.w / iw, el.h / ih)
            dw, dh = iw * scale, ih * scale
            x = el.x + (el.w - dw) * el.pos[0]
            y = el.y + (el.h - dh) * el.pos[1]
            pic = shapes.add_picture(str(el.src), _emu(x), _emu(y), _emu(dw), _emu(dh))
        else:
            pic = shapes.add_picture(str(el.src), _emu(el.x), _emu(el.y), _emu(el.w), _emu(el.h))
            box, img = el.w / el.h, iw / ih
            if img > box:
                excess = 1 - box / img
                pic.crop_left, pic.crop_right = excess * el.pos[0], excess * (1 - el.pos[0])
            elif img < box:
                excess = 1 - img / box
                pic.crop_top, pic.crop_bottom = excess * el.pos[1], excess * (1 - el.pos[1])
        if el.radius:
            geom = pic._element.spPr.find(qn("a:prstGeom"))
            geom.set("prst", "roundRect")
            av = geom.find(qn("a:avLst"))
            if av is None:
                av = geom.makeelement(qn("a:avLst"), {})
                geom.append(av)
            gd = av.makeelement(qn("a:gd"), {
                "name": "adj", "fmla": f"val {int(min(50000, el.radius / min(el.w, el.h) * 100000))}"
            })
            av.append(gd)
        if el.name:
            pic.name = el.name
        return
    if isinstance(el, Text):
        box = shapes.add_textbox(_emu(el.x), _emu(el.y), _emu(el.w), _emu(el.h))
        if el.name:
            box.name = el.name
        frame = box.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
        frame.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                                 "bottom": MSO_ANCHOR.BOTTOM}[el.valign]
        for index, para in enumerate(_paras(el)):
            p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                           "right": PP_ALIGN.RIGHT}[el.align]
            p.line_spacing = Pt(el.size * el.lh * 0.5)
            if index and el.pgap:
                p.space_before = Pt(el.pgap * 0.5)
            for r in para:
                s = _style(el, r)
                name, bold = family(s["font"], s["weight"])
                run = p.add_run()
                run.text = r.text.upper() if el.upper else r.text
                font = run.font
                font.name = name
                font.size = Pt(s["size"] * 0.5)
                font.bold = bold
                font.italic = bool(s["italic"])
                font.color.rgb = _rgb(s["color"])
                rpr = run._r.get_or_add_rPr()
                if s["ls"]:
                    rpr.set("spc", str(int(round(s["ls"] * s["size"] * 0.5 * 100))))
                if r.sup:
                    rpr.set("baseline", "30000")
                for tag in ("a:ea", "a:cs"):
                    child = rpr.find(qn(tag))
                    if child is None:
                        child = rpr.makeelement(qn(tag), {})
                        rpr.append(child)
                    child.set("typeface", name)
        return
    raise TypeError(el)


def render_pptx(slides: list[Slide], out: Path) -> Path:
    prs = Presentation()
    prs.slide_width = _emu(W)
    prs.slide_height = _emu(H)
    blank = prs.slide_layouts[6]
    for spec in slides:
        slide = prs.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(spec.bg)
        for el in spec.elements:
            _pptx_el(slide, el)
        if spec.notes:
            slide.notes_slide.notes_text_frame.text = spec.notes
    prs.core_properties.title = "Quenya: investor presentation"
    prs.core_properties.author = "Quenya"
    prs.save(str(out))
    return out
