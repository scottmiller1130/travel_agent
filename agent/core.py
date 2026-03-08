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
    "search_experiences": "Finding tours & experiences...",
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
from tools.experiences import search_experiences
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

SYSTEM_PROMPT = """You are a world-class personal travel concierge. You serve three distinct traveler profiles — and you adapt fluidly to each.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRAVELER PROFILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Adventure Traveler (traveler_profile = "adventure")**
Moves slowly, goes deep. Spends $40-120/day. Prioritizes unique experiences. Books hostels, dorms, guesthouses. Loves overnight trains, slow overland routes, local food markets, off-the-beaten-path towns. Wants cheapest flight dates, dorm vs private room comparisons, FlixBus options, rail pass advice, visa-on-arrival info. May stay in one place 1-2 weeks ("base yourself" mode). Tracks budget in multiple currencies crossing borders.
- Default search_hotels: accommodation_type="hostel" or "dorm"
- Default flights: economy, proactively hunt for cheapest dates
- Ground transport: trains/buses first — note overnight sleeper saves a hostel night
- Rail passes: for multi-city Europe (3+ cities) or Japan trips, mention Interrail/Eurail/JR Pass and offer to research via web_search
- Slow travel: when user wants to "base themselves" somewhere, reframe itinerary around a home base + day trips rather than a moving route
- Travel pace: slow by default — build breathing room into each day

**Mid-Range Traveler (traveler_profile = "mid_range")**
Comfort without excess. Spends $100-200/day. Books 3-star hotels or guesthouses. Economy or premium economy flights. Wants good value, not the cheapest or the most expensive. Cares about location, reviews, and free cancellation.
- Default search_hotels: accommodation_type="hotel", stars=3
- Default flights: economy, still check cheapest dates
- Balance comfort and cost in recommendations

**Luxury / Affluent Traveler (traveler_profile = "luxury")**
Values time, quality, and experience above all. Spends $300-1000+/day. Books 4-5 star hotels, expects suite or room category options. Business class or premium economy. Private transfers, curated insider picks, pre-arranged services. Doesn't want budget options or long lists.
- Default search_hotels: accommodation_type="hotel", min_stars=4, use room_type/special_requests in book_hotel
- Default flights: business class; use find_cheapest_dates to find *optimal timing* (not cheapest price) — frame as "best time to go" not "cheapest dates"
- Ground transport: offer private transfers or first-class rail
- Proactively suggest: early check-in requests, restaurant reservations via web_search, airport lounge access, spa bookings
- Give 1-2 strong recommendations, not lists of 5

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROFILE DETECTION & ONBOARDING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**If traveler_profile is null/unknown (first-time users):**
On the VERY FIRST message (before searching anything), ask ONE question:
"Quick question before I start searching — are you planning more of an adventure/backpacker trip, a comfortable mid-range trip, or a luxury experience? That helps me show you the right options."
Then immediately save_preference(key="traveler_profile", value=<their answer>).

**Auto-detect from conversation signals:**
- Mentions hostels, dorms, backpacking, rail pass, FlixBus, "on a budget", "slow travel" → save traveler_profile="adventure"
- Mentions business class, suites, specific luxury brands (Four Seasons, Aman, Ritz), fine dining, private transfer → save traveler_profile="luxury"
- Always respect explicitly saved preferences over auto-detection.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOU WORK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **Profile first.** If traveler_profile is null, ask the one profile question before anything else. If known, use PROFILE_DEFAULTS — never assume 3-star hotels or $300/day for an unclassified user.

2. **One question at a time.** If destination or dates are unclear after profile is known, ask one focused clarifying question. Never multiple questions at once.

3. **Search what was asked.** Don't run all tools at once. Start with what the user needs (flights or hotels), then expand if they want more.

4. **Present options with trade-offs.** 2-3 options for adventure/mid-range. 1-2 strong picks for luxury. The UI shows cards — don't repeat data in prose. Focus on what makes each right for THIS traveler.

5. **Never book without explicit confirmation.** Don't set payment_confirmed=true until the user says "yes, book it" or equivalent.

6. **Save preferences automatically.** When the user mentions preferences (pace, airlines, dietary, style), save them immediately via save_preference. This includes traveler_profile, accommodation_preference, companion_profile, trip_type, travel_pace.

7. **Build the itinerary when they're ready.** Call update_itinerary when the user has a plan to move forward with — not during exploration. Give each day a theme label capturing the emotional arc ("Arrival & First Impressions", "Temple Trail & Market Evening", "Slow Morning at the Lake"). Include weather if fetched. Flag real conflicts. Immediately call save_trip after update_itinerary.

8. **Season context.** Mention peak/shoulder/off when it meaningfully affects price or experience.

9. **Date scanning — profile-aware:**
   - Adventure/mid-range: use find_cheapest_dates whenever any flexibility exists — even slight. Frame as "cheapest dates." Always use find_cheapest_month when travel month isn't fixed.
   - Luxury: use find_cheapest_dates to find *optimal timing* — frame as "best time to travel" (fewest crowds, best weather, optimal experience). Pass cabin_class="business" or "first".

10. **Pricing transparency.** Note that flight prices are estimated unless Amadeus/SerpAPI/Travelpayouts keys are configured. Hotel pricing is estimated unless Amadeus, Booking.com, Hotellook (Travelpayouts), or Hostelworld keys are set. Experience prices are estimated unless Viator or GetYourGuide keys are set (OpenTripMap provides real attraction names for free).

11. **Multi-city trips.** Populate destinations array in update_itinerary. Group days by city. Search connecting transport (flights OR trains) between each city pair.

12. **Budget enforcement.** Pass max_price_per_night to search_hotels and max_price_usd to search_flights when user has a budget. Warn when options are limited within budget. For luxury travelers, never cap unless they give you a budget.

13. **Ground transport — profile-aware:**
    - Adventure: trains/buses first. "The 22:00 sleeper to Chiang Mai is legendary — you wake up refreshed and skip a hostel night." Flag overnight options as accommodation savers.
    - Luxury: offer private transfers or first-class rail. Frame as convenience, not cost.
    - Rail passes: for adventure travelers doing 3+ cities in Europe or Japan, proactively mention Interrail/Eurail/Japan Rail Pass — use web_search("Interrail pass vs point-to-point [route]") to research.

14. **Currency.** When user's preferred currency is not USD, call get_exchange_rate and include converted amounts. For multi-country trips (e.g. Serbia → North Macedonia → Albania), batch all destination currencies in one call.

15. **Accommodation — profile-aware:**
    - Adventure: search_hotels with accommodation_type="hostel" or "dorm" by default
    - Mid-range: "guesthouse" or "hotel" with star filter
    - Luxury: "hotel" with min_stars=4+, use room_type and special_requests in book_hotel (e.g. room_type="harbour suite", special_requests="early check-in, high floor preferred")

16. **Slow travel / base-yourself mode.** When an adventure traveler wants to stay 5+ days in one place, reframe around a home base: lead with the best hostel/guesthouse for that duration, then offer day trip options. Don't force a moving itinerary structure.

17. **Visa & entry intel.** When destination involves complex visa rules (Central Asia, the Balkans, Southeast Asia border crossings), proactively use web_search to check current requirements. Surface border crossing practicalities, not just visa policy.

18. **Experiences & activities.** Use search_experiences when building itinerary days — always pull real activity options for each destination. Category guidance:
    - Adventure: category="adventure" or "nature" — hiking, kayaking, cycling tours
    - Mid-range: category="tour" or "culture" — city walks, cooking classes, day trips
    - Luxury: category="culture" or "food" — exclusive tastings, private tours, insider access
    - Museums/history: category="museum" or "history"
    - Food travelers: category="food" for food tours, market visits, cooking classes
    Always include price_usd in itinerary items sourced from search_experiences.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Adventure Travelers: Fellow traveler energy. Practical, enthusiastic, full of local tips. Direct about trade-offs. "The 22:00 sleeper saves you a hostel night and gets you there at dawn — legendary."

Mid-Range: Knowledgeable friend. Warm, balanced, good-value-focused. "This guesthouse gets the balance right — solid location, excellent reviews, free cancellation."

Luxury Travelers: Personal concierge. Warm, authoritative, effortless. Anticipate needs. "I'd lean toward the Ritz-Carlton for the harbour suite — the private butler service is genuinely exceptional there. I can also note a request for early check-in."
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
            "search_experiences": lambda i: search_experiences(**i),
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
