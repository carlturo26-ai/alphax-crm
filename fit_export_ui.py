"""
fit_export_ui.py — Panel reutilizable de exportación Garmin `.FIT` Course
(Trayecto GPS + Virtual Partner + Alertas de intensidad por segmento y nutrición)
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
    Renderiza el Centro de Exportación Garmin `.FIT` Course al final de un plan de carrera:
      - Trazado GPS completo + Virtual Partner (adelantado/atrasado respecto al plan físico AlphaX)
      - Course Points anclados a coordenadas GPS exactas del recorrido (sin desfase aunque cambien la salida)
    """
    if not sim_segments or len(sim_segments) < 2:
        st.info("Carga una ruta GPS válida para habilitar la exportación al reloj (.FIT Course).")
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
                        ⌚ EXPORTAR TRAYECTO AL RELOJ (GARMIN .FIT COURSE · GPS + VIRTUAL PARTNER)
                    </div>
                    <span style="background:rgba(0,238,255,0.14); color:{accent}; border:1px solid rgba(0,238,255,0.35);
                                 border-radius:999px; padding:2px 10px; font-size:0.74rem; font-weight:800;">
                        {sport_title}
                    </span>
                </div>
                <div style="color:#CBD5E1; font-size:0.82rem; margin-top:6px; line-height:1.45;">
                    Descarga el archivo <b>.FIT Course</b> con mapa GPS, <b>Virtual Partner</b> (muestra si vas adelantado o atrasado al plan)
                    y <b>alertas emergentes ancladas a la ubicación GPS exacta</b> de cada segmento del recorrido — aunque la organización
                    mueva la salida o T1, el reloj siempre te avisa en el lugar real del circuito.
                </div>
            </div>
            """
        )

    # Controles de personalización de alertas GPS
    if sport == "running":
        c1, c2, c3, c4 = st.columns([1.45, 1.25, 1.15, 1.05])
    else:
        c1, c2, c3 = st.columns([1.65, 1.2, 1.15])
        c4 = None

    with c1:
        seg_choice = st.selectbox(
            "🧠 Alertas de segmento en el mapa:",
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
                "🎯 Dato en los banners GPS:",
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
                    "🎚️ Rango de potencia (±% W):",
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
                    "🎚️ Margen de ritmo (±s/km):",
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
            help="Inserta Course Points GPS de nutrición/geles en el punto exacto del recorrido.",
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
            max_steps=55,
            tolerance_pct=tol_pct,
            pace_tol_sec=pace_tol_s,
            include_nutrition_alerts=include_nut,
            start_dt=start_dt,
        )
    except Exception as exc:
        st.error(f"No se pudo generar el archivo .FIT Course: {exc}")
        return

    # Botón principal de descarga (.FIT Course)
    nut_txt = f" + **{bundle['num_nutrition_points']} alertas de nutrición/geles**" if (include_nut and bundle["num_nutrition_points"] > 0) else ""
    st.download_button(
        f"🗺️ Descargar Trayecto + Virtual Partner (.FIT Course · {bundle['course_size_kb']} KB)",
        data=bundle["course_fit_bytes"],
        file_name=bundle["course_filename"],
        mime="application/octet-stream",
        type="primary",
        use_container_width=True,
        key=f"{key_prefix}_dl_course_fit",
    )
    st.caption(
        f"Incluye ruta GPS suavizada + Virtual Partner + **{bundle['num_steps']} alertas GPS de intensidad por segmento**{nut_txt}."
    )

    # Previsualización exacta de todas las alertas GPS (Up Ahead / Course Points)
    with st.expander(
        f"👁️ Ver las {bundle['num_total_alerts']} alertas GPS exactas que recibirá el reloj en el circuito",
        expanded=False,
    ):
        st.dataframe(pd.DataFrame(bundle["preview_rows"]), use_container_width=True, hide_index=True)

    # Guía rápida de instalación en el dispositivo
    with st.expander("📖 Cómo cargar el .FIT Course en Garmin (Edge / Forerunner / Fenix) y consejo de salida", expanded=False):
        st.markdown(
            """
            **Opción A — Por USB (100% conserva todos los Course Points y Virtual Partner):**
            - Conecta tu Garmin al PC/Mac y copia el archivo `.fit` dentro de la carpeta **`GARMIN/NewFiles`**.
            - Al desconectar aparecerá listo en **Navegación → Trayectos** (con la pantalla *Más adelante / Up Ahead* y el *Virtual Partner* activos).

            **Opción B — Desde Garmin Connect Web (sin cable):**
            - Entra a **Entrenamiento y planificación → Trayectos → Importar**, sube el `.fit` y pulsa **Enviar al dispositivo**.

            **📍 Si el día de carrera cambian el punto de salida o T1 (ej. arrancas en el km 1–2):**
            - Cuando el reloj te pregunte *«¿Navegar al inicio?»*, elige **NO**.
            - En cuanto entres al circuito real, el GPS detecta tu ubicación (*«Trayecto encontrado»*) y **todas las alertas de intensidad y nutrición saltan en el punto exacto del recorrido**, sin desfases.
            """
        )
