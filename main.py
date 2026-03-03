
from datetime import datetime
import os
import streamlit as st
import pandas as pd

try:
    from database import init_db, SessionLocal, Member, Transaction, Expense, engine
    from logic import import_excel_data, get_summary_kpis, update_member_phones
except Exception as e:
    st.error(f"💀 Error CRÍTICO de Importación: {e}")
    st.stop()
import plotly.express as px

# Configuración de página
st.set_page_config(
    page_title="AlphaX CRM",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos personalizados (CSS Hack para branding)
st.markdown("""
    <style>
    /* Import Nunito Sans */
    @import url('https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700&display=swap');

    /* GLOBAL RESET: Dark Mode Standard */
    html, body, [class*="css"], .stApp {
        font-family: 'Nunito Sans', sans-serif;
        color: #FFFFFF !important;
        background-color: #0e1117 !important;
    }

    /* TEXT VISIBILITY: Force all main text to White */
    p, h1, h2, h3, h4, h5, h6, li, span, div, label, .stMarkdown, .stText {
        color: #FFFFFF !important;
    }
    
    /* INPUTS: Force WHITE TEXT (to match Dark Background) */
    input, textarea, select, .stTextInput > div > div, .stNumberInput > div > div {
        color: #FFFFFF !important;
        background-color: transparent !important; /* Let Streamlit Dark BG show */
        -webkit-text-fill-color: #FFFFFF !important;
        caret-color: #33C1FF;
    }

    /* SELECTBOX / DROPDOWNS - The tricky part */
    div[data-baseweb="select"] > div {
        background-color: #262730 !important; /* Dark Grey */
        color: #FFFFFF !important;
        border-color: #33C1FF !important;
    }
    /* The selected value text */
    div[data-testid="stSelectbox"] div[data-baseweb="select"] div {
        color: #FFFFFF !important;
    }
    
    /* DROPDOWN MENU OPTIONS (When opened) */
    div[data-baseweb="menu"], div[role="listbox"] {
        background-color: #262730 !important;
    }
    div[role="option"] {
        color: #FFFFFF !important;
        background-color: #262730 !important;
    }
    /* Highlighted option */
    div[role="option"]:hover, div[role="option"][aria-selected="true"] {
        background-color: #33C1FF !important;
        color: #000000 !important; /* Black text on Blue highlight */
    }

    /* PLACEHOLDERS */
    ::placeholder {
        color: #CCCCCC !important; /* Light Grey */
        opacity: 1;
    }
    
    /* FILE UPLOADER */
    div[data-testid="stFileUploader"] {
        color: #FFFFFF !important;
    }
    div[data-testid="stFileUploader"] section {
        background-color: #262730 !important;
    }

    /* DATAFRAME FIXES */
    div[data-testid="stDataFrame"] {
        background-color: #262730;
    }

    /* HEADERS & ACCENTS */
    h1, h2, h3, .stMetricLabel {
        color: #33C1FF !important; 
    }

    /* BUTTONS */
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        border: 1px solid #33C1FF;
        color: #FFFFFF !important;
        background-color: #262730 !important; /* Dark Button */
    }
    .stButton > button:hover {
        background-color: #33C1FF !important;
        color: #000000 !important;
        border-color: #33C1FF;
    }
    </style>
    """, unsafe_allow_html=True)

# Inicializar DB
init_db()

# Sidebar
with st.sidebar:
    # Try multiple logo variations from the new folder
    logo_dir = "LOGOS " # Note the space
    # Prioritize Aqua/White logos as requested
    logo_candidates = ["ALPHAX-SIMBOLO-AGUA-M.png", "ALPHAX-TEXTO-BLANCO.png", "LOGO-ALPHAX1 BLUE212.png", "logo.png"]
    
    current_logo = None
    for logo in logo_candidates:
        if os.path.exists(os.path.join(logo_dir, logo)):
            current_logo = os.path.join(logo_dir, logo)
            break
        elif os.path.exists(logo): # Root fallback
            current_logo = logo
            break
            
    if current_logo:
        st.image(current_logo, use_container_width=True)
    
    st.markdown("<h2 style='text-align: center; color: #33C1FF; margin-top: 10px;'>ALPHAX TEAM ADMIN</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    page = st.radio("Navegación", ["Dashboard", "Socios", "Novedades/Pagos", "Gastos", "Configuración"])


    
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
    txs = tx_query.filter(Transaction.status == 'PAID').all()
    total_income = sum(t.amount for t in txs)
    
    # Calculate Expenses
    try:
        expense_query = session.query(Expense)
        # If filtering by group, we assume expenses are GLOBAL for now unless we add group to expenses too.
        # For now, let's keep expenses Global.
        total_expenses = sum(e.amount for e in expense_query.all())
    except Exception as e:
        # Schema Error Catch - Schema Migration Handler
        st.error(f"⚠️ Actualización Requerida: {e}")
        st.info("Esto es normal por la nueva función de 'Control de Saldos'.")
        
        st.markdown("---")
        st.subheader("🔧 Herramientas de Mantenimiento")
        
        if st.button("🛠️ ACTUALIZAR DB (Agregar campos: Teléfono y Control Saldos)"):
            try:
                from sqlalchemy import text
                with engine.connect() as conn:
                    # 1. Add Paid By to Expenses
                    try:
                        conn.execute(text("ALTER TABLE expenses ADD COLUMN paid_by VARCHAR;"))
                    except:
                        pass # Probably exists
                    
                    # 2. Add Phone to Members
                    try:
                        conn.execute(text("ALTER TABLE members ADD COLUMN phone VARCHAR;"))
                    except:
                        pass # Probably exists
                        
                    conn.commit()
                st.success("✅ ¡Base de datos actualizada! Ahora puedes registrar teléfonos.")
            except Exception as e:
                st.error(f"Error en migración: {e}")
        total_expenses = 0 # Fallback
    
    net_profit = total_income - total_expenses
    
    try:
        member_count = member_query.count()
    except Exception as e_mem:
        st.error(f"⚠️ Necesitas actualizar la base de datos para usar WhatsApp.")
        member_count = 0
        if st.button("🛠️ TOCAR AQUÍ PARA HABILITAR WHATSAPP", type="primary", key="fix_members"):
             try:
                from sqlalchemy import text
                with engine.connect() as conn:
                    # 1. Add Phone to Members
                    try:
                        conn.execute(text("ALTER TABLE members ADD COLUMN phone VARCHAR;"))
                    except:
                        pass # Probably exists
                        
                    # 2. Ensure Paid By to Expenses exists too
                    try:
                        conn.execute(text("ALTER TABLE expenses ADD COLUMN paid_by VARCHAR;"))
                    except:
                        pass 
                        
                    conn.commit()
                st.success("✅ ¡Actualizado! Recargando...")
                st.rerun()
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
    
    # 1. Preparar Datos de Ingresos
    data_rev = []
    for t in txs:
        data_rev.append({"month": t.month, "amount": t.amount, "type": "Ingreso"})
    df_rev = pd.DataFrame(data_rev)
    if not df_rev.empty:
        df_rev['month'] = pd.Categorical(df_rev['month'], categories=months_order, ordered=True)
        df_rev_grouped = df_rev.groupby("month", observed=False)["amount"].sum().reset_index()
    else:
        df_rev_grouped = pd.DataFrame({"month": months_order, "amount": [0]*12})
        df_rev_grouped['month'] = pd.Categorical(df_rev_grouped['month'], categories=months_order, ordered=True)

    # 2. Preparar Datos de Gastos
    data_exp = []
    try:
        all_expenses = expense_query.all()
        for e in all_expenses:
            if hasattr(e, 'date') and e.date:
                m_str = months_order[e.date.month - 1]
            else:
                m_str = "SIN MES"
            paid_by_val = getattr(e, 'paid_by', 'AlphaX (Caja)')
            data_exp.append({
                "month": m_str,
                "Category": e.category or "General", 
                "Amount": e.amount, 
                "Pagado Por": paid_by_val,
                "Descripción": e.description
            })
        df_exp = pd.DataFrame(data_exp)
    except Exception as e:
        session.rollback()
        df_exp = pd.DataFrame()
        st.warning(f"⚠️ Error cargando gastos: {e}")

    if not df_exp.empty:
        df_exp['month'] = pd.Categorical(df_exp['month'], categories=months_order, ordered=True)
        df_exp_grouped = df_exp.groupby("month", observed=False)["Amount"].sum().reset_index()
        df_exp_cat = df_exp.groupby(["month", "Category"], observed=False)["Amount"].sum().reset_index()
        # Filtramos ceros para el gráfico de barras apiladas
        df_exp_cat = df_exp_cat[df_exp_cat["Amount"] > 0]
    else:
        df_exp_grouped = pd.DataFrame({"month": months_order, "Amount": [0]*12})
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
        
        # 3. Diff
        pending_ids = active_ids - paid_ids
        pending_members = [m for m in active_members if m.id in pending_ids]
        
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
                            wa_link = f"https://wa.me/{phone_number}?text={encoded_msg}"
                        else:
                             # Fallback if no phone
                             wa_link = f"https://wa.me/?text={encoded_msg}"

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
    
    session.close()

# --- PAGE: SOCIOS ---
elif page == "Socios":
    st.header("👥 Gestión de Socios")
    session = SessionLocal()

    # 1. Add New Member
    with st.expander("➕ Registrar Nuevo Atleta"):
        with st.form("new_member"):
            c1, c2 = st.columns(2)
            new_name = c1.text_input("Nombre Completo")
            new_phone = c1.text_input("Teléfono (Ej: 57300...)", placeholder="573001234567")
            
            existing_groups = [r[0] for r in session.query(Member.group).distinct().all() if r[0]]
            new_group = c2.selectbox("Grupo", existing_groups + ["Nuevo..."])
            
            submit_member = st.form_submit_button("Guardar Atleta")
            
            if submit_member and new_name:
                # Check duplicate
                exists = session.query(Member).filter(Member.name == new_name.upper()).first()
                if exists:
                    st.error("Ya existe un atleta con este nombre.")
                else:
                    m = Member(name=new_name.upper(), group=new_group, phone=new_phone, active=True)
                    session.add(m)
                    session.commit()
                    st.success(f"Atleta {new_name} creado exitosamente.")
                    st.rerun()

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
                
                # Fetch and update
                m_obj = session.query(Member).filter(Member.id == m_id).first()
                if m_obj:
                    # Update if changed
                    if (m_obj.active != m_active) or (m_obj.group != m_group) or (m_obj.phone != m_phone):
                        m_obj.active = m_active
                        m_obj.group = m_group
                        m_obj.phone = m_phone
            
            session.commit()
            st.success("Cambios actualizados correctamente.")
            st.rerun()
            
    else:
        st.info("No se encontraron socios.")
        
    session.close()

# --- PAGE: NOVEDADES/PAGOS ---
elif page == "Novedades/Pagos":
    st.header("💰 Registrar Pago")
    
    session = SessionLocal()
    
    col1, col2 = st.columns(2)
    
    with col1:
        members = session.query(Member).filter(Member.active == True).all()
        member_names = [m.name for m in members]
        selected_member = st.selectbox("Seleccionar Socio", member_names)
    
    with col2:
        amount = st.number_input("Monto", min_value=0, step=1000, value=150000)
    
    col3, col4 = st.columns(2)
    with col3:
        # Month List for Indexing
        months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                       "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        start_month = st.selectbox("Mes de Inicio", months_list)
        
    with col4:
        # Payment Type / Frequency
        package_type = st.selectbox("Tipo de Pago", ["Mensual", "Trimestral (3 Meses)", "Semestral (6 Meses)", "Anual (12 Meses)"])
        
    # Status is mostly PAID for packages, but let's keep it flexible
    status = st.selectbox("Estado", ["PAID", "PENDING"])
        
    if st.button("Registrar Transacción", type="primary"):
        if selected_member:
            mem_obj = session.query(Member).filter(Member.name == selected_member).first()
            
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
                    period=f"{current_year}-{current_month_name[:3]}"
                )
                session.add(new_tx)
            
            session.commit()
            if duration > 1:
                st.success(f"¡Paquete registrado! Se crearon {duration} pagos (Mes 1: ${amount:,.0f}, Resto: $0 cubierto).")
            else:
                st.success("Pago registrado exitosamente!")
            
            session.add(new_tx)
            session.commit()
            st.success("Pago registrado exitosamente!")
            
    # --- HISTORIAL Y ANULACIÓN ---
    st.markdown("---")
    st.subheader("📜 Historial de Pagos Recientes")
    
    # Get recent TXs
    # Joined with Member to see names
    recent_txs = session.query(Transaction).join(Member).order_by(Transaction.id.desc()).limit(20).all()
    
    if recent_txs:
        # Table data
        hist_data = [{"ID": t.id, "Fecha": t.created_at, "Socio": t.member.name, "Mes": t.month, "Monto": f"${t.amount:,.0f}", "Estado": t.status} 
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
                    st.rerun()
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
            st.rerun()
            
    st.markdown("---")
    st.subheader("Historial de Gastos")
    
    expenses = session.query(Expense).order_by(Expense.date.desc()).all()
    if expenses:
        # Include Paid By in table
        data = [{"ID": e.id, "Fecha": e.date, "Descripción": e.description, "Categoría": e.category, "Monto": f"${e.amount:,.0f}", "Pagado Por": e.paid_by} for e in expenses]
        st.dataframe(data, use_container_width=True)
        
        if st.button("Borrar Último Gasto"):
             last = expenses[0]
             session.delete(last)
             session.commit()
             st.rerun()
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
        
    st.markdown("---")
    st.subheader("🔧 Herramientas de Mantenimiento")
    
    # Updated Migration Button for Phone & Expenses & Fixes
    if st.button("🛠️ ACTUALIZAR DB (Agregar campos: Teléfono y Control Saldos)"):
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                # 1. Add Paid By to Expenses
                try:
                    conn.execute(text("ALTER TABLE expenses ADD COLUMN paid_by VARCHAR;"))
                except:
                    pass # Probably exists
                
                # 2. Add Phone to Members
                try:
                    conn.execute(text("ALTER TABLE members ADD COLUMN phone VARCHAR;"))
                except:
                    pass # Probably exists
                    
                # 3. FIX: Convert 'active' to BOOLEAN (Crucial for Dashboard)
                try:
                    # Force conversion from BigInt/Integer to Boolean causes: operator does not exist: bigint = boolean
                    conn.execute(text("ALTER TABLE members ALTER COLUMN active DROP DEFAULT;")) 
                    # Robust conversion: 1 -> true, 0 -> false
                    conn.execute(text("ALTER TABLE members ALTER COLUMN active TYPE BOOLEAN USING (active::integer <> 0);"))
                    conn.execute(text("ALTER TABLE members ALTER COLUMN active SET DEFAULT true;"))
                except Exception as ex_bool:
                    # st.write(f"Nota debug: {ex_bool}")
                    pass 

                conn.commit()
            st.success("✅ ¡Base de datos actualizada y REPARADA! (Columnas y tipos corregidos).")
            import time
            time.sleep(1)
            st.rerun() # Auto-refresh to show fixes immediately
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
                df_members.to_excel(writer, sheet_name='Socios', index=False)
                df_transactions.to_excel(writer, sheet_name='Pagos', index=False)
                df_expenses.to_excel(writer, sheet_name='Gastos', index=False)
                
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

