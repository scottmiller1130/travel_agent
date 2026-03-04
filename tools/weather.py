"""
Weather tool — returns forecast for a destination and date range.
Uses mock data; swap in OpenWeatherMap when API key is set.
"""

import os
import random


CLIMATE_PROFILES = {
    # destination keyword → (avg_temp_c, conditions)
    "tropical": (28, ["Sunny", "Partly Cloudy", "Humid", "Afternoon Showers"]),
    "mediterranean": (22, ["Sunny", "Sunny", "Sunny", "Partly Cloudy", "Windy"]),
    "northern": (8, ["Cloudy", "Rain", "Overcast", "Partly Cloudy", "Foggy"]),
    "alpine": (5, ["Sunny", "Snow", "Cloudy", "Clear and Cold"]),
    "desert": (35, ["Sunny", "Sunny", "Hot and Dry", "Clear"]),
}

DESTINATION_PROFILES = {
    "lisbon": "mediterranean", "barcelona": "mediterranean", "rome": "mediterranean",
    "paris": "northern", "london": "northern", "amsterdam": "northern",
    "bali": "tropical", "cancun": "tropical", "bangkok": "tropical",
    "denver": "alpine", "zurich": "alpine", "innsbruck": "alpine",
    "dubai": "desert", "phoenix": "desert", "marrakech": "desert",
}


def get_weather(destination: str, start_date: str, end_date: str) -> dict:
    """Get weather forecast for a destination and date range."""
    if os.getenv("OPENWEATHER_API_KEY"):
        raise NotImplementedError("Real OpenWeatherMap integration not yet wired up")

    profile_key = None
    dest_lower = destination.lower()
    for city, profile in DESTINATION_PROFILES.items():
        if city in dest_lower:
            profile_key = profile
            break
    if not profile_key:
        profile_key = "mediterranean"  # default

    avg_temp, conditions = CLIMATE_PROFILES[profile_key]
    random.seed(f"{destination}{start_date}")

    try:
        from datetime import datetime, timedelta
        fmt = "%Y-%m-%d"
        start = datetime.strptime(start_date, fmt)
        end = datetime.strptime(end_date, fmt)
        days = max(1, (end - start).days + 1)
    except Exception:
        days = 5
        start = None

    daily = []
    for i in range(min(days, 14)):
        temp_c = avg_temp + random.randint(-5, 5)
        temp_f = round(temp_c * 9 / 5 + 32)
        condition = random.choice(conditions)
        date_str = (start + __import__("datetime").timedelta(days=i)).strftime("%Y-%m-%d") if start else f"Day {i+1}"
        daily.append({
            "date": date_str,
            "condition": condition,
            "temp_high_c": temp_c + 3,
            "temp_low_c": temp_c - 4,
            "temp_high_f": temp_f + 5,
            "temp_low_f": temp_f - 7,
            "precipitation_chance": random.randint(0, 40) if "rain" not in condition.lower() else random.randint(60, 90),
            "humidity_pct": random.randint(40, 85),
        })

    rain_days = sum(1 for d in daily if d["precipitation_chance"] > 50)
    avg_high = sum(d["temp_high_c"] for d in daily) // len(daily)

    return {
        "status": "success",
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
        "summary": f"Mostly {conditions[0].lower()} with average highs of {avg_high}°C. {rain_days} rainy day(s) expected.",
        "climate_type": profile_key,
        "daily_forecast": daily,
        "packing_suggestions": _packing_tips(profile_key, avg_high),
        "note": "Mock forecast — connect OpenWeatherMap API for live data",
    }


def _packing_tips(profile: str, avg_temp: int) -> list[str]:
    tips = {
        "tropical": ["Light breathable clothes", "Sunscreen SPF 50+", "Insect repellent", "Rain jacket", "Sandals"],
        "mediterranean": ["Light layers", "Sunscreen", "Comfortable walking shoes", "Sunglasses"],
        "northern": ["Waterproof jacket", "Warm layers", "Umbrella", "Waterproof shoes"],
        "alpine": ["Heavy coat", "Thermal underlayers", "Gloves and hat", "Snow boots"],
        "desert": ["Lightweight sun-protective clothing", "Sunscreen", "Lots of water", "Hat"],
    }
    return tips.get(profile, ["Comfortable clothes", "Walking shoes"])
