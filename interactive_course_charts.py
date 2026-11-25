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


def _downsample_route_for_synced_widget(route_data: dict, max_pts: int = 340) -> dict:
    """Reduce segmentos manteniendo sumas EXACTAS de distancia, +D y -D por punto."""
    segs = route_data.get("segments") or []
    if not segs:
        return {}
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

    return {
        "route_name": str(route_data.get("route_name", "Circuito")),
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
    }


def render_synced_elevation_planimetry_widget(route_data: dict, height: int = 470):
    """
    Renderiza el Perfil Altimétrico y la Planimetría GPS 2D sincronizados en tiempo real:
      - Hover sincronizado bidireccional (altimetría <-> mapa GPS).
      - Zoom interactivo con cálculo en vivo de distancia, +D, -D, pendiente y resalte en el mapa.
    """
    payload = _downsample_route_for_synced_widget(route_data)
    if not payload or not payload.get("dist_km"):
        return

    uid = "sync_" + uuid.uuid4().hex[:8]
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

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
    border: 1.5px solid rgba(0,238,255,0.32);
    border-radius: 10px;
    padding: 8px 12px;
    margin-bottom: 8px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }}
  .bar.zoomed {{
    border-color: #FFD700;
    box-shadow: 0 0 16px rgba(255, 215, 0, 0.25);
  }}
  .badge {{
    font-size: 0.72rem;
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
    font-size: 0.78rem;
  }}
  .m {{ display: flex; flex-direction: column; line-height: 1.2; }}
  .m .lbl {{ font-size: 0.62rem; color: #8E9BAE; font-weight: 700; text-transform: uppercase; }}
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
  .btn-reset:hover {{ background: rgba(0, 238, 255, 0.2); border-color: #00EEFF; color: #FFFFFF; }}
  .grid {{
    display: grid;
    grid-template-columns: 1.48fr 1fr;
    gap: 10px;
    align-items: stretch;
  }}
  @media (max-width: 760px) {{
    .grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-box {{
    background: #0E121C;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    overflow: hidden;
    position: relative;
  }}
  .hint {{
    font-size: 0.7rem;
    color: #8E9BAE;
    margin-top: 5px;
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
  <div class="grid">
    <div class="chart-box"><div id="elev_{uid}" style="width:100%; height:355px;"></div></div>
    <div class="chart-box"><div id="map_{uid}" style="width:100%; height:355px;"></div></div>
  </div>
  <div class="hint">
    <span>💡 <b>Cursor sincronizado:</b> desliza sobre el perfil altimétrico para ver tu posición exacta en el mapa GPS (y viceversa).</span>
    <span>🔍 <b>Zoom interactivo:</b> arrastra horizontalmente sobre la altimetría para medir cualquier subida o bajada (+D / −D). Doble clic para reiniciar.</span>
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

  const hoverTexts = D.dist_km.map((km, i) =>
    `<b>Km ${{km.toFixed(2)}}</b><br>Altitud: <b>${{D.ele_m[i].toFixed(0)}} m</b><br>Pendiente: <b>${{D.grade_pct[i] >= 0 ? '+' : ''}}${{D.grade_pct[i].toFixed(1)}}%</b>`
  );

  // ── 1. PERFIL ALTIMÉTRICO (IZQUIERDA) ──
  const elevMin = Math.min(...D.ele_m);
  const elevMax = Math.max(...D.ele_m);
  const elevPad = Math.max(15, (elevMax - elevMin) * 0.12);

  const elevTraces = [
    {{
      x: D.dist_km,
      y: D.ele_m,
      type: "scatter",
      mode: "lines",
      fill: "tozeroy",
      fillcolor: "rgba(100, 116, 139, 0.22)",
      line: {{ color: "rgba(148,163,184,0.55)", width: 1.5 }},
      hoverinfo: "skip",
      showlegend: false
    }},
    {{
      x: D.dist_km,
      y: D.ele_m,
      type: "scatter",
      mode: "markers",
      marker: {{ size: 6, color: D.colors, line: {{ width: 0 }} }},
      text: hoverTexts,
      hovertemplate: "%{{text}}<extra></extra>",
      showlegend: false
    }},
    // Trace 2: Live synchronized cursor dot on elevation
    {{
      x: [null],
      y: [null],
      type: "scatter",
      mode: "markers",
      marker: {{ size: 13, color: "#00EEFF", line: {{ color: "#FFFFFF", width: 2.5 }} }},
      hoverinfo: "skip",
      showlegend: false
    }}
  ];

  const elevLayout = {{
    title: {{ text: "⛰️ Altimetría — Arrastra para hacer Zoom (+D / −D)", font: {{ color: "#FFFFFF", size: 12.5 }}, x: 0.02 }},
    paper_bgcolor: "#0E121C",
    plot_bgcolor: "#0E121C",
    font: {{ color: "#CBD5E1", size: 10.5 }},
    margin: {{ l: 48, r: 14, t: 36, b: 38 }},
    xaxis: {{ title: "Distancia (km)", gridcolor: "rgba(255,255,255,0.06)", zeroline: false }},
    yaxis: {{ title: "Altitud (m)", range: [Math.max(0, elevMin - elevPad), elevMax + elevPad], gridcolor: "rgba(255,255,255,0.06)", zeroline: false }},
    dragmode: "zoom",
    hovermode: "closest"
  }};

  // ── 2. PLANIMETRÍA GPS 2D (DERECHA) ──
  const mapTraces = [
    // Trace 0: Base route colored by gradient heatmap
    {{
      x: D.lon,
      y: D.lat,
      type: "scatter",
      mode: "lines+markers",
      line: {{ color: "rgba(148,163,184,0.35)", width: 2.5 }},
      marker: {{ size: 5.5, color: D.colors, opacity: 0.92 }},
      text: hoverTexts,
      hovertemplate: "%{{text}}<extra></extra>",
      showlegend: false
    }},
    // Trace 1: Highlighted zoomed segment on GPS map
    {{
      x: [],
      y: [],
      type: "scatter",
      mode: "lines+markers",
      line: {{ color: "#FFD700", width: 5 }},
      marker: {{ size: 6.5, color: "#FFD700" }},
      hoverinfo: "skip",
      showlegend: false
    }},
    // Trace 2: Start & Finish pins
    {{
      x: [D.lon[0], D.lon[N - 1]],
      y: [D.lat[0], D.lat[N - 1]],
      type: "scatter",
      mode: "markers+text",
      marker: {{ size: [11, 11], color: ["#00FF88", "#FF5252"], line: {{ color: "#000000", width: 1.5 }} }},
      text: ["🟢 Salida", "🏁 Meta"],
      textposition: ["top right", "bottom left"],
      textfont: {{ color: "#FFFFFF", size: 10 }},
      hoverinfo: "skip",
      showlegend: false
    }},
    // Trace 3: Live synchronized moving dot on GPS map
    {{
      x: [null],
      y: [null],
      type: "scatter",
      mode: "markers+text",
      marker: {{ size: 15, color: "#00EEFF", line: {{ color: "#FFFFFF", width: 3 }} }},
      text: [""],
      textposition: "top center",
      textfont: {{ color: "#00EEFF", size: 11 }},
      hoverinfo: "skip",
      showlegend: false
    }}
  ];

  const mapLayout = {{
    title: {{ text: "🧭 Planimetría GPS (Sincronizada)", font: {{ color: "#FFFFFF", size: 12.5 }}, x: 0.03 }},
    paper_bgcolor: "#0E121C",
    plot_bgcolor: "#0B101A",
    font: {{ color: "#CBD5E1", size: 10.5 }},
    margin: {{ l: 10, r: 10, t: 36, b: 12 }},
    xaxis: {{ showgrid: false, zeroline: false, showticklabels: false, title: "" }},
    yaxis: {{ showgrid: false, zeroline: false, showticklabels: false, scaleanchor: "x", scaleratio: 1, title: "" }},
    dragmode: "zoom",
    hovermode: "closest"
  }};

  const cfg = {{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "autoScale2d", "toggleSpikelines"] }};

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
    document.getElementById("m_grd_{uid}").textContent = `${{avgG >= 0 ? '+' : ''}}${{avgG.toFixed(1)}}% / +${{Math.max(0, maxG).toFixed(1)}}%`;
    document.getElementById("m_ele_{uid}").textContent = `${{Math.round(eMin)}}–${{Math.round(eMax)}} m`;

    // Resaltar el tramo en el mapa GPS
    if (isZoom) {{
      const hLon = idxs.map(i => D.lon[i]);
      const hLat = idxs.map(i => D.lat[i]);
      Plotly.restyle(mapDiv, {{ "marker.opacity": 0.25, "line.color": "rgba(148,163,184,0.18)" }}, [0]);
      Plotly.restyle(mapDiv, {{ x: [hLon], y: [hLat] }}, [1]);
    }} else {{
      Plotly.restyle(mapDiv, {{ "marker.opacity": 0.92, "line.color": "rgba(148,163,184,0.35)" }}, [0]);
      Plotly.restyle(mapDiv, {{ x: [[]], y: [[]] }}, [1]);
    }}
  }}

  Plotly.newPlot(elevDiv, elevTraces, elevLayout, cfg).then(() => {{
    return Plotly.newPlot(mapDiv, mapTraces, mapLayout, cfg);
  }}).then(() => {{
    updateSummary(0, D.total_dist_km + 1, false);

    // Hover en Altimetría -> mover marcador en Mapa GPS
    elevDiv.on("plotly_hover", function(ev) {{
      if (!ev || !ev.points || !ev.points.length) return;
      const pt = ev.points[0];
      const idx = pt.pointIndex;
      if (idx === undefined || idx < 0 || idx >= N) return;
      const tag = `Km ${{D.dist_km[idx].toFixed(1)}} · ${{Math.round(D.ele_m[idx])}}m`;
      Plotly.restyle(mapDiv, {{ x: [[D.lon[idx]]], y: [[D.lat[idx]]], text: [[tag]] }}, [3]);
      Plotly.restyle(elevDiv, {{ x: [[D.dist_km[idx]]], y: [[D.ele_m[idx]]] }}, [2]);
    }});
    elevDiv.on("plotly_unhover", function() {{
      Plotly.restyle(mapDiv, {{ x: [[null]], y: [[null]], text: [[""]] }}, [3]);
      Plotly.restyle(elevDiv, {{ x: [[null]], y: [[null]] }}, [2]);
    }});

    // Hover en Mapa GPS -> mover marcador en Altimetría
    mapDiv.on("plotly_hover", function(ev) {{
      if (!ev || !ev.points || !ev.points.length) return;
      const pt = ev.points[0];
      if (pt.curveNumber !== 0) return;
      const idx = pt.pointIndex;
      if (idx === undefined || idx < 0 || idx >= N) return;
      const tag = `Km ${{D.dist_km[idx].toFixed(1)}} · ${{Math.round(D.ele_m[idx])}}m`;
      Plotly.restyle(elevDiv, {{ x: [[D.dist_km[idx]]], y: [[D.ele_m[idx]]] }}, [2]);
      Plotly.restyle(mapDiv, {{ x: [[D.lon[idx]]], y: [[D.lat[idx]]], text: [[tag]] }}, [3]);
    }});
    mapDiv.on("plotly_unhover", function() {{
      Plotly.restyle(elevDiv, {{ x: [[null]], y: [[null]] }}, [2]);
      Plotly.restyle(mapDiv, {{ x: [[null]], y: [[null]], text: [[""]] }}, [3]);
    }});

    // Zoom en Altimetría -> recalcular distancia, +D, -D y resaltar en Mapa GPS
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

    // Zoom en Mapa GPS -> encontrar rango de puntos visibles y actualizar resumen
    mapDiv.on("plotly_relayout", function(ed) {{
      if (!ed) return;
      if (ed["xaxis.autorange"] || ed["yaxis.autorange"]) {{
        updateSummary(0, D.total_dist_km + 1, false);
        return;
      }}
      const x0 = ed["xaxis.range[0]"], x1 = ed["xaxis.range[1]"];
      const y0 = ed["yaxis.range[0]"], y1 = ed["yaxis.range[1]"];
      if (x0 !== undefined && x1 !== undefined && y0 !== undefined && y1 !== undefined) {{
        const loX = Math.min(x0, x1), hiX = Math.max(x0, x1);
        const loY = Math.min(y0, y1), hiY = Math.max(y0, y1);
        let kMin = 1e9, kMax = -1e9, cnt = 0;
        for (let i = 0; i < N; i++) {{
          if (D.lon[i] >= loX && D.lon[i] <= hiX && D.lat[i] >= loY && D.lat[i] <= hiY) {{
            if (D.dist_km[i] < kMin) kMin = D.dist_km[i];
            if (D.dist_km[i] > kMax) kMax = D.dist_km[i];
            cnt++;
          }}
        }}
        if (cnt >= 2 && (kMax - kMin) < D.total_dist_km * 0.98) {{
          updateSummary(kMin, kMax, true);
          Plotly.relayout(elevDiv, {{ "xaxis.range": [kMin, kMax] }});
        }}
      }}
    }});

    document.getElementById("reset_{uid}").addEventListener("click", function() {{
      Plotly.relayout(elevDiv, {{ "xaxis.autorange": true, "yaxis.range": [Math.max(0, elevMin - elevPad), elevMax + elevPad] }});
      Plotly.relayout(mapDiv, {{ "xaxis.autorange": true, "yaxis.autorange": true }});
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
    traces.push({{
      x: D.dist_km, y: D.ele_m, name: "⛰️ Altimetría (m)",
      type: "scatter", mode: "lines", fill: "tozeroy",
      fillcolor: "rgba(100,116,139,0.28)", line: {{ color: "#94A3B8", width: 1.6 }},
      hovertemplate: "Altitud: <b>%{{y:.0f}} m</b><extra></extra>",
      xaxis: "x", yaxis: "y3"
    }});
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
      yaxis3: {{ title: "Altitud (m)", domain: [0.0, 0.28], gridcolor: "rgba(255,255,255,0.05)", color: "#94A3B8" }}
    }};
  }} else {{
    // CICLISMO: Potencia (Amarillo #FFD700) + Velocidad (Verde #00FF88) + Altimetría debajo
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
    traces.push({{
      x: D.dist_km, y: D.ele_m, name: "⛰️ Altimetría (m)",
      type: "scatter", mode: "lines", fill: "tozeroy",
      fillcolor: "rgba(100,116,139,0.28)", line: {{ color: "#94A3B8", width: 1.6 }},
      hovertemplate: "Altitud: <b>%{{y:.0f}} m</b><extra></extra>",
      xaxis: "x", yaxis: "y3"
    }});

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
      yaxis3: {{ title: "Altitud (m)", domain: [0.0, 0.28], gridcolor: "rgba(255,255,255,0.05)", color: "#94A3B8" }}
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
