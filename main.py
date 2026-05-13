
from datetime import datetime
import os
import streamlit as st
import pandas as pd
import requests
import json

try:
    from database import init_db, SessionLocal, Member, Transaction, Expense, engine
    from logic import import_excel_data, get_summary_kpis, update_member_phones
except Exception as e:
    st.error(f"💀 Error CRÍTICO de Importación: {e}")
    st.stop()
import plotly.express as px

def force_schema_update(db_engine):
    """
    Executes schema migrations in AUTOCOMMIT mode.
    This prevents Postgres from aborting the entire transaction block 
    if one of the columns already exists.
    """
    from sqlalchemy import text
    queries = [
        "ALTER TABLE expenses ADD COLUMN paid_by VARCHAR;",
        "ALTER TABLE members ADD COLUMN phone VARCHAR;",
        "ALTER TABLE transactions ADD COLUMN received_by VARCHAR;",
        "ALTER TABLE members ADD COLUMN start_month VARCHAR DEFAULT 'ENERO';"
    ]
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for q in queries:
            try:
                conn.execute(text(q))
            except Exception:
                pass 
                
    with db_engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE members ALTER COLUMN active DROP DEFAULT;"))
            conn.execute(text("ALTER TABLE members ALTER COLUMN active TYPE BOOLEAN USING (active::integer <> 0);"))
            conn.execute(text("ALTER TABLE members ALTER COLUMN active SET DEFAULT true;"))
            conn.commit()
        except:
            conn.rollback()
            pass

# Configuración de página
st.set_page_config(
    page_title="AlphaX CRM",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos personalizados (CSS Hack para branding AlphaX)
css_styles = """
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700;800;900&display=swap" rel="stylesheet">
<style>
/* GLOBAL RESET: Dark Mode Standard */
html, body, [class*="css"], .stApp { font-family: 'Nunito Sans', sans-serif !important; background-color: #050505 !important; color: #FFFFFF !important; }
/* Make all grey text bright cyan */
p, label, span, .stMarkdown, h1, h2, h3, h4, h5, h6 { color: #00EEFF !important; }
/* Cards */
.metric-card { background-color: #121212; border: 1px solid #00EEFF; padding: 20px; border-radius: 10px; color: #00EEFF; box-shadow: 0 0 10px rgba(0, 238, 255, 0.1); }
/* Sidebar and inputs to fit theme */
section[data-testid="stSidebar"] { background-color: #0a0a0a !important; border-right: 1px solid rgba(0, 238, 255, 0.2); }
.stDataFrame, .stTextInput > div > div > input, .stSelectbox > div > div > div, .stNumberInput > div > div > input { color: #FFFFFF !important; caret-color: #00EEFF; }
div[data-baseweb="select"] > div { background-color: #121212 !important; border-color: #00EEFF !important; }
div[data-baseweb="menu"], div[role="listbox"], div[role="option"] { background-color: #121212 !important; color: #FFFFFF !important; }
div[role="option"]:hover, div[role="option"][aria-selected="true"] { background-color: #00EEFF !important; color: #000000 !important; }
/* BUTTONS */
.stButton > button { border-radius: 8px; font-weight: 700; border: 1px solid #00EEFF; color: #00EEFF !important; background-color: transparent !important; transition: all 0.3s ease; }
.stButton > button:hover { background-color: #00EEFF !important; color: #000000 !important; box-shadow: 0 0 15px rgba(0, 238, 255, 0.4); }
</style>
"""
st.markdown(css_styles, unsafe_allow_html=True)

# Inicializar DB
init_db()

# Sidebar
with st.sidebar:
    # Use relative path so it resolves correctly on both local Mac and Streamlit Cloud
    try:
        st.image("assets/images/alphax_banner_logo.png", use_container_width=True)
    except Exception as e:
        st.error(f"Logo no encontrado: {e}")
    
    st.markdown("<h2 style='text-align: center; color: #00EEFF; margin-top: 10px;'>ALPHAX TEAM ADMIN</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    page = st.radio("Navegación", ["Dashboard", "Socios", "Novedades/Pagos", "Gastos", "Configuración", "ASSQ (Sueño)"])


    
    st.markdown("---")
    st.markdown("### Importar Excel")
    uploaded_file = st.file_uploader("Cargar 'CUENTA MULTISPORT'", type=["xlsx"])
    
    # NEW CHECKBOX FOR MERGE
    merge_phones = st.checkbox("☑️ Solo actualizar teléfonos (Merge)", help="Marca esto para subir tu Backup y recuperar los teléfonos sin borrar nada más.")
    
    if uploaded_file:
        if st.button("Procesar Archivo"):
            with st.spinner("Importando datos..."):
                # Save temp to process
                temp_path = f"temp_{uploaded_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                if merge_phones:
                     msg = update_member_phones(temp_path)
                     st.success(msg)
                else:
                     msg = import_excel_data(temp_path)
                     st.success(msg)

# --- PAGE: DASHBOARD ---
if page == "Dashboard":
    st.header("📊 Resumen del Club (2026)")
    
    # Filter by Group
    session = SessionLocal()
    groups = ["Todos"] + [r[0] for r in session.query(Member.group).distinct().all() if r[0]]
    selected_group = st.selectbox("Filtrar por Grupo:", groups)
    
    # Base Query
    tx_query = session.query(Transaction).join(Member)
    member_query = session.query(Member).filter(Member.active == True)
    
    if selected_group != "Todos":
        tx_query = tx_query.filter(Member.group == selected_group)
        member_query = member_query.filter(Member.group == selected_group)
        
    # KPIs Calculation
    try:
        txs = tx_query.filter(Transaction.status == 'PAID').all()
        
        all_txs = session.query(Transaction).filter(Transaction.status == 'PAID').all()
        all_expenses = session.query(Expense).all()
        total_expenses_raw = sum(e.amount for e in all_expenses)
        
        inc_ale = sum(t.amount for t in all_txs if t.member.group == "Alejandro")
        inc_car = sum(t.amount for t in all_txs if t.member.group == "Carlos")
        inc_apr = sum(t.amount for t in all_txs if t.member.group == "Aprendizaje")
        
        if selected_group == "Todos":
            total_income = sum(t.amount for t in txs)
            total_expenses = total_expenses_raw
        elif selected_group == "Carlos":
            total_income = inc_car + (inc_ale * 0.20) + (inc_apr / 2.0)
            total_expenses = total_expenses_raw / 2.0
        elif selected_group == "Alejandro":
            total_income = (inc_ale * 0.80) + (inc_apr / 2.0)
            total_expenses = total_expenses_raw / 2.0
        elif selected_group == "Aprendizaje":
            total_income = inc_apr
            total_expenses = total_expenses_raw
        else:
            total_income = sum(t.amount for t in txs)
            total_expenses = 0
            
        net_profit = total_income - total_expenses
        
    except Exception as e_mig:
        session.rollback()
        st.error(f"⚠️ Actualización Requerida en Pagos/Gastos: Selecciona el botón de mantenimiento abajo.")
        
        st.markdown("---")
        st.subheader("🔧 Herramientas de Mantenimiento Rápido")
        
        if st.button("🛠️ FORZAR ACTUALIZACIÓN DE TABLAS"):
            try:
                force_schema_update(engine)
                st.success("✅ Tablas reparadas en la nube. ¡Recarga la página (F5)!")
            except Exception as e_mig_2:
                st.error(f"Error forzando actualización: {e_mig_2}")
        
        txs = []
        total_income = 0
        total_expenses = 0
        net_profit = 0
    
    try:
        member_count = member_query.count()
    except Exception as e_mem:
        session.rollback()
        st.error(f"⚠️ Necesitas actualizar la base de datos para usar WhatsApp.")
        member_count = 0
        if st.button("🛠️ TOCAR AQUÍ PARA HABILITAR WHATSAPP", type="primary", key="fix_members"):
             try:
                force_schema_update(engine)
                st.success("✅ ¡Actualizado! Recargando...")
                session.close(); st.rerun()
             except Exception as e:
                st.error(str(e))
    
    # Metrics Layout
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ingresos", f"${total_income:,.0f}")
    col2.metric("Gastos", f"${total_expenses:,.0f}", delta=-total_expenses, delta_color="inverse")
    col3.metric("Resultado Neto", f"${net_profit:,.0f}")
    col4.metric("Socios", member_count)
    
    # --- GRÁFICOS Y ANÁLISIS ---
    months_order = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    
    # 1. Preparar Datos Mensuales con Lógica Estratégica
    monthly_data = {m: {"Income": 0.0, "Expense": 0.0} for m in months_order}
    data_exp_cat = [] # Detalle para gráfico apilado de gastos
    
    # --- PROCESAR INGRESOS ---
    # Usamos all_txs (todas las transacciones pagadas) para calcular pedazos según el Entrenador seleccionado
    for t in all_txs:
        m = t.month
        amt = t.amount
        grp = t.member.group
        
        if m in monthly_data:
            if selected_group == "Todos":
                monthly_data[m]["Income"] += amt
            elif selected_group == "Carlos":
                if grp == "Carlos": monthly_data[m]["Income"] += amt
                elif grp == "Alejandro": monthly_data[m]["Income"] += amt * 0.20
                elif grp == "Aprendizaje": monthly_data[m]["Income"] += amt / 2.0
            elif selected_group == "Alejandro":
                if grp == "Alejandro": monthly_data[m]["Income"] += amt * 0.80
                elif grp == "Aprendizaje": monthly_data[m]["Income"] += amt / 2.0
            elif selected_group == "Aprendizaje":
                if grp == "Aprendizaje": monthly_data[m]["Income"] += amt
                
    # --- PROCESAR GASTOS ---
    try:
        expense_query = session.query(Expense)
        all_expenses_list = expense_query.all()
        for e in all_expenses_list:
            if hasattr(e, 'date') and e.date:
                m_str = months_order[e.date.month - 1]
            else:
                m_str = "SIN MES"
                
            amt = e.amount
            # Aplicar partición de gastos si filtramos por Alejandro o Carlos
            if selected_group in ["Carlos", "Alejandro"]:
                allocated_exp = amt / 2.0
            else:
                allocated_exp = amt
                
            if m_str in monthly_data:
                monthly_data[m_str]["Expense"] += allocated_exp
                paid_by_val = getattr(e, 'paid_by', 'AlphaX (Caja)')
                data_exp_cat.append({
                    "month": m_str,
                    "Category": e.category or "General", 
                    "Amount": allocated_exp, 
                    "Pagado Por": paid_by_val,
                    "Descripción": e.description
                })
        df_exp = pd.DataFrame(data_exp_cat)
    except Exception as e:
        session.rollback()
        df_exp = pd.DataFrame()
        st.warning(f"⚠️ Error cargando gastos: {e}")
        
    # Construir DataFrames de Resultados para Plotly
    df_rev_grouped = pd.DataFrame({"month": list(monthly_data.keys()), "amount": [d["Income"] for d in monthly_data.values()]})
    df_rev_grouped['month'] = pd.Categorical(df_rev_grouped['month'], categories=months_order, ordered=True)
    
    if not df_exp.empty:
        df_exp['month'] = pd.Categorical(df_exp['month'], categories=months_order, ordered=True)
        # Re-agrupar para validar la base de datos visual
        df_exp_grouped = df_exp.groupby("month", observed=False)["Amount"].sum().reset_index()
        df_exp_cat = df_exp.groupby(["month", "Category"], observed=False)["Amount"].sum().reset_index()
        df_exp_cat = df_exp_cat[df_exp_cat["Amount"] > 0]
    else:
        df_exp_grouped = pd.DataFrame({"month": list(monthly_data.keys()), "Amount": [d["Expense"] for d in monthly_data.values()]})
        df_exp_grouped['month'] = pd.Categorical(df_exp_grouped['month'], categories=months_order, ordered=True)
        df_exp_cat = pd.DataFrame()

    # 3. Preparar Datos de Utilidad Neta
    df_net = pd.merge(df_rev_grouped, df_exp_grouped, on="month", how="outer")
    df_net["amount"] = df_net["amount"].fillna(0)
    df_net["Amount"] = df_net["Amount"].fillna(0)
    df_net["Neto"] = df_net["amount"] - df_net["Amount"]
    df_net["Tipo"] = df_net["Neto"].apply(lambda x: "Ganancia" if x >= 0 else "Pérdida")
    
    # Eliminar meses futuros sin datos para que el gráfico no se vea vacío a la derecha
    # Opcional: Mostrar solo meses con movimientos
    has_movement = (df_net["amount"] > 0) | (df_net["Amount"] > 0) | (df_net["Neto"] != 0)
    # Por ahora dejamos todos los meses como pidió el usuario para ver evolución, o filtramos si vemos que es mejor.
    
    # --- FILA DE GRÁFICOS 1 (Ingresos y Gastos) ---
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("### 📈 Evolución Ingresos")
        fig_rev = px.bar(
            df_rev_grouped, x="month", y="amount", 
            color_discrete_sequence=["#33C1FF"], text_auto='.2s'
        )
        fig_rev.update_traces(hovertemplate='Mes: %{x}<br>Total: $%{y:,.0f}<extra></extra>')
        fig_rev.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white")
        st.plotly_chart(fig_rev, use_container_width=True)
        
    with col_c2:
        st.markdown("### 📉 Evolución Gastos (Por Categoría)")
        if not df_exp_cat.empty:
            fig_exp = px.bar(
                df_exp_cat, x="month", y="Amount", color="Category",
                text_auto='.2s', color_discrete_sequence=px.colors.qualitative.Pastel,
                barmode='stack'
            )
            fig_exp.update_traces(hovertemplate='Mes: %{x}<br>Categoría: %{color}<br>Total: $%{y:,.0f}<extra></extra>')
            fig_exp.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white")
            st.plotly_chart(fig_exp, use_container_width=True)
        else:
            st.info("No hay gastos registrados aún.")

    # --- FILA DE GRÁFICOS 2 (Utilidad Neta) ---
    st.markdown("### ⚖️ Utilidad Neta (Ingresos - Gastos)")
    fig_net = px.bar(
        df_net, x="month", y="Neto", text_auto='.2s',
        color="Tipo", color_discrete_map={"Ganancia": "#33C1FF", "Pérdida": "#FF4B4B"}
    )
    fig_net.update_traces(hovertemplate='Mes: %{x}<br>Neto: $%{y:,.0f}<extra></extra>')
    fig_net.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white")
    st.plotly_chart(fig_net, use_container_width=True)
    
    # --- TABLA DETALLADA DE GASTOS ---
    if not df_exp.empty:
        with st.expander("📋 Ver Desglose Detallado de Gastos por Mes", expanded=False):
            df_disp = df_exp.copy()
            df_disp['Amount'] = df_disp['Amount'].apply(lambda x: f"${x:,.0f}")
            df_disp.rename(columns={'month': 'Mes', 'Category': 'Categoría', 'Amount': 'Monto'}, inplace=True)
            st.dataframe(df_disp[["Mes", "Categoría", "Descripción", "Monto", "Pagado Por"]], use_container_width=True)
    
    # --- DEUDORES / PENDIENTES ---
    st.markdown("---")
    st.subheader("⚠️ Pendientes de Pago (Mes Actual)")
    
    # Logic: Active Members - Members with Paid Tx in Current Month (or Selected Year-Month in filter ideally)
    # For now, let's assume we look at "February 2026" (Hardcoded context or dynamic)
    # Let's make it dynamic based on current real date or a selector? 
    # User asked for "Alerta del mes". Let's use current month dynamic.
    
    current_month_index = datetime.now().month - 1 # 0-11
    months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                   "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    current_month_name = months_list[current_month_index]
    current_year = 2026 # Still forcing 2026 context per previous task
    
    # Alert Logic
    current_day = datetime.now().day
    if current_day > 10:
        st.error(f"🚨 **Alerta de Cobro**: Estamos a día {current_day}. Verifica los pendientes abajo.")
    elif current_day > 5:
        st.warning(f"Estamos a día {current_day}. Recuerda enviar cobros pronto.")
        
    # Query Debtors
    try:
        # 1. Get all active members IDs (filtered by group if selected)
        active_m_query = session.query(Member).filter(Member.active == True)
        if selected_group != "Todos":
            active_m_query = active_m_query.filter(Member.group == selected_group)
        
        active_members = active_m_query.all()
        active_ids = {m.id for m in active_members}
        
        # 2. Get members who PAID in current month
        paid_query = session.query(Transaction.member_id).join(Member).filter(
            Transaction.year == current_year,
            Transaction.month == current_month_name,
            Transaction.status == 'PAID'
        )
        if selected_group != "Todos":
            paid_query = paid_query.filter(Member.group == selected_group)
            
        paid_ids = {r[0] for r in paid_query.all()}
        
        # 3. Diff and Filter by Start Month
        pending_ids = active_ids - paid_ids
        
        true_pending_members = []
        for m in active_members:
            if m.id in pending_ids:
                m_start = getattr(m, 'start_month', 'ENERO')
                m_start_idx = months_list.index(m_start) if m_start in months_list else 0
                if m_start_idx <= current_month_index:
                    true_pending_members.append(m)
        pending_members = true_pending_members
        
        col_d1, col_d2 = st.columns([1, 3])
        col_d1.metric("Pendientes", len(pending_members), delta_color="inverse")
        
        with col_d2:
            if pending_members:
                with st.expander(f":black[Ver Lista de {len(pending_members)} Deudores ({current_month_name})]", expanded=True):
                    # Prepare data with WhatsApp Link
                    dept_data = []
                    import urllib.parse
                    
                    for m in pending_members:
                        # User-Defined Copy (Persuasive & Wolf Theme)
                        msg = f"¡Hola *{m.name.title()}*! espero todo vaya super bien!\n\nTe escribo un mensajito rápido para recordarte el pago de la mensualidad correspondiente a este mes. Agradezco mucho si puedes gestionarlo pronto para mantener todo en orden administrativo ✅.\n\n¡Un abrazo y a seguir sumando kilómetros 🐺!"
                        encoded_msg = urllib.parse.quote(msg)
                        
                        # Phone Logic (Safe Access)
                        phone_number = getattr(m, 'phone', "")
                        # Clean phone number (remove +, spaces)
                        if phone_number:
                            phone_number = "".join(filter(str.isdigit, phone_number))
                            # Use web.whatsapp.com explicitly to force browser handling (Business account)
                            wa_link = f"https://web.whatsapp.com/send?phone={phone_number}&text={encoded_msg}"
                        else:
                             # Fallback if no phone
                             wa_link = f"https://web.whatsapp.com/send?text={encoded_msg}"

                        # Modern DataFrame with Width Control
                        dept_data.append({"Nombre": m.name, "WhatsApp": wa_link})
                        
                    df_dept = pd.DataFrame(dept_data)
                    
                    st.dataframe(
                        df_dept,
                        column_config={
                            "WhatsApp": st.column_config.LinkColumn(
                                "Acción (WhatsApp)",
                                help="Click para abrir WhatsApp Web",
                                validate="^https://.*",
                                display_text="📲 Enviar Cobro"
                            ),
                            "Nombre": st.column_config.TextColumn(
                                "Socio Pendiente",
                                width="large" # Force wider name column
                            )
                        },
                        hide_index=True,
                        use_container_width=True # FIX: Expands to full width
                    )
            else:
                 st.success("¡Nadie debe nada! 🎉")
                 
    except Exception as e:
        session.rollback()
        st.error(f"⚠️ Error cargando deudores: {e}")
        st.info("👉 Ve a 'Configuración' -> 'ACTUALIZAR DB'.")
    
    # --- LIQUIDACIÓN DE SOCIOS ---
    st.markdown("---")
    st.subheader("🤝 Liquidación Mensual de Entrenadores")
    st.caption("Calculadora financiera para dividir honorarios según el modelo de negocio AlphaX.")
    
    # Selectores para la liquidación
    c_liq1, c_liq2 = st.columns(2)
    with c_liq1:
        liq_month = st.selectbox("Mes a liquidar", months_list, index=current_month_index)
    with c_liq2:
        liq_year = st.number_input("Año a liquidar", value=current_year, step=1)
        
    try:
        # Fetching valid payouts for the month
        liq_txs = session.query(Transaction).join(Member).filter(
            Transaction.month == liq_month,
            Transaction.year == liq_year,
            Transaction.status == 'PAID'
        ).all()
        
        # Fetching expenses for the month
        from sqlalchemy import extract
        liq_month_idx = months_list.index(liq_month) + 1
        
        liq_exp_query = session.query(Expense).filter(
            extract('month', Expense.date) == liq_month_idx,
            extract('year', Expense.date) == liq_year
        ).all()
        
        # 1. Variables Base
        income_alejandro = sum(t.amount for t in liq_txs if t.member.group == "Alejandro")
        income_carlos = sum(t.amount for t in liq_txs if t.member.group == "Carlos")
        income_aprendizaje = sum(t.amount for t in liq_txs if t.member.group == "Aprendizaje")
        
        total_expenses_liq = sum(e.amount for e in liq_exp_query)
        expenses_paid_by_alejandro = sum(e.amount for e in liq_exp_query if getattr(e, 'paid_by', '') == 'Alejandro')
        expenses_paid_by_carlos = sum(e.amount for e in liq_exp_query if getattr(e, 'paid_by', '') == 'Carlos')
        
        # New: Tracking physical cash received
        caja_alejandro = 0
        caja_carlos = 0
        
        for t in liq_txs:
            # Si el pago tiene 'received_by' (nuevo sistema)
            if getattr(t, 'received_by', None) and t.received_by in ["Alejandro", "Carlos"]:
                if t.received_by == "Alejandro":
                    caja_alejandro += t.amount
                elif t.received_by == "Carlos":
                    caja_carlos += t.amount
            # Fallback para pagos viejos sin 'received_by' (Enero/Febrero)
            else:
                if t.member.group == "Alejandro":
                    caja_alejandro += t.amount
                elif t.member.group == "Carlos":
                    caja_carlos += t.amount
                elif t.member.group == "Aprendizaje":
                    if liq_month in ["ENERO", "FEBRERO"] and liq_year == 2026:
                        caja_alejandro += t.amount
                    else:
                        caja_carlos += t.amount

        # 2. Lógica Comercial Negociada (Honorarios que GANAN)
        alejandro_share_alejandro = income_alejandro * 0.80
        carlos_share_alejandro = income_alejandro * 0.20
        
        pozo_aprendizaje = income_aprendizaje
        gastos_a_cubrir = total_expenses_liq
        balance_aprendizaje = pozo_aprendizaje - gastos_a_cubrir
        mitad_balance = balance_aprendizaje / 2.0 
        
        # Honorarios Totales (Lo que cada uno DEBE tener al final)
        honorarios_alejandro_base = alejandro_share_alejandro + mitad_balance
        honorarios_carlos_base = income_carlos + carlos_share_alejandro + mitad_balance
        
        total_alejandro = honorarios_alejandro_base + expenses_paid_by_alejandro
        total_carlos = honorarios_carlos_base + expenses_paid_by_carlos
        
        # 3. Flujo de Caja (Quién le debe a quién)
        deuda_a_alejandro = total_alejandro - caja_alejandro
        deuda_a_carlos = total_carlos - caja_carlos
        
        # UI
        st.markdown(f"#### Resultados de {liq_month.title()} {int(liq_year)}")
        
        # Alerta de Transferencia Principal
        st.markdown("---")
        if deuda_a_alejandro > 0:
            st.error(f"### 💸 TRANSFERENCIA PENDIENTE: **Carlos** debe girarle **${deuda_a_alejandro:,.0f}** a Alejandro.")
        elif deuda_a_carlos > 0:
            st.error(f"### 💸 TRANSFERENCIA PENDIENTE: **Alejandro** debe girarle **${deuda_a_carlos:,.0f}** a Carlos.")
        else:
            st.success("### ✅ CUENTAS SALDADAS: Ninguno se debe dinero este mes.")
        st.markdown("---")
        
        # Usamos contenedores estilizados
        col_res1, col_res2 = st.columns(2)
        
        with col_res1:
            st.info("🐺 **ALEJANDRO**")
            st.markdown(f"**Revisión de Honorarios (Gané):**")
            st.markdown(f"- 80% Grupo Alejandro: `+${alejandro_share_alejandro:,.0f}`")
            if balance_aprendizaje < 0:
                st.markdown(f"- 50% Déficit Gastos: `-${abs(mitad_balance):,.0f}`")
            else:
                st.markdown(f"- 50% Sobrante Aprend.: `+${mitad_balance:,.0f}`")
            if expenses_paid_by_alejandro > 0:
                st.markdown(f"- Devolución Gastos (Puse Dinero): `+${expenses_paid_by_alejandro:,.0f}`")
            
            st.metric("Total que DEBE tener (Honorarios)", value=f"${total_alejandro:,.0f}")
            st.metric("Total que TIENE FÍSICAMENTE (Caja)", value=f"${caja_alejandro:,.0f}", delta=f"{caja_alejandro - total_alejandro:,.0f} (Tiene vs Ideal)", delta_color="inverse")

        with col_res2:
            st.error("🦁 **CARLOS**")
            st.markdown(f"**Revisión de Honorarios (Gané):**")
            st.markdown(f"- 100% Grupo Carlos: `+${income_carlos:,.0f}`")
            st.markdown(f"- 20% Grupo Alejandro: `+${carlos_share_alejandro:,.0f}`")
            if balance_aprendizaje < 0:
                st.markdown(f"- 50% Déficit Gastos: `-${abs(mitad_balance):,.0f}`")
            else:
                st.markdown(f"- 50% Sobrante Aprend.: `+${mitad_balance:,.0f}`")
            if expenses_paid_by_carlos > 0:
                st.markdown(f"- Devolución Gastos (Puse Dinero): `+${expenses_paid_by_carlos:,.0f}`")
            
            st.metric("Total que DEBE tener (Honorarios)", value=f"${total_carlos:,.0f}")
            st.metric("Total que TIENE FÍSICAMENTE (Caja)", value=f"${caja_carlos:,.0f}", delta=f"{caja_carlos - total_carlos:,.0f} (Tiene vs Ideal)", delta_color="inverse")

        with st.expander("Ver Matemática del Pozo de Aprendizaje y Gastos"):
            st.write(f"1. **Ingresos del Grupo Aprendizaje:** `${pozo_aprendizaje:,.0f}`")
            st.write(f"2. **Total de Gastos Realizados en el Mes:** `${gastos_a_cubrir:,.0f}`")
            
            if balance_aprendizaje >= 0:
                st.success(f"**El Pozo cubrió todo y sobraron:** `${balance_aprendizaje:,.0f}` (Se divide entre 2)")
            else:
                st.warning(f"**El Pozo no alcanzó. Faltó:** `${abs(balance_aprendizaje):,.0f}` (Se divide entre 2 para que lo asuman)")

    except Exception as e:
        session.rollback()
        st.error(f"⚠️ Actualización Requerida en Liquidación: El banco de datos necesita la nueva columna 'Cuenta Destino'.")
        
        st.markdown("---")
        st.subheader("🔧 Herramientas de Mantenimiento Rápido")
        if st.button("🛠️ FORZAR ACTUALIZACIÓN DE TABLAS", key="fix_liq"):
            try:
                force_schema_update(engine)
                st.success("✅ Tablas reparadas. ¡Recarga la página (F5)!")
                import time
                time.sleep(1)
                session.close(); st.rerun()
            except Exception as e_mig:
                st.error(f"Error forzando actualización: {e_mig}")
        
    session.close()

# --- PAGE: SOCIOS ---
elif page == "Socios":
    st.header("👥 Gestión de Socios")
    session = SessionLocal()

    # Months list for dropdowns
    months_order = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

    # 1. Add New Member
    with st.expander("➕ Registrar Nuevo Atleta"):
        with st.form("new_member"):
            c1, c2 = st.columns(2)
            new_name = c1.text_input("Nombre Completo")
            new_phone = c1.text_input("Teléfono (Ej: 57300...)", placeholder="573001234567")
            
            existing_groups = [r[0] for r in session.query(Member.group).distinct().all() if r[0]]
            new_group = c2.selectbox("Grupo", existing_groups + ["Nuevo..."])
            new_start_month = c2.selectbox("Mes de Inicio (Inscripción)", months_order)
            
            submit_member = st.form_submit_button("Guardar Atleta")
            
            if submit_member and new_name:
                # Check duplicate
                exists = session.query(Member).filter(Member.name == new_name.upper()).first()
                if exists:
                    st.error("Ya existe un atleta con este nombre.")
                else:
                    m = Member(name=new_name.upper(), group=new_group, phone=new_phone, start_month=new_start_month, active=True)
                    session.add(m)
                    session.commit()
                    st.success(f"Atleta {new_name} creado exitosamente. Obligaciones inician en {new_start_month}.")
                    session.close(); st.rerun()

    st.markdown("---")
    
    # 2. Manage Members (Edit Status)
    st.subheader("Directorio de Atletas")
    search = st.text_input("Buscar socio...", "")
    
    query = session.query(Member)
    if search:
        query = query.filter(Member.name.ilike(f"%{search}%"))
        
    members_list = query.order_by(Member.name).all()
    
    if members_list:
        # Prepare data with Visual Status
        data = {
            "ID": [m.id for m in members_list],
            "Estado Visual": ["✅ Activo" if m.active else "❌ Inactivo" for m in members_list], # NEW
            "Nombre": [m.name for m in members_list],
            "Teléfono": [m.phone for m in members_list],
            "Grupo": [m.group for m in members_list],
            "Mes Inicio": [getattr(m, 'start_month', 'ENERO') for m in members_list],
            "Activo": [m.active for m in members_list] # Keep for editing
        }
        df_members = pd.DataFrame(data)
        
        # --- EDITOR ---
        st.markdown("### ✏️ Editor de Datos")
        st.caption("Agrega los teléfonos aquí para activar WhatsApp directo.")
        
        existing_groups = [r[0] for r in session.query(Member.group).distinct().all() if r[0]] 
        
        edited_df = st.data_editor(
            df_members,
            column_config={
                "ID": st.column_config.NumberColumn(disabled=True),
                "Estado Visual": st.column_config.TextColumn("Estado", disabled=True), # Read-only Visual
                "Nombre": st.column_config.TextColumn(disabled=True),
                "Teléfono": st.column_config.TextColumn("WhatsApp (Ej: 57...)", required=False),
                "Grupo": st.column_config.SelectboxColumn("Grupo", options=existing_groups, required=True),
                "Mes Inicio": st.column_config.SelectboxColumn("Inicio (Cobros)", options=months_order, required=True),
                "Activo": st.column_config.CheckboxColumn("¿Activo?", help="Desmarcar para inhabilitar")
            },
            hide_index=True,
            use_container_width=True,
            key="member_editor_main"
        )
        
        if st.button("Guardar Cambios en Directorio"):
            # Update loop
            for index, row in edited_df.iterrows():
                m_id = row["ID"]
                m_active = row["Activo"]
                m_group = row["Grupo"]
                m_phone = row["Teléfono"]
                m_start_month = row["Mes Inicio"]
                
                # Fetch and update
                m_obj = session.query(Member).filter(Member.id == m_id).first()
                if m_obj:
                    old_start = getattr(m_obj, 'start_month', 'ENERO')
                    # Update if changed
                    if (m_obj.active != m_active) or (m_obj.group != m_group) or (m_obj.phone != m_phone) or (old_start != m_start_month):
                        m_obj.active = m_active
                        m_obj.group = m_group
                        m_obj.phone = m_phone
                        m_obj.start_month = m_start_month
            
            session.commit()
            st.success("Cambios actualizados correctamente.")
            session.close(); st.rerun()
            
    else:
        st.info("No se encontraron socios.")
        
    session.close()

# --- PAGE: NOVEDADES/PAGOS ---
elif page == "Novedades/Pagos":
    st.header("💰 Registrar Pago")
    
    session = SessionLocal()
    
    col1, col2 = st.columns(2)
    
    with col1:
        members = session.query(Member).filter(Member.active == True).order_by(Member.name).all()
        member_names = ["+ Agregar Nuevo Socio..."] + [m.name for m in members]
        selected_member_ui = st.selectbox("Seleccionar Socio", member_names)
        
        new_member_group = None
        new_member_start_month = "ENERO" # Default
        if selected_member_ui == "+ Agregar Nuevo Socio...":
            final_member_name = st.text_input("Escribe el nombre del nuevo deportista:").strip().upper()
            new_member_group = st.selectbox("Grupo al que ingresa:", ["Aprendizaje", "Alejandro", "Carlos"])
            new_member_start_month = st.selectbox("Mes de Inicio (Inscripción):", ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"])
        else:
            final_member_name = selected_member_ui
    
    with col2:
        tarifas = [300000, 315000, 350000, 400000, 405000, 450000, 530000, 1000000, 1320000, 1500000, 1900000, 2280000, 2600000, 3600000, 5000000, 5500000, "Otro Monto..."]
        amount_sel = st.selectbox("Monto", tarifas, format_func=lambda x: f"${x:,.0f}" if isinstance(x, int) else x)
        if amount_sel == "Otro Monto...":
            amount = st.number_input("Monto Manual", min_value=0, step=1000, value=150000)
        else:
            amount = amount_sel
    
    col3, col4 = st.columns(2)
    with col3:
        # Month List for Indexing
        months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                       "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        
        # Filter logic to prevent duplicate month entries AND enforce start month
        paid_months = []
        member_start_idx = 0
        if final_member_name and selected_member_ui != "+ Agregar Nuevo Socio...":
            try:
                mem_obj_temp = session.query(Member).filter(Member.name == final_member_name).first()
                if mem_obj_temp:
                    # Enforce start month
                    m_start = getattr(mem_obj_temp, 'start_month', 'ENERO')
                    if m_start in months_list:
                        member_start_idx = months_list.index(m_start)
                        
                    paid_txs = session.query(Transaction.month).filter(
                        Transaction.member_id == mem_obj_temp.id,
                        Transaction.year == 2026, # Current operational year
                        Transaction.status == 'PAID'
                    ).all()
                    paid_months = [m[0] for m in paid_txs]
            except Exception:
                pass # Safe fallback
        elif final_member_name:
            member_start_idx = months_list.index(new_member_start_month) if new_member_start_month in months_list else 0
        
        valid_months = months_list[member_start_idx:]
        available_months = [m for m in valid_months if m not in paid_months]
        
        if not available_months:
            st.warning("Este socio ya completó todos sus pagos de 2026.")
            start_month = None
        else:
            start_month = st.selectbox("Mes de Inicio (Disponibles)", available_months)
        
    with col4:
        # Payment Type / Frequency
        package_type = st.selectbox("Tipo de Pago", ["Mensual", "Trimestral (3 Meses)", "Semestral (6 Meses)", "Anual (12 Meses)"])
        
    c5, c6 = st.columns(2)
    with c5:
        # Status is mostly PAID for packages, but let's keep it flexible
        status = st.selectbox("Estado", ["PAID", "PENDING"])
    with c6:
        # Bank Account routing
        received_by = st.selectbox("Cuenta Destino (Ingresó a)", ["Carlos", "Alejandro", "Efectivo/Caja"])
        
    if st.button("Registrar Transacción", type="primary"):
        if final_member_name and start_month:
            mem_obj = session.query(Member).filter(Member.name == final_member_name).first()
            if not mem_obj:
                mem_obj = Member(name=final_member_name, group=new_member_group, active=True, phone="", start_month=new_member_start_month)
                session.add(mem_obj)
                session.commit()
                session.refresh(mem_obj)
            
            # Determine Duration
            duration = 1
            if "Trimestral" in package_type: duration = 3
            elif "Semestral" in package_type: duration = 6
            elif "Anual" in package_type: duration = 12
            
            # Start Index
            start_idx = months_list.index(start_month)
            start_year = 2026 # Default context
            
            # Loop for creation
            for i in range(duration):
                # Calculate current month/year for this iteration
                current_month_idx = (start_idx + i) % 12
                # Year increments if we cross December
                year_offset = (start_idx + i) // 12
                current_year = start_year + year_offset
                
                current_month_name = months_list[current_month_idx]
                
                # Logic: First month gets the Full Amount (Cash Basis).
                # Subsequent months get $0 but are marked PAID to avoid "Debtor" flag.
                current_amount = amount if i == 0 else 0
                
                new_tx = Transaction(
                    member_id=mem_obj.id,
                    month=current_month_name,
                    year=current_year,
                    amount=current_amount,
                    status=status,
                    period=f"{current_year}-{current_month_name[:3]}",
                    received_by=received_by if i == 0 else "" # Solo la cuota 1 trae la plata real al banco
                )
                session.add(new_tx)
            
            session.commit()
            if duration > 1:
                st.success(f"¡Paquete registrado! Se crearon {duration} pagos (Mes 1: ${amount:,.0f}, Resto: $0 cubierto).")
            else:
                st.success("Pago registrado exitosamente!")
            
    # --- HISTORIAL Y ANULACIÓN ---
    st.markdown("---")
    st.subheader("📜 Historial de Pagos Recientes")
    
    # Get recent TXs
    # Joined with Member to see names
    try:
        recent_txs = session.query(Transaction).join(Member).order_by(Transaction.id.desc()).limit(20).all()
    except Exception as e_hist:
        session.rollback()
        st.error("⚠️ Actualización Requerida: El banco de datos necesita la nueva columna 'Cuenta Destino'.")
        if st.button("🛠️ FORZAR ACTUALIZACIÓN DE TABLAS", key="fix_hist_pagos"):
            try:
                force_schema_update(engine)
                st.success("✅ Tablas reparadas. ¡Recarga la página (F5)!")
                import time
                time.sleep(1)
                session.close(); st.rerun()
            except Exception as e_mig:
                st.error(f"Error forzando actualización: {e_mig}")
        recent_txs = []
    
    if recent_txs:
        # Table data
        hist_data = [{"ID": t.id, "Fecha": t.created_at, "Socio": t.member.name, "Mes": t.month, "Monto": f"${t.amount:,.0f}", "Cuenta Destino": t.received_by, "Estado": t.status} 
                     for t in recent_txs]
        st.dataframe(hist_data, use_container_width=True)
        
        # Delete Interface
        with st.expander("🗑️ Anular/Eliminar un Pago Incorrecto"):
            st.warning("Esta acción borrará el pago de la base de datos permanentemente.")
            # Dropdown is safer than ID input
            # Format: "ID: [123] - Juan Perez - $50.000"
            options = {f"ID: {t.id} - {t.member.name} - ${t.amount:,.0f} ({t.month})": t.id for t in recent_txs}
            selected_option = st.selectbox("Seleccionar Pago a Eliminar", list(options.keys()))
            
            if st.button("Eliminar Pago Seleccionado", type="primary"):
                tx_id_to_delete = options[selected_option]
                tx_to_del = session.query(Transaction).filter(Transaction.id == tx_id_to_delete).first()
                if tx_to_del:
                    session.delete(tx_to_del)
                    session.commit()
                    st.success(f"Pago {tx_id_to_delete} eliminado.")
                    session.close(); st.rerun()
                else:
                    st.error("No se encontró el pago.")
    else:
        st.info("No hay pagos registrados aún.")
            
    session.close()

# --- PAGE: GASTOS ---
elif page == "Gastos":
    from database import Expense
    st.header("📉 Gestión de Gastos")
    
    session = SessionLocal()
    
    with st.form("add_expense"):
        st.subheader("Nuevo Gasto")
        c1, c2 = st.columns(2)
        desc = c1.text_input("Descripción", placeholder="Ej. Pago Piscina")
        amt = c2.number_input("Monto", min_value=0, step=1000)
        c3, c4 = st.columns(2)
        cat = c3.selectbox("Categoría", ["Operativo", "Honorarios", "Alquiler", "Mantenimiento", "Otros"])
        
        # New Field: Paid By
        payer = c4.selectbox("Pagado Por", ["AlphaX (Caja)", "Carlos", "Alejandro"])
        
        date_exp = st.date_input("Fecha", value=datetime.today())
        
        submitted = st.form_submit_button("Registrar Gasto")
        if submitted and desc and amt > 0:
            # Save new field
            exp = Expense(description=desc, amount=amt, category=cat, date=date_exp, paid_by=payer)
            session.add(exp)
            session.commit()
            st.success("Gasto guardado.")
            session.close(); st.rerun()
            
    st.markdown("---")
    st.subheader("Historial de Gastos")
    
    expenses = session.query(Expense).order_by(Expense.date.desc()).limit(50).all()
    if expenses:
        # Include Paid By in table
        data = [{"ID": e.id, "Fecha": e.date, "Descripción": e.description, "Categoría": e.category, "Monto": f"${e.amount:,.0f}", "Pagado Por": e.paid_by} for e in expenses]
        st.dataframe(data, use_container_width=True)
        
        # Selective Deletion for Expenses
        with st.expander("🗑️ Eliminar un Gasto"):
            st.warning("Esta acción borrará el gasto seleccionado.")
            options = {f"ID: {e.id} - {e.description} - ${e.amount:,.0f} ({e.date})": e.id for e in expenses}
            selected_option = st.selectbox("Seleccionar Gasto a Eliminar", list(options.keys()))
            
            if st.button("Eliminar Gasto Seleccionado", type="primary"):
                exp_id_to_delete = options[selected_option]
                exp_to_del = session.query(Expense).filter(Expense.id == exp_id_to_delete).first()
                if exp_to_del:
                    session.delete(exp_to_del)
                    session.commit()
                    st.success(f"Gasto {exp_id_to_delete} eliminado.")
                    session.close(); st.rerun()
                else:
                    st.error("No se encontró el gasto.")
    else:
        st.info("No hay gastos registrados.")
        
    session.close()

# --- PAGE: CONFIG ---
elif page == "Configuración":
    st.header("⚙️ Configuración")
    st.write("Versión 1.2 - AlphaX CRM (Con Soporte WhatsApp)")
    
    if st.button("⚠️ BORRAR BASE DE DATOS (RESETEO TOTAL)"):
        session = SessionLocal()
        try:
            from sqlalchemy import text
            # HARD RESET: Drop tables to force schema rebuild (Fixes BigInt vs Boolean issues)
            session.execute(text("DROP TABLE IF EXISTS transactions CASCADE;"))
            session.execute(text("DROP TABLE IF EXISTS expenses CASCADE;"))
            session.execute(text("DROP TABLE IF EXISTS members CASCADE;"))
            session.commit()
            st.warning("Tablas eliminadas...")
            
            # Re-init DB to create tables with correct schema
            from database import init_db
            init_db()
            st.success("♻️ Base de datos RECONSTRUIDA y lista. (Schema Fixed)")
            
        except Exception as e:
            st.error(f"Error reseteando: {e}")
        finally:
            session.close()
            
    with st.expander("🧹 Limpiar Contabilidad (Mantener Atletas)"):
        st.warning("Esta opción borrará todos los **Pagos** y **Gastos** registrados, pero mantendrá intacta tu lista de Socios, Grupos y Teléfonos.")
        if st.button("BORRAR SÓLO PAGOS Y GASTOS", type="primary"):
            session = SessionLocal()
            try:
                from sqlalchemy import text
                # Delete records from transactions and expenses, keep tables
                session.execute(text("DELETE FROM transactions;"))
                session.execute(text("DELETE FROM expenses;"))
                session.commit()
                st.success("✅ Historial de contabilidad en $0. ¡Los atletas siguen a salvo!")
            except Exception as e_del:
                st.error(f"Error limpiando contabilidad: {e_del}")
            finally:
                session.close()
        
    st.markdown("---")
    st.subheader("🔧 Herramientas de Mantenimiento")
    
    # Updated Migration Button for Phone & Expenses & Fixes
    if st.button("🛠️ ACTUALIZAR DB (Agregar campos nuevos generales)"):
        try:
            force_schema_update(engine)
            st.success("✅ ¡Base de datos actualizada y REPARADA! (Columnas y tipos corregidos).")
            import time
            time.sleep(1)
            session.close(); st.rerun() # Auto-refresh to show fixes immediately
        except Exception as e:
            st.error(f"Error en migración: {e}")

    # Secrets Revealer for Bot
    st.markdown("---")
    with st.expander("🔌 Conexión para el Bot Local"):
        st.info("Copia esta dirección y pégala cuando el Bot te la pida (en la ventana negra).")
        
        db_url = None
        if "DATABASE_URL" in st.secrets:
             db_url = st.secrets["DATABASE_URL"]
        elif "DATABASE_URL" in os.environ:
             db_url = os.environ["DATABASE_URL"]
        
        if db_url:
             st.code(db_url, language="bash")
        else:
             st.error("No se encontró DATABASE_URL configurada en esta App.")

    # New Download Button Section (SQLite)
    st.markdown("---")
    st.subheader("💾 Copia de Seguridad (SQLite Local)")
    st.info("Si tu App está usando una memoria temporal (sin Secretos), descarga tus datos aquí para usarlos con el Bot.")
    
    db_file_path = os.path.join("data", "club_crm.db")
    if os.path.exists(db_file_path):
        with open(db_file_path, "rb") as f:
            st.download_button(
                label="📥 Descargar Base de Datos (club_crm.db)",
                data=f,
                file_name="club_crm.db",
                mime="application/octet-stream"
            )
    else:
        st.warning("⚠️ No se encontró archivo de base de datos local (club_crm.db).")

    # Excel Export Button (Universal)
    st.markdown("---")
    st.subheader("📊 Exportar Datos a Excel (Backup Completo)")
    st.info("Genera una copia de seguridad en Excel con todas tus tablas: Socios, Pagos y Gastos.")
    
    if st.button("📥 Generar Backup en Excel"):
        try:
            # Create a BytesIO buffer
            from io import BytesIO
            import pandas as pd
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                # Get DataFrames
                # Use current engine (Postgres or SQLite)
                df_members = pd.read_sql("SELECT * FROM members", engine)
                df_transactions = pd.read_sql("SELECT * FROM transactions", engine)
                df_expenses = pd.read_sql("SELECT * FROM expenses", engine)
                
                # Write Sheets
                df_members_out = df_members.rename(columns={
                    "id": "ID", "name": "Nombre", "group": "Grupo", 
                    "active": "Activo", "created_at": "Fecha Registro", "phone": "Teléfono"
                })
                # Drop unnecessary columns if they exist
                if "created_at" in df_members_out.columns: df_members_out["Fecha Registro"] = pd.to_datetime(df_members_out["Fecha Registro"]).dt.tz_localize(None)

                # Build a dictionary to map IDs to Names
                id_to_name = dict(zip(df_members["id"], df_members["name"]))
                df_transactions["Atleta"] = df_transactions["member_id"].map(id_to_name).fillna("Desconocido")
                
                df_transactions_out = df_transactions.rename(columns={
                    "id": "ID", "period": "Periodo", 
                    "year": "Año", "month": "Mes", "amount": "Monto", 
                    "status": "Estado", "payment_date": "Fecha Pago", 
                    "created_at": "Fecha Registro", "received_by": "Cuenta Destino"
                })
                
                # Enforce a clean, human-readable column order
                wanted_tx_cols = ["ID", "Atleta", "Periodo", "Año", "Mes", "Monto", "Estado", "Cuenta Destino", "Fecha Pago", "Fecha Registro"]
                valid_tx_cols = [c for c in wanted_tx_cols if c in df_transactions_out.columns]
                df_transactions_out = df_transactions_out[valid_tx_cols]
                
                if "Fecha Registro" in df_transactions_out.columns: df_transactions_out["Fecha Registro"] = pd.to_datetime(df_transactions_out["Fecha Registro"]).dt.tz_localize(None)
                if "Fecha Pago" in df_transactions_out.columns: df_transactions_out["Fecha Pago"] = pd.to_datetime(df_transactions_out["Fecha Pago"]).dt.tz_localize(None)

                df_expenses_out = df_expenses.rename(columns={
                    "id": "ID", "description": "Descripción", "amount": "Monto", 
                    "date": "Fecha", "category": "Categoría", "created_at": "Fecha Registro",
                    "paid_by": "Pagado Por"
                })
                if "Fecha" in df_expenses_out.columns: df_expenses_out["Fecha"] = pd.to_datetime(df_expenses_out["Fecha"]).dt.tz_localize(None)
                if "Fecha Registro" in df_expenses_out.columns: df_expenses_out["Fecha Registro"] = pd.to_datetime(df_expenses_out["Fecha Registro"]).dt.tz_localize(None)

                df_members_out.to_excel(writer, sheet_name='Socios', index=False)
                df_transactions_out.to_excel(writer, sheet_name='Pagos', index=False)
                df_expenses_out.to_excel(writer, sheet_name='Gastos', index=False)
                
            output.seek(0)
            
            st.download_button(
                label="⬇️ Descargar Backup Excel (.xlsx)",
                data=output,
                file_name=f"alphax_backup_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success("✅ Archivo generado. ¡Descárgalo arriba!")
            
        except Exception as e:
            st.error(f"Error generando Excel: {e}")

# --- PAGE: ASSQ ---
elif page == "ASSQ (Sueño)":
    # --- VARIABLES DE CONFIGURACIÓN ---
    N8N_WEBHOOK_URL = "TU_URL_DE_WEBHOOK_N8N_AQUI"
    
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
    st.info("Coach: AlphaX Training Team", icon="📋")

    with st.form("form_assq", clear_on_submit=True):
        
        # 1. Identificación
        st.subheader("👤 Identificación")
        atleta = st.selectbox("Selecciona al atleta:", ["-- Selecciona el nombre --"] + atletas_nombres)
        
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
        if atleta == "-- Selecciona el nombre --":
            st.warning("⚠️ Por favor, selecciona un nombre antes de enviar el formulario.")
        else:
            with st.spinner('Calculando métricas y enviando reporte...'):
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

                # 3. Construcción del Payload para el CRM (vía n8n)
                payload = {
                    "fecha_registro": datetime.now().isoformat(),
                    "atleta": atleta,
                    "score_sds": sds_score,
                    "categoria_clinica": categoria,
                    "respuestas_crudas": {
                        "horas_sueno": horas,
                        "calidad": calidad,
                        "latencia": latencia,
                        "despertares": despertares
                    }
                }
                
                # 4. Transmisión a n8n
                try:
                    if N8N_WEBHOOK_URL == "TU_URL_DE_WEBHOOK_N8N_AQUI":
                        st.success(f"✅ ¡Gracias! El reporte de {atleta} ha sido procesado (Modo Prueba).")
                        with st.expander("Ver data que llegará a tu webhook"):
                            st.json(payload)
                    else:
                        response = requests.post(N8N_WEBHOOK_URL, json=payload)
                        if response.status_code == 200:
                            st.success("✅ ¡Reporte enviado exitosamente!")
                        else:
                            st.error(f"⚠️ Hubo un error de comunicación con el servidor. Código: {response.status_code}")
                except Exception as e:
                    st.error(f"Error técnico de conexión: {e}")
