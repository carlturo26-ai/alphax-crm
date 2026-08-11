import sqlite3
import pandas as pd

conn = sqlite3.connect('/Users/macbook/Documents/alphax_crm/data/club_crm.db')

print("--- Desglose de Ingresos CARLOS - MARZO 2026 en CRM ---")

query = """
SELECT m.name, t.amount
FROM transactions t
JOIN members m ON m.id = t.member_id
WHERE t.month = 'MARZO'
  AND t.year = 2026
  AND t.status = 'PAID'
  AND m."group" = 'Carlos'
ORDER BY t.amount DESC
"""

df = pd.read_sql_query(query, conn)

if not df.empty:
    print(df.to_string(index=False))
    total_carlos = df['amount'].sum()
    print(f"\nTOTAL CRM CARLOS MARZO: ${total_carlos:,.0f}")
else:
    print("No transactions found.")

print("\n--- Desglose ALEJANDRO - MARZO ---")
df_ale = pd.read_sql_query("""
SELECT m.name, t.amount FROM transactions t JOIN members m ON m.id = t.member_id 
WHERE t.month = 'MARZO' AND t.year = 2026 AND t.status = 'PAID' AND m."group" = 'Alejandro'
""", conn)
total_ale = df_ale['amount'].sum() if not df_ale.empty else 0
print(f"Total Alejandro Marzo: ${total_ale:,.0f}")

print("\n--- Desglose APRENDIZAJE - MARZO ---")
df_apr = pd.read_sql_query("""
SELECT m.name, t.amount FROM transactions t JOIN members m ON m.id = t.member_id 
WHERE t.month = 'MARZO' AND t.year = 2026 AND t.status = 'PAID' AND m."group" = 'Aprendizaje'
""", conn)
total_apr = df_apr['amount'].sum() if not df_apr.empty else 0
print(f"Total Aprendizaje Marzo: ${total_apr:,.0f}")

conn.close()
