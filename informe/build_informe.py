"""Arma el informe final (Word editable + PDF) a partir de la documentación del repo.

Estructura = documentación obligatoria de la consigna; las capturas van como anexo.

    python informe/build_informe.py            # genera informe/TF_Vaca_Muerta_Boyer.docx y .pdf

Requiere pandoc, mmdc (Mermaid CLI) y LibreOffice (soffice).
"""
from __future__ import annotations

import glob
import re
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "informe"
BUILD = OUT / "build"
NAME = "TF_Vaca_Muerta_Boyer"

CHAPTERS = [
    ("introduccion", OUT / "00_introduccion.md"),
    ("arquitectura", DOCS / "arquitectura.md"),
    ("despliegue", DOCS / "despliegue.md"),
    ("endpoints", DOCS / "endpoints.md"),
    ("resultados", DOCS / "resultados.md"),
    ("reflexion", DOCS / "reflexion.md"),
]
BLUE = RGBColor(0x1F, 0x3A, 0x5F)


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


# ---------------------------------------------------------------- Markdown
def render_mermaid(text: str, stem: str) -> str:
    """Reemplaza cada bloque ```mermaid por una imagen PNG."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        src = BUILD / f"{stem}_{n}.mmd"
        png = BUILD / f"{stem}_{n}.png"
        # En el informe el diagrama va de arriba hacia abajo: en horizontal queda ilegible al ancho de la página.
        src.write_text(m.group(1).replace("flowchart LR", "flowchart TB", 1), encoding="utf-8")
        run(["mmdc", "-i", str(src), "-o", str(png), "-w", "2000", "-b", "white", "-s", "2"])
        return f"![Diagrama de arquitectura]({png.as_posix()}){{height=21cm}}\n"

    return re.sub(r"```mermaid\n(.*?)```\n", repl, text, flags=re.S)


def adapt(text: str, stem: str) -> str:
    text = render_mermaid(text, stem)
    # Referencias entre documentos del repo -> capítulos del informe.
    for doc, chap in [("resultados", "Resultados"), ("reflexion", "Reflexión"), ("arquitectura", "Arquitectura"),
                      ("despliegue", "Despliegue"), ("endpoints", "Endpoints"), ("evidencias", "Anexo")]:
        text = re.sub(rf"`?(?:docs/)?{doc}\.md`?", chap, text)
    # Imágenes relativas a docs/.
    text = re.sub(r"!\[([^\]]*)\]\((img/[^)]+)\)", lambda m: f"![{m.group(1)}]({(DOCS / m.group(2)).as_posix()}){{width=15cm}}", text)
    return text


def anexo() -> str:
    """Anexo de evidencias a partir de docs/evidencias.md (filas con captura .png)."""
    lines = (DOCS / "evidencias.md").read_text(encoding="utf-8").splitlines()
    out = ["# Anexo: evidencias de la ejecución", "",
           "Capturas de la ejecución real, agrupadas con la misma lógica que los entregables del Laboratorio 8 "
           "(CI, Terraform y despliegue, pods y `/predict`, Grafana, carga y escalado, estado final) más las propias "
           "del trabajo (datos, entrenamiento, k3d). Son un respaldo de lo informado en los capítulos anteriores, "
           "no una sección aparte del análisis.", ""]
    section = None
    for line in lines:
        if line.startswith("## "):
            section = line[3:].strip()
            out += [f"## {section}", ""]
            continue
        m = re.match(r"\|\s*([0-9][^|]*?)\s*\|\s*`?(capturas/[^|`]+?)`?\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$", line)
        if not m:
            continue
        num, path, desc, _ = m.groups()
        if "…" in path:  # 7.9b1…3_*.png
            files = sorted(glob.glob(str(DOCS / "capturas" / (num.split("–")[0][:-1] + "*.png"))))
        else:
            files = [str(DOCS / path)] if (DOCS / path).suffix == ".png" and (DOCS / path).exists() else []
        for i, f in enumerate(files):
            label = num if len(files) == 1 else Path(f).name.split("_")[0]
            out += [f"![]({Path(f).as_posix()}){{width=16cm}}", "",
                    f"**Evidencia {label}.** {desc if i == 0 else '(continuación)'}", ""]
    return "\n".join(out) + "\n"


def build_markdown() -> Path:
    parts = []
    for stem, path in CHAPTERS:
        text = path.read_text(encoding="utf-8")
        parts.append(adapt(text, stem))
    parts.append(anexo())
    meta = """---
title: "Plataforma de estimación de producción de pozos shale de Vaca Muerta con Machine Learning en Google Cloud"
subtitle: "Trabajo Final · Diplomatura en Inteligencia Artificial · Infraestructura para IA"
author: "Calvin Boyer (trabajo individual)"
date: "Noviembre de 2026"
lang: es-AR
toc-title: "Índice"
---

"""
    md = BUILD / f"{NAME}.md"
    md.write_text(meta + "\n\n".join(parts), encoding="utf-8")
    return md


# ---------------------------------------------------------------- Word
def reference_docx() -> Path:
    ref = BUILD / "reference.docx"
    run(["pandoc", "-o", str(ref), "--print-default-data-file", "reference.docx"])
    doc = Document(ref)
    byname = {s.name: s for s in doc.styles}
    for name in ("Normal", "Body Text", "First Paragraph", "Compact"):
        if name in byname:
            byname[name].font.name = "Calibri"
            byname[name].font.size = Pt(10.5)
    for name, size in (("Title", 24), ("Subtitle", 14), ("Heading 1", 18), ("Heading 2", 14),
                       ("Heading 3", 12), ("Heading 4", 11)):
        s = byname[name]
        s.font.name = "Calibri"
        s.font.size = Pt(size)
        s.font.bold = name != "Subtitle"
        s.font.color.rgb = BLUE
    byname["Heading 1"].paragraph_format.page_break_before = True
    byname["Verbatim Char"].font.size = Pt(8.5)
    doc.save(ref)
    return ref


def postprocess(docx_path: Path) -> None:
    doc = Document(docx_path)
    # Tablas con bordes finos, encabezado sombreado y letra algo menor.
    for t in doc.tables:
        tbl_pr = t._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), "4")
            e.set(qn("w:color"), "A6B4C8")
            borders.append(e)
        tbl_pr.append(borders)
        for r_i, row in enumerate(t.rows):
            for cell in row.cells:
                if r_i == 0:
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:val"), "clear")
                    shd.set(qn("w:fill"), "E8EEF6")
                    cell._tc.get_or_add_tcPr().append(shd)
                for p in cell.paragraphs:
                    for run_ in p.runs:
                        run_.font.size = Pt(9)
    # Imágenes centradas.
    for p in doc.paragraphs:
        if p._p.xpath(".//w:drawing"):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next = True  # la imagen no se separa de su epígrafe
    doc.save(docx_path)


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    md = build_markdown()
    ref = reference_docx()
    docx_path = OUT / f"{NAME}.docx"
    run(["pandoc", str(md), "-o", str(docx_path), "--toc", "--toc-depth=2", f"--reference-doc={ref}",
         f"--resource-path={DOCS}", "--from", "markdown+pipe_tables+grid_tables-implicit_figures"])
    postprocess(docx_path)
    # PDF con el índice actualizado (soffice --convert-to deja el campo TOC vacío).
    run(["python3", str(OUT / "lo_pdf.py"), str(docx_path), str(OUT / f"{NAME}.pdf")])
    print("Listo:", docx_path, OUT / f"{NAME}.pdf")


if __name__ == "__main__":
    main()
