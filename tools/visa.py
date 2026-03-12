"""
Visa & entry requirements tool.

Uses the free Passport Index API (no key required) as primary source,
with a structured fallback containing common visa policies.
"""

import logging
from typing import Optional

import httpx

from tools.cache import ttl_cache

log = logging.getLogger("travel_agent.tools.visa")

# Passport Index open API — returns visa requirement between two countries
_PASSPORT_API = "https://rough-sun-2523.fly.dev/api/{passport}/{destination}"

# Common visa-on-arrival / e-visa / free countries for popular passports
# (US, UK, EU, AU, CA) — used as offline fallback
_FALLBACK_NOTES = {
    "JP": "Most Western passports: 90 days visa-free. No tourist visa required.",
    "VN": "US/EU/UK/AU/CA: 45-day e-visa ($25). Apply at evisa.xuatnhapcanh.gov.vn.",
    "ID": "Most Western passports: 30-day visa-on-arrival (~$35) or free 30-day VOA at major airports.",
    "IN": "e-Visa required for most nationalities. Apply at indianvisaonline.gov.in. Usually approved in 72h.",
    "CN": "Visa required for most nationalities. Apply at Chinese embassy/consulate. Allow 2–4 weeks.",
    "RU": "Visa required for most nationalities. e-Visa available for some; single-entry 16-day limit.",
    "BR": "US/EU/UK: Visa-free up to 90 days. AU/CA: e-Visa required.",
    "MX": "Most Western passports: 180-day visa-free. No advance visa needed.",
    "MA": "Most Western passports: 90-day visa-free.",
    "EG": "Visa-on-arrival ($25) or e-Visa at visa2egypt.gov.eg. 30 days, extendable.",
    "KE": "e-Visa required. Apply at evisa.go.ke. ~$51 single entry.",
    "TZ": "e-Visa or visa-on-arrival. Apply at eservices.immigration.go.tz.",
    "ZA": "Most Western passports: 90-day visa-free.",
    "AU": "Visa or ETA required for most non-NZ passports. Australian ETA: AUD $20 via app.",
    "NZ": "NZeTA required for visa-waiver countries. NZD $17 online.",
    "US": "ESTA required for VWP countries ($21, valid 2 years). Others need B-1/B-2 visa.",
    "GB": "ETA required from 2025 for visa-waiver nationals (£10). Check gov.uk.",
    "FR": "Schengen: 90 days in any 180-day period for most Western passports.",
    "DE": "Schengen: 90 days in any 180-day period for most Western passports.",
    "ES": "Schengen: 90 days in any 180-day period for most Western passports.",
    "IT": "Schengen: 90 days in any 180-day period for most Western passports.",
    "GR": "Schengen: 90 days in any 180-day period for most Western passports.",
    "PT": "Schengen: 90 days in any 180-day period for most Western passports.",
    "TR": "Most Western passports: 90-day e-Visa (~$50) at evisa.gov.tr.",
    "JO": "Visa-on-arrival (~JOD 40) or free via Aqaba entry. Jordan Pass waives fee.",
    "AE": "Most Western passports: 30-day visa-on-arrival (free). Extendable.",
    "TH": "Most Western passports: 60-day e-Visa (~$35) or 30-day VOA. Book at tp.consular.go.th.",
    "SG": "Most Western passports: 30-day visa-free.",
    "MY": "Most Western passports: 90-day visa-free.",
    "PH": "Most Western passports: 30-day visa-free (extendable to 59 days at BI).",
    "KH": "e-Visa ($36) at evisa.gov.kh or visa-on-arrival ($30). 30 days.",
    "MM": "e-Visa required (~$50). Apply at evisa.moip.gov.mm.",
    "LA": "Visa-on-arrival ($30–$42) or e-Visa. 30 days.",
    "NP": "Visa-on-arrival at Kathmandu airport or land borders. 15/30/90 days.",
    "LK": "ETA required ($50). Apply at eta.gov.lk.",
    "MV": "Free 30-day visa-on-arrival for all nationalities.",
    "AR": "Most Western passports: 90-day visa-free.",
    "PE": "Most Western passports: 183-day visa-free.",
    "CO": "Most Western passports: 90-day visa-free (180 days/year).",
    "CL": "Most Western passports: 90-day visa-free.",
    "CU": "Tourist card required (~$25–$50). Check with airline — often sold at check-in.",
    "IS": "Schengen: 90 days in any 180-day period.",
    "NO": "Schengen: 90 days in any 180-day period.",
    "CH": "Schengen: 90 days in any 180-day period.",
}

# ISO alpha-2 country code lookup for common destination names
_COUNTRY_TO_ISO = {
    "thailand": "TH", "japan": "JP", "vietnam": "VN", "indonesia": "ID",
    "india": "IN", "china": "CN", "russia": "RU", "brazil": "BR",
    "mexico": "MX", "morocco": "MA", "egypt": "EG", "kenya": "KE",
    "tanzania": "TZ", "south africa": "ZA", "australia": "AU",
    "new zealand": "NZ", "united states": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB", "france": "FR", "germany": "DE",
    "spain": "ES", "italy": "IT", "greece": "GR", "portugal": "PT",
    "turkey": "TR", "jordan": "JO", "uae": "AE",
    "united arab emirates": "AE", "dubai": "AE", "singapore": "SG",
    "malaysia": "MY", "philippines": "PH", "cambodia": "KH",
    "myanmar": "MM", "burma": "MM", "laos": "LA", "nepal": "NP",
    "sri lanka": "LK", "maldives": "MV", "argentina": "AR",
    "peru": "PE", "colombia": "CO", "chile": "CL", "cuba": "CU",
    "iceland": "IS", "norway": "NO", "switzerland": "CH",
    "bali": "ID", "phuket": "TH", "chiang mai": "TH",
    "ho chi minh": "VN", "hanoi": "VN", "bangkok": "TH",
    "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
    "paris": "FR", "rome": "IT", "barcelona": "ES", "lisbon": "PT",
    "athens": "GR", "istanbul": "TR",
    "cancun": "MX", "havana": "CU", "buenos aires": "AR",
    "cape town": "ZA", "nairobi": "KE",
}

# Passport ISO codes for common nationalities
_PASSPORT_ISO = {
    "american": "US", "us": "US", "united states": "US",
    "british": "GB", "uk": "GB", "english": "GB",
    "australian": "AU", "canadian": "CA", "ca": "CA",
    "european": "EU", "german": "DE", "french": "FR",
    "italian": "IT", "spanish": "ES",
    "japanese": "JP", "chinese": "CN", "indian": "IN",
    "brazilian": "BR", "mexican": "MX",
    "new zealand": "NZ", "kiwi": "NZ",
    "south african": "ZA",
    "singaporean": "SG", "malaysian": "MY",
    "swedish": "SE", "danish": "DK", "dutch": "NL", "norwegian": "NO",
    "swiss": "CH", "austrian": "AT", "belgian": "BE",
}


def _resolve_country(name: str) -> Optional[str]:
    """Return ISO alpha-2 code from a country or city name."""
    key = name.strip().lower()
    if key.upper() == key and len(key) == 2:
        return key.upper()
    return _COUNTRY_TO_ISO.get(key)


def _resolve_passport(name: str) -> Optional[str]:
    """Return ISO alpha-2 passport code from a nationality string."""
    key = name.strip().lower()
    if key.upper() == key and len(key) == 2:
        return key.upper()
    return _PASSPORT_ISO.get(key)


@ttl_cache(ttl=86400)  # Cache for 24 hours — visa rules change infrequently
def get_visa_requirements(
    destination: str,
    passport_country: str = "US",
    trip_purpose: str = "tourism",
) -> dict:
    """
    Return visa and entry requirements for a destination.

    Args:
        destination: Country or city name (e.g. "Thailand", "Tokyo")
        passport_country: Traveler's passport nationality (e.g. "US", "British", "Australian")
        trip_purpose: "tourism" | "business" | "transit"

    Returns:
        dict with visa_required, visa_type, duration_days, notes, source, official_link
    """
    dest_iso = _resolve_country(destination)
    passport_iso = _resolve_passport(passport_country) or passport_country.upper()[:2]

    result: dict = {
        "destination": destination,
        "destination_iso": dest_iso,
        "passport": passport_country,
        "passport_iso": passport_iso,
        "trip_purpose": trip_purpose,
        "visa_required": None,
        "visa_type": None,
        "duration_days": None,
        "cost_usd": None,
        "processing_time": None,
        "notes": None,
        "official_link": None,
        "source": "fallback",
    }

    # ── 1. Try live Passport Index API ──────────────────────────────────────
    if dest_iso and passport_iso:
        try:
            url = _PASSPORT_API.format(
                passport=passport_iso.lower(),
                destination=dest_iso.lower(),
            )
            resp = httpx.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                # API returns {"Visa": "visa free", "Duration": 90} etc.
                visa_val = (data.get("Visa") or "").lower()
                duration = data.get("Duration")

                if "free" in visa_val or "no visa" in visa_val:
                    result["visa_required"] = False
                    result["visa_type"] = "visa_free"
                elif "on arrival" in visa_val or "voa" in visa_val:
                    result["visa_required"] = False
                    result["visa_type"] = "visa_on_arrival"
                elif "e-visa" in visa_val or "evisa" in visa_val:
                    result["visa_required"] = True
                    result["visa_type"] = "e_visa"
                elif "required" in visa_val or visa_val == "visa":
                    result["visa_required"] = True
                    result["visa_type"] = "embassy_visa"
                else:
                    result["visa_type"] = visa_val or "check_required"

                if duration:
                    try:
                        result["duration_days"] = int(duration)
                    except (ValueError, TypeError):
                        pass

                result["source"] = "passport_index_api"
                log.info("visa API ok: %s → %s = %s", passport_iso, dest_iso, visa_val)
        except Exception as e:
            log.debug("visa API failed: %s", e)

    # ── 2. Enrich with offline notes ─────────────────────────────────────────
    if dest_iso and dest_iso in _FALLBACK_NOTES:
        result["notes"] = _FALLBACK_NOTES[dest_iso]
        if result["source"] == "fallback":
            result["source"] = "offline_notes"

    # ── 3. Add official links ─────────────────────────────────────────────────
    if dest_iso:
        # IATA Timatic (industry standard, free lookup for travellers)
        result["official_link"] = (
            "https://www.iatatravelcentre.com/passport-visa-health-travel-document-requirements.htm"
        )
        # Country-specific overrides
        _links = {
            "IN": "https://indianvisaonline.gov.in",
            "VN": "https://evisa.xuatnhapcanh.gov.vn",
            "TR": "https://www.evisa.gov.tr",
            "EG": "https://visa2egypt.gov.eg",
            "KE": "https://evisa.go.ke",
            "TZ": "https://eservices.immigration.go.tz",
            "AU": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601",
            "NZ": "https://www.immigration.govt.nz/new-zealand-visas/visas/visa/nzeta",
            "US": "https://esta.cbp.dhs.gov",
            "GB": "https://www.gov.uk/get-uk-visa",
            "KH": "https://www.evisa.gov.kh",
            "LK": "https://eta.gov.lk",
            "MM": "https://evisa.moip.gov.mm",
        }
        if dest_iso in _links:
            result["official_link"] = _links[dest_iso]

    # ── 4. Final fallback message ─────────────────────────────────────────────
    if result["visa_required"] is None and not result["notes"]:
        result["notes"] = (
            f"Visa requirements for {destination} vary by passport. "
            "Check IATA Travel Centre or your country's foreign affairs website for up-to-date requirements."
        )

    result["disclaimer"] = (
        "Visa rules change frequently. Always verify with the official embassy or "
        "consulate of your destination before travelling."
    )
    return result
