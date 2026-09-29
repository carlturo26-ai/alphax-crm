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
from database import SessionLocal, Member, BloodworkRecord, LactateTest, SleepRecord
from bloodwork_constants import get_bloodwork_ranges, classify_bloodwork_value


# ═════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LA API KEY DE GEMINI
# ═════════════════════════════════════════════════════════════════════

def get_gemini_api_key() -> str:
    """Busca la API Key de Gemini en session_state, Streamlit Secrets o variables de entorno."""
    # 1. Sesión interactiva (si el usuario la ingresó temporalmente en la app)
    if st.session_state.get("gemini_api_key_override"):
        return st.session_state["gemini_api_key_override"].strip()
    
    # 2. Streamlit Cloud Secrets
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"].strip()
    except Exception:
        pass
        
    # 3. Variable de entorno / archivo .env
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

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
    Genera respuestas inteligentes inmediatas basadas en la fisiología de AlphaX
    y los datos reales del atleta cuando no hay API Key de Gemini configurada.
    """
    q = user_query.lower()
    name = athlete_ctx.get("athlete_name", "atleta")
    gender = athlete_ctx.get("gender", "Hombre")
    bw = athlete_ctx.get("bloodwork") or {}
    lac = athlete_ctx.get("lactate") or {}

    # 1. Pregunta sobre Transporte de Oxígeno / Hemoglobina / Ferritina / Hematocrito
    if any(w in q for w in ["oxigeno", "oxígeno", "hemoglobina", "ferritina", "hematocrito", "hierro", "sangre"]):
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
                resp.append(f"- **VCM (Volumen Corpuscular Medio)**: `{vcm} fL`")
            if fer is not None:
                resp.append(f"- **Ferritina Sérica**: `{fer} ng/mL`")
                if fer < 35.0:
                    resp.append(f"\n⚠️ **Ojo con tus depósitos de hierro:** Tu ferritina está en `{fer} ng/mL`. Para un atleta de resistencia ({gender}), buscamos idealmente valores superiores a **35-50 ng/mL**. Cuando está baja, aunque la hemoglobina parezca aceptable, tu capacidad de regenerar glóbulos rojos tras cargas altas se ve mermada.")
                else:
                    resp.append(f"\n✅ **Buenos depósitos de hierro:** Tu ferritina de `{fer} ng/mL` respalda una adecuada síntesis de hemoglobina y transporte de $O_2$ a los músculos.")
            
            resp.append("\n💡 **Concepto AlphaX:** En deportistas aeróbicos es común la *pseudoanemia por hemodilución*: al entrenar mucho, tu plasma sanguíneo se expande hasta un 15-20%, haciendo que la hemoglobina y el hematocrito se vean ligeramente más bajos de forma natural, pero con una sangre más fluida para el corazón.")
        else:
            resp.append("Aún no tienes exámenes de sangre registrados en tu perfil. Sube tu último análisis en la pestaña **🩸 Hemogramas** y podré analizar tu hemoglobina, ferritina y glóbulos rojos al instante.")
            
        return "\n".join(resp)

    # 2. Pregunta sobre Daño Muscular / Creatina Quinasa (CK) / Cansancio
    if any(w in q for w in ["ck", "creatina quinasa", "creatina kinasa", "muscular", "dolor", "agujetas", "fatiga", "recuperacion", "recuperación"]):
        ck = bw.get("ck")
        resp = [f"🐺 **Hablemos de daño muscular y recuperación ({name}):**\n"]
        if ck is not None:
            resp.append(f"Tu última **Creatina Quinasa (CK)** registrada es de `{ck} U/L`.")
            if ck > 300:
                resp.append(f"⚡ **Nivel elevado:** La CK es una enzima que se fuga a la sangre cuando hay micro-roturas en las fibras musculares (muy común tras series intensas, bajadas pronunciadas o fondos largos).")
                resp.append(f"🎯 **Acción AlphaX:** Hoy prioriza hidratación abundante, descanso activo (caminata suave o rodillo en Z1 muy liviano) y buen aporte de proteínas y electrolitos. Si la CK pasa de 800-1000 U/L, no hagas sesiones de alta intensidad hasta que descienda.")
            else:
                resp.append(f"✅ **Excelente recuperación:** Tu CK está en rangos seguros, lo que indica que no tienes una sobrecarga muscular excesiva acumulada.")
        else:
            resp.append("La **Creatina Quinasa (CK)** es el mejor semáforo biológico para saber si tus músculos ya asimilaron el entrenamiento duro previo o si aún están inflamados.")
            
        return "\n".join(resp)

    # 3. Pregunta sobre Lactato y Umbrales (LT1 / LT2)
    if any(w in q for w in ["lactato", "umbral", "lt1", "lt2", "zonas", "ftp", "ritmos"]):
        resp = [f"🐺 **Tus Umbrales Fisiológicos de Lactato:**\n"]
        if lac.get("lt1_power") or lac.get("lt2_power"):
            resp.append(f"En tu última prueba de **{lac.get('sport', 'Resistencia')}**:")
            resp.append(f"- **LT1 (Umbral Aeróbico)**: `{lac.get('lt1_power')} W/Pace` (FC: ~{lac.get('lt1_hr')} bpm). Es el ritmo donde quemas máxima grasa y puedes mantenerte horas sin fatiga severa.")
            resp.append(f"- **LT2 (Umbral Anaeróbico / MLSS)**: `{lac.get('lt2_power')} W/Pace` (FC: ~{lac.get('lt2_hr')} bpm). Es el ritmo de máximo estado estable de lactato.")
        else:
            resp.append("El lactato no es un 'desecho', ¡es un combustible premium para tu corazón y fibras lentas!")
            resp.append("- **LT1 (Z2)**: Base aeróbica pura.")
            resp.append("- **LT2 (Z4)**: Ritmo de umbral funcional.")
        resp.append("\nPuedes ver tus curvas completas y tabla de zonas en la pestaña **🧪 Pruebas de Lactato**.")
        return "\n".join(resp)

    # 4. Pregunta sobre la App AlphaX
    if any(w in q for w in ["app", "crm", "como uso", "cómo uso", "subir", "descargar", "pantalla", "pestaña"]):
        return f"""🐺 **Guía Rápida de la App AlphaX para Atletas:**

1. **💤 Sueño (ASSQ):** Aquí llenas tu cuestionario de sueño y ves la gráfica histórica de calidad de descanso.
2. **🩸 Hemogramas:** Muestra tus 15 marcadores sanguíneos con un radar gráfico interactivo, semáforos óptimos de resistencia y alertas personalizadas.
3. **🧪 Pruebas de Lactato:** Contiene tu curva de lactato vs potencia/FC y tus zonas de entrenamiento individualizadas (Z1 a Z5).
4. **🐺 Wolfy AI Coach:** ¡Aquí estoy yo para resolverte cualquier duda sobre tu rendimiento, tu salud y tus entrenamientos!"""

    # Respuesta por defecto inteligente
    return f"""🐺 **¡Hola, {name}! Soy Wolfy, tu mentor fisiológico en AlphaX.**

Puedo ayudarte con:
* 🩸 **Tus exámenes de sangre:** Analizo tu hemoglobina, hematocrito, ferritina, CK y glucosa con enfoque deportivo.
* 🧪 **Tus umbrales de lactato:** Explicación de tus zonas Z1-Z5, LT1 y LT2.
* 💤 **Recuperación y sueño:** Estrategias para asimilar mejor las cargas.
* 📱 **Uso de la App AlphaX:** Cómo interpretar tus métricas y reportes.

*Pruébame preguntándome:* **"¿Cómo está mi transporte de oxígeno?"** o **"¿Qué significa tener la CK alta?"**."""


# ═════════════════════════════════════════════════════════════════════
#  RENDERIZADO PRINCIPAL DE LA PESTAÑA WOLFY
# ═════════════════════════════════════════════════════════════════════

def render_wolfy_tab(member_id: int):
    """Renderiza la interfaz completa de Wolfy AI Coach dentro del Portal de Atletas."""
    
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
    status_badge = '<span style="background: rgba(0,255,0,0.15); color: #00FF00; padding: 3px 8px; border-radius: 12px; font-size: 0.72rem; font-weight: bold; border: 1px solid rgba(0,255,0,0.3);">🟢 Gemini Neuronal Activo</span>' if api_key else '<span style="background: rgba(0,238,255,0.15); color: #00EEFF; padding: 3px 8px; border-radius: 12px; font-size: 0.72rem; font-weight: bold; border: 1px solid rgba(0,238,255,0.3);">🧠 AlphaX Base Fisiológica</span>'

    header_html = f"""
    <div style="background: linear-gradient(135deg, rgba(16, 22, 34, 0.95), rgba(10, 13, 22, 0.98)); border: 1.5px solid rgba(0, 238, 255, 0.35); border-radius: 16px; padding: 16px 20px; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 15px;">
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

    # 3. Inicializar historial de chat en session_state
    chat_key = f"wolfy_chat_{member_id}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = [
            {
                "role": "assistant",
                "content": f"¡Aúpa, **{athlete_ctx['athlete_name']}**! 🐺 Soy **Wolfy**, tu compañero de inteligencia artificial en **AlphaX**.\n\nConozco tus métricas fisiológicas y estoy aquí para resolver cualquier duda sobre tus **exámenes de sangre**, tus **umbrales de lactato**, tu **recuperación** o sobre el funcionamiento de la **App AlphaX**.\n\n¿Qué te gustaría analizar hoy?"
            }
        ]

    # 4. Botones de Preguntas Rápidas (Pills)
    st.markdown("<div style='color: #8E9BAE; font-size: 0.78rem; font-weight: bold; text-transform: uppercase; margin-bottom: 6px;'>💡 Preguntas rápidas sugeridas:</div>", unsafe_allow_html=True)
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
        if st.button("📱 Guía App AlphaX", use_container_width=True, key="btn_q4"):
            quick_query = "¿Cómo puedo aprovechar al máximo las gráficas y reportes de la app AlphaX?"

    # 5. Renderizar mensajes previos del chat
    avatar_user = "🏃"
    avatar_wolfy = avatar_path if os.path.exists(avatar_path) else "🐺"

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"], avatar=avatar_wolfy if msg["role"] == "assistant" else avatar_user):
            st.markdown(msg["content"])

    # 6. Capturar nueva entrada (input del usuario o botón rápido)
    user_prompt = st.chat_input("Escribe tu duda fisiológica o sobre la app AlphaX...") or quick_query

    if user_prompt:
        # Agregar mensaje del usuario al chat
        st.session_state[chat_key].append({"role": "user", "content": user_prompt})
        with st.chat_message("user", avatar=avatar_user):
            st.markdown(user_prompt)

        # Generar respuesta de Wolfy
        with st.chat_message("assistant", avatar=avatar_wolfy):
            with st.spinner("Wolfy analizando fisiología y métricas..."):
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

    # 7. Controles inferiores (Limpiar historial y configuración de API)
    st.markdown("---")
    col_ctrl1, col_ctrl2 = st.columns([3, 1])
    with col_ctrl1:
        with st.expander("⚙️ Configuración del Motor Gemini (Opcional)", expanded=False):
            st.caption("Wolfy funciona automáticamente con su base de conocimiento de AlphaX. Si deseas conectarlo a Google Gemini para razonamiento ilimitado, ingresa tu clave o agrégala a los Secrets de Streamlit.")
            current_override = st.session_state.get("gemini_api_key_override", "")
            new_key = st.text_input("Ingresar GEMINI_API_KEY personalizada:", value=current_override, type="password", key="input_gemini_key")
            if new_key != current_override:
                st.session_state["gemini_api_key_override"] = new_key
                st.success("¡Clave de Gemini actualizada para esta sesión!")
                st.rerun()
    with col_ctrl2:
        if st.button("🧹 Limpiar Chat", use_container_width=True, type="secondary"):
            st.session_state.pop(chat_key, None)
            st.rerun()
