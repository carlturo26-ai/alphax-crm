"""
hemograma_parser.py — Extrae marcadores clínicos de PDFs e imágenes de hemogramas.

Soporta:
  - PDFs con texto (pdfplumber)
  - PDFs protegidos con contraseña
  - PDFs escaneados / imágenes (PyMuPDF render + pytesseract OCR)

Marcadores extraídos:
  hemoglobin, vcm, chcm, rbc, hematocrit, ferritin
"""

import re
import io
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════
#  TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════

def extract_text_from_pdf(file_bytes: bytes, password: str = None) -> str:
    """
    Extrae texto de un PDF.  Intenta pdfplumber primero (más preciso para
    PDFs con texto), y si el texto está vacío recurre a OCR vía PyMuPDF.
    """
    text = _extract_with_pdfplumber(file_bytes, password)
    if text and len(text.strip()) > 30:
        return text

    # Fallback: render a imagen y OCR
    return _extract_with_ocr(file_bytes, password)


def extract_text_from_image(file_bytes: bytes) -> str:
    """Extrae texto de una imagen (PNG, JPG) usando pytesseract."""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(img, lang="spa")
        return text
    except ImportError:
        return "[ERROR] Pillow o pytesseract no están instalados."
    except Exception as e:
        return f"[ERROR] OCR falló: {e}"


# ── Helpers internos ─────────────────────────────────────────────

def _extract_with_pdfplumber(file_bytes: bytes, password: str = None) -> str:
    try:
        import pdfplumber
        kwargs = {}
        if password:
            kwargs["password"] = password
        with pdfplumber.open(io.BytesIO(file_bytes), **kwargs) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            return "\n\n".join(pages_text)
    except Exception as e:
        return f"[ERROR] pdfplumber: {e}"


def _extract_with_ocr(file_bytes: bytes, password: str = None) -> str:
    """Renderiza cada página del PDF a imagen y aplica OCR."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            if password:
                doc.authenticate(password)
            else:
                return "[ERROR] PDF protegido con contraseña. Ingresa la clave."

        all_text = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang="spa")
            if text:
                all_text.append(text)
        doc.close()
        return "\n\n".join(all_text)
    except ImportError:
        return "[ERROR] PyMuPDF o pytesseract no están instalados."
    except Exception as e:
        return f"[ERROR] OCR-PDF falló: {e}"


# ═══════════════════════════════════════════════════════════════════
#  MARKER PARSING
# ═══════════════════════════════════════════════════════════════════

# Cada entrada: (clave_destino, [patrones regex], factor_conversión)
# Los patrones buscan: NOMBRE ... VALOR numérico
# factor_conversión se usa si la unidad requiere escalar (normalmente 1.0)

_MARKER_PATTERNS = [
    (
        "hemoglobin",
        [
            r"[Hh]emoglobina\s+(\d+[\.,]\d+)",
            r"(?:HGB|Hb)\s+(\d+[\.,]\d+)",
        ],
        1.0,
    ),
    (
        "vcm",
        [
            r"[Vv]olumen\s+[Cc]orpuscular\s+[Mm]edio\s*\(?(?:VCM|MCV)?\)?\s+(\d+[\.,]\d+)",
            r"(?:VCM|MCV)\s+(\d+[\.,]\d+)",
            r"Promedio\s+Volumen\s+Corpuscular\s*\(VCM\)\s+(\d+[\.,]\d+)",
        ],
        1.0,
    ),
    (
        "chcm",
        [
            r"[Pp]romedio\s+[Cc]oncentraci[oó]n\s*\(MCHC\)\s+(\d+[\.,]\d+)",
            r"(?:CHCM|MCHC|CCMH)\s+(\d+[\.,]\d+)",
            r"[Cc]oncentraci[oó]n\s+(?:de\s+)?[Hh]emoglobina\s+[Cc]orpuscular\s+(?:[Mm]edia\s+)?(\d+[\.,]\d+)",
        ],
        1.0,
    ),
    (
        "rbc",
        [
            r"[Rr]ecuento\s+de\s+[Ee]ritrocitos\s+(\d+[\.,]\d+)",
            r"(?:RBC|Eritrocitos)\s+(\d+[\.,]\d+)",
            r"[Gg]l[oó]bulos\s+[Rr]ojos\s+(\d+[\.,]\d+)",
        ],
        1.0,
    ),
    (
        "hematocrit",
        [
            r"[Hh]ematocrito\s+(\d+[\.,]\d+)",
            r"(?:HCT|Hto)\s+(\d+[\.,]\d+)",
        ],
        1.0,
    ),
    (
        "ferritin",
        [
            r"[Ff]erritina\s+(\d+[\.,]\d+)",
            r"[Ff]erritin\s+(\d+[\.,]\d+)",
        ],
        1.0,
    ),
]


def _parse_number(s: str) -> float:
    """Convierte '14,7' o '14.7' a float."""
    return float(s.replace(",", "."))


def parse_hemograma(text: str) -> dict:
    """
    Busca los 6 marcadores clínicos en el texto extraído.

    Retorna:
        {
            "hemoglobin": 14.7,
            "vcm": 94.2,
            ...
            "ferritin": None,  # si no se encontró
            "date": "2020-12-10",  # fecha detectada o None
            "patient_name": "CARLOS ARTURO ZULUAGA GOMEZ",
            "raw_text": "...",
            "markers_found": 5,
            "markers_total": 6,
        }
    """
    result = {
        "hemoglobin": None,
        "vcm": None,
        "chcm": None,
        "rbc": None,
        "hematocrit": None,
        "ferritin": None,
        "date": None,
        "patient_name": None,
        "raw_text": text,
        "markers_found": 0,
        "markers_total": 6,
    }

    if not text or text.startswith("[ERROR]"):
        return result

    # ── Extraer fecha ────────────────────────────────────────────
    # Formatos comunes: dd/mm/yyyy, yyyy-mm-dd
    date_patterns = [
        (r"[Ff]echa\s+(?:de\s+)?[Ii]ngreso[:\s]+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", "dmy"),
        (r"[Ff]echa[:\s]+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", "dmy"),
        (r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", "ymd"),
        (r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", "dmy"),
    ]
    for pat, fmt in date_patterns:
        m = re.search(pat, text)
        if m:
            try:
                if fmt == "dmy":
                    result["date"] = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
                else:
                    result["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                break
            except Exception:
                pass

    # ── Extraer nombre del paciente ──────────────────────────────
    name_match = re.search(r"[Pp]aciente[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\s{2,}|Empresa|Identificación|$)", text)
    if name_match:
        result["patient_name"] = name_match.group(1).strip()

    # ── Extraer marcadores ───────────────────────────────────────
    found = 0
    for key, patterns, factor in _MARKER_PATTERNS:
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                try:
                    val = _parse_number(m.group(1)) * factor
                    result[key] = round(val, 2)
                    found += 1
                    break
                except (ValueError, IndexError):
                    continue

    result["markers_found"] = found
    return result


# ═══════════════════════════════════════════════════════════════════
#  CONVENIENCE: Process any uploaded file
# ═══════════════════════════════════════════════════════════════════

def process_file(file_bytes: bytes, filename: str, password: str = None) -> dict:
    """
    Punto de entrada principal.  Detecta tipo de archivo y extrae marcadores.

    Args:
        file_bytes: contenido del archivo en bytes
        filename: nombre del archivo (para detectar extensión)
        password: contraseña del PDF (opcional)

    Returns:
        dict con marcadores y metadata (ver parse_hemograma)
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        text = extract_text_from_pdf(file_bytes, password)
    elif ext in ("png", "jpg", "jpeg", "tiff", "bmp", "webp"):
        text = extract_text_from_image(file_bytes)
    else:
        text = f"[ERROR] Formato no soportado: .{ext}"

    return parse_hemograma(text)
