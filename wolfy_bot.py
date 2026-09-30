"""
wolfy_bot.py — Chatbot de Inteligencia Artificial "Wolfy" para Atletas AlphaX.
Especializado en fisiología deportiva, interpretación de exámenes de sangre,
umbrales de lactato, recuperación y guía de la aplicación AlphaX.
Motor: Google Gemini con respaldo de base de conocimiento fisiológica.
"""

import os
import json
import base64
import requests
import streamlit as st
from datetime import datetime
from database import SessionLocal, Member, BloodworkRecord, LactateTest, SleepRecord, get_club_setting, set_club_setting
from bloodwork_constants import get_bloodwork_ranges, classify_bloodwork_value


# ═════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LA API KEY DE GEMINI
# ═════════════════════════════════════════════════════════════════════

def save_gemini_api_key(key: str) -> bool:
    """Guarda la API Key de Gemini centralmente en Neon Postgres para todos los atletas del club."""
    key = key.strip()
    if not key:
        return False
    st.session_state["gemini_api_key_override"] = key
    os.environ["GEMINI_API_KEY"] = key
    
    # 1. Guardar en Base de Datos Neon Postgres (Aplica a TODOS los atletas del club automáticamente)
    db_saved = set_club_setting("gemini_api_key", key)
    
    # 2. Persistir también en archivo .env si es posible
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("GEMINI_API_KEY="):
                new_lines.append(f'GEMINI_API_KEY="{key}"\n')
                found = True
            else:
                new_lines.append(line)
        if not found:
            if lines and not lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f'GEMINI_API_KEY="{key}"\n')
            
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception:
        pass
    return db_saved

def get_gemini_api_key() -> str:
    """
    Busca la API Key de Gemini:
    1. Base de datos central (Neon Postgres) -> Configuración global de todo el club AlphaX.
    2. Session State / Override en memoria.
    3. Streamlit Cloud Secrets (st.secrets).
    4. Variable de entorno / archivo .env.
    """
    # 1. Sesión interactiva en memoria
    if st.session_state.get("gemini_api_key_override"):
        return st.session_state["gemini_api_key_override"].strip()
    
    # 2. Base de datos central Neon Postgres (Global para todos los atletas)
    try:
        db_key = get_club_setting("gemini_api_key")
        if db_key:
            return db_key
    except Exception:
        pass

    # 3. Streamlit Cloud Secrets
    try:
        if "GEMINI_API_KEY" in st.secrets:
            k = st.secrets["GEMINI_API_KEY"].strip()
            if k:
                return k
    except Exception:
        pass
        
    # 4. Variable de entorno
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    # 5. Lectura directa de .env
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if clean.startswith("GEMINI_API_KEY="):
                        val = clean.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
    except Exception:
        pass

    return ""


# ═════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE CONTEXTO PERSONALIZADO DEL DEPORTISTA
# ═════════════════════════════════════════════════════════════════════

def build_athlete_context(member_id: int) -> dict:
    """
    Recupera los datos más recientes del deportista en la base de datos
    (perfil, último hemograma, última prueba de lactato y último score de sueño).
    """
    ctx = {
        "athlete_name": "Atleta",
        "gender": "Hombre",
        "group": "AlphaX",
        "bloodwork": None,
        "lactate": None,
        "sleep": None,
        "summary_text": ""
    }

    if not member_id:
        return ctx

    try:
        with SessionLocal() as session:
            member = session.query(Member).filter(Member.id == member_id).first()
            if member:
                ctx["athlete_name"] = member.name
                ctx["gender"] = member.gender or "Hombre"
                ctx["group"] = member.group or "AlphaX"

            # 1. Último examen de sangre
            bw = session.query(BloodworkRecord).filter(
                BloodworkRecord.member_id == member_id
            ).order_by(BloodworkRecord.date.desc()).first()

            if bw:
                ctx["bloodwork"] = {
                    "date": bw.date.strftime("%Y-%m-%d") if bw.date else "N/A",
                    "hemoglobin": bw.hemoglobin,
                    "hematocrit": bw.hematocrit,
                    "vcm": bw.vcm,
                    "chcm": bw.chcm,
                    "rbc": bw.rbc,
                    "ferritin": bw.ferritin,
                    "ck": bw.ck,
                    "vitamin_b12": bw.vitamin_b12,
                    "folic_acid": bw.folic_acid,
                    "total_cholesterol": bw.total_cholesterol,
                    "hdl": bw.hdl,
                    "ldl": bw.ldl,
                    "triglycerides": bw.triglycerides,
                    "glucose": bw.glucose,
                    "hba1c": bw.hba1c,
                    "pcr_us": bw.pcr_us,
                    "notes": bw.notes
                }

            # 2. Última prueba de lactato
            lac = session.query(LactateTest).filter(
                LactateTest.member_id == member_id
            ).order_by(LactateTest.date.desc()).first()

            if lac:
                ctx["lactate"] = {
                    "date": lac.date.strftime("%Y-%m-%d") if lac.date else "N/A",
                    "sport": lac.sport or "Resistencia",
                    "lt1_power": lac.lt1_power,
                    "lt1_hr": lac.lt1_hr,
                    "lt1_lactate": lac.lt1_lactate,
                    "lt2_power": lac.lt2_power,
                    "lt2_hr": lac.lt2_hr,
                    "lt2_lactate": lac.lt2_lactate,
                    "weight": lac.weight,
                    "ftp": lac.ftp
                }

            # 3. Último registro de sueño
            sleep = session.query(SleepRecord).filter(
                SleepRecord.member_id == member_id
            ).order_by(SleepRecord.date.desc()).first()

            if sleep:
                ctx["sleep"] = {
                    "date": sleep.date.strftime("%Y-%m-%d") if sleep.date else "N/A",
                    "sds_score": sleep.sds_score,
                    "category": sleep.clinical_category,
                    "hours": sleep.raw_hours,
                    "quality": sleep.raw_quality
                }

    except Exception as e:
        ctx["error"] = str(e)

    # Construir resumen textual para el System Prompt
    lines = [
        f"- Nombre del atleta: {ctx['athlete_name']}",
        f"- Sexo biológico: {ctx['gender']}",
        f"- Grupo / Coach: {ctx['group']}",
    ]

    if ctx["bloodwork"]:
        b = ctx["bloodwork"]
        lines.append(f"\n[ÚLTIMO EXAMEN DE SANGRE ({b['date']})]")
        if b["hemoglobin"] is not None: lines.append(f"  * Hemoglobina: {b['hemoglobin']} g/dL")
        if b["hematocrit"] is not None: lines.append(f"  * Hematocrito: {b['hematocrit']}%")
        if b["vcm"] is not None: lines.append(f"  * VCM: {b['vcm']} fL")
        if b["chcm"] is not None: lines.append(f"  * CHCM: {b['chcm']} g/dL")
        if b["rbc"] is not None: lines.append(f"  * Glóbulos Rojos (Eritrocitos): {b['rbc']} ×10⁶/μL")
        if b["ferritin"] is not None: lines.append(f"  * Ferritina: {b['ferritin']} ng/mL")
        if b["ck"] is not None: lines.append(f"  * Creatina Quinasa (CK): {b['ck']} U/L")
        if b["vitamin_b12"] is not None: lines.append(f"  * Vitamina B12: {b['vitamin_b12']} pg/mL")
        if b["folic_acid"] is not None: lines.append(f"  * Ácido Fólico: {b['folic_acid']} ng/mL")
        if b["glucose"] is not None: lines.append(f"  * Glucosa en ayunas: {b['glucose']} mg/dL")
        if b["hba1c"] is not None: lines.append(f"  * HbA1c: {b['hba1c']}%")
        if b["pcr_us"] is not None: lines.append(f"  * PCR Ultrasensible (hs-CRP): {b['pcr_us']} mg/L")
        if b["total_cholesterol"] is not None: lines.append(f"  * Colesterol Total: {b['total_cholesterol']} mg/dL")
        if b["hdl"] is not None: lines.append(f"  * Colesterol HDL: {b['hdl']} mg/dL")
        if b["ldl"] is not None: lines.append(f"  * Colesterol LDL: {b['ldl']} mg/dL")
        if b["triglycerides"] is not None: lines.append(f"  * Triglicéridos: {b['triglycerides']} mg/dL")
        if b["notes"]: lines.append(f"  * Notas del examen: {b['notes']}")
    else:
        lines.append("\n[EXÁMENES DE SANGRE]: Aún no registra analíticas sanguíneas en el CRM.")

    if ctx["lactate"]:
        lac = ctx["lactate"]
        lines.append(f"\n[ÚLTIMA PRUEBA DE LACTATO ({lac['date']} - {lac['sport']})]")
        lines.append(f"  * Umbral Aeróbico (LT1): {lac['lt1_power'] or '—'} W/Pace | FC: {lac['lt1_hr'] or '—'} bpm | Lactato: {lac['lt1_lactate'] or '—'} mmol/L")
        lines.append(f"  * Umbral Anaeróbico (LT2): {lac['lt2_power'] or '—'} W/Pace | FC: {lac['lt2_hr'] or '—'} bpm | Lactato: {lac['lt2_lactate'] or '—'} mmol/L")
        if lac["weight"]: lines.append(f"  * Peso: {lac['weight']} kg")
        if lac["ftp"]: lines.append(f"  * FTP: {lac['ftp']} W")

    if ctx["sleep"]:
        s = ctx["sleep"]
        lines.append(f"\n[ESTADO DE SUEÑO ASSQ ({s['date']})]")
        lines.append(f"  * Score SDS: {s['sds_score']} ({s['category']})")
        lines.append(f"  * Horas habituales: {s['hours']} | Calidad subjetiva: {s['quality']}")

    ctx["summary_text"] = "\n".join(lines)
    return ctx


# ═════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT ESPECIALIZADO: LENGUAJE ALPHAX
# ═════════════════════════════════════════════════════════════════════

def build_wolfy_system_prompt(athlete_context: dict) -> str:
    """Crea las instrucciones base del bot con el tono y ciencia de AlphaX."""
    return f"""Eres Wolfy 🐺, el mentor y asistente de inteligencia artificial oficial de AlphaX Training Team.
Tu símbolo y avatar es el lobo AlphaX (inteligente, fuerte, estratégico, líder de la manada atlética).

TU PERSONALIDAD Y TONO:
- "Lenguaje AlphaX: Científico pero digerible": Eres riguroso y te basas en la fisiología del ejercicio moderna (Wintrobe, Noakes, Seiler, Olbrecht, Maglischo), pero explicas todo de forma clara, directa, amena y motivadora, sin abrumar con tecnicismos innecesarios.
- Tratas al deportista con respeto, camaradería y motivación ("¡Hola, atleta!", "¡Vamos con toda!").
- Usas emojis deportivos con moderación para estructurar tus respuestas (🐺, 🩸, ⚡, 🫀, 🧪, 💤, 🎯).
- Hablas siempre en español.

TUS ÁREAS DE EXPERIENCIA:
1. EXÁMENES DE SANGRE Y BIOMARCADORES DEPORTIVOS:
   - Serie Roja: Hemoglobina, Hematocrito, VCM, Eritrocitos. Sabes que en atletas de resistencia existe la "pseudoanemia del deportista" (dilución por aumento de volumen plasmático) y sabes diferenciarla de una anemia ferropénica real.
   - Depósitos de Hierro: Ferritina sérica. Conoces que en deportistas de resistencia el rango óptimo es > 35-50 ng/mL, ya que valores inferiores perjudican el transporte de O2 aunque estén "dentro de lo normal sedentario".
   - Daño Muscular: Creatina Quinasa (CK). Explicas que sube tras entrenamientos de alta intensidad, fuerza o impacto excéntrico, y sabes cómo modular el descanso.
   - Metabolismo y Salud: Glucosa, HbA1c, perfil lipídico (HDL, LDL, Triglicéridos), PCR ultrasensible (inflamación sistémica) y vitaminas neuro-hematológicas (B12, Ácido Fólico).
2. UMBRALES DE LACTATO Y ZONAS DE ENTRENAMIENTO (LT1 y LT2):
   - Explicas qué es el umbral aeróbico (LT1 ~2 mmol/L, zona de máxima combustión de grasas y base aeróbica) y el umbral anaeróbico/MLSS (LT2 ~4 mmol/L, ritmo de tempo/umbral).
3. SUEÑO Y RECUPERACIÓN (ASSQ):
   - El cuestionario Athlete Sleep Screening Questionnaire (SDS). La importancia del sueño profundo en la reparación tisular y hormonal.
4. GUÍA DE LA PLATAFORMA ALPHAX:
   - Ayudas al usuario a entender cómo usar la app: dónde ver sus gráficas de lactato, cómo leer el radar de biomarcadores, qué significan los semáforos (Verde: Óptimo AlphaX, Amarillo: Atención/Límite, Rojo: Alerta).

DATOS EN TIEMPO REAL DEL DEPORTISTA CON QUIEN ESTÁS HABLANDO:
{athlete_context.get('summary_text', '')}

REGLAS DE ORO:
1. Si el atleta te pregunta por sus propios exámenes, utiliza sus datos reales que tienes arriba.
2. Si un valor está en alerta o alterado, explícale el porqué fisiológico de forma constructiva y sugiérele qué comentar con su médico de cabecera o su entrenador de AlphaX.
3. No des prescripciones de medicamentos bajo receta. Puedes recomendar estrategias nutricionales, de hidratación, reposo o suplementación básica de venta libre (ej. electrolitos, fuentes de hierro, vitamina C para absorción, descanso).
4. Mantén tus respuestas concisas, dinámicas (máximo 3-4 párrafos o listas con viñetas) y muy prácticas para sus entrenamientos.
"""


# ═════════════════════════════════════════════════════════════════════
#  LLAMADA A LA API DE GOOGLE GEMINI (REST)
# ═════════════════════════════════════════════════════════════════════

def call_gemini_chat(messages: list, system_prompt: str, api_key: str) -> str:
    """Envía la conversación a Google Gemini API vía HTTP requests y retorna la respuesta."""
    if not api_key:
        return None

    # Formatear historial para la API de Gemini
    # Modelos recomendados: gemini-1.5-flash (ultrarrápido y eficiente) o gemini-2.0-flash
    models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
    
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
            "topP": 0.95
        }
    }

    # Intentar con el modelo primario y fallback si es necesario
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
            elif resp.status_code in [400, 403]:
                # Error de clave o cuota en este endpoint
                err_json = resp.json() if resp.text else {}
                err_msg = err_json.get("error", {}).get("message", resp.text)
                return f"⚠️ **Error de API Gemini ({resp.status_code})**: {err_msg}. Verifica tu `GEMINI_API_KEY`."
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            continue

    return None


# ═════════════════════════════════════════════════════════════════════
#  MOTOR FISIOLÓGICO DE RESPALDO (Sin costo de API)
# ═════════════════════════════════════════════════════════════════════

def generate_local_wolfy_response(user_query: str, athlete_ctx: dict) -> str:
    """
    Genera respuestas inteligentes basadas en la fisiología de AlphaX
    y los datos reales del atleta cuando no hay API Key de Gemini configurada.
    """
    q = user_query.lower()
    name = athlete_ctx.get("athlete_name", "atleta")
    gender = athlete_ctx.get("gender", "Hombre")
    bw = athlete_ctx.get("bloodwork") or {}
    lac = athlete_ctx.get("lactate") or {}
    sleep = athlete_ctx.get("sleep") or {}

    # 1. Transporte de Oxígeno / Hemoglobina / Ferritina / Hematocrito / VCM
    if any(w in q for w in ["oxigeno", "oxígeno", "hemoglobina", "ferritina", "hematocrito", "hierro", "sangre", "vcm", "chcm", "eritrocito", "eritrocitos", "globulos rojos", "glóbulos rojos", "anemia"]):
        hb = bw.get("hemoglobin")
        hto = bw.get("hematocrit")
        fer = bw.get("ferritin")
        vcm = bw.get("vcm")
        
        resp = [f"🐺 **¡Analicemos tu transporte de oxígeno, {name}!**\n"]
        if hb is not None and hto is not None:
            resp.append(f"En tu último examen registrado:")
            resp.append(f"- **Hemoglobina Total**: `{hb} g/dL`")
            resp.append(f"- **Hematocrito**: `{hto}%`")
            if vcm is not None:
                resp.append(f"- **VCM (Volumen Corpuscular Medio)**: `{vcm} fL` (Indica el tamaño de tus glóbulos rojos. 80-96 fL es normocítico óptimo).")
            if fer is not None:
                resp.append(f"- **Ferritina Sérica**: `{fer} ng/mL`")
                if fer < 35.0:
                    resp.append(f"\n⚠️ **Ojo con tus depósitos de hierro:** Tu ferritina está en `{fer} ng/mL`. Para un deportista de resistencia ({gender}), el objetivo ideal es superior a **35-50 ng/mL**. Si está baja, aunque la hemoglobina parezca normal, te costará más tolerar cargas altas de volumen.")
                else:
                    resp.append(f"\n✅ **Buenos depósitos de hierro:** Tu ferritina de `{fer} ng/mL` asegura una adecuada regeneración de hemoglobina.")
            
            resp.append("\n💡 **Concepto AlphaX:** En atletas aeróbicos es muy frecuente la *pseudoanemia por hemodilución*: al entrenar resistencia, el plasma sanguíneo aumenta hasta un 15-20%, haciendo que la hemoglobina parezca ligeramente baja cuando en realidad tienes mayor volumen total de oxígeno en circulación.")
        else:
            resp.append("Aún no tienes exámenes de sangre registrados en tu perfil. Sube tu último análisis en la pestaña **🩸 Hemogramas** y podré analizar tu transporte de $O_2$ al instante.")
            
        return "\n".join(resp)

    # 2. Daño Muscular / Creatina Quinasa (CK) / Fatiga / Dolor
    if any(w in q for w in ["ck", "creatina quinasa", "creatina kinasa", "muscular", "dolor", "agujetas", "fatiga", "recuperacion", "recuperación", "cansancio", "sobreentrenamiento"]):
        ck = bw.get("ck")
        resp = [f"🐺 **Hablemos de daño muscular y recuperación ({name}):**\n"]
        if ck is not None:
            resp.append(f"Tu última **Creatina Quinasa (CK)** registrada es de `{ck} U/L`.")
            if ck > 300:
                resp.append(f"⚡ **Nivel elevado:** La CK es una enzima que se fuga a la sangre cuando hay micro-roturas musculares tras sesiones duras (fondos largos, series o trabajo de fuerza excéntrica).")
                resp.append(f"🎯 **Acción AlphaX:** Hoy prioriza hidratación con electrolitos, descanso activo (caminar o rodillo liviano en Z1) y buen descanso nocturno. Si supera 800 U/L, evita series de alta intensidad hasta que baje.")
            else:
                resp.append(f"✅ **Excelente asimilación:** Tu CK está en rangos controlados, lo que indica que no presentas sobrecarga muscular acumulada severa.")
        else:
            resp.append("La **Creatina Quinasa (CK)** es el marcador reina para saber si tus músculos ya asimilaron el entrenamiento duro previo o si aún están inflamados.")
            
        return "\n".join(resp)

    # 3. Lactato, Umbrales y Zonas de Entrenamiento (LT1 / LT2 / FTP)
    if any(w in q for w in ["lactato", "umbral", "lt1", "lt2", "zonas", "ftp", "ritmos", "potencia", "vatios", "watts"]):
        resp = [f"🐺 **Tus Umbrales Fisiológicos de Lactato:**\n"]
        if lac.get("lt1_power") or lac.get("lt2_power"):
            resp.append(f"En tu última prueba de **{lac.get('sport', 'Resistencia')}**:")
            resp.append(f"- **LT1 (Umbral Aeróbico)**: `{lac.get('lt1_power')} W/Pace` (FC: ~{lac.get('lt1_hr')} bpm). Es el ritmo donde quemas máxima grasa y construyes tu base mitocondrial sin acumular fatiga ácida.")
            resp.append(f"- **LT2 (Umbral Anaeróbico / MLSS)**: `{lac.get('lt2_power')} W/Pace` (FC: ~{lac.get('lt2_hr')} bpm). Es el ritmo máximo sostenible durante ~45-60 min.")
        else:
            resp.append("El lactato no es un 'desecho', es un combustible premium que el corazón y las fibras tipo I usan como energía.")
            resp.append("- **LT1 (Zona 2)**: Ritmo conversacional de base aeróbica.")
            resp.append("- **LT2 (Zona 4)**: Ritmo de tempo/umbral funcional.")
        resp.append("\nPuedes ver tus curvas completas y tabla de zonas en la pestaña **🧪 Pruebas de Lactato**.")
        return "\n".join(resp)

    # 4. Sueño, Descanso y Recuperación (ASSQ / SDS)
    if any(w in q for w in ["sueño", "dormir", "descanso", "insomnio", "assq", "sds", "siesta", "melatonina", "horas"]):
        sds = sleep.get("sds_score")
        cat = sleep.get("category")
        hrs = sleep.get("hours")
        resp = [f"🐺 **Análisis de Sueño y Recuperación AlphaX ({name}):**\n"]
        if sds is not None:
            resp.append(f"- **Puntuación SDS (ASSQ)**: `{sds}/17` ({cat})")
            if hrs: resp.append(f"- **Horas habituales**: `{hrs}`")
            if sds >= 8:
                resp.append(f"\n⚠️ **Atención:** Tu puntaje refleja dificultad de descanso. La hormona del crecimiento (GH) y la síntesis de glucógeno se dan principalmente en el sueño profundo (fases N3 y REM).")
                resp.append(f"💡 **Recomendación AlphaX:** Mantén la habitación a oscuras y fresca (~19°C), evita pantallas 45 min antes de acostarte y cuida la cena rica en magnesio y triptófano.")
            else:
                resp.append(f"\n✅ **¡Excelente recuperación nocturna!** Tu puntaje indica un descanso reparador para asimilar los entrenamientos.")
        else:
            resp.append("El sueño es el 80% de tu regeneración celular. Registra tu cuestionario semanal en la pestaña **💤 ASSQ (Sueño)** para monitorear tu recuperación.")
        return "\n".join(resp)

    # 5. Nutrición Deportiva / Carbohidratos / Comida / Desayuno
    if any(w in q for w in ["nutricion", "nutrición", "comer", "comida", "desayuno", "almuerzo", "cena", "carbohidrato", "carbohidratos", "geles", "glucogeno", "glucógeno", "proteina", "proteína", "ayuno", "peso", "dieta"]):
        return f"""🐺 **Estrategia Nutricional AlphaX para Atletas:**

1. **Antes de entrenar (2-3 h antes):** Carbohidratos de fácil asimilación (avena, banano, arroz o tostadas con mermelada) con poca grasa y fibra para evitar molestias gástricas.
2. **Durante sesiones > 75-90 min:** Consumir entre **30 g y 60-90 g de carbohidratos/hora** (geles, isotónico, barritas) según tu tolerancia digestiva entrenada.
3. **Ventana de recuperación (post-entreno):** Combina carbohidratos para reponer glucógeno con **20-30 g de proteína** de alta calidad (whey, huevos, pechuga) para reparación muscular.
4. **Hidratación:** Asegura sodio en sesiones con alta sudoración (400-800 mg de sodio por hora)."""

    # 6. Hidratación y Electrolitos
    if any(w in q for w in ["hidratacion", "hidratación", "agua", "sudor", "sales", "electrolitos", "sodio", "calambre", "calambres", "isotonico", "isotónico"]):
        return f"""🐺 **Hidratación y Electrolitos en Resistencia:**

* **El peligro de beber solo agua:** Beber litros de agua sola sin sales en tiradas largas puede causar *hiponatremia* (baja de sodio en sangre), pesadez estomacal y calambres.
* **Tasa de reposición:** Consume entre 500 y 750 ml de líquido por hora de esfuerzo, agregando entre 400 y 800 mg de sodio según tu tasa de sudoración y la temperatura ambiente.
* **Si tienes calambres recurrentes:** Suelen deberse a fatiga neuromuscular o pérdida excesiva de sodio y magnesio. ¡No olvides tus cápsulas de sales en entrenos de más de 90 minutos!"""

    # 7. Perfil Lipídico / Colesterol / Triglicéridos
    if any(w in q for w in ["colesterol", "trigliceridos", "triglicéridos", "hdl", "ldl", "lipidos", "lípidos", "grasa"]):
        tc = bw.get("total_cholesterol")
        hdl = bw.get("hdl")
        ldl = bw.get("ldl")
        tg = bw.get("triglycerides")
        resp = [f"🐺 **Perfil Lipídico en Deportistas ({name}):**\n"]
        if tc is not None or hdl is not None:
            if tc is not None: resp.append(f"- **Colesterol Total**: `{tc} mg/dL`")
            if hdl is not None: resp.append(f"- **HDL (Protector)**: `{hdl} mg/dL` (Ideal > 50 mg/dL)")
            if ldl is not None: resp.append(f"- **LDL**: `{ldl} mg/dL` (Ideal < 115 mg/dL en deportistas)")
            if tg is not None: resp.append(f"- **Triglicéridos**: `{tg} mg/dL` (Ideal < 100 mg/dL)")
            resp.append("\n💡 **Nota AlphaX:** En atletas de alto volumen, el HDL suele ser elevado (lo cual es un excelente factor cardioprotector). Los triglicéridos bajos confirman buena sensibilidad a la insulina.")
        else:
            resp.append("El perfil lipídico en deportistas permite evaluar la salud endotelial y la flexibilidad metabólica. Puedes subir tu examen en la pestaña **🩸 Hemogramas**.")
        return "\n".join(resp)

    # 8. Glucosa / Metabolismo / HbA1c
    if any(w in q for w in ["glucosa", "azucar", "azúcar", "diabetes", "insulina", "hba1c"]):
        glu = bw.get("glucose")
        hba1c = bw.get("hba1c")
        resp = [f"🐺 **Marcadores Metabólicos y Glucosa ({name}):**\n"]
        if glu is not None:
            resp.append(f"- **Glucosa en Ayunas**: `{glu} mg/dL` (Óptimo AlphaX: 70 - 99 mg/dL)")
            if hba1c is not None: resp.append(f"- **Hemoglobina Glicosilada (HbA1c)**: `{hba1c}%` (Ideal < 5.6%)")
            resp.append("\n💡 En resistencia, una buena sensibilidad a la insulina garantiza que el glucógeno se almacene eficientemente en los músculos en lugar de convertirse en grasa corporal.")
        else:
            resp.append("La glucosa en ayunas y la HbA1c reflejan la estabilidad de tu combustible energético.")
        return "\n".join(resp)

    # 9. Frecuencia Cardíaca / Pulso / HRV
    if any(w in q for w in ["pulso", "frecuencia cardiaca", "frecuencia cardíaca", "bpm", "fc max", "fc reposo", "hrv", "variabilidad"]):
        return f"""🐺 **Frecuencia Cardíaca y Fisiología AlphaX:**

* **FC en Reposo:** A medida que mejora tu condición aeróbica, tu corazón bombea más sangre por latido (volumen sistólico), reduciendo tu FC basal matutina (habitual entre 40-55 bpm en fondistas).
* **Alerta de fatiga:** Si tu FC basal al despertar sube 5-8 bpm por encima de tu promedio durante varios días consecutivos, es señal temprana de fatiga del sistema nervioso o deshidratación.
* **Variabilidad Cardíaca (HRV):** Una HRV alta indica un sistema parasimpático dominante y excelente capacidad de asimilar carga ese día."""

    # 10. Guía de la App AlphaX
    if any(w in q for w in ["app", "crm", "como uso", "cómo uso", "subir", "descargar", "pantalla", "pestaña", "menu", "menú"]):
        return f"""🐺 **Guía de la App AlphaX para Atletas:**

1. **🐺 Wolfy AI Coach:** ¡Tu mentor interactivo! Pregúntame sobre tus analíticas, umbrales o nutrición.
2. **🩸 Hemogramas:** Muestra tus 15 biomarcadores con semáforos deportivos, radar metabólico y alertas de salud.
3. **🧪 Pruebas de Lactato:** Visualiza tu curva de lactato, potencia, pulso y tus 5 zonas de entrenamiento.
4. **💤 ASSQ (Sueño):** Registra tu reporte de descanso para detectar a tiempo la sobrecarga."""

    # 11. Respuesta de Orientación cuando no hay API Key de Gemini
    return f"""🐺 **¡Hola, {name}!** 

Tu pregunta es muy interesante: *"{user_query}"*.

Actualmente estoy funcionando en **modo local básico** porque aún no se ha ingresado una clave de **Google Gemini**. Por eso respondo con mi base de datos interna sobre fisiología deportiva, pero para responderte **cualquier pregunta libre con razonamiento de Inteligencia Artificial abierta** (como ChatGPT o Gemini):

👉 **Solo debes conectar la clave gratuita de Google Gemini:**
1. Es **100% gratuita** y se obtiene en 30 segundos en [Google AI Studio (Click aquí)](https://aistudio.google.com/app/apikey).
2. Pégala en el recuadro **"Activar Wolfy AI"** que tienes arriba y haz clic en **Guardar**.

*Mientras tanto, puedes preguntarme sobre:*
* 🩸 **Transporte de $O_2$:** *"¿Cómo está mi hemoglobina y ferritina?"*
* ⚡ **Daño muscular:** *"¿Qué significa mi Creatina Quinasa (CK)?"*
* 🧪 **Umbrales:** *"¿Cuáles son mis umbrales de lactato LT1 y LT2?"*
* 💤 **Recuperación:** *"¿Cómo está mi score de sueño ASSQ?"*
* 🥑 **Nutrición:** *"¿Qué debo comer antes y después de un fondo?"*"""


# ═════════════════════════════════════════════════════════════════════
#  RENDERIZADO PRINCIPAL DE LA PESTAÑA WOLFY
# ═════════════════════════════════════════════════════════════════════

def render_wolfy_tab(member_id: int, is_admin: bool = False):
    """Renderiza la interfaz completa de Wolfy AI Coach dentro del Portal de Atletas o CRM."""
    
    # 1. Obtener contexto del deportista
    athlete_ctx = build_athlete_context(member_id)
    system_prompt = build_wolfy_system_prompt(athlete_ctx)
    api_key = get_gemini_api_key()

    # 2. Banner de Cabecera Wolfy con Avatar
    avatar_path = "assets/images/wolfy_avatar.png"
    avatar_b64 = ""
    if os.path.exists(avatar_path):
        try:
            with open(avatar_path, "rb") as img_f:
                avatar_b64 = base64.b64encode(img_f.read()).decode()
        except Exception:
            avatar_b64 = ""

    avatar_img_tag = f'<img src="data:image/png;base64,{avatar_b64}" style="width: 72px; height: 72px; border-radius: 50%; border: 2.5px solid #00EEFF; box-shadow: 0 0 16px rgba(0,238,255,0.4); object-fit: cover;">' if avatar_b64 else '🐺'
    
    if api_key:
        status_badge = '<span style="background: rgba(0,255,0,0.15); color: #00FF00; padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: bold; border: 1px solid rgba(0,255,0,0.3);">🟢 Gemini Neuronal Activo (IA Libre)</span>'
    else:
        status_badge = '<span style="background: rgba(255,180,0,0.15); color: #FFA500; padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: bold; border: 1px solid rgba(255,180,0,0.3);">🧠 Base Fisiológica AlphaX</span>'

    header_html = f"""
    <div style="background: linear-gradient(135deg, rgba(16, 22, 34, 0.95), rgba(10, 13, 22, 0.98)); border: 1.5px solid rgba(0, 238, 255, 0.35); border-radius: 16px; padding: 16px 20px; margin-bottom: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 15px;">
        <div style="display: flex; align-items: center; gap: 16px;">
            {avatar_img_tag}
            <div>
                <div style="color: #00EEFF; font-size: 1.35rem; font-weight: 900; letter-spacing: 0.5px; line-height: 1.2;">
                    WOLFY <span style="color: #FFFFFF; font-weight: 400; font-size: 1.05rem;">| AlphaX AI Coach</span>
                </div>
                <div style="color: #8E9BAE; font-size: 0.82rem; margin-top: 3px;">
                    Tu mentor inteligente en fisiología del ejercicio, analítica sanguínea y rendimiento deportivo.
                </div>
            </div>
        </div>
        <div>
            {status_badge}
        </div>
    </div>
    """
    if hasattr(st, "html"):
        st.html(header_html)
    else:
        st.markdown(header_html, unsafe_allow_html=True)

    # 3. Panel de Configuración Central (Solo visible para el Coach / Administrador en el CRM)
    if is_admin:
        with st.expander("⚙️ Clave Central de Google Gemini (Para todo el Club AlphaX)", expanded=(not api_key)):
            if api_key:
                st.success(f"✅ **Clave de Gemini Activa para todo el Club AlphaX:** `...{api_key[-6:] if len(api_key)>6 else '***'}`\n\nTodos los deportistas tienen acceso libre a Wolfy IA en sus teléfonos sin tener que configurar nada.")
            else:
                st.warning("🔑 **Configura la API Key de Gemini una sola vez para todo el club:**\nAl guardarla aquí, se almacena en la base de datos central de AlphaX y queda habilitada automáticamente para **todos los atletas** en sus teléfonos.")
                st.markdown("Obtén tu clave gratuita en 👉 [**Google AI Studio (Click aquí)**](https://aistudio.google.com/app/apikey).")

            col_k1, col_k2 = st.columns([3, 1])
            with col_k1:
                input_k = st.text_input("Ingresar GEMINI_API_KEY para todo el club:", type="password", key="admin_gemini_input", placeholder="AIzaSy...")
            with col_k2:
                st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
                if st.button("💾 Guardar para Todos", use_container_width=True, key="btn_save_key"):
                    if input_k.strip():
                        if save_gemini_api_key(input_k.strip()):
                            st.success("✅ ¡Clave de Gemini guardada en la base de datos central! Activa para todos los atletas.")
                            st.rerun()
                        else:
                            st.error("No se pudo guardar en la base de datos.")
                    else:
                        st.error("Por favor ingresa una clave válida.")

    # 4. Inicializar historial de chat en session_state
    chat_key = f"wolfy_chat_{member_id}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = [
            {
                "role": "assistant",
                "content": f"¡Aúpa, **{athlete_ctx['athlete_name']}**! 🐺 Soy **Wolfy**, tu compañero de inteligencia artificial en **AlphaX**.\n\nConozco tus métricas fisiológicas y estoy listo para resolver cualquier duda sobre tus **exámenes de sangre**, tus **umbrales de lactato**, tu **recuperación** o tus entrenamientos.\n\n¿Qué te gustaría analizar hoy?"
            }
        ]

    # 5. Botones de Preguntas Rápidas (Pills)
    st.markdown("<div style='color: #8E9BAE; font-size: 0.78rem; font-weight: bold; text-transform: uppercase; margin-bottom: 6px;'>💡 Preguntas sugeridas para empezar:</div>", unsafe_allow_html=True)
    c_q1, c_q2, c_q3, c_q4 = st.columns(4)
    
    quick_query = None
    with c_q1:
        if st.button("🩸 Transporte de Oxígeno", use_container_width=True, key="btn_q1"):
            quick_query = "¿Cómo está mi transporte de oxígeno (hemoglobina, hematocrito y ferritina) para mi deporte de resistencia?"
    with c_q2:
        if st.button("⚡ Daño Muscular (CK)", use_container_width=True, key="btn_q2"):
            quick_query = "¿Qué significa el valor de mi Creatina Quinasa (CK) y cómo debo modular mi entrenamiento hoy?"
    with c_q3:
        if st.button("🧪 Umbrales de Lactato", use_container_width=True, key="btn_q3"):
            quick_query = "¿Cómo interpreto mis umbrales de lactato LT1 y LT2 según mi última prueba?"
    with c_q4:
        if st.button("🥑 Nutrición e Hidratación", use_container_width=True, key="btn_q4"):
            quick_query = "¿Qué estrategia de nutrición e hidratación debo seguir para una sesión de resistencia larga?"

    # 6. Renderizar mensajes previos del chat
    avatar_user = "🏃"
    avatar_wolfy = avatar_path if os.path.exists(avatar_path) else "🐺"

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"], avatar=avatar_wolfy if msg["role"] == "assistant" else avatar_user):
            st.markdown(msg["content"])

    # 7. Capturar nueva entrada (input del usuario o botón rápido)
    user_prompt = st.chat_input("Escribe cualquier pregunta a Wolfy (exámenes, nutrición, lactato, entreno)...") or quick_query

    if user_prompt:
        # Agregar mensaje del usuario al chat
        st.session_state[chat_key].append({"role": "user", "content": user_prompt})
        with st.chat_message("user", avatar=avatar_user):
            st.markdown(user_prompt)

        # Generar respuesta de Wolfy
        with st.chat_message("assistant", avatar=avatar_wolfy):
            with st.spinner("Wolfy razonando con ciencia AlphaX..."):
                response_text = None
                
                # Intentar llamar a Google Gemini si hay clave configurada
                if api_key:
                    response_text = call_gemini_chat(
                        st.session_state[chat_key], 
                        system_prompt, 
                        api_key
                    )
                
                # Si no hay clave o falló la conexión externa, usar el motor de conocimiento local AlphaX
                if not response_text:
                    response_text = generate_local_wolfy_response(user_prompt, athlete_ctx)
                
                st.markdown(response_text)
                st.session_state[chat_key].append({"role": "assistant", "content": response_text})

    # 8. Controles inferiores (Limpiar historial y configuración de API)
    st.markdown("---")
    col_ctrl1, col_ctrl2 = st.columns([3, 1])
    with col_ctrl1:
        if api_key:
            with st.expander("⚙️ Clave de Gemini conectada", expanded=False):
                st.caption(f"Clave actual en uso: `...{api_key[-6:] if len(api_key)>6 else '***'}`")
                new_key = st.text_input("Cambiar clave GEMINI_API_KEY:", type="password", key="input_gemini_key_change")
                if st.button("Actualizar Clave"):
                    if new_key.strip():
                        save_gemini_api_key(new_key.strip())
                        st.success("¡Clave actualizada!")
                        st.rerun()
    with col_ctrl2:
        if st.button("🧹 Limpiar Chat", use_container_width=True, type="secondary"):
            st.session_state.pop(chat_key, None)
            st.rerun()
