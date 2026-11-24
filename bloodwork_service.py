"""
bloodwork_service.py — Servicio compartido de gestión, detección anti-duplicados
y fusión inteligente de exámenes de sangre para AlphaX CRM y Portal de Atletas.
"""

from collections import defaultdict
from datetime import date, datetime
from bloodwork_constants import ALL_MARKER_KEYS


def clean_numeric_marker(value):
    """Convierte un valor de entrada a float positivo o None."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
        return round(f, 2) if f > 0 else None
    except (ValueError, TypeError):
        return None


def extract_marker_dict(record_or_dict) -> dict:
    """Extrae un diccionario limpio {clave: float|None} desde un BloodworkRecord o dict."""
    out = {}
    is_dict = isinstance(record_or_dict, dict)
    for k in ALL_MARKER_KEYS:
        raw = record_or_dict.get(k) if is_dict else getattr(record_or_dict, k, None)
        out[k] = clean_numeric_marker(raw)
    return out


def count_record_markers(record_or_dict) -> int:
    """Cuenta cuántos de los 16 biomarcadores tienen un valor válido."""
    m = extract_marker_dict(record_or_dict)
    return sum(1 for v in m.values() if v is not None)


def _normalize_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    return datetime.now().date()


def _merge_filenames(existing_name: str, new_name: str) -> str:
    """Combina nombres de archivos PDF/foto evitando repetir nombres ya registrados."""
    parts = []
    for raw in (existing_name, new_name):
        if not raw:
            continue
        for item in str(raw).split(" + "):
            clean = item.strip()
            if clean and clean not in parts:
                parts.append(clean)
    return " + ".join(parts) if parts else None


def _compare_marker_dicts(existing_m: dict, new_m: dict) -> dict:
    """
    Compara biomarcadores existentes vs nuevos.
    Retorna:
      - matching: claves con el mismo valor (tolerancia <= 0.05)
      - complementary: claves que estaban vacías en el existente y el nuevo aporta
      - conflicts: lista de tuplas (key, old_val, new_val) donde ambos tienen valores distintos
    """
    matching = []
    complementary = []
    conflicts = []

    for k in ALL_MARKER_KEYS:
        ev = existing_m.get(k)
        nv = new_m.get(k)
        if nv is None:
            continue
        if ev is None:
            complementary.append(k)
        elif abs(ev - nv) <= 0.05:
            matching.append(k)
        else:
            conflicts.append((k, ev, nv))

    return {
        "matching": matching,
        "complementary": complementary,
        "conflicts": conflicts,
    }


def find_potential_duplicates(
    existing_records: list,
    exam_date,
    new_values: dict,
    filename: str = None,
    exclude_record_id: int = None,
) -> list:
    """
    Detecta si un examen por guardar coincide o duplica algún registro existente del atleta.

    Criterios detectados (ordenados por prioridad):
      1. `misma_fecha`: Ya existe un examen en la misma fecha exacta.
      2. `valores_identicos`: Al menos 3 biomarcadores coinciden exactamente (≤ 0.05) y sin conflictos
         (muy común cuando el OCR no leyó la fecha y quedó con la fecha de hoy).
      3. `archivo_repetido`: Mismo nombre de archivo PDF/foto ya cargado anteriormente.
      4. `fecha_cercana`: Examen a ±2 días de distancia con datos complementarios o coincidentes.

    Retorna lista de dicts ordenados por relevancia.
    """
    target_date = _normalize_date(exam_date)
    new_m = extract_marker_dict(new_values)
    new_count = sum(1 for v in new_m.values() if v is not None)
    clean_fname = (filename or "").strip().lower()

    matches = []
    for rec in existing_records or []:
        if exclude_record_id is not None and getattr(rec, "id", None) == exclude_record_id:
            continue

        rec_date = _normalize_date(rec.date) if getattr(rec, "date", None) else None
        day_diff = abs((target_date - rec_date).days) if rec_date else 999
        rec_m = extract_marker_dict(rec)
        rec_count = sum(1 for v in rec_m.values() if v is not None)
        cmp = _compare_marker_dicts(rec_m, new_m)

        rec_fnames = [
            p.strip().lower()
            for p in str(getattr(rec, "pdf_filename", "") or "").split(" + ")
            if p.strip()
        ]
        new_fnames = [
            p.strip().lower()
            for p in clean_fname.split(" + ")
            if p.strip()
        ]
        same_file = bool(new_fnames and any(nf in rec_fnames for nf in new_fnames))

        reason = None
        priority = 99

        if day_diff == 0:
            reason = "misma_fecha"
            priority = 1
        elif len(cmp["matching"]) >= 3 and len(cmp["conflicts"]) == 0:
            reason = "valores_identicos"
            priority = 2
        elif same_file:
            reason = "archivo_repetido"
            priority = 3
        elif day_diff <= 2 and (len(cmp["matching"]) >= 1 or (len(cmp["complementary"]) >= 2 and rec_count < len(ALL_MARKER_KEYS))):
            reason = "fecha_cercana"
            priority = 4

        if reason:
            is_exact_duplicate = (
                new_count > 0
                and len(cmp["complementary"]) == 0
                and len(cmp["conflicts"]) == 0
                and len(cmp["matching"]) == new_count
            )
            matches.append({
                "record_id": rec.id,
                "record": rec,
                "date": rec_date,
                "day_diff": day_diff,
                "reason": reason,
                "priority": priority,
                "existing_count": rec_count,
                "new_count": new_count,
                "matching": cmp["matching"],
                "complementary": cmp["complementary"],
                "conflicts": cmp["conflicts"],
                "same_file": same_file,
                "is_exact_duplicate": is_exact_duplicate,
                "pdf_filename": getattr(rec, "pdf_filename", None),
            })

    matches.sort(key=lambda x: (x["priority"], x["day_diff"], -x["existing_count"]))
    return matches


def save_or_merge_bloodwork(
    session,
    BloodworkRecordModel,
    member_id: int,
    exam_date,
    values: dict,
    pdf_filename: str = None,
    notes: str = None,
    mode: str = "create",
    target_record_id: int = None,
    overwrite_conflicts: bool = True,
) -> tuple:
    """
    Guarda, combina o reemplaza un registro de examen de sangre de forma segura.

    mode:
      - "create": Crea un nuevo registro.
      - "merge_fill": Combina sobre `target_record_id` llenando los marcadores vacíos
        (y opcionalmente actualizando conflictos si `overwrite_conflicts=True`).
      - "replace": Reemplaza todos los marcadores de `target_record_id` con los valores confirmados.

    Retorna (BloodworkRecord_guardado, mensaje_descriptivo).
    Lanza ValueError si no hay al menos 1 biomarcador válido.
    """
    clean_m = extract_marker_dict(values)
    filled_count = sum(1 for v in clean_m.values() if v is not None)
    if filled_count == 0:
        raise ValueError("Debes ingresar al menos un biomarcador válido antes de guardar.")

    target_date = _normalize_date(exam_date)

    if mode in ("merge_fill", "replace") and target_record_id is not None:
        rec = session.query(BloodworkRecordModel).filter(
            BloodworkRecordModel.id == target_record_id,
            BloodworkRecordModel.member_id == member_id,
        ).first()
        if not rec:
            raise ValueError("No se encontró el examen existente seleccionado para actualizar.")

        if mode == "replace":
            rec.date = target_date
            for k in ALL_MARKER_KEYS:
                setattr(rec, k, clean_m[k])
            if pdf_filename:
                rec.pdf_filename = pdf_filename
            rec.notes = notes.strip() if notes and notes.strip() else rec.notes
            session.commit()
            total_now = count_record_markers(rec)
            return rec, f"Examen del {target_date.strftime('%d/%m/%Y')} actualizado correctamente ({total_now}/16 biomarcadores)."

        # mode == "merge_fill"
        added = 0
        updated = 0
        for k in ALL_MARKER_KEYS:
            nv = clean_m[k]
            if nv is None:
                continue
            ev = clean_numeric_marker(getattr(rec, k, None))
            if ev is None:
                setattr(rec, k, nv)
                added += 1
            elif overwrite_conflicts and abs(ev - nv) > 0.05:
                setattr(rec, k, nv)
                updated += 1

        if pdf_filename:
            rec.pdf_filename = _merge_filenames(rec.pdf_filename, pdf_filename)
        if notes and notes.strip():
            if rec.notes and notes.strip() not in rec.notes:
                rec.notes = f"{rec.notes} | {notes.strip()}"
            elif not rec.notes:
                rec.notes = notes.strip()

        session.commit()
        total_now = count_record_markers(rec)
        detail = f"+{added} nuevos" if added else "sin campos nuevos"
        if updated:
            detail += f", {updated} actualizados"
        return rec, f"Examen del {rec.date.strftime('%d/%m/%Y')} combinado exitosamente ({detail}; total {total_now}/16 biomarcadores)."

    # mode == "create"
    new_rec = BloodworkRecordModel(
        member_id=member_id,
        date=target_date,
        pdf_filename=pdf_filename or None,
        notes=notes.strip() if notes and notes.strip() else None,
        **clean_m,
    )
    session.add(new_rec)
    session.commit()
    return new_rec, f"Examen del {target_date.strftime('%d/%m/%Y')} guardado exitosamente ({filled_count}/16 biomarcadores)."


def find_duplicate_groups(records: list) -> list:
    """
    Encuentra grupos de registros duplicados (2 o más registros del mismo atleta
    en la misma fecha exacta).
    Retorna lista de dicts: [{"date": date, "records": [...], "merged_preview": {...}}].
    """
    by_date = defaultdict(list)
    for r in records or []:
        if getattr(r, "date", None):
            by_date[_normalize_date(r.date)].append(r)

    groups = []
    for dt, recs in sorted(by_date.items(), key=lambda x: x[0], reverse=True):
        if len(recs) >= 2:
            # Ordenar primero los que tienen más marcadores y luego el más reciente
            ordered = sorted(recs, key=lambda r: (count_record_markers(r), r.id), reverse=True)
            merged_preview = {k: None for k in ALL_MARKER_KEYS}
            for r in ordered:
                for k in ALL_MARKER_KEYS:
                    if merged_preview[k] is None:
                        val = clean_numeric_marker(getattr(r, k, None))
                        if val is not None:
                            merged_preview[k] = val
            groups.append({
                "date": dt,
                "records": ordered,
                "count": len(ordered),
                "merged_count": sum(1 for v in merged_preview.values() if v is not None),
                "merged_preview": merged_preview,
            })
    return groups


def merge_duplicate_records(session, BloodworkRecordModel, member_id: int, record_ids: list) -> tuple:
    """
    Unifica múltiples registros duplicados de un atleta en un solo registro consolidado
    sin perder ningún biomarcador, archivo ni nota.
    Retorna (registro_principal, cantidad_eliminada).
    """
    if not record_ids or len(record_ids) < 2:
        raise ValueError("Se requieren al menos 2 registros para unificar.")

    recs = session.query(BloodworkRecordModel).filter(
        BloodworkRecordModel.id.in_(record_ids),
        BloodworkRecordModel.member_id == member_id,
    ).all()

    if len(recs) < 2:
        raise ValueError("No se encontraron suficientes registros válidos para unificar.")

    # Elegir como maestro el registro más completo (y más reciente en caso de empate)
    recs.sort(key=lambda r: (count_record_markers(r), r.id), reverse=True)
    master = recs[0]
    others = recs[1:]

    for other in others:
        for k in ALL_MARKER_KEYS:
            if clean_numeric_marker(getattr(master, k, None)) is None:
                ov = clean_numeric_marker(getattr(other, k, None))
                if ov is not None:
                    setattr(master, k, ov)
        master.pdf_filename = _merge_filenames(master.pdf_filename, other.pdf_filename)
        if other.notes and other.notes.strip():
            if not master.notes:
                master.notes = other.notes.strip()
            elif other.notes.strip() not in master.notes:
                master.notes = f"{master.notes} | {other.notes.strip()}"
        session.delete(other)

    session.commit()
    return master, len(others)
