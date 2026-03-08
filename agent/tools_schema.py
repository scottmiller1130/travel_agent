"""
Tool schemas — Claude uses these definitions to decide when and how to call each tool.
"""

TOOLS: list[dict] = [
    {
        "name": "search_flights",
        "description": (
            "Search for available flights between two cities. Returns ranked options with "
            "pricing, duration, stops, and airline. Use this before recommending or booking flights."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin airport city or IATA code (e.g. 'New York' or 'JFK')"},
                "destination": {"type": "string", "description": "Destination city or IATA code (e.g. 'Lisbon' or 'LIS')"},
                "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                "return_date": {"type": "string", "description": "Return date in YYYY-MM-DD format (optional for one-way)"},
                "passengers": {"type": "integer", "description": "Number of passengers", "default": 1},
                "cabin_class": {
                    "type": "string",
                    "enum": ["economy", "premium_economy", "business", "first"],
                    "description": "Cabin class preference",
                    "default": "economy",
                },
                "max_results": {"type": "integer", "description": "Max number of results to return", "default": 5},
                "max_price_usd": {"type": "integer", "description": "Maximum total price in USD (optional). Pass the user's budget when known."},
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "book_flight",
        "description": (
            "Book a specific flight by flight_id. IMPORTANT: Always ask the user to confirm "
            "before setting payment_confirmed=true. Show the flight details and total cost first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "flight_id": {"type": "string", "description": "The flight_id from search_flights results"},
                "passenger_name": {"type": "string", "description": "Full name of the passenger"},
                "passenger_email": {"type": "string", "description": "Email address for booking confirmation"},
                "payment_confirmed": {"type": "boolean", "description": "Set to true only after user explicitly confirms payment", "default": False},
            },
            "required": ["flight_id", "passenger_name", "passenger_email"],
        },
    },
    {
        "name": "search_hotels",
        "description": (
            "Search for available hotels at a destination for given dates. Returns options with "
            "star rating, amenities, price per night, and cancellation policy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "City or area to search hotels in"},
                "check_in": {"type": "string", "description": "Check-in date in YYYY-MM-DD format"},
                "check_out": {"type": "string", "description": "Check-out date in YYYY-MM-DD format"},
                "guests": {"type": "integer", "description": "Number of guests", "default": 1},
                "rooms": {"type": "integer", "description": "Number of rooms needed", "default": 1},
                "max_results": {"type": "integer", "description": "Max number of results", "default": 5},
                "max_price_per_night": {"type": "integer", "description": "Maximum price per night in USD (optional)"},
                "accommodation_type": {
                    "type": "string",
                    "enum": ["hotel", "hostel", "guesthouse", "dorm"],
                    "description": "Type of accommodation. Use 'hostel' for budget private rooms (~$20-50/night), 'dorm' for shared dorm beds (~$8-45/night per person), 'guesthouse' for family-run stays. Defaults to 'hotel'.",
                    "default": "hotel",
                },
                "min_stars": {
                    "type": "integer",
                    "description": (
                        "Minimum hotel star rating filter (1-5). Use 4 or 5 for luxury travelers. "
                        "Omit for adventure travelers (not relevant for hostels/dorms)."
                    ),
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["destination", "check_in", "check_out"],
        },
    },
    {
        "name": "book_hotel",
        "description": (
            "Book a specific hotel by hotel_id. IMPORTANT: Always ask the user to confirm "
            "before setting payment_confirmed=true. Show total cost first. "
            "For luxury travelers, always include room_type and special_requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "The hotel_id from search_hotels results"},
                "guest_name": {"type": "string", "description": "Full name of the primary guest"},
                "guest_email": {"type": "string", "description": "Email address for booking confirmation"},
                "room_type": {
                    "type": "string",
                    "description": (
                        "Room or suite category preference (e.g. 'harbour suite', 'deluxe king', "
                        "'ocean view double', 'dorm bed', 'private ensuite'). "
                        "Use for luxury travelers or whenever the user specifies a room preference."
                    ),
                },
                "bed_preference": {
                    "type": "string",
                    "enum": ["king", "queen", "twin", "double", "single", "any"],
                    "description": "Bed type preference. Omit if not relevant.",
                },
                "special_requests": {
                    "type": "string",
                    "description": (
                        "Free-text special requests to pass to the hotel "
                        "(e.g. 'early check-in requested', 'high floor preferred', "
                        "'quiet room away from elevator', 'anniversary — flowers in room if possible'). "
                        "Always populate for luxury travelers."
                    ),
                },
                "payment_confirmed": {"type": "boolean", "description": "Set to true only after user explicitly confirms payment", "default": False},
            },
            "required": ["hotel_id", "guest_name", "guest_email"],
        },
    },
    {
        "name": "find_cheapest_dates",
        "description": (
            "Find the cheapest flight dates for a route within a flexible window. "
            "Use this to hunt for deals — it searches ±flexibility_days around a target date and returns "
            "dates ranked by price with savings vs the target date. Always offer to search for cheap dates "
            "when the user has any flexibility at all. Returns sorted results with savings_vs_target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin city or IATA code"},
                "destination": {"type": "string", "description": "Destination city or IATA code"},
                "target_date": {"type": "string", "description": "Preferred departure date YYYY-MM-DD"},
                "flexibility_days": {"type": "integer", "description": "Days to search before/after target date", "default": 7},
                "passengers": {"type": "integer", "description": "Number of passengers", "default": 1},
                "cabin_class": {
                    "type": "string",
                    "enum": ["economy", "premium_economy", "business", "first"],
                    "default": "economy",
                },
                "trip_duration_nights": {"type": "integer", "description": "Length of stay in nights (for round-trip pricing context)"},
            },
            "required": ["origin", "destination", "target_date"],
        },
    },
    {
        "name": "find_cheapest_month",
        "description": (
            "Scan the next N months to find the cheapest time to fly a route. "
            "Use this when the user asks 'when is cheapest to fly to X?' or has flexible travel dates. "
            "Returns monthly average prices with season context (peak/shoulder/off) and savings vs peak month. "
            "Returns both a price-ranked list and a chronological list for charting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin city or IATA code"},
                "destination": {"type": "string", "description": "Destination city or IATA code"},
                "months_ahead": {"type": "integer", "description": "How many months to scan", "default": 12},
                "trip_duration_nights": {"type": "integer", "description": "Length of stay for round-trip pricing", "default": 7},
                "passengers": {"type": "integer", "default": 1},
                "cabin_class": {
                    "type": "string",
                    "enum": ["economy", "premium_economy", "business", "first"],
                    "default": "economy",
                },
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "get_weather",
        "description": (
            "Get weather forecast for a destination during a date range. Returns daily forecasts, "
            "climate summary, packing suggestions, and peak/shoulder/off-season context. "
            "Always check weather before finalizing trip plans."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "City or destination to get weather for"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
            "required": ["destination", "start_date", "end_date"],
        },
    },
    {
        "name": "search_experiences",
        "description": (
            "Search for bookable tours, activities, and experiences at a destination. "
            "Use this for: day trips, guided tours, cooking classes, adventure sports, "
            "museum tickets, food tours, cultural experiences, and nightlife. "
            "Returns real attraction names (OpenTripMap) or bookable listings (Viator/GetYourGuide "
            "when keys are set). Always call this when building an itinerary's activity days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "City or destination to search in"},
                "category": {
                    "type": "string",
                    "description": (
                        "Type of experience: 'attraction', 'tour', 'museum', 'food', 'adventure', "
                        "'culture', 'history', 'nature', 'sport', 'nightlife', 'shopping'"
                    ),
                    "default": "attraction",
                },
                "date": {"type": "string", "description": "Travel date YYYY-MM-DD for availability (optional)"},
                "max_results": {"type": "integer", "description": "Max results to return", "default": 6},
                "max_price_usd": {"type": "integer", "description": "Max price per person in USD (optional)"},
            },
            "required": ["destination"],
        },
    },
    {
        "name": "search_places",
        "description": (
            "Search for places of interest (attractions, restaurants, museums, beaches, etc.) "
            "at a destination. Use to build itineraries and recommend activities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "City or destination to search in"},
                "category": {
                    "type": "string",
                    "description": "Type of place (e.g. 'attraction', 'restaurant', 'museum', 'beach', 'park', 'shopping')",
                    "default": "attraction",
                },
                "query": {"type": "string", "description": "Specific search query (optional)"},
                "limit": {"type": "integer", "description": "Max number of places to return", "default": 6},
            },
            "required": ["destination"],
        },
    },
    {
        "name": "get_distance",
        "description": "Get travel distance and estimated time between two locations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Starting location"},
                "destination": {"type": "string", "description": "Destination location"},
                "mode": {
                    "type": "string",
                    "enum": ["driving", "transit", "walking", "cycling"],
                    "description": "Mode of transport",
                    "default": "transit",
                },
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "get_exchange_rate",
        "description": (
            "Get live currency exchange rates and convert amounts between currencies. "
            "Use this when the user asks about costs in their local currency, when showing "
            "budgets for international trips, or when they ask 'how much is X in EUR/GBP/etc'. "
            "to_currency can be a comma-separated list to convert to multiple currencies at once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency": {
                    "type":        "string",
                    "description": "Source currency code (e.g. 'USD', 'EUR', 'GBP')",
                    "default":     "USD",
                },
                "to_currency": {
                    "type":        "string",
                    "description": "Target currency code or comma-separated list (e.g. 'EUR' or 'EUR,GBP,JPY')",
                },
                "amount": {
                    "type":        "number",
                    "description": "Amount to convert (default 1.0)",
                    "default":     1.0,
                },
            },
            "required": ["to_currency"],
        },
    },
    {
        "name": "search_ground_transport",
        "description": (
            "Search ground transportation options (car rental, train, bus) between two cities. "
            "Use this for short-to-medium distance city pairs where flying is impractical, "
            "for airport-to-city transfers, or when the user asks about trains, buses, or car rental. "
            "Returns ranked options with prices, travel times, and provider details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin":      {"type": "string", "description": "Origin city or airport code"},
                "destination": {"type": "string", "description": "Destination city or airport code"},
                "date":        {"type": "string", "description": "Travel date in YYYY-MM-DD format"},
                "passengers":  {"type": "integer", "description": "Number of passengers", "default": 1},
                "transport_types": {
                    "type":  "array",
                    "items": {"type": "string", "enum": ["car", "train", "bus"]},
                    "description": "Which transport types to include. Omit for all.",
                },
            },
            "required": ["origin", "destination", "date"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Check if the user is available (no calendar conflicts) for a date range. "
            "Always check availability before proposing trip dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "add_to_calendar",
        "description": "Add a trip, flight, or hotel booking to the user's calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title (e.g. 'Trip to Lisbon', 'Flight LIS → JFK')"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "description": {"type": "string", "description": "Event details (confirmation codes, notes, etc.)"},
                "location": {"type": "string", "description": "Location of the event"},
            },
            "required": ["title", "start_date", "end_date"],
        },
    },
    {
        "name": "get_inspiration",
        "description": (
            "Extract travel ideas from any URL, blog post, article, or pasted text. "
            "Use this when the user shares a link (travel blog, YouTube video, TripAdvisor article, "
            "Instagram caption) or pastes notes they want to turn into a trip plan. "
            "Fetches the content, extracts destinations and activity ideas, and returns a "
            "structured seed for planning. This is the 'Start Anywhere' feature — "
            "users can start from any inspiration source."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "A URL (https://...) to fetch travel content from, OR a block of "
                        "pasted text/notes to extract trip ideas from."
                    ),
                },
                "trip_type": {
                    "type": "string",
                    "description": "Optional trip type hint: luxury, adventure, road_trip, backpacker, family, honeymoon, solo, group, wellness, foodie",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "log_expense",
        "description": (
            "Log an actual expense to the trip budget tracker. "
            "Use this when the user mentions spending money during a trip "
            "(e.g. 'I paid $45 for dinner', 'flight cost me $320'). "
            "Tracks running total vs budget."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["flights", "hotels", "food", "activities", "transport", "shopping", "other"],
                    "description": "Expense category",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Amount in USD",
                },
                "description": {
                    "type": "string",
                    "description": "What this expense was for (e.g. 'Dinner at La Boqueria', 'Airbnb night 3')",
                },
                "date": {
                    "type": "string",
                    "description": "Date of expense YYYY-MM-DD (optional, defaults to today)",
                },
            },
            "required": ["category", "amount_usd", "description"],
        },
    },
    {
        "name": "get_budget_status",
        "description": (
            "Show current trip spending broken down by category. "
            "Call this when the user asks 'how much have I spent?', 'what's my budget status?', "
            "or 'am I over budget?'. Returns total spent, by-category breakdown, and "
            "remaining budget if a budget was set."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trip_budget_usd": {
                    "type": "number",
                    "description": "Total trip budget in USD (optional, to show remaining budget)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web for travel information: visa requirements, travel advisories, "
            "local tips, currency advice, transportation options, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results to return", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_preference",
        "description": (
            "Save a user travel preference to memory. Use this when the user tells you their "
            "preferences (airlines, budget, seat type, dietary needs, etc.) so you remember for future trips."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Preference key. Use 'traveler_profile' as the primary profile anchor "
                        "(adventure | mid_range | luxury) — it drives defaults for cabin class, "
                        "hotel stars, budget, pace, and accommodation type."
                    ),
                    "enum": [
                        "traveler_profile",
                        "preferred_airlines", "avoided_airlines", "seat_preference",
                        "cabin_class", "hotel_min_stars", "max_budget_per_day_usd",
                        "dietary_restrictions", "accessibility_needs", "preferred_activities",
                        "avoided_activities", "travel_pace", "home_airport", "home_city",
                        "currency", "name", "email",
                        "travel_style", "values", "companion_profile", "trip_type",
                        "accommodation_preference",
                    ],
                },
                "value": {"description": "Value to store (string, number, or list)"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "get_preferences",
        "description": "Retrieve all saved user preferences. Call this at the start of planning to personalize recommendations.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "save_trip",
        "description": "Save the current trip plan to memory for future reference.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trip": {
                    "type": "object",
                    "description": "Trip object with destination, start_date, end_date, flights, hotels, itinerary, budget, status",
                },
            },
            "required": ["trip"],
        },
    },
    {
        "name": "get_trips",
        "description": "Retrieve saved trips from memory. Useful for reviewing past or planned trips.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["planned", "completed", "cancelled"],
                    "description": "Filter by trip status (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_itinerary",
        "description": (
            "Push a structured trip itinerary to the visual planning board. "
            "Call this whenever you have a concrete day-by-day plan — the user will see "
            "draggable cards they can rearrange across days. Include all known flights, "
            "hotels, activities, transfers, plus any issues or budget breakdown. "
            "Call again whenever the plan changes significantly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "Primary trip destination (first city for multi-city trips)"},
                "destinations": {
                    "type": "array",
                    "description": "For multi-city trips, ordered list of all destination cities (e.g. ['Paris', 'Rome', 'Barcelona']). Include when planning a trip with 2+ cities.",
                    "items": {"type": "string"},
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "days": {
                    "type": "array",
                    "description": "Day-by-day breakdown of the trip",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "label": {"type": "string", "description": "Short theme e.g. 'Arrival', 'Beach Day', 'City Tour'"},
                            "weather": {
                                "type": "object",
                                "description": "Weather forecast if known",
                                "properties": {
                                    "condition": {"type": "string"},
                                    "temp_high": {"type": "number", "description": "High temp in Celsius"},
                                    "temp_low": {"type": "number", "description": "Low temp in Celsius"},
                                },
                            },
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string", "description": "Unique ID e.g. 'f1', 'h1', 'a1'"},
                                        "type": {
                                            "type": "string",
                                            "enum": ["flight", "hotel", "activity", "transfer", "restaurant", "free_time"],
                                        },
                                        "title": {"type": "string"},
                                        "subtitle": {"type": "string", "description": "Secondary info (airline, address, etc.)"},
                                        "time": {"type": "string", "description": "Start time HH:MM 24h"},
                                        "end_time": {"type": "string", "description": "End time HH:MM"},
                                        "duration_hours": {"type": "number"},
                                        "price_usd": {"type": "number"},
                                        "notes": {"type": "string"},
                                        "status": {"type": "string", "enum": ["confirmed", "suggested", "alternative"]},
                                    },
                                    "required": ["id", "type", "title"],
                                },
                            },
                        },
                        "required": ["date", "items"],
                    },
                },
                "travelers": {
                    "type": "integer",
                    "description": "Number of travelers. Default 1. Used to calculate per-person cost breakdowns.",
                    "minimum": 1,
                },
                "max_budget_usd": {
                    "type": "number",
                    "description": "Maximum total budget in USD for all travelers combined. Used to show budget utilization as a percentage.",
                },
                "budget": {
                    "type": "object",
                    "description": "Estimated cost breakdown by category in USD (totals for ALL travelers combined, not per person)",
                    "properties": {
                        "flights": {"type": "number"},
                        "hotels": {"type": "number"},
                        "activities": {"type": "number"},
                        "food": {"type": "number"},
                        "transport": {"type": "number"},
                    },
                },
                "season": {
                    "type": "object",
                    "description": "Season context for the destination (from get_weather or get_season tool). Include when known.",
                    "properties": {
                        "season": {"type": "string", "description": "peak, shoulder, or off"},
                        "label": {"type": "string", "description": "Human label e.g. 'Peak Season'"},
                        "emoji": {"type": "string"},
                        "crowd_level": {"type": "string"},
                        "price_context": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                },
                "issues": {
                    "type": "array",
                    "description": "Known conflicts or warnings to surface on the board",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                            "message": {"type": "string"},
                            "item_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["severity", "message"],
                    },
                },
            },
            "required": ["destination", "days"],
        },
    },
    {
        "name": "get_visa_requirements",
        "description": (
            "Get visa and entry requirements for a destination based on the traveler's passport. "
            "Use this proactively when planning international trips — especially for destinations "
            "with complex visa rules (Asia, Africa, Middle East, Central Asia). "
            "Returns visa type (free/on-arrival/e-visa/embassy), duration allowed, cost, and official links."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Destination country or city (e.g. 'Thailand', 'India', 'Tokyo')",
                },
                "passport_country": {
                    "type": "string",
                    "description": "Traveler's passport country or nationality (e.g. 'US', 'British', 'Australian')",
                    "default": "US",
                },
                "trip_purpose": {
                    "type": "string",
                    "enum": ["tourism", "business", "transit"],
                    "description": "Purpose of travel",
                    "default": "tourism",
                },
            },
            "required": ["destination"],
        },
    },
    {
        "name": "get_travel_advisory",
        "description": (
            "Get the current government travel advisory / safety level for a destination. "
            "Always call this when the user asks about safety, or for destinations in regions "
            "with elevated risk (Middle East, Africa, Central/South Asia, parts of Latin America). "
            "Returns advisory level 1–4, safety message, and practical tips. "
            "Level 1 = normal precautions, 2 = increased caution, 3 = reconsider travel, 4 = do not travel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Destination country or city (e.g. 'Mexico', 'Egypt', 'Bangkok')",
                },
                "passport_country": {
                    "type": "string",
                    "description": "Traveler's passport country for context (e.g. 'US', 'UK')",
                    "default": "US",
                },
            },
            "required": ["destination"],
        },
    },
    {
        "name": "generate_packing_list",
        "description": (
            "Generate a personalised packing list for a trip based on destination, duration, "
            "climate, planned activities, and traveler profile. "
            "Call this when the user asks 'what should I pack?', 'help me pack', or is finalising their trip. "
            "Returns categorised list (essentials, clothing, activity gear, toiletries) with packing tips."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Trip destination (e.g. 'Thailand', 'Swiss Alps', 'Tokyo')",
                },
                "duration_days": {
                    "type": "integer",
                    "description": "Length of trip in days",
                },
                "climate": {
                    "type": "string",
                    "enum": ["warm", "tropical", "mild", "cold", "snowy", "desert"],
                    "description": "Expected climate / weather at destination",
                    "default": "mild",
                },
                "activities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Planned activities (e.g. ['hiking', 'beach', 'city', 'business', 'skiing'])",
                },
                "traveler_profile": {
                    "type": "string",
                    "enum": ["adventure", "mid_range", "luxury"],
                    "description": "Traveler profile — affects how minimal or extensive the list is",
                    "default": "mid_range",
                },
                "trip_type": {
                    "type": "string",
                    "description": "Optional trip type hint: honeymoon, family, business, backpacker, solo",
                },
            },
            "required": ["destination", "duration_days"],
        },
    },
]
