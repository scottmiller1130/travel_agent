# Travel Agent

A personal AI travel agent powered by Claude. Plans trips, searches flights and hotels, checks weather, builds day-by-day itineraries, and books on your behalf — all from the terminal.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 3. (Optional) Set up your preferences for personalized recommendations
python cli.py --setup

# 4. Start chatting
python cli.py
```

## Example Conversations

```
You: Plan a 5-day trip to Lisbon in May for 2 people, budget around $3000
You: Find me flights from New York next weekend, I prefer window seats
You: What's the weather like in Bali in July? Should I bring a rain jacket?
You: Book the cheapest hotel you found
You: I always fly Delta and avoid middle seats — remember that
```

## CLI Commands

| Command | Description |
|---|---|
| `python cli.py` | Start the travel agent |
| `python cli.py --setup` | Interactive preference setup wizard |
| `python cli.py --trips` | View all saved trips |
| `python cli.py --prefs` | View your saved preferences |
| `reset` (in chat) | Start a new conversation |
| `trips` (in chat) | View saved trips |
| `quit` (in chat) | Exit |

## Architecture

```
cli.py                    # Rich terminal UI + booking confirmation prompts
agent/
  core.py                 # Agentic loop (Claude ↔ tools ↔ user)
  tools_schema.py         # Tool definitions Claude uses to reason about capabilities
tools/
  flights.py              # Flight search & booking (mock → Amadeus/Duffel)
  hotels.py               # Hotel search & booking (mock → Booking.com/Expedia)
  weather.py              # Weather forecasts (mock → OpenWeatherMap)
  maps.py                 # Places & distances (mock → Google Maps)
  calendar.py             # Availability & calendar events
  search.py               # Web search for travel research (mock → Brave/Serper)
memory/
  preferences.py          # SQLite-backed user preferences (airlines, budget, etc.)
  trips.py                # SQLite-backed trip history
```

## How the Agent Works

1. **User sends a message** → agent appends to conversation
2. **Claude reasons** about what info it needs and calls tools in parallel
3. **Tools execute** (flight search, weather, maps, etc.) and results feed back
4. **Claude synthesizes** results into a plan, recommendation, or action
5. **Before booking**: agent pauses and asks for explicit confirmation
6. **After booking**: calendar is updated, trip is saved to memory

## Connecting Real APIs

Each tool file has a comment showing where to add the real API call. Set the corresponding env var in `.env` and the mock will be bypassed:

| Service | Env Var | Tool |
|---|---|---|
| Amadeus (flights) | `AMADEUS_CLIENT_ID` + `AMADEUS_CLIENT_SECRET` | `tools/flights.py` |
| Booking.com (hotels) | `BOOKING_API_KEY` | `tools/hotels.py` |
| OpenWeatherMap | `OPENWEATHER_API_KEY` | `tools/weather.py` |
| Google Maps | `GOOGLE_MAPS_API_KEY` | `tools/maps.py` |
| Brave Search | `BRAVE_SEARCH_API_KEY` | `tools/search.py` |
| Google Calendar | `GOOGLE_CALENDAR_API_KEY` | `tools/calendar.py` |
