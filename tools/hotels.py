"""
Hotel search and booking tool.
Uses mock data by default; swap in Booking.com or Expedia API when keys are set.
"""

import os
import random
import string


def search_hotels(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int = 1,
    rooms: int = 1,
    max_results: int = 5,
    max_price_per_night: int | None = None,
) -> dict:
    """Search for available hotels."""
    if os.getenv("BOOKING_API_KEY"):
        raise NotImplementedError("Real Booking.com integration not yet wired up")

    hotel_names = [
        f"The Grand {destination.title()} Hotel",
        f"{destination.title()} Boutique Inn",
        f"Marriott {destination.title()}",
        f"Hilton {destination.title()} City Center",
        f"Airbnb: Cozy Apartment in {destination.title()}",
        f"Ibis {destination.title()} Central",
        f"{destination.title()} Hostel & Lounge",
    ]
    amenities_pool = ["WiFi", "Pool", "Gym", "Breakfast included", "Airport shuttle",
                      "Parking", "Spa", "Restaurant", "Bar", "Pet friendly", "Kitchen"]

    random.seed(f"{destination}{check_in}{check_out}")
    results = []

    for i in range(min(max_results, len(hotel_names))):
        price_per_night = random.randint(40, 600)
        if max_price_per_night and price_per_night > max_price_per_night:
            price_per_night = max_price_per_night - random.randint(1, 20)

        stars = random.randint(2, 5)
        amenities = random.sample(amenities_pool, random.randint(3, 7))

        results.append({
            "hotel_id": f"HTL{i+1:03d}",
            "name": hotel_names[i],
            "destination": destination,
            "stars": stars,
            "rating": round(random.uniform(3.5, 5.0), 1),
            "review_count": random.randint(50, 2000),
            "check_in": check_in,
            "check_out": check_out,
            "guests": guests,
            "rooms": rooms,
            "price_per_night_usd": price_per_night,
            "total_price_usd": price_per_night * _nights(check_in, check_out) * rooms,
            "nights": _nights(check_in, check_out),
            "amenities": amenities,
            "free_cancellation": random.choice([True, True, False]),
            "neighborhood": random.choice(["City Center", "Old Town", "Beachfront", "Airport Area", "Arts District"]),
        })

    results.sort(key=lambda x: x["price_per_night_usd"])
    return {
        "status": "success",
        "query": {
            "destination": destination,
            "check_in": check_in,
            "check_out": check_out,
            "guests": guests,
            "rooms": rooms,
        },
        "results": results,
        "currency": "USD",
        "note": "Mock data — connect Booking.com/Expedia API for live availability",
    }


def book_hotel(hotel_id: str, guest_name: str, guest_email: str, payment_confirmed: bool = False) -> dict:
    """Book a specific hotel."""
    if not payment_confirmed:
        return {
            "status": "pending_confirmation",
            "message": "Payment not confirmed. Set payment_confirmed=True to proceed with booking.",
        }

    confirmation_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return {
        "status": "booked",
        "confirmation_code": confirmation_code,
        "hotel_id": hotel_id,
        "guest_name": guest_name,
        "guest_email": guest_email,
        "message": f"Hotel booked successfully. Confirmation: {confirmation_code}",
        "note": "Mock booking — integrate real payment + hotel API for production",
    }


def _nights(check_in: str, check_out: str) -> int:
    try:
        from datetime import datetime
        fmt = "%Y-%m-%d"
        return max(1, (datetime.strptime(check_out, fmt) - datetime.strptime(check_in, fmt)).days)
    except Exception:
        return 1
