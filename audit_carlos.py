
from database import SessionLocal, Member, Transaction
from sqlalchemy import func

session = SessionLocal()

print("--- Auditoría Grupo CARLOS ---")
# Get members of group Carlos
members = session.query(Member).filter(Member.group == "Carlos").all()

total_group = 0
print(f"{'ID':<5} {'Name':<30} {'Total Paid':<15}")
print("-" * 50)

for m in members:
    # Sum paid txs
    total = session.query(func.sum(Transaction.amount)).filter(
        Transaction.member_id == m.id,
        Transaction.status == 'PAID'
    ).scalar() or 0
    
    if total > 0:
        print(f"{m.id:<5} {m.name:<30} ${total:,.0f}")
        total_group += total

print("-" * 50)
print(f"TOTAL REPORTADO: ${total_group:,.0f}")
session.close()
