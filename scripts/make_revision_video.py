#!/usr/bin/env python3
"""Turn a revision note (revision_notes/Chapter_NN_*.md) into a narrated 1080p revision video.

Each ### subsection becomes one or more slides whose bullets appear one at a time,
in sync with an edge-tts voice-over. Tables are redrawn as slides (rows revealed as
they are read), figures are shown full-size with their caption, and every ## section
gets a divider slide. Outputs an MP4 (with a soft subtitle track) plus an .srt file.

Usage:
    python scripts/make_revision_video.py Chapter_04_Maternal_Physiology
    python scripts/make_revision_video.py Chapter_04_Maternal_Physiology --limit 12      # quick preview
    python scripts/make_revision_video.py Chapter_04_Maternal_Physiology --frames-only   # slide PNGs only

Needs: edge-tts, Pillow, fonttools (see .revision_work/venv) and ffmpeg on PATH.
"""
import argparse
import asyncio
import hashlib
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import edge_tts
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "revision_notes"
OUT_DIR = ROOT / "videos" / "revision"
WORK = ROOT / ".revision_work" / "video"
CACHE = WORK / "tts_cache"

W, H, FPS, SR = 1920, 1080, 30, 24000
VOICE = "en-US-AriaNeural"
PAD = 0.35  # silence after each step, seconds

NAVY, RED, INK, MUTED = (11, 79, 138), (138, 28, 28), (29, 29, 31), (95, 105, 120)
BG, ROW_ALT, HILITE = (250, 251, 253), (243, 247, 251), (255, 244, 204)
QUOTE_BG, QUOTE_BAR = (255, 247, 224), (224, 168, 0)
X0, X1, TOP, BOTTOM = 110, 1810, 250, 1000

# ---------------------------------------------------------------- fonts
HELV = "/System/Library/Fonts/HelveticaNeue.ttc"
FALLBACKS = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"]
_cmaps = {HELV: set(TTCollection(HELV).fonts[0].getBestCmap())}
for p in FALLBACKS:
    _cmaps[p] = set(TTFont(p).getBestCmap())
_fonts = {}


def font(size, bold=False, italic=False, path=HELV):
    key = (size, bold, italic, path)
    if key not in _fonts:
        index = (1 if bold else 0) + (2 if italic else 0) if path == HELV else 0
        _fonts[key] = ImageFont.truetype(path, size, index=index)
    return _fonts[key]


def runs(text, size, bold=False, italic=False):
    """Split text into (substring, font) runs, falling back per character for missing glyphs."""
    out = []
    for ch in text:
        path = HELV
        if ord(ch) not in _cmaps[HELV]:
            path = next((p for p in FALLBACKS if ord(ch) in _cmaps[p]), HELV)
        f = font(size, bold, italic, path)
        if out and out[-1][1] is f:
            out[-1][0] += ch
        else:
            out.append([ch, f])
    return out


def text_width(text, size, bold=False, italic=False):
    return sum(f.getlength(s) for s, f in runs(text, size, bold, italic))


def draw_runs(draw, x, baseline, text, size, color, bold=False, italic=False):
    for s, f in runs(text, size, bold, italic):
        draw.text((x, baseline), s, font=f, fill=color, anchor="ls")
        x += f.getlength(s)
    return x


# ---------------------------------------------------------------- rich text
DISPLAY_FIX = {"⚠️": "⚠", "ᵃ": "a", "ᵇ": "b", "​": ""}


def display(text):
    for a, b in DISPLAY_FIX.items():
        text = text.replace(a, b)
    return text.replace("`", "")


def segments(text):
    """'**bold** plain *ital*' -> [(text, bold)] (single-star italics are just unwrapped)."""
    text = display(text)
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        part = re.sub(r"(?<!\w)\*(\S.*?)\*(?!\w)", r"\1", part)
        if part:
            out.append((part, i % 2 == 1))
    return out


def wrap(text, size, width, italic=False, all_bold=False):
    """Word-wrap rich text. Returns list of lines; each line is a list of (x, word, bold)."""
    lines, line, x = [], [], 0.0
    for seg, bold in segments(text):
        bold = bold or all_bold
        for word in re.findall(r"\s+|\S+\s*", seg):
            if not word.strip():  # whitespace at a bold/plain boundary
                if line:
                    x += text_width(word, size, bold, italic)
                continue
            w = text_width(word.rstrip(), size, bold, italic)
            if line and x + w > width:
                lines.append(line)
                line, x = [], 0.0
            line.append((x, word, bold))
            x += text_width(word, size, bold, italic)
    if line:
        lines.append(line)
    return lines or [[]]


def draw_wrapped(draw, lines, x, top, size, lh, color, bold_color=RED, italic=False):
    for i, line in enumerate(lines):
        base = top + i * lh + size
        for dx, word, bold in line:
            draw_runs(draw, x + dx, base, word, size, bold_color if bold else color, bold, italic)


# ---------------------------------------------------------------- speech text
UNITS = [
    (r"mL/min", "milliliters per minute"), (r"mg/dL", "milligrams per deciliter"),
    (r"g/dL", "grams per deciliter"), (r"mmol/L", "millimoles per liter"), (r"mEq/L", "milliequivalents per liter"),
    (r"ng/mL", "nanograms per milliliter"), (r"pg/mL", "picograms per milliliter"), (r"µg/dL", "micrograms per deciliter"),
    (r"mg/d", "milligrams per day"), (r"g/d", "grams per day"), (r"kcal/d", "kilocalories per day"),
    (r"mm Hg", "millimeters of mercury"), (r"mmHg", "millimeters of mercury"), (r"bpm", "beats per minute"),
    (r"/µL", "per microliter"), (r"/mm³", "per cubic millimeter"), (r"kcal", "kilocalories"),
    (r"wk", "weeks"), (r"mo", "months"), (r"yr", "years"), (r"min", "minutes"), (r"hr?", "hours"),
    (r"kg", "kilograms"), (r"mg", "milligrams"), (r"µg", "micrograms"), (r"g", "grams"),
    (r"mL", "milliliters"), (r"L", "liters"), (r"cm", "centimeters"), (r"mm", "millimeters"),
]
SYMBOLS = [
    ("→", " leads to "), ("←", " from "), ("↑", " increased "), ("↓", " decreased "), ("↔", " unchanged "),
    ("≥", " at least "), ("≤", " at most "), ("≈", " about "), ("~", " about "), ("×", " times "),
    ("∝", " proportional to "), ("±", " with or without "), ("½", " half "), ("⅓", " one third "),
    ("⁴", " to the fourth "), ("²", " squared "), ("³", ""), ("⁵", ""), ("⁺", ""), ("⁻", ""), ("₂", "2"), ("₃", "3"),
    ("°", " degrees "), ("α", "alpha "), ("µ", "micro"), ("⚠️", ""), ("⚠", ""), ("ᵃ", ""), ("ᵇ", ""),
    ("&", " and "), ("—", ", "), ("|", ", "),
]


ABBREV = [
    (r"\b1st Tri\b", "first trimester"), (r"\b2nd Tri\b", "second trimester"), (r"\b3rd Tri\b", "third trimester"),
    (r"\bHCO₃⁻|\bHCO3\b", "bicarbonate"), (r"\bO₂|\bO2\b", "oxygen"), (r"\bCO₂|\bCO2\b", "carbon dioxide"),
    (r"\bHb\b", "hemoglobin"), (r"\bHct\b", "hematocrit"), (r"\bNSC\b", "no significant change"),
    (r"mm Hg|mmHg", "millimeters of mercury"), (r"\bkJ/d\b", "kilojoules per day"), (r"\bg/d\b", "grams per day"),
    (r"\bMJ\b", "megajoules"), (r"\bkcal\b", "kilocalories"), (r"\bmOsm/kg\b", "milliosmoles per kilogram"), (r"\bmOsm\b", "milliosmoles"),
    (r"\bdyn/sec/cm⁻⁵", "dynes"), (r"\bg/m/m²", "grams per meter squared"), (r"\bL/min\b", "liters per minute"),
    (r"\bbeats/min\b", "beats per minute"), (r"\bµg/mL\b", "micrograms per milliliter"),
]


def speech(text):
    t = re.sub(r"[ᵃᵇᶜ]", "", text)
    t = re.sub(r"\*+", "", display(t))
    t = re.sub(r"(\d)\s*±\s*(\d)", r"\1 plus or minus \2", t)
    t = t.replace("=", " equals ")
    for pat, word in ABBREV:
        t = re.sub(pat, word, t)
    t = re.sub(r"\be\.g\.,?", "for example,", t)
    t = re.sub(r"\bi\.e\.,?", "that is,", t)
    t = re.sub(r"\bvs\.?\b", "versus", t)
    t = re.sub(r"\bCh (\d+)", r"Chapter \1", t)
    t = re.sub(r"(\d)\s*[–-]\s*(\d)", r"\1 to \2", t)                   # ranges
    t = re.sub(r"(^|[\s(])[−-](\d)", r"\1minus \2", t)                  # negative numbers
    t = re.sub(r"(^|[\s(])\+(\d)", r"\1plus \2", t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = t.replace(">", " greater than ").replace("<", " less than ")
    for unit, word in UNITS:
        t = re.sub(r"(\d[\d.]*\s*)" + unit + r"(?![\w/])", r"\1" + word, t)
    for sym, word in SYMBOLS:
        t = t.replace(sym, word)
    t = re.sub(r"(?<=[A-Za-z])/(?=[A-Za-z])", " or ", t)
    t = t.replace("/", " per ").replace("–", " to ").replace("−", " minus ")
    t = re.sub(r"[()\[\]]", ", ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r",\s*,+", ",", t)
    t = re.sub(r"[,;:]+\s*([.!?])", r"\1", t)
    t = re.sub(r"[,;:]\s*$", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip(" ,")
    if t and t[-1] not in ".!?:":
        t += "."
    return t


# ---------------------------------------------------------------- parsing
BULLET = re.compile(r"^( *)(?:[-*+]|\d+\.) +(.*)")


def parse(md):
    lines = md.split("\n")
    doc = {"title": "", "intro": [], "sections": []}
    sec = sub = None
    pending_table_title, i = None, 0
    stack = []

    def blocks():
        nonlocal sec, sub
        if sec is None:
            return doc["intro"]
        if sub is None:
            sub = {"h3": None, "blocks": []}
            sec["subs"].append(sub)
        return sub["blocks"]

    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if line.startswith("# "):
            doc["title"] = line[2:].replace("— Revision Notes", "").strip()
        elif line.startswith("## "):
            sec = {"h2": line[3:].strip(), "subs": []}
            doc["sections"].append(sec)
            sub, pending_table_title = None, None
        elif line.startswith("### "):
            title = line[4:].strip()
            if title.lower().startswith("table"):
                pending_table_title = title
            else:
                sub = {"h3": title, "blocks": []}
                sec["subs"].append(sub)
                pending_table_title = None
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            notes = []
            while i < len(lines) and (not lines[i].strip() or re.fullmatch(r"\*[^*].*\*", lines[i].strip())):
                if lines[i].strip():
                    notes.append(lines[i].strip().strip("*"))
                elif notes:
                    break
                i += 1
            blocks().append({"t": "table", "title": pending_table_title, "head": rows[0], "rows": rows[1:], "notes": notes})
            pending_table_title = None
            continue
        elif s.startswith("!["):
            m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", s)
            cap = ""
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].strip().startswith("*"):
                cap, i = lines[j].strip().strip("*"), j
            blocks().append({"t": "figure", "path": NOTES / m.group(1), "caption": cap})
        elif s.startswith(">"):
            text = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                text.append(lines[i].strip().lstrip(">").strip())
                i += 1
            if sec is not None:  # the header block above the first section is source info, not content
                blocks().append({"t": "quote", "text": " ".join(text)})
            continue
        elif BULLET.match(line):
            m = BULLET.match(line)
            indent = len(m.group(1))
            while stack and indent < stack[-1]:
                stack.pop()
            if not stack or indent > stack[-1]:
                stack.append(indent)
            blocks().append({"t": "bullet", "text": m.group(2), "level": min(len(stack) - 1, 2)})
            i += 1
            continue
        elif s and s != "---":
            b = blocks()
            if re.fullmatch(r"\*\*[^*]+\*\*:?", s):
                b.append({"t": "label", "text": s.strip("*: ")})
            elif b and b[-1]["t"] in ("bullet", "para") and line.startswith(" "):
                b[-1]["text"] += " " + s
            else:
                b.append({"t": "para", "text": s})
        if not BULLET.match(line) and s:
            stack = []
        i += 1
    return doc


# ---------------------------------------------------------------- slide layout
BODY = 38


def item_layout(b):
    """Return (height, lines, size, lh, indent) for a content item."""
    level = b.get("level", 0)
    size = {0: BODY, 1: 34, 2: 31}[level] if b["t"] == "bullet" else (40 if b["t"] == "label" else 36 if b["t"] == "quote" else BODY)
    indent = {"bullet": 46 + 52 * level, "quote": 40, "label": 0, "para": 0}[b["t"]]
    width = X1 - X0 - indent - (30 if b["t"] == "quote" else 0)
    lh = round(size * 1.3)
    lines = wrap(b["text"], size, width, all_bold=b["t"] == "label")
    h = len(lines) * lh + (24 if b["t"] == "quote" else 0)
    return h, lines, size, lh, indent


def gap(b):
    return 26 if b["t"] == "label" else 12 if b.get("level", 0) else 18


def paginate(items):
    pages, page, y = [], [], TOP
    for k, b in enumerate(items):
        h = item_layout(b)[0]
        need = h
        if b["t"] == "label" and k + 1 < len(items):
            need += gap(items[k + 1]) + item_layout(items[k + 1])[0]
        if page and y + gap(b) + need > BOTTOM:
            pages.append(page)
            page, y = [], TOP
        y += (gap(b) if page else 0) + h
        page.append(b)
    if page:
        pages.append(page)
    return pages


def table_layout(tbl, rows):
    """Choose a font size and column widths so the table fits; returns None if it cannot."""
    ncol = len(tbl["head"])
    avail = X1 - X0
    for size in range(32, 19, -1):
        pad, lh = 14, round(size * 1.25)
        cells = [tbl["head"]] + rows
        natural = [max(text_width(re.sub(r"\*", "", display(r[c] if c < len(r) else "")), size) for r in cells) + 2 * pad for c in range(ncol)]
        longest = [max(max((text_width(w, size, True) for w in re.sub(r"\*", "", display(r[c] if c < len(r) else "")).split()), default=0) for r in cells) + 2 * pad for c in range(ncol)]
        if sum(longest) > avail:
            continue
        total = sum(natural)
        if total <= avail:
            widths = [n * avail / total for n in natural]
        else:
            extra = avail - sum(longest)
            spare = [n - l for n, l in zip(natural, longest)]
            widths = [l + extra * s / (sum(spare) or 1) for l, s in zip(longest, spare)]
        wrapped = [[wrap(r[c] if c < len(r) else "", size, widths[c] - 2 * pad, all_bold=(ri == 0)) for c in range(ncol)] for ri, r in enumerate(cells)]
        heights = [max(len(w) for w in row) * lh + 2 * 10 for row in wrapped]
        notes_h = sum(len(wrap(n, 24, avail)) * 31 for n in tbl["notes"])
        if TOP + sum(heights) + notes_h + 10 <= BOTTOM:
            return {"size": size, "pad": pad, "lh": lh, "widths": widths, "wrapped": wrapped, "heights": heights}
    return None


# ---------------------------------------------------------------- rendering
def base(slide, idx, total):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 96], fill=NAVY)
    draw_runs(d, X0, 62, display(slide.get("band", "")), 32, (255, 255, 255), bold=True)
    tag = slide["chapter"]
    draw_runs(d, X1 - text_width(tag, 26), 60, tag, 26, (190, 214, 240))
    if slide.get("heading"):
        size = 52
        while text_width(slide["heading"], size, True) > X1 - X0 and size > 22:
            size -= 2
        draw_runs(d, X0, 190, display(slide["heading"]), size, NAVY, bold=True)
        d.line([X0, 212, X1, 212], fill=(201, 216, 232), width=3)
    d.rectangle([0, H - 8, W, H], fill=(221, 229, 238))
    d.rectangle([0, H - 8, round(W * (idx + 1) / total), H], fill=NAVY)
    return img, d


def render(step, idx, total):
    slide = step["slide"]
    kind = slide["kind"]
    if kind == "title":
        img = Image.new("RGB", (W, H), NAVY)
        d = ImageDraw.Draw(img)
        draw_runs(d, 160, 340, "WILLIAMS OBSTETRICS 26e · REVISION", 34, (160, 196, 232), bold=True)
        y = 350
        for line in wrap(slide["title"], 88, W - 320, all_bold=True):
            y += 110
            draw_wrapped(d, [line], 160, y - 88, 88, 110, (255, 255, 255), (255, 255, 255))
        if slide.get("intro"):
            lines = wrap(slide["intro"], 36, W - 320)
            draw_wrapped(d, lines, 160, y + 70, 36, 50, (220, 232, 245), (255, 214, 102))
        return img
    if kind == "section":
        img = Image.new("RGB", (W, H), NAVY)
        d = ImageDraw.Draw(img)
        m = re.match(r"(\d+)\.\s*(.*)", slide["h2"])
        num, name = (m.group(1), m.group(2)) if m else ("", slide["h2"])
        if num:
            draw_runs(d, 160, 480, f"SECTION {num}", 40, (160, 196, 232), bold=True)
        lines = wrap(name, 84, W - 320, all_bold=True)
        draw_wrapped(d, lines, 160, 500, 84, 104, (255, 255, 255), (255, 255, 255))
        d.rectangle([0, H - 8, round(W * (idx + 1) / total), H], fill=(56, 189, 248))
        return img

    img, d = base(slide, idx, total)
    if kind == "content":
        y = TOP
        for k, b in enumerate(slide["items"][: step["reveal"]]):
            h, lines, size, lh, indent = item_layout(b)
            if k:
                y += gap(b)
            x = X0 + indent
            if k in step["current"]:
                d.rounded_rectangle([X0 - 24, y - 6, X1 + 24, y + h + 6], 10, fill=HILITE)
            if b["t"] == "bullet":
                cy = y + size * 0.62
                r = 7 if b.get("level", 0) == 0 else 5
                bx = x - 30
                if b.get("level", 0) == 0:
                    d.ellipse([bx - r, cy - r, bx + r, cy + r], fill=NAVY)
                else:
                    d.ellipse([bx - r, cy - r, bx + r, cy + r], outline=NAVY, width=3)
            if b["t"] == "quote":
                d.rounded_rectangle([X0, y, X1, y + h], 8, fill=QUOTE_BG)
                d.rectangle([X0, y, X0 + 8, y + h], fill=QUOTE_BAR)
                draw_wrapped(d, lines, x, y + 12, size, lh, INK)
            elif b["t"] == "label":
                draw_wrapped(d, lines, x, y, size, lh, NAVY, NAVY)
            else:
                draw_wrapped(d, lines, x, y, size, lh, INK)
            y += h
    elif kind == "table":
        L = slide["layout"]
        y = TOP
        for ri, row in enumerate(L["wrapped"][: step["reveal"] + 1]):
            rh = L["heights"][ri]
            fill = NAVY if ri == 0 else HILITE if ri - 1 == step["reveal"] - 1 and step["narrating_row"] else ROW_ALT if ri % 2 == 0 else (255, 255, 255)
            d.rectangle([X0, y, X1, y + rh], fill=fill)
            x = X0
            for c, lines in enumerate(row):
                color = (255, 255, 255) if ri == 0 else INK
                draw_wrapped(d, lines, x + L["pad"], y + 10, L["size"], L["lh"], color, color if ri == 0 else RED)
                x += L["widths"][c]
            if ri:
                d.line([X0, y + rh, X1, y + rh], fill=(221, 229, 238), width=2)
            y += rh
        if step["reveal"] >= len(slide["rows"]):
            y += 14
            for n in slide["notes"]:
                lines = wrap(n, 24, X1 - X0, italic=True)
                draw_wrapped(d, lines, X0, y, 24, 31, MUTED, MUTED, italic=True)
                y += len(lines) * 31
    elif kind == "figure":
        pic = Image.open(slide["path"]).convert("RGB")
        cap_lines = wrap(slide["caption"], 32, X1 - X0 - 80, italic=True)
        cap_h = len(cap_lines) * 42
        box_w, box_h = X1 - X0, BOTTOM - TOP - cap_h - 30
        scale = min(box_w / pic.width, box_h / pic.height, 2.5)
        pic = pic.resize((round(pic.width * scale), round(pic.height * scale)), Image.LANCZOS)
        px = (W - pic.width) // 2
        img.paste(pic, (px, TOP))
        d.rectangle([px - 1, TOP - 1, px + pic.width, TOP + pic.height], outline=(201, 216, 232), width=2)
        cy = TOP + pic.height + 26
        for line in cap_lines:
            lw = line[-1][0] + text_width(line[-1][1].rstrip(), 32, italic=True) if line else 0
            draw_wrapped(d, [line], (W - lw) / 2, cy, 32, 42, MUTED, RED, italic=True)
            cy += 42
    return img


# ---------------------------------------------------------------- planning
def plan(doc, chapter_tag):
    steps = []
    title_slide = {"kind": "title", "title": doc["title"], "chapter": chapter_tag,
                   "intro": " ".join(b["text"] for b in doc["intro"] if b["t"] == "para")}
    intro_speech = f"Williams Obstetrics. {doc['title']}. Revision notes. " + speech(title_slide["intro"])
    steps.append({"slide": title_slide, "say": intro_speech})

    for sec in doc["sections"]:
        h2 = sec["h2"]
        m = re.match(r"(\d+)\.\s*(.*)", h2)
        say = f"Section {m.group(1)}. {m.group(2)}." if m else speech(h2.replace(":", "."))
        steps.append({"slide": {"kind": "section", "h2": h2, "chapter": chapter_tag}, "say": say})
        band = re.sub(r"^\d+\.\s*", "", h2)
        for sub in sec["subs"]:
            heading = sub["h3"] or band
            said_heading = False
            chunk = []

            def flush():
                nonlocal said_heading
                for p, page in enumerate(paginate(chunk)):
                    slide = {"kind": "content", "band": band, "chapter": chapter_tag, "items": page,
                             "heading": heading + (" (cont.)" if p else "")}
                    k = 0
                    while k < len(page):
                        cur = [k]
                        if page[k]["t"] == "label" and k + 1 < len(page):
                            cur.append(k + 1)
                        text = " ".join(speech(page[c]["text"]) for c in cur)
                        if not said_heading and sub["h3"]:
                            text = f"{speech(sub['h3'])} {text}"
                            said_heading = True
                        steps.append({"slide": slide, "reveal": cur[-1] + 1, "current": cur, "say": text})
                        k = cur[-1] + 1
                chunk.clear()

            for b in sub["blocks"]:
                if b["t"] in ("bullet", "para", "label", "quote"):
                    chunk.append(b)
                    continue
                flush()
                if b["t"] == "figure":
                    if not b["path"].exists():
                        print(f"  ! missing figure {b['path']}", file=sys.stderr)
                        continue
                    slide = {"kind": "figure", "band": band, "chapter": chapter_tag, "heading": heading,
                             "path": b["path"], "caption": b["caption"]}
                    cap = re.sub(r"^Figure\s+[\d-]+\.?\s*", "", b["caption"])
                    steps.append({"slide": slide, "say": speech(cap)})
                elif b["t"] == "table":
                    steps += table_steps(b, band, heading, chapter_tag, sub, said_heading)
                    said_heading = True
            flush()
    return steps


def table_steps(tbl, band, heading, chapter_tag, sub, said_heading):
    head, rows = tbl["head"], tbl["rows"]
    parts, start = [], 0
    while start < len(rows):
        n = len(rows) - start
        while n > 1 and table_layout(tbl, rows[start:start + n]) is None:
            n -= 1
        parts.append(rows[start:start + n])
        start += n
    col0 = [re.sub(r"\*", "", r[0]) for r in rows if r]
    # First column is a row label (read bare) unless it is itself a category, e.g. "↑ Increases"
    label_col = not re.search(r"[↑↓↔]", head[0]) and sum(len(c) for c in col0) / max(len(col0), 1) < 60
    title = tbl["title"] or heading
    title_say = re.sub(r"^Table\s+[\d-]+\.?\s*", "", tbl["title"]) if tbl["title"] else (sub["h3"] if not said_heading and sub["h3"] else "")
    steps = []
    for p, part in enumerate(parts):
        slide = {"kind": "table", "band": band, "chapter": chapter_tag, "rows": part, "notes": tbl["notes"] if p == len(parts) - 1 else [],
                 "heading": re.sub(r"^Table\s+", "Table ", title) + (" (cont.)" if p else ""),
                 "layout": table_layout(dict(tbl, notes=tbl["notes"] if p == len(parts) - 1 else []), part) or table_layout(dict(tbl, notes=[]), part)}
        if p == 0 and title_say:
            steps.append({"slide": slide, "reveal": 0, "narrating_row": False, "say": speech(title_say)})
        for ri, row in enumerate(part):
            bits = []
            for c, cell in enumerate(row):
                if not cell or cell.strip() in ("—", "-", "–"):
                    continue
                hdr = head[c] if c < len(head) else ""
                if c == 0 and label_col:
                    bits.append(speech(cell).rstrip("."))
                elif hdr:
                    bits.append(f"{speech(re.sub(r'[↑↓↔]', '', hdr)).rstrip('.')}: {speech(cell).rstrip('.')}")
                else:
                    bits.append(speech(cell).rstrip("."))
            steps.append({"slide": slide, "reveal": ri + 1, "narrating_row": True, "say": ". ".join(bits) + "."})
    return steps


# ---------------------------------------------------------------- audio
async def tts(text, voice, rate):
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()
    mp3, sub = CACHE / f"{key}.mp3", CACHE / f"{key}.srt.txt"
    if mp3.exists() and sub.exists():
        return mp3, [l.split("\t", 2) for l in sub.read_text().splitlines() if l]
    for attempt in range(5):
        try:
            data, cues = bytearray(), []
            async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
                elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                    cues.append((str(chunk["offset"]), str(chunk["duration"]), chunk["text"]))
            if not data:
                raise RuntimeError("no audio returned")
            mp3.write_bytes(bytes(data))
            sub.write_text("\n".join("\t".join(c) for c in cues))
            return mp3, [list(c) for c in cues]
        except Exception as e:  # network hiccups: back off and retry
            if attempt == 4:
                raise
            print(f"  retry TTS ({e})", file=sys.stderr)
            await asyncio.sleep(2 * (attempt + 1))


async def synth_all(texts, voice, rate):
    sem = asyncio.Semaphore(6)
    done = 0

    async def one(t):
        nonlocal done
        async with sem:
            r = await tts(t, voice, rate)
            done += 1
            if done % 25 == 0 or done == len(texts):
                print(f"  narration {done}/{len(texts)}")
            return r

    return await asyncio.gather(*(one(t) for t in texts))


def pcm(mp3):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                          check=True, capture_output=True).stdout


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("note", help="revision note name, e.g. Chapter_04_Maternal_Physiology")
    ap.add_argument("--limit", type=int, help="only the first N steps (preview)")
    ap.add_argument("--frames-only", action="store_true", help="write slide PNGs, no audio/video")
    ap.add_argument("--voice", default=VOICE)
    ap.add_argument("--rate", default="+0%", help='speaking rate, e.g. "+10%%"')
    a = ap.parse_args()

    name = a.note[:-3] if a.note.endswith(".md") else a.note
    md_path = NOTES / f"{name}.md"
    doc = parse(md_path.read_text(encoding="utf-8"))
    num = re.match(r"Chapter_(\d+)", name).group(1).lstrip("0")
    tag = f"Ch {num} · {re.sub(r'^Chapter \d+:\s*', '', doc['title'])}"
    steps = plan(doc, tag)
    if a.limit:
        steps = steps[: a.limit]
    print(f"{name}: {len(steps)} steps")

    work = WORK / name
    frames = work / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    for i, st in enumerate(steps):
        render(st, i, len(steps)).save(frames / f"{i:04}.png")
    print(f"  rendered {len(steps)} frames → {frames}")
    if a.frames_only:
        return

    results = asyncio.run(synth_all([st["say"] for st in steps], a.voice, a.rate))

    audio = bytearray()
    concat, srt, t = [], [], 0.0
    per_frame = SR // FPS
    for i, (st, (mp3, cues)) in enumerate(zip(steps, results)):
        samples = pcm(mp3)
        n = len(samples) // 2
        pad = PAD + (0.5 if st["slide"]["kind"] in ("section", "title") else 0) + (0.8 if st["slide"]["kind"] == "figure" else 0)
        frames_n = math.ceil((n / SR + pad) * FPS)
        total = frames_n * per_frame
        audio += samples[: total * 2] + b"\0" * (2 * max(0, total - n))
        dur = frames_n / FPS
        concat.append(f"file '{frames / f'{i:04}.png'}'\nduration {dur:.6f}")
        for off, length, text in cues:
            s = t + int(off) / 1e7
            e = min(t + (int(off) + int(length)) / 1e7, t + dur)
            srt.append((s, e, text))
        t += dur
    concat.append(f"file '{frames / f'{len(steps) - 1:04}.png'}'")

    (work / "audio.raw").write_bytes(bytes(audio))
    (work / "frames.txt").write_text("\n".join(concat))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_preview" if a.limit else ""
    out = OUT_DIR / f"{name}_Revision{suffix}.mp4"
    srt_path = out.with_suffix(".srt")
    srt_path.write_text("\n".join(f"{k + 1}\n{srt_time(s)} --> {srt_time(e)}\n{txt}\n" for k, (s, e, txt) in enumerate(srt)), encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(work / "frames.txt"),
        "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", str(work / "audio.raw"),
        "-i", str(srt_path),
        "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-tune", "stillimage", "-crf", "20", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
        "-movflags", "+faststart", "-shortest", str(out),
    ], check=True)
    (work / "audio.raw").unlink()
    print(f"✓ {out.relative_to(ROOT)}  ({t / 60:.1f} min, {out.stat().st_size / 1e6:.1f} MB)")
    print(f"✓ {srt_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
