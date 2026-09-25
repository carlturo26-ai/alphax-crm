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
    """
    if not latest_rec:
        return []
    
    is_female = str(gender).strip().lower() in ["mujer", "femenino", "f", "female"]
    sex_label = "Mujer" if is_female else "Hombre"
    alerts = []

    # 1. Hemoglobina
    hb_low_threshold = 12.0 if is_female else 13.5
    hb_high_threshold = 16.0 if is_female else 18.0
    if latest_rec.hemoglobin is not None:
        if latest_rec.hemoglobin < hb_low_threshold:
            alerts.append((
                "🚨", 
                f"Hemoglobina BAJA ({sex_label})", 
                f"Hb = {latest_rec.hemoglobin:.1f} g/dL (< {hb_low_threshold:.1f}) → Posible anemia en atleta {sex_label.lower()}. Reduce capacidad aeróbica y oxigenación. Revisar ferritina y B12.", 
                "#FF4B4B"
            ))
        elif latest_rec.hemoglobin > hb_high_threshold:
            alerts.append((
                "⚠️", 
                f"Hemoglobina ALTA ({sex_label})", 
                f"Hb = {latest_rec.hemoglobin:.1f} g/dL (> {hb_high_threshold:.1f}) → Viscosidad sanguínea elevada. Revisar hidratación y aclimatación a altitud.", 
                "#FFD700"
            ))

    # 2. Ferritina
    fer_critical = 20.0 if is_female else 30.0
    fer_opt_min = 35.0 if is_female else 50.0
    if latest_rec.ferritin is not None:
        if latest_rec.ferritin < fer_critical:
            alerts.append((
                "⚡", 
                f"Ferritina CRÍTICA ({sex_label})", 
                f"Ferritina = {latest_rec.ferritin:.0f} ng/mL (< {fer_critical:.0f}) → Depósitos de hierro agotados. Suplementación y valoración urgente.", 
                "#FF4B4B"
            ))
        elif latest_rec.ferritin < fer_opt_min:
            alerts.append((
                "⚡", 
                f"Ferritina BAJA para Deportista ({sex_label})", 
                f"Ferritina = {latest_rec.ferritin:.0f} ng/mL (< {fer_opt_min:.0f}) → Reserva de hierro disminuida. Rendimiento aeróbico en riesgo.", 
                "#FFD700"
            ))

    # 3. Hematocrito
    hto_low = 36.0 if is_female else 40.0
    hto_high = 48.0 if is_female else 52.0
    if latest_rec.hematocrit is not None:
        if latest_rec.hematocrit < hto_low:
            alerts.append((
                "⚠️", 
                f"Hematocrito BAJO ({sex_label})", 
                f"Hto = {latest_rec.hematocrit:.1f}% (< {hto_low:.1f}%) → Posible hemodilución o masa eritrocitaria baja.", 
                "#FF4B4B"
            ))
        elif latest_rec.hematocrit > hto_high:
            alerts.append((
                "⚠️", 
                f"Hematocrito ALTO ({sex_label})", 
                f"Hto = {latest_rec.hematocrit:.1f}% (> {hto_high:.1f}%) → Sangre muy concentrada. Hidratarse y descansar.", 
                "#FFD700"
            ))

    # 4. Creatina Kinasa (CK)
    ck_high = 250.0 if is_female else 350.0
    if latest_rec.ck is not None:
        if latest_rec.ck > 1000.0:
            alerts.append((
                "🚨", 
                "CK MUY ELEVADA (ALARMA CLÍNICA)", 
                f"CK = {latest_rec.ck:.0f} U/L → Daño muscular agudo o sobreentrenamiento severo. Requiere reposo absoluto y supervisión médica.", 
                "#FF4B4B"
            ))
        elif latest_rec.ck > ck_high:
            alerts.append((
                "⚠️", 
                f"CK ELEVADA — Fatiga Muscular ({sex_label})", 
                f"CK = {latest_rec.ck:.0f} U/L (> {ck_high:.0f}) → Daño muscular por carga reciente. Priorizar descanso y recuperación activa.", 
                "#FFD700"
            ))

    # 5. Proteína C Reactiva Ultra Sensible (PCR-us)
    if latest_rec.pcr_us is not None:
        if latest_rec.pcr_us > 3.0:
            alerts.append((
                "🔥", 
                "PCR-us ELEVADA (Inflamación Sistémica)", 
                f"PCR-us = {latest_rec.pcr_us:.2f} mg/L (> 3.0) → Inflamación alta. Posible sobrecarga de entreno, infección latente o microlesión.", 
                "#FF4B4B"
            ))
        elif latest_rec.pcr_us > 1.0:
            alerts.append((
                "⚠️", 
                "PCR-us MODERADA (Estrés de Entrenamiento)", 
                f"PCR-us = {latest_rec.pcr_us:.2f} mg/L (1.0–3.0) → Estado inflamatorio residual de la carga física reciente.", 
                "#FFD700"
            ))

    # 6. Glucemia
    if latest_rec.glucose is not None:
        if latest_rec.glucose < 70.0:
            alerts.append((
                "⚠️", 
                "Glucemia BAJA (Hipoglucemia en Ayunas)", 
                f"Glucosa = {latest_rec.glucose:.0f} mg/dL (< 70) → Riesgo de hipoglucemia y fatiga glucolítica en entrenamientos.", 
                "#FF4B4B"
            ))
        elif latest_rec.glucose > 100.0:
            alerts.append((
                "⚠️", 
                "Glucemia ELEVADA en Ayunas", 
                f"Glucosa = {latest_rec.glucose:.0f} mg/dL (> 100) → Posible alteración en el metabolismo de carbohidratos o estrés agudo.", 
                "#FFD700"
            ))

    # 7. Perfil Lipídico
    hdl_min = 50.0 if is_female else 40.0
    if latest_rec.hdl is not None and latest_rec.hdl < hdl_min:
        alerts.append((
            "⚠️", 
            f"Colesterol HDL BAJO ({sex_label})", 
            f"HDL = {latest_rec.hdl:.0f} mg/dL (< {hdl_min:.0f}) → Nivel subóptimo de colesterol cardioprotector.", 
            "#FFD700"
        ))
    if latest_rec.ldl is not None and latest_rec.ldl > 130.0:
        alerts.append((
            "⚠️", 
            "Colesterol LDL ELEVADO", 
            f"LDL = {latest_rec.ldl:.0f} mg/dL (> 130) → Fracción aterogénica alta. Revisar composición de grasas en la dieta.", 
            "#FFD700"
        ))
    if latest_rec.triglycerides is not None and latest_rec.triglycerides > 150.0:
        alerts.append((
            "⚠️", 
            "Triglicéridos ELEVADOS", 
            f"Triglicéridos = {latest_rec.triglycerides:.0f} mg/dL (> 150) → Ajustar ingesta de azúcares simples y carbohidratos refinados.", 
            "#FFD700"
        ))

    # 8. Vitaminas B12 y Ácido Fólico
    if latest_rec.vitamin_b12 is not None and latest_rec.vitamin_b12 < 300.0:
        alerts.append((
            "🚨", 
            "Vitamina B12 BAJA (Deficiencia)", 
            f"B12 = {latest_rec.vitamin_b12:.0f} pg/mL (< 300) → Riesgo de anemia y fatiga neuromuscular. Considerar suplementación.", 
            "#FF4B4B"
        ))
    if latest_rec.folic_acid is not None and latest_rec.folic_acid < 4.0:
        alerts.append((
            "🚨", 
            "Ácido Fólico BAJO (Deficiencia)", 
            f"Folato = {latest_rec.folic_acid:.1f} ng/mL (< 4.0) → Aumentar ingesta de vegetales verdes o suplementar.", 
            "#FF4B4B"
        ))

    # 9. Patrón anemia ferropénica clásico adaptado
    if (latest_rec.hemoglobin is not None and latest_rec.hemoglobin < hb_low_threshold and
        latest_rec.ferritin is not None and latest_rec.ferritin < fer_opt_min):
        alerts.append((
            "🩺", 
            f"PATRÓN: Anemia Ferropénica ({sex_label})", 
            f"Hb↓ (< {hb_low_threshold:.1f}) + Ferritina↓ (< {fer_opt_min:.0f}) → Anemia confirmada por falta de hierro. Consulta médica y suplementación urgente.", 
            "#FF4B4B"
        ))

    return alerts
