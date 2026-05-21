import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import hashlib
import unicodedata
from streamlit_cookies_controller import CookieController
from background_base64 import BACKGROUND_IMAGE_BASE64

try:
    from database import SessionLocal, Member, SleepRecord, AthleteUser, engine
    from sqlalchemy import text
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        try:
            conn.execute(text("ALTER TABLE sleep_records ADD COLUMN raw_medications VARCHAR;"))
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

# Try to get the user from cookies if not in session
athlete_cookie = cookie_controller.get("athlete_user_cookie")
if athlete_cookie and st.session_state["athlete_user"] is None:
    st.session_state["athlete_user"] = athlete_cookie
    # Fetch and cache the member_id once upon cookie login
    try:
        with SessionLocal() as session:
            m_obj = session.query(Member).filter(Member.name == athlete_cookie).first()
            if m_obj:
                st.session_state["athlete_member_id"] = m_obj.id
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
                        # Fetch and cache the member_id immediately
                        m_obj = session.query(Member).filter(Member.name == user.athlete_name).first()
                        if m_obj:
                            st.session_state["athlete_member_id"] = m_obj.id
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
        reg_name = st.text_input("Tu Nombre Completo (tal como está en AlphaX)", key="reg_name")
        reg_email = st.text_input("Tu Correo Electrónico", key="reg_email")
        reg_pass = st.text_input("Crea una Contraseña", type="password", key="reg_pass")
        if st.button("Registrarme", use_container_width=True, key="btn_reg"):
            if not reg_name or not reg_email or not reg_pass:
                st.warning("Por favor, llena todos los campos.")
            else:
                try:
                    with SessionLocal() as session:
                        # Obtener todos los socios activos para coincidencia flexible
                        active_members = session.query(Member).filter(Member.active == True).all()
                        
                        input_cleaned = clean_and_normalize(reg_name)
                        input_words = input_cleaned.split()
                        
                        m_obj = None
                        
                        # 1. Coincidencia exacta (normalizada)
                        for m in active_members:
                            if clean_and_normalize(m.name) == input_cleaned:
                                m_obj = m
                                break
                        
                        # 2. Coincidencia por palabras (si la entrada tiene al menos 2 palabras)
                        if not m_obj and len(input_words) >= 2:
                            potential_matches = []
                            for m in active_members:
                                db_cleaned = clean_and_normalize(m.name)
                                if all(word in db_cleaned for word in input_words):
                                    potential_matches.append(m)
                            if len(potential_matches) == 1:
                                m_obj = potential_matches[0]
                                
                        if not m_obj:
                            st.error("No encontramos tu nombre en la base de datos del club. Asegúrate de escribirlo sin errores y completo.")
                        else:
                            existing_email = session.query(AthleteUser).filter(AthleteUser.email == reg_email.lower().strip()).first()
                            if existing_email:
                                st.error("Este correo ya está registrado.")
                            else:
                                new_user = AthleteUser(
                                    email=reg_email.lower().strip(),
                                    password_hash=hash_password(reg_pass),
                                    athlete_name=m_obj.name
                                )
                                session.add(new_user)
                                session.commit()
                                st.success(f"✅ Cuenta creada exitosamente vinculada a **{m_obj.name}**. Ahora ve a la pestaña 'Iniciar Sesión'.")
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
            cookie_controller.remove("athlete_user_cookie")
            st.rerun()
            
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
