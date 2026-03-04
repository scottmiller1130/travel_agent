"""
Maps and places tool — POI search and distance/travel time estimates.
Uses mock data; swap in Google Maps API when key is set.
"""

import os
import random


POPULAR_POIS = {
    "lisbon": ["Belém Tower", "Jerónimos Monastery", "Alfama District", "Time Out Market", "Sintra", "LX Factory"],
    "paris": ["Eiffel Tower", "Louvre Museum", "Notre-Dame Cathedral", "Montmartre", "Versailles", "Musée d'Orsay"],
    "barcelona": ["Sagrada Família", "Park Güell", "Las Ramblas", "Gothic Quarter", "Camp Nou", "Barceloneta Beach"],
    "rome": ["Colosseum", "Vatican Museums", "Trevi Fountain", "Pantheon", "Borghese Gallery", "Trastevere"],
    "bali": ["Ubud Monkey Forest", "Tanah Lot Temple", "Tegallalang Rice Terraces", "Seminyak Beach", "Uluwatu Temple"],
    "london": ["Big Ben", "Tower of London", "British Museum", "Covent Garden", "Borough Market", "Hyde Park"],
    "tokyo": ["Senso-ji Temple", "Shibuya Crossing", "Tsukiji Market", "Meiji Shrine", "Akihabara", "Mount Fuji"],
    "new york": ["Central Park", "Metropolitan Museum", "Brooklyn Bridge", "Times Square", "The High Line", "MoMA"],
}

CATEGORY_POIS = {
    "restaurant": ["La Trattoria", "Saffron Garden", "The Blue Anchor", "Mama Rosa's", "Fusion 88", "Harbor View"],
    "museum": ["City History Museum", "Modern Art Gallery", "Natural Science Museum", "Archaeological Museum"],
    "beach": ["North Beach", "Crystal Cove", "Sunset Bay", "Palm Shore"],
    "park": ["Central Gardens", "Riverside Park", "Mountain View Reserve"],
    "shopping": ["Grand Market Hall", "Old Town Bazaar", "Fashion District Mall", "Artisan Market"],
}


def search_places(
    destination: str,
    category: str = "attraction",
    query: str | None = None,
    limit: int = 6,
) -> dict:
    """Search for places of interest in a destination."""
    if os.getenv("GOOGLE_MAPS_API_KEY"):
        raise NotImplementedError("Real Google Maps integration not yet wired up")

    dest_lower = destination.lower()
    poi_list = None
    for city, pois in POPULAR_POIS.items():
        if city in dest_lower:
            poi_list = pois
            break

    if poi_list is None:
        cat_lower = category.lower()
        for cat, pois in CATEGORY_POIS.items():
            if cat in cat_lower:
                poi_list = pois
                break
        poi_list = poi_list or [f"Popular Attraction {i+1}" in destination for i in range(6)]
        poi_list = [f"Popular {category.title()} {i+1} in {destination}" for i in range(6)]

    random.seed(f"{destination}{category}")
    results = []
    for i, name in enumerate(poi_list[:limit]):
        results.append({
            "place_id": f"PLC{i+1:03d}",
            "name": name,
            "category": category,
            "rating": round(random.uniform(3.8, 5.0), 1),
            "review_count": random.randint(100, 15000),
            "address": f"{random.randint(1, 200)} {random.choice(['Main St', 'Old Town Sq', 'Via Roma', 'Rue de la Paix'])}, {destination}",
            "opening_hours": "09:00–18:00" if category != "restaurant" else "12:00–23:00",
            "price_range": random.choice(["Free", "$", "$$", "$$$"]),
            "description": f"A must-visit {category} in {destination}.",
            "recommended_duration": f"{random.randint(1, 4)} hour(s)",
        })

    return {
        "status": "success",
        "destination": destination,
        "category": category,
        "query": query,
        "results": results,
        "note": "Mock data — connect Google Maps/Foursquare for live results",
    }


def get_distance(origin: str, destination: str, mode: str = "transit") -> dict:
    """Get travel distance and time between two places."""
    if os.getenv("GOOGLE_MAPS_API_KEY"):
        raise NotImplementedError("Real Google Maps integration not yet wired up")

    random.seed(f"{origin}{destination}{mode}")
    km = random.randint(1, 80)
    speeds = {"driving": 40, "transit": 25, "walking": 5, "cycling": 15}
    speed = speeds.get(mode, 25)
    minutes = int((km / speed) * 60)

    return {
        "status": "success",
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "distance_km": km,
        "distance_miles": round(km * 0.621, 1),
        "duration_minutes": minutes,
        "duration_display": f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m",
        "note": "Mock data — connect Google Maps Directions API for real routing",
    }
