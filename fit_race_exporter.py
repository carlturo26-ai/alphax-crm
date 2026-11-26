"""
fit_race_exporter.py — Generador nativo de archivos binarios Garmin .FIT (Protocolo 2.0)
en Python puro (sin dependencias externas) para AlphaX Race Planner (Ciclismo y Running).

Genera dos archivos complementarios estilo Best Bike Split para Garmin Edge / Forerunner / Fenix / Wahoo / Coros:
  1. FIT Course (`file_id.type = 6`):
     - Trazado GPS completo (`record` #20) con altimetría, velocidad, potencia objetivo y
       timestamps simulados del motor físico AlphaX -> activa el **Virtual Partner / Corredor Virtual**
       en tiempo real (muestra si vas adelantado o atrasado respecto al plan).
     - Alertas GPS emergentes (`course_point` #32) en cada cambio de segmento táctico
       (ej. "245-265W SUB", "4:10-4:20 SUB") + recordatorios opcionales de nutrición/geles.
  2. FIT Workout (`file_id.type = 5`):
     - Entrenamiento estructurado por **distancia** (`workout` #26 + `workout_step` #27),
       comprimido inteligentemente a <= 50 pasos (límite oficial de Garmin).
     - Alertas activas de rango en pantalla con pitido/vibración:
         * Ciclismo o Running Potencia (Stryd/Garmin): `target_type = power` (Vatios absolutos, offset +1000).
         * Running Ritmo (Pace): `target_type = speed` (m/s * 1000, rango rápido/lento).
"""

from __future__ import annotations

import hashlib
import math
import struct
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ═════════════════════════════════════════════════════════════════════
#  CONSTANTES DEL PROTOCOLO GARMIN FIT (SDK Profile 21.133)
# ═════════════════════════════════════════════════════════════════════

FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
DEG_TO_SEMICIRCLES = (2 ** 31) / 180.0
SEMICIRCLES_TO_DEG = 180.0 / (2 ** 31)

# Base types FIT (Little Endian: bit 0x80 para tipos multibyte)
BT_ENUM = 0x00       # 1B unsigned enum, invalid 0xFF
BT_UINT8 = 0x02      # 1B uint8, invalid 0xFF
BT_STRING = 0x07     # NB null-terminated UTF-8 string
BT_UINT16 = 0x84     # 2B uint16 LE, invalid 0xFFFF
BT_SINT32 = 0x85     # 4B sint32 LE, invalid 0x7FFFFFFF
BT_UINT32 = 0x86     # 4B uint32 LE, invalid 0xFFFFFFFF
BT_UINT32Z = 0x8C    # 4B uint32z LE, invalid 0x00000000

# Global Message Numbers
MESG_FILE_ID = 0
MESG_LAP = 19
MESG_RECORD = 20
MESG_EVENT = 21
MESG_WORKOUT = 26
MESG_WORKOUT_STEP = 27
MESG_COURSE = 31
MESG_COURSE_POINT = 32

# Enums FIT
FILE_TYPE_WORKOUT = 5
FILE_TYPE_COURSE = 6

MANUFACTURER_GARMIN = 1
GARMIN_PRODUCT_CONNECT = 65534

SPORT_RUNNING = 1
SPORT_CYCLING = 2

EVENT_TIMER = 0
EVENT_TYPE_START = 0
EVENT_TYPE_STOP_ALL = 4

# course_point enum
CP_GENERIC = 0
CP_SUMMIT = 1
CP_VALLEY = 2
CP_WATER = 3
CP_FOOD = 4
CP_DANGER = 5
CP_FOURTH_CAT = 10
CP_FIRST_CAT = 13

# workout_step enums
WKT_DUR_TIME = 0
WKT_DUR_DISTANCE = 1
WKT_TARGET_SPEED = 0
WKT_TARGET_OPEN = 2
WKT_TARGET_POWER = 4
INTENSITY_ACTIVE = 0
INTENSITY_RECOVERY = 4

# course_capabilities bitmask (processed|valid|time|distance|position|power|navigation)
COURSE_CAP_CYCLING = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000040 | 0x00000400
COURSE_CAP_RUNNING = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000400
WORKOUT_CAP_FITNESS = 0x00000020

# Tabla CRC-16 oficial Garmin FIT SDK
_FIT_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def fit_crc16(data: bytes) -> int:
    """Calcula el CRC-16 oficial del protocolo Garmin FIT."""
    crc = 0
    for byte in data:
        tmp = _FIT_CRC_TABLE[crc & 0x0F]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _FIT_CRC_TABLE[byte & 0x0F]
        tmp = _FIT_CRC_TABLE[crc & 0x0F]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _FIT_CRC_TABLE[(byte >> 4) & 0x0F]
    return crc & 0xFFFF


def _clean_ascii(text: str, max_len: int) -> str:
    """
    Convierte texto a ASCII limpio compatible con pantallas Garmin/Wahoo/Coros
    (elimina tildes/emojis para evitar símbolos rotos en el reloj).
    """
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text))
    cleaned = "".join(ch for ch in norm if 32 <= ord(ch) <= 126)
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_len].strip()


def _pack_fixed_string(text: str, byte_len: int) -> bytes:
    """Empaqueta un string ASCII de longitud fija terminado en NUL (byte_len incluye el NUL)."""
    clean = _clean_ascii(text, max(1, byte_len - 1)).encode("ascii", errors="ignore")
    if len(clean) >= byte_len:
        clean = clean[: byte_len - 1]
    return clean + b"\x00" * (byte_len - len(clean))


def _to_semicircles(deg: float) -> int:
    val = int(round(float(deg) * DEG_TO_SEMICIRCLES))
    return max(-2147483647, min(2147483647, val))


def _to_fit_timestamp(dt: Optional[datetime] = None) -> int:
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int(round((dt - FIT_EPOCH).total_seconds()))
    return max(1, min(0xFFFFFFFE, secs))


def _deterministic_serial(seed_text: str) -> int:
    digest = hashlib.sha256(seed_text.encode("utf-8", errors="ignore")).digest()
    val = struct.unpack("<I", digest[:4])[0]
    return max(1, val & 0x7FFFFFFF)


def _format_pace_mmss(sec_per_km: float) -> str:
    sec = max(120, min(1500, int(round(sec_per_km))))
    return f"{sec // 60}:{sec % 60:02d}"


def _format_hms_short(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ═════════════════════════════════════════════════════════════════════
#  ESCRITOR BINARIO FIT 2.0 LIGERO
# ═════════════════════════════════════════════════════════════════════

class _FitBinaryWriter:
    """Ensamblador binario FIT 2.0 (Little Endian) con definición de mensajes y CRC-16."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._defs: Dict[int, List[Tuple[int, int, int]]] = {}

    def define(self, local_num: int, global_num: int, fields: List[Tuple[int, int, int]]) -> None:
        """
        Emite un Definition Message para `local_num` (0..15).
        `fields`: lista de tuplas (field_def_num, byte_size, base_type).
        """
        local_num &= 0x0F
        self._buf.append(0x40 | local_num)  # Normal Header: Definition bit = 1
        self._buf.append(0x00)              # Reserved
        self._buf.append(0x00)              # Architecture: 0 = Little Endian
        self._buf.extend(struct.pack("<H", global_num))
        self._buf.append(len(fields))
        for f_num, f_size, f_type in fields:
            self._buf.append(f_num & 0xFF)
            self._buf.append(f_size & 0xFF)
            self._buf.append(f_type & 0xFF)
        self._defs[local_num] = fields

    def write_data(self, local_num: int, values: List[object]) -> None:
        """Emite un Data Message con los valores exactos en el orden de su definición."""
        local_num &= 0x0F
        fields = self._defs[local_num]
        if len(values) != len(fields):
            raise ValueError(f"Número de valores ({len(values)}) != campos definidos ({len(fields)})")
        self._buf.append(local_num)  # Normal Header: Data message
        for (_, f_size, f_type), val in zip(fields, values):
            if f_type in (BT_ENUM, BT_UINT8):
                v = 0xFF if val is None else max(0, min(0xFF, int(val)))
                self._buf.append(v)
            elif f_type == BT_UINT16:
                v = 0xFFFF if val is None else max(0, min(0xFFFF, int(val)))
                self._buf.extend(struct.pack("<H", v))
            elif f_type == BT_SINT32:
                v = 0x7FFFFFFF if val is None else max(-2147483648, min(2147483647, int(val)))
                self._buf.extend(struct.pack("<i", v))
            elif f_type == BT_UINT32:
                v = 0xFFFFFFFF if val is None else max(0, min(0xFFFFFFFF, int(val)))
                self._buf.extend(struct.pack("<I", v))
            elif f_type == BT_UINT32Z:
                v = 0x00000000 if val is None else max(0, min(0xFFFFFFFF, int(val)))
                self._buf.extend(struct.pack("<I", v))
            elif f_type == BT_STRING:
                self._buf.extend(_pack_fixed_string(str(val or ""), f_size))
            else:
                raise ValueError(f"Base type FIT no soportado: {hex(f_type)}")

    def build(self) -> bytes:
        """Construye el archivo .FIT completo con cabecera de 14 bytes + header CRC + file CRC."""
        data_bytes = bytes(self._buf)
        header_no_crc = struct.pack(
            "<BBHI4s",
            14,             # header_size
            0x20,           # protocol_version 2.0
            2133,           # profile_version 21.33
            len(data_bytes),
            b".FIT",
        )
        header_crc = fit_crc16(header_no_crc)
        header = header_no_crc + struct.pack("<H", header_crc)
        body = header + data_bytes
        file_crc = fit_crc16(body)
        return body + struct.pack("<H", file_crc)


# ═════════════════════════════════════════════════════════════════════
#  NORMALIZACIÓN Y SEGMENTACIÓN TÁCTICA INTELIGENTE (ESTILO BBS)
# ═════════════════════════════════════════════════════════════════════

def _normalize_sim_segments(sim_segments: List[Dict]) -> List[Dict]:
    """
    Unifica el formato de segmentos provenientes de `race_strategy_engine` (ciclismo)
    y `running_strategy_engine` (running) en una estructura estándar.
    """
    norm: List[Dict] = []
    prev_dist_m = 0.0
    prev_cum_t = 0.0

    for sg in sim_segments or []:
        dist_end_m = float(sg.get("dist_end_km", 0.0)) * 1000.0
        if dist_end_m <= prev_dist_m + 0.5:
            continue
        length_m = dist_end_m - prev_dist_m
        cum_t = float(sg.get("cum_time_s", prev_cum_t))
        dt_s = float(sg.get("time_seg_s", sg.get("dt_s", max(0.1, cum_t - prev_cum_t))))
        if dt_s <= 0.05:
            dt_s = max(0.1, cum_t - prev_cum_t)
        speed_ms = float(sg.get("speed_ms", 0.0))
        if speed_ms <= 0.1:
            spd_kmh = float(sg.get("speed_kmh", 0.0))
            speed_ms = (spd_kmh / 3.6) if spd_kmh > 0.2 else (length_m / max(0.1, dt_s))

        norm.append({
            "dist_start_m": prev_dist_m,
            "dist_end_m": dist_end_m,
            "length_m": length_m,
            "lat": float(sg.get("lat", 0.0)),
            "lon": float(sg.get("lon", 0.0)),
            "ele_m": float(sg.get("ele_m", 0.0)),
            "grade_pct": float(sg.get("grade_pct", 0.0)),
            "power_w": float(sg.get("power_w", 0.0)),
            "speed_ms": max(0.5, speed_ms),
            "dt_s": dt_s,
            "cum_time_s": max(prev_cum_t + 0.05, cum_t),
            "headwind_kmh": float(sg.get("headwind_kmh", 0.0)),
        })
        prev_dist_m = dist_end_m
        prev_cum_t = norm[-1]["cum_time_s"]

    return norm


def _terrain_bucket(grade_pct: float, sport: str) -> str:
    if sport == "cycling":
        if grade_pct >= 4.5:
            return "climb_steep"
        if grade_pct >= 1.8:
            return "climb"
        if grade_pct <= -4.0:
            return "descent_steep"
        if grade_pct <= -1.8:
            return "descent"
        return "flat"
    else:
        if grade_pct >= 3.5:
            return "climb_steep"
        if grade_pct >= 1.3:
            return "climb"
        if grade_pct <= -3.5:
            return "descent_steep"
        if grade_pct <= -1.3:
            return "descent"
        return "flat"


def _terrain_short_es(bucket: str) -> Tuple[str, str, int]:
    """Retorna (etiqueta_corta_garmin, etiqueta_ui, course_point_type)."""
    mapping = {
        "climb_steep": ("SUB+", "⛰️ Subida Fuerte", CP_FIRST_CAT),
        "climb": ("SUB", "↗️ Subida", CP_SUMMIT),
        "flat": ("LLANO", "➡️ Llano", CP_GENERIC),
        "descent": ("BAJ", "↘️ Bajada", CP_VALLEY),
        "descent_steep": ("DESC", "⬇️ Descenso", CP_VALLEY),
    }
    return mapping.get(bucket, ("TRAMO", "➡️ Llano", CP_GENERIC))


def _summarize_block(
    items: List[Dict],
    sport: str,
    target_metric: str,
    ref_threshold_w: float,
    tolerance_pct: float,
    pace_tol_sec: float,
    idx: int,
) -> Dict:
    """Agrega una lista de micro-segmentos en un bloque táctico completo para el dispositivo."""
    d_start_m = items[0]["dist_start_m"]
    d_end_m = items[-1]["dist_end_m"]
    length_m = max(10.0, d_end_m - d_start_m)
    tot_t = max(0.1, sum(x["dt_s"] for x in items))
    cum_t_start = max(0.0, items[0]["cum_time_s"] - items[0]["dt_s"])
    cum_t_end = items[-1]["cum_time_s"]

    avg_power_w = sum(x["power_w"] * x["dt_s"] for x in items) / tot_t
    avg_speed_ms = length_m / tot_t
    avg_grade_pct = sum(x["grade_pct"] * x["length_m"] for x in items) / length_m
    avg_hw_kmh = sum(x["headwind_kmh"] * x["length_m"] for x in items) / length_m
    pace_sec_km = 1000.0 / max(0.5, avg_speed_ms)

    bucket = _terrain_bucket(avg_grade_pct, sport)
    short_tag, ui_terrain, cp_type = _terrain_short_es(bucket)

    # Detectar si es bajada muy pronunciada de ahorro energético en ciclismo
    is_coast_descent = (
        sport == "cycling"
        and avg_grade_pct <= -3.8
        and ref_threshold_w > 0
        and avg_power_w < ref_threshold_w * 0.55
    )

    # Calcular bandas de alerta para Workout
    tol_frac = max(0.02, min(0.15, float(tolerance_pct) / 100.0))
    if is_coast_descent:
        p_low = 0
        p_high = max(80, int(round(avg_power_w * 1.25)))
        intensity = INTENSITY_RECOVERY
    else:
        min_band_w = 10.0 if sport == "cycling" else 6.0
        half_w = max(min_band_w, avg_power_w * tol_frac)
        p_low = max(10, int(round(avg_power_w - half_w)))
        p_high = max(p_low + 5, int(round(avg_power_w + half_w)))
        intensity = INTENSITY_ACTIVE

    # Rango de ritmo / velocidad (nota: ritmo más LENTO = velocidad MÁS BAJA = speed_low)
    half_pace_s = max(3.0, min(25.0, float(pace_tol_sec)))
    pace_fast_s = max(120.0, pace_sec_km - half_pace_s)
    pace_slow_s = min(1200.0, pace_sec_km + half_pace_s)
    speed_low_ms = 1000.0 / pace_slow_s
    speed_high_ms = 1000.0 / pace_fast_s

    pct_thr = (avg_power_w / max(50.0, ref_threshold_w)) * 100.0 if ref_threshold_w > 0 else 0.0

    # Textos concisos optimizados para pantallas Garmin Edge / Forerunner / Fenix
    if target_metric == "power":
        if is_coast_descent:
            cp_label = _clean_ascii(f"AERO <{p_high}W {short_tag}", 15)
            target_ui = f"0–{p_high} W (Ahorro)"
        else:
            cp_label = _clean_ascii(f"{p_low}-{p_high}W {short_tag}", 15)
            target_ui = f"{p_low}–{p_high} W ({int(round(avg_power_w))}W)"
        step_name = _clean_ascii(f"S{idx} {short_tag} {int(round(avg_power_w))}W", 22)
        notes = _clean_ascii(
            f"Km {d_start_m/1000:.1f}-{d_end_m/1000:.1f} | {short_tag} {avg_grade_pct:+.1f}% | "
            f"Obj {p_low}-{p_high}W ({pct_thr:.0f}%) | {avg_speed_ms*3.6:.1f} km/h",
            78,
        )
    else:
        p_fast_str = _format_pace_mmss(pace_fast_s)
        p_slow_str = _format_pace_mmss(pace_slow_s)
        p_mid_str = _format_pace_mmss(pace_sec_km)
        cp_label = _clean_ascii(f"{p_mid_str} {short_tag}", 15)
        target_ui = f"{p_fast_str}–{p_slow_str} /km ({p_mid_str})"
        step_name = _clean_ascii(f"S{idx} {short_tag} {p_mid_str}/k", 22)
        notes = _clean_ascii(
            f"Km {d_start_m/1000:.1f}-{d_end_m/1000:.1f} | {short_tag} {avg_grade_pct:+.1f}% | "
            f"Ritmo {p_fast_str}-{p_slow_str}/km | {int(round(avg_power_w))}W",
            78,
        )

    return {
        "index": idx,
        "dist_start_m": d_start_m,
        "dist_end_m": d_end_m,
        "length_m": length_m,
        "start_lat": items[0]["lat"],
        "start_lon": items[0]["lon"],
        "start_ele_m": items[0]["ele_m"],
        "cum_time_start_s": cum_t_start,
        "cum_time_end_s": cum_t_end,
        "duration_s": tot_t,
        "avg_grade_pct": round(avg_grade_pct, 1),
        "avg_headwind_kmh": round(avg_hw_kmh, 1),
        "avg_power_w": round(avg_power_w, 1),
        "pct_threshold": round(pct_thr, 1),
        "power_low_w": p_low,
        "power_high_w": p_high,
        "avg_speed_ms": avg_speed_ms,
        "speed_low_ms": speed_low_ms,
        "speed_high_ms": speed_high_ms,
        "pace_sec_km": pace_sec_km,
        "pace_fast_str": _format_pace_mmss(pace_fast_s),
        "pace_slow_str": _format_pace_mmss(pace_slow_s),
        "bucket": bucket,
        "short_tag": short_tag,
        "ui_terrain": ui_terrain,
        "cp_type": cp_type,
        "cp_label": cp_label,
        "step_name": step_name,
        "notes": notes,
        "target_ui": target_ui,
        "intensity": intensity,
    }


def build_device_segments(
    sim_segments: List[Dict],
    sport: str = "cycling",
    target_metric: str = "power",
    ref_threshold_w: float = 250.0,
    mode: str = "terrain",
    fixed_split_km: float = 5.0,
    max_steps: int = 45,
    tolerance_pct: float = 5.0,
    pace_tol_sec: float = 5.0,
) -> List[Dict]:
    """
    Construye bloques tácticos de carrera optimizados para relojes y ciclocomputadores Garmin (<= 50 pasos):
      - `mode="terrain"`: Agrupa por cambios reales de terreno (subidas, llanos, bajadas) e intensidad objetivo
        (estilo Best Bike Split), evitando ruido y dividiendo tramos demasiado largos.
      - `mode="splits"`: Agrupa por distancia fija (`fixed_split_km`), ajustando el paso si supera `max_steps`.
    """
    norm = _normalize_sim_segments(sim_segments)
    if not norm:
        return []

    max_steps = max(2, min(64, int(max_steps)))
    total_m = norm[-1]["dist_end_m"]

    groups: List[List[Dict]] = []

    if mode == "splits":
        step_m = max(250.0, float(fixed_split_km) * 1000.0)
        if total_m / step_m > max_steps:
            step_m = math.ceil((total_m / max_steps) / 100.0) * 100.0
        cur: List[Dict] = []
        next_mark = step_m
        for sg in norm:
            cur.append(sg)
            if sg["dist_end_m"] >= next_mark - 15.0:
                groups.append(cur)
                cur = []
                next_mark += step_m
        if cur:
            if groups and (cur[-1]["dist_end_m"] - cur[0]["dist_start_m"]) < max(150.0, step_m * 0.2):
                groups[-1].extend(cur)
            else:
                groups.append(cur)
    else:
        # Modo Inteligente por Terreno e Intensidad (estilo Best Bike Split)
        max_block_m = (10000.0 if total_m >= 60000.0 else 5000.0) if sport == "cycling" else (5000.0 if total_m >= 25000.0 else 3000.0)
        min_block_m = (800.0 if total_m >= 40000.0 else 450.0) if sport == "cycling" else (400.0 if total_m >= 15000.0 else 250.0)
        intensity_diff_thr = 0.065 if target_metric == "power" else 0.045

        cur = [norm[0]]
        cur_bucket = _terrain_bucket(norm[0]["grade_pct"], sport)
        cur_val_sum = (norm[0]["power_w"] if target_metric == "power" else norm[0]["speed_ms"]) * norm[0]["dt_s"]
        cur_t_sum = norm[0]["dt_s"]

        for sg in norm[1:]:
            b = _terrain_bucket(sg["grade_pct"], sport)
            sg_val = sg["power_w"] if target_metric == "power" else sg["speed_ms"]
            cur_mean = cur_val_sum / max(0.1, cur_t_sum)
            rel_diff = abs(sg_val - cur_mean) / max(1.0 if target_metric == "power" else 0.5, cur_mean)
            cur_len = cur[-1]["dist_end_m"] - cur[0]["dist_start_m"]

            should_split = (
                cur_len >= max_block_m
                or (cur_len >= min_block_m and (b != cur_bucket or rel_diff > intensity_diff_thr))
            )
            if should_split:
                groups.append(cur)
                cur = [sg]
                cur_bucket = b
                cur_val_sum = sg_val * sg["dt_s"]
                cur_t_sum = sg["dt_s"]
            else:
                cur.append(sg)
                cur_val_sum += sg_val * sg["dt_s"]
                cur_t_sum += sg["dt_s"]
        if cur:
            groups.append(cur)

        # Paso 2: Absorber micro-bloques residuales demasiado cortos en su vecino más afín
        def _grp_metric(g: List[Dict]) -> float:
            t_s = max(0.1, sum(x["dt_s"] for x in g))
            if target_metric == "power":
                return sum(x["power_w"] * x["dt_s"] for x in g) / t_s
            return (g[-1]["dist_end_m"] - g[0]["dist_start_m"]) / t_s

        changed = True
        while changed and len(groups) > 1:
            changed = False
            for i in range(len(groups)):
                g_len = groups[i][-1]["dist_end_m"] - groups[i][0]["dist_start_m"]
                if g_len < min_block_m * 0.75:
                    m_i = _grp_metric(groups[i])
                    if i == 0:
                        groups[1] = groups[0] + groups[1]
                        groups.pop(0)
                    elif i == len(groups) - 1:
                        groups[-2] = groups[-2] + groups[-1]
                        groups.pop()
                    else:
                        d_left = abs(m_i - _grp_metric(groups[i - 1]))
                        d_right = abs(m_i - _grp_metric(groups[i + 1]))
                        if d_left <= d_right:
                            groups[i - 1] = groups[i - 1] + groups[i]
                            groups.pop(i)
                        else:
                            groups[i + 1] = groups[i] + groups[i + 1]
                            groups.pop(i)
                    changed = True
                    break

        # Paso 2b: Fusionar bloques adyacentes con mismo terreno e intensidad prácticamente idéntica
        # (evita alertas consecutivas repetidas en pantalla como "5:07 SUB" seguido de "5:07 SUB")
        merged = True
        while merged and len(groups) > 1:
            merged = False
            for i in range(len(groups) - 1):
                g1, g2 = groups[i], groups[i + 1]
                comb_len = g2[-1]["dist_end_m"] - g1[0]["dist_start_m"]
                if comb_len > max_block_m:
                    continue
                g1_grade = sum(x["grade_pct"] * x["length_m"] for x in g1) / max(1.0, g1[-1]["dist_end_m"] - g1[0]["dist_start_m"])
                g2_grade = sum(x["grade_pct"] * x["length_m"] for x in g2) / max(1.0, g2[-1]["dist_end_m"] - g2[0]["dist_start_m"])
                if _terrain_bucket(g1_grade, sport) == _terrain_bucket(g2_grade, sport):
                    m1, m2 = _grp_metric(g1), _grp_metric(g2)
                    if abs(m1 - m2) / max(1.0 if target_metric == "power" else 0.5, 0.5 * (m1 + m2)) < 0.028:
                        groups[i] = g1 + g2
                        groups.pop(i + 1)
                        merged = True
                        break

        # Paso 3: Si todavía supera `max_steps` (límite Garmin <= 50), fusionar el par adyacente más parecido
        while len(groups) > max_steps:
            best_i = 0
            best_cost = float("inf")
            metrics = [_grp_metric(g) for g in groups]
            lengths = [g[-1]["dist_end_m"] - g[0]["dist_start_m"] for g in groups]
            for i in range(len(groups) - 1):
                diff = abs(metrics[i] - metrics[i + 1]) / max(1.0, 0.5 * (metrics[i] + metrics[i + 1]))
                # Penalizar ligeramente fusionar dos bloques ya muy largos
                len_factor = 1.0 + ((lengths[i] + lengths[i + 1]) / max(1000.0, total_m)) * 0.4
                cost = diff * len_factor
                if cost < best_cost:
                    best_cost = cost
                    best_i = i
            groups[best_i] = groups[best_i] + groups[best_i + 1]
            groups.pop(best_i + 1)

    return [
        _summarize_block(
            items=grp,
            sport=sport,
            target_metric=target_metric,
            ref_threshold_w=ref_threshold_w,
            tolerance_pct=tolerance_pct,
            pace_tol_sec=pace_tol_sec,
            idx=i + 1,
        )
        for i, grp in enumerate(groups)
    ]


# ═════════════════════════════════════════════════════════════════════
#  1) GENERADOR FIT COURSE (TRAYECTO GPS + VIRTUAL PARTNER + ALERTAS)
# ═════════════════════════════════════════════════════════════════════

def _build_nutrition_course_points(
    norm_segs: List[Dict],
    sport: str,
    nutrition: Optional[Dict],
    start_ts: int,
) -> List[Dict]:
    """Construye Course Points de nutrición/geles posicionados en las coordenadas GPS exactas."""
    pts: List[Dict] = []
    if not norm_segs or not nutrition:
        return pts

    def _find_seg_at_km(target_km: float) -> Optional[Dict]:
        target_m = target_km * 1000.0
        for sg in norm_segs:
            if sg["dist_end_m"] >= target_m:
                return sg
        return norm_segs[-1]

    def _find_seg_at_time(target_s: float) -> Optional[Dict]:
        for sg in norm_segs:
            if sg["cum_time_s"] >= target_s:
                return sg
        return None

    if sport == "running" and nutrition.get("gel_schedule"):
        for g in nutrition["gel_schedule"][:12]:
            km = float(g.get("km", 0.0))
            sg = _find_seg_at_km(km)
            if not sg:
                continue
            cho_g = int(round(float(g.get("cho_g", 25))))
            pts.append({
                "timestamp": start_ts + int(round(sg["cum_time_s"])),
                "lat": sg["lat"],
                "lon": sg["lon"],
                "distance_m": sg["dist_end_m"],
                "cp_type": CP_FOOD,
                "name": _clean_ascii(f"GEL #{g.get('gel_num', 1)} {cho_g}g", 15),
            })
    elif sport == "cycling":
        total_s = norm_segs[-1]["cum_time_s"]
        cho_h = int(round(float(nutrition.get("cho_g_per_h", 70))))
        if total_s >= 3000.0:  # >= 50 min
            interval_s = 1800.0 if total_s <= 14400.0 else 2400.0  # cada 30 min (o 40 min en IM Full)
            cho_dose = int(round(cho_h * (interval_s / 3600.0)))
            t_mark = interval_s
            n_idx = 1
            while t_mark <= total_s - 600.0 and n_idx <= 12:
                sg = _find_seg_at_time(t_mark)
                if sg:
                    pts.append({
                        "timestamp": start_ts + int(round(sg["cum_time_s"])),
                        "lat": sg["lat"],
                        "lon": sg["lon"],
                        "distance_m": sg["dist_end_m"],
                        "cp_type": CP_FOOD,
                        "name": _clean_ascii(f"NUTRI {cho_dose}g #{n_idx}", 15),
                    })
                t_mark += interval_s
                n_idx += 1

    return pts


def generate_fit_course_bytes(
    course_name: str,
    sim_segments: List[Dict],
    device_blocks: List[Dict],
    sport: str = "cycling",
    nutrition: Optional[Dict] = None,
    include_nutrition_alerts: bool = True,
    start_dt: Optional[datetime] = None,
) -> bytes:
    """
    Genera un archivo binario `.FIT` tipo **Course** (`file_id.type = 6`) compatible con
    Garmin Edge, Forerunner, Fenix, Epix, Wahoo ELEMNT y Coros:
      - Trazado GPS + elevación + velocidad + potencia + timestamps simulados (**Virtual Partner**).
      - **Course Points** en cada bloque táctico de intensidad y puntos de nutrición/geles.
    """
    norm = _normalize_sim_segments(sim_segments)
    if len(norm) < 2:
        raise ValueError("Se requieren al menos 2 segmentos GPS para generar un archivo FIT Course.")

    sport_enum = SPORT_CYCLING if sport == "cycling" else SPORT_RUNNING
    caps = COURSE_CAP_CYCLING if sport == "cycling" else COURSE_CAP_RUNNING
    start_ts = _to_fit_timestamp(start_dt)

    # Remuestreo suave solo si hubiera excesivos puntos (>2500) para mantener el archivo ágil en relojes
    track_pts = norm
    if len(track_pts) > 2500:
        stride = max(1, int(math.ceil(len(track_pts) / 2500.0)))
        sampled = track_pts[::stride]
        if sampled[-1] is not track_pts[-1]:
            sampled.append(track_pts[-1])
        track_pts = sampled

    total_dist_m = track_pts[-1]["dist_end_m"]
    total_time_s = max(1.0, track_pts[-1]["cum_time_s"])
    avg_speed_ms = total_dist_m / total_time_s

    total_ascent = 0.0
    total_descent = 0.0
    for i in range(1, len(track_pts)):
        de = track_pts[i]["ele_m"] - track_pts[i - 1]["ele_m"]
        if de > 0:
            total_ascent += de
        else:
            total_descent += abs(de)

    end_ts = start_ts + int(round(total_time_s))
    clean_name = _clean_ascii(course_name or "AlphaX Race Plan", 31) or "AlphaX Race Plan"

    w = _FitBinaryWriter()

    # 1. file_id (local 0, global 0)
    w.define(0, MESG_FILE_ID, [
        (0, 1, BT_ENUM),      # type = 6 (course)
        (1, 2, BT_UINT16),    # manufacturer
        (2, 2, BT_UINT16),    # product
        (3, 4, BT_UINT32Z),   # serial_number
        (4, 4, BT_UINT32),    # time_created
    ])
    w.write_data(0, [
        FILE_TYPE_COURSE,
        MANUFACTURER_GARMIN,
        GARMIN_PRODUCT_CONNECT,
        _deterministic_serial(f"course:{clean_name}:{total_dist_m:.0f}"),
        start_ts,
    ])

    # 2. course (local 1, global 31)
    w.define(1, MESG_COURSE, [
        (4, 1, BT_ENUM),      # sport
        (5, 32, BT_STRING),   # name (fixed 32 bytes)
        (6, 4, BT_UINT32Z),   # capabilities
    ])
    w.write_data(1, [sport_enum, clean_name, caps])

    # 3. lap (local 2, global 19)
    w.define(2, MESG_LAP, [
        (253, 4, BT_UINT32),  # timestamp (end)
        (2, 4, BT_UINT32),    # start_time
        (3, 4, BT_SINT32),    # start_position_lat
        (4, 4, BT_SINT32),    # start_position_long
        (5, 4, BT_SINT32),    # end_position_lat
        (6, 4, BT_SINT32),    # end_position_long
        (7, 4, BT_UINT32),    # total_elapsed_time (ms)
        (8, 4, BT_UINT32),    # total_timer_time (ms)
        (9, 4, BT_UINT32),    # total_distance (cm)
        (13, 2, BT_UINT16),   # avg_speed (mm/s)
        (21, 2, BT_UINT16),   # total_ascent (m)
        (22, 2, BT_UINT16),   # total_descent (m)
    ])
    w.write_data(2, [
        end_ts,
        start_ts,
        _to_semicircles(track_pts[0]["lat"]),
        _to_semicircles(track_pts[0]["lon"]),
        _to_semicircles(track_pts[-1]["lat"]),
        _to_semicircles(track_pts[-1]["lon"]),
        int(round(total_time_s * 1000.0)),
        int(round(total_time_s * 1000.0)),
        int(round(total_dist_m * 100.0)),
        min(65534, int(round(avg_speed_ms * 1000.0))),
        min(65534, int(round(total_ascent))),
        min(65534, int(round(total_descent))),
    ])

    # 4. event (local 3, global 21) — Timer Start
    w.define(3, MESG_EVENT, [
        (253, 4, BT_UINT32),  # timestamp
        (0, 1, BT_ENUM),      # event = timer (0)
        (1, 1, BT_ENUM),      # event_type = start (0) / stop_all (4)
        (4, 1, BT_UINT8),     # event_group = 0
    ])
    w.write_data(3, [start_ts, EVENT_TIMER, EVENT_TYPE_START, 0])

    # 5. record (local 4, global 20) — Track points con Virtual Partner timestamps
    w.define(4, MESG_RECORD, [
        (253, 4, BT_UINT32),  # timestamp
        (0, 4, BT_SINT32),    # position_lat
        (1, 4, BT_SINT32),    # position_long
        (2, 2, BT_UINT16),    # altitude ((ele + 500) * 5)
        (5, 4, BT_UINT32),    # distance (m * 100)
        (6, 2, BT_UINT16),    # speed (m/s * 1000)
        (7, 2, BT_UINT16),    # power (W)
        (73, 4, BT_UINT32),   # enhanced_speed (m/s * 1000)
        (78, 4, BT_UINT32),   # enhanced_altitude ((ele + 500) * 5)
    ])

    # Emitir punto inicial en distancia 0 y tiempo start_ts
    first = track_pts[0]
    ele0_raw = max(0, int(round((first["ele_m"] + 500.0) * 5.0)))
    spd0_raw = max(0, int(round(first["speed_ms"] * 1000.0)))
    pwr0_raw = max(0, min(65534, int(round(first["power_w"]))))
    w.write_data(4, [
        start_ts,
        _to_semicircles(first["lat"]),
        _to_semicircles(first["lon"]),
        min(65534, ele0_raw),
        0,
        min(65534, spd0_raw),
        pwr0_raw,
        spd0_raw,
        ele0_raw,
    ])

    last_ts = start_ts
    for idx_pt, sg in enumerate(track_pts):
        ts = start_ts + int(round(sg["cum_time_s"]))
        if ts < last_ts:
            ts = last_ts
        if idx_pt == len(track_pts) - 1 and ts < end_ts:
            ts = end_ts
        last_ts = ts
        ele_raw = max(0, int(round((sg["ele_m"] + 500.0) * 5.0)))
        spd_raw = max(0, int(round(sg["speed_ms"] * 1000.0)))
        pwr_raw = max(0, min(65534, int(round(sg["power_w"]))))
        w.write_data(4, [
            ts,
            _to_semicircles(sg["lat"]),
            _to_semicircles(sg["lon"]),
            min(65534, ele_raw),
            int(round(sg["dist_end_m"] * 100.0)),
            min(65534, spd_raw),
            pwr_raw,
            spd_raw,
            ele_raw,
        ])

    # 6. event — Timer Stop (inmediatamente tras el último record para orden cronológico estricto)
    w.write_data(3, [max(end_ts, last_ts), EVENT_TIMER, EVENT_TYPE_STOP_ALL, 0])

    # 7. course_point (local 5, global 32) — Alertas GPS por segmento + Nutrición (pantalla Up Ahead)
    cp_list: List[Dict] = []
    for blk in device_blocks:
        cp_list.append({
            "timestamp": start_ts + int(round(blk["cum_time_start_s"])),
            "lat": blk["start_lat"],
            "lon": blk["start_lon"],
            "distance_m": blk["dist_start_m"],
            "cp_type": blk["cp_type"],
            "name": blk["cp_label"],
        })

    if include_nutrition_alerts:
        cp_list.extend(_build_nutrition_course_points(norm, sport, nutrition, start_ts))

    # Ordenar course_points por distancia ascendente (requisito estricto de Garmin Edge/Forerunner)
    cp_list.sort(key=lambda c: (c["distance_m"], c["timestamp"]))
    # Tope de seguridad (Garmin tolera muy bien hasta ~100-200 course points; limitamos a 80 para claridad)
    cp_list = cp_list[:80]

    if cp_list:
        w.define(5, MESG_COURSE_POINT, [
            (254, 2, BT_UINT16),  # message_index
            (1, 4, BT_UINT32),    # timestamp
            (2, 4, BT_SINT32),    # position_lat
            (3, 4, BT_SINT32),    # position_long
            (4, 4, BT_UINT32),    # distance (m * 100)
            (5, 1, BT_ENUM),      # type (course_point enum)
            (6, 16, BT_STRING),   # name (15 chars + NUL, tamaño óptimo pantalla Garmin)
        ])
        for idx_cp, cp in enumerate(cp_list):
            w.write_data(5, [
                idx_cp,
                cp["timestamp"],
                _to_semicircles(cp["lat"]),
                _to_semicircles(cp["lon"]),
                int(round(max(0.0, cp["distance_m"]) * 100.0)),
                cp["cp_type"],
                cp["name"],
            ])

    return w.build()


# ═════════════════════════════════════════════════════════════════════
#  2) GENERADOR FIT WORKOUT (PLAN ESTRUCTURADO POR DISTANCIA - BBS)
# ═════════════════════════════════════════════════════════════════════

def generate_fit_workout_bytes(
    workout_name: str,
    device_blocks: List[Dict],
    sport: str = "cycling",
    target_metric: str = "power",
    start_dt: Optional[datetime] = None,
) -> bytes:
    """
    Genera un archivo binario `.FIT` tipo **Workout** (`file_id.type = 5`) estructurado por
    **distancia** con alertas activas de rango de Potencia (W) o Ritmo (min/km -> m/s),
    idéntico al formato que exporta Best Bike Split para Garmin (`GARMIN/NewFiles`).
    """
    if not device_blocks:
        raise ValueError("No hay segmentos para generar el archivo FIT Workout.")

    steps = device_blocks[:50]  # Límite estricto de Garmin: máx. 50 pasos
    sport_enum = SPORT_CYCLING if sport == "cycling" else SPORT_RUNNING
    created_ts = _to_fit_timestamp(start_dt)
    clean_name = _clean_ascii(workout_name or "AlphaX Pacing Plan", 31) or "AlphaX Pacing Plan"

    w = _FitBinaryWriter()

    # 1. file_id (local 0, global 0)
    w.define(0, MESG_FILE_ID, [
        (0, 1, BT_ENUM),      # type = 5 (workout)
        (1, 2, BT_UINT16),    # manufacturer
        (2, 2, BT_UINT16),    # product
        (3, 4, BT_UINT32Z),   # serial_number
        (4, 4, BT_UINT32),    # time_created
    ])
    w.write_data(0, [
        FILE_TYPE_WORKOUT,
        MANUFACTURER_GARMIN,
        GARMIN_PRODUCT_CONNECT,
        _deterministic_serial(f"workout:{clean_name}:{len(steps)}"),
        created_ts,
    ])

    # 2. workout (local 1, global 26)
    w.define(1, MESG_WORKOUT, [
        (4, 1, BT_ENUM),      # sport
        (5, 4, BT_UINT32Z),   # capabilities
        (6, 2, BT_UINT16),    # num_valid_steps
        (8, 32, BT_STRING),   # wkt_name
    ])
    w.write_data(1, [
        sport_enum,
        WORKOUT_CAP_FITNESS,
        len(steps),
        clean_name,
    ])

    # 3. workout_step (local 2, global 27)
    w.define(2, MESG_WORKOUT_STEP, [
        (254, 2, BT_UINT16),  # message_index
        (0, 24, BT_STRING),   # wkt_step_name
        (1, 1, BT_ENUM),      # duration_type = 1 (distance)
        (2, 4, BT_UINT32),    # duration_value (cm = m * 100)
        (3, 1, BT_ENUM),      # target_type = 4 (power) | 0 (speed)
        (4, 4, BT_UINT32),    # target_value = 0 (custom range)
        (5, 4, BT_UINT32),    # custom_target_value_low
        (6, 4, BT_UINT32),    # custom_target_value_high
        (7, 1, BT_ENUM),      # intensity = active (0) / recovery (4)
        (8, 80, BT_STRING),   # notes
    ])

    for i, blk in enumerate(steps):
        dist_cm = max(1000, int(round(blk["length_m"] * 100.0)))  # mínimo 10 m (1000 cm)
        if target_metric == "power":
            t_type = WKT_TARGET_POWER
            # Garmin FIT SDK: Vatios absolutos llevan offset +1000 (0..1000 es %FTP)
            t_low = int(round(max(0, blk["power_low_w"]))) + 1000
            t_high = int(round(max(blk["power_low_w"] + 5, blk["power_high_w"]))) + 1000
        else:
            t_type = WKT_TARGET_SPEED
            # Garmin FIT SDK: velocidad en m/s * 1000 (low = ritmo más lento, high = ritmo más rápido)
            t_low = max(500, int(round(blk["speed_low_ms"] * 1000.0)))
            t_high = max(t_low + 50, int(round(blk["speed_high_ms"] * 1000.0)))

        w.write_data(2, [
            i,
            blk["step_name"],
            WKT_DUR_DISTANCE,
            dist_cm,
            t_type,
            0,  # custom target
            t_low,
            t_high,
            blk["intensity"],
            blk["notes"],
        ])

    return w.build()


# ═════════════════════════════════════════════════════════════════════
#  API DE ALTO NIVEL PARA UI (PAQUETE LISTO PARA DESCARGA + TABLA)
# ═════════════════════════════════════════════════════════════════════

def build_fit_export_bundle(
    plan_name: str,
    sim_segments: List[Dict],
    sport: str = "cycling",
    target_metric: str = "power",
    ref_threshold_w: float = 250.0,
    nutrition: Optional[Dict] = None,
    segmentation_mode: str = "terrain",
    fixed_split_km: float = 5.0,
    max_steps: int = 55,
    tolerance_pct: float = 5.0,
    pace_tol_sec: float = 5.0,
    include_nutrition_alerts: bool = True,
    start_dt: Optional[datetime] = None,
) -> Dict:
    """
    Construye el paquete Garmin `.FIT` Course (Trayecto GPS + Virtual Partner + Alertas por Segmento)
    anclado 100% a coordenadas GPS del recorrido:
      - `course_fit_bytes`: Archivo binario `.FIT` Course (`file_id.type = 6`)
      - `blocks`: Segmentos tácticos del recorrido
      - `preview_rows`: Filas de previsualización de todas las alertas GPS (segmentos + nutrición)
      - `course_filename`, `num_steps`, `num_nutrition_points`, `course_size_kb`
    """
    blocks = build_device_segments(
        sim_segments=sim_segments,
        sport=sport,
        target_metric=target_metric,
        ref_threshold_w=ref_threshold_w,
        mode=segmentation_mode,
        fixed_split_km=fixed_split_km,
        max_steps=max_steps,
        tolerance_pct=tolerance_pct,
        pace_tol_sec=pace_tol_sec,
    )

    course_bytes = generate_fit_course_bytes(
        course_name=plan_name,
        sim_segments=sim_segments,
        device_blocks=blocks,
        sport=sport,
        nutrition=nutrition,
        include_nutrition_alerts=include_nutrition_alerts,
        start_dt=start_dt,
    )

    slug = _clean_ascii(plan_name, 36).replace(" ", "_")
    slug = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in slug).strip("_") or "AlphaX_Plan"
    sport_tag = "Bici" if sport == "cycling" else "Run"

    # Construir previsualización combinada y ordenada por km de todas las alertas GPS (segmentos + nutrición)
    norm_segs = _normalize_sim_segments(sim_segments)
    nut_pts = (
        _build_nutrition_course_points(norm_segs, sport, nutrition, _to_fit_timestamp(start_dt))
        if include_nutrition_alerts
        else []
    )
    start_ts_ref = _to_fit_timestamp(start_dt)

    combined_events: List[Tuple[float, Dict]] = []
    for blk in blocks:
        combined_events.append((
            blk["dist_start_m"],
            {
                "Km GPS": f"Km {blk['dist_start_m']/1000:.1f}",
                "Tramo": f"{blk['dist_start_m']/1000:.1f}–{blk['dist_end_m']/1000:.1f} km",
                "Tipo": f"{blk['ui_terrain']} ({blk['avg_grade_pct']:+.1f}%)",
                "Banner en Reloj (GPS)": blk["cp_label"],
                "Intensidad Objetivo": blk["target_ui"],
                "Tiempo Acum.": _format_hms_short(blk["cum_time_start_s"]),
            },
        ))
    for np_item in nut_pts:
        rel_s = max(0, int(np_item["timestamp"]) - start_ts_ref)
        combined_events.append((
            float(np_item["distance_m"]),
            {
                "Km GPS": f"Km {np_item['distance_m']/1000:.1f}",
                "Tramo": "Punto puntual",
                "Tipo": "🥤 Nutrición / Gel",
                "Banner en Reloj (GPS)": np_item["name"],
                "Intensidad Objetivo": "Tomar CHO + agua",
                "Tiempo Acum.": _format_hms_short(rel_s),
            },
        ))

    combined_events.sort(key=lambda item: item[0])
    preview_rows = []
    for idx_ev, (_, row) in enumerate(combined_events[:80], start=1):
        preview_rows.append({"#": idx_ev, **row})

    return {
        "course_fit_bytes": course_bytes,
        "course_filename": f"AlphaX_Trayecto_{sport_tag}_{slug}.fit",
        "blocks": blocks,
        "preview_rows": preview_rows,
        "num_steps": len(blocks),
        "num_nutrition_points": len(nut_pts),
        "num_total_alerts": len(preview_rows),
        "course_size_kb": round(len(course_bytes) / 1024.0, 1),
    }
