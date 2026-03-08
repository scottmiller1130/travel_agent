"""
Packing list generator.

Builds a context-aware packing list from trip data: duration, weather,
activities, traveler profile, and destination type.
"""

import logging
from typing import Optional

log = logging.getLogger("travel_agent.tools.packing")

# Base items every traveler needs regardless of trip type
_ALWAYS_PACK = [
    "Passport (+ photocopy stored separately)",
    "Travel insurance documents",
    "Flight/accommodation confirmations (printed or offline)",
    "Credit/debit cards + some local cash",
    "Phone + charger",
    "Universal power adapter",
    "Medications (+ extra supply)",
    "Basic first aid (pain reliever, anti-diarrheal, blister pads)",
    "Sunscreen SPF 30+",
    "Lip balm with SPF",
    "Hand sanitizer",
    "Reusable water bottle",
    "Daypack / small backpack",
]

# Clothing base (adjustable by duration and climate)
_CLOTHING_BASE = {
    "warm": [
        "Lightweight t-shirts / tops",
        "Shorts or light trousers",
        "Sandals or breathable shoes",
        "Light rain jacket or packable poncho",
        "Swimwear",
        "Underwear (1 per day + 2 spare)",
        "Socks (1 per day + 2 spare)",
        "Comfortable walking shoes",
        "Light scarf (doubles as wrap for temples)",
    ],
    "mild": [
        "T-shirts / tops",
        "Jeans or versatile trousers",
        "Light jacket or fleece",
        "Comfortable walking shoes / sneakers",
        "Underwear (1 per day + 2 spare)",
        "Socks (1 per day + 2 spare)",
        "Layers for cool evenings",
        "Light rain jacket",
    ],
    "cold": [
        "Thermal base layers (top + bottom)",
        "Warm sweater or mid-layer fleece",
        "Heavy winter coat or down jacket",
        "Waterproof outer layer",
        "Warm trousers / jeans",
        "Thermal socks (merino wool recommended)",
        "Gloves and winter hat / beanie",
        "Scarf",
        "Waterproof boots or insulated shoes",
        "Underwear (1 per day + 2 spare)",
    ],
}

# Activity-specific additions
_ACTIVITY_EXTRAS = {
    "hiking": [
        "Trekking boots (broken in before trip)",
        "Moisture-wicking hiking socks",
        "Trekking poles",
        "Trail snacks / energy bars",
        "Blister plasters",
        "Headlamp + spare batteries",
        "Compass or GPS device",
        "Emergency whistle",
        "High-SPF sunscreen",
        "Insect repellent (DEET or picaridin)",
    ],
    "beach": [
        "Beach towel (microfibre saves space)",
        "Multiple swimwear sets",
        "Reef-safe sunscreen SPF 50+",
        "Sun hat with brim",
        "Waterproof phone case / dry bag",
        "Snorkel set (if snorkelling planned)",
        "Flip flops",
        "After-sun lotion / aloe vera gel",
    ],
    "city": [
        "Stylish but comfortable walking shoes",
        "Smart-casual outfit for restaurants",
        "Compact umbrella",
        "Crossbody bag or anti-theft backpack",
        "Portable phone charger / power bank",
        "City maps offline (download before flying)",
    ],
    "adventure": [
        "Quick-dry clothing",
        "Insect repellent (DEET or picaridin)",
        "Water purification tablets or filter",
        "Emergency space blanket",
        "Multi-tool or Swiss army knife",
        "Waterproof dry bags",
        "Headlamp + spare batteries",
        "First aid kit (more comprehensive)",
    ],
    "business": [
        "Business formal / smart attire",
        "Dress shoes (polished)",
        "Laptop + charger + adapters",
        "Business cards",
        "Portable battery pack",
        "Wrinkle-resistant garment bag",
        "Travel iron or steamer",
    ],
    "skiing": [
        "Ski jacket + ski trousers (or rent on-site)",
        "Base layers (merino wool)",
        "Ski socks (no cotton)",
        "Ski helmet (or rent)",
        "Ski goggles",
        "Neck gaiter / balaclava",
        "Hand warmers",
        "High-SPF lip balm + face sunscreen",
        "Après-ski boots / warm casual shoes",
    ],
}

# Health & toiletries
_TOILETRIES = [
    "Toothbrush + toothpaste",
    "Deodorant",
    "Shampoo + conditioner (travel size)",
    "Body wash or soap",
    "Face moisturiser",
    "Razor",
    "Feminine hygiene products (if applicable)",
    "Contact lenses + solution (if applicable)",
    "Glasses + case",
]

# Traveler profile extras
_PROFILE_EXTRAS = {
    "adventure": [
        "Padlock for hostel lockers",
        "Money belt or hidden pouch",
        "Microfibre towel",
        "Earplugs (for dorms / overnight transport)",
        "Sleep sheet / liner (for hostels)",
        "Lightweight laundry detergent (for hand washing)",
        "Clothes pegs",
        "Duct tape (small roll — fixes everything)",
        "Spare SIM card or portable WiFi",
    ],
    "luxury": [
        "Formal / smart evening wear",
        "Dress shoes",
        "Portable clothes steamer",
        "Premium skincare / toiletries",
        "Silk sleep mask",
        "Noise-cancelling headphones",
        "Premium luggage locks",
    ],
    "mid_range": [
        "Portable door alarm (for added security)",
        "Small umbrella",
        "Reusable shopping bag",
        "Portable WiFi or local SIM plan",
    ],
}

# Duration-based multipliers for clothing quantities
def _clothing_count_note(days: int) -> str:
    if days <= 3:
        return f"Pack for {days} days — no laundry needed for a short trip."
    elif days <= 7:
        return "Pack for 4–5 days; plan one mid-trip laundry or hand-wash."
    elif days <= 14:
        return "Pack for 5–7 days; 1–2 laundry trips recommended. Most hostels and hotels offer laundry."
    else:
        return (
            f"For a {days}+ day trip, pack light and plan regular laundry. "
            "Quick-dry fabrics are your best friend — 5 days of clothes maximum."
        )


def generate_packing_list(
    destination: str,
    duration_days: int,
    climate: str = "mild",
    activities: Optional[list] = None,
    traveler_profile: str = "mid_range",
    trip_type: Optional[str] = None,
) -> dict:
    """
    Generate a context-aware packing list.

    Args:
        destination: Trip destination (e.g. "Thailand", "Swiss Alps")
        duration_days: Length of trip in days
        climate: "warm" | "mild" | "cold" | "tropical" | "desert" | "snowy"
        activities: List of planned activity types (e.g. ["hiking", "beach", "city"])
        traveler_profile: "adventure" | "mid_range" | "luxury"
        trip_type: Optional trip type hint (e.g. "business", "honeymoon", "family")

    Returns:
        dict with categorised packing list and packing tips
    """
    activities = activities or []
    climate_key = "warm" if climate in ("tropical", "desert", "warm") else (
        "cold" if climate in ("cold", "snowy", "arctic") else "mild"
    )

    # Build list by category
    essentials = list(_ALWAYS_PACK)
    clothing = list(_CLOTHING_BASE.get(climate_key, _CLOTHING_BASE["mild"]))
    activity_gear: list[str] = []
    toiletries = list(_TOILETRIES)
    extras: list[str] = []

    # Add activity-specific items (deduplicate)
    seen: set[str] = set()
    for activity in activities:
        key = activity.lower()
        for k, items in _ACTIVITY_EXTRAS.items():
            if k in key or key in k:
                for item in items:
                    if item not in seen:
                        activity_gear.append(item)
                        seen.add(item)

    # Always add city items for city-heavy trips
    if not activities or "city" in " ".join(activities).lower():
        for item in _ACTIVITY_EXTRAS["city"]:
            if item not in seen:
                activity_gear.append(item)
                seen.add(item)

    # Traveler profile extras
    profile_key = traveler_profile if traveler_profile in _PROFILE_EXTRAS else "mid_range"
    extras.extend(_PROFILE_EXTRAS[profile_key])

    # Business trip overrides
    if trip_type == "business" or "business" in activities:
        for item in _ACTIVITY_EXTRAS["business"]:
            if item not in seen:
                extras.append(item)
                seen.add(item)

    # Honeymoon / romantic extras
    if trip_type in ("honeymoon", "romantic"):
        extras += [
            "Smart evening outfits (2–3)",
            "Perfume / cologne",
            "Camera or upgraded phone for memories",
        ]

    # Family extras
    if trip_type == "family":
        extras += [
            "Child medications (if applicable)",
            "Snacks for travel days",
            "Small toys / entertainment for kids",
            "Baby carrier or compact stroller (if applicable)",
            "Child sunscreen SPF 50+",
        ]

    # Warm climate: remind about modest dress for temples
    if climate_key == "warm" and destination.lower() not in ("maldives", "ibiza", "cancun"):
        clothing.append("Lightweight long trousers or skirt (for temple/mosque visits)")

    # Cold: add hand/foot warmers
    if climate_key == "cold":
        extras.append("Disposable hand warmers")
        extras.append("Lip balm (cold air causes chapping)")

    clothing_note = _clothing_count_note(duration_days)

    # Pack light tips
    packing_tips = [
        "Roll clothes instead of folding to save 20–30% space.",
        "Pack shoes on the bottom of your bag (heaviest items low and central).",
        "Use packing cubes to compress and organise by category.",
        "Leave one third of your bag empty — you'll always buy things.",
        clothing_note,
        "Photograph your passport, insurance, and booking confirmations. Save offline AND email to yourself.",
    ]

    if traveler_profile == "adventure":
        packing_tips.append(
            "Rule of thumb: if you're unsure whether you need it, you don't. Hostels and local shops cover the gaps."
        )
    elif traveler_profile == "luxury":
        packing_tips.append(
            "Your hotel concierge can arrange last-minute items. Pack light and let them handle the rest."
        )

    return {
        "status": "success",
        "destination": destination,
        "duration_days": duration_days,
        "climate": climate,
        "traveler_profile": traveler_profile,
        "packing_list": {
            "essentials": essentials,
            "clothing": clothing,
            "activity_gear": activity_gear,
            "toiletries": toiletries,
            "extras": extras,
        },
        "packing_tips": packing_tips,
        "total_items": (
            len(essentials) + len(clothing) + len(activity_gear)
            + len(toiletries) + len(extras)
        ),
    }
