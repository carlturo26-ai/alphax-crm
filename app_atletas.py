import streamlit as st
from datetime import datetime
import pandas as pd
import plotly.express as px
import hashlib

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
css_styles = """
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700;800;900&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stApp { font-family: 'Nunito Sans', sans-serif !important; background-color: #050505 !important; color: #FFFFFF !important; }
p, label, span, .stMarkdown, h1, h2, h3, h4, h5, h6 { color: #00EEFF !important; }
.stDataFrame, .stTextInput > div > div > input, .stSelectbox > div > div > div, .stNumberInput > div > div > input { color: #FFFFFF !important; caret-color: #00EEFF; }
div[data-baseweb="select"] > div { background-color: #121212 !important; border-color: #00EEFF !important; }
div[data-baseweb="menu"], div[role="listbox"], div[role="option"] { background-color: #121212 !important; color: #FFFFFF !important; }
div[role="option"]:hover, div[role="option"][aria-selected="true"] { background-color: #00EEFF !important; color: #000000 !important; }
.stButton > button { border-radius: 8px; font-weight: 700; border: 1px solid #00EEFF; color: #00EEFF !important; background-color: transparent !important; transition: all 0.3s ease; }
.stButton > button:hover { background-color: #00EEFF !important; color: #000000 !important; box-shadow: 0 0 15px rgba(0, 238, 255, 0.4); }
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

# --- INTERFAZ DE USUARIO ---
st.image("https://images.unsplash.com/photo-1554342872-034a06541bad?q=80&w=1200&auto=format&fit=crop", use_container_width=True)
st.title("Monitoreo de Recuperación")
st.markdown("**Athlete Sleep Screening Questionnaire (ASSQ)**")
st.info("AlphaX Training Team", icon="📋")

if "athlete_user" not in st.session_state:
    st.session_state["athlete_user"] = None

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
            session = SessionLocal()
            user = session.query(AthleteUser).filter(AthleteUser.email == login_email.lower().strip()).first()
            if user and user.password_hash == hash_password(login_pass):
                st.session_state["athlete_user"] = user.athlete_name
                st.rerun()
            else:
                st.error("Correo o contraseña incorrectos.")
            session.close()
            
    with tab2:
        st.markdown("¿Es tu primera vez? Crea tu cuenta para vincular tu progreso.")
        reg_name = st.text_input("Tu Nombre Completo (tal como está en AlphaX)", key="reg_name")
        reg_email = st.text_input("Tu Correo Electrónico", key="reg_email")
        reg_pass = st.text_input("Crea una Contraseña", type="password", key="reg_pass")
        if st.button("Registrarme", use_container_width=True, key="btn_reg"):
            if not reg_name or not reg_email or not reg_pass:
                st.warning("Por favor, llena todos los campos.")
            else:
                session = SessionLocal()
                # Validar nombre de manera insensible a mayúsculas/minúsculas
                m_obj = session.query(Member).filter(Member.name.ilike(reg_name.strip())).first()
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
                        st.success("✅ Cuenta creada exitosamente. Ahora ve a la pestaña 'Iniciar Sesión'.")
                session.close()

else:
    atleta = st.session_state["athlete_user"]
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**👤 Atleta:** {atleta}")
    with col2:
        if st.button("Salir", key="logout_btn"):
            st.session_state["athlete_user"] = None
            st.rerun()
            
    # --- HISTORIAL PERSONAL ---
    st.markdown("---")
    st.subheader(f"📈 Tu Historial de Recuperación")
    
    session = SessionLocal()
    try:
        m_obj = session.query(Member).filter(Member.name == atleta).first()
        if m_obj:
            records = session.query(SleepRecord).filter(SleepRecord.member_id == m_obj.id).order_by(SleepRecord.date).all()
            if records:
                df_history = pd.DataFrame([{
                    "Fecha": r.date,
                    "Score (SDS)": r.sds_score,
                    "Categoría": r.clinical_category
                } for r in records])
                
                # Crear gráfico
                fig = px.line(
                    df_history, x="Fecha", y="Score (SDS)", markers=True,
                    title="Evolución de tu Calidad de Sueño (Menor puntaje es mejor)",
                    color_discrete_sequence=["#00EEFF"]
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)", 
                    font_color="white",
                    yaxis=dict(autorange="reversed") # Menor score es mejor (0-4 ninguna dificultad)
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Pequeña leyenda explicativa
                st.caption("Puntaje 0-4: Ninguna Dificultad | 5-7: Leve | 8-10: Moderada | >10: Severa")
            else:
                st.info("Aún no tienes registros de sueño. ¡Llena tu primer reporte abajo!")
    except Exception as e:
        st.error(f"Error cargando historial: {e}")
    finally:
        session.close()

    # Formulario para nuevo reporte
    st.markdown("---")
    st.subheader("📝 Registrar Nuevo Reporte")
    with st.form("form_assq", clear_on_submit=True):
        
        # 2. Cuestionario Clínico
        st.subheader("💤 Calidad del Descanso")
    
        horas = st.radio("1. En los ultimos 7 dias, ¿cuántas horas de sueño real tuviste por la noche? (Es diferente del número de horas que pasaste en la cama)", 
                         options=list(puntajes_horas.keys()))
        
        calidad = st.radio("2. ¿Qué tan satisfecho/insatisfecho estás con la calidad de tu sueño?", 
                           options=list(puntajes_calidad.keys()))
        
        latencia = st.radio("3. En los ultimos 7 dias, ¿cuánto tiempo te toma habitualmente quedarte dormido cada noche?", 
                            options=list(puntajes_latencia.keys()))
        
        despertares = st.radio("4. ¿Con qué frecuencia tienes problemas para mantenerte dormido?", 
                               options=list(puntajes_despertares.keys()))
                               
        medicamentos = st.radio("5. En los ultimos 7 dias, ¿con qué frecuencia has tomado medicamentos (recetados o de venta libre) para ayudarte a dormir?", 
                               options=list(puntajes_medicamentos.keys()))
        
        st.markdown("---")
        submitted = st.form_submit_button("Enviar Reporte", use_container_width=True)

# --- PROCESAMIENTO DE DATOS ---
if submitted:
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
            try:
                session = SessionLocal()
                m_obj = session.query(Member).filter(Member.name == atleta).first()
                
                if m_obj:
                    nuevo_registro = SleepRecord(
                        member_id=m_obj.id,
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
                    st.success(f"✅ ¡Gracias {atleta}! Tu reporte ha sido enviado a tu entrenador.")
                    st.info(f"📊 **Tu Score SDS:** {sds_score}/12 ({categoria})")
                    st.balloons()
                    
                    import time
                    time.sleep(3)
                    st.rerun()
                else:
                    st.error("⚠️ No se encontró tu nombre en la base de datos del club.")
                    
            except Exception as e:
                session.rollback()
                st.error(f"Error técnico al guardar: {e}")
            finally:
                session.close()
