import re
import pandas as pd
from datetime import datetime

def clean_string(val):
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()

def parse_float(val):
    if pd.isna(val) or val is None:
        return None
    val_str = clean_string(val).replace(",", ".").replace(" ", "")
    # Remove any trailing non-numeric characters, like % or units
    val_str = re.sub(r"[^\d.-]", "", val_str)
    try:
        return float(val_str) if val_str else None
    except ValueError:
        return None

def parse_int(val):
    f_val = parse_float(val)
    return int(f_val) if f_val is not None else None

def parse_date(val):
    val_str = clean_string(val)
    if not val_str:
        return None
    # Try various formats
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            continue
    
    # Spanish months mapping
    months_es = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12
    }
    
    # Match something like "22 Julio 2026" or "22 de Julio de 2026"
    match = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-zA-Záéíóúñ]+)\s+(?:de\s+)?(\d{4})", val_str, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))
        if month_name in months_es:
            month = months_es[month_name]
            try:
                return datetime(year, month, day).date()
            except ValueError:
                pass
                
    # Fallback to current date or just try standard pandas parsing
    try:
        return pd.to_datetime(val_str).date()
    except Exception:
        return datetime.now().date()

def parse_grid_to_data(grid):
    """
    Parses a 2D list of strings (grid) into header_metadata and step_rows.
    """
    header = {
        "athlete_name": "",
        "date": datetime.now().date(),
        "sport": "Ciclismo",
        "simulator": "",
        "lactate_meter": "",
        "bike_or_shoes": "",
        "power_source": "",
        "weight": None,
        "ftp": None,
        "temperature": None,
        "ctl": None,
        "atl": None,
        "tsb": None,
        "breakfast": "",
        "duration": "",
        "intensity": "",
        "last_training": ""
    }
    
    step_rows = []
    
    # Helper to check key-value extraction
    def extract_val(cell_text, next_cell_text):
        cell_clean = cell_text.lower()
        if ":" in cell_text:
            parts = cell_text.split(":", 1)
            return parts[1].strip()
        return next_cell_text.strip() if next_cell_text else ""

    # 1. Parse header info scanning cells
    rows_count = len(grid)
    for r in range(rows_count):
        row = grid[r]
        cols_count = len(row)
        for c in range(cols_count):
            cell = clean_string(row[c])
            if not cell:
                continue
            
            cell_lower = cell.lower()
            next_cell = clean_string(row[c+1]) if c + 1 < cols_count else ""
            
            # Match metadata keys
            if "nombre" in cell_lower or "atleta" in cell_lower:
                val = extract_val(cell, next_cell)
                # Strip out 'edad' if it was in the same cell, e.g. "Nombre: Carlos Zuluaga Edad: 40"
                if "edad" in val.lower():
                    val = re.split(r"(?i)\bedad\b", val)[0].strip().rstrip(":")
                header["athlete_name"] = val.upper()
                
            elif "fecha" in cell_lower:
                val = extract_val(cell, next_cell)
                header["date"] = parse_date(val)
                
            elif "deporte" in cell_lower:
                val = extract_val(cell, next_cell)
                val_clean = val.capitalize()
                if "cicli" in val_clean.lower():
                    header["sport"] = "Ciclismo"
                elif "run" in val_clean.lower() or "carr" in val_clean.lower() or "trot" in val_clean.lower():
                    header["sport"] = "Running"
                else:
                    header["sport"] = val_clean
                    
            elif "simulador" in cell_lower:
                header["simulator"] = extract_val(cell, next_cell)
                
            elif "medidor de lactato" in cell_lower or ("medidor" in cell_lower and "lactato" in cell_lower):
                header["lactate_meter"] = extract_val(cell, next_cell)
                
            elif "bicicleta o zapatos" in cell_lower or "bicicleta" in cell_lower or "zapatos" in cell_lower:
                header["bike_or_shoes"] = extract_val(cell, next_cell)
                
            elif "fuente de potencia" in cell_lower or "fuente de potenia" in cell_lower or "potencia" in cell_lower:
                if "fuente" in cell_lower:
                    header["power_source"] = extract_val(cell, next_cell)
                
            elif "peso" in cell_lower:
                val = extract_val(cell, next_cell)
                header["weight"] = parse_float(val)
                
            elif "ftp" in cell_lower or "ftp/cp" in cell_lower:
                val = extract_val(cell, next_cell)
                header["ftp"] = parse_float(val)
                
            elif "temperatura" in cell_lower:
                val = extract_val(cell, next_cell)
                header["temperature"] = parse_float(val)
                
            elif cell_lower == "ctl":
                header["ctl"] = parse_float(next_cell if next_cell else cell)
                
            elif cell_lower == "atl":
                header["atl"] = parse_float(next_cell if next_cell else cell)
                
            elif cell_lower == "tsb":
                header["tsb"] = parse_float(next_cell if next_cell else cell)
                
            elif "desayuno" in cell_lower:
                header["breakfast"] = extract_val(cell, next_cell)
                
            elif cell_lower == "duracion" or cell_lower == "duración":
                header["duration"] = extract_val(cell, next_cell)
                
            elif cell_lower == "intensidad":
                header["intensity"] = extract_val(cell, next_cell)
                
            elif "ultimo entrenamiento" in cell_lower or "último entrenamiento" in cell_lower:
                header["last_training"] = extract_val(cell, next_cell)

    # 2. Find table start and columns
    table_start_row = -1
    col_mapping = {}
    
    for r in range(rows_count):
        row = grid[r]
        row_str = " ".join([clean_string(c).lower() for c in row])
        
        # Look for headers strictly: must have a step column indicator AND a measurement indicator
        has_escalon = any("escalon" in clean_string(c).lower() or "escalón" in clean_string(c).lower() or clean_string(c).strip() == "#" for c in row)
        has_measurement = any("pot/rel" in clean_string(c).lower() or "lactato" in clean_string(c).lower() or "power" in clean_string(c).lower() or "pulso" in clean_string(c).lower() for c in row)
        
        if has_escalon and has_measurement:
            table_start_row = r
            # Map column indices
            for c_idx, cell_val in enumerate(row):
                val_clean = clean_string(cell_val).lower()
                if not val_clean:
                    continue
                if "escalon" in val_clean or "escalón" in val_clean or val_clean == "#":
                    col_mapping["step_number"] = c_idx
                elif "pot/rel" in val_clean or "w/kg" in val_clean:
                    col_mapping["pot_rel"] = c_idx
                elif "lactato" in val_clean and "x 100" not in val_clean:
                    col_mapping["lactate"] = c_idx
                elif "power" in val_clean or "watts" in val_clean or "vatios" in val_clean:
                    col_mapping["watts"] = c_idx
                elif "pulso" in val_clean or "heart" in val_clean or "hr" in val_clean or "lpm" in val_clean:
                    col_mapping["heart_rate"] = c_idx
                elif "duracion" in val_clean or "duración" in val_clean or val_clean == "segs":
                    col_mapping["duration"] = c_idx
                elif "rpe" in val_clean or "borg" in val_clean:
                    col_mapping["rpe"] = c_idx
            break

    # Parse rows below table start
    if table_start_row != -1:
        for r in range(table_start_row + 1, rows_count):
            row = grid[r]
            if not row:
                continue
            
            # Check if this row is empty or contains header/units
            row_str = " ".join([clean_string(c).lower() for c in row]).strip()
            if not row_str:
                continue
            
            # Skip unit rows like "w/kg", "mmol", "watts", "lpm"
            if "w/kg" in row_str or "mmol" in row_str or "watts" in row_str or "lpm" in row_str or "borg/mod" in row_str:
                continue
                
            # Read cells based on mapped columns
            step_data = {}
            has_data = False
            
            # Step number
            step_col = col_mapping.get("step_number")
            if step_col is not None and step_col < len(row):
                step_val = clean_string(row[step_col])
                if step_val:
                    step_data["step_number"] = step_val.capitalize()
                    has_data = True
            
            if not has_data:
                continue # Skip if step number is missing
                
            # Potencia Relativa
            pr_col = col_mapping.get("pot_rel")
            if pr_col is not None and pr_col < len(row):
                step_data["pot_rel"] = parse_float(row[pr_col])
                
            # Lactate
            lac_col = col_mapping.get("lactate")
            if lac_col is not None and lac_col < len(row):
                step_data["lactate"] = parse_float(row[lac_col])
                
            # Watts (or speed/pace)
            w_col = col_mapping.get("watts")
            if w_col is not None and w_col < len(row):
                step_data["watts"] = parse_float(row[w_col])
                
            # Heart rate
            hr_col = col_mapping.get("heart_rate")
            if hr_col is not None and hr_col < len(row):
                step_data["heart_rate"] = parse_int(row[hr_col])
                
            # Duration
            dur_col = col_mapping.get("duration")
            if dur_col is not None and dur_col < len(row):
                step_data["duration"] = parse_int(row[dur_col])
                
            # RPE
            rpe_col = col_mapping.get("rpe")
            if rpe_col is not None and rpe_col < len(row):
                step_data["rpe"] = parse_float(row[rpe_col])
                
            step_rows.append(step_data)
            
    return header, step_rows

def parse_tsv_text(text_content):
    """
    Parses tab-separated values pasted directly from Google Sheets or Excel.
    """
    lines = text_content.strip().split("\n")
    grid = []
    for line in lines:
        cells = line.split("\t")
        grid.append(cells)
    return parse_grid_to_data(grid)

def parse_excel_file(file_path):
    """
    Parses an Excel file by loading it into a grid and calling parse_grid_to_data.
    """
    try:
        # Load the sheet. Since we don't know the exact sheet name, let's load the first sheet.
        xl = pd.ExcelFile(file_path)
        sheet_name = xl.sheet_names[0]
        
        # Read without headers to get raw grid
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
        
        # Convert df to 2D list of strings
        grid = []
        for r_idx, row in df.iterrows():
            grid.append([str(c) if pd.notna(c) else "" for c in row])
            
        return parse_grid_to_data(grid)
    except Exception as e:
        print(f"Error parsing Excel: {e}")
        return None, None

def estimate_thresholds(steps):
    """
    Heuristics to suggest LT1 and LT2 from the list of parsed steps:
    - LT1 (Aerobic Threshold): Suggest first step (excluding Reposo) where lactate >= basal plateau + 0.3 mmol/L.
    - LT2 (Anaerobic Threshold): Suggest the active step closest to 4.0 mmol/L.
    """
    # Filter reposo step to establish baseline
    reposo = None
    active_steps = []
    
    for s in steps:
        step_num = str(s.get("step_number", "")).lower()
        if "reposo" in step_num or "rest" in step_num or "0" == step_num:
            reposo = s
        else:
            active_steps.append(s)
            
    # Find basal plateau (minimum lactate value across all steps)
    all_lactates = [s.get("lactate") for s in steps if s.get("lactate") is not None]
    baseline_lactate = min(all_lactates) if all_lactates else 1.2
    
    lt1_idx = None
    lt2_idx = None
    
    # 1. Estimate LT1: first step (excluding reposo) where lactate >= baseline + 0.3 mmol/L
    for idx, s in enumerate(active_steps):
        lac = s.get("lactate")
        if lac is not None:
            if lac >= (baseline_lactate + 0.3):
                lt1_idx = idx
                break
                
    # 2. Estimate LT2: the step closest to 4.0 mmol/L
    best_diff = float("inf")
    for idx, s in enumerate(active_steps):
        lac = s.get("lactate")
        if lac is not None:
            diff = abs(lac - 4.0)
            if diff < best_diff:
                best_diff = diff
                lt2_idx = idx
            
    # Default fallbacks if not found
    if lt1_idx is None and active_steps:
        lt1_idx = min(len(active_steps) // 2, len(active_steps) - 1)
    if lt2_idx is None and active_steps:
        lt2_idx = len(active_steps) - 1
        
    lt1_step = active_steps[lt1_idx] if lt1_idx is not None and lt1_idx < len(active_steps) else None
    lt2_step = active_steps[lt2_idx] if lt2_idx is not None and lt2_idx < len(active_steps) else None
    
    return lt1_step, lt2_step
