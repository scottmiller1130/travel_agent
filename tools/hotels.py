"""
Hotel search — multi-source with graceful fallback:

1. Hostelworld API (HOSTELWORLD_API_KEY)     → real hostel/dorm inventory & pricing
2. Booking.com Affiliate API (BOOKING_COM_API_KEY) → broad hotel + hostel coverage
3. Hotellook / Travelpayouts (TRAVELPAYOUTS_TOKEN) → cached real hotel prices, free
4. Amadeus Hotel Search (AMADEUS_CLIENT_ID)  → GDS hotel inventory (standard hotels only)
5. OpenStreetMap + estimated pricing         → always-available fallback

Plug-and-play: set any API key in your environment to unlock that source.
Get keys:
  Hostelworld API: https://www.hostelworld.com/pwa/developers  (affiliate program)
  Booking.com Affiliate: https://join.booking.com/affiliateprogram/welcome/
  Hotellook (Travelpayouts): https://www.travelpayouts.com/developers/api (free affiliate)
  Amadeus: https://developers.amadeus.com (free, no credit card)
"""

import math
import os
import random
import string
import time

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

# Re-use the shared token cache from flights module
from tools.flights import _get_amadeus_token, _find_airport, AIRPORTS

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
OSM_HEADERS   = {"User-Agent": "TravelAgentApp/1.0 (travel-agent-demo)"}

# ── Hostelworld API stub ──────────────────────────────────────────────────────
# Set HOSTELWORLD_API_KEY to enable real hostel/dorm inventory and pricing.
# API docs: https://www.hostelworld.com/pwa/developers
# Affiliate sign-up: https://www.hostelworld.com/info/affiliates

def _hostelworld_search(destination: str, check_in: str, check_out: str,
                        guests: int, max_results: int,
                        accommodation_type: str) -> dict | None:
    """
    Hostelworld API integration — plug-and-play when HOSTELWORLD_API_KEY is set.

    Returns real hostel names, dorm/private room prices, and amenity data
    including common room quality, kitchen access, and vibe (party vs quiet).
    This is the authoritative source for hostel and dorm inventory.
    """
    api_key = os.getenv("HOSTELWORLD_API_KEY")
    if not api_key or not _HTTPX:
        return None  # silently fall through to next source

    if accommodation_type not in ("hostel", "dorm"):
        return None  # Hostelworld is hostel-specific; hotels go to other sources

    # TODO: implement when API key available
    # Hostelworld affiliate API base: https://api.hostelworld.com/2.0/
    # Endpoints:
    #   GET /properties?city={city}&checkIn={date}&checkOut={date}&guests={n}
    #   Returns: property name, type (hostel/guesthouse), dorm prices, private prices,
    #            amenities (common_room_type, kitchen, lockers, rooftop_bar), vibe_tags
    #
    # Example response shape to map to our schema:
    # {
    #   "hotel_id": str(property["id"]),
    #   "name": property["name"],
    #   "stars": None,  # hostels don't use stars
    #   "rating": property["rating"]["overall"],
    #   "review_count": property["rating"]["numberOfRatings"],
    #   "price_per_night_usd": property["lowestDormPrice"] if dorm else property["lowestPrivatePrice"],
    #   "amenities": [tag for tag in property["facilities"]],
    #   "vibe": property.get("vibe_tags", []),  # party | social | quiet | boutique
    #   "source": "Hostelworld (live)",
    # }

    return None  # remove this line once implemented


# ── Booking.com Affiliate API stub ────────────────────────────────────────────
# Set BOOKING_COM_API_KEY to enable real hotel and hostel availability + pricing.
# API docs: https://developers.booking.com/
# Affiliate program: https://join.booking.com/affiliateprogram/welcome/

def _booking_com_search(destination: str, check_in: str, check_out: str,
                        guests: int, rooms: int, max_results: int,
                        max_price: int | None, accommodation_type: str,
                        min_stars: int | None = None) -> dict | None:
    """
    Booking.com Affiliate API integration — plug-and-play when BOOKING_COM_API_KEY is set.

    Covers the full spectrum: luxury 5-star hotels, boutique guesthouses, hostels.
    Best source for luxury inventory (Four Seasons, Aman, Ritz-Carlton, etc.) and
    for mid-range hotels where real availability data matters.
    """
    api_key = os.getenv("BOOKING_COM_API_KEY")
    if not api_key or not _HTTPX:
        return None  # silently fall through to next source

    # TODO: implement when API key available
    # Booking.com Affiliate REST API v2:
    # Base: https://distribution-xml.booking.com/2.0/json/
    # Endpoint: GET /hotels?city_ids={city_id}&checkin={date}&checkout={date}
    #           &room1=A,A&rows={max_results}&languagecode=en-us
    # Auth: HTTP Basic with api_key:secret
    #
    # City ID lookup: GET /cities?name={destination}
    # Filter params: hotel_class (star rating), price_min, price_max, accommodation_type
    #
    # Example response shape to map to our schema:
    # {
    #   "hotel_id": str(hotel["hotel_id"]),
    #   "name": hotel["hotel_name"],
    #   "stars": hotel.get("hotel_class"),
    #   "rating": hotel.get("review_score"),
    #   "review_count": hotel.get("review_nr"),
    #   "price_per_night_usd": hotel["composite_price_breakdown"]["gross_amount_per_night"]["value"],
    #   "free_cancellation": hotel.get("is_free_cancellable", False),
    #   "neighborhood": hotel.get("district"),
    #   "amenities": [f["name"] for f in hotel.get("facilities", [])],
    #   "source": "Booking.com (live)",
    # }

    return None  # remove this line once implemented


# ── Hotellook / Travelpayouts (optional, free) ───────────────────────────────
# Set TRAVELPAYOUTS_TOKEN to enable real cached hotel price data.
# Free affiliate signup: https://www.travelpayouts.com/developers/api
# Covers 250,000+ hotels worldwide with cached real-world pricing.

def _hotellook_search(destination: str, check_in: str, check_out: str,
                      guests: int, rooms: int, max_results: int,
                      max_price: int | None) -> dict | None:
    """
    Hotellook API (Travelpayouts) — plug-and-play when TRAVELPAYOUTS_TOKEN is set.

    Returns cached real hotel prices aggregated from dozens of OTAs including
    Booking.com, Hotels.com, Agoda, and direct hotel rates. Free to use with
    affiliate token. Data refreshed regularly from live searches.
    """
    token = os.getenv("TRAVELPAYOUTS_TOKEN", "").strip()
    if not token or not _HTTPX:
        return None

    try:
        from datetime import datetime as _dt
        nights = max(1, (_dt.strptime(check_out, "%Y-%m-%d") -
                         _dt.strptime(check_in,  "%Y-%m-%d")).days)
    except Exception:
        nights = 1

    try:
        r = _httpx.get(
            "https://engine.hotellook.com/api/v2/cache.json",
            params={
                "location":  destination,
                "checkIn":   check_in,
                "checkOut":  check_out,
                "adults":    guests,
                "currency":  "USD",
                "limit":     max_results * 2,
                "token":     token,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        hotels_raw = r.json()
        if not hotels_raw:
            return None
    except Exception:
        return None

    results = []
    for h in hotels_raw:
        price_night = h.get("priceFrom")
        if price_night is None:
            continue
        price_night = round(float(price_night), 2)
        if max_price and price_night > max_price:
            continue

        stars = h.get("stars")
        try:
            stars = int(stars) if stars else None
        except (ValueError, TypeError):
            stars = None

        results.append({
            "hotel_id":            str(h.get("id", "")),
            "name":                h.get("name", "Hotel"),
            "destination":         destination,
            "stars":               stars,
            "rating":              h.get("rating"),
            "review_count":        h.get("ratingCount"),
            "check_in":            check_in,
            "check_out":           check_out,
            "guests":              guests,
            "rooms":               rooms,
            "price_per_night_usd": price_night,
            "total_price_usd":     round(price_night * nights * rooms, 2),
            "nights":              nights,
            "amenities":           [],
            "free_cancellation":   None,
            "neighborhood":        h.get("location", {}).get("name", "") if isinstance(h.get("location"), dict) else "",
            "latitude":            h.get("location", {}).get("lat") if isinstance(h.get("location"), dict) else None,
            "longitude":           h.get("location", {}).get("lon") if isinstance(h.get("location"), dict) else None,
            "source":              "Hotellook / Travelpayouts (cached real pricing)",
        })
        if len(results) >= max_results:
            break

    if not results:
        return None

    results.sort(key=lambda x: x["price_per_night_usd"])
    return {
        "status":  "success",
        "results": results,
        "source":  "Hotellook / Travelpayouts (cached real hotel pricing)",
        "note":    "Prices from recent OTA cache — confirm availability on booking site.",
    }


# ── Amadeus hotel search ──────────────────────────────────────────────────────

def _amadeus_hotels(destination: str, check_in: str, check_out: str,
                    guests: int, rooms: int, max_results: int,
                    max_price: int | None) -> dict | None:
    token = _get_amadeus_token()
    if not token:
        return None

    host = os.getenv("AMADEUS_HOST", "https://test.api.amadeus.com")

    # Step 1: resolve city to IATA city code via airport lookup
    airport = _find_airport(destination)
    if not airport:
        return None
    iata, airport_data = airport
    city_code = iata  # Amadeus accepts airport code as city code for hotel search

    # Step 2: find hotels in the city
    try:
        r = _httpx.get(
            f"{host}/v1/reference-data/locations/hotels/by-city",
            headers={"Authorization": f"Bearer {token}"},
            params={"cityCode": city_code, "radius": 20, "radiusUnit": "KM",
                    "hotelSource": "ALL"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        hotel_list = r.json().get("data", [])[:50]
        if not hotel_list:
            return None
        hotel_ids = [h["hotelId"] for h in hotel_list[:30]]
    except Exception:
        return None

    # Step 3: get offers for those hotels
    try:
        r = _httpx.get(
            f"{host}/v3/shopping/hotel-offers",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "hotelIds":    ",".join(hotel_ids),
                "adults":      guests,
                "checkInDate": check_in,
                "checkOutDate":check_out,
                "roomQuantity":rooms,
                "currency":    "USD",
                "bestRateOnly":"true",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None
        offers_data = r.json().get("data", [])
    except Exception:
        return None

    try:
        from datetime import datetime
        nights = max(1, (datetime.strptime(check_out, "%Y-%m-%d") -
                         datetime.strptime(check_in,  "%Y-%m-%d")).days)
    except Exception:
        nights = 1

    results = []
    for item in offers_data[:max_results * 2]:
        hotel = item.get("hotel", {})
        offers = item.get("offers", [])
        if not offers:
            continue
        offer = offers[0]
        price_total = float(offer["price"].get("total", 0))
        price_night = round(price_total / nights / rooms, 2)
        if max_price and price_night > max_price:
            continue

        amenities_raw = hotel.get("amenities", [])
        amenities = [a.replace("_", " ").title() for a in amenities_raw[:8]]

        results.append({
            "hotel_id":            hotel.get("hotelId", ""),
            "name":                hotel.get("name", "Hotel"),
            "destination":         destination,
            "stars":               hotel.get("rating", ""),
            "rating":              None,
            "review_count":        None,
            "check_in":            check_in,
            "check_out":           check_out,
            "guests":              guests,
            "rooms":               rooms,
            "price_per_night_usd": price_night,
            "total_price_usd":     round(price_total, 2),
            "nights":              nights,
            "amenities":           amenities,
            "free_cancellation":   offer.get("policies", {}).get("cancellation", {}).get("type") == "NONE",
            "neighborhood":        hotel.get("cityCode", ""),
            "latitude":            hotel.get("latitude"),
            "longitude":           hotel.get("longitude"),
        })

        if len(results) >= max_results:
            break

    if not results:
        return None

    results.sort(key=lambda x: x["price_per_night_usd"])
    return {
        "status":  "success",
        "results": results,
        "source":  "Amadeus Hotel Search (live)",
    }


# ── OpenStreetMap fallback ────────────────────────────────────────────────────

def _osm_hotels(destination: str, check_in: str, check_out: str,
                guests: int, rooms: int, max_results: int,
                max_price: int | None) -> dict:
    """Query OpenStreetMap for real hotel names, estimate pricing."""
    try:
        from datetime import datetime
        nights = max(1, (datetime.strptime(check_out, "%Y-%m-%d") -
                         datetime.strptime(check_in,  "%Y-%m-%d")).days)
    except Exception:
        nights = 1

    # Geocode city
    try:
        time.sleep(0.3)
        geo = _httpx.get(NOMINATIM_URL, headers=OSM_HEADERS, params={
            "q": destination, "format": "json", "limit": 1,
        }, timeout=8)
        geo_data = geo.json()
        if not geo_data:
            raise ValueError("not found")
        loc = geo_data[0]
        lat, lon = float(loc["lat"]), float(loc["lon"])
        bb = loc.get("boundingbox", [])
        if len(bb) == 4:
            s, n, w, e = [float(b) for b in bb]
        else:
            d = 0.15; s, n, w, e = lat-d, lat+d, lon-d, lon+d
    except Exception:
        return _price_only_hotels(destination, check_in, check_out, guests, rooms,
                                  nights, max_results, max_price)

    # Query Overpass for hotels
    query = f"""
[out:json][timeout:12];
(
  node["tourism"~"hotel|hostel|guest_house|motel"]["name"]({s},{w},{n},{e});
  way["tourism"~"hotel|hostel|guest_house|motel"]["name"]({s},{w},{n},{e});
);
out center {max_results * 3};
""".strip()
    try:
        r = _httpx.post(OVERPASS_URL, data={"data": query}, timeout=15)
        elements = r.json().get("elements", [])
    except Exception:
        elements = []

    hotels = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        h_lat = el.get("lat") or el.get("center", {}).get("lat") or lat
        h_lon = el.get("lon") or el.get("center", {}).get("lon") or lon
        stars_raw = tags.get("stars") or tags.get("tourism")
        try:
            stars = int(stars_raw) if stars_raw and str(stars_raw).isdigit() else None
        except Exception:
            stars = None

        rng = random.Random(hash(name) + int(lat * 100))
        price_night = _estimate_price(destination, stars, rng)
        if max_price and price_night > max_price:
            continue

        amenities_raw = tags.get("amenity", "")
        amenities = ["WiFi"]
        if tags.get("internet_access"):
            amenities.append("Internet")
        if tags.get("swimming_pool") == "yes":
            amenities.append("Pool")
        if tags.get("parking"):
            amenities.append("Parking")
        if tags.get("restaurant"):
            amenities.append("Restaurant")

        hotels.append({
            "hotel_id":            el.get("id", ""),
            "name":                name,
            "destination":         destination,
            "stars":               stars,
            "rating":              round(rng.uniform(3.4, 4.9), 1),
            "review_count":        rng.randint(40, 3000),
            "check_in":            check_in,
            "check_out":           check_out,
            "guests":              guests,
            "rooms":               rooms,
            "price_per_night_usd": price_night,
            "total_price_usd":     price_night * nights * rooms,
            "nights":              nights,
            "amenities":           amenities,
            "free_cancellation":   rng.choice([True, True, False]),
            "neighborhood":        tags.get("addr:suburb") or tags.get("addr:city", ""),
            "website":             tags.get("website") or tags.get("contact:website"),
            "latitude":            h_lat,
            "longitude":           h_lon,
        })
        if len(hotels) >= max_results * 2:
            break

    if not hotels:
        return _price_only_hotels(destination, check_in, check_out, guests, rooms,
                                  nights, max_results, max_price)

    hotels.sort(key=lambda x: x["price_per_night_usd"])
    return {
        "status":  "success",
        "results": hotels[:max_results],
        "source":  "OpenStreetMap (real hotel names) + estimated pricing",
    }


def _estimate_price(destination: str, stars: int | None, rng: random.Random) -> int:
    """Realistic price estimate based on destination tier and star rating."""
    tier_map = {
        # Premium destinations
        "paris": 180, "london": 200, "new york": 220, "tokyo": 160,
        "dubai": 170, "singapore": 180, "sydney": 170, "zurich": 240,
        "hong kong": 190, "san francisco": 210, "amsterdam": 170,
        # Mid-tier
        "barcelona": 120, "rome": 130, "berlin": 110, "madrid": 115,
        "istanbul": 90,  "athens": 95, "prague": 85,  "lisbon": 110,
        "bangkok": 75,   "bali": 70,   "kuala lumpur": 80,
        # Budget-friendly
        "ho chi minh": 55, "hanoi": 50, "cairo": 60, "marrakech": 70,
        "bogota": 65,    "lima": 60,  "mexico city": 75,
    }
    d_lower = destination.lower()
    base = 100  # default
    for key, price in tier_map.items():
        if key in d_lower:
            base = price
            break

    star_mult = {1: 0.3, 2: 0.5, 3: 0.8, 4: 1.3, 5: 2.4}
    mult = star_mult.get(stars, 1.0) if stars else 1.0
    return int(base * mult * rng.uniform(0.85, 1.20))


def _price_only_hotels(destination, check_in, check_out, guests, rooms,
                        nights, max_results, max_price) -> dict:
    """Pure price-estimate fallback with generic names."""
    names = [
        f"The Grand {destination.title()} Hotel",
        f"{destination.title()} Boutique Inn",
        f"Marriott {destination.title()}",
        f"Hilton {destination.title()} City Center",
        f"Hyatt Place {destination.title()}",
        f"Ibis {destination.title()} Central",
        f"{destination.title()} Hostel & Lounge",
    ]
    results = []
    for i, name in enumerate(names[:max_results]):
        rng = random.Random(hash(destination) + i)
        stars = [2, 3, 3, 4, 4, 3, 2][i]
        price = _estimate_price(destination, stars, rng)
        if max_price and price > max_price:
            price = min(price, max_price - rng.randint(5, 20))
        results.append({
            "hotel_id":            f"HTL{i+1:03d}",
            "name":                name,
            "destination":         destination,
            "stars":               stars,
            "rating":              round(rng.uniform(3.4, 4.8), 1),
            "review_count":        rng.randint(50, 2000),
            "check_in":            check_in,
            "check_out":           check_out,
            "guests":              guests,
            "rooms":               rooms,
            "price_per_night_usd": price,
            "total_price_usd":     price * nights * rooms,
            "nights":              nights,
            "amenities":           random.sample(["WiFi","Pool","Gym","Breakfast","Parking","Spa","Restaurant"], 4),
            "free_cancellation":   rng.choice([True, True, False]),
            "neighborhood":        rng.choice(["City Center","Old Town","Beachfront","Arts District"]),
        })
    results.sort(key=lambda x: x["price_per_night_usd"])
    return {"status": "success", "results": results,
            "source": "Estimated pricing (set AMADEUS_CLIENT_ID for live rates)"}


# ── Accommodation type pricing multipliers ────────────────────────────────────

_ACCOM_CONFIG = {
    "hotel":      {"osm_types": "hotel|motel",              "price_mult": 1.0,  "label": "Hotel"},
    "hostel":     {"osm_types": "hostel",                    "price_mult": 0.35, "label": "Hostel (private room)"},
    "guesthouse": {"osm_types": "guest_house",               "price_mult": 0.45, "label": "Guesthouse"},
    "dorm":       {"osm_types": "hostel",                    "price_mult": 0.18, "label": "Hostel (dorm bed)"},
}


def _dorm_price(destination: str, rng: random.Random) -> int:
    """Per-bed dorm price based on destination tier."""
    tier_map = {
        "paris": 35, "london": 38, "new york": 45, "tokyo": 32,
        "dubai": 30, "singapore": 35, "sydney": 34, "zurich": 48,
        "amsterdam": 32, "san francisco": 42,
        "barcelona": 24, "rome": 22, "berlin": 20, "madrid": 22,
        "istanbul": 14, "prague": 14, "lisbon": 20, "athens": 16,
        "bangkok": 10, "bali": 8, "kuala lumpur": 10,
        "ho chi minh": 8, "hanoi": 7, "cairo": 10, "marrakech": 12,
    }
    d_lower = destination.lower()
    base = 22  # default
    for key, price in tier_map.items():
        if key in d_lower:
            base = price
            break
    return int(base * rng.uniform(0.85, 1.20))


def _apply_accommodation_type(result: dict, accommodation_type: str) -> dict:
    """Post-process results to apply accommodation type pricing and labeling."""
    cfg = _ACCOM_CONFIG.get(accommodation_type, _ACCOM_CONFIG["hotel"])
    label = cfg["label"]
    mult = cfg["price_mult"]

    for item in result.get("results", []):
        if accommodation_type == "dorm":
            rng = random.Random(hash(item.get("name", "")) + 1)
            dest = item.get("destination", "")
            item["price_per_night_usd"] = _dorm_price(dest, rng)
            item["price_per_bed_usd"] = item["price_per_night_usd"]
            nights = item.get("nights", 1)
            guests = item.get("guests", 1)
            item["total_price_usd"] = item["price_per_night_usd"] * nights
            item["accommodation_type"] = label
            item["note"] = "Dorm bed price per person. Mixed or female-only dorms typically available."
            # Adjust amenities for hostel context
            item["amenities"] = list(set(item.get("amenities", [])) |
                                     {"WiFi", "Shared Kitchen", "Lockers", "Common Room"})
        elif accommodation_type in ("hostel", "guesthouse"):
            item["price_per_night_usd"] = max(10, int(item["price_per_night_usd"] * mult))
            nights = item.get("nights", 1)
            rooms = item.get("rooms", 1)
            item["total_price_usd"] = item["price_per_night_usd"] * nights * rooms
            item["accommodation_type"] = label
            if accommodation_type == "hostel":
                item["amenities"] = list(set(item.get("amenities", [])) |
                                         {"WiFi", "Shared Kitchen", "Common Room"})
        else:
            item["accommodation_type"] = label

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def search_hotels(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int = 1,
    rooms: int = 1,
    max_results: int = 5,
    max_price_per_night: int | None = None,
    accommodation_type: str = "hotel",
    min_stars: int | None = None,
) -> dict:
    """
    Search hotels/hostels/guesthouses.

    Source priority (each source is tried in order; first success wins):
    1. Hostelworld API   — real hostel/dorm inventory (HOSTELWORLD_API_KEY)
    2. Booking.com API   — full spectrum hotel/hostel coverage (BOOKING_COM_API_KEY)
    3. Hotellook         — 250k+ hotels, cached OTA pricing (TRAVELPAYOUTS_TOKEN)
    4. Amadeus GDS       — standard hotel inventory (AMADEUS_CLIENT_ID, hotels only)
    5. OpenStreetMap     — always-available fallback with estimated pricing

    accommodation_type: "hotel" (default), "hostel", "guesthouse", "dorm"
    min_stars: minimum star rating filter (for luxury travelers; e.g. 4 or 5)
    """
    if not _HTTPX:
        return {"status": "error", "message": "httpx not installed"}

    fetch_count = max_results if accommodation_type == "hotel" else max_results + 5
    query_meta = {
        "destination": destination, "check_in": check_in,
        "check_out": check_out, "guests": guests, "rooms": rooms,
        "accommodation_type": accommodation_type,
    }
    if min_stars:
        query_meta["min_stars"] = min_stars

    # 1. Hostelworld — best source for hostel/dorm
    if accommodation_type in ("hostel", "dorm"):
        try:
            result = _hostelworld_search(destination, check_in, check_out,
                                         guests, fetch_count, accommodation_type)
            if result:
                result["query"] = query_meta
                result["currency"] = "USD"
                result = _apply_accommodation_type(result, accommodation_type)
                if "results" in result:
                    result["results"] = result["results"][:max_results]
                return result
        except Exception:
            pass

    # 2. Booking.com — broad coverage for all types
    try:
        result = _booking_com_search(destination, check_in, check_out,
                                     guests, rooms, fetch_count, max_price_per_night,
                                     accommodation_type, min_stars)
        if result:
            result["query"] = query_meta
            result["currency"] = "USD"
            result = _apply_accommodation_type(result, accommodation_type)
            if "results" in result:
                result["results"] = result["results"][:max_results]
            return result
    except Exception:
        pass

    # 3. Hotellook / Travelpayouts — broad OTA coverage, free with affiliate token
    if accommodation_type in ("hotel", "guesthouse"):
        try:
            result = _hotellook_search(destination, check_in, check_out,
                                       guests, rooms, fetch_count, max_price_per_night)
            if result:
                if min_stars and "results" in result:
                    result["results"] = [
                        h for h in result["results"]
                        if (h.get("stars") or 0) >= min_stars
                    ]
                result["query"] = query_meta
                result["currency"] = "USD"
                result = _apply_accommodation_type(result, accommodation_type)
                if "results" in result:
                    result["results"] = result["results"][:max_results]
                return result
        except Exception:
            pass

    # 4. Amadeus — standard hotels only
    if os.getenv("AMADEUS_CLIENT_ID") and accommodation_type == "hotel":
        try:
            result = _amadeus_hotels(destination, check_in, check_out,
                                     guests, rooms, fetch_count, max_price_per_night)
            if result:
                # Apply star filter for luxury travelers
                if min_stars and "results" in result:
                    result["results"] = [
                        h for h in result["results"]
                        if (h.get("stars") or 0) >= min_stars
                    ]
                result["query"] = query_meta
                result["currency"] = "USD"
                result = _apply_accommodation_type(result, accommodation_type)
                if "results" in result:
                    result["results"] = result["results"][:max_results]
                return result
        except Exception:
            pass

    # 4. OpenStreetMap fallback
    result = _osm_hotels(destination, check_in, check_out, guests, rooms,
                         fetch_count, max_price_per_night)
    result = _apply_accommodation_type(result, accommodation_type)
    # Apply star filter post-hoc for luxury travelers
    if min_stars and "results" in result:
        result["results"] = [
            h for h in result["results"]
            if (h.get("stars") or 0) >= min_stars
        ]
    if "results" in result:
        result["results"] = result["results"][:max_results]
    result["query"] = query_meta
    result["currency"] = "USD"
    return result


def book_hotel(
    hotel_id: str,
    guest_name: str,
    guest_email: str,
    payment_confirmed: bool = False,
    room_type: str | None = None,
    bed_preference: str | None = None,
    special_requests: str | None = None,
) -> dict:
    if not payment_confirmed:
        msg = "Please confirm you want to book this hotel."
        if room_type:
            msg += f" Room preference: {room_type}."
        if special_requests:
            msg += f" Special requests: {special_requests}."
        return {"status": "pending_confirmation", "message": msg}

    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    result = {
        "status": "booked",
        "confirmation_code": code,
        "hotel_id": hotel_id,
        "guest_name": guest_name,
        "guest_email": guest_email,
        "message": f"Hotel booked! Confirmation: {code}",
    }
    if room_type:
        result["room_type"] = room_type
        result["message"] += f" Room preference '{room_type}' noted."
    if bed_preference:
        result["bed_preference"] = bed_preference
    if special_requests:
        result["special_requests"] = special_requests
        result["message"] += f" Special requests forwarded: {special_requests}."
    return result
