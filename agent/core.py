"""
Core agentic loop for the travel agent.

The agent runs in a loop: send messages to Claude → Claude calls tools →
execute tools → feed results back → repeat until Claude returns a final answer.

Booking actions (book_flight, book_hotel) require explicit user confirmation
before payment_confirmed is set to True.
"""

import base64
import copy
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable

import anthropic

from agent.tools_schema import TOOLS
from memory.preferences import PreferenceStore
from memory.trips import TripStore
from memory.users import PLAN_LIMITS, UserStore
from tools.advisory import get_travel_advisory
from tools.budget import get_budget_status, log_expense
from tools.calendar import add_to_calendar, check_availability
from tools.currency import get_exchange_rate
from tools.experiences import search_experiences
from tools.flights import (
    book_flight,
    find_cheapest_dates,
    find_cheapest_month,
    search_flights,
)
from tools.hotels import book_hotel, search_hotels
from tools.inspiration import get_inspiration
from tools.maps import get_distance, search_places
from tools.packing import generate_packing_list
from tools.search import web_search
from tools.transport import search_ground_transport
from tools.visa import get_visa_requirements
from tools.weather import get_weather

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


def _tool_use_ids(msg: dict) -> set[str]:
    """Return the set of tool_use IDs in an assistant message."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return set()
    return {b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")}


def _tool_result_ids(msg: dict) -> set[str]:
    """Return the set of tool_use_ids referenced by tool_result blocks in a user message."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return set()
    return {
        b["tool_use_id"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id")
    }


def _heal_conversation(conversation: list[dict]) -> list[dict]:
    """Return a copy of *conversation* with every structural violation removed.

    The Anthropic API enforces these invariants that can be broken by trimming,
    DB corruption, aborted requests, or conversation stitching:

      (0) Trailing assistant-with-tool_use  — _sanitize_conversation strips it.
      (1) Leading assistant message          — API requires first message = user.
      (2) Consecutive same-role messages    — two users or two assistants in a row.
      (3) Orphaned tool_result (Case 1)     — user message whose tool_result IDs
            have no matching tool_use in the immediately preceding assistant turn.
      (4) Orphaned tool_use (Case 2)        — assistant message whose tool_use IDs
            are not all answered in the immediately following user turn.

    The loop repeats until no pass finds anything to remove, because fixing one
    violation can expose another (e.g. removing an assistant(tool_use) may leave
    two adjacent user messages that then trigger rule 2).
    """
    result = _sanitize_conversation(list(conversation))

    changed = True
    while changed:
        changed = False

        # Rule 1: conversation must start with a user message.
        if result and result[0].get("role") != "user":
            result.pop(0)
            result = _sanitize_conversation(result)
            changed = True
            continue

        for i, msg in enumerate(result):
            role = msg.get("role")

            # Rule 2: no two consecutive messages with the same role.
            if i > 0 and result[i - 1].get("role") == role:
                # Keep the later message — it has more recent context.
                # Exception: if the earlier message holds tool_results that are
                # still needed, keep the earlier one instead.
                keep_later = not bool(_tool_result_ids(result[i - 1]))
                result.pop(i - 1 if keep_later else i)
                result = _sanitize_conversation(result)
                changed = True
                break

            content = msg.get("content", [])
            if not isinstance(content, list):
                content = []

            if role == "user":
                # Rule 3 (Case 1): orphaned tool_result — no matching tool_use
                # in the immediately preceding assistant message.
                result_ids = _tool_result_ids(msg)
                if not result_ids:
                    continue
                prev = result[i - 1] if i > 0 else None
                use_ids = _tool_use_ids(prev) if prev else set()
                if result_ids - use_ids:
                    result.pop(i)
                    result = _sanitize_conversation(result)
                    changed = True
                    break

            elif role == "assistant":
                # Rule 4 (Case 2): orphaned tool_use — not all IDs answered by
                # the immediately following user message's tool_results.
                use_ids = _tool_use_ids(msg)
                if not use_ids:
                    continue
                next_msg = result[i + 1] if i + 1 < len(result) else None
                result_ids = _tool_result_ids(next_msg) if next_msg else set()
                if use_ids - result_ids:
                    result.pop(i)
                    result = _sanitize_conversation(result)
                    changed = True
                    break

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

def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 characters per token for conversational content.

    Avoids an extra API call just to count tokens. Good enough for trimming
    decisions — we're targeting a 15k-token threshold, not an exact limit.

    Document blocks (base64 PDFs) are excluded from counting — they are
    stripped from history after the first API call via _strip_document_blocks,
    so their multi-MB base64 data never inflates the estimate.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "document":
                        continue  # base64 PDFs would inflate the estimate wildly
                    total += len(json.dumps(block))
    return total // 4


def _strip_document_blocks(conversation: list[dict]) -> list[dict]:
    """Replace document (base64 PDF) blocks in all but the last user message
    with a slim placeholder so the multi-MB base64 payload is never re-sent.

    After the first API call the model has already seen the document.  Keeping
    the full base64 in the conversation list would (a) inflate _estimate_tokens,
    causing the tail-builder to discard nearly the entire history, and (b)
    waste tokens on every subsequent call.
    """
    if not conversation:
        return conversation

    result = []
    # Find the index of the last user message that contains a document block.
    last_doc_user_idx = -1
    for i, msg in enumerate(conversation):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "document" for b in content
            ):
                last_doc_user_idx = i

    for i, msg in enumerate(conversation):
        if i == last_doc_user_idx:
            # Keep this one intact — it's the one that will be (or was just) sent.
            result.append(msg)
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        # Strip document blocks from older messages, leaving a text placeholder.
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "document":
                source = block.get("source", {})
                media = source.get("media_type", "document")
                new_content.append({
                    "type": "text",
                    "text": f"[{media} attachment was processed in a prior turn]",
                })
            else:
                new_content.append(block)
        result.append({**msg, "content": new_content})

    return result


TOOL_LABELS = {
    "search_experiences": "Finding tours & experiences...",
    "get_inspiration":    "Reading your inspiration source...",
    "log_expense":        "Logging expense...",
    "get_budget_status":  "Checking your budget...",
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
    "get_visa_requirements": "Checking visa requirements...",
    "get_travel_advisory":   "Checking travel advisories...",
    "generate_packing_list": "Building your packing list...",
}


# Tool list with a prompt-cache breakpoint on the last entry.
# Anthropic will cache the entire tools block on the first call and charge
# only 10% of normal input-token price on subsequent cache hits, cutting the
# ~8,800-token tool-schema cost by ~90% for every call after the first.
_tools_with_cache: list[dict] = copy.deepcopy(TOOLS)
_tools_with_cache[-1]["cache_control"] = {"type": "ephemeral"}

# Actions that require a human confirmation step before proceeding
CONFIRMATION_REQUIRED = {"book_flight", "book_hotel"}

# Tools counted against the monthly api_calls quota
METERED_TOOLS = {"search_flights", "search_hotels", "find_cheapest_dates", "find_cheapest_month", "search_experiences"}

MAX_CONVERSATION_TOKENS = 15_000  # Trim when estimated conversation history exceeds this
KEEP_TAIL_TOKEN_BUDGET  = 10_000  # Keep the most recent messages that fit in this budget
KEEP_INITIAL_MESSAGES   = 2       # Always keep the very first user/assistant exchange

SYSTEM_PROMPT = """You are a world-class personal travel concierge — the most capable AI travel planner available. You combine the depth of a specialist travel agent with the speed of AI and real data across flights, hotels, experiences, weather, currency, transport, and deal-hunting. You adapt fluidly to three traveler profiles and handle everything from visa research to day-by-day itinerary creation.

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

19. **Inspiration import ("Start Anywhere").** When the user shares a URL, blog post, YouTube link, TripAdvisor article, or pastes text/notes, use get_inspiration to extract destinations and activity ideas from it. This is a core feature — turning any inspiration source into a concrete trip plan. After get_inspiration returns content, immediately identify the destinations, ask a quick clarifying question (dates? who's travelling?), then start planning.

20. **Expense tracking.** When the user mentions actual spending during a trip (e.g. "dinner cost $60", "paid $220 for hotel"), use log_expense to track it. Use get_budget_status when they ask how much they've spent or whether they're on budget. Always relate spending back to the per-day budget target.

21. **Local & authentic experiences.** Beyond tourist highlights, surface local gems: neighborhood food markets, family-run guesthouses with character, transport options locals actually use (regional trains, shared vans). For adventure travelers especially, the best experiences are often free or very cheap — focus on those.

22. **Trip pacing by traveler type:**
    - Adventure: slow is good. One place 5+ days. Day trips from a base. Evening = local bar or night market, not the tourist strip.
    - Mid-range: rhythm of 2-3 nights per city. Must-sees + one hidden gem per day. Rest built in.
    - Luxury: fewer destinations, deeper experiences. 3+ nights minimum. Private guides, curated restaurants, pre-booked entrances.

23. **Multi-person trips.** When the user mentions traveling with others (partner, family, group of friends), note it and factor it into: room types, group vs private tours, table booking timing, budget per-person vs total.

24. **Proactive deal alerts.** If the user hasn't fixed their dates, always run find_cheapest_dates or find_cheapest_month in the background for the route. Present the best date as the default recommendation. For luxury, frame as "best travel window" (lowest crowds, best weather) not "cheapest."

25. **Language & local context.** For non-English destinations, include: key phrases, tipping customs, whether credit cards are widely accepted, and any local etiquette that matters (dress codes, haggling culture, tuk-tuk scams to avoid).

26. **Visa & entry requirements.** Use get_visa_requirements whenever planning an international trip. Call it proactively — don't wait for the user to ask. Surface the result early in the planning conversation so visa lead-time doesn't surprise them. For multi-destination trips, call it for each country.

27. **Travel advisories.** Use get_travel_advisory for any destination outside Western Europe, North America, Japan, Singapore, Australia, and New Zealand. Always call it for: Middle East, Africa, Central/South Asia, Southeast Asia (Level 2+ risk), Latin America, Eastern Europe. Present the level clearly. For Level 3 or 4, strongly flag it and recommend the user check official sources.

28. **Packing lists.** Use generate_packing_list when the user asks "what should I pack?", "help me pack", or is finalising their itinerary. Pass the correct climate (warm/tropical/mild/cold/snowy/desert), duration_days, activities (from the itinerary), and traveler_profile. Present the list in clean sections. For adventure travelers: emphasise the light-packing tips. For luxury: skip the budget gear items.

TONE

Adventure Travelers: Fellow traveler energy. Practical, enthusiastic, full of local tips. Direct about trade-offs. "The 22:00 sleeper saves you a hostel night and gets you there at dawn — legendary."

Mid-Range: Knowledgeable friend. Warm, balanced, good-value-focused. "This guesthouse gets the balance right — solid location, excellent reviews, free cancellation."

Luxury Travelers: Personal concierge. Warm, authoritative, effortless. Anticipate needs. "I'd lean toward the Ritz-Carlton for the harbour suite — the private butler service is genuinely exceptional there. I can also note a request for early check-in."
"""

# ── Profile-section splitting ─────────────────────────────────────────────────
# When the traveler_profile is already known we only need to send ONE profile
# section instead of all three.  Skipping the other two saves ~200 tokens on
# every API call in the agentic loop.
#
# We derive the parts from SYSTEM_PROMPT at import time so the source of truth
# stays in one place and the split stays in sync automatically.
def _split_system_prompt(prompt: str) -> tuple[str, dict[str, str], str]:
    """Return (intro, {profile_key: block}, tail) from SYSTEM_PROMPT."""
    adv_marker = '**Adventure Traveler (traveler_profile = "adventure")**'
    mid_marker = '**Mid-Range Traveler (traveler_profile = "mid_range")**'
    lux_marker = '**Luxury / Affluent Traveler (traveler_profile = "luxury")**'
    det_marker = "━" * 40 + "\nPROFILE DETECTION"

    adv_start = prompt.index(adv_marker)
    mid_start = prompt.index(mid_marker)
    lux_start = prompt.index(lux_marker)
    det_start = prompt.index(det_marker)

    intro  = prompt[:adv_start]
    blocks = {
        "adventure": prompt[adv_start:mid_start],
        "mid_range":  prompt[mid_start:lux_start],
        "luxury":     prompt[lux_start:det_start],
    }
    tail = prompt[det_start:]
    return intro, blocks, tail

_SYSTEM_INTRO, _PROFILE_BLOCKS, _SYSTEM_TAIL = _split_system_prompt(SYSTEM_PROMPT)


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
        self._expenses: list[dict] = []   # in-session expense tracker
        self._progress_callback = None
        self._system_prompt_cache: str | None = None

    def _trim_conversation(self) -> None:
        """Keep conversation within token budget using a sliding window.

        Preserves the first KEEP_INITIAL_MESSAGES messages (original intent)
        and the most recent messages that fit within KEEP_TAIL_TOKEN_BUDGET,
        dropping the middle when estimated tokens exceed MAX_CONVERSATION_TOKENS.

        Uses a character-based approximation (~4 chars/token) to avoid the cost
        of a separate counting API call.
        """
        if _estimate_tokens(self._conversation) <= MAX_CONVERSATION_TOKENS:
            return

        head = self._conversation[:KEEP_INITIAL_MESSAGES]

        # Build the tail greedily from the end, respecting the token budget.
        tail: list[dict] = []
        tail_tokens = 0
        for msg in reversed(self._conversation[KEEP_INITIAL_MESSAGES:]):
            msg_tokens = _estimate_tokens([msg])
            if tail_tokens + msg_tokens > KEEP_TAIL_TOKEN_BUDGET:
                break
            tail.insert(0, msg)
            tail_tokens += msg_tokens

        dropped = len(self._conversation) - len(head) - len(tail)
        bridge = {
            "role": "user",
            "content": (
                f"[{dropped} earlier messages were trimmed to stay within context limits. "
                "The conversation above captures the original request; the messages below "
                "are the most recent exchanges.]"
            ),
        }
        # Heal the fully-assembled conversation so that stitching head + bridge
        # + tail together doesn't leave orphaned tool_use blocks in the head or
        # orphaned tool_results at the start of the tail.
        self._conversation = _heal_conversation(head + [bridge] + tail)

    def chat(
        self,
        user_message: str,
        progress_callback: Callable | None = None,
        file_bytes: bytes | None = None,
        file_name: str | None = None,
        file_media_type: str | None = None,
    ) -> str:
        """Send a message and run the agentic loop until a final response is produced.

        Args:
            progress_callback: Optional callable(event_type: str, data: dict).
                               Called with ("tool_start", {"tool": ..., "label": ...})
                               and ("tool_done", {"tool": ...}) around each tool call.
            file_bytes: Raw bytes of an attached file (PDF or plain text).
            file_name: Original filename, used for context in the prompt.
            file_media_type: MIME type — "application/pdf" or "text/plain".
        """
        self._progress_callback = progress_callback

        if file_bytes:
            content = self._build_file_content(user_message, file_bytes, file_name, file_media_type)
        else:
            content = user_message
        self._conversation.append({"role": "user", "content": content})
        # Strip base64 document payloads from older turns before trimming/healing
        # so the multi-MB data doesn't inflate _estimate_tokens and cause the
        # tail-builder to discard the entire conversation history.
        self._conversation = _strip_document_blocks(self._conversation)
        self._trim_conversation()
        # Heal the full conversation before every API call so any corruption
        # (from DB, trimming, or a previous crash) is fixed regardless of cause.
        self._conversation = _heal_conversation(self._conversation)
        system = self._build_system_prompt()

        # Wrap the system prompt in a list block so Anthropic can cache it.
        # This covers the ~3,000-token system prompt at ~10% of normal cost
        # after the first call.
        cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

        _api_retry_delays = [2, 4, 8]  # seconds between retries for transient errors

        while True:
            for attempt, _delay in enumerate([0] + _api_retry_delays):
                if _delay:
                    time.sleep(_delay)
                try:
                    response = self._client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=cached_system,
                        tools=_tools_with_cache,
                        messages=self._conversation,
                    )
                    break  # success — exit retry loop
                except anthropic.BadRequestError as e:
                    body = e.body or {}
                    err = body.get("error", {}) if isinstance(body, dict) else {}
                    if "usage limits" in err.get("message", "").lower():
                        raise RuntimeError(
                            "The AI service is temporarily unavailable due to an API usage limit on the server. "
                            "Please try again later or contact support."
                        ) from None
                    raise  # non-retryable bad request
                except (anthropic.APIStatusError, anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                    status = getattr(e, "status_code", None)
                    # 529 = overloaded, 529/503/502 = transient server errors
                    retryable = status in (429, 502, 503, 529) or isinstance(
                        e, (anthropic.APIConnectionError, anthropic.APITimeoutError)
                    )
                    if retryable and attempt < len(_api_retry_delays):
                        log.warning("Anthropic API transient error (status=%s), retrying in %ds…", status, _api_retry_delays[attempt])
                        continue
                    if status in (429, 502, 503, 529):
                        raise RuntimeError(
                            "The AI service is temporarily overloaded. Please wait a moment and try again."
                        ) from None
                    raise

            self._conversation.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})

            if response.stop_reason == "end_turn":
                text = self._extract_text(response.content)
                return text if text else "Done! Let me know if you'd like any changes."

            if response.stop_reason == "tool_use":
                tool_blocks = [b for b in response.content if b.type == "tool_use"]

                # Notify the UI about every tool that's about to run so the
                # progress indicators appear all at once before any work starts.
                for block in tool_blocks:
                    if progress_callback:
                        progress_callback("tool_start", {
                            "tool": block.name,
                            "label": TOOL_LABELS.get(block.name, f"Using {block.name}..."),
                        })

                # Booking tools require a blocking confirmation dialog — run
                # everything serially in that case to avoid concurrent modals.
                # Otherwise dispatch all tools in parallel for speed.
                has_confirmation = any(b.name in CONFIRMATION_REQUIRED for b in tool_blocks)
                n_workers = 1 if has_confirmation else len(tool_blocks)

                results_map: dict[str, dict] = {}
                with ThreadPoolExecutor(max_workers=n_workers) as pool:
                    future_to_block = {
                        pool.submit(self._dispatch_tool, b.name, b.input): b
                        for b in tool_blocks
                    }
                    try:
                        for future in as_completed(future_to_block, timeout=TOOL_TIMEOUT_SECONDS):
                            block = future_to_block[future]
                            try:
                                result = future.result()
                                log.info("tool %s OK", block.name)
                            except Exception as e:
                                log.error("tool %s error: %s", block.name, e)
                                result = {"status": "error", "message": str(e)}
                            if progress_callback:
                                progress_callback("tool_done", {"tool": block.name})
                            results_map[block.id] = result
                    except FuturesTimeoutError:
                        # One or more tools exceeded the timeout; error out any
                        # that didn't finish and fire their done callbacks.
                        for future, block in future_to_block.items():
                            if block.id not in results_map:
                                log.warning("tool %s timed out after %ds", block.name, TOOL_TIMEOUT_SECONDS)
                                if progress_callback:
                                    progress_callback("tool_done", {"tool": block.name})
                                results_map[block.id] = {
                                    "status": "error",
                                    "message": f"Tool '{block.name}' timed out after {TOOL_TIMEOUT_SECONDS}s.",
                                }

                # Assemble tool_results in original block order so the
                # conversation remains valid regardless of completion order.
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": json.dumps(results_map[b.id]),
                    }
                    for b in tool_blocks
                ]

                if tool_results:
                    self._conversation.append({"role": "user", "content": tool_results})
                continue

            break

        text = self._extract_text(response.content)
        return text if text else "Done! Let me know if you'd like any changes."

    def reset(self):
        """Start a fresh conversation (keeps memory/preferences)."""
        self._conversation = []
        self._current_trip = {}
        self._system_prompt_cache = None

    # ── Persistence helpers ───────────────────────────────────────────────────

    def get_conversation(self) -> list[dict]:
        """Return the conversation as a JSON-serialisable list."""
        return self._conversation

    def load_conversation(self, conversation: list[dict]) -> None:
        """Restore a previously saved conversation, healing any incomplete tool turns."""
        self._conversation = _heal_conversation(conversation)

    def get_itinerary(self) -> dict | None:
        """Return the most recent itinerary pushed via update_itinerary."""
        return self._current_trip if self._current_trip else None

    def load_itinerary(self, itinerary: dict | None) -> None:
        """Restore a previously saved itinerary."""
        self._current_trip = itinerary or {}

    def _build_file_content(
        self,
        user_message: str,
        file_bytes: bytes,
        file_name: str | None,
        file_media_type: str | None,
    ) -> list[dict]:
        """Build an Anthropic content block list that includes the attached file.

        PDFs are sent as native document blocks so Claude reads them directly.
        Plain-text files are inlined as a fenced code block in a text message.
        """
        label = f'"{file_name}"' if file_name else "the attached document"
        instruction = (
            user_message
            or f"Please analyse {label} and import any itinerary it contains."
        )

        if file_media_type == "application/pdf":
            return [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(file_bytes).decode(),
                    },
                },
                {"type": "text", "text": instruction},
            ]

        # Plain text / markdown — inline the content
        text_content = file_bytes.decode("utf-8", errors="replace")
        return [
            {
                "type": "text",
                "text": (
                    f"[Attached file: {file_name or 'document'}]\n\n"
                    f"```\n{text_content}\n```\n\n{instruction}"
                ),
            }
        ]

    def _build_system_prompt(self) -> str:
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache

        # When the traveler profile is known, send only the relevant profile
        # section instead of all three, saving ~200 tokens per API call.
        profile = self._prefs.get("traveler_profile", user_id=self._user_id)
        if profile in _PROFILE_BLOCKS:
            base_prompt = _SYSTEM_INTRO + _PROFILE_BLOCKS[profile] + _SYSTEM_TAIL
        else:
            base_prompt = SYSTEM_PROMPT

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
        self._system_prompt_cache = f"{base_prompt}\n\n{prefs_context}\n\n{trips_context}{itinerary_context}"
        return self._system_prompt_cache

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
            "get_inspiration":    lambda i: get_inspiration(**i),
            "log_expense":        self._handle_log_expense,
            "get_budget_status":  self._handle_get_budget_status,
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
            "get_visa_requirements": lambda i: get_visa_requirements(**i),
            "get_travel_advisory":   lambda i: get_travel_advisory(**i),
            "generate_packing_list": lambda i: generate_packing_list(**i),
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
        self._system_prompt_cache = None
        return {"status": "success", "message": f"Preference '{key}' saved: {value}"}

    def _handle_save_trip(self, inputs: dict) -> dict:
        trip = inputs["trip"]
        trip_id = self._trips.save_trip(trip, user_id=self._user_id)
        self._current_trip = trip
        self._system_prompt_cache = None
        return {"status": "success", "trip_id": trip_id, "message": "Trip saved."}

    def _handle_update_itinerary(self, inputs: dict) -> dict:
        self._current_trip = inputs  # persist so get_itinerary() is always current
        self._system_prompt_cache = None
        # Auto-save every itinerary push to TripStore so nothing is ever lost.
        # Derive a stable ID from destination + start_date so repeated updates
        # to the same trip overwrite the existing row rather than duplicating.
        try:
            if not inputs.get("id"):
                import hashlib
                key = f"{inputs.get('destination', '')}-{inputs.get('start_date', '')}"
                inputs["id"] = "TRIP-" + hashlib.md5(key.encode()).hexdigest()[:10]
            self._trips.save_trip(inputs, user_id=self._user_id)
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

    def _handle_log_expense(self, inputs: dict) -> dict:
        result = log_expense(
            expenses=self._expenses,
            category=inputs.get("category", "other"),
            amount_usd=inputs["amount_usd"],
            description=inputs["description"],
            date=inputs.get("date"),
        )
        # result mutates self._expenses in place via the list reference
        return result

    def _handle_get_budget_status(self, inputs: dict) -> dict:
        return get_budget_status(
            expenses=self._expenses,
            trip_budget_usd=inputs.get("trip_budget_usd"),
        )

    def _handle_get_trips(self, inputs: dict) -> dict:
        status = inputs.get("status")
        trips = self._trips.get_all_trips(status=status, user_id=self._user_id)
        return {"status": "success", "trips": trips, "count": len(trips)}

    @staticmethod
    def _extract_text(content: list) -> str:
        parts = [
            block.text
            for block in content
            if hasattr(block, "type") and block.type == "text" and block.text
        ]
        return " ".join(parts)
