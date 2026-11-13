"""Extraccion de texto de la hoja de vida (PDF o Word)."""
import re
from pathlib import Path


def texto_de_cv(ruta: str | Path) -> str:
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        texto = "\n".join(p.extract_text() or "" for p in PdfReader(ruta).pages)
    elif ext == ".docx":
        from docx import Document
        doc = Document(ruta)
        partes = [p.text for p in doc.paragraphs]
        for tabla in doc.tables:  # muchos CV usan tablas para el diseno
            for fila in tabla.rows:
                partes.append(" | ".join(c.text for c in fila.cells))
        texto = "\n".join(partes)
    elif ext in (".txt", ".md"):
        texto = ruta.read_text(encoding="utf-8")
    else:
        raise ValueError(f"Formato no soportado: {ext} (use PDF, DOCX o TXT)")

    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto).strip()
    if len(texto) < 200:
        raise ValueError("Se extrajo muy poco texto. Si el PDF es una imagen escaneada, envie el Word.")
    return texto
