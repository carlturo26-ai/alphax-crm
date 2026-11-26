"""
interactive_course_charts.py — Gráficas interactivas sincronizadas en tiempo real (Plotly.js 60 FPS)
para AlphaX Race Split (Ciclismo y Running):

  1. render_synced_elevation_planimetry_widget(route_data):
     - Perfil altimétrico con Mapa de Calor de desniveles + Planimetría GPS 2D sincronizados.
     - Cursor dinámico en tiempo real: al mover el mouse sobre el perfil altimétrico, un marcador
       se desplaza sobre la planimetría GPS (y viceversa).
     - Al hacer zoom o seleccionar cualquier tramo, calcula al instante (0 ms):
       Distancia del zoom, Desnivel Positivo (+D), Desnivel Negativo (-D), Pendiente Media/Máx y Altitud,
       resaltando ese tramo exacto sobre el mapa GPS.

  2. render_interactive_pacing_zoom_widget(sim, sport, ftp_or_rcp):
     - Plan de Pacing interactivo (Ciclismo: Amarillo=Potencia W, Verde=Velocidad km/h;
       Running: Verde=Ritmo min/km, Cyan=GAP, Amarillo=Potencia Stryd W + Geles).
     - Al hacer zoom en cualquier tramo, calcula al instante (0 ms):
       Distancia, Tiempo en tramo, Velocidad / Pace medio, GAP (Running), Potencia (W),
       Intensidad (% FTP / % rCP) y Desnivel (+D / -D).
"""

import json
import math
import uuid

import streamlit.components.v1 as components


def _grade_heat_color(grade_pct: float) -> str:
    if grade_pct <= -3.5:
        return "#00B0FF"   # Descenso veloz
    if grade_pct <= -1.0:
        return "#00E676"   # Bajada rápida
    if grade_pct < 1.2:
        return "#94A3B8"   # Llano
    if grade_pct < 2.8:
        return "#FFD600"   # Repecho
    if grade_pct < 5.0:
        return "#FF8A00"   # Subida moderada
    return "#FF2E43"       # Subida fuerte


def _grade_heat_rgba(grade_pct: float) -> str:
    if grade_pct <= -3.5:
        return "rgba(0, 176, 255, 0.46)"
    if grade_pct <= -1.0:
        return "rgba(0, 230, 118, 0.42)"
    if grade_pct < 1.2:
        return "rgba(100, 116, 139, 0.34)"
    if grade_pct < 2.8:
        return "rgba(255, 214, 0, 0.46)"
    if grade_pct < 5.0:
        return "rgba(255, 138, 0, 0.54)"
    return "rgba(255, 46, 67, 0.60)"


def _grade_heat_label(grade_pct: float) -> str:
    if grade_pct <= -3.5:
        return "🔵 Descenso Veloz"
    if grade_pct <= -1.0:
        return "🟢 Bajada Rápida"
    if grade_pct < 1.2:
        return "⚪ Llano"
    if grade_pct < 2.8:
        return "🟡 Repecho"
    if grade_pct < 5.0:
        return "🟠 Subida Moderada"
    return "🔴 Subida Fuerte"


def _downsample_route_for_synced_widget(
    route_data: dict,
    sim: dict = None,
    sport: str = "cycling",
    wind_from_deg: float = 0.0,
    wind_kmh: float = 0.0,
    max_pts: int = 340,
) -> dict:
    """
    Reduce segmentos manteniendo sumas EXACTAS de distancia, +D y -D por punto.
    Si se entrega `sim`, enriquece cada punto con potencia, velocidad/ritmo,
    viento relativo, dirección real del viento y puntos GPS de geles (Running).
    """
    segs = route_data.get("segments") or []
    if not segs:
        return {}
    sim_segs = (sim.get("segments") or []) if isinstance(sim, dict) else []
    has_sim = len(sim_segs) == len(segs) and len(sim_segs) > 0

    n = len(segs)
    step = max(1, int(math.ceil(n / float(max_pts))))

    dist_km = []
    len_km = []
    ele_m = []
    pos_m = []
    neg_m = []
    grade_pct = []
    lat = []
    lon = []
    colors = []
    rgbas = []
    labels = []
    power_w = []
    speed_kmh = []
    pace_sec = []
    headwind_kmh = []
    crosswind_kmh = []
    local_wind_kmh = []
    local_wind_deg = []
    laps_arr = []

    for i in range(0, n, step):
        chunk = segs[i : i + step]
        d_mid = float(chunk[len(chunk) // 2].get("dist_mid_km", 0.0))
        c_len_m = sum(float(c.get("length_m", 0.0)) for c in chunk)
        c_ele = sum(float(c.get("ele_m", 0.0)) for c in chunk) / len(chunk)
        c_pos = 0.0
        c_neg = 0.0
        for c in chunk:
            de = float(c.get("ele_end", c.get("ele_m", 0.0))) - float(c.get("ele_start", c.get("ele_m", 0.0)))
            if de > 0:
                c_pos += de
            else:
                c_neg += abs(de)
        g_pct = ((c_pos - c_neg) / max(1.0, c_len_m)) * 100.0
        mid = chunk[len(chunk) // 2]

        dist_km.append(round(d_mid, 3))
        len_km.append(round(c_len_m / 1000.0, 4))
        ele_m.append(round(c_ele, 1))
        pos_m.append(round(c_pos, 2))
        neg_m.append(round(c_neg, 2))
        grade_pct.append(round(g_pct, 2))
        lat.append(round(float(mid.get("lat", 0.0)), 6))
        lon.append(round(float(mid.get("lon", 0.0)), 6))
        colors.append(_grade_heat_color(g_pct))
        rgbas.append(_grade_heat_rgba(g_pct))
        labels.append(_grade_heat_label(g_pct))
        laps_arr.append(int(mid.get("lap", 1)))

        if has_sim:
            s_chunk = sim_segs[i : i + step]
            s_mid = s_chunk[len(s_chunk) // 2]
            t_sum = sum(float(sc.get("time_seg_s", sc.get("dt_s", 1.0))) for sc in s_chunk)
            t_sum = max(0.05, t_sum)
            p_avg = sum(float(sc.get("power_w", 0.0)) * float(sc.get("time_seg_s", sc.get("dt_s", 1.0))) for sc in s_chunk) / t_sum
            v_kmh = (c_len_m / 1000.0) / (t_sum / 3600.0) if t_sum > 0.05 else float(s_mid.get("speed_kmh", 0.0))
            v_kmh = max(2.0, min(95.0, v_kmh))
            p_sec = 3600.0 / max(1.0, v_kmh)
            hw = sum(float(sc.get("headwind_kmh", 0.0)) for sc in s_chunk) / len(s_chunk)
            cw = sum(float(sc.get("crosswind_kmh", 0.0)) for sc in s_chunk) / len(s_chunk)
            lw_spd = float(s_mid.get("wind_kmh", wind_kmh))
            lw_deg = float(s_mid.get("wind_from_deg", s_mid.get("wind_deg", wind_from_deg)))
            power_w.append(round(p_avg, 0))
            speed_kmh.append(round(v_kmh, 1))
            pace_sec.append(round(p_sec, 1))
            headwind_kmh.append(round(hw, 1))
            crosswind_kmh.append(round(cw, 1))
            local_wind_kmh.append(round(lw_spd, 1))
            local_wind_deg.append(round(lw_deg, 1))

    # Construir flechas de viento sobre la vuelta 1 (para evitar solapamiento en circuitos multi-vuelta)
    wind_arrows = []
    if has_sim:
        lap1_idxs = [idx for idx, lp in enumerate(laps_arr) if lp == 1] or list(range(len(lat)))
        has_any_wind = (float(wind_kmh or 0.0) > 0.5) or (local_wind_kmh and max(local_wind_kmh) > 0.5)
        if has_any_wind and len(lap1_idxs) >= 4:
            n_arr = min(14, max(6, len(lap1_idxs) // 14))
            arr_step = max(1, len(lap1_idxs) // n_arr)
            for k in range(arr_step // 2, len(lap1_idxs), arr_step):
                idx = lap1_idxs[k]
                w_spd = local_wind_kmh[idx]
                if w_spd <= 0.4:
                    continue
                w_from = local_wind_deg[idx]
                hw = headwind_kmh[idx]
                blow_to = (w_from + 180.0) % 360.0
                if hw >= 3.0:
                    col = "#FF4B4B"
                    rel_lbl = "En contra"
                elif hw <= -3.0:
                    col = "#00E676"
                    rel_lbl = "A favor"
                else:
                    col = "#38BDF8"
                    rel_lbl = "Cruzado"
                wind_arrows.append({
                    "lat": lat[idx],
                    "lon": lon[idx],
                    "blow_to_deg": round(blow_to, 1),
                    "wind_kmh": w_spd,
                    "hw": hw,
                    "color": col,
                    "rel_lbl": rel_lbl,
                    "km": round(dist_km[idx], 1),
                })

    # Puntos GPS de geles en Running
    gel_pins = []
    if has_sim and sport == "running" and isinstance(sim.get("nutrition"), dict):
        for g in sim["nutrition"].get("gel_schedule", [])[:12]:
            g_km = float(g.get("km", 0.0))
            best_i = 0
            best_d = 1e9
            for idx, dk in enumerate(dist_km):
                dd = abs(dk - g_km)
                if dd < best_d:
                    best_d = dd
                    best_i = idx
            gel_pins.append({
                "lat": lat[best_i],
                "lon": lon[best_i],
                "num": int(g.get("gel_num", 1)),
                "km": round(g_km, 1),
                "time_str": str(g.get("time_str", "")),
                "cho_g": int(round(float(g.get("cho_g", 25)))),
            })

    return {
        "route_name": str(route_data.get("route_name", "Circuito")),
        "sport": sport,
        "has_sim": has_sim,
        "total_dist_km": round(float(route_data.get("total_distance_km", sum(len_km))), 2),
        "total_gain_m": round(float(route_data.get("elevation_gain_m", sum(pos_m))), 0),
        "total_loss_m": round(float(route_data.get("elevation_loss_m", sum(neg_m))), 0),
        "dist_km": dist_km,
        "len_km": len_km,
        "ele_m": ele_m,
        "pos_m": pos_m,
        "neg_m": neg_m,
        "grade_pct": grade_pct,
        "lat": lat,
        "lon": lon,
        "colors": colors,
        "rgbas": rgbas,
        "labels": labels,
        "power_w": power_w,
        "speed_kmh": speed_kmh,
        "pace_sec": pace_sec,
        "headwind_kmh": headwind_kmh,
        "crosswind_kmh": crosswind_kmh,
        "wind_arrows": wind_arrows,
        "gel_pins": gel_pins,
    }


def render_synced_elevation_planimetry_widget(
    route_data: dict,
    height: int = 560,
    sim: dict = None,
    sport: str = "cycling",
    wind_from_deg: float = 0.0,
    wind_kmh: float = 0.0,
):
    """
    Widget unificado de Altimetría + Mapa GPS Real de Calles (Leaflet.js + Plotly.js) sincronizados a 60 FPS:
      - Mapa real interactivo con calles, avenidas, intersecciones y selector instantáneo (Calles / Claro / Oscuro / Satélite / Topo).
      - Selector integrado "Colorear mapa por": Desnivel (Mapa de Calor), Viento Relativo, Potencia Objetivo (W) y Ritmo (/km en Running).
      - Flechas vectoriales de viento sobre las calles reales + pines GPS de Geles (en Running).
      - Hover bidireccional sincronizado (Altimetría <-> Mapa GPS Real) y Zoom con cálculo en vivo (+D / −D) + auto-enfoque del mapa.
    """
    payload = _downsample_route_for_synced_widget(
        route_data=route_data,
        sim=sim,
        sport=sport,
        wind_from_deg=wind_from_deg,
        wind_kmh=wind_kmh,
    )
    if not payload or not payload.get("dist_km"):
        return

    uid = "sync_" + uuid.uuid4().hex[:8]
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    has_sim = bool(payload.get("has_sim"))
    has_wind = len(payload.get("wind_arrows") or []) > 0
    has_gels = len(payload.get("gel_pins") or []) > 0

    # Botones de "Colorear ruta por"
    color_btns = ['<button class="pill active" data-cmode="grade">⛰️ Desnivel</button>']
    if has_sim:
        color_btns.append('<button class="pill" data-cmode="wind">🌬️ Viento</button>')
        color_btns.append('<button class="pill" data-cmode="power">⚡ Potencia (W)</button>')
        if sport == "running":
            color_btns.append('<button class="pill" data-cmode="pace">🏃 Ritmo (/km)</button>')
    color_btns_html = "".join(color_btns)

    extra_toggles = []
    if has_wind:
        extra_toggles.append(f'<button id="btn_wind_{uid}" class="pill active" title="Mostrar/ocultar flechas de viento">🧭 Viento</button>')
    if has_gels:
        extra_toggles.append(f'<button id="btn_gels_{uid}" class="pill active" title="Mostrar/ocultar pines GPS de geles">🥤 Geles</button>')
    extra_toggles_html = "".join(extra_toggles)

    chart_h = max(340, int(height) - 165)

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  body {{ background: transparent; color: #E2E8F0; overflow: hidden; }}
  .wrap {{
    background: #0B101A;
    border: 1.5px solid rgba(0, 238, 255, 0.28);
    border-radius: 14px;
    padding: 10px 12px 8px 12px;
  }}
  .bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: linear-gradient(135deg, rgba(16,24,40,0.96), rgba(10,14,24,0.98));
    border: 1.5px solid rgba(0,238,255,0.32);
    border-radius: 10px;
    padding: 7px 11px;
    margin-bottom: 7px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }}
  .bar.zoomed {{
    border-color: #FFD700;
    box-shadow: 0 0 16px rgba(255, 215, 0, 0.25);
  }}
  .badge {{
    font-size: 0.71rem;
    font-weight: 900;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(0, 238, 255, 0.14);
    color: #00EEFF;
    letter-spacing: 0.3px;
    white-space: nowrap;
  }}
  .bar.zoomed .badge {{
    background: rgba(255, 215, 0, 0.18);
    color: #FFD700;
  }}
  .metrics {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    font-size: 0.76rem;
  }}
  .m {{ display: flex; flex-direction: column; line-height: 1.2; }}
  .m .lbl {{ font-size: 0.60rem; color: #8E9BAE; font-weight: 700; text-transform: uppercase; }}
  .m .val {{ font-size: 0.85rem; font-weight: 900; color: #FFFFFF; }}
  .btn-reset {{
    background: rgba(255, 255, 255, 0.08);
    color: #E2E8F0;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 0.72rem;
    font-weight: 800;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn-reset:hover {{ background: rgba(0, 238, 255, 0.2); border-color: #00EEFF; color: #FFFFFF; }}
  .toolbar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: rgba(14, 18, 28, 0.94);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 9px;
    padding: 6px 10px;
    margin-bottom: 8px;
  }}
  .tb-group {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
  }}
  .tb-lbl {{
    font-size: 0.67rem;
    font-weight: 800;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    margin-right: 2px;
  }}
  .pill {{
    background: rgba(255,255,255,0.05);
    color: #CBD5E1;
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 999px;
    padding: 3px 9px;
    font-size: 0.71rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
  }}
  .pill:hover {{ border-color: #00EEFF; color: #FFFFFF; }}
  .pill.active {{
    background: rgba(0, 238, 255, 0.18);
    color: #00EEFF;
    border-color: #00EEFF;
    font-weight: 800;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 1.08fr 1.18fr;
    gap: 10px;
    align-items: stretch;
  }}
  @media (max-width: 780px) {{
    .grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-box {{
    background: #0E121C;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 10px;
    overflow: hidden;
    position: relative;
  }}
  .leaflet-container {{
    background: #0E121C !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
  }}
  .map-legend {{
    position: absolute;
    bottom: 8px;
    left: 8px;
    z-index: 650;
    background: rgba(11, 16, 26, 0.88);
    backdrop-filter: blur(6px);
    border: 1px solid rgba(255,255,255,0.16);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 0.67rem;
    color: #E2E8F0;
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    align-items: center;
    pointer-events: none;
    max-width: calc(100% - 88px);
  }}
  .lg-dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 3px;
    vertical-align: middle;
  }}
  .map-hud {{
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 650;
    background: rgba(11, 16, 26, 0.90);
    border: 1px solid rgba(0, 238, 255, 0.45);
    border-radius: 8px;
    padding: 4px 9px;
    font-size: 0.71rem;
    font-weight: 800;
    color: #FFFFFF;
    pointer-events: none;
    display: none;
    box-shadow: 0 3px 10px rgba(0,0,0,0.45);
  }}
  .pin-lbl {{
    background: rgba(10, 14, 24, 0.92);
    color: #FFFFFF;
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 10px;
    font-weight: 900;
    white-space: nowrap;
    box-shadow: 0 2px 6px rgba(0,0,0,0.55);
  }}
  .hint {{
    font-size: 0.69rem;
    color: #8E9BAE;
    margin-top: 6px;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 6px;
  }}
</style>
</head>
<body>
<div class="wrap">
  <div id="bar_{uid}" class="bar">
    <span id="badge_{uid}" class="badge">🗺️ CIRCUITO COMPLETO</span>
    <div class="metrics">
      <div class="m"><span class="lbl">Tramo</span><span id="m_range_{uid}" class="val">—</span></div>
      <div class="m"><span class="lbl">Distancia</span><span id="m_dist_{uid}" class="val" style="color:#00EEFF;">—</span></div>
      <div class="m"><span class="lbl">Desnivel +</span><span id="m_pos_{uid}" class="val" style="color:#FF5252;">—</span></div>
      <div class="m"><span class="lbl">Desnivel −</span><span id="m_neg_{uid}" class="val" style="color:#38BDF8;">—</span></div>
      <div class="m"><span class="lbl">Pend. Media / Máx</span><span id="m_grd_{uid}" class="val" style="color:#FFD700;">—</span></div>
      <div class="m"><span class="lbl">Altitud Mín–Máx</span><span id="m_ele_{uid}" class="val">—</span></div>
    </div>
    <button id="reset_{uid}" class="btn-reset" title="Restaurar vista completa">🔄 Ver circuito completo</button>
  </div>

  <div class="toolbar">
    <div class="tb-group" id="cmode_grp_{uid}">
      <span class="tb-lbl">🎨 Colorear ruta:</span>
      {color_btns_html}
      {extra_toggles_html}
    </div>
    <div class="tb-group" id="bmap_grp_{uid}">
      <span class="tb-lbl">🌍 Mapa:</span>
      <button class="pill active" data-bmap="osm">🗺️ Calles</button>
      <button class="pill" data-bmap="voyager">✨ Claro</button>
      <button class="pill" data-bmap="dark">🌙 Oscuro</button>
      <button class="pill" data-bmap="sat">🛰️ Satélite</button>
      <button class="pill" data-bmap="topo">🏔️ Topo</button>
    </div>
  </div>

  <div class="grid">
    <div class="chart-box"><div id="elev_{uid}" style="width:100%; height:{chart_h}px;"></div></div>
    <div class="chart-box" style="height:{chart_h}px;">
      <div id="map_{uid}" style="width:100%; height:100%;"></div>
      <div id="hud_{uid}" class="map-hud"></div>
      <div id="legend_{uid}" class="map-legend"></div>
    </div>
  </div>

  <div class="hint">
    <span>💡 <b>GPS Real Sincronizado:</b> desliza sobre la altimetría para ver la calle exacta donde vas (o pasa el mouse sobre la ruta en el mapa).</span>
    <span>🔍 <b>Zoom inteligente:</b> arrastra horizontalmente sobre la altimetría para medir cualquier puerto (+D / −D) y acercar el mapa a esas calles.</span>
  </div>
</div>

<script>
(function() {{
  const D = {data_json};
  const N = D.dist_km.length;
  const elevDiv = document.getElementById("elev_{uid}");
  const mapDiv = document.getElementById("map_{uid}");
  const barEl = document.getElementById("bar_{uid}");
  const badgeEl = document.getElementById("badge_{uid}");
  const hudEl = document.getElementById("hud_{uid}");
  const legendEl = document.getElementById("legend_{uid}");

  function fmtPace(sec) {{
    const s = Math.max(120, Math.min(1200, Math.round(sec)));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${{m}}:${{r < 10 ? "0" : ""}}${{r}}`;
  }}

  function lerpColor(hexA, hexB, t) {{
    t = Math.max(0, Math.min(1, t));
    const a = parseInt(hexA.slice(1), 16), b = parseInt(hexB.slice(1), 16);
    const ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255;
    const br = (b >> 16) & 255, bg = (b >> 8) & 255, bb = b & 255;
    const rr = Math.round(ar + (br - ar) * t);
    const rg = Math.round(ag + (bg - ag) * t);
    const rb = Math.round(ab + (bb - ab) * t);
    return `rgb(${{rr}},${{rg}},${{rb}})`;
  }}

  // Calcular rangos de potencia / ritmo para escalas de color dinámicas
  const pMin = D.has_sim && D.power_w.length ? Math.min(...D.power_w) : 100;
  const pMax = D.has_sim && D.power_w.length ? Math.max(...D.power_w) : 350;
  const paceMin = D.has_sim && D.pace_sec.length ? Math.min(...D.pace_sec) : 220;
  const paceMax = D.has_sim && D.pace_sec.length ? Math.max(...D.pace_sec) : 360;

  function segColor(idx, mode) {{
    if (mode === "wind" && D.has_sim) {{
      const hw = D.headwind_kmh[idx] || 0;
      if (hw >= 3.0) return lerpColor("#38BDF8", "#FF2E43", Math.min(1, hw / 18.0));
      if (hw <= -3.0) return lerpColor("#38BDF8", "#00E676", Math.min(1, Math.abs(hw) / 18.0));
      return "#38BDF8";
    }}
    if (mode === "power" && D.has_sim) {{
      const t = (D.power_w[idx] - pMin) / Math.max(10, pMax - pMin);
      if (t < 0.5) return lerpColor("#6B4E00", "#FFB300", t * 2);
      return lerpColor("#FFB300", "#FFF176", (t - 0.5) * 2);
    }}
    if (mode === "pace" && D.has_sim) {{
      // Más rápido (pace menor) = verde brillante, más lento = naranja/rojo
      const t = (D.pace_sec[idx] - paceMin) / Math.max(10, paceMax - paceMin);
      if (t < 0.5) return lerpColor("#00E676", "#FFD600", t * 2);
      return lerpColor("#FFD600", "#FF3B30", (t - 0.5) * 2);
    }}
    return D.colors[idx];
  }}

  function buildHoverHtml(i) {{
    let txt = `<b>Km ${{D.dist_km[i].toFixed(2)}}</b> · Alt: <b>${{Math.round(D.ele_m[i])}} m</b><br>`
            + `Pend: <b>${{D.grade_pct[i] >= 0 ? "+" : ""}}${{D.grade_pct[i].toFixed(1)}}%</b> (${{D.labels[i]}})`;
    if (D.has_sim) {{
      if (D.sport === "running") {{
        txt += `<br>Ritmo: <b>${{fmtPace(D.pace_sec[i])}}/km</b> · Stryd: <b>${{Math.round(D.power_w[i])}} W</b>`;
      }} else {{
        txt += `<br>Potencia: <b>${{Math.round(D.power_w[i])}} W</b> · Vel: <b>${{D.speed_kmh[i].toFixed(1)}} km/h</b>`;
      }}
      txt += `<br>Viento rel: <b>${{D.headwind_kmh[i] >= 0 ? "+" : ""}}${{D.headwind_kmh[i].toFixed(1)}} km/h</b>`;
    }}
    return txt;
  }}

  const hoverTexts = D.dist_km.map((_, i) => buildHoverHtml(i));

  // ── 1. PERFIL ALTIMÉTRICO CON MAPA DE CALOR (IZQUIERDA - PLOTLY) ──
  const elevMin = Math.min(...D.ele_m);
  const elevMax = Math.max(...D.ele_m);
  const elevSpan = Math.max(25, elevMax - elevMin);
  const elevPad = elevSpan * 0.12;
  const yFloor = Math.max(0, elevMin - elevPad);

  const elevTraces = [];
  let rStart = 0;
  for (let i = 1; i <= N; i++) {{
    if (i === N || D.colors[i] !== D.colors[rStart]) {{
      const endIdx = Math.min(N - 1, i);
      const xs = [], ys = [];
      for (let k = rStart; k <= endIdx; k++) {{
        xs.push(D.dist_km[k]);
        ys.push(D.ele_m[k]);
      }}
      if (xs.length >= 2) {{
        elevTraces.push({{
          x: xs.concat([xs[xs.length - 1], xs[0], xs[0]]),
          y: ys.concat([yFloor, yFloor, ys[0]]),
          type: "scatter",
          mode: "lines",
          fill: "toself",
          fillcolor: D.rgbas[rStart],
          line: {{ color: D.colors[rStart], width: 0.6 }},
          hoverinfo: "skip",
          showlegend: false
        }});
      }}
      rStart = i;
    }}
  }}

  const elevMainTraceIdx = elevTraces.length;
  elevTraces.push({{
    x: D.dist_km,
    y: D.ele_m,
    type: "scatter",
    mode: "lines+markers",
    line: {{ color: "rgba(255,255,255,0.74)", width: 1.5 }},
    marker: {{ size: 5.0, color: D.colors, line: {{ width: 0 }} }},
    text: hoverTexts,
    hovertemplate: "%{{text}}<extra></extra>",
    showlegend: false
  }});

  const elevCursorIdx = elevTraces.length;
  elevTraces.push({{
    x: [null],
    y: [null],
    type: "scatter",
    mode: "markers",
    marker: {{ size: 13, color: "#00EEFF", line: {{ color: "#FFFFFF", width: 2.5 }} }},
    hoverinfo: "skip",
    showlegend: false
  }});

  const elevLayout = {{
    title: {{ text: "⛰️ Altimetría — Mapa de Calor (Arrastra para Zoom)", font: {{ color: "#FFFFFF", size: 12 }}, x: 0.02 }},
    paper_bgcolor: "#0E121C",
    plot_bgcolor: "#0E121C",
    font: {{ color: "#CBD5E1", size: 10.5 }},
    margin: {{ l: 48, r: 12, t: 34, b: 36 }},
    xaxis: {{ title: "Distancia (km)", gridcolor: "rgba(255,255,255,0.06)", zeroline: false }},
    yaxis: {{ title: "Altitud (m)", range: [yFloor, elevMax + elevPad], gridcolor: "rgba(255,255,255,0.06)", zeroline: false }},
    dragmode: "zoom",
    hovermode: "closest"
  }};
  const cfg = {{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "autoScale2d", "toggleSpikelines"] }};

  // ── 2. MAPA GPS REAL DE CALLES (DERECHA - LEAFLET.JS) ──
  const map = L.map("map_{uid}", {{
    zoomControl: true,
    attributionControl: true,
    preferCanvas: true
  }});
  map.attributionControl.setPrefix("");

  const baseLayers = {{
    osm: L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19, attribution: "© OpenStreetMap"
    }}),
    voyager: L.tileLayer("https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      maxZoom: 19, attribution: "© CARTO / OSM"
    }}),
    dark: L.tileLayer("https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      maxZoom: 19, attribution: "© CARTO / OSM"
    }}),
    sat: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}", {{
      maxZoom: 18, attribution: "© Esri Satélite"
    }}),
    topo: L.tileLayer("https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 17, attribution: "© OpenTopoMap"
    }})
  }};

  let activeBase = baseLayers.osm.addTo(map);
  const allLatLngs = D.lat.map((la, i) => [la, D.lon[i]]);
  const fullBounds = L.latLngBounds(allLatLngs);
  map.fitBounds(fullBounds, {{ padding: [24, 24] }});

  // Borde oscuro inferior para máximo contraste sobre calles claras y satélite
  L.polyline(allLatLngs, {{
    color: "#090D16",
    weight: 8,
    opacity: 0.72,
    lineJoin: "round",
    lineCap: "round",
    interactive: false
  }}).addTo(map);

  // Capa de segmentos coloreados + capa de resaltado de zoom + viento + geles
  const routeLayer = L.layerGroup().addTo(map);
  const zoomHighlightLayer = L.layerGroup().addTo(map);
  const windLayer = L.layerGroup().addTo(map);
  const gelLayer = L.layerGroup().addTo(map);

  let currentColorMode = "grade";
  let showWind = true;
  let showGels = true;
  let currentZoomIdxs = null;

  // Marcador dinámico sincronizado (cyan brillante)
  const syncMarker = L.circleMarker([D.lat[0], D.lon[0]], {{
    radius: 8,
    color: "#FFFFFF",
    weight: 3,
    fillColor: "#00EEFF",
    fillOpacity: 1.0,
    interactive: false
  }});
  let syncAdded = false;

  function showCursorAt(idx) {{
    if (idx === undefined || idx < 0 || idx >= N) return;
    const ll = [D.lat[idx], D.lon[idx]];
    syncMarker.setLatLng(ll);
    if (!syncAdded) {{ syncMarker.addTo(map); syncAdded = true; }}
    Plotly.restyle(elevDiv, {{ x: [[D.dist_km[idx]]], y: [[D.ele_m[idx]]] }}, [elevCursorIdx]);

    let hud = `Km ${{D.dist_km[idx].toFixed(1)}} · ${{Math.round(D.ele_m[idx])}}m · ${{D.grade_pct[idx] >= 0 ? "+" : ""}}${{D.grade_pct[idx].toFixed(1)}}%`;
    if (D.has_sim) {{
      hud += D.sport === "running"
        ? ` · ${{fmtPace(D.pace_sec[idx])}}/km · ${{Math.round(D.power_w[idx])}}W`
        : ` · ${{Math.round(D.power_w[idx])}}W · ${{D.speed_kmh[idx].toFixed(1)}} km/h`;
    }}
    hudEl.textContent = hud;
    hudEl.style.display = "block";
  }}

  function hideCursor() {{
    if (syncAdded) {{ map.removeLayer(syncMarker); syncAdded = false; }}
    Plotly.restyle(elevDiv, {{ x: [[null]], y: [[null]] }}, [elevCursorIdx]);
    hudEl.style.display = "none";
  }}

  function updateLegend(mode) {{
    if (mode === "wind") {{
      legendEl.innerHTML =
        `<span><i class="lg-dot" style="background:#00E676"></i>A favor</span>` +
        `<span><i class="lg-dot" style="background:#38BDF8"></i>Cruzado/Calma</span>` +
        `<span><i class="lg-dot" style="background:#FF2E43"></i>En contra</span>`;
    }} else if (mode === "power") {{
      legendEl.innerHTML =
        `<span><i class="lg-dot" style="background:#6B4E00"></i>${{Math.round(pMin)}} W</span>` +
        `<span><i class="lg-dot" style="background:#FFB300"></i>${{Math.round((pMin+pMax)/2)}} W</span>` +
        `<span><i class="lg-dot" style="background:#FFF176"></i>${{Math.round(pMax)}} W</span>`;
    }} else if (mode === "pace") {{
      legendEl.innerHTML =
        `<span><i class="lg-dot" style="background:#00E676"></i>${{fmtPace(paceMin)}}/km</span>` +
        `<span><i class="lg-dot" style="background:#FFD600"></i>${{fmtPace((paceMin+paceMax)/2)}}/km</span>` +
        `<span><i class="lg-dot" style="background:#FF3B30"></i>${{fmtPace(paceMax)}}/km</span>`;
    }} else {{
      legendEl.innerHTML =
        `<span><i class="lg-dot" style="background:#00B0FF"></i>Desc</span>` +
        `<span><i class="lg-dot" style="background:#00E676"></i>Bajada</span>` +
        `<span><i class="lg-dot" style="background:#94A3B8"></i>Llano</span>` +
        `<span><i class="lg-dot" style="background:#FFD600"></i>Repecho</span>` +
        `<span><i class="lg-dot" style="background:#FF8A00"></i>Subida</span>` +
        `<span><i class="lg-dot" style="background:#FF2E43"></i>Fuerte</span>`;
    }}
  }}

  function renderColoredRoute(mode, dimmed) {{
    routeLayer.clearLayers();
    const op = dimmed ? 0.30 : 0.96;
    const wt = dimmed ? 4.5 : 5.2;
    for (let i = 0; i < N - 1; i++) {{
      const col = segColor(i, mode);
      const seg = L.polyline([[D.lat[i], D.lon[i]], [D.lat[i + 1], D.lon[i + 1]]], {{
        color: col,
        weight: wt,
        opacity: op,
        lineJoin: "round",
        lineCap: "round"
      }});
      seg.on("mouseover", () => showCursorAt(i));
      seg.on("click", () => showCursorAt(i));
      seg.bindTooltip(hoverTexts[i], {{ sticky: true, direction: "top", opacity: 0.95 }});
      routeLayer.addLayer(seg);
    }}
    updateLegend(mode);
  }}

  // Pines de Salida y Meta sobre el mapa real
  L.marker([D.lat[0], D.lon[0]], {{
    icon: L.divIcon({{
      className: "",
      html: `<div class="pin-lbl" style="border:1.5px solid #00FF88; color:#00FF88;">🟢 Salida</div>`,
      iconSize: [64, 20],
      iconAnchor: [32, 24]
    }})
  }}).addTo(map);

  L.marker([D.lat[N - 1], D.lon[N - 1]], {{
    icon: L.divIcon({{
      className: "",
      html: `<div class="pin-lbl" style="border:1.5px solid #FF5252; color:#FF8A80;">🏁 Meta</div>`,
      iconSize: [58, 20],
      iconAnchor: [29, -4]
    }})
  }}).addTo(map);

  // Renderizar flechas de viento sobre calles reales
  function renderWindArrows() {{
    windLayer.clearLayers();
    if (!showWind || !D.wind_arrows || !D.wind_arrows.length) return;
    for (const wa of D.wind_arrows) {{
      const svg = `<div style="transform:rotate(${{wa.blow_to_deg}}deg); width:24px; height:24px; display:flex; align-items:center; justify-content:center; filter:drop-shadow(0 1px 2px rgba(0,0,0,0.85));">` +
        `<svg width="22" height="22" viewBox="0 0 24 24"><path d="M12 2 L17 11 L13.5 11 L13.5 21 L10.5 21 L10.5 11 L7 11 Z" fill="${{wa.color}}" stroke="#0B101A" stroke-width="1.4"/></svg></div>`;
      const mk = L.marker([wa.lat, wa.lon], {{
        icon: L.divIcon({{ className: "", html: svg, iconSize: [24, 24], iconAnchor: [12, 12] }})
      }});
      mk.bindTooltip(
        `<b>🧭 Viento · Km ${{wa.km}}</b><br>Real: <b>${{wa.wind_kmh}} km/h</b><br>Relativo: <b>${{wa.hw >= 0 ? "+" : ""}}${{wa.hw}} km/h</b> (${{wa.rel_lbl}})`,
        {{ direction: "top", opacity: 0.95 }}
      );
      windLayer.addLayer(mk);
    }}
  }}

  // Renderizar pines GPS de Geles (Running)
  function renderGelPins() {{
    gelLayer.clearLayers();
    if (!showGels || !D.gel_pins || !D.gel_pins.length) return;
    for (const gp of D.gel_pins) {{
      const mk = L.marker([gp.lat, gp.lon], {{
        icon: L.divIcon({{
          className: "",
          html: `<div class="pin-lbl" style="border:1.5px solid #FFAA00; color:#FFD166;">⚡G${{gp.num}}</div>`,
          iconSize: [42, 18],
          iconAnchor: [21, 9]
        }})
      }});
      mk.bindTooltip(`<b>⚡ Gel #${{gp.num}} (${{gp.cho_g}}g CHO)</b><br>Km ${{gp.km}} · Tiempo est: ${{gp.time_str}}`, {{ direction: "top", opacity: 0.95 }});
      gelLayer.addLayer(mk);
    }}
  }}

  renderColoredRoute(currentColorMode, false);
  renderWindArrows();
  renderGelPins();

  map.on("mouseout", () => hideCursor());

  function updateSummary(x0, x1, isZoom) {{
    let idxs = [];
    for (let i = 0; i < N; i++) {{
      if (D.dist_km[i] >= x0 - 0.001 && D.dist_km[i] <= x1 + 0.001) idxs.push(i);
    }}
    if (idxs.length === 0) {{
      for (let i = 0; i < N; i++) idxs.push(i);
      isZoom = false;
    }}
    const i0 = idxs[0], i1 = idxs[idxs.length - 1];
    let distSum = 0, posSum = 0, negSum = 0, maxG = -999, eMin = 1e9, eMax = -1e9;
    for (const i of idxs) {{
      distSum += D.len_km[i];
      posSum += D.pos_m[i];
      negSum += D.neg_m[i];
      if (D.grade_pct[i] > maxG) maxG = D.grade_pct[i];
      if (D.ele_m[i] < eMin) eMin = D.ele_m[i];
      if (D.ele_m[i] > eMax) eMax = D.ele_m[i];
    }}
    if (!isZoom) {{
      distSum = D.total_dist_km;
      posSum = D.total_gain_m;
      negSum = D.total_loss_m;
    }}
    const avgG = distSum > 0.01 ? ((posSum - negSum) / (distSum * 1000.0)) * 100.0 : 0.0;

    if (isZoom) {{
      barEl.classList.add("zoomed");
      badgeEl.textContent = "🔍 TRAMO EN ZOOM";
    }} else {{
      barEl.classList.remove("zoomed");
      badgeEl.textContent = "🗺️ CIRCUITO COMPLETO";
    }}
    document.getElementById("m_range_{uid}").textContent = `Km ${{D.dist_km[i0].toFixed(1)}} → Km ${{D.dist_km[i1].toFixed(1)}}`;
    document.getElementById("m_dist_{uid}").textContent = `${{distSum.toFixed(2)}} km`;
    document.getElementById("m_pos_{uid}").textContent = `+${{Math.round(posSum)}} m`;
    document.getElementById("m_neg_{uid}").textContent = `−${{Math.round(negSum)}} m`;
    document.getElementById("m_grd_{uid}").textContent = `${{avgG >= 0 ? "+" : ""}}${{avgG.toFixed(1)}}% / +${{Math.max(0, maxG).toFixed(1)}}%`;
    document.getElementById("m_ele_{uid}").textContent = `${{Math.round(eMin)}}–${{Math.round(eMax)}} m`;

    zoomHighlightLayer.clearLayers();
    if (isZoom) {{
      currentZoomIdxs = idxs;
      renderColoredRoute(currentColorMode, true);
      const hLatLngs = idxs.map(i => [D.lat[i], D.lon[i]]);
      L.polyline(hLatLngs, {{ color: "#000000", weight: 9, opacity: 0.75, interactive: false }}).addTo(zoomHighlightLayer);
      L.polyline(hLatLngs, {{ color: "#FFD700", weight: 5.5, opacity: 1.0, interactive: false }}).addTo(zoomHighlightLayer);
      if (hLatLngs.length >= 2) {{
        map.fitBounds(L.latLngBounds(hLatLngs), {{ padding: [32, 32], maxZoom: 16 }});
      }}
    }} else {{
      currentZoomIdxs = null;
      renderColoredRoute(currentColorMode, false);
      map.fitBounds(fullBounds, {{ padding: [24, 24] }});
    }}
  }}

  // Eventos de botones de "Colorear ruta"
  document.querySelectorAll("#cmode_grp_{uid} .pill[data-cmode]").forEach(btn => {{
    btn.addEventListener("click", () => {{
      document.querySelectorAll("#cmode_grp_{uid} .pill[data-cmode]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentColorMode = btn.getAttribute("data-cmode");
      renderColoredRoute(currentColorMode, currentZoomIdxs !== null);
    }});
  }});

  // Botón toggle Viento
  const btnWind = document.getElementById("btn_wind_{uid}");
  if (btnWind) {{
    btnWind.addEventListener("click", () => {{
      showWind = !showWind;
      btnWind.classList.toggle("active", showWind);
      renderWindArrows();
    }});
  }}

  // Botón toggle Geles
  const btnGels = document.getElementById("btn_gels_{uid}");
  if (btnGels) {{
    btnGels.addEventListener("click", () => {{
      showGels = !showGels;
      btnGels.classList.toggle("active", showGels);
      renderGelPins();
    }});
  }}

  // Selector de mapa base real (Calles / Claro / Oscuro / Satélite / Topo)
  document.querySelectorAll("#bmap_grp_{uid} .pill[data-bmap]").forEach(btn => {{
    btn.addEventListener("click", () => {{
      document.querySelectorAll("#bmap_grp_{uid} .pill[data-bmap]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const key = btn.getAttribute("data-bmap");
      if (baseLayers[key]) {{
        map.removeLayer(activeBase);
        activeBase = baseLayers[key].addTo(map);
      }}
    }});
  }});

  // Inicializar Plotly Altimetría + Sincronización
  Plotly.newPlot(elevDiv, elevTraces, elevLayout, cfg).then(() => {{
    updateSummary(0, D.total_dist_km + 1, false);
    setTimeout(() => map.invalidateSize(), 180);

    elevDiv.on("plotly_hover", function(ev) {{
      if (!ev || !ev.points || !ev.points.length) return;
      const pt = ev.points.find(p => p.curveNumber === elevMainTraceIdx) || ev.points[0];
      showCursorAt(pt.pointIndex);
    }});
    elevDiv.on("plotly_unhover", function() {{
      hideCursor();
    }});

    elevDiv.on("plotly_relayout", function(ed) {{
      if (!ed) return;
      if (ed["xaxis.autorange"]) {{
        updateSummary(0, D.total_dist_km + 1, false);
        return;
      }}
      let x0 = ed["xaxis.range[0]"];
      let x1 = ed["xaxis.range[1]"];
      if (Array.isArray(ed["xaxis.range"])) {{
        x0 = ed["xaxis.range"][0];
        x1 = ed["xaxis.range"][1];
      }}
      if (x0 !== undefined && x1 !== undefined) {{
        const lo = Math.min(x0, x1), hi = Math.max(x0, x1);
        const isZ = (hi - lo) < (D.total_dist_km * 0.985);
        updateSummary(lo, hi, isZ);
      }}
    }});

    document.getElementById("reset_{uid}").addEventListener("click", function() {{
      Plotly.relayout(elevDiv, {{ "xaxis.autorange": true, "yaxis.range": [yFloor, elevMax + elevPad] }});
      updateSummary(0, D.total_dist_km + 1, false);
    }});
  }});
}})();
</script>
</body>
</html>"""

    components.html(html_doc, height=height, scrolling=False)


def _downsample_sim_for_pacing_widget(sim: dict, sport: str, ftp_or_rcp: float, max_pts: int = 320) -> dict:
    """Prepara series agregadas por tramo para cálculo exacto en 0 ms al hacer zoom en Pacing."""
    segs = sim.get("segments") or []
    if not segs:
        return {}
    n = len(segs)
    step = max(1, int(math.ceil(n / float(max_pts))))

    dist_km = []
    len_km = []
    dt_s = []
    ele_m = []
    pos_m = []
    neg_m = []
    grade_pct = []
    power_w = []
    speed_kmh = []
    pace_min = []
    gap_min = []
    colors = []
    rgbas = []

    for i in range(0, n, step):
        chunk = segs[i : i + step]
        d_mid = float(chunk[len(chunk) // 2].get("dist_km", 0.0))
        if sport == "running":
            c_dt = sum(float(c.get("dt_s", 0.0)) for c in chunk)
            c_len_km = sum((float(c.get("speed_ms", 0.0)) * float(c.get("dt_s", 0.0))) / 1000.0 for c in chunk)
        else:
            c_dt = sum(float(c.get("time_seg_s", 0.0)) for c in chunk)
            c_len_km = sum((float(c.get("speed_kmh", 0.0)) * float(c.get("time_seg_s", 0.0))) / 3600.0 for c in chunk)
        c_dt = max(0.1, c_dt)
        c_v_kmh = (c_len_km / (c_dt / 3600.0)) if c_len_km > 0 else float(chunk[0].get("speed_kmh", 0.0))
        c_p = sum(float(c.get("power_w", 0.0)) * float(c.get("dt_s" if sport == "running" else "time_seg_s", 1.0)) for c in chunk) / c_dt
        c_ele = sum(float(c.get("ele_m", 0.0)) for c in chunk) / len(chunk)
        c_g = sum(float(c.get("grade_pct", 0.0)) for c in chunk) / len(chunk)

        # Desnivel positivo / negativo dentro del chunk
        c_pos = 0.0
        c_neg = 0.0
        for j in range(len(chunk)):
            de = (float(chunk[j].get("grade_pct", 0.0)) / 100.0) * (
                (float(chunk[j].get("speed_ms", 0.0)) * float(chunk[j].get("dt_s", 0.0)))
                if sport == "running"
                else (float(chunk[j].get("speed_kmh", 0.0)) * float(chunk[j].get("time_seg_s", 0.0)) / 3.6)
            )
            if de > 0:
                c_pos += de
            else:
                c_neg += abs(de)

        dist_km.append(round(d_mid, 3))
        len_km.append(round(c_len_km, 4))
        dt_s.append(round(c_dt, 2))
        ele_m.append(round(c_ele, 1))
        pos_m.append(round(c_pos, 2))
        neg_m.append(round(c_neg, 2))
        grade_pct.append(round(c_g, 2))
        power_w.append(round(c_p, 1))
        speed_kmh.append(round(c_v_kmh, 2))
        colors.append(_grade_heat_color(c_g))
        rgbas.append(_grade_heat_rgba(c_g))

        if sport == "running":
            p_sec = (c_dt / max(0.001, c_len_km))
            g_sec = sum(float(c.get("gap_sec_km", p_sec)) * float(c.get("dt_s", 1.0)) for c in chunk) / c_dt
            pace_min.append(round(p_sec / 60.0, 3))
            gap_min.append(round(g_sec / 60.0, 3))

    gels = []
    if sport == "running":
        for g in sim.get("nutrition", {}).get("gel_schedule", []):
            g_km = float(g.get("km", 0.0))
            near_i = min(range(len(dist_km)), key=lambda idx: abs(dist_km[idx] - g_km))
            gels.append({
                "km": g_km,
                "ele": ele_m[near_i],
                "label": f"G{g['gel_num']}",
                "hover": f"⚡ Gel #{g['gel_num']} · Km {g_km:.1f} ({g['time_str']}) · {g['cho_g']}g",
            })

    s = sim.get("summary", {})
    return {
        "sport": sport,
        "ftp_or_rcp": round(float(ftp_or_rcp or 250.0), 1),
        "total_dist_km": round(float(s.get("total_distance_km", sum(len_km))), 2),
        "dist_km": dist_km,
        "len_km": len_km,
        "dt_s": dt_s,
        "ele_m": ele_m,
        "pos_m": pos_m,
        "neg_m": neg_m,
        "grade_pct": grade_pct,
        "power_w": power_w,
        "speed_kmh": speed_kmh,
        "pace_min": pace_min,
        "gap_min": gap_min,
        "colors": colors,
        "rgbas": rgbas,
        "gels": gels,
    }


def render_interactive_pacing_zoom_widget(
    sim: dict,
    sport: str = "cycling",
    ftp_or_rcp: float = 250.0,
    height: int = 525,
):
    """
    Gráfica de Plan de Pacing interactiva con Zoom en vivo (0 ms):
      - Ciclismo: Potencia (Amarillo #FFD700), Velocidad (Verde #00FF88), Altimetría debajo.
      - Running:  Ritmo Real (Verde #00FF88, eje invertido), GAP (Cyan punteado), Potencia Stryd (Amarillo #FFD700), Altimetría + Geles debajo.
      - Al arrastrar y hacer zoom en cualquier tramo muestra al instante:
        Distancia, Tiempo en tramo, Velocidad / Pace, GAP (Running), Potencia (W), Intensidad (% FTP / % rCP) y Desnivel (+D / -D).
    """
    payload = _downsample_sim_for_pacing_widget(sim, sport=sport, ftp_or_rcp=ftp_or_rcp)
    if not payload or not payload.get("dist_km"):
        return

    uid = "pac_" + uuid.uuid4().hex[:8]
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    is_run = sport == "running"

    if is_run:
        m3_lbl = "Ritmo Medio (Pace)"
        m4_lbl = "Ritmo GAP (Llano)"
        m5_lbl = "Potencia Stryd · % rCP"
        chart_title = "🏃‍♂️ Plan de Pacing Running — Ritmo (Verde) · GAP (Cyan) · Potencia Stryd (Amarillo) [Arrastra para Zoom]"
    else:
        m3_lbl = "Velocidad Media"
        m4_lbl = "Potencia Media / NP"
        m5_lbl = "Intensidad (% FTP / IF)"
        chart_title = "⚡ Plan de Pacing Ciclismo — Potencia (Amarillo) · Velocidad (Verde) · Altimetría [Arrastra para Zoom]"

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  body {{ background: transparent; color: #E2E8F0; overflow: hidden; }}
  .wrap {{
    background: #0B101A;
    border: 1px solid rgba(0, 238, 255, 0.22);
    border-radius: 14px;
    padding: 10px 12px 8px 12px;
  }}
  .bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: linear-gradient(135deg, rgba(16,24,40,0.96), rgba(10,14,24,0.98));
    border: 1.5px solid rgba(0,255,136,0.34);
    border-radius: 10px;
    padding: 8px 12px;
    margin-bottom: 8px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }}
  .bar.zoomed {{
    border-color: #FFD700;
    box-shadow: 0 0 18px rgba(255, 215, 0, 0.28);
  }}
  .badge {{
    font-size: 0.72rem;
    font-weight: 900;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(0, 255, 136, 0.14);
    color: #00FF88;
    white-space: nowrap;
  }}
  .bar.zoomed .badge {{
    background: rgba(255, 215, 0, 0.2);
    color: #FFD700;
  }}
  .metrics {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }}
  .m {{ display: flex; flex-direction: column; line-height: 1.2; }}
  .m .lbl {{ font-size: 0.61rem; color: #8E9BAE; font-weight: 700; text-transform: uppercase; }}
  .m .val {{ font-size: 0.88rem; font-weight: 900; color: #FFFFFF; }}
  .btn-reset {{
    background: rgba(255, 255, 255, 0.08);
    color: #E2E8F0;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 0.73rem;
    font-weight: 800;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn-reset:hover {{ background: rgba(255, 215, 0, 0.2); border-color: #FFD700; color: #FFFFFF; }}
  .chart-box {{
    background: #0E121C;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    overflow: hidden;
  }}
  .hint {{
    font-size: 0.7rem;
    color: #8E9BAE;
    margin-top: 5px;
  }}
</style>
</head>
<body>
<div class="wrap">
  <div id="pbar_{uid}" class="bar">
    <span id="pbadge_{uid}" class="badge">📊 PLAN COMPLETO</span>
    <div class="metrics">
      <div class="m"><span class="lbl">Tramo</span><span id="pm_rng_{uid}" class="val">—</span></div>
      <div class="m"><span class="lbl">Distancia</span><span id="pm_dist_{uid}" class="val" style="color:#00EEFF;">—</span></div>
      <div class="m"><span class="lbl">Tiempo Tramo</span><span id="pm_time_{uid}" class="val">—</span></div>
      <div class="m"><span class="lbl">{m3_lbl}</span><span id="pm_m3_{uid}" class="val" style="color:#00FF88;">—</span></div>
      <div class="m"><span class="lbl">{m4_lbl}</span><span id="pm_m4_{uid}" class="val" style="color:{'#38BDF8' if is_run else '#FFD700'};">—</span></div>
      <div class="m"><span class="lbl">{m5_lbl}</span><span id="pm_m5_{uid}" class="val" style="color:{'#FFD700' if is_run else '#C084FC'};">—</span></div>
      <div class="m"><span class="lbl">Desnivel + / −</span><span id="pm_el_{uid}" class="val" style="color:#FF6B6B;">—</span></div>
    </div>
    <button id="preset_{uid}" class="btn-reset">🔄 Ver plan completo</button>
  </div>
  <div class="chart-box">
    <div id="pplot_{uid}" style="width:100%; height:415px;"></div>
  </div>
  <div class="hint">🔍 <b>Arrastra horizontalmente sobre cualquier subida, llano o bajada</b> para analizar en vivo la distancia, velocidad/ritmo, potencia, intensidad y desnivel (+D / −D) de ese tramo exacto. Doble clic para volver al plan completo.</div>
</div>

<script>
(function() {{
  const D = {data_json};
  const N = D.dist_km.length;
  const isRun = D.sport === "running";
  const plotDiv = document.getElementById("pplot_{uid}");
  const barEl = document.getElementById("pbar_{uid}");
  const badgeEl = document.getElementById("pbadge_{uid}");

  function fmtHMS(sec) {{
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${{h}}:${{String(m).padStart(2,'0')}}:${{String(s).padStart(2,'0')}}`;
    return `${{String(m).padStart(2,'0')}}:${{String(s).padStart(2,'0')}}`;
  }}
  function fmtPace(secKm) {{
    if (!isFinite(secKm) || secKm <= 0) return "—";
    const s = Math.round(Math.max(120, Math.min(1800, secKm)));
    return `${{Math.floor(s/60)}}:${{String(s%60).padStart(2,'0')}}`;
  }}

  const traces = [];
  let layout = {{}};

  // Función auxiliar: polígonos rellenos de Mapa de Calor de Desniveles para la altimetría inferior (y3)
  const eMinP = Math.min(...D.ele_m);
  const eMaxP = Math.max(...D.ele_m);
  const ePadP = Math.max(20, (eMaxP - eMinP) * 0.14);
  const eFloorP = Math.max(0, eMinP - ePadP);

  function addColoredElevationSubTraces() {{
    let rs = 0;
    for (let i = 1; i <= N; i++) {{
      if (i === N || D.colors[i] !== D.colors[rs]) {{
        const endI = Math.min(N - 1, i);
        const xs = [], ys = [];
        for (let k = rs; k <= endI; k++) {{
          xs.push(D.dist_km[k]);
          ys.push(D.ele_m[k]);
        }}
        if (xs.length >= 2) {{
          traces.push({{
            x: xs.concat([xs[xs.length - 1], xs[0], xs[0]]),
            y: ys.concat([eFloorP, eFloorP, ys[0]]),
            type: "scatter", mode: "lines", fill: "toself",
            fillcolor: D.rgbas[rs], line: {{ color: D.colors[rs], width: 0.5 }},
            hoverinfo: "skip", showlegend: false, xaxis: "x", yaxis: "y3"
          }});
        }}
        rs = i;
      }}
    }}
    traces.push({{
      x: D.dist_km, y: D.ele_m, name: "⛰️ Altimetría (m)",
      type: "scatter", mode: "lines+markers",
      line: {{ color: "rgba(255,255,255,0.7)", width: 1.4 }},
      marker: {{ size: 3.5, color: D.colors }},
      hovertemplate: "Altitud: <b>%{{y:.0f}} m</b><extra></extra>",
      xaxis: "x", yaxis: "y3"
    }});
  }}

  if (isRun) {{
    const pTexts = D.dist_km.map((km, i) =>
      `<b>Km ${{km.toFixed(2)}}</b><br>Ritmo Real: <b>${{fmtPace(D.pace_min[i]*60)}} /km</b> (${{D.speed_kmh[i].toFixed(1)}} km/h)<br>GAP: <b>${{fmtPace(D.gap_min[i]*60)}} /km</b><br>Potencia Stryd: <b>${{D.power_w[i].toFixed(0)}} W</b> (${{Math.round(D.power_w[i]/D.ftp_or_rcp*100)}}% rCP)<br>Altitud: ${{Math.round(D.ele_m[i])}} m · Pend: ${{D.grade_pct[i] >= 0 ? '+' : ''}}${{D.grade_pct[i].toFixed(1)}}%`
    );
    traces.push({{
      x: D.dist_km, y: D.pace_min, name: "🏃‍♂️ Ritmo Real (min/km)",
      type: "scatter", mode: "lines", line: {{ color: "#00FF88", width: 2.6 }},
      text: pTexts, hovertemplate: "%{{text}}<extra></extra>",
      xaxis: "x", yaxis: "y"
    }});
    traces.push({{
      x: D.dist_km, y: D.gap_min, name: "🏔️ Ritmo GAP (Llano)",
      type: "scatter", mode: "lines", line: {{ color: "#38BDF8", width: 1.7, dash: "dot" }},
      hoverinfo: "skip", xaxis: "x", yaxis: "y"
    }});
    traces.push({{
      x: D.dist_km, y: D.power_w, name: "⚡ Potencia Stryd (W)",
      type: "scatter", mode: "lines", line: {{ color: "#FFD700", width: 2.2 }},
      hovertemplate: "⚡ Stryd: <b>%{{y:.0f}} W</b><extra></extra>",
      xaxis: "x", yaxis: "y2"
    }});
    addColoredElevationSubTraces();
    if (D.gels && D.gels.length) {{
      traces.push({{
        x: D.gels.map(g => g.km), y: D.gels.map(g => g.ele),
        name: "⚡ Toma de Gel", type: "scatter", mode: "markers+text",
        marker: {{ size: 10, color: "#FFAA00", symbol: "diamond", line: {{ color: "#FFFFFF", width: 1.2 }} }},
        text: D.gels.map(g => g.label), textposition: "top center",
        textfont: {{ color: "#FFAA00", size: 10 }},
        hovertext: D.gels.map(g => g.hover), hovertemplate: "<b>%{{hovertext}}</b><extra></extra>",
        xaxis: "x", yaxis: "y3"
      }});
    }}

    const pMin = Math.max(2.5, Math.min(...D.pace_min, ...D.gap_min) - 0.35);
    const pMax = Math.min(14.0, Math.max(...D.pace_min, ...D.gap_min) + 0.35);
    const stepT = (pMax - pMin) <= 3.5 ? 0.5 : 1.0;
    const tVals = [], tTxt = [];
    for (let v = Math.floor(pMin / stepT) * stepT; v <= pMax + 0.01; v += stepT) {{
      tVals.push(Number(v.toFixed(2)));
      tTxt.push(fmtPace(v * 60));
    }}

    layout = {{
      title: {{ text: "{chart_title}", font: {{ color: "#FFFFFF", size: 12.5 }}, x: 0.01 }},
      paper_bgcolor: "#0E121C", plot_bgcolor: "#0E121C",
      font: {{ color: "#E2E8F0", size: 10.5 }},
      margin: {{ l: 52, r: 52, t: 44, b: 38 }},
      legend: {{ orientation: "h", yanchor: "bottom", y: 1.01, xanchor: "right", x: 1, font: {{ size: 10 }} }},
      hovermode: "x unified",
      dragmode: "zoom",
      xaxis: {{ title: "Distancia (km)", domain: [0, 1], gridcolor: "rgba(255,255,255,0.06)", anchor: "y3" }},
      yaxis: {{ title: "Ritmo (min/km)", domain: [0.34, 1.0], range: [pMax, pMin], tickvals: tVals, ticktext: tTxt, gridcolor: "rgba(255,255,255,0.06)", color: "#00FF88" }},
      yaxis2: {{ title: "Stryd (W)", overlaying: "y", side: "right", showgrid: false, color: "#FFD700" }},
      yaxis3: {{ title: "Altitud (m)", domain: [0.0, 0.28], range: [eFloorP, eMaxP + ePadP], gridcolor: "rgba(255,255,255,0.05)", color: "#94A3B8" }}
    }};
  }} else {{
    // CICLISMO: Potencia (Amarillo #FFD700) + Velocidad (Verde #00FF88) + Altimetría coloreada debajo
    const cTexts = D.dist_km.map((km, i) =>
      `<b>Km ${{km.toFixed(2)}}</b><br>Potencia: <b>${{D.power_w[i].toFixed(0)}} W</b> (${{Math.round(D.power_w[i]/D.ftp_or_rcp*100)}}% FTP)<br>Velocidad: <b>${{D.speed_kmh[i].toFixed(1)}} km/h</b><br>Altitud: ${{Math.round(D.ele_m[i])}} m · Pend: ${{D.grade_pct[i] >= 0 ? '+' : ''}}${{D.grade_pct[i].toFixed(1)}}%`
    );
    traces.push({{
      x: D.dist_km, y: D.power_w, name: "⚡ Potencia Obj. (W)",
      type: "scatter", mode: "lines", line: {{ color: "#FFD700", width: 2.5 }},
      text: cTexts, hovertemplate: "%{{text}}<extra></extra>",
      xaxis: "x", yaxis: "y"
    }});
    traces.push({{
      x: [D.dist_km[0], D.dist_km[N - 1]], y: [D.ftp_or_rcp, D.ftp_or_rcp],
      name: `FTP (${{Math.round(D.ftp_or_rcp)}} W)`,
      type: "scatter", mode: "lines", line: {{ color: "rgba(255,82,82,0.7)", width: 1.4, dash: "dash" }},
      hoverinfo: "skip", xaxis: "x", yaxis: "y"
    }});
    traces.push({{
      x: D.dist_km, y: D.speed_kmh, name: "🚀 Velocidad Est. (km/h)",
      type: "scatter", mode: "lines", line: {{ color: "#00FF88", width: 2.2 }},
      hovertemplate: "🚀 Velocidad: <b>%{{y:.1f}} km/h</b><extra></extra>",
      xaxis: "x", yaxis: "y2"
    }});
    addColoredElevationSubTraces();

    layout = {{
      title: {{ text: "{chart_title}", font: {{ color: "#FFFFFF", size: 12.5 }}, x: 0.01 }},
      paper_bgcolor: "#0E121C", plot_bgcolor: "#0E121C",
      font: {{ color: "#E2E8F0", size: 10.5 }},
      margin: {{ l: 52, r: 52, t: 44, b: 38 }},
      legend: {{ orientation: "h", yanchor: "bottom", y: 1.01, xanchor: "right", x: 1, font: {{ size: 10 }} }},
      hovermode: "x unified",
      dragmode: "zoom",
      xaxis: {{ title: "Distancia (km)", domain: [0, 1], gridcolor: "rgba(255,255,255,0.06)", anchor: "y3" }},
      yaxis: {{ title: "Potencia (W)", domain: [0.34, 1.0], gridcolor: "rgba(255,255,255,0.06)", color: "#FFD700" }},
      yaxis2: {{ title: "Velocidad (km/h)", overlaying: "y", side: "right", showgrid: false, color: "#00FF88" }},
      yaxis3: {{ title: "Altitud (m)", domain: [0.0, 0.28], range: [eFloorP, eMaxP + ePadP], gridcolor: "rgba(255,255,255,0.05)", color: "#94A3B8" }}
    }};
  }}

  function updatePacingSummary(x0, x1, isZoom) {{
    let idxs = [];
    for (let i = 0; i < N; i++) {{
      if (D.dist_km[i] >= x0 - 0.001 && D.dist_km[i] <= x1 + 0.001) idxs.push(i);
    }}
    if (idxs.length === 0) {{
      for (let i = 0; i < N; i++) idxs.push(i);
      isZoom = false;
    }}
    const i0 = idxs[0], i1 = idxs[idxs.length - 1];
    let distSum = 0, tSum = 0, pTime = 0, p4Time = 0, posSum = 0, negSum = 0, gapSpeedTime = 0;
    for (const i of idxs) {{
      const dt = D.dt_s[i];
      distSum += D.len_km[i];
      tSum += dt;
      pTime += D.power_w[i] * dt;
      p4Time += Math.pow(D.power_w[i], 4) * dt;
      posSum += D.pos_m[i];
      negSum += D.neg_m[i];
      if (isRun && D.gap_min[i] > 0) {{
        gapSpeedTime += (1000.0 / (D.gap_min[i] * 60.0)) * dt;
      }}
    }}
    const avgV = tSum > 0 ? (distSum / (tSum / 3600.0)) : 0;
    const avgP = tSum > 0 ? (pTime / tSum) : 0;
    const npP = tSum > 0 ? Math.pow(p4Time / tSum, 0.25) : avgP;
    const pctRef = D.ftp_or_rcp > 0 ? (npP / D.ftp_or_rcp) * 100.0 : 0;
    const ifVal = pctRef / 100.0;
    const avgG = distSum > 0.01 ? ((posSum - negSum) / (distSum * 1000.0)) * 100.0 : 0;

    if (isZoom) {{
      barEl.classList.add("zoomed");
      badgeEl.textContent = "🔍 ZOOM DEL PLAN DE PACING";
    }} else {{
      barEl.classList.remove("zoomed");
      badgeEl.textContent = "📊 PLAN COMPLETO";
    }}

    document.getElementById("pm_rng_{uid}").textContent = `Km ${{D.dist_km[i0].toFixed(1)}} → Km ${{D.dist_km[i1].toFixed(1)}}`;
    document.getElementById("pm_dist_{uid}").textContent = `${{distSum.toFixed(2)}} km`;
    document.getElementById("pm_time_{uid}").textContent = fmtHMS(tSum);
    document.getElementById("pm_el_{uid}").textContent = `+${{Math.round(posSum)}}m / −${{Math.round(negSum)}}m (${{avgG >= 0 ? '+' : ''}}${{avgG.toFixed(1)}}%)`;

    if (isRun) {{
      const avgPaceSec = distSum > 0.005 ? (tSum / distSum) : 0;
      const avgGapSec = gapSpeedTime > 0 ? (1000.0 / (gapSpeedTime / tSum)) : avgPaceSec;
      document.getElementById("pm_m3_{uid}").textContent = `${{fmtPace(avgPaceSec)}} /km (${{avgV.toFixed(1)}} km/h)`;
      document.getElementById("pm_m4_{uid}").textContent = `${{fmtPace(avgGapSec)}} /km`;
      document.getElementById("pm_m5_{uid}").textContent = `${{Math.round(avgP)}} W · ${{Math.round(pctRef)}}% rCP`;
    }} else {{
      document.getElementById("pm_m3_{uid}").textContent = `${{avgV.toFixed(1)}} km/h`;
      document.getElementById("pm_m4_{uid}").textContent = `${{Math.round(avgP)}} W (NP ${{Math.round(npP)}} W)`;
      document.getElementById("pm_m5_{uid}").textContent = `${{Math.round(pctRef)}}% FTP (IF ${{ifVal.toFixed(2)}})`;
    }}
  }}

  const cfg = {{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"] }};
  Plotly.newPlot(plotDiv, traces, layout, cfg).then(() => {{
    updatePacingSummary(0, D.total_dist_km + 1, false);

    plotDiv.on("plotly_relayout", function(ed) {{
      if (!ed) return;
      if (ed["xaxis.autorange"]) {{
        updatePacingSummary(0, D.total_dist_km + 1, false);
        return;
      }}
      let x0 = ed["xaxis.range[0]"];
      let x1 = ed["xaxis.range[1]"];
      if (Array.isArray(ed["xaxis.range"])) {{
        x0 = ed["xaxis.range"][0];
        x1 = ed["xaxis.range"][1];
      }}
      if (x0 !== undefined && x1 !== undefined) {{
        const lo = Math.min(x0, x1), hi = Math.max(x0, x1);
        const isZ = (hi - lo) < (D.total_dist_km * 0.985);
        updatePacingSummary(lo, hi, isZ);
      }}
    }});

    document.getElementById("preset_{uid}").addEventListener("click", function() {{
      Plotly.relayout(plotDiv, {{ "xaxis.autorange": true }});
      updatePacingSummary(0, D.total_dist_km + 1, false);
    }});
  }});
}})();
</script>
</body>
</html>"""

    components.html(html_doc, height=height, scrolling=False)
