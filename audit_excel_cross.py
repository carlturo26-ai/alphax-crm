import pandas as pd
import numpy as np

file_path = "/Users/macbook/Documents/alphax_crm/CUENTA MULTISPORT 2.xlsx"

try:
    xl = pd.ExcelFile(file_path)
    print("Sheets available:", xl.sheet_names)
    
    for sheet in ["CARLOS 2026", "ALEJANDRO 2026"]:
        if sheet in xl.sheet_names:
            df = xl.parse(sheet, header=0)
            print(f"\n--- Headers for {sheet} ---")
            print(df.columns.tolist())
            
            # Print unique values in some columns just to see if there is an indicator of "Cuenta"
            # Sometimes notes are left in some column.
            for col in df.columns:
                if 'Unnamed' not in str(col):
                    # Sample some non-null values
                    non_nulls = df[col].dropna()
                    if len(non_nulls) > 0 and len(non_nulls) < 100:
                        # Just to peek at string columns that might contain "Cuenta"
                        if non_nulls.dtype == object:
                            sample = non_nulls.head(5).tolist()
                            print(f"Col '{col}': {sample}")
                            
            print("\n--- Rows with word 'Cuenta' or 'Carlos' ---")
            # Search for 'cuenta' or 'carlos' in the dataframe text
            mask = df.astype(str).apply(lambda x: x.str.contains('(?i)cuenta|carlos', na=False)).any(axis=1)
            if mask.any():
                print(df[mask].head(10).to_string())
            else:
                print("No rows found with 'cuenta' or 'carlos' in text.")
                
            # Search for 'alejandro' in CARLOS sheet, and 'carlos' in ALEJANDRO sheet
            opp = 'alejandro' if sheet == 'CARLOS 2026' else 'carlos'
            mask_opp = df.astype(str).apply(lambda x: x.str.contains(f'(?i){opp}', na=False)).any(axis=1)
            if mask_opp.any():
                print(f"\n--- Rows with word '{opp}' in {sheet} ---")
                print(df[mask_opp].head(10).to_string())
                
except Exception as e:
    print(f"Error reading Excel: {e}")
