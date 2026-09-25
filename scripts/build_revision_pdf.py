#!/usr/bin/env python3
"""Render revision_notes/*.md to PDF (revision_notes/pdf/) via python-markdown + WeasyPrint.

Usage:
    python3 scripts/build_revision_pdf.py                 # all chapter notes
    python3 scripts/build_revision_pdf.py Chapter_04_...  # specific note(s), with or without .md
"""
import re
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "revision_notes"
OUT = NOTES / "pdf"

CSS = """
@page {
  size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; }
  @top-right { content: string(chapter); font-size: 8pt; color: #888; }
}
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 9.6pt; line-height: 1.42; color: #1d1d1f; }
h1 { string-set: chapter content(); font-size: 19pt; color: #0b4f8a; border-bottom: 2.5px solid #0b4f8a; padding-bottom: 4px; margin: 0 0 8px; }
h2 { font-size: 13.5pt; color: #fff; background: #0b4f8a; padding: 4px 8px; margin: 18px 0 8px; border-radius: 3px; page-break-after: avoid; }
h3 { font-size: 11pt; color: #0b4f8a; border-bottom: 1px solid #c9d8e8; padding-bottom: 2px; margin: 14px 0 6px; page-break-after: avoid; }
p, li { margin: 3px 0; }
ul, ol { padding-left: 18px; margin: 3px 0 6px; }
strong { color: #8a1c1c; }
hr { border: 0; border-top: 1px dashed #bbb; margin: 14px 0; }
blockquote { margin: 8px 0; padding: 6px 10px; background: #fff7e0; border-left: 4px solid #e0a800; border-radius: 2px; }
blockquote p { margin: 2px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.6pt; page-break-inside: avoid; }
th { background: #0b4f8a; color: #fff; text-align: left; padding: 4px 6px; }
td { padding: 3px 6px; border-bottom: 1px solid #dde5ee; vertical-align: top; }
tr:nth-child(even) td { background: #f3f7fb; }
td strong, th strong { color: inherit; }
p:has(> img) { text-align: center; margin: 10px 0 2px; page-break-inside: avoid; page-break-after: avoid; }
img { max-width: 70%; max-height: 72mm; }
p:has(> img) + p > em:only-child { display: block; text-align: center; font-size: 8.4pt; color: #444; margin-bottom: 10px; }
"""


LIST_ITEM = re.compile(r"^( *)([-*+]|\d+\.) ")


def normalize_lists(text: str) -> str:
    """Make python-markdown render lists the way GitHub does.

    - A list right after a paragraph/bold line gets a blank line before it
      (otherwise the bullets are merged into the paragraph).
    - Nested items are re-indented to 4 spaces per level, so 2-space nesting
      is not flattened.
    """
    out, stack, prev, top_kind = [], [], "", None
    for line in text.split("\n"):
        m = LIST_ITEM.match(line)
        if m:
            indent = len(m.group(1))
            if not prev.strip() or not (LIST_ITEM.match(prev) or prev.startswith(" ")):
                stack = []
            while stack and indent < stack[-1]:
                stack.pop()
            if not stack or indent > stack[-1]:
                stack.append(indent)
            level = len(stack) - 1
            if prev.strip() and level == 0 and not LIST_ITEM.match(prev) and not prev.startswith(" "):
                out.append("")
            elif level == 0 and top_kind is not None and top_kind != m.group(2)[0].isdigit() and prev.strip():
                out.append("")  # numbered <-> bulleted switch: start a separate list
            if level == 0:
                top_kind = m.group(2)[0].isdigit()
            line = " " * (4 * level) + line[indent:]
            # "- >100" would otherwise become a blockquote inside the list item
            line = re.sub(r"^(\s*(?:[-*+]|\d+\.) )>", r"\1\\>", line)
        elif line.strip() and not line.startswith(" "):
            stack, top_kind = [], None
            # a table directly under a text line is otherwise rendered as plain text
            if line.lstrip().startswith("|") and prev.strip() and not prev.lstrip().startswith("|"):
                out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def build(md_path: Path) -> Path:
    body = markdown.markdown(normalize_lists(md_path.read_text(encoding="utf-8")), extensions=["tables", "sane_lists"])
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    OUT.mkdir(exist_ok=True)
    html_path = OUT / (md_path.stem + ".html")
    pdf_path = OUT / (md_path.stem + ".pdf")
    html_path.write_text(html, encoding="utf-8")
    # base_url so relative image paths (images/ch04/...) resolve against revision_notes/
    subprocess.run(["weasyprint", "--base-url", str(NOTES) + "/", str(html_path), str(pdf_path)], check=True)
    html_path.unlink()
    return pdf_path


def main():
    names = sys.argv[1:]
    if names:
        paths = [NOTES / (n if n.endswith(".md") else n + ".md") for n in names]
    else:
        paths = sorted(NOTES.glob("Chapter_*.md"))
    for p in paths:
        print(f"✓ {build(p).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
