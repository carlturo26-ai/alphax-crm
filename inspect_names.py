
import pandas as pd

file_path = '/Users/macbook/Documents/alphax_crm/CUENTA MULTISPORT para CRM.xlsx'

def inspect_sheet(sheet_name):
    print(f"\n--- Names (Column 0) in {sheet_name} ---")
    # Read just column 0 (Atleta) to see the list of names/labels
    # Header is row 3 (confirmed previously)
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=3, usecols=[0])
    
    # Print all rows to find the delimiters
    print(df.to_string())

inspect_sheet("ALEJANDRO 2026")
inspect_sheet("CARLOS 2026")
