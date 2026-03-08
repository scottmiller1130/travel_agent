"""
Ground transportation tool — car rental, intercity trains, and buses.

Uses estimated pricing based on route distance and duration.
No external API required; prices reflect realistic market rates.
"""

import math
import random
from datetime import datetime, timedelta

from tools.flights import _find_airport, _haversine

# ── Car rental companies by region ────────────────────────────────────────────
_CAR_COMPANIES = ["Hertz", "Avis", "Budget", "Enterprise", "Europcar", "Sixt", "Thrifty"]

_CAR_CATEGORIES = {
    "economy":     {"name": "Economy",      "example": "Toyota Yaris or similar",    "rate_usd": 35},
    "compact":     {"name": "Compact",      "example": "VW Golf or similar",         "rate_usd": 45},
    "midsize":     {"name": "Midsize",      "example": "Toyota Camry or similar",    "rate_usd": 60},
    "suv":         {"name": "SUV",          "example": "Ford Explorer or similar",   "rate_usd": 90},
    "luxury":      {"name": "Luxury",       "example": "BMW 5-Series or similar",    "rate_usd": 140},
    "van":         {"name": "Minivan",      "example": "Chrysler Pacifica or similar","rate_usd": 85},
}

# ── Train operators by country/region ─────────────────────────────────────────
_TRAIN_OPERATORS = {
    "US":  ["Amtrak"],
    "FR":  ["SNCF (TGV)", "Eurostar"],
    "DE":  ["Deutsche Bahn (ICE)", "Flixbus Rail"],
    "GB":  ["National Rail", "Eurostar"],
    "IT":  ["Trenitalia (Frecciarossa)", "Italo"],
    "ES":  ["Renfe (AVE)"],
    "JP":  ["JR (Shinkansen)"],
    "AU":  ["NSW TrainLink", "Queensland Rail"],
    "CA":  ["VIA Rail"],
    "default": ["InterCity Rail", "Regional Express"],
}

# ── Bus operators ──────────────────────────────────────────────────────────────
_BUS_OPERATORS = ["FlixBus", "Greyhound", "Megabus", "Blablacar Bus", "National Express"]


def _country_train_operator(country: str) -> str:
    ops = _TRAIN_OPERATORS.get(country) or _TRAIN_OPERATORS["default"]
    return ops[0]


def _price_jitter(base: float, seed: int, pct: float = 0.15) -> int:
    return int(base * random.Random(seed).uniform(1 - pct, 1 + pct))


def search_ground_transport(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 1,
    transport_types: list[str] | None = None,
) -> dict:
    """
    Search ground transportation options between two cities.

    Returns available car rentals, trains, and/or buses depending on
    route distance and transport_types filter.

    Args:
        origin:           Origin city or airport code
        destination:      Destination city or airport code
        date:             Travel date in YYYY-MM-DD format
        passengers:       Number of passengers (affects train/bus pricing)
        transport_types:  List subset of ["car", "train", "bus"]. Defaults to all.

    Returns:
        dict with keys: status, results (list of options), route_info
    """
    if transport_types is None:
        transport_types = ["car", "train", "bus"]

    o_res = _find_airport(origin)
    d_res = _find_airport(destination)

    if not o_res:
        return {"status": "error", "message": f"Unknown origin: '{origin}'"}
    if not d_res:
        return {"status": "error", "message": f"Unknown destination: '{destination}'"}

    origin_iata,  (o_city, o_country, o_lat, o_lon, _) = o_res
    dest_iata,    (d_city, d_country, d_lat, d_lon, _) = d_res
    km = _haversine(o_lat, o_lon, d_lat, d_lon)
    seed = int(km) + hash(date) % 9999

    route = f"{o_city} → {d_city}"
    results = []

    # ── Car rental ────────────────────────────────────────────────────────────
    if "car" in transport_types:
        # Car rental makes sense for any distance up to ~1500 km
        if km <= 1500:
            drive_hours = km / 90  # avg 90 km/h including breaks
            for i, (cat_key, cat) in enumerate(list(_CAR_CATEGORIES.items())[:4]):
                company = _CAR_COMPANIES[i % len(_CAR_COMPANIES)]
                daily_rate = _price_jitter(cat["rate_usd"], seed + i)
                results.append({
                    "type":         "car_rental",
                    "provider":     company,
                    "category":     cat["name"],
                    "vehicle":      cat["example"],
                    "pickup_date":  date,
                    "drive_time":   f"{int(drive_hours)}h {int((drive_hours % 1) * 60)}m",
                    "distance_km":  round(km),
                    "price_per_day_usd": daily_rate,
                    "price_usd":    daily_rate,  # 1-day rental by default
                    "notes":        "Prices exclude fuel, insurance, and cross-border fees",
                })
        elif km > 1500:
            results.append({
                "type":     "car_rental",
                "provider": "N/A",
                "notes":    f"Driving {round(km):,} km is not recommended. Consider flying.",
                "available": False,
            })

    # ── Train ─────────────────────────────────────────────────────────────────
    if "train" in transport_types:
        if km <= 2000:  # Extended: overnight trains cover up to ~2000 km
            operator  = _country_train_operator(o_country)
            is_hsr    = km < 800 and o_country in ("FR", "DE", "ES", "IT", "JP", "GB")
            speed_kmh = 280 if is_hsr else 120
            travel_h  = km / speed_kmh
            # Base price per person: short trips $20-50, medium $40-120, long $80-200
            if km < 200:
                base_pp = 25 + km * 0.12
            elif km < 500:
                base_pp = 45 + km * 0.14
            else:
                base_pp = 70 + km * 0.16

            base_pp  = _price_jitter(base_pp, seed + 10)

            # Generate 2-3 daytime departure times
            times = [("06:30", "Earlybird", int(base_pp * 0.85)),
                     ("09:15", "Flexible",  base_pp),
                     ("14:45", "Afternoon", int(base_pp * 0.95))]

            for dep_time, fare_type, pp_price in times:
                arr_min   = int(travel_h * 60)
                arr_h, arr_m = divmod(
                    int(dep_time.split(":")[0]) * 60 + int(dep_time.split(":")[1]) + arr_min, 60
                )
                results.append({
                    "type":            "train",
                    "provider":        operator,
                    "train_type":      "High-Speed" if is_hsr else "Intercity",
                    "fare_type":       fare_type,
                    "departure_time":  dep_time,
                    "arrival_time":    f"{arr_h % 24:02d}:{arr_m:02d}",
                    "travel_time":     f"{int(travel_h)}h {int((travel_h % 1) * 60)}m",
                    "departure_date":  date,
                    "distance_km":     round(km),
                    "passengers":      passengers,
                    "price_per_person_usd": pp_price,
                    "price_usd":       pp_price * passengers,
                    "notes":           "Book early for best fares; seat reservation may be required",
                })

            # Add overnight sleeper option for routes > 400 km
            if km > 400:
                from datetime import datetime as _dt, timedelta as _td
                overnight_pp = _price_jitter(int(base_pp * 1.15), seed + 30)
                dep = "22:00"
                arr_total_min = 22 * 60 + int(travel_h * 60)
                arr_day_offset = arr_total_min // (24 * 60)
                arr_h_ov = (arr_total_min % (24 * 60)) // 60
                arr_m_ov = arr_total_min % 60
                try:
                    arr_date = (_dt.strptime(date, "%Y-%m-%d") + _td(days=arr_day_offset)).strftime("%Y-%m-%d")
                except Exception:
                    arr_date = date
                results.append({
                    "type":             "train",
                    "provider":         operator,
                    "train_type":       "Overnight / Sleeper",
                    "fare_type":        "Sleeper",
                    "departure_time":   dep,
                    "arrival_time":     f"{arr_h_ov:02d}:{arr_m_ov:02d}",
                    "arrival_date":     arr_date,
                    "travel_time":      f"{int(travel_h)}h {int((travel_h % 1) * 60)}m",
                    "departure_date":   date,
                    "distance_km":      round(km),
                    "passengers":       passengers,
                    "price_per_person_usd": overnight_pp,
                    "price_usd":        overnight_pp * passengers,
                    "notes":            "Overnight sleeper — saves one night of accommodation. Couchette or private cabin available.",
                })
        else:
            results.append({
                "type":      "train",
                "available": False,
                "notes":     f"No direct rail service found for {route} ({round(km):,} km). Consider flying or a multi-leg journey.",
            })

    # ── Bus ───────────────────────────────────────────────────────────────────
    if "bus" in transport_types:
        if km <= 1500:
            operator   = _BUS_OPERATORS[seed % len(_BUS_OPERATORS)]
            bus_h      = km / 80  # avg speed incl. stops
            # Bus is always cheapest: ~$0.06-0.10/km per person
            base_pp    = max(12, km * 0.07)
            base_pp    = _price_jitter(base_pp, seed + 20)

            for dep_time in ["07:00", "12:30", "18:00"]:
                arr_min  = int(bus_h * 60)
                arr_h, arr_m = divmod(
                    int(dep_time.split(":")[0]) * 60 + int(dep_time.split(":")[1]) + arr_min, 60
                )
                results.append({
                    "type":           "bus",
                    "provider":       operator,
                    "departure_time": dep_time,
                    "arrival_time":   f"{arr_h % 24:02d}:{arr_m:02d}",
                    "travel_time":    f"{int(bus_h)}h {int((bus_h % 1) * 60)}m",
                    "departure_date": date,
                    "distance_km":    round(km),
                    "passengers":     passengers,
                    "price_per_person_usd": base_pp,
                    "price_usd":      base_pp * passengers,
                    "amenities":      ["WiFi", "Electrical outlets", "Reclining seats"],
                    "notes":          "Cheapest option; book online for discount fares",
                })
        else:
            results.append({
                "type":      "bus",
                "available": False,
                "notes":     f"No bus service found for {route} ({round(km):,} km). Consider train or flight.",
            })

    # Sort by price (unavailable options last)
    available = [r for r in results if r.get("available", True)]
    unavailable = [r for r in results if not r.get("available", True)]
    available.sort(key=lambda r: r.get("price_usd", 999999))

    return {
        "status": "success",
        "route": route,
        "route_info": {
            "origin_city":      o_city,
            "destination_city": d_city,
            "distance_km":      round(km),
            "date":             date,
            "passengers":       passengers,
        },
        "results":   available + unavailable,
        "tip": (
            "🚆 Train is often the best city-centre-to-city-centre option for routes under 500 km. "
            "🚌 Bus is cheapest for short routes. 🚗 Car rental gives flexibility for rural areas."
        ),
    }
