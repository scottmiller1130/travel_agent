"""
Core agentic loop for the travel agent.

The agent runs in a loop: send messages to Claude → Claude calls tools →
execute tools → feed results back → repeat until Claude returns a final answer.

Booking actions (book_flight, book_hotel) require explicit user confirmation
before payment_confirmed is set to True.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable

import anthropic

log = logging.getLogger("travel_agent.agent")

TOOL_TIMEOUT_SECONDS = 30


def _sanitize_conversation(conversation: list[dict]) -> list[dict]:
    """Remove trailing assistant messages whose tool_use blocks have no tool_result.

    If the server crashed or a tool raised an exception between appending the
    assistant message and appending the tool results, the stored conversation
    ends with an orphaned tool_use block.  The Claude API rejects that with a
    400 error, so we strip those messages before sending.
    """
    result = list(conversation)
    while result:
        last = result[-1]
        if last.get("role") != "assistant":
            break
        content = last.get("content", [])
        has_tool_use = any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            for b in (content if isinstance(content, list) else [])
        )
        if not has_tool_use:
            break
        # This assistant message has tool_use blocks with no following tool_result.
        result.pop()
    return result


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
    "search_ground_transport": "Checking trains, buses & car rental...",
    "get_exchange_rate": "Looking up exchange rates...",
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
from memory.users import UserStore, PLAN_LIMITS
from tools.flights import search_flights, book_flight, find_cheapest_dates, find_cheapest_month
from tools.hotels import search_hotels, book_hotel
from tools.weather import get_weather
from tools.maps import search_places, get_distance
from tools.calendar import check_availability, add_to_calendar
from tools.search import web_search
from tools.transport import search_ground_transport
from tools.currency import get_exchange_rate
from agent.tools_schema import TOOLS

# Actions that require a human confirmation step before proceeding
CONFIRMATION_REQUIRED = {"book_flight", "book_hotel"}

# Tools counted against the monthly api_calls quota
METERED_TOOLS = {"search_flights", "search_hotels", "find_cheapest_dates", "find_cheapest_month"}

MAX_CONVERSATION_MESSAGES = 30  # Summarize when conversation exceeds this length
KEEP_RECENT_MESSAGES = 16       # Always keep the most recent N messages
KEEP_INITIAL_MESSAGES = 2       # Always keep the very first user/assistant exchange

SYSTEM_PROMPT = """You are a world-class personal travel concierge. You serve two distinct traveler profiles — and you adapt fluidly to each:

**Higher-End Backpacker (Adventure Traveler)**
Moves slowly, goes deep. Spends $40-120/day. Prioritizes unique experiences over comfort, but values good sleep. Books hostels, guesthouses, and boutique stays. Loves overnight trains, slow overland routes, local food markets, off-the-beaten-path towns. Wants to know the cheapest date to fly and uses flexibility to save money. Asks about dorm vs private room, FlixBus, rail passes, visa-on-arrival. Use search_hotels with accommodation_type="hostel" or "dorm" by default unless they specify otherwise. Always proactively scan for cheaper flight dates when they have any flexibility.

**Affluent Traveler**
Values time, quality, and experience. Spends $300-1000+/day. Books 4-5 star hotels, business class or premium economy, private transfers. Wants curated insider picks — the best table at the right restaurant, the suite with the view, the private tour. Expects you to think ahead: pre-arranged airport transfers, early check-in requests, spa reservations. Doesn't want to be bothered with budget options. Give strong recommendations rather than long lists of options.

**How to detect which profile:**
- If they mention hostels, dorms, backpacking, rail passes, long trips on small budgets → Adventure Traveler mode
- If they mention business class, suites, specific luxury brands, fine dining, or have no budget constraint → Affluent Traveler mode
- If unsure, ask one quick clarifying question: "Are you going for an adventure/budget trip or a luxury experience?"
- Always respect saved preferences: travel_style, accommodation_preference, cabin_class, max_budget_per_day_usd

Your capabilities:
- Search and compare flights and hotels (including hostels, guesthouses, dorms)
- Hunt for the cheapest flight dates using find_cheapest_dates or find_cheapest_month
- Check weather forecasts and build day-by-day itineraries with a theme for each day
- Research destinations (visa requirements, currency, safety, local tips)
- Check the user's calendar availability
- Book flights and hotels (with explicit user confirmation before payment)
- Save trips and preferences to memory for future personalization
- Plan multi-city trips (e.g. Bangkok → Chiang Mai → Luang Prabang)
- Search ground transportation: trains, buses, car rental, overnight sleepers via search_ground_transport
- Look up live currency exchange rates via get_exchange_rate

How you work:
1. If destination or dates are unclear, ask one focused clarifying question before searching. Don't ask multiple questions at once.
2. Search for what was asked — don't run all tools at once. Start with flights or hotels based on context, then expand only if the user wants more.
3. Present 2-3 options concisely. The UI shows price/detail cards — don't repeat that data in prose. Focus on the trade-offs and what makes each option right for this traveler.
4. NEVER book anything (set payment_confirmed=true) without the user explicitly saying "yes, book it" or equivalent.
5. Save preferences automatically when the user mentions them (including travel_style, accommodation_preference, companion_profile, trip_type).
6. After booking, add the trip to the calendar automatically.
7. Call update_itinerary once the user has a plan they want to move forward with — not during exploration. Give each day a theme label that captures the emotional arc (e.g. "Arrival & First Impressions", "Temple Trail", "Slow Morning at the Market"). Include weather if fetched, and flag real conflicts (tight connections, missing transfers). Immediately after update_itinerary, call save_trip.
8. Mention season context (peak/shoulder/off) when it meaningfully affects price or experience.
9. Proactively offer to scan for cheaper dates with find_cheapest_dates whenever the user has any flexibility — even slight. For affluent travelers, still check if they asked about best time to go. Always use find_cheapest_month when the user hasn't fixed their travel month.
10. Note that flight prices are estimated unless Amadeus API credentials are configured. Be transparent about this when relevant.
11. Multi-city trips: populate the `destinations` array in update_itinerary and set `destination` to the first city. Group itinerary days by city. Search connecting transport (flights OR trains) between each city pair.
12. Budget enforcement: if the user's preferences include max_budget_per_day_usd, pass it as max_price_per_night to search_hotels and max_price_usd to search_flights. Proactively warn when options are limited within budget.
13. Ground transport: use search_ground_transport for city-to-city routes under ~2000 km. For adventure travelers, always check trains/buses first — they're often cheaper and more scenic. Note overnight train options as they save a night's accommodation cost. For affluent travelers, offer private transfers or business class rail.
14. Currency: when the user's preferred currency is not USD, call get_exchange_rate and include converted amounts. Note both USD and home currency in budgets.
15. Accommodation: for adventure/budget travelers, use accommodation_type="hostel" or "dorm" in search_hotels by default. For mid-range, use "guesthouse". For luxury, use "hotel" with appropriate star filters.

Tone for Adventure Travelers: Fellow traveler energy. Practical, enthusiastic, full of local tips. "The 22:00 sleeper to Chiang Mai is legendary — you wake up refreshed and skip a hostel night."
Tone for Affluent Travelers: Personal concierge. Warm, authoritative, effortless. "I've pulled together three options that match your taste — I'd lean toward the Ritz-Carlton for the harbour suite, but the boutique property in the old town has something special."
"""


class TravelAgent:
    def __init__(
        self,
        confirm_callback: Callable[[str], bool] | None = None,
        user_id: str | None = None,
        user_store: UserStore | None = None,
    ):
        """
        Args:
            confirm_callback: Called before any booking action. Receives a description
                              of the action and returns True if user confirms, False otherwise.
                              If None, bookings are blocked by default.
            user_id: Clerk user ID for per-user data isolation.
                     When set, preferences and trips are scoped to this user only.
                     When None, operates in anonymous/global mode (backward compat).
            user_store: Shared UserStore instance for quota enforcement. When provided
                        and user_id is set, metered tools (search_flights, search_hotels,
                        find_cheapest_dates, find_cheapest_month) will be gated by the
                        user's monthly api_calls limit.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set. Please add it to your Railway environment variables.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._user_id: str | None = user_id
        self._user_store: UserStore | None = user_store
        self._prefs = PreferenceStore()
        self._trips = TripStore()
        self._confirm = confirm_callback or (lambda msg: False)
        self._conversation: list[dict] = []
        self._current_trip: dict = {}
        self._progress_callback = None

    def _trim_conversation(self) -> None:
        """Keep conversation within token budget using a sliding window.

        Preserves the first KEEP_INITIAL_MESSAGES messages (original intent)
        and the last KEEP_RECENT_MESSAGES messages (current context), dropping
        the middle when total length exceeds MAX_CONVERSATION_MESSAGES.
        """
        if len(self._conversation) <= MAX_CONVERSATION_MESSAGES:
            return

        head = self._conversation[:KEEP_INITIAL_MESSAGES]
        tail = self._conversation[-KEEP_RECENT_MESSAGES:]

        dropped = len(self._conversation) - KEEP_INITIAL_MESSAGES - KEEP_RECENT_MESSAGES
        bridge = {
            "role": "user",
            "content": (
                f"[{dropped} earlier messages were trimmed to stay within context limits. "
                "The conversation above captures the original request; the messages below "
                "are the most recent exchanges.]"
            ),
        }
        self._conversation = head + [bridge] + tail

    def chat(self, user_message: str, progress_callback: Callable | None = None) -> str:
        """Send a message and run the agentic loop until a final response is produced.

        Args:
            progress_callback: Optional callable(event_type: str, data: dict).
                               Called with ("tool_start", {"tool": ..., "label": ...})
                               and ("tool_done", {"tool": ...}) around each tool call.
        """
        self._progress_callback = progress_callback
        self._conversation.append({"role": "user", "content": user_message})
        self._trim_conversation()
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

                    t_start = time.monotonic()
                    try:
                        # Booking tools wait for user confirmation (up to 5 min).
                        timeout = None if block.name in CONFIRMATION_REQUIRED else TOOL_TIMEOUT_SECONDS
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            future = pool.submit(self._dispatch_tool, block.name, block.input)
                            result = future.result(timeout=timeout)
                        elapsed = int((time.monotonic() - t_start) * 1000)
                        log.info("tool %s OK %dms", block.name, elapsed)
                    except FuturesTimeoutError:
                        log.warning("tool %s timed out after %ds", block.name, TOOL_TIMEOUT_SECONDS)
                        result = {
                            "status": "error",
                            "message": f"Tool '{block.name}' timed out after {TOOL_TIMEOUT_SECONDS}s.",
                        }
                    except Exception as e:
                        log.error("tool %s error: %s", block.name, e)
                        result = {"status": "error", "message": str(e)}

                    if progress_callback:
                        progress_callback("tool_done", {"tool": block.name})

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

                # Always append tool_results so the conversation stays valid.
                # An empty tool_results list here would mean stop_reason was
                # "tool_use" but no tool_use blocks were present — shouldn't
                # happen, but guard anyway to avoid an orphaned assistant message.
                if tool_results:
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
        """Restore a previously saved conversation, stripping any incomplete tool turns."""
        self._conversation = _sanitize_conversation(conversation)

    def get_itinerary(self) -> dict | None:
        """Return the most recent itinerary pushed via update_itinerary."""
        return self._current_trip if self._current_trip else None

    def load_itinerary(self, itinerary: dict | None) -> None:
        """Restore a previously saved itinerary."""
        self._current_trip = itinerary or {}

    def _build_system_prompt(self) -> str:
        prefs_context = self._prefs.as_context_string(user_id=self._user_id)
        trips_context = self._trips.as_context_string(user_id=self._user_id)
        itinerary_context = ""
        if self._current_trip:
            itinerary_context = (
                "\n\n## Current Trip Board\n"
                "The following itinerary is currently loaded on the user's trip board. "
                "You can reference, modify, or extend it based on the user's requests.\n"
                f"```json\n{json.dumps(self._current_trip, indent=2)}\n```"
            )
        return f"{SYSTEM_PROMPT}\n\n{prefs_context}\n\n{trips_context}{itinerary_context}"

    def _dispatch_tool(self, name: str, inputs: dict) -> dict:
        """Route a tool call to the correct implementation."""

        # --- Metered tools: check + increment monthly api_calls quota ---
        if name in METERED_TOOLS and self._user_store and self._user_id:
            user_rec = self._user_store.get(self._user_id)
            if user_rec:
                usage = self._user_store.get_usage(self._user_id)
                if not self._user_store.within_limit(user_rec, "api_calls", usage):
                    plan = user_rec["plan"]
                    cap  = PLAN_LIMITS[plan]["api_calls"]
                    return {
                        "status": "error",
                        "message": (
                            f"Monthly search limit reached ({cap} searches on {plan} plan). "
                            "Please upgrade to Pro for 200 searches/month, or Team for 500."
                        ),
                    }
                self._user_store.increment_api(self._user_id)

        # --- Booking tools: require explicit user confirmation ---
        if name in CONFIRMATION_REQUIRED and inputs.get("payment_confirmed"):
            # Notify the frontend so it can show a confirmation modal.
            if self._progress_callback:
                self._progress_callback("booking_confirm", {
                    "tool": name,
                    "inputs": inputs,
                })
            if not self._confirm(f"Confirm {name} with inputs: {json.dumps(inputs, indent=2)}"):
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
            "search_ground_transport": lambda i: search_ground_transport(**i),
            "get_exchange_rate": lambda i: get_exchange_rate(**i),
            "check_availability": lambda i: check_availability(**i),
            "add_to_calendar": lambda i: add_to_calendar(**i),
            "web_search": lambda i: web_search(**i),
            "save_preference": self._handle_save_preference,
            "get_preferences": lambda i: self._prefs.get_all(user_id=self._user_id),
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
        self._prefs.set(key, value, user_id=self._user_id)
        return {"status": "success", "message": f"Preference '{key}' saved: {value}"}

    def _handle_save_trip(self, inputs: dict) -> dict:
        trip = inputs["trip"]
        trip_id = self._trips.save_trip(trip, user_id=self._user_id)
        self._current_trip = trip
        return {"status": "success", "trip_id": trip_id, "message": "Trip saved."}

    def _handle_update_itinerary(self, inputs: dict) -> dict:
        self._current_trip = inputs  # persist so get_itinerary() is always current
        # Auto-save every itinerary push to TripStore so nothing is ever lost.
        # Derive a stable ID from destination + start_date so repeated updates
        # to the same trip overwrite the existing row rather than duplicating.
        try:
            trip_copy = dict(inputs)
            if not trip_copy.get("id"):
                import hashlib
                key = f"{trip_copy.get('destination', '')}-{trip_copy.get('start_date', '')}"
                trip_copy["id"] = "TRIP-" + hashlib.md5(key.encode()).hexdigest()[:10]
            self._trips.save_trip(trip_copy, user_id=self._user_id)
        except Exception:
            pass  # never let a save failure break the itinerary update
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
        trips = self._trips.get_all_trips(status=status, user_id=self._user_id)
        return {"status": "success", "trips": trips, "count": len(trips)}

    @staticmethod
    def _extract_text(content: list) -> str:
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                return block.text
        return ""
