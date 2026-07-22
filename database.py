
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os
import streamlit as st

# Database setup
# Check for Streamlit Secrets (Cloud) or Environment Variable
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.environ.get("DATABASE_URL")

if not DB_PATH:
    try:
        if "DATABASE_URL" in st.secrets:
            DB_PATH = st.secrets["DATABASE_URL"]
    except Exception:
        pass

if not DB_PATH:
    # Local fallback
    DB_FOLDER = os.path.join(os.path.dirname(__file__), "data")
    if not os.path.exists(DB_FOLDER):
        os.makedirs(DB_FOLDER)
    DB_PATH = f"sqlite:///{os.path.join(DB_FOLDER, 'club_crm.db')}"

if DB_PATH.startswith("postgres://"):
    DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)

@st.cache_resource(show_spinner=False)
def get_engine(db_url):
    # Optimize pooling settings specifically for Cloud Postgres (Neon)
    if db_url.startswith("sqlite"):
        return create_engine(db_url, echo=False, connect_args={"check_same_thread": False})
    else:
        # pool_recycle=300 limits lifespan of connections to 5 min, matching Neon's default autosuspend
        return create_engine(
            db_url, 
            echo=False, 
            pool_size=5, 
            max_overflow=10, 
            pool_recycle=300, 
            pool_pre_ping=True
        )

engine = get_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

# Models
class Member(Base):
    __tablename__ = "members"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    group = Column(String) # Carlos, Alejandro, Aprendizaje
    phone = Column(String, nullable=True) # WhatsApp Number (International Format)
    active = Column(Boolean, default=True)
    start_month = Column(String, default="ENERO") # Month the member joined the club
    notes = Column(String, nullable=True)
    
    transactions = relationship("Transaction", back_populates="member")

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"))
    period = Column(String)  # Format: "YYYY-MM" or "MonthName" depending on usage, let's stick to Month Name for simplicity with Excel or YYYY-MM
    year = Column(Integer)
    month = Column(String)
    amount = Column(Float)
    status = Column(String)  # PAID, PENDING, WAIVED
    payment_date = Column(Date, default=datetime.now)
    received_by = Column(String, nullable=True) # Account that received the money
    created_at = Column(Date, default=datetime.now)
    
    member = relationship("Member", back_populates="transactions")

class Expense(Base):
    __tablename__ = "expenses"
    
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String)
    amount = Column(Float)
    date = Column(Date, default=datetime.now)
    category = Column(String, nullable=True) # e.g. Operativo, Honorarios, Piscina
    paid_by = Column(String, nullable=True) # Carlos, Alejandro, AlphaX
    created_at = Column(Date, default=datetime.now)

class SleepRecord(Base):
    __tablename__ = "sleep_records"
    
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"))
    date = Column(Date, default=datetime.now)
    sds_score = Column(Integer)
    clinical_category = Column(String)
    raw_hours = Column(String)
    raw_quality = Column(String)
    raw_latency = Column(String)
    raw_awakenings = Column(String)
    raw_medications = Column(String)
    
    member = relationship("Member")

class AthleteUser(Base):
    __tablename__ = "athlete_users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    athlete_name = Column(String) # Link logically to Member.name
    created_at = Column(Date, default=datetime.now)

class LactateTest(Base):
    __tablename__ = "lactate_tests"
    
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"))
    date = Column(Date, default=datetime.now)
    sport = Column(String)  # Ciclismo, Running
    simulator = Column(String, nullable=True)
    lactate_meter = Column(String, nullable=True)
    bike_or_shoes = Column(String, nullable=True)
    power_source = Column(String, nullable=True)
    weight = Column(Float, nullable=True)
    ftp = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    ctl = Column(Float, nullable=True)
    atl = Column(Float, nullable=True)
    tsb = Column(Float, nullable=True)
    breakfast = Column(String, nullable=True)
    duration = Column(String, nullable=True)
    intensity = Column(String, nullable=True)
    last_training = Column(String, nullable=True)
    
    # Thresholds
    lt1_power = Column(Float, nullable=True)
    lt1_hr = Column(Integer, nullable=True)
    lt1_lactate = Column(Float, nullable=True)
    lt2_power = Column(Float, nullable=True)
    lt2_hr = Column(Integer, nullable=True)
    lt2_lactate = Column(Float, nullable=True)
    
    created_at = Column(Date, default=datetime.now)
    
    member = relationship("Member")
    steps = relationship("LactateTestStep", back_populates="test", cascade="all, delete-orphan")

class LactateTestStep(Base):
    __tablename__ = "lactate_test_steps"
    
    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("lactate_tests.id"))
    step_number = Column(String)  # Reposo, 1, 2, 3, etc.
    pot_rel = Column(Float, nullable=True)  # w/kg
    lactate = Column(Float, nullable=True)  # mmol/L
    watts = Column(Float, nullable=True)  # watts or km/h or pace depending on sport
    heart_rate = Column(Integer, nullable=True)  # bpm
    duration = Column(Integer, nullable=True)  # seconds
    rpe = Column(Float, nullable=True)  # borg scale
    
    test = relationship("LactateTest", back_populates="steps")

def init_db():
    Base.metadata.create_all(bind=engine)
    
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

