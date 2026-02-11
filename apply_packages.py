
from database import SessionLocal, Member, Transaction
import datetime

session = SessionLocal()

# Config of patches
# Name, Start Month Name, Duration (Months)
updates = [
    ("SEBASTIAN TORRES", "FEBRERO", 6), # Semestre Feb-Jul
    ("JUANITA MESA", "ENERO", 3),       # Trimestre Jan-Mar
    ("LUCAS ANGULO", "ENERO", 6),       # Semestre Jan-Jun
    ("MARLON GIRALDO", "FEBRERO", 3),   # Trimestre Feb-Apr
    ("MANUELA CORREA", "ENERO", 3),     # Trimestre Jan-Mar
    ("JOSE SANDOVAL", "ENERO", 12)      # Anualidad Jan-Dec
]

months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
               "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

print("--- Applying Package Corrections ---")

for name, start_month, duration in updates:
    member = session.query(Member).filter(Member.name == name).first()
    if not member:
        print(f"❌ Member not found: {name}")
        continue
        
    print(f"Processing {name} ({duration} months from {start_month})...")
    
    start_idx = months_list.index(start_month)
    year = 2026
    
    # We assume the First Month (Cash Payment) is already there from Import or Manual Entry.
    # We only need to ensure the coverage for the SUBSEQUENT months.
    
    # Verify Start Month exists (just for info)
    start_tx = session.query(Transaction).filter(
        Transaction.member_id == member.id,
        Transaction.month == start_month,
        Transaction.year == year
    ).first()
    
    if start_tx:
        print(f"  -> Found initial payment in {start_month}: ${start_tx.amount:,.0f}")
    else:
        print(f"  -> ⚠️ WARNING: No initial payment found for {start_month}. Creating $0 coverage anyway?")
        # If not found, maybe they paid later? Or data missing. 
        # User implies they paid. Let's create the coverage for subsequent months at least.
        
    # Create coverage for subsequent months
    for i in range(1, duration):
        current_idx = (start_idx + i) % 12
        current_month = months_list[current_idx]
        
        # Check if exists
        exists = session.query(Transaction).filter(
            Transaction.member_id == member.id,
            Transaction.month == current_month,
            Transaction.year == year
        ).first()
        
        if exists:
            if exists.amount == 0:
                print(f"  -> Month {current_month}: Already covered ($0).")
            else:
                print(f"  -> Month {current_month}: Has payment ${exists.amount:,.0f}. Skipping overwrite.")
        else:
            print(f"  -> Month {current_month}: Creating coverage ($0).")
            new_tx = Transaction(
                member_id=member.id,
                month=current_month,
                year=year,
                amount=0,
                status="PAID",
                period=f"{year}-{current_month[:3]}"
            )
            session.add(new_tx)

session.commit()
print("--- Done ---")
session.close()
