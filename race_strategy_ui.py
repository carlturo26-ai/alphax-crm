"""
race_strategy_ui.py — Interfaz interactiva de Estrategias de Carrera en Ciclismo
(AlphaX Race Split — estilo Best Bike Split) para el CRM de Entrenadores.
"""

import base64
import gzip
import hashlib
import html
import json
import math
import re
import urllib.parse
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database import LactateTest, Member, RaceStrategyScenario, SessionLocal, init_db
from weather_service import deg_to_compass, fetch_race_weather
from interactive_course_charts import (
    render_interactive_pacing_zoom_widget,
    render_synced_elevation_planimetry_widget,
)
from cda_estimator import (
    estimate_cda_from_activity,
    estimate_cda_from_race_summary,
    parse_activity_file,
    parse_route_from_any_file,
    trim_activity_range,
)
from race_strategy_engine import (
    APPAREL_OPTIONS,
    BIKE_TYPES,
    DRIVETRAINS,
    HELMETS,
    HYDRATION_SETUP,
    RACE_PRESETS,
    ROAD_SURFACES,
    TIRES_CRR,
    WHEEL_SETS,
    WIND_DIRECTIONS,
    calc_air_density,
    estimate_equipment_specs,
    format_hms,
    format_time_delta,
    generate_demo_gpx,
    parse_gpx_bytes,
    simulate_race_strategy,
)


def _render_html(html_str: str):
    if hasattr(st, "html"):
        st.html(html_str)
    else:
        st.markdown(html_str, unsafe_allow_html=True)


def _compress_route_data(route_data: dict) -> str:
    """Comprime route_data (máx. ~240 segmentos) a gzip+base64 (~4 KB) para guardarlo en config_json."""
    if not route_data or not route_data.get("segments"):
        return ""
    segs = route_data["segments"]
    step = max(1, int(math.ceil(len(segs) / 240.0)))
    compact_segs = []
    for i in range(0, len(segs), step):
        chunk = segs[i : i + step]
        d0 = float(chunk[0]["dist_start_m"])
        d1 = float(chunk[-1]["dist_end_m"])
        l_m = max(5.0, d1 - d0)
        e0 = float(chunk[0]["ele_start"])
        e1 = float(chunk[-1]["ele_end"])
        grade = max(-0.22, min(0.25, (e1 - e0) / l_m))
        mid = chunk[len(chunk) // 2]
        compact_segs.append({
            "lat": round(float(mid["lat"]), 5),
            "lon": round(float(mid["lon"]), 5),
            "ele_m": round(0.5 * (e0 + e1), 1),
            "ele_start": round(e0, 1),
            "ele_end": round(e1, 1),
            "length_m": round(l_m, 1),
            "dist_start_m": round(d0, 1),
            "dist_end_m": round(d1, 1),
            "dist_mid_km": round((d0 + 0.5 * l_m) / 1000.0, 3),
            "grade": round(grade, 5),
            "bearing_deg": round(float(mid.get("bearing_deg", 0.0)), 1),
            "turn_deg": round(max(float(c.get("turn_deg", 0.0)) for c in chunk), 1),
            "lap": int(mid.get("lap", 1)),
        })
    payload = {
        "route_name": route_data.get("route_name", "Circuito"),
        "laps": int(route_data.get("laps", 1)),
        "total_distance_km": route_data.get("total_distance_km", 0.0),
        "elevation_gain_m": route_data.get("elevation_gain_m", 0.0),
        "elevation_loss_m": route_data.get("elevation_loss_m", 0.0),
        "min_ele_m": route_data.get("min_ele_m", 0.0),
        "max_ele_m": route_data.get("max_ele_m", 0.0),
        "avg_ele_m": route_data.get("avg_ele_m", 0.0),
        "max_grade_pct": route_data.get("max_grade_pct", 0.0),
        "min_grade_pct": route_data.get("min_grade_pct", 0.0),
        "segments": compact_segs,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")


def _decompress_route_data(b64_str: str) -> dict:
    """Descomprime route_data guardado en config_json['route_data_gz']."""
    if not b64_str:
        return None
    try:
        raw = gzip.decompress(base64.b64decode(b64_str.encode("ascii")))
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("segments"):
            return data
    except Exception:
        pass
    return None


def _reconstruct_route_from_saved_scenario(sc, cfg: dict, splits: list) -> dict:
    """
    Recupera route_data desde config_json['route_data_gz'] o, para escenarios antiguos ya guardados,
    reconstruye un perfil altimétrico/físico fiel directamente desde splits_json para correr 'Qué Pasa Si...'
    sin tener que volver a subir el archivo GPX/FIT.
    """
    if isinstance(cfg, dict) and cfg.get("route_data_gz"):
        rd = _decompress_route_data(cfg["route_data_gz"])
        if rd:
            return rd

    total_dist_km = max(1.0, float(sc.distance_km or 40.0))
    total_gain_m = max(0.0, float(sc.elevation_gain_m or 0.0))
    wind_deg_ref = float(cfg.get("wind_deg", 45.0)) if isinstance(cfg, dict) else 45.0

    # Parsear splits guardados (Tramo, Pend. %, Viento)
    parsed_bins = []
    if splits and isinstance(splits, list):
        prev_km = 0.0
        for sp in splits:
            tramo = str(sp.get("Tramo", ""))
            nums = re.findall(r"\d+(?:\.\d+)?", tramo)
            if len(nums) >= 2:
                k0, k1 = float(nums[0]), float(nums[1])
            else:
                k0 = prev_km
                k1 = prev_km + (total_dist_km / max(1, len(splits)))
            if k1 <= k0 + 0.05:
                k1 = k0 + max(0.5, total_dist_km / max(1, len(splits)))
            prev_km = k1
            g_pct = float(sp.get("Pend. %", 0.0) or 0.0)
            v_str = str(sp.get("Viento", ""))
            if "Contra" in v_str:
                bearing = wind_deg_ref % 360.0
            elif "Favor" in v_str:
                bearing = (wind_deg_ref + 180.0) % 360.0
            else:
                bearing = (wind_deg_ref + 90.0) % 360.0
            parsed_bins.append((k0, k1, max(-0.18, min(0.20, g_pct / 100.0)), bearing))

    if not parsed_bins:
        # Fallback mínimo por distancia y desnivel total
        avg_g = (total_gain_m / max(1000.0, total_dist_km * 1000.0))
        n_b = 20
        step_k = total_dist_km / n_b
        for i in range(n_b):
            g_i = avg_g * 2.0 if i < n_b // 2 else -avg_g * 2.0
            parsed_bins.append((i * step_k, (i + 1) * step_k, g_i, 0.0 if i < n_b // 2 else 180.0))

    # Subdividir en segmentos de ~500 m con trazado sintético limpio
    segments = []
    cum_m = 0.0
    cur_ele = 500.0
    lat0, lon0 = 6.15, -75.37
    elev_gain = 0.0
    elev_loss = 0.0
    for k0, k1, grade, bearing in parsed_bins:
        bin_len_m = max(100.0, (k1 - k0) * 1000.0)
        n_sub = max(1, min(12, int(round(bin_len_m / 500.0))))
        sub_l = bin_len_m / n_sub
        b_rad = math.radians(bearing)
        for _ in range(n_sub):
            e0 = cur_ele
            de = grade * sub_l
            e1 = max(0.0, e0 + de)
            cur_ele = e1
            if de > 0:
                elev_gain += de
            else:
                elev_loss += abs(de)
            d_start = cum_m
            cum_m += sub_l
            lat0 += (sub_l * math.cos(b_rad)) / 111320.0
            lon0 += (sub_l * math.sin(b_rad)) / (111320.0 * max(0.2, math.cos(math.radians(lat0))))
            segments.append({
                "lat": round(lat0, 5),
                "lon": round(lon0, 5),
                "ele_m": round(0.5 * (e0 + e1), 1),
                "ele_start": round(e0, 1),
                "ele_end": round(e1, 1),
                "length_m": round(sub_l, 1),
                "dist_start_m": round(d_start, 1),
                "dist_end_m": round(cum_m, 1),
                "dist_mid_km": round((d_start + 0.5 * sub_l) / 1000.0, 3),
                "grade": round(grade, 5),
                "bearing_deg": round(bearing, 1),
                "turn_deg": 0.0,
                "lap": 1,
            })

    eles = [sg["ele_m"] for sg in segments]
    grades_pct = [sg["grade"] * 100.0 for sg in segments]
    return {
        "route_name": sc.route_name or sc.scenario_name or "Plan Guardado",
        "laps": 1,
        "total_distance_km": round(cum_m / 1000.0, 2),
        "elevation_gain_m": round(total_gain_m if total_gain_m > 0 else elev_gain, 0),
        "elevation_loss_m": round(elev_loss, 0),
        "min_ele_m": round(min(eles), 0),
        "max_ele_m": round(max(eles), 0),
        "avg_ele_m": round(sum(eles) / len(eles), 0),
        "max_grade_pct": round(max(grades_pct), 1),
        "min_grade_pct": round(min(grades_pct), 1),
        "segments": segments,
        "reconstructed_from_splits": True,
    }


def _build_ctx_from_saved_scenario(sc, ath_defaults: dict) -> tuple:
    """Construye (route_sig, route_data, ctx) completos a partir de un RaceStrategyScenario guardado en DB."""
    cfg = _safe_parse_json(sc.config_json, {})
    if not isinstance(cfg, dict):
        cfg = {}
    splits = _safe_parse_json(sc.splits_json, [])
    if not isinstance(splits, list):
        splits = []

    route_data = _reconstruct_route_from_saved_scenario(sc, cfg, splits)
    route_sig = f"saved_sc_{sc.id}_{round(float(sc.distance_km or 0), 1)}_{len(route_data['segments'])}"

    bike_type = cfg.get("bike_type") if cfg.get("bike_type") in BIKE_TYPES else list(BIKE_TYPES.keys())[0]
    wheels = cfg.get("wheels") if cfg.get("wheels") in WHEEL_SETS else list(WHEEL_SETS.keys())[1]
    helmet = cfg.get("helmet") if cfg.get("helmet") in HELMETS else list(HELMETS.keys())[0]
    tires = cfg.get("tires") if cfg.get("tires") in TIRES_CRR else list(TIRES_CRR.keys())[1]
    drivetrain = cfg.get("drivetrain") if cfg.get("drivetrain") in DRIVETRAINS else list(DRIVETRAINS.keys())[1]
    apparel = cfg.get("apparel") if cfg.get("apparel") in APPAREL_OPTIONS else list(APPAREL_OPTIONS.keys())[0]
    hydration = cfg.get("hydration") if cfg.get("hydration") in HYDRATION_SETUP else list(HYDRATION_SETUP.keys())[0]
    surface = cfg.get("surface") if cfg.get("surface") in ROAD_SURFACES else list(ROAD_SURFACES.keys())[1]

    rider_weight = float(cfg.get("rider_weight_kg") or ath_defaults["weight_kg"])
    rider_height = float(cfg.get("rider_height_cm") or ath_defaults["height_cm"])
    saved_if = float(cfg.get("target_if") or sc.intensity_factor or 0.80)
    saved_if = max(0.45, min(1.12, saved_if))
    if cfg.get("ftp_w"):
        ftp_w = float(cfg["ftp_w"])
    elif sc.norm_power_w and saved_if > 0.4:
        ftp_w = round(float(sc.norm_power_w) / saved_if, 1)
    else:
        ftp_w = float(ath_defaults["ftp_w"])

    saved_cda = float(sc.cda) if (sc.cda and 0.14 <= float(sc.cda) <= 0.55) else None
    saved_bike_kg = None
    if sc.total_mass_kg and float(sc.total_mass_kg) - rider_weight >= 5.0:
        saved_bike_kg = round(float(sc.total_mass_kg) - rider_weight, 2)

    equipment = estimate_equipment_specs(
        bike_type=bike_type, wheels=wheels, helmet=helmet, tires=tires,
        drivetrain=drivetrain, apparel=apparel, hydration=hydration,
        surface=surface, rider_height_cm=rider_height,
        manual_cda=saved_cda, manual_bike_weight_kg=saved_bike_kg,
    )

    temp_c = float(cfg.get("temp_c") if cfg.get("temp_c") is not None else 24.0)
    humidity_pct = float(cfg.get("humidity_pct") if cfg.get("humidity_pct") is not None else 65.0)
    wind_kmh = float(cfg.get("wind_kmh") if cfg.get("wind_kmh") is not None else 8.0)
    wind_deg = float(cfg.get("wind_deg") if cfg.get("wind_deg") is not None else 45.0)

    strat_label = str(cfg.get("strategy") or "⚡ Óptimo AlphaX (Variable por Pendiente y Viento)")
    if "Constante" in strat_label:
        strategy_code = "constant"
    elif "Negative" in strat_label:
        strategy_code = "negative_split"
    else:
        strategy_code = "optimal"

    try:
        race_date = datetime.strptime(cfg["race_date"], "%Y-%m-%d").date() if cfg.get("race_date") else (date.today() + timedelta(days=3))
    except Exception:
        race_date = date.today() + timedelta(days=3)
    race_time_str = str(cfg.get("race_start_time") or "07:00")

    sim = _sim_fast(
        route_sig=route_sig, route_data=route_data, rider_weight_kg=rider_weight,
        ftp_w=ftp_w, target_if=saved_if, equipment=equipment,
        temp_c=temp_c, humidity_pct=humidity_pct, wind_kmh=wind_kmh, wind_deg=wind_deg,
        strategy=strategy_code, max_climb_pct=1.05, max_desc_kmh=68.0,
        split_step_km=5.0, hourly_weather=None,
    )
    ctx = {
        "sim": sim,
        "s": sim["summary"],
        "nut": sim["nutrition"],
        "equipment": equipment,
        "rider_weight": rider_weight,
        "rider_height": rider_height,
        "ftp_w": ftp_w,
        "target_if": saved_if,
        "temp_c": temp_c,
        "humidity_pct": humidity_pct,
        "wind_kmh": wind_kmh,
        "wind_deg": wind_deg,
        "strategy_code": strategy_code,
        "strat_label": strat_label,
        "max_climb_pct": 1.05,
        "max_desc_kmh": 68.0,
        "split_step_km": 5.0,
        "active_hourly_weather": None,
        "bike_type": bike_type,
        "wheels": wheels,
        "helmet": helmet,
        "tires": tires,
        "drivetrain": drivetrain,
        "apparel": apparel,
        "hydration": hydration,
        "surface": surface,
        "race_date": race_date,
        "race_time_str": race_time_str,
        "weather_data": {"source_label": cfg.get("weather_source", "Manual")},
        "weather_matches_route": False,
        "loaded_from_scenario_name": sc.scenario_name,
        "loaded_from_scenario_id": sc.id,
    }
    return route_sig, route_data, ctx


@st.cache_resource(show_spinner=False)
def _ensure_db_ready() -> bool:
    """Inicializa tablas una sola vez por proceso (evita latencia de DB en cada interacción)."""
    init_db()
    return True


@st.cache_data(ttl=60, show_spinner=False)
def _load_active_members_map() -> dict:
    """Carga lista de deportistas activos con caché de 60 s."""
    with SessionLocal() as session:
        members = session.query(Member).filter(Member.active == True).order_by(Member.name).all()
        if not members:
            members = session.query(Member).order_by(Member.name).all()
        return {f"{m.name} ({m.group or 'AlphaX'})": m.id for m in members}


@st.cache_data(max_entries=16, show_spinner=False)
def _cached_parse_route(file_hash: str, _file_bytes: bytes, filename: str, laps: int) -> dict:
    """Parsea archivos GPX/FIT/TCX una sola vez por archivo + vueltas."""
    return parse_route_from_any_file(_file_bytes, filename, laps=int(laps))


@st.cache_data(max_entries=8, show_spinner=False)
def _cached_parse_activity(file_hash: str, _file_bytes: bytes, filename: str) -> dict:
    """Parsea archivos de actividad para calibrar CdA una sola vez por archivo."""
    return parse_activity_file(_file_bytes, filename)


def _hourly_weather_signature(hourly_weather) -> str:
    if not hourly_weather:
        return ""
    return "|".join(
        f"{h.get('hour', i)}:{round(float(h.get('temp_c', 0)), 1)}:{round(float(h.get('wind_kmh', 0)), 1)}:{round(float(h.get('wind_deg', 0)), 0)}"
        for i, h in enumerate(hourly_weather[:12])
    )


@st.cache_data(max_entries=256, show_spinner=False)
def _cached_simulate_race(
    route_sig: str,
    _route_data: dict,
    rider_weight_kg: float,
    ftp_w: float,
    target_if: float,
    cda: float,
    crr: float,
    bike_kg: float,
    efficiency: float,
    sail_factor: float,
    temp_c: float,
    humidity_pct: float,
    wind_kmh: float,
    wind_deg: float,
    strategy: str,
    max_climb_pct: float,
    max_desc_kmh: float,
    split_step_km: float,
    hourly_sig: str,
    _hourly_weather,
) -> dict:
    """Ejecuta y cachea simulaciones físicas por firma exacta de parámetros (0 ms en caché)."""
    eq = {
        "cda": float(cda),
        "crr": float(crr),
        "bike_and_gear_kg": float(bike_kg),
        "efficiency": float(efficiency),
        "sail_factor": float(sail_factor),
    }
    return simulate_race_strategy(
        route_data=_route_data,
        rider_weight_kg=float(rider_weight_kg),
        ftp_w=float(ftp_w),
        target_if=float(target_if),
        equipment=eq,
        temp_c=float(temp_c),
        rel_humidity_pct=float(humidity_pct),
        wind_speed_kmh=float(wind_kmh),
        wind_from_deg=float(wind_deg),
        strategy=strategy,
        max_climb_pct_ftp=float(max_climb_pct),
        max_descent_kmh=float(max_desc_kmh),
        split_interval_km=float(split_step_km),
        hourly_weather=_hourly_weather,
    )


def _sim_fast(
    route_sig: str,
    route_data: dict,
    rider_weight_kg: float,
    ftp_w: float,
    target_if: float,
    equipment: dict,
    temp_c: float,
    humidity_pct: float,
    wind_kmh: float,
    wind_deg: float,
    strategy: str,
    max_climb_pct: float,
    max_desc_kmh: float,
    split_step_km: float = 5.0,
    hourly_weather=None,
) -> dict:
    return _cached_simulate_race(
        route_sig=route_sig,
        _route_data=route_data,
        rider_weight_kg=round(float(rider_weight_kg), 2),
        ftp_w=round(float(ftp_w), 1),
        target_if=round(float(target_if), 4),
        cda=round(float(equipment["cda"]), 4),
        crr=round(float(equipment["crr"]), 5),
        bike_kg=round(float(equipment["bike_and_gear_kg"]), 2),
        efficiency=round(float(equipment["efficiency"]), 4),
        sail_factor=round(float(equipment.get("sail_factor", 0.03)), 3),
        temp_c=round(float(temp_c), 1),
        humidity_pct=round(float(humidity_pct), 0),
        wind_kmh=round(float(wind_kmh), 1),
        wind_deg=round(float(wind_deg), 0),
        strategy=strategy,
        max_climb_pct=round(float(max_climb_pct), 3),
        max_desc_kmh=round(float(max_desc_kmh), 1),
        split_step_km=round(float(split_step_km), 1),
        hourly_sig=_hourly_weather_signature(hourly_weather),
        _hourly_weather=hourly_weather,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _load_athlete_defaults(member_id: int) -> dict:
    """Obtiene peso, estatura y FTP/LT2 reales del deportista desde la base de datos."""
    out = {
        "name": "Deportista",
        "weight_kg": 70.0,
        "weight_src": "estimado",
        "height_cm": 175.0,
        "height_src": "estimado",
        "ftp_w": 250.0,
        "ftp_src": "estimado",
        "lt1_w": None,
        "lt2_w": None,
        "calibrated_cda": None,
        "calibrated_cda_bike": None,
        "calibrated_cda_date": None,
    }
    if not member_id:
        return out

    try:
        with SessionLocal() as session:
            m = session.query(Member).filter(Member.id == member_id).first()
            if m:
                out["name"] = m.name
                if m.weight_kg and 35.0 <= m.weight_kg <= 160.0:
                    out["weight_kg"] = float(m.weight_kg)
                    out["weight_src"] = "perfil CRM"
                if m.height_cm and 130.0 <= m.height_cm <= 220.0:
                    out["height_cm"] = float(m.height_cm)
                    out["height_src"] = "perfil CRM"
                if getattr(m, "calibrated_cda", None) and 0.14 <= m.calibrated_cda <= 0.55:
                    out["calibrated_cda"] = float(m.calibrated_cda)
                    out["calibrated_cda_bike"] = getattr(m, "calibrated_cda_bike", None)
                    out["calibrated_cda_date"] = getattr(m, "calibrated_cda_date", None)

            # Última prueba de lactato (preferir ciclismo si existe)
            lacs = (
                session.query(LactateTest)
                .filter(LactateTest.member_id == member_id)
                .order_by(LactateTest.date.desc(), LactateTest.id.desc())
                .limit(5)
                .all()
            )
            chosen_lac = None
            for l in lacs:
                if "cicl" in str(l.sport or "").lower():
                    chosen_lac = l
                    break
            if not chosen_lac and lacs:
                chosen_lac = lacs[0]

            if chosen_lac:
                if out["weight_src"] == "estimado" and chosen_lac.weight and 35.0 <= chosen_lac.weight <= 160.0:
                    out["weight_kg"] = float(chosen_lac.weight)
                    out["weight_src"] = f"test lactato ({chosen_lac.date.strftime('%d/%m/%Y') if chosen_lac.date else 'último'})"
                if chosen_lac.ftp and 80.0 <= chosen_lac.ftp <= 550.0:
                    out["ftp_w"] = float(chosen_lac.ftp)
                    out["ftp_src"] = f"FTP test ({chosen_lac.date.strftime('%d/%m/%Y') if chosen_lac.date else 'último'})"
                elif chosen_lac.lt2_power and 80.0 <= chosen_lac.lt2_power <= 550.0:
                    out["ftp_w"] = float(chosen_lac.lt2_power)
                    out["ftp_src"] = f"LT2 lactato ({chosen_lac.date.strftime('%d/%m/%Y') if chosen_lac.date else 'último'})"
                out["lt1_w"] = chosen_lac.lt1_power
                out["lt2_w"] = chosen_lac.lt2_power
    except Exception:
        pass
    return out


def _build_elevation_power_chart(segments: list, ftp_w: float) -> go.Figure:
    """Gráfica principal estilo Best Bike Split: Altimetría + Potencia Objetivo + Velocidad."""
    df = pd.DataFrame(segments)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Perfil altimétrico relleno en el fondo (eje secundario o normalizado visualmente — usamos eje primario para Potencia y secundario para Elevación)
    fig.add_trace(
        go.Scatter(
            x=df["dist_km"],
            y=df["ele_m"],
            name="Altimetría (m)",
            mode="lines",
            fill="tozeroy",
            line=dict(color="rgba(148, 163, 184, 0.55)", width=1.5),
            fillcolor="rgba(148, 163, 184, 0.16)",
            hovertemplate="Km %{x:.1f}<br>Altitud: %{y:.0f} m<extra></extra>",
        ),
        secondary_y=True,
    )

    # Curva de potencia objetivo (W) — Estándar AlphaX: AMARILLO para Potencia
    fig.add_trace(
        go.Scatter(
            x=df["dist_km"],
            y=df["power_w"],
            name="Potencia Objetivo (W)",
            mode="lines",
            line=dict(color="#FFD700", width=2.8),
            hovertemplate="Km %{x:.1f}<br>Potencia: %{y:.0f} W<extra></extra>",
        ),
        secondary_y=False,
    )

    fig.add_hline(
        y=ftp_w,
        line_dash="dot",
        line_color="#FFA726",
        annotation_text=f"FTP ({ftp_w:.0f} W)",
        annotation_position="top right",
        annotation_font=dict(color="#FFB74D", size=11),
        secondary_y=False,
    )

    fig.update_layout(
        title=dict(
            text="📈 Plan de Potencia Objetivo vs Perfil Altimétrico del Circuito",
            x=0.5,
            xanchor="center",
            font=dict(color="#FFD700", size=15),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0"),
        height=390,
        margin=dict(l=15, r=15, t=55, b=15),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Distancia (km)", gridcolor="rgba(255,255,255,0.06)")
    fig.update_yaxes(
        title_text="Potencia (W)",
        gridcolor="rgba(255,255,255,0.08)",
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="Elevación (m)",
        showgrid=False,
        secondary_y=True,
    )
    return fig


def _build_speed_wind_chart(segments: list) -> go.Figure:
    """Gráfica de Velocidad Estimada (km/h) y Viento Relativo (km/h) a lo largo de la ruta."""
    df = pd.DataFrame(segments)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Curva de velocidad estimada (km/h) — Estándar AlphaX: VERDE para Velocidad
    fig.add_trace(
        go.Scatter(
            x=df["dist_km"],
            y=df["speed_kmh"],
            name="Velocidad Estimada (km/h)",
            mode="lines",
            line=dict(color="#00FF88", width=2.6),
            hovertemplate="Km %{x:.1f}<br>Velocidad: %{y:.1f} km/h<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(
            x=df["dist_km"],
            y=df["headwind_kmh"],
            name="Viento (+Contra / -Favor km/h)",
            marker_color=["rgba(255, 75, 75, 0.45)" if v > 0 else "rgba(56, 189, 248, 0.45)" for v in df["headwind_kmh"]],
            hovertemplate="Km %{x:.1f}<br>Viento relativo: %{y:+.1f} km/h<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=dict(
            text="🚀 Velocidad Estimada y Viento Relativo por Kilómetro",
            x=0.5,
            xanchor="center",
            font=dict(color="#00FF88", size=14),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0"),
        height=290,
        margin=dict(l=15, r=15, t=48, b=15),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Distancia (km)", gridcolor="rgba(255,255,255,0.06)")
    fig.update_yaxes(title_text="Velocidad (km/h)", gridcolor="rgba(255,255,255,0.08)", secondary_y=False)
    fig.update_yaxes(title_text="Viento (+Contra / -Favor)", showgrid=False, secondary_y=True)
    return fig


_GRADE_HEATMAP_SCALE = [
    [0.00, "#00B0FF"],  # Descenso veloz (<= -6%)
    [0.25, "#00E676"],  # Bajada rápida (~ -2.5%)
    [0.43, "#94A3B8"],  # Llano (~ 0%)
    [0.62, "#FFD600"],  # Falso llano / repecho (~ +2.7%)
    [0.80, "#FF7A00"],  # Subida moderada (~ +5.2%)
    [1.00, "#FF1744"],  # Subida fuerte / muro (>= +8%)
]


def _classify_grade_heat(grade_pct: float) -> tuple:
    """Clasifica una pendiente (%) en cubeta de mapa de calor: (id, etiqueta, color_hex, fill_rgba)."""
    if grade_pct >= 8.0:
        return ("wall", "🔥 Muro (≥8%)", "#B71C1C", "rgba(183, 28, 28, 0.85)")
    if grade_pct >= 5.0:
        return ("steep_up", "🔴 Subida Fuerte (5–8%)", "#FF2E43", "rgba(255, 46, 67, 0.78)")
    if grade_pct >= 2.5:
        return ("mod_up", "🟠 Subida Moderada (2.5–5%)", "#FF8A00", "rgba(255, 138, 0, 0.74)")
    if grade_pct >= 1.0:
        return ("mild_up", "🟡 Repecho (+1–2.5%)", "#FFD600", "rgba(255, 214, 0, 0.62)")
    if grade_pct > -1.0:
        return ("flat", "⚪ Llano (-1% a +1%)", "#64748B", "rgba(100, 116, 139, 0.42)")
    if grade_pct > -3.5:
        return ("fast_down", "🟢 Bajada Rápida (-1% a -3.5%)", "#00E676", "rgba(0, 230, 118, 0.68)")
    return ("steep_down", "🔵 Descenso Veloz (≤-3.5%)", "#00B0FF", "rgba(0, 176, 255, 0.78)")


def _build_course_elevation_heatmap(route_data: dict) -> tuple:
    """
    Perfil altimétrico con mapa de calor de pendientes (%):
    Rojo/Naranja = mayor desnivel positivo (subidas exigentes),
    Verde/Azul = bajadas rápidas donde se desarrolla máxima velocidad.
    """
    segs = route_data.get("segments") or []
    if not segs:
        return go.Figure(), {}

    # Estadísticas de distribución de terreno reales sobre todos los segmentos
    climb_m = flat_m = descent_m = steep_up_m = fast_down_m = 0.0
    for sg in segs:
        g_pct = float(sg.get("grade", 0.0)) * 100.0
        l_m = float(sg.get("length_m", 0.0))
        if g_pct >= 1.0:
            climb_m += l_m
            if g_pct >= 5.0:
                steep_up_m += l_m
        elif g_pct <= -1.0:
            descent_m += l_m
            if g_pct <= -3.0:
                fast_down_m += l_m
        else:
            flat_m += l_m

    terrain_stats = {
        "climb_km": round(climb_m / 1000.0, 1),
        "flat_km": round(flat_m / 1000.0, 1),
        "descent_km": round(descent_m / 1000.0, 1),
        "steep_up_km": round(steep_up_m / 1000.0, 1),
        "fast_down_km": round(fast_down_m / 1000.0, 1),
        "max_grade_pct": route_data.get("max_grade_pct", 0.0),
        "min_grade_pct": route_data.get("min_grade_pct", 0.0),
    }

    # Agrupar segmentos en ~180 bloques uniformes para una visualización limpia y veloz
    target_bins = 180
    group_sz = max(1, int(math.ceil(len(segs) / float(target_bins))))
    bins = []
    for i in range(0, len(segs), group_sz):
        chunk = segs[i : i + group_sz]
        d0 = float(chunk[0]["dist_start_m"]) / 1000.0
        d1 = float(chunk[-1]["dist_end_m"]) / 1000.0
        e0 = float(chunk[0]["ele_start"])
        e1 = float(chunk[-1]["ele_end"])
        tot_l = sum(float(c["length_m"]) for c in chunk)
        g_pct = ((e1 - e0) / max(1.0, tot_l)) * 100.0
        b_id, b_lbl, b_hex, b_rgba = _classify_grade_heat(g_pct)
        bins.append({
            "d0": d0, "d1": d1, "dm": 0.5 * (d0 + d1),
            "e0": e0, "e1": e1, "em": 0.5 * (e0 + e1),
            "grade_pct": round(g_pct, 1),
            "b_id": b_id, "b_lbl": b_lbl, "b_hex": b_hex, "b_rgba": b_rgba,
        })

    all_eles = [b["e0"] for b in bins] + [b["e1"] for b in bins]
    min_e = min(all_eles)
    max_e = max(all_eles)
    span_e = max(25.0, max_e - min_e)
    y_floor = min_e - span_e * 0.12

    # Unir bloques consecutivos del mismo color en polígonos continuos
    runs = []
    for b in bins:
        if runs and runs[-1]["b_id"] == b["b_id"]:
            runs[-1]["items"].append(b)
        else:
            runs.append({"b_id": b["b_id"], "b_rgba": b["b_rgba"], "b_hex": b["b_hex"], "items": [b]})

    fig = go.Figure()
    for run in runs:
        its = run["items"]
        xs = [its[0]["d0"]]
        ys = [its[0]["e0"]]
        for it in its:
            xs.append(it["d1"])
            ys.append(it["e1"])
        # Cerrar el polígono hacia la base y_floor
        poly_x = xs + [xs[-1], xs[0], xs[0]]
        poly_y = ys + [y_floor, y_floor, ys[0]]
        fig.add_trace(
            go.Scatter(
                x=poly_x,
                y=poly_y,
                mode="lines",
                fill="toself",
                fillcolor=run["b_rgba"],
                line=dict(color=run["b_hex"], width=0.5),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    # Línea superior continua + hover interactivo detallado con color por pendiente
    line_x = [b["dm"] for b in bins]
    line_y = [b["em"] for b in bins]
    hover_texts = [
        f"<b>Km {b['dm']:.1f}</b><br>Altitud: <b>{b['em']:.0f} m</b><br>Pendiente: <b>{b['grade_pct']:+.1f}%</b><br>{b['b_lbl']}"
        for b in bins
    ]
    fig.add_trace(
        go.Scatter(
            x=line_x,
            y=line_y,
            mode="lines+markers",
            line=dict(color="rgba(255,255,255,0.75)", width=1.6),
            marker=dict(
                size=4,
                color=[b["grade_pct"] for b in bins],
                colorscale=_GRADE_HEATMAP_SCALE,
                cmin=-6.0,
                cmax=8.0,
                showscale=False,
            ),
            text=hover_texts,
            hoverinfo="text",
            name="Perfil del Circuito",
            showlegend=False,
        )
    )

    fig.update_layout(
        title=dict(
            text="⛰️ Perfil Altimétrico — Mapa de Calor de Desniveles (%)",
            x=0.5,
            xanchor="center",
            font=dict(color="#FFFFFF", size=14),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0", size=11),
        height=330,
        margin=dict(l=12, r=12, t=42, b=14),
        hovermode="x",
        xaxis=dict(title="Distancia (km)", gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(
            title="Elevación (m)",
            range=[y_floor, max_e + span_e * 0.12],
            gridcolor="rgba(255,255,255,0.06)",
        ),
    )
    return fig, terrain_stats


def _build_course_planimetry_heatmap(route_data: dict) -> go.Figure:
    """
    Planimetría GPS 2D (vista desde arriba Lat/Lon) pintada como mapa de calor de pendientes:
    Rojo/Naranja = subidas duras, Verde/Azul = bajadas donde se va más rápido.
    """
    segs = route_data.get("segments") or []
    lap1 = [s for s in segs if s.get("lap", 1) == 1] or segs
    if not lap1:
        return go.Figure()

    lons = [float(s["lon"]) for s in lap1]
    lats = [float(s["lat"]) for s in lap1]
    grades_pct = [round(float(s.get("grade", 0.0)) * 100.0, 1) for s in lap1]
    eles = [float(s.get("ele_m", 0.0)) for s in lap1]
    dists = [float(s.get("dist_mid_km", 0.0)) for s in lap1]

    hover_txt = [
        f"<b>Km {d:.1f}</b><br>Altitud: <b>{e:.0f} m</b><br>Pendiente: <b>{g:+.1f}%</b><br>{_classify_grade_heat(g)[1]}"
        for d, e, g in zip(dists, eles, grades_pct)
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=lons,
            y=lats,
            mode="markers+lines",
            line=dict(color="rgba(255,255,255,0.16)", width=2),
            marker=dict(
                size=6.5,
                color=grades_pct,
                colorscale=_GRADE_HEATMAP_SCALE,
                cmin=-6.0,
                cmax=8.0,
                colorbar=dict(
                    title=dict(text="Pend. %", font=dict(size=10)),
                    thickness=11,
                    ticksuffix="%",
                ),
                showscale=True,
            ),
            text=hover_txt,
            hoverinfo="text",
            name="Planimetría",
            showlegend=False,
        )
    )

    # Detectar si es circuito cerrado (salida cerca de meta) o punto a punto
    d_start_end = math.hypot((lats[-1] - lats[0]) * 111.0, (lons[-1] - lons[0]) * 111.0 * math.cos(math.radians(lats[0])))
    start_lbl = "SALIDA / META" if d_start_end < 0.35 else "SALIDA"
    fig.add_trace(
        go.Scatter(
            x=[lons[0]],
            y=[lats[0]],
            mode="markers+text",
            marker=dict(size=13, color="#00FF88", symbol="star", line=dict(width=1.5, color="#000")),
            text=[start_lbl],
            textposition="top center",
            textfont=dict(color="#00FF88", size=10),
            showlegend=False,
        )
    )
    if d_start_end >= 0.35:
        fig.add_trace(
            go.Scatter(
                x=[lons[-1]],
                y=[lats[-1]],
                mode="markers+text",
                marker=dict(size=11, color="#FFD700", symbol="diamond", line=dict(width=1.5, color="#000")),
                text=["META"],
                textposition="bottom center",
                textfont=dict(color="#FFD700", size=10),
                showlegend=False,
            )
        )

    fig.update_layout(
        title=dict(
            text="🗺️ Planimetría GPS (Mapa de Calor de Pendiente)",
            x=0.5,
            xanchor="center",
            font=dict(color="#00EEFF", size=13.5),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0", size=11),
        height=330,
        margin=dict(l=8, r=8, t=42, b=10),
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        showlegend=False,
    )
    return fig


@st.cache_data(max_entries=16, show_spinner=False)
def _cached_course_heatmaps(route_sig: str, _route_data: dict) -> tuple:
    """Construye y cachea la altimetría y planimetría con mapa de calor de pendientes una sola vez por circuito."""
    fig_e, stats_t = _build_course_elevation_heatmap(_route_data)
    fig_p = _build_course_planimetry_heatmap(_route_data)
    return fig_e, stats_t, fig_p


def _build_gps_map_chart(
    segments: list,
    color_mode: str = "grade",
    wind_from_deg: float = 0.0,
    wind_kmh: float = 0.0,
    show_wind_arrows: bool = True,
) -> go.Figure:
    """
    Mapa 2D del circuito GPX coloreado por mapa de calor de desnivel, viento relativo o potencia objetivo,
    con flechas vectoriales de dirección del viento real y velocidad del viento relativo sobre el recorrido.
    """
    # Usar solo la primera vuelta para que el trazo sea nítido en circuitos multi-vuelta
    lap1 = [s for s in segments if s.get("lap", 1) == 1] or segments
    df = pd.DataFrame(lap1)

    extra_marker_kwargs = {}
    if color_mode == "grade":
        c_vals = df["grade_pct"]
        c_title = "Pendiente (%)"
        c_scale = _GRADE_HEATMAP_SCALE
        extra_marker_kwargs = {"cmin": -6.0, "cmax": 8.0}
        hover_txt = [
            f"Km {r['dist_km']:.1f}<br>Pendiente: <b>{r['grade_pct']:+.1f}%</b> ({_classify_grade_heat(r['grade_pct'])[1]})<br>Altitud: {r['ele_m']:.0f} m<br>Vel: {r['speed_kmh']:.1f} km/h · Pot: {r['power_w']:.0f} W<br>Viento rel.: {r['headwind_kmh']:+.1f} km/h"
            for _, r in df.iterrows()
        ]
    elif color_mode == "power":
        c_vals = df["power_w"]
        c_title = "Potencia (W)"
        # Escala en tonos amarillos/dorados (Estándar AlphaX: Amarillo = Potencia)
        c_scale = [[0.0, "#4A3800"], [0.35, "#B8860B"], [0.7, "#FFD700"], [1.0, "#FFF59D"]]
        hover_txt = [
            f"Km {r['dist_km']:.1f}<br>Potencia: <b>{r['power_w']:.0f} W</b><br>Pend: {r['grade_pct']:+.1f}% · Vel: {r['speed_kmh']:.1f} km/h<br>Viento rel.: {r['headwind_kmh']:+.1f} km/h"
            for _, r in df.iterrows()
        ]
    else:
        c_vals = df["headwind_kmh"]
        c_title = "Viento Rel. (+Contra/-Favor)"
        c_scale = [[0.0, "#00FF88"], [0.5, "#38BDF8"], [1.0, "#FF4B4B"]]
        hover_txt = [
            f"Km {r['dist_km']:.1f}<br>Viento rel.: <b>{r['headwind_kmh']:+.1f} km/h</b> (Lat: {r.get('crosswind_kmh', 0):.1f} km/h)<br>Viento real: {r.get('wind_kmh', wind_kmh):.0f} km/h desde {deg_to_compass(r.get('wind_from_deg', wind_from_deg))}<br>Potencia: {r['power_w']:.0f} W · Vel: {r['speed_kmh']:.1f} km/h"
            for _, r in df.iterrows()
        ]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["lon"],
            y=df["lat"],
            mode="markers+lines",
            line=dict(color="rgba(255,255,255,0.18)", width=2),
            marker=dict(
                size=6,
                color=c_vals,
                colorscale=c_scale,
                colorbar=dict(title=dict(text=c_title, font=dict(size=10)), thickness=12),
                showscale=True,
                **extra_marker_kwargs,
            ),
            text=hover_txt,
            hoverinfo="text",
            name="Circuito GPX",
        )
    )

    # ── FLECHAS VECTORIALES DE DIRECCIÓN DEL VIENTO + VELOCIDAD RELATIVA ──
    has_any_wind = (float(wind_kmh or 0.0) > 0.5) or (
        "wind_kmh" in df.columns and float(df["wind_kmh"].max()) > 0.5
    )
    if show_wind_arrows and has_any_wind and len(df) >= 4:
        lon_span = max(1e-4, float(df["lon"].max() - df["lon"].min()))
        lat_span = max(1e-4, float(df["lat"].max() - df["lat"].min()))
        base_span = max(lon_span, lat_span)
        arrow_len = base_span * 0.085

        n_pts = len(df)
        n_arrows = min(14, max(6, n_pts // 12))
        step = max(1, (n_pts - 1) // n_arrows)
        sample_idxs = list(range(step // 2, n_pts, step))[:n_arrows]

        arrow_hover_x, arrow_hover_y, arrow_hover_txt = [], [], []
        for k_i, idx in enumerate(sample_idxs):
            row = df.iloc[idx]
            seg_w_spd = float(row["wind_kmh"]) if "wind_kmh" in df.columns else float(wind_kmh)
            seg_w_deg = float(row["wind_from_deg"]) if "wind_from_deg" in df.columns else float(wind_from_deg)
            if seg_w_spd <= 0.4:
                continue

            hw = float(row["headwind_kmh"])
            cw = float(row.get("crosswind_kmh", 0.0))
            # Convención meteorológica: el viento viene DESDE seg_w_deg y sopla HACIA (seg_w_deg + 180)°
            blow_to_rad = math.radians((seg_w_deg + 180.0) % 360.0)
            ux = math.sin(blow_to_rad)
            uy = math.cos(blow_to_rad)

            # Escala sutil de longitud según intensidad del viento local
            spd_scale = max(0.75, min(1.35, seg_w_spd / max(8.0, float(wind_kmh or 12.0))))
            dx = ux * arrow_len * spd_scale
            dy = uy * arrow_len * spd_scale

            cx = float(row["lon"])
            cy = float(row["lat"])
            tail_x = cx - 0.48 * dx
            tail_y = cy - 0.48 * dy
            tip_x = cx + 0.52 * dx
            tip_y = cy + 0.52 * dy

            if hw >= 3.0:
                arr_col = "#FF4B4B"
                rel_tag = "Contra"
            elif hw <= -3.0:
                arr_col = "#00FF88"
                rel_tag = "Favor"
            else:
                arr_col = "#38BDF8"
                rel_tag = "Cruzado"

            # Flecha vectorial real en coordenadas de datos (se escala con el zoom)
            fig.add_annotation(
                x=tip_x,
                y=tip_y,
                ax=tail_x,
                ay=tail_y,
                xref="x",
                yref="y",
                axref="x",
                ayref="y",
                showarrow=True,
                arrowhead=2,
                arrowsize=1.25,
                arrowwidth=2.3,
                arrowcolor=arr_col,
                opacity=0.92,
            )

            # Etiqueta compacta con velocidad del viento relativo (1 de cada 2 flechas para evitar solapamientos)
            if k_i % 2 == 0 or len(sample_idxs) <= 7:
                lbl_x = cx + 0.72 * dx
                lbl_y = cy + 0.72 * dy
                fig.add_annotation(
                    x=lbl_x,
                    y=lbl_y,
                    xref="x",
                    yref="y",
                    text=f"<b>{hw:+.0f} km/h</b>",
                    showarrow=False,
                    font=dict(color=arr_col, size=9.5),
                    bgcolor="rgba(10, 14, 24, 0.78)",
                    bordercolor=arr_col,
                    borderwidth=1,
                    borderpad=2,
                )

            arrow_hover_x.append(cx)
            arrow_hover_y.append(cy)
            arrow_hover_txt.append(
                f"<b>🌬️ Viento en Km {row['dist_km']:.1f}</b><br>"
                f"Viento real: <b>{seg_w_spd:.0f} km/h</b> desde {deg_to_compass(seg_w_deg)} ({seg_w_deg:.0f}°)<br>"
                f"Viento relativo: <b>{hw:+.1f} km/h</b> ({rel_tag})<br>"
                f"Viento lateral: <b>{cw:.1f} km/h</b>"
            )

        if arrow_hover_x:
            fig.add_trace(
                go.Scatter(
                    x=arrow_hover_x,
                    y=arrow_hover_y,
                    mode="markers",
                    marker=dict(size=10, color="rgba(0,0,0,0)"),
                    text=arrow_hover_txt,
                    hoverinfo="text",
                    showlegend=False,
                )
            )

    # Punto de salida
    fig.add_trace(
        go.Scatter(
            x=[df["lon"].iloc[0]],
            y=[df["lat"].iloc[0]],
            mode="markers+text",
            marker=dict(size=12, color="#00FF88", symbol="star", line=dict(width=1.5, color="#000")),
            text=["SALIDA"],
            textposition="top center",
            textfont=dict(color="#00FF88", size=10),
            showlegend=False,
        )
    )

    wind_note = (
        f" · Viento {wind_kmh:.0f} km/h desde {deg_to_compass(wind_from_deg)} ({wind_from_deg:.0f}°)"
        if wind_kmh > 0
        else " · Sin viento"
    )
    fig.update_layout(
        title=dict(
            text=f"🗺️ Trazado GPS del Circuito{wind_note}",
            x=0.5,
            xanchor="center",
            font=dict(color="#00EEFF", size=13),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0"),
        height=395,
        margin=dict(l=12, r=12, t=46, b=14),
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        showlegend=False,
    )
    return fig


def _build_resistance_donut(resistance: dict) -> go.Figure:
    """Gráfica de dona del reparto de energía entre Aerodinámica (Rojo), Gravedad y Rodamiento."""
    labels = ["Aerodinámica (CdA)", "Gravedad (Subidas)", "Rodamiento (Crr)"]
    values = [resistance["aero_pct"], resistance["gravity_pct"], resistance["rolling_pct"]]
    colors = ["#FF3B30", "#FF9100", "#38BDF8"]

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.58,
                marker=dict(colors=colors, line=dict(color="#0E121C", width=2)),
                textinfo="label+percent",
                textfont=dict(color="#FFFFFF", size=11),
            )
        ]
    )
    fig.update_layout(
        title=dict(
            text="⚖️ ¿Dónde se va tu energía?",
            x=0.5,
            xanchor="center",
            font=dict(color="#00EEFF", size=13),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0"),
        height=270,
        margin=dict(l=10, r=10, t=42, b=10),
        showlegend=False,
    )
    return fig


def _safe_parse_json(raw_str, default):
    if not raw_str:
        return default
    try:
        val = json.loads(raw_str)
        return val if val is not None else default
    except Exception:
        return default


def _estimate_fallback_nutrition(total_time_s: float, temp_c: float = 24.0, humidity_pct: float = 65.0) -> dict:
    """Calcula un plan nutricional e hidratación fisiológico si un escenario antiguo no lo guardó."""
    duration_h = max(0.25, float(total_time_s or 0.0) / 3600.0)
    if duration_h < 1.1:
        cho_g_h = 45
    elif duration_h < 2.5:
        cho_g_h = 70
    elif duration_h < 4.5:
        cho_g_h = 85
    else:
        cho_g_h = 90
    t_val = float(temp_c if temp_c is not None else 24.0)
    h_val = float(humidity_pct if humidity_pct is not None else 65.0)
    heat_bonus = max(0.0, (t_val - 18.0) * 28.0) + max(0.0, (h_val - 60.0) * 3.0)
    fluid_ml_h = int(round(min(1200.0, max(500.0, 550.0 + heat_bonus)) / 50.0) * 50)
    sodium_mg_h = int(round(min(1100.0, max(450.0, 500.0 + (t_val - 18.0) * 25.0)) / 50.0) * 50)
    return {
        "cho_g_per_h": cho_g_h,
        "cho_total_g": int(round(cho_g_h * duration_h)),
        "fluid_ml_per_h": fluid_ml_h,
        "fluid_total_l": round((fluid_ml_h * duration_h) / 1000.0, 1),
        "sodium_mg_per_h": sodium_mg_h,
        "sodium_total_mg": int(round(sodium_mg_h * duration_h)),
    }


def _build_saved_splits_pacing_chart(splits_list: list, scenario_name: str = "", is_running: bool = False) -> go.Figure:
    """Gráfica dual de Potencia Objetivo (W — Amarillo) y Velocidad/Ritmo (Verde) desde splits guardados."""
    tramos = [str(sp.get("Tramo", f"T{i+1}")) for i, sp in enumerate(splits_list)]

    if is_running:
        from running_strategy_engine import format_pace as _fp, parse_time_str_to_sec as _pts
        watts = [float(sp.get("Potencia Stryd (W)", sp.get("Potencia Obj. (W)", 0)) or 0) for sp in splits_list]
        paces_min = []
        pace_lbls = []
        for sp in splits_list:
            p_raw = sp.get("Ritmo Obj. (/km)", "")
            p_s = _pts(p_raw)
            if p_s and p_s > 0:
                paces_min.append(p_s / 60.0)
                pace_lbls.append(_fp(p_s))
            else:
                paces_min.append(None)
                pace_lbls.append("—")

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        if any(w > 0 for w in watts):
            fig.add_trace(
                go.Bar(
                    x=tramos,
                    y=watts,
                    name="⚡ Potencia Stryd (W)",
                    marker=dict(color="#FFD700", line=dict(color="#FFA000", width=0.8)),
                    opacity=0.82,
                    text=[f"{int(round(w))}W" for w in watts] if len(tramos) <= 18 else None,
                    textposition="outside",
                    textfont=dict(color="#FFD700", size=10),
                    hovertemplate="<b>%{x}</b><br>Potencia Stryd: <b>%{y:.0f} W</b><extra></extra>",
                ),
                secondary_y=False,
            )
        valid_p = [p for p in paces_min if p is not None]
        if valid_p:
            fig.add_trace(
                go.Scatter(
                    x=tramos,
                    y=paces_min,
                    name="🏃‍♂️ Ritmo Obj. (min/km)",
                    mode="lines+markers",
                    line=dict(color="#00FF88", width=2.8),
                    marker=dict(size=7, color="#00FF88"),
                    text=pace_lbls,
                    hovertemplate="<b>%{x}</b><br>Ritmo Obj.: <b>%{text} /km</b><extra></extra>",
                ),
                secondary_y=True,
            )
            p_lo, p_hi = max(2.5, min(valid_p) - 0.35), min(14.0, max(valid_p) + 0.35)
            fig.update_yaxes(title_text="Ritmo (min/km)", range=[p_hi, p_lo], showgrid=False, secondary_y=True)
        fig.update_layout(
            title=dict(text="🏃‍♂️ Plan de Pacing Running (Potencia Stryd W y Ritmo min/km)", font=dict(size=13.5, color="#FFFFFF")),
            paper_bgcolor="#0E121C",
            plot_bgcolor="#0E121C",
            font=dict(color="#E2E8F0", size=11),
            height=315,
            margin=dict(l=10, r=10, t=44, b=18),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
            xaxis=dict(tickangle=-30 if len(tramos) > 8 else 0, gridcolor="rgba(255,255,255,0.05)"),
        )
        fig.update_yaxes(title_text="Potencia Stryd (W)", gridcolor="rgba(255,255,255,0.06)", secondary_y=False)
        return fig

    watts = [float(sp.get("Potencia Obj. (W)", 0) or 0) for sp in splits_list]
    speeds = [float(sp.get("Vel. Est. (km/h)", 0) or 0) for sp in splits_list]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=tramos,
            y=watts,
            name="Potencia Obj. (W)",
            marker=dict(color="#FFD700", line=dict(color="#FFA000", width=0.8)),
            opacity=0.88,
            text=[f"{int(round(w))}W" for w in watts] if len(tramos) <= 18 else None,
            textposition="outside",
            textfont=dict(color="#FFD700", size=10),
            hovertemplate="<b>%{x}</b><br>Potencia: <b>%{y:.0f} W</b><extra></extra>",
        ),
        secondary_y=False,
    )
    if any(v > 0 for v in speeds):
        fig.add_trace(
            go.Scatter(
                x=tramos,
                y=speeds,
                name="Velocidad Est. (km/h)",
                mode="lines+markers",
                line=dict(color="#00FF88", width=2.6),
                marker=dict(size=6, color="#00FF88"),
                hovertemplate="<b>%{x}</b><br>Velocidad: <b>%{y:.1f} km/h</b><extra></extra>",
            ),
            secondary_y=True,
        )

    fig.update_layout(
        title=dict(
            text="⚡ Plan de Pacing Tramo a Tramo (Potencia W y Velocidad km/h)",
            font=dict(size=13.5, color="#FFFFFF"),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0", size=11),
        height=310,
        margin=dict(l=10, r=10, t=44, b=18),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        xaxis=dict(tickangle=-30 if len(tramos) > 8 else 0, gridcolor="rgba(255,255,255,0.05)"),
    )
    fig.update_yaxes(title_text="Potencia (W)", gridcolor="rgba(255,255,255,0.06)", secondary_y=False)
    fig.update_yaxes(title_text="Velocidad (km/h)", showgrid=False, secondary_y=True)
    return fig


def _build_race_plan_text_card(sc, cfg: dict, nut: dict, splits_list: list, athlete_name: str) -> str:
    """Genera una ficha imprimible / de manillar o brazalete en texto plano con Pacing + Nutrición."""
    est_time_s = float(sc.estimated_time_sec or 0.0)
    is_run = isinstance(cfg, dict) and cfg.get("sport") == "running"

    if is_run:
        lines = [
            "==============================================================",
            "  ALPHAX ENDURANCE COACHING — PLAN OFICIAL DE RUNNING",
            "==============================================================",
            f"Deportista : {athlete_name}",
            f"Plan       : {sc.scenario_name}",
            f"Circuito   : {sc.route_name}",
        ]
        if cfg.get("race_date"):
            lines.append(f"Fecha/Hora : {cfg.get('race_date')} {cfg.get('race_start_time', '')}".strip())
        lines.extend([
            f"Distancia  : {float(sc.distance_km or 0):.2f} km   |   Desnivel: +{float(sc.elevation_gain_m or 0):.0f} m",
            f"Tiempo Obj.: {format_hms(est_time_s)}   |   Ritmo Medio: {cfg.get('avg_pace_str', '—')} /km   |   GAP: {cfg.get('avg_gap_str', '—')} /km",
            f"Potencia   : Stryd {float(sc.avg_power_w or 0):.0f} W ({cfg.get('w_per_kg', 0)} W/kg)   |   rCP ref: {cfg.get('run_cp_w', 0):.0f} W   |   rTSS: {float(sc.tss or 0):.0f}",
        ])
        if cfg.get("shoe_name"):
            lines.append(f"Calzado    : {cfg.get('shoe_name')}")
        lines.extend([
            "",
            "--------------------------------------------------------------",
            "  ESTRATEGIA NUTRICIONAL Y GELES EN CARRERA",
            "--------------------------------------------------------------",
            f"• Carbohidratos : {nut.get('cho_g_per_h', 0)} g/h  (Total: {nut.get('cho_total_g', 0)} g · {nut.get('gels_count', 0)} geles)",
            f"• Hidratación   : {nut.get('fluid_ml_per_h', 0)} mL/h  (Total: {nut.get('fluid_total_l', 0)} L)",
            f"• Sodio         : {nut.get('sodium_mg_per_h', 0)} mg/h  (Total: {nut.get('sodium_total_mg', 0)} mg)",
        ])
        for g in nut.get("gel_schedule", []):
            lines.append(f"  - Gel #{g['gel_num']}: Km {g['km']:.1f} (~{g['time_str']}) -> {g['cho_g']} g CHO")
        if splits_list:
            lines.extend([
                "",
                "--------------------------------------------------------------",
                "  PLAN DE PACING RUNNING TRAMO A TRAMO",
                "--------------------------------------------------------------",
                f"{'Tramo':<13} {'Pend':>6} {'Ritmo':>7} {'GAP':>7} {'Stryd':>7} {'Acum':>9}  Nutrición",
            ])
            for sp in splits_list:
                tramo = str(sp.get("Tramo", ""))[:12]
                pend = f"{sp.get('Pend. %', 0)}%"
                rit = str(sp.get("Ritmo Obj. (/km)", ""))
                gap = str(sp.get("GAP (/km)", ""))
                pw = f"{sp.get('Potencia Stryd (W)', 0)}W"
                acum = str(sp.get("Tiempo Acum.", ""))
                gel = str(sp.get("🥤 Gel / Nutrición", ""))
                lines.append(f"{tramo:<13} {pend:>6} {rit:>7} {gap:>7} {pw:>7} {acum:>9}  {gel}")
        lines.append("==============================================================")
        return "\n".join(lines)

    work_kj = cfg.get("work_kj") or round((float(sc.avg_power_w or 0.0) * est_time_s) / 1000.0)
    bottles_750 = max(1, int(math.ceil((float(nut.get("fluid_total_l", 0.0)) * 1000.0) / 750.0)))
    cho_per_20m = int(round(float(nut.get("cho_g_per_h", 75)) / 3.0))

    lines = [
        "==============================================================",
        "  ALPHAX ENDURANCE COACHING — PLAN OFICIAL DE CARRERA",
        "==============================================================",
        f"Deportista : {athlete_name}",
        f"Plan       : {sc.scenario_name}",
        f"Circuito   : {sc.route_name}",
    ]
    if cfg.get("race_date"):
        lines.append(f"Fecha/Hora : {cfg.get('race_date')} {cfg.get('race_start_time', '')}".strip())
    lines.extend([
        f"Distancia  : {float(sc.distance_km or 0):.1f} km   |   Desnivel: +{float(sc.elevation_gain_m or 0):.0f} m",
        f"Tiempo Obj.: {format_hms(est_time_s)}   |   Vel. Media: {float(sc.avg_speed_kmh or 0):.1f} km/h",
        f"Potencia   : NP {float(sc.norm_power_w or 0):.0f} W  /  AP {float(sc.avg_power_w or 0):.0f} W   |   IF {float(sc.intensity_factor or 0):.2f}   |   TSS {float(sc.tss or 0):.0f}",
        f"CdA / Masa : {float(sc.cda or 0):.3f} m²   |   Masa Total: {float(sc.total_mass_kg or 0):.1f} kg   |   Gasto: ~{int(work_kj)} kJ",
        "",
        "--------------------------------------------------------------",
        "  ESTRATEGIA NUTRICIONAL E HIDRATACIÓN",
        "--------------------------------------------------------------",
        f"• Carbohidratos : {nut.get('cho_g_per_h', 0)} g/h  (Total: {nut.get('cho_total_g', 0)} g  |  ~{cho_per_20m} g cada 20 min)",
        f"• Líquidos      : {nut.get('fluid_ml_per_h', 0)} mL/h  (Total: {nut.get('fluid_total_l', 0)} L  |  ~{bottles_750} bidones de 750 mL)",
        f"• Sodio         : {nut.get('sodium_mg_per_h', 0)} mg/h  (Total: {nut.get('sodium_total_mg', 0)} mg)",
    ])
    if cfg.get("temp_c") is not None:
        lines.append(
            f"• Clima ref.    : {cfg.get('temp_c')}°C · {cfg.get('humidity_pct', 65)}% HR · Viento {cfg.get('wind_kmh', 0)} km/h"
        )
    if cfg.get("bike_type") or cfg.get("strategy"):
        lines.append("")
        lines.append("--------------------------------------------------------------")
        lines.append("  CONFIGURACIÓN Y ESTRATEGIA")
        lines.append("--------------------------------------------------------------")
        if cfg.get("strategy"):
            lines.append(f"• Estrategia : {cfg.get('strategy')}")
        if cfg.get("bike_type"):
            lines.append(f"• Bicicleta  : {cfg.get('bike_type')}")
        if cfg.get("wheels"):
            lines.append(f"• Ruedas     : {cfg.get('wheels')}")
        if cfg.get("helmet"):
            lines.append(f"• Casco      : {cfg.get('helmet')}")
        if cfg.get("tires"):
            lines.append(f"• Llantas    : {cfg.get('tires')}")

    if splits_list:
        lines.extend([
            "",
            "--------------------------------------------------------------",
            "  PLAN DE PACING TRAMO A TRAMO",
            "--------------------------------------------------------------",
            f"{'Tramo':<15} {'Pend':>6} {'Pot(W)':>7} {'%FTP':>6} {'Vel':>6} {'Parcial':>8} {'Acum':>9}  Viento",
        ])
        for sp in splits_list:
            tramo = str(sp.get("Tramo", ""))[:14]
            pend = f"{sp.get('Pend. %', 0)}%"
            pot = f"{sp.get('Potencia Obj. (W)', 0)}W"
            pftp = str(sp.get("% FTP", ""))
            vel = f"{sp.get('Vel. Est. (km/h)', 0)}"
            parc = str(sp.get("Tiempo Tramo", ""))
            acum = str(sp.get("Tiempo Acum.", ""))
            viento = str(sp.get("Viento", ""))
            lines.append(f"{tramo:<15} {pend:>6} {pot:>7} {pftp:>6} {vel:>6} {parc:>8} {acum:>9}  {viento}")

    lines.append("==============================================================")
    return "\n".join(lines)


def render_saved_scenarios_library(
    member_id: int,
    athlete_name: str,
    allow_delete: bool = False,
    is_athlete_portal: bool = False,
    key_prefix: str = "lib",
):
    """
    Biblioteca completa de Planes de Carrera Guardados (reutilizable en Admin y Portal del Atleta).
    Muestra resumen comparativo de todos los escenarios, plan de pacing tramo a tramo con gráfica,
    estrategia nutricional completa y ficha técnica descargable.
    """
    _ensure_db_ready()
    if not member_id:
        st.info("No se encontró un perfil de deportista vinculado para consultar planes de carrera.")
        return

    with SessionLocal() as s_db:
        saved_recs = (
            s_db.query(RaceStrategyScenario)
            .filter(RaceStrategyScenario.member_id == member_id)
            .order_by(RaceStrategyScenario.created_at.desc(), RaceStrategyScenario.id.desc())
            .all()
        )

    if not saved_recs:
        if is_athlete_portal:
            _render_html(
                f"""
                <div style="background:rgba(15,20,32,0.88); border:1.5px dashed rgba(0,238,255,0.35);
                            border-radius:16px; padding:24px 18px; text-align:center; margin:12px 0;">
                    <div style="font-size:2rem; margin-bottom:6px;">🏁</div>
                    <div style="color:#FFFFFF; font-size:1.05rem; font-weight:800;">
                        Aún no tienes planes de carrera publicados
                    </div>
                    <div style="color:#94A3B8; font-size:0.86rem; margin-top:6px; line-height:1.5;">
                        Cuando tu entrenador AlphaX diseñe y guarde tus escenarios de carrera (plan de pacing, vatios por tramo y estrategia nutricional), aparecerán aquí automáticamente para ti.
                    </div>
                </div>
                """
            )
        else:
            st.info(
                f"📂 **{athlete_name}** aún no tiene planes de carrera guardados. "
                "Sube un archivo `.fit`, `.gpx` o `.tcx` arriba (o elige una ruta demo) y pulsa **💾 Guardar Escenario** para que aparezca aquí y en la App del Atleta."
            )
        return

    # Pre-parsear configuraciones y nutrición de todos los escenarios
    parsed_items = []
    for r in saved_recs:
        cfg = _safe_parse_json(r.config_json, {})
        if not isinstance(cfg, dict):
            cfg = {}
        splits = _safe_parse_json(r.splits_json, [])
        if not isinstance(splits, list):
            splits = []
        nut = cfg.get("nutrition")
        if not isinstance(nut, dict) or not nut.get("cho_g_per_h"):
            nut = _estimate_fallback_nutrition(
                r.estimated_time_sec or 0.0,
                temp_c=cfg.get("temp_c", 24.0),
                humidity_pct=cfg.get("humidity_pct", 65.0),
            )
        parsed_items.append({"rec": r, "cfg": cfg, "splits": splits, "nut": nut})

    # ── 1. RESUMEN EN TARJETAS DE LOS ESCENARIOS GUARDADOS ──────────
    n_plans = len(parsed_items)
    section_title = (
        f"🏁 Tus {n_plans} Planes de Carrera Diseñados por tu Coach"
        if is_athlete_portal
        else f"📚 Planes de Carrera Guardados — {athlete_name} ({n_plans})"
    )
    st.markdown(f"### {section_title}")
    if is_athlete_portal:
        st.caption(
            "Compara tus escenarios de carrera y selecciona cualquiera abajo para ver el plan completo de pacing tramo a tramo y tu estrategia nutricional."
        )
    else:
        st.caption(
            "Estos son los escenarios guardados en la base de datos (visibles automáticamente en modo solo lectura desde la App del Atleta)."
        )

    # Identificar el más rápido solo si hay >= 2 escenarios
    min_time_all = min((float(it["rec"].estimated_time_sec or 0.0) for it in parsed_items), default=0.0)
    same_route_set = {str(it["rec"].route_name or "").strip().lower() for it in parsed_items}
    compare_deltas_valid = n_plans >= 2 and len(same_route_set) == 1

    cards_html_parts = []
    for idx, item in enumerate(parsed_items[:6]):
        r = item["rec"]
        cfg = item["cfg"]
        nut = item["nut"]
        t_sec = float(r.estimated_time_sec or 0.0)
        is_fastest = n_plans >= 2 and abs(t_sec - min_time_all) < 0.5 and compare_deltas_valid
        border_col = "#00FF88" if is_fastest else ("rgba(0,238,255,0.45)" if idx == 0 else "rgba(255,255,255,0.14)")
        badge = ""
        if is_fastest:
            badge = '<span style="background:rgba(0,255,136,0.18); color:#00FF88; font-size:0.65rem; font-weight:800; padding:2px 7px; border-radius:6px;">🥇 MÁS RÁPIDO</span>'
        elif compare_deltas_valid and t_sec > min_time_all + 0.5:
            badge = f'<span style="background:rgba(255,215,0,0.14); color:#FFD700; font-size:0.65rem; font-weight:800; padding:2px 7px; border-radius:6px;">{format_time_delta(t_sec - min_time_all)}</span>'
        else:
            dt_lbl = r.created_at.strftime("%d/%m/%Y") if r.created_at else ""
            badge = f'<span style="background:rgba(0,238,255,0.14); color:#00EEFF; font-size:0.65rem; font-weight:800; padding:2px 7px; border-radius:6px;">Plan #{idx+1} · {dt_lbl}</span>'

        race_dt_chip = f" · 📅 {html.escape(str(cfg.get('race_date')))}" if cfg.get("race_date") else ""
        sc_title = html.escape(str(r.scenario_name or f"Plan #{idx+1}"))
        rt_title = html.escape(str(r.route_name or "Circuito"))
        is_run_card = cfg.get("sport") == "running"
        sport_chip = (
            '<span style="background:rgba(0,255,136,0.16); color:#00FF88; font-size:0.62rem; font-weight:900; padding:2px 6px; border-radius:5px;">🏃‍♂️ RUNNING</span>'
            if is_run_card
            else '<span style="background:rgba(0,238,255,0.14); color:#00EEFF; font-size:0.62rem; font-weight:900; padding:2px 6px; border-radius:5px;">🚴‍♂️ CICLISMO</span>'
        )
        if is_run_card:
            right_metric = f"<span style='color:#00FF88; font-size:0.88rem; font-weight:900;'>{html.escape(str(cfg.get('avg_pace_str', '—')))} /km</span>"
            pills_html = (
                f'<span style="background:rgba(56,189,248,0.12); color:#38BDF8; padding:2px 6px; border-radius:6px; font-weight:700;">GAP {html.escape(str(cfg.get("avg_gap_str", "—")))}/km</span>'
                f'<span style="background:rgba(255,215,0,0.14); color:#FFD700; padding:2px 6px; border-radius:6px; font-weight:700;">⚡ {float(r.avg_power_w or 0):.0f} W</span>'
                f'<span style="background:rgba(255,170,0,0.12); color:#FFAA00; padding:2px 6px; border-radius:6px; font-weight:700;">🥤 {nut.get("gels_count", 0)} geles</span>'
            )
        else:
            right_metric = f"<span style='color:#00FF88; font-size:0.86rem; font-weight:800;'>{float(r.avg_speed_kmh or 0):.1f} km/h</span>"
            pills_html = (
                f'<span style="background:rgba(255,215,0,0.14); color:#FFD700; padding:2px 6px; border-radius:6px; font-weight:700;">⚡ NP {float(r.norm_power_w or 0):.0f} W</span>'
                f'<span style="background:rgba(192,132,252,0.14); color:#C084FC; padding:2px 6px; border-radius:6px; font-weight:700;">IF {float(r.intensity_factor or 0):.2f} · TSS {float(r.tss or 0):.0f}</span>'
                f'<span style="background:rgba(255,59,48,0.14); color:#FF6B6B; padding:2px 6px; border-radius:6px; font-weight:700;">CdA {float(r.cda or 0):.3f}</span>'
                f'<span style="background:rgba(255,170,0,0.12); color:#FFAA00; padding:2px 6px; border-radius:6px; font-weight:700;">🍌 {nut.get("cho_g_per_h", 0)} g/h</span>'
            )

        cards_html_parts.append(
            f"""
            <div style="background:linear-gradient(145deg, rgba(16,22,36,0.95), rgba(9,13,22,0.98));
                        border:1.5px solid {border_col}; border-radius:14px; padding:13px 14px;
                        box-shadow:0 6px 18px rgba(0,0,0,0.4); display:flex; flex-direction:column; justify-content:space-between;">
                <div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; margin-bottom:6px;">
                        {sport_chip}
                        {badge}
                    </div>
                    <div style="color:#FFFFFF; font-size:0.95rem; font-weight:800; line-height:1.25; margin-bottom:4px;">{sc_title}</div>
                    <div style="color:#8E9BAE; font-size:0.74rem; margin-bottom:8px;">🗺️ {rt_title}{race_dt_chip}</div>
                </div>
                <div>
                    <div style="display:flex; align-items:baseline; justify-content:space-between; border-top:1px solid rgba(255,255,255,0.08); padding-top:8px; margin-bottom:6px;">
                        <span style="color:#00EEFF; font-size:1.35rem; font-weight:900;">⏱️ {format_hms(t_sec)}</span>
                        {right_metric}
                    </div>
                    <div style="display:flex; flex-wrap:wrap; gap:5px; font-size:0.72rem; color:#CBD5E1;">
                        {pills_html}
                    </div>
                </div>
            </div>
            """
        )

    _render_html(
        f"""
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:10px; margin:8px 0 14px 0;">
            {''.join(cards_html_parts)}
        </div>
        """
    )

    # Tabla resumen comparativa cuando hay 2+ escenarios
    if n_plans >= 2:
        with st.expander(f"📊 Ver Tabla Comparativa de los {n_plans} Escenarios", expanded=(n_plans <= 4)):
            summary_rows = []
            for item in parsed_items:
                r = item["rec"]
                nut = item["nut"]
                t_sec = float(r.estimated_time_sec or 0.0)
                delta_txt = (
                    "🥇 Más rápido"
                    if (compare_deltas_valid and abs(t_sec - min_time_all) < 0.5)
                    else (format_time_delta(t_sec - min_time_all) if compare_deltas_valid else "—")
                )
                row_d = {
                    "Escenario": r.scenario_name,
                    "Ruta": r.route_name,
                    "Dist. (km)": round(float(r.distance_km or 0), 1),
                    "Tiempo": format_hms(t_sec),
                }
                if compare_deltas_valid:
                    row_d["Delta"] = delta_txt
                row_d.update({
                    "Vel. (km/h)": round(float(r.avg_speed_kmh or 0), 1),
                    "NP (W)": int(round(float(r.norm_power_w or 0))),
                    "IF": round(float(r.intensity_factor or 0), 2),
                    "TSS": int(round(float(r.tss or 0))),
                    "CdA": round(float(r.cda or 0), 3),
                    "Carbos": f"{nut.get('cho_g_per_h', 0)} g/h",
                    "Líquido": f"{nut.get('fluid_total_l', 0)} L",
                })
                summary_rows.append(row_d)
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            # Mini gráfica comparativa de tiempo total
            fig_cmp = go.Figure(
                data=[
                    go.Bar(
                        x=[float(it["rec"].estimated_time_sec or 0) / 60.0 for it in parsed_items[:6]],
                        y=[str(it["rec"].scenario_name or "")[:36] for it in parsed_items[:6]],
                        orientation="h",
                        marker_color=[
                            "#00FF88" if (compare_deltas_valid and abs(float(it["rec"].estimated_time_sec or 0) - min_time_all) < 0.5) else "#00EEFF"
                            for it in parsed_items[:6]
                        ],
                        text=[format_hms(float(it["rec"].estimated_time_sec or 0)) for it in parsed_items[:6]],
                        textposition="auto",
                    )
                ]
            )
            fig_cmp.update_layout(
                title="⏱️ Comparación de Tiempo Estimado (minutos)",
                paper_bgcolor="#0E121C",
                plot_bgcolor="#0E121C",
                font=dict(color="#E2E8F0", size=11),
                height=max(190, 44 * min(6, n_plans) + 65),
                margin=dict(l=10, r=15, t=38, b=12),
                xaxis_title="Minutos",
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_cmp, use_container_width=True)

    # ── 2. DETALLE COMPLETO DEL PLAN SELECCIONADO (PACING + NUTRICIÓN) ──
    st.markdown("---")
    pick_options = {}
    for idx, item in enumerate(parsed_items):
        r = item["rec"]
        c_i = item["cfg"]
        dt_s = r.created_at.strftime("%d/%m/%Y") if r.created_at else ""
        if c_i.get("sport") == "running":
            lbl = f"🏃‍♂️ Plan #{idx+1}: {r.scenario_name} — ⏱️ {format_hms(r.estimated_time_sec or 0)} ({c_i.get('avg_pace_str', '—')}/km · Stryd {float(r.avg_power_w or 0):.0f} W · {dt_s})"
        else:
            lbl = f"🚴‍♂️ Plan #{idx+1}: {r.scenario_name} — ⏱️ {format_hms(r.estimated_time_sec or 0)} (NP {float(r.norm_power_w or 0):.0f} W · IF {float(r.intensity_factor or 0):.2f} · {dt_s})"
        pick_options[lbl] = item

    sel_lbl = st.selectbox(
        "🔍 Selecciona el Plan de Carrera para ver el Pacing completo y la Estrategia Nutricional:",
        list(pick_options.keys()),
        key=f"rs_lib_pick_{key_prefix}_{member_id}",
    )
    sel_item = pick_options[sel_lbl]
    sel_sc = sel_item["rec"]
    sel_cfg = sel_item["cfg"]
    sel_splits = sel_item["splits"]
    sel_nut = sel_item["nut"]
    is_run_sel = sel_cfg.get("sport") == "running"

    t_sec = float(sel_sc.estimated_time_sec or 0.0)
    work_kj = sel_cfg.get("work_kj") or round((float(sel_sc.avg_power_w or 0.0) * t_sec) / 1000.0)
    w_kg_txt = f" · {sel_cfg['w_per_kg_np']:.2f} W/kg" if sel_cfg.get("w_per_kg_np") else ""
    vi_txt = f" · VI {sel_cfg['vi']:.2f}" if sel_cfg.get("vi") else ""

    # KPIs Hero del Plan Seleccionado (Running o Ciclismo)
    if is_run_sel:
        _render_html(
            f"""
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap:8px; margin:8px 0 14px 0;">
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid #00EEFF; border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">⏱️ Tiempo Objetivo</div>
                    <div style="color:#00EEFF; font-size:1.35rem; font-weight:900; margin-top:3px;">{format_hms(t_sec)}</div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{float(sel_sc.distance_km or 0):.2f} km · +{float(sel_sc.elevation_gain_m or 0):.0f} m</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(0,255,136,0.5); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🏃‍♂️ Ritmo Medio</div>
                    <div style="color:#00FF88; font-size:1.35rem; font-weight:900; margin-top:3px;">{html.escape(str(sel_cfg.get('avg_pace_str', '—')))} <span style="font-size:0.75rem;">/km</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{float(sel_sc.avg_speed_kmh or 0):.1f} km/h</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(56,189,248,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🏔️ Ritmo GAP</div>
                    <div style="color:#38BDF8; font-size:1.35rem; font-weight:900; margin-top:3px;">{html.escape(str(sel_cfg.get('avg_gap_str', '—')))} <span style="font-size:0.75rem;">/km</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">1ª {sel_cfg.get('first_half_str', '—')} · 2ª {sel_cfg.get('second_half_str', '—')}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,215,0,0.5); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">⚡ Potencia Stryd</div>
                    <div style="color:#FFD700; font-size:1.35rem; font-weight:900; margin-top:3px;">{float(sel_sc.avg_power_w or 0):.0f} <span style="font-size:0.75rem;">W</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{sel_cfg.get('w_per_kg', 0)} W/kg · rTSS {float(sel_sc.tss or 0):.0f}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,170,0,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🥤 Geles & Carbos</div>
                    <div style="color:#FFAA00; font-size:1.3rem; font-weight:900; margin-top:3px;">{sel_nut.get('gels_count', 0)} <span style="font-size:0.72rem;">geles</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{sel_nut.get('cho_g_per_h', 0)} g/h · {sel_nut.get('fluid_total_l', 0)} L</div>
                </div>
            </div>
            """
        )
    else:
        _render_html(
            f"""
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap:8px; margin:8px 0 14px 0;">
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid #00EEFF; border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">⏱️ Tiempo Objetivo</div>
                    <div style="color:#00EEFF; font-size:1.35rem; font-weight:900; margin-top:3px;">{format_hms(t_sec)}</div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{float(sel_sc.distance_km or 0):.1f} km · +{float(sel_sc.elevation_gain_m or 0):.0f} m</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(0,255,136,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🚀 Velocidad Media</div>
                    <div style="color:#00FF88; font-size:1.35rem; font-weight:900; margin-top:3px;">{float(sel_sc.avg_speed_kmh or 0):.1f} <span style="font-size:0.75rem;">km/h</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{html.escape(str(sel_sc.route_name or '')[:24])}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,215,0,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">⚡ Potencia NP / AP</div>
                    <div style="color:#FFD700; font-size:1.35rem; font-weight:900; margin-top:3px;">{float(sel_sc.norm_power_w or 0):.0f} <span style="font-size:0.75rem;">W</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">AP: {float(sel_sc.avg_power_w or 0):.0f} W{w_kg_txt}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(192,132,252,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🎯 Intensidad & TSS</div>
                    <div style="color:#C084FC; font-size:1.35rem; font-weight:900; margin-top:3px;">{float(sel_sc.intensity_factor or 0):.2f} <span style="font-size:0.75rem;">IF</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">TSS: {float(sel_sc.tss or 0):.0f}{vi_txt}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,59,48,0.45); border-radius:12px; padding:11px 9px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.65rem; font-weight:800; text-transform:uppercase;">🌬️ CdA & Gasto</div>
                    <div style="color:#FF5252; font-size:1.3rem; font-weight:900; margin-top:3px;">{float(sel_sc.cda or 0):.3f} <span style="font-size:0.72rem;">m²</span></div>
                    <div style="color:#94A3B8; font-size:0.68rem;">{float(sel_sc.total_mass_kg or 0):.1f} kg · ~{int(work_kj)} kJ</div>
                </div>
            </div>
            """
        )

    # ── ESTRATEGIA NUTRICIONAL E HIDRATACIÓN COMPLETA ────────────────
    bottles_750 = max(1, int(math.ceil((float(sel_nut.get("fluid_total_l", 0.0)) * 1000.0) / 750.0)))
    cho_per_20m = int(round(float(sel_nut.get("cho_g_per_h", 75)) / 3.0))
    gels_est = sel_nut.get("gels_count") if is_run_sel else max(1, int(round(float(sel_nut.get("cho_total_g", 0)) / 30.0)))
    climate_tag = ""
    if sel_cfg.get("temp_c") is not None:
        climate_tag = f" ({sel_cfg.get('temp_c')}°C · {sel_cfg.get('humidity_pct', 65)}% HR · Viento {sel_cfg.get('wind_kmh', 0)} km/h)"

    gel_sched_html = ""
    if is_run_sel and sel_nut.get("gel_schedule"):
        gel_chips = "".join(
            f'<span style="background:rgba(255,170,0,0.16); border:1px solid rgba(255,170,0,0.45); color:#FFD166; '
            f'padding:3px 8px; border-radius:7px; font-size:0.75rem; font-weight:800;">'
            f'⚡ Gel #{g["gel_num"]} · Km {g["km"]:.1f} ({g["time_str"]}) · {g["cho_g"]}g</span>'
            for g in sel_nut["gel_schedule"]
        )
        gel_sched_html = f'<div style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px;">{gel_chips}</div>'

    nut_tip = (
        html.escape(str(sel_nut.get("gel_note", "Toma cada gel con un sorbo de agua en los puestos de hidratación.")))
        if is_run_sel
        else f"Toma <b>~{cho_per_20m} g de CHO cada 20 minutos</b> desde el minuto 15 (gel, goma o mezcla en bidón), bebe <b>2–3 sorbos cada 10–15 min</b> y repón electrolitos de forma continua. Gasto energético estimado del segmento: <b>~{int(work_kj)} kcal</b>."
    )
    seg_noun = "carrera" if is_run_sel else "bici"
    fluid_sub = (
        f"Total carrera: <b>{sel_nut.get('fluid_total_l', 0)} L</b> (hidratación en puestos)"
        if is_run_sel
        else f"Total bici: <b>{sel_nut.get('fluid_total_l', 0)} L</b> (~{bottles_750} bidones de 750 mL)"
    )

    _render_html(
        f"""
        <div style="background:linear-gradient(145deg, rgba(12,24,26,0.94), rgba(10,15,24,0.96));
                    border:1.5px solid rgba(0,255,136,0.42); border-radius:14px; padding:14px 16px; margin-bottom:14px;">
            <div style="color:#00FF88; font-weight:900; font-size:0.95rem; margin-bottom:10px;">
                🥤 Estrategia Nutricional e Hidratación en Carrera{html.escape(climate_tag)}
            </div>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap:9px; margin-bottom:10px;">
                <div style="background:rgba(255,170,0,0.09); border:1px solid rgba(255,170,0,0.35); border-radius:10px; padding:10px 12px;">
                    <div style="color:#FFAA00; font-size:0.7rem; font-weight:800; text-transform:uppercase;">🍌 Carbohidratos (CHO)</div>
                    <div style="color:#FFFFFF; font-size:1.25rem; font-weight:900; margin-top:2px;">{sel_nut.get('cho_g_per_h', 0)} g/hora</div>
                    <div style="color:#CBD5E1; font-size:0.76rem; margin-top:2px;">Total {seg_noun}: <b>{sel_nut.get('cho_total_g', 0)} g</b> (~{gels_est} geles)</div>
                </div>
                <div style="background:rgba(0,238,255,0.09); border:1px solid rgba(0,238,255,0.35); border-radius:10px; padding:10px 12px;">
                    <div style="color:#00EEFF; font-size:0.7rem; font-weight:800; text-transform:uppercase;">💧 Hidratación</div>
                    <div style="color:#FFFFFF; font-size:1.25rem; font-weight:900; margin-top:2px;">{sel_nut.get('fluid_ml_per_h', 0)} mL/hora</div>
                    <div style="color:#CBD5E1; font-size:0.76rem; margin-top:2px;">{fluid_sub}</div>
                </div>
                <div style="background:rgba(192,132,252,0.10); border:1px solid rgba(192,132,252,0.35); border-radius:10px; padding:10px 12px;">
                    <div style="color:#C084FC; font-size:0.7rem; font-weight:800; text-transform:uppercase;">🧂 Sodio / Electrolitos</div>
                    <div style="color:#FFFFFF; font-size:1.25rem; font-weight:900; margin-top:2px;">{sel_nut.get('sodium_mg_per_h', 0)} mg/hora</div>
                    <div style="color:#CBD5E1; font-size:0.76rem; margin-top:2px;">Total {seg_noun}: <b>{sel_nut.get('sodium_total_mg', 0)} mg</b></div>
                </div>
            </div>
            {gel_sched_html}
            <div style="color:#CBD5E1; font-size:0.8rem; line-height:1.5; background:rgba(255,255,255,0.03); border-radius:8px; padding:8px 11px;">
                💡 <b>Guía práctica de ejecución:</b> {nut_tip}
            </div>
        </div>
        """
    )

    # ── PLAN DE PACING TRAMO A TRAMO (GRÁFICA + TABLA + TÁCTICA) ─────
    if sel_splits:
        st.markdown(
            "#### 📋 Plan de Pacing Running Completo (Hoja de Ruta de Ritmo, Stryd y Geles)"
            if is_run_sel
            else "#### 📋 Plan de Pacing Completo (Hoja de Ruta de Potencia)"
        )

        if not is_run_sel:
            # Resumen táctico automático según los splits guardados (Ciclismo)
            climb_w = [float(sp.get("Potencia Obj. (W)", 0)) for sp in sel_splits if "Subida" in str(sp.get("Terreno", "")) or float(sp.get("Pend. %", 0) or 0) >= 2.5]
            flat_w = [float(sp.get("Potencia Obj. (W)", 0)) for sp in sel_splits if "Llano" in str(sp.get("Terreno", ""))]
            desc_w = [float(sp.get("Potencia Obj. (W)", 0)) for sp in sel_splits if "Bajada" in str(sp.get("Terreno", "")) or float(sp.get("Pend. %", 0) or 0) <= -2.5]
            hw_w = [float(sp.get("Potencia Obj. (W)", 0)) for sp in sel_splits if "Contra" in str(sp.get("Viento", ""))]

            tac_pills = []
            if flat_w:
                tac_pills.append(f"➡️ <b>Llano:</b> {sum(flat_w)/len(flat_w):.0f} W prom.")
            if climb_w:
                tac_pills.append(f"⛰️ <b>Subidas:</b> {sum(climb_w)/len(climb_w):.0f} W prom.")
            if desc_w:
                tac_pills.append(f"⬇️ <b>Bajadas:</b> {sum(desc_w)/len(desc_w):.0f} W prom.")
            if hw_w:
                tac_pills.append(f"🔴 <b>Viento en contra ({len(hw_w)} tramos):</b> {sum(hw_w)/len(hw_w):.0f} W prom.")

            if tac_pills:
                _render_html(
                    f"""
                    <div style="background:rgba(15,20,32,0.86); border:1px solid rgba(0,238,255,0.22); border-radius:10px;
                                padding:9px 13px; margin-bottom:10px; color:#E2E8F0; font-size:0.82rem; display:flex; flex-wrap:wrap; gap:14px;">
                        {' '.join(f'<span>{p}</span>' for p in tac_pills)}
                    </div>
                    """
                )

        st.plotly_chart(_build_saved_splits_pacing_chart(sel_splits, sel_sc.scenario_name, is_running=is_run_sel), use_container_width=True)
        df_sp = pd.DataFrame(sel_splits)
        st.dataframe(df_sp, use_container_width=True, hide_index=True)
    else:
        st.info("Este escenario no tiene tabla de splits guardada.")

    # ── FICHA TÉCNICA DE CONFIGURACIÓN (EQUIPO, CLIMA, ESTRATEGIA) ───
    tech_lines = []
    if sel_cfg.get("race_date"):
        tech_lines.append(f"📅 <b>Fecha / Hora:</b> {html.escape(str(sel_cfg.get('race_date')))} {html.escape(str(sel_cfg.get('race_start_time', '')))}")
    if sel_cfg.get("strategy"):
        tech_lines.append(f"🧠 <b>Estrategia:</b> {html.escape(str(sel_cfg.get('strategy')))}")
    if sel_cfg.get("bike_type"):
        tech_lines.append(f"🚲 <b>Bicicleta:</b> {html.escape(str(sel_cfg.get('bike_type')))}")
    if sel_cfg.get("wheels"):
        tech_lines.append(f"⭕ <b>Ruedas:</b> {html.escape(str(sel_cfg.get('wheels')))}")
    if sel_cfg.get("helmet"):
        tech_lines.append(f"⛑️ <b>Casco:</b> {html.escape(str(sel_cfg.get('helmet')))}")
    if sel_cfg.get("tires"):
        tech_lines.append(f"🛞 <b>Llantas:</b> {html.escape(str(sel_cfg.get('tires')))}")
    if sel_cfg.get("weather_source"):
        tech_lines.append(f"🌦️ <b>Fuente clima:</b> {html.escape(str(sel_cfg.get('weather_source')))}")
    if sel_cfg.get("source") == "what_if_interactive":
        tech_lines.append("🎚️ <b>Origen:</b> Escenario ajustado desde Comparador 'Qué Pasa Si...'")

    if tech_lines:
        with st.expander("⚙️ Ficha Técnica del Escenario (Equipo, Clima y Estrategia)", expanded=False):
            _render_html(
                "<div style='color:#CBD5E1; font-size:0.83rem; line-height:1.7;'>"
                + "<br>".join(tech_lines)
                + "</div>"
            )

    # ── ACCIONES: DESCARGAS / COMPARTIR (+ ELIMINAR SOLO EN ADMIN) ───
    txt_card = _build_race_plan_text_card(sel_sc, sel_cfg, sel_nut, sel_splits, athlete_name)
    safe_slug = "".join(c if c.isalnum() else "_" for c in str(sel_sc.scenario_name or f"plan_{sel_sc.id}"))[:35]

    if is_athlete_portal:
        # En la App móvil / iOS PWA (pantalla de inicio), st.download_button navega el WebView
        # a /media/... y deja al usuario sin botón "Atrás". Usamos WhatsApp + Copia inline 100% segura.
        if is_run_sel:
            wa_lines = [
                f"🏃‍♂️ *PLAN DE RUNNING ALPHAX — {athlete_name}*",
                f"📌 *{sel_sc.scenario_name}*",
                f"🗺️ {sel_sc.route_name} ({float(sel_sc.distance_km or 0):.2f} km · +{float(sel_sc.elevation_gain_m or 0):.0f} m)",
                f"⏱️ Tiempo Obj: *{format_hms(t_sec)}* · Ritmo *{sel_cfg.get('avg_pace_str', '—')}/km* (GAP {sel_cfg.get('avg_gap_str', '—')}/km)",
                f"⚡ Potencia Stryd: *{float(sel_sc.avg_power_w or 0):.0f} W* ({sel_cfg.get('w_per_kg', 0)} W/kg)",
                "",
                "🥤 *NUTRICIÓN Y GELES:*",
                f"• Carbos: *{sel_nut.get('cho_g_per_h', 0)} g/h* ({sel_nut.get('gels_count', 0)} geles · Total {sel_nut.get('cho_total_g', 0)} g)",
                f"• Líquidos: *{sel_nut.get('fluid_ml_per_h', 0)} mL/h* (Total {sel_nut.get('fluid_total_l', 0)} L) · Sodio {sel_nut.get('sodium_mg_per_h', 0)} mg/h",
            ]
            for g in sel_nut.get("gel_schedule", []):
                wa_lines.append(f"  ⚡ Gel #{g['gel_num']}: Km {g['km']:.1f} ({g['time_str']}) · {g['cho_g']}g")
            if sel_splits:
                wa_lines.append("")
                wa_lines.append("📋 *PACING POR TRAMO:*")
                for sp in sel_splits[:18]:
                    wa_lines.append(
                        f"• {sp.get('Tramo', '')}: *{sp.get('Ritmo Obj. (/km)', '')}/km* · {sp.get('Potencia Stryd (W)', 0)}W · Acum {sp.get('Tiempo Acum.', '')}"
                    )
        else:
            wa_lines = [
                f"🏁 *PLAN DE CARRERA ALPHAX — {athlete_name}*",
                f"📌 *{sel_sc.scenario_name}*",
                f"🗺️ {sel_sc.route_name} ({float(sel_sc.distance_km or 0):.1f} km · +{float(sel_sc.elevation_gain_m or 0):.0f} m)",
                f"⏱️ Tiempo Obj: *{format_hms(t_sec)}* ({float(sel_sc.avg_speed_kmh or 0):.1f} km/h)",
                f"⚡ Potencia: *NP {float(sel_sc.norm_power_w or 0):.0f} W* (AP {float(sel_sc.avg_power_w or 0):.0f} W) · IF {float(sel_sc.intensity_factor or 0):.2f} · TSS {float(sel_sc.tss or 0):.0f}",
                "",
                "🥤 *NUTRICIÓN E HIDRATACIÓN:*",
                f"• Carbos: *{sel_nut.get('cho_g_per_h', 0)} g/h* (Total {sel_nut.get('cho_total_g', 0)} g · ~{cho_per_20m} g c/20m)",
                f"• Líquidos: *{sel_nut.get('fluid_ml_per_h', 0)} mL/h* (Total {sel_nut.get('fluid_total_l', 0)} L · ~{bottles_750} bidones)",
                f"• Sodio: *{sel_nut.get('sodium_mg_per_h', 0)} mg/h* (Total {sel_nut.get('sodium_total_mg', 0)} mg)",
            ]
            if sel_splits:
                wa_lines.append("")
                wa_lines.append("📋 *PACING POR TRAMO:*")
                for sp in sel_splits[:18]:
                    wa_lines.append(
                        f"• {sp.get('Tramo', '')}: *{sp.get('Potencia Obj. (W)', 0)}W* ({sp.get('% FTP', '')}) · {sp.get('Vel. Est. (km/h)', 0)} km/h · Acum {sp.get('Tiempo Acum.', '')}"
                    )
                if len(sel_splits) > 18:
                    wa_lines.append(f"_(+{len(sel_splits) - 18} tramos más en tu App AlphaX)_")

        wa_text = "\n".join(wa_lines)[:1800]
        wa_url = f"https://wa.me/?text={urllib.parse.quote(wa_text)}"

        _render_html(
            f"""
            <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:10px 0 12px 0;">
                <a href="{wa_url}" target="_blank" rel="noopener noreferrer" style="text-decoration:none; flex:1 1 220px;">
                    <div style="background:linear-gradient(135deg,#25D366,#128C7E); color:#FFFFFF; text-align:center;
                                padding:11px 16px; border-radius:10px; font-weight:800; font-size:0.88rem;
                                box-shadow:0 4px 14px rgba(37,211,102,0.32);">
                        📲 Guardar / Enviar Plan por WhatsApp
                    </div>
                </a>
            </div>
            """
        )

        with st.expander("📋 Ver y Copiar Ficha Completa (Pacing + Nutrición para Notas)", expanded=False):
            st.caption("Toca el ícono de copiar en la esquina superior derecha del recuadro para pegarlo en Notas de tu teléfono sin salir de la app:")
            st.code(txt_card, language="text")
    else:
        if allow_delete:
            c_act0, c_act1, c_act2, c_act3 = st.columns([1.35, 1.05, 1.15, 0.85])
        else:
            c_act0, c_act1, c_act2 = st.columns([1.35, 1.1, 1.1])
            c_act3 = None

        with c_act0:
            if st.button(
                "⚔️ Abrir en 'Qué Pasa Si...'",
                type="primary",
                use_container_width=True,
                key=f"rs_open_wi_{key_prefix}_{sel_sc.id}",
            ):
                if is_run_sel:
                    st.session_state["rs_sport_mode"] = "running"
                    st.session_state["run_loaded_saved_sc_id"] = sel_sc.id
                    st.session_state["run_active_section"] = "comp"
                else:
                    st.session_state["rs_sport_mode"] = "cycling"
                    st.session_state["rs_loaded_saved_sc_id"] = sel_sc.id
                    st.session_state["rs_active_section"] = "comp"
                st.rerun()
        with c_act1:
            if sel_splits:
                st.download_button(
                    "⬇️ Pacing (CSV)",
                    data=pd.DataFrame(sel_splits).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"pacing_{safe_slug}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"rs_dl_csv_{key_prefix}_{sel_sc.id}",
                )
        with c_act2:
            st.download_button(
                "🏷️ Ficha Manillar (TXT)",
                data=txt_card.encode("utf-8"),
                file_name=f"plan_carrera_{safe_slug}.txt",
                mime="text/plain",
                use_container_width=True,
                key=f"rs_dl_txt_{key_prefix}_{sel_sc.id}",
            )
        if allow_delete and c_act3 is not None:
            with c_act3:
                if st.button(
                    "🗑️ Eliminar",
                    type="secondary",
                    use_container_width=True,
                    key=f"rs_del_sc_{key_prefix}_{sel_sc.id}",
                ):
                    try:
                        with SessionLocal() as s_del:
                            obj = s_del.query(RaceStrategyScenario).filter(RaceStrategyScenario.id == sel_sc.id).first()
                            if obj:
                                s_del.delete(obj)
                                s_del.commit()
                        st.toast("Escenario eliminado", icon="🗑️")
                        st.rerun()
                    except Exception as err_d:
                        st.error(f"Error al eliminar: {err_d}")


def render_athlete_race_plans_tab(member_id: int, athlete_name: str):
    """Vista exclusiva de solo lectura para la App del Atleta: Planes de Pacing y Nutrición creados por el Coach."""
    _render_html(
        """
        <div style="background: linear-gradient(135deg, rgba(14, 22, 38, 0.96), rgba(8, 12, 22, 0.98));
                    border: 1.5px solid rgba(0, 238, 255, 0.35); border-radius: 15px; padding: 14px 16px;
                    margin-bottom: 14px; box-shadow: 0 6px 20px rgba(0,0,0,0.45);">
            <div style="color:#00EEFF; font-size:1.12rem; font-weight:900; letter-spacing:0.3px;">
                🏁 ESTRATEGIAS Y PLANES DE CARRERA
            </div>
            <div style="color:#94A3B8; font-size:0.82rem; margin-top:4px; line-height:1.45;">
                Consulta los escenarios de carrera, tu <b>plan de pacing tramo a tramo</b> y tu <b>estrategia nutricional</b> diseñados por tu entrenador AlphaX.
            </div>
        </div>
        """
    )
    render_saved_scenarios_library(
        member_id=member_id,
        athlete_name=athlete_name,
        allow_delete=False,
        is_athlete_portal=True,
        key_prefix="ath_portal",
    )


def render_race_strategy_page():
    """Renderiza el módulo completo de Estrategias de Carrera (AlphaX Race Split) en el CRM Admin."""
    _ensure_db_ready()

    _render_html(
        """
        <div style="background: linear-gradient(135deg, rgba(14, 22, 38, 0.96), rgba(8, 12, 22, 0.98));
                    border: 1.5px solid rgba(0, 238, 255, 0.35); border-radius: 16px; padding: 14px 20px;
                    margin-bottom: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.45);">
            <div style="color:#00EEFF; font-size:1.45rem; font-weight:900; letter-spacing:0.4px;">
                🏁 ALPHAX RACE SPLIT <span style="color:#FFFFFF; font-weight:400; font-size:1.05rem;">| Simulador de Ciclismo & Running (FIT / GPX / TCX)</span>
                <span style="background:rgba(0,238,255,0.14); color:#00EEFF; font-size:0.68rem; font-weight:800; padding:2px 8px; border-radius:10px; margin-left:6px; vertical-align:middle;">v2.22</span>
            </div>
            <div style="color:#94A3B8; font-size:0.84rem; margin-top:3px;">
                Planifica carreras de <b>Ciclismo</b> (Potencia, CdA, viento) y <b>Running</b> (5K–42K por Ritmo/PB o Potencia Stryd, desnivel GAP y geles) sobre cualquier circuito GPX/FIT.
            </div>
        </div>
        """
    )

    def _nav_btn_css(key_name: str, is_active: bool, grad: str, solid_col: str, tint_bg: str, active_txt: str, glow: str) -> str:
        if is_active:
            return f"""
            .st-key-{key_name} button {{
                background: {grad} !important;
                color: {active_txt} !important;
                border: 2.5px solid {solid_col} !important;
                box-shadow: 0 0 22px {glow}, 0 6px 16px rgba(0,0,0,0.45) !important;
                min-height: 58px !important;
                border-radius: 14px !important;
                font-size: 0.96rem !important;
                font-weight: 900 !important;
                letter-spacing: 0.2px !important;
                transition: all 0.2s ease !important;
            }}
            .st-key-{key_name} button p {{
                color: {active_txt} !important;
                font-weight: 900 !important;
                font-size: 0.96rem !important;
            }}
            """
        return f"""
        .st-key-{key_name} button {{
            background: {tint_bg} !important;
            color: {solid_col} !important;
            border: 2px solid {solid_col} !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.35) !important;
            min-height: 58px !important;
            border-radius: 14px !important;
            font-size: 0.94rem !important;
            font-weight: 800 !important;
            letter-spacing: 0.2px !important;
            transition: all 0.2s ease !important;
        }}
        .st-key-{key_name} button p {{
            color: {solid_col} !important;
            font-weight: 800 !important;
            font-size: 0.94rem !important;
        }}
        .st-key-{key_name} button:hover {{
            background: {grad} !important;
            color: {active_txt} !important;
            box-shadow: 0 0 18px {glow} !important;
            transform: translateY(-2px) !important;
        }}
        .st-key-{key_name} button:hover p {{
            color: {active_txt} !important;
        }}
        """

    # ── SELECTOR DE DISCIPLINA: 🚴‍♂️ CICLISMO  |  🏃‍♂️ RUNNING (PACE & STRYD) ──
    if "rs_sport_mode" not in st.session_state:
        st.session_state["rs_sport_mode"] = "cycling"
    sport_mode = st.session_state["rs_sport_mode"]

    sport_css = "<style>" + "".join([
        _nav_btn_css("rs_sp_cyc", sport_mode == "cycling", "linear-gradient(135deg,#00EEFF,#0066FF)", "#00EEFF", "rgba(0,238,255,0.10)", "#04101C", "rgba(0,238,255,0.45)"),
        _nav_btn_css("rs_sp_run", sport_mode == "running", "linear-gradient(135deg,#00FF88,#00B86B)", "#00FF88", "rgba(0,255,136,0.10)", "#031A0E", "rgba(0,255,136,0.45)"),
    ]) + "</style>"
    _render_html(sport_css)

    sp_c1, sp_c2 = st.columns(2)
    with sp_c1:
        if st.button(("● " if sport_mode == "cycling" else "") + "🚴‍♂️ MODO CICLISMO (Potencia · CdA · Best Bike Split)", use_container_width=True, key="rs_sp_cyc"):
            st.session_state["rs_sport_mode"] = "cycling"
            st.rerun()
    with sp_c2:
        if st.button(("● " if sport_mode == "running" else "") + "🏃‍♂️ MODO RUNNING (Pace / PB 5K–42K · Potencia Stryd)", use_container_width=True, key="rs_sp_run"):
            st.session_state["rs_sport_mode"] = "running"
            st.rerun()

    st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    # Si estamos en MODO RUNNING, delegar directamente al módulo Running
    if sport_mode == "running":
        member_map = _load_active_members_map()
        if not member_map:
            st.warning("Aún no hay deportistas registrados en el CRM.")
            return
        import running_strategy_ui
        running_strategy_ui.render_running_strategy_section(member_map, _nav_btn_css)
        return

    # ── NAVEGACIÓN PRINCIPAL CICLISMO EN 4 BOTONES GRANDES Y COLORIDOS ──
    if "rs_active_section" not in st.session_state:
        st.session_state["rs_active_section"] = "sim"
    active_section = st.session_state["rs_active_section"]

    nav_css = "<style>" + "".join([
        _nav_btn_css("rs_nav_sim", active_section == "sim", "linear-gradient(135deg,#00EEFF,#0088FF)", "#00EEFF", "rgba(0,238,255,0.14)", "#04101C", "rgba(0,238,255,0.5)"),
        _nav_btn_css("rs_nav_cda", active_section == "cda", "linear-gradient(135deg,#FF3B30,#FF6B6B)", "#FF5252", "rgba(255,59,48,0.16)", "#FFFFFF", "rgba(255,59,48,0.5)"),
        _nav_btn_css("rs_nav_comp", active_section == "comp", "linear-gradient(135deg,#FFD700,#FF9100)", "#FFD700", "rgba(255,215,0,0.14)", "#140E00", "rgba(255,215,0,0.5)"),
        _nav_btn_css("rs_nav_saved", active_section == "saved", "linear-gradient(135deg,#00FF88,#00B86B)", "#00FF88", "rgba(0,255,136,0.14)", "#031A0E", "rgba(0,255,136,0.5)"),
    ]) + "</style>"
    _render_html(nav_css)

    b_col1, b_col2, b_col3, b_col4 = st.columns(4)
    with b_col1:
        lbl_sim = ("● " if active_section == "sim" else "") + "⚙️ Simulador y Plan de Carrera"
        if st.button(lbl_sim, use_container_width=True, key="rs_nav_sim"):
            st.session_state["rs_active_section"] = "sim"
            st.rerun()
    with b_col2:
        lbl_cda = ("● " if active_section == "cda" else "") + "📐 Calibrar CdA Real"
        if st.button(lbl_cda, use_container_width=True, key="rs_nav_cda"):
            st.session_state["rs_active_section"] = "cda"
            st.rerun()
    with b_col3:
        lbl_comp = ("● " if active_section == "comp" else "") + "⚔️ Comparador 'Qué Pasa Si...'"
        if st.button(lbl_comp, use_container_width=True, key="rs_nav_comp"):
            st.session_state["rs_active_section"] = "comp"
            st.rerun()
    with b_col4:
        lbl_sav = ("● " if active_section == "saved" else "") + "🗂️ Escenarios Guardados"
        if st.button(lbl_sav, use_container_width=True, key="rs_nav_saved"):
            st.session_state["rs_active_section"] = "saved"
            st.rerun()

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)

    # ── 1. SELECCIÓN DE DEPORTISTA (+ CARGA DE RUTA GPX/FIT) ────────
    member_map = _load_active_members_map()

    if not member_map:
        st.warning("Aún no hay deportistas registrados en el CRM.")
        return

    route_file_bytes = None
    route_file_label = ""
    uploaded_custom_file = False
    laps = int(st.session_state.get("rs_laps", 1))

    # Mantener SIEMPRE la misma estructura de columnas para que Streamlit nunca desincronice el deportista seleccionado
    col_top1, col_top2 = st.columns([1.15, 1.85])

    member_labels = list(member_map.keys())
    with col_top1:
        # Sincronizar explícitamente el deportista seleccionado entre cambios de botón superior
        if "rs_athlete_select" in st.session_state and st.session_state["rs_athlete_select"] in member_map:
            st.session_state["rs_locked_athlete_lbl"] = st.session_state["rs_athlete_select"]
        saved_lbl = st.session_state.get("rs_locked_athlete_lbl")
        def_idx = member_labels.index(saved_lbl) if saved_lbl in member_labels else 0

        def _on_athlete_change():
            st.session_state["rs_locked_athlete_lbl"] = st.session_state["rs_athlete_select"]
            st.session_state.pop("rs_loaded_saved_sc_id", None)

        sel_member_label = st.selectbox(
            "🏃‍♂️ Seleccionar Deportista:",
            member_labels,
            index=def_idx,
            key="rs_athlete_select",
            on_change=_on_athlete_change,
        )
        st.session_state["rs_locked_athlete_lbl"] = sel_member_label
        member_id = member_map[sel_member_label]
        ath_defaults = _load_athlete_defaults(member_id)
        cda_pill = ""
        if ath_defaults.get("calibrated_cda"):
            cda_dt = ath_defaults["calibrated_cda_date"].strftime("%d/%m/%Y") if ath_defaults.get("calibrated_cda_date") else ""
            cda_pill = f" · 📐 **CdA Calibrado: `{ath_defaults['calibrated_cda']:.3f} m²`**" + (f" ({cda_dt})" if cda_dt else "")
        st.caption(
            f"📌 Datos del CRM: Peso **{ath_defaults['weight_kg']:.1f} kg** ({ath_defaults['weight_src']}) · "
            f"Estatura **{ath_defaults['height_cm']:.0f} cm** ({ath_defaults['height_src']}) · "
            f"FTP **{ath_defaults['ftp_w']:.0f} W** ({ath_defaults['ftp_src']}){cda_pill}"
        )

    # Consultar planes guardados de CICLISMO de ESTE deportista (para poder abrir Qué Pasa Si directo sobre ellos)
    with SessionLocal() as _s_chk:
        _all_saved_plans = (
            _s_chk.query(RaceStrategyScenario)
            .filter(RaceStrategyScenario.member_id == member_id)
            .order_by(RaceStrategyScenario.created_at.desc(), RaceStrategyScenario.id.desc())
            .all()
        )
    member_saved_plans = [
        p for p in _all_saved_plans
        if not (isinstance(_safe_parse_json(p.config_json, {}), dict) and _safe_parse_json(p.config_json, {}).get("sport") == "running")
    ]

    # Recuperar archivo persistido en sesión POR DEPORTISTA para no mezclar rutas entre atletas
    persisted_map = st.session_state.setdefault("rs_persisted_route_map", {})
    persisted = persisted_map.get(member_id)
    loaded_saved_sc = None

    if active_section in ("sim", "comp"):
        with col_top2:
            src_options = []
            if member_saved_plans:
                src_options.append("🗂️ Plan Guardado del Deportista")
            src_options.extend([
                "📂 Subir archivo FIT / GPX / TCX",
                "🌴 Demo: Ironman 70.3 Cartagena (90 km)",
                "⛰️ Demo: Circuito Rionegro–Las Palmas (45 km)",
            ])
            # Si venimos de pulsar "Abrir en Qué Pasa Si..." o estamos en Comparador sin archivo subido y tiene planes guardados
            want_saved_id = st.session_state.get("rs_loaded_saved_sc_id")
            default_src_idx = 0
            if member_saved_plans and (want_saved_id or (active_section == "comp" and not persisted)):
                default_src_idx = 0
            elif member_saved_plans and active_section == "sim" and persisted:
                default_src_idx = 1  # mantener archivo subido en simulador

            src_radio_key = f"rs_route_source_{member_id}_{active_section}_{1 if want_saved_id else 0}"
            route_source = st.radio(
                "🗺️ Origen del Circuito de Ciclismo:",
                src_options,
                index=default_src_idx,
                horizontal=True,
                key=src_radio_key,
            )

            if route_source.startswith("🗂️") and member_saved_plans:
                plan_pick_map = {
                    f"{p.scenario_name} ({format_hms(p.estimated_time_sec)} · {float(p.distance_km or 0):.0f} km)": p
                    for p in member_saved_plans
                }
                plan_keys = list(plan_pick_map.keys())
                plan_idx = 0
                if want_saved_id:
                    for pi, pk in enumerate(plan_keys):
                        if plan_pick_map[pk].id == want_saved_id:
                            plan_idx = pi
                            break
                sel_plan_lbl = st.selectbox(
                    f"📌 Plan guardado base de {ath_defaults['name']}:",
                    plan_keys,
                    index=plan_idx,
                    key=f"rs_saved_base_pick_{member_id}_{active_section}",
                )
                loaded_saved_sc = plan_pick_map[sel_plan_lbl]
            else:
                c_gpx1, c_gpx2 = st.columns([2.2, 1])
                with c_gpx1:
                    if route_source.startswith("📂"):
                        up_gpx = st.file_uploader(
                            "Sube la ruta o actividad (.fit, .gpx, .tcx o .zip de Garmin/Strava/Wahoo/TrainingPeaks):",
                            type=["fit", "gpx", "tcx", "zip", "gz"],
                            key=f"rs_gpx_uploader_{member_id}",
                        )
                        if up_gpx is not None:
                            route_file_bytes = up_gpx.getvalue()
                            route_file_label = up_gpx.name
                            uploaded_custom_file = True
                            persisted_map[member_id] = (route_file_bytes, route_file_label, True)
                        elif persisted and persisted[2]:
                            route_file_bytes, route_file_label, uploaded_custom_file = persisted
                            st.caption(f"📎 Circuito activo en memoria: **`{route_file_label}`**")
                            if st.button("✕ Quitar archivo en memoria", key=f"rs_clear_persisted_{member_id}"):
                                persisted_map.pop(member_id, None)
                                st.rerun()
                    elif "Cartagena" in route_source:
                        route_file_bytes, route_file_label = generate_demo_gpx("cartagena_703")
                        route_file_label = "demo_cartagena.gpx"
                        persisted_map[member_id] = (route_file_bytes, route_file_label, False)
                    else:
                        route_file_bytes, route_file_label = generate_demo_gpx("rionegro_climb")
                        route_file_label = "demo_rionegro.gpx"
                        persisted_map[member_id] = (route_file_bytes, route_file_label, False)
                with c_gpx2:
                    laps = st.number_input(
                        "🔁 Vueltas al circuito:",
                        min_value=1,
                        max_value=15,
                        value=1,
                        step=1,
                        help="Si tu archivo es una sola vuelta de un circuito, indica cuántas vueltas tiene la carrera.",
                        key="rs_laps",
                    )
    else:
        if persisted:
            route_file_bytes, route_file_label, uploaded_custom_file = persisted
        with col_top2:
            if active_section == "saved":
                st.caption("🗂️ Vista directa de planes guardados. Pulsa **⚔️ Abrir en 'Qué Pasa Si...'** sobre cualquier plan para simular mejoras al instante.")
            else:
                st.caption("📐 Calibra el CdA real usando cualquier carrera pasada (.FIT/.TCX/.GPX) sin necesidad de subir un circuito en el simulador.")

    # ── SECCIÓN DIRECTA: ESCENARIOS GUARDADOS (NO REQUIERE SUBIR ARCHIVO) ──
    if active_section == "saved":
        render_saved_scenarios_library(
            member_id=member_id,
            athlete_name=ath_defaults["name"],
            allow_delete=True,
            is_athlete_portal=False,
            key_prefix="admin_tab",
        )
        return

    # Parsear ruta (desde Plan Guardado seleccionado O desde archivo GPX/FIT/TCX)
    route_data = None
    route_sig = None
    saved_plan_ctx = None
    if loaded_saved_sc is not None:
        route_sig, route_data, saved_plan_ctx = _build_ctx_from_saved_scenario(loaded_saved_sc, ath_defaults)
    elif route_file_bytes:
        route_file_hash = hashlib.sha256(route_file_bytes).hexdigest()[:16]
        route_sig = f"{route_file_hash}_{route_file_label}_{int(laps)}"
        try:
            route_data = _cached_parse_route(route_file_hash, route_file_bytes, route_file_label, int(laps))
        except Exception as gpx_err:
            if active_section in ("sim", "comp"):
                st.error(f"⚠️ No se pudo leer el archivo `{route_file_label}`: {gpx_err}")
                return

    # Si estamos en Simulador o Comparador pero aún no hay circuito cargado ni plan guardado:
    if active_section in ("sim", "comp") and not route_data:
        st.info(
            "📂 Para usar el **Simulador** o el **Comparador 'Qué Pasa Si...'**, sube arriba un archivo `.fit`, `.gpx`, `.tcx` o `.zip` (o elige una ruta Demo). "
            f"Mientras tanto, aquí tienes los planes ya guardados de **{ath_defaults['name']}**:"
        )
        render_saved_scenarios_library(
            member_id=member_id,
            athlete_name=ath_defaults["name"],
            allow_delete=True,
            is_athlete_portal=False,
            key_prefix="admin_home",
        )
        return

    # Banner compacto del circuito + Planimetría/Altimetría (en Simulador y Comparador)
    if active_section in ("sim", "comp") and route_data:
        _render_html(
            f"""
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap:8px; margin:6px 0 12px 0;">
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Circuito</div>
                    <div style="color:#FFFFFF; font-size:0.86rem; font-weight:800; margin-top:2px;">{html.escape(str(route_data['route_name'])[:32])}</div>
                </div>
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Distancia Total</div>
                    <div style="color:#00EEFF; font-size:1.1rem; font-weight:900;">{route_data['total_distance_km']:.1f} km</div>
                </div>
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(255,82,82,0.32); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">⛰️ Desnivel Positivo</div>
                    <div style="color:#FF5252; font-size:1.1rem; font-weight:900;">+{route_data['elevation_gain_m']:.0f} m</div>
                </div>
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(56,189,248,0.32); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">⬇️ Desnivel Negativo</div>
                    <div style="color:#38BDF8; font-size:1.1rem; font-weight:900;">−{float(route_data.get('elevation_loss_m', 0)):.0f} m</div>
                </div>
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Altitud (Mín–Máx)</div>
                    <div style="color:#F8FAFC; font-size:1.0rem; font-weight:800;">{route_data['min_ele_m']:.0f}–{route_data['max_ele_m']:.0f} m</div>
                </div>
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(255,215,0,0.28); border-radius:10px; padding:8px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Pendiente Máx / Mín</div>
                    <div style="color:#FFD700; font-size:1.05rem; font-weight:900;">+{route_data['max_grade_pct']:.1f}% / {route_data['min_grade_pct']:.1f}%</div>
                </div>
            </div>
            """
        )

        # ── PLANIMETRÍA Y ALTIMETRÍA SINCRONIZADAS CON MAPA DE CALOR Y ZOOM EN VIVO ──
        _fig_elev_h, t_stats, _fig_plan_h = _cached_course_heatmaps(route_sig, route_data)

        with st.expander(
            "🗺️ Planimetría GPS y Altimetría del Circuito — Cursor Sincronizado & Zoom (+D / −D)",
            expanded=(active_section == "sim"),
        ):
            _render_html(
                f"""
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:8px;
                            background:rgba(14,18,28,0.9); border:1px solid rgba(255,255,255,0.09); border-radius:10px;
                            padding:8px 12px; margin-bottom:8px; font-size:0.75rem; color:#CBD5E1;">
                    <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
                        <span style="font-weight:800; color:#94A3B8;">MAPA DE CALOR:</span>
                        <span style="background:rgba(0,176,255,0.18); color:#00B0FF; padding:2px 7px; border-radius:6px; font-weight:700;">🔵 Descenso Veloz (≤-3.5%)</span>
                        <span style="background:rgba(0,230,118,0.16); color:#00E676; padding:2px 7px; border-radius:6px; font-weight:700;">🟢 Bajada Rápida</span>
                        <span style="background:rgba(100,116,139,0.22); color:#CBD5E1; padding:2px 7px; border-radius:6px; font-weight:700;">⚪ Llano</span>
                        <span style="background:rgba(255,214,0,0.16); color:#FFD600; padding:2px 7px; border-radius:6px; font-weight:700;">🟡 Repecho</span>
                        <span style="background:rgba(255,138,0,0.18); color:#FF8A00; padding:2px 7px; border-radius:6px; font-weight:700;">🟠 Subida (2.5–5%)</span>
                        <span style="background:rgba(255,46,67,0.2); color:#FF4D5E; padding:2px 7px; border-radius:6px; font-weight:700;">🔴 Subida Fuerte (≥5%)</span>
                    </div>
                    <div style="display:flex; flex-wrap:wrap; gap:10px; font-weight:700;">
                        <span style="color:#FF6B6B;">⛰️ Subida: {t_stats.get('climb_km', 0)} km</span>
                        <span style="color:#94A3B8;">➡️ Llano: {t_stats.get('flat_km', 0)} km</span>
                        <span style="color:#00E676;">⬇️ Bajada: {t_stats.get('descent_km', 0)} km</span>
                        <span style="color:#00B0FF;">🚀 Desc. rápido: {t_stats.get('fast_down_km', 0)} km</span>
                    </div>
                </div>
                """
            )
            render_synced_elevation_planimetry_widget(route_data, height=475)

    sim_ctx_key = f"rs_sim_ctx_{member_id}_{route_sig}" if route_sig else None

    # ═════════════════════════════════════════════════════════════════
    #  SECCIÓN 1: SIMULADOR COMPLETO ESTILO BEST BIKE SPLIT
    # ═════════════════════════════════════════════════════════════════
    if active_section == "sim":
        col_cfg1, col_cfg2, col_cfg3 = st.columns(3)

        # ── COL 1: BICICLETA, COMPONENTES Y CDA ─────────────────────
        with col_cfg1:
            st.markdown("#### 🚲 Bicicleta, Componentes y CdA")
            bike_type = st.selectbox("Tipo de Bicicleta:", list(BIKE_TYPES.keys()), key="rs_bike_type")
            wheels = st.selectbox("Juego de Ruedas:", list(WHEEL_SETS.keys()), index=1, key="rs_wheels")
            helmet = st.selectbox("Casco:", list(HELMETS.keys()), index=0 if "TT" in bike_type else 1, key="rs_helmet")
            tires = st.selectbox("Llantas / Cubiertas (Crr):", list(TIRES_CRR.keys()), index=1, key="rs_tires")
            drivetrain = st.selectbox("Transmisión:", list(DRIVETRAINS.keys()), index=1, key="rs_drivetrain")
            apparel = st.selectbox("Indumentaria:", list(APPAREL_OPTIONS.keys()), index=0 if "TT" in bike_type else 1, key="rs_apparel")
            hydration = st.selectbox("Hidratación / Carga:", list(HYDRATION_SETUP.keys()), index=0 if "TT" in bike_type else 1, key="rs_hydration")
            surface = st.selectbox("Superficie del Asfalto:", list(ROAD_SURFACES.keys()), index=1, key="rs_surface")

        # ── COL 2: FECHA, HORA, CLIMA REAL/HISTÓRICO Y VIENTO ───────
        with col_cfg2:
            st.markdown("#### 🌬️ Clima de Competencia y Viento")

            # Coordenadas del centroide real de la ruta GPX
            segs_all = route_data["segments"]
            route_lat = sum(seg["lat"] for seg in segs_all) / max(1, len(segs_all))
            route_lon = sum(seg["lon"] for seg in segs_all) / max(1, len(segs_all))

            c_dt1, c_dt2 = st.columns([1.25, 1])
            with c_dt1:
                race_date = st.date_input(
                    "📅 Fecha de competencia:",
                    value=date.today() + timedelta(days=3),
                    min_value=date(2020, 1, 1),
                    max_value=date(2030, 12, 31),
                    key="rs_race_date",
                )
            with c_dt2:
                hour_opts = [f"{h:02d}:00" for h in range(5, 19)]
                race_time_str = st.selectbox(
                    "⏰ Hora salida bici:",
                    options=hour_opts,
                    index=2,  # 07:00
                    key="rs_race_hour",
                )
                race_start_hour = int(race_time_str.split(":")[0])

            route_sig = f"{route_data['route_name']}_{route_lat:.3f}_{route_lon:.3f}"
            if st.button(
                "🌦️ Buscar Clima Real del Circuito (Fecha y Hora)",
                type="secondary",
                use_container_width=True,
                key="rs_fetch_weather_btn",
            ):
                with st.spinner("Consultando modelos meteorológicos (Open-Meteo ECMWF/GFS/ERA5) para el circuito..."):
                    w_res = fetch_race_weather(
                        lat=route_lat,
                        lon=route_lon,
                        race_date=race_date,
                        start_hour=race_start_hour,
                        duration_hours=8,
                    )
                if w_res.get("ok") and w_res.get("summary"):
                    ws = w_res["summary"]
                    st.session_state["rs_weather_data"] = w_res
                    st.session_state["rs_weather_sig"] = route_sig
                    st.session_state["rs_temp"] = max(5, min(42, int(round(ws["temp_c"]))))
                    st.session_state["rs_hum"] = max(15, min(100, int(round(ws["humidity_pct"] / 5.0) * 5)))
                    st.session_state["rs_wind_spd"] = max(0, min(50, int(round(ws["wind_kmh"]))))
                    st.session_state["rs_wind_dir_lbl"] = "Personalizado (grados)"
                    st.session_state["rs_wind_deg_custom"] = int(round(ws["wind_deg"] / 5.0) * 5) % 360
                    st.session_state["rs_use_hourly_weather"] = True
                    st.toast("Clima cargado para el circuito", icon="🌦️")
                    st.rerun()
                else:
                    st.error(f"⚠️ No se pudo obtener el clima automáticamente: {w_res.get('error', 'sin conexión')}. Puedes ajustar los controles manualmente.")

            weather_data = st.session_state.get("rs_weather_data")
            weather_matches_route = bool(
                weather_data
                and weather_data.get("ok")
                and st.session_state.get("rs_weather_sig") == route_sig
            )

            active_hourly_weather = None
            if weather_matches_route:
                ws = weather_data["summary"]
                rain_txt = f" · 🌧️ Prob. lluvia: **{ws['precip_prob_pct']}%**" if ws.get("precip_prob_pct") is not None else ""
                press_txt = f" · Presión: `{ws['pressure_hpa']} hPa`" if ws.get("pressure_hpa") else ""
                _render_html(
                    f"""
                    <div style="background:rgba(0,238,255,0.08); border:1px solid rgba(0,238,255,0.38);
                                border-radius:10px; padding:9px 11px; margin:4px 0 8px 0;">
                        <div style="color:#00EEFF; font-size:0.75rem; font-weight:800;">
                            {weather_data['source_label']}
                        </div>
                        <div style="color:#E2E8F0; font-size:0.79rem; margin-top:4px; line-height:1.45;">
                            {ws['condition']} · <b>{ws['temp_c']}°C</b> (rango {ws['temp_min_c']}–{ws['temp_max_c']}°C) · <b>{ws['humidity_pct']}% HR</b><br>
                            💨 Viento: <b>{ws['wind_kmh']} km/h</b> (ráfagas {ws['wind_gusts_kmh']} km/h) desde <b>{ws['wind_compass']} ({ws['wind_deg']:.0f}°)</b>{press_txt}
                        </div>
                    </div>
                    """
                )
                if rain_txt:
                    st.caption(f"📍 GPS circuito ({weather_data['lat']}, {weather_data['lon']}){rain_txt}")

                use_hourly = st.checkbox(
                    "⏱️ Simular evolución climática hora a hora (como Best Bike Split)",
                    value=st.session_state.get("rs_use_hourly_weather", True),
                    key="rs_use_hourly_weather",
                    help="Actualiza temperatura, humedad, velocidad y dirección del viento automáticamente cada hora a medida que avanza la carrera.",
                )
                if use_hourly:
                    active_hourly_weather = weather_data.get("hourly")

                with st.expander("🕒 Ver pronóstico hora a hora del circuito", expanded=False):
                    df_hw = pd.DataFrame([
                        {
                            "Hora": h["time_label"],
                            "Clima": h["condition"],
                            "Temp (°C)": h["temp_c"],
                            "HR (%)": int(h["humidity_pct"]),
                            "Viento (km/h)": h["wind_kmh"],
                            "Ráfagas": h["wind_gusts_kmh"],
                            "Dirección": f"{h['wind_compass']} ({h['wind_deg']:.0f}°)",
                        }
                        for h in weather_data.get("hourly", [])
                    ])
                    st.dataframe(df_hw, use_container_width=True, hide_index=True)

            temp_c = st.slider("🌡️ Temperatura (°C):", min_value=5, max_value=42, value=24, step=1, key="rs_temp")
            humidity_pct = st.slider("💧 Humedad relativa (%):", min_value=15, max_value=100, value=65, step=5, key="rs_hum")
            wind_kmh = st.slider("💨 Velocidad del viento (km/h):", min_value=0, max_value=50, value=12, step=1, key="rs_wind_spd")
            wind_dir_lbl = st.selectbox(
                "🧭 Dirección desde donde sopla el viento:",
                list(WIND_DIRECTIONS.keys()) + ["Personalizado (grados)"],
                index=1,
                key="rs_wind_dir_lbl",
            )
            if wind_dir_lbl == "Personalizado (grados)":
                wind_deg = float(st.slider("Ángulo del viento (0°=N, 90°=E, 180°=S, 270°=O):", 0, 355, 45, 5, key="rs_wind_deg_custom"))
            else:
                wind_deg = WIND_DIRECTIONS[wind_dir_lbl]

            rho_preview = calc_air_density(
                route_data["avg_ele_m"],
                temp_c,
                humidity_pct,
                surface_pressure_hpa=(weather_data["summary"].get("pressure_hpa") if weather_matches_route else None),
            )
            st.caption(
                f"🌬️ Densidad del aire (ρ): `{rho_preview:.4f} kg/m³` · Altitud media: **{route_data['avg_ele_m']:.0f} m** · Viento desde **{deg_to_compass(wind_deg)} ({wind_deg:.0f}°)**"
            )

            st.markdown("##### 📐 Ajuste fino de CdA y Peso de Bici")
            cda_ver = st.session_state.get(f"rs_cda_ver_{member_id}", 0)
            has_cal_cda = bool(ath_defaults.get("calibrated_cda"))
            default_cda_val = float(ath_defaults["calibrated_cda"]) if has_cal_cda else 0.240
            override_label = (
                f"📐 Usar CdA calibrado/manual (guardado: {default_cda_val:.3f} m²)"
                if has_cal_cda
                else "Ingresar CdA o peso exacto manualmente"
            )
            override_aero = st.checkbox(
                override_label,
                value=has_cal_cda,
                key=f"rs_override_aero_{member_id}_v{cda_ver}",
            )
            manual_cda = None
            manual_bike_kg = None
            if override_aero:
                manual_cda = st.number_input(
                    "CdA Exacto (m²):",
                    min_value=0.140,
                    max_value=0.550,
                    value=default_cda_val,
                    step=0.005,
                    format="%.3f",
                    key=f"rs_manual_cda_{member_id}_v{cda_ver}",
                )
                manual_bike_kg = st.number_input(
                    "Peso bici + agua + equipo (kg, 0 = estimado por componentes):",
                    min_value=0.0,
                    max_value=18.0,
                    value=0.0,
                    step=0.2,
                    key=f"rs_manual_bkg_{member_id}_v{cda_ver}",
                )
                if manual_bike_kg <= 3.0:
                    manual_bike_kg = None

        # ── COL 3: DEPORTISTA Y ESTRATEGIA DE POTENCIA ──────────────
        with col_cfg3:
            st.markdown("#### ⚡ Atleta y Estrategia de Potencia")
            preset_lbl = st.selectbox("🎯 Preset de Distancia / Prueba:", list(RACE_PRESETS.keys()), index=3, key="rs_preset")
            preset_cfg = RACE_PRESETS[preset_lbl]

            c_ath1, c_ath2 = st.columns(2)
            with c_ath1:
                rider_weight = st.number_input(
                    "⚖️ Peso atleta (kg):",
                    min_value=35.0,
                    max_value=150.0,
                    value=float(ath_defaults["weight_kg"]),
                    step=0.5,
                    key=f"rs_rw_{member_id}",
                )
            with c_ath2:
                rider_height = st.number_input(
                    "📏 Estatura (cm):",
                    min_value=135.0,
                    max_value=215.0,
                    value=float(ath_defaults["height_cm"]),
                    step=1.0,
                    key=f"rs_rh_{member_id}",
                )

            ftp_w = st.number_input(
                "🔥 FTP del Atleta (W):",
                min_value=80.0,
                max_value=550.0,
                value=float(ath_defaults["ftp_w"]),
                step=5.0,
                key=f"rs_ftp_{member_id}",
            )

            default_if = preset_cfg["target_if"] if preset_cfg else 0.82
            default_cap = int(round((preset_cfg["max_climb_pct"] if preset_cfg else 1.08) * 100))

            target_if = st.slider(
                "🎯 Factor de Intensidad objetivo (IF):",
                min_value=0.55,
                max_value=1.05,
                value=float(default_if),
                step=0.01,
                help=f"NP Objetivo ≈ {ftp_w * default_if:.0f} W",
                key=f"rs_if_{preset_lbl}",
            )

            strategy_map = {
                "⚡ Óptimo AlphaX (Variable por Pendiente y Viento)": "optimal",
                "📏 Potencia Constante (Isopotencia)": "constant",
                "🛡️ Conservador / Negative Split (-3.5% → +3.5%)": "negative_split",
            }
            strat_label = st.selectbox("🧠 Modo de Pacing:", list(strategy_map.keys()), key="rs_strategy_mode")
            strategy_code = strategy_map[strat_label]

            c_cap1, c_cap2 = st.columns(2)
            with c_cap1:
                max_climb_pct = st.slider("Techo en subidas (% FTP):", 85, 130, default_cap, 1, key=f"rs_cap_{preset_lbl}") / 100.0
            with c_cap2:
                max_desc_kmh = st.slider("Vel. máx bajada (km/h):", 45, 85, 68, 1, key="rs_max_desc")

            split_step_km = st.selectbox("Intervalo de Splits (km):", [2.0, 5.0, 10.0, 15.0], index=1, key="rs_split_km")

        # Calcular especificaciones físicas del equipo
        equipment = estimate_equipment_specs(
            bike_type=bike_type,
            wheels=wheels,
            helmet=helmet,
            tires=tires,
            drivetrain=drivetrain,
            apparel=apparel,
            hydration=hydration,
            surface=surface,
            rider_height_cm=rider_height,
            manual_cda=manual_cda,
            manual_bike_weight_kg=manual_bike_kg,
        )

        # Ejecutar simulación principal (con caché instantáneo y clima horario dinámico si está activo)
        sim = _sim_fast(
            route_sig=route_sig,
            route_data=route_data,
            rider_weight_kg=rider_weight,
            ftp_w=ftp_w,
            target_if=target_if,
            equipment=equipment,
            temp_c=float(temp_c),
            humidity_pct=float(humidity_pct),
            wind_kmh=float(wind_kmh),
            wind_deg=float(wind_deg),
            strategy=strategy_code,
            max_climb_pct=max_climb_pct,
            max_desc_kmh=float(max_desc_kmh),
            split_step_km=float(split_step_km),
            hourly_weather=active_hourly_weather,
        )
        s = sim["summary"]
        nut = sim["nutrition"]

        # Persistir contexto completo de la simulación base para que el Comparador "Qué Pasa Si..." lo use al instante
        if sim_ctx_key:
            st.session_state[sim_ctx_key] = {
                "sim": sim,
                "s": s,
                "nut": nut,
                "equipment": equipment,
                "rider_weight": rider_weight,
                "rider_height": rider_height,
                "ftp_w": ftp_w,
                "target_if": target_if,
                "temp_c": temp_c,
                "humidity_pct": humidity_pct,
                "wind_kmh": wind_kmh,
                "wind_deg": wind_deg,
                "strategy_code": strategy_code,
                "strat_label": strat_label,
                "max_climb_pct": max_climb_pct,
                "max_desc_kmh": max_desc_kmh,
                "split_step_km": split_step_km,
                "active_hourly_weather": active_hourly_weather,
                "bike_type": bike_type,
                "wheels": wheels,
                "helmet": helmet,
                "tires": tires,
                "drivetrain": drivetrain,
                "apparel": apparel,
                "hydration": hydration,
                "surface": surface,
                "race_date": race_date,
                "race_time_str": race_time_str,
                "weather_data": weather_data,
                "weather_matches_route": weather_matches_route,
            }

        # ── TARJETAS HERO DE RESULTADO DE SIMULACIÓN ────────────────
        st.markdown("---")
        _render_html(
            f"""
            <div style="display:grid; grid-template-columns: repeat(6, 1fr); gap:10px; margin-bottom:14px;">
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid #00EEFF; border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">⏱️ Tiempo Estimado</div>
                    <div style="color:#00EEFF; font-size:1.45rem; font-weight:900; margin-top:4px;">{s['total_time_str']}</div>
                    <div style="color:#94A3B8; font-size:0.7rem;">{s['distance_km']:.1f} km · +{s['elevation_gain_m']:.0f} m</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,215,0,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">🚀 Velocidad Media</div>
                    <div style="color:#FFD700; font-size:1.45rem; font-weight:900; margin-top:4px;">{s['avg_speed_kmh']:.1f} <span style="font-size:0.8rem;">km/h</span></div>
                    <div style="color:#94A3B8; font-size:0.7rem;">Máx: {s['max_speed_kmh']:.1f} km/h</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(0,255,136,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">⚡ Potencia NP / AP</div>
                    <div style="color:#00FF88; font-size:1.4rem; font-weight:900; margin-top:4px;">{s['norm_power_w']:.0f} <span style="font-size:0.8rem;">W</span></div>
                    <div style="color:#94A3B8; font-size:0.7rem;">AP: {s['avg_power_w']:.0f} W · {s['w_per_kg_np']:.2f} W/kg</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(165,94,234,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">🎯 IF · VI · TSS</div>
                    <div style="color:#C084FC; font-size:1.4rem; font-weight:900; margin-top:4px;">{s['intensity_factor']:.2f} <span style="font-size:0.85rem;">IF</span></div>
                    <div style="color:#94A3B8; font-size:0.7rem;">VI: {s['vi']:.2f} · TSS: {s['tss']:.0f}</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,107,107,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">🔋 Gasto Energético</div>
                    <div style="color:#FF8787; font-size:1.4rem; font-weight:900; margin-top:4px;">{s['work_kj']:.0f} <span style="font-size:0.8rem;">kJ</span></div>
                    <div style="color:#94A3B8; font-size:0.7rem;">≈ {s['work_kj']:.0f} kcal</div>
                </div>
                <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(56,189,248,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#8E9BAE; font-size:0.68rem; font-weight:800; text-transform:uppercase;">🌬️ CdA & Masa Total</div>
                    <div style="color:#38BDF8; font-size:1.35rem; font-weight:900; margin-top:4px;">{s['cda']:.3f} <span style="font-size:0.78rem;">m²</span></div>
                    <div style="color:#94A3B8; font-size:0.7rem;">{s['total_mass_kg']:.1f} kg · Crr {s['crr']:.4f}</div>
                </div>
            </div>
            """
        )

        # Gráfica principal interactiva: Plan de Pacing con Zoom en vivo (Distancia, Velocidad, Potencia, % FTP y +D/-D)
        render_interactive_pacing_zoom_widget(sim, sport="cycling", ftp_or_rcp=ftp_w, height=535)
        st.plotly_chart(_build_speed_wind_chart(sim["segments"]), use_container_width=True)

        # Fila de Mapa GPS + Desglose de Resistencias y Nutrición
        col_vis1, col_vis2 = st.columns([1.35, 1])
        with col_vis1:
            def _render_tab1_gps_map():
                c_mopt1, c_mopt2 = st.columns([2.2, 1])
                with c_mopt1:
                    map_color = st.radio(
                        "Colorear mapa del circuito por:",
                        ["🌬️ Viento relativo", "⛰️ Desnivel (Mapa de Calor)", "⚡ Potencia objetivo (W)"],
                        horizontal=True,
                        key="rs_map_color",
                    )
                with c_mopt2:
                    show_wind_arrows = st.checkbox(
                        "🧭 Flechas de viento",
                        value=True,
                        help="Muestra las flechas con la dirección real hacia donde sopla el viento y la velocidad del viento relativo (+Contra / -Favor) a lo largo del recorrido.",
                        key="rs_show_wind_arrows",
                    )
                _mc = "grade" if "Desnivel" in map_color else ("power" if "Potencia" in map_color else "wind")
                st.plotly_chart(
                    _build_gps_map_chart(
                        sim["segments"],
                        color_mode=_mc,
                        wind_from_deg=wind_deg,
                        wind_kmh=wind_kmh,
                        show_wind_arrows=show_wind_arrows,
                    ),
                    use_container_width=True,
                )

            if hasattr(st, "fragment"):
                st.fragment(_render_tab1_gps_map)()
            else:
                _render_tab1_gps_map()

        with col_vis2:
            st.plotly_chart(_build_resistance_donut(sim["resistance"]), use_container_width=True)
            _render_html(
                f"""
                <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,255,136,0.35); border-radius:12px; padding:12px 14px; margin-top:4px;">
                    <div style="color:#00FF88; font-weight:800; font-size:0.86rem; margin-bottom:6px;">
                        🥤 Plan Nutricional e Hidratación Sugerido ({s['avg_temp_c']}°C · {s['avg_humidity_pct']}% HR{" · Dinámico" if s.get("dynamic_weather") else ""})
                    </div>
                    <div style="color:#E2E8F0; font-size:0.82rem; line-height:1.55;">
                        • <b>Carbohidratos:</b> <code>{nut['cho_g_per_h']} g/h</code> → <b>Total: {nut['cho_total_g']} g</b><br>
                        • <b>Líquidos:</b> <code>{nut['fluid_ml_per_h']} mL/h</code> → <b>Total: {nut['fluid_total_l']} L</b><br>
                        • <b>Sodio (Electrolitos):</b> <code>{nut['sodium_mg_per_h']} mg/h</code> → <b>Total: {nut['sodium_total_mg']} mg</b>
                    </div>
                </div>
                """
            )

        # Tabla de Splits + Descarga CSV
        st.markdown("---")
        st.markdown("### 📋 Plan de Splits (Hoja de Ruta de Potencia para Carrera)")
        df_splits = pd.DataFrame(sim["splits"])
        st.dataframe(df_splits, use_container_width=True, hide_index=True)

        c_dl, c_save1, c_save2 = st.columns([1, 1.8, 1])
        with c_dl:
            csv_bytes = df_splits.to_csv(index=False).encode("utf-8-sig")
            safe_file = f"plan_carrera_{ath_defaults['name'].replace(' ', '_')}_{route_data['total_distance_km']:.0f}km.csv"
            st.download_button(
                "⬇️ Descargar Splits (CSV)",
                data=csv_bytes,
                file_name=safe_file,
                mime="text/csv",
                use_container_width=True,
                key="rs_dl_csv",
            )

        default_scen_name = (
            f"{route_data['route_name'][:28]} · {bike_type.split('(')[0].strip()} · "
            f"IF {target_if:.2f} · Viento {wind_kmh} km/h"
        )
        with c_save1:
            scen_name_input = st.text_input(
                "Nombre del escenario para guardar y comparar:",
                value=default_scen_name,
                label_visibility="collapsed",
                key="rs_save_name",
            )
        with c_save2:
            if st.button("💾 Guardar Escenario", type="primary", use_container_width=True, key="rs_save_btn"):
                try:
                    cfg_dump = {
                        "race_date": race_date.strftime("%Y-%m-%d"),
                        "race_start_time": race_time_str,
                        "weather_source": weather_data["source_label"] if weather_matches_route else "Manual",
                        "dynamic_hourly_weather": bool(active_hourly_weather),
                        "bike_type": bike_type,
                        "wheels": wheels,
                        "helmet": helmet,
                        "tires": tires,
                        "drivetrain": drivetrain,
                        "apparel": apparel,
                        "hydration": hydration,
                        "surface": surface,
                        "temp_c": temp_c,
                        "humidity_pct": humidity_pct,
                        "wind_kmh": wind_kmh,
                        "wind_deg": wind_deg,
                        "rider_weight_kg": rider_weight,
                        "rider_height_cm": rider_height,
                        "ftp_w": ftp_w,
                        "target_if": target_if,
                        "strategy": strat_label,
                        "work_kj": s["work_kj"],
                        "w_per_kg_np": s["w_per_kg_np"],
                        "vi": s["vi"],
                        "resistance": sim["resistance"],
                        "nutrition": nut,
                        "route_data_gz": _compress_route_data(route_data),
                    }
                    with SessionLocal() as s_db:
                        new_sc = RaceStrategyScenario(
                            member_id=member_id,
                            scenario_name=(scen_name_input.strip() or default_scen_name),
                            route_name=route_data["route_name"],
                            distance_km=s["distance_km"],
                            elevation_gain_m=s["elevation_gain_m"],
                            estimated_time_sec=s["total_time_s"],
                            avg_speed_kmh=s["avg_speed_kmh"],
                            avg_power_w=s["avg_power_w"],
                            norm_power_w=s["norm_power_w"],
                            intensity_factor=s["intensity_factor"],
                            tss=s["tss"],
                            cda=s["cda"],
                            total_mass_kg=s["total_mass_kg"],
                            config_json=json.dumps(cfg_dump, ensure_ascii=False),
                            splits_json=json.dumps(sim["splits"], ensure_ascii=False),
                            created_at=datetime.now(),
                        )
                        s_db.add(new_sc)
                        s_db.commit()
                    st.success(f"✅ Escenario guardado para **{ath_defaults['name']}**. Ya puedes compararlo en la pestaña **⚔️ Comparador**.")
                    st.toast("Escenario de carrera guardado", icon="🏁")
                except Exception as save_err:
                    st.error(f"Error guardando escenario: {save_err}")

    # ═════════════════════════════════════════════════════════════════
    #  SECCIÓN 2: CALIBRAR CdA REAL DESDE ARCHIVO O RESUMEN DE COMPETENCIA
    # ═════════════════════════════════════════════════════════════════
    elif active_section == "cda":
        st.subheader(f"📐 Calibración Real de CdA (m²) — {ath_defaults['name']}")
        _render_html(
            """
            <div style="background:rgba(0,238,255,0.07); border:1px solid rgba(0,238,255,0.32);
                        border-radius:12px; padding:12px 15px; margin-bottom:14px;">
                <div style="color:#00EEFF; font-weight:800; font-size:0.9rem;">
                    🧪 Estima el CdA real desde una competencia pasada (ej. Ironman 70.3 Cartagena)
                </div>
                <div style="color:#CBD5E1; font-size:0.82rem; margin-top:4px; line-height:1.45;">
                    Sube el archivo de la carrera (<b>.FIT</b> de Garmin/Wahoo, <b>.TCX</b> o <b>.GPX</b> con potencia) o usa el circuito GPX + tiempo y vatios oficiales.
                    Consultamos automáticamente el <b>clima real histórico (ERA5)</b> de ese día y hora exacta en esas coordenadas para despejar el <b>CdA real (m²)</b> del atleta.
                </div>
            </div>
            """
        )

        cal_mode = st.radio(
            "¿Cómo deseas calibrar el CdA? (Totalmente independiente del circuito del simulador):",
            [
                "📂 Subir archivo de OTRA carrera mía (.FIT, .TCX, .GPX, .ZIP con potencia)",
                "📝 Resumen oficial (Tiempo + Potencia Media) sobre un archivo de ruta pasado",
            ],
            horizontal=True,
            key=f"cda_mode_{member_id}",
        )

        cda_res_key = f"cda_cal_result_{member_id}"

        # Parámetros comunes de masa y rodamiento de aquella competencia
        c_m1, c_m2, c_m3, c_m4 = st.columns(4)
        with c_m1:
            cal_rider_w = st.number_input(
                "⚖️ Peso atleta ese día (kg):",
                min_value=35.0,
                max_value=150.0,
                value=float(ath_defaults["weight_kg"]),
                step=0.5,
                key=f"cda_rw_{member_id}",
            )
        with c_m2:
            cal_bike_w = st.number_input(
                "🚲 Peso bici + agua + equipo (kg):",
                min_value=5.5,
                max_value=18.0,
                value=9.5,
                step=0.2,
                key=f"cda_bw_{member_id}",
            )
        with c_m3:
            cal_tire_lbl = st.selectbox(
                "🛞 Llantas usadas:",
                list(TIRES_CRR.keys()),
                index=1,
                key=f"cda_tire_{member_id}",
            )
            cal_surf_lbl = st.selectbox(
                "🛣️ Asfalto de esa carrera:",
                list(ROAD_SURFACES.keys()),
                index=1,
                key=f"cda_surf_{member_id}",
            )
        with c_m4:
            cal_dt_lbl = st.selectbox(
                "⚙️ Transmisión usada:",
                list(DRIVETRAINS.keys()),
                index=1,
                key=f"cda_dt_{member_id}",
            )
            cal_bike_lbl = st.selectbox(
                "🏷️ Bici usada (etiqueta):",
                list(BIKE_TYPES.keys()),
                index=0,
                key=f"cda_bikelbl_{member_id}",
            )

        cal_crr = TIRES_CRR[cal_tire_lbl]["crr"] * ROAD_SURFACES[cal_surf_lbl]
        cal_eff = DRIVETRAINS[cal_dt_lbl]["efficiency"]
        cal_total_mass = cal_rider_w + cal_bike_w

        # Opción de clima histórico automático vs manual
        use_auto_era5 = st.checkbox(
            "🌦️ Buscar automáticamente el clima real histórico (ERA5) del día y hora de esa carrera",
            value=True,
            key=f"cda_auto_era5_{member_id}",
        )
        man_t, man_h, man_ws, man_wd = 26.0, 70.0, 12.0, 45.0
        if not use_auto_era5:
            cmw1, cmw2, cmw3, cmw4 = st.columns(4)
            with cmw1:
                man_t = float(st.number_input("Temp. ese día (°C):", 5.0, 45.0, 26.0, 1.0, key=f"cda_mt_{member_id}"))
            with cmw2:
                man_h = float(st.number_input("Humedad (%):", 10.0, 100.0, 70.0, 5.0, key=f"cda_mh_{member_id}"))
            with cmw3:
                man_ws = float(st.number_input("Viento (km/h):", 0.0, 55.0, 12.0, 1.0, key=f"cda_mws_{member_id}"))
            with cmw4:
                man_wd = float(st.number_input("Dirección viento (0-359°):", 0.0, 359.0, 45.0, 5.0, key=f"cda_mwd_{member_id}"))

        # ── MODO 1: ARCHIVO INDEPENDIENTE DE COMPETENCIA (.FIT / .TCX / .GPX / .ZIP) ──
        if cal_mode.startswith("📂"):
            st.caption(
                "💡 Este cargador es **100% independiente** del circuito del simulador de arriba: "
                "puedes subir cualquier carrera pasada tuya (ej. tu 70.3 del año pasado) para estimar tu CdA real y luego aplicarlo al nuevo circuito."
            )
            act_file = st.file_uploader(
                "📂 Sube TU archivo de carrera o entreno con potencia (.fit, .tcx, .gpx, .zip o .gz):",
                type=["fit", "tcx", "gpx", "zip", "gz"],
                key=f"cda_file_up_{member_id}",
            )

            use_top_file = False
            if act_file is None and uploaded_custom_file and route_file_bytes:
                use_top_file = st.checkbox(
                    f"♻️ Opcional: usar en su lugar el mismo archivo cargado arriba (`{route_file_label}`)",
                    value=False,
                    key=f"cda_reuse_top_v2_{member_id}",
                )

            act_bytes = None
            act_name = None
            if act_file is not None:
                act_bytes = act_file.getvalue()
                act_name = act_file.name
            elif use_top_file:
                act_bytes = route_file_bytes
                act_name = route_file_label

            if act_bytes is not None and act_name:
                try:
                    act_hash = hashlib.sha256(act_bytes).hexdigest()[:16]
                    activity = _cached_parse_activity(act_hash, act_bytes, act_name)
                    act_dt = activity["activity_date"] or (date.today() - timedelta(days=30))
                    pwr_txt = (
                        f"⚡ Potencia: **{activity['avg_power_w']:.0f} W** (NP: {activity['norm_power_w']:.0f} W)"
                        if activity["has_power"]
                        else "⚠️ Sin canal de potencia segundo a segundo (puedes ingresar tu Potencia Media abajo)"
                    )
                    if activity.get("is_multisport"):
                        st.success(
                            f"🏊‍♂️➡️🚴‍♂️➡️🏃‍♂️ **Archivo Multideporte (Triatlón) detectado:** se aisló automáticamente únicamente el segmento de **Ciclismo** "
                            f"({activity['distance_km']:.1f} km · {activity['moving_time_str']})."
                        )

                    # Recorte manual opcional por km (por si el archivo trae bici+carrera pegados o tramos neutralizados)
                    max_km_act = max(2.0, float(activity["distance_km"]))
                    km_lo, km_hi = st.slider(
                        "✂️ Recortar tramo a analizar (Km inicio – Km fin):",
                        min_value=0.0,
                        max_value=round(max_km_act, 1),
                        value=(0.0, round(max_km_act, 1)),
                        step=1.0,
                        help="Si tu archivo tiene la carrera a pie pegada al final o tráfico inicial, recorta exactamente los kilómetros de bici (ej. 0 a 180 km).",
                        key=f"cda_trim_{member_id}_{act_name}_{round(max_km_act, 1)}",
                    )
                    if km_lo > 0.1 or km_hi < (max_km_act - 0.5):
                        activity = trim_activity_range(activity, km_lo, km_hi)
                        pwr_txt = (
                            f"⚡ Potencia: **{activity['avg_power_w']:.0f} W** (NP: {activity['norm_power_w']:.0f} W)"
                            if activity["has_power"]
                            else "⚠️ Sin canal de potencia segundo a segundo (puedes ingresar tu Potencia Media abajo)"
                        )

                    sensor_t_txt = f" · 🌡️ Sensor bici: **{activity['sensor_avg_temp_c']}°C**" if activity.get("sensor_avg_temp_c") is not None else ""
                    st.info(
                        f"📄 **{activity['name']}** · 📅 Fecha: **{act_dt.strftime('%d/%m/%Y')}** · "
                        f"Distancia: **{activity['distance_km']:.1f} km** · Tiempo en movimiento: **{activity['moving_time_str']}** "
                        f"({activity['avg_speed_kmh']:.1f} km/h) · {pwr_txt}{sensor_t_txt}"
                    )

                    fallback_avg_p = None
                    if not activity["has_power"]:
                        fallback_avg_p = st.number_input(
                            "⚡ Ingresa la Potencia Media (AP) de esa carrera (W):",
                            min_value=60.0,
                            max_value=500.0,
                            value=210.0,
                            step=5.0,
                            key=f"cda_fb_pwr_{member_id}",
                        )

                    if st.button(
                        "📐 Calcular CdA Real desde este Archivo",
                        type="primary",
                        use_container_width=True,
                        key=f"cda_run_file_{member_id}",
                    ):
                        with st.spinner("Consultando clima histórico ERA5 y resolviendo física inversa de CdA..."):
                            w_hist = None
                            t_used, h_used, ws_used, wd_used = man_t, man_h, man_ws, man_wd
                            hourly_used = None
                            if use_auto_era5:
                                w_hist = fetch_race_weather(
                                    lat=activity["lat"],
                                    lon=activity["lon"],
                                    race_date=act_dt,
                                    start_hour=activity["start_hour"],
                                    duration_hours=max(3, int(math.ceil(activity["moving_time_s"] / 3600.0)) + 1),
                                    start_ts_utc=activity.get("start_ts_utc"),
                                )
                                if w_hist.get("ok") and w_hist.get("summary"):
                                    t_used = w_hist["summary"]["temp_c"]
                                    h_used = w_hist["summary"]["humidity_pct"]
                                    ws_used = w_hist["summary"]["wind_kmh"]
                                    wd_used = w_hist["summary"]["wind_deg"]
                                    hourly_used = w_hist["hourly"]

                            if activity["has_power"]:
                                res_cda = estimate_cda_from_activity(
                                    activity=activity,
                                    total_mass_kg=cal_total_mass,
                                    crr=cal_crr,
                                    efficiency=cal_eff,
                                    temp_c=t_used,
                                    rel_humidity_pct=h_used,
                                    wind_speed_kmh=ws_used,
                                    wind_from_deg=wd_used,
                                    hourly_weather=hourly_used,
                                )
                            else:
                                res_cda = estimate_cda_from_race_summary(
                                    route_data=activity["route_data"],
                                    actual_time_sec=activity["moving_time_s"],
                                    actual_avg_power_w=float(fallback_avg_p or 210.0),
                                    rider_weight_kg=cal_rider_w,
                                    bike_and_gear_kg=cal_bike_w,
                                    crr=cal_crr,
                                    efficiency=cal_eff,
                                    temp_c=t_used,
                                    rel_humidity_pct=h_used,
                                    wind_speed_kmh=ws_used,
                                    wind_from_deg=wd_used,
                                    hourly_weather=hourly_used,
                                )

                            res_cda["race_date"] = act_dt
                            res_cda["bike_label"] = cal_bike_lbl
                            res_cda["weather_used"] = w_hist if (w_hist and w_hist.get("ok")) else {
                                "source_label": "Manual",
                                "summary": {"temp_c": t_used, "humidity_pct": h_used, "wind_kmh": ws_used, "wind_deg": wd_used, "wind_compass": deg_to_compass(wd_used)},
                            }
                            st.session_state[cda_res_key] = res_cda
                except Exception as act_err:
                    st.error(f"⚠️ Error leyendo archivo de carrera: {act_err}")

        # ── MODO 2: RESUMEN OFICIAL + ARCHIVO DE RUTA INDEPENDIENTE ──
        else:
            sum_route_file = st.file_uploader(
                "📂 Sube el archivo de la ruta de ESA carrera pasada (.fit, .gpx, .tcx, .zip) — opcional si es el mismo circuito de arriba:",
                type=["fit", "gpx", "tcx", "zip", "gz"],
                key=f"cda_sum_route_up_{member_id}",
            )
            sum_route_data = route_data
            if sum_route_file is not None:
                try:
                    sum_route_data = parse_route_from_any_file(sum_route_file.getvalue(), sum_route_file.name, laps=1)
                except Exception as sr_err:
                    st.error(f"⚠️ No se pudo leer la ruta pasada: {sr_err}")
                    sum_route_data = None

            if not sum_route_data:
                st.info("📂 Sube el archivo de la ruta de ESA carrera pasada (.fit, .gpx, .tcx o .zip) arriba para calcular tu CdA con el resumen oficial.")
            if sum_route_data:
                sum_segs = sum_route_data["segments"]
                sum_lat = sum(seg["lat"] for seg in sum_segs) / max(1, len(sum_segs))
                sum_lon = sum(seg["lon"] for seg in sum_segs) / max(1, len(sum_segs))
                st.caption(
                    f"🗺️ Ruta para calibración: **`{sum_route_data['route_name']}`** "
                    f"({sum_route_data['total_distance_km']:.1f} km · +{sum_route_data['elevation_gain_m']:.0f} m)"
                )
                cs1, cs2, cs3, cs4, cs5 = st.columns([1.2, 0.9, 0.7, 0.7, 1.1])
                with cs1:
                    sum_date = st.date_input(
                        "📅 Fecha de esa carrera:",
                        value=date.today() - timedelta(days=180),
                        min_value=date(2015, 1, 1),
                        max_value=date.today(),
                        key=f"cda_sum_dt_{member_id}",
                    )
                with cs2:
                    sum_hour_str = st.selectbox("⏰ Hora salida bici:", [f"{h:02d}:00" for h in range(5, 19)], index=2, key=f"cda_sum_hr_{member_id}")
                    sum_hour = int(sum_hour_str.split(":")[0])
                with cs3:
                    t_hh = st.number_input("Horas:", 0, 12, 2, 1, key=f"cda_hh_{member_id}")
                with cs4:
                    t_mm = st.number_input("Minutos:", 0, 59, 25, 1, key=f"cda_mm_{member_id}")
                with cs5:
                    sum_pwr = st.number_input("⚡ Potencia Media (W):", 60.0, 500.0, float(round(ath_defaults["ftp_w"] * 0.80)), 5.0, key=f"cda_sum_p_{member_id}")

                if st.button("📐 Calcular CdA Real desde Resumen de Carrera", type="primary", use_container_width=True, key=f"cda_run_sum_{member_id}"):
                    total_real_s = (int(t_hh) * 3600) + (int(t_mm) * 60)
                    if total_real_s < 120:
                        st.error("Ingresa un tiempo real de carrera válido (mínimo 2 minutos).")
                    else:
                        with st.spinner("Consultando clima histórico ERA5 de esa fecha y despejando tu CdA exacto..."):
                            w_hist = None
                            t_used, h_used, ws_used, wd_used = man_t, man_h, man_ws, man_wd
                            hourly_used = None
                            if use_auto_era5:
                                w_hist = fetch_race_weather(
                                    lat=sum_lat,
                                    lon=sum_lon,
                                    race_date=sum_date,
                                    start_hour=sum_hour,
                                    duration_hours=max(3, int(t_hh) + 2),
                                )
                                if w_hist.get("ok") and w_hist.get("summary"):
                                    t_used = w_hist["summary"]["temp_c"]
                                    h_used = w_hist["summary"]["humidity_pct"]
                                    ws_used = w_hist["summary"]["wind_kmh"]
                                    wd_used = w_hist["summary"]["wind_deg"]
                                    hourly_used = w_hist["hourly"]

                            res_cda = estimate_cda_from_race_summary(
                                route_data=sum_route_data,
                                actual_time_sec=float(total_real_s),
                                actual_avg_power_w=float(sum_pwr),
                                rider_weight_kg=cal_rider_w,
                                bike_and_gear_kg=cal_bike_w,
                                crr=cal_crr,
                                efficiency=cal_eff,
                                temp_c=t_used,
                                rel_humidity_pct=h_used,
                                wind_speed_kmh=ws_used,
                                wind_from_deg=wd_used,
                                hourly_weather=hourly_used,
                            )
                            res_cda["race_date"] = sum_date
                            res_cda["bike_label"] = cal_bike_lbl
                            res_cda["weather_used"] = w_hist if (w_hist and w_hist.get("ok")) else {
                                "source_label": "Manual",
                                "summary": {"temp_c": t_used, "humidity_pct": h_used, "wind_kmh": ws_used, "wind_deg": wd_used, "wind_compass": deg_to_compass(wd_used)},
                            }
                            st.session_state[cda_res_key] = res_cda

        # ── RESULTADO DE CALIBRACIÓN DE CdA ─────────────────────────
        cal_res = st.session_state.get(cda_res_key)
        if cal_res:
            cls = cal_res["classification"]
            wu = cal_res.get("weather_used") or {}
            wus = wu.get("summary") or {}
            st.markdown("---")
            _render_html(
                f"""
                <div style="background:linear-gradient(135deg,#0F172A,#0B1120); border:2px solid {cls['color']};
                            border-radius:14px; padding:16px 18px; margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                        <div>
                            <div style="color:#94A3B8; font-size:0.75rem; font-weight:800; text-transform:uppercase;">
                                📐 CdA Real Calibrado para {ath_defaults['name']}
                            </div>
                            <div style="color:{cls['color']}; font-size:2.2rem; font-weight:900; line-height:1.1; margin-top:4px;">
                                {cal_res['cda']:.3f} <span style="font-size:1rem; color:#CBD5E1;">m²</span>
                            </div>
                            <div style="color:#FFFFFF; font-size:0.9rem; font-weight:700; margin-top:4px;">
                                {cls['level']} · Confianza: {cal_res['confidence']}
                            </div>
                        </div>
                        <div style="background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.12);
                                    border-radius:10px; padding:10px 14px; font-size:0.8rem; color:#E2E8F0; line-height:1.5;">
                            <div style="color:#00EEFF; font-weight:800;">{wu.get('source_label', 'Clima usado')}</div>
                            🌡️ {wus.get('temp_c', '—')}°C · 💧 {wus.get('humidity_pct', '—')}% HR<br>
                            💨 Viento: {wus.get('wind_kmh', '—')} km/h desde {wus.get('wind_compass', '—')} ({wus.get('wind_deg', 0):.0f}°)
                        </div>
                    </div>
                    <div style="color:#CBD5E1; font-size:0.83rem; margin-top:10px; line-height:1.45;">
                        • <b>Diagnóstico:</b> {cls['tip']}<br>
                        • <b>Método:</b> {cal_res['method']} ({cal_res['confidence_note']})
                    </div>
                </div>
                """
            )

            if cal_res.get("fatigue_note"):
                st.warning(cal_res["fatigue_note"])

            # Gráfica de estabilidad de CdA por tramos (si vino de telemetría con potencia)
            if cal_res.get("chunks"):
                df_ch = pd.DataFrame(cal_res["chunks"])
                fig_cda = go.Figure()
                fig_cda.add_trace(
                    go.Bar(
                        x=df_ch["Tramo"],
                        y=df_ch["CdA Est. (m²)"],
                        marker_color="#FF3B30",
                        text=[f"{v:.3f}" for v in df_ch["CdA Est. (m²)"]],
                        textposition="outside",
                        name="CdA por tramo",
                    )
                )
                fig_cda.add_hline(
                    y=cal_res["cda"],
                    line_dash="dash",
                    line_color="#FF8A80",
                    annotation_text=f"CdA Global: {cal_res['cda']:.3f} m²",
                    annotation_position="top left",
                    annotation_font=dict(color="#FF8A80"),
                )
                fig_cda.update_layout(
                    title="📊 Estabilidad Aerodinámica (CdA) a lo largo de la Carrera",
                    paper_bgcolor="#0E121C",
                    plot_bgcolor="#0E121C",
                    font=dict(color="#E2E8F0"),
                    height=310,
                    margin=dict(l=15, r=15, t=45, b=15),
                    yaxis_title="CdA (m²)",
                    yaxis=dict(range=[0, max(df_ch["CdA Est. (m²)"]) * 1.18]),
                )
                st.plotly_chart(fig_cda, use_container_width=True)

            # Mostrar confirmación si recién se guardó
            cda_flash_key = f"cda_saved_flash_{member_id}"
            if st.session_state.get(cda_flash_key):
                st.success(st.session_state.pop(cda_flash_key))

            # Botón 1 clic para guardar en perfil y aplicar al simulador
            if st.button(
                f"💾 Guardar CdA ({cal_res['cda']:.3f} m²) en Perfil de {ath_defaults['name']} y Aplicar al Simulador",
                type="primary",
                use_container_width=True,
                key=f"cda_save_apply_{member_id}",
            ):
                try:
                    with SessionLocal() as s_up:
                        m_db = s_up.query(Member).filter(Member.id == member_id).first()
                        if m_db:
                            m_db.calibrated_cda = float(cal_res["cda"])
                            m_db.calibrated_cda_bike = cal_res.get("bike_label")
                            m_db.calibrated_cda_date = cal_res.get("race_date")
                            s_up.commit()
                    # Invalidar caché de perfil e incrementar versión de widgets de CdA
                    _load_athlete_defaults.clear()
                    if sim_ctx_key:
                        st.session_state.pop(sim_ctx_key, None)
                    st.session_state[f"rs_cda_ver_{member_id}"] = st.session_state.get(f"rs_cda_ver_{member_id}", 0) + 1
                    st.session_state[cda_flash_key] = (
                        f"🎉 CdA real (`{cal_res['cda']:.3f} m²`) guardado en el perfil de **{ath_defaults['name']}** "
                        "y aplicado automáticamente en **⚙️ Simulador y Plan de Carrera**."
                    )
                    st.toast("CdA calibrado guardado y aplicado", icon="📐")
                    st.rerun()
                except Exception as save_cda_err:
                    st.error(f"Error guardando CdA: {save_cda_err}")

    # ═════════════════════════════════════════════════════════════════
    #  SECCIÓN 3: COMPARADOR INTERACTIVO "QUÉ PASA SI..." (ESTILO BEST BIKE SPLIT)
    # ═════════════════════════════════════════════════════════════════
    elif active_section == "comp":
        ctx = saved_plan_ctx or (st.session_state.get(sim_ctx_key) if sim_ctx_key else None)
        if saved_plan_ctx and saved_plan_ctx.get("loaded_from_scenario_name"):
            st.success(
                f"📌 Analizando en **'Qué Pasa Si...'** el plan guardado: **{saved_plan_ctx['loaded_from_scenario_name']}** "
                + ("*(perfil altimétrico reconstruido desde splits guardados)*" if route_data.get("reconstructed_from_splits") else "*(circuito GPX completo guardado)*")
            )
        if not ctx:
            # Construir escenario base por defecto al instante si el usuario entró directo al Comparador
            bike_type = list(BIKE_TYPES.keys())[0]
            wheels = list(WHEEL_SETS.keys())[1]
            helmet = list(HELMETS.keys())[0]
            tires = list(TIRES_CRR.keys())[1]
            drivetrain = list(DRIVETRAINS.keys())[1]
            apparel = list(APPAREL_OPTIONS.keys())[0]
            hydration = list(HYDRATION_SETUP.keys())[0]
            surface = list(ROAD_SURFACES.keys())[1]
            rider_weight = float(ath_defaults["weight_kg"])
            rider_height = float(ath_defaults["height_cm"])
            ftp_w = float(ath_defaults["ftp_w"])
            target_if = 0.80
            temp_c, humidity_pct, wind_kmh, wind_deg = 24.0, 65.0, 8.0, 45.0
            strategy_code = "optimal"
            strat_label = "⚡ Óptimo AlphaX (Variable por Pendiente y Viento)"
            max_climb_pct, max_desc_kmh, split_step_km = 1.05, 68.0, 5.0
            active_hourly_weather = None
            race_date = date.today() + timedelta(days=3)
            race_time_str = "07:00"
            weather_data = None
            weather_matches_route = False
            equipment = estimate_equipment_specs(
                bike_type=bike_type, wheels=wheels, helmet=helmet, tires=tires,
                drivetrain=drivetrain, apparel=apparel, hydration=hydration,
                surface=surface, rider_height_cm=rider_height,
                manual_cda=ath_defaults.get("calibrated_cda"),
            )
            sim = _sim_fast(
                route_sig=route_sig, route_data=route_data, rider_weight_kg=rider_weight,
                ftp_w=ftp_w, target_if=target_if, equipment=equipment,
                temp_c=temp_c, humidity_pct=humidity_pct, wind_kmh=wind_kmh, wind_deg=wind_deg,
                strategy=strategy_code, max_climb_pct=max_climb_pct, max_desc_kmh=max_desc_kmh,
                split_step_km=split_step_km, hourly_weather=active_hourly_weather,
            )
            s = sim["summary"]
        else:
            sim = ctx["sim"]
            s = ctx["s"]
            equipment = ctx["equipment"]
            rider_weight = ctx["rider_weight"]
            rider_height = ctx["rider_height"]
            ftp_w = ctx["ftp_w"]
            target_if = ctx["target_if"]
            temp_c = ctx["temp_c"]
            humidity_pct = ctx["humidity_pct"]
            wind_kmh = ctx["wind_kmh"]
            wind_deg = ctx["wind_deg"]
            strategy_code = ctx["strategy_code"]
            strat_label = ctx["strat_label"]
            max_climb_pct = ctx["max_climb_pct"]
            max_desc_kmh = ctx["max_desc_kmh"]
            split_step_km = ctx["split_step_km"]
            active_hourly_weather = ctx["active_hourly_weather"]
            bike_type = ctx["bike_type"]
            wheels = ctx["wheels"]
            helmet = ctx["helmet"]
            tires = ctx["tires"]
            drivetrain = ctx["drivetrain"]
            apparel = ctx["apparel"]
            hydration = ctx["hydration"]
            surface = ctx["surface"]
            race_date = ctx["race_date"]
            race_time_str = ctx["race_time_str"]
            weather_data = ctx["weather_data"]
            weather_matches_route = ctx["weather_matches_route"]

        st.subheader("🎚️ Simulador Interactivo 'Qué Pasa Si...' (Estilo Best Bike Split)")
        st.caption(
            f"Desliza cualquier variable desde tu escenario objetivo para ver al instante su impacto real en tiempo, velocidad y TSS para **{ath_defaults['name']}** "
            f"en `{route_data['route_name']}` (Base: **{s['total_time_str']}** · CdA `{s['cda']:.3f}` · NP `{s['norm_power_w']:.0f} W` · Peso `{rider_weight:.1f} kg` · IF `{target_if:.2f}` · Viento `{wind_kmh:.0f} km/h`)."
        )

        def _shift_hourly_wind(h_weather, delta_w_kmh):
            if not h_weather or delta_w_kmh == 0:
                return h_weather
            return [
                dict(h, wind_kmh=max(0.0, round(float(h.get("wind_kmh", 0.0)) + float(delta_w_kmh), 1)))
                for h in h_weather
            ]

        def _run_whatif(delta_cda=0.0, delta_w=0.0, delta_kg=0.0, delta_if=0.0, delta_crr=0.0, delta_wind=0.0):
            wi_eq = dict(
                equipment,
                cda=max(0.140, min(0.550, round(equipment["cda"] + delta_cda, 4))),
                crr=max(0.0020, min(0.0100, round(equipment["crr"] + delta_crr, 5))),
            )
            wi_rw = max(35.0, float(rider_weight) + float(delta_kg))
            wi_ftp = max(70.0, float(ftp_w) + float(delta_w))
            wi_if = max(0.45, min(1.15, float(target_if) + float(delta_if)))
            wi_wind = max(0.0, float(wind_kmh) + float(delta_wind))
            wi_hourly = _shift_hourly_wind(active_hourly_weather, delta_wind)
            return _sim_fast(
                route_sig=route_sig,
                route_data=route_data,
                rider_weight_kg=wi_rw,
                ftp_w=wi_ftp,
                target_if=wi_if,
                equipment=wi_eq,
                temp_c=float(temp_c),
                humidity_pct=float(humidity_pct),
                wind_kmh=wi_wind,
                wind_deg=float(wind_deg),
                strategy=strategy_code,
                max_climb_pct=max_climb_pct,
                max_desc_kmh=float(max_desc_kmh),
                split_step_km=float(split_step_km),
                hourly_weather=wi_hourly,
            )

        base_t = s["total_time_s"]
        ftp_ref = max(70.0, float(ftp_w))
        base_cda = max(0.145, min(0.545, round(float(equipment["cda"]), 3)))
        base_if_val = max(0.46, min(1.11, round(float(target_if), 2)))
        base_rw = max(36.0, min(149.0, round(float(rider_weight), 1)))
        base_crr = max(0.0021, min(0.0094, round(float(equipment["crr"]), 4)))
        base_wind = max(0, min(55, int(round(float(wind_kmh)))))

        # Rangos simétricos centrados en el valor base de cada variable
        cda_span = round(min(0.080, max(0.030, min(base_cda - 0.140, 0.550 - base_cda))), 3)
        cda_min = round(max(0.140, base_cda - cda_span), 3)
        cda_max = round(min(0.550, base_cda + cda_span), 3)

        # IF y Potencia Objetivo NP comparten exactamente el mismo rango sincronizado (NP = FTP × IF)
        if_span = round(min(0.15, max(0.06, min(base_if_val - 0.45, 1.12 - base_if_val))), 2)
        if_min = round(max(0.45, base_if_val - if_span), 2)
        if_max = round(min(1.12, base_if_val + if_span), 2)

        np_min = max(60, int(round(ftp_ref * if_min)))
        np_max = min(550, int(round(ftp_ref * if_max)))
        if np_max <= np_min:
            np_max = np_min + 20
        base_np_target = max(np_min, min(np_max, int(round(ftp_ref * base_if_val))))

        rw_span = round(min(10.0, max(5.0, min(base_rw - 35.0, 150.0 - base_rw))), 1)
        rw_min = round(max(35.0, base_rw - rw_span), 1)
        rw_max = round(min(150.0, base_rw + rw_span), 1)
        rw_step = 0.5 if abs(base_rw * 2 - round(base_rw * 2)) < 1e-6 else 0.1

        crr_span = round(min(0.0016, max(0.0006, min(base_crr - 0.0020, 0.0095 - base_crr))), 4)
        crr_min = round(max(0.0020, base_crr - crr_span), 4)
        crr_max = round(min(0.0095, base_crr + crr_span), 4)

        if base_wind >= 15:
            wind_min, wind_max = base_wind - 15, min(60, base_wind + 15)
        elif base_wind > 0:
            wind_min, wind_max = 0, max(base_wind * 2, 24)
        else:
            wind_min, wind_max = 0, 30

        def _render_whatif_sandbox():
            wi_ver_key = f"rs_wi_ver_{member_id}"
            wi_ver = st.session_state.get(wi_ver_key, 0)

            # Firma de base para que si cambia el escenario en Tab 1, los sliders se recentren solos
            base_sig = f"{base_cda:.3f}_{base_np_target}_{round(ftp_ref)}_{base_rw:.1f}_{base_if_val:.2f}_{base_crr:.4f}_{base_wind}_v{wi_ver}"

            # Sincronización bidireccional Potencia Objetivo NP (W) ↔ Intensidad (IF) sobre FTP real
            wi_w_key = f"wi_w_{member_id}_{base_sig}"
            wi_if_key = f"wi_if_{member_id}_{base_sig}"
            if wi_w_key not in st.session_state:
                st.session_state[wi_w_key] = int(base_np_target)
            if wi_if_key not in st.session_state:
                st.session_state[wi_if_key] = float(base_if_val)

            def _sync_if_from_np(_wk=wi_w_key, _ik=wi_if_key, _ftp=ftp_ref, _imin=if_min, _imax=if_max):
                new_np = float(st.session_state[_wk])
                st.session_state[_ik] = round(max(_imin, min(_imax, new_np / _ftp)), 2)

            def _sync_np_from_if(_wk=wi_w_key, _ik=wi_if_key, _ftp=ftp_ref, _nmin=np_min, _nmax=np_max):
                new_if = float(st.session_state[_ik])
                st.session_state[_wk] = int(round(max(_nmin, min(_nmax, new_if * _ftp))))

            # Panel de controles deslizantes dinámicos (6 palancas centradas en los valores objetivo)
            with st.container(border=True):
                c_wi_hdr1, c_wi_hdr2 = st.columns([3.2, 1])
                with c_wi_hdr1:
                    st.markdown("##### 🎛️ Ajusta los valores reales (el punto inicial es tu escenario base del simulador)")
                    st.caption(f"🔗 **Potencia Objetivo NP (W)** e **Intensidad (IF)** están enlazadas automáticamente según tu FTP (`{ftp_ref:.0f} W`): al mover una, la otra se ajusta al instante.")
                with c_wi_hdr2:
                    if st.button("🔄 Restablecer al Base", use_container_width=True, key=f"wi_reset_{member_id}_{wi_ver}"):
                        st.session_state[wi_ver_key] = wi_ver + 1
                        try:
                            st.rerun(scope="fragment")
                        except TypeError:
                            st.rerun()

                w_c1, w_c2, w_c3 = st.columns(3)
                with w_c1:
                    sel_cda = st.slider(
                        f"🌬️ CdA (m²) · Base: {base_cda:.3f}",
                        min_value=cda_min,
                        max_value=cda_max,
                        value=base_cda,
                        step=0.001,
                        format="%.3f",
                        help="Desliza a la izquierda para mejorar aerodinámica (menor CdA = más rápido).",
                        key=f"wi_cda_{member_id}_{base_sig}",
                    )
                    sel_crr = st.slider(
                        f"🛞 Rodamiento Crr · Base: {base_crr:.4f}",
                        min_value=crr_min,
                        max_value=crr_max,
                        value=base_crr,
                        step=0.0001,
                        format="%.4f",
                        help="Menor Crr = llantas más rápidas o asfalto más liso.",
                        key=f"wi_crr_{member_id}_{base_sig}",
                    )
                with w_c2:
                    sel_np_w = st.slider(
                        f"🔥 Potencia Objetivo NP (W) · Base: {base_np_target} W",
                        min_value=int(np_min),
                        max_value=int(np_max),
                        step=1,
                        format="%d W",
                        help=f"Enlazado con IF (FTP {ftp_ref:.0f} W): si subes o bajas vatios, el IF se actualiza automáticamente.",
                        key=wi_w_key,
                        on_change=_sync_if_from_np,
                    )
                    sel_if = st.slider(
                        f"🎯 Intensidad (IF) · Base: {base_if_val:.2f}",
                        min_value=float(if_min),
                        max_value=float(if_max),
                        step=0.01,
                        format="%.2f",
                        help=f"Enlazado con Potencia NP (FTP {ftp_ref:.0f} W): si mueves el IF, la potencia objetivo se actualiza automáticamente.",
                        key=wi_if_key,
                        on_change=_sync_np_from_if,
                    )
                with w_c3:
                    sel_rw = st.slider(
                        f"⚖️ Peso Atleta (kg) · Base: {base_rw:.1f} kg",
                        min_value=rw_min,
                        max_value=rw_max,
                        value=base_rw,
                        step=rw_step,
                        format="%.1f kg",
                        help="Parte desde el peso actual del atleta en el simulador.",
                        key=f"wi_kg_{member_id}_{base_sig}",
                    )
                    sel_wind = st.slider(
                        f"💨 Velocidad del Viento (km/h) · Base: {base_wind} km/h",
                        min_value=int(wind_min),
                        max_value=int(wind_max),
                        value=int(base_wind),
                        step=1,
                        format="%d km/h",
                        help="Parte desde la velocidad del viento configurada en tu escenario.",
                        key=f"wi_wind_{member_id}_{base_sig}",
                    )

            # Calcular deltas internos exactos respecto al escenario base
            d_cda = round(sel_cda - base_cda, 4)
            d_np_w = int(sel_np_w - base_np_target)
            eff_if_exact = round(float(sel_np_w) / ftp_ref, 4)
            d_if_sim = round(eff_if_exact - float(target_if), 4) if d_np_w != 0 else 0.0
            d_weight = round(sel_rw - base_rw, 2)
            d_crr = round(sel_crr - base_crr, 5)
            d_wind = int(sel_wind - base_wind)

            any_changed = any([
                abs(d_cda) > 1e-6,
                d_np_w != 0,
                abs(d_weight) > 1e-6,
                abs(d_crr) > 1e-6,
                d_wind != 0,
            ])

            wi_sim = _run_whatif(d_cda, 0.0, d_weight, d_if_sim, d_crr, d_wind) if any_changed else sim
            ws_sum = wi_sim["summary"]
            delta_total_s = ws_sum["total_time_s"] - base_t
            delta_spd = ws_sum["avg_speed_kmh"] - s["avg_speed_kmh"]
            delta_tss = ws_sum["tss"] - s["tss"]
            delta_np = ws_sum["norm_power_w"] - s["norm_power_w"]

            if delta_total_s < -0.5:
                delta_color = "#00FF88"
                delta_badge = f"⚡ {format_time_delta(delta_total_s)} MÁS RÁPIDO"
            elif delta_total_s > 0.5:
                delta_color = "#FF5577"
                delta_badge = f"🐢 {format_time_delta(delta_total_s)} MÁS LENTO"
            else:
                delta_color = "#00EEFF"
                delta_badge = "⚖️ SIN CAMBIO (ESCENARIO BASE)"

            # Banner Hero de impacto instantáneo
            _render_html(
                f"""
                <div style="display:grid; grid-template-columns: 1.1fr 1.1fr 1.3fr 1fr 1fr; gap:10px; margin:8px 0 14px 0;">
                    <div style="background:#111827; border:1px solid rgba(255,255,255,0.14); border-radius:12px; padding:12px 10px; text-align:center;">
                        <div style="color:#94A3B8; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Tiempo Base</div>
                        <div style="color:#E2E8F0; font-size:1.35rem; font-weight:900; margin-top:3px;">{s['total_time_str']}</div>
                        <div style="color:#64748B; font-size:0.7rem;">{s['avg_speed_kmh']:.1f} km/h · NP {s['norm_power_w']:.0f} W</div>
                    </div>
                    <div style="background:#111827; border:1.5px solid #00EEFF; border-radius:12px; padding:12px 10px; text-align:center;">
                        <div style="color:#00EEFF; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Nuevo Tiempo</div>
                        <div style="color:#FFFFFF; font-size:1.45rem; font-weight:900; margin-top:3px;">{ws_sum['total_time_str']}</div>
                        <div style="color:#94A3B8; font-size:0.7rem;">{ws_sum['avg_speed_kmh']:.1f} km/h ({delta_spd:+.1f})</div>
                    </div>
                    <div style="background:linear-gradient(135deg,#111827,#0B1120); border:2px solid {delta_color}; border-radius:12px; padding:12px 10px; text-align:center;">
                        <div style="color:#94A3B8; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Diferencia de Tiempo</div>
                        <div style="color:{delta_color}; font-size:1.35rem; font-weight:900; margin-top:4px;">{delta_badge}</div>
                        <div style="color:#94A3B8; font-size:0.7rem;">CdA {ws_sum['cda']:.3f} · Masa {ws_sum['total_mass_kg']:.1f} kg</div>
                    </div>
                    <div style="background:#111827; border:1px solid rgba(255,215,0,0.4); border-radius:12px; padding:12px 10px; text-align:center;">
                        <div style="color:#94A3B8; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Potencia NP</div>
                        <div style="color:#FFD700; font-size:1.3rem; font-weight:900; margin-top:3px;">{ws_sum['norm_power_w']:.0f} W</div>
                        <div style="color:#94A3B8; font-size:0.7rem;">{delta_np:+.0f} W · {ws_sum['w_per_kg_np']:.2f} W/kg</div>
                    </div>
                    <div style="background:#111827; border:1px solid rgba(192,132,252,0.35); border-radius:12px; padding:12px 10px; text-align:center;">
                        <div style="color:#94A3B8; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Desgaste (TSS)</div>
                        <div style="color:#C084FC; font-size:1.3rem; font-weight:900; margin-top:3px;">{ws_sum['tss']:.0f}</div>
                        <div style="color:#94A3B8; font-size:0.7rem;">{delta_tss:+.0f} TSS · IF {ws_sum['intensity_factor']:.2f}</div>
                    </div>
                </div>
                """
            )

            # Desglose de contribución individual por cada palanca movida + botón guardar escenario Qué Pasa Si
            if any_changed:
                contrib_items = []
                if abs(d_cda) > 1e-6:
                    dt_c = _run_whatif(delta_cda=d_cda)["summary"]["total_time_s"] - base_t
                    contrib_items.append((f"🌬️ CdA → {sel_cda:.3f} m² ({d_cda:+.3f})", dt_c))
                if d_np_w != 0:
                    dt_w = _run_whatif(delta_if=d_if_sim)["summary"]["total_time_s"] - base_t
                    contrib_items.append((f"🔥 Potencia / IF → {sel_np_w} W · IF {sel_if:.2f} ({d_np_w:+d} W)", dt_w))
                if abs(d_weight) > 1e-6:
                    dt_kg = _run_whatif(delta_kg=d_weight)["summary"]["total_time_s"] - base_t
                    contrib_items.append((f"⚖️ Peso → {sel_rw:.1f} kg ({d_weight:+.1f})", dt_kg))
                if abs(d_crr) > 1e-6:
                    dt_cr = _run_whatif(delta_crr=d_crr)["summary"]["total_time_s"] - base_t
                    contrib_items.append((f"🛞 Crr → {sel_crr:.4f} ({d_crr:+.4f})", dt_cr))
                if d_wind != 0:
                    dt_wd = _run_whatif(delta_wind=d_wind)["summary"]["total_time_s"] - base_t
                    contrib_items.append((f"💨 Viento → {sel_wind} km/h ({d_wind:+d})", dt_wd))

                c_attr1, c_attr2 = st.columns([2.2, 1])
                with c_attr1:
                    fig_attr = go.Figure(
                        data=[
                            go.Bar(
                                x=[x[1] / 60.0 for x in contrib_items],
                                y=[x[0] for x in contrib_items],
                                orientation="h",
                                marker_color=["#00FF88" if x[1] < 0 else "#FF5577" for x in contrib_items],
                                text=[format_time_delta(x[1]) for x in contrib_items],
                                textposition="outside",
                                textfont=dict(color="#FFFFFF", size=11),
                            )
                        ]
                    )
                    fig_attr.add_vline(x=0, line_color="#64748B", line_width=1.5)
                    fig_attr.update_layout(
                        title="📊 Impacto Individual de Cada Cambio (minutos ganados / perdidos)",
                        paper_bgcolor="#0E121C",
                        plot_bgcolor="#0E121C",
                        font=dict(color="#E2E8F0"),
                        height=max(190, 52 * len(contrib_items) + 70),
                        margin=dict(l=10, r=55, t=42, b=15),
                        xaxis_title="Minutos (negativo = más rápido)",
                    )
                    st.plotly_chart(fig_attr, use_container_width=True)
                with c_attr2:
                    st.markdown("##### 💾 Guardar este Escenario")
                    wi_auto_name = f"QuéPasaSi · {route_data['route_name'][:20]} (CdA {ws_sum['cda']:.3f} · NP {ws_sum['norm_power_w']:.0f}W)"
                    wi_name_in = st.text_input("Nombre:", value=wi_auto_name, key=f"wi_save_name_{member_id}_{wi_ver}")
                    if st.button("💾 Guardar Escenario 'Qué Pasa Si'", type="primary", use_container_width=True, key=f"wi_save_btn_{member_id}_{wi_ver}"):
                        try:
                            with SessionLocal() as s_db:
                                new_sc = RaceStrategyScenario(
                                    member_id=member_id,
                                    scenario_name=(wi_name_in.strip() or wi_auto_name),
                                    route_name=route_data["route_name"],
                                    distance_km=ws_sum["distance_km"],
                                    elevation_gain_m=ws_sum["elevation_gain_m"],
                                    estimated_time_sec=ws_sum["total_time_s"],
                                    avg_speed_kmh=ws_sum["avg_speed_kmh"],
                                    avg_power_w=ws_sum["avg_power_w"],
                                    norm_power_w=ws_sum["norm_power_w"],
                                    intensity_factor=ws_sum["intensity_factor"],
                                    tss=ws_sum["tss"],
                                    cda=ws_sum["cda"],
                                    total_mass_kg=ws_sum["total_mass_kg"],
                                    config_json=json.dumps({
                                        "source": "what_if_interactive",
                                        "race_date": race_date.strftime("%Y-%m-%d"),
                                        "race_start_time": race_time_str,
                                        "weather_source": weather_data["source_label"] if weather_matches_route else "Manual",
                                        "bike_type": bike_type,
                                        "wheels": wheels,
                                        "helmet": helmet,
                                        "tires": tires,
                                        "temp_c": temp_c,
                                        "humidity_pct": humidity_pct,
                                        "wind_kmh": sel_wind,
                                        "wind_deg": wind_deg,
                                        "rider_weight_kg": sel_rw,
                                        "ftp_w": round(ftp_ref, 1),
                                        "target_if": round(eff_if_exact, 3),
                                        "strategy": strat_label,
                                        "work_kj": ws_sum["work_kj"],
                                        "w_per_kg_np": ws_sum["w_per_kg_np"],
                                        "vi": ws_sum["vi"],
                                        "d_cda": d_cda, "d_np_w": d_np_w, "d_weight": d_weight,
                                        "d_if": d_if_sim, "d_crr": d_crr, "d_wind": d_wind,
                                        "nutrition": wi_sim["nutrition"],
                                        "route_data_gz": _compress_route_data(route_data),
                                    }, ensure_ascii=False),
                                    splits_json=json.dumps(wi_sim["splits"], ensure_ascii=False),
                                    created_at=datetime.now(),
                                )
                                s_db.add(new_sc)
                                s_db.commit()
                            st.success("✅ Escenario guardado. Aparecerá en el comparador de abajo.")
                            st.toast("Escenario 'Qué Pasa Si' guardado", icon="🏁")
                        except Exception as wi_err:
                            st.error(f"Error guardando escenario: {wi_err}")

            # ── CURVAS DE SENSIBILIDAD ESTILO BEST BIKE SPLIT (CACHEADAS) ──
            with st.expander("📈 Ver Curvas de Ganancia de Tiempo en esta Ruta (CdA vs Vatios vs Peso)", expanded=True):
                c_sw1, c_sw2, c_sw3 = st.columns(3)

                with c_sw1:
                    cda_steps = [-0.04, -0.02, 0.0, 0.02, 0.04]
                    cda_mins = [(_run_whatif(delta_cda=x)["summary"]["total_time_s"] - base_t) / 60.0 for x in cda_steps]
                    fig_sw1 = go.Figure()
                    fig_sw1.add_trace(go.Scatter(x=[base_cda + x for x in cda_steps], y=cda_mins, mode="lines+markers", line=dict(color="#FF3B30", width=3), marker=dict(size=8, color="#FF3B30"), name="Δ Tiempo"))
                    if abs(d_cda) > 1e-6:
                        cur_y = (_run_whatif(delta_cda=d_cda)["summary"]["total_time_s"] - base_t) / 60.0
                        fig_sw1.add_trace(go.Scatter(x=[sel_cda], y=[cur_y], mode="markers", marker=dict(size=13, color="#FFFFFF", symbol="star", line=dict(color="#FF3B30", width=1.5)), name="Tu ajuste"))
                    fig_sw1.add_hline(y=0, line_dash="dot", line_color="#64748B")
                    fig_sw1.update_layout(title="🌬️ Sensibilidad a CdA (m²)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="CdA (m²)", yaxis_title="Δ Minutos", showlegend=False)
                    st.plotly_chart(fig_sw1, use_container_width=True)

                with c_sw2:
                    np_steps = [-25, -15, 0, 15, 25, 35]
                    np_mins = [(_run_whatif(delta_if=x / ftp_ref)["summary"]["total_time_s"] - base_t) / 60.0 for x in np_steps]
                    fig_sw2 = go.Figure()
                    fig_sw2.add_trace(go.Scatter(x=[base_np_target + x for x in np_steps], y=np_mins, mode="lines+markers", line=dict(color="#FFD700", width=3), marker=dict(size=8, color="#FFD700"), name="Δ Tiempo"))
                    if d_np_w != 0:
                        cur_y = (_run_whatif(delta_if=d_if_sim)["summary"]["total_time_s"] - base_t) / 60.0
                        fig_sw2.add_trace(go.Scatter(x=[sel_np_w], y=[cur_y], mode="markers", marker=dict(size=13, color="#FFFFFF", symbol="star", line=dict(color="#FFD700", width=1.5)), name="Tu ajuste"))
                    fig_sw2.add_hline(y=0, line_dash="dot", line_color="#64748B")
                    fig_sw2.update_layout(title="🔥 Sensibilidad a Potencia Objetivo (W)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="Potencia Objetivo NP (W)", yaxis_title="Δ Minutos", showlegend=False)
                    st.plotly_chart(fig_sw2, use_container_width=True)

                with c_sw3:
                    kg_steps = [-6.0, -3.0, 0.0, 3.0, 6.0]
                    kg_mins = [(_run_whatif(delta_kg=x)["summary"]["total_time_s"] - base_t) / 60.0 for x in kg_steps]
                    fig_sw3 = go.Figure()
                    fig_sw3.add_trace(go.Scatter(x=[base_rw + x for x in kg_steps], y=kg_mins, mode="lines+markers", line=dict(color="#00EEFF", width=3), marker=dict(size=8, color="#00EEFF"), name="Δ Tiempo"))
                    if abs(d_weight) > 1e-6:
                        cur_y = (_run_whatif(delta_kg=d_weight)["summary"]["total_time_s"] - base_t) / 60.0
                        fig_sw3.add_trace(go.Scatter(x=[sel_rw], y=[cur_y], mode="markers", marker=dict(size=13, color="#FFFFFF", symbol="star", line=dict(color="#00EEFF", width=1.5)), name="Tu ajuste"))
                    fig_sw3.add_hline(y=0, line_dash="dot", line_color="#64748B")
                    fig_sw3.update_layout(title="⚖️ Sensibilidad a Peso (kg)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="Peso Atleta (kg)", yaxis_title="Δ Minutos", showlegend=False)
                    st.plotly_chart(fig_sw3, use_container_width=True)

        if hasattr(st, "fragment"):
            st.fragment(_render_whatif_sandbox)()
        else:
            _render_whatif_sandbox()

        st.markdown("---")
        col_b1, col_b2 = st.columns(2)

        # ── A. BENCHMARK DE ESTRATEGIA DE PACING (MISMO IF) ─────────
        with col_b1:
            st.markdown("##### 🧠 1. Estrategia de Pacing (al mismo desgaste IF)")
            pacing_rows = []
            p_base_t = None
            for p_lbl, p_code in [
                ("⚡ Óptimo AlphaX (Variable)", "optimal"),
                ("📏 Potencia Constante", "constant"),
                ("🛡️ Negative Split", "negative_split"),
            ]:
                p_sim = _sim_fast(
                    route_sig=route_sig,
                    route_data=route_data,
                    rider_weight_kg=rider_weight,
                    ftp_w=ftp_w,
                    target_if=target_if,
                    equipment=equipment,
                    temp_c=float(temp_c),
                    humidity_pct=float(humidity_pct),
                    wind_kmh=float(wind_kmh),
                    wind_deg=float(wind_deg),
                    strategy=p_code,
                    max_climb_pct=max_climb_pct,
                    max_desc_kmh=float(max_desc_kmh),
                    hourly_weather=active_hourly_weather,
                )["summary"]
                if p_code == "constant":
                    p_base_t = p_sim["total_time_s"]
                pacing_rows.append((p_lbl, p_sim))

            p_table = []
            for p_lbl, ps in pacing_rows:
                delta_vs_const = ps["total_time_s"] - (p_base_t or ps["total_time_s"])
                p_table.append({
                    "Estrategia": p_lbl,
                    "Tiempo": ps["total_time_str"],
                    "vs Constante": "Base" if abs(delta_vs_const) < 0.5 else format_time_delta(delta_vs_const),
                    "Vel. (km/h)": ps["avg_speed_kmh"],
                    "NP / AP (W)": f"{ps['norm_power_w']:.0f} / {ps['avg_power_w']:.0f}",
                    "VI": ps["vi"],
                    "TSS": ps["tss"],
                })
            st.dataframe(pd.DataFrame(p_table), use_container_width=True, hide_index=True)

        # ── B. SENSIBILIDAD AERODINÁMICA VS PESO EN ESTA RUTA ───────
        with col_b2:
            st.markdown("##### 🔬 2. Referencia Rápida (Aero vs Potencia vs Peso)")
            t_aero = _run_whatif(delta_cda=-0.020)["summary"]["total_time_s"]
            t_weight = _run_whatif(delta_kg=-2.0)["summary"]["total_time_s"]
            t_ftp = _run_whatif(delta_w=10.0)["summary"]["total_time_s"]
            t_crr = _run_whatif(delta_crr=-0.0010)["summary"]["total_time_s"]

            sens_df = pd.DataFrame([
                {"Mejora Simulada": "🌬️ Mejorar CdA en -0.020 m² (posición/casco)", "Nuevo Tiempo": format_hms(t_aero), "Ahorro": format_time_delta(t_aero - base_t)},
                {"Mejora Simulada": "🔥 Subir FTP en +10 W (mismo IF)", "Nuevo Tiempo": format_hms(t_ftp), "Ahorro": format_time_delta(t_ftp - base_t)},
                {"Mejora Simulada": "⚖️ Bajar 2.0 kg (atleta o bici)", "Nuevo Tiempo": format_hms(t_weight), "Ahorro": format_time_delta(t_weight - base_t)},
                {"Mejora Simulada": "🛞 Mejores llantas (-0.0010 Crr)", "Nuevo Tiempo": format_hms(t_crr), "Ahorro": format_time_delta(t_crr - base_t)},
            ])
            st.dataframe(sens_df, use_container_width=True, hide_index=True)

        # ── C. BENCHMARK DE TIPOS DE BICICLETA SOBRE ESTE GPX (EDITABLE EN FRAGMENTO INSTANTÁNEO) ───
        def _render_bike_benchmark():
            c_bk_hdr1, c_bk_hdr2 = st.columns([3.2, 1])
            with c_bk_hdr1:
                st.markdown("##### 🚲 3. Comparación Directa de Bicicletas en este Circuito (CdA y Peso Editables)")
            bench_ver_key = f"rs_bike_bench_ver_{member_id}"
            bench_ver = st.session_state.get(bench_ver_key, 0)
            with c_bk_hdr2:
                if st.button("🔄 Restablecer Valores", use_container_width=True, key=f"rs_bk_reset_{member_id}_{bench_ver}"):
                    st.session_state[bench_ver_key] = bench_ver + 1
                    try:
                        st.rerun(scope="fragment")
                    except TypeError:
                        st.rerun()

            st.caption(
                f"Referencias científicas para triatleta aficionado competitivo (estatura `{rider_height:.0f} cm`, peso atleta `{rider_weight:.1f} kg`). "
                "✏️ Haz clic en cualquier valor de **CdA (m²)** o **Peso Bici + Equipo (kg)** para personalizarlo: la **Masa Total** y los tiempos se recalculan al instante."
            )

            bike_presets_spec = [
                ("Contrarreloj / Triatlón (TT / Cabra)", "Lenticular trasera + Perfil Alto 80 mm", "Casco Aero TT con visor"),
                ("Ruta Aero con acoples", "Perfil Alto Carbono (60–80 mm)", "Casco Aero Ruta (semi-cerrado)"),
                ("Ruta Aero (manubrio normal / drops)", "Perfil Medio Carbono (45–50 mm)", "Casco Aero Ruta (semi-cerrado)"),
                ("Ruta Escaladora / Ligera", "Ultraligeras Escaladoras (28–35 mm)", "Casco Ruta Ventilado estándar"),
            ]

            default_eq_list = []
            edit_init_rows = []
            for b_name, w_name, h_name in bike_presets_spec:
                b_eq = estimate_equipment_specs(
                    bike_type=b_name, wheels=w_name, helmet=h_name, tires=tires,
                    drivetrain=drivetrain, apparel=apparel, hydration=hydration,
                    surface=surface, rider_height_cm=rider_height,
                )
                default_eq_list.append((b_name, b_eq))
                edit_init_rows.append({
                    "Configuración": b_name,
                    "CdA (m²)": round(float(b_eq["cda"]), 3),
                    "Peso Bici + Equipo (kg)": round(float(b_eq["bike_and_gear_kg"]), 2),
                })

            edited_bikes_df = st.data_editor(
                pd.DataFrame(edit_init_rows),
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=["Configuración"],
                column_config={
                    "Configuración": st.column_config.TextColumn("🚲 Configuración"),
                    "CdA (m²)": st.column_config.NumberColumn(
                        "🌬️ CdA (m²)",
                        min_value=0.170,
                        max_value=0.450,
                        step=0.005,
                        format="%.3f",
                        help="Edita el CdA real o estimado para cada bicicleta.",
                    ),
                    "Peso Bici + Equipo (kg)": st.column_config.NumberColumn(
                        "⚖️ Peso Bici + Equipo (kg)",
                        min_value=5.5,
                        max_value=18.0,
                        step=0.1,
                        format="%.2f",
                        help="Peso de bici + ruedas + hidratación/herramientas. Al cambiarlo actualiza la Masa Total.",
                    ),
                },
                key=f"rs_bike_bench_ed_{member_id}_{round(rider_height)}_{bench_ver}",
            )

            bike_bench = []
            for idx_b, (b_name, base_eq) in enumerate(default_eq_list):
                row_ed = edited_bikes_df.iloc[idx_b]
                raw_cda_ed = row_ed["CdA (m²)"]
                raw_kg_ed = row_ed["Peso Bici + Equipo (kg)"]
                ed_cda = float(raw_cda_ed) if pd.notna(raw_cda_ed) and float(raw_cda_ed) >= 0.15 else float(base_eq["cda"])
                ed_bike_kg = float(raw_kg_ed) if pd.notna(raw_kg_ed) and float(raw_kg_ed) >= 4.0 else float(base_eq["bike_and_gear_kg"])

                custom_eq = dict(
                    base_eq,
                    cda=round(ed_cda, 4),
                    bike_and_gear_kg=round(ed_bike_kg, 2),
                )
                b_sim = _sim_fast(
                    route_sig=route_sig,
                    route_data=route_data,
                    rider_weight_kg=rider_weight,
                    ftp_w=ftp_w,
                    target_if=target_if,
                    equipment=custom_eq,
                    temp_c=float(temp_c),
                    humidity_pct=float(humidity_pct),
                    wind_kmh=float(wind_kmh),
                    wind_deg=float(wind_deg),
                    strategy=strategy_code,
                    max_climb_pct=max_climb_pct,
                    max_desc_kmh=float(max_desc_kmh),
                    hourly_weather=active_hourly_weather,
                )["summary"]
                bike_bench.append((b_name, ed_bike_kg, b_sim))

            fastest_b = min(x[2]["total_time_s"] for x in bike_bench)
            bike_df = pd.DataFrame([
                {
                    "Configuración": bn,
                    "CdA (m²)": round(bs["cda"], 3),
                    "Peso Bici + Eq. (kg)": round(bk_kg, 2),
                    "Masa Total (kg)": round(bs["total_mass_kg"], 2),
                    "Tiempo Estimado": bs["total_time_str"],
                    "Diferencia vs Más Rápida": "🥇 Más rápida" if abs(bs["total_time_s"] - fastest_b) < 0.5 else format_time_delta(bs["total_time_s"] - fastest_b),
                    "Vel. Media (km/h)": bs["avg_speed_kmh"],
                }
                for bn, bk_kg, bs in bike_bench
            ])
            st.dataframe(bike_df, use_container_width=True, hide_index=True)

        if hasattr(st, "fragment"):
            st.fragment(_render_bike_benchmark)()
        else:
            _render_bike_benchmark()

        # ── D. COMPARADOR DE ESCENARIOS GUARDADOS EN DB ─────────────
        st.markdown("---")
        st.markdown(f"### 📊 Comparar Escenarios Guardados de {ath_defaults['name']}")
        with SessionLocal() as s_db:
            saved_list = (
                s_db.query(RaceStrategyScenario)
                .filter(RaceStrategyScenario.member_id == member_id)
                .order_by(RaceStrategyScenario.created_at.desc(), RaceStrategyScenario.id.desc())
                .all()
            )
        if len(saved_list) >= 2:
            sc_map = {f"{sc.scenario_name} ({format_hms(sc.estimated_time_sec)} · ID:{sc.id})": sc for sc in saved_list}
            sel_comp = st.multiselect(
                "Selecciona de 2 a 4 escenarios para comparar lado a lado:",
                list(sc_map.keys()),
                default=list(sc_map.keys())[: min(3, len(sc_map))],
                max_selections=4,
                key=f"rs_comp_multi_{member_id}",
            )
            if len(sel_comp) >= 2:
                chosen_scs = [sc_map[k] for k in sel_comp]
                min_t_sc = min(c.estimated_time_sec for c in chosen_scs)
                comp_rows = []
                for c in chosen_scs:
                    comp_rows.append({
                        "Escenario": c.scenario_name,
                        "Ruta": c.route_name,
                        "Tiempo": format_hms(c.estimated_time_sec),
                        "Delta": "🥇 Más rápido" if abs(c.estimated_time_sec - min_t_sc) < 0.5 else format_time_delta(c.estimated_time_sec - min_t_sc),
                        "Vel. (km/h)": round(c.avg_speed_kmh or 0, 2),
                        "NP (W)": round(c.norm_power_w or 0, 0),
                        "IF": round(c.intensity_factor or 0, 2),
                        "TSS": round(c.tss or 0, 0),
                        "CdA": round(c.cda or 0, 3),
                        "Masa (kg)": round(c.total_mass_kg or 0, 1),
                    })
                st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

                fig_bar = go.Figure(
                    data=[
                        go.Bar(
                            x=[c.estimated_time_sec / 60.0 for c in chosen_scs],
                            y=[c.scenario_name[:35] for c in chosen_scs],
                            orientation="h",
                            marker_color=["#00FF88" if abs(c.estimated_time_sec - min_t_sc) < 0.5 else "#00EEFF" for c in chosen_scs],
                            text=[format_hms(c.estimated_time_sec) for c in chosen_scs],
                            textposition="auto",
                        )
                    ]
                )
                fig_bar.update_layout(
                    title="Comparación de Tiempo Total (minutos)",
                    paper_bgcolor="#0E121C",
                    plot_bgcolor="#0E121C",
                    font=dict(color="#E2E8F0"),
                    height=260,
                    margin=dict(l=15, r=15, t=42, b=15),
                    xaxis_title="Minutos",
                )
                st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("Guarda al menos 2 escenarios desde **⚙️ Simulador y Plan de Carrera** para compararlos aquí lado a lado.")
