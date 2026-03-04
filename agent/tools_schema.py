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
            },
            "required": ["destination", "check_in", "check_out"],
        },
    },
    {
        "name": "book_hotel",
        "description": (
            "Book a specific hotel by hotel_id. IMPORTANT: Always ask the user to confirm "
            "before setting payment_confirmed=true. Show total cost first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hotel_id": {"type": "string", "description": "The hotel_id from search_hotels results"},
                "guest_name": {"type": "string", "description": "Full name of the primary guest"},
                "guest_email": {"type": "string", "description": "Email address for booking confirmation"},
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
                    "description": "Preference key",
                    "enum": [
                        "preferred_airlines", "avoided_airlines", "seat_preference",
                        "cabin_class", "hotel_min_stars", "max_budget_per_day_usd",
                        "dietary_restrictions", "accessibility_needs", "preferred_activities",
                        "avoided_activities", "travel_pace", "home_airport", "home_city",
                        "currency", "name", "email",
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
                "destination": {"type": "string", "description": "Primary trip destination"},
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
                "budget": {
                    "type": "object",
                    "description": "Estimated cost breakdown by category in USD",
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
]
