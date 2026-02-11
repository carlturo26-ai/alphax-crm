
from database import SessionLocal, Member, Transaction

session = SessionLocal()

member_name = "CARLOS LONDOÑO"
member = session.query(Member).filter(Member.name == member_name).first()

if not member:
    print(f"Member {member_name} not found.")
else:
    # Find the test transactions. 
    # Logic: We know I created Feb, Mar, Apr for 2026 during the test.
    # Feb was ~300k (or 100k? Logic was 300k Package). Mar/Apr were 0.
    
    txs = session.query(Transaction).filter(
        Transaction.member_id == member.id,
        Transaction.year == 2026,
        Transaction.month.in_(["FEBRERO", "MARZO", "ABRIL"])
    ).all()
    
    print(f"Found {len(txs)} transactions for {member_name}:")
    for tx in txs:
        print(f" - ID: {tx.id} | Month: {tx.month} | Amount: ${tx.amount:,.0f} | Status: {tx.status}")
        session.delete(tx)
        
    session.commit()
    print("Deleted all test transactions. Carlos should be a debtor again for Feb.")

session.close()
