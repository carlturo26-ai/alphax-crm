
import pandas as pd
from database import SessionLocal, Member, Transaction, init_db
from sqlalchemy.exc import IntegrityError
from datetime import datetime

# Force Reload Trigger (Debug)
print("DEBUG: Logic Module Loaded") 

def is_stop_word(name):
    """Returns True if the name suggests a calculation, expense or divider line."""
    if not isinstance(name, str):
        return True
    
    name = name.upper().strip()
    stop_words = [
        "TOTAL", "HONORARIOS", "SALDO", "ENTRO", "GASTOS", "PAGO", 
        "TRAININGPEAKS", "REDES", "OTROS", "CLASES", "VENTA", "ALPHAX", "GRAN TOTAL"
    ]
    
    for sw in stop_words:
        if name.startswith(sw) or sw in name:
            return True
    return False

def import_excel_data(file_path, sheets_to_import=None):
    init_db()
    session = SessionLocal()
    
    # Clear existing data to avoid duplicates/confusion during this "Reset" import
    # The user is essentially re-importing to fix the structure.
    # We will wipe the DB to ensure clean slate for groups.
    session.query(Transaction).delete()
    session.query(Member).delete()
    session.commit()
    
    if sheets_to_import is None:
        sheets_to_import = ["CARLOS 2026", "ALEJANDRO 2026"]
        
    total_new_members = 0
    total_txs = 0
    
    try:
        xls = pd.ExcelFile(file_path)
        available_sheets = xls.sheet_names
        
        for sheet_name in sheets_to_import:
            if sheet_name not in available_sheets:
                print(f"Skipping {sheet_name}")
                continue
                
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=3)
            df.columns = [str(c).strip().upper() for c in df.columns]
            
            known_months = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                            "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
            month_cols = [c for c in df.columns if c in known_months]
            
            # Identify Athlete col
            athlete_col = df.columns[0]
            if 'ATLETA' in df.columns: athlete_col = 'ATLETA'
            
            # --- Logic by Sheet ---
            if "CARLOS" in sheet_name:
                current_group = "Carlos"
            elif "ALEJANDRO" in sheet_name:
                current_group = "Alejandro"
            else:
                current_group = "General"
                
            # State machine for Alejandro's sheet splitting
            # State 0: Normal Athletes
            # State 1: Hit Divider (Honorarios/Etc), Waiting for block 2
            # State 2: Reading Block 2 (Aprendizaje)
            sheet_state = 0 
            
            for index, row in df.iterrows():
                raw_name = row[athlete_col]
                
                if pd.isna(raw_name) or str(raw_name).strip() == "":
                    continue
                    
                name_str = str(raw_name).strip()
                
                # Handling for ALEJANDRO special split
                if "ALEJANDRO" in sheet_name:
                    is_stop = is_stop_word(name_str)
                    
                    if sheet_state == 0:
                        if is_stop:
                            # We hit the first calculator block (HONORARIOS ALEJANDRO)
                            sheet_state = 1 # Waiting
                            continue
                        else:
                            # It's an Alejandro Athlete
                            current_group = "Alejandro"
                    
                    elif sheet_state == 1:
                        if not is_stop:
                            # Found a name after the calculation block! 
                            # This must be "Nadadores Aprendizaje" block
                            sheet_state = 2
                            current_group = "Aprendizaje"
                        else:
                            # Still in calculation rows
                            continue
                            
                    elif sheet_state == 2:
                        if is_stop:
                            # Hit "TOTAL", "CLASES", or "GASTOS" at bottom
                            # Stop reading
                            continue
                        else:
                            # Still in Aprendizaje
                            current_group = "Aprendizaje"
                
                # Handling for CARLOS
                elif "CARLOS" in sheet_name:
                    if is_stop_word(name_str):
                        continue # Skip totals/expenses
                
                # --- Create Member & Transaction ---
                # Check for "Total" row one last time just in case
                if "TOTAL" in name_str.upper():
                    continue

                athlete_name = name_str.upper()
                
                member = session.query(Member).filter(Member.name == athlete_name).first()
                if not member:
                    member = Member(name=athlete_name, group=current_group, active=True)
                    session.add(member)
                    session.commit()
                    total_new_members += 1
                else:
                    # Update group if needed (e.g. re-import)
                    if member.group != current_group:
                        member.group = current_group
                        session.commit()
                
                # Transactions
                for month in month_cols:
                    amount = row[month]
                    
                    # Filtering: Expenses are often negative or just labeled as such, 
                    # but here we rely on ignoring LIMIT rows. 
                    # We assume athlete rows only contain PAYMENTS (Positive).
                    
                    if pd.notna(amount) and isinstance(amount, (int, float)):
                        # Some formats might use negative for debt? 
                        # User said: "el ingreso que sea netamente de lo que entra"
                        # We take positive numbers.
                        if amount > 0:
                             tx = Transaction(
                                member_id=member.id,
                                month=month,
                                year=2026, # Hardcoded per user context (these are 2026 sheets)
                                period=f"2026-{month[:3]}",
                                amount=float(amount),
                                status="PAID",
                                payment_date=datetime.now()
                            )
                             session.add(tx)
                             total_txs += 1
                             
            session.commit()
            
        return f"Re-Importación completada: {total_new_members} socios, {total_txs} pagos. (Base de datos limpiada previo a carga)"
        
    except Exception as e:
        session.rollback()
        return f"Error: {str(e)}"
    finally:
        session.close()

def update_member_phones(file_path):
    """
    Reads an Excel file (likely a Backup or Custom list) and updates
    PHONE numbers for existing members.
    Does NOT delete or create members/transactions.
    """
    session = SessionLocal()
    updated_count = 0
    not_found_count = 0
    
    try:
        xls = pd.ExcelFile(file_path)
        # Try to find a "Socios" sheet first (from our backup), else use first sheet
        target_sheet = "Socios" if "Socios" in xls.sheet_names else xls.sheet_names[0]
        
        df = pd.read_excel(file_path, sheet_name=target_sheet)
        
        # Normalize columns
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Identify Columns
        name_col = None
        phone_col = None
        
        # Heuristic for Name
        possible_name_cols = ["NOMBRE", "NAME", "SOCIO", "ATLETA", "MEMBER"]
        for c in df.columns:
            if c in possible_name_cols:
                name_col = c
                break
        if not name_col: name_col = df.columns[0] # Fallback
        
        # Heuristic for Phone
        possible_phone_cols = ["TELEFONO", "PHONE", "CELULAR", "WHATSAPP", "TELÉFONO"]
        for c in df.columns:
            if c in possible_phone_cols:
                phone_col = c
                break
                
        if not phone_col:
            return "Error: No se encontró columna de Teléfono/WhatsApp en el archivo."
            
        # Iterate and Update
        for index, row in df.iterrows():
            raw_name = row[name_col]
            raw_phone = row[phone_col]
            
            if pd.isna(raw_name) or pd.isna(raw_phone):
                continue
                
            name_str = str(raw_name).strip().upper()
            phone_str = str(raw_phone).strip()
            
            # Find Member
            member = session.query(Member).filter(Member.name == name_str).first()
            if member:
                # Update if different? Just overwrite to be safe
                if member.phone != phone_str:
                    member.phone = phone_str
                    updated_count += 1
            else:
                not_found_count += 1
                
        session.commit()
        return f"✅ Teléfonos actualizados: {updated_count}. (No encontrados: {not_found_count})"
        
    except Exception as e:
        session.rollback()
        return f"Error actualizando teléfonos: {str(e)}"
    finally:
        session.close()

def get_summary_kpis():
    session = SessionLocal()
    
    total_rev = 0
    for t in session.query(Transaction).filter(Transaction.status == "PAID"):
        total_rev += t.amount
        
    count = session.query(Member).filter(Member.active == True).count()
    session.close()
    return total_rev, count
