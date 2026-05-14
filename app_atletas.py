import streamlit as st
from datetime import datetime
import pandas as pd
import plotly.express as px
try:
    from database import SessionLocal, Member, SleepRecord
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

session = SessionLocal()
# Leer todos los deportistas activos de la base de datos
try:
    active_members = session.query(Member).filter(Member.active == True).order_by(Member.name).all()
    atletas_nombres = [m.name for m in active_members]
except Exception as e:
    atletas_nombres = []
    st.error(f"Error cargando socios: {e}")
finally:
    session.close()

# --- LÓGICA DE PUNTUACIÓN CLÍNICA (ASSQ) ---
puntajes_horas = {
    "Más de 8 horas": 0, 
    "7 a 8 horas": 1, 
    "5 a 6 horas": 2, 
    "Menos de 5 horas": 3
}
puntajes_calidad = {
    "Muy buena (Reparadora)": 0, 
    "Buena": 1, 
    "Regular (Fragmentada)": 2, 
    "Mala (Agotadora)": 3
}
puntajes_latencia = {
    "Me duermo casi de inmediato (< 15 min)": 0, 
    "Tardo un poco (16-30 min)": 1, 
    "Me cuesta dormir (31-60 min)": 2, 
    "Doy muchas vueltas (> 60 min)": 3
}
puntajes_despertares = {
    "No me despierto o vuelvo a dormir rápido": 0, 
    "1-2 veces por semana me cuesta volver a dormir": 1, 
    "3 o más veces por semana": 2
}

# --- INTERFAZ DE USUARIO ---
st.image("https://images.unsplash.com/photo-1554342872-034a06541bad?q=80&w=1200&auto=format&fit=crop", use_container_width=True)
st.title("Monitoreo de Recuperación")
st.markdown("**Athlete Sleep Screening Questionnaire (ASSQ)**")
st.info("AlphaX Training Team", icon="📋")

# 1. Identificación
st.subheader("👤 Identificación")
atleta = st.selectbox("Selecciona tu nombre:", ["-- Selecciona tu nombre --"] + atletas_nombres)

if atleta != "-- Selecciona tu nombre --":
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
    
    horas = st.radio("1. Durante la última semana, ¿cuántas horas de sueño real tuviste por noche?", 
                     options=list(puntajes_horas.keys()))
    
    calidad = st.radio("2. ¿Cómo calificarías la calidad general de tu sueño?", 
                       options=list(puntajes_calidad.keys()))
    
    latencia = st.radio("3. ¿Cuánto tiempo sueles tardar en quedarte dormido?", 
                        options=list(puntajes_latencia.keys()))
    
    despertares = st.radio("4. ¿Con qué frecuencia te despiertas en medio de la noche y te cuesta volver a dormir?", 
                           options=list(puntajes_despertares.keys()))
    
    st.markdown("---")
    submitted = st.form_submit_button("Enviar Reporte", use_container_width=True)

# --- PROCESAMIENTO DE DATOS ---
if submitted:
    with st.spinner('Guardando tu reporte...'):
            # 1. Cálculo Automático del Score SDS
            sds_score = (
                puntajes_horas[horas] + 
                puntajes_calidad[calidad] + 
                puntajes_latencia[latencia] + 
                puntajes_despertares[despertares]
            )
            
            # 2. Estratificación Clínica
            if sds_score <= 4:
                categoria = "Ninguna Dificultad"
            elif sds_score <= 7:
                categoria = "Dificultad Leve"
            elif sds_score <= 10:
                categoria = "Dificultad Moderada"
            else:
                categoria = "Dificultad Severa"

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
                        raw_awakenings=despertares
                    )
                    session.add(nuevo_registro)
                    session.commit()
                    st.success(f"✅ ¡Gracias {atleta}! Tu reporte ha sido enviado a tu entrenador.")
                    st.balloons()
                else:
                    st.error("⚠️ No se encontró tu nombre en la base de datos del club.")
                    
            except Exception as e:
                session.rollback()
                st.error(f"Error técnico al guardar: {e}")
            finally:
                session.close()
