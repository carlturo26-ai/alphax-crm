
from database import SessionLocal, Member, Transaction
import datetime

session = SessionLocal()
name = "DANILO GIRALDO"
member = session.query(Member).filter(Member.name == name).first()

if member:
    print(f"Restoring payment for {name}...")
    new_tx = Transaction(
        member_id=member.id,
        month="FEBRERO",
        year=2026,
        amount=150000,
        status="PAID",
        period="2026-FEB"
    )
    session.add(new_tx)
    session.commit()
    print("Payment restored.")
else:
    print("Member not found.")

session.close()
