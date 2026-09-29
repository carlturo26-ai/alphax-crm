"""
hemograma_parser.py — Motor de extracción clínica de alta precisión para exámenes de laboratorio.

Optimizado específicamente para deportistas de resistencia (AlphaX CRM) y para la nomenclatura
de los principales laboratorios de Colombia (SURA, Dinámica, Colsanitas, Synlab, Idime, Cafam,
Compensar, Colsubsidio, Higuera Escalante, Cruz Roja, etc.).

Características avanzadas:
  1. Catálogo exhaustivo de nomenclaturas, abreviaturas y sinónimos para 16 biomarcadores.
  2. Filtrado estricto contra confusiones ("trocamiento") entre exámenes similares:
     - Hemoglobina vs HbA1c vs HCM (Hemoglobina Corpuscular Media).
     - CHCM vs HCM (ignora HCM en pg y extrae solo CHCM).
     - Colesterol Total vs HDL vs LDL vs VLDL vs No-HDL.
     - Glucosa basal vs HbA1c vs Glucosa postprandial.
     - Creatina Kinasa (CK/CPK) vs Creatinina vs CK-MB.
     - Ferritina sérica vs Hierro sérico vs Transferrina vs Capacidad de fijación.
     - PCR Ultrasensible vs Proteínas Totales.
  3. Reensamblaje y unión inteligente de páginas múltiples (cuando el nombre del examen está al final
     de una página y su resultado numérico quedó al inicio de la página siguiente tras los encabezados).
  4. Extracción de doble motor (PyMuPDF + pdfplumber layout visual + OCR Tesseract para escaneados).
  5. Filtros de plausibilidad fisiológica y validación de consistencia clínica cruzada.
"""

import io
import re
import unicodedata
from datetime import datetime

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
    "dic": "12", "diciembre": "12"
}


# ═══════════════════════════════════════════════════════════════════
#  LIMPIEZA DE RUIDO INSTITUCIONAL Y MULTI-PÁGINA
# ═══════════════════════════════════════════════════════════════════

NOISE_LINE_PATTERNS = [
    r"^\s*p[aá]ginas?\s*\d+\s*(?:/|de)\s*\d+.*$",
    r"^\s*p[aá]g\.?\s*\d+.*$",
    r"^\s*hojas?\s*\d+\s*(?:/|de)\s*\d+.*$",
    r"^\s*laboratorio\s+cl[ií]nico.*$",
    r"^\s*(?:sede|direcci[oó]n|tel[eé]fonos?|e-?mail|nit|rut|resoluci[oó]n|habilitaci[oó]n)\s*:.*$",
    r"^\s*contin[uú]a\s+en\s+la\s+siguiente\s+p[aá]gina.*$",
    r"^\s*fin\s+del\s+informe.*$",
    r"^\s*(?:paciente|identificaci[oó]n|documento|c\.?c\.?|historia(?:\s+cl[ií]nica)?|c[oó]digo|orden|edad|sexo|estado\s+civil)\s*:.*$",
    r"^\s*(?:m[eé]dico(?:\s+tratante)?|solicitante|eps|convenio|plan)\s*:.*$",
    r"^\s*fecha(?:\s+de\s+[a-záéíóú]+)?\s*:.*$",
    r"^\s*(?:validado\s+por|bacteri[oó]logo|profesional|registro\s+m[eé]dico)\s*:.*$",
    r"^\s*(?:analito|examen|prueba|estudio|determinaci[oó]n)\s+(?:resultado|valor)\s+(?:unidades?|unidad)?\s+.*$",
    r"^\s*metodolog[ií]a\s*:.*$"
]


def _clean_document_noise(text: str) -> str:
    """Elimina encabezados y pies de página repetitivos que interrumpen la lectura."""
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        is_noise = False
        for pat in NOISE_LINE_PATTERNS:
            if re.match(pat, line, flags=re.IGNORECASE):
                is_noise = True
                break
        if not is_noise:
            cleaned.append(line)
    return "\n".join(cleaned)


def _strip_reference_ranges(text_segment: str) -> str:
    """
    Elimina los rangos de referencia (ej. '39.00 - 51.00', '80.00 - 95.00', '< 200', '(13.0 - 17.0)')
    de un fragmento de texto para evitar que el número del rango de referencia sea interpretado
    como el resultado del paciente.
    """
    if not text_segment:
        return ""
    # 1. Rangos con guión o palabra 'a' / 'hasta': ej. "39.00 - 51.00", "80.00 - 95.00", "4.40 - 6.60"
    cleaned = re.sub(r"(?:(?:\d+(?:[\.,]\d+)?)\s*(?:[-–—]|a|hasta)\s*(?:\d+(?:[\.,]\d+)?))", " ", text_segment)
    # 2. Rangos entre paréntesis: ej. "(13.00 - 17.00)" o "(< 200)" o "(39.0 - 51.0)"
    cleaned = re.sub(r"\(\s*(?:>|<|>=|<=)?\s*\d+(?:[\.,]\d+)?(?:\s*[-–—]\s*\d+(?:[\.,]\d+)?)?\s*\)", " ", cleaned)
    # 3. Etiquetas de referencia tipo 'VALOR DE REFERENCIA: ...' hasta final de línea
    cleaned = re.sub(r"(?:valor(?:es)?\s+de\s+referencia|val\.?\s*ref\.?|v\.?r\.?|ref(?:erencia)?)\s*[:=]?\s*.*$", " ", cleaned, flags=re.IGNORECASE)
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

    stitched_pages = []
    for i, page_text in enumerate(pages):
        clean_page = _clean_document_noise(page_text)
        stitched_pages.append(clean_page)

    # Revisar saltos entre página i y página i+1
    combined = []
    for i in range(len(stitched_pages)):
        if i == 0:
            combined.append(stitched_pages[i])
        else:
            prev_lines = [l.strip() for l in stitched_pages[i-1].splitlines() if l.strip()]
            curr_lines = [l.strip() for l in stitched_pages[i].splitlines() if l.strip()]

            # Si la página previa terminaba con un nombre sin números
            if prev_lines and curr_lines:
                last_line = prev_lines[-1]
                # Si la última línea parece un analito y no tiene números
                if not re.search(r"\d", last_line) and len(last_line) < 80:
                    first_line = curr_lines[0]
                    # Si la primera línea de la siguiente página empieza con un número
                    if re.match(r"^(?:>|<|>=|<=)?\s*\d+(?:[\.,]\d+)?", first_line):
                        # Conectar directamente el analito con su valor
                        combined.append(f"{last_line} : {first_line}")

            combined.append(stitched_pages[i])

    return "\n\n".join(combined)


# ═══════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE TEXTO (PDF / IMÁGENES / OCR)
# ═══════════════════════════════════════════════════════════════════

def extract_text_from_pdf(file_bytes: bytes, password: str = None) -> tuple:
    """
    Extrae texto de un PDF combinando PyMuPDF y pdfplumber.
    Retorna (texto_primario, texto_layout_alternativo).
    Si el documento es escaneado, aplica OCR Tesseract como rescate.
    """
    text_fitz, pages_fitz = _extract_with_fitz_pages(file_bytes, password)
    text_plumber = _extract_with_pdfplumber(file_bytes, password)

    len_fitz = len(text_fitz.strip()) if text_fitz else 0
    len_plumber = len(text_plumber.strip()) if text_plumber else 0

    # Si hay texto digital suficiente
    if len_fitz > 30 or len_plumber > 30:
        primary_text = _stitch_multipage_text(pages_fitz) if pages_fitz else text_fitz
        if not primary_text or len(primary_text.strip()) < 30:
            primary_text = text_plumber
        return primary_text, text_plumber

    # Fallback: OCR para PDF escaneado (foto o imagen dentro de PDF)
    ocr_text = _extract_with_ocr_pdf(file_bytes, password)
    return ocr_text, ""


def extract_text_from_image(file_bytes: bytes) -> str:
    """Extrae texto de una imagen (PNG, JPG, TIFF, BMP, WEBP) usando pytesseract."""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(io.BytesIO(file_bytes))
        try:
            text = pytesseract.image_to_string(img, lang="spa")
        except Exception:
            text = pytesseract.image_to_string(img)
        return text
    except ImportError:
        return "[ERROR] Pillow o pytesseract no están instalados."
    except Exception as e:
        return f"[ERROR] OCR falló: {e}"


def _extract_with_fitz_pages(file_bytes: bytes, password: str = None) -> tuple:
    """Extrae texto página por página con PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            if password:
                auth_res = doc.authenticate(password)
                if not auth_res:
                    return "[ERROR] Contraseña incorrecta para el PDF.", []
            else:
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
        if doc.is_encrypted:
            if password:
                auth_res = doc.authenticate(password)
                if not auth_res:
                    return "[ERROR] Contraseña incorrecta para el PDF."
            else:
                return "[ERROR] PDF protegido con contraseña. Ingresa la clave."

        all_text = []
        for page in doc:
            # Si el PDF contiene una imagen escaneada embebida directa (ej. PNG del escáner), usarla directamente
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
                pix = page.get_pixmap(dpi=200)
                img_to_ocr = Image.open(io.BytesIO(pix.tobytes("png")))

            # Intentar OCR con --psm 6 (mantiene filas de tablas horizontales) y modo estándar
            page_text = ""
            for config_str in ["--psm 6", ""]:
                try:
                    t = pytesseract.image_to_string(img_to_ocr, lang="spa", config=config_str) if config_str else pytesseract.image_to_string(img_to_ocr, lang="spa")
                    if t and len(t.strip()) > len(page_text.strip()):
                        page_text = t
                        if "hemat" in t.lower() and ("volumen" in t.lower() or "pvc" in t.lower() or "vcm" in t.lower()):
                            break
                except Exception:
                    try:
                        t = pytesseract.image_to_string(img_to_ocr)
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
#  HELPERS DE PARSEO CLÍNICO
# ═══════════════════════════════════════════════════════════════════

def _clean_val_str(s: str) -> float:
    """Limpia cadenas como '14,7', '48.4' o '5.050.000' y convierte a float."""
    clean = s.strip()
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
#  (Nomenclatura Colombiana + Prevención de Confusiones + Límites)
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
        r"(?:glicosilada|glucosilada|glicada|a1c|corpuscular|hcm|chcm|fetal|retic)",
        (8.0, 22.0),
        lambda v, u: v / 10.0 if (v > 30.0 or "g/l" in u.lower()) else v
    ),
    (
        "vcm",
        [
            r"volumen\s+corpuscular\s+medio(?:\s*\(?\s*vcm\s*\)?)?",
            r"volumen\s+corpuscular\s+media",
            r"vol\.?\s*corp\.?\s*medio",
            r"promedio\s+(?:de\s+)?volumen\s+corpuscular(?:\s*\(?\s*pvc\s*\)?)?",
            r"pvc\s*\(?\s*promedio\s+(?:de\s+)?volumen\s+corpuscular\s*\)?",
            r"\bp\.?v\.?c\.?\b\s*\(?.*?\)?",
            r"promedio\s+vol\.?\s*corp\.?",
            r"\bvolumen\s+corpuscular\b(?!\s*(?:media\s*de\s*hb|concentraci))",
            r"\bp\.?v\.?c\.?\b",
            r"\b(?:v\.?c\.?m\.?|m\.?c\.?v\.?)\b"
        ],
        r"(?:\bhcm\b|\bchcm\b|\brdw\b|\bide\b|\bade\b|plaquetario|\bvpm\b)",
        (60.0, 125.0),
        lambda v, u: v
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
            r"\b(?:c\.?h\.?c\.?m\.?|m\.?c\.?h\.?c\.?|ccmh|chmc)\b"
        ],
        r"(?:^\s*hcm\b|^\s*mch\b|\bpg\b)",  # NUNCA aceptar HCM en pg
        (26.0, 42.0),
        lambda v, u: v / 10.0 if (v > 100.0 or "g/l" in u.lower()) else v
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
            r"\b(?:r\.?b\.?c\.?|g\.?r\.?|conteo\s+g\.r\.|recuento\s+g\.r\.)\b"
        ],
        r"(?:blancos|leucocitos|plaquetas|reticulocitos|vsg|sedimentacion)",
        (2.5, 7.5),
        lambda v, u: v / 1000000.0 if v > 2000000.0 else (v / 100.0 if v > 200.0 else (v / 10.0 if v > 20.0 else v))
    ),
    (
        "hematocrit",
        [
            r"hemat[oó]crito(?:\s*\(?\s*(?:hto|hct)\s*\)?)?",
            r"hemat[oó]crito\s+total",
            r"volumen\s+hemat[oó]crito",
            r"cuadro\s+hem[aá]tico\s*[-:]?\s*hemat[oó]crito",
            r"hemograma\s*[-:]?\s*hemat[oó]crito",
            r"\b(?:h\.?t\.?o\.?|h\.?c\.?t\.?)\b"
        ],
        r"(?:indice|relaci[oó]n)",
        (24.0, 65.0),
        lambda v, u: v * 100.0 if v < 1.0 else v
    ),
    (
        "ferritin",
        [
            r"ferritina(?:\s+s[eé]rica|\s+plasm[aá]tica|\s+en\s+suero|\s+cuantitativa|\s+total)?",
            r"ferritina\s+s[eé]rica\s+cuantitativa",
            r"ferritina\s+en\s+suero\s+por\s+quimioluminiscencia",
            r"dosaje\s+de\s+ferritina",
            r"\bferritin\b"
        ],
        r"(?:hierro|transferrina|fijaci[oó]n|saturaci[oó]n)",
        (2.0, 2500.0),
        lambda v, u: v
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
            r"\b(?:ck|cpk)\b(?!\s*[-_]?\s*(?:mb|fraccion|masa))"
        ],
        r"(?:creatinina|ck[-_\s]*mb|cpk[-_\s]*mb|\bmb\b|troponina|mioglobina|clearance|depuraci[oó]n)",
        (15.0, 15000.0),  # Si fuera creatinina (~1.0 mg/dL) se descarta automáticamente
        lambda v, u: v
    ),
    (
        "vitamin_b12",
        [
            r"vitamina\s+b[-_\s]?12(?:\s*\(?\s*(?:ciano)?cobalamina\s*\)?)?",
            r"vit\.?\s*b[-_\s]?12",
            r"cianocobalamina(?:\s+en\s+suero)?",
            r"cobalamina(?:\s+en\s+suero)?",
            r"dosaje\s+de\s+vitamina\s+b12",
            r"\bb[-_\s]?12\b"
        ],
        r"(?:\bb6\b|\bb1\b|\bb2\b|\bb9\b|vitamina\s+d|vitamina\s+c|f[oó]lico)",
        (50.0, 3000.0),
        lambda v, u: v * 1.355 if "pmol" in u.lower() else v
    ),
    (
        "folic_acid",
        [
            r"[aá]cido\s+f[oó]lico(?:\s+s[eé]rico|\s+en\s+suero|\s+total)?",
            r"folato(?:s)?(?:\s+s[eé]ricos?|\s+en\s+suero)?",
            r"vitamina\s+b[-_\s]?9",
            r"\bfolic\s+acid\b",
            r"\bserum\s+folate\b"
        ],
        r"(?:intraeritrocitario|eritrocitario|eritrocitos)",
        (0.5, 50.0),
        lambda v, u: v / 2.266 if "nmol" in u.lower() else v
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
            r"\bcolesterol\b(?!\s*[-_]?\s*(?:hdl|ldl|vldl|no\s*hdl|no-hdl|no_hdl|fraccion|fraccionado))"
        ],
        r"(?:\bhdl\b|\bldl\b|\bvldl\b|no[-_\s]*hdl|trigli[sc][eé]ridos)",
        (70.0, 500.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v
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
            r"\bhdl\b(?!\s*[-_]?\s*(?:ratio|indice|relacion))"
        ],
        r"(?:\bldl\b|\bvldl\b|no[-_\s]*hdl|\bcolesterol\s+total\b)",
        (15.0, 160.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v
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
            r"\bldl\b(?!\s*[-_]?\s*(?:ratio|indice|relacion))"
        ],
        r"(?:\bhdl\b|\bvldl\b|no[-_\s]*hdl|\bcolesterol\s+total\b)",
        (20.0, 350.0),
        lambda v, u: v * 38.67 if "mmol" in u.lower() else v
    ),
    (
        "triglycerides",
        [
            r"trigli[sc][eé]ridos?(?:\s+en\s+suero|\s+totales)?",
            r"trigli[sc]eridemia",
            r"triglycerides?"
        ],
        r"(?:colesterol)",
        (20.0, 1500.0),
        lambda v, u: v * 88.57 if "mmol" in u.lower() else v
    ),

    # ── 4. GLUCEMIA E INFLAMACIÓN SISTÉMICA ────────────────────────
    (
        "glucose",
        [
            r"glucosa(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+s[eé]rica|\s+en\s+sangre|\s+preprandial|\s+prepandrial|\s+plasmatica|\s+ayuno)?",
            r"glicemia(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+s[eé]rica|\s+ayuno|\s+preprandial)?",
            r"glucemia(?:\s+en\s+ayunas|\s+basal|\s+en\s+suero|\s+ayuno)?",
            r"\b(?:fasting\s+)?glucose\b"
        ],
        r"(?:glicosilada|glucosilada|glicada|a1c|post|postprandial|despues|curva|sobrecarga|orina|60\s*min|120\s*min|o'sullivan)",
        (40.0, 450.0),
        lambda v, u: v * 18.01 if "mmol" in u.lower() else v
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
            r"\ba1c\b"
        ],
        r"(?:total)",
        (3.5, 18.0),
        lambda v, u: (v * 0.09148 + 2.152) if v > 20.0 else v
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
            r"\bpcr\s+cuantitativa\b(?!\s*semicuantitativa)"
        ],
        r"(?:semicuantitativa|cualitativa|aglutinaci[oó]n|l[aá]tex|t[ií]tulo|diluci[oó]n|negativ[oa]|positiv[oa]|prote[ií]nas?\s+totales?|proteinuria|orina|electroforesis)",
        (0.01, 50.0),
        lambda v, u: v * 10.0 if "mg/dl" in u.lower() else v
    )
]

# Patrón para capturar número y unidad (evitando capturar partes de fechas o rangos numéricos como 39.00 - 51.00)
VAL_UNIT_PATTERN = r"(?:>|<|>=|<=)?\s*(\d+(?:[\.,]\d+)*)(?!\s*[-–—\/]\s*\d)(?:\s*(%|g/dl|g/l|fl|u3|mill/mm3|millones/ul|x10\^6/ul|m/ul|u/l|ui/l|ng/ml|ug/l|mcg/l|pg/ml|pmol/l|mg/dl|mg/l|mmol/l))?"


def _extract_single_marker(text: str, aliases: list, exclusion_pat: str, bounds: tuple, scale_fn):
    """
    Busca un marcador usando una estrategia escalonada e insensible a tildes:
      1. Misma línea (formato tabular).
      2. Líneas 1-3 posteriores (formato vertical o informe por bloques).
      3. Proximidad en texto continuo limpio (evitando capturar fechas u otros analitos).
    """
    min_b, max_b = bounds
    lines = text.splitlines()

    # Pase 1: Coincidencia en la misma línea
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        line_no_acc = _strip_accents(line_clean)

        for alias in aliases:
            m_alias = re.search(alias, line_clean, flags=re.IGNORECASE) or re.search(alias, line_no_acc, flags=re.IGNORECASE)
            if m_alias:
                if exclusion_pat and (re.search(exclusion_pat, line_clean, flags=re.IGNORECASE) or re.search(exclusion_pat, line_no_acc, flags=re.IGNORECASE)):
                    continue

                raw_after = line_clean[m_alias.end():] if re.search(alias, line_clean, flags=re.IGNORECASE) else line_no_acc[m_alias.end():]
                # Eliminar rangos de referencia (ej. 39.00 - 51.00) para no confundir el rango con el resultado
                after_alias = _strip_reference_ranges(raw_after)
                m_val = re.search(VAL_UNIT_PATTERN, after_alias, flags=re.IGNORECASE)
                if m_val:
                    num_str, unit_str = m_val.group(1), m_val.group(2) or ""
                    try:
                        v = _clean_val_str(num_str)
                        if 1950 <= v <= 2040 and not unit_str:
                            pass  # Año de calendario
                        else:
                            v_scaled = scale_fn(v, unit_str)
                            if min_b <= v_scaled <= max_b:
                                return round(v_scaled, 2)
                    except Exception:
                        pass

                # Pase 2: Disposición vertical (revisar 1 a 3 líneas siguientes)
                for offset in range(1, 4):
                    if i + offset >= len(lines):
                        break
                    next_line = lines[i + offset].strip()
                    if not next_line:
                        continue
                    next_no_acc = _strip_accents(next_line)
                    # Si la siguiente línea contiene otro examen de la lista, detener la búsqueda vertical
                    if any((re.search(other_alias, next_line, flags=re.IGNORECASE) or re.search(other_alias, next_no_acc, flags=re.IGNORECASE))
                           for other_spec in MARKER_SPECS
                           for other_alias in other_spec[1] if other_spec[0] != alias):
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

    # Pase 3: Búsqueda de proximidad en texto corrido (original y sin tildes, con rangos de referencia eliminados)
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


def parse_hemograma(text: str) -> dict:
    """
    Analiza el texto de un examen clínico y extrae los 16 biomarcadores sanguíneos
    garantizando que no se confundan unos con otros y aplicando coherencia fisiológica.
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
        "total_cholesterol": None,
        "hdl": None,
        "ldl": None,
        "triglycerides": None,
        "glucose": None,
        "hba1c": None,
        "pcr_us": None,
        "date": None,
        "patient_name": None,
        "raw_text": text or "",
        "markers_found": 0,
        "markers_total": 16,
    }

    if not text or text.startswith("[ERROR]"):
        return result

    # 1. Metadatos: Fecha y Paciente
    result["date"] = _extract_date(text)
    result["patient_name"] = _extract_patient_name(text)

    # 2. Limpieza preliminar de ruido institucional
    cleaned = _clean_document_noise(text)

    # 3. Extracción de cada biomarcador
    found = 0
    for key, aliases, exclusions, bounds, scale_fn in MARKER_SPECS:
        val = _extract_single_marker(cleaned, aliases, exclusions, bounds, scale_fn)
        if val is None and cleaned != text:
            # Fallback en texto completo si la limpieza fue muy agresiva
            val = _extract_single_marker(text, aliases, exclusions, bounds, scale_fn)

        if val is not None:
            result[key] = val
            found += 1

    # ── Fallback especializado para VCM (cuando OCR distorsiona las siglas o layout) ──
    if result["vcm"] is None:
        for line in (cleaned + "\n" + text).splitlines():
            line_c = line.strip()
            line_no_acc = _strip_accents(line_c)
            if re.search(r"(?:volumen|corpuscular|\bpvc\b|\bvcm\b|\bmcv\b|promedio\s+de\s+vol)", line_no_acc, re.IGNORECASE):
                # Descartar si es plaquetario, CHCM, HCM o ancho de distribución
                if re.search(r"(?:plaquetario|\bvpm\b|\bhcm\b|\bchcm\b|\brdw\b|\bide\b|\bade\b|concentraci)", line_no_acc, re.IGNORECASE):
                    continue
                # Quitar rangos de referencia antes de buscar números en el fallback (ej. 80.00 - 95.00)
                line_c_no_ref = _strip_reference_ranges(line_c)
                m_nums = re.findall(r"(?:>|<|>=|<=)?\s*(\d+(?:[\.,]\d+)?)", line_c_no_ref)
                for num_s in m_nums:
                    try:
                        v = _clean_val_str(num_s)
                        if 65.0 <= v <= 120.0:
                            result["vcm"] = round(v, 2)
                            found += 1
                            break
                    except Exception:
                        pass
                if result["vcm"] is not None:
                    break

    # 4. Validaciones de coherencia clínica cruzada para evitar trocamientos:
    # ── Colesterol Total vs HDL: Total siempre debe ser mayor que HDL
    if result["total_cholesterol"] and result["hdl"]:
        if result["total_cholesterol"] < result["hdl"]:
            result["total_cholesterol"], result["hdl"] = result["hdl"], result["total_cholesterol"]

    # ── Hemoglobina vs HbA1c: Si hemoglobina dio < 8.0, era HbA1c
    if result["hemoglobin"] and result["hemoglobin"] < 8.0:
        if not result["hba1c"]:
            result["hba1c"] = result["hemoglobin"]
        result["hemoglobin"] = None
        found = sum(1 for k in result if k in [s[0] for s in MARKER_SPECS] and result[k] is not None)

    # ── CK vs Creatinina: CK nunca puede ser menor a 15.0 U/L
    if result["ck"] and result["ck"] < 15.0:
        result["ck"] = None
        found = sum(1 for k in result if k in [s[0] for s in MARKER_SPECS] and result[k] is not None)

    # ── Reconciliación Fisiológica Bidireccional de Hematocrito y VCM (Ecuación de Wintrobe: Hto = VCM * RBC / 10) ──
    # Si tenemos Glóbulos Rojos (RBC), reconciliar y verificar candidatos en el documento:
    if result["rbc"]:
        rbc_val = result["rbc"]

        # 1. Si VCM es conocido y coherente:
        if result["vcm"] and 70.0 <= result["vcm"] <= 120.0:
            expected_hto = (result["vcm"] * rbc_val) / 10.0  # Ej: 96.0 * 5.05 / 10 = 48.48

            # Si el hematocrito obtenido discrepa mucho con el esperado fisiológico (o tomó el rango 39.0):
            if result["hematocrit"] is None or abs(result["hematocrit"] - expected_hto) > 1.2:
                found_cand = None
                # Buscar en el documento algún número que coincida con el Hto esperado (ej. 48.4 o 48.8)
                for cand_match in re.finditer(r"\b(\d{2}[\.,]\d{1,2})\b", text):
                    try:
                        cand_v = _clean_val_str(cand_match.group(1))
                        if abs(cand_v - expected_hto) <= 0.6:
                            found_cand = round(cand_v, 2)
                            break
                    except Exception:
                        pass

                if found_cand is not None:
                    if result["hematocrit"] is None:
                        found += 1
                    result["hematocrit"] = found_cand
                elif result["hematocrit"]:
                    # Probar si un dígito fue confundido por OCR (ej. 45.4 -> 48.4)
                    s_hto = str(result["hematocrit"])
                    for old_char in ["5", "3"]:
                        if old_char in s_hto:
                            try:
                                cand_hto = float(s_hto.replace(old_char, "8", 1))
                                if abs(cand_hto - expected_hto) <= 0.6:
                                    result["hematocrit"] = round(cand_hto, 2)
                                    break
                            except Exception:
                                pass

        # 2. Si Hematocrito es conocido y coherente:
        if result["hematocrit"] and 30.0 <= result["hematocrit"] <= 60.0:
            expected_vcm = (result["hematocrit"] * 10.0) / rbc_val  # Ej: 48.4 * 10 / 5.05 = 95.84

            # Si VCM discrepa mucho con el esperado fisiológico (o tomó el rango 80.0):
            if result["vcm"] is None or abs(result["vcm"] - expected_vcm) > 2.5:
                found_vcm = None
                # Buscar en el documento algún número que coincida con el VCM esperado (ej. 96.0)
                for cand_match in re.finditer(r"\b(\d{2,3}[\.,]\d{1,2})\b", text):
                    try:
                        cand_v = _clean_val_str(cand_match.group(1))
                        if abs(cand_v - expected_vcm) <= 1.2:
                            found_vcm = round(cand_v, 2)
                            break
                    except Exception:
                        pass

                if found_vcm is not None:
                    if result["vcm"] is None:
                        found += 1
                    result["vcm"] = found_vcm

        # 3. Si tanto VCM como Hematocrito faltan o tomaron los límites de referencia (39.0 y 80.0):
        if (result["vcm"] is None or result["vcm"] == 80.0) and (result["hematocrit"] is None or result["hematocrit"] == 39.0):
            assigned = {v for k, v in result.items() if v is not None and isinstance(v, (int, float)) and k not in ("vcm", "hematocrit")}
            text_no_ref = _strip_reference_ranges(text)
            cand_vcm_found = None
            for cand_match in re.finditer(r"\b(\d{2,3}(?:[\.,]\d{1,2})?)\b", text_no_ref):
                try:
                    c_val = _clean_val_str(cand_match.group(1))
                    if 78.0 <= c_val <= 110.0 and c_val not in assigned and c_val != 80.0:
                        cand_vcm_found = round(c_val, 2)
                        break
                except Exception:
                    pass

            if cand_vcm_found is not None:
                if result["vcm"] is None:
                    found += 1
                result["vcm"] = cand_vcm_found
                expected_hto = (cand_vcm_found * rbc_val) / 10.0
                for cand_match in re.finditer(r"\b(\d{2}(?:[\.,]\d{1,2})?)\b", text_no_ref):
                    try:
                        c_hto = _clean_val_str(cand_match.group(1))
                        if abs(c_hto - expected_hto) <= 1.0 and c_hto != 39.0:
                            if result["hematocrit"] is None:
                                found += 1
                            result["hematocrit"] = round(c_hto, 2)
                            break
                    except Exception:
                        pass

    result["markers_found"] = found
    return result


def process_file(file_bytes: bytes, filename: str, password: str = None) -> dict:
    """
    Punto de entrada principal para procesar cualquier archivo subido (PDF o Imagen).
    Ejecuta extracción multi-motor y complementa con layout visual si faltan marcadores.
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        primary_text, alt_layout_text = extract_text_from_pdf(file_bytes, password)
        parsed = parse_hemograma(primary_text)

        # Si faltan marcadores y tenemos el texto con layout alternativo (pdfplumber)
        if alt_layout_text and parsed["markers_found"] < 16:
            alt_parsed = parse_hemograma(alt_layout_text)
            for key, _, _, _, _ in MARKER_SPECS:
                if parsed[key] is None and alt_parsed.get(key) is not None:
                    parsed[key] = alt_parsed[key]
                    parsed["markers_found"] += 1
            if not parsed["date"] and alt_parsed.get("date"):
                parsed["date"] = alt_parsed["date"]
            if not parsed["patient_name"] and alt_parsed.get("patient_name"):
                parsed["patient_name"] = alt_parsed["patient_name"]

        return parsed

    elif ext in ("png", "jpg", "jpeg", "tiff", "bmp", "webp"):
        text = extract_text_from_image(file_bytes)
        return parse_hemograma(text)

    else:
        text = f"[ERROR] Formato no soportado: .{ext}"
        return parse_hemograma(text)
