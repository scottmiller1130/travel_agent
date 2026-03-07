# Architecture Deep-Dive

This document explains how the Travel Agent is built, how its components interact, and the key design decisions behind it.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Backend Components](#backend-components)
  - [FastAPI Server](#fastapi-server-serverpy)
  - [Agent Core](#agent-core-agentcorepy)
  - [Tools Layer](#tools-layer)
  - [Memory Layer](#memory-layer)
- [Frontend Architecture](#frontend-architecture-staticindexhtml)
- [Data Flows](#data-flows)
  - [Message → Response](#1-message--response-flow)
  - [Booking Confirmation](#2-booking-confirmation-flow)
  - [Session Restore](#3-session-restore-flow)
  - [Deal Hunting](#4-deal-hunting-flow)
- [External APIs](#external-apis)
- [Data Models](#data-models)
- [Security Architecture](#security-architecture)
- [Deployment Architecture](#deployment-architecture)

---

## System Architecture

```mermaid
graph TD
    subgraph Browser["Browser"]
        SPA["Single-Page App\nindex.html\n(HTML + CSS + JS)"]
    end

    subgraph Server["FastAPI Server  —  server.py"]
        Routes["HTTP Routes\nPOST /api/chat\nGET /api/session\nDELETE /api/session"]
        SSE["SSE Stream\nReal-time events"]
        RateLimit["Rate Limiter\n20 req/min per IP"]
        AgentCache["In-memory\nAgent Cache"]
    end

    subgraph AgentLayer["Agent Layer  —  agent/"]
        Core["core.py\nAgentic Loop"]
        Schema["tools_schema.py\n16 Tool Definitions"]
    end

    subgraph ToolsLayer["Tools Layer  —  tools/"]
        Flights["flights.py"]
        Hotels["hotels.py"]
        Weather["weather.py"]
        Maps["maps.py"]
        Search["search.py"]
        Seasons["seasons.py\n(in-process)"]
        Calendar["calendar.py"]
    end

    subgraph MemoryLayer["Memory Layer  —  memory/"]
        Prefs["preferences.py\nUser Settings"]
        Trips["trips.py\nTrip History"]
        Sessions["sessions.py\nConversations"]
    end

    subgraph ExternalAPIs["External APIs"]
        Claude["Anthropic\nClaude Sonnet 4.6"]
        Amadeus["Amadeus API\n(flights + hotels)"]
        OpenMeteo["Open-Meteo\n(weather, free)"]
        OSM["OpenStreetMap\n(maps + POI, free)"]
        Wiki["Wikipedia\n(search, free)"]
        Brave["Brave Search\n(optional)"]
    end

    subgraph Storage["SQLite Storage  (~/.travel_agent/)"]
        SessionsDB[("sessions.db")]
        PrefsDB[("preferences.db")]
        TripsDB[("trips.db")]
    end

    SPA -->|"POST /api/chat\n{message, session_id}"| Routes
    Routes -->|SSE events| SPA
    Routes --> RateLimit
    Routes --> AgentCache
    AgentCache --> Core
    Core -->|"messages + tool schemas"| Claude
    Claude -->|"tool_use blocks"| Core
    Core --> Schema
    Core -->|parallel dispatch| Flights
    Core -->|parallel dispatch| Hotels
    Core -->|parallel dispatch| Weather
    Core -->|parallel dispatch| Maps
    Core -->|parallel dispatch| Search
    Core -->|parallel dispatch| Calendar
    Core --> MemoryLayer

    Flights --> Amadeus
    Hotels --> Amadeus
    Hotels --> OSM
    Weather --> OpenMeteo
    Maps --> OSM
    Search --> Wiki
    Search --> Brave

    Prefs --> PrefsDB
    Trips --> TripsDB
    Sessions --> SessionsDB
```

---

## Backend Components

### FastAPI Server (`server.py`)

The server is the entry point for all web traffic. It manages:

- **Sessions**: Each browser tab gets a UUID `session_id`. The server caches `TravelAgent` instances in memory and restores from SQLite on cache miss.
- **SSE Streaming**: Chat responses are streamed via Server-Sent Events so the UI updates in real-time as tools execute.
- **Rate Limiting**: A sliding window of 20 requests/minute per IP protects against abuse.
- **Persistence**: After each chat exchange, the conversation and current itinerary are written to `sessions.db`.

**Key routes:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve `static/index.html` |
| `POST` | `/api/chat/{session_id}` | Send a message; returns SSE stream |
| `GET` | `/api/session/{session_id}` | Restore a session (conversation + itinerary) |
| `DELETE` | `/api/session/{session_id}` | Clear a session (start fresh) |
| `GET` | `/api/sessions` | List all sessions |

**SSE event types emitted during a chat:**

```
tool_start      → { "tool": "search_flights", "label": "Searching flights…" }
tool_done       → { "tool": "search_flights" }
itinerary_update → { "itinerary": { "destination": "…", "days": […] } }
deal_result     → { "deal": { "origin": "…", "results": […] } }
month_result    → { "deal": { "origin": "…", "months": […] } }
error           → { "message": "…" }
done            → { "content": "Final agent response text" }
```

---

### Agent Core (`agent/core.py`)

The `TravelAgent` class runs the **agentic reasoning loop**:

```mermaid
flowchart TD
    Start(["agent.chat(message)"])
    Append["Append user message\nto conversation history"]
    BuildPrompt["Build system prompt\n(with user prefs + recent trips)"]
    CallClaude["Call Claude API\n(full conversation + 16 tool schemas)"]
    CheckStop{{"Claude\nend_turn?"}}
    ExtractText["Extract text\nreturn to server"]
    ExtractTools["Extract tool_use blocks"]
    Confirm{{"Booking tool\nwithout confirmation?"}}
    AskUser["Inject confirmation\nrequest into response"]
    Dispatch["Dispatch all tool calls\nin parallel (threads)"]
    Collect["Collect tool results"]
    Append2["Append tool results\nto conversation"]

    Start --> Append
    Append --> BuildPrompt
    BuildPrompt --> CallClaude
    CallClaude --> CheckStop
    CheckStop -->|"yes"| ExtractText
    CheckStop -->|"no"| ExtractTools
    ExtractTools --> Confirm
    Confirm -->|"yes"| AskUser
    AskUser --> ExtractText
    Confirm -->|"no"| Dispatch
    Dispatch --> Collect
    Collect --> Append2
    Append2 --> CallClaude
```

**Key design decisions:**
- **Parallel tool execution**: All tool calls in a single Claude response are dispatched simultaneously using `ThreadPoolExecutor`.
- **Context injection**: User preferences and recent trips are injected into the system prompt on every turn, keeping Claude informed without the user repeating themselves.
- **Conversation history**: The full conversation is sent to Claude each turn (no external memory retrieval needed for short sessions).

---

### Tools Layer

Each tool is a Python module that Claude can call. Tools have two modes:

1. **Real API mode**: Activated when environment variables are set.
2. **Fallback mode**: Uses free public APIs (Open-Meteo, OpenStreetMap, Wikipedia) or mock data.

```mermaid
graph LR
    subgraph flights["flights.py"]
        F1["search_flights()"]
        F2["book_flight()"]
        F3["find_cheapest_dates()"]
        F4["find_cheapest_month()"]
    end
    subgraph hotels["hotels.py"]
        H1["search_hotels()"]
        H2["book_hotel()"]
    end
    subgraph weather["weather.py"]
        W1["get_weather()"]
    end
    subgraph maps["maps.py"]
        M1["search_places()"]
        M2["get_distance()"]
    end
    subgraph search["search.py"]
        S1["web_search()"]
    end
    subgraph calendar["calendar.py"]
        C1["check_availability()"]
        C2["add_to_calendar()"]
    end

    F1 & F2 & F3 & F4 --> Amadeus[("Amadeus API\nor mock")]
    H1 & H2 --> AmadeusH[("Amadeus API\nor OSM + mock")]
    W1 --> OpenMeteo[("Open-Meteo\nor climate profile")]
    M1 & M2 --> OSM[("OpenStreetMap\nNominatim + Overpass")]
    S1 --> WikiBrave[("Wikipedia\nor Brave Search")]
    C1 & C2 --> CalFile[("~/.travel_agent/\ncalendar.json")]
```

#### Tool: `find_cheapest_dates` / `find_cheapest_month`

The deal-hunting flagship feature. Scans flight prices across a flexible date window and returns ranked results:

```
find_cheapest_dates(origin, destination, target_date, flexibility_days=7)
  → Scans [target_date - N ... target_date + N]
  → Returns: best_price, savings_vs_target, days_from_target

find_cheapest_month(origin, destination, year, month)
  → Scans all departure dates in the month
  → Groups by week, applies season multipliers
  → Returns: cheapest_week, price_range, season_context
```

#### Tool: `update_itinerary`

A special tool that has no external API side-effect — it simply causes the server to emit an `itinerary_update` SSE event, which the frontend uses to update the visual trip board in real-time.

---

### Memory Layer

Three SQLite-backed stores in `~/.travel_agent/` (or `$TRAVEL_AGENT_DATA_DIR`):

```mermaid
erDiagram
    PREFERENCES {
        text key PK
        text value_json
        datetime updated_at
    }

    TRIPS {
        text id PK
        text destination
        date start_date
        date end_date
        text status
        text data_json
        datetime created_at
    }

    SESSIONS {
        text session_id PK
        text conversation_json
        text itinerary_json
        datetime created_at
        datetime updated_at
    }
```

**Preference keys stored by default:**

| Key | Example Value |
|---|---|
| `preferred_airlines` | `["Delta", "United"]` |
| `seat_preference` | `"window"` |
| `budget_per_day` | `200` |
| `home_airport` | `"JFK"` |
| `dietary_restrictions` | `["vegetarian"]` |
| `travel_pace` | `"relaxed"` |
| `accommodation_type` | `"hotel"` |

---

## Frontend Architecture (`static/index.html`)

The entire frontend is a single HTML file (~2000 lines). It uses no build step, no npm, and only one CDN dependency (`marked.js` for Markdown rendering).

```mermaid
graph TD
    subgraph Layout["Three-Column Layout"]
        Sidebar["Sidebar (270px)\n• Logo\n• Session list\n• Preferences panel\n• Reset button"]
        Chat["Chat Panel (flex)\n• Message history\n• Tool progress indicators\n• Deal cards\n• Input box"]
        Board["Trip Board (400px)\n• Day-by-day cards\n• Drag-and-drop\n• Budget summary\n• Weather badges"]
    end

    subgraph SSE["SSE Event Handlers"]
        OnToolStart["tool_start\n→ show progress spinner"]
        OnToolDone["tool_done\n→ hide spinner"]
        OnItinerary["itinerary_update\n→ render trip board"]
        OnDeal["deal_result\n→ render deal card"]
        OnDone["done\n→ render agent message"]
    end

    subgraph Components["Key UI Components"]
        DealCard["Deal Card\n• Price vs target date\n• Savings badge\n• Heat map calendar"]
        ItineraryCard["Itinerary Day Card\n• Flight/hotel/activity items\n• Drag handle\n• Remove button"]
        BudgetBar["Budget Bar\n• Category chips\n• Color-coded by % used"]
        WeatherBadge["Season Badge\n• peak / shoulder / off"]
    end
```

**State management** is entirely in plain JavaScript module-level variables:

```javascript
let currentSessionId   // UUID for this browser tab
let currentItinerary   // { destination, days: [...], budget: {...} }
let conversationEl     // DOM reference to chat panel
let activeSseSource    // EventSource for current chat request
```

---

## Data Flows

### 1. Message → Response Flow

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend (JS)
    participant S as Server (SSE)
    participant A as Agent Loop
    participant C as Claude API
    participant T as Tools (parallel)

    U->>F: Submit message
    F->>S: POST /api/chat/{session_id}\n{message: "Plan a trip to Tokyo"}
    activate S
    S-->>F: SSE stream opened
    S->>A: agent.chat(message, progress_cb)
    activate A

    A->>C: {system: "...", messages: [...], tools: [16 schemas]}
    activate C
    C-->>A: tool_use: search_flights(JFK, NRT, 2025-05-10)
    C-->>A: tool_use: get_weather(Tokyo, May)
    C-->>A: tool_use: web_search("Tokyo travel tips")
    deactivate C

    A-->>S: progress("search_flights")
    S-->>F: {event: tool_start, tool: "search_flights"}
    F->>F: Show "Searching flights…" spinner

    par Parallel tool execution
        A->>T: search_flights(...)
    and
        A->>T: get_weather(...)
    and
        A->>T: web_search(...)
    end
    T-->>A: [flight results, weather data, search results]

    A-->>S: progress done
    S-->>F: {event: tool_done, tool: "search_flights"}

    A->>C: tool results appended
    activate C
    C-->>A: tool_use: update_itinerary({days: [...]})
    deactivate C

    A-->>S: itinerary data
    S-->>F: {event: itinerary_update, itinerary: {...}}
    F->>F: Render trip board

    A->>C: tool result
    activate C
    C-->>A: end_turn: "Here's your 7-day Tokyo trip…"
    deactivate C

    A-->>S: final response
    deactivate A
    S-->>F: {event: done, content: "Here's your 7-day Tokyo trip…"}
    deactivate S
    S->>S: persist session to SQLite

    F->>U: Render chat message + trip board
```

---

### 2. Booking Confirmation Flow

```mermaid
sequenceDiagram
    participant U as User
    participant C as Claude
    participant BT as book_flight tool
    participant CAL as Calendar
    participant DB as trips.db

    U->>C: "Book that $842 flight"
    C->>BT: book_flight(JFK, LIS, 2025-05-10,\n  payment_confirmed=false)
    BT-->>C: {"status": "awaiting_confirmation",\n  "message": "Ready to book for $842"}
    C->>U: "I'll book JFK→LIS on May 10 for $842.\nShall I confirm?"
    U->>C: "Yes, go ahead"
    C->>BT: book_flight(JFK, LIS, 2025-05-10,\n  payment_confirmed=true)
    BT-->>C: {"confirmation": "IB3847", "pnr": "XZ9Q2"}
    C->>CAL: add_to_calendar(trip_event)
    C->>DB: save_trip(trip_data)
    C->>U: "✓ Booked! Confirmation: IB3847\nAdded to your calendar."
```

---

### 3. Session Restore Flow

```mermaid
sequenceDiagram
    participant B as Browser (new tab)
    participant S as Server
    participant DB as sessions.db

    B->>B: Generate or read session_id\n(localStorage)
    B->>S: GET /api/session/{session_id}
    S->>DB: SELECT conversation, itinerary WHERE id=?
    alt Session exists
        DB-->>S: {conversation: [...], itinerary: {...}}
        S-->>B: 200 OK with session data
        B->>B: Restore chat history\n+ render trip board
    else No session
        DB-->>S: empty
        S-->>B: 200 OK with empty data
        B->>B: Fresh start
    end
```

---

### 4. Deal Hunting Flow

```mermaid
flowchart TD
    U(["User: Find cheapest\nflights to Paris\nin September"])
    Claude["Claude calls\nfind_cheapest_month(\n  origin=JFK,\n  dest=CDG,\n  year=2025, month=9\n)"]
    Scan["Scan all Sep 2025\ndeparture dates"]
    Season["Apply season\nmultipliers from\nseasons.py database"]
    Rank["Rank by price\nGroup by week"]
    SSE["Emit month_result\nSSE event"]
    UI["Frontend renders\nmonth card with\nheat-map calendar"]

    U --> Claude
    Claude --> Scan
    Scan --> Season
    Season --> Rank
    Rank --> SSE
    SSE --> UI
```

---

## External APIs

### Amadeus (Flights & Hotels)

- Uses OAuth 2.0 client credentials flow; token is cached until expiry.
- **Test sandbox**: `https://test.api.amadeus.com` (free, limited data)
- **Production**: `https://api.amadeus.com` (requires paid plan)
- Set `AMADEUS_HOST` env var to switch between them.

```
Authentication:
  POST /v1/security/oauth2/token
  → access_token (cached, ~30 min TTL)

Flight search:
  GET /v2/shopping/flight-offers
  → price, segments, carriers, cabin class

Hotel search:
  GET /v3/shopping/hotel-offers
  → properties, rates, amenities
```

### Open-Meteo (Weather)

Free, no API key required. Returns hourly/daily forecasts using WMO weather codes.

```
GET https://api.open-meteo.com/v1/forecast
  ?latitude=38.72&longitude=-9.14
  &daily=temperature_2m_max,precipitation_sum,weathercode
  &forecast_days=7
```

### OpenStreetMap (Maps & POI)

Two free APIs used:

- **Nominatim** — Geocoding (place name → lat/lon)
- **Overpass API** — POI search (restaurants, museums, beaches, etc.) using OSM tag queries

```
Nominatim:
  GET https://nominatim.openstreetmap.org/search
  ?q=Lisbon&format=json&limit=1

Overpass:
  POST https://overpass-api.de/api/interpreter
  [out:json]; node["amenity"="restaurant"](around:2000,38.7,-9.1); out 10;
```

### Wikipedia (Search)

Free REST API, no key needed. Returns article summaries for travel research.

```
GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}
GET https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}
```

---

## Data Models

### Itinerary (sent via `update_itinerary` tool)

```json
{
  "destination": "Lisbon, Portugal",
  "start_date": "2025-05-10",
  "end_date": "2025-05-17",
  "travelers": 2,
  "max_budget_usd": 3000,
  "season": "shoulder",
  "days": [
    {
      "day": 1,
      "date": "2025-05-10",
      "title": "Arrival Day",
      "items": [
        {
          "type": "flight",
          "time": "08:30",
          "title": "JFK → LIS",
          "detail": "TAP Air Portugal · 7h 20m",
          "price_usd": 620
        },
        {
          "type": "hotel",
          "title": "Bairro Alto Hotel",
          "detail": "Check-in · Superior Room",
          "price_usd": 180
        },
        {
          "type": "activity",
          "time": "19:00",
          "title": "Dinner in Alfama",
          "detail": "Traditional fado restaurant",
          "price_usd": 45
        }
      ]
    }
  ],
  "budget_breakdown": {
    "flights": 1240,
    "hotels": 1260,
    "activities": 300,
    "food": 350,
    "transport": 100
  }
}
```

### Deal Card (sent via `deal_result` SSE event)

```json
{
  "origin": "JFK",
  "origin_city": "New York",
  "destination": "CDG",
  "destination_city": "Paris",
  "target_date": "2025-09-15",
  "results_by_price": [
    {
      "date": "2025-09-12",
      "price_usd": 680,
      "days_from_target": -3,
      "savings_usd": 145,
      "savings_pct": 17
    }
  ],
  "heatmap": {
    "2025-09-01": 920,
    "2025-09-02": 880,
    "2025-09-12": 680
  }
}
```

---

## Security Architecture

```mermaid
graph TD
    Internet(["Internet"])
    RateLimit["Rate Limiter\n20 req/min per IP"]
    Headers["Security Headers\nCSP · X-Frame-Options\nX-Content-Type-Options"]
    Pydantic["Input Validation\n(Pydantic models)"]
    NoKeys["No API keys in\nfrontend / JS"]
    Confirm["Booking Confirmation\nRequired before payment"]
    SQLite["SQLite\n(local file, not network)"]

    Internet --> RateLimit
    RateLimit --> Headers
    Headers --> Pydantic
    Pydantic --> NoKeys
    NoKeys --> Confirm
    Confirm --> SQLite
```

**Protections in place:**
- Rate limiting prevents API abuse and cost amplification
- CSP blocks XSS by restricting script/style sources
- All API keys are server-side only; the frontend gets only sanitized data
- Booking tools require `payment_confirmed=true`, which Claude only sets after explicit user confirmation
- Pydantic validates all incoming request bodies

---

## Deployment Architecture

### Local Development

```
Developer Machine
├── uvicorn server:app --reload   (port 8000)
├── ~/.travel_agent/
│   ├── sessions.db
│   ├── preferences.db
│   └── trips.db
└── .env  (API keys)
```

### Railway.app Production

```
Railway Project
├── Web Service (Nixpacks from Procfile)
│   └── uvicorn server:app --host 0.0.0.0 --port $PORT
├── Persistent Volume  →  /data
│   ├── sessions.db
│   ├── preferences.db
│   └── trips.db
└── Environment Variables
    ├── ANTHROPIC_API_KEY
    ├── AMADEUS_CLIENT_ID
    ├── AMADEUS_CLIENT_SECRET
    ├── BRAVE_SEARCH_API_KEY
    └── TRAVEL_AGENT_DATA_DIR=/data
```

**`railway.json` config:**
```json
{
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

The `TRAVEL_AGENT_DATA_DIR` environment variable is the key to persistence on Railway — it redirects all SQLite databases to the mounted volume, surviving deployments and restarts.
