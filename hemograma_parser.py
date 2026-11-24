"""
hemograma_parser.py — Motor híbrido de extracción clínica de alta precisión para exámenes de sangre.

Optimizado para deportistas de resistencia (AlphaX CRM) y para los principales laboratorios
de Colombia (SURA, Dinámica, Colsanitas, Synlab, Idime, San Vicente Fundación, Unilab,
Colcan, Cafam, Compensar, Colsubsidio, Higuera Escalante, Cruz Roja, etc.).

Características:
  1. Motor Híbrido Dual:
     - Motor IA Multimodal (Google Gemini) cuando hay API Key configurada en el club:
       lee PDFs nativos (incluso recién desbloqueados con contraseña) y fotos/capturas de celular
       entendiendo tablas complejas, unidades y fechas con precisión clínica.
     - Motor Local Avanzado (PyMuPDF + pdfplumber + OCR Tesseract con corrección EXIF y
       preprocesamiento de contraste) como respaldo automático y validación cruzada.
  2. Detección proactiva de PDFs protegidos con contraseña (`is_pdf_encrypted`).
  3. Soporte nativo para carga múltiple (`process_multiple_files`): une varios PDFs o varias
     fotos del mismo examen en un solo registro consolidado.
  4. Prevención estricta contra confusiones entre analitos y rangos de referencia:
     - Hemoglobina vs HbA1c vs HCM vs CHCM
     - Colesterol Total vs HDL vs LDL vs VLDL vs No-HDL
     - Creatina Kinasa (CK/CPK) vs Creatinina vs CK-MB
     - Ferritina vs Hierro sérico vs Transferrina
     - Glucemia basal vs HbA1c vs Postprandial
     - PCR Ultrasensible vs Proteínas totales
  5. Validación de plausibilidad fisiológica y reconciliación de Wintrobe (Hto = VCM * RBC / 10).
"""

import base64
import io
import json
import os
import re
import unicodedata
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None


ALL_BLOODWORK_KEYS = [
    "hemoglobin",
    "vcm",
    "chcm",
    "rbc",
    "hematocrit",
    "ferritin",
    "ck",
    "vitamin_b12",
    "folic_acid",
    "total_cholesterol",
    "hdl",
    "ldl",
    "triglycerides",
    "glucose",
    "hba1c",
    "pcr_us",
]


def _strip_accents(s: str) -> str:
    """Elimina tildes para comparación insensible a acentos: ej. Hematócrito -> Hematocrito."""
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


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
    "dic": "12", "diciembre": "12",
}


# ═══════════════════════════════════════════════════════════════════
#  DETECCIÓN PROACTIVA DE PDF ENCRIPTADO
# ═══════════════════════════════════════════════════════════════════

def is_pdf_encrypted(file_bytes: bytes) -> bool:
    """Detecta rápidamente si un PDF requiere contraseña para abrirse."""
    if not file_bytes:
        return False
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        encrypted = bool(doc.is_encrypted and doc.needs_pass)
        doc.close()
        return encrypted
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as _:
            return False
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg or "decrypt" in msg:
            return True
    return False


def validate_pdf_password(file_bytes: bytes, password: str) -> bool:
    """Verifica si la contraseña abre correctamente un PDF encriptado."""
    if not file_bytes:
        return False
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if not doc.is_encrypted or not doc.needs_pass:
            doc.close()
            return True
        ok = bool(password and doc.authenticate(password))
        doc.close()
        return ok
    except Exception:
        return False


def _decrypt_pdf_bytes(file_bytes: bytes, password: str = None):
    """
    Si el PDF está encriptado y la contraseña es válida, retorna bytes sin contraseña.
    Retorna (bytes_desencriptados, error_str_o_None).
    """
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted and doc.needs_pass:
            if not password:
                doc.close()
                return None, "[ERROR] PDF protegido con contraseña. Ingresa la clave del archivo."
            if not doc.authenticate(password):
                doc.close()
                return None, "[ERROR] Contraseña incorrecta para el PDF. Verifica la clave e intenta de nuevo."
            unlocked = doc.tobytes()
            doc.close()
            return unlocked, None
        doc.close()
        return file_bytes, None
    except Exception:
        return file_bytes, None


# ═══════════════════════════════════════════════════════════════════
#  LIMPIEZA DE RUIDO INSTITUCIONAL Y MULTI-PÁGINA
# ═══════════════════════════════════════════════════════════════════

NOISE_LINE_PATTERNS = [
    r"^\s*=+\s*page\s*\d+\s*=+\s*$",
    r"^\s*p[aá]ginas?\s*:?\s*\d+(?:\s*(?:/|de)\s*\d+)?.*$",
    r"^\s*p[aá]g\.?\s*:?\s*\d+.*$",
    r"^\s*hojas?\s*\d+\s*(?:/|de)\s*\d+.*$",
    r"^\s*laboratorio\s+cl[ií]nico.*$",
    r"^\s*informe\s+de\s+resultados.*$",
    r"^\s*san\s*vicente\s+fundaci[oó]n.*$",
    r"^\s*hospital\s+universitario.*$",
    r"^\s*(?:calle|carrera|cra\.?|cll\.?|av\.?|avenida|vereda|km\.?)\s+\d+.*$",
    r"^\s*(?:sede|direcci[oó]n|tel[eé]fonos?|pbx|correo\s+electr[oó]nico|e-?mail|nit|rut|resoluci[oó]n|habilitaci[oó]n|www\.)\b.*$",
    r"^\s*contin[uú]a\s+en\s+la\s+siguiente\s+p[aá]gina.*$",
    r"^\s*fin\s+del\s+informe.*$",
    r"^\s*(?:paciente|identificaci[oó]n|documento|c\.?c\.?|historia(?:\s+cl[ií]nica)?|c[oó]digo|orden|n[°º]?\s*orden|edad|sexo|g[eé]nero|estado\s+civil|servicio|cama|episodio|entidad|empresa|sucursal)\b.*$",
    r"^\s*(?:m[eé]dico(?:\s+tratante)?|solicitante|eps|convenio|plan)\s*:.*$",
    r"^\s*fecha(?:\s+(?:de\s+)?[a-záéíóú\s\-]+)?\s*[:\s].*$",
    r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}.*$",
    r"^\s*(?:validado\s+por|responsable|bacteri[oó]log[oa]?|profesional|registro\s+m[eé]dico|cargo)\s*:.*$",
    r"^\s*\d{6,12}\s*-\s*bacteri[oó]log.*$",
    r"^\s*(?:analito|examen|prueba|estudio|determinaci[oó]n)\s+(?:resultado|valor)\b.*$",
    r"^\s*(?:m[eé]todo|metodolog[ií]a|t[eé]cnica(?:\s+estudio)?|tipo\s+de\s+muestra|muestra)\s*:.*$",
    r"^\s*procesado\s+por\b.*$",
    r"^\s*valores?\s+de\s+referencia\s+(?:ajustados|de\s+hemoglobina\s+en\s+embarazadas).*$",
]


def _clean_document_noise(text: str) -> str:
    """Elimina encabezados, pies de página y metadatos repetitivos que rompen la lectura entre páginas."""
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_noise = False
        for pat in NOISE_LINE_PATTERNS:
            if re.match(pat, stripped, flags=re.IGNORECASE):
                is_noise = True
                break
        if not is_noise:
            cleaned.append(stripped)
    return "\n".join(cleaned)


def _strip_reference_ranges(text_segment: str) -> str:
    """
    Elimina rangos de referencia (ej. '39.00 - 51.00', '14.0 A 18.0', '[30 - 200]', '(13.0 - 17.0)',
    'Página 2/12') para evitar que un rango o número de página sea tomado como resultado del paciente.
    """
    if not text_segment:
        return ""
    # 0. Eliminar indicadores de página tipo 'Página 2/12' o '2/12'
    cleaned = re.sub(r"p[aá]g(?:ina)?\.?\s*\d+\s*(?:/|de)\s*\d+", " ", text_segment, flags=re.IGNORECASE)
    # 1. Rangos con guión o palabra 'a' / 'hasta': ej. "39.00 - 51.00", "14.0 A 18.0", "22 - 322"
    cleaned = re.sub(
        r"(?:\d+(?:[\.,]\d+)?)\s*(?:[-–—]|\ba\b|\bhasta\b)\s*(?:\d+(?:[\.,]\d+)?)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    # 2. Rangos entre paréntesis o corchetes: ej. "(13.00 - 17.00)", "(< 200)", "[30 - 200]"
    cleaned = re.sub(
        r"[\(\[]\s*(?:>|<|>=|<=)?\s*\d+(?:[\.,]\d+)?(?:\s*(?:[-–—]|\ba\b)\s*\d+(?:[\.,]\d+)?)?\s*[\)\]]",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    # 3. Etiquetas de referencia tipo 'VALOR DE REFERENCIA: ...' hasta final de línea
    cleaned = re.sub(
        r"(?:valor(?:es)?\s+de\s+referencia|val\.?\s*ref\.?|v\.?r\.?|ref(?:erencia)?)\s*[:=]?\s*.*$",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _stitch_multipage_text(pages: list) -> str:
    """
    Une páginas de un PDF resolviendo el corte de página donde el nombre del examen
    queda al final de la página N y el resultado numérico al inicio de la página N+1.
    """
    if not pages:
        return ""
    if len(pages) == 1:
        return pages[0]

    stitched_pages = [_clean_document_noise(p) for p in pages]
    combined = []
    for i, curr_clean in enumerate(stitched_pages):
        if i == 0:
            combined.append(curr_clean)
            continue

        prev_lines = [l.strip() for l in stitched_pages[i - 1].splitlines() if l.strip()]
        curr_lines = [l.strip() for l in curr_clean.splitlines() if l.strip()]

        if prev_lines and curr_lines:
            last_line = prev_lines[-1]
            first_line = curr_lines[0]
            if not re.search(r"\d", last_line) and len(last_line) < 80:
                if re.match(r"^(?:>|<|>=|<=)?\s*\d+(?:[\.,]\d+)?", first_line):
                    combined.append(f"{last_line} : {first_line}")

        combined.append(curr_clean)

    return "\n".join(combined)


# ═══════════════════════════════════════════════════════════════════
#  PREPROCESAMIENTO DE IMÁGENES (EXIF + CONTRASTE PARA FOTOS MÓVILES)
# ═══════════════════════════════════════════════════════════════════

def _prepare_image_bytes(file_bytes: bytes, max_dim: int = 2200) -> tuple:
    """
    Corrige orientación EXIF (fotos verticales de iPhone/Android), convierte a RGB
    y redimensiona si es demasiado grande.
    Retorna (PIL.Image, jpeg_bytes).
    """
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / float(max(w, h))
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    rgb_img = img.convert("RGB") if img.mode != "RGB" else img
    rgb_img.save(buf, format="JPEG", quality=88)
    return img, buf.getvalue()


def _enhance_image_for_ocr(img):
    """Preprocesa una imagen para maximizar la precisión de Tesseract en fotos de celular."""
    from PIL import ImageEnhance, ImageOps

    gray = ImageOps.grayscale(img)
    # Escalar si es pequeña para mejorar lectura de decimales
    w, h = gray.size
    if max(w, h) < 1500:
        scale = min(2.0, 1800.0 / float(max(w, h)))
        gray = gray.resize((int(w * scale), int(h * scale)), resample=3)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.5)
    gray = ImageEnhance.Sharpness(gray).enhance(1.4)
    return gray


# ═══════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE TEXTO LOCAL (PDF / IMÁGENES / OCR)
# ═══════════════════════════════════════════════════════════════════

def extract_text_from_pdf(file_bytes: bytes, password: str = None) -> tuple:
    """
    Extrae texto de un PDF combinando PyMuPDF y pdfplumber.
    Retorna (texto_primario, texto_layout_alternativo).
    Si el documento es escaneado, aplica OCR Tesseract como rescate.
    """
    unlocked_bytes, err = _decrypt_pdf_bytes(file_bytes, password)
    if err:
        return err, ""

    text_fitz, pages_fitz = _extract_with_fitz_pages(unlocked_bytes, password=None)
    text_plumber = _extract_with_pdfplumber(unlocked_bytes, password=None)

    len_fitz = len(text_fitz.strip()) if text_fitz and not text_fitz.startswith("[ERROR]") else 0
    len_plumber = len(text_plumber.strip()) if text_plumber else 0

    if len_fitz > 30 or len_plumber > 30:
        # Preservar el texto original para extracción de fecha/paciente, y ofrecer versión unida
        stitched = _stitch_multipage_text(pages_fitz) if pages_fitz else text_fitz
        primary_text = text_fitz if len_fitz >= len_plumber else text_plumber
        alt_text = stitched if stitched and len(stitched.strip()) > 30 else text_plumber
        if text_plumber and text_plumber != primary_text and alt_text != text_plumber:
            alt_text = alt_text + "\n\n" + text_plumber
        return primary_text, alt_text

    # Fallback: OCR para PDF escaneado (foto o imagen dentro de PDF)
    ocr_text = _extract_with_ocr_pdf(unlocked_bytes, password=None)
    return ocr_text, ""


def extract_text_from_image(file_bytes: bytes) -> str:
    """Extrae texto de una imagen (PNG, JPG, TIFF, BMP, WEBP) corrigiendo EXIF y aplicando OCR."""
    try:
        import pytesseract

        img, _ = _prepare_image_bytes(file_bytes)
        enhanced = _enhance_image_for_ocr(img)

        best_text = ""
        for candidate_img in (enhanced, img):
            for cfg in ("--psm 6", "--psm 4", ""):
                try:
                    t = (
                        pytesseract.image_to_string(candidate_img, lang="spa", config=cfg)
                        if cfg
                        else pytesseract.image_to_string(candidate_img, lang="spa")
                    )
                except Exception:
                    try:
                        t = pytesseract.image_to_string(candidate_img, config=cfg) if cfg else pytesseract.image_to_string(candidate_img)
                    except Exception:
                        t = ""
                if t and len(t.strip()) > len(best_text.strip()):
                    best_text = t
        return best_text
    except ImportError:
        return "[ERROR] Pillow o pytesseract no están instalados."
    except Exception as e:
        return f"[ERROR] OCR falló: {e}"


def _extract_with_fitz_pages(file_bytes: bytes, password: str = None) -> tuple:
    """Extrae texto página por página con PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted and doc.needs_pass:
            if password:
                if not doc.authenticate(password):
                    doc.close()
                    return "[ERROR] Contraseña incorrecta para el PDF.", []
            else:
                doc.close()
                return "[ERROR] PDF protegido con contraseña. Ingresa la clave.", []

        pages_text = []
        for page in doc:
            t = page.get_text("text", sort=True)
            if t and t.strip():
                pages_text.append(t)
        doc.close()
        full_text = "\n\n".join(pages_text)
        return full_text, pages_text
    except Exception:
        return "", []


def _extract_with_pdfplumber(file_bytes: bytes, password: str = None) -> str:
    """Extrae texto con pdfplumber preservando alineación visual de columnas."""
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
    """Renderiza páginas de PDF a imágenes y aplica OCR con Tesseract."""
    try:
        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted and doc.needs_pass:
            if password:
                if not doc.authenticate(password):
                    doc.close()
                    return "[ERROR] Contraseña incorrecta para el PDF."
            else:
                doc.close()
                return "[ERROR] PDF protegido con contraseña. Ingresa la clave."

        all_text = []
        for page in doc:
            img_to_ocr = None
            img_list = page.get_images()
            if img_list:
                try:
                    xref = img_list[0][0]
                    base_image = doc.extract_image(xref)
                    img_to_ocr = Image.open(io.BytesIO(base_image["image"]))
                except Exception:
                    img_to_ocr = None

            if img_to_ocr is None:
                pix = page.get_pixmap(dpi=220)
                img_to_ocr = Image.open(io.BytesIO(pix.tobytes("png")))

            enhanced = _enhance_image_for_ocr(img_to_ocr)
            page_text = ""
            for cand_img in (enhanced, img_to_ocr):
                for config_str in ("--psm 6", ""):
                    try:
                        t = (
                            pytesseract.image_to_string(cand_img, lang="spa", config=config_str)
                            if config_str
                            else pytesseract.image_to_string(cand_img, lang="spa")
                        )
                        if t and len(t.strip()) > len(page_text.strip()):
                            page_text = t
                    except Exception:
                        try:
                            t = pytesseract.image_to_string(cand_img)
                            if t and len(t.strip()) > len(page_text.strip()):
                                page_text = t
                        except Exception:
                            pass

            if page_text:
                all_text.append(page_text)
        doc.close()
        return "\n\n".join(all_text)
    except Exception as e:
        return f"[ERROR] El documento escaneado requiere OCR (tesseract): {e}"


# ═══════════════════════════════════════════════════════════════════
#  MOTOR IA MULTIMODAL (GOOGLE GEMINI — PDFs Y FOTOS DE CELULAR)
# ═══════════════════════════════════════════════════════════════════

def _get_gemini_api_key_safe() -> str:
    """Obtiene la API Key de Gemini del club de forma segura sin fallar fuera de Streamlit."""
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from wolfy_bot import get_gemini_api_key
        k = get_gemini_api_key()
        if k:
            return k.strip()
    except Exception:
        pass
    return ""


GEMINI_BLOODWORK_PROMPT = """Eres un médico especialista en laboratorio clínico deportivo (Colombia y Latinoamérica).
Extrae ÚNICAMENTE los resultados REALES DEL PACIENTE (NUNCA los valores de referencia ni rangos normales ni códigos de examen ni números de página) del examen de sangre adjunto.

Devuelve EXCLUSIVAMENTE un objeto JSON válido con estas claves exactas (usa null si el biomarcador NO aparece en el documento):
{
  "date": "YYYY-MM-DD o null (fecha de toma de muestra, solicitud o resultado; NUNCA fecha de nacimiento)",
  "patient_name": "string o null",
  "lab_name": "string o null (nombre del laboratorio si aparece)",
  "hemoglobin": number o null,       // g/dL (si está en g/L divide por 10). NO confundir con HbA1c ni HCM ni CHCM.
  "vcm": number o null,              // fL (Volumen Corpuscular Medio / MCV / PVC). NO confundir con VPM (plaquetario) ni RDW.
  "chcm": number o null,             // g/dL (Concentración Media de Hb Corpuscular / MCHC / CHMC). NUNCA usar HCM (MCH en pg).
  "rbc": number o null,              // millones/uL (×10⁶/μL). Si está en millones completos ej. 5.140.000 -> 5.14; si está en miles ej. 5140 -> 5.14.
  "hematocrit": number o null,       // % (ej. 44.6). Si está en proporción 0.446 -> 44.6.
  "ferritin": number o null,         // ng/mL (ug/L). NO confundir con hierro sérico ni transferrina.
  "ck": number o null,               // U/L (Creatina Kinasa / CPK Total). NUNCA confundir con Creatinina (~0.7-1.3 mg/dL) ni CK-MB.
  "vitamin_b12": number o null,      // pg/mL (si está en pmol/L multiplicar por 1.355).
  "folic_acid": number o null,       // ng/mL (si está en nmol/L dividir por 2.266).
  "total_cholesterol": number o null,// mg/dL (Colesterol Total; si está en mmol/L multiplicar por 38.67). NO confundir con HDL/LDL/VLDL/No-HDL.
  "hdl": number o null,              // mg/dL (Colesterol HDL).
  "ldl": number o null,              // mg/dL (Colesterol LDL).
  "triglycerides": number o null,    // mg/dL (Triglicéridos; si está en mmol/L multiplicar por 88.57).
  "glucose": number o null,          // mg/dL (Glucosa / Glicemia basal en sangre; NO postprandial ni en orina; si está en mmol/L multiplicar por 18.01).
  "hba1c": number o null,            // % (Hemoglobina Glicosilada HbA1c; rango típico 4.0-14.0%).
  "pcr_us": number o null            // mg/L (Proteína C Reactiva Ultra Sensible / cuantitativa; si está en mg/dL multiplicar por 10. Si dice '< 0.5' usa 0.5. NO usar resultados cualitativos negativo/positivo ni proteínas totales).
}

Reglas críticas:
- Si un biomarcador no aparece en el documento, devuelve null. NUNCA inventes ni estimes valores.
- Revisa cuidadosamente las columnas: Resultado del paciente vs Valor de referencia. Extrae solo el RESULTADO DEL PACIENTE.
- Verifica coherencia: Hematocrito ≈ VCM * RBC / 10; Colesterol Total > HDL.
- Usa punto decimal (ej. 15.9, nunca comas).
"""


def _extract_with_gemini_ai(file_bytes: bytes, mime_type: str, fallback_text: str = "") -> dict:
    """
    Envía el PDF o imagen directamente a Gemini Multimodal para extracción JSON estructurada.
    Retorna dict con los campos extraídos, o None si no hay API key o falla.
    """
    if requests is None:
        return None
    api_key = _get_gemini_api_key_safe()
    if not api_key:
        return None

    parts = []
    # Límite seguro para inline_data (~14MB)
    if file_bytes and len(file_bytes) <= 14 * 1024 * 1024:
        b64 = base64.b64encode(file_bytes).decode("ascii")
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": b64,
            }
        })
        parts.append({"text": GEMINI_BLOODWORK_PROMPT})
    elif fallback_text and len(fallback_text.strip()) > 25:
        parts.append({
            "text": GEMINI_BLOODWORK_PROMPT + "\n\nTEXTO DEL EXAMEN:\n" + fallback_text[:30000]
        })
    else:
        return None

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024,
        },
    }

    models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-flash-latest",
    ]

    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=28,
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    continue
                res_parts = candidates[0].get("content", {}).get("parts", [])
                if not res_parts or "text" not in res_parts[0]:
                    continue
                raw_json = res_parts[0]["text"].strip()
                # Limpiar posibles bloques ```json ... ```
                raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.IGNORECASE)
                raw_json = re.sub(r"\s*```$", "", raw_json)
                parsed_json = json.loads(raw_json)
                if isinstance(parsed_json, dict):
                    return parsed_json
            elif resp.status_code in (400, 403):
                # Clave inválida o sin permiso; no reintentar todos los modelos
                return None
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════
#  HELPERS DE PARSEO CLÍNICO LOCAL
# ═══════════════════════════════════════════════════════════════════

def _clean_val_str(s: str) -> float:
    """Limpia cadenas como '14,7', '48.4' o '5.050.000' y convierte a float."""
    clean = str(s).strip()
    if clean.count(".") > 1:
        clean = clean.replace(".", "")
    elif clean.count(",") > 1:
        clean = clean.replace(",", "")
    else:
        clean = clean.replace(",", ".")
    return float(clean)


def _extract_date(text: str) -> str:
    """
    Busca la fecha del examen (muestra / solicitud / informe / recepción / validación).
    Excluye estrictamente fechas de nacimiento (años < 2010 o etiquetas de Nacimiento).
    """
    if not text:
        return None

    # Prioridad 1: Etiquetas prioritarias de toma de muestra / solicitud / informe / resultado
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

    # Prioridad 2: Filtrar líneas de Nacimiento
    lines = text.splitlines()
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

    # Prioridad 3: Fechas numéricas genéricas en texto filtrado
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
            if len(candidate) > 4 and not re.search(r"hemograma|examen|laboratorio|resultado|quimica", candidate, re.I):
                return candidate.upper()
    return None


# ═══════════════════════════════════════════════════════════════════
#  ESPECIFICACIÓN COMPLETA DE LOS 16 BIOMARCADORES
# ═══════════════════════════════════════════════════════════════════

MARKER_SPECS = [
    # ── 1. SERIE ROJA Y TRANSPORTE DE OXÍGENO ──────────────────────
    (
        "hemoglobin",
        [
            r"hemoglobina\s+total",
            r"hemoglobina\s*\(?\s*(?:hb|hgb)\s*\)?",
            r"cuadro\s+hem[aá]tico\s*[-:]?\s*hemoglobina",
            r"hemograma\s*[-:]?\s*hemoglobina",
            r"hemoglobina\s+automatizada",
            r"hemoglobina\s+en\s+sangre(?:\s+total)?",
            r"hemoglobina(?!\s*(?:glicosilada|glucosilada|glicada|a1c|corpuscular|media|hcm|chcm|fetal|retic))",
            r"\b(?:hb|hgb)\s+total\b",
            r"\b(?:hb|hgb)\b(?!\s*(?:glicosilada|glucosilada|glicada|a1c|corpuscular|media|hcm|chcm|retic))",
        ],
        r"(?:glicosilada|glucosilada|glicada|a1c|corpuscular|hcm|chcm|fetal|retic|embarazadas)",
        (7.5, 22.0),
        lambda v, u: v / 10.0 if (v > 30.0 or "g/l" in u.lower()) else v,
    ),
    (
        "vcm",
        [
            r"volumen\s+corpuscular\s+medio(?:\s*\(?\s*vcm\s*\)?)?",
            r"volumen\s+corpuscular\s+media",
            r"vol\.?\s*corp\.?\s*medio",
            r"promedio\s+(?:de\s+)?volumen\s+corpuscular(?:\s*\(?\s*(?:pvc|vcm)\s*\)?)?",
            r"pvc\s*\(?\s*promedio\s+(?:de\s+)?volumen\s+corpuscular\s*\)?",
            r"\bp\.?v\.?c\.?\b\s*\(?.*?\)?",
            r"promedio\s+vol\.?\s*corp\.?",
            r"\bvolumen\s+corpuscular\b(?!\s*(?:media\s*de\s*hb|concentraci))",
            r"\bp\.?v\.?c\.?\b",
            r"\b(?:v\.?c\.?m\.?|m\.?c\.?v\.?)\b",
        ],
        r"(?:\bhcm\b|\bchcm\b|\brdw\b|\bide\b|\bade\b|plaquetario|\bvpm\b)",
        (60.0, 125.0),
        lambda v, u: v,
    ),
    (
        "chcm",
        [
            r"concentraci[oó]n\s+(?:de\s+)?hemoglobina\s+corpuscular\s+media(?:\s*\(?\s*chcm\s*\)?)?",
            r"concentraci[oó]n\s+corpuscular\s+media\s+(?:de\s+)?(?:hemoglobina|hb)",
            r"concentraci[oó]n\s+media\s+de\s+(?:hemoglobina|hb)(?:\s+corpuscular)?",
            r"concentraci[oó]n\s+corpuscular\s+media",
            r"conc\.?\s*(?:de\s*)?hb\.?\s*corp\.?\s*media",
            r"conc\.?\s*corp\.?\s*media\s*(?:de\s*)?hb",
            r"promedio\s+concentraci[oó]n\s+(?:de\s+)?hb\.?\s+corp\.?(?:\s+media)?",
            r"prom\.?\s*concentraci[oó]n\s+(?:de\s+)?(?:hemoglobina|hb)(?:\s+corpuscular|\s+corpus)?",
            r"promedio\s+concentraci[oó]n\s*\(?\s*mchc\s*\)?",
            r"\b(?:c\.?h\.?c\.?m\.?|m\.?c\.?h\.?c\.?|ccmh|chmc)\b",
        ],
        r"(?:^\s*hcm\b|^\s*mch\b|\bpg\b)",
        (26.0, 42.0),
        lambda v, u: v / 10.0 if (v > 100.0 or "g/l" in u.lower()) else v,
    ),
    (
        "rbc",
        [
            r"recuento\s+(?:de\s+)?(?:gl[oó]bulos\s+rojos|eritrocitos|hemat[ií]es)",
            r"conteo\s+(?:de\s+)?(?:gl[oó]bulos\s+rojos|eritrocitos|hemat[ií]es)",
            r"conteo\s+eritrocitario",
            r"serie\s+roja\s*[-:]?\s*(?:gl[oó]bulos\s+rojos|eritrocitos|hemat[ií]es)",
            r"gl[oó]bulos\s+rojos",
            r"eritrocitos",
            r"hemat[ií]es",
            r"\b(?:r\.?b\.?c\.?|g\.?r\.?|conteo\s+g\.r\.|recuento\s+g\.r\.)\b",
        ],
        r"(?:blancos|leucocitos|plaquetas|reticulocitos|vsg|sedimentacion|amplitud|distribucion)",
        (2.5, 7.5),
        lambda v, u: (
            v / 1000000.0 if v > 1000000.0
            else (v / 1000.0 if v > 2000.0 else (v / 100.0 if v > 200.0 else (v / 10.0 if v > 20.0 else v)))
        ),
    ),
    (
        "hematocrit",
        [
            r"hemat[oó]crito(?:\s*\(?\s*(?:hto|hct)\s*\)?)?",
            r"hemat[oó]crito\s+total",
            r"volumen\s+hemat[oó]crito",
            r"cuadro\s+hem[aá]tico\s*[-:]?\s*hemat[oó]crito",
            r"hemograma\s*[-:]?\s*hemat[oó]crito",
            r"\b(?:h\.?t\.?o\.?|h\.?c\.?t\.?)\b",
        ],
        r"(?:indice|relaci[oó]n)",
        (24.0, 65.0),
        lambda v, u: v * 100.0 if v < 1.0 else v,
    ),
    (
        "ferritin",
        [
            r"ferritina(?:\s+s[eé]rica|\s+plasm[aá]tica|\s+en\s+suero|\s+cuantitativa|\s+total)?",
            r"ferritina\s+s[eé]rica\s+cuantitativa",
            r"ferritina\s+en\s+suero\s+por\s+quimioluminiscencia",
            r"dosaje\s+de\s+ferritina",
            r"\bferritin\b",
        ],
        r"(?:hierro|transferrina|fijaci[oó]n|saturaci[oó]n)",
        (2.0, 2500.0),
        lambda v, u: v,
    ),

    # ── 2. DAÑO MUSCULAR Y METABOLISMO CELULAR ─────────────────────
    (
        "ck",
        [
            r"creatin(?:a)?\s*(?:quinasa|kinasa)(?:\s+total)?(?:\s*\(?\s*(?:ck|cpk)\s*\)?)?",
            r"creatin(?:a)?\s*fosfo(?:quinasa|kinasa)(?:\s+total)?",
            r"creatin(?:a)?(?:quinasa|kinasa)\s+total",
            r"creatin(?:a)?(?:quinasa|kinasa)",
            r"creatin(?:a)?\s*(?:quinasa|kinasa)\s+en\s+suero",
            r"\b(?:ck|cpk)\s+total(?:\s*\(?\s*suero\s*\)?)?\b",
            r"\b(?:ck|cpk)[-_]total\b",
            r"\bcreatine\s+kinase(?:\s+total)?\b",
            r"\b(?:ck|cpk)\s*\(?\s*nac\s*\)?\b",
            r"\b(?:ck|cpk)\b(?!\s*[-_]?\s*(?:mb|fraccion|masa))",
        ],
        r"(?:creatinina|ck[-_\s]*mb|cpk[-_\s]*mb|\bmb\b|troponina|mioglobina|clearance|depuraci[oó]n)",
        (15.0, 15000.0),
        lambda v, u: v,
    ),
    (
        "vitamin_b12",
        [
            r"vitamina\s+b[-_\s]?12(?:\s*\(?\s*(?:ciano)?cobalamina\s*\)?)?",
            r"vit\.?\s*b[-_\s]?12",
            r"cianocobalamina(?:\s+en\s+suero)?",
            r"cobalamina(?:\s+en\s+suero)?",
            r"dosaje\s+de\s+vitamina\s+b12",
            r"\bb[-_\s]?12\b",
        ],
        r"(?:\bb6\b|\bb1\b|\bb2\b|\bb9\b|vitamina\s+d|vitamina\s+c|f[oó]lico)",
        (50.0, 3000.0),
        lambda v, u: v * 1.355 if "pmol" in u.lower() else v,
    ),
    (
        "folic_acid",
        [
            r"[aá]cido\s+f[oó]lico(?:\s+s[eé]rico|\s+en\s+suero|\s+total)?",
            r"folato(?:s)?(?:\s+s[eé]ricos?|\s+en\s+suero)?",
            r"vitamina\s+b[-_\s]?9",
            r"\bfolic\s+acid\b",
            r"\bserum\s+folate\b",
        ],
        r"(?:intraeritrocitario|eritrocitario|eritrocitos)",
        (0.5, 50.0),
        lambda v, u: v / 2.266 if "nmol" in u.lower() else v,
    ),

    # ── 3. PERFIL LIPÍDICO Y CARDIOVASCULAR ────────────────────────
    (
        "total_cholesterol",
        [
            r"colesterol\s+total(?:\s+en\s+suero)?",
            r"colesterolemia\s+total",
            r"colesterol\s+en\s+suero\s+total",
            r"total\s+cholesterol",
            r"cholesterol\s+total",
            r"\bcolesterol\b(?!\s*[-_]?\s*(?:hdl|ldl|vldl|no\s*hdl|no-hdl|no_hdl|fraccion|fraccionado))",
        ],
        r"(?:\bhdl\b|\bldl\b|\bvldl\b|no[-_\s]*hdl|trigli[sc][eé]ridos)",
        (70.0, 500.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v,
    ),
    (
        "hdl",
        [
            r"colesterol\s+(?:de\s+)?alta\s+densidad(?:\s*\(?\s*hdl\s*\)?)?",
            r"colesterol\s*[-_]?\s*hdl",
            r"c[-_\.]?hdl",
            r"hdl\s*[-_]?\s*colesterol",
            r"hdl[-_\s]?c\b",
            r"hdl\s+cholesterol",
            r"high\s+density\s+lipoprotein",
            r"\bhdl\b(?!\s*[-_]?\s*(?:ratio|indice|relacion))",
        ],
        r"(?:\bldl\b|\bvldl\b|no[-_\s]*hdl|\bcolesterol\s+total\b)",
        (15.0, 160.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v,
    ),
    (
        "ldl",
        [
            r"colesterol\s+(?:de\s+)?baja\s+densidad(?:\s*\(?\s*ldl\s*\)?)?",
            r"colesterol\s*[-_]?\s*ldl",
            r"c[-_\.]?ldl",
            r"ldl\s*[-_]?\s*colesterol",
            r"ldl[-_\s]?c\b",
            r"ldl\s+(?:calculado|directo|estimado)",
            r"ldl\s+cholesterol",
            r"low\s+density\s+lipoprotein",
            r"\bldl\b(?!\s*[-_]?\s*(?:ratio|indice|relacion))",
        ],
        r"(?:\bhdl\b|\bvldl\b|no[-_\s]*hdl|\bcolesterol\s+total\b)",
        (20.0, 350.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v,
    ),
    (
        "triglycerides",
        [
            r"trigli[sc][eé]ridos?(?:\s+en\s+suero|\s+totales)?",
            r"trigli[sc]eridemia",
            r"triglycerides?",
        ],
        r"(?:colesterol)",
        (20.0, 1500.0),
        lambda v, u: v * 88.57 if "mmol" in u.lower() else v,
    ),

    # ── 4. GLUCEMIA E INFLAMACIÓN SISTÉMICA ────────────────────────
    (
        "glucose",
        [
            r"glucosa(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+s[eé]rica|\s+en\s+sangre|\s+preprandial|\s+prepandrial|\s+plasmatica|\s+ayuno)?",
            r"glicemia(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+s[eé]rica|\s+ayuno|\s+preprandial)?",
            r"glucemia(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+ayuno)?",
            r"\b(?:fasting\s+)?glucose\b",
        ],
        r"(?:glicosilada|glucosilada|glicada|a1c|post|postprandial|despues|curva|sobrecarga|orina|60\s*min|120\s*min|o'sullivan)",
        (40.0, 450.0),
        lambda v, u: v * 18.01 if "mmol" in u.lower() else v,
    ),
    (
        "hba1c",
        [
            r"hemoglobina\s+glicosilada(?:\s*\(?\s*hba1c\s*\)?)?",
            r"hemoglobina\s+glucosilada(?:\s*\(?\s*hba1c\s*\)?)?",
            r"hemoglobina\s+glicada",
            r"hemoglobina\s+glicosilada\s+a1c",
            r"hemoglobina\s+glucosilada\s+a1c",
            r"hemoglobina\s+a1c",
            r"\bhba1c(?:\s*\(?\s*(?:ngsp|ifcc)\s*\)?)?\b",
            r"\bhb[-_\s]?a1[-_\s]?c\b",
            r"\bglicohemoglobina\b",
            r"\bfracci[oó]n\s+a1c\b",
            r"\ba1c\b",
        ],
        r"(?:total)",
        (3.5, 18.0),
        lambda v, u: (v * 0.09148 + 2.152) if v > 20.0 else v,
    ),
    (
        "pcr_us",
        [
            r"prote[ií]na\s+c\s+reactiva\s+ultra\s*sensible",
            r"prote[ií]na\s+c\s+reactiva\s+de\s+alta\s+sensibilidad",
            r"prote[ií]na\s+c\s+reactiva\s+cuantitativa\s+(?:ultra\s*sensible|alta\s+sensibilidad)",
            r"pcr\s+ultra\s*sensible",
            r"pcr\s*[-_]?\s*us\b",
            r"pcr\s*[-_]?\s*as\b",
            r"hs[-_\s]?crp\b",
            r"crp[-_\s]?hs\b",
            r"high\s+sensitivity\s+c[-_\s]?reactive\s+protein",
            r"prote[ií]na\s+c\s+reactiva\s+cuantitativa(?!\s*semicuantitativa)",
            r"\bpcr\s+cuantitativa\b(?!\s*semicuantitativa)",
        ],
        r"(?:semicuantitativa|cualitativa|aglutinaci[oó]n|l[aá]tex|t[ií]tulo|diluci[oó]n|negativ[oa]|positiv[oa]|prote[ií]nas?\s+totales?|proteinuria|orina|electroforesis)",
        (0.01, 50.0),
        lambda v, u: v * 10.0 if "mg/dl" in u.lower() else v,
    ),
]

MARKER_BOUNDS = {spec[0]: spec[3] for spec in MARKER_SPECS}

# Patrón para capturar número y unidad (evitando fechas o rangos como 39.00 - 51.00 o 2/12)
VAL_UNIT_PATTERN = (
    r"(?:>|<|>=|<=)?\s*(\d+(?:[\.,]\d+)*)(?!\s*[-–—\/]\s*\d)"
    r"(?:\s*(%|g/dl|gr/dl|g/l|fl|u3|mill/mm3|mill/ul|millones/ul|x10\^6/ul|m/ul|u/l|ui/l|ng/ml|ug/l|mcg/l|pg/ml|pmol/l|mg/dl|mg/l|mmol/l))?"
)


def _extract_single_marker(text: str, current_key: str, aliases: list, exclusion_pat: str, bounds: tuple, scale_fn):
    """
    Busca un marcador usando una estrategia escalonada e insensible a tildes:
      1. Misma línea (formato tabular).
      2. Líneas 1-4 posteriores (formato vertical o informe por bloques / multi-página limpio).
      3. Proximidad en texto continuo limpio.
    """
    min_b, max_b = bounds
    lines = text.splitlines()

    # Pase 1 y 2: Línea por línea + continuación vertical
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        line_no_acc = _strip_accents(line_clean)

        for alias in aliases:
            m_clean = re.search(alias, line_clean, flags=re.IGNORECASE)
            m_noacc = None if m_clean else re.search(alias, line_no_acc, flags=re.IGNORECASE)
            m_alias = m_clean or m_noacc
            if not m_alias:
                continue

            if exclusion_pat and (
                re.search(exclusion_pat, line_clean, flags=re.IGNORECASE)
                or re.search(exclusion_pat, line_no_acc, flags=re.IGNORECASE)
            ):
                continue

            raw_after = line_clean[m_alias.end():] if m_clean else line_no_acc[m_alias.end():]
            after_alias = _strip_reference_ranges(raw_after)
            m_val = re.search(VAL_UNIT_PATTERN, after_alias, flags=re.IGNORECASE)
            if m_val:
                num_str, unit_str = m_val.group(1), m_val.group(2) or ""
                try:
                    v = _clean_val_str(num_str)
                    if not (1950 <= v <= 2040 and not unit_str):
                        v_scaled = scale_fn(v, unit_str)
                        if min_b <= v_scaled <= max_b:
                            return round(v_scaled, 2)
                except Exception:
                    pass

            # Pase 2: Disposición vertical (revisar 1 a 4 líneas siguientes)
            for offset in range(1, 5):
                if i + offset >= len(lines):
                    break
                next_line = lines[i + offset].strip()
                if not next_line:
                    continue
                next_no_acc = _strip_accents(next_line)

                # Detener si la siguiente línea pertenece a OTRO biomarcador
                hit_other = False
                for other_spec in MARKER_SPECS:
                    if other_spec[0] == current_key:
                        continue
                    for other_alias in other_spec[1]:
                        if re.search(other_alias, next_line, flags=re.IGNORECASE) or re.search(other_alias, next_no_acc, flags=re.IGNORECASE):
                            hit_other = True
                            break
                    if hit_other:
                        break
                if hit_other:
                    break

                next_no_ref = _strip_reference_ranges(next_line)
                m_val_next = re.search(VAL_UNIT_PATTERN, next_no_ref, flags=re.IGNORECASE)
                if m_val_next:
                    num_str, unit_str = m_val_next.group(1), m_val_next.group(2) or ""
                    try:
                        v = _clean_val_str(num_str)
                        if 1950 <= v <= 2040 and not unit_str:
                            continue
                        v_scaled = scale_fn(v, unit_str)
                        if min_b <= v_scaled <= max_b:
                            return round(v_scaled, 2)
                    except Exception:
                        pass

    # Pase 3: Búsqueda de proximidad en texto corrido limpio
    text_clean_no_ref = _strip_reference_ranges(text)
    text_clean_no_acc = _strip_accents(text_clean_no_ref)
    for alias in aliases:
        pat_prox = rf"(?:{alias})[^\w\n\r]{{0,60}}?(?:resultado|valor)?[:\s\-=]*{VAL_UNIT_PATTERN}"
        for target_txt in (text_clean_no_ref, text_clean_no_acc):
            for m in re.finditer(pat_prox, target_txt, flags=re.IGNORECASE):
                full_match = m.group(0)
                if exclusion_pat and re.search(exclusion_pat, full_match, flags=re.IGNORECASE):
                    continue
                num_str, unit_str = m.group(1), m.group(2) or ""
                try:
                    v = _clean_val_str(num_str)
                    if 1950 <= v <= 2040 and not unit_str:
                        continue
                    v_scaled = scale_fn(v, unit_str)
                    if min_b <= v_scaled <= max_b:
                        return round(v_scaled, 2)
                except Exception:
                    pass

    return None


def validate_marker_values(values: dict) -> tuple:
    """
    Valida valores numéricos de biomarcadores contra rangos de plausibilidad fisiológica
    y coherencia clínica cruzada.
    Retorna (dict_limpio, lista_de_advertencias).
    """
    working = dict(values or {})
    cleaned = {}
    warnings = []

    # Pre-chequeo de intercambio Colesterol Total vs HDL antes de descartar por límites
    try:
        raw_tc = float(working["total_cholesterol"]) if working.get("total_cholesterol") not in (None, "") else None
        raw_hdl = float(working["hdl"]) if working.get("hdl") not in (None, "") else None
        if raw_tc is not None and raw_hdl is not None and raw_tc < raw_hdl:
            tc_lo, tc_hi = MARKER_BOUNDS["total_cholesterol"]
            hdl_lo, hdl_hi = MARKER_BOUNDS["hdl"]
            if (tc_lo <= raw_hdl <= tc_hi) and (hdl_lo <= raw_tc <= hdl_hi):
                working["total_cholesterol"], working["hdl"] = raw_hdl, raw_tc
                warnings.append("Se ajustó un posible intercambio entre Colesterol Total y HDL.")
    except (ValueError, TypeError):
        pass

    for key in ALL_BLOODWORK_KEYS:
        raw_v = working.get(key)
        if raw_v is None or raw_v == "":
            cleaned[key] = None
            continue
        try:
            v = float(raw_v)
            if v <= 0:
                cleaned[key] = None
                continue
        except (ValueError, TypeError):
            cleaned[key] = None
            continue

        min_b, max_b = MARKER_BOUNDS[key]
        # Auto-correcciones seguras de unidades obvias
        if key == "hemoglobin" and 75.0 <= v <= 220.0:
            v = v / 10.0
        elif key == "hematocrit" and 0.24 <= v <= 0.65:
            v = v * 100.0
        elif key == "rbc" and v > 1000000.0:
            v = v / 1000000.0
        elif key == "rbc" and 2500.0 <= v <= 7500.0:
            v = v / 1000.0

        if min_b <= v <= max_b:
            cleaned[key] = round(v, 2)
        else:
            cleaned[key] = None
            warnings.append(
                f"Se descartó un valor fuera de rango fisiológico para {key}: {raw_v} (esperado {min_b}–{max_b})."
            )

    # Coherencia Colesterol Total vs HDL
    if cleaned.get("total_cholesterol") and cleaned.get("hdl"):
        if cleaned["total_cholesterol"] < cleaned["hdl"]:
            cleaned["total_cholesterol"], cleaned["hdl"] = cleaned["hdl"], cleaned["total_cholesterol"]
            warnings.append("Se ajustó un posible intercambio entre Colesterol Total y HDL.")

    # Coherencia Hemoglobina vs HbA1c
    if cleaned.get("hemoglobin") and cleaned["hemoglobin"] < 8.0:
        if not cleaned.get("hba1c"):
            cleaned["hba1c"] = cleaned["hemoglobin"]
        cleaned["hemoglobin"] = None

    # Coherencia CK vs Creatinina
    if cleaned.get("ck") and cleaned["ck"] < 15.0:
        cleaned["ck"] = None

    return cleaned, warnings


def parse_hemograma(text: str) -> dict:
    """
    Analiza el texto de un examen clínico y extrae los 16 biomarcadores sanguíneos
    garantizando que no se confundan unos con otros y aplicando coherencia fisiológica.
    """
    result = {k: None for k in ALL_BLOODWORK_KEYS}
    result.update({
        "date": None,
        "patient_name": None,
        "lab_name": None,
        "raw_text": text or "",
        "markers_found": 0,
        "markers_total": len(ALL_BLOODWORK_KEYS),
        "extraction_engine": "local",
        "warnings": [],
    })

    if not text or text.startswith("[ERROR]"):
        if text and text.startswith("[ERROR]"):
            result["extraction_engine"] = "error"
        return result

    # 1. Metadatos: Fecha y Paciente (siempre sobre el texto original)
    result["date"] = _extract_date(text)
    result["patient_name"] = _extract_patient_name(text)

    # 2. Limpieza preliminar de ruido institucional y cortes de página
    cleaned = _clean_document_noise(text)

    # 3. Extracción de cada biomarcador
    for key, aliases, exclusions, bounds, scale_fn in MARKER_SPECS:
        val = _extract_single_marker(cleaned, key, aliases, exclusions, bounds, scale_fn)
        if val is None and cleaned != text:
            val = _extract_single_marker(text, key, aliases, exclusions, bounds, scale_fn)
        if val is not None:
            result[key] = val

    # ── Fallback especializado para VCM (cuando OCR distorsiona las siglas o layout) ──
    if result["vcm"] is None:
        for line in (cleaned + "\n" + text).splitlines():
            line_c = line.strip()
            line_no_acc = _strip_accents(line_c)
            if re.search(r"(?:volumen|corpuscular|\bpvc\b|\bvcm\b|\bmcv\b|promedio\s+de\s+vol)", line_no_acc, re.IGNORECASE):
                if re.search(r"(?:plaquetario|\bvpm\b|\bhcm\b|\bchcm\b|\brdw\b|\bide\b|\bade\b|concentraci)", line_no_acc, re.IGNORECASE):
                    continue
                line_c_no_ref = _strip_reference_ranges(line_c)
                m_nums = re.findall(r"(?:>|<|>=|<=)?\s*(\d+(?:[\.,]\d+)?)", line_c_no_ref)
                for num_s in m_nums:
                    try:
                        v = _clean_val_str(num_s)
                        if 65.0 <= v <= 120.0:
                            result["vcm"] = round(v, 2)
                            break
                    except Exception:
                        pass
                if result["vcm"] is not None:
                    break

    # 4. Validaciones de coherencia clínica cruzada
    validated, warns = validate_marker_values(result)
    for k in ALL_BLOODWORK_KEYS:
        result[k] = validated[k]
    result["warnings"].extend(warns)

    # 5. Reconciliación Fisiológica Bidireccional de Hematocrito y VCM (Wintrobe: Hto = VCM * RBC / 10)
    if result["rbc"]:
        rbc_val = result["rbc"]

        if result["vcm"] and 70.0 <= result["vcm"] <= 120.0:
            expected_hto = (result["vcm"] * rbc_val) / 10.0
            if result["hematocrit"] is None or abs(result["hematocrit"] - expected_hto) > 1.2:
                found_cand = None
                for cand_match in re.finditer(r"\b(\d{2}[\.,]\d{1,2})\b", text):
                    try:
                        cand_v = _clean_val_str(cand_match.group(1))
                        if abs(cand_v - expected_hto) <= 0.6:
                            found_cand = round(cand_v, 2)
                            break
                    except Exception:
                        pass
                if found_cand is not None:
                    result["hematocrit"] = found_cand
                elif result["hematocrit"]:
                    s_hto = str(result["hematocrit"])
                    for old_char in ("5", "3"):
                        if old_char in s_hto:
                            try:
                                cand_hto = float(s_hto.replace(old_char, "8", 1))
                                if abs(cand_hto - expected_hto) <= 0.6:
                                    result["hematocrit"] = round(cand_hto, 2)
                                    break
                            except Exception:
                                pass

        if result["hematocrit"] and 30.0 <= result["hematocrit"] <= 60.0:
            expected_vcm = (result["hematocrit"] * 10.0) / rbc_val
            if result["vcm"] is None or abs(result["vcm"] - expected_vcm) > 2.5:
                for cand_match in re.finditer(r"\b(\d{2,3}[\.,]\d{1,2})\b", text):
                    try:
                        cand_v = _clean_val_str(cand_match.group(1))
                        if abs(cand_v - expected_vcm) <= 1.2:
                            result["vcm"] = round(cand_v, 2)
                            break
                    except Exception:
                        pass

    result["markers_found"] = sum(1 for k in ALL_BLOODWORK_KEYS if result[k] is not None)
    return result


def _merge_ai_and_local(ai_data: dict, local_parsed: dict) -> dict:
    """
    Combina los resultados del motor IA Multimodal con el motor local:
    - Valida los valores de IA con `validate_marker_values`.
    - Prioriza IA (entiende tablas, fotos rotadas y columnas complejas),
      y complementa con el motor local cualquier marcador que IA dejó en null.
    """
    merged = dict(local_parsed)
    if not ai_data:
        return merged

    ai_clean, ai_warns = validate_marker_values(ai_data)
    ai_found = sum(1 for k in ALL_BLOODWORK_KEYS if ai_clean.get(k) is not None)

    if ai_found == 0 and local_parsed.get("markers_found", 0) > 0:
        return merged

    for k in ALL_BLOODWORK_KEYS:
        if ai_clean.get(k) is not None:
            merged[k] = ai_clean[k]
        elif local_parsed.get(k) is not None:
            merged[k] = local_parsed[k]

    # Fecha válida desde IA
    ai_date = ai_data.get("date")
    if isinstance(ai_date, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", ai_date.strip()):
        try:
            dt = datetime.strptime(ai_date.strip(), "%Y-%m-%d")
            if 2010 <= dt.year <= 2035:
                merged["date"] = ai_date.strip()
        except Exception:
            pass

    if ai_data.get("patient_name") and isinstance(ai_data["patient_name"], str):
        merged["patient_name"] = ai_data["patient_name"].strip().upper()
    if ai_data.get("lab_name") and isinstance(ai_data["lab_name"], str):
        merged["lab_name"] = ai_data["lab_name"].strip()

    merged["warnings"] = list(dict.fromkeys((merged.get("warnings") or []) + ai_warns))
    merged["markers_found"] = sum(1 for k in ALL_BLOODWORK_KEYS if merged.get(k) is not None)
    merged["extraction_engine"] = "ia_hibrida"
    return merged


def process_file(file_bytes: bytes, filename: str, password: str = None, use_ai: bool = True) -> dict:
    """
    Punto de entrada principal para procesar cualquier archivo subido (PDF o Imagen).
    Ejecuta motor IA Multimodal (cuando hay API Key configurada) + motor local avanzado.
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        unlocked_bytes, err = _decrypt_pdf_bytes(file_bytes, password)
        if err:
            return parse_hemograma(err)

        primary_text, alt_layout_text = extract_text_from_pdf(unlocked_bytes, password=None)
        parsed = parse_hemograma(primary_text)

        if alt_layout_text and parsed["markers_found"] < len(ALL_BLOODWORK_KEYS):
            alt_parsed = parse_hemograma(alt_layout_text)
            for k in ALL_BLOODWORK_KEYS:
                if parsed[k] is None and alt_parsed.get(k) is not None:
                    parsed[k] = alt_parsed[k]
            if not parsed["date"] and alt_parsed.get("date"):
                parsed["date"] = alt_parsed["date"]
            if not parsed["patient_name"] and alt_parsed.get("patient_name"):
                parsed["patient_name"] = alt_parsed["patient_name"]
            parsed["markers_found"] = sum(1 for k in ALL_BLOODWORK_KEYS if parsed[k] is not None)

        if use_ai:
            ai_res = _extract_with_gemini_ai(unlocked_bytes, "application/pdf", fallback_text=primary_text)
            if ai_res:
                parsed = _merge_ai_and_local(ai_res, parsed)

        return parsed

    elif ext in ("png", "jpg", "jpeg", "tiff", "bmp", "webp"):
        # Preparar imagen (corrige rotación EXIF de celulares)
        jpeg_bytes = file_bytes
        mime_type = "image/jpeg"
        try:
            _, jpeg_bytes = _prepare_image_bytes(file_bytes)
        except Exception:
            mime_type = "image/png" if ext == "png" else ("image/webp" if ext == "webp" else "image/jpeg")

        ai_res = None
        if use_ai:
            ai_res = _extract_with_gemini_ai(jpeg_bytes, mime_type)

        # Ejecutar OCR local como respaldo / complemento (si IA no encontró todo o no está activa)
        if ai_res:
            ai_clean, _ = validate_marker_values(ai_res)
            ai_found = sum(1 for k in ALL_BLOODWORK_KEYS if ai_clean.get(k) is not None)
            if ai_found >= 4:
                base = parse_hemograma("")
                base["raw_text"] = "[Lectura directa con IA Multimodal sobre imagen]"
                return _merge_ai_and_local(ai_res, base)

        text = extract_text_from_image(file_bytes)
        parsed = parse_hemograma(text)
        if ai_res:
            parsed = _merge_ai_and_local(ai_res, parsed)
        return parsed

    else:
        return parse_hemograma(f"[ERROR] Formato no soportado: .{ext}. Sube un PDF o una foto JPG/PNG.")


def process_multiple_files(files_list: list, passwords=None, use_ai: bool = True) -> dict:
    """
    Procesa uno o varios archivos (ej. 2 PDFs separados o 3 fotos de un mismo examen)
    y consolida todos los biomarcadores en un único diccionario unificado.

    files_list: lista de tuplas (file_bytes, filename)
    passwords: str opcional (contraseña común) o dict {filename: password}
    """
    unified = {k: None for k in ALL_BLOODWORK_KEYS}
    unified.update({
        "date": None,
        "date_detected": False,
        "dates_found": [],
        "patient_name": None,
        "lab_name": None,
        "raw_text": "",
        "markers_found": 0,
        "markers_total": len(ALL_BLOODWORK_KEYS),
        "extraction_engine": "local",
        "per_file_results": [],
        "warnings": [],
        "errors": [],
        "filenames": [],
    })

    raw_chunks = []
    used_ai = False
    dates_seen = []

    for file_bytes, fname in files_list:
        pwd = None
        if isinstance(passwords, dict):
            pwd = passwords.get(fname) or None
        elif isinstance(passwords, str) and passwords.strip():
            pwd = passwords.strip()

        res = process_file(file_bytes, fname, password=pwd, use_ai=use_ai)
        raw_t = res.get("raw_text", "")

        if raw_t.startswith("[ERROR]"):
            unified["errors"].append(f"{fname}: {raw_t.replace('[ERROR]', '').strip()}")
            unified["per_file_results"].append({
                "filename": fname,
                "ok": False,
                "markers_found": 0,
                "date": None,
                "engine": "error",
                "error": raw_t.replace("[ERROR]", "").strip(),
            })
            continue

        unified["filenames"].append(fname)
        if res.get("extraction_engine") == "ia_hibrida":
            used_ai = True

        # Unir biomarcadores (si un archivo posterior tiene un marcador faltante, se completa)
        for k in ALL_BLOODWORK_KEYS:
            if res.get(k) is not None and unified[k] is None:
                unified[k] = res[k]

        if res.get("date"):
            if res["date"] not in dates_seen:
                dates_seen.append(res["date"])
            if not unified["date"]:
                unified["date"] = res["date"]
                unified["date_detected"] = True

        if not unified["patient_name"] and res.get("patient_name"):
            unified["patient_name"] = res["patient_name"]
        if not unified["lab_name"] and res.get("lab_name"):
            unified["lab_name"] = res["lab_name"]

        for w in res.get("warnings") or []:
            if w not in unified["warnings"]:
                unified["warnings"].append(w)

        raw_chunks.append(f"=== {fname} ===\n{raw_t}")
        unified["per_file_results"].append({
            "filename": fname,
            "ok": True,
            "markers_found": res.get("markers_found", 0),
            "date": res.get("date"),
            "engine": res.get("extraction_engine", "local"),
            "error": None,
        })

    unified["dates_found"] = dates_seen
    if len(dates_seen) > 1:
        unified["warnings"].append(
            f"Los archivos tienen fechas distintas ({', '.join(dates_seen)}). Verifica la fecha antes de guardar."
        )

    unified["raw_text"] = "\n\n".join(raw_chunks)
    unified["markers_found"] = sum(1 for k in ALL_BLOODWORK_KEYS if unified[k] is not None)
    if unified["errors"] and not unified["filenames"]:
        unified["extraction_engine"] = "error"
    else:
        unified["extraction_engine"] = "ia_hibrida" if used_ai else "local"

    return unified
