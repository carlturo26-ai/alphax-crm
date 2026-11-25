"""
running_strategy_engine.py — Motor científico de simulación y estrategias de carrera a pie
(AlphaX Race Split — Modo Running: Por Ritmo/Pace + PB 5K–42K y Por Potencia Stryd/Garmin rCP).

Calibrado exactamente con la física de Stryd y el SuperPower Calculator de Steve Palladino:
  - Running Effectiveness (RE) en unidades Stryd/Palladino:
        RE = velocidad (m/s) / (Potencia (W) / Peso_Stryd (kg))   [típico: 0.90 – 1.03]
    En llano y sin viento:  P_Stryd = (m * v) / RE  <==>  v = RE * (P / m)
  - Coste por pendiente: Minetti et al. (2002) en subidas + curva GAP biomecánica (Strava/Stryd) en bajadas.
  - Viento incremental (Stryd Air Power): solo el viento relativo respecto a calma (v + hw vs v) suma/resta ΔP_air.
  - Ajuste ambiental estilo Stryd ("Trained At" vs "Racing At"): penaliza únicamente la diferencia
    de temperatura efectiva/humedad y altitud respecto a las condiciones donde el atleta entrena habitualmente.
  - Proyección de marcas personales (5K, 10K, 21.1K, 42.2K) vía Riegel + estimación de umbral LT2 y Stryd rCP.
"""

import math
import re
from typing import Dict, List, Optional, Tuple

from race_strategy_engine import calc_air_density, format_hms, format_time_delta


# ═════════════════════════════════════════════════════════════════════
#  CATÁLOGOS DE EQUIPAMIENTO, SUPERFICIE Y PRESETS DE RUNNING
# ═════════════════════════════════════════════════════════════════════

RUNNING_SHOES: Dict[str, Dict] = {
    "Supershoe Placa de Carbono (Vaporfly / Alphafly / Adios Pro / Metaspeed)": {
        "delta_re": +0.020,        # +0.020 RE (~2.1% más veloz vs Daily Trainer)
        "economy_factor": 0.978,   # usado en modo Pace al comparar calzado
        "mass_g": 205,
        "short": "Carbono Supershoe",
    },
    "Competición Ligera / Mixta (placa nylon o espuma PEBA)": {
        "delta_re": +0.010,        # +0.010 RE (~1.0% vs Daily Trainer)
        "economy_factor": 0.990,
        "mass_g": 225,
        "short": "Mixta / Racing",
    },
    "Entrenamiento Diario (Daily Trainer)": {
        "delta_re": 0.000,         # Referencia base 0.000
        "economy_factor": 1.000,
        "mass_g": 275,
        "short": "Entrenamiento Diario",
    },
    "Trail Running con Tacos (Montaña / Terreno técnico)": {
        "delta_re": -0.020,        # -0.020 RE en asfalto por peso/tacos
        "economy_factor": 1.022,
        "mass_g": 305,
        "short": "Trail con Tacos",
    },
}

RUNNING_SURFACES: Dict[str, Dict] = {
    "Asfalto liso de competición": {"factor": 1.000, "delta_re": +0.005},
    "Asfalto urbano estándar / irregular": {"factor": 1.000, "delta_re": 0.000},
    "Adoquín / Destapado compacto (Gravel)": {"factor": 1.038, "delta_re": -0.035},
    "Sendero Trail / Montaña técnica": {"factor": 1.115, "delta_re": -0.095},
}

RUNNING_RACE_PRESETS: Dict[str, Dict] = {
    "5K (Carrera Rápida)": {
        "dist_km": 5.0,
        "default_pct_rcp": 103.0,
        "split_km": 1.0,
        "post_bike_fatigue": 0.0,
        "desc": "Esfuerzo por encima de umbral / rCP (102–105% rCP).",
    },
    "10K (Asfalto)": {
        "dist_km": 10.0,
        "default_pct_rcp": 98.5,
        "split_km": 1.0,
        "post_bike_fatigue": 0.0,
        "desc": "Muy cercano a umbral LT2 / rCP (97–100% rCP).",
    },
    "21.1K Media Maratón": {
        "dist_km": 21.0975,
        "default_pct_rcp": 93.0,
        "split_km": 2.0,
        "post_bike_fatigue": 0.0,
        "desc": "Ritmo sostenido sub-umbral (91–94% rCP, Palladino Riegel -0.09/-0.10).",
    },
    "42.2K Maratón": {
        "dist_km": 42.195,
        "default_pct_rcp": 86.0,
        "split_km": 5.0,
        "post_bike_fatigue": 0.0,
        "desc": "Gestión aeróbica y glucógeno (84–88% rCP). Negative split clave.",
    },
    "10K Triatlón Olímpico (Post-Bici)": {
        "dist_km": 10.0,
        "default_pct_rcp": 93.0,
        "split_km": 1.0,
        "post_bike_fatigue": 0.045,
        "desc": "Incluye fatiga neuromuscular post-ciclismo (~4.5%).",
    },
    "21.1K Ironman 70.3 (Post-Bici)": {
        "dist_km": 21.0975,
        "default_pct_rcp": 86.5,
        "split_km": 2.0,
        "post_bike_fatigue": 0.060,
        "desc": "Media maratón tras 90 km en bici (~6% fatiga acumulada).",
    },
    "42.2K Ironman Full (Post-Bici)": {
        "dist_km": 42.195,
        "default_pct_rcp": 78.0,
        "split_km": 5.0,
        "post_bike_fatigue": 0.095,
        "desc": "Maratón tras 180 km en bici (~9.5% fatiga acumulada).",
    },
    "Trail / Carrera de Montaña": {
        "dist_km": 21.0,
        "default_pct_rcp": 84.0,
        "split_km": 2.0,
        "post_bike_fatigue": 0.0,
        "desc": "Prioriza esfuerzo/GAP constante en subidas y control excéntrico.",
    },
}

RUNNING_STRATEGIES: Dict[str, str] = {
    "Esfuerzo / GAP / Potencia Constante (Óptimo en lomas y montaña)": "even_effort",
    "Negative Split Progresivo (Conservador 1ra mitad → Cierre fuerte)": "negative_split",
    "Ritmo Constante (min/km fijo — solo recomendado en circuitos planos)": "constant_pace",
}

PB_DISTANCES_KM: Dict[str, float] = {
    "5K": 5.0,
    "10K": 10.0,
    "21K (Media Maratón)": 21.0975,
    "42K (Maratón)": 42.195,
}


# ═════════════════════════════════════════════════════════════════════
#  UTILIDADES DE TIEMPO, RITMO Y RUNNING EFFECTIVENESS (RE STRYD)
# ═════════════════════════════════════════════════════════════════════

def parse_time_str_to_sec(text: Optional[str]) -> Optional[float]:
    """
    Convierte strings de tiempo en segundos:
      - "19:30"    -> 1170 s (MM:SS)
      - "40:15"    -> 2415 s (MM:SS)
      - "1:28:40"  -> 5320 s (H:MM:SS)
      - "3:08:00"  -> 11280 s (H:MM:SS)
      - "5:15"     -> 315 s (MM:SS)
    """
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s:
        return None
    s = s.replace("/km", "").replace("min", "").strip()
    parts = re.split(r"[:\s]+", s)
    parts = [p for p in parts if p != ""]
    try:
        if len(parts) == 3:
            h, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
            total = h * 3600.0 + m * 60.0 + sec
            return total if total > 0 else None
        if len(parts) == 2:
            a, b = float(parts[0]), float(parts[1])
            total = a * 60.0 + b
            return total if total > 0 else None
        if len(parts) == 1:
            val = float(parts[0])
            return val * 60.0 if val > 0 else None
    except ValueError:
        return None
    return None


def format_pace(sec_per_km: Optional[float]) -> str:
    """Formatea segundos/km como '5:12'."""
    if sec_per_km is None or sec_per_km <= 0 or math.isinf(sec_per_km) or math.isnan(sec_per_km):
        return "—"
    sec_round = int(round(max(120.0, min(1800.0, float(sec_per_km)))))
    m = sec_round // 60
    s = sec_round % 60
    return f"{m}:{s:02d}"


def format_pace_unit(sec_per_km: Optional[float]) -> str:
    p = format_pace(sec_per_km)
    return f"{p} /km" if p != "—" else "—"


def format_pace_delta(delta_sec_per_km: float) -> str:
    if abs(delta_sec_per_km) < 0.5:
        return "0:00 /km"
    sign = "+" if delta_sec_per_km > 0 else "-"
    val = int(round(abs(delta_sec_per_km)))
    m = val // 60
    s = val % 60
    return f"{sign}{m}:{s:02d} /km"


def pace_to_speed_ms(sec_per_km: float) -> float:
    return 1000.0 / max(120.0, float(sec_per_km))


def speed_ms_to_pace_sec(v_ms: float) -> float:
    return 1000.0 / max(0.5, float(v_ms))


def estimate_default_stryd_re(rcp_wkg: float) -> float:
    """
    Estima el Running Effectiveness (RE = v / (W/kg), unidades Stryd / Steve Palladino)
    esperado en asfalto cuando el atleta aún no lo ha calibrado con un entreno real:
      - 3.0 W/kg -> ~0.93
      - 3.5 W/kg -> ~0.94–0.95 (ej. 236 W / 67 kg = 3.52 W/kg -> 0.94)
      - 4.1 W/kg -> ~0.96
      - 4.8 W/kg -> ~0.98
      - 5.5+ W/kg -> ~1.00
    """
    wkg = max(2.2, min(6.5, float(rcp_wkg or 3.6)))
    re_val = 0.865 + 0.022 * wkg
    return round(max(0.88, min(1.03, re_val)), 2)


def calc_stryd_re_from_workout(power_w: float, pace_sec_km: float, stryd_weight_kg: float) -> float:
    """
    Calcula el Running Effectiveness real (RE Stryd / Palladino) desde un entreno o carrera en llano:
        RE = velocidad (m/s) / (Potencia_media (W) / Peso_Stryd (kg))
    """
    v_ms = pace_to_speed_ms(pace_sec_km)
    wkg = max(0.5, float(power_w) / max(35.0, float(stryd_weight_kg)))
    return round(max(0.70, min(1.20, v_ms / wkg)), 3)


# ═════════════════════════════════════════════════════════════════════
#  FÍSICA DE RUNNING STRYD / PALLADINO + MINETTI / GAP
# ═════════════════════════════════════════════════════════════════════

def calc_run_cda(weight_kg: float, height_cm: float) -> float:
    """Área frontal aerodinámica efectiva corriendo (m², típicamente 0.21–0.28 m²)."""
    w = max(40.0, min(120.0, float(weight_kg)))
    h = max(140.0, min(215.0, float(height_cm)))
    return round(0.24 * ((h / 175.0) ** 0.65) * ((w / 70.0) ** 0.25), 4)


def gap_cost_factor(grade: float) -> float:
    """
    Factor de coste mecánico/metabólico por pendiente relativo al llano (= 1.000 en 0%):
      - Subidas (grade >= 0): polinomio de Minetti et al. (2002) normalizado por C(0)=3.6 J/kg/m.
      - Bajadas (grade < 0): curva GAP biomecánica (Strava/Stryd), beneficio máx. ~12.5% cerca
        de -9% y aumento gradual del coste por frenado excéntrico por debajo de -12%.
    """
    g = max(-0.30, min(0.30, float(grade)))
    if g >= 0.0:
        c = (
            155.4 * (g ** 5)
            - 30.4 * (g ** 4)
            - 43.3 * (g ** 3)
            + 46.3 * (g ** 2)
            + 19.5 * g
            + 3.6
        )
        return max(1.0, c / 3.6)
    else:
        ag = abs(g)
        if ag <= 0.09:
            return 0.875 + 0.125 * (((0.09 - ag) / 0.09) ** 2)
        else:
            over = ag - 0.09
            return 0.875 + 11.8 * (over ** 1.9)


def running_power_watts(
    v_ms: float,
    grade: float,
    weight_kg: float,
    cda_run: float,
    rho: float,
    headwind_ms: float = 0.0,
    stryd_re: float = 0.95,
) -> float:
    """
    Potencia total en Running calibrada exactamente a Stryd / Palladino (W):
        P_base = (m * gap_cost_factor(i) * v) / stryd_re
        ΔP_wind = 0.5 * rho * CdA_run * [(v + hw)*|v + hw| - v^2] * v
        P_total = P_base + ΔP_wind

    Nota física clave: En llano (grade=0) y sin viento (hw=0), ΔP_wind = 0 y
    P_total = (m * v) / stryd_re  <==>  v = stryd_re * (P / m),
    coincidiendo al 100% con Steve Palladino SuperPower Calculator y Stryd!
    """
    v = max(0.5, float(v_ms))
    re_safe = max(0.65, min(1.25, float(stryd_re)))
    p_base = (float(weight_kg) * gap_cost_factor(grade) * v) / re_safe
    hw = float(headwind_ms)
    if abs(hw) > 0.05:
        v_air = v + hw
        dp_wind = 0.5 * float(rho) * float(cda_run) * (v_air * abs(v_air) - v * v) * v
    else:
        dp_wind = 0.0
    return max(20.0, p_base + dp_wind)


def solve_running_speed_for_power(
    target_power_w: float,
    grade: float,
    weight_kg: float,
    cda_run: float,
    rho: float,
    headwind_ms: float = 0.0,
    stryd_re: float = 0.95,
) -> float:
    """
    Resuelve la velocidad v (m/s) para una potencia objetivo dada en Running (Stryd / Palladino).
    """
    p_obj = max(35.0, float(target_power_w))
    re_safe = max(0.65, min(1.25, float(stryd_re)))
    k_lin = (float(weight_kg) * gap_cost_factor(grade)) / re_safe
    hw = float(headwind_ms)

    # Caso sin viento (solución cerrada exacta en 1 operación!)
    if abs(hw) <= 0.05:
        return max(0.8, min(7.5, p_obj / max(10.0, k_lin)))

    # Con viento: bisección ultra-estable (22 iteraciones -> precisión < 0.0005 m/s)
    lo, hi = 0.8, 7.5
    for _ in range(24):
        mid = 0.5 * (lo + hi)
        p_mid = running_power_watts(mid, grade, weight_kg, cda_run, rho, hw, re_safe)
        if p_mid < p_obj:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ═════════════════════════════════════════════════════════════════════
#  PROYECCIÓN DESDE MARCAS PERSONALES (PB) Y ESTIMACIÓN DE STRYD rCP
# ═════════════════════════════════════════════════════════════════════

def riegel_project_time(ref_dist_km: float, ref_time_sec: float, target_dist_km: float) -> float:
    """
    Proyección de Riegel calibrada para corredores de resistencia:
      T2 = T1 * (D2 / D1) ^ exp
    """
    d1 = max(1.0, float(ref_dist_km))
    t1 = max(120.0, float(ref_time_sec))
    d2 = max(1.0, float(target_dist_km))
    if abs(d2 - d1) < 0.05:
        return t1
    if d2 > 25.0 and d1 <= 25.0:
        t_hm = t1 * ((21.0975 / d1) ** 1.06)
        return t_hm * ((d2 / 21.0975) ** 1.075)
    elif d1 > 25.0 and d2 <= 25.0:
        t_hm = t1 / ((d1 / 21.0975) ** 1.075)
        return t_hm * ((d2 / 21.0975) ** 1.06)
    exp = 1.072 if (d1 > 25.0 and d2 > 25.0) else 1.06
    return t1 * ((d2 / d1) ** exp)


def estimate_run_metrics_from_pb(
    pb_dist_km: float,
    pb_time_sec: float,
    weight_kg: float = 70.0,
    height_cm: float = 175.0,
    target_course_km: float = 21.0975,
    stryd_re: Optional[float] = None,
) -> Dict:
    """
    A partir de una marca personal (PB en 5K, 10K, 21K o 42K), calcula:
      - Tiempos y ritmos equivalentes en llano para 5K, 10K, 21.1K, 42.2K
      - Ritmo de Umbral LT2 estimado (~ritmo máximo sostenible de 55–60 min)
      - Ritmo GAP proyectado para la distancia exacta del circuito objetivo
      - Potencia Crítica Stryd (rCP) estimada en W y W/kg usando RE realista
    """
    d_ref = max(1.0, float(pb_dist_km))
    t_ref = max(600.0, float(pb_time_sec))

    eq_5k = riegel_project_time(d_ref, t_ref, 5.0)
    eq_10k = riegel_project_time(d_ref, t_ref, 10.0)
    eq_21k = riegel_project_time(d_ref, t_ref, 21.0975)
    eq_42k = riegel_project_time(d_ref, t_ref, 42.195)
    eq_target = riegel_project_time(d_ref, t_ref, max(1.0, float(target_course_km)))

    # Distancia sostenible en ~55–58 min (umbral LT2 / Stryd CP)
    d_lt = 12.0
    for _ in range(6):
        t_lt = riegel_project_time(d_ref, t_ref, d_lt)
        d_lt = max(5.0, min(21.5, d_lt * (3480.0 / max(300.0, t_lt)) ** 0.94))
    lt2_pace_sec_km = riegel_project_time(d_ref, t_ref, d_lt) / d_lt
    lt2_speed_ms = pace_to_speed_ms(lt2_pace_sec_km)

    # Estimar RE consistente con velocidad de umbral si el usuario no pasó uno explícito
    if stryd_re is None:
        # Corredor más rápido tiende a tener RE ligeramente mayor (0.93 a ~3.0 m/s, 0.96 a ~4.0 m/s, 0.99 a ~5.0 m/s)
        est_re = round(max(0.90, min(1.02, 0.84 + 0.030 * lt2_speed_ms)), 2)
    else:
        est_re = float(stryd_re)

    est_rcp_w = (float(weight_kg) * lt2_speed_ms) / max(0.75, est_re)

    return {
        "ref_dist_km": d_ref,
        "ref_time_sec": t_ref,
        "ref_pace_sec_km": t_ref / d_ref,
        "lt2_pace_sec_km": round(lt2_pace_sec_km, 1),
        "lt2_pace_str": format_pace(lt2_pace_sec_km),
        "est_re": est_re,
        "est_rcp_w": round(est_rcp_w, 0),
        "est_rcp_wkg": round(est_rcp_w / max(35.0, float(weight_kg)), 2),
        "target_flat_time_sec": round(eq_target, 1),
        "target_flat_pace_sec_km": round(eq_target / max(1.0, float(target_course_km)), 1),
        "equivalents": {
            "5K": {"time_sec": eq_5k, "time_str": format_hms(eq_5k), "pace_str": format_pace(eq_5k / 5.0)},
            "10K": {"time_sec": eq_10k, "time_str": format_hms(eq_10k), "pace_str": format_pace(eq_10k / 10.0)},
            "21.1K": {"time_sec": eq_21k, "time_str": format_hms(eq_21k), "pace_str": format_pace(eq_21k / 21.0975)},
            "42.2K": {"time_sec": eq_42k, "time_str": format_hms(eq_42k), "pace_str": format_pace(eq_42k / 42.195)},
        },
    }


def calc_environmental_derating(
    temp_c: float,
    humidity_pct: float,
    course_ele_m: float,
    home_altitude_m: float = 0.0,
    trained_temp_c: float = 18.0,
    trained_humidity_pct: float = 65.0,
) -> Dict:
    """
    Ajuste fisiológico estilo Stryd ("Trained At" vs "Racing At"):
      - Calor/Humedad: solo penaliza cuando la carga térmica de la carrera supera la de entrenamiento habitual
        (y está por encima de la zona termoneutral ~14°C).
      - Altitud: solo penaliza la altitud de carrera que exceda la altitud habitual donde entrena (>1000 m),
        a razón de ~0.65% por cada 100 m adicionales (Péronnet et al.).
    """
    def _t_eff(t_val: float, rh_val: float) -> float:
        rh_c = max(10.0, min(100.0, float(rh_val)))
        return float(t_val) + 0.050 * max(0.0, rh_c - 50.0) * max(0.0, float(t_val) - 12.0) / 10.0

    t_eff_race = _t_eff(temp_c, humidity_pct)
    t_eff_train = max(14.5, _t_eff(trained_temp_c, trained_humidity_pct))

    # Solo penaliza si la carrera es más calurosa/húmeda que lo entrenado
    heat_excess = max(0.0, t_eff_race - t_eff_train)
    heat_penalty = min(0.18, 0.0032 * (heat_excess ** 1.15))

    # Altitud no aclimatada respecto a donde entrena
    home_alt = max(0.0, float(home_altitude_m or 0.0))
    effective_base_alt = max(1000.0, home_alt)
    alt_excess_m = max(0.0, float(course_ele_m) - effective_base_alt)
    alt_penalty = min(0.20, (alt_excess_m / 100.0) * 0.0065)

    # Pequeño bonus fisiológico (~0.5–1.5%) si entrena a >1600 m y compite cerca del nivel del mar (<600 m)
    sea_level_bonus = 0.0
    if home_alt >= 1600.0 and float(course_ele_m) <= 600.0:
        sea_level_bonus = min(0.018, ((home_alt - 1200.0) / 1000.0) * 0.012)

    combined_factor = max(0.72, min(1.025, (1.0 - heat_penalty) * (1.0 - alt_penalty) * (1.0 + sea_level_bonus)))
    return {
        "heat_penalty_pct": round(heat_penalty * 100.0, 2),
        "alt_penalty_pct": round(alt_penalty * 100.0, 2),
        "sea_level_bonus_pct": round(sea_level_bonus * 100.0, 2),
        "combined_factor": round(combined_factor, 5),
        "t_eff_race_c": round(t_eff_race, 1),
        "t_eff_train_c": round(t_eff_train, 1),
    }


# ═════════════════════════════════════════════════════════════════════
#  NUTRICIÓN Y CALENDARIO DE GELES PARA RUNNING
# ═════════════════════════════════════════════════════════════════════

def calculate_running_nutrition(
    total_time_sec: float,
    total_dist_km: float,
    weight_kg: float,
    temp_c: float,
    humidity_pct: float,
    seg_cum_dist_km: Optional[List[float]] = None,
    seg_cum_time_sec: Optional[List[float]] = None,
    cho_per_gel_g: int = 25,
) -> Dict:
    """
    Calcula carbohidratos, hidratación, sodio y el calendario exacto de geles (km + minuto)
    adaptado a la duración de la carrera a pie (5K / 10K / 21K / 42K).
    """
    hrs = max(0.1, float(total_time_sec) / 3600.0)
    mins = hrs * 60.0

    if mins < 48:
        cho_g_h = 0
        gel_note = "Carrera <48 min: no necesitas gel en ruta (basta carga previa + enjuague o 1 gel 15 min antes de largar)."
    elif mins < 75:
        cho_g_h = 35
        gel_note = "1–2 geles en ruta (cada ~25–30 min) para sostener ritmo final sin caída de glucosa."
    elif mins < 130:
        cho_g_h = 65
        gel_note = "3–5 geles (cada ~22–25 min) con sorbo de agua en cada puesto de hidratación."
    else:
        cho_g_h = 80
        gel_note = "Alta ingesta (75–90 g/h): 1 gel cada ~18–22 min desde el minuto 18 + electrolitos constantes."

    cho_total_g = int(round(cho_g_h * hrs))

    heat_idx = max(0.0, float(temp_c) - 14.0) + max(0.0, float(humidity_pct) - 55.0) * 0.14
    fluid_ml_h = int(round(max(350.0, min(950.0, 440.0 + heat_idx * 22.0))))
    sodium_mg_h = int(round(max(350.0, min(1000.0, 420.0 + heat_idx * 25.0))))
    fluid_total_l = round((fluid_ml_h * hrs) / 1000.0, 2)
    sodium_total_mg = int(round(sodium_mg_h * hrs))

    gel_schedule: List[Dict] = []
    if cho_g_h > 0 and mins >= 50:
        n_gels = max(1, int(round(cho_total_g / max(20, cho_per_gel_g))))
        usable_min = max(25.0, mins - 10.0)
        first_gel_min = 18.0 if mins >= 95 else 22.0
        if n_gels == 1:
            gel_mins = [min(usable_min, max(20.0, mins * 0.45))]
        else:
            step_m = max(15.0, (usable_min - first_gel_min) / max(1, n_gels - 1))
            gel_mins = [first_gel_min + k * step_m for k in range(n_gels)]
            gel_mins = [gm for gm in gel_mins if gm <= mins - 7.0]

        for idx_g, gm in enumerate(gel_mins, start=1):
            t_target_s = gm * 60.0
            km_mark = (gm / mins) * float(total_dist_km)
            if seg_cum_dist_km and seg_cum_time_sec and len(seg_cum_dist_km) == len(seg_cum_time_sec):
                for k_i, t_s in enumerate(seg_cum_time_sec):
                    if t_s >= t_target_s:
                        km_mark = seg_cum_dist_km[k_i]
                        break
            gel_schedule.append({
                "gel_num": idx_g,
                "minute": int(round(gm)),
                "time_str": format_hms(t_target_s),
                "km": round(km_mark, 1),
                "cho_g": int(cho_per_gel_g),
            })

    kcal_est = int(round(float(weight_kg) * float(total_dist_km) * 1.03))
    return {
        "cho_g_per_h": cho_g_h,
        "cho_total_g": cho_total_g,
        "fluid_ml_per_h": fluid_ml_h,
        "fluid_total_l": fluid_total_l,
        "sodium_mg_per_h": sodium_mg_h,
        "sodium_total_mg": sodium_total_mg,
        "gels_count": len(gel_schedule),
        "cho_per_gel_g": int(cho_per_gel_g),
        "gel_schedule": gel_schedule,
        "gel_note": gel_note,
        "kcal_est": kcal_est,
    }


# ═════════════════════════════════════════════════════════════════════
#  MOTOR PRINCIPAL DE SIMULACIÓN DE RUNNING
# ═════════════════════════════════════════════════════════════════════

def simulate_running_strategy(
    route_data: Dict,
    runner_weight_kg: float,
    runner_height_cm: float,
    mode: str = "pace",               # "pace" (Ritmo/PB) | "stryd" (Potencia Running rCP + RE)
    target_gap_sec_km: float = 270.0, # Solo usado si mode == "pace"
    run_cp_w: float = 285.0,          # Potencia Crítica Stryd (W)
    target_pct_rcp: float = 93.0,     # % de rCP objetivo (ej. 93% para 21K)
    target_re: Optional[float] = None,# Running Effectiveness Stryd (ej. 0.94). Si None, se estima desde rCP/kg
    shoe_name: str = "Supershoe Placa de Carbono (Vaporfly / Alphafly / Adios Pro / Metaspeed)",
    base_shoe_name: Optional[str] = None,  # En modo Stryd, calzado incluido en Target RE (para deltas en Qué Pasa Si)
    surface_name: str = "Asfalto urbano estándar / irregular",
    temp_c: float = 20.0,
    humidity_pct: float = 65.0,
    wind_speed_kmh: float = 8.0,
    wind_from_deg: float = 45.0,
    home_altitude_m: float = 0.0,
    trained_temp_c: float = 18.0,
    trained_humidity_pct: float = 65.0,
    strategy: str = "even_effort",    # "even_effort" | "negative_split" | "constant_pace"
    apply_env_derating: bool = True,
    post_bike_fatigue_pct: float = 0.0,
    split_interval_km: float = 1.0,
    hourly_weather: Optional[List[Dict]] = None,
) -> Dict:
    """
    Simula segmento a segmento una carrera a pie sobre cualquier circuito GPX/FIT/TCX,
    calibrado a Stryd y al SuperPower Calculator de Steve Palladino.
    """
    segments = route_data.get("segments") or []
    if not segments:
        raise ValueError("La ruta no tiene segmentos válidos para simular.")

    total_dist_km = float(route_data.get("total_distance_km") or 0.0)
    avg_ele_m = float(route_data.get("avg_ele_m") or 0.0)
    weight = max(38.0, float(runner_weight_kg))

    shoe_spec = RUNNING_SHOES.get(shoe_name, list(RUNNING_SHOES.values())[0])
    surf_spec = RUNNING_SURFACES.get(surface_name, list(RUNNING_SURFACES.values())[1])
    cda_run = calc_run_cda(weight, runner_height_cm)

    # Resolver Running Effectiveness (RE Stryd = v / (W/kg)):
    # - En modo Stryd: target_re ES el RE objetivo en asfalto con el calzado seleccionado (sin distorsiones ocultas).
    #   Si base_shoe_name se pasa (ej. en 'Qué Pasa Si...' o Benchmark de Zapatillas), aplicamos SOLO la diferencia de calzado.
    # - En modo Pace: el RE base se estima desde rCP/kg y cambiar zapatilla ajusta RE vs Mixta.
    rcp_wkg = float(run_cp_w) / weight
    re_base = float(target_re) if (target_re and 0.70 <= float(target_re) <= 1.18) else estimate_default_stryd_re(rcp_wkg)

    if mode == "stryd":
        if base_shoe_name and base_shoe_name in RUNNING_SHOES:
            shoe_re_delta = float(shoe_spec["delta_re"]) - float(RUNNING_SHOES[base_shoe_name]["delta_re"])
        else:
            shoe_re_delta = 0.0
        surf_re_delta = float(surf_spec["delta_re"])
        re_eff = max(0.72, min(1.15, re_base + shoe_re_delta + surf_re_delta))
    else:
        # En modo Pace, la referencia neutra es Mixta (+0.010) en Asfalto urbano (0.000)
        shoe_re_delta = float(shoe_spec["delta_re"]) - 0.010
        surf_re_delta = float(surf_spec["delta_re"])
        re_eff = max(0.72, min(1.15, re_base + shoe_re_delta + surf_re_delta))

    # Ajuste fisiológico ambiental estilo Stryd (Trained At vs Racing At)
    env_info = calc_environmental_derating(
        temp_c=temp_c,
        humidity_pct=humidity_pct,
        course_ele_m=avg_ele_m,
        home_altitude_m=home_altitude_m,
        trained_temp_c=trained_temp_c,
        trained_humidity_pct=trained_humidity_pct,
    )
    env_mult = env_info["combined_factor"] if apply_env_derating else 1.0
    fatigue_mult = max(0.75, 1.0 - max(0.0, float(post_bike_fatigue_pct)) / 100.0)
    physio_mult = env_mult * fatigue_mult

    # Calibrar potencia objetivo base (P_base_w)
    if mode == "stryd":
        p_raw_target = float(run_cp_w) * (float(target_pct_rcp) / 100.0)
        p_base_w = max(60.0, p_raw_target * physio_mult)
        v_gap_base = (p_base_w / weight) * re_eff
    else:
        v_gap_ideal = pace_to_speed_ms(target_gap_sec_km)
        # Potencia equivalente en llano con RE de referencia (re_base)
        p_equiv_ideal = (weight * v_gap_ideal) / max(0.72, re_base)
        p_base_w = max(60.0, p_equiv_ideal * physio_mult)
        v_gap_base = (p_base_w / weight) * re_eff

    v_const_target = v_gap_base

    # Simulación segmento a segmento
    seg_results: List[Dict] = []
    cum_time_s = 0.0
    cum_dist_km_list: List[float] = []
    cum_time_s_list: List[float] = []
    work_j = 0.0
    weighted_p_sum = 0.0
    weighted_gap_speed_sum = 0.0
    p4_time_sum = 0.0

    base_wind_ms = float(wind_speed_kmh) / 3.6
    base_wind_deg = float(wind_from_deg)

    for seg in segments:
        length_m = float(seg["length_m"])
        grade = float(seg["grade"])
        ele_m = float(seg["ele_m"])
        dist_mid_km = float(seg["dist_mid_km"])
        dist_end_km = float(seg["dist_end_m"]) / 1000.0
        bearing_deg = float(seg.get("bearing_deg", 0.0))
        progress = min(1.0, max(0.0, dist_mid_km / max(0.1, total_dist_km)))

        seg_temp = float(temp_c)
        seg_hum = float(humidity_pct)
        seg_wind_ms = base_wind_ms
        seg_wind_deg = base_wind_deg
        if hourly_weather:
            hr_idx = min(len(hourly_weather) - 1, int(cum_time_s // 3600))
            hw_rec = hourly_weather[hr_idx]
            seg_temp = float(hw_rec.get("temp_c", seg_temp))
            seg_hum = float(hw_rec.get("humidity_pct", seg_hum))
            seg_wind_ms = float(hw_rec.get("wind_kmh", wind_speed_kmh)) / 3.6
            seg_wind_deg = float(hw_rec.get("wind_deg", seg_wind_deg))

        rho = calc_air_density(altitude_m=ele_m, temp_c=seg_temp, rel_humidity_pct=seg_hum)
        rel_angle_rad = math.radians(seg_wind_deg - bearing_deg)
        headwind_ms = seg_wind_ms * math.cos(rel_angle_rad)

        if strategy == "negative_split":
            strat_mult = 0.978 + 0.044 * progress
        else:
            strat_mult = 1.0

        if strategy == "constant_pace":
            p_needed = running_power_watts(v_const_target, grade, weight, cda_run, rho, headwind_ms, re_eff)
            p_cap = p_base_w * 1.18
            p_floor = p_base_w * 0.78
            if p_needed > p_cap:
                p_seg = p_cap
                v_seg = solve_running_speed_for_power(p_seg, grade, weight, cda_run, rho, headwind_ms, re_eff)
            elif p_needed < p_floor:
                p_seg = p_floor
                v_seg = solve_running_speed_for_power(p_seg, grade, weight, cda_run, rho, headwind_ms, re_eff)
            else:
                p_seg = p_needed
                v_seg = v_const_target
        else:
            p_seg = p_base_w * strat_mult
            # En bajadas pronunciadas (<-6%), el corredor protege cuádriceps reduciendo ligeramente potencia
            if grade < -0.06:
                p_seg *= max(0.90, 1.0 + (grade + 0.06) * 0.7)
            v_seg = solve_running_speed_for_power(p_seg, grade, weight, cda_run, rho, headwind_ms, re_eff)

        dt = length_m / max(0.5, v_seg)
        cum_time_s += dt
        work_j += p_seg * dt
        weighted_p_sum += p_seg * dt
        p4_time_sum += (p_seg ** 4) * dt

        v_gap_seg = v_seg * gap_cost_factor(grade)
        weighted_gap_speed_sum += v_gap_seg * dt
        pace_seg = speed_ms_to_pace_sec(v_seg)
        gap_pace_seg = speed_ms_to_pace_sec(v_gap_seg)

        cum_dist_km_list.append(dist_end_km)
        cum_time_s_list.append(cum_time_s)

        seg_results.append({
            "dist_km": round(dist_mid_km, 3),
            "dist_end_km": round(dist_end_km, 3),
            "lat": seg["lat"],
            "lon": seg["lon"],
            "ele_m": ele_m,
            "grade_pct": round(grade * 100.0, 2),
            "speed_ms": round(v_seg, 3),
            "speed_kmh": round(v_seg * 3.6, 2),
            "pace_sec_km": round(pace_seg, 1),
            "pace_str": format_pace(pace_seg),
            "gap_sec_km": round(gap_pace_seg, 1),
            "gap_str": format_pace(gap_pace_seg),
            "power_w": round(p_seg, 1),
            "headwind_kmh": round(headwind_ms * 3.6, 1),
            "dt_s": dt,
            "cum_time_s": cum_time_s,
            "temp_c": round(seg_temp, 1),
            "wind_deg": round(seg_wind_deg, 0),
        })

    # Métricas globales
    avg_speed_ms = (total_dist_km * 1000.0) / max(1.0, cum_time_s)
    avg_pace_sec_km = speed_ms_to_pace_sec(avg_speed_ms)
    avg_gap_speed_ms = weighted_gap_speed_sum / max(1.0, cum_time_s)
    avg_gap_sec_km = speed_ms_to_pace_sec(avg_gap_speed_ms)
    avg_power_w = weighted_p_sum / max(1.0, cum_time_s)
    norm_power_w = (p4_time_sum / max(1.0, cum_time_s)) ** 0.25
    ref_rcp = max(80.0, float(run_cp_w))
    intensity_factor = norm_power_w / ref_rcp
    rtss = (cum_time_s * norm_power_w * intensity_factor) / (ref_rcp * 36.0)
    w_per_kg = avg_power_w / weight
    course_re = avg_speed_ms / max(0.5, w_per_kg)

    nutrition = calculate_running_nutrition(
        total_time_sec=cum_time_s,
        total_dist_km=total_dist_km,
        weight_kg=weight,
        temp_c=temp_c,
        humidity_pct=humidity_pct,
        seg_cum_dist_km=cum_dist_km_list,
        seg_cum_time_sec=cum_time_s_list,
    )

    # Tabla de splits (1 / 2 / 5 km)
    step_km = max(0.5, float(split_interval_km))
    n_bins = max(1, int(math.ceil(total_dist_km / step_km)))
    splits: List[Dict] = []
    gel_sched = nutrition.get("gel_schedule", [])

    for b in range(n_bins):
        k0 = b * step_km
        k1 = min(total_dist_km, (b + 1) * step_km)
        if k1 - k0 < 0.08:
            continue
        bin_segs = [sg for sg in seg_results if k0 <= sg["dist_km"] < k1 or (b == n_bins - 1 and sg["dist_km"] >= k0)]
        if not bin_segs:
            continue

        b_time = sum(sg["dt_s"] for sg in bin_segs)
        b_dist_m = sum(sg["speed_ms"] * sg["dt_s"] for sg in bin_segs)
        b_v = b_dist_m / max(0.1, b_time)
        b_pace = speed_ms_to_pace_sec(b_v)
        b_gap_v = sum(pace_to_speed_ms(sg["gap_sec_km"]) * sg["dt_s"] for sg in bin_segs) / max(0.1, b_time)
        b_gap = speed_ms_to_pace_sec(b_gap_v)
        b_p = sum(sg["power_w"] * sg["dt_s"] for sg in bin_segs) / max(0.1, b_time)
        b_grade = sum(sg["grade_pct"] * sg["dt_s"] for sg in bin_segs) / max(0.1, b_time)
        b_hw = sum(sg["headwind_kmh"] * sg["dt_s"] for sg in bin_segs) / max(0.1, b_time)
        b_cum_t = bin_segs[-1]["cum_time_s"]

        if b_grade >= 2.5:
            terr = "⛰️ Subida"
        elif b_grade <= -2.5:
            terr = "⬇️ Bajada"
        elif b_grade >= 0.8:
            terr = "↗️ Falso llano +"
        elif b_grade <= -0.8:
            terr = "↘️ Falso llano -"
        else:
            terr = "➡️ Llano"

        if b_hw >= 5.0:
            v_lbl = f"🔴 Contra ({b_hw:+.0f})"
        elif b_hw <= -5.0:
            v_lbl = f"🟢 Favor ({abs(b_hw):.0f})"
        else:
            v_lbl = "⚪ Neutro"

        gels_in_bin = [g for g in gel_sched if k0 < g["km"] <= k1 + 0.05]
        gel_txt = ", ".join(f"⚡ Gel #{g['gel_num']} ({g['cho_g']}g)" for g in gels_in_bin) if gels_in_bin else "💧 Agua"

        splits.append({
            "Tramo": f"Km {k0:.0f}–{k1:.1f}" if (k1 % 1 > 0.05) else f"Km {k0:.0f}–{k1:.0f}",
            "Terreno": terr,
            "Pend. %": round(b_grade, 1),
            "Ritmo Obj. (/km)": format_pace(b_pace),
            "GAP (/km)": format_pace(b_gap),
            "Potencia Stryd (W)": int(round(b_p)),
            "% rCP": f"{(b_p / ref_rcp) * 100.0:.0f}%",
            "Tiempo Tramo": format_hms(b_time),
            "Tiempo Acum.": format_hms(b_cum_t),
            "Viento": v_lbl,
            "🥤 Gel / Nutrición": gel_txt,
            "_pace_sec": round(b_pace, 1),
            "_gap_sec": round(b_gap, 1),
            "_speed_kmh": round(b_v * 3.6, 2),
        })

    half_km = total_dist_km / 2.0
    t_first_half = next((sg["cum_time_s"] for sg in seg_results if sg["dist_end_km"] >= half_km), cum_time_s / 2.0)
    t_second_half = cum_time_s - t_first_half

    summary = {
        "total_time_sec": round(cum_time_s, 1),
        "total_time_str": format_hms(cum_time_s),
        "total_distance_km": round(total_dist_km, 2),
        "elevation_gain_m": round(float(route_data.get("elevation_gain_m") or 0.0), 0),
        "avg_pace_sec_km": round(avg_pace_sec_km, 1),
        "avg_pace_str": format_pace(avg_pace_sec_km),
        "avg_gap_sec_km": round(avg_gap_sec_km, 1),
        "avg_gap_str": format_pace(avg_gap_sec_km),
        "avg_speed_kmh": round(avg_speed_ms * 3.6, 2),
        "avg_power_w": round(avg_power_w, 0),
        "norm_power_w": round(norm_power_w, 0),
        "w_per_kg": round(w_per_kg, 2),
        "intensity_factor": round(intensity_factor, 3),
        "pct_rcp": round(intensity_factor * 100.0, 1),
        "rtss": round(rtss, 0),
        "work_kj": round(work_j / 1000.0, 0),
        "cda_run": cda_run,
        "target_re": round(re_base, 3),
        "re_eff": round(re_eff, 3),
        "course_re": round(course_re, 3),
        "first_half_str": format_hms(t_first_half),
        "second_half_str": format_hms(t_second_half),
        "split_diff_sec": round(t_second_half - t_first_half, 0),
        "env_info": env_info,
        "mode": mode,
        "strategy": strategy,
    }

    return {
        "summary": summary,
        "nutrition": nutrition,
        "splits": splits,
        "segments": seg_results,
    }


def build_re_sensitivity_table(
    route_data: Dict,
    runner_weight_kg: float,
    runner_height_cm: float,
    run_cp_w: float,
    target_pct_rcp: float,
    base_target_re: float,
    shoe_name: str,
    surface_name: str,
    temp_c: float,
    humidity_pct: float,
    wind_speed_kmh: float,
    wind_from_deg: float,
    home_altitude_m: float,
    trained_temp_c: float,
    trained_humidity_pct: float,
    strategy: str,
    apply_env_derating: bool,
    post_bike_fatigue_pct: float = 0.0,
) -> List[Dict]:
    """
    Tabla de sensibilidad por Running Effectiveness (RE) estilo Steve Palladino SuperPower Calculator:
    muestra el tiempo estimado en este circuito exacto y en llano puro para RE -0.02, -0.01, Base, +0.01, +0.02.
    """
    rows = []
    total_km = float(route_data.get("total_distance_km") or 21.0975)
    p_flat_w = float(run_cp_w) * (float(target_pct_rcp) / 100.0)
    wkg_flat = p_flat_w / max(38.0, float(runner_weight_kg))

    for d_re in (-0.02, -0.01, 0.0, +0.01, +0.02):
        re_i = round(max(0.75, min(1.12, float(base_target_re) + d_re)), 2)
        sim_i = simulate_running_strategy(
            route_data=route_data,
            runner_weight_kg=runner_weight_kg,
            runner_height_cm=runner_height_cm,
            mode="stryd",
            run_cp_w=run_cp_w,
            target_pct_rcp=target_pct_rcp,
            target_re=re_i,
            shoe_name=shoe_name,
            base_shoe_name=shoe_name,
            surface_name=surface_name,
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            wind_speed_kmh=wind_speed_kmh,
            wind_from_deg=wind_from_deg,
            home_altitude_m=home_altitude_m,
            trained_temp_c=trained_temp_c,
            trained_humidity_pct=trained_humidity_pct,
            strategy=strategy,
            apply_env_derating=apply_env_derating,
            post_bike_fatigue_pct=post_bike_fatigue_pct,
            split_interval_km=5.0,
        )
        si = sim_i["summary"]
        # Tiempo de referencia Palladino en llano puro (sin desnivel ni clima)
        v_flat = wkg_flat * re_i
        t_flat_s = (total_km * 1000.0) / max(0.5, v_flat)
        rows.append({
            "Target RE": f"{re_i:.2f}" + (" ⭐" if abs(d_re) < 1e-6 else ""),
            "Potencia (W)": f"{si['avg_power_w']:.0f} W",
            "W/kg": f"{si['w_per_kg']:.2f}",
            "% rCP": f"{si['pct_rcp']:.1f}%",
            "Tiempo en Llano (Palladino)": format_hms(t_flat_s),
            "Tiempo en ESTE Circuito": si["total_time_str"],
            "Ritmo Circuito (/km)": f"{si['avg_pace_str']} /km",
            "RE Circuito": f"{si['course_re']:.2f}",
        })
    return rows


# ═════════════════════════════════════════════════════════════════════
#  RUTAS DEMO DE RUNNING (21.1K MEDELLÍN & 10K CARTAGENA)
# ═════════════════════════════════════════════════════════════════════

def generate_demo_running_gpx(preset: str = "medellin_21k") -> Tuple[bytes, str]:
    """
    Genera archivos GPX realistas de prueba para Running:
      - 'medellin_21k': Media Maratón de Medellín (21.1 km, ~1495 msnm, +165 m desnivel ondulado)
      - 'cartagena_10k': 10K Costa Cartagena (10.0 km, nivel del mar ~4 m, plano rápido)
    """
    pts = []
    if preset == "cartagena_10k":
        name = "Demo 10K Rápido Cartagena (10.0 km)"
        total_m = 10000.0
        n_pts = 200
        lat0, lon0 = 10.4236, -75.5470
        for i in range(n_pts + 1):
            frac = i / n_pts
            if frac <= 0.5:
                d = frac * 2.0 * 5000.0
                lat = lat0 + (d / 111320.0) * 0.72
                lon = lon0 + (d / 109500.0) * 0.69
            else:
                d = (1.0 - frac) * 2.0 * 5000.0
                lat = lat0 + (d / 111320.0) * 0.72 + 0.0003
                lon = lon0 + (d / 109500.0) * 0.69 - 0.0002
            ele = 4.0 + 3.0 * math.sin(frac * 4.0 * math.pi) + 1.5 * math.sin(frac * 10.0 * math.pi)
            pts.append((lat, lon, max(2.0, ele)))
    else:
        name = "Demo Media Maratón Medellín (21.1 km · Ondulado)"
        total_m = 21097.5
        n_pts = 320
        lat0, lon0 = 6.2442, -75.5740
        r_lat = 0.032
        r_lon = 0.018
        for i in range(n_pts + 1):
            frac = i / n_pts
            ang = frac * 2.0 * math.pi
            lat = lat0 + r_lat * math.sin(ang) + 0.004 * math.sin(3.0 * ang)
            lon = lon0 + r_lon * (1.0 - math.cos(ang)) * 0.55 + 0.003 * math.cos(2.0 * ang)
            ele = (
                1475.0
                + 85.0 * math.sin(frac * math.pi) ** 1.4
                + 24.0 * math.sin(frac * 5.0 * math.pi)
                + 12.0 * math.sin(frac * 11.0 * math.pi)
            )
            pts.append((lat, lon, ele))

    def _hav(a, b):
        r = 6371000.0
        p1, p2 = math.radians(a[0]), math.radians(b[0])
        dp = math.radians(b[0] - a[0])
        dl = math.radians(b[1] - a[1])
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(min(1.0, math.sqrt(h)))

    raw_len = sum(_hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    scale = total_m / max(1.0, raw_len)
    p0 = pts[0]
    scaled = [(p0[0] + (p[0] - p0[0]) * scale, p0[1] + (p[1] - p0[1]) * scale, p[2]) for p in pts]

    trkpts = "\n".join(
        f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{ele:.1f}</ele></trkpt>'
        for lat, lon, ele in scaled
    )
    gpx = f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="AlphaX Race Split Running" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>{name}</name>
    <trkseg>
{trkpts}
    </trkseg>
  </trk>
</gpx>
"""
    return gpx.encode("utf-8"), f"demo_{preset}.gpx"
