"""
race_strategy_engine.py — Motor físico de simulación y optimización de estrategias de carrera
en ciclismo (estilo Best Bike Split) para AlphaX CRM.

Modelo físico basado en Martin et al. (1998) — Validación de potencia en ciclismo en ruta:
  P_rueda = eta * P_ciclista
          = 0.5 * rho(h, T, RH) * CdA(yaw) * v_aire * |v_aire| * v
          + m * g * Crr * cos(alpha) * v
          + m * g * sin(alpha) * v

Incluye:
  - Parser GPX 1.0/1.1 con suavizado de elevación, cálculo Haversine, rumbo por tramo,
    detección de curvas cerradas y soporte multi-vuelta (circuitos).
  - Cálculo real de densidad del aire (rho) según altitud local del GPX, temperatura y humedad.
  - Descomposición vectorial de viento (en contra, a favor y cruzado con efecto vela en ruedas aero).
  - Catálogo técnico de bicicletas, ruedas, cascos, llantas (Crr), transmisión, vestimenta y asfalto.
  - Optimizador de pacing (Óptimo AlphaX variable por pendiente/viento, Isopotencia y Negative Split)
    calibrado al Factor de Intensidad (IF) objetivo mediante Potencia Normalizada (NP 30s).
  - Generador de tabla de splits y plan de nutrición/hidratación ajustado a temperatura y gasto (kJ).
"""

import math
import xml.etree.ElementTree as ET


GRAVITY = 9.80665
R_DRY_AIR = 287.058
R_WATER_VAPOR = 461.495


# ═════════════════════════════════════════════════════════════════════
#  CATÁLOGO TÉCNICO DE BICICLETAS Y COMPONENTES (CdA, PESO, Crr, ETA)
# ═════════════════════════════════════════════════════════════════════

BIKE_TYPES = {
    "Contrarreloj / Triatlón (TT / Cabra)": {
        "cda": 0.235,
        "weight_kg": 8.6,
        "desc": "Posición acoplada sobre barras aerodinámicas. Mínima resistencia al viento.",
    },
    "Ruta Aero con acoples": {
        "cda": 0.272,
        "weight_kg": 7.7,
        "desc": "Bicicleta de ruta con barras clip-on cortas o largas.",
    },
    "Ruta Aero (manubrio normal / drops)": {
        "cda": 0.305,
        "weight_kg": 7.5,
        "desc": "Cuadro aerodinámico de ruta en postura baja/agresiva.",
    },
    "Ruta Escaladora / Ligera": {
        "cda": 0.325,
        "weight_kg": 6.9,
        "desc": "Máxima ligereza para puertos largos y alta relación W/kg.",
    },
    "Gravel": {
        "cda": 0.375,
        "weight_kg": 9.2,
        "desc": "Postura más erguida y neumáticos anchos.",
    },
}

WHEEL_SETS = {
    "Lenticular trasera + Perfil Alto 80 mm": {
        "delta_cda": -0.018,
        "delta_kg": 0.35,
        "sail_factor": 0.08,
    },
    "Perfil Alto Carbono (60–80 mm)": {
        "delta_cda": -0.011,
        "delta_kg": 0.15,
        "sail_factor": 0.05,
    },
    "Perfil Medio Carbono (45–50 mm)": {
        "delta_cda": -0.005,
        "delta_kg": 0.0,
        "sail_factor": 0.03,
    },
    "Ultraligeras Escaladoras (28–35 mm)": {
        "delta_cda": 0.002,
        "delta_kg": -0.25,
        "sail_factor": 0.01,
    },
    "Aluminio Entrenamiento (perfil bajo)": {
        "delta_cda": 0.006,
        "delta_kg": 0.30,
        "sail_factor": 0.0,
    },
}

HELMETS = {
    "Casco Aero TT con visor": {"delta_cda": -0.016, "delta_kg": 0.12},
    "Casco Aero Ruta (semi-cerrado)": {"delta_cda": -0.007, "delta_kg": 0.03},
    "Casco Ruta Ventilado estándar": {"delta_cda": 0.0, "delta_kg": 0.0},
}

TIRES_CRR = {
    "Competición TT Tubeless / Latex (GP5000 TT / Corsa Pro Speed)": {"crr": 0.0029, "delta_kg": -0.08},
    "Ruta Competición All-Round (GP5000 S TR / Corsa Pro 28mm)": {"crr": 0.0034, "delta_kg": 0.0},
    "Ruta Entrenamiento / Antipinchazos (Gatorskin / 4-Season)": {"crr": 0.0046, "delta_kg": 0.16},
    "Gravel Fast-Rolling (38–42 mm)": {"crr": 0.0058, "delta_kg": 0.40},
}

DRIVETRAINS = {
    "Competición: Cadena encerada + roldanas cerámicas (98.2%)": {"efficiency": 0.982},
    "Óptima: Cadena limpia y lubricada de calidad (97.4%)": {"efficiency": 0.974},
    "Estándar / Entrenamiento (96.5%)": {"efficiency": 0.965},
}

APPAREL_OPTIONS = {
    "Skinsuit / Trisuit Aero competición": {"delta_cda": -0.012},
    "Uniforme Aero ajustado (Race fit)": {"delta_cda": -0.005},
    "Uniforme Ciclismo estándar (Club fit)": {"delta_cda": 0.0},
}

HYDRATION_SETUP = {
    "Integrada aero (BTA + detrás del sillín)": {"delta_cda": 0.0, "delta_kg": 1.1},
    "2 caramañolas en cuadro (estándar)": {"delta_cda": 0.005, "delta_kg": 1.3},
    "Caramañolas + bolsas de cuadro/herramientas grandes": {"delta_cda": 0.012, "delta_kg": 1.8},
}

ROAD_SURFACES = {
    "Asfalto nuevo / competición liso (×1.00)": 1.00,
    "Asfalto estándar de carretera (×1.12)": 1.12,
    "Asfalto rugoso / deteriorado (×1.30)": 1.30,
    "Destapado / Gravel compacto (×1.55)": 1.55,
}

RACE_PRESETS = {
    "Personalizado": None,
    "⏱️ Contrarreloj 20–40 km (IF ~0.98–1.02)": {"target_if": 0.99, "max_climb_pct": 1.15, "strategy": "optimal"},
    "⚡ Triatlón Sprint / Olímpico (IF ~0.88–0.92)": {"target_if": 0.89, "max_climb_pct": 1.12, "strategy": "optimal"},
    "🔥 Ironman 70.3 / Media Distancia (IF ~0.80–0.84)": {"target_if": 0.82, "max_climb_pct": 1.05, "strategy": "optimal"},
    "🏆 Ironman Full 180 km (IF ~0.70–0.75)": {"target_if": 0.73, "max_climb_pct": 0.95, "strategy": "optimal"},
    "⛰️ Gran Fondo / Reto de Montaña (IF ~0.78–0.82)": {"target_if": 0.80, "max_climb_pct": 1.08, "strategy": "optimal"},
}

WIND_DIRECTIONS = {
    "N (Norte - 0°)": 0.0,
    "NE (Noreste - 45°)": 45.0,
    "E (Este - 90°)": 90.0,
    "SE (Sureste - 135°)": 135.0,
    "S (Sur - 180°)": 180.0,
    "SO (Suroeste - 225°)": 225.0,
    "O (Oeste - 270°)": 270.0,
    "NO (Noroeste - 315°)": 315.0,
}


def estimate_equipment_specs(
    bike_type: str,
    wheels: str,
    helmet: str,
    tires: str,
    drivetrain: str,
    apparel: str,
    hydration: str,
    surface: str,
    rider_height_cm: float = 175.0,
    manual_cda: float = None,
    manual_bike_weight_kg: float = None,
) -> dict:
    """
    Calcula el CdA efectivo, peso total de bici+equipo, Crr y eficiencia mecánica
    a partir de los componentes elegidos (con opción de sobrescribir CdA o peso exactos).
    """
    b = BIKE_TYPES[bike_type]
    w = WHEEL_SETS[wheels]
    h = HELMETS[helmet]
    t = TIRES_CRR[tires]
    d = DRIVETRAINS[drivetrain]
    a = APPAREL_OPTIONS[apparel]
    hy = HYDRATION_SETUP[hydration]
    surf_mult = ROAD_SURFACES[surface]

    # Escalado suave de área frontal por estatura del ciclista (referencia 175 cm)
    h_cm = max(145.0, min(205.0, float(rider_height_cm or 175.0)))
    height_scale = (h_cm / 175.0) ** 0.65

    raw_cda = (b["cda"] + w["delta_cda"] + h["delta_cda"] + a["delta_cda"] + hy["delta_cda"]) * height_scale
    est_cda = round(max(0.175, min(0.55, raw_cda)), 4)

    est_bike_kg = round(
        max(5.5, b["weight_kg"] + w["delta_kg"] + h["delta_kg"] + t["delta_kg"] + hy["delta_kg"]),
        2,
    )

    final_cda = round(float(manual_cda), 4) if (manual_cda is not None and manual_cda > 0.1) else est_cda
    final_bike_kg = round(float(manual_bike_weight_kg), 2) if (manual_bike_weight_kg is not None and manual_bike_weight_kg > 3.0) else est_bike_kg
    final_crr = round(t["crr"] * surf_mult, 5)

    return {
        "cda": final_cda,
        "estimated_cda": est_cda,
        "bike_and_gear_kg": final_bike_kg,
        "estimated_bike_kg": est_bike_kg,
        "crr": final_crr,
        "efficiency": d["efficiency"],
        "sail_factor": w["sail_factor"],
    }


# ═════════════════════════════════════════════════════════════════════
#  FÍSICA ATMOSFÉRICA Y GEOESPACIAL
# ═════════════════════════════════════════════════════════════════════

def calc_air_density(
    altitude_m: float,
    temp_c: float = 22.0,
    rel_humidity_pct: float = 60.0,
    surface_pressure_hpa: float = None,
) -> float:
    """
    Calcula la densidad del aire rho (kg/m³) usando la presión barométrica real (si está
    disponible) o la atmósfera estándar por altitud, con corrección de humedad (Tetens).
    """
    h = max(-100.0, min(5500.0, float(altitude_m or 0.0)))
    t_k = float(temp_c) + 273.15
    rh = max(0.0, min(100.0, float(rel_humidity_pct))) / 100.0

    # Presión barométrica a altitud h (Pa)
    if surface_pressure_hpa is not None and 500.0 <= float(surface_pressure_hpa) <= 1080.0:
        p_total = float(surface_pressure_hpa) * 100.0
    else:
        p_total = 101325.0 * ((1.0 - (0.0065 * h) / 288.15) ** 5.25588)

    # Presión de vapor de saturación (Pa) - Tetens
    p_sat = 610.78 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    p_vapor = rh * p_sat
    p_dry = max(1000.0, p_total - p_vapor)

    rho = (p_dry / (R_DRY_AIR * t_k)) + (p_vapor / (R_WATER_VAPOR * t_k))
    return round(rho, 4)


def _interp_angle_deg(a_deg: float, b_deg: float, frac: float) -> float:
    """Interpola suavemente por el camino angular más corto entre dos direcciones en grados."""
    diff = ((b_deg - a_deg + 180.0) % 360.0) - 180.0
    return (a_deg + diff * frac) % 360.0


def _sample_hourly_weather(
    hourly_weather: list,
    elapsed_hours: float,
    fallback_temp: float,
    fallback_hum: float,
    fallback_wind_kmh: float,
    fallback_wind_deg: float,
) -> tuple:
    """
    Interpola (temp_c, humidity_pct, wind_kmh, wind_deg, pressure_hpa) en función del tiempo
    transcurrido de carrera (`elapsed_hours`) usando la serie horaria real/climatológica.
    """
    if not hourly_weather:
        return fallback_temp, fallback_hum, fallback_wind_kmh, fallback_wind_deg, None
    if len(hourly_weather) == 1 or elapsed_hours <= 0.0:
        p0 = hourly_weather[0]
        return (
            float(p0.get("temp_c", fallback_temp)),
            float(p0.get("humidity_pct", fallback_hum)),
            float(p0.get("wind_kmh", fallback_wind_kmh)),
            float(p0.get("wind_deg", fallback_wind_deg)),
            p0.get("pressure_hpa"),
        )
    max_idx = len(hourly_weather) - 1
    if elapsed_hours >= max_idx:
        pl = hourly_weather[max_idx]
        return (
            float(pl.get("temp_c", fallback_temp)),
            float(pl.get("humidity_pct", fallback_hum)),
            float(pl.get("wind_kmh", fallback_wind_kmh)),
            float(pl.get("wind_deg", fallback_wind_deg)),
            pl.get("pressure_hpa"),
        )
    i0 = int(math.floor(elapsed_hours))
    i1 = min(max_idx, i0 + 1)
    frac = elapsed_hours - i0
    p0, p1 = hourly_weather[i0], hourly_weather[i1]
    t_c = float(p0["temp_c"]) * (1.0 - frac) + float(p1["temp_c"]) * frac
    hum = float(p0["humidity_pct"]) * (1.0 - frac) + float(p1["humidity_pct"]) * frac
    w_k = float(p0["wind_kmh"]) * (1.0 - frac) + float(p1["wind_kmh"]) * frac
    w_d = _interp_angle_deg(float(p0["wind_deg"]), float(p1["wind_deg"]), frac)
    pr0, pr1 = p0.get("pressure_hpa"), p1.get("pressure_hpa")
    pr = (float(pr0) * (1.0 - frac) + float(pr1) * frac) if (pr0 is not None and pr1 is not None) else (pr0 or pr1)
    return t_c, hum, w_k, w_d, pr


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs((a - b + 180.0) % 360.0 - 180.0)
    return d


# ═════════════════════════════════════════════════════════════════════
#  PARSER GPX + SUAVIZADO + CIRCUITOS DEMO
# ═════════════════════════════════════════════════════════════════════

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_gpx_bytes(gpx_bytes: bytes, laps: int = 1, target_step_m: float = 100.0) -> dict:
    """
    Lee un archivo GPX (1.0 o 1.1, trkpt o rtept), limpia ruido de altimetría,
    remuestrea en segmentos uniformes (~100m) y soporta repetición de vueltas (laps).
    """
    try:
        root = ET.fromstring(gpx_bytes)
    except Exception as e:
        raise ValueError(f"El archivo GPX no es un XML válido: {e}")

    route_name = "Circuito GPX"
    raw_pts = []

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "name" and elem.text and elem.text.strip() and route_name == "Circuito GPX":
            route_name = elem.text.strip()
        elif tag in ("trkpt", "rtept"):
            lat = elem.attrib.get("lat")
            lon = elem.attrib.get("lon")
            if lat is None or lon is None:
                continue
            ele = None
            for child in elem:
                if _strip_ns(child.tag) == "ele" and child.text:
                    try:
                        ele = float(child.text.strip())
                    except ValueError:
                        ele = None
            raw_pts.append((float(lat), float(lon), ele))

    if len(raw_pts) < 2:
        raise ValueError("El archivo GPX no contiene suficientes puntos GPS (mínimo 2 puntos).")

    # Rellenar elevaciones faltantes por interpolación o 0.0
    first_valid_ele = next((p[2] for p in raw_pts if p[2] is not None), 0.0)
    filled_pts = []
    last_ele = first_valid_ele
    for lat, lon, ele in raw_pts:
        e = ele if ele is not None else last_ele
        last_ele = e
        filled_pts.append((lat, lon, e))

    # Construir distancias acumuladas de 1 vuelta
    lap_pts = []
    cum_d = 0.0
    for idx, (lat, lon, ele) in enumerate(filled_pts):
        if idx > 0:
            step_d = _haversine_m(filled_pts[idx - 1][0], filled_pts[idx - 1][1], lat, lon)
            if step_d < 0.5:
                continue
            cum_d += step_d
        lap_pts.append((lat, lon, ele, cum_d))

    if len(lap_pts) < 2 or cum_d < 100.0:
        raise ValueError("La ruta GPX es demasiado corta (< 100 metros).")

    # Remuestreo a segmentos de ~target_step_m para eliminar ruido GPS y mantener simulación ultra-rápida
    # Ajustar step automáticamente para rutas muy largas (>120 km -> 150m)
    lap_count = max(1, int(laps))
    total_raw_m = cum_d * lap_count
    eff_step = target_step_m if total_raw_m <= 120000 else 150.0

    sampled_lap = [lap_pts[0]]
    last_d = 0.0
    for pt in lap_pts[1:-1]:
        if pt[3] - last_d >= eff_step:
            sampled_lap.append(pt)
            last_d = pt[3]
    if lap_pts[-1][3] - sampled_lap[-1][3] >= 5.0:
        sampled_lap.append(lap_pts[-1])

    # Suavizado de elevación (ventana móvil de 3 puntos para eliminar picos falsos de GPS)
    n_s = len(sampled_lap)
    smooth_eles = []
    for i in range(n_s):
        lo = max(0, i - 1)
        hi = min(n_s, i + 2)
        avg_e = sum(sampled_lap[j][2] for j in range(lo, hi)) / float(hi - lo)
        smooth_eles.append(avg_e)

    # Construir segmentos de 1 vuelta
    lap_segments = []
    for i in range(n_s - 1):
        lat1, lon1, _, d1 = sampled_lap[i]
        lat2, lon2, _, d2 = sampled_lap[i + 1]
        seg_len = max(5.0, d2 - d1)
        e1 = smooth_eles[i]
        e2 = smooth_eles[i + 1]
        de = e2 - e1
        grade = max(-0.22, min(0.25, de / seg_len))
        bearing = _bearing_deg(lat1, lon1, lat2, lon2)
        lap_segments.append({
            "lat": (lat1 + lat2) / 2.0,
            "lon": (lon1 + lon2) / 2.0,
            "ele_m": (e1 + e2) / 2.0,
            "ele_start": e1,
            "ele_end": e2,
            "length_m": seg_len,
            "grade": grade,
            "bearing_deg": bearing,
            "turn_deg": 0.0,
        })

    # Calcular ángulo de curva entre segmentos consecutivos
    for i in range(1, len(lap_segments)):
        lap_segments[i]["turn_deg"] = _angle_diff_deg(
            lap_segments[i - 1]["bearing_deg"],
            lap_segments[i]["bearing_deg"],
        )

    # Expandir vueltas (laps)
    segments = []
    cum_dist = 0.0
    elev_gain = 0.0
    elev_loss = 0.0
    for lap_idx in range(lap_count):
        for seg in lap_segments:
            s_copy = dict(seg)
            s_copy["dist_start_m"] = cum_dist
            cum_dist += s_copy["length_m"]
            s_copy["dist_end_m"] = cum_dist
            s_copy["dist_mid_km"] = (s_copy["dist_start_m"] + s_copy["length_m"] / 2.0) / 1000.0
            s_copy["lap"] = lap_idx + 1
            de = s_copy["ele_end"] - s_copy["ele_start"]
            if de > 0:
                elev_gain += de
            else:
                elev_loss += abs(de)
            segments.append(s_copy)

    eles = [s["ele_m"] for s in segments]
    grades = [s["grade"] * 100.0 for s in segments]
    return {
        "route_name": route_name if lap_count == 1 else f"{route_name} ({lap_count} vueltas)",
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


def generate_demo_gpx(preset_key: str) -> tuple:
    """Genera archivos GPX realistas de prueba para demostrar el simulador al instante."""
    pts = []
    if preset_key == "cartagena_703":
        name = "Demo: Ironman 70.3 Cartagena (90 km Ida y Vuelta Costero)"
        # Ida 45 km hacia el NE por la Vía al Mar (casi llano con leves repechos), vuelta 45 km al SO
        lat0, lon0 = 10.4236, -75.5378
        n_half = 180  # ~250m cada punto
        for i in range(n_half + 1):
            frac = i / n_half
            lat = lat0 + frac * 0.31
            lon = lon0 + frac * 0.25 + 0.008 * math.sin(frac * 6.0)
            ele = 5.0 + 18.0 * (math.sin(frac * math.pi * 5.0) ** 2) + 8.0 * frac
            pts.append((lat, lon, ele))
        for i in range(n_half - 1, -1, -1):
            lat, lon, ele = pts[i]
            pts.append((lat - 0.0005, lon + 0.0005, ele))
    else:
        name = "Demo: Circuito Ondulado Rionegro - Las Palmas (45 km)"
        # Circuito a ~2100m con puerto de montaña, falso llano y descenso técnico
        lat0, lon0 = 6.1535, -75.3740
        n_pts = 225
        for i in range(n_pts + 1):
            ang = 2.0 * math.pi * (i / n_pts)
            lat = lat0 + 0.065 * math.sin(ang) + 0.015 * math.sin(2 * ang)
            lon = lon0 + 0.072 * (1.0 - math.cos(ang))
            frac = i / n_pts
            # Subida principal km 8-20 (+450m), repechos y bajada
            climb1 = 420.0 * math.exp(-((frac - 0.42) / 0.16) ** 2)
            climb2 = 160.0 * math.exp(-((frac - 0.78) / 0.08) ** 2)
            ele = 2080.0 + climb1 + climb2 + 25.0 * math.sin(frac * 14.0)
            pts.append((lat, lon, ele))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="AlphaX Race Split" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{name}</name><trkseg>",
    ]
    for lat, lon, ele in pts:
        lines.append(f'    <trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{ele:.1f}</ele></trkpt>')
    lines.append("  </trkseg></trk></gpx>")
    return "\n".join(lines).encode("utf-8"), name


# ═════════════════════════════════════════════════════════════════════
#  SOLVER DE VELOCIDAD POR POTENCIA + OPTIMIZADOR DE PACING
# ═════════════════════════════════════════════════════════════════════

def solve_segment_speed(
    power_rider_w: float,
    total_mass_kg: float,
    cda_base: float,
    crr: float,
    efficiency: float,
    rho: float,
    grade: float,
    headwind_ms: float,
    crosswind_ms: float,
    sail_factor: float = 0.03,
    max_speed_ms: float = 24.0,
) -> dict:
    """
    Resuelve la velocidad estacionaria v (m/s) para una potencia dada sobre un tramo.
    Retorna velocidad (m/s) y desglose de potencia (aero, rolling, gravity).
    """
    p_wheel = max(0.0, power_rider_w) * efficiency
    cos_a = 1.0 / math.sqrt(1.0 + grade * grade)
    sin_a = grade * cos_a
    f_roll = total_mass_kg * GRAVITY * crr * cos_a
    f_grav = total_mass_kg * GRAVITY * sin_a

    def _effective_cda(v_ground: float) -> float:
        v_air_long = v_ground + headwind_ms
        yaw_rad = math.atan2(abs(crosswind_ms), max(0.5, abs(v_air_long)))
        yaw_deg = math.degrees(yaw_rad)
        # Efecto vela moderado en ángulos de 5° a 18° en ruedas de perfil/lenticular
        if 3.0 <= yaw_deg <= 20.0 and sail_factor > 0:
            sail_bonus = sail_factor * math.sin(math.pi * (yaw_deg - 3.0) / 17.0)
            return max(0.15, cda_base * (1.0 - sail_bonus))
        elif yaw_deg > 25.0:
            # En viento muy cruzado (>25°) la aerodinámica empeora ligeramente
            return cda_base * (1.0 + 0.04 * min(1.0, (yaw_deg - 25.0) / 35.0))
        return cda_base

    def _power_required(v: float) -> float:
        v_air = v + headwind_ms
        cda_eff = _effective_cda(v)
        f_aero = 0.5 * rho * cda_eff * v_air * abs(v_air)
        return (f_aero + f_roll + f_grav) * v

    # Bisección robusta en [0.5 m/s, max_speed_ms] (converge siempre en 28 pasos a < 0.0001 m/s)
    lo, hi = 0.5, max_speed_ms
    if _power_required(lo) > p_wheel:
        v_sol = lo
    elif _power_required(hi) <= p_wheel:
        v_sol = hi
    else:
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            if _power_required(mid) < p_wheel:
                lo = mid
            else:
                hi = mid
        v_sol = 0.5 * (lo + hi)

    v_air = v_sol + headwind_ms
    cda_eff = _effective_cda(v_sol)
    p_aero = max(0.0, 0.5 * rho * cda_eff * v_air * abs(v_air) * v_sol)
    p_roll = f_roll * v_sol
    p_grav = f_grav * v_sol
    return {
        "speed_ms": v_sol,
        "cda_eff": cda_eff,
        "p_aero": p_aero,
        "p_roll": p_roll,
        "p_grav": p_grav,
    }


def _compute_normalized_power(seg_powers: list, seg_times: list) -> float:
    """
    Calcula la Potencia Normalizada (NP, algoritmo Coggan de ventana móvil de 30s)
    remuestreando en pasos de 1 segundo o aproximación ponderada exacta para velocidad.
    """
    if not seg_powers or not seg_times:
        return 0.0
    total_t = sum(seg_times)
    if total_t <= 0:
        return 0.0

    # Discretizar en bloques de 5 segundos (ultra rápido y prácticamente idéntico a 1s para carreras > 5 min)
    dt_bin = 5.0
    samples = []
    acc_t = 0.0
    for p, t in zip(seg_powers, seg_times):
        n_bins = max(1, int(round(t / dt_bin)))
        samples.extend([p] * n_bins)
        acc_t += t

    win = max(1, int(round(30.0 / dt_bin)))  # 6 bins = 30s
    if len(samples) <= win:
        mean_p = sum(samples) / len(samples)
        return mean_p

    # Suma acumulada para rolling average O(N)
    prefix = [0.0] * (len(samples) + 1)
    for i, val in enumerate(samples):
        prefix[i + 1] = prefix[i] + val

    fourth_sum = 0.0
    count = 0
    for i in range(win, len(samples) + 1):
        avg30 = (prefix[i] - prefix[i - win]) / win
        fourth_sum += avg30 ** 4
        count += 1

    return (fourth_sum / max(1, count)) ** 0.25


def _generate_raw_power_profile(
    segments: list,
    base_power_w: float,
    ftp_w: float,
    strategy: str,
    wind_speed_ms: float,
    wind_from_deg: float,
    max_climb_pct_ftp: float = 1.08,
    descent_coast_grade: float = -0.045,
) -> list:
    """
    Genera el perfil relativo de potencia objetivo segmento a segmento según la estrategia:
      - 'optimal': Estilo Best Bike Split — más vatios en subidas y viento en contra,
        ahorro inteligente en bajadas rápidas donde la ganancia aerodinámica es marginal.
      - 'constant': Isopotencia estable (solo suelta pedales en bajadas muy pronunciadas).
      - 'negative_split': Primera mitad -3.5%, segunda mitad +3.5% (para guardar piernas).
    """
    n = len(segments)
    max_cap = ftp_w * max_climb_pct_ftp
    min_pedaling = ftp_w * 0.40
    powers = []

    for idx, seg in enumerate(segments):
        g = seg["grade"]
        rel_angle = math.radians(wind_from_deg - seg["bearing_deg"])
        headwind_ms = wind_speed_ms * math.cos(rel_angle)

        if strategy == "constant":
            if g <= descent_coast_grade:
                p = min_pedaling * 0.5
            else:
                p = base_power_w
        elif strategy == "negative_split":
            progress = idx / max(1, n - 1)
            split_mult = 0.965 + 0.07 * progress  # 0.965 -> 1.035
            if g <= descent_coast_grade:
                p = min_pedaling * 0.5
            elif g > 0.03:
                p = min(max_cap, base_power_w * split_mult * (1.0 + min(0.08, g * 1.1)))
            else:
                p = base_power_w * split_mult
        else:
            # 'optimal' (Best Bike Split style)
            if g <= descent_coast_grade:
                # Bajada pronunciada: postura aero y ahorro energético (coasting/suave)
                p = min_pedaling * 0.45
            elif g < -0.02:
                # Bajada suave: reducir potencia (~78-88%)
                p = base_power_w * max(0.68, 1.0 + g * 5.0)
            elif g <= 0.015:
                # Llano: ligero ajuste por viento en contra (+) o a favor (-)
                wind_adj = max(-0.04, min(0.06, headwind_ms * 0.008))
                p = base_power_w * (1.0 + wind_adj)
            else:
                # Subida: empujar más donde el beneficio de tiempo por vatio es máximo
                climb_boost = min(0.22, g * 2.1)
                wind_adj = max(-0.02, min(0.04, headwind_ms * 0.005))
                p = base_power_w * (1.0 + climb_boost + wind_adj)

        powers.append(max(20.0, min(max_cap, p)))

    return powers


def simulate_race_strategy(
    route_data: dict,
    rider_weight_kg: float,
    ftp_w: float,
    target_if: float,
    equipment: dict,
    temp_c: float = 22.0,
    rel_humidity_pct: float = 60.0,
    wind_speed_kmh: float = 10.0,
    wind_from_deg: float = 0.0,
    strategy: str = "optimal",
    max_climb_pct_ftp: float = 1.08,
    max_descent_kmh: float = 68.0,
    split_interval_km: float = 5.0,
    hourly_weather: list = None,
) -> dict:
    """
    Ejecuta la simulación completa de carrera calibrando el plan de potencia para que
    la Potencia Normalizada (NP) alcance el Factor de Intensidad (IF) objetivo.
    Si se entrega `hourly_weather`, actualiza temperatura, humedad, viento y dirección
    dinámicamente hora a hora a medida que avanza el reloj de carrera.
    """
    segments = route_data["segments"]
    total_mass_kg = float(rider_weight_kg) + float(equipment["bike_and_gear_kg"])
    cda_base = float(equipment["cda"])
    crr = float(equipment["crr"])
    efficiency = float(equipment["efficiency"])
    sail_factor = float(equipment.get("sail_factor", 0.03))
    wind_speed_ms = float(wind_speed_kmh) / 3.6
    target_np = float(ftp_w) * float(target_if)
    max_speed_ms = max(12.0, float(max_descent_kmh) / 3.6)
    avg_route_ele = float(route_data.get("avg_ele_m", 0.0))

    def _run_pass(base_p: float):
        raw_powers = _generate_raw_power_profile(
            segments=segments,
            base_power_w=base_p,
            ftp_w=ftp_w,
            strategy=strategy,
            wind_speed_ms=wind_speed_ms,
            wind_from_deg=wind_from_deg,
            max_climb_pct_ftp=max_climb_pct_ftp,
        )

        seg_results = []
        seg_times = []
        seg_powers = []
        prev_v = 8.5  # velocidad inicial de arranque (~30 km/h lanzada)
        cum_t = 0.0
        total_work_j = 0.0
        e_aero = 0.0
        e_roll = 0.0
        e_grav_pos = 0.0

        for seg, p_w in zip(segments, raw_powers):
            seg_t_c, seg_hum, seg_w_kmh, seg_w_deg, seg_p_hpa = _sample_hourly_weather(
                hourly_weather=hourly_weather,
                elapsed_hours=cum_t / 3600.0,
                fallback_temp=float(temp_c),
                fallback_hum=float(rel_humidity_pct),
                fallback_wind_kmh=float(wind_speed_kmh),
                fallback_wind_deg=float(wind_from_deg),
            )
            # Ajustar presión barométrica por diferencia de altitud local respecto a la media de la ruta
            local_p_hpa = None
            if seg_p_hpa is not None:
                dh = float(seg["ele_m"]) - avg_route_ele
                local_p_hpa = float(seg_p_hpa) * math.exp(-dh / 8400.0)

            rho = calc_air_density(seg["ele_m"], seg_t_c, seg_hum, surface_pressure_hpa=local_p_hpa)
            seg_w_ms = seg_w_kmh / 3.6
            rel_angle = math.radians(seg_w_deg - seg["bearing_deg"])
            headwind_ms = seg_w_ms * math.cos(rel_angle)
            crosswind_ms = seg_w_ms * abs(math.sin(rel_angle))

            # En modo óptimo con clima horario dinámico, afinar potencia al viento real de esa hora
            eff_p_w = p_w
            if hourly_weather and strategy == "optimal" and seg["grade"] > -0.02:
                delta_hw = headwind_ms - (wind_speed_ms * math.cos(math.radians(wind_from_deg - seg["bearing_deg"])))
                eff_p_w = max(20.0, min(ftp_w * max_climb_pct_ftp, p_w * (1.0 + max(-0.04, min(0.05, delta_hw * 0.008)))))

            # Límite realista de velocidad en curva cerrada
            seg_max_v = max_speed_ms
            if seg.get("turn_deg", 0.0) > 55.0:
                seg_max_v = min(seg_max_v, 9.5)   # ~34 km/h curva cerrada
            elif seg.get("turn_deg", 0.0) > 35.0:
                seg_max_v = min(seg_max_v, 12.5)  # ~45 km/h curva media

            sol = solve_segment_speed(
                power_rider_w=eff_p_w,
                total_mass_kg=total_mass_kg,
                cda_base=cda_base,
                crr=crr,
                efficiency=efficiency,
                rho=rho,
                grade=seg["grade"],
                headwind_ms=headwind_ms,
                crosswind_ms=crosswind_ms,
                sail_factor=sail_factor,
                max_speed_ms=seg_max_v,
            )

            # Inercia suave entre segmentos consecutivos (transiciones realistas)
            v_eff = 0.78 * sol["speed_ms"] + 0.22 * prev_v
            v_eff = max(1.0, min(seg_max_v, v_eff))
            prev_v = v_eff

            t_seg = seg["length_m"] / v_eff
            cum_t += t_seg
            total_work_j += eff_p_w * t_seg
            e_aero += sol["p_aero"] * t_seg
            e_roll += max(0.0, sol["p_roll"] * t_seg)
            if sol["p_grav"] > 0:
                e_grav_pos += sol["p_grav"] * t_seg

            seg_times.append(t_seg)
            seg_powers.append(eff_p_w)
            seg_results.append({
                "dist_km": seg["dist_mid_km"],
                "dist_end_km": seg["dist_end_m"] / 1000.0,
                "lat": seg["lat"],
                "lon": seg["lon"],
                "ele_m": seg["ele_m"],
                "grade_pct": round(seg["grade"] * 100.0, 2),
                "power_w": round(eff_p_w, 1),
                "pct_ftp": round((eff_p_w / ftp_w) * 100.0, 1),
                "speed_kmh": round(v_eff * 3.6, 1),
                "time_seg_s": t_seg,
                "cum_time_s": cum_t,
                "headwind_kmh": round(headwind_ms * 3.6, 1),
                "crosswind_kmh": round(crosswind_ms * 3.6, 1),
                "temp_c": round(seg_t_c, 1),
                "humidity_pct": round(seg_hum, 0),
                "rho": rho,
                "lap": seg.get("lap", 1),
            })

        np_w = _compute_normalized_power(seg_powers, seg_times)
        return seg_results, seg_times, seg_powers, np_w, cum_t, total_work_j, e_aero, e_roll, e_grav_pos

    # Calibración iterativa (bisección de 10 pasos) para ajustar base_power hasta que NP == target_np
    p_lo, p_hi = max(50.0, target_np * 0.65), min(ftp_w * 1.25, target_np * 1.25)
    best_pack = None
    for _ in range(10):
        p_mid = 0.5 * (p_lo + p_hi)
        pack = _run_pass(p_mid)
        np_got = pack[3]
        best_pack = pack
        if abs(np_got - target_np) <= 0.6:
            break
        if np_got < target_np:
            p_lo = p_mid
        else:
            p_hi = p_mid

    seg_results, seg_times, seg_powers, np_w, total_time_s, total_work_j, e_aero, e_roll, e_grav_pos = best_pack

    total_dist_km = route_data["total_distance_km"]
    avg_speed_kmh = (total_dist_km / (total_time_s / 3600.0)) if total_time_s > 0 else 0.0
    avg_power_w = (total_work_j / total_time_s) if total_time_s > 0 else 0.0
    actual_if = (np_w / ftp_w) if ftp_w > 0 else 0.0
    vi = (np_w / avg_power_w) if avg_power_w > 0 else 1.0
    tss = (total_time_s * np_w * actual_if) / (ftp_w * 36.0) if ftp_w > 0 else 0.0
    work_kj = total_work_j / 1000.0
    max_speed_kmh = max((s["speed_kmh"] for s in seg_results), default=0.0)

    # Desglose porcentual de resistencias
    e_total_resist = max(1.0, e_aero + e_roll + e_grav_pos)
    resist_breakdown = {
        "aero_pct": round(100.0 * e_aero / e_total_resist, 1),
        "gravity_pct": round(100.0 * e_grav_pos / e_total_resist, 1),
        "rolling_pct": round(100.0 * e_roll / e_total_resist, 1),
    }

    # Plan nutricional e hidratación ajustado a duración, kJ y temperatura
    duration_h = total_time_s / 3600.0
    if duration_h < 1.1:
        cho_g_h = 45
    elif duration_h < 2.5:
        cho_g_h = 70
    elif duration_h < 4.5:
        cho_g_h = 85
    else:
        cho_g_h = 90

    # Fluido por hora ajustado a temperatura y humedad reales vividas durante la carrera
    avg_temp_race = (
        sum(s.get("temp_c", temp_c) * s["time_seg_s"] for s in seg_results) / max(1.0, total_time_s)
        if seg_results else float(temp_c)
    )
    avg_hum_race = (
        sum(s.get("humidity_pct", rel_humidity_pct) * s["time_seg_s"] for s in seg_results) / max(1.0, total_time_s)
        if seg_results else float(rel_humidity_pct)
    )
    heat_bonus = max(0.0, (avg_temp_race - 18.0) * 28.0) + max(0.0, (avg_hum_race - 60.0) * 3.0)
    fluid_ml_h = int(round(min(1200.0, max(500.0, 550.0 + heat_bonus)) / 50.0) * 50)
    sodium_mg_h = int(round(min(1100.0, max(450.0, 500.0 + (avg_temp_race - 18.0) * 25.0)) / 50.0) * 50)

    nutrition = {
        "cho_g_per_h": cho_g_h,
        "cho_total_g": int(round(cho_g_h * duration_h)),
        "fluid_ml_per_h": fluid_ml_h,
        "fluid_total_l": round((fluid_ml_h * duration_h) / 1000.0, 1),
        "sodium_mg_per_h": sodium_mg_h,
        "sodium_total_mg": int(round(sodium_mg_h * duration_h)),
    }

    # Construir tabla de splits cada `split_interval_km`
    splits = _build_splits_table(seg_results, split_interval_km=split_interval_km, ftp_w=ftp_w)

    avg_rho = sum(s["rho"] for s in seg_results) / max(1, len(seg_results))
    return {
        "summary": {
            "total_time_s": round(total_time_s, 1),
            "total_time_str": format_hms(total_time_s),
            "distance_km": total_dist_km,
            "elevation_gain_m": route_data["elevation_gain_m"],
            "avg_speed_kmh": round(avg_speed_kmh, 2),
            "max_speed_kmh": round(max_speed_kmh, 1),
            "avg_power_w": round(avg_power_w, 1),
            "norm_power_w": round(np_w, 1),
            "w_per_kg_np": round(np_w / max(30.0, rider_weight_kg), 2),
            "intensity_factor": round(actual_if, 3),
            "vi": round(vi, 3),
            "tss": round(tss, 1),
            "work_kj": round(work_kj, 0),
            "cda": cda_base,
            "crr": crr,
            "total_mass_kg": round(total_mass_kg, 2),
            "avg_rho": round(avg_rho, 4),
            "avg_temp_c": round(avg_temp_race, 1),
            "avg_humidity_pct": int(round(avg_hum_race)),
            "dynamic_weather": bool(hourly_weather),
        },
        "resistance": resist_breakdown,
        "nutrition": nutrition,
        "splits": splits,
        "segments": seg_results,
    }


def _build_splits_table(seg_results: list, split_interval_km: float = 5.0, ftp_w: float = 250.0) -> list:
    """Agrupa los segmentos detallados en una tabla de splits clara para la carrera."""
    if not seg_results:
        return []
    interval = max(1.0, float(split_interval_km))
    splits = []
    bucket = []
    next_mark = interval
    split_start_km = 0.0

    def _flush_bucket(b_items, start_km, end_km):
        t_b = sum(x["time_seg_s"] for x in b_items)
        if t_b <= 0:
            return None
        d_b = max(0.01, end_km - start_km)
        avg_p = sum(x["power_w"] * x["time_seg_s"] for x in b_items) / t_b
        avg_v = d_b / (t_b / 3600.0)
        avg_g = sum(x["grade_pct"] for x in b_items) / len(b_items)
        avg_hw = sum(x["headwind_kmh"] for x in b_items) / len(b_items)
        cum_t = b_items[-1]["cum_time_s"]

        if avg_g >= 2.5:
            terrain = "⛰️ Subida"
        elif avg_g <= -2.5:
            terrain = "⬇️ Bajada"
        else:
            terrain = "➡️ Llano"

        if avg_hw >= 4.0:
            wind_lbl = f"🔴 Contra ({avg_hw:+.0f} km/h)"
        elif avg_hw <= -4.0:
            wind_lbl = f"🟢 Favor ({abs(avg_hw):.0f} km/h)"
        else:
            wind_lbl = "🔵 Cruzado / Calma"

        return {
            "Tramo": f"Km {start_km:.1f}–{end_km:.1f}",
            "Terreno": terrain,
            "Pend. %": round(avg_g, 1),
            "Viento": wind_lbl,
            "Potencia Obj. (W)": int(round(avg_p)),
            "% FTP": f"{(avg_p / max(1.0, ftp_w)) * 100.0:.0f}%",
            "Vel. Est. (km/h)": round(avg_v, 1),
            "Tiempo Tramo": format_hms(t_b, short=True),
            "Tiempo Acum.": format_hms(cum_t),
        }

    for item in seg_results:
        bucket.append(item)
        if item["dist_end_km"] >= next_mark - 0.02:
            row = _flush_bucket(bucket, split_start_km, item["dist_end_km"])
            if row:
                splits.append(row)
            split_start_km = item["dist_end_km"]
            next_mark += interval
            bucket = []

    if bucket:
        row = _flush_bucket(bucket, split_start_km, bucket[-1]["dist_end_km"])
        if row:
            splits.append(row)

    return splits


def format_hms(seconds: float, short: bool = False) -> str:
    """Formatea segundos en HH:MM:SS (o MM:SS si short=True y < 1h)."""
    s = max(0, int(round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if short and h == 0:
        return f"{m:02d}:{sec:02d}"
    return f"{h:02d}:{m:02d}:{sec:02d}"


def format_time_delta(delta_s: float) -> str:
    """Formatea diferencia de tiempo en segundos con signo claro (ej. '-02:18' o '+01:05')."""
    sign = "-" if delta_s < -0.5 else ("+" if delta_s > 0.5 else "")
    return f"{sign}{format_hms(abs(delta_s), short=True)}"
