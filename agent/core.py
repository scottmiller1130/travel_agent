"""
Core agentic loop for the travel agent.

The agent runs in a loop: send messages to Claude → Claude calls tools →
execute tools → feed results back → repeat until Claude returns a final answer.

Booking actions (book_flight, book_hotel) require explicit user confirmation
before payment_confirmed is set to True.
"""

import json
import os
from typing import Callable

import anthropic

from memory.preferences import PreferenceStore
from memory.trips import TripStore
from tools.flights import search_flights, book_flight
from tools.hotels import search_hotels, book_hotel
from tools.weather import get_weather
from tools.maps import search_places, get_distance
from tools.calendar import check_availability, add_to_calendar
from tools.search import web_search
from agent.tools_schema import TOOLS

# Actions that require a human confirmation step before proceeding
CONFIRMATION_REQUIRED = {"book_flight", "book_hotel"}

SYSTEM_PROMPT = """You are a personal travel agent with full authority to plan, research, and book travel on behalf of the user.

Your capabilities:
- Search and compare flights and hotels
- Check weather forecasts and build day-by-day itineraries
- Research destinations (visa requirements, currency, safety, local tips)
- Check the user's calendar availability
- Book flights and hotels (with explicit user confirmation before payment)
- Save trips and preferences to memory for future personalization

How you work:
1. When a user wants to plan a trip, proactively gather all relevant information: check their calendar, search flights, check weather, find hotels, and research the destination — before presenting options.
2. Present 2-3 well-reasoned options with pros/cons rather than overwhelming the user.
3. NEVER book anything (set payment_confirmed=true) without the user explicitly saying "yes, book it" or equivalent.
4. Always save updated preferences when the user mentions preferences.
5. After booking, add the trip to the calendar automatically.
6. Be proactive: if you notice a better flight option, a weather issue, or a price drop opportunity, mention it.
7. Keep a running trip budget and flag if options exceed it.

Tone: Knowledgeable, efficient, and personalized. You know the user's preferences and apply them automatically.
"""


class TravelAgent:
    def __init__(self, confirm_callback: Callable[[str], bool] | None = None):
        """
        Args:
            confirm_callback: Called before any booking action. Receives a description
                              of the action and returns True if user confirms, False otherwise.
                              If None, bookings are blocked by default.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set.")

        self._client = anthropic.Anthropic(api_key=api_key)
        self._prefs = PreferenceStore()
        self._trips = TripStore()
        self._confirm = confirm_callback or (lambda msg: False)
        self._conversation: list[dict] = []
        self._current_trip: dict = {}

    def chat(self, user_message: str) -> str:
        """Send a message and run the agentic loop until a final response is produced."""
        self._conversation.append({"role": "user", "content": user_message})

        # Build a dynamic system prompt with user context
        system = self._build_system_prompt()

        while True:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=system,
                tools=TOOLS,
                messages=self._conversation,
            )

            # Append assistant turn to conversation
            self._conversation.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract the final text response
                return self._extract_text(response.content)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    result = self._dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

                self._conversation.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason
            break

        return self._extract_text(response.content)

    def reset(self):
        """Start a fresh conversation (keeps memory/preferences)."""
        self._conversation = []
        self._current_trip = {}

    def _build_system_prompt(self) -> str:
        prefs_context = self._prefs.as_context_string()
        trips_context = self._trips.as_context_string()
        return f"{SYSTEM_PROMPT}\n\n{prefs_context}\n\n{trips_context}"

    def _dispatch_tool(self, name: str, inputs: dict) -> dict:
        """Route a tool call to the correct implementation."""

        # --- Booking tools: require confirmation ---
        if name in CONFIRMATION_REQUIRED:
            if not inputs.get("payment_confirmed"):
                # Claude will handle showing details and asking for confirmation
                # We just execute — Claude controls payment_confirmed
                pass
            elif not self._confirm(f"Confirm {name} with inputs: {json.dumps(inputs, indent=2)}"):
                return {
                    "status": "cancelled",
                    "message": "User declined to confirm the booking. No charge was made.",
                }

        dispatch = {
            "search_flights": lambda i: search_flights(**i),
            "book_flight": lambda i: book_flight(**i),
            "search_hotels": lambda i: search_hotels(**i),
            "book_hotel": lambda i: book_hotel(**i),
            "get_weather": lambda i: get_weather(**i),
            "search_places": lambda i: search_places(**i),
            "get_distance": lambda i: get_distance(**i),
            "check_availability": lambda i: check_availability(**i),
            "add_to_calendar": lambda i: add_to_calendar(**i),
            "web_search": lambda i: web_search(**i),
            "save_preference": self._handle_save_preference,
            "get_preferences": lambda i: self._prefs.get_all(),
            "save_trip": self._handle_save_trip,
            "get_trips": self._handle_get_trips,
        }

        handler = dispatch.get(name)
        if not handler:
            return {"status": "error", "message": f"Unknown tool: {name}"}

        try:
            return handler(inputs)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_save_preference(self, inputs: dict) -> dict:
        key = inputs["key"]
        value = inputs["value"]
        self._prefs.set(key, value)
        return {"status": "success", "message": f"Preference '{key}' saved: {value}"}

    def _handle_save_trip(self, inputs: dict) -> dict:
        trip = inputs["trip"]
        trip_id = self._trips.save_trip(trip)
        self._current_trip = trip
        return {"status": "success", "trip_id": trip_id, "message": "Trip saved."}

    def _handle_get_trips(self, inputs: dict) -> dict:
        status = inputs.get("status")
        trips = self._trips.get_all_trips(status=status)
        return {"status": "success", "trips": trips, "count": len(trips)}

    @staticmethod
    def _extract_text(content: list) -> str:
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                return block.text
        return ""
