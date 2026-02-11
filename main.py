
from datetime import datetime
import os
import streamlit as st
import pandas as pd
from database import init_db, SessionLocal, Member, Transaction, Expense, engine
from logic import import_excel_data, get_summary_kpis
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
    if uploaded_file:
        if st.button("Procesar Archivo"):
            with st.spinner("Importando datos..."):
                # Save temp to process
                temp_path = f"temp_{uploaded_file.name}"
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
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
    
    # Graphs
    # Get dataframe from filtered stats
    data_rev = []
    for t in txs:
        data_rev.append({"month": t.month, "amount": t.amount, "type": "Ingreso"})
    
    # Add expenses to graph? Or kept separate. Maybe specific graph for Balance.
    # Let's keep the Income graph by month as is, and add an Expense Breakdown?
    
    df_rev = pd.DataFrame(data_rev)
    
    if not df_rev.empty:
        months_order = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                        "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        
        df_rev['month'] = pd.Categorical(df_rev['month'], categories=months_order, ordered=True)
        df_grouped = df_rev.groupby("month")["amount"].sum().reset_index()
        
        # --- CHARTS ROW ---
        col_c1, col_c2 = st.columns(2)
        
        with col_c1:
            st.markdown("### 📈 Ingresos Mensuales")
            fig_rev = px.bar(
                df_grouped, 
                x="month", 
                y="amount", 
                # title="Ingresos Mensuales", 
                color_discrete_sequence=["#33C1FF"],
                text_auto='.2s'
            )
            fig_rev.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white")
            st.plotly_chart(fig_rev, use_container_width=True)
            
        with col_c2:
            st.markdown("### 📉 Desglose de Gastos")
            # Get Expenses Data
            all_expenses = expense_query.all()
            if all_expenses:
                # Prepare data for charts
                data_exp = [{"Category": e.category or "General", "Amount": e.amount, "Pagado Por": e.paid_by or "AlphaX (Caja)"} for e in all_expenses]
                df_exp = pd.DataFrame(data_exp)
                
                # Group by Category (Pie Chart)
                df_exp_grouped = df_exp.groupby("Category")["Amount"].sum().reset_index()
                
                fig_exp = px.pie(
                    df_exp_grouped, 
                    values="Amount", 
                    names="Category", 
                    color_discrete_sequence=px.colors.sequential.RdBu,
                    hole=0.4
                )
                fig_exp.update_traces(textposition='inside', textinfo='percent+label')
                fig_exp.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white", showlegend=False)
                st.plotly_chart(fig_exp, use_container_width=True)
                
                # NEW: Breakdown by Payer
                st.markdown("##### 👥 Control de Saldos")
                df_payer = df_exp.groupby("Pagado Por")["Amount"].sum().reset_index()
                
                fig_payer = px.bar(df_payer, x="Pagado Por", y="Amount", text_auto='.2s', color="Pagado Por", color_discrete_sequence=px.colors.qualitative.Pastel)
                fig_payer.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="white", showlegend=False)
                st.plotly_chart(fig_payer, use_container_width=True)
                
                # Optional: Show Detailed List expander
                with st.expander("Ver Detalle de Gastos"):
                    st.dataframe(df_exp, use_container_width=True)
            else:
                st.info("No hay gastos registrados aún.")
    
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
                    # Customizable Message
                    msg = f"Hola {m.name.title()}, te recordamos amablemente tu pago de la mensualidad de *{current_month_name}* en AlphaX. ¡Gracias!"
                    encoded_msg = urllib.parse.quote(msg)
                    
                    # Phone Logic
                    phone_number = m.phone if m.phone else ""
                    # Clean phone number (remove +, spaces)
                    if phone_number:
                        phone_number = "".join(filter(str.isdigit, phone_number))
                        wa_link = f"https://wa.me/{phone_number}?text={encoded_msg}"
                    else:
                        wa_link = f"https://wa.me/?text={encoded_msg}"
                    
                    dept_data.append({
                        "Nombre": m.name,
                        "Grupo": m.group,
                        "Estado": "Pendiente ⏳",
                        "Acción": wa_link
                    })
                
                # Display DataFrame with Link Column
                st.dataframe(
                    dept_data, 
                    column_config={
                        "Acción": st.column_config.LinkColumn(
                            "Notificar",
                            help="Abrir WhatsApp con mensaje pre-llenado",
                            display_text="📲 Enviar WhatsApp"
                        )
                    },
                    use_container_width=True
                )
                
                # Copy helper
                names_text = "\n".join([m.name for m in pending_members])
                st.text_area("Copiar lista de nombres (si prefieres usar lista de difusión):", value=names_text, height=100)
        else:
            st.success(f"¡Todos los socios ('{selected_group}') están al día en {current_month_name}! 🎉")
    
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
        # Prepare data
        data = {
            "ID": [m.id for m in members_list],
            "Nombre": [m.name for m in members_list],
            "Teléfono": [m.phone for m in members_list],
            "Grupo": [m.group for m in members_list],
            "Activo": [m.active for m in members_list]
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
    st.write("Versión 1.0 - AlphaX CRM")
    
    if st.button("Resetear Base de Datos (⚠️ Peligro)"):
        # Logic to clear tables
        session = SessionLocal()
        session.query(Transaction).delete()
        session.query(Member).delete()
        session.commit()
        session.close()
        st.warning("Base de datos limpiada.")
        
    st.markdown("---")
    st.subheader("🔧 Herramientas de Mantenimiento")
    
    if st.button("🛠️ Migrar DB (Agregar campo 'pagado_por')"):
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                # Postgres specific
                conn.execute(text("ALTER TABLE expenses ADD COLUMN IF NOT EXISTS paid_by VARCHAR;"))
                conn.commit()
            st.success("✅ Columna 'paid_by' agregada exitosamente. ¡Ya puedes usar los Gastos!")
        except Exception as e:
            st.error(f"Error en migración: {e}")

