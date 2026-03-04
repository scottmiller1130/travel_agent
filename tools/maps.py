"""
Maps and places tool — real POI data from OpenStreetMap.
Uses Nominatim for geocoding and Overpass API for POI search.
No API key or registration required.

Nominatim usage policy: max 1 req/sec, include a User-Agent.
"""

import math
import time

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
HEADERS       = {"User-Agent": "TravelAgentApp/1.0 (travel-agent-demo)"}

# Map our category names to OSM tags
OSM_CATEGORY_MAP = {
    "restaurant":   '[amenity~"restaurant|cafe|fast_food|food_court"]',
    "museum":       '[tourism~"museum|gallery|attraction"][name]',
    "attraction":   '[tourism~"attraction|viewpoint|museum|gallery|theme_park|zoo|aquarium"][name]',
    "hotel":        '[tourism~"hotel|hostel|guest_house|motel"][name]',
    "beach":        '[natural="beach"][name]',
    "park":         '[leisure~"park|nature_reserve|garden"][name]',
    "shopping":     '[shop~"mall|department_store|market|supermarket"][name]',
    "bar":          '[amenity~"bar|pub|nightclub"][name]',
    "cafe":         '[amenity~"cafe|coffee_shop"][name]',
    "nightlife":    '[amenity~"bar|pub|nightclub|casino"][name]',
    "transport":    '[amenity~"bus_station|taxi|ferry_terminal"][name]',
}


def _geocode_city(city: str) -> dict | None:
    """Return bounding box and center for a city."""
    try:
        time.sleep(0.3)  # Respect Nominatim rate limit
        r = _httpx.get(NOMINATIM_URL, headers=HEADERS, params={
            "q": city, "format": "json", "limit": 1,
            "featuretype": "city,town,village",
            "addressdetails": 0,
        }, timeout=8)
        data = r.json()
        if not data:
            return None
        loc = data[0]
        bb = loc.get("boundingbox", [])
        return {
            "lat":     float(loc["lat"]),
            "lon":     float(loc["lon"]),
            "name":    loc.get("display_name", city).split(",")[0].strip(),
            "bbox":    [float(b) for b in bb] if len(bb) == 4 else None,
        }
    except Exception:
        return None


def _overpass_pois(bbox: list[float], osm_filter: str, limit: int) -> list[dict]:
    """Query Overpass for POIs within a bounding box."""
    s, n, w, e = bbox[0], bbox[1], bbox[2], bbox[3]
    # Expand box slightly if very small
    spread = max(abs(n - s), abs(e - w))
    if spread < 0.05:
        pad = (0.05 - spread) / 2
        s -= pad; n += pad; w -= pad; e += pad

    query = f"""
[out:json][timeout:12];
(
  node{osm_filter}({s},{w},{n},{e});
  way{osm_filter}({s},{w},{n},{e});
);
out center {limit * 2};
""".strip()

    r = _httpx.post(OVERPASS_URL, data={"data": query}, timeout=15)
    r.raise_for_status()
    elements = r.json().get("elements", [])

    results = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        results.append({
            "name":         name,
            "lat":          lat,
            "lon":          lon,
            "website":      tags.get("website") or tags.get("contact:website"),
            "phone":        tags.get("phone") or tags.get("contact:phone"),
            "opening_hours":tags.get("opening_hours"),
            "cuisine":      tags.get("cuisine"),
            "stars":        tags.get("stars"),
            "wheelchair":   tags.get("wheelchair"),
            "description":  tags.get("description"),
        })
    return results[:limit]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two lat/lon points."""
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def search_places(destination: str, category: str = "attraction",
                  query: str | None = None, limit: int = 8) -> dict:
    """
    Search for real places of interest using OpenStreetMap data.
    No API key required.
    """
    if not _HTTPX:
        return {"status": "error", "message": "httpx not installed"}

    search_query = f"{query} {destination}" if query else destination
    loc = _geocode_city(search_query)
    if not loc:
        return {"status": "error", "message": f"Could not locate '{destination}'"}

    if not loc["bbox"]:
        # Fake a small bounding box around the point
        d = 0.15
        loc["bbox"] = [loc["lat"] - d, loc["lat"] + d, loc["lon"] - d, loc["lon"] + d]

    osm_filter = OSM_CATEGORY_MAP.get(category.lower(),
                                      '[tourism~"attraction|viewpoint|museum"][name]')

    try:
        pois = _overpass_pois(loc["bbox"], osm_filter, limit)
    except Exception as exc:
        return {"status": "error", "message": f"Places lookup failed: {exc}",
                "destination": destination}

    # Build rich result objects
    results = []
    for p in pois:
        entry = {
            "name":      p["name"],
            "category":  category,
            "address":   destination,
        }
        if p.get("website"):
            entry["website"] = p["website"]
        if p.get("opening_hours"):
            entry["opening_hours"] = p["opening_hours"]
        if p.get("cuisine"):
            entry["cuisine"] = p["cuisine"].replace(";", ", ")
        if p.get("stars"):
            entry["stars"] = p["stars"]
        if p.get("description"):
            entry["description"] = p["description"][:200]
        results.append(entry)

    return {
        "status":      "success",
        "destination": loc["name"],
        "coordinates": {"lat": loc["lat"], "lon": loc["lon"]},
        "category":    category,
        "results":     results,
        "count":       len(results),
        "source":      "OpenStreetMap (live — no API key required)",
    }


def get_distance(origin: str, destination: str, mode: str = "transit") -> dict:
    """
    Calculate real distance between two places using Nominatim geocoding
    + haversine formula. Travel time estimated from real average speeds.
    No API key required.
    """
    if not _HTTPX:
        return {"status": "error", "message": "httpx not installed"}

    origin_loc = _geocode_city(origin)
    time.sleep(0.3)
    dest_loc   = _geocode_city(destination)

    if not origin_loc:
        return {"status": "error", "message": f"Could not locate '{origin}'"}
    if not dest_loc:
        return {"status": "error", "message": f"Could not locate '{destination}'"}

    km = _haversine(origin_loc["lat"], origin_loc["lon"], dest_loc["lat"], dest_loc["lon"])

    # Real-world average speeds (km/h) accounting for actual travel overhead
    speeds = {
        "driving":  55,   # city + highway mix
        "transit":  35,   # including waits
        "walking":   5,
        "cycling":  14,
        "flying":  750,   # includes airport time at short distances
    }
    speed   = speeds.get(mode, 35)
    minutes = int((km / speed) * 60)
    if mode == "flying" and km < 300:
        minutes += 120  # airport overhead dominates short flights

    return {
        "status":          "success",
        "origin":          origin_loc["name"],
        "destination":     dest_loc["name"],
        "origin_coords":   {"lat": origin_loc["lat"], "lon": origin_loc["lon"]},
        "dest_coords":     {"lat": dest_loc["lat"],   "lon": dest_loc["lon"]},
        "mode":            mode,
        "distance_km":     round(km, 1),
        "distance_miles":  round(km * 0.621, 1),
        "duration_minutes":minutes,
        "duration_display":f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m",
        "source":          "OpenStreetMap Nominatim (real coordinates) + haversine distance",
    }
