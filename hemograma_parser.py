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
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                text = pytesseract.image_to_string(img, lang="spa")
            except Exception:
                text = pytesseract.image_to_string(img)
            if text:
                all_text.append(text)
        doc.close()
        return "\n\n".join(all_text)
    except Exception as e:
        return f"[ERROR] El documento escaneado requiere OCR (tesseract): {e}"


# ═══════════════════════════════════════════════════════════════════
#  HELPERS DE PARSEO CLÍNICO
# ═══════════════════════════════════════════════════════════════════

def _clean_val_str(s: str) -> float:
    """Limpia cadenas como '14,7' o '1.450' y convierte a float."""
    clean = s.replace(",", ".").strip()
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
            r"promedio\s+volumen\s+corpuscular",
            r"\b(?:v\.?c\.?m\.?|m\.?c\.?v\.?)\b"
        ],
        r"(?:hcm|chcm|rdw|ide|ade|plaquetario|vpm)",
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
            r"hematocrito(?:\s*\(?\s*(?:hto|hct)\s*\)?)?",
            r"hematocrito\s+total",
            r"volumen\s+hematocrito",
            r"cuadro\s+hem[aá]tico\s*[-:]?\s*hematocrito",
            r"hemograma\s*[-:]?\s*hematocrito",
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
            r"prote[ií]na\s+c\s+reactiva\s+cuantitativa",
            r"pcr\s+ultra\s*sensible",
            r"pcr\s*[-_]?\s*us\b",
            r"pcr\s*[-_]?\s*as\b",
            r"pcr\s+cuantitativa",
            r"hs[-_\s]?crp\b",
            r"crp[-_\s]?hs\b",
            r"high\s+sensitivity\s+c[-_\s]?reactive\s+protein",
            r"prote[ií]na\s+c\s+reactiva",
            r"\bpcr\b"
        ],
        r"(?:prote[ií]nas\s+totales|proteinuria|orina|electroforesis)",
        (0.01, 50.0),
        lambda v, u: v * 10.0 if "mg/dl" in u.lower() else v
    )
]

# Patrón para capturar número y unidad (evitando capturar partes de fechas como 2026-03-01)
VAL_UNIT_PATTERN = r"(?:>|<|>=|<=)?\s*(\d+(?:[\.,]\d+)?)(?![\-\/]\d)(?:\s*(%|g/dl|g/l|fl|u3|mill/mm3|millones/ul|x10\^6/ul|m/ul|u/l|ui/l|ng/ml|ug/l|mcg/l|pg/ml|pmol/l|mg/dl|mg/l|mmol/l))?"


def _extract_single_marker(text: str, aliases: list, exclusion_pat: str, bounds: tuple, scale_fn):
    """
    Busca un marcador usando una estrategia escalonada:
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
        for alias in aliases:
            m_alias = re.search(alias, line_clean, flags=re.IGNORECASE)
            if m_alias:
                if exclusion_pat and re.search(exclusion_pat, line_clean, flags=re.IGNORECASE):
                    continue

                after_alias = line_clean[m_alias.end():]
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
                    # Si la siguiente línea contiene otro examen de la lista, detener la búsqueda vertical
                    if any(re.search(other_alias, next_line, flags=re.IGNORECASE)
                           for other_spec in MARKER_SPECS
                           for other_alias in other_spec[1] if other_spec[0] != alias):
                        break

                    m_val_next = re.search(VAL_UNIT_PATTERN, next_line, flags=re.IGNORECASE)
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

    # Pase 3: Búsqueda de proximidad en texto corrido
    for alias in aliases:
        pat_prox = rf"(?:{alias})[^\w\n\r]{{0,60}}?(?:resultado|valor)?[:\s\-=]*{VAL_UNIT_PATTERN}"
        for m in re.finditer(pat_prox, text, flags=re.IGNORECASE):
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
