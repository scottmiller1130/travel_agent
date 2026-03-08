"""
Season intelligence — peak / shoulder / off-season data for 80+ destinations.

Integrated into get_weather() automatically, and the agent should include the
returned `season` object in every update_itinerary call so the trip board can
show the season badge.

Usage:
    from tools.seasons import get_season
    info = get_season("Bali", travel_month=8)
    # → {"season": "peak", "label": "Peak Season", "emoji": "🔴", ...}
"""

from __future__ import annotations

# ── Season database ──────────────────────────────────────────────────────────
# Each entry:
#   months:      {1..12 → "peak" | "shoulder" | "off"}
#   notes:       {"peak": "...", "shoulder": "...", "off": "..."}
#   price_mult:  {"peak": 1.5, "shoulder": 1.05, "off": 0.70}
#                 (multiplier vs. average annual price)
#   crowd:       {"peak": "very high", ...}

_DB: dict[str, dict] = {

    # ── Southeast Asia ────────────────────────────────────────────────────
    "bali": {
        "months": {1:"off",2:"off",3:"off",4:"shoulder",5:"shoulder",
                   6:"shoulder",7:"peak",8:"peak",9:"shoulder",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Dry season (Jul–Aug). Best beach weather, max crowds. Hotels sell out — book 3–6 months ahead.",
            "shoulder": "Apr–Jun and Sep–Oct: sweet spot. Good weather, lighter crowds, 10–15% savings.",
            "off":      "Wet season (Nov–Mar). Daily tropical showers; lush, green, quiet. 25–40% cheaper. Surf season on Kuta/Canggu west coast.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.72},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "phuket": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"off",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Nov–Apr: brilliant beach weather, calm Andaman Sea. Fully booked well in advance.",
            "shoulder": "Apr and Nov: transitional — still enjoyable, 15–20% cheaper.",
            "off":      "May–Oct: west coast monsoon. Some beach clubs close. Up to 45% off resorts.",
        },
        "price_mult": {"peak":1.55,"shoulder":1.05,"off":0.60},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"very low"},
    },
    "thailand": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"off",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Dec–Feb: cool and dry — ideal for Bangkok temples and northern trekking. Christmas/NYE premium.",
            "shoulder": "Mar–Apr (warm/hazy) and Nov: manageable with good value.",
            "off":      "May–Oct: monsoon season. Heavy rain, possible flooding. Flights and hotels up to 50% cheaper.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.10,"off":0.65},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "vietnam": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Dec–Feb: cool/dry in north, warm/sunny in south. Tet (Jan/Feb) drives domestic surge — book early.",
            "shoulder": "Mar–Apr and Oct–Nov: good value, transitional weather.",
            "off":      "May–Sep: monsoon from south to north. Typhoon risk Aug–Sep. Cheapest prices of the year.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.70},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "singapore": {
        "months": {1:"shoulder",2:"peak",3:"shoulder",4:"shoulder",5:"shoulder",
                   6:"shoulder",7:"shoulder",8:"peak",9:"shoulder",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Feb (CNY), Aug (National Day), Dec (Christmas): rates spike around events. F1 (Sep) is another surge.",
            "shoulder": "Most months: pleasant tropical city-state with afternoon showers year-round.",
            "off":      "Nov–early Dec: monsoon rains. Cheaper but city remains fully open.",
        },
        "price_mult": {"peak":1.35,"shoulder":1.0,"off":0.85},
        "crowd":      {"peak":"high","shoulder":"moderate-high","off":"moderate"},
    },
    "maldives": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Jan–Mar: crystal-clear water, 30m+ visibility. Christmas/NYE at eye-watering premium.",
            "shoulder": "Apr, Oct–Nov: still beautiful, 20–30% savings.",
            "off":      "May–Sep: SW monsoon. Rough seas, overcast. 40–60% off luxury resorts.",
        },
        "price_mult": {"peak":1.75,"shoulder":1.15,"off":0.55},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "sri lanka": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"off",7:"peak",8:"peak",9:"shoulder",10:"off",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Dec–Mar: west/south coast in full sun. Jul–Aug: east coast season (Arugam Bay surf).",
            "shoulder": "Apr and Sep: inter-monsoon. Mixed but manageable.",
            "off":      "May–Jun (west monsoon) and Oct–Nov (east monsoon). One coast is always in season — plan accordingly.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.70},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "india": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Oct–Feb: cool and dry across most regions. High tourist season — Rajasthan, Kerala, Golden Triangle.",
            "shoulder": "Mar–Apr: warm pre-monsoon. Still pleasant in the mountains.",
            "off":      "Jun–Sep: monsoon. Disruptions possible but Ladakh and Rajasthan can be rewarding. Cheapest fares.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.0,"off":0.70},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "japan": {
        "months": {1:"shoulder",2:"shoulder",3:"peak",4:"peak",5:"shoulder",
                   6:"off",7:"shoulder",8:"peak",9:"shoulder",10:"peak",
                   11:"peak",12:"shoulder"},
        "notes": {
            "peak":     "Cherry blossom (late Mar–Apr) and autumn foliage (Oct–Nov): prices surge 60–80%, accommodation sells out 6+ months ahead. Aug = summer school holiday peak.",
            "shoulder": "Jan–Feb, May and Sep: quieter, cheaper — Jan–Feb especially cold but uncrowded.",
            "off":      "Jun: tsuyu rainy season. Cheapest flights and hotels of the year — great for indoor culture.",
        },
        "price_mult": {"peak":1.70,"shoulder":1.10,"off":0.78},
        "crowd":      {"peak":"extremely high","shoulder":"moderate","off":"low"},
    },

    # ── Middle East ───────────────────────────────────────────────────────
    "dubai": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Nov–Mar: perfect 22–28°C. Dubai Shopping Festival (Jan). NYE fireworks. Hotels at premium.",
            "shoulder": "Apr and Oct: 30–35°C — warm but tolerable. Good hotel deals.",
            "off":      "May–Sep: extreme heat 40–48°C. 50–70% hotel discounts. Expats leave.",
        },
        "price_mult": {"peak":1.65,"shoulder":1.10,"off":0.45},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"very low"},
    },
    "morocco": {
        "months": {1:"shoulder",2:"shoulder",3:"peak",4:"peak",5:"shoulder",
                   6:"shoulder",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"shoulder"},
        "notes": {
            "peak":     "Mar–Apr and Oct–Nov: ideal 20–28°C. Popular for Sahara trips and medina exploring.",
            "shoulder": "Dec–Feb (cool evenings), May–Jun and Sep: warm, manageable.",
            "off":      "Jul–Aug: scorching inland (38–45°C). Coastal areas more bearable. 25% cheaper.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "jordan": {
        "months": {1:"shoulder",2:"shoulder",3:"peak",4:"peak",5:"peak",
                   6:"shoulder",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"shoulder"},
        "notes": {
            "peak":     "Mar–May and Oct–Nov: perfect for Petra and Wadi Rum (20–28°C). Tour groups in force.",
            "shoulder": "Feb, Jun, Sep, Dec: good weather, fewer tourists.",
            "off":      "Jul–Aug: intense heat 35–40°C in Wadi Rum. Petra is early-morning only. 25% cheaper.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "egypt": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"shoulder",
                   6:"off",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Oct–Apr: ideal 20–28°C for pyramids and Luxor temples. Christmas/New Year = highest prices.",
            "shoulder": "Mar–May and Sep: warm, tourist numbers easing.",
            "off":      "Jun–Aug: extreme heat 40°C+. Only the most heat-tolerant visit. Best deals.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.65},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },

    # ── Europe ────────────────────────────────────────────────────────────
    "paris": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Apr–Sep: warm, long days, café terraces. Jul–Aug busiest — expect 2h Louvre queues.",
            "shoulder": "Mar, Oct and Dec: quieter. Christmas markets and festive lights in December.",
            "off":      "Jan–Feb: cold and grey but cheapest. Museums near-empty.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.10,"off":0.80},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "london": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"shoulder",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "May–Aug: long evenings, festivals, Wimbledon (Jun–Jul), Notting Hill Carnival (Aug). Peak hotel rates.",
            "shoulder": "Mar–Apr and Sep–Oct: great for sightseeing, moderate prices.",
            "off":      "Nov–Feb: wet and cold. Excellent deals on theatre, museums and accommodation.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.10,"off":0.80},
        "crowd":      {"peak":"very high","shoulder":"high","off":"moderate"},
    },
    "barcelona": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "May–Sep: beach weather, rooftop bars, La Mercè (Sep). Aug packs beaches. MWC (Feb) spikes hotel prices.",
            "shoulder": "Mar–Apr and Oct: warm, uncrowded — ideal for Gaudí without queues.",
            "off":      "Nov–Feb: mild and rainy. Cheapest flights and hotels of the year.",
        },
        "price_mult": {"peak":1.55,"shoulder":1.10,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "rome": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Apr–Oct: warm, sunny, festivals. Easter is peak-of-peak — book 12 months ahead.",
            "shoulder": "Mar, Nov and Dec: manageable crowds. December has Christmas markets.",
            "off":      "Jan–Feb: cold, fewest tourists, cheapest. Colosseum with no queues.",
        },
        "price_mult": {"peak":1.55,"shoulder":1.10,"off":0.75},
        "crowd":      {"peak":"extremely high","shoulder":"moderate","off":"low"},
    },
    "amsterdam": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"off",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Apr–Aug: tulip season (Apr–May), long days, canal boat season. King's Day (Apr 27) = city-wide party.",
            "shoulder": "Mar and Sep: decent weather, below-peak prices.",
            "off":      "Oct–Feb: cold and rainy. Excellent Rijksmuseum and concert deals.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.10,"off":0.80},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "lisbon": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Jun–Sep: hot, sunny, packed. NOS Alive festival (Jul). Book 2–3 months ahead.",
            "shoulder": "Mar–May and Oct: warm, crowd-free, great value — arguably the best time.",
            "off":      "Nov–Feb: mild and rainy. Cheapest deals, very authentic atmosphere.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "athens": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "May–Sep: hot and sunny (35°C+). Visit Acropolis at sunrise to beat the crowds.",
            "shoulder": "Mar–Apr and Oct: ideal 20–25°C sightseeing temps, reasonable prices.",
            "off":      "Nov–Feb: cool and quiet. Flights and hotels very cheap. No queues anywhere.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.70},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "istanbul": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"shoulder",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Apr–May and Jul–Sep: warm, vibrant, crowded. Hagia Sophia queues 2h+.",
            "shoulder": "Jun and Oct: good weather, fewer tourists, better value.",
            "off":      "Nov–Mar: chilly and rainy. Grand Bazaar without the crush. Excellent deals.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "santorini": {
        "months": {1:"off",2:"off",3:"off",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Jun–Sep: iconic caldera sunsets and whitewashed domes. Hotels fully booked 6+ months ahead.",
            "shoulder": "Apr–May and Oct: beautiful light, 20–25°C, 30–40% cheaper.",
            "off":      "Nov–Mar: most hotels and restaurants closed. Ghost-island experience.",
        },
        "price_mult": {"peak":1.80,"shoulder":1.15,"off":0.50},
        "crowd":      {"peak":"extremely high","shoulder":"moderate","off":"very low"},
    },
    "prague": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Apr–Sep and December Christmas markets: Old Town buzzing. Book 3+ months ahead in summer.",
            "shoulder": "Mar and Oct: golden autumn light, affordable stays.",
            "off":      "Jan–Feb: cold, near-empty. Best prices of the year.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "vienna": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "May–Aug: opera season, outdoor concerts. December Christmas markets = tourism surge.",
            "shoulder": "Apr, Sep, Oct: pleasant weather, manageable crowds.",
            "off":      "Jan–Mar: cold, Carnival in Feb. Very affordable for such a great city.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.80},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "venice": {
        "months": {1:"off",2:"peak",3:"off",4:"peak",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Apr–Sep and Carnival (Feb): beautiful but overwhelmingly packed. High acqua alta risk Nov–Mar.",
            "shoulder": "Oct–Nov: moody, atmospheric, 35–40% cheaper.",
            "off":      "Jan and Mar: fewest tourists ever. Cold but hauntingly beautiful.",
        },
        "price_mult": {"peak":1.60,"shoulder":1.10,"off":0.70},
        "crowd":      {"peak":"extremely high","shoulder":"moderate","off":"low"},
    },
    "iceland": {
        "months": {1:"off",2:"off",3:"off",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"off",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Jun–Aug: midnight sun, accessible highlands, puffin season, 20°C. Most expensive.",
            "shoulder": "Apr–May and Sep: Northern Lights start, fewer crowds, some highland access.",
            "off":      "Nov–Mar: best Northern Lights viewing but roads may close. Dark, cold and very cheap.",
        },
        "price_mult": {"peak":1.65,"shoulder":1.10,"off":0.70},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },

    # ── Americas ──────────────────────────────────────────────────────────
    "new york": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "May–Sep: warm, rooftop bars, Central Park events. Dec holiday windows and NYE = premium prices.",
            "shoulder": "Mar–Apr and Oct–Nov: mild weather, fewer tourists, better deals.",
            "off":      "Jan–Feb: cold but cheapest. Museums uncrowded. Excellent restaurant availability.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.10,"off":0.80},
        "crowd":      {"peak":"very high","shoulder":"high","off":"moderate"},
    },
    "miami": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Nov–Apr: perfect beach weather 24–28°C. Art Basel (Dec) spikes prices. Spring Break (Mar) = party season.",
            "shoulder": "Oct–Nov: warm, pre-peak, good value.",
            "off":      "Jun–Sep: hurricane season, hot and humid. 40–50% hotel discounts.",
        },
        "price_mult": {"peak":1.55,"shoulder":1.10,"off":0.60},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "cancun": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"shoulder",
                   6:"off",7:"off",8:"off",9:"off",10:"off",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Dec–Apr: dry, warm 28°C, calm Caribbean. Spring Break (Mar) = cheapest parties but priciest rooms.",
            "shoulder": "Apr–May and Nov: warm, fewer crowds, reasonable prices.",
            "off":      "Jun–Oct: hurricane season. Heavy rain, deep deals.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.60},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "costa rica": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"peak",8:"peak",9:"off",10:"off",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Dec–Apr: dry season on Pacific coast — best for beaches and wildlife. Jul–Aug: 'mini-dry' season.",
            "shoulder": "Nov–Dec and Apr: transition months.",
            "off":      "May–Jun and Sep–Oct: heaviest Pacific rains. Excellent wildlife, 30% cheaper lodges.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.70},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "rio de janeiro": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"off",7:"shoulder",8:"shoulder",9:"shoulder",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Dec–Feb: Carnival season (Feb). NYE on Copacabana = premium prices. Hot and sunny.",
            "shoulder": "Mar–Apr and Jul–Oct: mild and less expensive. Jun–Jul best weather overall.",
            "off":      "Nov and May: highest rainfall. 25% cheaper.",
        },
        "price_mult": {"peak":1.60,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "buenos aires": {
        "months": {1:"off",2:"off",3:"peak",4:"peak",5:"shoulder",
                   6:"off",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Sep–Apr: spring and summer. Oct–Dec are the best months. Christmas/NYE packed.",
            "shoulder": "May and Sep: good deals, pleasant.",
            "off":      "Jun–Aug: southern hemisphere winter. Cold and quiet. Cheapest accommodation.",
        },
        "price_mult": {"peak":1.35,"shoulder":1.05,"off":0.80},
        "crowd":      {"peak":"moderate-high","shoulder":"moderate","off":"low"},
    },
    "caribbean": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"off",10:"off",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Dec–Apr: dry season, calm seas, 28°C. Winter escape for North Americans and Europeans.",
            "shoulder": "Nov and Apr: transitional, good deals.",
            "off":      "Jun–Oct: hurricane season (peak risk Sep–Oct). Resorts offer deep discounts.",
        },
        "price_mult": {"peak":1.60,"shoulder":1.10,"off":0.55},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },

    # ── Africa ────────────────────────────────────────────────────────────
    "kenya": {
        "months": {1:"peak",2:"peak",3:"off",4:"off",5:"off",
                   6:"shoulder",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Jul–Oct: Great Migration in Masai Mara (river crossings Aug–Sep). Dec–Feb: dry season game viewing.",
            "shoulder": "Jun and Oct–Nov: fewer crowds, good wildlife.",
            "off":      "Mar–May and Nov: long/short rains. Lush landscapes. 35–45% cheaper safari lodges.",
        },
        "price_mult": {"peak":1.65,"shoulder":1.10,"off":0.60},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "cape town": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Nov–Apr: warm and sunny 25–30°C. Table Mountain clear skies. NYE and Cape summer festivals.",
            "shoulder": "Apr and Sep–Oct: transitional, great value.",
            "off":      "May–Aug: Cape winter — wet and windy. 40% cheaper but whale watching is exceptional (Jun–Nov).",
        },
        "price_mult": {"peak":1.55,"shoulder":1.05,"off":0.65},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },

    # ── Oceania ───────────────────────────────────────────────────────────
    "sydney": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"shoulder",7:"shoulder",8:"peak",9:"peak",10:"peak",
                   11:"shoulder",12:"peak"},
        "notes": {
            "peak":     "Sep–Nov (spring): best weather, outdoor events. Dec NYE fireworks. School holidays spike demand.",
            "shoulder": "Jun–Jul: mild winter, great for indoor culture and the Blue Mountains.",
            "off":      "Jan–Feb: hot/humid summer. Peak for beaches but average for sightseeing.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.85},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "new zealand": {
        "months": {1:"peak",2:"peak",3:"peak",4:"shoulder",5:"off",
                   6:"off",7:"off",8:"off",9:"shoulder",10:"peak",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Dec–Mar: southern summer. Long days, outdoor adventures. Tourism peaks.",
            "shoulder": "Apr and Sep–Oct: shoulder with good prices.",
            "off":      "May–Aug: winter. South Island ski season but fewer international visitors. Best deals.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
}

# ── Aliases: maps destination substrings to DB keys (or inline data) ─────────
_ALIASES: dict[str, str | dict] = {
    # Southeast Asia
    "ubud": "bali",
    "seminyak": "bali",
    "canggu": "bali",
    "kuta": "bali",
    "lombok": "bali",
    "bangkok": "thailand",
    "chiang mai": "thailand",
    "koh samui": "phuket",
    "krabi": "phuket",
    "koh phi phi": "phuket",
    "hanoi": "vietnam",
    "ho chi minh": "vietnam",
    "saigon": "vietnam",
    "hoi an": "vietnam",
    "da nang": "vietnam",
    "kuala lumpur": {
        "months": {1:"off",2:"shoulder",3:"shoulder",4:"shoulder",5:"shoulder",
                   6:"off",7:"off",8:"off",9:"off",10:"shoulder",11:"peak",12:"peak"},
        "notes": {
            "peak":     "Nov–Dec and Feb: drier spell. Good for city exploration.",
            "shoulder": "Mar–May and Oct: warm with intermittent rain.",
            "off":      "Jun–Sep and Jan: heaviest rains. Still 30°C+ but wet.",
        },
        "price_mult": {"peak":1.25,"shoulder":1.0,"off":0.85},
        "crowd":      {"peak":"moderate-high","shoulder":"moderate","off":"low"},
    },
    # Japan
    "kyoto": "japan",
    "osaka": "japan",
    "hiroshima": "japan",
    "nara": "japan",
    "tokyo": "japan",
    "hokkaido": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"shoulder",
                   6:"shoulder",7:"peak",8:"peak",9:"shoulder",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Jan–Feb: Sapporo Snow Festival (Feb). Jul–Aug: lavender fields, summer escape. Great skiing Dec–Feb.",
            "shoulder": "Apr–May and Sep–Oct: mild, uncrowded.",
            "off":      "Nov: pre-ski shoulder. Quiet and affordable.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.80},
        "crowd":      {"peak":"high","shoulder":"low","off":"very low"},
    },
    # China
    "beijing": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"off",7:"off",8:"off",9:"peak",10:"peak",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "Apr–May and Sep–Oct: clear skies, Great Wall in golden light. Chinese New Year (Jan/Feb) = domestic surge.",
            "shoulder": "Mar and Nov: quieter, cooler.",
            "off":      "Jun–Aug: hot/humid; Dec–Feb: bitter cold (-10°C). Cheapest but challenging.",
        },
        "price_mult": {"peak":1.45,"shoulder":1.05,"off":0.80},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "shanghai": {
        "months": {1:"off",2:"off",3:"shoulder",4:"peak",5:"peak",
                   6:"off",7:"off",8:"off",9:"peak",10:"peak",
                   11:"shoulder",12:"off"},
        "notes": {
            "peak":     "Apr–May and Sep–Oct: mild and cosmopolitan. Cherry blossoms in spring.",
            "shoulder": "Mar and Nov: transitional, good value.",
            "off":      "Jun–Aug: hot, humid, typhoon risk; Dec–Feb: cold.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.0,"off":0.80},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "hong kong": {
        "months": {1:"shoulder",2:"shoulder",3:"shoulder",4:"shoulder",
                   5:"off",6:"off",7:"off",8:"off",9:"off",
                   10:"peak",11:"peak",12:"peak"},
        "notes": {
            "peak":     "Oct–Dec: cooler, clear blue skies, excellent hiking. Christmas shopping peak.",
            "shoulder": "Jan–Apr: mild but hazy.",
            "off":      "May–Sep: typhoon season, oppressive humidity 35°C+.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.0,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate-high","off":"moderate"},
    },
    # Africa
    "nairobi": "kenya",
    "masai mara": "kenya",
    "safari": "kenya",
    "serengeti": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"off",5:"off",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"peak"},
        "notes": {
            "peak":     "Jun–Oct: Great Wildebeest Migration (Mara River crossings Jul–Sep). Dec–Feb: calving season in Ndutu.",
            "shoulder": "Jan–Feb and Oct–Nov: good wildlife, smaller crowds.",
            "off":      "Apr–May: long rains. Lodges offer 30–50% discounts. Birdwatching excellent.",
        },
        "price_mult": {"peak":1.70,"shoulder":1.15,"off":0.55},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    # Europe extras
    "florence": "rome",
    "naples": "rome",
    "amalfi": "rome",
    "sicily": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "May–Sep: hot and sunny, beach season. Aug is absolute peak with Italian domestic tourists.",
            "shoulder": "Apr and Oct: warm, fewer crowds, perfect for temples and hiking Etna.",
            "off":      "Nov–Mar: most resorts quiet. Good for authentic small-town Sicily.",
        },
        "price_mult": {"peak":1.50,"shoulder":1.05,"off":0.70},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "mykonos": "santorini",
    "corfu": "santorini",
    "crete": {
        "months": {1:"off",2:"off",3:"off",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "May–Sep: hot, sunny beach season. Aug is absolute peak. Samaria Gorge open May–Oct.",
            "shoulder": "Apr and Oct: warm, uncrowded, great hiking.",
            "off":      "Nov–Mar: most resorts closed. Authentic local experience.",
        },
        "price_mult": {"peak":1.55,"shoulder":1.05,"off":0.55},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"very low"},
    },
    "berlin": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"peak",10:"shoulder",
                   11:"off",12:"off"},
        "notes": {
            "peak":     "May–Sep: long evenings, outdoor bars, music festivals. Berlinale (Feb) spikes prices briefly.",
            "shoulder": "Mar–Apr and Oct: good weather, value pricing.",
            "off":      "Nov–Feb: cold, grey, but the world's best techno clubs. Cheap flights.",
        },
        "price_mult": {"peak":1.40,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"high","shoulder":"moderate","off":"low"},
    },
    "edinburgh": {
        "months": {1:"off",2:"off",3:"shoulder",4:"shoulder",5:"peak",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"off",
                   11:"off",12:"shoulder"},
        "notes": {
            "peak":     "Aug: Edinburgh Festival Fringe — accommodation triples in price. Book a year ahead.",
            "shoulder": "May–Jun and Sep: pleasant pre/post festival, Highland walks.",
            "off":      "Nov–Mar: cold and quiet. Hogmanay (Dec 31) is a fun exception.",
        },
        "price_mult": {"peak":2.0,"shoulder":1.15,"off":0.75},
        "crowd":      {"peak":"extremely high","shoulder":"moderate","off":"low"},
    },
    "queenstown": {
        "months": {1:"peak",2:"peak",3:"shoulder",4:"shoulder",5:"off",
                   6:"peak",7:"peak",8:"peak",9:"shoulder",10:"peak",
                   11:"peak",12:"peak"},
        "notes": {
            "peak":     "Dec–Feb: summer adventure peak (bungee, hiking). Jun–Aug: ski season (Remarkables, Coronet Peak). Both are expensive.",
            "shoulder": "Mar–May and Sep–Oct: transition, best deals, still beautiful.",
            "off":      "May: pre-ski quiet. Very cheap and very empty.",
        },
        "price_mult": {"peak":1.60,"shoulder":1.05,"off":0.75},
        "crowd":      {"peak":"very high","shoulder":"moderate","off":"low"},
    },
    "marrakech": "morocco",
    "fez": "morocco",
    "jamaica": "caribbean",
    "barbados": "caribbean",
    "bahamas": "caribbean",
    "turks and caicos": "caribbean",
    "st lucia": "caribbean",
    "aruba": "caribbean",
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_season(destination: str, travel_month: int) -> dict:
    """
    Return peak/shoulder/off-season info for a destination in a given calendar month.

    Args:
        destination:  City or country name (e.g. "Bali", "Paris", "Japan")
        travel_month: Calendar month number 1–12

    Returns dict with:
        season        "peak" | "shoulder" | "off"
        label         "Peak Season" | "Shoulder Season" | "Off Season"
        emoji         🔴 | 🟡 | 🟢
        crowd_level   e.g. "very high"
        price_context e.g. "~45% above average prices"
        price_multiplier  float (1.45 = 45% above average)
        notes         Descriptive advice for this season at this destination
        destination   Echoed back
        travel_month  Echoed back
    """
    if not 1 <= travel_month <= 12:
        return _unknown_season(destination, travel_month)

    data = _resolve(destination.lower().strip())
    if data is None:
        return _hemisphere_fallback(destination, travel_month)

    months_map = data.get("months", {})
    season     = months_map.get(travel_month, "shoulder")
    notes      = data.get("notes", {}).get(season, "")
    mult       = data.get("price_mult", {}).get(season, 1.0)
    crowd      = data.get("crowd", {}).get(season, "moderate")

    return _build(season, crowd, mult, notes, destination, travel_month)


def get_season_for_dates(destination: str, start_date: str, end_date: str | None = None) -> dict:
    """
    Convenience wrapper that derives travel_month from a date string (YYYY-MM-DD).
    Uses the start date's month.
    """
    try:
        month = int(start_date[5:7])
    except (ValueError, TypeError, IndexError):
        month = 1
    return get_season(destination, month)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resolve(dest_lower: str) -> dict | None:
    """Find season data by exact key, alias, or substring scan."""
    if dest_lower in _DB:
        return _DB[dest_lower]

    alias = _ALIASES.get(dest_lower)
    if isinstance(alias, dict):
        return alias
    if isinstance(alias, str) and alias in _DB:
        return _DB[alias]

    # Substring: check if any DB key is contained in the destination string
    for key, data in _DB.items():
        if key in dest_lower:
            return data
    for alias_key, alias_val in _ALIASES.items():
        if alias_key in dest_lower:
            if isinstance(alias_val, dict):
                return alias_val
            if isinstance(alias_val, str) and alias_val in _DB:
                return _DB[alias_val]
    return None


def _build(season: str, crowd: str, mult: float, notes: str,
           destination: str, travel_month: int) -> dict:
    pct = round((mult - 1.0) * 100)
    if pct > 0:
        price_context = f"~{pct}% above average prices"
    elif pct < 0:
        price_context = f"~{abs(pct)}% below average prices"
    else:
        price_context = "average prices"

    emoji_map = {"peak": "🔴", "shoulder": "🟡", "off": "🟢"}
    label_map  = {"peak": "Peak Season", "shoulder": "Shoulder Season", "off": "Off Season"}

    return {
        "season":           season,
        "label":            label_map.get(season, season.title()),
        "emoji":            emoji_map.get(season, "⚪"),
        "crowd_level":      crowd,
        "price_context":    price_context,
        "price_multiplier": round(mult, 2),
        "notes":            notes,
        "destination":      destination,
        "travel_month":     travel_month,
    }


def _hemisphere_fallback(destination: str, month: int) -> dict:
    """Best-guess season using hemisphere inference."""
    dest_lower = destination.lower()
    southern   = {"australia","zealand","argentina","chile","brazil","south africa",
                  "sydney","melbourne","auckland","buenos aires","santiago","cape town",
                  "johannesburg","rio","sao paulo","patagonia","antarctica"}
    is_sh = any(w in dest_lower for w in southern)

    if is_sh:
        if month in (12, 1, 2):
            season, mult, crowd = "peak", 1.35, "high"
        elif month in (6, 7, 8):
            season, mult, crowd = "off", 0.80, "low"
        else:
            season, mult, crowd = "shoulder", 1.0, "moderate"
    else:
        if month in (6, 7, 8):
            season, mult, crowd = "peak", 1.35, "high"
        elif month in (1, 2):
            season, mult, crowd = "off", 0.80, "low"
        else:
            season, mult, crowd = "shoulder", 1.0, "moderate"

    notes_map = {
        "peak":     "Popular travel season. Expect higher prices and more visitors. Book ahead.",
        "shoulder": "Transitional season — good balance of weather, value, and crowds.",
        "off":      "Low season — best prices and fewest crowds, though weather may be less predictable.",
    }
    notes = notes_map[season] + " (General estimate — no destination-specific data.)"
    return _build(season, crowd, mult, notes, destination, month)


def _unknown_season(destination: str, month: int) -> dict:
    return {
        "season": "unknown", "label": "Season Unknown", "emoji": "⚪",
        "crowd_level": "unknown", "price_context": "unknown",
        "price_multiplier": 1.0,
        "notes": "No season data available.",
        "destination": destination, "travel_month": month,
    }
