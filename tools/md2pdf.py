"""Convertit les documents Markdown du projet en PDF (reportlab, sous-ensemble de Markdown).

Gère : titres #/##/###, paragraphes, listes à puces (imbriquées) et numérotées, tableaux, blocs de code,
citations >, séparateurs ---, et en ligne **gras**, *italique*, `code`.
Usage : python tools/md2pdf.py docs/METHODE.md [autres.md ...] [--out docs/pdf]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, Preformatted, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

FONTS_DIR = Path("C:/Windows/Fonts")


def register_fonts() -> tuple[str, str]:
    """Polices TrueType (Unicode : ≤ → Σ « ») si disponibles, sinon Helvetica/Courier."""
    try:
        pdfmetrics.registerFont(TTFont("Body", str(FONTS_DIR / "arial.ttf")))
        pdfmetrics.registerFont(TTFont("Body-B", str(FONTS_DIR / "arialbd.ttf")))
        pdfmetrics.registerFont(TTFont("Body-I", str(FONTS_DIR / "ariali.ttf")))
        pdfmetrics.registerFont(TTFont("Body-BI", str(FONTS_DIR / "arialbi.ttf")))
        pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-B", italic="Body-I", boldItalic="Body-BI")
        pdfmetrics.registerFont(TTFont("Mono", str(FONTS_DIR / "consola.ttf")))
        return "Body", "Mono"
    except Exception:  # noqa: BLE001 - hors Windows : polices standard (caractères spéciaux possiblement absents)
        return "Helvetica", "Courier"


BODY, MONO = register_fonts()
ACCENT = colors.HexColor("#2f5d3a")
S = {
    "h1": ParagraphStyle("h1", fontName=BODY, fontSize=20, leading=24, spaceAfter=8, textColor=ACCENT),
    "h2": ParagraphStyle("h2", fontName=BODY, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6,
                         textColor=ACCENT),
    "h3": ParagraphStyle("h3", fontName=BODY, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName=BODY, fontSize=10, leading=14, spaceAfter=6, alignment=TA_LEFT),
    "quote": ParagraphStyle("quote", fontName=BODY, fontSize=9.5, leading=13.5, leftIndent=10, borderPadding=6,
                            backColor=colors.HexColor("#f1f5f0"), borderColor=colors.HexColor("#9bb59f"),
                            borderWidth=0, spaceBefore=4, spaceAfter=10),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=8.8, leading=11.5, leftIndent=6, borderPadding=6,
                           backColor=colors.HexColor("#f4f4f4"), spaceBefore=2, spaceAfter=10),
    "cell": ParagraphStyle("cell", fontName=BODY, fontSize=8.6, leading=11),
    "cellh": ParagraphStyle("cellh", fontName=BODY, fontSize=8.6, leading=11, textColor=colors.white),
}


def inline(text: str) -> str:
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = re.split(r"(`[^`]+`)", t)  # le code en ligne n'est pas interprété
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f'<font name="{MONO}" size="9">{p[1:-1]}</font>')
            continue
        p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        p = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", p)
        out.append(p)
    return "".join(out)


def bullet_par(text: str, level: int, mark: str) -> Paragraph:
    st = ParagraphStyle(f"li{level}{mark}", parent=S["p"], leftIndent=14 + 14 * level, bulletIndent=4 + 14 * level,
                        spaceAfter=3)
    return Paragraph(inline(text), st, bulletText=mark)


def col_widths(rows: list[list[str]], width: float) -> list[float]:
    """Chaque colonne a au moins la largeur de son mot le plus long (aucun mot coupé) ; le reste de la largeur
    est réparti selon la longueur du texte."""
    pad = 8.0
    ncol = len(rows[0])

    def plain(c: str) -> str:
        return re.sub(r"[*`]", "", c)

    mins = [max(pdfmetrics.stringWidth(w, BODY, 8.6) for r in rows for w in plain(r[j]).split() or [""]) + pad
            for j in range(ncol)]
    want = [max(min(len(plain(r[j])), 60) for r in rows) + 4 for j in range(ncol)]
    spare = max(0.0, width - sum(mins))
    extra = [spare * w / sum(want) for w in want]
    return [m + e for m, e in zip(mins, extra)]


def make_table(rows: list[list[str]], width: float) -> Table:
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    data = [[Paragraph(inline(c), S["cellh"] if i == 0 else S["cell"]) for c in r] for i, r in enumerate(rows)]
    tbl = Table(data, colWidths=col_widths(rows, width), repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f6")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3ca")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def convert(md: str, width: float) -> list:
    lines = md.splitlines()
    story: list = []
    para: list[str] = []
    i = 0

    def flush():
        if para:
            story.append(Paragraph(inline(" ".join(para)), S["p"]))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            story.append(Preformatted("\n".join(lines[i + 1:j]), S["code"]))
            i = j + 1
            continue
        if not s:
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            flush()
            story.append(Paragraph(inline(m.group(2)), S["h" + str(len(m.group(1)))]))
            i += 1
            continue
        if s == "---":
            flush()
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#c9d3ca"),
                                    spaceBefore=4, spaceAfter=8))
            i += 1
            continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                r = split_row(lines[i])
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in r):
                    rows.append(r)
                i += 1
            story += [make_table(rows, width), Spacer(1, 8)]
            continue
        if s.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            story.append(Paragraph("<br/>".join(inline(x) for x in q), S["quote"]))
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", line)
        if m:
            flush()
            level = len(m.group(1)) // 2
            mark = "•" if m.group(2) in "-*" else m.group(2)
            text = m.group(3)
            # lignes de continuation (indentées, ni puce ni vide)
            while (i + 1 < len(lines) and lines[i + 1].startswith(" ") and lines[i + 1].strip()
                   and not re.match(r"^\s*([-*]|\d+\.)\s+", lines[i + 1])):
                i += 1
                text += " " + lines[i].strip()
            story.append(bullet_par(text, level, mark))
            i += 1
            continue
        para.append(s)
        i += 1
    flush()
    return story


def build_pdf(src: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / (src.stem + ".pdf")
    md = src.read_text(encoding="utf-8")
    title = next((ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")), src.stem)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, f"Champignons Radar — {title}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(dst), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=title, author="Champignons Radar")
    story = convert(md, doc.width)
    # un titre ne reste pas seul en bas de page
    grouped: list = []
    for f in story:
        if grouped and isinstance(grouped[-1], Paragraph) and grouped[-1].style.name in ("h2", "h3"):
            grouped[-1] = KeepTogether([grouped[-1], f])
        else:
            grouped.append(f)
    doc.build(grouped, onFirstPage=footer, onLaterPages=footer)
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("docs/pdf"))
    a = ap.parse_args(argv)
    for f in a.files:
        print(build_pdf(f, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
