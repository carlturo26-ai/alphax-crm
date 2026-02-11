from sqlalchemy import create_engine, text
import os
import streamlit as st

# Setup DB Connection similar to database.py
try:
    if "DATABASE_URL" in st.secrets:
        DB_PATH = st.secrets["DATABASE_URL"]
        if DB_PATH.startswith("postgres://"):
            DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)
    elif "DATABASE_URL" in os.environ:
        DB_PATH = os.environ["DATABASE_URL"]
    else:
        # Fallback to local for testing if needed
        DB_FOLDER = os.path.join(os.path.dirname(__file__), "data")
        DB_PATH = f"sqlite:///{os.path.join(DB_FOLDER, 'club_crm.db')}"
except:
    DB_FOLDER = os.path.join(os.path.dirname(__file__), "data")
    DB_PATH = f"sqlite:///{os.path.join(DB_FOLDER, 'club_crm.db')}"

print(f"Connecting to: {DB_PATH}")
engine = create_engine(DB_PATH)

def add_column():
    with engine.connect() as conn:
        try:
            # Check if column exists first (Postgres specific)
            # Or just try adding it.
            # SQLite syntax vs Postgres syntax.
            if "sqlite" in DB_PATH:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN paid_by VARCHAR;"))
            else:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN IF NOT EXISTS paid_by VARCHAR;"))
                
            print("✅ Column 'paid_by' added successfully.")
        except Exception as e:
            print(f"⚠️ Error (column might exist): {e}")

if __name__ == "__main__":
    add_column()
