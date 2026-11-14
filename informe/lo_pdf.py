"""Exporta el .docx a PDF con LibreOffice actualizando antes el índice (campo TOC).

Uso: python informe/lo_pdf.py entrada.docx salida.pdf
"""
import subprocess
import sys
import time
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def main(src: str, dst: str) -> None:
    pipe = "tfvm_lo_pipe"
    proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--norestore",
                             f"--accept=pipe,name={pipe};urp;"])
    try:
        resolver = uno.getComponentContext().ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", uno.getComponentContext())
        for _ in range(60):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.5)
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(Path(src).resolve().as_uri(), "_blank", 0, (prop("Hidden", True),))
        idx = doc.getDocumentIndexes()
        for i in range(idx.getCount()):
            idx.getByIndex(i).update()
        doc.refresh()
        doc.storeToURL(Path(dst).resolve().as_uri(), (prop("FilterName", "writer_pdf_Export"),))
        doc.close(True)
    finally:
        proc.terminate()
        proc.wait(timeout=30)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
