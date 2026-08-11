import pandas as pd

file_path = "/Users/macbook/Documents/alphax_crm/CUENTA MULTISPORT 2.xlsx"
df = pd.read_excel(file_path, sheet_name="ALEJANDRO 2026")
print(df.head(40).to_string())
