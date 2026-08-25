import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import hashlib
import unicodedata
from streamlit_cookies_controller import CookieController
from background_base64 import BACKGROUND_IMAGE_BASE64

try:
    from database import SessionLocal, Member, SleepRecord, AthleteUser, LactateTest, LactateTestStep, BloodworkRecord, engine
    from sqlalchemy import text
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        try:
            conn.execute(text("ALTER TABLE sleep_records ADD COLUMN raw_medications VARCHAR;"))
        except:
            pass
        try:
            conn.execute(text("ALTER TABLE athlete_users ADD COLUMN last_ip VARCHAR;"))
        except:
            pass
except Exception as e:
    st.error(f"💀 Error de Importación de DB: {e}")
    st.stop()

# --- CONFIGURACIÓN DE LA PÁGINA (Optimizada para móvil) ---
try:
    st.set_page_config(
        page_title="AlphaX Endurance Coaching App",
        page_icon="⚡",
        layout="centered",
        initial_sidebar_state="collapsed"
    )
except Exception:
    pass

# Estilos personalizados para la app pública (tema oscuro AlphaX)
css_styles = f"""
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700;800;900&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stApp {{ font-family: 'Nunito Sans', sans-serif !important; color: #FFFFFF !important; }} 
body, .stApp {{ background-image: url("data:image/png;base64,{BACKGROUND_IMAGE_BASE64}"); }}
div[data-baseweb="input"] > div, div[data-baseweb="base-input"] {{ background-color: #121212 !important; }}
div[data-baseweb="select"] > div {{ background-color: #121212 !important; border-color: #00EEFF !important; }}
div[data-baseweb="menu"], div[role="listbox"], div[role="option"] {{ background-color: #121212 !important; color: #FFFFFF !important; }}
div[role="option"]:hover, div[role="option"][aria-selected="true"] {{ background-color: #00EEFF !important; color: #000000 !important; }}
.stButton > button {{ border-radius: 8px; font-weight: 700; border: 1px solid #FFFFFF; color: #FFFFFF !important; background-color: transparent !important; transition: all 0.3s ease; }}
.stButton > button:hover {{ background-color: #FFFFFF !important; color: #000000 !important; box-shadow: 0 0 15px rgba(255, 255, 255, 0.4); }}
.stPlotlyChart {{ background-color: white !important; border-radius: 20px; border: 2px solid #00EEFF; padding: 0px; overflow: hidden; box-shadow: 0 0 20px rgba(0, 238, 255, 0.4); }}

/* Tabs styling - Cuadritos modernos para navegación y deslizamiento */
div[data-baseweb="tab-list"] {{
    display: flex !important;
    gap: 8px !important;
    padding: 8px 2px 14px 2px !important;
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
    border-bottom: 1px solid rgba(0, 238, 255, 0.2) !important;
    justify-content: center !important;
}}

button[data-baseweb="tab"] {{
    background: rgba(18, 18, 32, 0.9) !important;
    border: 1.5px solid rgba(0, 238, 255, 0.25) !important;
    border-radius: 12px !important;
    padding: 10px 14px !important;
    font-size: 0.9rem !important;
    font-weight: 700 !important;
    color: #BBBBBB !important;
    text-align: center !important;
    min-width: 100px !important;
    flex: 1 1 0px !important;
    max-width: 220px !important;
    transition: all 0.25s ease-in-out !important;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3) !important;
    white-space: normal !important;
    line-height: 1.25 !important;
}}

button[data-baseweb="tab"]:hover {{
    border-color: #00EEFF !important;
    color: #00EEFF !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 15px rgba(0, 238, 255, 0.3) !important;
}}

button[data-baseweb="tab"][aria-selected="true"] {{
    background: linear-gradient(135deg, rgba(0, 238, 255, 0.25), rgba(0, 100, 255, 0.4)) !important;
    border: 2px solid #00EEFF !important;
    color: #00EEFF !important;
    font-weight: 800 !important;
    box-shadow: 0 0 16px rgba(0, 238, 255, 0.45) !important;
}}

div[data-baseweb="tab-border"], div[data-baseweb="tab-highlight"] {{
    display: none !important;
}}
</style>
"""
st.markdown(css_styles, unsafe_allow_html=True)

# --- LÓGICA DE PUNTUACIÓN CLÍNICA (ASSQ) ---
puntajes_horas = {
    "Más de 9 horas": 0, "8 a 9 horas": 1, "7 a 8 horas": 2, "6 a 7 horas": 3, "5 a 6 horas": 4
}
puntajes_calidad = {
    "Muy satisfecho": 0, "Algo satisfecho": 1, "Ni satisfecho ni insatisfecho": 2, "Algo insatisfecho": 3, "Muy insatisfecho": 4
}
puntajes_latencia = {
    "15 minutos o menos": 0, "16 a 30 minutos": 1, "31 a 60 minutos": 2, "Más de 60 minutos": 3
}
puntajes_despertares = {
    "Ninguna": 0, "Una o dos veces por semana": 1, "Tres o cuatro veces por semana": 2, "Cinco a siete días por semana": 3
}
puntajes_medicamentos = {
    "Ninguna": 0, "Una o dos veces por semana": 1, "Tres o cuatro veces por semana": 2, "Cinco a siete veces por semana": 3
}

# --- RANGOS Y HELPERS DE HEMOGRAMA ---
BLOODWORK_RANGES_FULL = {
    "hemoglobin":  {"low": 13.5, "opt_lo": 15.0, "opt_hi": 17.5, "high": 18.0, "unit": "g/dL",    "name": "Hemoglobina Total",                                    "emoji": "🔴", "color": "#FF5555", "bg": "rgba(255, 85, 85, 0.05)"},
    "vcm":         {"low": 80,   "opt_lo": 82,   "opt_hi": 95,   "high": 100,  "unit": "fL",      "name": "Volumen Corpuscular Medio",                            "emoji": "🟠", "color": "#FF9F43", "bg": "rgba(255, 159, 67, 0.05)"},
    "chcm":        {"low": 32,   "opt_lo": 33,   "opt_hi": 36,   "high": 36,   "unit": "g/dL",    "name": "Concentración de Hemoglobina Corpuscular Media",      "emoji": "🟡", "color": "#FECA57", "bg": "rgba(254, 202, 87, 0.05)"},
    "rbc":         {"low": 4.5,  "opt_lo": 5.0,  "opt_hi": 5.8,  "high": 6.0,  "unit": "×10⁶/μL", "name": "Conteo de Eritrocitos (Glóbulos Rojos)",                 "emoji": "🩸", "color": "#FF6B6B", "bg": "rgba(255, 107, 107, 0.05)"},
    "hematocrit":  {"low": 35,   "opt_lo": 40,   "opt_hi": 50,   "high": 54,   "unit": "%",       "name": "Hematocrito",                                          "emoji": "💧", "color": "#00EEFF", "bg": "rgba(0, 238, 255, 0.05)"},
    "ferritin":    {"low": 30,   "opt_lo": 50,   "opt_hi": 150,  "high": 400,  "unit": "ng/mL",   "name": "Ferritina Sérica",                                     "emoji": "⚡", "color": "#B8E994", "bg": "rgba(184, 233, 148, 0.05)"},
    "ck":          {"low": 60,   "opt_lo": 61,   "opt_hi": 400,  "high": 500,  "unit": "U/L",     "name": "Creatina Kinasa (CK)",                                "emoji": "💪", "color": "#A55EEA", "bg": "rgba(165, 94, 234, 0.05)"},
    "vitamin_b12": {"low": 200,  "opt_lo": 400,  "opt_hi": 900,  "high": 900,  "unit": "pg/mL",   "name": "Vitamina B12 (Cobalamina)",                           "emoji": "💊", "color": "#2ED573", "bg": "rgba(46, 213, 115, 0.05)"},
    "folic_acid":  {"low": 3,    "opt_lo": 6,    "opt_hi": 20,   "high": 20,   "unit": "ng/mL",   "name": "Ácido Fólico (Folato)",                                "emoji": "🌿", "color": "#26DE81", "bg": "rgba(38, 222, 129, 0.05)"},
}

def classify_value_ath(key, value):
    if value is None:
        return "—", "#666666"
    r = BLOODWORK_RANGES_FULL[key]
    if value < r["low"]:
        return "BAJO", "#FF4B4B"
    elif value < r["opt_lo"]:
        label = "LÍMITE" if key in ["vitamin_b12", "folic_acid"] else "INTERMEDIO-BAJO"
        return label, "#FFD700"
    elif value <= r["opt_hi"]:
        return "ÓPTIMO", "#00FF00"
    elif value <= r["high"]:
        label = "ELEVADO" if key == "ck" else "INTERMEDIO-ALTO"
        return label, "#FFD700"
    else:
        label = "MUY ALTO" if key == "ck" else "ALTO"
        return label, "#FF4B4B"

def badge_html_ath(label, color):
    return f'<span style="background:{color}22; color:{color}; padding:2px 6px; border-radius:10px; font-size:0.7rem; font-weight:bold; border:1px solid {color}44;">{label}</span>'

def delta_html_ath(current, previous, default_color="#888888"):
    if current is None or previous is None:
        return '<span style="color:#555555; font-size:0.75rem;">—</span>'
    diff = current - previous
    if abs(diff) < 0.001:
        return f'<span style="color:{default_color}; font-size:0.75rem; font-weight:600;">= 0.0</span>'
    elif diff > 0:
        return f'<span style="color:#00FF00; font-weight:bold; font-size:0.82rem;">▲ +{diff:.1f}</span>'
    else:
        return f'<span style="color:#FF4B4B; font-weight:bold; font-size:0.82rem;">▼ {diff:.1f}</span>'

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def clean_and_normalize(name):
    if not name:
        return ""
    n = "".join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    n = n.lower().replace('ñ', 'n')
    n = "".join(c if c.isalnum() or c.isspace() else "" for c in n)
    return " ".join(n.split())

def get_best_matches(query, active_members):
    input_clean = clean_and_normalize(query)
    if not input_clean:
        return []
        
    input_words = input_clean.split()
    matches = []
    
    for m in active_members:
        member_clean = clean_and_normalize(m.name)
        member_words = member_clean.split()
        
        score = 0
        if member_clean == input_clean:
            score += 20
            
        for iw in input_words:
            if iw in member_words:
                score += 5
            elif any(iw in mw for mw in member_words):
                score += 2
                
        if score > 0:
            from difflib import SequenceMatcher
            similarity = SequenceMatcher(None, input_clean, member_clean).ratio()
            score += similarity * 2
            matches.append((m, score))
            
    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches]

def get_client_ip():
    try:
        headers = st.context.headers
        for header_name in ["x-forwarded-for", "x-real-ip", "forwarded"]:
            val = headers.get(header_name)
            if val:
                if header_name == "x-forwarded-for":
                    return val.split(",")[0].strip()
                elif header_name == "forwarded":
                    for part in val.split(";"):
                        if part.strip().startswith("for="):
                            return part.split("=")[1].strip().strip('"')
                return val.strip()
    except Exception:
        pass
    return None

def obtener_recomendaciones(score):
    if score <= 4:
        return (
            "🏆 **Óptimo (0-4):** ¡Excelente descanso! Mantén tus hábitos actuales de higiene del sueño. "
            "Estás durmiendo lo suficiente para asimilar de forma óptima las cargas de entrenamiento 🐺.<br><br>"
            "💡 *Consejo:* Evita pantallas 30 minutos antes de dormir para maximizar la melatonina natural y la relajación."
        )
    elif score <= 7:
        return (
            "💤 **Leve (5-7):** Tu descanso tiene pequeñas oportunidades de mejora. Podrías experimentar algo de fatiga acumulada.<br><br>"
            "💡 *Consejo:* Revisa que tu habitación esté completamente a oscuras y fresca (18-20°C). Limita la cafeína después de las 2:00 PM."
        )
    elif score <= 10:
        return (
            "⚠️ **Moderado (8-10):** ¡Alerta de recuperación! Tu descanso no está siendo óptimo y esto "
            "puede incrementar tu fatiga, mermar tu rendimiento y elevar el riesgo de lesiones.<br><br>"
            "💡 *Consejo:* Te recomendamos comentarle a tu coach cómo te estás sintiendo. Intenta realizar 5-10 minutos de "
            "respiración profunda o estiramiento suave antes de acostarte para inducir la relajación."
        )
    else:
        return (
            "🚨 **Severo (11-17):** ¡Alerta crítica de sueño! Tu descanso está severamente afectado. Tu cuerpo "
            "se encuentra en un estado de recuperación muy deficiente y alta susceptibilidad a fatiga crónica 🛑.<br><br>"
            "💡 *Consejo Urgente:* Es muy importante que hables con tu coach de inmediato para ajustar tus cargas de entrenamiento "
            "de forma temporal. Prioriza el descanso y considera consultar con un especialista del sueño."
        )

meses_es = {1: "ENE", 2: "FEB", 3: "MAR", 4: "ABR", 5: "MAY", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DIC"}

def format_date_es(d):
    if isinstance(d, str):
        try: d = datetime.strptime(d.split()[0], "%Y-%m-%d")
        except: pass
    if hasattr(d, "month"): return f"{d.day} {meses_es[d.month]}"
    return str(d)

# --- INTERFAZ DE USUARIO Y BANNER PRINCIPAL ---
st.markdown("<h1 style='text-align: center; font-size: clamp(1.3rem, 4vw, 2.2rem); color: #00EEFF; font-weight: 800; margin-top: 10px; margin-bottom: 0px;'>⚡ BIENVENIDO A ALPHAX ENDURANCE COACHING APP ⚡</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #BBBBBB; font-size: 0.95rem; font-weight: 600; margin-top: 4px; margin-bottom: 15px;'>Portal de Monitoreo de Rendimiento, Fisiología y Recuperación del Atleta</p>", unsafe_allow_html=True)

cookie_controller = CookieController()

if "athlete_user" not in st.session_state:
    st.session_state["athlete_user"] = None
if "athlete_member_id" not in st.session_state:
    st.session_state["athlete_member_id"] = None
if "last_score" not in st.session_state:
    st.session_state["last_score"] = None
if "last_recommendations" not in st.session_state:
    st.session_state["last_recommendations"] = None
if "show_toast" not in st.session_state:
    st.session_state["show_toast"] = False
if "logged_out" not in st.session_state:
    st.session_state["logged_out"] = False

# Rerun inicial único para garantizar que las cookies se lean del navegador
if "cookie_checked" not in st.session_state:
    st.session_state["cookie_checked"] = False

if not st.session_state["cookie_checked"]:
    st.session_state["cookie_checked"] = True
    st.rerun()

# Iniciar login automático multicapa
if not st.session_state["logged_out"] and st.session_state["athlete_user"] is None:
    # 1. Query Parameters
    query_athlete = st.query_params.get("athlete")
    query_email = st.query_params.get("email")
    
    if query_athlete or query_email:
        try:
            with SessionLocal() as session:
                user = None
                if query_email:
                    user = session.query(AthleteUser).filter(AthleteUser.email == query_email.lower().strip()).first()
                elif query_athlete:
                    user = session.query(AthleteUser).filter(AthleteUser.athlete_name == query_athlete).first()
                    if not user:
                        m_obj = session.query(Member).filter(Member.name == query_athlete).first()
                        if m_obj:
                            user = session.query(AthleteUser).filter(AthleteUser.athlete_name == m_obj.name).first()
                
                if user:
                    st.session_state["athlete_user"] = user.athlete_name
                    m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                    if m_obj:
                        st.session_state["athlete_member_id"] = m_obj.id
                    client_ip = get_client_ip()
                    if client_ip:
                        user.last_ip = client_ip
                        session.commit()
                    cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                    st.query_params.clear()
                    st.rerun()
        except Exception:
            pass

    # 2. Cookies
    try:
        athlete_cookie = cookie_controller.get("athlete_user_cookie")
    except Exception:
        athlete_cookie = None
    if athlete_cookie and st.session_state["athlete_user"] is None:
        st.session_state["athlete_user"] = athlete_cookie
        try:
            with SessionLocal() as session:
                m_obj = session.query(Member).filter(Member.name == athlete_cookie).first()
                if m_obj:
                    st.session_state["athlete_member_id"] = m_obj.id
        except Exception:
            pass
        st.rerun()

    # 3. IP del Dispositivo
    if st.session_state["athlete_user"] is None:
        client_ip = get_client_ip()
        if client_ip and client_ip not in ["127.0.0.1", "localhost", "::1", ""]:
            try:
                with SessionLocal() as session:
                    user = session.query(AthleteUser).filter(AthleteUser.last_ip == client_ip).first()
                    if user:
                        st.session_state["athlete_user"] = user.athlete_name
                        m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                        if m_obj:
                            st.session_state["athlete_member_id"] = m_obj.id
                        cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                        st.rerun()
            except Exception:
                pass

submitted = False

if not st.session_state["athlete_user"]:
    # LOGIN / REGISTRO
    st.subheader("👤 Acceso de Atleta")
    tab1, tab2 = st.tabs(["Iniciar Sesión", "Crear Cuenta"])
    
    with tab1:
        st.markdown("Si ya tienes cuenta, ingresa aquí:")
        login_email = st.text_input("Correo Electrónico", key="login_email")
        login_pass = st.text_input("Contraseña", type="password", key="login_pass")
        if st.button("Entrar", use_container_width=True, key="btn_login"):
            try:
                with SessionLocal() as session:
                    user = session.query(AthleteUser).filter(AthleteUser.email == login_email.lower().strip()).first()
                    if user and user.password_hash == hash_password(login_pass):
                        st.session_state["athlete_user"] = user.athlete_name
                        st.session_state["logged_out"] = False
                        m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                        if m_obj:
                            st.session_state["athlete_member_id"] = m_obj.id
                        client_ip = get_client_ip()
                        if client_ip:
                            user.last_ip = client_ip
                            session.commit()
                        cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                        st.rerun()
                    else:
                        st.error("Correo o contraseña incorrectos.")
            except Exception as e:
                st.error(f"Error al iniciar sesión: {e}")
        st.caption("¿Olvidaste tu contraseña? Comunícate con tu entrenador para restablecer tu acceso.")
            
    with tab2:
        st.markdown("¿Es tu primera vez? Crea tu cuenta para vincular tu progreso.")
        reg_name_query = st.text_input("Busca tu Nombre o Apellido (oficial de AlphaX):", key="reg_name_query")
        
        m_obj = None
        if reg_name_query.strip():
            try:
                with SessionLocal() as session:
                    active_members = session.query(Member).filter(Member.active == True).all()
                    matches = get_best_matches(reg_name_query, active_members)
                    if matches:
                        options = {m.name: m for m in matches}
                        selected_name = st.selectbox(
                            "Selecciona tu nombre oficial en AlphaX:", 
                            options=list(options.keys()), 
                            key="reg_selected_name"
                        )
                        m_obj = options[selected_name]
                    else:
                        st.error("❌ No encontramos coincidencias en la base de datos.")
            except Exception as e:
                st.error(f"Error buscando deportistas: {e}")
        else:
            st.info("Escribe tu nombre o apellido arriba para buscar tu perfil en AlphaX.")
            
        reg_email = st.text_input("Tu Correo Electrónico", key="reg_email")
        reg_pass = st.text_input("Crea una Contraseña", type="password", key="reg_pass")
        
        if st.button("Registrarme", use_container_width=True, key="btn_reg"):
            if not reg_name_query.strip() or not m_obj:
                st.error("Debes buscar y seleccionar tu nombre oficial de la lista.")
            elif not reg_email or not reg_pass:
                st.warning("Por favor, llena todos los campos.")
            else:
                try:
                    with SessionLocal() as session:
                        existing_email = session.query(AthleteUser).filter(AthleteUser.email == reg_email.lower().strip()).first()
                        if existing_email:
                            st.error("Este correo ya está registrado.")
                        else:
                            new_user = AthleteUser(
                                email=reg_email.lower().strip(),
                                password_hash=hash_password(reg_pass),
                                athlete_name=m_obj.name,
                                last_ip=get_client_ip()
                            )
                            session.add(new_user)
                            session.commit()
                            st.success(f"✅ Cuenta creada exitosamente vinculada a **{m_obj.name}**. Puedes iniciar sesión ahora.")
                except Exception as e:
                    st.error(f"Error al registrar la cuenta: {e}")

else:
    atleta = st.session_state["athlete_user"]
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**👤 Atleta:** <span style='color:#00EEFF; font-weight:bold;'>{atleta}</span>", unsafe_allow_html=True)
    with col2:
        if st.button("Salir", key="logout_btn"):
            st.session_state["athlete_user"] = None
            st.session_state["athlete_member_id"] = None
            st.session_state["logged_out"] = True
            cookie_controller.remove("athlete_user_cookie")
            st.rerun()
            
    # Mostrar enlace de acceso rápido personalizado
    try:
        host = st.context.headers.get("host", "localhost:8502")
        proto = st.context.headers.get("x-forwarded-proto", "http")
        base_url = f"{proto}://{host}"
        import urllib.parse
        direct_link = f"{base_url}/?app=atletas&athlete={urllib.parse.quote(atleta)}"
        
        st.markdown(
            f"""
            <div style="background: rgba(0, 238, 255, 0.05); border: 1px dashed #00EEFF; border-radius: 8px; padding: 10px; margin-top: 5px; margin-bottom: 12px;">
                <span style="font-size: 0.85rem; color: #00EEFF; font-weight: bold;">🔗 Enlace de Acceso Rápido:</span><br>
                <code style="word-break: break-all; color: #00EEFF; font-size: 0.8rem;">{direct_link}</code>
            </div>
            """,
            unsafe_allow_html=True
        )
    except Exception:
        pass
            
    if st.session_state.get("last_score"):
        st.success(st.session_state["last_score"])
        st.session_state["last_score"] = None
            
    # Obtener member_id
    member_id = st.session_state.get("athlete_member_id")
    if not member_id:
        try:
            with SessionLocal() as session:
                m_obj = session.query(Member).filter(Member.name == atleta).first()
                if m_obj:
                    member_id = m_obj.id
                    st.session_state["athlete_member_id"] = member_id
        except Exception as e:
            st.error(f"Error al obtener información del atleta: {e}")

    st.markdown("---")
    
    # ── BANNER GUÍA DE NAVEGACIÓN TÁCTIL / DESLIZAMIENTO ───────────────
    st.markdown(
        """
        <div style="background: rgba(0, 238, 255, 0.07); border: 1px dashed rgba(0, 238, 255, 0.4); border-radius: 12px; padding: 8px 14px; margin-bottom: 15px; display: flex; align-items: center; justify-content: center; gap: 8px; text-align: center;">
            <span style="font-size: 1.1rem;">📲</span>
            <span style="color: #00EEFF; font-size: 0.88rem; font-weight: 700; letter-spacing: 0.3px;">
                Toca o desliza en los 3 bloques para navegar entre tus marcadores
            </span>
            <span style="color: #00EEFF; font-size: 1rem; font-weight: bold;">⇄</span>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    # ── PESTAÑAS PRINCIPALES DEL PORTAL DE ATLETA (UNIFICADAS EN 3) ───
    tab_sleep, tab_bw, tab_lac = st.tabs([
        "💤 Sueño (ASSQ)",
        "🩸 Hemogramas",
        "🧪 Pruebas de Lactato"
    ])

    # ══════════════════════════════════════════════════════════════════
    #  PESTAÑA 1: SUEÑO Y RECUPERACIÓN (VISUALIZACIÓN + REGISTRO + GUÍA)
    # ══════════════════════════════════════════════════════════════════
    with tab_sleep:
        st.markdown("<h3 style='text-align: center; color: #00EEFF; font-weight: bold;'>📈 EVOLUCIÓN Y ESTADO DE SUEÑO (ASSQ)</h3>", unsafe_allow_html=True)
        if member_id:
            try:
                with SessionLocal() as session:
                    records = session.query(SleepRecord).filter(SleepRecord.member_id == member_id).order_by(SleepRecord.date).all()
                    if records:
                        df_history = pd.DataFrame([{
                            "Fecha": format_date_es(r.date),
                            "Score (SDS)": r.sds_score,
                            "Categoría": r.clinical_category
                        } for r in records])
                        
                        fig = px.line(
                            df_history, x="Fecha", y="Score (SDS)", markers=True,
                            title="EVOLUCIÓN DE CALIDAD DEL SUEÑO",
                            color_discrete_sequence=["#0066FF"]
                        )
                        fig.update_layout(title=dict(x=0.5, xanchor='center', font=dict(size=18, color="#121212", weight="bold")))
                        fig.update_traces(
                            line=dict(width=3),
                            marker=dict(symbol="circle", size=10, line=dict(width=2, color="white"))
                        )
                        fig.add_hrect(y0=-0.5, y1=4.5, fillcolor="rgba(0, 255, 0, 0.15)", line_width=0, annotation_text=" Óptimo (0-4)", annotation_font_color="#008000", annotation_position="inside left")
                        fig.add_hrect(y0=4.5, y1=7.5, fillcolor="rgba(0, 150, 255, 0.15)", line_width=0, annotation_text=" Leve (5-7)", annotation_font_color="#00509E", annotation_position="inside left")
                        fig.add_hrect(y0=7.5, y1=10.5, fillcolor="rgba(255, 165, 0, 0.15)", line_width=0, annotation_text=" Moderado (8-10)", annotation_font_color="#CC6600", annotation_position="inside left")
                        fig.add_hrect(y0=10.5, y1=17.5, fillcolor="rgba(255, 0, 0, 0.15)", line_width=0, annotation_text=" Severo (11-17)", annotation_font_color="#B30000", annotation_position="inside left")
                        
                        fig.update_layout(
                            paper_bgcolor="white", 
                            plot_bgcolor="white", 
                            font_color="#121212",
                            dragmode=False,
                            margin=dict(l=5, r=5, t=60, b=5),
                            yaxis=dict(range=[18, -1], title=dict(text="SCORE (SDS)", font=dict(color="black"), standoff=0), fixedrange=True, showgrid=True, gridcolor="#E0E0E0", tickfont=dict(color="black"), ticks=""),
                            xaxis=dict(title=dict(text="FECHA", font=dict(color="black"), standoff=0), fixedrange=True, showgrid=False, tickfont=dict(color="black"), type="category", ticks="")
                        )
                        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'staticPlot': True})
                        
                        last_record = session.query(SleepRecord).filter(SleepRecord.member_id == member_id).order_by(SleepRecord.date.desc(), SleepRecord.id.desc()).first()
                        if last_record:
                            st.markdown(
                                f"""
                                <div style="background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(0, 238, 255, 0.3); border-radius: 12px; padding: 15px; margin-top: 15px; margin-bottom: 15px;">
                                    <h3 style="margin-top: 0; color: #00EEFF; font-size: 1.1rem; font-weight: bold; display: flex; align-items: center; gap: 8px;">
                                        💡 TU RECOMENDACIÓN DE RECUPERACIÓN ACTUAL (Score: {last_record.sds_score}/17)
                                    </h3>
                                    <p style="margin-bottom: 0; line-height: 1.5; color: #FFFFFF; font-size: 0.95rem;">
                                        {obtener_recomendaciones(last_record.sds_score)}
                                    </p>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )
                    else:
                        st.info("Aún no tienes registros de sueño guardados. Diligencia el formulario abajo para tu primer reporte.")
            except Exception as e:
                st.error(f"Error cargando historial de sueño: {e}")
        else:
            st.info("No se encontró tu perfil de atleta en la base de datos.")

        # Sección de Registro de Sueño
        st.markdown("---")
        st.markdown("<h4 style='color: #00EEFF; font-weight: bold;'>📝 Registrar Nuevo Reporte de Sueño (ASSQ)</h4>", unsafe_allow_html=True)
        with st.form("form_assq", clear_on_submit=True):
            st.subheader("💤 Calidad del Descanso")
        
            horas = st.radio("**1. En los ultimos 7 dias, ¿cuántas horas de sueño real tuviste por la noche? (Es diferente del número de horas que pasaste en la cama)**", 
                             options=list(puntajes_horas.keys()), index=None)
            
            calidad = st.radio("**2. ¿Qué tan satisfecho/insatisfecho estás con la calidad de tu sueño?**", 
                               options=list(puntajes_calidad.keys()), index=None)
            
            latencia = st.radio("**3. En los ultimos 7 dias, ¿cuánto tiempo te toma habitualmente quedarte dormido cada noche?**", 
                                options=list(puntajes_latencia.keys()), index=None)
            
            despertares = st.radio("**4. ¿Con qué frecuencia tienes problemas para mantenerte dormido?**", 
                                   options=list(puntajes_despertares.keys()), index=None)
                                   
            medicamentos = st.radio("**5. En los ultimos 7 dias, ¿con qué frecuencia has tomado medicamentos (recetados o de venta libre) para ayudarte a dormir?**", 
                                   options=list(puntajes_medicamentos.keys()), index=None)
            
            st.markdown("---")
            submitted = st.form_submit_button("ENVIAR REPORTE", use_container_width=True)

        if submitted:
            if horas is None or calidad is None or latencia is None or despertares is None or medicamentos is None:
                st.error("⚠️ Por favor responde todas las preguntas antes de enviar el reporte.")
            else:
                with st.spinner('Guardando tu reporte...'):
                    sds_score = (
                        puntajes_horas[horas] + 
                        puntajes_calidad[calidad] + 
                        puntajes_latencia[latencia] + 
                        puntajes_despertares[despertares] +
                        puntajes_medicamentos[medicamentos]
                    )
                    
                    if sds_score <= 4: categoria = "Sin problema clínico"
                    elif sds_score <= 7: categoria = "Problema leve"
                    elif sds_score <= 10: categoria = "Problema moderado"
                    else: categoria = "Problema grave"

                    if member_id:
                        try:
                            with SessionLocal() as session:
                                nuevo_registro = SleepRecord(
                                    member_id=member_id,
                                    sds_score=sds_score,
                                    clinical_category=categoria,
                                    raw_hours=horas,
                                    raw_quality=calidad,
                                    raw_latency=latencia,
                                    raw_awakenings=despertares,
                                    raw_medications=medicamentos
                                )
                                session.add(nuevo_registro)
                                session.commit()
                                
                                st.session_state["last_score"] = f"✅ Reporte enviado a tu coach. 📊 **Tu último Score SDS:** {sds_score}/17 ({categoria})"
                                st.session_state["show_toast"] = True
                                st.rerun()
                        except Exception as e:
                            st.error(f"Error técnico al guardar: {e}")
                    else:
                        st.error("⚠️ No se encontró tu nombre en la base de datos.")

        # Guía e Interpretación al final
        st.markdown("---")
        st.markdown("<h4 style='text-align: center; color: #00EEFF; font-weight: bold;'>📊 GUÍA E INTERPRETACIÓN DEL SCORE DE SUEÑO (ESS / ASSQ)</h4>", unsafe_allow_html=True)
        st.image("assq_banner.jpg", use_container_width=True)

    # ══════════════════════════════════════════════════════════════════
    #  PESTAÑA 2: HEMOGRAMAS (CARGAR + HISTORIAL + ALERTAS + GRÁFICAS)
    # ══════════════════════════════════════════════════════════════════
    with tab_bw:
        st.markdown("<h3 style='text-align: center; color: #00EEFF; font-weight: bold;'>🩸 HISTORIAL Y CARGA DE MARCADORES CLÍNICOS</h3>", unsafe_allow_html=True)
        
        # Desplegable para subir nuevo examen
        with st.expander("📥 Cargar Nuevo Examen de Sangre (PDF o Imagen)", expanded=False):
            st.write("Sube tu examen de laboratorio (en archivo PDF o foto JPG/PNG) para extraer y registrar automáticamente tus marcadores en tu historial.")
            
            ath_file = st.file_uploader("Selecciona el archivo PDF o Imagen del examen:", type=["pdf", "png", "jpg", "jpeg"], key="ath_bw_file_main")
            ath_pwd = st.text_input("Clave del PDF (si está protegido con contraseña):", type="password", key="ath_bw_pwd_main")
            
            if ath_file is not None:
                if st.button("⚡ Procesar Examen", key="btn_proc_ath_main", use_container_width=True):
                    try:
                        with st.spinner("Leyendo y analizando marcadores clínicamente..."):
                            import importlib
                            import hemograma_parser
                            importlib.reload(hemograma_parser)
                            parsed = hemograma_parser.process_file(ath_file.getvalue(), ath_file.name, password=ath_pwd if ath_pwd else None)
                            
                            if parsed.get("raw_text", "").startswith("[ERROR]"):
                                st.error(f"Error al procesar archivo: {parsed.get('raw_text')}")
                            else:
                                rec_date = datetime.now().date()
                                if parsed.get("date"):
                                    try: rec_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
                                    except Exception: pass
                                    
                                with SessionLocal() as session:
                                    new_rec = BloodworkRecord(
                                        member_id=member_id,
                                        date=rec_date,
                                        hemoglobin=parsed.get("hemoglobin"),
                                        vcm=parsed.get("vcm"),
                                        chcm=parsed.get("chcm"),
                                        rbc=parsed.get("rbc"),
                                        hematocrit=parsed.get("hematocrit"),
                                        ferritin=parsed.get("ferritin"),
                                        ck=parsed.get("ck"),
                                        vitamin_b12=parsed.get("vitamin_b12"),
                                        folic_acid=parsed.get("folic_acid"),
                                        pdf_filename=ath_file.name
                                    )
                                    session.add(new_rec)
                                    session.commit()
                                st.success(f"🎉 Examen guardado exitosamente ({parsed.get('markers_found', 0)} marcadores extraídos).")
                                st.rerun()
                    except Exception as err:
                        st.error(f"Error procesando examen: {err}")

        # Historial y Gráficas
        if member_id:
            try:
                with SessionLocal() as session:
                    records = session.query(BloodworkRecord).filter(
                        BloodworkRecord.member_id == member_id
                    ).order_by(BloodworkRecord.date.desc()).all()
                    
                    if records:
                        marker_keys = ["hemoglobin", "vcm", "chcm", "rbc", "hematocrit", "ferritin", "ck", "vitamin_b12", "folic_acid"]
                        
                        table_html = """
                        <div style="overflow-x: auto;">
                        <table style="width:100%; border-collapse:collapse; font-size:0.85rem; background:#121212; border-radius:10px; overflow:hidden; border:1px solid #222;">
                        <thead>
                        <tr style="background:#181828; border-bottom:1px solid rgba(255,255,255,0.08);">
                            <th rowspan="2" style="padding:10px; color:#00EEFF; text-align:left; font-size:0.85rem; vertical-align:middle;">Fecha</th>
                        """
                        for key in marker_keys:
                            r = BLOODWORK_RANGES_FULL[key]
                            c_color = r["color"]
                            table_html += f'<th colspan="2" style="padding:10px 8px; color:{c_color}; text-align:center; border-left:1px solid rgba(255,255,255,0.08); font-size:0.82rem; background:{r["bg"]}; vertical-align:middle;">{r["emoji"]} {r["name"]}<br><span style="font-size:0.72rem; color:{c_color}CC; font-weight:normal;">({r["unit"]})</span></th>'
                        
                        table_html += '<th rowspan="2" style="padding:10px; color:#888; text-align:center; border-left:1px solid rgba(255,255,255,0.08); vertical-align:middle;">Notas</th>'
                        table_html += '<th rowspan="2" style="padding:10px; color:#888; text-align:center; border-left:1px solid rgba(255,255,255,0.08); vertical-align:middle;">PDF</th>'
                        table_html += '</tr><tr style="background:#141422; border-bottom:2px solid #00EEFF;">'
                        
                        for key in marker_keys:
                            r = BLOODWORK_RANGES_FULL[key]
                            c_color = r["color"]
                            table_html += f'<th style="padding:5px 8px; color:{c_color}EE; text-align:center; border-left:1px solid rgba(255,255,255,0.08); font-size:0.75rem; background:{r["bg"]}; font-weight:bold;">Valor</th>'
                            table_html += f'<th style="padding:5px 6px; color:{c_color}AA; text-align:center; font-size:0.75rem; background:{r["bg"]}; font-weight:600;">Δ</th>'
                        
                        table_html += "</tr></thead><tbody>"
                        
                        for idx, rec in enumerate(records):
                            prev_rec = records[idx + 1] if idx + 1 < len(records) else None
                            row_bg = "#161625" if idx % 2 == 0 else "#121212"
                            table_html += f'<tr style="background:{row_bg}; border-bottom:1px solid #222;">'
                            table_html += f'<td style="padding:8px 10px; color:#FFFFFF; font-weight:bold; white-space:nowrap;">{rec.date.strftime("%d/%m/%Y") if rec.date else "—"}</td>'
                            
                            for key in marker_keys:
                                r = BLOODWORK_RANGES_FULL[key]
                                val = getattr(rec, key)
                                prev_val = getattr(prev_rec, key) if prev_rec else None
                                label, val_status_color = classify_value_ath(key, val)
                                val_str = f"{val:.1f}" if val is not None else "—"
                                
                                table_html += f'<td style="padding:8px 6px; text-align:center; border-left:1px solid rgba(255,255,255,0.05); background:{r["bg"]}; white-space:nowrap;">'
                                table_html += f'<span style="font-weight:bold; color:{val_status_color}; font-size:0.9rem;">{val_str}</span> {badge_html_ath(label, val_status_color)}'
                                table_html += '</td>'
                                
                                table_html += f'<td style="padding:8px 6px; text-align:center; background:{r["bg"]}; white-space:nowrap;">'
                                table_html += f'{delta_html_ath(val, prev_val, default_color=r["color"])}'
                                table_html += '</td>'
                            
                            notes_str = (rec.notes or "")[:30]
                            pdf_str = "📎" if rec.pdf_filename else ""
                            table_html += f'<td style="padding:8px 6px; text-align:center; color:#888; font-size:0.8rem; border-left:1px solid rgba(255,255,255,0.05);">{notes_str}</td>'
                            table_html += f'<td style="padding:8px 6px; text-align:center; border-left:1px solid rgba(255,255,255,0.05);">{pdf_str}</td>'
                            table_html += "</tr>"
                        
                        table_html += "</tbody></table></div>"
                        st.markdown(table_html, unsafe_allow_html=True)
                        
                        # Alertas del último examen
                        latest = records[0]
                        alerts = []
                        if latest.hemoglobin is not None and latest.hemoglobin < 13.5:
                            alerts.append(("🚨", "Hemoglobina BAJA", f"Hb = {latest.hemoglobin:.1f} g/dL → Posible anemia. Revisa ferritina y consulta a tu coach.", "#FF4B4B"))
                        elif latest.hemoglobin is not None and latest.hemoglobin > 18.0:
                            alerts.append(("⚠️", "Hemoglobina ALTA", f"Hb = {latest.hemoglobin:.1f} g/dL → Viscosidad sanguínea elevada. Revisar hidratación.", "#FFD700"))

                        if latest.ferritin is not None and latest.ferritin < 30:
                            alerts.append(("⚡", "Ferritina BAJA", f"Ferritina = {latest.ferritin:.0f} ng/mL → Deficiencia de hierro. Reducción potencial de rendimiento.", "#FF4B4B"))
                        
                        if latest.ck is not None and latest.ck > 500:
                            alerts.append(("⚠️", "CK ELEVADA (Fatiga Muscular)", f"CK = {latest.ck:.0f} U/L → Daño/fatiga muscular alta. Importante priorizar descanso.", "#FFD700"))
                            
                        if alerts:
                            st.markdown("#### 💡 Observaciones de tu Último Examen")
                            for icon, title, msg, color in alerts:
                                st.markdown(
                                    f"""
                                    <div style="background:{color}15; border-left:4px solid {color}; padding:10px 14px; border-radius:6px; margin-bottom:8px;">
                                        <strong style="color:{color};">{icon} {title}:</strong> <span style="color:#EEE;">{msg}</span>
                                    </div>
                                    """,
                                    unsafe_allow_html=True
                                )

                        # ── Gráficas Plotly de evolución ─────────────────────
                        st.markdown("---")
                        st.markdown("<h4 style='text-align: center; color: #00EEFF; font-weight: bold;'>📈 EVOLUCIÓN TEMPORAL DE TUS MARCADORES CLÍNICOS</h4>", unsafe_allow_html=True)
                        
                        records_chrono = list(reversed(records))
                        dates_list = [r.date.strftime("%d/%m/%Y") if r.date else "" for r in records_chrono]
                        
                        marker_triplets = [
                            ("hemoglobin", "vcm", "chcm"),
                            ("rbc", "hematocrit", "ferritin"),
                            ("ck", "vitamin_b12", "folic_acid"),
                        ]
                        
                        for key1, key2, key3 in marker_triplets:
                            col1, col2, col3 = st.columns(3)
                            
                            for col, key in [(col1, key1), (col2, key2), (col3, key3)]:
                                with col:
                                    r = BLOODWORK_RANGES_FULL[key]
                                    values = [getattr(rec, key) for rec in records_chrono]
                                    values_clean = [v for v in values if v is not None]
                                    
                                    if not values_clean:
                                        st.info(f"Sin datos de {r['name']}")
                                        continue
                                    
                                    fig = go.Figure()
                                    
                                    # Bandas de referencia
                                    y_min = min(min(values_clean) * 0.85, r["low"] * 0.9)
                                    y_max = max(max(values_clean) * 1.1, r["high"] * 1.05)
                                    
                                    fig.add_hrect(y0=y_min, y1=r["low"], fillcolor="rgba(255, 75, 75, 0.12)", line_width=0, 
                                                  annotation_text="BAJO", annotation_position="inside left", annotation_font=dict(color="#FF4B4B", size=10))
                                    fig.add_hrect(y0=r["opt_lo"], y1=r["opt_hi"], fillcolor="rgba(0, 255, 0, 0.08)", line_width=0,
                                                  annotation_text="ÓPTIMO", annotation_position="inside left", annotation_font=dict(color="#00FF00", size=10))
                                    fig.add_hrect(y0=r["high"], y1=y_max, fillcolor="rgba(255, 165, 0, 0.12)", line_width=0,
                                                  annotation_text="ALTO", annotation_position="inside left", annotation_font=dict(color="#FFD700", size=10))
                                    
                                    # Línea de datos con el color único de cada marcador
                                    fig.add_trace(go.Scatter(
                                        x=dates_list,
                                        y=values,
                                        mode='lines+markers+text',
                                        name=r["name"],
                                        line=dict(color=r["color"], width=3),
                                        marker=dict(size=10, symbol='circle', line=dict(width=2, color="#121212"), color=r["color"]),
                                        text=[f"{v:.1f}" if v is not None else "" for v in values],
                                        textposition="top center",
                                        textfont=dict(color=r["color"], size=10, weight="bold"),
                                        connectgaps=True,
                                    ))
                                    
                                    fig.update_layout(
                                        title=dict(
                                            text=f"{r['emoji']} {r['name']} ({r['unit']})",
                                            x=0.5, xanchor='center',
                                            font=dict(color=r["color"], size=13, weight="bold")
                                        ),
                                        paper_bgcolor="#121212",
                                        plot_bgcolor="#121212",
                                        font_color="#FFFFFF",
                                        dragmode=False,
                                        xaxis=dict(gridcolor="#333333", showgrid=False, tickfont=dict(color="#FFFFFF", size=9), type="category", fixedrange=True),
                                        yaxis=dict(gridcolor="#222222", showgrid=True, tickfont=dict(color="#FFFFFF", size=9), range=[y_min, y_max], fixedrange=True),
                                        margin=dict(l=10, r=10, t=50, b=10),
                                        showlegend=False,
                                        height=280,
                                    )
                                    
                                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'staticPlot': True})
                    else:
                        st.info("Aún no tienes exámenes de sangre registrados. Puedes cargar uno en la sección de arriba.")
            except Exception as e:
                st.error(f"Error cargando hemogramas: {e}")

    # ══════════════════════════════════════════════════════════════════
    #  PESTAÑA 3: VER MIS MEDICIONES DE LACTATO
    # ══════════════════════════════════════════════════════════════════
    with tab_lac:
        st.markdown("<h3 style='text-align: center; color: #00EEFF; font-weight: bold;'>🧪 HISTORIAL Y CURVAS DE LACTATO</h3>", unsafe_allow_html=True)
        if member_id:
            try:
                with SessionLocal() as session:
                    tests = session.query(LactateTest).filter(
                        LactateTest.member_id == member_id
                    ).order_by(LactateTest.date.desc()).all()
                    
                    if tests:
                        st.markdown("#### 📋 Pruebas Registradas")
                        history_data = []
                        for t in tests:
                            history_data.append({
                                "id": t.id,
                                "Fecha": t.date.strftime("%Y-%m-%d"),
                                "Deporte": t.sport,
                                "Peso (kg)": t.weight or "",
                                "FTP/CP": t.ftp or "",
                                "LT1 Potencia": f"{t.lt1_power:.0f} W" if t.lt1_power else "",
                                "LT1 Pulso": f"{t.lt1_hr} lpm" if t.lt1_hr else "",
                                "LT2 Potencia": f"{t.lt2_power:.0f} W" if t.lt2_power else "",
                                "LT2 Pulso": f"{t.lt2_hr} lpm" if t.lt2_hr else "",
                                "CTL/ATL/TSB": f"{t.ctl or 0:.0f}/{t.atl or 0:.0f}/{t.tsb or 0:.0f}"
                            })
                        df_history = pd.DataFrame(history_data)
                        st.dataframe(df_history.drop(columns=["id"]), use_container_width=True, hide_index=True)
                        
                        st.markdown("---")
                        st.markdown("#### 📈 Superponer y Comparar Curvas Fisiológicas")
                        st.caption("Selecciona una o varias pruebas para graficar y ver tu evolución de lactato y frecuencia cardíaca:")
                        
                        test_options = {f"{t.date.strftime('%Y-%m-%d')} - {t.sport}": t.id for t in tests}
                        selected_tests = st.multiselect("Pruebas a visualizar:", list(test_options.keys()), default=[list(test_options.keys())[0]])
                        
                        if selected_tests:
                            selected_ids = [test_options[name] for name in selected_tests]
                            
                            fig = make_subplots(specs=[[{"secondary_y": True}]])
                            lac_colors = ["#00EEFF", "#3B82F6", "#1D4ED8", "#60A5FA", "#2563EB"]
                            hr_colors = ["#FF3333", "#F87171", "#DC2626", "#FCA5A5", "#EF4444"]
                            
                            for idx, t_id in enumerate(selected_ids):
                                t = session.query(LactateTest).filter(LactateTest.id == t_id).first()
                                steps = session.query(LactateTestStep).filter(LactateTestStep.test_id == t_id).order_by(LactateTestStep.id).all()
                                
                                df_steps = pd.DataFrame([{
                                    "step_number": s.step_number,
                                    "pot_rel": s.pot_rel or 0.0,
                                    "lactate": s.lactate,
                                    "watts": s.watts or 0.0,
                                    "heart_rate": s.heart_rate
                                } for s in steps])
                                
                                df_plot = df_steps.dropna(subset=["lactate"])
                                df_hr_plot = df_steps.dropna(subset=["heart_rate"])
                                
                                lac_color = lac_colors[idx % len(lac_colors)]
                                hr_color = hr_colors[idx % len(hr_colors)]
                                date_str = t.date.strftime("%Y-%m-%d")
                                sport_str = t.sport or ""
                                
                                lac_name = "Lactato" if len(selected_ids) == 1 else f"Lactato ({date_str})"
                                hr_name = "Pulso" if len(selected_ids) == 1 else f"Pulso ({date_str})"
                                
                                fig.add_trace(
                                    go.Scatter(
                                        x=df_plot["watts"], 
                                        y=df_plot["lactate"], 
                                        mode='lines+markers',
                                        name=lac_name,
                                        line=dict(color=lac_color, width=3),
                                        marker=dict(size=10, symbol='circle')
                                    ),
                                    secondary_y=False
                                )
                                
                                fig.add_trace(
                                    go.Scatter(
                                        x=df_hr_plot["watts"], 
                                        y=df_hr_plot["heart_rate"], 
                                        mode='lines+markers',
                                        name=hr_name,
                                        line=dict(color=hr_color, width=2, dash='dash'),
                                        marker=dict(size=12, symbol='triangle-up')
                                    ),
                                    secondary_y=True
                                )
                                
                                if len(selected_ids) == 1:
                                    if t.lt1_power:
                                        fig.add_vline(
                                            x=t.lt1_power, line_width=1.5, line_dash="dot", line_color="#FFD700", 
                                            annotation_text=f"LT1: {t.lt1_power:.0f} W", annotation_position="top left",
                                            annotation_font=dict(color="#FFD700", size=11, weight="bold")
                                        )
                                    if t.lt2_power:
                                        fig.add_vline(
                                            x=t.lt2_power, line_width=1.5, line_dash="dot", line_color="#FF3333", 
                                            annotation_text=f"LT2: {t.lt2_power:.0f} W", annotation_position="top left",
                                            annotation_font=dict(color="#FF3333", size=11, weight="bold")
                                        )
                                        
                            title_text = f"TEST DE LACTATO: {sport_str.upper()} - {date_str}" if len(selected_ids) == 1 else "COMPARACIÓN DE PRUEBAS DE LACTATO"
                            fig.update_layout(
                                title=dict(text=title_text, x=0.5, font=dict(size=16, color="#121212", weight="bold")),
                                paper_bgcolor="white",
                                plot_bgcolor="white",
                                font_color="#121212",
                                dragmode=False,
                                margin=dict(l=10, r=10, t=50, b=10),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                            )
                            fig.update_xaxes(title_text="Potencia / Carga (Watts)", gridcolor="#E0E0E0", title_font=dict(color="black"), fixedrange=True)
                            fig.update_yaxes(title_text="Lactato (mmol/L)", secondary_y=False, gridcolor="#E0E0E0", title_font=dict(color="#0066CC"), fixedrange=True)
                            fig.update_yaxes(title_text="Frecuencia Cardíaca (lpm)", secondary_y=True, showgrid=False, title_font=dict(color="#CC0000"), fixedrange=True)
                            
                            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'staticPlot': True})
                    else:
                        st.info("Aún no tienes pruebas de lactato registradas en tu perfil.")
            except Exception as e:
                st.error(f"Error cargando pruebas de lactato: {e}")

    # Animación Toast de confirmación
    if st.session_state.get("show_toast"):
        moon_animation = """
        <style>
        @keyframes floatUp {
            0% { transform: translateY(0) rotate(0deg); opacity: 1; }
            80% { opacity: 1; }
            100% { transform: translateY(-150vh) rotate(360deg); opacity: 0; display: none; }
        }
        .moon {
            position: fixed;
            bottom: -100px;
            font-size: 6rem;
            filter: drop-shadow(0 0 20px #FFD700);
            animation: floatUp 3s linear forwards;
            z-index: 999999;
        }
        .m1 { left: 10%; animation-duration: 2.5s; animation-delay: 0s; }
        .m2 { left: 30%; animation-duration: 3s; animation-delay: 0.2s; }
        .m3 { left: 50%; animation-duration: 2.2s; animation-delay: 0.1s; }
        .m4 { left: 70%; animation-duration: 3.5s; animation-delay: 0.4s; }
        .m5 { left: 90%; animation-duration: 2.8s; animation-delay: 0.1s; }
        .m6 { left: 20%; animation-duration: 3.2s; animation-delay: 0.3s; }
        .m7 { left: 80%; animation-duration: 2.6s; animation-delay: 0.5s; }
        </style>
        <div class="moon m1">🌙</div>
        <div class="moon m2">🌙</div>
        <div class="moon m3">🌙</div>
        <div class="moon m4">🌙</div>
        <div class="moon m5">🌙</div>
        <div class="moon m6">🌙</div>
        <div class="moon m7">🌙</div>
        """
        st.markdown(moon_animation, unsafe_allow_html=True)
        st.session_state["show_toast"] = False
