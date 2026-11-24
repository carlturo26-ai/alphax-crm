"""
wolfy_bot.py — Chatbot de Inteligencia Artificial "Wolfy" para Atletas AlphaX.
Especializado en fisiología deportiva, interpretación de exámenes de sangre,
umbrales de lactato, recuperación, antropometría y nutrición deportiva.

Características principales:
  1. Dossier Fisiológico 360° en tiempo real: estatura, peso, edad, IMC, deporte,
     volumen semanal, FC reposo/máx, altitud, objetivo, antecedentes, últimos 3 exámenes
     de sangre con clasificación AlphaX + alertas + evolución, últimas 3 pruebas de
     lactato con W/kg + curva paso a paso + CTL/ATL/TSB, y últimos 5 reportes de sueño.
  2. Continuidad conversacional real estilo ChatGPT / Gemini:
     - Memoria persistente en base de datos (`wolfy_chat_messages`) por deportista.
     - Streaming en tiempo real con `st.write_stream` vía Gemini SSE.
     - Preguntas sugeridas iniciales + chips de seguimiento dinámicos tras cada respuesta.
     - Historial saneado multi-turno para máxima coherencia conversacional.
  3. Editor integrado del Perfil Biométrico del Atleta (estatura, peso, edad, etc.)
     reutilizable tanto en el Portal de Atletas como en el CRM de Entrenadores.
"""

import base64
import json
import os
import re
from datetime import date, datetime

import requests
import streamlit as st

from bloodwork_constants import ALL_MARKER_KEYS, classify_bloodwork_value, get_bloodwork_ranges, get_clinical_alerts
from database import (
    BloodworkRecord,
    LactateTest,
    LactateTestStep,
    Member,
    SessionLocal,
    SleepRecord,
    WolfyChatMessage,
    get_club_setting,
    init_db,
    set_club_setting,
)


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

    db_saved = set_club_setting("gemini_api_key", key)

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
    1. Session State / Override en memoria.
    2. Base de datos central (Neon Postgres) -> Configuración global de todo el club AlphaX.
    3. Streamlit Cloud Secrets (st.secrets).
    4. Variable de entorno / archivo .env.
    """
    try:
        if st.session_state.get("gemini_api_key_override"):
            return st.session_state["gemini_api_key_override"].strip()
    except Exception:
        pass

    try:
        db_key = get_club_setting("gemini_api_key")
        if db_key:
            return db_key
    except Exception:
        pass

    try:
        if "GEMINI_API_KEY" in st.secrets:
            k = st.secrets["GEMINI_API_KEY"].strip()
            if k:
                return k
    except Exception:
        pass

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

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
#  PERSISTENCIA DE HISTORIAL DE CHAT EN BASE DE DATOS
# ═════════════════════════════════════════════════════════════════════

def load_chat_history(member_id: int, limit: int = 30) -> list:
    """Carga los últimos mensajes guardados de la conversación del deportista con Wolfy."""
    if not member_id:
        return []
    try:
        init_db()
        with SessionLocal() as session:
            rows = (
                session.query(WolfyChatMessage)
                .filter(WolfyChatMessage.member_id == member_id)
                .order_by(WolfyChatMessage.id.desc())
                .limit(limit)
                .all()
            )
            rows.reverse()
            return [
                {"role": r.role, "content": r.content}
                for r in rows
                if r.role in ("user", "assistant")
                and r.content
                and "modo local básico" not in r.content
                and "Google AI Studio" not in r.content
            ]
    except Exception:
        return []


def save_chat_message(member_id: int, role: str, content: str):
    """Guarda un mensaje del chat en la base de datos para continuidad entre sesiones."""
    if not member_id or not content or role not in ("user", "assistant"):
        return
    try:
        init_db()
        with SessionLocal() as session:
            msg = WolfyChatMessage(
                member_id=member_id,
                role=role,
                content=content,
                created_at=datetime.now(),
            )
            session.add(msg)
            session.commit()
    except Exception:
        pass


def clear_chat_history(member_id: int):
    """Borra el hilo persistido de Wolfy para iniciar una nueva conversación limpia."""
    if not member_id:
        return
    try:
        init_db()
        with SessionLocal() as session:
            session.query(WolfyChatMessage).filter(WolfyChatMessage.member_id == member_id).delete()
            session.commit()
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════
#  EDITOR REUTILIZABLE DE PERFIL BIOMÉTRICO (PORTAL ATLETAS + CRM)
# ═════════════════════════════════════════════════════════════════════

SPORT_OPTIONS = [
    "",
    "Ciclismo de Ruta",
    "Triatlón",
    "Running / Maratón",
    "Trail Running",
    "MTB / Gravel",
    "Natación / Aguas Abiertas",
    "Duatlón",
    "Fitness / Resistencia General",
]


def _calc_age_years(bdate) -> int:
    if not bdate:
        return None
    try:
        today = date.today()
        return today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
    except Exception:
        return None


def render_athlete_biometric_profile_editor(
    member_id: int,
    key_prefix: str = "wolfy_prof",
    expanded: bool = False,
    is_admin: bool = False,
):
    """
    Renderiza el formulario compacto para ver y actualizar la ficha física del atleta:
    estatura, peso, fecha de nacimiento, deporte principal, volumen semanal, FC reposo/máx,
    altitud habitual de entrenamiento, objetivo principal y notas médicas/nutricionales.
    """
    if not member_id:
        return

    init_db()
    with SessionLocal() as session:
        member = session.query(Member).filter(Member.id == member_id).first()
        if not member:
            return
        # Fallback de peso desde última prueba de lactato si aún no está guardado en Member
        fallback_weight = None
        if not member.weight_kg:
            last_lac = (
                session.query(LactateTest)
                .filter(LactateTest.member_id == member_id, LactateTest.weight.isnot(None))
                .order_by(LactateTest.date.desc(), LactateTest.id.desc())
                .first()
            )
            if last_lac and last_lac.weight:
                fallback_weight = float(last_lac.weight)

        m_data = {
            "name": member.name,
            "gender": member.gender or "Hombre",
            "height_cm": member.height_cm,
            "weight_kg": member.weight_kg or fallback_weight,
            "birth_date": member.birth_date,
            "primary_sport": member.primary_sport or "",
            "weekly_hours": member.weekly_hours,
            "resting_hr": member.resting_hr,
            "max_hr": member.max_hr,
            "altitude_m": member.altitude_m,
            "athlete_goal": member.athlete_goal or "",
            "medical_notes": member.medical_notes or "",
        }

    age = _calc_age_years(m_data["birth_date"])
    bmi = None
    if m_data["height_cm"] and m_data["weight_kg"] and m_data["height_cm"] > 80:
        h_m = m_data["height_cm"] / 100.0
        bmi = round(m_data["weight_kg"] / (h_m * h_m), 1)

    summary_bits = []
    if m_data["height_cm"]:
        summary_bits.append(f"📏 {m_data['height_cm']:.0f} cm")
    if m_data["weight_kg"]:
        summary_bits.append(f"⚖️ {m_data['weight_kg']:.1f} kg")
    if bmi:
        summary_bits.append(f"IMC {bmi}")
    if age:
        summary_bits.append(f"🎂 {age} años")
    if m_data["primary_sport"]:
        summary_bits.append(f"🏅 {m_data['primary_sport']}")
    if m_data["altitude_m"] is not None:
        summary_bits.append(f"🏔️ {m_data['altitude_m']} m")

    missing_core = []
    if not m_data["height_cm"]:
        missing_core.append("estatura")
    if not m_data["weight_kg"]:
        missing_core.append("peso")
    if not m_data["birth_date"]:
        missing_core.append("edad")

    if summary_bits:
        exp_title = f"👤 Perfil Físico: {' · '.join(summary_bits)}"
        if missing_core:
            exp_title += f" (falta: {', '.join(missing_core)})"
    else:
        exp_title = "👤 Completa tu Perfil Físico (Estatura, Peso, Edad, Objetivo) para que Wolfy sea 100% preciso"

    with st.expander(exp_title, expanded=expanded):
        if missing_core:
            who_txt = "de este atleta" if is_admin else "tuyos"
            st.info(
                f"💡 Agrega {', '.join(missing_core)} {who_txt} para que Wolfy calcule con precisión "
                "IMC, relación vatios/kilo (W/kg), gramos exactos de carbohidratos/proteína, zonas de pulso y ajustes por altitud."
            )

        with st.form(f"{key_prefix}_biometric_form_{member_id}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                in_height = st.number_input(
                    "📏 Estatura (cm)",
                    min_value=100.0,
                    max_value=230.0,
                    value=float(m_data["height_cm"]) if m_data["height_cm"] else None,
                    step=1.0,
                    placeholder="Ej. 176",
                    key=f"{key_prefix}_h_{member_id}",
                )
            with c2:
                in_weight = st.number_input(
                    "⚖️ Peso actual (kg)",
                    min_value=30.0,
                    max_value=180.0,
                    value=float(m_data["weight_kg"]) if m_data["weight_kg"] else None,
                    step=0.5,
                    placeholder="Ej. 68.5",
                    key=f"{key_prefix}_w_{member_id}",
                )
            with c3:
                in_bdate = st.date_input(
                    "🎂 Fecha de nacimiento",
                    value=m_data["birth_date"],
                    min_value=date(1940, 1, 1),
                    max_value=date.today(),
                    key=f"{key_prefix}_bd_{member_id}",
                )

            c4, c5, c6 = st.columns(3)
            with c4:
                sport_idx = SPORT_OPTIONS.index(m_data["primary_sport"]) if m_data["primary_sport"] in SPORT_OPTIONS else 0
                in_sport = st.selectbox(
                    "🏅 Deporte principal",
                    options=SPORT_OPTIONS,
                    index=sport_idx,
                    format_func=lambda x: x if x else "Seleccionar...",
                    key=f"{key_prefix}_sp_{member_id}",
                )
            with c5:
                in_hours = st.number_input(
                    "⏱️ Horas entreno / semana",
                    min_value=0.0,
                    max_value=45.0,
                    value=float(m_data["weekly_hours"]) if m_data["weekly_hours"] is not None else None,
                    step=0.5,
                    placeholder="Ej. 10",
                    key=f"{key_prefix}_hrs_{member_id}",
                )
            with c6:
                in_alt = st.number_input(
                    "🏔️ Altitud habitual (msnm)",
                    min_value=0,
                    max_value=4500,
                    value=int(m_data["altitude_m"]) if m_data["altitude_m"] is not None else None,
                    step=100,
                    placeholder="Ej. 1500 (Medellín) / 2600 (Bogotá)",
                    key=f"{key_prefix}_alt_{member_id}",
                )

            c7, c8 = st.columns(2)
            with c7:
                in_rhr = st.number_input(
                    "❤️ FC en Reposo (lpm)",
                    min_value=30,
                    max_value=120,
                    value=int(m_data["resting_hr"]) if m_data["resting_hr"] else None,
                    step=1,
                    placeholder="Ej. 48",
                    key=f"{key_prefix}_rhr_{member_id}",
                )
            with c8:
                in_mhr = st.number_input(
                    "🔥 FC Máxima conocida (lpm)",
                    min_value=110,
                    max_value=230,
                    value=int(m_data["max_hr"]) if m_data["max_hr"] else None,
                    step=1,
                    placeholder="Ej. 188",
                    key=f"{key_prefix}_mhr_{member_id}",
                )

            in_goal = st.text_input(
                "🎯 Objetivo o competencia principal",
                value=m_data["athlete_goal"],
                placeholder="Ej. Ironman 70.3 Cartagena, bajar de 3h15 en maratón, subir FTP...",
                key=f"{key_prefix}_goal_{member_id}",
            )
            in_med = st.text_area(
                "🩺 Alergias, tipo de dieta, suplementación o lesiones actuales (opcional)",
                value=m_data["medical_notes"],
                height=65,
                placeholder="Ej. Vegetariano, intolerancia a lactosa, tendinitis aquílea, tomo hierro y magnesio...",
                key=f"{key_prefix}_med_{member_id}",
            )

            if st.form_submit_button("💾 Guardar Perfil Físico", type="primary", use_container_width=True):
                try:
                    with SessionLocal() as s_save:
                        m_db = s_save.query(Member).filter(Member.id == member_id).first()
                        if m_db:
                            m_db.height_cm = float(in_height) if in_height else None
                            m_db.weight_kg = float(in_weight) if in_weight else None
                            m_db.birth_date = in_bdate if in_bdate else None
                            m_db.primary_sport = in_sport if in_sport else None
                            m_db.weekly_hours = float(in_hours) if in_hours is not None else None
                            m_db.altitude_m = int(in_alt) if in_alt is not None else None
                            m_db.resting_hr = int(in_rhr) if in_rhr else None
                            m_db.max_hr = int(in_mhr) if in_mhr else None
                            m_db.athlete_goal = in_goal.strip() if in_goal and in_goal.strip() else None
                            m_db.medical_notes = in_med.strip() if in_med and in_med.strip() else None
                            s_save.commit()
                    st.toast("Perfil físico actualizado para Wolfy", icon="✅")
                    st.rerun()
                except Exception as err:
                    st.error(f"Error guardando perfil físico: {err}")


# ═════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE CONTEXTO 360° DEL DEPORTISTA
# ═════════════════════════════════════════════════════════════════════

def build_athlete_context(member_id: int) -> dict:
    """
    Recupera el dossier fisiológico completo del deportista en la base de datos:
      - Antropometría y perfil atlético (estatura, peso, edad, IMC, deporte, altitud, FC, objetivo, notas)
      - Últimos 3 exámenes de sangre con clasificación AlphaX, deltas y alertas clínicas
      - Últimas 3 pruebas de lactato con W/kg, carga (CTL/ATL/TSB) y curva paso a paso
      - Últimos 5 reportes de sueño ASSQ con tendencia y detalles completos
    """
    init_db()
    ctx = {
        "athlete_name": "Atleta",
        "gender": "Hombre",
        "group": "AlphaX",
        "profile": {},
        "bloodwork": None,
        "bloodwork_history": [],
        "bloodwork_alerts": [],
        "lactate": None,
        "lactate_history": [],
        "sleep": None,
        "sleep_history": [],
        "derived": {},
        "summary_text": "",
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
                ctx["profile"] = {
                    "height_cm": member.height_cm,
                    "weight_kg": member.weight_kg,
                    "birth_date": member.birth_date.strftime("%Y-%m-%d") if member.birth_date else None,
                    "age_years": _calc_age_years(member.birth_date),
                    "primary_sport": member.primary_sport,
                    "weekly_hours": member.weekly_hours,
                    "resting_hr": member.resting_hr,
                    "max_hr": member.max_hr,
                    "altitude_m": member.altitude_m,
                    "athlete_goal": member.athlete_goal,
                    "medical_notes": member.medical_notes,
                    "coach_notes": member.notes,
                    "start_month": member.start_month,
                }

            # 1. Últimos 3 exámenes de sangre
            bw_recs = (
                session.query(BloodworkRecord)
                .filter(BloodworkRecord.member_id == member_id)
                .order_by(BloodworkRecord.date.desc(), BloodworkRecord.id.desc())
                .limit(3)
                .all()
            )
            if bw_recs:
                for rec in bw_recs:
                    entry = {
                        "date": rec.date.strftime("%Y-%m-%d") if rec.date else "N/A",
                        "notes": rec.notes,
                        "pdf_filename": rec.pdf_filename,
                    }
                    for k in ALL_MARKER_KEYS:
                        entry[k] = getattr(rec, k, None)
                    ctx["bloodwork_history"].append(entry)
                ctx["bloodwork"] = ctx["bloodwork_history"][0]
                alerts = get_clinical_alerts(bw_recs[0], gender=ctx["gender"])
                ctx["bloodwork_alerts"] = [
                    {"emoji": a[0], "title": a[1], "detail": a[2]} for a in alerts
                ]

            # 2. Últimas 3 pruebas de lactato + pasos de la última prueba
            lac_recs = (
                session.query(LactateTest)
                .filter(LactateTest.member_id == member_id)
                .order_by(LactateTest.date.desc(), LactateTest.id.desc())
                .limit(3)
                .all()
            )
            if lac_recs:
                for idx_l, lac in enumerate(lac_recs):
                    l_entry = {
                        "date": lac.date.strftime("%Y-%m-%d") if lac.date else "N/A",
                        "sport": lac.sport or "Resistencia",
                        "lt1_power": lac.lt1_power,
                        "lt1_hr": lac.lt1_hr,
                        "lt1_lactate": lac.lt1_lactate,
                        "lt2_power": lac.lt2_power,
                        "lt2_hr": lac.lt2_hr,
                        "lt2_lactate": lac.lt2_lactate,
                        "weight": lac.weight,
                        "ftp": lac.ftp,
                        "ctl": lac.ctl,
                        "atl": lac.atl,
                        "tsb": lac.tsb,
                        "temperature": lac.temperature,
                        "simulator": lac.simulator,
                        "breakfast": lac.breakfast,
                        "last_training": lac.last_training,
                        "duration": lac.duration,
                        "intensity": lac.intensity,
                        "steps": [],
                    }
                    if idx_l == 0:
                        steps = (
                            session.query(LactateTestStep)
                            .filter(LactateTestStep.test_id == lac.id)
                            .order_by(LactateTestStep.id.asc())
                            .all()
                        )
                        ignored_kw = {"v4", "lt1", "lt2", "fatmax", "anaerobica", "anaeróbica", "aerobica", "aeróbica", "umbral"}
                        for stp in steps:
                            sn = str(stp.step_number or "").strip()
                            if sn.lower() in ignored_kw:
                                continue
                            l_entry["steps"].append({
                                "step": sn,
                                "watts": stp.watts,
                                "lactate": stp.lactate,
                                "hr": stp.heart_rate,
                                "rpe": stp.rpe,
                                "pot_rel": stp.pot_rel,
                            })
                    ctx["lactate_history"].append(l_entry)
                ctx["lactate"] = ctx["lactate_history"][0]

            # 3. Últimos 5 reportes de sueño ASSQ
            sleep_recs = (
                session.query(SleepRecord)
                .filter(SleepRecord.member_id == member_id)
                .order_by(SleepRecord.date.desc(), SleepRecord.id.desc())
                .limit(5)
                .all()
            )
            if sleep_recs:
                for sl in sleep_recs:
                    ctx["sleep_history"].append({
                        "date": sl.date.strftime("%Y-%m-%d") if sl.date else "N/A",
                        "sds_score": sl.sds_score,
                        "category": sl.clinical_category,
                        "hours": sl.raw_hours,
                        "quality": sl.raw_quality,
                        "latency": sl.raw_latency,
                        "awakenings": sl.raw_awakenings,
                        "medications": sl.raw_medications,
                    })
                ctx["sleep"] = ctx["sleep_history"][0]

    except Exception as e:
        ctx["error"] = str(e)

    # ── CÁLCULOS DERIVADOS DE PRECISIÓN ──────────────────────────────
    prof = ctx["profile"]
    eff_weight = prof.get("weight_kg") or (ctx["lactate"].get("weight") if ctx["lactate"] else None)
    eff_height = prof.get("height_cm")
    eff_age = prof.get("age_years")
    derived = {}

    if eff_weight:
        derived["effective_weight_kg"] = round(float(eff_weight), 1)
        derived["protein_target_g"] = f"{round(eff_weight * 1.6)}–{round(eff_weight * 2.0)} g/día (1.6–2.0 g/kg)"
        wk_h = prof.get("weekly_hours") or 8.0
        if wk_h >= 12:
            c_lo, c_hi = 6.0, 8.0
        elif wk_h >= 7:
            c_lo, c_hi = 5.0, 7.0
        else:
            c_lo, c_hi = 4.0, 6.0
        derived["carb_target_g"] = f"{round(eff_weight * c_lo)}–{round(eff_weight * c_hi)} g/día ({c_lo:.0f}–{c_hi:.0f} g/kg)"

    if eff_weight and eff_height and eff_height > 80:
        h_m = float(eff_height) / 100.0
        derived["bmi"] = round(float(eff_weight) / (h_m * h_m), 1)

    if ctx["lactate"] and eff_weight and eff_weight > 0:
        lac0 = ctx["lactate"]
        is_cycling = "cicl" in str(lac0.get("sport", "")).lower() or "bici" in str(lac0.get("sport", "")).lower() or (lac0.get("lt2_power") and lac0["lt2_power"] > 60)
        if is_cycling:
            if lac0.get("lt1_power"):
                derived["lt1_wkg"] = round(lac0["lt1_power"] / eff_weight, 2)
            if lac0.get("lt2_power"):
                derived["lt2_wkg"] = round(lac0["lt2_power"] / eff_weight, 2)
            if lac0.get("ftp"):
                derived["ftp_wkg"] = round(lac0["ftp"] / eff_weight, 2)

    if prof.get("max_hr"):
        derived["max_hr_source"] = f"{prof['max_hr']} lpm (medida/conocida)"
    elif eff_age:
        est_mhr = round(208 - 0.7 * eff_age)
        derived["max_hr_source"] = f"~{est_mhr} lpm (estimada por fórmula Tanaka para {eff_age} años)"

    ctx["derived"] = derived

    # ── CONSTRUCCIÓN DEL DOSSIER TEXTUAL PARA EL SYSTEM PROMPT ───────
    ranges = get_bloodwork_ranges(ctx["gender"])
    lines = [
        "=== PERFIL ANTROPOMÉTRICO Y ATLÉTICO ===",
        f"- Nombre: {ctx['athlete_name']}",
        f"- Sexo biológico: {ctx['gender']}",
        f"- Grupo / Coach AlphaX: {ctx['group']}",
    ]
    if eff_age is not None:
        lines.append(f"- Edad: {eff_age} años (Nacimiento: {prof.get('birth_date')})")
    else:
        lines.append("- Edad: No registrada aún")
    if eff_height:
        lines.append(f"- Estatura: {eff_height:.0f} cm")
    else:
        lines.append("- Estatura: No registrada aún")
    if eff_weight:
        src_w = "perfil" if prof.get("weight_kg") else "última prueba de lactato"
        lines.append(f"- Peso actual: {eff_weight:.1f} kg (fuente: {src_w})")
    else:
        lines.append("- Peso actual: No registrado aún")
    if derived.get("bmi"):
        lines.append(f"- IMC calculado: {derived['bmi']} kg/m²")
    if prof.get("primary_sport"):
        lines.append(f"- Deporte principal: {prof['primary_sport']}")
    if prof.get("weekly_hours") is not None:
        lines.append(f"- Volumen habitual: {prof['weekly_hours']} horas/semana")
    if prof.get("altitude_m") is not None:
        lines.append(f"- Altitud habitual de entrenamiento: {prof['altitude_m']} msnm")
    if prof.get("resting_hr"):
        lines.append(f"- FC en reposo: {prof['resting_hr']} lpm")
    if derived.get("max_hr_source"):
        lines.append(f"- FC máxima: {derived['max_hr_source']}")
    if prof.get("athlete_goal"):
        lines.append(f"- Objetivo / competencia principal: {prof['athlete_goal']}")
    if prof.get("medical_notes"):
        lines.append(f"- Dieta, alergias, suplementación o lesiones reportadas: {prof['medical_notes']}")
    if prof.get("coach_notes"):
        lines.append(f"- Notas del entrenador: {prof['coach_notes']}")
    if derived.get("protein_target_g"):
        lines.append(f"- Referencia proteica calculada: {derived['protein_target_g']}")
    if derived.get("carb_target_g"):
        lines.append(f"- Referencia de carbohidratos calculada: {derived['carb_target_g']}")

    # Analítica sanguínea + evolución + alertas
    if ctx["bloodwork"]:
        b0 = ctx["bloodwork"]
        b_prev = ctx["bloodwork_history"][1] if len(ctx["bloodwork_history"]) > 1 else None
        lines.append(f"\n=== ÚLTIMO EXAMEN DE SANGRE ({b0['date']}) ===")
        for k in ALL_MARKER_KEYS:
            val = b0.get(k)
            if val is None:
                continue
            r_info = ranges[k]
            lbl, _ = classify_bloodwork_value(k, val, ctx["gender"])
            delta_str = ""
            if b_prev and b_prev.get(k) is not None:
                diff = val - b_prev[k]
                sign = "+" if diff >= 0 else ""
                delta_str = f" [vs {b_prev['date']}: {b_prev[k]} → {sign}{diff:.1f}]"
            lines.append(f"  * {r_info['name']}: {val} {r_info['unit']} → Estado AlphaX: {lbl}{delta_str}")
        if b0.get("notes"):
            lines.append(f"  * Notas del examen: {b0['notes']}")

        if ctx["bloodwork_alerts"]:
            lines.append("\n[ALERTAS CLÍNICAS ACTIVAS DEL ÚLTIMO HEMOGRAMA]")
            for al in ctx["bloodwork_alerts"]:
                lines.append(f"  - {al['emoji']} {al['title']}: {al['detail']}")

        if len(ctx["bloodwork_history"]) > 1:
            lines.append("\n[RESUMEN EXÁMENES DE SANGRE ANTERIORES]")
            for old_b in ctx["bloodwork_history"][1:]:
                bits = [f"{k}={old_b[k]}" for k in ("hemoglobin", "hematocrit", "ferritin", "ck", "glucose", "vitamin_b12", "pcr_us") if old_b.get(k) is not None]
                if bits:
                    lines.append(f"  * {old_b['date']}: " + ", ".join(bits))
    else:
        lines.append("\n=== EXÁMENES DE SANGRE ===\nAún no registra exámenes de sangre en el CRM.")

    # Pruebas de lactato + evolución + pasos
    if ctx["lactate"]:
        l0 = ctx["lactate"]
        lines.append(f"\n=== ÚLTIMA PRUEBA DE LACTATO ({l0['date']} - {l0['sport']}) ===")
        lt1_extra = f" ({derived['lt1_wkg']} W/kg)" if derived.get("lt1_wkg") else ""
        lt2_extra = f" ({derived['lt2_wkg']} W/kg)" if derived.get("lt2_wkg") else ""
        lines.append(f"  * Umbral Aeróbico (LT1): {l0['lt1_power'] or '—'} W/Pace{lt1_extra} | FC: {l0['lt1_hr'] or '—'} lpm | Lactato: {l0['lt1_lactate'] or '—'} mmol/L")
        lines.append(f"  * Umbral Anaeróbico (LT2): {l0['lt2_power'] or '—'} W/Pace{lt2_extra} | FC: {l0['lt2_hr'] or '—'} lpm | Lactato: {l0['lt2_lactate'] or '—'} mmol/L")
        if l0.get("ftp"):
            ftp_extra = f" ({derived['ftp_wkg']} W/kg)" if derived.get("ftp_wkg") else ""
            lines.append(f"  * FTP / CP reportado: {l0['ftp']} W{ftp_extra}")
        if any(l0.get(x) is not None for x in ("ctl", "atl", "tsb")):
            lines.append(f"  * Carga de entrenamiento en la prueba: CTL={l0.get('ctl') or '—'} | ATL={l0.get('atl') or '—'} | TSB={l0.get('tsb') or '—'}")
        if l0.get("temperature"):
            lines.append(f"  * Temperatura ambiente prueba: {l0['temperature']} °C")
        if l0.get("breakfast"):
            lines.append(f"  * Desayuno previo: {l0['breakfast']}")
        if l0.get("last_training"):
            lines.append(f"  * Último entrenamiento previo: {l0['last_training']}")

        if l0.get("steps"):
            step_parts = []
            for stp in l0["steps"][:12]:
                step_parts.append(
                    f"E{stp['step']}: {stp['watts'] if stp['watts'] is not None else '—'}W / "
                    f"{stp['lactate'] if stp['lactate'] is not None else '—'}mmol / "
                    f"{stp['hr'] if stp['hr'] is not None else '—'}lpm"
                )
            lines.append("  * Curva escalonada: " + " → ".join(step_parts))

        if len(ctx["lactate_history"]) > 1:
            lines.append("  * Pruebas de lactato anteriores:")
            for old_l in ctx["lactate_history"][1:]:
                lines.append(
                    f"    - {old_l['date']} ({old_l['sport']}): LT1={old_l['lt1_power'] or '—'} ({old_l['lt1_hr'] or '—'} lpm), "
                    f"LT2={old_l['lt2_power'] or '—'} ({old_l['lt2_hr'] or '—'} lpm), Peso={old_l['weight'] or '—'} kg"
                )
    else:
        lines.append("\n=== PRUEBAS DE LACTATO ===\nAún no registra pruebas de lactato en el CRM.")

    # Historial de sueño ASSQ
    if ctx["sleep"]:
        s0 = ctx["sleep"]
        lines.append(f"\n=== SUEÑO Y RECUPERACIÓN ASSQ (Último: {s0['date']}) ===")
        lines.append(f"  * Score SDS: {s0['sds_score']}/17 ({s0['category']})")
        if s0.get("hours"):
            lines.append(f"  * Horas reales de sueño: {s0['hours']}")
        if s0.get("quality"):
            lines.append(f"  * Calidad percibida: {s0['quality']}")
        if s0.get("latency"):
            lines.append(f"  * Latencia para dormir: {s0['latency']}")
        if s0.get("awakenings"):
            lines.append(f"  * Despertares nocturnos: {s0['awakenings']}")
        if s0.get("medications"):
            lines.append(f"  * Medicamentos para dormir: {s0['medications']}")
        if len(ctx["sleep_history"]) > 1:
            trend_bits = [f"{sh['date']}: {sh['sds_score']}/17" for sh in reversed(ctx["sleep_history"])]
            lines.append("  * Tendencia reciente SDS (menor es mejor): " + " → ".join(trend_bits))
    else:
        lines.append("\n=== SUEÑO ASSQ ===\nAún no registra reportes de sueño.")

    ctx["summary_text"] = "\n".join(lines)
    return ctx


# ═════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT CONVERSACIONAL ESTILO CHATGPT / GEMINI (ALPHAX)
# ═════════════════════════════════════════════════════════════════════

def build_wolfy_system_prompt(athlete_context: dict) -> str:
    """Crea instrucciones conversacionales de alta precisión con todo el dossier del atleta."""
    return f"""Eres Wolfy 🐺, el coach de inteligencia artificial personal de AlphaX Training Team.
Hablas siempre en español con naturalidad, cercanía, rigor científico y continuidad conversacional real (como un chat con ChatGPT o Gemini junto a un fisiólogo deportivo de élite que conoce TODO el historial del deportista).

ESTILO Y CONTINUIDAD CONVERSACIONAL:
- Mantén fluidez natural entre turnos: recuerda lo que ya hablaron en mensajes anteriores, profundiza sin repetir introducciones robóticas ni volver a presentarte en cada mensaje.
- Usa los datos EXACTOS del deportista (estatura, peso, edad, IMC, sexo, altitud, deporte, objetivo, biomarcadores con evolución, umbrales LT1/LT2 en W y W/kg, curva de lactato y sueño ASSQ) siempre que aporten precisión a tu respuesta.
- Cuando calcules nutrición (gramos de carbohidratos, proteína, hidratación/sodio), zonas de entrenamiento o rangos clínicos, haz los cálculos personalizados con SU peso, estatura, edad, sexo y volumen real.
- Si el atleta hace una pregunta donde falta un dato clave (por ejemplo su estatura, peso o edad no están registrados), respóndele lo mejor posible y menciónale amablemente que puede completar ese dato en **"👤 Perfil Físico"** aquí arriba para afinar aún más el cálculo.
- Si el atleta menciona en el chat un nuevo dato (ej. "ahora peso 70 kg" o "me duele la rodilla"), tenlo en cuenta inmediatamente en toda la conversación.
- Lenguaje AlphaX: científico pero claro, práctico y accionable. Usa formato limpio con negritas, viñetas o tablas cortas cuando ayude a leer en celular.
- Seguridad clínica: nunca prescribas medicamentos con fórmula médica; si un marcador está en alerta crítica, explica el porqué fisiológico y sugiere revisarlo con su médico y entrenador AlphaX.

DOSSIER COMPLETO EN TIEMPO REAL DEL DEPORTISTA:
{athlete_context.get('summary_text', '')}

PREGUNTAS DE SEGUIMIENTO DINÁMICAS (OBLIGATORIO AL FINAL):
Al terminar CADA respuesta, agrega en la última línea (sin explicación adicional) exactamente 3 preguntas cortas y muy relevantes para continuar esta conversación en este formato exacto:
<!--FOLLOWUPS:["Pregunta corta 1?", "Pregunta corta 2?", "Pregunta corta 3?"]-->
Las 3 preguntas deben estar redactadas en primera persona desde el punto de vista del deportista (máximo 55 caracteres cada una) y conectadas directamente con lo que acabas de responder y los datos reales del atleta."""


# ═════════════════════════════════════════════════════════════════════
#  PREPARACIÓN MULTI-TURNO + STREAMING GEMINI API (SSE)
# ═════════════════════════════════════════════════════════════════════

GEMINI_CHAT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-flash-latest",
]

FOLLOWUP_MARKER = "<!--FOLLOWUPS:"
FOLLOWUP_REGEX = re.compile(r"<!--FOLLOWUPS:\s*(\[.*?\])\s*-->", re.DOTALL)


def parse_followups_from_response(raw_text: str) -> tuple:
    """
    Separa el bloque oculto <!--FOLLOWUPS:[...]--> del texto visible.
    Retorna (texto_limpio, lista_de_hasta_3_preguntas).
    """
    if not raw_text:
        return "", []
    followups = []
    match = FOLLOWUP_REGEX.search(raw_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                followups = [str(q).strip() for q in parsed if str(q).strip()][:3]
        except Exception:
            pass
        clean = FOLLOWUP_REGEX.sub("", raw_text).strip()
    else:
        # Eliminar cualquier fragmento truncado de <!--FOLLOWUPS: si el modelo se cortó
        clean = raw_text.split(FOLLOWUP_MARKER)[0].strip()
    return clean, followups


def _prepare_gemini_contents(messages: list, max_turns: int = 24) -> list:
    """
    Convierte el historial de chat al formato estricto de Gemini:
    - Ignora mensajes de bienvenida sintéticos.
    - Garantiza empezar SIEMPRE con role='user'.
    - Alterna estrictamente user/model fusionando turnos consecutivos del mismo rol.
    """
    real_msgs = [
        m for m in (messages or [])
        if not m.get("is_welcome")
        and m.get("role") in ("user", "assistant")
        and str(m.get("content", "")).strip()
    ]
    recent = real_msgs[-max_turns:]

    # Descartar cualquier mensaje 'assistant' inicial para que el turno 0 sea siempre 'user'
    while recent and recent[0]["role"] != "user":
        recent.pop(0)

    contents = []
    for msg in recent:
        role = "user" if msg["role"] == "user" else "model"
        text = str(msg["content"]).strip()
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def _build_gemini_payload(messages: list, system_prompt: str) -> dict:
    return {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": _prepare_gemini_contents(messages),
        "generationConfig": {
            "temperature": 0.65,
            "maxOutputTokens": 2048,
            "topP": 0.95,
        },
    }


def stream_gemini_chat(messages: list, system_prompt: str, api_key: str, full_capture: dict):
    """
    Generador que transmite la respuesta de Google Gemini palabra por palabra vía SSE.
    Oculta en tiempo real la etiqueta final <!--FOLLOWUPS:...--> mientras acumula la
    respuesta completa en `full_capture["raw"]`.
    """
    if not api_key:
        return
    payload = _build_gemini_payload(messages, system_prompt)
    if not payload["contents"]:
        return

    for model_name in GEMINI_CHAT_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={api_key}"
        try:
            with requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                stream=True,
                timeout=30,
            ) as resp:
                if resp.status_code == 200:
                    raw_acc = ""
                    pending_buf = ""
                    got_any = False

                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if not raw_line:
                            continue
                        if not raw_line.startswith("data:"):
                            continue
                        data_str = raw_line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            chunk_json = json.loads(data_str)
                        except Exception:
                            continue
                        candidates = chunk_json.get("candidates", [])
                        if not candidates:
                            continue
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for p in parts:
                            piece = p.get("text", "")
                            if not piece:
                                continue
                            got_any = True
                            raw_acc += piece
                            pending_buf += piece

                            # Si ya apareció el marcador de follow-ups, dejar de emitir texto visible
                            marker_pos = pending_buf.find(FOLLOWUP_MARKER)
                            if marker_pos != -1:
                                if marker_pos > 0:
                                    yield pending_buf[:marker_pos]
                                pending_buf = pending_buf[marker_pos:]
                                continue

                            # Retener un pequeño sufijo si termina con prefijo parcial de '<!--FOLLOWUPS:'
                            hold = 0
                            max_check = min(len(FOLLOWUP_MARKER) - 1, len(pending_buf))
                            for k in range(max_check, 0, -1):
                                if pending_buf.endswith(FOLLOWUP_MARKER[:k]):
                                    hold = k
                                    break
                            emit_len = len(pending_buf) - hold
                            if emit_len > 0:
                                yield pending_buf[:emit_len]
                                pending_buf = pending_buf[emit_len:]

                    # Al cerrar el stream, emitir lo que quedó pendiente si no era el marcador
                    if pending_buf and FOLLOWUP_MARKER not in pending_buf:
                        yield pending_buf

                    if got_any:
                        full_capture["raw"] = raw_acc
                        full_capture["ok"] = True
                        full_capture["model"] = model_name
                        return
                elif resp.status_code in (400, 403):
                    err_txt = resp.text[:300] if resp.text else ""
                    full_capture["error"] = f"Error API Gemini ({resp.status_code}): {err_txt}"
                    return
        except Exception:
            continue


def call_gemini_chat(messages: list, system_prompt: str, api_key: str) -> str:
    """Llamada síncrona de respaldo a Google Gemini API (cuando el streaming no está disponible)."""
    if not api_key:
        return None
    payload = _build_gemini_payload(messages, system_prompt)
    if not payload["contents"]:
        return None

    for model_name in GEMINI_CHAT_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"]
            elif resp.status_code in (400, 403):
                err_json = resp.json() if resp.text else {}
                err_msg = err_json.get("error", {}).get("message", resp.text)
                return f"⚠️ **Error de API Gemini ({resp.status_code})**: {err_msg}. Verifica la clave configurada."
        except Exception:
            continue
    return None


# ═════════════════════════════════════════════════════════════════════
#  MOTOR FISIOLÓGICO DE RESPALDO (CON DATOS 360° + SEGUIMIENTO)
# ═════════════════════════════════════════════════════════════════════

def _default_followups_for_topic(user_query: str, athlete_ctx: dict) -> list:
    q = (user_query or "").lower()
    if any(w in q for w in ("hemoglobina", "ferritina", "sangre", "oxigeno", "oxígeno", "hierro", "anemia")):
        return [
            "¿Qué alimentos mejoran mi absorción de hierro?",
            "¿Cómo impacta mi sangre en mi umbral LT2?",
            "¿Cuándo debería repetir mi examen de sangre?",
        ]
    if any(w in q for w in ("lactato", "umbral", "lt1", "lt2", "ftp", "zonas", "potencia")):
        return [
            "¿Cómo distribuir mis entrenos en Z2 y umbral?",
            "¿Cuántos gramos de carbos necesito en fondos?",
            "¿Mi sangre respalda bien mi rendimiento aeróbico?",
        ]
    if any(w in q for w in ("sueño", "dormir", "descanso", "assq", "fatiga", "ck", "recuper")):
        return [
            "Dame una rutina nocturna práctica de 3 pasos",
            "¿Cómo ajusto el entreno de mañana si dormí mal?",
            "¿Cómo está mi perfil general de salud hoy?",
        ]
    if any(w in q for w in ("nutri", "comer", "carbo", "prote", "hidrata", "peso", "imc", "estatura")):
        return [
            "¿Qué desayunar 2 horas antes de un fondo largo?",
            "¿Cuánta agua y sodio necesito por hora?",
            "¿Cómo está mi glucosa y perfil lipídico?",
        ]
    return [
        "Hazme un resumen 360° de todos mis datos",
        "¿Qué debería priorizar esta semana en mi entreno?",
        "Calcula mi nutrición diaria según mi peso",
    ]


def generate_local_wolfy_response(user_query: str, athlete_ctx: dict, has_api_key: bool = False) -> tuple:
    """
    Motor de respaldo inteligente basado en el dossier completo 360° del atleta.
    Retorna (texto_respuesta, lista_followups).
    """
    q = (user_query or "").lower()
    name = athlete_ctx.get("athlete_name", "atleta")
    gender = athlete_ctx.get("gender", "Hombre")
    prof = athlete_ctx.get("profile") or {}
    derived = athlete_ctx.get("derived") or {}
    bw = athlete_ctx.get("bloodwork") or {}
    lac = athlete_ctx.get("lactate") or {}
    sleep = athlete_ctx.get("sleep") or {}
    followups = _default_followups_for_topic(user_query, athlete_ctx)

    # 0. Resumen Integral 360° / Perfil / Estatura / IMC
    if any(w in q for w in ("resumen", "360", "perfil", "estatura", "altura", "imc", "bmi", "edad", "datos", "todo")):
        resp = [f"🐺 **Tu Resumen Integral AlphaX, {name}:**\n"]
        bio_bits = []
        if prof.get("height_cm"):
            bio_bits.append(f"**Estatura:** `{prof['height_cm']:.0f} cm`")
        if derived.get("effective_weight_kg"):
            bio_bits.append(f"**Peso:** `{derived['effective_weight_kg']} kg`")
        if derived.get("bmi"):
            bio_bits.append(f"**IMC:** `{derived['bmi']} kg/m²`")
        if prof.get("age_years"):
            bio_bits.append(f"**Edad:** `{prof['age_years']} años`")
        if prof.get("altitude_m") is not None:
            bio_bits.append(f"**Altitud:** `{prof['altitude_m']} msnm`")
        if bio_bits:
            resp.append("📏 **Biometría:** " + " · ".join(bio_bits))
        else:
            resp.append("📏 **Biometría:** Aún no tienes estatura/peso registrados. Complétalos arriba en **👤 Perfil Físico** para cálculos exactos.")
        if bw:
            hb = bw.get("hemoglobin")
            fer = bw.get("ferritin")
            ck = bw.get("ck")
            resp.append(f"🩸 **Sangre ({bw.get('date')}):** Hb `{hb or '—'} g/dL` · Ferritina `{fer or '—'} ng/mL` · CK `{ck or '—'} U/L`")
        if lac:
            wkg2 = f" (`{derived['lt2_wkg']} W/kg`)" if derived.get("lt2_wkg") else ""
            resp.append(f"🧪 **Lactato ({lac.get('date')}):** LT1 `{lac.get('lt1_power') or '—'}` · LT2 `{lac.get('lt2_power') or '—'}`{wkg2}")
        if sleep:
            resp.append(f"💤 **Sueño ({sleep.get('date')}):** Score SDS `{sleep.get('sds_score')}/17` ({sleep.get('category')})")
        if derived.get("protein_target_g"):
            resp.append(f"🥑 **Metas diarias orientativas:** Proteína `{derived['protein_target_g']}` · Carbohidratos `{derived.get('carb_target_g', '—')}`")
        return "\n".join(resp), followups

    # 1. Transporte de Oxígeno / Sangre
    if any(w in q for w in ("oxigeno", "oxígeno", "hemoglobina", "ferritina", "hematocrito", "hierro", "sangre", "vcm", "chcm", "eritrocito", "globulos rojos", "glóbulos rojos", "anemia")):
        hb, hto, fer, vcm = bw.get("hemoglobin"), bw.get("hematocrit"), bw.get("ferritin"), bw.get("vcm")
        resp = [f"🐺 **Análisis de Transporte de Oxígeno ({name}):**\n"]
        if any(v is not None for v in (hb, hto, fer, vcm)):
            if hb is not None:
                lbl, _ = classify_bloodwork_value("hemoglobin", hb, gender)
                resp.append(f"- **Hemoglobina:** `{hb} g/dL` ({lbl})")
            if hto is not None:
                lbl, _ = classify_bloodwork_value("hematocrit", hto, gender)
                resp.append(f"- **Hematocrito:** `{hto}%` ({lbl})")
            if vcm is not None:
                resp.append(f"- **VCM:** `{vcm} fL`")
            if fer is not None:
                lbl, _ = classify_bloodwork_value("ferritin", fer, gender)
                resp.append(f"- **Ferritina:** `{fer} ng/mL` ({lbl})")
            if prof.get("altitude_m") and prof["altitude_m"] >= 1500:
                resp.append(f"\n🏔️ Como entrenas a **{prof['altitude_m']} msnm**, tu demanda de hierro y eritropoyesis es mayor; mantener ferritina > 50 ng/mL es clave.")
        else:
            resp.append("Aún no tienes exámenes de sangre cargados. Súbelos en la pestaña **🩸 Hemogramas** y los analizamos juntos.")
        return "\n".join(resp), followups

    # 2. Daño Muscular (CK)
    if any(w in q for w in ("ck", "creatina quinasa", "creatina kinasa", "muscular", "dolor", "fatiga", "sobreentrenamiento")):
        ck = bw.get("ck")
        resp = [f"🐺 **Daño Muscular y Recuperación ({name}):**\n"]
        if ck is not None:
            lbl, _ = classify_bloodwork_value("ck", ck, gender)
            resp.append(f"- **Creatina Kinasa (CK):** `{ck} U/L` ({lbl})")
            if ck > 300:
                resp.append("⚡ Indica carga muscular reciente o micro-roturas activas. Prioriza hidratación, proteína adecuada y rodaje suave en Z1-Z2.")
            else:
                resp.append("✅ Nivel bien controlado: tus músculos están asimilando correctamente la carga.")
        else:
            resp.append("Aún no registras un valor de CK en tu último examen. Si sientes pesadez persistente, incluir CK en tu próximo control es muy útil.")
        return "\n".join(resp), followups

    # 3. Lactato y Umbrales
    if any(w in q for w in ("lactato", "umbral", "lt1", "lt2", "zonas", "ftp", "ritmos", "potencia", "vatios", "watts")):
        resp = [f"🐺 **Tus Umbrales Fisiológicos ({name}):**\n"]
        if lac.get("lt1_power") or lac.get("lt2_power"):
            wkg1 = f" (`{derived['lt1_wkg']} W/kg`)" if derived.get("lt1_wkg") else ""
            wkg2 = f" (`{derived['lt2_wkg']} W/kg`)" if derived.get("lt2_wkg") else ""
            resp.append(f"Última prueba ({lac.get('date')} · {lac.get('sport')}):")
            resp.append(f"- **LT1 (Aeróbico):** `{lac.get('lt1_power') or '—'}`{wkg1} · FC `{lac.get('lt1_hr') or '—'} lpm`")
            resp.append(f"- **LT2 (Umbral Anaeróbico):** `{lac.get('lt2_power') or '—'}`{wkg2} · FC `{lac.get('lt2_hr') or '—'} lpm`")
        else:
            resp.append("Aún no tienes una prueba de lactato registrada. Cuando hagamos tu evaluación, aquí verás tus zonas exactas y W/kg.")
        return "\n".join(resp), followups

    # 4. Sueño ASSQ
    if any(w in q for w in ("sueño", "dormir", "descanso", "insomnio", "assq", "sds")):
        sds = sleep.get("sds_score")
        resp = [f"🐺 **Sueño y Recuperación ({name}):**\n"]
        if sds is not None:
            resp.append(f"- **Último Score SDS ({sleep.get('date')}):** `{sds}/17` ({sleep.get('category')})")
            if sleep.get("hours"):
                resp.append(f"- **Horas reales:** `{sleep.get('hours')}`")
            if sds >= 8:
                resp.append("⚠️ Vale la pena priorizar higiene del sueño (oscuridad total, 0 pantallas 45 min antes y cena ligera) antes de meter sesiones muy intensas.")
            else:
                resp.append("✅ Buen descanso nocturno para consolidar tus adaptaciones.")
        else:
            resp.append("Aún no tienes registros en **💤 Sueño (ASSQ)**. Diligencia tu cuestionario para monitorear tu recuperación.")
        return "\n".join(resp), followups

    # 5. Nutrición / Hidratación personalizada por peso
    if any(w in q for w in ("nutri", "comer", "comida", "desayuno", "carbo", "prote", "hidrata", "agua", "sales", "electrolitos", "peso", "dieta")):
        w_eff = derived.get("effective_weight_kg")
        resp = [f"🐺 **Estrategia Nutricional Personalizada ({name}):**\n"]
        if w_eff:
            resp.append(f"Calculado para tu peso actual de **`{w_eff} kg`**:")
            resp.append(f"- **Proteína diaria:** `{derived.get('protein_target_g')}` (repartida en 3-4 tomas de ~25-35 g).")
            resp.append(f"- **Carbohidratos diarios:** `{derived.get('carb_target_g')}` ajustando según intensidad del día.")
        resp.append("- **Durante sesiones > 75 min:** 45–75 g de carbohidratos/hora + 500–750 ml de líquido con 400–800 mg de sodio/hora.")
        resp.append("- **Post-entreno:** Combina carbohidratos rápidos + 25–30 g de proteína dentro de los 45 min posteriores.")
        return "\n".join(resp), followups

    # 6. Respuesta general
    if has_api_key:
        msg = (
            f"🐺 Hubo una breve intermitencia conectando con Gemini para responder *\"{user_query}\"*. "
            "Intenta enviarla de nuevo en unos segundos o elige uno de los temas rápidos de abajo."
        )
    else:
        msg = (
            f"🐺 Estoy en modo fisiológico local. Para activar conversación libre completa con Gemini, "
            "tu coach puede configurar la API Key central en el CRM. Mientras tanto, prueba una de las sugerencias abajo."
        )
    return msg, followups


# ═════════════════════════════════════════════════════════════════════
#  RENDERIZADO PRINCIPAL DE LA PESTAÑA WOLFY (ESTILO CHATGPT / GEMINI)
# ═════════════════════════════════════════════════════════════════════

STARTER_PROMPTS = [
    ("📋 Resumen 360°", "Hazme un análisis integral 360° de todos mis datos actuales (perfil físico, sangre, lactato y sueño) y dime mis 2 prioridades principales."),
    ("🩸 Oxígeno y Hierro", "¿Cómo está mi transporte de oxígeno (hemoglobina, hematocrito y ferritina) según mi perfil y cómo afecta mi rendimiento?"),
    ("⚡ Daño Muscular (CK)", "¿Cómo está mi Creatina Quinasa (CK) y mi nivel de recuperación muscular? ¿Cómo debería modular el entreno esta semana?"),
    ("🧪 Umbrales de Lactato", "Analiza mis umbrales LT1 y LT2 (incluyendo W/kg si aplica) y explícame cómo entrenar cada zona con precisión."),
    ("🥑 Nutrición a Medida", "Calcula una estrategia práctica de nutrición e hidratación (diaria y para fondos largos) personalizada con mi peso y estatura."),
    ("💤 Sueño y Descanso", "¿Cómo está mi calidad de sueño ASSQ últimamente y qué ajustes prácticos me recomiendas para recuperar mejor?"),
]


def _render_html(html_str: str):
    if hasattr(st, "html"):
        st.html(html_str)
    else:
        st.markdown(html_str, unsafe_allow_html=True)


def render_wolfy_tab(member_id: int, is_admin: bool = False):
    """Renderiza la interfaz conversacional dinámica de Wolfy AI Coach."""
    init_db()

    # 1. Contexto completo 360° del deportista
    athlete_ctx = build_athlete_context(member_id)
    system_prompt = build_wolfy_system_prompt(athlete_ctx)
    api_key = get_gemini_api_key()

    # 2. Cabecera compacta estilo ChatGPT / Gemini
    avatar_path = "assets/images/wolfy_avatar.png"
    avatar_b64 = ""
    if os.path.exists(avatar_path):
        try:
            with open(avatar_path, "rb") as img_f:
                avatar_b64 = base64.b64encode(img_f.read()).decode()
        except Exception:
            avatar_b64 = ""

    avatar_img_tag = (
        f'<img src="data:image/png;base64,{avatar_b64}" style="width: 58px; height: 58px; border-radius: 50%; border: 2px solid #00EEFF; box-shadow: 0 0 14px rgba(0,238,255,0.38); object-fit: cover;">'
        if avatar_b64
        else '<div style="font-size:2.2rem;">🐺</div>'
    )

    status_badge = (
        '<span style="background: rgba(0,255,136,0.14); color: #00FF88; padding: 4px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 800; border: 1px solid rgba(0,255,136,0.35);">● Gemini IA Activo</span>'
        if api_key
        else '<span style="background: rgba(255,180,0,0.14); color: #FFCC44; padding: 4px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 800; border: 1px solid rgba(255,180,0,0.35);">● Modo Fisiológico Local</span>'
    )

    # Indicadores de qué datos conoce Wolfy hoy
    data_pills = []
    prof = athlete_ctx.get("profile") or {}
    derived = athlete_ctx.get("derived") or {}
    if prof.get("height_cm") and derived.get("effective_weight_kg"):
        data_pills.append(f"📏 {prof['height_cm']:.0f} cm · {derived['effective_weight_kg']} kg")
    elif derived.get("effective_weight_kg"):
        data_pills.append(f"⚖️ {derived['effective_weight_kg']} kg")
    if athlete_ctx.get("bloodwork"):
        data_pills.append(f"🩸 Sangre ({athlete_ctx['bloodwork']['date']})")
    if athlete_ctx.get("lactate"):
        data_pills.append(f"🧪 Lactato ({athlete_ctx['lactate']['date']})")
    if athlete_ctx.get("sleep"):
        data_pills.append(f"💤 Sueño ({athlete_ctx['sleep']['sds_score']}/17)")

    pills_html = "".join(
        f'<span style="background:rgba(255,255,255,0.06); color:#CBD5E1; border:1px solid rgba(255,255,255,0.12); '
        f'padding:2px 8px; border-radius:12px; font-size:0.7rem; font-weight:600;">{p}</span>'
        for p in data_pills
    )

    _render_html(
        f"""
        <div style="background: linear-gradient(135deg, rgba(16, 22, 34, 0.96), rgba(10, 13, 22, 0.99));
                    border: 1.5px solid rgba(0, 238, 255, 0.32); border-radius: 16px; padding: 14px 16px;
                    margin-bottom: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.45);">
            <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    {avatar_img_tag}
                    <div>
                        <div style="color:#00EEFF; font-size:1.2rem; font-weight:900; letter-spacing:0.4px; line-height:1.15;">
                            WOLFY <span style="color:#FFFFFF; font-weight:400; font-size:0.95rem;">| AlphaX AI Coach</span>
                        </div>
                        <div style="color:#94A3B8; font-size:0.78rem; margin-top:2px;">
                            Conversación continua conectada a tu perfil 360° en tiempo real
                        </div>
                    </div>
                </div>
                <div>{status_badge}</div>
            </div>
            {"<div style='display:flex; flex-wrap:wrap; gap:6px; margin-top:10px;'>" + pills_html + "</div>" if pills_html else ""}
        </div>
        """
    )

    # 3. Perfil Biométrico del Atleta (Estatura, Peso, Edad, Altitud, Objetivo...)
    render_athlete_biometric_profile_editor(
        member_id=member_id,
        key_prefix=f"wolfy_{'adm' if is_admin else 'ath'}",
        expanded=False,
        is_admin=is_admin,
    )

    # 4. Panel de Configuración Central de Gemini (Solo Admin)
    if is_admin:
        with st.expander("⚙️ Clave Central de Google Gemini (Club AlphaX)", expanded=(not api_key)):
            if api_key:
                st.success(f"✅ **Clave de Gemini Activa:** `...{api_key[-6:] if len(api_key) > 6 else '***'}` — Habilitada para todos los atletas.")
            else:
                st.warning("🔑 Configura la API Key de Gemini una vez aquí y queda activa automáticamente para todos los atletas.")
                st.markdown("Obtén tu clave en 👉 [**Google AI Studio**](https://aistudio.google.com/app/apikey).")
            col_k1, col_k2 = st.columns([3, 1])
            with col_k1:
                input_k = st.text_input("GEMINI_API_KEY:", type="password", key=f"admin_gemini_in_{member_id}", placeholder="AIzaSy...")
            with col_k2:
                st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
                if st.button("💾 Guardar", use_container_width=True, key=f"btn_save_k_{member_id}"):
                    if input_k.strip() and save_gemini_api_key(input_k.strip()):
                        st.success("✅ Clave guardada y activa.")
                        st.rerun()
                    else:
                        st.error("Ingresa una clave válida.")

    # 5. Estado conversacional con persistencia en DB
    chat_key = f"wolfy_chat_{member_id}"
    loaded_key = f"wolfy_loaded_{member_id}"
    pending_key = f"wolfy_pending_prompt_{member_id}"
    followups_key = f"wolfy_followups_{member_id}"

    if not st.session_state.get(loaded_key):
        db_history = load_chat_history(member_id, limit=30)
        st.session_state[chat_key] = db_history
        st.session_state[loaded_key] = True

    messages = st.session_state.get(chat_key, [])
    has_conversation = len(messages) > 0

    # Barra superior del chat: botón "Nueva conversación" cuando ya hay hilo
    if has_conversation:
        c_bar1, c_bar2 = st.columns([3, 1.3])
        with c_bar1:
            st.caption(f"💬 Conversación activa con memoria ({len(messages)} mensajes) · Puedes seguir preguntando sobre el mismo tema.")
        with c_bar2:
            if st.button("✨ Nueva conversación", use_container_width=True, key=f"wolfy_new_{member_id}"):
                clear_chat_history(member_id)
                st.session_state[chat_key] = []
                st.session_state.pop(followups_key, None)
                st.session_state.pop(pending_key, None)
                st.rerun()

    avatar_user = "🏃"
    avatar_wolfy = avatar_path if os.path.exists(avatar_path) else "🐺"

    # 6. Pantalla de bienvenida limpia con preguntas preseleccionadas (si aún no empieza el chat)
    if not has_conversation:
        first_name = (athlete_ctx.get("athlete_name") or "Atleta").split()[0].title()
        missing_hint = ""
        if not prof.get("height_cm") or not derived.get("effective_weight_kg"):
            missing_hint = "\n\n💡 *Tip: si agregas tu **estatura, peso y edad** en **👤 Perfil Físico** aquí arriba, puedo calcular con precisión tu IMC, W/kg y nutrición exacta.*"

        with st.chat_message("assistant", avatar=avatar_wolfy):
            st.markdown(
                f"¡Hola, **{first_name}**! 🐺 Soy **Wolfy**, tu coach de inteligencia artificial en **AlphaX**.\n\n"
                f"Tengo tu perfil y tus métricas cargadas en tiempo real. Puedes elegir una pregunta rápida abajo o escribirme lo que quieras para empezar una conversación.{missing_hint}"
            )

        st.markdown(
            "<div style='color:#8E9BAE; font-size:0.75rem; font-weight:800; text-transform:uppercase; margin:10px 0 6px 0;'>💡 Preguntas rápidas para empezar:</div>",
            unsafe_allow_html=True,
        )
        for row_idx in range(0, len(STARTER_PROMPTS), 3):
            row = STARTER_PROMPTS[row_idx : row_idx + 3]
            cols = st.columns(len(row))
            for col_obj, (btn_lbl, prompt_txt) in zip(cols, row):
                with col_obj:
                    if st.button(btn_lbl, use_container_width=True, key=f"wolfy_starter_{member_id}_{btn_lbl}"):
                        st.session_state[pending_key] = prompt_txt
                        st.rerun()
    else:
        # 7. Renderizar historial completo de la conversación
        for msg in messages:
            with st.chat_message(msg["role"], avatar=avatar_wolfy if msg["role"] == "assistant" else avatar_user):
                st.markdown(msg["content"])

        # Chips dinámicos de seguimiento justo debajo de la última respuesta de Wolfy
        followups = st.session_state.get(followups_key) or []
        if not followups and messages and messages[-1]["role"] == "assistant":
            last_u = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            followups = _default_followups_for_topic(last_u, athlete_ctx)

        if followups and not st.session_state.get(pending_key):
            st.markdown(
                "<div style='color:#8E9BAE; font-size:0.73rem; font-weight:700; margin:6px 0 4px 0;'>✨ Continúa la conversación:</div>",
                unsafe_allow_html=True,
            )
            f_cols = st.columns(len(followups))
            for f_idx, f_q in enumerate(followups):
                with f_cols[f_idx]:
                    if st.button(f"💬 {f_q}", use_container_width=True, key=f"wolfy_fu_{member_id}_{len(messages)}_{f_idx}"):
                        st.session_state[pending_key] = f_q
                        st.rerun()

        # Acceso rápido discreto a preguntas preseleccionadas durante el chat
        with st.expander("💡 Ver preguntas sugeridas rápidas", expanded=False):
            for row_idx in range(0, len(STARTER_PROMPTS), 3):
                row = STARTER_PROMPTS[row_idx : row_idx + 3]
                cols = st.columns(len(row))
                for col_obj, (btn_lbl, prompt_txt) in zip(cols, row):
                    with col_obj:
                        if st.button(btn_lbl, use_container_width=True, key=f"wolfy_inchat_starter_{member_id}_{btn_lbl}"):
                            st.session_state[pending_key] = prompt_txt
                            st.rerun()

    # 8. Caja de entrada de chat + procesamiento con streaming en tiempo real
    typed_prompt = st.chat_input(
        "Pregúntale lo que quieras a Wolfy (exámenes, lactato, nutrición, entreno, sueño)...",
        key=f"wolfy_chat_input_{member_id}",
    )
    pending_prompt = st.session_state.pop(pending_key, None)
    user_prompt = typed_prompt or pending_prompt

    if user_prompt:
        clean_user_prompt = user_prompt.strip()
        if clean_user_prompt:
            # Agregar y guardar mensaje del usuario
            st.session_state[chat_key].append({"role": "user", "content": clean_user_prompt})
            save_chat_message(member_id, "user", clean_user_prompt)

            with st.chat_message("user", avatar=avatar_user):
                st.markdown(clean_user_prompt)

            # Generar y transmitir respuesta de Wolfy en vivo
            with st.chat_message("assistant", avatar=avatar_wolfy):
                final_clean_text = ""
                dynamic_followups = []

                if api_key:
                    capture = {"raw": "", "ok": False, "error": None}
                    try:
                        stream_gen = stream_gemini_chat(
                            st.session_state[chat_key],
                            system_prompt,
                            api_key,
                            capture,
                        )
                        st.write_stream(stream_gen)
                    except Exception:
                        pass

                    if capture.get("ok") and capture.get("raw"):
                        final_clean_text, dynamic_followups = parse_followups_from_response(capture["raw"])
                    else:
                        # Fallback a llamada síncrona de Gemini si SSE falló
                        with st.spinner("Wolfy analizando tu perfil completo..."):
                            raw_sync = call_gemini_chat(st.session_state[chat_key], system_prompt, api_key)
                        if raw_sync:
                            final_clean_text, dynamic_followups = parse_followups_from_response(raw_sync)
                            st.markdown(final_clean_text)

                if not final_clean_text:
                    final_clean_text, dynamic_followups = generate_local_wolfy_response(
                        clean_user_prompt,
                        athlete_ctx,
                        has_api_key=bool(api_key),
                    )
                    st.markdown(final_clean_text)

                if not dynamic_followups:
                    dynamic_followups = _default_followups_for_topic(clean_user_prompt, athlete_ctx)

                st.session_state[chat_key].append({"role": "assistant", "content": final_clean_text})
                save_chat_message(member_id, "assistant", final_clean_text)
                st.session_state[followups_key] = dynamic_followups
                st.rerun()
