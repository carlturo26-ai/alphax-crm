
from database import SessionLocal, Member, Transaction

session = SessionLocal()
name = "DANILO GIRALDO"
member = session.query(Member).filter(Member.name == name).first()

if member:
    # Find the recent payment we just added (or any Feb payment)
    tx = session.query(Transaction).filter(
        Transaction.member_id == member.id,
        Transaction.month == "FEBRERO",
        Transaction.year == 2026
    ).order_by(Transaction.id.desc()).first()
    
    if tx:
        print(f"Found transaction {tx.id} with amount ${tx.amount:,.0f}")
        print("Updating amount to $0 to avoid double counting income...")
        tx.amount = 0
        session.commit()
        print("Updated. Danilo is still 'PAID' but contributes $0 to total.")
    else:
        # If no transaction exists (maybe user deleted it?), create a $0 one
        print("No payment found. Creating a $0 coverage payment.")
        new_tx = Transaction(
            member_id=member.id,
            month="FEBRERO",
            year=2026,
            amount=0,
            status="PAID",
            period="2026-FEB"
        )
        session.add(new_tx)
        session.commit()
        print("Created $0 payment.")
else:
    print("Member not found.")

session.close()
