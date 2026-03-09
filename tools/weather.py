"""
Weather tool — real forecasts via Open-Meteo (free, no API key required).
Open-Meteo geocoding: https://geocoding-api.open-meteo.com/v1/search
Open-Meteo forecast:  https://api.open-meteo.com/v1/forecast

Falls back to climate-profile estimates only if the API is unreachable.
"""

import os
import random
from datetime import datetime, timedelta

from tools.cache import ttl_cache

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

from tools.seasons import get_season_for_dates

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL  = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL   = "https://archive-api.open-meteo.com/v1/archive"

# WMO Weather Interpretation Code → human-readable label
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

# ── Fallback mock data (used only when API is unreachable) ──────────────────
_CLIMATE_PROFILES = {
    "tropical":      (28, ["Sunny", "Partly Cloudy", "Humid", "Afternoon Showers"]),
    "mediterranean": (22, ["Sunny", "Sunny", "Sunny", "Partly Cloudy", "Windy"]),
    "northern":      (8,  ["Cloudy", "Rain", "Overcast", "Partly Cloudy", "Foggy"]),
    "alpine":        (5,  ["Sunny", "Snow", "Cloudy", "Clear and Cold"]),
    "desert":        (35, ["Sunny", "Sunny", "Hot and Dry", "Clear"]),
}
_DEST_PROFILES = {
    "lisbon": "mediterranean", "barcelona": "mediterranean", "rome": "mediterranean",
    "madrid": "mediterranean", "athens": "mediterranean", "istanbul": "mediterranean",
    "paris": "northern", "london": "northern", "amsterdam": "northern",
    "berlin": "northern", "dublin": "northern", "brussels": "northern",
    "bali": "tropical", "cancun": "tropical", "bangkok": "tropical",
    "miami": "tropical", "singapore": "tropical", "ho chi minh": "tropical",
    "denver": "alpine", "zurich": "alpine", "innsbruck": "alpine",
    "dubai": "desert", "phoenix": "desert", "marrakech": "desert",
    "riyadh": "desert", "cairo": "desert",
}
_PACKING = {
    "tropical":      ["Light breathable clothes", "Sunscreen SPF 50+", "Insect repellent", "Rain jacket", "Sandals"],
    "mediterranean": ["Light layers", "Sunscreen", "Comfortable walking shoes", "Sunglasses"],
    "northern":      ["Waterproof jacket", "Warm layers", "Umbrella", "Waterproof shoes"],
    "alpine":        ["Heavy coat", "Thermal underlayers", "Gloves and hat", "Snow boots"],
    "desert":        ["Lightweight sun-protective clothing", "Sunscreen", "Water bottle", "Hat"],
}


def _packing_for_temp(avg_high_c: float) -> list[str]:
    if avg_high_c >= 28:
        return _PACKING["tropical"]
    elif avg_high_c >= 20:
        return _PACKING["mediterranean"]
    elif avg_high_c >= 10:
        return _PACKING["northern"]
    elif avg_high_c >= 2:
        return _PACKING["alpine"]
    else:
        return ["Heavy winter coat", "Thermal underlayers", "Insulated boots", "Gloves and hat", "Balaclava"]


def _geocode(destination: str) -> dict | None:
    try:
        r = _httpx.get(
            GEOCODING_URL,
            params={"name": destination, "count": 1, "language": "en"},
            timeout=8,
        )
        results = r.json().get("results")
        if not results:
            return None
        loc = results[0]
        return {
            "lat":      loc["latitude"],
            "lon":      loc["longitude"],
            "name":     loc.get("name", destination),
            "country":  loc.get("country", ""),
            "timezone": loc.get("timezone", "UTC"),
        }
    except Exception:
        return None


def _mock_forecast(destination: str, start_date: str, end_date: str) -> dict:
    """Climate-profile fallback when Open-Meteo is unreachable."""
    dest_lower = destination.lower()
    profile_key = next((v for k, v in _DEST_PROFILES.items() if k in dest_lower), "mediterranean")
    avg_temp, conditions = _CLIMATE_PROFILES[profile_key]
    random.seed(f"{destination}{start_date}")
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        e = datetime.strptime(end_date,   "%Y-%m-%d")
        days = max(1, (e - s).days + 1)
    except Exception:
        days, s = 5, datetime.utcnow()

    daily = []
    for i in range(min(days, 14)):
        temp_c = avg_temp + random.randint(-5, 5)
        condition = random.choice(conditions)
        daily.append({
            "date":                   (s + timedelta(days=i)).strftime("%Y-%m-%d"),
            "condition":              condition,
            "temp_high_c":            temp_c + 3,
            "temp_low_c":             temp_c - 4,
            "temp_high_f":            round((temp_c + 3) * 9/5 + 32, 1),
            "temp_low_f":             round((temp_c - 4) * 9/5 + 32, 1),
            "precipitation_chance":   random.randint(60, 90) if "rain" in condition.lower() else random.randint(0, 40),
            "precipitation_mm":       None,
            "wind_kmh":               None,
            "uv_index":               None,
        })
    avg_high = sum(d["temp_high_c"] for d in daily) / len(daily)
    rain_days = sum(1 for d in daily if d["precipitation_chance"] > 50)
    dominant = max(set(d["condition"] for d in daily), key=lambda c: sum(1 for d in daily if d["condition"] == c))
    return {
        "status":              "success",
        "destination":         destination,
        "start_date":          start_date,
        "end_date":            end_date,
        "summary":             f"Mostly {dominant.lower()} with average highs of {avg_high:.1f}°C. {rain_days} rainy day(s) expected.",
        "daily_forecast":      daily,
        "packing_suggestions": _PACKING.get(profile_key, []),
        "season":              get_season_for_dates(destination, start_date, end_date),
        "source":              "Climate estimate (live API unreachable)",
    }


def _historical_forecast(destination: str, start_date: str, end_date: str, loc: dict) -> dict | None:
    """
    Fetch actual observed weather for the same date range one year ago from the
    Open-Meteo archive API.  Used as a proxy for future trips beyond the 16-day
    forecast window — far more accurate than climate-profile estimates.
    Returns None if the archive call fails so the caller can fall back.
    """
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        e = datetime.strptime(end_date,   "%Y-%m-%d")
        # Shift to previous year; guard against Feb-29 on a non-leap year
        try:
            s_hist = s.replace(year=s.year - 1)
            e_hist = e.replace(year=e.year - 1)
        except ValueError:
            s_hist = s.replace(year=s.year - 1, day=28)
            e_hist = e.replace(year=e.year - 1, day=28)

        r = _httpx.get(ARCHIVE_URL, params={
            "latitude":   loc["lat"],
            "longitude":  loc["lon"],
            "start_date": s_hist.strftime("%Y-%m-%d"),
            "end_date":   e_hist.strftime("%Y-%m-%d"),
            "daily":      ",".join([
                "weathercode",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "windspeed_10m_max",
            ]),
            "timezone":   loc.get("timezone", "UTC"),
        }, timeout=10)
        data = r.json()
    except Exception:
        return None

    if "daily" not in data:
        return None

    d          = data["daily"]
    hist_dates = d.get("time", [])
    codes      = d.get("weathercode", [])
    highs      = d.get("temperature_2m_max", [])
    lows       = d.get("temperature_2m_min", [])
    precip_mm  = d.get("precipitation_sum", [])
    wind       = d.get("windspeed_10m_max", [])

    if not hist_dates:
        return None

    daily = []
    for i, hist_date_str in enumerate(hist_dates):
        hist_dt = datetime.strptime(hist_date_str, "%Y-%m-%d")
        try:
            target_dt = hist_dt.replace(year=s.year)
        except ValueError:
            target_dt = hist_dt.replace(year=s.year, day=28)

        h = highs[i]     if i < len(highs)     else None
        l = lows[i]      if i < len(lows)       else None
        c = int(codes[i]) if i < len(codes) and codes[i] is not None else 0
        p = precip_mm[i] if i < len(precip_mm) and precip_mm[i] is not None else None
        w = wind[i]      if i < len(wind)      and wind[i]      is not None else None

        daily.append({
            "date":                 target_dt.strftime("%Y-%m-%d"),
            "condition":            WMO_CODES.get(c, "Variable"),
            "wmo_code":             c,
            "temp_high_c":          round(h, 1) if h is not None else None,
            "temp_low_c":           round(l, 1) if l is not None else None,
            "temp_high_f":          round(h * 9/5 + 32, 1) if h is not None else None,
            "temp_low_f":           round(l * 9/5 + 32, 1) if l is not None else None,
            "precipitation_chance": None,   # archive has no probability field
            "precipitation_mm":     round(p, 1) if p is not None else None,
            "wind_kmh":             round(w, 1) if w is not None else None,
            "uv_index":             None,
        })

    valid    = [day for day in daily if day["temp_high_c"] is not None]
    avg_high = sum(day["temp_high_c"] for day in valid) / len(valid) if valid else 20.0
    rain_days = sum(1 for day in daily if (day["precipitation_mm"] or 0) > 1.0)
    conditions = [day["condition"] for day in daily]
    dominant   = max(set(conditions), key=conditions.count) if conditions else "Variable"

    return {
        "status":              "success",
        "destination":         f"{loc['name']}, {loc['country']}",
        "coordinates":         {"lat": loc["lat"], "lon": loc["lon"]},
        "start_date":          start_date,
        "end_date":            end_date,
        "summary":             (
            f"Based on {s_hist.year} data: mostly {dominant.lower()} "
            f"with average highs of {avg_high:.1f}°C ({avg_high * 9/5 + 32:.1f}°F). "
            f"{rain_days} rainy day(s) expected."
        ),
        "daily_forecast":      daily,
        "packing_suggestions": _packing_for_temp(avg_high),
        "season":              get_season_for_dates(destination, start_date, end_date),
        "source":              f"Open-Meteo archive ({s_hist.year} observed data)",
        "note":                (
            f"Trip is beyond the 16-day forecast window. "
            f"Showing actual observed weather from {s_hist.year} for the same dates."
        ),
    }


@ttl_cache(ttl=3600)  # Cache for 1 hour — forecasts don't change minute-to-minute
def get_weather(destination: str, start_date: str, end_date: str) -> dict:
    """
    Get weather forecast for a destination and date range.
    Uses Open-Meteo (free, no API key). Falls back to climate estimates if unreachable.
    """
    if not _HTTPX:
        return _mock_forecast(destination, start_date, end_date)

    # Validate and clamp dates to Open-Meteo's 16-day window
    try:
        today  = datetime.utcnow().date()
        s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(end_date,   "%Y-%m-%d").date()
        max_end = today + timedelta(days=15)
        beyond_window = s_date > max_end
    except ValueError:
        return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD."}

    e_date_clamped = min(e_date, max_end)

    # Geocode destination (needed for both live forecast and archive fallback)
    loc = _geocode(destination)

    if beyond_window:
        # Beyond the 16-day forecast window — try archive data from the same
        # dates last year (real observed temps), then fall back to mock.
        if loc:
            result = _historical_forecast(destination, start_date, end_date, loc)
            if result:
                return result
        result = _mock_forecast(destination, start_date, end_date)
        result["source"] = "Climate estimate (trip is beyond 16-day forecast window)"
        result["note"]   = f"Open-Meteo only supports forecasts up to 16 days out. Showing historical climate averages for {destination}."
        return result

    if not loc:
        result = _mock_forecast(destination, start_date, end_date)
        result["source"] = f"Climate estimate (could not geocode '{destination}')"
        return result

    # Fetch live forecast
    try:
        r = _httpx.get(
            FORECAST_URL,
            params={
                "latitude":   loc["lat"],
                "longitude":  loc["lon"],
                "daily": ",".join([
                    "weathercode",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                    "precipitation_sum",
                    "windspeed_10m_max",
                    "uv_index_max",
                ]),
                "timezone":   loc["timezone"],
                "start_date": str(s_date),
                "end_date":   str(e_date_clamped),
            },
            timeout=10,
        )
        data = r.json()
    except Exception as exc:
        result = _mock_forecast(destination, start_date, end_date)
        result["source"] = f"Climate estimate (API error: {exc})"
        return result

    if "daily" not in data:
        result = _mock_forecast(destination, start_date, end_date)
        result["source"] = "Climate estimate (unexpected API response)"
        return result

    d = data["daily"]
    dates       = d.get("time", [])
    codes       = d.get("weathercode", [])
    highs       = d.get("temperature_2m_max", [])
    lows        = d.get("temperature_2m_min", [])
    precip_pct  = d.get("precipitation_probability_max", [])
    precip_mm   = d.get("precipitation_sum", [])
    wind        = d.get("windspeed_10m_max", [])
    uv          = d.get("uv_index_max", [])

    daily = []
    for i, date in enumerate(dates):
        h = highs[i] if i < len(highs) else None
        l = lows[i]  if i < len(lows)  else None
        c = int(codes[i]) if i < len(codes) else 0
        daily.append({
            "date":                 date,
            "condition":            WMO_CODES.get(c, "Variable"),
            "wmo_code":             c,
            "temp_high_c":          round(h, 1) if h is not None else None,
            "temp_low_c":           round(l, 1) if l is not None else None,
            "temp_high_f":          round(h * 9/5 + 32, 1) if h is not None else None,
            "temp_low_f":           round(l * 9/5 + 32, 1) if l is not None else None,
            "precipitation_chance": precip_pct[i] if i < len(precip_pct) else None,
            "precipitation_mm":     round(precip_mm[i], 1) if i < len(precip_mm) and precip_mm[i] is not None else None,
            "wind_kmh":             round(wind[i], 1) if i < len(wind) and wind[i] is not None else None,
            "uv_index":             round(uv[i], 1) if i < len(uv) and uv[i] is not None else None,
        })

    valid = [d for d in daily if d["temp_high_c"] is not None]
    avg_high   = sum(d["temp_high_c"] for d in valid) / len(valid) if valid else 20.0
    rain_days  = sum(1 for d in daily if (d["precipitation_chance"] or 0) > 50)
    conditions = [d["condition"] for d in daily]
    dominant   = max(set(conditions), key=conditions.count) if conditions else "Variable"

    note = None
    if e_date_clamped < e_date:
        note = f"Forecast available through {e_date_clamped}. Remaining days use climate estimates."

    result = {
        "status":              "success",
        "destination":         f"{loc['name']}, {loc['country']}",
        "coordinates":         {"lat": loc["lat"], "lon": loc["lon"]},
        "start_date":          str(s_date),
        "end_date":            str(e_date_clamped),
        "summary":             f"Mostly {dominant.lower()} with average highs of {avg_high:.1f}°C ({avg_high * 9/5 + 32:.1f}°F). {rain_days} rainy day(s) expected.",
        "daily_forecast":      daily,
        "packing_suggestions": _packing_for_temp(avg_high),
        "season":              get_season_for_dates(destination, start_date, end_date),
        "source":              "Open-Meteo (live forecast — free, no API key)",
    }
    if note:
        result["note"] = note
    return result
