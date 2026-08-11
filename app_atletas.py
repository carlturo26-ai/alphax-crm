import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import hashlib
import unicodedata
from streamlit_cookies_controller import CookieController
from background_base64 import BACKGROUND_IMAGE_BASE64

try:
    from database import SessionLocal, Member, SleepRecord, AthleteUser, LactateTest, BloodworkRecord, engine
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
        page_title="ASSQ | Alphax Training",
        page_icon="💤",
        layout="centered",
        initial_sidebar_state="collapsed"
    )
except Exception:
    pass

# Estilos personalizados para la app pública (tema oscuro AlphaX)
css_styles = f"""
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700;800;900&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stApp {{ font-family: 'Nunito Sans', sans-serif !important; color: #FFFFFF !important; }} body, .stApp {{ background-image: url("data:image/png;base64,{BACKGROUND_IMAGE_BASE64}"); }}
div[data-baseweb="input"] > div, div[data-baseweb="base-input"] {{ background-color: #121212 !important; }}
div[data-baseweb="select"] > div {{ background-color: #121212 !important; border-color: #00EEFF !important; }}
div[data-baseweb="menu"], div[role="listbox"], div[role="option"] {{ background-color: #121212 !important; color: #FFFFFF !important; }}
div[role="option"]:hover, div[role="option"][aria-selected="true"] {{ background-color: #00EEFF !important; color: #000000 !important; }}
.stButton > button {{ border-radius: 8px; font-weight: 700; border: 1px solid #FFFFFF; color: #FFFFFF !important; background-color: transparent !important; transition: all 0.3s ease; }}
.stButton > button:hover {{ background-color: #FFFFFF !important; color: #000000 !important; box-shadow: 0 0 15px rgba(255, 255, 255, 0.4); }}
.stPlotlyChart {{ background-color: white !important; border-radius: 20px; border: 2px solid #00EEFF; padding: 0px; overflow: hidden; box-shadow: 0 0 20px rgba(0, 238, 255, 0.4); }}
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

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def clean_and_normalize(name):
    if not name:
        return ""
    # Normalize unicode to separate characters from their accent marks
    n = "".join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    # Lowercase and replace ñ/Ñ with n/N
    n = n.lower().replace('ñ', 'n')
    # Keep only letters, numbers and spaces
    n = "".join(c if c.isalnum() or c.isspace() else "" for c in n)
    # Collapse multiple spaces and trim
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
        
        # Calculate matching score
        score = 0
        
        # Exact match bonus
        if member_clean == input_clean:
            score += 20
            
        # Word overlap
        for iw in input_words:
            # Exact word match
            if iw in member_words:
                score += 5
            # Substring match (e.g. "alej" matches "alejandro")
            elif any(iw in mw for mw in member_words):
                score += 2
                
        if score > 0:
            # Add small similarity ratio to resolve ties
            from difflib import SequenceMatcher
            similarity = SequenceMatcher(None, input_clean, member_clean).ratio()
            score += similarity * 2
            
            matches.append((m, score))
            
    # Sort matches by score descending
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

def send_coach_email_alert(athlete_name, sds_score, category):
    import smtplib
    import os
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    # Try loading credentials from environment or Streamlit secrets
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    coach_email = os.environ.get("COACH_EMAIL")

    # Fallback to streamlit secrets
    if not smtp_server:
        try:
            smtp_server = st.secrets.get("SMTP_SERVER")
            smtp_port = st.secrets.get("SMTP_PORT")
            smtp_user = st.secrets.get("SMTP_USER")
            smtp_password = st.secrets.get("SMTP_PASSWORD")
            coach_email = st.secrets.get("COACH_EMAIL")
        except Exception:
            pass

    if not smtp_server or not smtp_user or not smtp_password or not coach_email:
        # Gracefully print to stdout without breaking user execution
        print("⚠️ Alerta de Email no enviada: Configuración SMTP o Email del Coach ausente.")
        return False

    try:
        smtp_port = int(smtp_port) if smtp_port else 587
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = coach_email
        msg['Subject'] = f"🚨 ALERTA SUEÑO ASSQ: {athlete_name.upper()} - Puntaje Alto ({sds_score}/17)"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #0d0d0d; color: #ffffff; padding: 20px;">
            <div style="border: 2px solid #ff4b4b; border-radius: 10px; padding: 20px; background-color: #161616; color: #ffffff;">
                <h2 style="color: #ff4b4b; text-align: center; margin-top: 0;">⚠️ Alerta de Recuperación Crítica ⚠️</h2>
                <hr style="border-color: #ff4b4b;">
                <p>Hola Coach,</p>
                <p>El deportista <strong>{athlete_name}</strong> acaba de registrar su cuestionario de sueño ASSQ y se ha detectado un puntaje de alerta clínica:</p>
                
                <table style="width: 100%; border-collapse: collapse; margin: 20px 0; background-color: #222; color: #fff;">
                    <tr style="border-bottom: 1px solid #444;">
                        <td style="padding: 10px; color: #aaa;">Deportista:</td>
                        <td style="padding: 10px; font-weight: bold; color: #00eeff;">{athlete_name}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #444;">
                        <td style="padding: 10px; color: #aaa;">Score ASSQ (SDS):</td>
                        <td style="padding: 10px; font-weight: bold; color: #ff4b4b; font-size: 1.2rem;">{sds_score} / 17</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #444;">
                        <td style="padding: 10px; color: #aaa;">Categoría Clínica:</td>
                        <td style="padding: 10px; font-weight: bold; color: #ff4b4b; text-transform: uppercase;">{category}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; color: #aaa;">Fecha de Registro:</td>
                        <td style="padding: 10px; color: #aaa;">{datetime.now().strftime('%Y-%m-%d %H:%M')}</td>
                    </tr>
                </table>
                
                <div style="background-color: #2b1616; border-left: 4px solid #ff4b4b; padding: 15px; border-radius: 4px; margin-top: 20px; color: #ffbaba;">
                    <strong>Recomendación:</strong><br>
                    Un puntaje de {sds_score} indica un problema {category.lower()}. Te sugerimos conversar con el atleta para evaluar su fatiga, cargas de entrenamiento y hábitos de sueño.
                </div>
                
                <br>
                <p style="text-align: center; color: #888; font-size: 0.8rem; margin-bottom: 0;">Este es un mensaje automático del sistema de monitoreo AlphaX Training.</p>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, coach_email, msg.as_string())
        print(f"📩 Alerta de email enviada exitosamente al coach para {athlete_name}")
        return True
    except Exception as e:
        print(f"❌ Error enviando email de alerta: {e}")
        return False

meses_es = {1: "ENE", 2: "FEB", 3: "MAR", 4: "ABR", 5: "MAY", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DIC"}

def format_date_es(d):
    if isinstance(d, str):
        try: d = datetime.strptime(d.split()[0], "%Y-%m-%d")
        except: pass
    if hasattr(d, "month"): return f"{d.day} {meses_es[d.month]}"
    return str(d)

# --- INTERFAZ DE USUARIO ---
st.image("assq_banner.jpg", use_container_width=True)
st.markdown("<h1 style='text-align: center; white-space: nowrap; font-size: clamp(1.2rem, 5vw, 2.5rem);'>MONITOREO DE RECUPERACIÓN</h1>", unsafe_allow_html=True)
st.markdown("**Athlete Sleep Screening Questionnaire (ASSQ)**")
st.info("ALPHAX TRAINING TEAM", icon="📋")

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

# --- LOGOUT E INITIAL STATE ---
if "logged_out" not in st.session_state:
    st.session_state["logged_out"] = False

# Rerun inicial único para garantizar que las cookies de Streamlit se lean del navegador
if "cookie_checked" not in st.session_state:
    st.session_state["cookie_checked"] = False

if not st.session_state["cookie_checked"]:
    st.session_state["cookie_checked"] = True
    st.rerun()

# Iniciar login automático multicapa si el usuario no ha cerrado sesión manualmente
if not st.session_state["logged_out"] and st.session_state["athlete_user"] is None:
    # 1. Login por parámetros de consulta URL (Query Parameters)
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
                        # Buscar si el miembro existe y ver si tiene cuenta
                        m_obj = session.query(Member).filter(Member.name == query_athlete).first()
                        if m_obj:
                            user = session.query(AthleteUser).filter(AthleteUser.athlete_name == m_obj.name).first()
                
                if user:
                    st.session_state["athlete_user"] = user.athlete_name
                    m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                    if m_obj:
                        st.session_state["athlete_member_id"] = m_obj.id
                    # Actualizar IP al iniciar por enlace
                    client_ip = get_client_ip()
                    if client_ip:
                        user.last_ip = client_ip
                        session.commit()
                    # Guardar cookie
                    cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                    st.query_params.clear()
                    st.rerun()
        except Exception:
            pass

    # 2. Login por Cookies
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

    # 3. Login por IP del Dispositivo
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
                        # Guardar cookie por si acaso
                        cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                        st.rerun()
            except Exception:
                pass

submitted = False

if not st.session_state["athlete_user"]:
    # 1. Identificación y Login
    st.subheader("👤 Identificación")
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
                        # Fetch and cache the member_id immediately
                        m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                        if m_obj:
                            st.session_state["athlete_member_id"] = m_obj.id
                        # Registrar la dirección IP del dispositivo
                        client_ip = get_client_ip()
                        if client_ip:
                            user.last_ip = client_ip
                            session.commit()
                        # Guardar cookie que expira en 30 días
                        cookie_controller.set("athlete_user_cookie", user.athlete_name, max_age=30*86400)
                        st.rerun()
                    else:
                        st.error("Correo o contraseña incorrectos.")
            except Exception as e:
                st.error(f"Error al iniciar sesión: {e}")
        st.caption("¿Olvidaste tu contraseña? Comunícate con tu entrenador para que restablezca tu acceso.")
            
    with tab2:
        st.markdown("¿Es tu primera vez? Crea tu cuenta para vincular tu progreso.")
        reg_name_query = st.text_input("Busca tu Nombre o Apellido (oficial de AlphaX):", key="reg_name_query")
        
        # Búsqueda dinámica y selección de Member
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
                        st.error("❌ No encontramos coincidencias en la base de datos. Intenta con otra variación o apellido.")
            except Exception as e:
                st.error(f"Error buscando deportistas: {e}")
        else:
            st.info("Escribe tu nombre o apellido arriba para buscar tu perfil en la base de datos de AlphaX.")
            
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
                            # Guardar usuario con last_ip
                            new_user = AthleteUser(
                                email=reg_email.lower().strip(),
                                password_hash=hash_password(reg_pass),
                                athlete_name=m_obj.name,
                                last_ip=get_client_ip()
                            )
                            session.add(new_user)
                            session.commit()
                            st.success(f"✅ Cuenta creada exitosamente vinculada a **{m_obj.name}**. Ahora puedes ir a la pestaña 'Iniciar Sesión'.")
                except Exception as e:
                    st.error(f"Error al registrar la cuenta: {e}")

else:
    atleta = st.session_state["athlete_user"]
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**👤 Atleta:** {atleta}")
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
            <div style="background: rgba(0, 238, 255, 0.05); border: 1px dashed #00EEFF; border-radius: 8px; padding: 12px; margin-top: 10px; margin-bottom: 15px;">
                <span style="font-size: 0.9rem; color: #00EEFF; font-weight: bold;">🔗 Enlace de Acceso Rápido:</span><br>
                <span style="font-size: 0.8rem; color: #bbbbbb;">Guarda este enlace en tus favoritos o WhatsApp para ingresar directamente sin contraseña:</span><br>
                <code style="word-break: break-all; color: #00EEFF; font-size: 0.85rem;">{direct_link}</code>
            </div>
            """,
            unsafe_allow_html=True
        )
    except Exception:
        pass
            
    if st.session_state.get("last_score"):
        st.success(st.session_state["last_score"])
        st.session_state["last_score"] = None  # Limpiar inmediatamente para que no se quede fijo
            
    # --- HISTORIAL PERSONAL ---
    st.markdown("---")
    st.markdown("<h2 style='text-align: center; white-space: nowrap; font-size: clamp(1.1rem, 4vw, 2rem);'>📈 HISTORIAL DE RECUPERACIÓN</h2>", unsafe_allow_html=True)
    
    # Get cached member_id or query it once
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
                    
                    # Crear gráfico
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
                        margin=dict(l=5, r=5, t=60, b=5),
                        yaxis=dict(range=[18, -1], title=dict(text="SCORE (SDS)", font=dict(color="black"), standoff=0), fixedrange=True, showgrid=True, gridcolor="#E0E0E0", tickfont=dict(color="black"), ticks=""),
                        xaxis=dict(title=dict(text="FECHA", font=dict(color="black"), standoff=0), fixedrange=True, showgrid=False, tickfont=dict(color="black"), type="category", ticks="")
                    )
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                    
                    # Cargar última recomendación directamente de la base de datos para persistencia total
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
                    st.info("Aún no tienes registros de sueño. ¡Llena tu primer reporte abajo!")
        except Exception as e:
            st.error(f"Error cargando historial: {e}")
    else:
        st.info("No se encontró tu perfil de atleta en la base de datos.")

    # ── MI ESTADO INTEGRAL: Hemograma + Lactato ──────────────────────
    if member_id:
        st.markdown("---")
        st.markdown("<h2 style='text-align: center; white-space: nowrap; font-size: clamp(1.1rem, 4vw, 2rem);'>📊 MI ESTADO INTEGRAL</h2>", unsafe_allow_html=True)
        
        _BW_RANGES_ATH = {
            "hemoglobin":  {"low": 13.5, "opt_lo": 15.0, "opt_hi": 17.5, "high": 18.0, "unit": "g/dL",    "name": "Hemoglobina Total"},
            "vcm":         {"low": 80,   "opt_lo": 82,   "opt_hi": 95,   "high": 100,  "unit": "fL",      "name": "Volumen Corpuscular Medio"},
            "chcm":        {"low": 32,   "opt_lo": 33,   "opt_hi": 36,   "high": 36,   "unit": "g/dL",    "name": "Concentración de Hemoglobina Corpuscular Media"},
            "rbc":         {"low": 4.5,  "opt_lo": 5.0,  "opt_hi": 5.8,  "high": 6.0,  "unit": "×10⁶/μL", "name": "Conteo de Eritrocitos (Glóbulos Rojos)"},
            "hematocrit":  {"low": 35,   "opt_lo": 40,   "opt_hi": 50,   "high": 54,   "unit": "%",       "name": "Hematocrito"},
            "ferritin":    {"low": 30,   "opt_lo": 50,   "opt_hi": 150,  "high": 400,  "unit": "ng/mL",   "name": "Ferritina Sérica"},
        }
        def _classify_ath(key, val):
            if val is None: return "—", "#999"
            r = _BW_RANGES_ATH[key]
            if val < r["low"]: return "🔴", "#dc3545"
            elif val <= r["opt_hi"]: return "🟢", "#28a745"
            elif val <= r["high"]: return "🟡", "#ffc107"
            else: return "🔴", "#dc3545"
        
        try:
            with SessionLocal() as session:
                col_bw_ath, col_lac_ath = st.columns(2)
                
                with col_bw_ath:
                    st.markdown("**🩸 Último Hemograma**")
                    last_bw = session.query(BloodworkRecord).filter(
                        BloodworkRecord.member_id == member_id
                    ).order_by(BloodworkRecord.date.desc()).first()
                    
                    if last_bw:
                        bw_date = last_bw.date.strftime("%d/%m/%Y") if last_bw.date else "—"
                        card_html = f'<div style="background:white; border:2px solid #00EEFF; border-radius:12px; padding:14px; font-size:0.9rem; color:#333; box-shadow: 0 0 10px rgba(0,238,255,0.15);">'
                        card_html += f'<div style="color:#00EEFF; font-weight:bold; margin-bottom:8px;">📅 {bw_date}</div>'
                        
                        for key in ["hemoglobin", "vcm", "chcm", "rbc", "hematocrit", "ferritin"]:
                            val = getattr(last_bw, key)
                            icon, _ = _classify_ath(key, val)
                            val_str = f"{val:.1f}" if val is not None else "—"
                            card_html += f'{icon} <strong>{_BW_RANGES_ATH[key]["name"]}:</strong> {val_str} <span style="color:#888;">{_BW_RANGES_ATH[key]["unit"]}</span><br>'
                        
                        card_html += '</div>'
                        st.markdown(card_html, unsafe_allow_html=True)
                    else:
                        st.caption("Aún no tienes hemogramas registrados.")

                    with st.expander("📥 Cargar Examen (PDF/Foto)", expanded=False):
                        ath_file = st.file_uploader("Subir PDF o Foto del examen:", type=["pdf", "png", "jpg", "jpeg"], key="ath_bw_file")
                        ath_pwd = st.text_input("Clave del PDF (si aplica):", type="password", key="ath_bw_pwd")
                        if ath_file is not None:
                            if st.button("⚡ Procesar Examen", key="btn_proc_ath"):
                                try:
                                    import importlib
                                    import hemograma_parser
                                    importlib.reload(hemograma_parser)
                                    parsed = hemograma_parser.process_file(ath_file.getvalue(), ath_file.name, password=ath_pwd if ath_pwd else None)
                                    if parsed.get("raw_text", "").startswith("[ERROR]"):
                                        st.error(f"Error al leer: {parsed.get('raw_text')}")
                                    else:
                                        rec_date = datetime.now().date()
                                        if parsed.get("date"):
                                            try:
                                                rec_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
                                            except Exception:
                                                pass
                                        new_rec = BloodworkRecord(
                                            member_id=member_id,
                                            date=rec_date,
                                            hemoglobin=parsed.get("hemoglobin"),
                                            vcm=parsed.get("vcm"),
                                            chcm=parsed.get("chcm"),
                                            rbc=parsed.get("rbc"),
                                            hematocrit=parsed.get("hematocrit"),
                                            ferritin=parsed.get("ferritin"),
                                            pdf_filename=ath_file.name
                                        )
                                        session.add(new_rec)
                                        session.commit()
                                        st.success(f"🎉 Examen guardado exitosamente ({parsed.get('markers_found', 0)} marcadores extraídos).")
                                        st.rerun()
                                except Exception as err:
                                    st.error(f"Error procesando examen: {err}")

                
                with col_lac_ath:
                    st.markdown("**🧪 Última Prueba de Lactato**")
                    last_lac = session.query(LactateTest).filter(
                        LactateTest.member_id == member_id
                    ).order_by(LactateTest.date.desc()).first()
                    
                    if last_lac:
                        lac_date = last_lac.date.strftime("%d/%m/%Y") if last_lac.date else "—"
                        card_html = f'<div style="background:white; border:2px solid #00EEFF; border-radius:12px; padding:14px; font-size:0.9rem; color:#333; box-shadow: 0 0 10px rgba(0,238,255,0.15);">'
                        card_html += f'<div style="color:#00EEFF; font-weight:bold; margin-bottom:8px;">📅 {lac_date} — {last_lac.sport or ""}</div>'
                        
                        if last_lac.lt1_power:
                            card_html += f'<span style="color:#B8860B; font-weight:bold;">LT1:</span> {last_lac.lt1_power:.0f} W'
                            if last_lac.lt1_hr: card_html += f' · {last_lac.lt1_hr} lpm'
                            if last_lac.lt1_lactate: card_html += f' · {last_lac.lt1_lactate:.1f} mmol/L'
                            card_html += '<br>'
                        if last_lac.lt2_power:
                            card_html += f'<span style="color:#dc3545; font-weight:bold;">LT2:</span> {last_lac.lt2_power:.0f} W'
                            if last_lac.lt2_hr: card_html += f' · {last_lac.lt2_hr} lpm'
                            if last_lac.lt2_lactate: card_html += f' · {last_lac.lt2_lactate:.1f} mmol/L'
                            card_html += '<br>'
                        if last_lac.weight:
                            card_html += f'<span style="color:#888;">Peso:</span> {last_lac.weight:.1f} kg<br>'
                        
                        card_html += '</div>'
                        st.markdown(card_html, unsafe_allow_html=True)
                    else:
                        st.caption("Aún no tienes pruebas de lactato registradas.")
        except Exception as e:
            st.caption(f"Datos no disponibles.")

    # Formulario para nuevo reporte
    st.markdown("---")
    st.markdown("<h2 style='text-align: center; white-space: nowrap; font-size: clamp(1.1rem, 4vw, 2rem);'>📝 REGISTRAR NUEVO REPORTE</h2>", unsafe_allow_html=True)
    with st.form("form_assq", clear_on_submit=True):
        
        # 2. Cuestionario Clínico
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
                
                # 2. Estratificación Clínica
                if sds_score <= 4:
                    categoria = "Sin problema clínico"
                elif sds_score <= 7:
                    categoria = "Problema leve"
                elif sds_score <= 10:
                    categoria = "Problema moderado"
                else:
                    categoria = "Problema grave"

                # 3. Guardar en Base de Datos
                member_id = st.session_state.get("athlete_member_id")
                if not member_id:
                    try:
                        with SessionLocal() as session:
                            m_obj = session.query(Member).filter(Member.name == atleta).first()
                            if m_obj:
                                member_id = m_obj.id
                                st.session_state["athlete_member_id"] = member_id
                    except Exception:
                        pass
                
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
                            
                            # Alertas por correo electrónico desactivadas a petición del entrenador.
                            # Todo el monitoreo se realiza mediante alertas visuales en el CRM.
                                    
                            st.session_state["show_toast"] = True
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error técnico al guardar: {e}")
                else:
                    st.error("⚠️ No se encontró tu nombre en la base de datos del club.")

    # Bloque de renderizado removido para reubicación a la sección del gráfico
        
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
