"""
Travel advisory tool.

Fetches safety and travel advisory information from:
  1. travel-advisory.info (free, no-key public API — aggregates US, UK, AU, CA, DE advisories)
  2. Offline risk-level fallback for common destinations

Advisory levels follow the US State Dept scale:
  1 = Exercise Normal Precautions
  2 = Exercise Increased Caution
  3 = Reconsider Travel
  4 = Do Not Travel
"""

import logging
from typing import Optional

import httpx

from tools.cache import ttl_cache

log = logging.getLogger("travel_agent.tools.advisory")

_ADVISORY_API = "https://www.travel-advisory.info/api"

# Offline snapshot of advisory levels for popular destinations
# (updated periodically — live API is always preferred)
_OFFLINE_LEVELS: dict[str, dict] = {
    "TH": {"level": 1, "message": "Exercise normal precautions. Political protests can occur; avoid demonstrations."},
    "JP": {"level": 1, "message": "Exercise normal precautions. Be aware of earthquake risk."},
    "VN": {"level": 1, "message": "Exercise normal precautions."},
    "ID": {"level": 2, "message": "Exercise increased caution. Terrorism risk; some regions have natural disaster risk."},
    "IN": {"level": 2, "message": "Exercise increased caution. High crime in some cities; terrorism risk in border areas."},
    "CN": {"level": 2, "message": "Exercise increased caution. Arbitrary enforcement of local laws."},
    "MX": {"level": 2, "message": "Exercise increased caution. Crime and kidnapping are widespread in some states. Check state-level advisories."},
    "MA": {"level": 1, "message": "Exercise normal precautions."},
    "EG": {"level": 2, "message": "Exercise increased caution. Terrorism risk, particularly in Sinai Peninsula."},
    "TR": {"level": 2, "message": "Exercise increased caution. Terrorism and arbitrary detentions have occurred."},
    "JO": {"level": 2, "message": "Exercise increased caution. Terrorism risk near Syrian/Iraqi borders."},
    "AE": {"level": 1, "message": "Exercise normal precautions."},
    "SG": {"level": 1, "message": "Exercise normal precautions."},
    "MY": {"level": 1, "message": "Exercise normal precautions."},
    "KH": {"level": 1, "message": "Exercise normal precautions. Petty crime is common in tourist areas."},
    "NP": {"level": 1, "message": "Exercise normal precautions. Trekking in remote areas requires permits and preparation."},
    "LK": {"level": 1, "message": "Exercise normal precautions. Economic recovery underway."},
    "BR": {"level": 2, "message": "Exercise increased caution. High crime and gang activity in some cities."},
    "AR": {"level": 1, "message": "Exercise normal precautions."},
    "CO": {"level": 2, "message": "Exercise increased caution. Terrorism and crime risk, particularly in border regions."},
    "PE": {"level": 2, "message": "Exercise increased caution. Crime and political instability in some regions."},
    "CU": {"level": 2, "message": "Exercise increased caution. Limited medical facilities; civil unrest risk."},
    "ZA": {"level": 2, "message": "Exercise increased caution. High crime rate including violent crime."},
    "KE": {"level": 2, "message": "Exercise increased caution. Terrorism risk near Somalia border; crime in Nairobi."},
    "TZ": {"level": 1, "message": "Exercise normal precautions. Take care in Zanzibar and border regions."},
    "RU": {"level": 4, "message": "Do Not Travel. Ongoing conflict; arbitrary detention of US nationals."},
    "UA": {"level": 4, "message": "Do Not Travel. Active armed conflict."},
    "AF": {"level": 4, "message": "Do Not Travel. Terrorism, civil unrest, armed conflict."},
    "SY": {"level": 4, "message": "Do Not Travel. Active armed conflict, terrorism, kidnapping."},
    "IQ": {"level": 4, "message": "Do Not Travel. Terrorism, kidnapping, armed conflict."},
    "YE": {"level": 4, "message": "Do Not Travel. Armed conflict, terrorism, kidnapping."},
    "BY": {"level": 4, "message": "Do Not Travel. Arbitrary arrest; Lukashenko regime risk for foreign nationals."},
    "MM": {"level": 3, "message": "Reconsider Travel. Military coup, civil unrest, arbitrary detention risk."},
    "PK": {"level": 3, "message": "Reconsider Travel. Terrorism risk, particularly in border regions."},
    "NG": {"level": 3, "message": "Reconsider Travel. Crime, terrorism, kidnapping."},
    "ET": {"level": 3, "message": "Reconsider Travel. Civil conflict in northern regions."},
    "SD": {"level": 4, "message": "Do Not Travel. Active armed conflict."},
    "LY": {"level": 4, "message": "Do Not Travel. Civil conflict, terrorism, kidnapping."},
    "SO": {"level": 4, "message": "Do Not Travel. Terrorism, kidnapping, piracy."},
    "CD": {"level": 3, "message": "Reconsider Travel. Armed conflict in eastern DRC."},
    "IS": {"level": 1, "message": "Exercise normal precautions. Monitor volcanic and geothermal activity."},
    "NO": {"level": 1, "message": "Exercise normal precautions."},
    "CH": {"level": 1, "message": "Exercise normal precautions."},
    "AU": {"level": 1, "message": "Exercise normal precautions. Bush fire season Dec–Mar."},
    "NZ": {"level": 1, "message": "Exercise normal precautions. Earthquake and volcanic risk."},
    "US": {"level": 1, "message": "Exercise normal precautions."},
    "GB": {"level": 1, "message": "Exercise normal precautions. Terrorism threat level varies."},
    "FR": {"level": 1, "message": "Exercise normal precautions. Pickpocketing common in tourist areas."},
    "IT": {"level": 1, "message": "Exercise normal precautions. Pickpocketing common in tourist areas."},
    "ES": {"level": 1, "message": "Exercise normal precautions. Terrorism threat exists."},
    "GR": {"level": 1, "message": "Exercise normal precautions."},
    "PT": {"level": 1, "message": "Exercise normal precautions."},
    "DE": {"level": 1, "message": "Exercise normal precautions."},
}

_LEVEL_LABELS = {
    1: "Exercise Normal Precautions",
    2: "Exercise Increased Caution",
    3: "Reconsider Travel",
    4: "Do Not Travel",
}

_LEVEL_COLORS = {
    1: "green",
    2: "yellow",
    3: "orange",
    4: "red",
}

# Destination name → ISO alpha-2
_DEST_ISO = {
    "thailand": "TH", "japan": "JP", "vietnam": "VN", "indonesia": "ID",
    "india": "IN", "china": "CN", "russia": "RU", "brazil": "BR",
    "mexico": "MX", "morocco": "MA", "egypt": "EG", "turkey": "TR",
    "jordan": "JO", "uae": "AE", "dubai": "AE",
    "united arab emirates": "AE",
    "singapore": "SG", "malaysia": "MY", "cambodia": "KH",
    "nepal": "NP", "sri lanka": "LK", "maldives": "MV",
    "argentina": "AR", "colombia": "CO", "peru": "PE", "chile": "CL",
    "cuba": "CU", "south africa": "ZA", "kenya": "KE", "tanzania": "TZ",
    "ukraine": "UA", "myanmar": "MM", "burma": "MM",
    "pakistan": "PK", "nigeria": "NG", "ethiopia": "ET",
    "australia": "AU", "new zealand": "NZ", "united states": "US",
    "usa": "US", "united kingdom": "GB", "uk": "GB",
    "france": "FR", "italy": "IT", "spain": "ES", "germany": "DE",
    "greece": "GR", "portugal": "PT", "iceland": "IS",
    "norway": "NO", "switzerland": "CH",
    # Cities → country
    "bangkok": "TH", "phuket": "TH", "chiang mai": "TH",
    "bali": "ID", "jakarta": "ID",
    "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
    "ho chi minh": "VN", "hanoi": "VN",
    "paris": "FR", "rome": "IT", "barcelona": "ES", "lisbon": "PT",
    "athens": "GR", "istanbul": "TR",
    "cancun": "MX", "mexico city": "MX",
    "havana": "CU", "buenos aires": "AR",
    "cape town": "ZA", "nairobi": "KE",
    "kathmandu": "NP", "colombo": "LK",
    "abu dhabi": "AE",
}


def _resolve_iso(destination: str) -> Optional[str]:
    key = destination.strip().lower()
    if len(key) == 2:
        return key.upper()
    return _DEST_ISO.get(key)


@ttl_cache(ttl=3600)  # Cache for 1 hour — advisories change infrequently
def get_travel_advisory(destination: str, passport_country: str = "US") -> dict:
    """
    Retrieve the current travel advisory / safety level for a destination.

    Args:
        destination: Country or city name (e.g. "Mexico", "Bangkok")
        passport_country: Traveler's passport country for context (e.g. "US")

    Returns:
        dict with advisory_level (1–4), label, message, sources, and practical safety tips
    """
    iso = _resolve_iso(destination)

    result: dict = {
        "destination": destination,
        "destination_iso": iso,
        "advisory_level": None,
        "level_label": None,
        "level_color": None,
        "message": None,
        "sources": [],
        "practical_tips": [],
        "source": "fallback",
    }

    # ── 1. Try live travel-advisory.info API (free, aggregates 10 gov sources) ──
    if iso:
        try:
            resp = httpx.get(
                _ADVISORY_API,
                params={"countrycode": iso},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                country_data = data.get("data", {}).get(iso, {})
                if country_data:
                    advisory = country_data.get("advisory", {})
                    score = advisory.get("score")  # 0–5 float
                    message = advisory.get("message", "")
                    sources_list = advisory.get("sources_active", [])

                    # Map 0–5 score to 1–4 level
                    if score is not None:
                        if score < 2.0:
                            level = 1
                        elif score < 3.0:
                            level = 2
                        elif score < 4.0:
                            level = 3
                        else:
                            level = 4
                        result["advisory_level"] = level
                        result["level_label"] = _LEVEL_LABELS[level]
                        result["level_color"] = _LEVEL_COLORS[level]

                    if message:
                        result["message"] = message
                    result["sources"] = sources_list[:5]  # top 5 sources
                    result["source"] = "travel-advisory.info"
                    log.info("advisory API ok: %s level=%s", iso, result.get("advisory_level"))
        except Exception as e:
            log.debug("advisory API failed: %s", e)

    # ── 2. Fill from offline snapshot if API didn't return data ────────────
    if result["advisory_level"] is None and iso and iso in _OFFLINE_LEVELS:
        offline = _OFFLINE_LEVELS[iso]
        result["advisory_level"] = offline["level"]
        result["level_label"] = _LEVEL_LABELS[offline["level"]]
        result["level_color"] = _LEVEL_COLORS[offline["level"]]
        result["message"] = result["message"] or offline["message"]
        result["source"] = "offline_snapshot"

    # ── 3. Always add official source links ─────────────────────────────────
    result["official_sources"] = [
        {
            "country": "US",
            "name": "US State Department",
            "url": f"https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/{(destination or 'world').lower().replace(' ', '-')}.html",
        },
        {
            "country": "UK",
            "name": "UK Foreign, Commonwealth & Development Office",
            "url": f"https://www.gov.uk/foreign-travel-advice/{(destination or 'world').lower().replace(' ', '-')}",
        },
        {
            "country": "AU",
            "name": "Australian Smartraveller",
            "url": f"https://www.smartraveller.gov.au/destinations/{(destination or 'world').lower().replace(' ', '-')}",
        },
    ]

    # ── 4. Add practical safety tips based on level ─────────────────────────
    level = result.get("advisory_level", 1)
    tips: list[str] = []

    if level == 1:
        tips = [
            "Register your trip with your country's embassy (takes 2 minutes, helps in emergencies).",
            "Keep digital copies of your passport, insurance, and bookings.",
            "Share your itinerary with someone at home.",
            "Standard travel insurance is sufficient for this destination.",
        ]
    elif level == 2:
        tips = [
            "Buy comprehensive travel insurance before departure.",
            "Register with your embassy on arrival.",
            "Avoid protests, crowds, and politically sensitive areas.",
            "Use reputable transport — avoid unmarked taxis.",
            "Keep valuables in hotel safes; use anti-theft bags in public.",
            "Research your specific regions — risk varies significantly within the country.",
            "Keep digital + printed copies of all documents.",
        ]
    elif level == 3:
        tips = [
            "Seriously reconsider whether this trip is necessary.",
            "If travelling, get comprehensive evacuation insurance.",
            "Register with your embassy BEFORE travel.",
            "Identify the nearest embassy/consulate and their emergency number.",
            "Avoid non-essential travel outside major cities.",
            "Keep a low profile. Do not announce travel plans publicly.",
            "Have a clear emergency exit plan.",
        ]
    elif level == 4:
        tips = [
            "This destination is currently at the highest safety risk level.",
            "Most governments strongly advise against all travel.",
            "If you must travel, consult your government's embassy for specific guidance.",
            "Ensure evacuation insurance covers conflict zones.",
            "Register with your embassy immediately upon arrival.",
            "Have an emergency evacuation plan and contacts.",
        ]

    result["practical_tips"] = tips

    if result["advisory_level"] is None:
        result["message"] = (
            f"No advisory data found for {destination}. "
            "Check your government's travel advisory website directly."
        )
        result["advisory_level"] = 2  # default to caution when unknown
        result["level_label"] = "Check Official Sources"
        result["level_color"] = "yellow"

    result["disclaimer"] = (
        "Advisory levels change rapidly. Always check your government's official travel advisory "
        "website immediately before departure."
    )

    return result
