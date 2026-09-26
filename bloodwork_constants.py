"""
bloodwork_constants.py — Rangos clínicos y deportivos de referencia para deportistas de resistencia.
Diferenciados por sexo biológico (Hombre vs Mujer) y optimizados para deportes aeróbicos (AlphaX Coaching).
"""

ALL_MARKER_KEYS = [
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
    "pcr_us"
]

def get_bloodwork_ranges(gender="Hombre"):
    """
    Retorna la configuración completa de rangos de referencia para 15 biomarcadores sanguíneos,
    diferenciando umbrales óptimos y límites para Hombres y Mujeres.
    """
    is_female = str(gender).strip().lower() in ["mujer", "femenino", "f", "female"]
    
    return {
        # ── 1. SERIE ROJA Y TRANSPORTE DE OXÍGENO ──────────────────
        "hemoglobin": {
            "low": 12.0 if is_female else 13.5,
            "opt_lo": 13.0 if is_female else 14.5,
            "opt_hi": 15.5 if is_female else 17.5,
            "high": 16.0 if is_female else 18.0,
            "unit": "g/dL",
            "name": "Hemoglobina Total",
            "emoji": "🔴",
            "color": "#FF5555",
            "bg": "rgba(255, 85, 85, 0.05)",
            "help": "13.0–15.5 g/dL (Mujeres) | 14.5–17.5 g/dL (Hombres)" if is_female else "14.5–17.5 g/dL (Hombres) | 13.0–15.5 g/dL (Mujeres)"
        },
        "vcm": {
            "low": 80.0,
            "opt_lo": 82.0,
            "opt_hi": 96.0,
            "high": 100.0,
            "unit": "fL",
            "name": "Volumen Corpuscular Medio (VCM)",
            "emoji": "🔵",
            "color": "#48DBFB",
            "bg": "rgba(72, 219, 251, 0.05)",
            "help": "82–96 fL (Tamaño promedio del eritrocito)"
        },
        "chcm": {
            "low": 32.0,
            "opt_lo": 33.0,
            "opt_hi": 36.0,
            "high": 36.5,
            "unit": "g/dL",
            "name": "Conc. Hb Corp. Media (CHCM)",
            "emoji": "🟡",
            "color": "#FECA57",
            "bg": "rgba(254, 202, 87, 0.05)",
            "help": "33–36 g/dL (Saturación de hemoglobina celular)"
        },
        "rbc": {
            "low": 3.8 if is_female else 4.3,
            "opt_lo": 4.0 if is_female else 4.5,
            "opt_hi": 5.2 if is_female else 5.8,
            "high": 5.5 if is_female else 6.0,
            "unit": "×10⁶/μL",
            "name": "Glóbulos Rojos (Eritrocitos)",
            "emoji": "⭕",
            "color": "#FF6B6B",
            "bg": "rgba(255, 107, 107, 0.05)",
            "help": "4.0–5.2 ×10⁶/μL (Mujeres) | 4.5–5.8 ×10⁶/μL (Hombres)" if is_female else "4.5–5.8 ×10⁶/μL (Hombres) | 4.0–5.2 ×10⁶/μL (Mujeres)"
        },
        "hematocrit": {
            "low": 36.0 if is_female else 40.0,
            "opt_lo": 38.0 if is_female else 42.0,
            "opt_hi": 46.0 if is_female else 50.0,
            "high": 48.0 if is_female else 52.0,
            "unit": "%",
            "name": "Hematocrito",
            "emoji": "🩸",
            "color": "#00EEFF",
            "bg": "rgba(0, 238, 255, 0.05)",
            "help": "38–46% (Mujeres) | 42–50% (Hombres)" if is_female else "42–50% (Hombres) | 38–46% (Mujeres)"
        },
        "ferritin": {
            "low": 20.0 if is_female else 30.0,
            "opt_lo": 35.0 if is_female else 50.0,
            "opt_hi": 150.0 if is_female else 250.0,
            "high": 200.0 if is_female else 350.0,
            "unit": "ng/mL",
            "name": "Ferritina Sérica",
            "emoji": "🧲",
            "color": "#FF9F43",
            "bg": "rgba(255, 159, 67, 0.05)",
            "help": "35–150 ng/mL (Mujeres) | 50–250 ng/mL (Hombres)" if is_female else "50–250 ng/mL (Hombres) | 35–150 ng/mL (Mujeres)"
        },

        # ── 2. DAÑO MUSCULAR Y METABOLISMO CELULAR ─────────────────
        "ck": {
            "low": 20.0,
            "opt_lo": 40.0 if is_female else 50.0,
            "opt_hi": 180.0 if is_female else 250.0,
            "high": 250.0 if is_female else 350.0,
            "unit": "U/L",
            "name": "Creatina Kinasa (CK Total)",
            "emoji": "⚡",
            "color": "#A55EEA",
            "bg": "rgba(165, 94, 234, 0.05)",
            "help": "Basal: <180 U/L (Mujeres) | <250 U/L (Hombres)" if is_female else "Basal: <250 U/L (Hombres) | <180 U/L (Mujeres)"
        },
        "vitamin_b12": {
            "low": 250.0,
            "opt_lo": 400.0,
            "opt_hi": 900.0,
            "high": 1000.0,
            "unit": "pg/mL",
            "name": "Vitamina B12 (Cobalamina)",
            "emoji": "💊",
            "color": "#1DD1A1",
            "bg": "rgba(29, 209, 161, 0.05)",
            "help": "400–900 pg/mL (Límite bajo: 250-400)"
        },
        "folic_acid": {
            "low": 3.5,
            "opt_lo": 6.0,
            "opt_hi": 18.0,
            "high": 20.0,
            "unit": "ng/mL",
            "name": "Ácido Fólico (Vitamina B9)",
            "emoji": "🥬",
            "color": "#10AC84",
            "bg": "rgba(16, 172, 132, 0.05)",
            "help": "6–18 ng/mL (Límite bajo: 3.5-5.9)"
        },

        # ── 3. PERFIL LIPÍDICO Y CARDIOVASCULAR ────────────────────
        "total_cholesterol": {
            "low": 120.0,
            "opt_lo": 130.0,
            "opt_hi": 190.0,
            "high": 200.0,
            "unit": "mg/dL",
            "name": "Colesterol Total",
            "emoji": "🫀",
            "color": "#FF7675",
            "bg": "rgba(255, 118, 117, 0.05)",
            "help": "130–190 mg/dL (Deseable <200)"
        },
        "hdl": {
            "low": 50.0 if is_female else 40.0,
            "opt_lo": 60.0 if is_female else 50.0,
            "opt_hi": 95.0 if is_female else 85.0,
            "high": 110.0 if is_female else 100.0,
            "unit": "mg/dL",
            "name": "Colesterol HDL (Bueno)",
            "emoji": "🛡️",
            "color": "#00CEC9",
            "bg": "rgba(0, 206, 201, 0.05)",
            "help": ">60 mg/dL (Mujeres) | >50 mg/dL (Hombres)" if is_female else ">50 mg/dL (Hombres) | >60 mg/dL (Mujeres)"
        },
        "ldl": {
            "low": 40.0,
            "opt_lo": 50.0,
            "opt_hi": 100.0,
            "high": 130.0,
            "unit": "mg/dL",
            "name": "Colesterol LDL (Malo)",
            "emoji": "⚠️",
            "color": "#FAB1A0",
            "bg": "rgba(250, 177, 160, 0.05)",
            "help": "50–100 mg/dL (Elevado: >130)"
        },
        "triglycerides": {
            "low": 40.0,
            "opt_lo": 50.0,
            "opt_hi": 120.0,
            "high": 150.0,
            "unit": "mg/dL",
            "name": "Triglicéridos",
            "emoji": "🧪",
            "color": "#FDCB6E",
            "bg": "rgba(253, 203, 110, 0.05)",
            "help": "50–120 mg/dL (Deseable <150)"
        },

        # ── 4. GLUCEMIA E INFLAMACIÓN SISTÉMICA ───────────────────
        "glucose": {
            "low": 70.0,
            "opt_lo": 75.0,
            "opt_hi": 95.0,
            "high": 100.0,
            "unit": "mg/dL",
            "name": "Glucemia (Ayunas)",
            "emoji": "🍯",
            "color": "#E17055",
            "bg": "rgba(225, 112, 85, 0.05)",
            "help": "75–95 mg/dL (Prediabetes: >100, Hipoglucemia: <70)"
        },
        "hba1c": {
            "low": 4.5,
            "opt_lo": 4.8,
            "opt_hi": 5.4,
            "high": 5.7,
            "unit": "%",
            "name": "Hemoglobina Glicosilada (HbA1c)",
            "emoji": "🧬",
            "color": "#FF7675",
            "bg": "rgba(255, 118, 117, 0.05)",
            "help": "4.8–5.4% (Óptimo deportistas) | <5.7% (Normal) | 5.7–6.4% (Prediabetes) | ≥6.5% (Diabetes)"
        },
        "pcr_us": {
            "low": 0.0,
            "opt_lo": 0.05,
            "opt_hi": 1.0,
            "high": 3.0,
            "unit": "mg/L",
            "name": "PCR Ultra Sensible (hs-CRP)",
            "emoji": "🔥",
            "color": "#D63031",
            "bg": "rgba(214, 48, 49, 0.05)",
            "help": "<1.0 mg/L (Óptimo) | 1.0–3.0 mg/L (Carga de entreno) | >3.0 mg/L (Inflamación alta)"
        }
    }


def classify_bloodwork_value(key, value, gender="Hombre"):
    """
    Clasifica un marcador clínico según rangos biológicos masculinos o femeninos.
    Retorna (etiqueta, color_hex).
    """
    if value is None:
        return "—", "#666666"
    
    ranges = get_bloodwork_ranges(gender)
    r = ranges.get(key)
    if not r:
        return "—", "#666666"

    # Caso especial HDL: mayor es mejor
    if key == "hdl":
        if value < r["low"]:
            return "BAJO", "#FF4B4B"
        elif value < r["opt_lo"]:
            return "ACEPTABLE", "#FFD700"
        elif value <= r["opt_hi"]:
            return "ÓPTIMO", "#00FF00"
        else:
            return "EXCELENTE", "#00EEFF"

    # Caso especial PCR-us: inflamación
    if key == "pcr_us":
        if value <= r["opt_hi"]:
            return "ÓPTIMO (BAJO RIESGO)", "#00FF00"
        elif value <= r["high"]:
            return "MODERADO (INFLAMACIÓN)", "#FFD700"
        else:
            return "ELEVADO (ESTRÉS/RIESGO)", "#FF4B4B"

    # Caso especial Glucemia: hipo y prediabetes
    if key == "glucose":
        if value < r["low"]:
            return "HIPOGLUCEMIA", "#FF4B4B"
        elif value < r["opt_lo"]:
            return "LÍMITE BAJO", "#FFD700"
        elif value <= r["opt_hi"]:
            return "ÓPTIMO", "#00FF00"
        elif value <= r["high"]:
            return "LÍMITE ALTO", "#FFD700"
        else:
            return "ELEVADO", "#FF4B4B"

    # Caso especial HbA1c (Hemoglobina Glicosilada)
    if key == "hba1c":
        if value < r["low"]:
            return "BAJO", "#FFD700"
        elif value <= r["opt_hi"]:
            return "ÓPTIMO", "#00FF00"
        elif value < r["high"]:
            return "NORMAL-ALTO", "#FFD700"
        elif value < 6.5:
            return "PREDIABETES (ELEVADA)", "#FF7675"
        else:
            return "DIABETES / MUY ALTA", "#FF4B4B"

    # Caso general
    if value < r["low"]:
        return "BAJO", "#FF4B4B"
    elif value < r["opt_lo"]:
        label = "LÍMITE" if key in ["vitamin_b12", "folic_acid", "ferritin"] else "INTERMEDIO-BAJO"
        return label, "#FFD700"
    elif value <= r["opt_hi"]:
        return "ÓPTIMO", "#00FF00"
    elif value <= r["high"]:
        label = "ELEVADO" if key in ["ck", "ldl", "total_cholesterol", "triglycerides"] else "INTERMEDIO-ALTO"
        return label, "#FFD700"
    else:
        label = "MUY ALTO" if key in ["ck", "ldl", "triglycerides"] else "ALTO"
        return label, "#FF4B4B"


def get_clinical_alerts(latest_rec, gender="Hombre"):
    """
    Evalúa patrones clínicos y genera alertas adaptadas al sexo del atleta.
    Retorna una lista de tuplas: (emoji, titulo, detalle, color_hex).
    Utiliza _v() con getattr para garantizar compatibilidad total sin AttributeError.
    """
    if not latest_rec:
        return []
    
    def _v(key):
        return getattr(latest_rec, key, None)
    
    is_female = str(gender).strip().lower() in ["mujer", "femenino", "f", "female"]
    sex_label = "Mujer" if is_female else "Hombre"
    alerts = []

    # 1. Hemoglobina
    hb_low_threshold = 12.0 if is_female else 13.5
    hb_high_threshold = 16.0 if is_female else 18.0
    val_hb = _v("hemoglobin")
    if val_hb is not None:
        if val_hb < hb_low_threshold:
            alerts.append((
                "🚨", 
                f"Hemoglobina BAJA ({sex_label})", 
                f"Hb = {val_hb:.1f} g/dL (< {hb_low_threshold:.1f}) → Posible anemia en atleta {sex_label.lower()}. Reduce capacidad aeróbica y oxigenación. Revisar ferritina y B12.", 
                "#FF4B4B"
            ))
        elif val_hb > hb_high_threshold:
            alerts.append((
                "⚠️", 
                f"Hemoglobina ALTA ({sex_label})", 
                f"Hb = {val_hb:.1f} g/dL (> {hb_high_threshold:.1f}) → Viscosidad sanguínea elevada. Revisar hidratación y aclimatación a altitud.", 
                "#FFD700"
            ))

    # 2. Ferritina
    fer_critical = 20.0 if is_female else 30.0
    fer_opt_min = 35.0 if is_female else 50.0
    val_fer = _v("ferritin")
    if val_fer is not None:
        if val_fer < fer_critical:
            alerts.append((
                "⚡", 
                f"Ferritina CRÍTICA ({sex_label})", 
                f"Ferritina = {val_fer:.0f} ng/mL (< {fer_critical:.0f}) → Depósitos de hierro agotados. Suplementación y valoración urgente.", 
                "#FF4B4B"
            ))
        elif val_fer < fer_opt_min:
            alerts.append((
                "⚡", 
                f"Ferritina BAJA para Deportista ({sex_label})", 
                f"Ferritina = {val_fer:.0f} ng/mL (< {fer_opt_min:.0f}) → Reserva de hierro disminuida. Rendimiento aeróbico en riesgo.", 
                "#FFD700"
            ))

    # 3. Hematocrito
    hto_low = 36.0 if is_female else 40.0
    hto_high = 48.0 if is_female else 52.0
    val_hto = _v("hematocrit")
    if val_hto is not None:
        if val_hto < hto_low:
            alerts.append((
                "⚠️", 
                f"Hematocrito BAJO ({sex_label})", 
                f"Hto = {val_hto:.1f}% (< {hto_low:.1f}%) → Posible hemodilución o masa eritrocitaria baja.", 
                "#FF4B4B"
            ))
        elif val_hto > hto_high:
            alerts.append((
                "⚠️", 
                f"Hematocrito ALTO ({sex_label})", 
                f"Hto = {val_hto:.1f}% (> {hto_high:.1f}%) → Sangre muy concentrada. Hidratarse y descansar.", 
                "#FFD700"
            ))

    # 4. Creatina Kinasa (CK)
    ck_high = 250.0 if is_female else 350.0
    val_ck = _v("ck")
    if val_ck is not None:
        if val_ck > 1000.0:
            alerts.append((
                "🚨", 
                "CK MUY ELEVADA (ALARMA CLÍNICA)", 
                f"CK = {val_ck:.0f} U/L → Daño muscular agudo o sobreentrenamiento severo. Requiere reposo absoluto y supervisión médica.", 
                "#FF4B4B"
            ))
        elif val_ck > ck_high:
            alerts.append((
                "⚠️", 
                f"CK ELEVADA — Fatiga Muscular ({sex_label})", 
                f"CK = {val_ck:.0f} U/L (> {ck_high:.0f}) → Daño muscular por carga reciente. Priorizar descanso y recuperación activa.", 
                "#FFD700"
            ))

    # 5. Proteína C Reactiva Ultra Sensible (PCR-us)
    val_pcr = _v("pcr_us")
    if val_pcr is not None:
        if val_pcr > 3.0:
            alerts.append((
                "🔥", 
                "PCR-us ELEVADA (Inflamación Sistémica)", 
                f"PCR-us = {val_pcr:.2f} mg/L (> 3.0) → Inflamación alta. Posible sobrecarga de entreno, infección latente o microlesión.", 
                "#FF4B4B"
            ))
        elif val_pcr > 1.0:
            alerts.append((
                "⚠️", 
                "PCR-us MODERADA (Estrés de Entrenamiento)", 
                f"PCR-us = {val_pcr:.2f} mg/L (1.0–3.0) → Estado inflamatorio residual de la carga física reciente.", 
                "#FFD700"
            ))

    # 6. Glucemia y Hemoglobina Glicosilada (HbA1c)
    val_glu = _v("glucose")
    if val_glu is not None:
        if val_glu < 70.0:
            alerts.append((
                "⚠️", 
                "Glucemia BAJA (Hipoglucemia en Ayunas)", 
                f"Glucosa = {val_glu:.0f} mg/dL (< 70) → Riesgo de hipoglucemia y fatiga glucolítica en entrenamientos.", 
                "#FF4B4B"
            ))
        elif val_glu > 100.0:
            alerts.append((
                "⚠️", 
                "Glucemia ELEVADA en Ayunas", 
                f"Glucosa = {val_glu:.0f} mg/dL (> 100) → Posible alteración en el metabolismo de carbohidratos o estrés agudo.", 
                "#FFD700"
            ))

    val_a1c = _v("hba1c")
    if val_a1c is not None:
        if val_a1c >= 6.5:
            alerts.append((
                "🚨",
                "HbA1c MUY ELEVADA (Rango Diabetes)",
                f"HbA1c = {val_a1c:.1f}% (≥ 6.5%) → Control glucémico crónico descompensado. Requiere evaluación médica inmediata.",
                "#FF4B4B"
            ))
        elif val_a1c >= 5.7:
            alerts.append((
                "⚠️",
                "HbA1c ELEVADA (Riesgo Prediabetes / Resistencia a la Insulina)",
                f"HbA1c = {val_a1c:.1f}% (5.7%–6.4%) → Promedio glucémico de últimos 3 meses por encima de lo óptimo. Revisar timing y calidad de carbohidratos.",
                "#FFD700"
            ))
        elif val_a1c < 4.5:
            alerts.append((
                "⚠️",
                "HbA1c BAJA",
                f"HbA1c = {val_a1c:.1f}% (< 4.5%) → Posible recambio eritrocitario acelerado o hipoglucemias frecuentes.",
                "#FFD700"
            ))

    # 7. Perfil Lipídico
    hdl_min = 50.0 if is_female else 40.0
    val_hdl = _v("hdl")
    if val_hdl is not None and val_hdl < hdl_min:
        alerts.append((
            "⚠️", 
            f"Colesterol HDL BAJO ({sex_label})", 
            f"HDL = {val_hdl:.0f} mg/dL (< {hdl_min:.0f}) → Nivel subóptimo de colesterol cardioprotector.", 
            "#FFD700"
        ))
    val_ldl = _v("ldl")
    if val_ldl is not None and val_ldl > 130.0:
        alerts.append((
            "⚠️", 
            "Colesterol LDL ELEVADO", 
            f"LDL = {val_ldl:.0f} mg/dL (> 130) → Fracción aterogénica alta. Revisar composición de grasas en la dieta.", 
            "#FFD700"
        ))
    val_trig = _v("triglycerides")
    if val_trig is not None and val_trig > 150.0:
        alerts.append((
            "⚠️", 
            "Triglicéridos ELEVADOS", 
            f"Triglicéridos = {val_trig:.0f} mg/dL (> 150) → Ajustar ingesta de azúcares simples y carbohidratos refinados.", 
            "#FFD700"
        ))

    # 8. Vitaminas B12 y Ácido Fólico
    val_b12 = _v("vitamin_b12")
    if val_b12 is not None and val_b12 < 300.0:
        alerts.append((
            "🚨", 
            "Vitamina B12 BAJA (Deficiencia)", 
            f"B12 = {val_b12:.0f} pg/mL (< 300) → Riesgo de anemia y fatiga neuromuscular. Considerar suplementación.", 
            "#FF4B4B"
        ))
    val_fol = _v("folic_acid")
    if val_fol is not None and val_fol < 4.0:
        alerts.append((
            "🚨", 
            "Ácido Fólico BAJO (Deficiencia)", 
            f"Folato = {val_fol:.1f} ng/mL (< 4.0) → Aumentar ingesta de vegetales verdes o suplementar.", 
            "#FF4B4B"
        ))

    # 9. Patrón anemia ferropénica clásico adaptado
    if (val_hb is not None and val_hb < hb_low_threshold and
        val_fer is not None and val_fer < fer_opt_min):
        alerts.append((
            "🩺", 
            f"PATRÓN: Anemia Ferropénica ({sex_label})", 
            f"Hb↓ (< {hb_low_threshold:.1f}) + Ferritina↓ (< {fer_opt_min:.0f}) → Anemia confirmada por falta de hierro. Consulta médica y suplementación urgente.", 
            "#FF4B4B"
        ))

    return alerts


# ══════════════════════════════════════════════════════════════════
#  CATÁLOGO MAESTRO DE BIOMARCADORES PARA DEPORTISTAS DE RESISTENCIA
# ══════════════════════════════════════════════════════════════════

ENDURANCE_MARKERS_CATALOG = [
    # ── 1. SERIE ROJA Y TRANSPORTE DE OXÍGENO ──
    {
        "key": "hemoglobin",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Hemoglobina Total",
        "emoji": "🔴",
        "unit": "g/dL",
        "male_opt": "14.5 – 17.5",
        "female_opt": "13.0 – 15.5",
        "male_alert": "< 13.5 (Anemia) | > 18.0 (Viscosidad)",
        "female_alert": "< 12.0 (Anemia) | > 16.0",
        "relevance": "Transporte de oxígeno en sangre. Determinante directo del VO₂max y de la capacidad de mantener ritmos de umbral elevados."
    },
    {
        "key": "vcm",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Volumen Corpuscular Medio (VCM)",
        "emoji": "🔵",
        "unit": "fL",
        "male_opt": "82.0 – 96.0",
        "female_opt": "82.0 – 96.0",
        "male_alert": "< 80.0 (Microcitosis / Fe↓) | > 98.0 (B12/Folato↓)",
        "female_alert": "< 80.0 (Microcitosis / Fe↓) | > 98.0 (B12/Folato↓)",
        "relevance": "Tamaño del eritrocito. Si baja (<80) orienta a falta de hierro; si sube (>98) a déficit de B12 o reticulocitosis por altitud."
    },
    {
        "key": "chcm",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Conc. Hb Corpuscular Media",
        "emoji": "🟡",
        "unit": "g/dL",
        "male_opt": "33.0 – 36.0",
        "female_opt": "33.0 – 36.0",
        "male_alert": "< 32.0 (Hipocromía) | > 36.5",
        "female_alert": "< 32.0 (Hipocromía) | > 36.5",
        "relevance": "Concentración de hemoglobina por eritrocito. Garantiza la capacidad de saturación de oxígeno por célula."
    },
    {
        "key": "rbc",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Glóbulos Rojos (Conteo Eritrocitario)",
        "emoji": "⭕",
        "unit": "×10⁶/μL",
        "male_opt": "4.50 – 5.80",
        "female_opt": "4.00 – 5.20",
        "male_alert": "< 4.30 | > 6.00",
        "female_alert": "< 3.80 | > 5.50",
        "relevance": "Masa total de transportadores de gas. Puede mostrar hemodilución adaptativa por expansión de plasma en atletas aeróbicos."
    },
    {
        "key": "hematocrit",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Hematocrito",
        "emoji": "🩸",
        "unit": "%",
        "male_opt": "42.0% – 50.0%",
        "female_opt": "38.0% – 46.0%",
        "male_alert": "< 40.0% | > 52.0% (Viscosidad alta)",
        "female_alert": "< 36.0% | > 48.0%",
        "relevance": "Porcentaje de glóbulos rojos sobre el volumen sanguíneo. Vital para monitorear hidratación y respuesta a concentración en altura."
    },
    {
        "key": "ferritin",
        "category": "🔴 SERIE ROJA Y TRANSPORTE DE OXÍGENO",
        "name": "Ferritina Sérica (Depósito de Hierro)",
        "emoji": "🧲",
        "unit": "ng/mL",
        "male_opt": "50.0 – 250.0",
        "female_opt": "40.0 – 150.0",
        "male_alert": "< 50.0 (Subóptimo) | < 30.0 (Crítico)",
        "female_alert": "< 35.0 (Subóptimo) | < 20.0 (Crítico)",
        "relevance": "Reserva de hierro corporal para síntesis de hemoglobina y mioglobina. En mujeres y corredores es el primer biomarcador en caer por impacto y pérdidas."
    },

    # ── 2. DAÑO MUSCULAR Y METABOLISMO CELULAR ──
    {
        "key": "ck",
        "category": "⚡ DAÑO MUSCULAR Y VITAMINAS",
        "name": "Creatina Kinasa (CK Total)",
        "emoji": "⚡",
        "unit": "U/L",
        "male_opt": "50 – 250 (Basal)",
        "female_opt": "40 – 180 (Basal)",
        "male_alert": "> 350 (Carga alta) | > 1000 (Riesgo lesión)",
        "female_alert": "> 250 (Carga alta) | > 800 (Riesgo lesión)",
        "relevance": "Marcador de microrotura y estrés muscular por impacto excéntrico o volumen. Guía la intensidad de las semanas de descarga."
    },
    {
        "key": "vitamin_b12",
        "category": "⚡ DAÑO MUSCULAR Y VITAMINAS",
        "name": "Vitamina B12 (Cobalamina)",
        "emoji": "💊",
        "unit": "pg/mL",
        "male_opt": "400 – 900",
        "female_opt": "400 – 900",
        "male_alert": "< 300 (Deficiencia) | < 400 (Límite)",
        "female_alert": "< 300 (Deficiencia) | < 400 (Límite)",
        "relevance": "Esencial para la síntesis de glóbulos rojos, sistema nervioso neuromuscular y metabolismo energético celular."
    },
    {
        "key": "folic_acid",
        "category": "⚡ DAÑO MUSCULAR Y VITAMINAS",
        "name": "Ácido Fólico (Vitamina B9)",
        "emoji": "🥬",
        "unit": "ng/mL",
        "male_opt": "6.0 – 18.0",
        "female_opt": "6.0 – 18.0",
        "male_alert": "< 4.0 (Deficiencia) | < 6.0 (Subóptimo)",
        "female_alert": "< 4.0 (Deficiencia) | < 6.0 (Subóptimo)",
        "relevance": "Coenzima imprescindible en la división celular y reparación tisular. Actúa en sinergia con la B12 en el rendimiento aeróbico."
    },

    # ── 3. PERFIL LIPÍDICO Y CARDIOVASCULAR ──
    {
        "key": "total_cholesterol",
        "category": "🫀 PERFIL LIPÍDICO CARDIOVASCULAR",
        "name": "Colesterol Total",
        "emoji": "🫀",
        "unit": "mg/dL",
        "male_opt": "130 – 190",
        "female_opt": "130 – 190",
        "male_alert": "< 120 (Déficit esteroideo) | > 200",
        "female_alert": "< 120 (Déficit esteroideo) | > 200",
        "relevance": "Precursor indispensable de hormonas esteroideas (testosterona, estrógenos, cortisol). Valores < 120 mg/dL alertan de baja disponibilidad energética (RED-S)."
    },
    {
        "key": "hdl",
        "category": "🫀 PERFIL LIPÍDICO CARDIOVASCULAR",
        "name": "Colesterol HDL (Cardioprotector)",
        "emoji": "🛡️",
        "unit": "mg/dL",
        "male_opt": "50 – 85",
        "female_opt": "60 – 95",
        "male_alert": "< 40 (Riesgo)",
        "female_alert": "< 50 (Riesgo)",
        "relevance": "Transporte inverso de colesterol. Fisiológicamente más elevado en mujeres por acción de estrógenos endógenos."
    },
    {
        "key": "ldl",
        "category": "🫀 PERFIL LIPÍDICO CARDIOVASCULAR",
        "name": "Colesterol LDL (Fracción Aterogénica)",
        "emoji": "⚠️",
        "unit": "mg/dL",
        "male_opt": "50 – 100",
        "female_opt": "50 – 100",
        "male_alert": "> 130 (Elevado) | > 160 (Alto)",
        "female_alert": "> 130 (Elevado) | > 160 (Alto)",
        "relevance": "Fracción lipoproteica aterogénica. Debe vigilarse en atletas con dietas muy altas en grasas saturadas o cetogénicas."
    },
    {
        "key": "triglycerides",
        "category": "🫀 PERFIL LIPÍDICO CARDIOVASCULAR",
        "name": "Triglicéridos",
        "emoji": "🧪",
        "unit": "mg/dL",
        "male_opt": "50 – 120",
        "female_opt": "50 – 120",
        "male_alert": "> 150 (Elevado) | > 200 (Alto)",
        "female_alert": "> 150 (Elevado) | > 200 (Alto)",
        "relevance": "Sustrato de grasas circulantes. En atletas de resistencia suelen mantenerse muy bajos gracias a la continua beta-oxidación de ácidos grasos."
    },

    # ── 4. GLUCEMIA, HBA1C E INFLAMACIÓN ──
    {
        "key": "glucose",
        "category": "🍯 METABOLISMO GLUCÍDICO E INFLAMACIÓN",
        "name": "Glucemia Basal (Ayunas)",
        "emoji": "🍯",
        "unit": "mg/dL",
        "male_opt": "75 – 95",
        "female_opt": "75 – 95",
        "male_alert": "< 70 (Hipoglucemia) | > 100 (Prediabetes)",
        "female_alert": "< 70 (Hipoglucemia) | > 100 (Prediabetes)",
        "relevance": "Nivel de glucosa en reposo. Evalúa la sensibilidad a la insulina y previene fatiga glucolítica matutina en entrenos intensos."
    },
    {
        "key": "hba1c",
        "category": "🍯 METABOLISMO GLUCÍDICO E INFLAMACIÓN",
        "name": "Hemoglobina Glicosilada (HbA1c)",
        "emoji": "🧬",
        "unit": "%",
        "male_opt": "4.8% – 5.4%",
        "female_opt": "4.8% – 5.4%",
        "male_alert": "≥ 5.7% (Prediabetes) | ≥ 6.5% (Diabetes)",
        "female_alert": "≥ 5.7% (Prediabetes) | ≥ 6.5% (Diabetes)",
        "relevance": "Promedio glucémico de los últimos 90 días. Descarte 'gold standard' de resistencia a la insulina o picos glucémicos crónicos inadvertidos."
    },
    {
        "key": "pcr_us",
        "category": "🍯 METABOLISMO GLUCÍDICO E INFLAMACIÓN",
        "name": "PCR Ultra Sensible (hs-CRP)",
        "emoji": "🔥",
        "unit": "mg/L",
        "male_opt": "< 1.0 (Sin inflamación)",
        "female_opt": "< 1.0 (Sin inflamación)",
        "male_alert": "1.0 – 3.0 (Estrés entrenamiento) | > 3.0 (Inflamación alta)",
        "female_alert": "1.0 – 3.0 (Estrés entrenamiento) | > 3.0 (Inflamación alta)",
        "relevance": "Reactante de fase aguda. Permite diferenciar entre una fatiga normal post-entreno (<1.0 mg/L) y un estado inflamatorio crónico o infección (>3.0 mg/L)."
    }
]


def generate_endurance_reference_table_html(gender="Hombre") -> str:
    """
    Genera el HTML completo de la Tabla de Referencia Médica y Deportiva,
    optimizado al 100% para dispositivos móviles y pantallas de escritorio.
    - Columna de Biomarcador fija (sticky) para nunca perder el contexto al deslizar.
    - Desplazamiento táctil fluido (-webkit-overflow-scrolling: touch).
    - Ancho compacto (580px) eliminando columnas redundantes e integrando la unidad.
    - Texto altamente legible y estructurado.
    """
    is_female = str(gender).strip().lower() in ["mujer", "femenino", "f", "female"]
    active_label = "👩 Mujer" if is_female else "👨 Hombre"
    badge_bg = "rgba(255, 118, 117, 0.25)" if is_female else "rgba(0, 238, 255, 0.2)"
    badge_color = "#FF7675" if is_female else "#00EEFF"
    badge_border = "#FF7675" if is_female else "#00EEFF"
    
    male_bg = "background: rgba(0, 238, 255, 0.12); border-left: 1.5px solid #00EEFF; border-right: 1.5px solid #00EEFF;" if not is_female else "background: rgba(255, 255, 255, 0.02);"
    female_bg = "background: rgba(255, 118, 117, 0.15); border-left: 1.5px solid #FF7675; border-right: 1.5px solid #FF7675;" if is_female else "background: rgba(255, 255, 255, 0.02);"

    rows_html = []
    current_cat = None
    for item in ENDURANCE_MARKERS_CATALOG:
        if item["category"] != current_cat:
            current_cat = item["category"]
            rows_html.append(
                f'<tr style="background: #181C2E; border-top: 1.5px solid rgba(0, 238, 255, 0.3);">'
                f'<td colspan="5" style="position: sticky; left: 0; z-index: 2; padding: 6px 10px; font-weight: 800; font-size: 0.74rem; color: #00EEFF; letter-spacing: 0.4px; background: #181C2E;">'
                f'{current_cat}'
                f'</td></tr>'
            )
        
        m_style = male_bg
        f_style = female_bg
        alert_str = ('H: ' + item['male_alert'] + '<br>M: ' + item['female_alert']) if item['male_alert'] != item['female_alert'] else item['male_alert']

        rows_html.append(
            f'<tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.06); background: rgba(15, 18, 28, 0.6);">'
            f'<td style="position: sticky; left: 0; z-index: 3; background: #111422; padding: 7px 8px; font-weight: 600; font-size: 0.77rem; border-right: 1.5px solid rgba(0, 238, 255, 0.25); white-space: normal; line-height: 1.25; min-width: 140px; max-width: 155px;">'
            f'{item["emoji"]} {item["name"]}<br><span style="color: #00EEFFCC; font-size: 0.70rem; font-weight: normal;">({item["unit"]})</span>'
            f'</td>'
            f'<td style="padding: 7px 6px; text-align: center; font-weight: bold; font-size: 0.78rem; color: #00EEFF; min-width: 90px; {m_style}">{item["male_opt"]}</td>'
            f'<td style="padding: 7px 6px; text-align: center; font-weight: bold; font-size: 0.78rem; color: #FF7675; min-width: 90px; {f_style}">{item["female_opt"]}</td>'
            f'<td style="padding: 7px 6px; text-align: center; font-size: 0.72rem; color: #D8D8D8; min-width: 110px; line-height: 1.25;">{alert_str}</td>'
            f'<td style="padding: 7px 8px; font-size: 0.72rem; color: #BBBBBB; line-height: 1.3; min-width: 150px; max-width: 220px;">{item["relevance"]}</td>'
            f'</tr>'
        )

    all_rows = "".join(rows_html)

    html = (
        f'<div style="background: #0B0E16; border: 1.5px solid #00EEFF44; border-radius: 12px; padding: 12px 10px; margin-top: 8px;">'
        f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">'
        f'<h4 style="color: #00EEFF; margin: 0; font-size: 0.95rem; font-weight: 700;">📚 GUÍA MAESTRA — ATLETAS DE RESISTENCIA</h4>'
        f'<span style="background: {badge_bg}; color: {badge_color}; font-weight: bold; padding: 3px 8px; border-radius: 6px; font-size: 0.78rem; border: 1px solid {badge_border};">'
        f'Perfil: {active_label}'
        f'</span>'
        f'</div>'
        f'<div style="font-size: 0.72rem; color: #00EEFFAA; margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">'
        f'<span>↔️ <em>Desliza horizontalmente para ver rangos y relevancia</em></span>'
        f'</div>'
        f'<div style="overflow-x: auto; -webkit-overflow-scrolling: touch; touch-action: pan-x pan-y; overscroll-behavior-x: contain; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08);">'
        f'<table style="width: 100%; border-collapse: collapse; font-size: 0.78rem; color: #FFFFFF; min-width: 580px;">'
        f'<thead>'
        f'<tr style="background: #141724; border-bottom: 2px solid #00EEFF;">'
        f'<th style="position: sticky; left: 0; z-index: 4; background: #141724; padding: 8px; text-align: left; color: #00EEFF; border-right: 1.5px solid rgba(0, 238, 255, 0.35); min-width: 140px; max-width: 155px;">Biomarcador</th>'
        f'<th style="padding: 8px 6px; text-align: center; color: #00EEFF; min-width: 90px; {male_bg}">👨 Hombres</th>'
        f'<th style="padding: 8px 6px; text-align: center; color: #FF7675; min-width: 90px; {female_bg}">👩 Mujeres</th>'
        f'<th style="padding: 8px 6px; text-align: center; color: #FFD700; min-width: 110px;">⚠️ Límites / Alertas</th>'
        f'<th style="padding: 8px; text-align: left; color: #A55EEA; min-width: 150px;">🎯 Relevancia en el Rendimiento</th>'
        f'</tr>'
        f'</thead>'
        f'<tbody>{all_rows}</tbody>'
        f'</table>'
        f'</div>'
        f'<div style="margin-top: 10px; padding: 10px; background: rgba(0, 238, 255, 0.04); border-left: 3px solid #00EEFF; border-radius: 6px; font-size: 0.74rem; color: #DDDDDD; line-height: 1.4;">'
        f'<p style="margin: 0 0 4px 0;"><strong style="color: #00EEFF;">💡 Fisiología del Rendimiento:</strong></p>'
        f'<ul style="margin: 0; padding-left: 16px;">'
        f'<li><strong style="color: #48DBFB;">Pseudoanemia del Deportista:</strong> La expansión plasmática (15-20%) diluye hemoglobina y hematocrito. Si la ferritina está óptima, es un signo de alta adaptación aeróbica.</li>'
        f'<li><strong style="color: #FF7675;">Ferritina en Mujeres:</strong> Mantenerla ≥ 35 ng/mL evita fatiga neuromuscular prematura y pérdida de potencia aeróbica.</li>'
        f'<li><strong style="color: #FECA57;">Cinética CK:</strong> Pico a las 24-48h post-esfuerzo excéntrico. Basales > 250 U/L (M) o > 350 U/L (H) exigen descarga.</li>'
        f'</ul>'
        f'</div>'
        f'</div>'
    )
    return html

