"""
running_strategy_ui.py — Interfaz interactiva de Estrategias de Carrera a Pie (Running)
para AlphaX Race Split:
  - Modo ⏱️ Por Ritmo / Pace (proyección científica desde PB 5K/10K/21K/42K o ritmo GAP manual)
  - Modo ⚡ Por Potencia en Running (Stryd / Garmin / Coros rCP)
  - Planimetría & Altimetría Mapa de Calor, Clima Open-Meteo, Calendario de Geles por Km,
    Comparador 'Qué Pasa Si...' con Benchmark de Zapatillas y Estrategias, y guardado en CRM/App Atleta.
"""

import hashlib
import html
import json
import math
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database import LactateTest, Member, RaceStrategyScenario, SessionLocal
from weather_service import deg_to_compass, fetch_race_weather
from interactive_course_charts import (
    render_interactive_pacing_zoom_widget,
    render_synced_elevation_planimetry_widget,
)
from race_strategy_engine import format_hms, format_time_delta
from running_strategy_engine import (
    PB_DISTANCES_KM,
    RUNNING_RACE_PRESETS,
    RUNNING_SHOES,
    RUNNING_STRATEGIES,
    RUNNING_SURFACES,
    build_re_sensitivity_table,
    calc_stryd_re_from_workout,
    estimate_default_stryd_re,
    estimate_run_metrics_from_pb,
    format_pace,
    format_pace_delta,
    generate_demo_running_gpx,
    parse_time_str_to_sec,
    simulate_running_strategy,
)


def _render_html(html_str: str):
    if hasattr(st, "html"):
        st.html(html_str)
    else:
        st.markdown(html_str, unsafe_allow_html=True)


def _safe_json(val, fallback):
    if not val:
        return fallback
    try:
        return json.loads(val)
    except Exception:
        return fallback


@st.cache_data(ttl=60, show_spinner=False)
def _load_running_athlete_defaults(member_id: int) -> dict:
    """Carga peso, estatura, altitud habitual, PBs (5K/10K/21K/42K), Stryd rCP y lactato de running."""
    res = {
        "name": "Atleta",
        "weight_kg": 70.0,
        "weight_src": "estimado",
        "height_cm": 175.0,
        "height_src": "estimado",
        "altitude_m": 0,
        "city": "",
        "pb_5k": "",
        "pb_10k": "",
        "pb_21k": "",
        "pb_42k": "",
        "run_cp_w": None,
        "run_re": None,
        "lactate_run_lt2": None,
    }
    if not member_id:
        return res
    with SessionLocal() as s:
        m = s.query(Member).filter(Member.id == member_id).first()
        if m:
            res["name"] = m.name
            if m.weight_kg and m.weight_kg > 35:
                res["weight_kg"] = float(m.weight_kg)
                res["weight_src"] = "perfil"
            if m.height_cm and m.height_cm > 120:
                res["height_cm"] = float(m.height_cm)
                res["height_src"] = "perfil"
            if m.altitude_m is not None:
                res["altitude_m"] = int(m.altitude_m)
            res["city"] = (getattr(m, "city", None) or "").strip()
            res["pb_5k"] = (getattr(m, "pb_5k", None) or "").strip()
            res["pb_10k"] = (getattr(m, "pb_10k", None) or "").strip()
            res["pb_21k"] = (getattr(m, "pb_21k", None) or "").strip()
            res["pb_42k"] = (getattr(m, "pb_42k", None) or "").strip()
            if getattr(m, "run_cp_w", None) and float(m.run_cp_w) >= 100:
                res["run_cp_w"] = float(m.run_cp_w)
            if getattr(m, "run_re", None) and 0.70 <= float(m.run_re) <= 1.20:
                res["run_re"] = float(m.run_re)

        # Fallback de peso desde última prueba de lactato + búsqueda de prueba de lactato en Running
        lacs = (
            s.query(LactateTest)
            .filter(LactateTest.member_id == member_id)
            .order_by(LactateTest.date.desc(), LactateTest.id.desc())
            .limit(5)
            .all()
        )
        for lac in lacs:
            if res["weight_src"] == "estimado" and lac.weight and lac.weight > 35:
                res["weight_kg"] = float(lac.weight)
                res["weight_src"] = "lactato"
            sp_low = str(lac.sport or "").lower()
            if any(k in sp_low for k in ("run", "corr", "atlet", "trote", "pie")):
                if lac.lt2_power and not res["lactate_run_lt2"]:
                    res["lactate_run_lt2"] = float(lac.lt2_power)
    return res


def _hourly_sig(hourly_weather) -> str:
    if not hourly_weather:
        return ""
    return "|".join(
        f"{h.get('hour', i)}:{round(float(h.get('temp_c', 0)), 1)}:{round(float(h.get('wind_kmh', 0)), 1)}:{round(float(h.get('wind_deg', 0)), 0)}"
        for i, h in enumerate(hourly_weather[:12])
    )


@st.cache_data(max_entries=256, show_spinner=False)
def _cached_simulate_running(
    route_sig: str,
    _route_data: dict,
    runner_weight_kg: float,
    runner_height_cm: float,
    mode: str,
    target_gap_sec_km: float,
    run_cp_w: float,
    target_pct_rcp: float,
    target_re: float,
    shoe_name: str,
    base_shoe_name: str,
    surface_name: str,
    temp_c: float,
    humidity_pct: float,
    wind_kmh: float,
    wind_deg: float,
    home_altitude_m: float,
    trained_temp_c: float,
    trained_humidity_pct: float,
    strategy: str,
    apply_env_derating: bool,
    post_bike_fatigue_pct: float,
    split_interval_km: float,
    hourly_sig_str: str,
    _hourly_weather,
) -> dict:
    return simulate_running_strategy(
        route_data=_route_data,
        runner_weight_kg=float(runner_weight_kg),
        runner_height_cm=float(runner_height_cm),
        mode=mode,
        target_gap_sec_km=float(target_gap_sec_km),
        run_cp_w=float(run_cp_w),
        target_pct_rcp=float(target_pct_rcp),
        target_re=float(target_re) if target_re and target_re > 0 else None,
        shoe_name=shoe_name,
        base_shoe_name=base_shoe_name or None,
        surface_name=surface_name,
        temp_c=float(temp_c),
        humidity_pct=float(humidity_pct),
        wind_speed_kmh=float(wind_kmh),
        wind_from_deg=float(wind_deg),
        home_altitude_m=float(home_altitude_m),
        trained_temp_c=float(trained_temp_c),
        trained_humidity_pct=float(trained_humidity_pct),
        strategy=strategy,
        apply_env_derating=bool(apply_env_derating),
        post_bike_fatigue_pct=float(post_bike_fatigue_pct),
        split_interval_km=float(split_interval_km),
        hourly_weather=_hourly_weather,
    )


def _sim_run_fast(
    route_sig: str,
    route_data: dict,
    runner_weight_kg: float,
    runner_height_cm: float,
    mode: str,
    target_gap_sec_km: float,
    run_cp_w: float,
    target_pct_rcp: float,
    shoe_name: str,
    surface_name: str,
    temp_c: float,
    humidity_pct: float,
    wind_kmh: float,
    wind_deg: float,
    home_altitude_m: float,
    strategy: str,
    apply_env_derating: bool,
    post_bike_fatigue_pct: float,
    split_interval_km: float,
    hourly_weather=None,
    target_re: float = 0.0,
    base_shoe_name: str = "",
    trained_temp_c: float = 18.0,
    trained_humidity_pct: float = 65.0,
) -> dict:
    return _cached_simulate_running(
        route_sig=route_sig,
        _route_data=route_data,
        runner_weight_kg=round(float(runner_weight_kg), 2),
        runner_height_cm=round(float(runner_height_cm), 1),
        mode=str(mode),
        target_gap_sec_km=round(float(target_gap_sec_km), 1),
        run_cp_w=round(float(run_cp_w), 1),
        target_pct_rcp=round(float(target_pct_rcp), 2),
        target_re=round(float(target_re or 0.0), 3),
        shoe_name=str(shoe_name),
        base_shoe_name=str(base_shoe_name or ""),
        surface_name=str(surface_name),
        temp_c=round(float(temp_c), 1),
        humidity_pct=round(float(humidity_pct), 0),
        wind_kmh=round(float(wind_kmh), 1),
        wind_deg=round(float(wind_deg), 0),
        home_altitude_m=round(float(home_altitude_m), 0),
        trained_temp_c=round(float(trained_temp_c), 1),
        trained_humidity_pct=round(float(trained_humidity_pct), 0),
        strategy=str(strategy),
        apply_env_derating=bool(apply_env_derating),
        post_bike_fatigue_pct=round(float(post_bike_fatigue_pct), 2),
        split_interval_km=round(float(split_interval_km), 1),
        hourly_sig_str=_hourly_sig(hourly_weather),
        _hourly_weather=hourly_weather,
    )


# ═════════════════════════════════════════════════════════════════════
#  GRÁFICAS DE RUNNING (ESTÁNDAR ALPHAX: VERDE=RITMO/VEL, AMARILLO=POTENCIA)
# ═════════════════════════════════════════════════════════════════════

def _build_running_pacing_chart(sim: dict, title_suffix: str = "") -> go.Figure:
    segs = sim["segments"]
    step = max(1, len(segs) // 220)
    ds = segs[::step]
    if ds[-1] != segs[-1]:
        ds.append(segs[-1])

    x_km = [d["dist_km"] for d in ds]
    ele = [d["ele_m"] for d in ds]
    pace_min = [d["pace_sec_km"] / 60.0 for d in ds]
    gap_min = [d["gap_sec_km"] / 60.0 for d in ds]
    power_w = [d["power_w"] for d in ds]
    pace_txt = [d["pace_str"] for d in ds]
    gap_txt = [d["gap_str"] for d in ds]
    grades = [d["grade_pct"] for d in ds]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.66, 0.34],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
    )

    # Panel superior: Ritmo Real (Verde #00FF88), GAP (Cyan punteado) y Potencia Stryd (Amarillo #FFD700)
    fig.add_trace(
        go.Scatter(
            x=x_km,
            y=pace_min,
            name="🏃‍♂️ Ritmo Real (min/km)",
            mode="lines",
            line=dict(color="#00FF88", width=2.6),
            customdata=list(zip(pace_txt, gap_txt, power_w, grades)),
            hovertemplate="<b>Km %{x:.1f}</b><br>Ritmo Real: <b>%{customdata[0]} /km</b><br>GAP: %{customdata[1]} /km<br>Potencia Stryd: %{customdata[2]:.0f} W<br>Pendiente: %{customdata[3]:+.1f}%<extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x_km,
            y=gap_min,
            name="🏔️ Ritmo GAP (Equiv. Llano)",
            mode="lines",
            line=dict(color="#38BDF8", width=1.6, dash="dot"),
            hoverinfo="skip",
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x_km,
            y=power_w,
            name="⚡ Potencia Stryd (W)",
            mode="lines",
            line=dict(color="#FFD700", width=2.2),
            hovertemplate="⚡ Potencia Stryd: <b>%{y:.0f} W</b><extra></extra>",
        ),
        row=1,
        col=1,
        secondary_y=True,
    )

    # Panel inferior: Altimetría del circuito + marcadores de gel
    fig.add_trace(
        go.Scatter(
            x=x_km,
            y=ele,
            name="⛰️ Altimetría (m)",
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(100,116,139,0.28)",
            line=dict(color="#94A3B8", width=1.6),
            hovertemplate="Elevación: <b>%{y:.0f} m</b><extra></extra>",
        ),
        row=2,
        col=1,
    )

    # Marcadores de Geles sobre el perfil de elevación
    gels = sim.get("nutrition", {}).get("gel_schedule", [])
    if gels and x_km:
        g_x, g_y, g_txt = [], [], []
        for g in gels:
            g_km = float(g["km"])
            # Encontrar elevación más cercana
            nearest_i = min(range(len(x_km)), key=lambda idx: abs(x_km[idx] - g_km))
            g_x.append(g_km)
            g_y.append(ele[nearest_i])
            g_txt.append(f"Gel #{g['gel_num']} (Km {g_km:.1f} · {g['time_str']})")
        fig.add_trace(
            go.Scatter(
                x=g_x,
                y=g_y,
                name="⚡ Toma de Gel",
                mode="markers+text",
                marker=dict(size=10, color="#FFAA00", symbol="diamond", line=dict(color="#FFFFFF", width=1.2)),
                text=[f"G{g['gel_num']}" for g in gels],
                textposition="top center",
                textfont=dict(color="#FFAA00", size=10, family="sans-serif"),
                hovertext=g_txt,
                hovertemplate="<b>%{hovertext}</b><extra></extra>",
            ),
            row=2,
            col=1,
        )

    # Formato de ticks min/km invertido (más rápido arriba)
    p_lo = max(2.5, min(min(pace_min), min(gap_min)) - 0.35)
    p_hi = min(14.0, max(max(pace_min), max(gap_min)) + 0.35)
    tick_step = 0.5 if (p_hi - p_lo) <= 3.5 else 1.0
    t_start = math.floor(p_lo / tick_step) * tick_step
    t_vals = []
    v_cur = t_start
    while v_cur <= p_hi + 0.01:
        t_vals.append(round(v_cur, 2))
        v_cur += tick_step
    t_lbls = [format_pace(v * 60.0) for v in t_vals]

    fig.update_layout(
        title=dict(
            text=f"🏃‍♂️ Plan de Pacing Running — Ritmo (Verde) vs Potencia Stryd (Amarillo){title_suffix}",
            font=dict(size=14, color="#FFFFFF"),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0E121C",
        font=dict(color="#E2E8F0", size=11),
        height=430,
        margin=dict(l=12, r=12, t=46, b=16),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    fig.update_yaxes(
        title_text="Ritmo (min/km)",
        range=[p_hi, p_lo],  # Invertido: más rápido arriba
        tickvals=t_vals,
        ticktext=t_lbls,
        gridcolor="rgba(255,255,255,0.06)",
        row=1,
        col=1,
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="Potencia Stryd (W)",
        showgrid=False,
        color="#FFD700",
        row=1,
        col=1,
        secondary_y=True,
    )
    fig.update_yaxes(title_text="Altitud (m)", gridcolor="rgba(255,255,255,0.05)", row=2, col=1)
    fig.update_xaxes(title_text="Distancia (km)", gridcolor="rgba(255,255,255,0.05)", row=2, col=1)
    return fig


def _build_running_gps_map(sim: dict, color_by: str, wind_deg: float, wind_kmh: float) -> go.Figure:
    """Mapa GPS 2D del circuito de Running con flechas de viento y color por Ritmo, Potencia o Pendiente."""
    segs = sim["segments"]
    step = max(1, len(segs) // 240)
    ds = segs[::step]

    lats = [d["lat"] for d in ds]
    lons = [d["lon"] for d in ds]

    if color_by == "Potencia Stryd (W)":
        vals = [d["power_w"] for d in ds]
        cscale = [[0.0, "#1E293B"], [0.4, "#F59E0B"], [0.75, "#FFD700"], [1.0, "#FFF59D"]]
        cbar_title = "Stryd (W)"
    elif color_by == "Ritmo Real (min/km)":
        vals = [d["speed_kmh"] for d in ds]
        cscale = [[0.0, "#064E3B"], [0.5, "#10B981"], [1.0, "#00FF88"]]
        cbar_title = "Vel. (km/h)"
    else:
        vals = [d["grade_pct"] for d in ds]
        cscale = [
            [0.0, "#00B0FF"],
            [0.35, "#00E676"],
            [0.5, "#64748B"],
            [0.7, "#FFD600"],
            [0.85, "#FF6D00"],
            [1.0, "#FF1744"],
        ]
        cbar_title = "Pend. (%)"

    hovers = [
        f"Km {d['dist_km']:.1f} · Ritmo {d['pace_str']}/km (GAP {d['gap_str']})<br>"
        f"Potencia: {d['power_w']:.0f} W · Pend: {d['grade_pct']:+.1f}% · Viento rel: {d['headwind_kmh']:+.0f} km/h"
        for d in ds
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=lons,
            y=lats,
            mode="markers+lines",
            line=dict(color="rgba(148,163,184,0.35)", width=2),
            marker=dict(
                size=7,
                color=vals,
                colorscale=cscale,
                showscale=True,
                colorbar=dict(title=cbar_title, thickness=12, len=0.78),
            ),
            text=hovers,
            hovertemplate="%{text}<extra></extra>",
            name="Circuito Running",
        )
    )

    # Salida y Meta
    if lats:
        fig.add_trace(
            go.Scatter(
                x=[lons[0]],
                y=[lats[0]],
                mode="markers+text",
                marker=dict(size=12, color="#00FF88", symbol="circle", line=dict(color="#000", width=1.5)),
                text=["🟢 Salida"],
                textposition="top right",
                textfont=dict(color="#00FF88", size=11),
                showlegend=False,
            )
        )

    # Flechas de dirección del viento sobre el mapa
    if wind_kmh >= 2.0 and lats:
        lat_span = max(0.002, max(lats) - min(lats))
        lon_span = max(0.002, max(lons) - min(lons))
        # El viento VIENE desde wind_deg -> sopla hacia wind_deg + 180
        to_rad = math.radians((float(wind_deg) + 180.0) % 360.0)
        dx = math.sin(to_rad) * lon_span * 0.08
        dy = math.cos(to_rad) * lat_span * 0.08
        annotations = []
        n_arr = min(8, max(3, len(ds) // 25))
        idxs = [int(i * (len(ds) - 1) / max(1, n_arr)) for i in range(1, n_arr)]
        for idx in idxs:
            ax_lon = lons[idx] - dx * 0.5
            ax_lat = lats[idx] - dy * 0.5
            annotations.append(
                dict(
                    x=ax_lon + dx,
                    y=ax_lat + dy,
                    ax=ax_lon,
                    ay=ax_lat,
                    xref="x",
                    yref="y",
                    axref="x",
                    ayref="y",
                    showarrow=True,
                    arrowhead=2,
                    arrowsize=1.2,
                    arrowwidth=2,
                    arrowcolor="#38BDF8",
                )
            )
        fig.update_layout(annotations=annotations)

    fig.update_layout(
        title=dict(
            text=f"🗺️ Trazado GPS Running — {color_by} · 🌬️ Viento {wind_kmh:.0f} km/h ({deg_to_compass(wind_deg)})",
            font=dict(size=13, color="#FFFFFF"),
        ),
        paper_bgcolor="#0E121C",
        plot_bgcolor="#0B101A",
        font=dict(color="#E2E8F0", size=11),
        height=370,
        margin=dict(l=10, r=10, t=42, b=10),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, title=""),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, scaleanchor="x", scaleratio=1, title=""),
        showlegend=False,
    )
    return fig


def _build_run_ctx_from_saved(sc, ath_defaults: dict) -> tuple:
    """Reconstruye (route_sig, route_data, ctx) para un plan de Running guardado."""
    from race_strategy_ui import _reconstruct_route_from_saved_scenario

    cfg = _safe_json(sc.config_json, {})
    if not isinstance(cfg, dict):
        cfg = {}
    splits = _safe_json(sc.splits_json, [])
    if not isinstance(splits, list):
        splits = []

    route_data = _reconstruct_route_from_saved_scenario(sc, cfg, splits)
    route_sig = f"run_saved_{sc.id}_{len(route_data.get('segments', []))}"

    weight_kg = float(cfg.get("runner_weight_kg") or sc.total_mass_kg or ath_defaults["weight_kg"])
    height_cm = float(cfg.get("runner_height_cm") or ath_defaults["height_cm"])
    mode = str(cfg.get("running_mode") or "pace")
    run_cp_w = float(cfg.get("run_cp_w") or ath_defaults.get("run_cp_w") or 285.0)
    target_pct_rcp = float(cfg.get("target_pct_rcp") or (float(sc.intensity_factor or 0.92) * 100.0))
    target_gap_sec = float(cfg.get("target_gap_sec_km") or 270.0)
    shoe_name = cfg.get("shoe_name") if cfg.get("shoe_name") in RUNNING_SHOES else list(RUNNING_SHOES.keys())[0]
    surface_name = cfg.get("surface_name") if cfg.get("surface_name") in RUNNING_SURFACES else list(RUNNING_SURFACES.keys())[1]
    temp_c = float(cfg.get("temp_c") if cfg.get("temp_c") is not None else 20.0)
    humidity_pct = float(cfg.get("humidity_pct") if cfg.get("humidity_pct") is not None else 65.0)
    wind_kmh = float(cfg.get("wind_kmh") if cfg.get("wind_kmh") is not None else 8.0)
    wind_deg = float(cfg.get("wind_deg") if cfg.get("wind_deg") is not None else 45.0)
    home_alt = float(cfg.get("home_altitude_m") if cfg.get("home_altitude_m") is not None else ath_defaults.get("altitude_m", 0))
    trained_temp = float(cfg.get("trained_temp_c") if cfg.get("trained_temp_c") is not None else 19.0)
    trained_hum = float(cfg.get("trained_humidity_pct") if cfg.get("trained_humidity_pct") is not None else 70.0)
    target_re = float(cfg.get("target_re") or ath_defaults.get("run_re") or estimate_default_stryd_re(run_cp_w / max(38.0, weight_kg)))
    strategy_code = str(cfg.get("strategy_code") or "even_effort")
    apply_env = bool(cfg.get("apply_env_derating", True))
    fatigue_pct = float(cfg.get("post_bike_fatigue_pct") or 0.0)
    split_km = float(cfg.get("split_interval_km") or 2.0)

    sim = _sim_run_fast(
        route_sig=route_sig,
        route_data=route_data,
        runner_weight_kg=weight_kg,
        runner_height_cm=height_cm,
        mode=mode,
        target_gap_sec_km=target_gap_sec,
        run_cp_w=run_cp_w,
        target_pct_rcp=target_pct_rcp,
        shoe_name=shoe_name,
        surface_name=surface_name,
        temp_c=temp_c,
        humidity_pct=humidity_pct,
        wind_kmh=wind_kmh,
        wind_deg=wind_deg,
        home_altitude_m=home_alt,
        strategy=strategy_code,
        apply_env_derating=apply_env,
        post_bike_fatigue_pct=fatigue_pct,
        split_interval_km=split_km,
        target_re=target_re,
        base_shoe_name=shoe_name,
        trained_temp_c=trained_temp,
        trained_humidity_pct=trained_hum,
    )

    ctx = {
        "sim": sim,
        "s": sim["summary"],
        "nut": sim["nutrition"],
        "runner_weight_kg": weight_kg,
        "runner_height_cm": height_cm,
        "mode": mode,
        "target_gap_sec_km": target_gap_sec,
        "run_cp_w": run_cp_w,
        "target_pct_rcp": target_pct_rcp,
        "target_re": target_re,
        "shoe_name": shoe_name,
        "surface_name": surface_name,
        "temp_c": temp_c,
        "humidity_pct": humidity_pct,
        "wind_kmh": wind_kmh,
        "wind_deg": wind_deg,
        "home_altitude_m": home_alt,
        "trained_temp_c": trained_temp,
        "trained_humidity_pct": trained_hum,
        "strategy_code": strategy_code,
        "strat_label": cfg.get("strategy") or list(RUNNING_STRATEGIES.keys())[0],
        "apply_env_derating": apply_env,
        "post_bike_fatigue_pct": fatigue_pct,
        "split_interval_km": split_km,
        "hourly_weather": None,
        "race_date": cfg.get("race_date") or str(date.today()),
        "race_time_str": cfg.get("race_start_time") or "07:00",
        "weather_source": cfg.get("weather_source") or "Manual",
        "loaded_from_scenario_name": sc.scenario_name,
        "loaded_from_scenario_id": sc.id,
    }
    return route_sig, route_data, ctx


def _save_running_scenario_to_db(
    member_id: int,
    scenario_name: str,
    route_data: dict,
    sim: dict,
    ctx: dict,
    source_tag: str = "running_simulator",
):
    from race_strategy_ui import _compress_route_data

    s = sim["summary"]
    nut = sim["nutrition"]
    # Limpiar claves privadas (_pace_sec, etc.) antes de guardar tabla visible
    clean_splits = [
        {k: v for k, v in sp.items() if not str(k).startswith("_")}
        for sp in sim["splits"]
    ]
    cfg_payload = {
        "sport": "running",
        "source": source_tag,
        "running_mode": ctx["mode"],
        "runner_weight_kg": ctx["runner_weight_kg"],
        "runner_height_cm": ctx["runner_height_cm"],
        "target_gap_sec_km": ctx["target_gap_sec_km"],
        "run_cp_w": ctx["run_cp_w"],
        "target_pct_rcp": ctx["target_pct_rcp"],
        "target_re": ctx.get("target_re", s.get("target_re")),
        "course_re": s.get("course_re"),
        "avg_pace_str": s["avg_pace_str"],
        "avg_gap_str": s["avg_gap_str"],
        "w_per_kg": s["w_per_kg"],
        "first_half_str": s["first_half_str"],
        "second_half_str": s["second_half_str"],
        "shoe_name": ctx["shoe_name"],
        "surface_name": ctx["surface_name"],
        "strategy": ctx["strat_label"],
        "strategy_code": ctx["strategy_code"],
        "temp_c": ctx["temp_c"],
        "humidity_pct": ctx["humidity_pct"],
        "wind_kmh": ctx["wind_kmh"],
        "wind_deg": ctx["wind_deg"],
        "home_altitude_m": ctx["home_altitude_m"],
        "trained_temp_c": ctx.get("trained_temp_c", 19.0),
        "trained_humidity_pct": ctx.get("trained_humidity_pct", 70.0),
        "apply_env_derating": ctx["apply_env_derating"],
        "post_bike_fatigue_pct": ctx["post_bike_fatigue_pct"],
        "split_interval_km": ctx["split_interval_km"],
        "race_date": str(ctx.get("race_date", "")),
        "race_start_time": str(ctx.get("race_time_str", "")),
        "weather_source": ctx.get("weather_source", "Manual"),
        "work_kj": s["work_kj"],
        "nutrition": nut,
        "route_data_gz": _compress_route_data(route_data),
    }

    with SessionLocal() as s_db:
        rec = RaceStrategyScenario(
            member_id=member_id,
            scenario_name=scenario_name.strip() or f"Running {route_data['route_name']}",
            route_name=route_data["route_name"],
            distance_km=float(s["total_distance_km"]),
            elevation_gain_m=float(s["elevation_gain_m"]),
            estimated_time_sec=float(s["total_time_sec"]),
            avg_speed_kmh=float(s["avg_speed_kmh"]),
            avg_power_w=float(s["avg_power_w"]),
            norm_power_w=float(s["norm_power_w"]),
            intensity_factor=float(s["intensity_factor"]),
            tss=float(s["rtss"]),
            cda=float(s["cda_run"]),
            crr=float(s["re_eff"]),
            total_mass_kg=float(ctx["runner_weight_kg"]),
            config_json=json.dumps(cfg_payload, ensure_ascii=False),
            splits_json=json.dumps(clean_splits, ensure_ascii=False),
        )
        s_db.add(rec)
        s_db.commit()


# ═════════════════════════════════════════════════════════════════════
#  FRAGMENTOS INTERACTIVOS (MAPA GPS & COMPARADOR 'QUÉ PASA SI...')
# ═════════════════════════════════════════════════════════════════════

@st.fragment
def _render_running_gps_map_fragment(sim: dict, wind_deg: float, wind_kmh: float, member_id: int):
    color_opt = st.radio(
        "Colorear trazado GPS por:",
        ["Pendiente (%)", "Ritmo Real (min/km)", "Potencia Stryd (W)"],
        horizontal=True,
        key=f"run_map_color_{member_id}",
    )
    st.plotly_chart(_build_running_gps_map(sim, color_opt, wind_deg, wind_kmh), use_container_width=True)


@st.fragment
def _render_running_what_if_fragment(
    route_sig: str,
    route_data: dict,
    base_ctx: dict,
    member_id: int,
    ath_name: str,
):
    base_s = base_ctx["s"]
    mode = base_ctx["mode"]
    base_w = float(base_ctx["runner_weight_kg"])
    base_temp = float(base_ctx["temp_c"])
    base_wind = float(base_ctx["wind_kmh"])
    base_rcp = float(base_ctx["run_cp_w"])
    base_pct = float(base_ctx["target_pct_rcp"])
    base_gap = float(base_ctx["target_gap_sec_km"])
    base_shoe = base_ctx["shoe_name"]
    base_re = float(base_ctx.get("target_re") or base_s.get("target_re") or estimate_default_stryd_re(base_rcp / max(38.0, base_w)))
    base_tr_temp = float(base_ctx.get("trained_temp_c", 19.0))
    base_tr_hum = float(base_ctx.get("trained_humidity_pct", 70.0))

    st.markdown("### ⚔️ Comparador Interactivo Running 'Qué Pasa Si...'")
    st.caption(
        f"Base actual: **⏱️ {base_s['total_time_str']}** · Ritmo **{base_s['avg_pace_str']} /km** (GAP **{base_s['avg_gap_str']} /km**) · "
        f"Stryd **{base_s['avg_power_w']:.0f} W** (Target RE **{base_re:.2f}**) · **{base_w:.1f} kg** · **{base_temp:.0f}°C** · **{RUNNING_SHOES[base_shoe]['short']}**."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if mode == "stryd":
            wi_p_key = f"run_wi_pow_{member_id}_{route_sig}"
            wi_pct_key = f"run_wi_pct_{member_id}_{route_sig}"
            base_p_target = round(base_rcp * (base_pct / 100.0))
            p_lo = max(100, int(base_p_target - 60))
            p_hi = min(550, int(base_p_target + 60))
            pct_lo = max(60.0, round(base_pct - 20.0, 1))
            pct_hi = min(115.0, round(base_pct + 20.0, 1))

            if wi_p_key not in st.session_state:
                st.session_state[wi_p_key] = int(base_p_target)
            if wi_pct_key not in st.session_state:
                st.session_state[wi_pct_key] = round(base_pct, 1)

            def _sync_wi_from_p():
                p_v = float(st.session_state[wi_p_key])
                st.session_state[wi_pct_key] = round(max(pct_lo, min(pct_hi, (p_v / max(80.0, base_rcp)) * 100.0)), 1)

            def _sync_wi_from_pct():
                pc_v = float(st.session_state[wi_pct_key])
                st.session_state[wi_p_key] = int(round(max(p_lo, min(p_hi, base_rcp * (pc_v / 100.0)))))

            wi_pow = st.slider(
                "⚡ Potencia Objetivo Stryd (W)",
                min_value=p_lo,
                max_value=p_hi,
                step=2,
                key=wi_p_key,
                on_change=_sync_wi_from_p,
            )
            wi_pct = st.slider(
                "🎯 Intensidad (% rCP)",
                min_value=pct_lo,
                max_value=pct_hi,
                step=0.5,
                key=wi_pct_key,
                on_change=_sync_wi_from_pct,
            )
            wi_re = st.slider(
                "📏 Target RE Stryd (Running Effectiveness)",
                min_value=round(max(0.80, base_re - 0.05), 2),
                max_value=round(min(1.10, base_re + 0.05), 2),
                value=round(base_re, 2),
                step=0.01,
                key=f"run_wi_re_{member_id}_{route_sig}",
            )
            wi_gap = base_gap
        else:
            delta_pace = st.slider(
                "🏃‍♂️ Cambio en Ritmo GAP Objetivo (seg/km)",
                min_value=-25,
                max_value=25,
                value=0,
                step=1,
                help="Negativo = correr más rápido (ej. -5 s/km). Positivo = correr más conservador.",
                key=f"run_wi_dpace_{member_id}_{route_sig}",
            )
            wi_gap = max(150.0, base_gap + float(delta_pace))
            st.caption(f"GAP objetivo ajustado: **`{format_pace(wi_gap)} /km`** ({format_pace_delta(delta_pace)})")
            wi_pct = base_pct
            wi_re = base_re

    with c2:
        delta_kg = st.slider(
            "⚖️ Cambio de Peso (kg)",
            min_value=-5.0,
            max_value=5.0,
            value=0.0,
            step=0.5,
            key=f"run_wi_dkg_{member_id}_{route_sig}",
        )
        wi_temp = st.slider(
            "🌡️ Temperatura de Carrera (°C)",
            min_value=max(4.0, round(base_temp - 12.0)),
            max_value=min(40.0, round(base_temp + 12.0)),
            value=round(base_temp, 0),
            step=1.0,
            key=f"run_wi_temp_{member_id}_{route_sig}",
        )

    with c3:
        wi_wind = st.slider(
            "💨 Velocidad del Viento (km/h)",
            min_value=0.0,
            max_value=max(35.0, round(base_wind + 20.0)),
            value=round(base_wind, 0),
            step=1.0,
            key=f"run_wi_wind_{member_id}_{route_sig}",
        )
        shoe_keys = list(RUNNING_SHOES.keys())
        wi_shoe = st.selectbox(
            "👟 Zapatillas:",
            shoe_keys,
            index=shoe_keys.index(base_shoe) if base_shoe in shoe_keys else 0,
            key=f"run_wi_shoe_{member_id}_{route_sig}",
        )

    wi_weight = max(40.0, base_w + float(delta_kg))
    wi_sim = _sim_run_fast(
        route_sig=route_sig,
        route_data=route_data,
        runner_weight_kg=wi_weight,
        runner_height_cm=base_ctx["runner_height_cm"],
        mode=mode,
        target_gap_sec_km=wi_gap,
        run_cp_w=base_rcp,
        target_pct_rcp=wi_pct,
        shoe_name=wi_shoe,
        surface_name=base_ctx["surface_name"],
        temp_c=wi_temp,
        humidity_pct=base_ctx["humidity_pct"],
        wind_kmh=wi_wind,
        wind_deg=base_ctx["wind_deg"],
        home_altitude_m=base_ctx["home_altitude_m"],
        strategy=base_ctx["strategy_code"],
        apply_env_derating=base_ctx["apply_env_derating"],
        post_bike_fatigue_pct=base_ctx["post_bike_fatigue_pct"],
        split_interval_km=base_ctx["split_interval_km"],
        hourly_weather=base_ctx.get("hourly_weather"),
        target_re=wi_re,
        base_shoe_name=base_shoe,
        trained_temp_c=base_tr_temp,
        trained_humidity_pct=base_tr_hum,
    )
    ws = wi_sim["summary"]
    dt_sec = ws["total_time_sec"] - base_s["total_time_sec"]
    dp_sec = ws["avg_pace_sec_km"] - base_s["avg_pace_sec_km"]
    dw = ws["avg_power_w"] - base_s["avg_power_w"]

    if dt_sec < -2.0:
        d_col, d_badge = "#00FF88", f"🚀 {format_time_delta(dt_sec)} MÁS RÁPIDO"
    elif dt_sec > 2.0:
        d_col, d_badge = "#FF5252", f"🐢 {format_time_delta(dt_sec)} MÁS LENTO"
    else:
        d_col, d_badge = "#00EEFF", "⚖️ SIN CAMBIO SIGNIFICATIVO"

    _render_html(
        f"""
        <div style="background:linear-gradient(135deg, rgba(16,24,38,0.96), rgba(9,13,22,0.98));
                    border:2px solid {d_col}; border-radius:14px; padding:14px 18px; margin:10px 0 14px 0;
                    display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px;">
            <div>
                <div style="color:#94A3B8; font-size:0.72rem; font-weight:800; text-transform:uppercase;">Resultado Escenario 'Qué Pasa Si...'</div>
                <div style="color:{d_col}; font-size:1.55rem; font-weight:900; margin-top:2px;">{d_badge}</div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:16px; text-align:center;">
                <div>
                    <div style="color:#8E9BAE; font-size:0.68rem;">TIEMPO NUEVO</div>
                    <div style="color:#00EEFF; font-size:1.3rem; font-weight:900;">{ws['total_time_str']}</div>
                    <div style="color:#64748B; font-size:0.7rem;">Base: {base_s['total_time_str']}</div>
                </div>
                <div>
                    <div style="color:#8E9BAE; font-size:0.68rem;">RITMO MEDIO</div>
                    <div style="color:#00FF88; font-size:1.3rem; font-weight:900;">{ws['avg_pace_str']} /km</div>
                    <div style="color:#94A3B8; font-size:0.7rem;">Delta: {format_pace_delta(dp_sec)}</div>
                </div>
                <div>
                    <div style="color:#8E9BAE; font-size:0.68rem;">POTENCIA STRYD</div>
                    <div style="color:#FFD700; font-size:1.3rem; font-weight:900;">{ws['avg_power_w']:.0f} W</div>
                    <div style="color:#94A3B8; font-size:0.7rem;">{dw:+.0f} W · {ws['w_per_kg']:.2f} W/kg</div>
                </div>
            </div>
        </div>
        """
    )

    # Guardar escenario What-If
    c_sv1, c_sv2 = st.columns([2.2, 1])
    with c_sv1:
        wi_name = st.text_input(
            "Nombre para guardar este escenario 'Qué Pasa Si...':",
            value=f"Running {route_data['route_name'][:24]} ({ws['avg_pace_str']}/km · {RUNNING_SHOES[wi_shoe]['short']})",
            key=f"run_wi_save_name_{member_id}_{route_sig}",
        )
    with c_sv2:
        st.markdown("<div style='height:27px;'></div>", unsafe_allow_html=True)
        if st.button("💾 Guardar Escenario", type="primary", use_container_width=True, key=f"run_wi_save_btn_{member_id}_{route_sig}"):
            wi_ctx = dict(base_ctx)
            wi_ctx.update({
                "runner_weight_kg": wi_weight,
                "target_gap_sec_km": wi_gap,
                "target_pct_rcp": wi_pct,
                "target_re": wi_re,
                "shoe_name": wi_shoe,
                "temp_c": wi_temp,
                "wind_kmh": wi_wind,
            })
            _save_running_scenario_to_db(member_id, wi_name, route_data, wi_sim, wi_ctx, source_tag="running_what_if")
            st.toast(f"Escenario Running guardado para {ath_name}!", icon="✅")

    # ── BENCHMARK 1: COMPARATIVA DE ZAPATILLAS EN ESTE CIRCUITO ─────
    st.markdown("#### 👟 Benchmark de Calzado en este Circuito")
    shoe_rows = []
    for sh_name, sh_meta in RUNNING_SHOES.items():
        sh_sim = _sim_run_fast(
            route_sig=route_sig,
            route_data=route_data,
            runner_weight_kg=wi_weight,
            runner_height_cm=base_ctx["runner_height_cm"],
            mode=mode,
            target_gap_sec_km=wi_gap,
            run_cp_w=base_rcp,
            target_pct_rcp=wi_pct,
            shoe_name=sh_name,
            surface_name=base_ctx["surface_name"],
            temp_c=wi_temp,
            humidity_pct=base_ctx["humidity_pct"],
            wind_kmh=wi_wind,
            wind_deg=base_ctx["wind_deg"],
            home_altitude_m=base_ctx["home_altitude_m"],
            strategy=base_ctx["strategy_code"],
            apply_env_derating=base_ctx["apply_env_derating"],
            post_bike_fatigue_pct=base_ctx["post_bike_fatigue_pct"],
            split_interval_km=base_ctx["split_interval_km"],
            hourly_weather=base_ctx.get("hourly_weather"),
            target_re=wi_re,
            base_shoe_name=base_shoe,
            trained_temp_c=base_tr_temp,
            trained_humidity_pct=base_tr_hum,
        )
        ss = sh_sim["summary"]
        shoe_rows.append({
            "Zapatilla": sh_meta["short"],
            "Δ RE": f"{sh_meta['delta_re']:+.3f}",
            "Tiempo": ss["total_time_str"],
            "Ritmo (/km)": ss["avg_pace_str"],
            "Potencia (W)": int(ss["avg_power_w"]),
            "_sec": ss["total_time_sec"],
        })
    min_sh_t = min(r["_sec"] for r in shoe_rows)
    for r in shoe_rows:
        r["Diferencia"] = "🥇 Más rápida" if abs(r["_sec"] - min_sh_t) < 0.5 else format_time_delta(r["_sec"] - min_sh_t)
        del r["_sec"]
    st.dataframe(pd.DataFrame(shoe_rows), use_container_width=True, hide_index=True)

    # ── BENCHMARK 2: ESTRATEGIAS DE PACING ──────────────────────────
    st.markdown("#### 🧠 Benchmark de Estrategias de Pacing")
    strat_rows = []
    for st_lbl, st_code in RUNNING_STRATEGIES.items():
        st_sim = _sim_run_fast(
            route_sig=route_sig,
            route_data=route_data,
            runner_weight_kg=wi_weight,
            runner_height_cm=base_ctx["runner_height_cm"],
            mode=mode,
            target_gap_sec_km=wi_gap,
            run_cp_w=base_rcp,
            target_pct_rcp=wi_pct,
            shoe_name=wi_shoe,
            surface_name=base_ctx["surface_name"],
            temp_c=wi_temp,
            humidity_pct=base_ctx["humidity_pct"],
            wind_kmh=wi_wind,
            wind_deg=base_ctx["wind_deg"],
            home_altitude_m=base_ctx["home_altitude_m"],
            strategy=st_code,
            apply_env_derating=base_ctx["apply_env_derating"],
            post_bike_fatigue_pct=base_ctx["post_bike_fatigue_pct"],
            split_interval_km=base_ctx["split_interval_km"],
            hourly_weather=base_ctx.get("hourly_weather"),
            target_re=wi_re,
            base_shoe_name=base_shoe,
            trained_temp_c=base_tr_temp,
            trained_humidity_pct=base_tr_hum,
        )
        ss = st_sim["summary"]
        strat_rows.append({
            "Estrategia": st_lbl.split("(")[0].strip(),
            "Tiempo Total": ss["total_time_str"],
            "1ra Mitad": ss["first_half_str"],
            "2da Mitad": ss["second_half_str"],
            "Ritmo (/km)": ss["avg_pace_str"],
            "Stryd NP (W)": int(ss["norm_power_w"]),
            "rTSS": int(ss["rtss"]),
        })
    st.dataframe(pd.DataFrame(strat_rows), use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════════════
#  PÁGINA PRINCIPAL DEL MODO RUNNING
# ═════════════════════════════════════════════════════════════════════

def render_running_strategy_section(
    member_map: dict,
    nav_btn_css_fn,
):
    """
    Renderiza el módulo completo de Running (Pace / PB 5K-42K y Potencia Stryd) dentro de Estrategias de Carrera.
    """
    from race_strategy_ui import (
        _cached_course_heatmaps,
        _cached_parse_route,
        render_saved_scenarios_library,
    )

    if "run_active_section" not in st.session_state:
        st.session_state["run_active_section"] = "sim"
    active_section = st.session_state["run_active_section"]

    # 3 Botones Grandes y Coloridos (Simulador Running, Qué Pasa Si, Escenarios Guardados)
    nav_css = "<style>" + "".join([
        nav_btn_css_fn("run_nav_sim", active_section == "sim", "linear-gradient(135deg,#00EEFF,#0088FF)", "#00EEFF", "rgba(0,238,255,0.14)", "#04101C", "rgba(0,238,255,0.5)"),
        nav_btn_css_fn("run_nav_comp", active_section == "comp", "linear-gradient(135deg,#FFD700,#FF9100)", "#FFD700", "rgba(255,215,0,0.14)", "#140E00", "rgba(255,215,0,0.5)"),
        nav_btn_css_fn("run_nav_saved", active_section == "saved", "linear-gradient(135deg,#00FF88,#00B86B)", "#00FF88", "rgba(0,255,136,0.14)", "#031A0E", "rgba(0,255,136,0.5)"),
    ]) + "</style>"
    _render_html(nav_css)

    rb1, rb2, rb3 = st.columns(3)
    with rb1:
        lbl = ("● " if active_section == "sim" else "") + "⚙️ Simulador Running (Pace / Stryd)"
        if st.button(lbl, use_container_width=True, key="run_nav_sim"):
            st.session_state["run_active_section"] = "sim"
            st.rerun()
    with rb2:
        lbl = ("● " if active_section == "comp" else "") + "⚔️ Comparador 'Qué Pasa Si...'"
        if st.button(lbl, use_container_width=True, key="run_nav_comp"):
            st.session_state["run_active_section"] = "comp"
            st.rerun()
    with rb3:
        lbl = ("● " if active_section == "saved" else "") + "🗂️ Escenarios Guardados"
        if st.button(lbl, use_container_width=True, key="run_nav_saved"):
            st.session_state["run_active_section"] = "saved"
            st.rerun()

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)

    # ── SELECCIÓN DE DEPORTISTA + ORIGEN DE RUTA (COLUMNAS CONSTANTES) ──
    member_labels = list(member_map.keys())
    col_top1, col_top2 = st.columns([1.15, 1.85])

    with col_top1:
        if "rs_athlete_select" in st.session_state and st.session_state["rs_athlete_select"] in member_map:
            st.session_state["rs_locked_athlete_lbl"] = st.session_state["rs_athlete_select"]
        saved_lbl = st.session_state.get("rs_locked_athlete_lbl")
        def_idx = member_labels.index(saved_lbl) if saved_lbl in member_labels else 0

        def _on_run_ath_change():
            st.session_state["rs_locked_athlete_lbl"] = st.session_state["run_athlete_select"]
            st.session_state["rs_athlete_select"] = st.session_state["run_athlete_select"]
            st.session_state.pop("run_loaded_saved_sc_id", None)

        sel_member_label = st.selectbox(
            "🏃‍♂️ Seleccionar Deportista:",
            member_labels,
            index=def_idx,
            key="run_athlete_select",
            on_change=_on_run_ath_change,
        )
        st.session_state["rs_locked_athlete_lbl"] = sel_member_label
        member_id = member_map[sel_member_label]
        ath = _load_running_athlete_defaults(member_id)

        pb_chips = []
        if ath["pb_5k"]:
            pb_chips.append(f"5K `{ath['pb_5k']}`")
        if ath["pb_10k"]:
            pb_chips.append(f"10K `{ath['pb_10k']}`")
        if ath["pb_21k"]:
            pb_chips.append(f"21K `{ath['pb_21k']}`")
        if ath["pb_42k"]:
            pb_chips.append(f"42K `{ath['pb_42k']}`")
        pb_txt = " · 🏅 PB: " + " / ".join(pb_chips) if pb_chips else " · _(sin PB guardado aún)_"
        rcp_txt = f" · ⚡ Stryd rCP: **`{ath['run_cp_w']:.0f} W`**" if ath.get("run_cp_w") else ""
        alt_txt = f" · 🏔️ Casa: **{ath['altitude_m']} m**" if ath.get("altitude_m") else ""
        st.caption(
            f"📌 CRM: Peso **{ath['weight_kg']:.1f} kg** · Estatura **{ath['height_cm']:.0f} cm**{alt_txt}{rcp_txt}{pb_txt}"
        )

    # Consultar planes guardados de Running de este deportista
    with SessionLocal() as _s_r:
        all_plans = (
            _s_r.query(RaceStrategyScenario)
            .filter(RaceStrategyScenario.member_id == member_id)
            .order_by(RaceStrategyScenario.created_at.desc(), RaceStrategyScenario.id.desc())
            .all()
        )
    run_saved_plans = [
        p for p in all_plans
        if isinstance(_safe_json(p.config_json, {}), dict) and _safe_json(p.config_json, {}).get("sport") == "running"
    ]

    persisted_map = st.session_state.setdefault("run_persisted_route_map", {})
    persisted = persisted_map.get(member_id)
    route_file_bytes = None
    route_file_label = ""
    laps = int(st.session_state.get("run_laps", 1))
    loaded_saved_sc = None

    if active_section in ("sim", "comp"):
        with col_top2:
            src_options = []
            if run_saved_plans:
                src_options.append("🗂️ Plan Running Guardado")
            src_options.extend([
                "📂 Subir archivo FIT / GPX / TCX",
                "🏔️ Demo: Media Maratón Medellín (21.1 km)",
                "🌴 Demo: 10K Rápido Cartagena (10.0 km)",
            ])
            want_saved_id = st.session_state.get("run_loaded_saved_sc_id")
            default_src_idx = 0
            if run_saved_plans and (want_saved_id or (active_section == "comp" and not persisted)):
                default_src_idx = 0
            elif run_saved_plans and active_section == "sim" and persisted:
                default_src_idx = 1

            src_key = f"run_route_src_{member_id}_{active_section}_{1 if want_saved_id else 0}"
            route_source = st.radio(
                "🗺️ Origen del Circuito de Running:",
                src_options,
                index=default_src_idx,
                horizontal=True,
                key=src_key,
            )

            if route_source.startswith("🗂️") and run_saved_plans:
                pmap = {}
                for p in run_saved_plans:
                    pcfg = _safe_json(p.config_json, {})
                    pmap[f"{p.scenario_name} ({format_hms(p.estimated_time_sec)} · {pcfg.get('avg_pace_str', '')}/km)"] = p
                pkeys = list(pmap.keys())
                pidx = 0
                if want_saved_id:
                    for pi, pk in enumerate(pkeys):
                        if pmap[pk].id == want_saved_id:
                            pidx = pi
                            break
                sel_pk = st.selectbox(
                    f"📌 Plan Running base de {ath['name']}:",
                    pkeys,
                    index=pidx,
                    key=f"run_saved_base_{member_id}_{active_section}",
                )
                loaded_saved_sc = pmap[sel_pk]
            else:
                if route_source.startswith("📂"):
                    up_f = st.file_uploader(
                        "Sube la ruta o carrera (.fit, .gpx, .tcx, .zip de Garmin/Strava/Coros/Stryd):",
                        type=["fit", "gpx", "tcx", "zip", "gz"],
                        key=f"run_gpx_up_{member_id}",
                    )
                    if up_f is not None:
                        route_file_bytes = up_f.getvalue()
                        route_file_label = up_f.name
                        persisted_map[member_id] = (route_file_bytes, route_file_label, True)
                    elif persisted and persisted[2]:
                        route_file_bytes, route_file_label, _ = persisted
                        st.caption(f"📎 Circuito Running en memoria: **`{route_file_label}`**")
                        if st.button("✕ Quitar archivo en memoria", key=f"run_clear_pers_{member_id}"):
                            persisted_map.pop(member_id, None)
                            st.rerun()
                elif "Medellín" in route_source:
                    route_file_bytes, route_file_label = generate_demo_running_gpx("medellin_21k")
                    persisted_map[member_id] = (route_file_bytes, route_file_label, False)
                else:
                    route_file_bytes, route_file_label = generate_demo_running_gpx("cartagena_10k")
                    persisted_map[member_id] = (route_file_bytes, route_file_label, False)

                laps = st.number_input(
                    "🔁 Vueltas al circuito:",
                    min_value=1,
                    max_value=15,
                    value=1,
                    step=1,
                    key="run_laps",
                )
    else:
        with col_top2:
            st.caption("🗂️ Biblioteca unificada de planes guardados (Running y Ciclismo). Pulsa **⚔️ Abrir en 'Qué Pasa Si...'** para simular mejoras.")

    # ── SECCIÓN DIRECTA: ESCENARIOS GUARDADOS ───────────────────────
    if active_section == "saved":
        render_saved_scenarios_library(
            member_id=member_id,
            athlete_name=ath["name"],
            allow_delete=True,
            is_athlete_portal=False,
            key_prefix="admin_run_tab",
        )
        return

    # ── PARSEAR RUTA ────────────────────────────────────────────────
    route_data = None
    route_sig = None
    saved_ctx = None
    if loaded_saved_sc is not None:
        route_sig, route_data, saved_ctx = _build_run_ctx_from_saved(loaded_saved_sc, ath)
    elif route_file_bytes:
        fhash = hashlib.sha256(route_file_bytes).hexdigest()[:16]
        route_sig = f"run_{fhash}_{route_file_label}_{int(laps)}"
        try:
            route_data = _cached_parse_route(fhash, route_file_bytes, route_file_label, int(laps))
        except Exception as err:
            st.error(f"⚠️ No se pudo leer el archivo `{route_file_label}`: {err}")
            return

    if not route_data:
        st.info(
            "📂 Para simular una carrera a pie (5K, 10K, 21K, Maratón o Trail), sube arriba un archivo `.fit`, `.gpx`, `.tcx` o `.zip` "
            "(o selecciona una ruta Demo: **Medellín 21.1K** o **Cartagena 10K**)."
        )
        render_saved_scenarios_library(
            member_id=member_id,
            athlete_name=ath["name"],
            allow_delete=True,
            is_athlete_portal=False,
            key_prefix="admin_run_home",
        )
        return

    # ── BANNER DEL CIRCUITO + PLANIMETRÍA & ALTIMETRÍA SINCRONIZADAS ─
    _render_html(
        f"""
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap:8px; margin:6px 0 12px 0;">
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,255,136,0.28); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">🏃‍♂️ Circuito Running</div>
                <div style="color:#FFFFFF; font-size:0.86rem; font-weight:800; margin-top:2px;">{html.escape(route_data['route_name'][:32])}</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Distancia Total</div>
                <div style="color:#00EEFF; font-size:1.1rem; font-weight:900;">{route_data['total_distance_km']:.2f} km</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(255,59,48,0.32); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">⛰️ Desnivel Positivo</div>
                <div style="color:#FF5252; font-size:1.1rem; font-weight:900;">+{route_data['elevation_gain_m']:.0f} m</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(56,189,248,0.32); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">⬇️ Desnivel Negativo</div>
                <div style="color:#38BDF8; font-size:1.1rem; font-weight:900;">−{float(route_data.get('elevation_loss_m', 0)):.0f} m</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(255,215,0,0.28); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Altitud Media / Máx</div>
                <div style="color:#FFD700; font-size:1.05rem; font-weight:900;">{route_data['avg_ele_m']:.0f} / {route_data['max_ele_m']:.0f} m</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(192,132,252,0.28); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Pendiente Máx / Mín</div>
                <div style="color:#C084FC; font-size:1.05rem; font-weight:900;">+{route_data['max_grade_pct']:.1f}% / {route_data['min_grade_pct']:.1f}%</div>
            </div>
        </div>
        """
    )

    if active_section == "sim":
        with st.expander("🗺️ Planimetría GPS y Altimetría del Circuito — Cursor Sincronizado & Zoom (+D / −D)", expanded=True):
            _fig_e_r, t_stats_r, _fig_p_r = _cached_course_heatmaps(route_sig, route_data)
            _render_html(
                f"""
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:8px;
                            background:rgba(14,18,28,0.9); border:1px solid rgba(255,255,255,0.09); border-radius:10px;
                            padding:8px 12px; margin-bottom:8px; font-size:0.75rem; color:#CBD5E1;">
                    <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
                        <span style="font-weight:800; color:#94A3B8;">MAPA DE CALOR:</span>
                        <span style="background:rgba(0,176,255,0.18); color:#00B0FF; padding:2px 7px; border-radius:6px; font-weight:700;">🔵 Descenso Veloz</span>
                        <span style="background:rgba(0,230,118,0.16); color:#00E676; padding:2px 7px; border-radius:6px; font-weight:700;">🟢 Bajada</span>
                        <span style="background:rgba(100,116,139,0.22); color:#CBD5E1; padding:2px 7px; border-radius:6px; font-weight:700;">⚪ Llano</span>
                        <span style="background:rgba(255,214,0,0.16); color:#FFD600; padding:2px 7px; border-radius:6px; font-weight:700;">🟡 Repecho</span>
                        <span style="background:rgba(255,138,0,0.18); color:#FF8A00; padding:2px 7px; border-radius:6px; font-weight:700;">🟠 Subida</span>
                        <span style="background:rgba(255,46,67,0.2); color:#FF4D5E; padding:2px 7px; border-radius:6px; font-weight:700;">🔴 Subida Fuerte</span>
                    </div>
                    <div style="display:flex; flex-wrap:wrap; gap:10px; font-weight:700;">
                        <span style="color:#FF6B6B;">⛰️ Subida: {t_stats_r.get('climb_km', 0)} km</span>
                        <span style="color:#94A3B8;">➡️ Llano: {t_stats_r.get('flat_km', 0)} km</span>
                        <span style="color:#00E676;">⬇️ Bajada: {t_stats_r.get('descent_km', 0)} km</span>
                    </div>
                </div>
                """
            )
            render_synced_elevation_planimetry_widget(route_data, height=475)

    # ── COMPARADOR DIRECTO SI SE ELIGIÓ 'QUÉ PASA SI...' ────────────
    if active_section == "comp":
        ctx_use = saved_ctx or st.session_state.get(f"run_last_ctx_{member_id}")
        if ctx_use is None or ctx_use.get("route_sig") != route_sig:
            # Crear contexto rápido por defecto con los datos del atleta (PB si tiene, o 4:30/km)
            dist_km = float(route_data["total_distance_km"])
            default_gap = 270.0
            default_rcp = ath.get("run_cp_w") or 285.0
            for d_k, f_k in [(10.0, "pb_10k"), (21.0975, "pb_21k"), (5.0, "pb_5k"), (42.195, "pb_42k")]:
                t_pb = parse_time_str_to_sec(ath.get(f_k))
                if t_pb:
                    pr = estimate_run_metrics_from_pb(d_k, t_pb, ath["weight_kg"], ath["height_cm"], dist_km)
                    default_gap = pr["target_flat_pace_sec_km"]
                    if not ath.get("run_cp_w"):
                        default_rcp = pr["est_rcp_w"]
                    break
            shoe0 = list(RUNNING_SHOES.keys())[0]
            surf0 = list(RUNNING_SURFACES.keys())[1]
            sp_k = 1.0 if dist_km <= 12.0 else (2.0 if dist_km <= 26.0 else 5.0)
            sim0 = _sim_run_fast(
                route_sig=route_sig, route_data=route_data,
                runner_weight_kg=ath["weight_kg"], runner_height_cm=ath["height_cm"],
                mode="pace", target_gap_sec_km=default_gap, run_cp_w=default_rcp,
                target_pct_rcp=92.5, shoe_name=shoe0, surface_name=surf0,
                temp_c=20.0, humidity_pct=65.0, wind_kmh=8.0, wind_deg=45.0,
                home_altitude_m=ath["altitude_m"], strategy="even_effort",
                apply_env_derating=True, post_bike_fatigue_pct=0.0, split_interval_km=sp_k,
            )
            ctx_use = {
                "route_sig": route_sig, "sim": sim0, "s": sim0["summary"], "nut": sim0["nutrition"],
                "runner_weight_kg": ath["weight_kg"], "runner_height_cm": ath["height_cm"],
                "mode": "pace", "target_gap_sec_km": default_gap, "run_cp_w": default_rcp,
                "target_pct_rcp": 92.5, "shoe_name": shoe0, "surface_name": surf0,
                "temp_c": 20.0, "humidity_pct": 65.0, "wind_kmh": 8.0, "wind_deg": 45.0,
                "home_altitude_m": ath["altitude_m"], "strategy_code": "even_effort",
                "strat_label": list(RUNNING_STRATEGIES.keys())[0], "apply_env_derating": True,
                "post_bike_fatigue_pct": 0.0, "split_interval_km": sp_k,
                "hourly_weather": None, "race_date": str(date.today()), "race_time_str": "07:00",
                "weather_source": "Manual",
            }
        _render_running_what_if_fragment(route_sig, route_data, ctx_use, member_id, ath["name"])
        return

    # ═════════════════════════════════════════════════════════════════
    #  SIMULADOR DE RUNNING (PACE/PB Y POTENCIA STRYD)
    # ═════════════════════════════════════════════════════════════════
    dist_km = float(route_data["total_distance_km"])

    # Auto-detectar preset sugerido por distancia del circuito
    preset_keys = list(RUNNING_RACE_PRESETS.keys())
    if dist_km <= 6.5:
        auto_preset_idx = 0  # 5K
    elif dist_km <= 14.0:
        auto_preset_idx = 1  # 10K
    elif dist_km <= 27.0:
        auto_preset_idx = 2  # 21.1K
    else:
        auto_preset_idx = 3  # 42.2K

    # Selector de Modo de Planificación (Pace vs Stryd)
    mode_lbl = st.radio(
        "🎛️ Modo de Planificación de Running:",
        [
            "⏱️ Por Ritmo / Pace (desde Marca Personal 5K–42K o Ritmo GAP)",
            "⚡ Por Potencia en Running (Stryd / Garmin / Coros rCP)",
        ],
        horizontal=True,
        key=f"run_plan_mode_{member_id}",
    )
    plan_mode = "stryd" if mode_lbl.startswith("⚡") else "pace"

    # ── PANEL DE CONFIGURACIÓN EN 3 BLOQUES LIMPIOS ─────────────────
    col_cfg1, col_cfg2, col_cfg3 = st.columns([1.15, 1.15, 1.0])

    with col_cfg1:
        st.markdown("##### 🏃‍♂️ Fisiología, PB y Objetivo")
        sel_preset_lbl = st.selectbox(
            "Tipo de Carrera / Fatiga:",
            preset_keys,
            index=auto_preset_idx,
            key=f"run_preset_{member_id}_{route_sig}",
        )
        preset_meta = RUNNING_RACE_PRESETS[sel_preset_lbl]
        st.caption(f"💡 {preset_meta['desc']}")

        wt_label = "⚖️ Peso configurado en Stryd (kg)" if plan_mode == "stryd" else "⚖️ Peso del Atleta (kg)"
        wt_help = (
            "MUY IMPORTANTE: Usa el peso exacto que tiene configurado el deportista en la app de Stryd para que W/kg y Target RE coincidan al 100% con su CP."
            if plan_mode == "stryd"
            else "Peso actual del atleta en kg."
        )
        runner_weight = st.number_input(
            wt_label,
            min_value=40.0, max_value=120.0,
            value=round(float(ath["weight_kg"]), 1),
            step=0.5,
            help=wt_help,
            key=f"run_wt_{member_id}",
        )
        runner_height = st.number_input(
            "📏 Estatura (cm)",
            min_value=140.0, max_value=215.0,
            value=round(float(ath["height_cm"]), 0),
            step=1.0,
            key=f"run_ht_{member_id}",
        )
        home_alt = st.number_input(
            "🏔️ Altitud donde entrena ('Trained At', m)",
            min_value=0, max_value=4500,
            value=int(ath.get("altitude_m") or 0),
            step=50,
            help="Altitud habitual de entrenamiento (igual que 'Trained At' en Stryd). Solo penaliza si el circuito está más alto.",
            key=f"run_home_alt_{member_id}",
        )

    # Resolver PB / Ritmo GAP / Stryd rCP en columna 2
    with col_cfg2:
        if plan_mode == "pace":
            st.markdown("##### 🏅 Proyección por Marca Personal (PB) o Ritmo")
            pace_src = st.radio(
                "Fuente del ritmo:",
                ["🏅 Desde mejor 5K / 10K / 21K / 42K", "🎯 Ingresar Ritmo GAP manual"],
                horizontal=True,
                key=f"run_pace_src_{member_id}",
            )

            # Detectar qué PB guardado tiene el atleta para preseleccionarlo
            pb_options = list(PB_DISTANCES_KM.keys())
            pb_field_map = {"5K": "pb_5k", "10K": "pb_10k", "21K (Media Maratón)": "pb_21k", "42K (Maratón)": "pb_42k"}
            pb_defaults_demo = {"5K": "19:30", "10K": "42:00", "21K (Media Maratón)": "1:33:00", "42K (Maratón)": "3:18:00"}

            def_pb_idx = 1  # 10K por defecto
            for idx_o, opt_k in enumerate(pb_options):
                if ath.get(pb_field_map[opt_k]):
                    def_pb_idx = idx_o
                    break

            if pace_src.startswith("🏅"):
                sel_pb_dist_lbl = st.selectbox(
                    "Distancia de tu Marca Personal (PB) de referencia:",
                    pb_options,
                    index=def_pb_idx,
                    key=f"run_pb_dist_{member_id}",
                )
                pb_d_km = PB_DISTANCES_KM[sel_pb_dist_lbl]
                saved_pb_str = ath.get(pb_field_map[sel_pb_dist_lbl]) or pb_defaults_demo[sel_pb_dist_lbl]
                pb_in_str = st.text_input(
                    f"⏱️ Tu mejor tiempo en {sel_pb_dist_lbl} (MM:SS o H:MM:SS):",
                    value=saved_pb_str,
                    key=f"run_pb_val_{member_id}_{sel_pb_dist_lbl}",
                )
                pb_sec = parse_time_str_to_sec(pb_in_str)
                if not pb_sec:
                    st.warning("Formato no reconocido. Usa `MM:SS` (ej. `42:00`) o `H:MM:SS` (ej. `1:32:00`).")
                    pb_sec = parse_time_str_to_sec(pb_defaults_demo[sel_pb_dist_lbl])

                proj = estimate_run_metrics_from_pb(
                    pb_dist_km=pb_d_km,
                    pb_time_sec=pb_sec,
                    weight_kg=runner_weight,
                    height_cm=runner_height,
                    target_course_km=dist_km,
                )
                effort_adj_pct = st.slider(
                    "🎚️ Factor de Objetivo vs PB (100% = al tope de tu PB):",
                    min_value=82,
                    max_value=104,
                    value=100,
                    step=1,
                    help="Ej: 96% para salir conservador o carrera B; 100% para intentar marca personal ajustada al perfil del circuito.",
                    key=f"run_pb_eff_{member_id}_{route_sig}",
                )
                target_gap_sec_km = proj["target_flat_pace_sec_km"] / (effort_adj_pct / 100.0)
                run_cp_w = float(ath.get("run_cp_w") or proj["est_rcp_w"])
                target_pct_rcp = float(preset_meta["default_pct_rcp"])

                # Mostrar equivalencias y botón para guardar este PB en la ficha del deportista
                eq = proj["equivalents"]
                _render_html(
                    f"""
                    <div style="background:rgba(0,255,136,0.07); border:1px solid rgba(0,255,136,0.3);
                                border-radius:10px; padding:8px 10px; margin-top:4px; font-size:0.76rem; color:#CBD5E1;">
                        <b style="color:#00FF88;">📊 Proyección Llano (Riegel):</b>
                        5K <b>{eq['5K']['time_str']}</b> · 10K <b>{eq['10K']['time_str']}</b> · 21K <b>{eq['21.1K']['time_str']}</b> · 42K <b>{eq['42.2K']['time_str']}</b><br>
                        🎯 Umbral LT2 est.: <b>{proj['lt2_pace_str']} /km</b> · ⚡ Stryd rCP est.: <b>{proj['est_rcp_w']:.0f} W</b> ({proj['est_rcp_wkg']:.2f} W/kg) · GAP obj: <b>{format_pace(target_gap_sec_km)} /km</b>
                    </div>
                    """
                )
                if pb_in_str.strip() != (ath.get(pb_field_map[sel_pb_dist_lbl]) or ""):
                    if st.button(f"💾 Guardar este PB ({sel_pb_dist_lbl}) en ficha de {ath['name']}", key=f"run_save_pb_{member_id}_{sel_pb_dist_lbl}"):
                        with SessionLocal() as _s_pb:
                            m_obj = _s_pb.query(Member).filter(Member.id == member_id).first()
                            if m_obj:
                                setattr(m_obj, pb_field_map[sel_pb_dist_lbl], pb_in_str.strip())
                                _s_pb.commit()
                        _load_running_athlete_defaults.clear()
                        st.toast("Marca personal guardada en el perfil del atleta!", icon="✅")
            else:
                manual_pace_str = st.text_input(
                    "🎯 Ritmo GAP Objetivo en Llano (MM:SS /km):",
                    value="4:30",
                    help="Ritmo objetivo equivalente en llano (ej. `4:15`, `4:45`, `5:20`). En subidas se ralentizará proporcionalmente al desnivel.",
                    key=f"run_man_pace_{member_id}",
                )
                p_sec = parse_time_str_to_sec(manual_pace_str)
                target_gap_sec_km = float(p_sec) if (p_sec and 150 <= p_sec <= 900) else 270.0
                # Estimar rCP si no tiene en perfil
                pr_m = estimate_run_metrics_from_pb(dist_km, target_gap_sec_km * dist_km, runner_weight, runner_height, dist_km)
                run_cp_w = float(ath.get("run_cp_w") or pr_m["est_rcp_w"])
                target_pct_rcp = float(preset_meta["default_pct_rcp"])
                st.caption(f"⚡ Stryd rCP de referencia: **`{run_cp_w:.0f} W`** · GAP base: **`{format_pace(target_gap_sec_km)} /km`**")
        else:
            st.markdown("##### ⚡ Potencia Running (Stryd / Palladino RE)")
            # Estimar rCP inicial si no tiene uno guardado
            init_rcp = ath.get("run_cp_w")
            if not init_rcp:
                for d_k, f_k in [(10.0, "pb_10k"), (21.0975, "pb_21k"), (5.0, "pb_5k")]:
                    t_pb = parse_time_str_to_sec(ath.get(f_k))
                    if t_pb:
                        init_rcp = estimate_run_metrics_from_pb(d_k, t_pb, runner_weight, runner_height, dist_km)["est_rcp_w"]
                        break
            init_rcp = float(init_rcp or round(runner_weight * 3.80))

            run_cp_w = st.number_input(
                "⚡ Potencia Crítica Running rCP (W)",
                min_value=100.0, max_value=600.0,
                value=round(init_rcp, 0),
                step=1.0,
                help="Critical Power (CP) reportado por Stryd, Garmin Running Power o Coros.",
                key=f"run_rcp_in_{member_id}",
            )
            rcp_wkg = run_cp_w / max(38.0, runner_weight)

            # Target RE (Running Effectiveness Stryd / Steve Palladino = velocidad / (W/kg))
            re_key = f"run_re_in_{member_id}"
            pend_re_key = f"run_re_pending_{member_id}"
            if pend_re_key in st.session_state:
                st.session_state[re_key] = float(st.session_state.pop(pend_re_key))

            auto_re = ath.get("run_re") or estimate_default_stryd_re(rcp_wkg)
            target_re = st.number_input(
                "🎯 Running Effectiveness — Target RE (Stryd / Palladino)",
                min_value=0.75, max_value=1.15,
                value=round(float(auto_re), 2),
                step=0.01,
                format="%.2f",
                help="RE = Velocidad (m/s) ÷ (W/kg). Igual que 'Target RE' en SuperPower Calculator de Steve Palladino (típico: 0.92–0.98 en asfalto).",
                key=re_key,
            )
            st.caption(f"rCP: **`{rcp_wkg:.2f} W/kg`** · RE sugerido por W/kg: **`{estimate_default_stryd_re(rcp_wkg):.2f}`**")

            # Sliders bidireccionalmente vinculados: Potencia Objetivo (W) <-> % rCP
            def_pct = float(preset_meta["default_pct_rcp"])
            p_s_key = f"run_stryd_w_{member_id}_{route_sig}_{sel_preset_lbl}"
            pct_s_key = f"run_stryd_pct_{member_id}_{route_sig}_{sel_preset_lbl}"
            w_min = max(80, int(round(run_cp_w * 0.60)))
            w_max = min(650, int(round(run_cp_w * 1.15)))

            if p_s_key not in st.session_state:
                st.session_state[p_s_key] = int(round(max(w_min, min(w_max, run_cp_w * (def_pct / 100.0)))))
            if pct_s_key not in st.session_state:
                st.session_state[pct_s_key] = round(def_pct, 1)

            def _sync_run_from_w():
                wv = float(st.session_state[p_s_key])
                st.session_state[pct_s_key] = round(max(60.0, min(115.0, (wv / max(80.0, run_cp_w)) * 100.0)), 1)

            def _sync_run_from_pct():
                pv = float(st.session_state[pct_s_key])
                st.session_state[p_s_key] = int(round(max(w_min, min(w_max, run_cp_w * (pv / 100.0)))))

            st.session_state[p_s_key] = int(max(w_min, min(w_max, int(st.session_state[p_s_key]))))

            st.slider(
                "⚡ Potencia Objetivo en Carrera (W)",
                min_value=w_min, max_value=w_max,
                step=1,
                key=p_s_key,
                on_change=_sync_run_from_w,
            )
            target_pct_rcp = st.slider(
                "🎯 Intensidad Objetivo (% rCP)",
                min_value=60.0, max_value=115.0,
                step=0.2,
                key=pct_s_key,
                on_change=_sync_run_from_pct,
            )
            target_gap_sec_km = 270.0

            need_save_stryd = (
                ath.get("run_cp_w") is None
                or abs(float(ath["run_cp_w"]) - run_cp_w) >= 1.0
                or ath.get("run_re") is None
                or abs(float(ath["run_re"]) - target_re) >= 0.005
            )
            if need_save_stryd:
                if st.button(f"💾 Guardar rCP ({run_cp_w:.0f} W) y RE ({target_re:.2f}) en ficha", key=f"run_save_rcp_{member_id}"):
                    with SessionLocal() as _s_cp:
                        m_obj = _s_cp.query(Member).filter(Member.id == member_id).first()
                        if m_obj:
                            m_obj.run_cp_w = float(run_cp_w)
                            m_obj.run_re = float(target_re)
                            _s_cp.commit()
                    _load_running_athlete_defaults.clear()
                    st.toast("Stryd rCP y Target RE guardados en el perfil del atleta!", icon="✅")

            with st.expander("🔧 Calcular RE real desde un entreno o carrera Stryd", expanded=False):
                st.caption("Ingresa la potencia media y el ritmo medio de un bloque/carrera en llano con Stryd:")
                cal_w = st.number_input("Potencia media del bloque (W):", min_value=80.0, max_value=600.0, value=round(run_cp_w * 0.93, 0), step=1.0, key=f"run_cal_w_{member_id}")
                cal_p_str = st.text_input("Ritmo medio en llano (MM:SS /km):", value="5:24", key=f"run_cal_p_{member_id}")
                cal_p_sec = parse_time_str_to_sec(cal_p_str)
                if cal_p_sec and 150 <= cal_p_sec <= 900:
                    re_calc = calc_stryd_re_from_workout(cal_w, cal_p_sec, runner_weight)
                    st.markdown(f"✅ **RE calculado:** **`{re_calc:.3f}`** *(usando {runner_weight:.1f} kg, {cal_w:.0f} W y {format_pace(cal_p_sec)}/km)*")
                    if st.button(f"Aplicar RE = {re_calc:.2f} al Simulador", key=f"run_apply_re_{member_id}"):
                        st.session_state[pend_re_key] = round(re_calc, 2)
                        st.rerun()

    with col_cfg3:
        st.markdown("##### 👟 Calzado, Terreno y Estrategia")
        shoe_name = st.selectbox(
            "👟 Zapatillas de Carrera:",
            list(RUNNING_SHOES.keys()),
            index=0,
            key=f"run_shoe_{member_id}",
        )
        surface_name = st.selectbox(
            "🛣️ Superficie del Circuito:",
            list(RUNNING_SURFACES.keys()),
            index=1,
            key=f"run_surf_{member_id}",
        )
        strat_label = st.selectbox(
            "🧠 Estrategia de Pacing:",
            list(RUNNING_STRATEGIES.keys()),
            index=0,
            key=f"run_strat_{member_id}",
        )
        strategy_code = RUNNING_STRATEGIES[strat_label]
        split_opts = [1.0, 2.0, 5.0]
        def_sp = preset_meta["split_km"]
        split_interval_km = st.selectbox(
            "📏 Intervalo de Splits (tabla):",
            split_opts,
            index=split_opts.index(def_sp) if def_sp in split_opts else 0,
            format_func=lambda x: f"Cada {int(x)} km",
            key=f"run_split_km_{member_id}_{route_sig}",
        )
        apply_env_derating = st.checkbox(
            "🌡️ Ajustar rendimiento por Calor, Humedad y Altitud",
            value=True,
            help="Ajusta el ritmo/potencia sostenible según temperatura, humedad y altitud del circuito respecto a donde vive el atleta.",
            key=f"run_env_chk_{member_id}",
        )

    # ── PANEL DE CLIMA REAL / HISTÓRICO (OPEN-METEO) ────────────────
    with st.expander("🌦️ Condiciones Climáticas y Viento (Pronóstico Open-Meteo o Manual)", expanded=False):
        cw1, cw2, cw3 = st.columns([1, 1, 1.3])
        with cw1:
            race_date = st.date_input("📅 Fecha de carrera:", value=date.today(), key=f"run_w_date_{member_id}")
        with cw2:
            race_time_str = st.selectbox(
                "⏰ Hora de salida:",
                ["05:30", "06:00", "06:30", "07:00", "07:30", "08:00", "09:00", "16:00", "17:00"],
                index=3,
                key=f"run_w_time_{member_id}",
            )
        with cw3:
            st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
            if st.button("🌦️ Buscar Clima Real del Circuito (Open-Meteo)", use_container_width=True, key=f"run_w_btn_{member_id}_{route_sig}"):
                seg0 = route_data["segments"][0]
                try:
                    w_res = fetch_race_weather(
                        lat=float(seg0["lat"]),
                        lon=float(seg0["lon"]),
                        race_date=race_date,
                        start_hour=int(race_time_str.split(":")[0]),
                        duration_hours=4,
                    )
                    if w_res and w_res.get("ok") and w_res.get("summary"):
                        ws_sum = w_res["summary"]
                        st.session_state[f"run_w_data_{route_sig}"] = w_res
                        st.session_state[f"run_temp_sl_{route_sig}"] = max(4.0, min(40.0, round(float(ws_sum.get("temp_c", 20.0)), 1)))
                        st.session_state[f"run_hum_sl_{route_sig}"] = max(20, min(100, int(round(float(ws_sum.get("humidity_pct", 65.0))))))
                        st.session_state[f"run_wind_sl_{route_sig}"] = max(0.0, min(45.0, round(float(ws_sum.get("wind_kmh", 8.0)), 1)))
                        st.session_state[f"run_wdeg_sl_{route_sig}"] = max(0, min(355, int(round(float(ws_sum.get("wind_deg", 45.0)) / 15.0) * 15)))
                        st.toast(f"Clima cargado: {w_res.get('source_label', 'Open-Meteo')}", icon="🌦️")
                    else:
                        st.warning(f"⚠️ No se pudo obtener el clima: {w_res.get('error', 'sin respuesta')}. Puedes ajustarlo manualmente.")
                except Exception as w_err:
                    st.warning(f"⚠️ No se pudo consultar Open-Meteo ({w_err}). Ajusta los controles manualmente.")

        w_saved = st.session_state.get(f"run_w_data_{route_sig}")
        wc1, wc2, wc3, wc4 = st.columns(4)
        with wc1:
            temp_c = st.slider(
                "🌡️ Temperatura (°C)", 4.0, 40.0,
                value=float(st.session_state.get(f"run_temp_{route_sig}", 20.0)),
                step=0.5, key=f"run_temp_sl_{route_sig}",
            )
        with wc2:
            humidity_pct = st.slider(
                "💧 Humedad (%)", 20, 100,
                value=int(st.session_state.get(f"run_hum_{route_sig}", 65)),
                step=5, key=f"run_hum_sl_{route_sig}",
            )
        with wc3:
            wind_kmh = st.slider(
                "💨 Viento (km/h)", 0.0, 45.0,
                value=float(st.session_state.get(f"run_wind_{route_sig}", 8.0)),
                step=1.0, key=f"run_wind_sl_{route_sig}",
            )
        with wc4:
            wind_deg = st.slider(
                "🧭 Dirección Viento (°)", 0, 355,
                value=int(st.session_state.get(f"run_wdeg_{route_sig}", 45)),
                step=15,
                help="Dirección DESDE donde sopla el viento (0°=Norte, 90°=Este, 180°=Sur, 270°=Oeste).",
                key=f"run_wdeg_sl_{route_sig}",
            )

        wc4_out = st.columns(2)
        with wc4_out[0]:
            trained_temp_c = st.slider(
                "🏠 Temp. habitual de entrenamiento ('Trained At', °C)",
                5.0, 35.0,
                value=19.0,
                step=1.0,
                help="Temperatura donde el atleta entrena habitualmente (estilo Stryd). Solo penaliza si la carrera es más calurosa que su clima de entreno.",
                key=f"run_tr_temp_{member_id}",
            )
        with wc4_out[1]:
            trained_humidity_pct = st.slider(
                "🏠 Humedad habitual de entrenamiento ('Trained At', %)",
                20, 100,
                value=75,
                step=5,
                help="Humedad habitual de entrenamiento (estilo Stryd).",
                key=f"run_tr_hum_{member_id}",
            )

    # Resolver target_re si estamos en modo Pace
    if plan_mode == "pace":
        target_re = float(ath.get("run_re") or estimate_default_stryd_re(run_cp_w / max(38.0, runner_weight)))

    # ── EJECUTAR SIMULACIÓN DE RUNNING ──────────────────────────────
    hourly_w = w_saved.get("hourly") if isinstance(w_saved, dict) else None
    sim = _sim_run_fast(
        route_sig=route_sig,
        route_data=route_data,
        runner_weight_kg=runner_weight,
        runner_height_cm=runner_height,
        mode=plan_mode,
        target_gap_sec_km=target_gap_sec_km,
        run_cp_w=run_cp_w,
        target_pct_rcp=target_pct_rcp,
        shoe_name=shoe_name,
        surface_name=surface_name,
        temp_c=temp_c,
        humidity_pct=humidity_pct,
        wind_kmh=wind_kmh,
        wind_deg=wind_deg,
        home_altitude_m=home_alt,
        strategy=strategy_code,
        apply_env_derating=apply_env_derating,
        post_bike_fatigue_pct=float(preset_meta["post_bike_fatigue"]) * 100.0,
        split_interval_km=float(split_interval_km),
        hourly_weather=hourly_w,
        target_re=target_re,
        base_shoe_name=shoe_name,
        trained_temp_c=trained_temp_c,
        trained_humidity_pct=trained_humidity_pct,
    )
    s = sim["summary"]
    nut = sim["nutrition"]

    # Guardar contexto en sesión para transición instantánea al Comparador 'Qué Pasa Si...'
    active_ctx = {
        "route_sig": route_sig,
        "sim": sim,
        "s": s,
        "nut": nut,
        "runner_weight_kg": runner_weight,
        "runner_height_cm": runner_height,
        "mode": plan_mode,
        "target_gap_sec_km": target_gap_sec_km,
        "run_cp_w": run_cp_w,
        "target_pct_rcp": target_pct_rcp,
        "target_re": target_re,
        "shoe_name": shoe_name,
        "surface_name": surface_name,
        "temp_c": temp_c,
        "humidity_pct": humidity_pct,
        "wind_kmh": wind_kmh,
        "wind_deg": wind_deg,
        "home_altitude_m": home_alt,
        "trained_temp_c": trained_temp_c,
        "trained_humidity_pct": trained_humidity_pct,
        "strategy_code": strategy_code,
        "strat_label": strat_label,
        "apply_env_derating": apply_env_derating,
        "post_bike_fatigue_pct": float(preset_meta["post_bike_fatigue"]) * 100.0,
        "split_interval_km": float(split_interval_km),
        "hourly_weather": hourly_w,
        "race_date": str(race_date),
        "race_time_str": str(race_time_str),
        "weather_source": w_saved.get("source_label", "Manual") if isinstance(w_saved, dict) else "Manual",
    }
    st.session_state[f"run_last_ctx_{member_id}"] = active_ctx

    # ── KPI HERO CARDS DE RUNNING ───────────────────────────────────
    env = s["env_info"]
    env_notes = []
    if apply_env_derating:
        if env["heat_penalty_pct"] >= 0.5:
            env_notes.append(f"🌡️ Calor/HR -{env['heat_penalty_pct']:.1f}%")
        if env["alt_penalty_pct"] >= 0.5:
            env_notes.append(f"🏔️ Altura -{env['alt_penalty_pct']:.1f}%")
        if env["sea_level_bonus_pct"] >= 0.3:
            env_notes.append(f"🌊 Nivel del mar +{env['sea_level_bonus_pct']:.1f}%")
    env_badge = " · ".join(env_notes) if env_notes else "Condiciones neutras"

    split_diff_s = float(s["split_diff_sec"])
    split_tag = (
        f"Negative split {format_time_delta(split_diff_s)}" if split_diff_s < -5
        else (f"Positive split {format_time_delta(split_diff_s)}" if split_diff_s > 5 else "Split parejo")
    )

    _render_html(
        f"""
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:9px; margin:10px 0 14px 0;">
            <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid #00EEFF; border-radius:12px; padding:12px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.66rem; font-weight:800; text-transform:uppercase;">⏱️ Tiempo Estimado</div>
                <div style="color:#00EEFF; font-size:1.55rem; font-weight:900; margin-top:2px;">{s['total_time_str']}</div>
                <div style="color:#94A3B8; font-size:0.68rem;">1ª {s['first_half_str']} · 2ª {s['second_half_str']} ({split_tag})</div>
            </div>
            <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(0,255,136,0.55); border-radius:12px; padding:12px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.66rem; font-weight:800; text-transform:uppercase;">🏃‍♂️ Ritmo Medio</div>
                <div style="color:#00FF88; font-size:1.55rem; font-weight:900; margin-top:2px;">{s['avg_pace_str']} <span style="font-size:0.75rem;">/km</span></div>
                <div style="color:#94A3B8; font-size:0.68rem;">Vel. media: {s['avg_speed_kmh']:.1f} km/h</div>
            </div>
            <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(56,189,248,0.45); border-radius:12px; padding:12px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.66rem; font-weight:800; text-transform:uppercase;">🏔️ Ritmo GAP (Llano)</div>
                <div style="color:#38BDF8; font-size:1.55rem; font-weight:900; margin-top:2px;">{s['avg_gap_str']} <span style="font-size:0.75rem;">/km</span></div>
                <div style="color:#94A3B8; font-size:0.68rem;">{env_badge}</div>
            </div>
            <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,215,0,0.55); border-radius:12px; padding:12px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.66rem; font-weight:800; text-transform:uppercase;">⚡ Potencia Stryd</div>
                <div style="color:#FFD700; font-size:1.55rem; font-weight:900; margin-top:2px;">{s['avg_power_w']:.0f} <span style="font-size:0.75rem;">W</span></div>
                <div style="color:#94A3B8; font-size:0.68rem;">{s['w_per_kg']:.2f} W/kg · {s['pct_rcp']:.0f}% rCP · RE {s['course_re']:.2f}</div>
            </div>
            <div style="background:linear-gradient(145deg,#111827,#0B1120); border:1.5px solid rgba(255,170,0,0.5); border-radius:12px; padding:12px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.66rem; font-weight:800; text-transform:uppercase;">🥤 Nutrición & Geles</div>
                <div style="color:#FFAA00; font-size:1.45rem; font-weight:900; margin-top:2px;">{nut['gels_count']} <span style="font-size:0.75rem;">geles</span></div>
                <div style="color:#94A3B8; font-size:0.68rem;">{nut['cho_g_per_h']} g/h · {nut['fluid_total_l']} L · ~{nut['kcal_est']} kcal</div>
            </div>
        </div>
        """
    )

    # ── TABLA DE SENSIBILIDAD POR RE (ESTILO STEVE PALLADINO / STRYD) ─
    if plan_mode == "stryd":
        with st.expander(f"📊 Tabla de Sensibilidad por Target RE (estilo Steve Palladino SuperPower Calculator · Base RE {target_re:.2f})", expanded=True):
            re_sens_rows = build_re_sensitivity_table(
                route_data=route_data,
                runner_weight_kg=runner_weight,
                runner_height_cm=runner_height,
                run_cp_w=run_cp_w,
                target_pct_rcp=target_pct_rcp,
                base_target_re=target_re,
                shoe_name=shoe_name,
                surface_name=surface_name,
                temp_c=temp_c,
                humidity_pct=humidity_pct,
                wind_speed_kmh=wind_kmh,
                wind_from_deg=wind_deg,
                home_altitude_m=home_alt,
                trained_temp_c=trained_temp_c,
                trained_humidity_pct=trained_humidity_pct,
                strategy=strategy_code,
                apply_env_derating=apply_env_derating,
                post_bike_fatigue_pct=float(preset_meta["post_bike_fatigue"]) * 100.0,
            )
            st.dataframe(pd.DataFrame(re_sens_rows), use_container_width=True, hide_index=True)
            st.caption("💡 **Tiempo en Llano (Palladino)** = `v = RE × (W/kg)` sin desnivel ni clima. **Tiempo en ESTE Circuito** incluye las subidas/bajadas reales del GPX/FIT, el viento y la altitud respecto a donde entrena.")

    # ── GRÁFICA PRINCIPAL DE PACING INTERACTIVO CON ZOOM EN VIVO + MAPA GPS ──
    render_interactive_pacing_zoom_widget(sim, sport="running", ftp_or_rcp=run_cp_w, height=535)

    with st.expander("🗺️ Mapa GPS Interactivo del Circuito Running (con Flechas de Viento)", expanded=False):
        _render_running_gps_map_fragment(sim, wind_deg, wind_kmh, member_id)

    # ── ESTRATEGIA NUTRICIONAL Y CALENDARIO EXACTO DE GELES ─────────
    gel_pills = ""
    if nut["gel_schedule"]:
        gel_pills = "".join(
            f'<span style="background:rgba(255,170,0,0.16); border:1px solid rgba(255,170,0,0.45); color:#FFD166; '
            f'padding:4px 9px; border-radius:8px; font-size:0.76rem; font-weight:800;">'
            f'⚡ Gel #{g["gel_num"]} · Km {g["km"]:.1f} ({g["time_str"]}) · {g["cho_g"]}g</span>'
            for g in nut["gel_schedule"]
        )
    else:
        gel_pills = '<span style="color:#94A3B8; font-size:0.78rem;">Carrera corta: sin gel obligatorio en ruta (1 gel opcional 15 min antes de largar).</span>'

    _render_html(
        f"""
        <div style="background:linear-gradient(145deg, rgba(12,24,26,0.94), rgba(10,15,24,0.96));
                    border:1.5px solid rgba(0,255,136,0.4); border-radius:14px; padding:13px 16px; margin:8px 0 14px 0;">
            <div style="color:#00FF88; font-weight:900; font-size:0.94rem; margin-bottom:8px;">
                🥤 Plan de Nutrición e Hidratación en Carrera a Pie ({temp_c:.0f}°C · {humidity_pct}% HR)
            </div>
            <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; margin-bottom:10px;">
                <div style="background:rgba(255,170,0,0.08); border:1px solid rgba(255,170,0,0.3); border-radius:10px; padding:8px 11px;">
                    <div style="color:#FFAA00; font-size:0.68rem; font-weight:800;">🍌 CARBOHIDRATOS</div>
                    <div style="color:#FFF; font-size:1.15rem; font-weight:900;">{nut['cho_g_per_h']} g/h <span style="font-size:0.75rem; color:#CBD5E1;">(Total {nut['cho_total_g']} g)</span></div>
                </div>
                <div style="background:rgba(0,238,255,0.08); border:1px solid rgba(0,238,255,0.3); border-radius:10px; padding:8px 11px;">
                    <div style="color:#00EEFF; font-size:0.68rem; font-weight:800;">💧 HIDRATACIÓN</div>
                    <div style="color:#FFF; font-size:1.15rem; font-weight:900;">{nut['fluid_ml_per_h']} mL/h <span style="font-size:0.75rem; color:#CBD5E1;">(Total {nut['fluid_total_l']} L)</span></div>
                </div>
                <div style="background:rgba(192,132,252,0.08); border:1px solid rgba(192,132,252,0.3); border-radius:10px; padding:8px 11px;">
                    <div style="color:#C084FC; font-size:0.68rem; font-weight:800;">🧂 SODIO / ELECTROLITOS</div>
                    <div style="color:#FFF; font-size:1.15rem; font-weight:900;">{nut['sodium_mg_per_h']} mg/h <span style="font-size:0.75rem; color:#CBD5E1;">(Total {nut['sodium_total_mg']} mg)</span></div>
                </div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px;">{gel_pills}</div>
            <div style="color:#CBD5E1; font-size:0.78rem;">💡 {html.escape(nut['gel_note'])}</div>
        </div>
        """
    )

    # ── TABLA DE SPLITS TRAMO A TRAMO ───────────────────────────────
    st.markdown(f"#### 📋 Hoja de Ruta Running — Splits cada {int(split_interval_km)} km")
    visible_splits = [
        {k: v for k, v in sp.items() if not str(k).startswith("_")}
        for sp in sim["splits"]
    ]
    df_splits = pd.DataFrame(visible_splits)
    st.dataframe(df_splits, use_container_width=True, hide_index=True)

    # ── GUARDAR ESCENARIO EN CRM Y APP DEL ATLETA ───────────────────
    st.markdown("---")
    cs1, cs2 = st.columns([2.3, 1])
    mode_tag = "Stryd" if plan_mode == "stryd" else "Pace"
    with cs1:
        sc_name = st.text_input(
            "💾 Nombre del Plan de Carrera Running (se publica también en la App del Atleta):",
            value=f"🏃 {route_data['route_name'][:26]} ({s['total_time_str']} · {s['avg_pace_str']}/km · {mode_tag})",
            key=f"run_sc_name_{member_id}_{route_sig}",
        )
    with cs2:
        st.markdown("<div style='height:27px;'></div>", unsafe_allow_html=True)
        if st.button("💾 Guardar Escenario Running", type="primary", use_container_width=True, key=f"run_save_btn_{member_id}_{route_sig}"):
            _save_running_scenario_to_db(member_id, sc_name, route_data, sim, active_ctx, source_tag="running_simulator")
            st.toast(f"Plan Running guardado para {ath['name']} (visible en CRM y App Atleta)!", icon="✅")
            st.rerun()
