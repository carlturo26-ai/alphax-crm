import pandas as pd
from sqlalchemy import create_engine, text
import os
import time

# 1. Setup Connections
# Local SQLite
db_path = os.path.join(os.path.dirname(__file__), "data", "club_crm.db")
sqlite_url = f"sqlite:///{db_path}"
local_engine = create_engine(sqlite_url)

# Cloud Postgres (Neon)
# Using the URL you provided previously
CLOUD_URL = "postgresql://neondb_owner:npg_qNTCmA8hbuf4@ep-late-darkness-aiunizhm-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require"
cloud_engine = create_engine(CLOUD_URL)

def migrate():
    print("🚀 Starting Data Migration: Local -> Cloud...")
    
    # Tables to migrate
    tables = ['members', 'transactions', 'expenses']
    
    with cloud_engine.connect() as cloud_conn:
        for table in tables:
            print(f"\n📦 Processing table: {table}")
            
            try:
                # Read from Local
                df = pd.read_sql(f"SELECT * FROM {table}", local_engine)
                print(f"   Shape: {df.shape}")
                
                if not df.empty:
                    # Clear Cloud Table (Optional: to avoid duplicates if re-running)
                    # cloud_conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
                    # But since we just created it, it's empty.
                    # If we use if_exists='replace', it drops the table and loses schema. 
                    # Better to use 'append'.
                    
                    # We'll use 'append' but we need to handle potential primary key conflicts if we ran the app
                    # However, since it's a fresh DB:
                    
                    df.to_sql(table, cloud_engine, if_exists='append', index=False, method='multi', chunksize=1000)
                    print(f"   ✅ Successfully transferred {len(df)} rows.")
                else:
                    print("   ⚠️ Table is empty locally.")
                    
            except Exception as e:
                print(f"   ❌ Error migrating {table}: {e}")

    print("\n✨ Migration Complete! Your cloud database now has your data.")

if __name__ == "__main__":
    confirm = input("Are you sure you want to overwrite/append to the Cloud Database? (yes/no): ")
    if confirm.lower() == 'yes':
        migrate()
    else:
        print("Migration cancelled.")
