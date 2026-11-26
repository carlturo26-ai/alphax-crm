"""
fit_export_ui.py — Panel reutilizable de exportación Garmin .FIT (Course + Workout)
para AlphaX Race Planner (Ciclismo y Running).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from fit_race_exporter import build_fit_export_bundle


def _render_html(html_str: str) -> None:
    if hasattr(st, "html"):
        st.html(html_str)
    else:
        st.markdown(html_str, unsafe_allow_html=True)


def render_garmin_fit_export_panel(
    plan_name: str,
    sim_segments: List[Dict],
    sport: str = "cycling",
    default_target_metric: str = "power",
    ref_threshold_w: float = 250.0,
    nutrition: Optional[Dict] = None,
    default_split_km: float = 5.0,
    key_prefix: str = "fit_exp",
    start_dt: Optional[datetime] = None,
    compact: bool = False,
) -> None:
    """
    Renderiza el Centro de Exportación Garmin .FIT al final de un plan de carrera:
      - Archivo 1: `.FIT` Course (Trayecto GPS + Virtual Partner + Alertas de segmento y nutrición)
      - Archivo 2: `.FIT` Workout (Plan estructurado por distancia con alertas de rango W / ritmo)
    """
    if not sim_segments or len(sim_segments) < 2:
        st.info("Carga una ruta GPS válida para habilitar la exportación al reloj (.FIT).")
        return

    sport_title = "Ciclismo (Garmin Edge / Fenix / Forerunner)" if sport == "cycling" else "Running (Garmin Forerunner / Fenix / Epix)"
    accent = "#FFD700" if sport == "cycling" else "#00FF88"

    if not compact:
        _render_html(
            f"""
            <div style="background:linear-gradient(135deg, rgba(14,22,38,0.96), rgba(10,16,28,0.98));
                        border:1.5px solid rgba(0,238,255,0.38); border-radius:14px; padding:14px 16px;
                        margin:14px 0 12px 0; box-shadow:0 6px 20px rgba(0,0,0,0.35);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                    <div style="color:#00EEFF; font-size:1.03rem; font-weight:900; letter-spacing:0.3px;">
                        ⌚ EXPORTAR AL RELOJ / CICLOCOMPUTADOR (GARMIN .FIT — ESTILO BEST BIKE SPLIT)
                    </div>
                    <span style="background:rgba(0,238,255,0.14); color:{accent}; border:1px solid rgba(0,238,255,0.35);
                                 border-radius:999px; padding:2px 10px; font-size:0.74rem; font-weight:800;">
                        {sport_title}
                    </span>
                </div>
                <div style="color:#CBD5E1; font-size:0.82rem; margin-top:6px; line-height:1.45;">
                    Descarga ambos archivos <b>.FIT</b> nativos: el <b>Trayecto (Course)</b> con mapa GPS, <b>Virtual Partner</b>
                    (adelantado/atrasado al plan) y banners por segmento, más el <b>Workout por Distancia</b> con alertas activas
                    de pitido/vibración si el deportista se sale del rango objetivo.
                </div>
            </div>
            """
        )

    # Controles de personalización de segmentos y tolerancia
    if sport == "running":
        c1, c2, c3, c4 = st.columns([1.45, 1.25, 1.15, 1.05])
    else:
        c1, c2, c3 = st.columns([1.65, 1.2, 1.15])
        c4 = None

    with c1:
        seg_choice = st.selectbox(
            "🧠 Segmentación para el reloj:",
            [
                "🧠 Inteligente por Terreno (Subidas / Llanos / Bajadas)",
                f"📏 Splits Fijos (cada {default_split_km:g} km)",
            ],
            key=f"{key_prefix}_seg_mode",
        )
        seg_mode = "terrain" if seg_choice.startswith("🧠") else "splits"

    if sport == "running":
        with c2:
            metric_opts = ["⏱️ Ritmo (min/km)", "⚡ Potencia (Stryd / Garmin W)"]
            default_idx = 1 if default_target_metric == "power" else 0
            metric_choice = st.selectbox(
                "🎯 Objetivo de alertas (Workout):",
                metric_opts,
                index=default_idx,
                key=f"{key_prefix}_run_metric",
            )
            target_metric = "power" if "Potencia" in metric_choice else "pace"
        tol_col = c3
        nut_col = c4
    else:
        target_metric = "power"
        tol_col = c2
        nut_col = c3

    with tol_col:
        if target_metric == "power":
            tol_pct = float(
                st.slider(
                    "🎚️ Margen alerta (±% W):",
                    min_value=3,
                    max_value=10,
                    value=5 if sport == "cycling" else 3,
                    step=1,
                    key=f"{key_prefix}_tol_w",
                )
            )
            pace_tol_s = 5.0
        else:
            pace_tol_s = float(
                st.slider(
                    "🎚️ Margen ritmo (±s/km):",
                    min_value=3,
                    max_value=12,
                    value=5,
                    step=1,
                    key=f"{key_prefix}_tol_pace",
                )
            )
            tol_pct = 5.0

    with nut_col:
        st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
        include_nut = st.checkbox(
            "🥤 Alertas nutrición/geles",
            value=True,
            key=f"{key_prefix}_inc_nut",
            help="Inserta Course Points GPS de nutrición/geles en el archivo Trayecto (.FIT Course).",
        )

    try:
        bundle = build_fit_export_bundle(
            plan_name=plan_name,
            sim_segments=sim_segments,
            sport=sport,
            target_metric=target_metric,
            ref_threshold_w=float(ref_threshold_w or 250.0),
            nutrition=nutrition,
            segmentation_mode=seg_mode,
            fixed_split_km=float(default_split_km or 5.0),
            max_steps=45,
            tolerance_pct=tol_pct,
            pace_tol_sec=pace_tol_s,
            include_nutrition_alerts=include_nut,
            start_dt=start_dt,
        )
    except Exception as exc:
        st.error(f"No se pudieron generar los archivos .FIT: {exc}")
        return

    # Botones de descarga duales (.FIT Course + .FIT Workout)
    b1, b2 = st.columns(2)
    with b1:
        st.download_button(
            f"🗺️ Descargar Trayecto + Virtual Partner (.FIT · {bundle['course_size_kb']} KB)",
            data=bundle["course_fit_bytes"],
            file_name=bundle["course_filename"],
            mime="application/octet-stream",
            type="primary",
            use_container_width=True,
            key=f"{key_prefix}_dl_course_fit",
        )
        st.caption(
            f"Incluye ruta GPS + Virtual Partner + **{bundle['num_steps']} alertas de segmento**"
            + (" + avisos de nutrición/geles." if include_nut else ".")
        )
    with b2:
        metric_lbl = "Potencia (W)" if target_metric == "power" else "Ritmo (/km)"
        st.download_button(
            f"⚡ Descargar Workout por Distancia (.FIT · {bundle['num_steps']} pasos)",
            data=bundle["workout_fit_bytes"],
            file_name=bundle["workout_filename"],
            mime="application/octet-stream",
            type="primary",
            use_container_width=True,
            key=f"{key_prefix}_dl_workout_fit",
        )
        st.caption(
            f"Plan estructurado por distancia (km) con alertas activas de **{metric_lbl}** (límite Garmin ≤ 50 pasos)."
        )

    # Previsualización de los segmentos y alertas exactas del reloj
    with st.expander(
        f"👁️ Ver los {bundle['num_steps']} segmentos y alertas exactas que recibirá el reloj",
        expanded=False,
    ):
        st.dataframe(pd.DataFrame(bundle["preview_rows"]), use_container_width=True, hide_index=True)

    # Guía rápida de instalación en el dispositivo
    with st.expander("📖 Cómo cargar los archivos .FIT en Garmin (Edge / Forerunner / Fenix)", expanded=False):
        st.markdown(
            """
            **1. Archivo Trayecto + Virtual Partner (`AlphaX_Trayecto_...fit`):**
            - **Sin cable (Garmin Connect Web):** Entra a *Entrenamiento y planificación → Trayectos → Importar*, sube el `.fit` y dale **Enviar al dispositivo**.
            - **Por USB (100% preserva todos los Course Points y Virtual Partner):** Conecta el reloj o Garmin Edge al PC/Mac y copia el archivo en la carpeta **`GARMIN/NewFiles`**. Al desconectar aparece en *Navegación → Trayectos*.

            **2. Archivo Workout por Distancia (`AlphaX_Workout_...fit`):**
            - Conecta el Garmin por USB y copia el archivo en **`GARMIN/NewFiles`**. Al desconectar aparece en *Entrenamiento → Sesiones de entrenamiento*.
            - En carrera, el reloj/Edge cambia automáticamente de paso al completar cada distancia GPS y **pita/vibra si te sales del rango objetivo**.

            **🏆 Truco Pro (Estilo Best Bike Split completo):**
            En Garmin Edge, Forerunner y Fenix puedes iniciar **primero el Workout** y luego en *Navegación → Trayectos* activar **el Trayecto AlphaX** dentro de la misma actividad: así llevas **simultáneamente** mapa + *Virtual Partner* + banners GPS + alertas activas de rango de potencia/ritmo.
            """
        )
