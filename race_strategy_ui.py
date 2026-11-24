"""
race_strategy_ui.py — Interfaz interactiva de Estrategias de Carrera en Ciclismo
(AlphaX Race Split — estilo Best Bike Split) para el CRM de Entrenadores.
"""

import json
import math
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from database import LactateTest, Member, RaceStrategyScenario, SessionLocal, init_db
from weather_service import deg_to_compass, fetch_race_weather
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

    # Curva de potencia objetivo (W)
    fig.add_trace(
        go.Scatter(
            x=df["dist_km"],
            y=df["power_w"],
            name="Potencia Objetivo (W)",
            mode="lines",
            line=dict(color="#00EEFF", width=2.8),
            hovertemplate="Km %{x:.1f}<br>Potencia: %{y:.0f} W<extra></extra>",
        ),
        secondary_y=False,
    )

    # Velocidad estimada (escalada visualmente o trazada en hover? Mejor en un segundo subplot limpio!)
    fig.add_hline(
        y=ftp_w,
        line_dash="dot",
        line_color="#FF5577",
        annotation_text=f"FTP ({ftp_w:.0f} W)",
        annotation_position="top right",
        annotation_font=dict(color="#FF7799", size=11),
        secondary_y=False,
    )

    fig.update_layout(
        title=dict(
            text="📈 Plan de Potencia Objetivo vs Perfil Altimétrico del Circuito",
            x=0.5,
            xanchor="center",
            font=dict(color="#00EEFF", size=15),
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

    fig.add_trace(
        go.Scatter(
            x=df["dist_km"],
            y=df["speed_kmh"],
            name="Velocidad Estimada (km/h)",
            mode="lines",
            line=dict(color="#FFD700", width=2.5),
            hovertemplate="Km %{x:.1f}<br>Velocidad: %{y:.1f} km/h<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(
            x=df["dist_km"],
            y=df["headwind_kmh"],
            name="Viento (+Contra / -Favor km/h)",
            marker_color=["rgba(255, 75, 75, 0.45)" if v > 0 else "rgba(0, 255, 136, 0.45)" for v in df["headwind_kmh"]],
            hovertemplate="Km %{x:.1f}<br>Viento relativo: %{y:+.1f} km/h<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=dict(
            text="🚀 Velocidad Estimada y Viento Relativo por Kilómetro",
            x=0.5,
            xanchor="center",
            font=dict(color="#FFD700", size=14),
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


def _build_gps_map_chart(segments: list, color_mode: str = "wind", wind_from_deg: float = 0.0, wind_kmh: float = 0.0) -> go.Figure:
    """Mapa 2D del circuito GPX coloreado por viento relativo o potencia objetivo."""
    # Usar solo la primera vuelta para que el trazo sea nítido en circuitos multi-vuelta
    lap1 = [s for s in segments if s.get("lap", 1) == 1] or segments
    df = pd.DataFrame(lap1)

    if color_mode == "power":
        c_vals = df["power_w"]
        c_title = "Potencia (W)"
        c_scale = "Turbo"
        hover_txt = [f"Km {r['dist_km']:.1f}<br>Potencia: {r['power_w']:.0f} W<br>Pend: {r['grade_pct']:+.1f}%<br>Vel: {r['speed_kmh']:.1f} km/h" for _, r in df.iterrows()]
    else:
        c_vals = df["headwind_kmh"]
        c_title = "Viento (+Contra / -Favor)"
        c_scale = [[0.0, "#00FF88"], [0.5, "#38BDF8"], [1.0, "#FF4B4B"]]
        hover_txt = [f"Km {r['dist_km']:.1f}<br>Viento: {r['headwind_kmh']:+.1f} km/h<br>Potencia: {r['power_w']:.0f} W<br>Vel: {r['speed_kmh']:.1f} km/h" for _, r in df.iterrows()]

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
            ),
            text=hover_txt,
            hoverinfo="text",
            name="Circuito GPX",
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

    wind_note = f" · Viento desde {wind_from_deg:.0f}° ({wind_kmh:.0f} km/h)" if wind_kmh > 0 else " · Sin viento"
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
        height=350,
        margin=dict(l=10, r=10, t=45, b=10),
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        showlegend=False,
    )
    return fig


def _build_resistance_donut(resistance: dict) -> go.Figure:
    """Gráfica de dona del reparto de energía entre Aerodinámica, Gravedad y Rodamiento."""
    labels = ["Aerodinámica (CdA)", "Gravedad (Subidas)", "Rodamiento (Crr)"]
    values = [resistance["aero_pct"], resistance["gravity_pct"], resistance["rolling_pct"]]
    colors = ["#00EEFF", "#FF5577", "#FFD700"]

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


def render_race_strategy_page():
    """Renderiza el módulo completo de Estrategias de Carrera (AlphaX Race Split) en el CRM Admin."""
    init_db()

    _render_html(
        """
        <div style="background: linear-gradient(135deg, rgba(14, 22, 38, 0.96), rgba(8, 12, 22, 0.98));
                    border: 1.5px solid rgba(0, 238, 255, 0.35); border-radius: 16px; padding: 16px 20px;
                    margin-bottom: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.45);">
            <div style="color:#00EEFF; font-size:1.45rem; font-weight:900; letter-spacing:0.4px;">
                🏁 ALPHAX RACE SPLIT <span style="color:#FFFFFF; font-weight:400; font-size:1.05rem;">| Simulador y Estrategias de Carrera (FIT / GPX / TCX)</span>
                <span style="background:rgba(0,238,255,0.14); color:#00EEFF; font-size:0.68rem; font-weight:800; padding:2px 8px; border-radius:10px; margin-left:6px; vertical-align:middle;">v2.5</span>
            </div>
            <div style="color:#94A3B8; font-size:0.86rem; margin-top:4px;">
                Sube el circuito GPX, configura CdA, bicicleta, componentes, viento y temperatura, y modela escenarios de potencia óptimos estilo Best Bike Split.
            </div>
        </div>
        """
    )

    # ── 1. SELECCIÓN DE DEPORTISTA Y CARGA DE RUTA GPX ──────────────
    with SessionLocal() as session:
        members = session.query(Member).filter(Member.active == True).order_by(Member.name).all()
        if not members:
            members = session.query(Member).order_by(Member.name).all()
        member_map = {f"{m.name} ({m.group or 'AlphaX'})": m.id for m in members}

    if not member_map:
        st.warning("Aún no hay deportistas registrados en el CRM.")
        return

    col_top1, col_top2 = st.columns([1.15, 1.85])
    with col_top1:
        sel_member_label = st.selectbox(
            "🏃‍♂️ Seleccionar Deportista:",
            list(member_map.keys()),
            key="rs_athlete_select",
        )
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

    with col_top2:
        route_source = st.radio(
            "🗺️ Origen del Circuito de Ciclismo:",
            ["📂 Subir archivo FIT / GPX / TCX", "🌴 Demo: Ironman 70.3 Cartagena (90 km)", "⛰️ Demo: Circuito Rionegro–Las Palmas (45 km)"],
            horizontal=True,
            key="rs_route_source",
        )

        route_file_bytes = None
        route_file_label = ""
        uploaded_custom_file = False
        c_gpx1, c_gpx2 = st.columns([2.2, 1])
        with c_gpx1:
            if route_source.startswith("📂"):
                up_gpx = st.file_uploader(
                    "Sube la ruta o actividad (.fit, .gpx, .tcx o .zip de Garmin/Strava/Wahoo/TrainingPeaks):",
                    type=["fit", "gpx", "tcx", "zip", "gz"],
                    key="rs_gpx_uploader",
                )
                if up_gpx is not None:
                    route_file_bytes = up_gpx.getvalue()
                    route_file_label = up_gpx.name
                    uploaded_custom_file = True
            elif "Cartagena" in route_source:
                route_file_bytes, route_file_label = generate_demo_gpx("cartagena_703")
                route_file_label = "demo_cartagena.gpx"
            else:
                route_file_bytes, route_file_label = generate_demo_gpx("rionegro_climb")
                route_file_label = "demo_rionegro.gpx"
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

    if not route_file_bytes:
        st.info("📂 Sube un archivo `.fit`, `.gpx`, `.tcx` o `.zip` arriba, o selecciona una de las rutas de demostración para empezar.")
        return

    try:
        route_data = parse_route_from_any_file(route_file_bytes, route_file_label, laps=int(laps))
    except Exception as gpx_err:
        st.error(f"⚠️ No se pudo leer el archivo `{route_file_label}`: {gpx_err}")
        return

    # Banner compacto del circuito cargado
    _render_html(
        f"""
        <div style="display:grid; grid-template-columns: repeat(5, 1fr); gap:8px; margin:6px 0 14px 0;">
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Circuito</div>
                <div style="color:#FFFFFF; font-size:0.86rem; font-weight:800; margin-top:2px;">{route_data['route_name'][:32]}</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Distancia Total</div>
                <div style="color:#00EEFF; font-size:1.1rem; font-weight:900;">{route_data['total_distance_km']:.1f} km</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Desnivel Positivo</div>
                <div style="color:#FF6B6B; font-size:1.1rem; font-weight:900;">+{route_data['elevation_gain_m']:.0f} m</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Altitud (Mín–Máx)</div>
                <div style="color:#F8FAFC; font-size:1.0rem; font-weight:800;">{route_data['min_ele_m']:.0f}–{route_data['max_ele_m']:.0f} m</div>
            </div>
            <div style="background:rgba(16,22,34,0.92); border:1px solid rgba(0,238,255,0.25); border-radius:10px; padding:8px 10px; text-align:center;">
                <div style="color:#8E9BAE; font-size:0.68rem; font-weight:700; text-transform:uppercase;">Pendiente Máx</div>
                <div style="color:#FFD700; font-size:1.1rem; font-weight:900;">{route_data['max_grade_pct']:.1f}%</div>
            </div>
        </div>
        """
    )

    tab_sim, tab_cda, tab_comp, tab_saved = st.tabs([
        "⚙️ Simulador y Plan de Carrera",
        "📐 Calibrar CdA Real (desde Carrera)",
        "⚔️ Comparador 'Qué Pasa Si...'",
        "🗂️ Escenarios Guardados",
    ])

    # ═════════════════════════════════════════════════════════════════
    #  TAB 1: SIMULADOR COMPLETO ESTILO BEST BIKE SPLIT
    # ═════════════════════════════════════════════════════════════════
    with tab_sim:
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

        # Ejecutar simulación principal (con clima horario dinámico si está activo)
        sim = simulate_race_strategy(
            route_data=route_data,
            rider_weight_kg=rider_weight,
            ftp_w=ftp_w,
            target_if=target_if,
            equipment=equipment,
            temp_c=float(temp_c),
            rel_humidity_pct=float(humidity_pct),
            wind_speed_kmh=float(wind_kmh),
            wind_from_deg=float(wind_deg),
            strategy=strategy_code,
            max_climb_pct_ftp=max_climb_pct,
            max_descent_kmh=float(max_desc_kmh),
            split_interval_km=float(split_step_km),
            hourly_weather=active_hourly_weather,
        )
        s = sim["summary"]
        nut = sim["nutrition"]

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

        # Gráfica principal: Potencia + Altimetría
        st.plotly_chart(_build_elevation_power_chart(sim["segments"], ftp_w), use_container_width=True)
        st.plotly_chart(_build_speed_wind_chart(sim["segments"]), use_container_width=True)

        # Fila de Mapa GPS + Desglose de Resistencias y Nutrición
        col_vis1, col_vis2 = st.columns([1.35, 1])
        with col_vis1:
            map_color = st.radio(
                "Colorear mapa del circuito por:",
                ["🌬️ Viento relativo", "⚡ Potencia objetivo (W)"],
                horizontal=True,
                key="rs_map_color",
            )
            st.plotly_chart(
                _build_gps_map_chart(
                    sim["segments"],
                    color_mode="power" if "Potencia" in map_color else "wind",
                    wind_from_deg=wind_deg,
                    wind_kmh=wind_kmh,
                ),
                use_container_width=True,
            )

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
                        "resistance": sim["resistance"],
                        "nutrition": nut,
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
    #  TAB 2: CALIBRAR CdA REAL DESDE ARCHIVO O RESUMEN DE COMPETENCIA
    # ═════════════════════════════════════════════════════════════════
    with tab_cda:
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
                    activity = parse_activity_file(act_bytes, act_name)
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
                        marker_color="#00EEFF",
                        text=[f"{v:.3f}" for v in df_ch["CdA Est. (m²)"]],
                        textposition="outside",
                        name="CdA por tramo",
                    )
                )
                fig_cda.add_hline(
                    y=cal_res["cda"],
                    line_dash="dash",
                    line_color="#00FF88",
                    annotation_text=f"CdA Global: {cal_res['cda']:.3f} m²",
                    annotation_position="top left",
                    annotation_font=dict(color="#00FF88"),
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
                    # Incrementar versión de widgets de CdA para que Tab 1 cargue el nuevo valor desde DB sin mutar widget keys
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
    #  TAB 3: COMPARADOR INTERACTIVO "QUÉ PASA SI..." (ESTILO BEST BIKE SPLIT)
    # ═════════════════════════════════════════════════════════════════
    with tab_comp:
        st.subheader("🎚️ Simulador Interactivo 'Qué Pasa Si...' (Estilo Best Bike Split)")
        st.caption(
            f"Desliza cualquier variable para ver al instante su impacto real en tiempo, velocidad y TSS para **{ath_defaults['name']}** "
            f"en `{route_data['route_name']}` (Base: **{s['total_time_str']}** · CdA `{s['cda']:.3f}` · FTP `{ftp_w:.0f} W` · Peso `{s['total_mass_kg']:.1f} kg` · IF `{target_if:.2f}`)."
        )

        wi_ver_key = f"rs_wi_ver_{member_id}"
        wi_ver = st.session_state.get(wi_ver_key, 0)

        # Panel de controles deslizantes dinámicos (6 palancas simultáneas)
        with st.container(border=True):
            c_wi_hdr1, c_wi_hdr2 = st.columns([3.2, 1])
            with c_wi_hdr1:
                st.markdown("##### 🎛️ Ajusta una o varias palancas a la vez")
            with c_wi_hdr2:
                if st.button("🔄 Restablecer a 0", use_container_width=True, key=f"wi_reset_{member_id}_{wi_ver}"):
                    st.session_state[wi_ver_key] = wi_ver + 1
                    st.rerun()

            w_c1, w_c2, w_c3 = st.columns(3)
            with w_c1:
                d_cda = st.slider(
                    "🌬️ Cambio en CdA (m²):",
                    min_value=-0.080,
                    max_value=0.080,
                    value=0.000,
                    step=0.005,
                    format="%+.3f",
                    help="Negativo = más aerodinámico (ej. -0.020 m² por mejor postura, casco o ruedas).",
                    key=f"wi_cda_{member_id}_v{wi_ver}",
                )
                d_crr = st.slider(
                    "🛞 Cambio en Rodamiento Crr:",
                    min_value=-0.0016,
                    max_value=0.0020,
                    value=0.0000,
                    step=0.0002,
                    format="%+.4f",
                    help="Negativo = llantas más rápidas o asfalto más liso.",
                    key=f"wi_crr_{member_id}_v{wi_ver}",
                )
            with w_c2:
                d_watts = st.slider(
                    "🔥 Cambio en Potencia / FTP (W):",
                    min_value=-40,
                    max_value=60,
                    value=0,
                    step=5,
                    format="%+d W",
                    help="Simula subir o bajar tu FTP manteniendo el mismo IF (o súbelo junto con IF).",
                    key=f"wi_w_{member_id}_v{wi_ver}",
                )
                d_if = st.slider(
                    "🎯 Cambio en Intensidad (IF):",
                    min_value=-0.10,
                    max_value=0.10,
                    value=0.00,
                    step=0.01,
                    format="%+.2f",
                    help="Simula competir más conservador (-IF para correr mejor) o más agresivo (+IF).",
                    key=f"wi_if_{member_id}_v{wi_ver}",
                )
            with w_c3:
                d_weight = st.slider(
                    "⚖️ Cambio en Peso Total (kg):",
                    min_value=-10.0,
                    max_value=10.0,
                    value=0.0,
                    step=0.5,
                    format="%+.1f kg",
                    help="Negativo = bajar peso corporal o aligerar la bicicleta.",
                    key=f"wi_kg_{member_id}_v{wi_ver}",
                )
                d_wind = st.slider(
                    "💨 Cambio en Velocidad del Viento (km/h):",
                    min_value=-15,
                    max_value=25,
                    value=0,
                    step=1,
                    format="%+d km/h",
                    help="Prueba qué pasa si el viento aumenta o disminuye el día de carrera.",
                    key=f"wi_wind_{member_id}_v{wi_ver}",
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
            return simulate_race_strategy(
                route_data=route_data,
                rider_weight_kg=wi_rw,
                ftp_w=wi_ftp,
                target_if=wi_if,
                equipment=wi_eq,
                temp_c=float(temp_c),
                rel_humidity_pct=float(humidity_pct),
                wind_speed_kmh=wi_wind,
                wind_from_deg=float(wind_deg),
                strategy=strategy_code,
                max_climb_pct_ftp=max_climb_pct,
                max_descent_kmh=float(max_desc_kmh),
                split_interval_km=float(split_step_km),
                hourly_weather=wi_hourly,
            )

        base_t = s["total_time_s"]
        any_changed = any([
            abs(d_cda) > 1e-6,
            d_watts != 0,
            abs(d_weight) > 1e-6,
            abs(d_if) > 1e-6,
            abs(d_crr) > 1e-6,
            d_wind != 0,
        ])

        wi_sim = _run_whatif(d_cda, d_watts, d_weight, d_if, d_crr, d_wind) if any_changed else sim
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
                <div style="background:#111827; border:1px solid rgba(0,255,136,0.3); border-radius:12px; padding:12px 10px; text-align:center;">
                    <div style="color:#94A3B8; font-size:0.68rem; font-weight:800; text-transform:uppercase;">Potencia NP</div>
                    <div style="color:#00FF88; font-size:1.3rem; font-weight:900; margin-top:3px;">{ws_sum['norm_power_w']:.0f} W</div>
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
                contrib_items.append((f"🌬️ CdA ({d_cda:+.3f} m²)", dt_c))
            if d_watts != 0:
                dt_w = _run_whatif(delta_w=d_watts)["summary"]["total_time_s"] - base_t
                contrib_items.append((f"🔥 Potencia ({d_watts:+d} W)", dt_w))
            if abs(d_weight) > 1e-6:
                dt_kg = _run_whatif(delta_kg=d_weight)["summary"]["total_time_s"] - base_t
                contrib_items.append((f"⚖️ Peso ({d_weight:+.1f} kg)", dt_kg))
            if abs(d_if) > 1e-6:
                dt_if = _run_whatif(delta_if=d_if)["summary"]["total_time_s"] - base_t
                contrib_items.append((f"🎯 Intensidad ({d_if:+.2f} IF)", dt_if))
            if abs(d_crr) > 1e-6:
                dt_cr = _run_whatif(delta_crr=d_crr)["summary"]["total_time_s"] - base_t
                contrib_items.append((f"🛞 Rodamiento ({d_crr:+.4f})", dt_cr))
            if d_wind != 0:
                dt_wd = _run_whatif(delta_wind=d_wind)["summary"]["total_time_s"] - base_t
                contrib_items.append((f"💨 Viento ({d_wind:+d} km/h)", dt_wd))

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
                                    "d_cda": d_cda, "d_watts": d_watts, "d_weight": d_weight,
                                    "d_if": d_if, "d_crr": d_crr, "d_wind": d_wind,
                                    "nutrition": wi_sim["nutrition"],
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

        # ── CURVAS DE SENSIBILIDAD ESTILO BEST BIKE SPLIT (CdA, POTENCIA, PESO) ──
        with st.expander("📈 Ver Curvas de Ganancia de Tiempo en esta Ruta (CdA vs Vatios vs Peso)", expanded=True):
            c_sw1, c_sw2, c_sw3 = st.columns(3)

            with c_sw1:
                cda_steps = [-0.04, -0.02, 0.0, 0.02, 0.04]
                cda_mins = [(_run_whatif(delta_cda=x)["summary"]["total_time_s"] - base_t) / 60.0 for x in cda_steps]
                fig_sw1 = go.Figure()
                fig_sw1.add_trace(go.Scatter(x=[equipment["cda"] + x for x in cda_steps], y=cda_mins, mode="lines+markers", line=dict(color="#00EEFF", width=3), marker=dict(size=8), name="Δ Tiempo"))
                if abs(d_cda) > 1e-6:
                    cur_y = (_run_whatif(delta_cda=d_cda)["summary"]["total_time_s"] - base_t) / 60.0
                    fig_sw1.add_trace(go.Scatter(x=[equipment["cda"] + d_cda], y=[cur_y], mode="markers", marker=dict(size=13, color="#00FF88", symbol="star"), name="Tu ajuste"))
                fig_sw1.add_hline(y=0, line_dash="dot", line_color="#64748B")
                fig_sw1.update_layout(title="🌬️ Sensibilidad a CdA (m²)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="CdA (m²)", yaxis_title="Δ Minutos", showlegend=False)
                st.plotly_chart(fig_sw1, use_container_width=True)

            with c_sw2:
                w_steps = [-20, -10, 0, 10, 20, 30]
                w_mins = [(_run_whatif(delta_w=x)["summary"]["total_time_s"] - base_t) / 60.0 for x in w_steps]
                fig_sw2 = go.Figure()
                fig_sw2.add_trace(go.Scatter(x=[ftp_w + x for x in w_steps], y=w_mins, mode="lines+markers", line=dict(color="#00FF88", width=3), marker=dict(size=8), name="Δ Tiempo"))
                if d_watts != 0:
                    cur_y = (_run_whatif(delta_w=d_watts)["summary"]["total_time_s"] - base_t) / 60.0
                    fig_sw2.add_trace(go.Scatter(x=[ftp_w + d_watts], y=[cur_y], mode="markers", marker=dict(size=13, color="#FFD700", symbol="star"), name="Tu ajuste"))
                fig_sw2.add_hline(y=0, line_dash="dot", line_color="#64748B")
                fig_sw2.update_layout(title="🔥 Sensibilidad a FTP (W)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="FTP (W)", yaxis_title="Δ Minutos", showlegend=False)
                st.plotly_chart(fig_sw2, use_container_width=True)

            with c_sw3:
                kg_steps = [-6.0, -3.0, 0.0, 3.0, 6.0]
                kg_mins = [(_run_whatif(delta_kg=x)["summary"]["total_time_s"] - base_t) / 60.0 for x in kg_steps]
                fig_sw3 = go.Figure()
                fig_sw3.add_trace(go.Scatter(x=[rider_weight + x for x in kg_steps], y=kg_mins, mode="lines+markers", line=dict(color="#FFD700", width=3), marker=dict(size=8), name="Δ Tiempo"))
                if abs(d_weight) > 1e-6:
                    cur_y = (_run_whatif(delta_kg=d_weight)["summary"]["total_time_s"] - base_t) / 60.0
                    fig_sw3.add_trace(go.Scatter(x=[rider_weight + d_weight], y=[cur_y], mode="markers", marker=dict(size=13, color="#00EEFF", symbol="star"), name="Tu ajuste"))
                fig_sw3.add_hline(y=0, line_dash="dot", line_color="#64748B")
                fig_sw3.update_layout(title="⚖️ Sensibilidad a Peso (kg)", paper_bgcolor="#0E121C", plot_bgcolor="#0E121C", font=dict(color="#E2E8F0", size=11), height=235, margin=dict(l=10, r=10, t=38, b=10), xaxis_title="Peso Atleta (kg)", yaxis_title="Δ Minutos", showlegend=False)
                st.plotly_chart(fig_sw3, use_container_width=True)

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
                p_sim = simulate_race_strategy(
                    route_data=route_data,
                    rider_weight_kg=rider_weight,
                    ftp_w=ftp_w,
                    target_if=target_if,
                    equipment=equipment,
                    temp_c=float(temp_c),
                    rel_humidity_pct=float(humidity_pct),
                    wind_speed_kmh=float(wind_kmh),
                    wind_from_deg=float(wind_deg),
                    strategy=p_code,
                    max_climb_pct_ftp=max_climb_pct,
                    max_descent_kmh=float(max_desc_kmh),
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

        # ── C. BENCHMARK DE TIPOS DE BICICLETA SOBRE THIS GPX ───────
        st.markdown("##### 🚲 3. Comparación Directa de Bicicletas en este Circuito")
        bike_bench = []
        for b_name, w_name, h_name in [
            ("Contrarreloj / Triatlón (TT / Cabra)", "Lenticular trasera + Perfil Alto 80 mm", "Casco Aero TT con visor"),
            ("Ruta Aero con acoples", "Perfil Alto Carbono (60–80 mm)", "Casco Aero Ruta (semi-cerrado)"),
            ("Ruta Aero (manubrio normal / drops)", "Perfil Medio Carbono (45–50 mm)", "Casco Aero Ruta (semi-cerrado)"),
            ("Ruta Escaladora / Ligera", "Ultraligeras Escaladoras (28–35 mm)", "Casco Ruta Ventilado estándar"),
        ]:
            b_eq = estimate_equipment_specs(
                bike_type=b_name, wheels=w_name, helmet=h_name, tires=tires,
                drivetrain=drivetrain, apparel=apparel, hydration=hydration,
                surface=surface, rider_height_cm=rider_height,
            )
            b_sim = simulate_race_strategy(
                route_data, rider_weight, ftp_w, target_if, b_eq,
                float(temp_c), float(humidity_pct), float(wind_kmh), float(wind_deg),
                strategy_code, max_climb_pct, float(max_desc_kmh), hourly_weather=active_hourly_weather,
            )["summary"]
            bike_bench.append((b_name, b_sim))

        fastest_b = min(x[1]["total_time_s"] for x in bike_bench)
        bike_df = pd.DataFrame([
            {
                "Configuración": bn,
                "CdA (m²)": bs["cda"],
                "Masa Total (kg)": bs["total_mass_kg"],
                "Tiempo Estimado": bs["total_time_str"],
                "Diferencia vs Más Rápida": "🥇 Más rápida" if abs(bs["total_time_s"] - fastest_b) < 0.5 else format_time_delta(bs["total_time_s"] - fastest_b),
                "Vel. Media (km/h)": bs["avg_speed_kmh"],
            }
            for bn, bs in bike_bench
        ])
        st.dataframe(bike_df, use_container_width=True, hide_index=True)

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
            st.info("Guarda al menos 2 escenarios desde la pestaña **⚙️ Simulador y Plan de Carrera** para compararlos aquí lado a lado.")

    # ═════════════════════════════════════════════════════════════════
    #  TAB 3: HISTORIAL Y GESTIÓN DE ESCENARIOS GUARDADOS
    # ═════════════════════════════════════════════════════════════════
    with tab_saved:
        st.subheader(f"🗂️ Escenarios Guardados — {ath_defaults['name']}")
        with SessionLocal() as s_db:
            saved_recs = (
                s_db.query(RaceStrategyScenario)
                .filter(RaceStrategyScenario.member_id == member_id)
                .order_by(RaceStrategyScenario.created_at.desc(), RaceStrategyScenario.id.desc())
                .all()
            )
        if not saved_recs:
            st.info(f"{ath_defaults['name']} aún no tiene escenarios de carrera guardados.")
        else:
            summary_rows = []
            for r in saved_recs:
                summary_rows.append({
                    "ID": r.id,
                    "Fecha": r.created_at.strftime("%d/%m/%Y") if r.created_at else "—",
                    "Escenario": r.scenario_name,
                    "Ruta": r.route_name,
                    "Dist. (km)": r.distance_km,
                    "Desnivel (m)": r.elevation_gain_m,
                    "Tiempo": format_hms(r.estimated_time_sec),
                    "Vel. (km/h)": r.avg_speed_kmh,
                    "NP (W)": r.norm_power_w,
                    "IF": r.intensity_factor,
                    "TSS": r.tss,
                    "CdA": r.cda,
                })
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            sc_pick_map = {f"{r.scenario_name} ({format_hms(r.estimated_time_sec)} · ID:{r.id})": r for r in saved_recs}
            sel_sc_lbl = st.selectbox("Inspeccionar o eliminar un escenario:", list(sc_pick_map.keys()), key=f"rs_saved_pick_{member_id}")
            sel_sc = sc_pick_map[sel_sc_lbl]

            c_det1, c_det2 = st.columns([3, 1])
            with c_det1:
                try:
                    splits_saved = json.loads(sel_sc.splits_json or "[]")
                    if splits_saved:
                        with st.expander("📋 Ver Plan de Splits guardado", expanded=False):
                            df_sp = pd.DataFrame(splits_saved)
                            st.dataframe(df_sp, use_container_width=True, hide_index=True)
                            st.download_button(
                                "⬇️ Descargar Splits CSV",
                                data=df_sp.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"escenario_{sel_sc.id}.csv",
                                mime="text/csv",
                                key=f"rs_dl_saved_{sel_sc.id}",
                            )
                except Exception:
                    pass
            with c_det2:
                if st.button("🗑️ Eliminar Escenario", type="secondary", use_container_width=True, key=f"rs_del_sc_{sel_sc.id}"):
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
