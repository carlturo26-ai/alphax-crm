import os
import time
import pywhatkit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Date, Float
from sqlalchemy.ext.declarative import declarative_base
from dotenv import load_dotenv

# --- CONFIGURACIÓN ---
load_dotenv() # Cargar variables de .env si existe

DATABASE_URL = os.getenv("DATABASE_URL")

def get_database_url():
    global DATABASE_URL
    
    # 0. Check for local SQLite file (DISABLED to force Cloud DB)
    # local_db = os.path.join("data", "club_crm.db")
    # We ignore local DB to ensure we use the Real Cloud Data with correct Schema.

    # 1. Normal Check
    if not DATABASE_URL:
        print("\n⚠️  No se encontró la configuración de la base de datos en .env")
        print("ℹ️  Ve a tus 'Secrets' en Streamlit Cloud o busca tu URL de conexión.")
        print("   Debe verse como: postgresql://usuario:password@host/neondb...")
        DATABASE_URL = input("\n👉 Por favor, PEGA AQUÍ TU DATABASE_URL completa: ").strip()
        
        # Guardar en .env para la próxima
        with open(".env", "w") as f:
            f.write(f'DATABASE_URL="{DATABASE_URL}"\n')
        print("✅ Configuración guardada en archivo .env localmente.\n")
    
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return DATABASE_URL

DATABASE_URL = get_database_url()

Base = declarative_base()

class Member(Base):
    __tablename__ = 'members'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    phone = Column(String)
    active = Column(Boolean)

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    member_id = Column(Integer)
    month = Column(String)
    year = Column(Integer)
    status = Column(String)

# --- MENSAJE ---
def generar_mensaje(nombre):
    return f"¡Hola *{nombre}*! espero todo vaya super bien!\n\nTe escribo un mensajito rápido para recordarte el pago de la mensualidad correspondiente a este mes. Agradezco mucho si puedes gestionarlo pronto para mantener todo en orden administrativo ✅.\n\n¡Un abrazo y a seguir sumando kilómetros 🐺!"

def run_bot():
    print("🤖 INICIANDO BOT DE COBRANZA ALPHAX...")
    
    try:
        engine = create_engine(DATABASE_URL)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        print("✅ Conexión a Base de Datos Exitosa.")
        
        # Verificación Ligera
        print("✅ Base de datos cargada correctamente.")

    except Exception as e:
        print(f"❌ Error conectando a DB: {e}")
        return

    # Calcular Mes Actual
    import datetime
    months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                   "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    current_month_index = datetime.datetime.now().month - 1
    current_month_name = months_list[current_month_index]
    current_year = 2026 # Contexto AlphaX
    
    print(f"📅 Analizando Deudas para: {current_month_name} {current_year}")
    
    # 1. Obtener Socios Activos
    active_members = session.query(Member).filter(Member.active == True).all()
    
    # 2. Obtener Pagos del Mes
    pagos = session.query(Transaction).filter(
        Transaction.year == current_year, 
        Transaction.month == current_month_name,
        Transaction.status == 'PAID'
    ).all()
    paid_ids = {p.member_id for p in pagos}
    
    # 3. Filtrar Deudores
    pending_members = [m for m in active_members if m.id not in paid_ids]
    
    print(f"🔍 Encontrados {len(pending_members)} socios pendientes de pago.")
    print("-" * 50)
    
    if not pending_members:
        print("🎉 ¡Todos están al día! No hay nada que cobrar.")
        return

    # Mostrar Lista Previa
    for m in pending_members:
        print(f" - {m.name} (Tel: {m.phone})")
    
    confirm = input("\n¿Deseas enviar mensajes a estos socios? (Escribe 'SI'): ")
    if confirm.upper() != "SI":
        print("❌ Cancelado.")
        return

    # Loop de Envío
    count = 0
    print("\n🚀 Enviando mensajes... (No toques el mouse/teclado)")
    
    for m in pending_members:
        try:
            if not m.phone:
                print(f"⚠️ Saltando a {m.name}: Sin teléfono.")
                continue
                
            phone = "".join(filter(str.isdigit, m.phone))
            if len(phone) < 10:
                 print(f"⚠️ Saltando a {m.name}: Teléfono inválido ({m.phone}).")
                 continue
            
            # Formato Internacional (Colombia +57)
            if not phone.startswith("57") and len(phone) == 10:
                phone = "57" + phone
            
            # Solo enviar si no empieza con +
            phone_fmt = "+" + phone
            
            msg = generar_mensaje(m.name.title())
            
            print(f"📨 Enviando a {m.name} ({phone_fmt})...")
            
            # Enviar mensaje (Abre WA Web en nueva pestaña)
            # wait_time=15 (tiempo para que cargue la web)
            # tab_close=True (cerrar pestaña después)
            # close_time=3 (tiempo de espera antes de cerrar)
            pywhatkit.sendwhatmsg_instantly(phone_fmt, msg, wait_time=15, tab_close=True, close_time=4)
            
            count += 1
            print("✅ Enviado.")
            time.sleep(5) # Pausa de seguridad
            
        except Exception as e:
            print(f"❌ Error en {m.name}: {e}")
            
    print("-" * 50)
    print(f"🤖 Proceso Finalizado. Se enviaron {count} mensajes.")
    session.close()

if __name__ == "__main__":
    run_bot()
