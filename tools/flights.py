"""
Flight search and booking tool.
Uses mock data by default; swap in Amadeus/Duffel SDK when API keys are set.
"""

import os
import random
import string
from datetime import datetime, timedelta


def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    passengers: int = 1,
    cabin_class: str = "economy",
    max_results: int = 5,
) -> dict:
    """Search for available flights."""
    # Real implementation: call Amadeus or Duffel API here
    # amadeus.shopping.flight_offers_search.get(...)
    if os.getenv("AMADEUS_CLIENT_ID"):
        raise NotImplementedError("Real Amadeus integration not yet wired up")

    airlines = ["Delta", "United", "American", "Lufthansa", "British Airways", "Air France", "Emirates"]
    results = []

    random.seed(f"{origin}{destination}{departure_date}")

    for i in range(min(max_results, 5)):
        airline = airlines[i % len(airlines)]
        base_price = random.randint(250, 1800)
        cabin_mult = {"economy": 1.0, "premium_economy": 1.6, "business": 3.5, "first": 6.0}
        price = int(base_price * cabin_mult.get(cabin_class, 1.0) * passengers)

        dep_hour = random.choice([6, 8, 10, 13, 16, 19, 21])
        duration_h = random.randint(2, 14)
        duration_m = random.choice([0, 15, 30, 45])
        stops = 0 if duration_h < 6 else random.randint(0, 1)

        results.append({
            "flight_id": f"FL{i+1:03d}",
            "airline": airline,
            "flight_number": f"{airline[:2].upper()}{random.randint(100, 999)}",
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "departure_time": f"{dep_hour:02d}:00",
            "arrival_time": f"{(dep_hour + duration_h) % 24:02d}:{duration_m:02d}",
            "duration": f"{duration_h}h {duration_m}m",
            "stops": stops,
            "cabin_class": cabin_class,
            "price_usd": price,
            "price_per_person_usd": price // passengers,
            "seats_available": random.randint(1, 12),
            "return_date": return_date,
        })

    results.sort(key=lambda x: x["price_usd"])
    return {
        "status": "success",
        "query": {
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_date": departure_date,
            "return_date": return_date,
            "passengers": passengers,
            "cabin_class": cabin_class,
        },
        "results": results,
        "currency": "USD",
        "note": "Mock data — connect Amadeus/Duffel for live pricing",
    }


def book_flight(flight_id: str, passenger_name: str, passenger_email: str, payment_confirmed: bool = False) -> dict:
    """Book a specific flight."""
    if not payment_confirmed:
        return {
            "status": "pending_confirmation",
            "message": "Payment not confirmed. Set payment_confirmed=True to proceed with booking.",
        }

    confirmation_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return {
        "status": "booked",
        "confirmation_code": confirmation_code,
        "flight_id": flight_id,
        "passenger_name": passenger_name,
        "passenger_email": passenger_email,
        "message": f"Flight booked successfully. Confirmation: {confirmation_code}",
        "note": "Mock booking — integrate real payment + Amadeus booking flow for production",
    }
