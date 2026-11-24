"""
cda_estimator.py — Motor inverso de calibración real de CdA (m²) a partir de archivos
de competencias o entrenamientos (.fit, .tcx, .gpx) o resúmenes de carrera.

Combina:
  1. Parser multi-formato de telemetría (.FIT binario Garmin/Wahoo, .TCX y .GPX con potencia).
  2. Clima histórico real ERA5 del día, hora y coordenadas exactas de la carrera (`weather_service.py`).
  3. Balance energético físico inverso (Martin et al. 1998 + energía cinética 0.5*m*Δv²):
       E_aero = E_pedaleo_rueda - E_rodamiento - E_gravedad - ΔE_cinética
       CdA = E_aero / Σ(0.5 * rho_i * v_aire_i * |v_aire_i| * v_i * dt_i)
  4. Solver inverso por resumen de carrera (cuando el archivo no trae vatios segundo a segundo):
       Bisección sobre `simulate_race_strategy` para hallar el CdA exacto que reproduce el tiempo real.
  5. Diagnóstico de estabilidad por tramos (detecta si el atleta perdió postura aero por fatiga
     en la segunda mitad o si hubo efecto pelotón/drafting).
"""

from datetime import date, datetime, timedelta, timezone
import gzip
import io
import math
import struct
import xml.etree.ElementTree as ET
import zipfile

from race_strategy_engine import (
    GRAVITY,
    _angle_diff_deg,
    _bearing_deg,
    _compute_normalized_power,
    _haversine_m,
    _sample_hourly_weather,
    calc_air_density,
    format_hms,
    parse_gpx_bytes,
    simulate_race_strategy,
)


FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
SEMICIRCLES_TO_DEG = 180.0 / (2 ** 31)


def unwrap_activity_bytes(file_bytes: bytes, filename: str) -> tuple:
    """
    Si el archivo viene comprimido en .zip (ej. 'Export Original' de Garmin Connect)
    o .gz (ej. Strava .fit.gz), extrae automáticamente el .fit, .tcx o .gpx interior.
    Retorna (bytes_extraidos, nombre_archivo_interno).
    """
    low = (filename or "").lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            candidates = [
                n for n in zf.namelist()
                if not n.endswith("/") and n.lower().rsplit(".", 1)[-1] in ("fit", "tcx", "gpx")
            ]
            if not candidates:
                raise ValueError("El archivo .zip no contiene ningún archivo .fit, .tcx ni .gpx.")
            # Priorizar .fit > .tcx > .gpx
            candidates.sort(key=lambda n: {"fit": 0, "tcx": 1, "gpx": 2}.get(n.lower().rsplit(".", 1)[-1], 9))
            inner_name = candidates[0]
            return zf.read(inner_name), inner_name
    if low.endswith(".gz"):
        decompressed = gzip.decompress(file_bytes)
        inner_name = filename[:-3] if len(filename) > 3 else "actividad.fit"
        return decompressed, inner_name
    return file_bytes, filename


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_iso_ts(ts_str: str):
    if not ts_str:
        return None
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None


# ═════════════════════════════════════════════════════════════════════
#  PARSERS DE TELEMETRÍA (.GPX, .TCX, .FIT)
# ═════════════════════════════════════════════════════════════════════

def _parse_gpx_activity(file_bytes: bytes) -> tuple:
    """Extrae puntos con timestamp, GPS, elevación y potencia (si existe) de un GPX."""
    root = ET.fromstring(file_bytes)
    name = "Actividad GPX"
    pts = []

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "name" and elem.text and elem.text.strip() and name == "Actividad GPX":
            name = elem.text.strip()
        elif tag == "trkpt":
            lat = elem.attrib.get("lat")
            lon = elem.attrib.get("lon")
            if lat is None or lon is None:
                continue
            ele = None
            ts = None
            power = None
            for child in elem.iter():
                ctag = _strip_ns(child.tag).lower()
                if ctag == "ele" and child.text:
                    try:
                        ele = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "time" and child.text and ts is None:
                    ts = _parse_iso_ts(child.text)
                elif ctag in ("power", "watts") and child.text:
                    try:
                        power = float(child.text.strip())
                    except ValueError:
                        pass
            pts.append({
                "lat": float(lat),
                "lon": float(lon),
                "ele": ele,
                "ts": ts,
                "power": power,
                "dist_m": None,
                "speed_ms": None,
            })
    return name, pts


def _parse_tcx_activity(file_bytes: bytes) -> tuple:
    """Extrae telemetría (GPS, elevación, distancia, tiempo y Watts) de un archivo Garmin/Strava TCX."""
    root = ET.fromstring(file_bytes)
    name = "Actividad TCX"
    pts = []

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "Notes" and elem.text and elem.text.strip() and name == "Actividad TCX":
            name = elem.text.strip()[:60]
        elif tag == "Trackpoint":
            lat = lon = ele = dist_m = power = speed_ms = None
            ts = None
            for child in elem.iter():
                ctag = _strip_ns(child.tag).lower()
                if ctag == "latitudedegrees" and child.text:
                    try:
                        lat = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "longitudedegrees" and child.text:
                    try:
                        lon = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "altitudemeters" and child.text:
                    try:
                        ele = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "distancemeters" and child.text:
                    try:
                        dist_m = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "time" and child.text and ts is None:
                    ts = _parse_iso_ts(child.text)
                elif ctag in ("watts", "power") and child.text:
                    try:
                        power = float(child.text.strip())
                    except ValueError:
                        pass
                elif ctag == "speed" and child.text:
                    try:
                        speed_ms = float(child.text.strip())
                    except ValueError:
                        pass
            if lat is not None and lon is not None:
                pts.append({
                    "lat": lat,
                    "lon": lon,
                    "ele": ele,
                    "ts": ts,
                    "power": power,
                    "dist_m": dist_m,
                    "speed_ms": speed_ms,
                })
    return name, pts


def _parse_fit_with_fitdecode(file_bytes: bytes) -> tuple:
    """Lee un archivo binario .FIT usando `fitdecode` (soporte completo Garmin/Wahoo/Hammerhead/Courses)."""
    import fitdecode

    pts = []
    fit_name = "Actividad FIT"
    crc_opt = getattr(fitdecode, "CrcCheck", None)
    reader_kwargs = {"check_crc": crc_opt.DISABLED} if crc_opt is not None else {}

    with fitdecode.FitReader(io.BytesIO(file_bytes), **reader_kwargs) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "course":
                c_name = frame.get_value("name", fallback=None)
                if c_name:
                    fit_name = str(c_name).strip()
            elif frame.name == "record":
                lat = frame.get_value("position_lat", fallback=None)
                lon = frame.get_value("position_long", fallback=None)
                if lat is None or lon is None:
                    continue
                lat_f, lon_f = float(lat), float(lon)
                lat_deg = lat_f * SEMICIRCLES_TO_DEG if abs(lat_f) > 180.0 else lat_f
                lon_deg = lon_f * SEMICIRCLES_TO_DEG if abs(lon_f) > 180.0 else lon_f
                if not (-90.0 <= lat_deg <= 90.0 and -180.0 <= lon_deg <= 180.0):
                    continue
                if abs(lat_deg) < 1e-5 and abs(lon_deg) < 1e-5:
                    continue
                ele = frame.get_value("enhanced_altitude", fallback=None)
                if ele is None:
                    ele = frame.get_value("altitude", fallback=None)
                spd = frame.get_value("enhanced_speed", fallback=None)
                if spd is None:
                    spd = frame.get_value("speed", fallback=None)
                dist = frame.get_value("distance", fallback=None)
                pwr = frame.get_value("power", fallback=None)
                ts = frame.get_value("timestamp", fallback=None)
                pts.append({
                    "lat": lat_deg,
                    "lon": lon_deg,
                    "ele": float(ele) if ele is not None else None,
                    "ts": ts if isinstance(ts, datetime) else None,
                    "power": float(pwr) if pwr is not None else None,
                    "dist_m": float(dist) if dist is not None else None,
                    "speed_ms": float(spd) if spd is not None else None,
                })
    return fit_name, pts


def _parse_fit_pure_python(file_bytes: bytes) -> tuple:
    """
    Decodificador binario FIT ligero en Python puro (respaldo sin dependencias)
    para mensajes globales `record` (#20): timestamp, lat, lon, altitude, distance, speed, power.
    """
    if len(file_bytes) < 14:
        raise ValueError("Archivo FIT inválido o demasiado corto.")
    hdr_size = file_bytes[0]
    data_size = struct.unpack_from("<I", file_bytes, 4)[0]
    if file_bytes[8:12] != b".FIT":
        raise ValueError("Firma .FIT no encontrada en el archivo.")

    pos = hdr_size
    end = min(len(file_bytes), hdr_size + data_size)
    local_defs = {}
    pts = []
    last_ts_int = 0

    BASE_FMT = {
        0: ("B", 1, 0xFF),
        1: ("b", 1, 0x7F),
        2: ("B", 1, 0xFF),
        3: ("h", 2, 0x7FFF),
        4: ("H", 2, 0xFFFF),
        5: ("i", 4, 0x7FFFFFFF),
        6: ("I", 4, 0xFFFFFFFF),
        7: ("s", 1, None),
        8: ("f", 4, None),
        9: ("d", 8, None),
        10: ("B", 1, 0x00),
        11: ("H", 2, 0x0000),
        12: ("I", 4, 0x00000000),
        13: ("B", 1, 0xFF),
        14: ("q", 8, 0x7FFFFFFFFFFFFFFF),
        15: ("Q", 8, 0xFFFFFFFFFFFFFFFF),
        16: ("Q", 8, 0x0000000000000000),
    }

    while pos < end:
        header = file_bytes[pos]
        pos += 1

        if header & 0x80:
            # Compressed timestamp header
            local_msg_type = (header >> 5) & 0x03
            time_offset = header & 0x1F
            if last_ts_int:
                if time_offset >= (last_ts_int & 0x1F):
                    last_ts_int = (last_ts_int & ~0x1F) + time_offset
                else:
                    last_ts_int = (last_ts_int & ~0x1F) + 0x20 + time_offset
            is_def = False
        else:
            is_def = bool(header & 0x40)
            has_dev = bool(header & 0x20)
            local_msg_type = header & 0x0F

        if is_def:
            if pos + 5 > end:
                break
            arch = file_bytes[pos + 1]
            endian = ">" if arch == 1 else "<"
            global_num = struct.unpack_from(endian + "H", file_bytes, pos + 2)[0]
            num_fields = file_bytes[pos + 4]
            pos += 5
            fields = []
            for _ in range(num_fields):
                if pos + 3 > end:
                    break
                f_num, f_size, f_type = file_bytes[pos], file_bytes[pos + 1], file_bytes[pos + 2]
                fields.append((f_num, f_size, f_type & 0x1F))
                pos += 3
            dev_size = 0
            if has_dev and pos < end:
                num_dev = file_bytes[pos]
                pos += 1
                for _ in range(num_dev):
                    if pos + 3 > end:
                        break
                    dev_size += file_bytes[pos + 1]
                    pos += 3
            local_defs[local_msg_type] = (global_num, endian, fields, dev_size)
        else:
            defn = local_defs.get(local_msg_type)
            if not defn:
                break
            global_num, endian, fields, dev_size = defn
            rec_vals = {}
            for f_num, f_size, b_type in fields:
                if pos + f_size > end:
                    pos = end
                    break
                raw_slice = file_bytes[pos : pos + f_size]
                pos += f_size
                if global_num == 20 and f_num in (253, 0, 1, 2, 5, 6, 7, 73, 78):
                    fmt_c, sz, inv = BASE_FMT.get(b_type, ("B", 1, None))
                    if fmt_c != "s" and f_size == sz:
                        val = struct.unpack(endian + fmt_c, raw_slice)[0]
                        if inv is None or val != inv:
                            rec_vals[f_num] = val
            pos += dev_size

            if global_num == 20 and 0 in rec_vals and 1 in rec_vals:
                if 253 in rec_vals:
                    last_ts_int = rec_vals[253]
                ts_dt = FIT_EPOCH + timedelta(seconds=last_ts_int) if last_ts_int else None
                lat_deg = rec_vals[0] * SEMICIRCLES_TO_DEG
                lon_deg = rec_vals[1] * SEMICIRCLES_TO_DEG
                if not (-90.0 <= lat_deg <= 90.0 and -180.0 <= lon_deg <= 180.0):
                    continue
                ele = None
                if 78 in rec_vals:
                    ele = (rec_vals[78] / 5.0) - 500.0
                elif 2 in rec_vals:
                    ele = (rec_vals[2] / 5.0) - 500.0
                spd = None
                if 73 in rec_vals:
                    spd = rec_vals[73] / 1000.0
                elif 6 in rec_vals:
                    spd = rec_vals[6] / 1000.0
                dist = (rec_vals[5] / 100.0) if 5 in rec_vals else None
                pwr = float(rec_vals[7]) if 7 in rec_vals else None
                pts.append({
                    "lat": lat_deg,
                    "lon": lon_deg,
                    "ele": ele,
                    "ts": ts_dt,
                    "power": pwr,
                    "dist_m": dist,
                    "speed_ms": spd,
                })

    return "Actividad FIT", pts


def parse_route_from_any_file(file_bytes: bytes, filename: str, laps: int = 1, target_step_m: float = 100.0) -> dict:
    """
    Carga un circuito de ciclismo desde cualquier archivo compatible:
    .GPX, .FIT (actividad o course de Garmin/Wahoo/Strava), .TCX, .ZIP o .GZ.
    Retorna `route_data` listo para `simulate_race_strategy` con soporte multi-vuelta.
    """
    raw_bytes, inner_name = unwrap_activity_bytes(file_bytes, filename)
    ext = (inner_name.lower().rsplit(".", 1)[-1] if "." in inner_name else "").strip()

    if ext == "gpx":
        return parse_gpx_bytes(raw_bytes, laps=laps, target_step_m=target_step_m)

    if ext == "tcx":
        r_name, raw_pts = _parse_tcx_activity(raw_bytes)
    elif ext == "fit":
        try:
            r_name, raw_pts = _parse_fit_with_fitdecode(raw_bytes)
        except ImportError:
            r_name, raw_pts = _parse_fit_pure_python(raw_bytes)
        except Exception:
            r_name, raw_pts = _parse_fit_pure_python(raw_bytes)
    else:
        raise ValueError(f"Formato no soportado: .{ext}. Sube un archivo .fit, .gpx, .tcx o .zip.")

    if len(raw_pts) < 2:
        raise ValueError("El archivo no contiene suficientes puntos GPS (mínimo 2 puntos).")

    if r_name in ("Actividad FIT", "Actividad TCX"):
        r_name = inner_name.rsplit(".", 1)[0]

    # Rellenar elevaciones faltantes
    first_ele = next((p["ele"] for p in raw_pts if p["ele"] is not None), 0.0)
    last_e = first_ele
    lap_pts = []
    cum_d = 0.0
    prev_lat, prev_lon = None, None
    for p in raw_pts:
        e = p["ele"] if p["ele"] is not None else last_e
        last_e = e
        if prev_lat is not None:
            step_d = _haversine_m(prev_lat, prev_lon, p["lat"], p["lon"])
            if step_d < 0.5:
                continue
            cum_d += step_d
        lap_pts.append((p["lat"], p["lon"], e, cum_d))
        prev_lat, prev_lon = p["lat"], p["lon"]

    if len(lap_pts) < 2 or cum_d < 100.0:
        raise ValueError("La ruta del archivo es demasiado corta (< 100 metros).")

    lap_count = max(1, int(laps))
    eff_step = target_step_m if (cum_d * lap_count) <= 120000 else 150.0
    sampled = [lap_pts[0]]
    last_d = 0.0
    for pt in lap_pts[1:-1]:
        if pt[3] - last_d >= eff_step:
            sampled.append(pt)
            last_d = pt[3]
    if lap_pts[-1][3] - sampled[-1][3] >= 5.0:
        sampled.append(lap_pts[-1])

    n_s = len(sampled)
    smooth_eles = []
    for i in range(n_s):
        lo, hi = max(0, i - 1), min(n_s, i + 2)
        smooth_eles.append(sum(sampled[j][2] for j in range(lo, hi)) / float(hi - lo))

    lap_segments = []
    for i in range(n_s - 1):
        lat1, lon1, _, d1 = sampled[i]
        lat2, lon2, _, d2 = sampled[i + 1]
        seg_len = max(5.0, d2 - d1)
        e1, e2 = smooth_eles[i], smooth_eles[i + 1]
        grade = max(-0.22, min(0.25, (e2 - e1) / seg_len))
        lap_segments.append({
            "lat": (lat1 + lat2) / 2.0,
            "lon": (lon1 + lon2) / 2.0,
            "ele_m": (e1 + e2) / 2.0,
            "ele_start": e1,
            "ele_end": e2,
            "length_m": seg_len,
            "grade": grade,
            "bearing_deg": _bearing_deg(lat1, lon1, lat2, lon2),
            "turn_deg": 0.0,
        })
    for i in range(1, len(lap_segments)):
        lap_segments[i]["turn_deg"] = _angle_diff_deg(lap_segments[i - 1]["bearing_deg"], lap_segments[i]["bearing_deg"])

    segments = []
    cum_dist = 0.0
    elev_gain = 0.0
    elev_loss = 0.0
    for lap_idx in range(lap_count):
        for seg in lap_segments:
            sc = dict(seg)
            sc["dist_start_m"] = cum_dist
            cum_dist += sc["length_m"]
            sc["dist_end_m"] = cum_dist
            sc["dist_mid_km"] = (sc["dist_start_m"] + sc["length_m"] / 2.0) / 1000.0
            sc["lap"] = lap_idx + 1
            de = sc["ele_end"] - sc["ele_start"]
            if de > 0:
                elev_gain += de
            else:
                elev_loss += abs(de)
            segments.append(sc)

    eles = [s["ele_m"] for s in segments]
    grades = [s["grade"] * 100.0 for s in segments]
    return {
        "route_name": r_name if lap_count == 1 else f"{r_name} ({lap_count} vueltas)",
        "laps": lap_count,
        "total_distance_km": round(cum_dist / 1000.0, 2),
        "elevation_gain_m": round(elev_gain, 0),
        "elevation_loss_m": round(elev_loss, 0),
        "min_ele_m": round(min(eles), 0),
        "max_ele_m": round(max(eles), 0),
        "avg_ele_m": round(sum(eles) / len(eles), 0),
        "max_grade_pct": round(max(grades), 1),
        "min_grade_pct": round(min(grades), 1),
        "segments": segments,
    }


def parse_activity_file(file_bytes: bytes, filename: str) -> dict:
    """
    Lee un archivo de carrera/entreno (.fit, .tcx, .gpx, .zip o .gz) y retorna telemetría limpia,
    metadatos de fecha/hora/ubicación, métricas reales (tiempo, velocidad, AP, NP)
    y `route_data` compatible con el simulador.
    """
    file_bytes, filename = unwrap_activity_bytes(file_bytes, filename)
    ext = (filename.lower().rsplit(".", 1)[-1] if "." in filename else "").strip()
    if ext == "gpx":
        name, raw_pts = _parse_gpx_activity(file_bytes)
    elif ext == "tcx":
        name, raw_pts = _parse_tcx_activity(file_bytes)
    elif ext == "fit":
        try:
            name, raw_pts = _parse_fit_with_fitdecode(file_bytes)
        except ImportError:
            name, raw_pts = _parse_fit_pure_python(file_bytes)
        except Exception:
            name, raw_pts = _parse_fit_pure_python(file_bytes)
    else:
        raise ValueError(f"Formato no soportado: .{ext}. Sube un archivo .fit, .tcx o .gpx.")

    if len(raw_pts) < 10:
        raise ValueError("El archivo contiene muy pocos puntos GPS (< 10) para calibrar el CdA.")

    # Rellenar elevaciones faltantes y suavizar suavemente (ventana de 5 puntos)
    first_ele = next((p["ele"] for p in raw_pts if p["ele"] is not None), 0.0)
    last_e = first_ele
    for p in raw_pts:
        if p["ele"] is None:
            p["ele"] = last_e
        else:
            last_e = p["ele"]

    n = len(raw_pts)
    smooth_eles = []
    for i in range(n):
        lo = max(0, i - 2)
        hi = min(n, i + 3)
        smooth_eles.append(sum(raw_pts[j]["ele"] for j in range(lo, hi)) / float(hi - lo))
    for i in range(n):
        raw_pts[i]["ele"] = smooth_eles[i]

    # Construir intervalos de telemetría (resampling ligero cada ~5 segundos o ~40 metros para máxima limpieza)
    first_ts = next((p["ts"] for p in raw_pts if p["ts"] is not None), None)
    records = []
    cum_dist = 0.0
    moving_time_s = 0.0
    elev_gain = 0.0
    power_samples = []
    time_samples = []

    anchor = raw_pts[0]
    anchor_dist = anchor["dist_m"] if anchor["dist_m"] is not None else 0.0
    for i in range(1, n):
        curr = raw_pts[i]
        seg_d = _haversine_m(anchor["lat"], anchor["lon"], curr["lat"], curr["lon"])
        if curr["dist_m"] is not None and anchor["dist_m"] is not None:
            d_sensor = curr["dist_m"] - anchor["dist_m"]
            if d_sensor > 0 and abs(d_sensor - seg_d) < max(35.0, seg_d * 0.35):
                seg_d = d_sensor

        dt = None
        if curr["ts"] is not None and anchor["ts"] is not None:
            dt = (curr["ts"] - anchor["ts"]).total_seconds()

        # Esperar al menos 3s o 25m para reducir ruido GPS en pendiente/velocidad
        if (dt is not None and dt < 3.0) and seg_d < 25.0 and i < n - 1:
            continue
        if seg_d < 3.0 and (dt is None or dt < 1.0):
            continue

        if dt is None or dt <= 0 or dt > 60.0:
            # Pausa larga o sin timestamps: avanzar ancla sin contar ese hueco
            if dt is not None and dt > 60.0:
                anchor = curr
                continue
            v_est = curr["speed_ms"] if (curr["speed_ms"] and curr["speed_ms"] > 1.0) else 8.5
            dt = seg_d / v_est

        v_ms = seg_d / dt
        if v_ms > 30.0:  # glitch GPS > 108 km/h
            anchor = curr
            continue

        de = curr["ele"] - anchor["ele"]
        if de > 0:
            elev_gain += de
        grade = max(-0.20, min(0.22, de / max(5.0, seg_d)))
        bearing = _bearing_deg(anchor["lat"], anchor["lon"], curr["lat"], curr["lon"])

        # Promediar potencia del bloque [anchor..curr]
        pwr_val = curr["power"] if curr["power"] is not None else anchor["power"]

        if v_ms >= 1.5:  # en movimiento (>= 5.4 km/h)
            moving_time_s += dt
            cum_dist += seg_d
            if pwr_val is not None:
                power_samples.append(max(0.0, float(pwr_val)))
                time_samples.append(dt)

            records.append({
                "dist_start_m": cum_dist - seg_d,
                "dist_end_m": cum_dist,
                "dist_mid_km": (cum_dist - seg_d / 2.0) / 1000.0,
                "length_m": seg_d,
                "dt": dt,
                "cum_time_s": moving_time_s,
                "speed_ms": v_ms,
                "lat": (anchor["lat"] + curr["lat"]) / 2.0,
                "lon": (anchor["lon"] + curr["lon"]) / 2.0,
                "ele_m": (anchor["ele"] + curr["ele"]) / 2.0,
                "ele_start": anchor["ele"],
                "ele_end": curr["ele"],
                "grade": grade,
                "bearing_deg": bearing,
                "turn_deg": 0.0,
                "power_w": float(pwr_val) if pwr_val is not None else None,
                "lap": 1,
            })

        anchor = curr

    if len(records) < 5 or cum_dist < 500.0:
        raise ValueError("No se encontraron suficientes tramos válidos en movimiento (>= 500 m).")

    has_power = len(power_samples) >= max(5, int(len(records) * 0.4))
    avg_power_w = None
    norm_power_w = None
    if has_power and sum(time_samples) > 0:
        avg_power_w = round(sum(p * t for p, t in zip(power_samples, time_samples)) / sum(time_samples), 1)
        norm_power_w = round(_compute_normalized_power(power_samples, time_samples), 1)

    eles = [r["ele_m"] for r in records]
    grades = [r["grade"] * 100.0 for r in records]
    lat_avg = sum(r["lat"] for r in records) / len(records)
    lon_avg = sum(r["lon"] for r in records) / len(records)

    # Estimar hora local aproximada desde longitud si timestamp viene en UTC
    race_date_obj = None
    start_hour_local = 7
    if first_ts is not None:
        if first_ts.tzinfo is not None:
            tz_offset_h = int(round(lon_avg / 15.0))
            local_ts = first_ts.astimezone(timezone(timedelta(hours=tz_offset_h)))
            race_date_obj = local_ts.date()
            start_hour_local = local_ts.hour
        else:
            race_date_obj = first_ts.date()
            start_hour_local = first_ts.hour

    total_dist_km = round(cum_dist / 1000.0, 2)
    avg_speed_kmh = round(total_dist_km / max(1e-4, moving_time_s / 3600.0), 2)

    route_data = {
        "route_name": name if name != "Actividad GPX" else filename,
        "laps": 1,
        "total_distance_km": total_dist_km,
        "elevation_gain_m": round(elev_gain, 0),
        "elevation_loss_m": 0.0,
        "min_ele_m": round(min(eles), 0),
        "max_ele_m": round(max(eles), 0),
        "avg_ele_m": round(sum(eles) / len(eles), 0),
        "max_grade_pct": round(max(grades), 1),
        "min_grade_pct": round(min(grades), 1),
        "segments": records,
    }

    return {
        "name": route_data["route_name"],
        "filename": filename,
        "activity_date": race_date_obj,
        "start_hour": start_hour_local,
        "lat": round(lat_avg, 5),
        "lon": round(lon_avg, 5),
        "distance_km": total_dist_km,
        "elevation_gain_m": round(elev_gain, 0),
        "moving_time_s": round(moving_time_s, 1),
        "moving_time_str": format_hms(moving_time_s),
        "avg_speed_kmh": avg_speed_kmh,
        "has_power": has_power,
        "avg_power_w": avg_power_w,
        "norm_power_w": norm_power_w,
        "records": records,
        "route_data": route_data,
    }


# ═════════════════════════════════════════════════════════════════════
#  MÉTODO A: ESTIMACIÓN INVERSA DE CdA DESDE TELEMETRÍA CON POTENCIA
# ═════════════════════════════════════════════════════════════════════

def estimate_cda_from_activity(
    activity: dict,
    total_mass_kg: float,
    crr: float = 0.0038,
    efficiency: float = 0.974,
    temp_c: float = 24.0,
    rel_humidity_pct: float = 65.0,
    wind_speed_kmh: float = 10.0,
    wind_from_deg: float = 0.0,
    hourly_weather: list = None,
) -> dict:
    """
    Estima el CdA real (m²) del ciclista resolviendo el balance energético de Martin et al.
    (con término de energía cinética 0.5 * m * (v2² - v1²) y clima horario ERA5) sobre todos
    los tramos aerodinámicamente limpios del archivo.
    """
    records = activity.get("records") or []
    if not activity.get("has_power") or len(records) < 10:
        raise ValueError("El archivo no contiene datos de potencia (vatios) suficientes para estimación directa.")

    avg_route_ele = float(activity["route_data"]["avg_ele_m"])
    total_e_aero = 0.0
    total_aero_denom = 0.0
    valid_secs = 0.0
    total_secs = 0.0

    # También dividir en bloques de ~10 km para analizar consistencia / fatiga postural
    chunk_km = max(5.0, round(activity["distance_km"] / 8.0))
    chunk_acc = {}

    prev_v = records[0]["speed_ms"]
    for rec in records:
        dt = rec["dt"]
        total_secs += dt
        v = rec["speed_ms"]
        p_w = rec["power_w"]
        g = rec["grade"]

        # Calcular cambio de energía cinética antes de filtrar
        dv2 = (v * v) - (prev_v * prev_v)
        prev_v = v

        # Filtrar tramos donde el ciclista frena, para, o baja muy rápido sin pedalear:
        # Solo usar tramos con velocidad >= 20 km/h (5.55 m/s), potencia >= 60 W y pendiente [-4.5%, +7%]
        if p_w is None or p_w < 60.0 or v < 5.55 or g < -0.045 or g > 0.07:
            continue

        seg_t_c, seg_hum, seg_w_kmh, seg_w_deg, seg_p_hpa = _sample_hourly_weather(
            hourly_weather=hourly_weather,
            elapsed_hours=rec["cum_time_s"] / 3600.0,
            fallback_temp=float(temp_c),
            fallback_hum=float(rel_humidity_pct),
            fallback_wind_kmh=float(wind_speed_kmh),
            fallback_wind_deg=float(wind_from_deg),
        )
        local_p_hpa = None
        if seg_p_hpa is not None:
            dh = float(rec["ele_m"]) - avg_route_ele
            local_p_hpa = float(seg_p_hpa) * math.exp(-dh / 8400.0)

        rho = calc_air_density(rec["ele_m"], seg_t_c, seg_hum, surface_pressure_hpa=local_p_hpa)
        rel_angle = math.radians(seg_w_deg - rec["bearing_deg"])
        headwind_ms = (seg_w_kmh / 3.6) * math.cos(rel_angle)
        v_air = v + headwind_ms
        if v_air <= 2.5:
            continue

        cos_a = 1.0 / math.sqrt(1.0 + g * g)
        sin_a = g * cos_a

        e_wheel = p_w * efficiency * dt
        e_roll = total_mass_kg * GRAVITY * crr * cos_a * v * dt
        e_grav = total_mass_kg * GRAVITY * sin_a * v * dt
        e_kin = 0.5 * total_mass_kg * dv2

        e_aero_i = e_wheel - e_roll - e_grav - e_kin
        denom_i = 0.5 * rho * v_air * abs(v_air) * v * dt

        # Descartar outliers instantáneos extremos (ej. drafting muy fuerte o frenazo súbito)
        inst_cda = e_aero_i / max(1e-6, denom_i)
        if not (0.10 <= inst_cda <= 0.65):
            continue

        total_e_aero += e_aero_i
        total_aero_denom += denom_i
        valid_secs += dt

        c_idx = int(rec["dist_mid_km"] // chunk_km)
        if c_idx not in chunk_acc:
            chunk_acc[c_idx] = {"e_aero": 0.0, "denom": 0.0, "dist_start": c_idx * chunk_km, "secs": 0.0, "pwr_t": 0.0, "spd_t": 0.0}
        chunk_acc[c_idx]["e_aero"] += e_aero_i
        chunk_acc[c_idx]["denom"] += denom_i
        chunk_acc[c_idx]["secs"] += dt
        chunk_acc[c_idx]["pwr_t"] += p_w * dt
        chunk_acc[c_idx]["spd_t"] += v * 3.6 * dt

    if total_aero_denom <= 0 or valid_secs < 60.0:
        raise ValueError("No hubo suficientes segmentos limpios a velocidad aerodinámica (>20 km/h) con potencia válida.")

    solved_cda = round(max(0.150, min(0.550, total_e_aero / total_aero_denom)), 4)

    # Perfil de CdA por tramos (permite ver pérdida de postura aero por fatiga o efecto viento/drafting)
    chunk_rows = []
    chunk_cdas = []
    for c_idx in sorted(chunk_acc.keys()):
        ch = chunk_acc[c_idx]
        if ch["denom"] > 0 and ch["secs"] >= 30.0:
            c_cda = round(ch["e_aero"] / ch["denom"], 4)
            chunk_cdas.append(c_cda)
            chunk_rows.append({
                "Tramo": f"Km {ch['dist_start']:.0f}–{min(activity['distance_km'], ch['dist_start'] + chunk_km):.0f}",
                "CdA Est. (m²)": c_cda,
                "Potencia (W)": round(ch["pwr_t"] / ch["secs"], 0),
                "Vel. (km/h)": round(ch["spd_t"] / ch["secs"], 1),
            })

    # Calcular estabilidad y nivel de confianza
    coverage_pct = round(100.0 * valid_secs / max(1.0, total_secs), 1)
    std_cda = 0.0
    if len(chunk_cdas) >= 2:
        mean_c = sum(chunk_cdas) / len(chunk_cdas)
        std_cda = math.sqrt(sum((x - mean_c) ** 2 for x in chunk_cdas) / len(chunk_cdas))

    if coverage_pct >= 55.0 and std_cda <= 0.025 and valid_secs >= 600:
        confidence = "🟢 Alta"
        conf_note = "Excelente consistencia entre tramos. CdA muy fiable para modelar futuras carreras."
    elif coverage_pct >= 30.0 and std_cda <= 0.045:
        confidence = "🟡 Media"
        conf_note = "Variación moderada entre tramos (posibles cambios de postura aero, ráfagas o tráfico en carrera)."
    else:
        confidence = "🟠 Referencial"
        conf_note = "Alta dispersión entre tramos (puede haber habido drafting, viento muy variable o tramos lentos/técnicos)."

    # Detectar fatiga postural (último tercio vs primer tercio)
    fatigue_note = None
    if len(chunk_cdas) >= 4:
        first_third = sum(chunk_cdas[: len(chunk_cdas) // 3]) / max(1, len(chunk_cdas) // 3)
        last_third = sum(chunk_cdas[-(len(chunk_cdas) // 3) :]) / max(1, len(chunk_cdas) // 3)
        diff_posture = last_third - first_third
        if diff_posture >= 0.018:
            fatigue_note = (
                f"📈 Se observa aumento de CdA al final de la carrera (`{first_third:.3f}` → `{last_third:.3f} m²`, `+{diff_posture:.3f}`), "
                "señal clásica de fatiga postural (salirse de los acoples en los últimos km)."
            )
        elif diff_posture <= -0.015:
            fatigue_note = (
                f"📉 El CdA aparente bajó al final (`{first_third:.3f}` → `{last_third:.3f} m²`), "
                "lo que suele indicar mejor postura aero o viento más favorable en el cierre."
            )

    return {
        "cda": solved_cda,
        "method": "Telemetría directa segundo a segundo (Balance Energético + Energía Cinética)",
        "confidence": confidence,
        "confidence_note": conf_note,
        "fatigue_note": fatigue_note,
        "coverage_pct": coverage_pct,
        "std_cda": round(std_cda, 4),
        "chunks": chunk_rows,
        "classification": classify_cda(solved_cda),
    }


# ═════════════════════════════════════════════════════════════════════
#  MÉTODO B: ESTIMACIÓN INVERSA POR RESUMEN (TIEMPO REAL + VATIOS + GPX)
# ═════════════════════════════════════════════════════════════════════

def estimate_cda_from_race_summary(
    route_data: dict,
    actual_time_sec: float,
    actual_avg_power_w: float,
    rider_weight_kg: float,
    bike_and_gear_kg: float = 9.5,
    crr: float = 0.0038,
    efficiency: float = 0.974,
    temp_c: float = 24.0,
    rel_humidity_pct: float = 65.0,
    wind_speed_kmh: float = 10.0,
    wind_from_deg: float = 0.0,
    hourly_weather: list = None,
) -> dict:
    """
    Halla el CdA exacto (m²) mediante bisección inversa sobre `simulate_race_strategy`
    cuando solo se dispone del GPX + tiempo oficial real + potencia media/NP de la carrera.
    """
    if actual_time_sec <= 60 or actual_avg_power_w <= 40:
        raise ValueError("Ingresa un tiempo real (> 1 min) y una potencia media válida (> 40 W).")

    lo, hi = 0.140, 0.600
    best_cda = 0.280
    best_sim = None

    for _ in range(18):
        mid = 0.5 * (lo + hi)
        eq = {
            "cda": mid,
            "bike_and_gear_kg": float(bike_and_gear_kg),
            "crr": float(crr),
            "efficiency": float(efficiency),
            "sail_factor": 0.04,
        }
        # Usamos ftp=actual_avg_power_w y target_if=1.0 con estrategia constante para igualar la potencia media real
        sim = simulate_race_strategy(
            route_data=route_data,
            rider_weight_kg=float(rider_weight_kg),
            ftp_w=float(actual_avg_power_w),
            target_if=1.0,
            equipment=eq,
            temp_c=float(temp_c),
            rel_humidity_pct=float(rel_humidity_pct),
            wind_speed_kmh=float(wind_speed_kmh),
            wind_from_deg=float(wind_from_deg),
            strategy="constant",
            max_climb_pct_ftp=1.15,
            hourly_weather=hourly_weather,
        )
        sim_t = sim["summary"]["total_time_s"]
        best_cda = mid
        best_sim = sim

        if abs(sim_t - actual_time_sec) <= 1.0:
            break
        # Si el tiempo simulado es MENOR (más rápido) que el real, el atleta tenía MAYOR CdA
        if sim_t < actual_time_sec:
            lo = mid
        else:
            hi = mid

    solved_cda = round(best_cda, 4)
    err_s = round(best_sim["summary"]["total_time_s"] - actual_time_sec, 1) if best_sim else 0.0
    note = (
        f"Ajuste exacto al tiempo oficial ({format_hms(actual_time_sec)}, diferencia de modelo: {err_s:+.0f} s). "
        "Ten en cuenta que paradas en avituallamientos o drafting en carrera pueden mover ligeramente el CdA estimado."
    )
    return {
        "cda": solved_cda,
        "method": "Calibración inversa por Resumen Oficial (GPX + Tiempo Real + Potencia + Clima)",
        "confidence": "🟡 Media-Alta",
        "confidence_note": note,
        "fatigue_note": None,
        "coverage_pct": 100.0,
        "std_cda": None,
        "chunks": [],
        "classification": classify_cda(solved_cda),
    }


def classify_cda(cda: float) -> dict:
    """Clasifica el CdA obtenido según referencias de ciclismo de ruta y triatlón."""
    if cda < 0.215:
        return {"level": "🏆 Élite Mundial TT", "color": "#00FF88", "tip": "Aerodinámica sobresaliente de nivel profesional en cabra."}
    if cda < 0.245:
        return {"level": "🔥 Muy Bueno (TT Competitivo)", "color": "#00EEFF", "tip": "Posición muy eficiente para 70.3 / Ironman y contrarreloj."}
    if cda < 0.275:
        return {"level": "✅ Bueno (TT Estándar / Ruta Aero Acoplada)", "color": "#38BDF8", "tip": "Sólido. Afinar casco, altura de acoples o hidratación puede rascar -0.015 a -0.025 m²."}
    if cda < 0.315:
        return {"level": "⚡ Ruta Aero / Margen de Mejora en TT", "color": "#FFD700", "tip": "Normal para bici de ruta; si fue en bici TT, hay gran potencial de ahorro con bike fit aero."}
    return {"level": "⛰️ Posición Erguida / Ruta Escaladora", "color": "#FF8787", "tip": "Típico de bici de ruta en escaladores o postura alta. Gran margen de mejora aerodinámica en llano."}
