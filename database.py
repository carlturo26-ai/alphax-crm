
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os
import streamlit as st

# Database setup
# Check for Streamlit Secrets (Cloud) or Environment Variable
try:
    if "DATABASE_URL" in st.secrets:
        DB_PATH = st.secrets["DATABASE_URL"]
        # Fix for some Postgres providers using 'postgres://' instead of 'postgresql://'
        if DB_PATH.startswith("postgres://"):
            DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)
    elif "DATABASE_URL" in os.environ:
        DB_PATH = os.environ["DATABASE_URL"]
    else:
        raise KeyError
except (FileNotFoundError, KeyError):
    # Local fallback
    DB_FOLDER = os.path.join(os.path.dirname(__file__), "data")
    if not os.path.exists(DB_FOLDER):
        os.makedirs(DB_FOLDER)
    DB_PATH = f"sqlite:///{os.path.join(DB_FOLDER, 'club_crm.db')}"

engine = create_engine(DB_PATH, echo=False)
SessionLocal = sessionmaker(bind=engine)
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

def init_db():
    Base.metadata.create_all(bind=engine)
    
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
