
from database import SessionLocal, Member, Transaction
from datetime import datetime

session = SessionLocal()
name = "DANILO GIRALDO"
member = session.query(Member).filter(Member.name == name).first()

print(f"--- Debug Info for {name} ---")
if not member:
    print("Member not found in DB.")
else:
    print(f"ID: {member.id} | Name: {member.name} | Group: {member.group} | Active: {member.active}")
    
    # Check Txs for 2026
    txs = session.query(Transaction).filter(
        Transaction.member_id == member.id,
        Transaction.year == 2026
    ).all()
    
    print(f"Transactions (2026): {len(txs)}")
    for t in txs:
        print(f" - ID: {t.id} | Month: {t.month} | Amount: ${t.amount:,.0f} | Status: {t.status}")

    # Simulate Pending Check Logic
    current_month_index = datetime.now().month - 1 # 0-11 (FEB = 1)
    # Be careful! Python datetime.now().month is 1-based.
    # FEB is 2. Index in list ["ENE"...] is 1.
    months_list = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                   "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    
    # Hardcoded check for Feb 2026 as per dashboard
    # dashboard uses: current_month_index = datetime.now().month - 1
    # months_list[current_month_index] => FEBRERO
    
    check_month = "FEBRERO"
    
    paid_in_month = session.query(Transaction).filter(
        Transaction.member_id == member.id,
        Transaction.year == 2026,
        Transaction.month == check_month,
        Transaction.status == 'PAID'
    ).first()
    
    if paid_in_month:
        print(f"✅ User HAS a PAID transaction for {check_month}. Should NOT be in Debtors.")
    else:
        print(f"❌ User HAS NO PAID transaction for {check_month}. SHOULD be in Debtors.")

session.close()
