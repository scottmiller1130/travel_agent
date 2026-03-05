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


def _blocks_to_dicts(content) -> list[dict] | str:
    """Convert Anthropic SDK content blocks to plain JSON-serialisable dicts.

    The API accepts plain dicts as message content, so storing everything as
    dicts lets us serialise the conversation to SQLite and restore it later.
    """
    if isinstance(content, str):
        return content
    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            result.append(block.model_dump())
        elif isinstance(block, dict):
            result.append(block)
        else:
            result.append({"type": "text", "text": str(block)})
    return result

TOOL_LABELS = {
    "search_flights": "Searching flights...",
    "book_flight": "Booking flight...",
    "search_hotels": "Searching hotels...",
    "book_hotel": "Booking hotel...",
    "get_weather": "Checking weather...",
    "search_places": "Finding places of interest...",
    "get_distance": "Calculating distances...",
    "check_availability": "Checking your calendar...",
    "add_to_calendar": "Adding to calendar...",
    "web_search": "Researching destination...",
    "save_preference": "Saving your preference...",
    "get_preferences": "Loading your preferences...",
    "save_trip": "Saving trip...",
    "get_trips": "Loading trip history...",
    "update_itinerary": "Building your trip board...",
    "find_cheapest_dates":  "Hunting for cheap dates...",
    "find_cheapest_month":  "Scanning months for best deals...",
}

from memory.preferences import PreferenceStore
from memory.trips import TripStore
from tools.flights import search_flights, book_flight, find_cheapest_dates, find_cheapest_month
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
- Hunt for the cheapest flight dates using find_cheapest_dates or find_cheapest_month
- Check weather forecasts and build day-by-day itineraries
- Research destinations (visa requirements, currency, safety, local tips)
- Check the user's calendar availability
- Book flights and hotels (with explicit user confirmation before payment)
- Save trips and preferences to memory for future personalization

How you work:
1. If destination or dates are unclear, ask one focused clarifying question before searching. Don't ask multiple questions at once.
2. Search for what was asked — don't run all tools at once. Start with flights or hotels based on context, then expand only if the user wants more.
3. Present 2-3 options concisely. The UI shows price/detail cards — don't repeat that data in prose. Focus on the trade-offs.
4. NEVER book anything (set payment_confirmed=true) without the user explicitly saying "yes, book it" or equivalent.
5. Save preferences automatically when the user mentions them.
6. After booking, add the trip to the calendar automatically.
7. Call update_itinerary once the user has a plan they want to move forward with — not during exploration. Include weather if you've already fetched it, and flag real conflicts (tight connections, missing transfers).
8. Mention season context (peak/shoulder/off) when it meaningfully affects price or experience.
9. Use find_cheapest_dates or find_cheapest_month when the user explicitly asks about deals or flexibility — not automatically on every request.
10. Note that flight prices are estimated unless Amadeus API credentials are configured. Be transparent about this when relevant.

Tone: Concise, knowledgeable, personalized. Prefer short bullet answers over long paragraphs. Let the UI cards do the heavy lifting.
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
            raise ValueError("ANTHROPIC_API_KEY is not set. Please add it to your Railway environment variables.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._prefs = PreferenceStore()
        self._trips = TripStore()
        self._confirm = confirm_callback or (lambda msg: False)
        self._conversation: list[dict] = []
        self._current_trip: dict = {}
        self._progress_callback = None

    def chat(self, user_message: str, progress_callback: Callable | None = None) -> str:
        """Send a message and run the agentic loop until a final response is produced.

        Args:
            progress_callback: Optional callable(event_type: str, data: dict).
                               Called with ("tool_start", {"tool": ..., "label": ...})
                               and ("tool_done", {"tool": ...}) around each tool call.
        """
        self._progress_callback = progress_callback
        self._conversation.append({"role": "user", "content": user_message})
        system = self._build_system_prompt()

        while True:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=system,
                tools=TOOLS,
                messages=self._conversation,
            )

            self._conversation.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})

            if response.stop_reason == "end_turn":
                return self._extract_text(response.content)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if progress_callback:
                        progress_callback("tool_start", {
                            "tool": block.name,
                            "label": TOOL_LABELS.get(block.name, f"Using {block.name}..."),
                        })

                    result = self._dispatch_tool(block.name, block.input)

                    if progress_callback:
                        progress_callback("tool_done", {"tool": block.name})

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

                self._conversation.append({"role": "user", "content": tool_results})
                continue

            break

        return self._extract_text(response.content)

    def reset(self):
        """Start a fresh conversation (keeps memory/preferences)."""
        self._conversation = []
        self._current_trip = {}

    # ── Persistence helpers ───────────────────────────────────────────────────

    def get_conversation(self) -> list[dict]:
        """Return the conversation as a JSON-serialisable list."""
        return self._conversation

    def load_conversation(self, conversation: list[dict]) -> None:
        """Restore a previously saved conversation."""
        self._conversation = conversation

    def get_itinerary(self) -> dict | None:
        """Return the most recent itinerary pushed via update_itinerary."""
        return self._current_trip if self._current_trip else None

    def load_itinerary(self, itinerary: dict | None) -> None:
        """Restore a previously saved itinerary."""
        self._current_trip = itinerary or {}

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
            "update_itinerary":    self._handle_update_itinerary,
            "find_cheapest_dates": self._handle_find_cheapest_dates,
            "find_cheapest_month": self._handle_find_cheapest_month,
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

    def _handle_update_itinerary(self, inputs: dict) -> dict:
        self._current_trip = inputs  # persist so get_itinerary() is always current
        if self._progress_callback:
            self._progress_callback("itinerary_update", {"itinerary": inputs})
        return {"status": "success", "message": "Trip board updated."}

    def _handle_find_cheapest_dates(self, inputs: dict) -> dict:
        result = find_cheapest_dates(**inputs)
        if self._progress_callback and result.get("status") == "success":
            self._progress_callback("deal_result", {"deal": result})
        return result

    def _handle_find_cheapest_month(self, inputs: dict) -> dict:
        result = find_cheapest_month(**inputs)
        if self._progress_callback and result.get("status") == "success":
            self._progress_callback("month_result", {"month_data": result})
        return result

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
