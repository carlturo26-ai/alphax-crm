"""
hemograma_parser.py — Extrae marcadores clínicos de PDFs e imágenes de hemogramas / exámenes de laboratorio.

Soporta:
  - PDFs con texto digital (PyMuPDF / fitz, pdfplumber)
  - PDFs protegidos con contraseña
  - PDFs escaneados e imágenes (PNG, JPG) usando OCR con pytesseract (fallback)

Marcadores extraídos (deportistas de resistencia):
  1. hemoglobin (g/dL)
  2. vcm (fL)
  3. chcm (g/dL)
  4. rbc (x10⁶/μL)
  5. hematocrit (%)
  6. ferritin (ng/mL)
"""

import re
import io
from datetime import datetime

SPANISH_MONTHS = {
    "ene": "01", "enero": "01",
    "feb": "02", "febrero": "02",
    "mar": "03", "marzo": "03",
    "abr": "04", "abril": "04",
    "may": "05", "mayo": "05",
    "jun": "06", "junio": "06",
    "jul": "07", "julio": "07",
    "ago": "08", "agosto": "08",
    "sep": "09", "sept": "09", "septiembre": "09", "setiembre": "09",
    "oct": "10", "octubre": "10",
    "nov": "11", "noviembre": "11",
    "dic": "12", "diciembre": "12"
}


# ═══════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE TEXTO (PDF / IMÁGENES)
# ═══════════════════════════════════════════════════════════════════

def extract_text_from_pdf(file_bytes: bytes, password: str = None) -> str:
    """
    Extrae texto de un PDF probando PyMuPDF (fitz) con ordenamiento visual y pdfplumber.
    Si no hay texto reconocible, intenta OCR si pytesseract está disponible.
    """
    text_fitz = _extract_with_fitz(file_bytes, password)
    if text_fitz and len(text_fitz.strip()) > 30:
        return text_fitz

    text_plumber = _extract_with_pdfplumber(file_bytes, password)
    if text_plumber and len(text_plumber.strip()) > 30:
        return text_plumber

    # Fallback: OCR para PDF escaneado
    return _extract_with_ocr_pdf(file_bytes, password)


def extract_text_from_image(file_bytes: bytes) -> str:
    """Extrae texto de una imagen (PNG, JPG, TIFF, BMP, WEBP) usando pytesseract."""
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


def _extract_with_fitz(file_bytes: bytes, password: str = None) -> str:
    """Extrae texto ordenado visualmente línea por línea con PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            if password:
                auth_res = doc.authenticate(password)
                if not auth_res:
                    return "[ERROR] Contraseña incorrecta para el PDF."
            else:
                return "[ERROR] PDF protegido con contraseña. Ingresa la clave."

        pages_text = []
        for page in doc:
            t = page.get_text("text", sort=True)
            if t and t.strip():
                pages_text.append(t)
        doc.close()
        return "\n\n".join(pages_text)
    except Exception:
        return ""


def _extract_with_pdfplumber(file_bytes: bytes, password: str = None) -> str:
    """Extrae texto con pdfplumber usando layout visual."""
    try:
        import pdfplumber
        kwargs = {}
        if password:
            kwargs["password"] = password
        with pdfplumber.open(io.BytesIO(file_bytes), **kwargs) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text(layout=True)
                if t and t.strip():
                    pages_text.append(t)
            return "\n\n".join(pages_text)
    except Exception:
        return ""


def _extract_with_ocr_pdf(file_bytes: bytes, password: str = None) -> str:
    """Renderiza páginas de PDF a imágenes y aplica OCR."""
    try:
        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            if password:
                auth_res = doc.authenticate(password)
                if not auth_res:
                    return "[ERROR] Contraseña incorrecta para el PDF."
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
    except Exception as e:
        return f"[ERROR] El documento escaneado requiere OCR (tesseract): {e}"


# ═══════════════════════════════════════════════════════════════════
#  PARSER CLÍNICO DE HEMOGRAMA Y FERRITINA
# ═══════════════════════════════════════════════════════════════════

def _parse_number(s: str) -> float:
    """Convierte cadenas numéricas como '14,7' o '14.7' a float."""
    clean_s = s.replace(",", ".").strip()
    return float(clean_s)


def _extract_date(text: str) -> str:
    """
    Busca la fecha del examen (muestra / solicitud / informe / recepción / validación).
    Excluye estrictamente la fecha de nacimiento (años < 2010 o etiquetas de Nacimiento).
    """
    if not text:
        return None

    # Prioridad 1: Buscar etiquetas prioritarias de toma de muestra / solicitud / informe / cargo / res
    label_patterns = [
        r"(?:fecha\s+(?:de\s+)?(?:toma(?:\s+de\s+muestra)?|muestra|solicitud|recepcion|recepción|ingreso|procesamiento|informe|emision|emisión|validacion|validación|resultado|cargo|res|reserva|examen|proceso|atencion|atención)|f\.?\s*(?:toma|muestra|solicitud|recep|ingreso|informe|cargo|res)|fecha\s*:)[\s\S]{0,120}?(\d{1,4})[/\-\.](\d{1,2})[/\-\.](\d{1,4})",
    ]
    for pat in label_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            g1, g2, g3 = m.group(1), m.group(2), m.group(3)
            try:
                if len(g1) == 4:
                    y, m_num, d = g1, int(g2), int(g3)
                elif len(g3) == 4:
                    y, m_num, d = g3, int(g2), int(g1)
                else:
                    y = f"20{g3}" if len(g3) == 2 else g3
                    m_num, d = int(g2), int(g1)
                if 2010 <= int(y) <= 2035 and 1 <= m_num <= 12 and 1 <= d <= 31:
                    return f"{y}-{m_num:02d}-{d:02d}"
            except Exception:
                pass

    # Prioridad 2: Filtrar líneas de Nacimiento o fechas sueltas directamente bajo Nacimiento
    lines = text.split("\n")
    filtered_lines = []
    skip_next = False
    for line in lines:
        if re.search(r"(nacimiento|f\.?\s*nac|fecha\s+nac|born|dob)", line, re.IGNORECASE):
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            if re.match(r"^\s*\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\s*$", line):
                continue
        filtered_lines.append(line)
    text_no_dob = "\n".join(filtered_lines)

    # Buscar fecha textual en español: ej. "06 de Mayo de 2026"
    m_text = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-zA-ZáéíóúÁÉÍÓÚ]{3,10})\s+(?:de\s+)?(\d{4})", text_no_dob, re.IGNORECASE)
    if m_text:
        d_str, mon_str, y_str = m_text.group(1), m_text.group(2).lower(), m_text.group(3)
        if mon_str in SPANISH_MONTHS and int(y_str) >= 2010:
            return f"{y_str}-{SPANISH_MONTHS[mon_str]}-{int(d_str):02d}"

    # Prioridad 3: Fechas numéricas genéricas en texto filtrado (excluyendo años < 2010)
    for m in re.finditer(r"\b(\d{1,4})[/\-\.](\d{1,2})[/\-\.](\d{1,4})\b", text_no_dob):
        g1, g2, g3 = m.group(1), m.group(2), m.group(3)
        try:
            if len(g1) == 4:
                y, m_num, d = g1, int(g2), int(g3)
            elif len(g3) == 4:
                y, m_num, d = g3, int(g2), int(g1)
            elif len(g3) == 2:
                y, m_num, d = f"20{g3}", int(g2), int(g1)
            else:
                continue
            if 2010 <= int(y) <= 2035 and 1 <= m_num <= 12 and 1 <= d <= 31:
                return f"{y}-{m_num:02d}-{d:02d}"
        except Exception:
            pass

    return None


def _extract_patient_name(text: str) -> str:
    """Extrae el nombre del paciente si se encuentra explícitamente."""
    if not text:
        return None

    name_patterns = [
        r"(?:PACIENTE|Paciente|USUARIO|Usuario|NOMBRE(?:\s+DEL\s+PACIENTE)?|Nombre(?:\s+del\s+paciente)?)[:\s]+([A-ZÁÉÍÓÚÑa-záéíóúñ\s,]{4,50}?)(?=\n|\r|\s{2,}|Edad|EDAD|Empresa|EMPRESA|Identificación|IDENTIFICACIÓN|ID|CC|Documento|Sexo|SEXO|FECHA|Fecha|$)",
    ]

    for pat in name_patterns:
        m = re.search(pat, text)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 4 and not re.search(r"hemograma|examen|laboratorio|resultado", candidate, re.I):
                return candidate.upper()
    return None


_MARKER_PATTERNS = [
    (
        "hemoglobin",
        [
            r"(?:hemoglobina(?:\s+total)?|hb\s+total|hgb|hb)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
            r"(?:hgb|hb)\b[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
        ],
        "g_dl"
    ),
    (
        "vcm",
        [
            r"(?:promedio\s+volumen\s+corpuscular|volumen\s+corpuscular(?:\s+medio|\s+media)?|vol\.\s*corp\.\s*(?:medio|media)?|vcm|mcv|v\.?c\.?m\.?|m\.?c\.?v\.?)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
            r"(?:v\.?c\.?m\.?|m\.?c\.?v\.?)[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
        ],
        "fl"
    ),
    (
        "chcm",
        [
            r"(?:promedio\s+concentraci[oó]n(?:\s+(?:de\s+)?hb\.?|\s+(?:de\s+)?hemoglobina)?(?:\s+corpuscular)?(?:\s+media)?|concentraci[oó]n\s+(?:media\s+de\s+|de\s+)?(?:hemoglobina|hb\.?)\s+corpuscular(?:\s+media)?|concentraci[oó]n\s+corpuscular\s+(?:de\s+)?(?:hemoglobina|hb\.?)(?:\s+media)?|conc\.\s*(?:media\s*)?(?:de\s*)?hb\.?\s*corp\.?(?:uscular)?(?:\s*media)?|chcm|mchc|ccmh|chmc|c\.?h\.?c\.?m\.?|c\.?h\.?m\.?c\.?|m\.?c\.?h\.?c\.?)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
            r"concentraci[oó]n\s+media\s+de\s+hb[\s\S]{0,30}?(\d{2}[\.,]\d{1,2})",
        ],
        "g_dl"
    ),
    (
        "rbc",
        [
            r"(?:recuento|conteo)\s+(?:de\s+)?(?:eritrocitos|gl[oó]bulos\s+rojos|hemat[ií]es)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
            r"(?:gl[oó]bulos\s+rojos|eritrocitos|hemat[ií]es|rbc|conteo\s+g\.r\.|g\.r\.)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
        ],
        "million_ul"
    ),
    (
        "hematocrit",
        [
            r"(?:hematocrito|hto|hct|h\.?t\.?o\.?|h\.?c\.?t\.?)\b(?:\s*\([^)]*\))?[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
            r"volumen\s+hematocrito[\s:;\-\.\)\n]+(\d+(?:[\.,]\d+)?)",
        ],
        "percent"
    ),
    (
        "ferritin",
        [
            r"(?:ferritina(?:\s+s[eé]rica|\s+plasm[aá]tica)?|ferritin)\b[\s\S]{0,800}?(\d+(?:[\.,]\d+)?)(?:\s*[\(\[]?\s*\d+(?:[\.,]\d+)?\s*[\-\–\—\:]\s*\d+(?:[\.,]\d+)?\s*[\)\]]?)?\s*(?:ng/ml|ug/l|mcg/l|microg/l)",
            r"ferritina\b[^\d\n]*?(\d+(?:[\.,]\d+)?)",
        ],
        "ng_ml"
    ),
    (
        "ck",
        [
            r"(?:creatin(?:a)?\s*kinasa(?:\s+total)?|creatin(?:a)?\s*quinasa(?:\s+total)?|creatinquinasa|creatinakinasa|ck\s+total|cpk\s+total|ck|cpk)\b[\s\S]{0,40}?(\d+(?:[\.,]\d+)?)",
            r"(?:ck|cpk)\b[^\d\n]*?(\d+(?:[\.,]\d+)?)",
        ],
        "u_l"
    ),
    (
        "vitamin_b12",
        [
            r"(?:vitamina\s+b-?12|vit\.?\s*b-?12|b12|cobalamina)\b[\s\S]{0,40}?(\d+(?:[\.,]\d+)?)",
            r"(?:vitamina\s+b12|vit\.\s*b12|cobalamina)\b[^\d\n]*?(\d+(?:[\.,]\d+)?)",
        ],
        "pg_ml"
    ),
    (
        "folic_acid",
        [
            r"(?:[aá]cido\s+f[oó]lico(?:\s+s[eé]rico)?|folato(?:s)?(?:\s+s[eé]ricos?)?|vitamina\s+b9|folic\s+acid)\b[\s\S]{0,40}?(\d+(?:[\.,]\d+)?)",
            r"(?:folato|folatos|[aá]cido\s+f[oó]lico)\b[^\d\n]*?(\d+(?:[\.,]\d+)?)",
        ],
        "ng_ml"
    ),
]


def parse_hemograma(text: str) -> dict:
    """
    Analiza el texto extraído del documento y extrae los marcadores sanguíneos.
    """
    result = {
        "hemoglobin": None,
        "vcm": None,
        "chcm": None,
        "rbc": None,
        "hematocrit": None,
        "ferritin": None,
        "ck": None,
        "vitamin_b12": None,
        "folic_acid": None,
        "date": None,
        "patient_name": None,
        "raw_text": text or "",
        "markers_found": 0,
        "markers_total": 9,
    }

    if not text or text.startswith("[ERROR]"):
        return result

    # ── Fecha y Paciente ─────────────────────────────────────────
    result["date"] = _extract_date(text)
    result["patient_name"] = _extract_patient_name(text)

    # ── Limpieza previa para regularizar espacios y saltos ───────
    cleaned_text = re.sub(r"[Pp][áa]ginas?\s*\d+(?:\s*(?:/|de)\s*\d+)?", "", text)
    cleaned_text = re.sub(r"Tel[ée]fonos?[:\s\d\.\-,]+", "", cleaned_text)
    
    # ── Extracción de marcadores ─────────────────────────────────
    found = 0
    for key, patterns, unit_type in _MARKER_PATTERNS:
        for pat in patterns:
            m = re.search(pat, cleaned_text, flags=re.IGNORECASE)
            if m:
                try:
                    val = _parse_number(m.group(1))

                    # Normalización específica por unidad
                    if key == "hemoglobin" and val > 30.0:
                        val = val / 10.0  # de g/L a g/dL (ej. 154 -> 15.4)

                    if key == "rbc" and val > 20.0:
                        val = val / 100.0 # ej. 512 x10^4 -> 5.12

                    result[key] = round(val, 2)
                    found += 1
                    break
                except (ValueError, IndexError):
                    continue

    # Fallback global para Ferritina si está en otra página / sección desalineada
    if result["ferritin"] is None and re.search(r"\bferritin", text, re.IGNORECASE):
        m_fer = re.search(r"(\d+(?:[\.,]\d+)?)(?:\s*[\(\[]?\s*\d+(?:[\.,]\d+)?\s*[\-\–\—\:]\s*\d+(?:[\.,]\d+)?\s*[\)\]]?)?\s*(?:ng/mL|ng/ml|ug/L|ug/l|mcg/L|microg/L)", text, re.IGNORECASE)
        if m_fer:
            try:
                result["ferritin"] = round(_parse_number(m_fer.group(1)), 2)
                found += 1
            except Exception:
                pass

    result["markers_found"] = found
    return result


def process_file(file_bytes: bytes, filename: str, password: str = None) -> dict:
    """
    Punto de entrada principal para procesar cualquier archivo subido (PDF o Imagen).
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        text = extract_text_from_pdf(file_bytes, password)
    elif ext in ("png", "jpg", "jpeg", "tiff", "bmp", "webp"):
        text = extract_text_from_image(file_bytes)
    else:
        text = f"[ERROR] Formato no soportado: .{ext}"

    return parse_hemograma(text)
