"""
weather_service.py — Servicio de clima real e histórico para AlphaX Race Split.

Utiliza Open-Meteo (modelos meteorológicos ECMWF IFS / NOAA GFS para pronósticos hasta 16 días,
y reanálisis histórico ERA5 para fechas pasadas o competencias a más de 16 días vista) sin
requerir API Key adicional.

Características:
  - Pronóstico horario real hasta +16 días (temperatura, humedad, velocidad y dirección del viento,
    ráfagas, probabilidad de lluvia, presión barométrica y condición WMO).
  - Promedio climatológico inteligente (últimos 3 años en la misma fecha y hora exacta) cuando la
    carrera es a más de 16 días en el futuro — igual que Best Bike Split.
  - Promedio vectorial circular para dirección del viento (evita el error clásico de promediar 355° y 5°).
  - Perfil horario de hasta 8 horas desde la salida para simulación dinámica hora a hora.
"""

import math
from datetime import date, datetime, timedelta
import requests


WMO_WEATHER_CODES = {
    0: "☀️ Despejado",
    1: "🌤️ Mayormente despejado",
    2: "⛅ Parcialmente nublado",
    3: "☁️ Nublado",
    45: "🌫️ Niebla",
    48: "🌫️ Niebla densa",
    51: "🌦️ Llovizna ligera",
    53: "🌦️ Llovizna moderada",
    55: "🌧️ Llovizna intensa",
    61: "🌦️ Lluvia ligera",
    63: "🌧️ Lluvia moderada",
    65: "🌧️ Lluvia fuerte",
    80: "🌦️ Chubascos ligeros",
    81: "🌧️ Chubascos moderados",
    82: "⛈️ Chubascos fuertes",
    95: "⛈️ Tormenta eléctrica",
    96: "⛈️ Tormenta con granizo",
    99: "⛈️ Tormenta severa",
}


def deg_to_compass(deg: float) -> str:
    """Convierte grados (0-360°, dirección DESDE donde sopla el viento) a brújula en español."""
    if deg is None:
        return "N"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    idx = int(round((float(deg) % 360.0) / 22.5)) % 16
    return dirs[idx]


def circular_mean_deg(angles_deg: list, weights: list = None) -> float:
    """Calcula el promedio circular (vectorial) de ángulos en grados [0, 360)."""
    if not angles_deg:
        return 0.0
    sx = 0.0
    sy = 0.0
    for idx, a in enumerate(angles_deg):
        if a is None:
            continue
        w = float(weights[idx]) if (weights and idx < len(weights) and weights[idx] is not None) else 1.0
        rad = math.radians(float(a))
        sx += w * math.cos(rad)
        sy += w * math.sin(rad)
    if abs(sx) < 1e-9 and abs(sy) < 1e-9:
        return float(angles_deg[0]) % 360.0
    return (math.degrees(math.atan2(sy, sx)) + 360.0) % 360.0


def _decode_wmo(code) -> str:
    if code is None:
        return "⛅ Condición variable"
    try:
        return WMO_WEATHER_CODES.get(int(code), "⛅ Condición variable")
    except Exception:
        return "⛅ Condición variable"


def _extract_hourly_window(hourly_dict: dict, start_hour: int, duration_hours: int = 8) -> list:
    """Extrae una ventana de `duration_hours` horas a partir de `start_hour` (0-23) del JSON de Open-Meteo."""
    times = hourly_dict.get("time") or []
    temps = hourly_dict.get("temperature_2m") or []
    hums = hourly_dict.get("relative_humidity_2m") or []
    wspds = hourly_dict.get("wind_speed_10m") or []
    wdirs = hourly_dict.get("wind_direction_10m") or []
    wgsts = hourly_dict.get("wind_gusts_10m") or []
    press = hourly_dict.get("surface_pressure") or []
    probs = hourly_dict.get("precipitation_probability") or []
    prcps = hourly_dict.get("precipitation") or []
    codes = hourly_dict.get("weather_code") or []

    n = len(times)
    if n == 0:
        return []

    start_idx = min(max(0, int(start_hour)), n - 1)
    end_idx = min(n, start_idx + max(1, int(duration_hours)))

    window = []
    for offset, i in enumerate(range(start_idx, end_idx)):
        t_str = str(times[i])
        hhmm = t_str.split("T")[1][:5] if "T" in t_str else f"{(start_hour + offset) % 24:02d}:00"
        t_val = temps[i] if i < len(temps) and temps[i] is not None else 22.0
        h_val = hums[i] if i < len(hums) and hums[i] is not None else 60.0
        ws_val = wspds[i] if i < len(wspds) and wspds[i] is not None else 10.0
        wd_val = wdirs[i] if i < len(wdirs) and wdirs[i] is not None else 0.0
        wg_val = wgsts[i] if i < len(wgsts) and wgsts[i] is not None else ws_val
        p_val = press[i] if i < len(press) and press[i] is not None else None
        pp_val = probs[i] if i < len(probs) and probs[i] is not None else None
        pr_val = prcps[i] if i < len(prcps) and prcps[i] is not None else 0.0
        wc_val = codes[i] if i < len(codes) else None

        window.append({
            "hour_offset": offset,
            "time_label": hhmm,
            "temp_c": round(float(t_val), 1),
            "humidity_pct": round(float(h_val), 0),
            "wind_kmh": round(float(ws_val), 1),
            "wind_gusts_kmh": round(float(wg_val), 1),
            "wind_deg": round(float(wd_val) % 360.0, 0),
            "wind_compass": deg_to_compass(wd_val),
            "pressure_hpa": round(float(p_val), 1) if p_val is not None else None,
            "precip_prob_pct": int(round(float(pp_val))) if pp_val is not None else None,
            "precip_mm": round(float(pr_val), 1),
            "weather_code": wc_val,
            "condition": _decode_wmo(wc_val),
        })
    return window


def _fetch_open_meteo_forecast(lat: float, lon: float, target_date: date, start_hour: int, duration_hours: int) -> dict:
    """Consulta el pronóstico horario de Open-Meteo (ECMWF/GFS) para fechas dentro de los próximos 16 días."""
    end_date = target_date + timedelta(days=1) if (start_hour + duration_hours) > 23 else target_date
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,precipitation_probability,precipitation,weather_code",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
        "start_date": target_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }
    resp = requests.get(url, params=params, timeout=14)
    resp.raise_for_status()
    data = resp.json()
    window = _extract_hourly_window(data.get("hourly") or {}, start_hour, duration_hours)
    if not window:
        raise ValueError("El proveedor de pronóstico no devolvió datos horarios para esa fecha.")
    return {
        "timezone": data.get("timezone", "auto"),
        "elevation_api_m": data.get("elevation"),
        "hourly": window,
    }


def _fetch_open_meteo_archive_day(lat: float, lon: float, hist_date: date, start_hour: int, duration_hours: int) -> dict:
    """Consulta el archivo histórico ERA5 de Open-Meteo para una fecha pasada concreta."""
    end_date = hist_date + timedelta(days=1) if (start_hour + duration_hours) > 23 else hist_date
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,precipitation,weather_code",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
        "start_date": hist_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }
    resp = requests.get(url, params=params, timeout=14)
    resp.raise_for_status()
    data = resp.json()
    window = _extract_hourly_window(data.get("hourly") or {}, start_hour, duration_hours)
    if not window:
        raise ValueError(f"Sin datos históricos ERA5 para {hist_date}.")
    return {
        "timezone": data.get("timezone", "auto"),
        "elevation_api_m": data.get("elevation"),
        "hourly": window,
    }


def _safe_replace_year(d: date, new_year: int) -> date:
    """Maneja 29 de febrero de forma segura al retroceder años."""
    try:
        return d.replace(year=new_year)
    except ValueError:
        return d.replace(year=new_year, day=28)


def fetch_race_weather(
    lat: float,
    lon: float,
    race_date: date,
    start_hour: int = 7,
    duration_hours: int = 7,
) -> dict:
    """
    Obtiene el estado del tiempo más preciso posible para una competencia en (lat, lon)
    en `race_date` comenzando a las `start_hour`:00:
      - Hoy hasta +15 días: Pronóstico horario real (Open-Meteo ECMWF/GFS).
      - Fecha pasada (> 2 días atrás): Clima histórico real ERA5 de ese día.
      - Fecha futura (> +15 días): Climatología ERA5 promediada de los últimos 3 años
        en esa misma fecha y hora (igual que Best Bike Split para carreras futuras).
    """
    today = date.today()
    days_ahead = (race_date - today).days
    dur = max(2, min(12, int(duration_hours)))
    sh = max(0, min(23, int(start_hour)))

    try:
        if -1 <= days_ahead <= 15:
            res = _fetch_open_meteo_forecast(lat, lon, race_date, sh, dur)
            source_type = "forecast"
            source_label = "📡 Pronóstico Meteorológico Real (Open-Meteo ECMWF / GFS)"
            years_used = [race_date.year]
            hourly = res["hourly"]
            tz = res["timezone"]
        elif days_ahead < -1:
            # Fecha pasada: buscar clima real del archivo ERA5 (si fue hace 2-5 días, ERA5 puede tener lag de 5 días -> fallback a forecast/past_days)
            try:
                res = _fetch_open_meteo_archive_day(lat, lon, race_date, sh, dur)
                source_type = "historical_exact"
                source_label = f"📚 Clima Histórico Real del Día ({race_date.strftime('%d/%m/%Y')} · ERA5)"
                years_used = [race_date.year]
                hourly = res["hourly"]
                tz = res["timezone"]
            except Exception:
                res = _fetch_open_meteo_forecast(lat, lon, race_date, sh, dur)
                source_type = "historical_exact"
                source_label = f"📚 Clima Reciente Registrado ({race_date.strftime('%d/%m/%Y')})"
                years_used = [race_date.year]
                hourly = res["hourly"]
                tz = res["timezone"]
        else:
            # Carrera futura (>15 días): Promedio histórico ERA5 de los últimos 3 años en esa fecha calendario
            ref_year = today.year if _safe_replace_year(race_date, today.year) < (today - timedelta(days=7)) else (today.year - 1)
            candidate_years = [ref_year, ref_year - 1, ref_year - 2]
            yearly_windows = []
            tz = "auto"
            used_years = []
            for yr in candidate_years:
                try:
                    h_dt = _safe_replace_year(race_date, yr)
                    r_yr = _fetch_open_meteo_archive_day(lat, lon, h_dt, sh, dur)
                    if r_yr["hourly"]:
                        yearly_windows.append(r_yr["hourly"])
                        used_years.append(yr)
                        tz = r_yr["timezone"]
                except Exception:
                    continue

            if not yearly_windows:
                raise RuntimeError("No se pudieron recuperar datos climatológicos históricos para esas coordenadas.")

            min_len = min(len(w) for w in yearly_windows)
            hourly = []
            for idx in range(min_len):
                pts = [w[idx] for w in yearly_windows]
                avg_t = sum(p["temp_c"] for p in pts) / len(pts)
                avg_h = sum(p["humidity_pct"] for p in pts) / len(pts)
                avg_ws = sum(p["wind_kmh"] for p in pts) / len(pts)
                avg_wg = sum(p["wind_gusts_kmh"] for p in pts) / len(pts)
                avg_wd = circular_mean_deg([p["wind_deg"] for p in pts], [max(0.5, p["wind_kmh"]) for p in pts])
                p_vals = [p["pressure_hpa"] for p in pts if p["pressure_hpa"] is not None]
                avg_p = round(sum(p_vals) / len(p_vals), 1) if p_vals else None
                rainy_cnt = sum(1 for p in pts if (p.get("precip_mm") or 0.0) >= 0.3)
                rain_prob = int(round(100.0 * rainy_cnt / len(pts)))
                cond = "🌧️ Posibilidad de lluvia" if rain_prob >= 50 else ("⛅ Parcialmente nublado (histórico)" if rain_prob >= 25 else "☀️ Mayormente seco (histórico)")

                hourly.append({
                    "hour_offset": idx,
                    "time_label": pts[0]["time_label"],
                    "temp_c": round(avg_t, 1),
                    "humidity_pct": round(avg_h, 0),
                    "wind_kmh": round(avg_ws, 1),
                    "wind_gusts_kmh": round(avg_wg, 1),
                    "wind_deg": round(avg_wd % 360.0, 0),
                    "wind_compass": deg_to_compass(avg_wd),
                    "pressure_hpa": avg_p,
                    "precip_prob_pct": rain_prob,
                    "precip_mm": round(sum(p.get("precip_mm", 0.0) for p in pts) / len(pts), 1),
                    "weather_code": pts[0].get("weather_code"),
                    "condition": cond,
                })

            source_type = "climatology_3yr"
            yrs_txt = ", ".join(str(y) for y in used_years)
            source_label = f"📚 Climatología Histórica ERA5 ({yrs_txt} para {race_date.strftime('%d/%m')})"
            years_used = used_years

        # Resumen promedio de las primeras 3 horas de carrera (o ventana disponible)
        ref_slice = hourly[: min(3, len(hourly))]
        summary = {
            "temp_c": round(sum(p["temp_c"] for p in ref_slice) / len(ref_slice), 1),
            "temp_min_c": min(p["temp_c"] for p in hourly),
            "temp_max_c": max(p["temp_c"] for p in hourly),
            "humidity_pct": int(round(sum(p["humidity_pct"] for p in ref_slice) / len(ref_slice))),
            "wind_kmh": round(sum(p["wind_kmh"] for p in ref_slice) / len(ref_slice), 1),
            "wind_gusts_kmh": round(max(p["wind_gusts_kmh"] for p in ref_slice), 1),
            "wind_deg": round(circular_mean_deg([p["wind_deg"] for p in ref_slice], [max(0.5, p["wind_kmh"]) for p in ref_slice]), 0),
            "pressure_hpa": ref_slice[0].get("pressure_hpa"),
            "precip_prob_pct": max((p["precip_prob_pct"] for p in ref_slice if p.get("precip_prob_pct") is not None), default=None),
            "condition": ref_slice[0]["condition"],
        }
        summary["wind_compass"] = deg_to_compass(summary["wind_deg"])

        return {
            "ok": True,
            "error": None,
            "source_type": source_type,
            "source_label": source_label,
            "years_used": years_used,
            "race_date": race_date.strftime("%Y-%m-%d"),
            "start_hour": sh,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "timezone": tz,
            "summary": summary,
            "hourly": hourly,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "source_type": "error",
            "source_label": "Sin conexión al servicio de clima",
            "hourly": [],
            "summary": None,
        }
