# Architecture Deep-Dive

This document explains how Travel Agent is built, how its components interact, and the key design decisions behind it.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Authentication & User Model](#authentication--user-model)
- [Backend Components](#backend-components)
  - [FastAPI Server](#fastapi-server-serverpy)
  - [Agent Core](#agent-core-agentcorepy)
  - [Tools Layer](#tools-layer)
  - [Memory Layer](#memory-layer)
- [Frontend Architecture](#frontend-architecture-staticindexhtml)
- [Data Flows](#data-flows)
  - [Message → Response](#1-message--response-flow)
  - [Auth Flow](#2-auth-flow)
  - [Booking Confirmation](#3-booking-confirmation-flow)
  - [Session Restore](#4-session-restore-flow)
  - [Deal Hunting](#5-deal-hunting-flow)
- [External APIs](#external-apis)
- [Data Models](#data-models)
- [Security Architecture](#security-architecture)
- [Deployment Architecture](#deployment-architecture)

---

## System Architecture

```
Browser  (static/index.html — vanilla JS SPA)
    │  Authorization: Bearer {Clerk JWT}
    │  REST + SSE streaming
    ▼
FastAPI (server.py)
    ├─ Auth: Clerk JWT → user_id  (RS256 via PyJWKClient)
    ├─ Rate limiting: per-user_id (auth) or per-IP (anon)
    ├─ Session ownership enforcement on all endpoints
    │
    ├─ TravelAgent (agent/core.py)
    │   ├─ Anthropic Claude claude-sonnet-4-6  (agentic tool-call loop)
    │   ├─ 18 tool definitions  (flights, hotels, weather, maps …)
    │   └─ Per-user PreferenceStore + TripStore
    │
    └─ PostgreSQL  (Railway managed)
        ├─ sessions          conversation + itinerary per session
        ├─ users             Clerk user records + subscription plan
        ├─ usage             monthly chat_turns + api_calls per user
        ├─ preferences       anonymous/global defaults
        ├─ user_preferences  per-user overrides
        ├─ trips             saved trip history  (per-user)
        ├─ workspaces        collaborative planning spaces + groups (type column)
        ├─ workspace_members roles: owner / editor / viewer
        ├─ invite_logs       DB-backed rate-limit log for outgoing invites
        └─ share_tokens      read-only itinerary share links
```

---

## Authentication & User Model

### Overview

Auth is **optional**. When `CLERK_JWKS_URL` is not set, the app runs in anonymous mode and all features continue to work. When Clerk is configured, users can sign in and get per-user data isolation, higher rate limits, and access to workspaces.

### Auth Flow

```
Browser                          FastAPI                      Clerk
  │                                │                            │
  │  1. User clicks "Sign in"      │                            │
  │─────────────────────────────────────────────────────────── ▶│
  │  2. Clerk modal (email/Google OAuth)                        │
  │  3. Clerk issues JWT (RS256)   │                            │
  │◀─────────────────────────────────────────────────────────── │
  │  4. JS stores token in memory  │                            │
  │                                │                            │
  │  5. Any /api/* request         │                            │
  │     Authorization: Bearer JWT ▶│                            │
  │                                │  6. PyJWKClient.get_key()  │
  │                                │───────────────────────────▶│
  │                                │◀─── public key (cached) ───│
  │                                │                            │
  │                                │  7. jwt.decode(RS256)      │
  │                                │     → user_id, email       │
  │                                │                            │
  │◀── response (user-scoped) ─────│                            │
```

### Key implementation notes

| Component | Detail |
|---|---|
| JWT library | `PyJWT[cryptography]` + `PyJWKClient(jwks_url, cache_keys=True)` |
| Algorithm | RS256 (asymmetric — no shared secret) |
| JWKS caching | Keys are cached in-process; rotations handled automatically |
| Auth-optional | `_user_from_request()` returns `None` if no valid token; all downstream code accepts `user_id=None` |
| `verify_aud` | Disabled — Clerk JWTs don't include an `aud` claim by default |

---

## Backend Components

### FastAPI Server (`server.py`)

The server is the entry point for all web traffic. It manages:

- **Auth**: Clerk JWT verified on every authenticated request via `_user_from_request()`
- **Sessions**: Each browser tab gets a `secrets.token_urlsafe(32)` session ID. The server caches `TravelAgent` instances in memory and restores from PostgreSQL on cache miss.
- **Session ownership**: `_require_session_access(session_id, auth_user)` raises 403 if the authenticated user doesn't own the session.
- **SSE Streaming**: Chat responses are streamed via Server-Sent Events so the UI updates in real-time as tools execute.
- **Rate Limiting**: Authenticated users → 40 req/min (keyed by `user_id`). Anonymous → 20 req/min (keyed by IP).
- **Plan limits**: Before each chat turn, `UserStore.within_limit()` checks the user's plan. Returns HTTP 402 if exhausted.

**Key routes:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/config` | None | Clerk publishable key + auth_enabled flag |
| `GET` | `/api/health` | None | Component status: db, anthropic, serpapi, clerk |
| `POST` | `/api/me` | Required | Upsert user from JWT claims |
| `GET` | `/api/me` | Required | Current user profile + usage + plan limits |
| `POST` | `/api/session/new` | Optional | Create session (links to user if authenticated) |
| `POST` | `/api/chat/{session_id}` | Optional | Chat — SSE stream of tool events + final response |
| `GET` | `/api/itinerary/{session_id}` | Optional | Fetch current itinerary JSON |
| `POST` | `/api/itinerary/{session_id}` | Optional | Save itinerary (drag reorder, travelers update) |
| `POST` | `/api/share/{session_id}` | Optional | Create read-only share link |
| `GET` | `/api/workspaces` | Required | List user's workspaces |
| `POST` | `/api/workspaces` | Required | Create workspace |
| `POST` | `/api/workspaces/{id}/invite` | Required | Invite member by email |
| `GET` | `/api/groups` | Required | List groups the user has joined |
| `POST` | `/api/groups` | Required | Create a new group |
| `GET` | `/api/groups/pending` | Required | Pending group invites for the user's email |
| `GET` | `/api/groups/{id}` | Required | Group details + member list |
| `POST` | `/api/groups/{id}/invite` | Required | Invite member by email (rate-limited) |
| `POST` | `/api/groups/{id}/join` | Required | Claim a pending invite |
| `GET` | `/api/groups/{id}/trips` | Required | All trips from joined members |
| `DELETE` | `/api/groups/{id}/members/{email}` | Required | Remove a member |
| `DELETE` | `/api/groups/{id}` | Required | Delete group (owner only) |

**SSE event types emitted during a chat:**

```
tool_start       → { "tool": "search_flights", "label": "Searching flights…" }
tool_done        → { "tool": "search_flights" }
itinerary_update → { "itinerary": { "destination": "…", "days": […] } }
deal_result      → { "deal": { "origin": "…", "results": […] } }
month_result     → { "deal": { "origin": "…", "months": […] } }
error            → { "message": "…" }
done             → { "content": "Final agent response text" }
```

---

### Agent Core (`agent/core.py`)

The `TravelAgent` class runs the **agentic reasoning loop**:

```
agent.chat(message)
  │
  ├─ Append user message to conversation history
  ├─ Build system prompt
  │   ├─ _prefs.as_context_string(user_id=self._user_id)
  │   └─ _trips.as_context_string(user_id=self._user_id)
  │
  ├─ Call Claude API (full conversation + 18 tool schemas)
  │
  ├─ Claude returns tool_use blocks?
  │   ├─ YES → dispatch all tool calls in parallel (ThreadPoolExecutor)
  │   │        collect results → append to conversation → loop
  │   └─ NO  → extract text → return to server
  │
  └─ Server persists session to PostgreSQL
```

**Per-user isolation in the agent:**

`TravelAgent.__init__(user_id=None)` accepts the authenticated user's ID. All internal calls to `PreferenceStore` and `TripStore` pass `user_id=self._user_id`, ensuring the agent reads and writes only that user's data. Without this, all users would share the same preference pool — a critical security flaw that is prevented by design.

**Key design decisions:**

| Decision | Why |
|---|---|
| Parallel tool execution | All tool calls in a single Claude response dispatch simultaneously via `ThreadPoolExecutor` |
| Context injection | User preferences and recent trips injected into system prompt every turn — Claude stays informed without re-asking |
| Full conversation history | Entire conversation sent to Claude each turn; no external retrieval needed for session-length context |
| Per-session in-memory agent cache | Avoids re-parsing long conversation histories on repeated requests |
| Groups as `workspaces` with `type='group'` | Reuses existing membership infrastructure; `type` column distinguishes persistent trip-sharing groups from session-linked planning workspaces |
| DB-backed invite rate limiting (`invite_logs`) | In-memory limits reset on restart; DB-backed limits persist across deployments and scale to multiple instances |

---

### Tools Layer

Each tool is a Python module that Claude can call. Tools have a fallback chain — the app always returns results even without API keys.

**Flight search priority chain:**

```
search_flights()
  1. SerpAPI (engine=google_flights)   ← primary; real Google Flights prices
     SERPAPI_KEY set?  yes → real data | no → skip
  2. Amadeus GDS                       ← fallback; production or test sandbox
     AMADEUS_CLIENT_ID set?  yes → real data | no → skip
  3. Distance-calculated mock pricing  ← always available; realistic estimates
```

**Tool modules:**

| Module | Functions | Data Source |
|---|---|---|
| `flights.py` | `search_flights`, `book_flight`, `find_cheapest_dates`, `find_cheapest_month` | SerpAPI → Amadeus → mock |
| `hotels.py` | `search_hotels`, `book_hotel` | Amadeus → OSM + mock |
| `weather.py` | `get_weather` | Open-Meteo (free, no key) |
| `maps.py` | `search_places`, `get_distance` | OpenStreetMap / Nominatim / Overpass |
| `search.py` | `web_search` | Brave Search → Wikipedia |
| `seasons.py` | (in-process) | Built-in destination database |
| `calendar.py` | `check_availability`, `add_to_calendar` | Local calendar JSON |

#### Tool: `find_cheapest_dates` / `find_cheapest_month`

```
find_cheapest_dates(origin, destination, target_date, flexibility_days=7)
  → Scans [target_date − N … target_date + N]
  → Returns: best_price, savings_vs_target, days_from_target

find_cheapest_month(origin, destination, year, month)
  → Scans all departure dates in the month
  → Groups by week, applies season multipliers
  → Returns: cheapest_week, price_range, season_context
```

#### Tool: `update_itinerary`

Causes the server to emit an `itinerary_update` SSE event. Accepts `travelers` (integer ≥ 1) and `max_budget_usd` fields in addition to the day-by-day structure. The frontend uses these to render the travelers stepper and per-person budget breakdown.

---

### Memory Layer

All data stored in **PostgreSQL** (Railway managed). Tables are created with `CREATE TABLE IF NOT EXISTS` on first boot; columns added with `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for zero-downtime migrations.

**Database schema:**

```sql
-- Core session storage
sessions (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT,               -- NULL for anonymous; FK to users
    conversation TEXT,               -- JSON conversation history
    itinerary    TEXT,               -- JSON current itinerary
    created_at   TEXT,
    updated_at   TEXT
)

-- User accounts (synced from Clerk JWT claims)
users (
    id         TEXT PRIMARY KEY,     -- Clerk user_id (e.g. user_abc123)
    email      TEXT,
    name       TEXT,
    plan       TEXT DEFAULT 'free',  -- free | pro | team
    created_at TEXT,
    updated_at TEXT
)

-- Monthly usage metering
usage (
    user_id     TEXT,
    month       TEXT,                -- YYYY-MM
    chat_turns  INTEGER DEFAULT 0,
    api_calls   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, month)
)

-- Anonymous/global preferences
preferences (
    key        TEXT PRIMARY KEY,
    value_json TEXT,
    updated_at TEXT
)

-- Per-user preference overrides
user_preferences (
    user_id    TEXT,
    key        TEXT,
    value_json TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_id, key)
)

-- Saved trips (per-user)
trips (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,                 -- NULL = anonymous
    name       TEXT,
    data_json  TEXT,
    created_at TEXT
)

-- Collaborative workspaces and groups
-- type = 'workspace' (session-linked planning) | 'group' (persistent trip sharing)
workspaces (
    id         TEXT PRIMARY KEY,     -- "WS" + token_urlsafe(10)
    name       TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    session_id TEXT,                 -- NULL for groups; linked session for workspaces
    type       TEXT DEFAULT 'workspace',
    created_at TEXT,
    updated_at TEXT
)

-- Workspace / group membership
workspace_members (
    workspace_id  TEXT NOT NULL,
    user_id       TEXT,              -- NULL until invite is claimed via /join
    invited_email TEXT NOT NULL,
    role          TEXT DEFAULT 'editor',  -- owner | editor | viewer
    joined_at     TEXT,
    PRIMARY KEY (workspace_id, invited_email)
)

-- Invite rate-limit log (DB-backed; survives restarts)
-- Limits: 20 total invites/day per user; 3 invites to same email/day per user
invite_logs (
    id            SERIAL PRIMARY KEY,
    inviter_id    TEXT NOT NULL,
    workspace_id  TEXT NOT NULL,
    invited_email TEXT NOT NULL,
    created_at    TEXT NOT NULL
)

-- Read-only share tokens
share_tokens (
    token      TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TEXT
)
```

**Plan limits** (defined in `memory/users.py:PLAN_LIMITS`):

| Plan | `chat_turns`/month | `api_calls`/month |
|---|---|---|
| free | 20 | 10 |
| pro | unlimited (−1) | 200 |
| team | unlimited (−1) | 500 |

---

## Frontend Architecture (`static/index.html`)

The entire frontend is a single HTML file. It uses no build step, no npm, and only one CDN dependency (`marked.js` for Markdown rendering). The Clerk JS SDK is loaded from the Clerk CDN when `CLERK_PUBLISHABLE_KEY` is configured.

**State management** (plain JavaScript module-level variables):

```javascript
let currentSessionId    // token_urlsafe(32) from server
let currentItinerary    // { destination, days, budget, travelers, max_budget_usd }
let _clerk              // Clerk JS SDK instance (null if auth disabled)
let _clerkToken         // current JWT string
let _currentUser        // { id, email, name, plan, usage }
```

**Auth-aware API calls:** All `/api/*` requests go through `window._apiFetch(url, opts)`, which injects `Authorization: Bearer {token}` when a Clerk session is active and falls back to unauthenticated fetch otherwise.

**Key UI panels:**

| Panel | Contents |
|---|---|
| Sidebar (left) | Logo, session list, workspace chip, user profile panel (plan badge + usage bar), sign-in prompt |
| Chat (center) | Message history, tool progress spinners, deal cards, input box |
| Trip Board (right) | Day-by-day cards (draggable), travelers stepper, budget bar + per-person breakdown, weather badges |

---

## Data Flows

### 1. Message → Response Flow

```
User submits message
  │
  ▼
Frontend: _apiFetch POST /api/chat/{session_id}
  Authorization: Bearer {JWT}               ← injected by _apiFetch
  │
  ▼
Server: _user_from_request()               ← verifies JWT via JWKS
  _check_rate_limit(user_id or IP)
  _require_session_access(session_id, user)
  plan limit check (UserStore.within_limit)
  _get_agent(session_id, user_id=uid)
  │
  ▼
TravelAgent.chat(message, progress_cb)
  build system prompt with user prefs + trips
  │
  ▼
Claude API ──► tool_use blocks (parallel)
  │           ├─ search_flights → SerpAPI → Amadeus → mock
  │           ├─ get_weather → Open-Meteo
  │           └─ update_itinerary → SSE itinerary_update event
  │
  ▼
SSE stream → Frontend
  tool_start / tool_done / itinerary_update / done
  │
  ▼
Server persists session to PostgreSQL
UserStore.increment_chat(user_id)          ← usage metering
```

### 2. Auth Flow

```
Page load
  │
  ▼
GET /api/config → { clerk_publishable_key, auth_enabled }
  │
  ├─ auth_enabled = false → skip Clerk, run anonymous
  │
  └─ auth_enabled = true
      │
      ▼
      Load Clerk JS from CDN
      Clerk.load() → check existing session
      │
      ├─ No session → show "Sign in" button in sidebar
      │
      └─ Session found
          getToken() → JWT string
          POST /api/me { Authorization: Bearer JWT }
          Server: upsert users table with email + name
          Response: { id, email, plan, usage }
          renderUserPanel(user) → show avatar + plan badge + usage bar
```

### 3. Booking Confirmation Flow

```
User: "Book that $842 flight"
  │
Claude: book_flight(payment_confirmed=false)
  │
Tool returns: { status: "awaiting_confirmation", summary: "JFK→LIS $842" }
  │
Claude asks user: "Shall I confirm this booking?"
  │
User: "Yes"
  │
Server: POST /api/booking/confirm/{session_id}
  │
Claude: book_flight(payment_confirmed=true)
  │
Tool returns: { confirmation: "IB3847", pnr: "XZ9Q2" }
  │
Claude: "✓ Booked! Confirmation: IB3847"
```

### 4. Session Restore Flow

```
Browser tab opens
  │
  ▼
Read session_id from localStorage (or request new one)
GET /api/itinerary/{session_id}
  │
  ├─ Session exists → restore chat history + render trip board
  └─ No session → fresh start, create new session_id
```

### 5. Deal Hunting Flow

```
User: "Find cheapest flights to Paris in September"
  │
Claude: find_cheapest_month(JFK, CDG, 2025, 9)
  │
  ▼
Scan all Sep 2025 departure dates (SerpAPI or mock)
Apply season multipliers from seasons.py database
Rank by price, group by week
  │
  ▼
SSE: month_result event
Frontend: render month card with heat-map calendar
User clicks cheapest week → search_flights for that date
```

---

## External APIs

### SerpAPI — Google Flights (Primary)

The primary flight data source. Scrapes Google Flights and returns structured JSON.

```
GET https://serpapi.com/search.json
  ?engine=google_flights
  &departure_id=JFK
  &arrival_id=NRT
  &outbound_date=2025-05-10
  &type=1  (1=round-trip, 2=one-way)
  &adults=2
  &travel_class=1  (1=economy, 2=premium, 3=business, 4=first)
  &currency=USD
  &api_key={SERPAPI_KEY}

Response shape:
  best_flights[].flights[].airline
  best_flights[].flights[].departure_airport.time
  best_flights[].price
  best_flights[].carbon_emissions.this_flight
```

### Amadeus GDS (Fallback)

OAuth 2.0 client credentials flow; token cached ~30 min.

```
POST /v1/security/oauth2/token → access_token
GET  /v2/shopping/flight-offers → price, segments, carriers
GET  /v3/shopping/hotel-offers  → properties, rates, amenities

AMADEUS_HOST: test.api.amadeus.com (default) or api.amadeus.com
```

### Open-Meteo (Weather — free, no key)

```
GET https://api.open-meteo.com/v1/forecast
  ?latitude=38.72&longitude=-9.14
  &daily=temperature_2m_max,precipitation_sum,weathercode
  &forecast_days=7
```

### OpenStreetMap / Nominatim (Maps — free, no key)

```
Nominatim:  GET https://nominatim.openstreetmap.org/search?q=Lisbon&format=json
Overpass:   POST https://overpass-api.de/api/interpreter
            [out:json]; node["amenity"="restaurant"](around:2000,38.7,-9.1); out 10;
```

### Brave Search / Wikipedia (Web research)

```
Brave:     GET https://api.search.brave.com/res/v1/web/search?q={query}
           X-Subscription-Token: {BRAVE_SEARCH_API_KEY}

Wikipedia: GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}
           (fallback when BRAVE_SEARCH_API_KEY not set)
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

`travelers` and `max_budget_usd` drive the per-person math in the frontend budget panel. All `price_usd` values are **total** for the group; the UI divides by `travelers` to show per-person amounts.

### Deal Card (sent via `deal_result` / `month_result` SSE event)

```json
{
  "origin": "JFK",
  "destination": "CDG",
  "target_date": "2025-09-15",
  "results_by_price": [
    { "date": "2025-09-12", "price_usd": 680, "savings_usd": 145, "savings_pct": 17 }
  ],
  "heatmap": { "2025-09-01": 920, "2025-09-12": 680 }
}
```

---

## Security Architecture

### Layers

```
Internet
  │
  ▼  Rate limiting: 40/min per user_id (auth) or 20/min per IP (anon)
  │
  ▼  Security headers: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
  │
  ▼  JWT verification: RS256 via PyJWKClient (Clerk JWKS endpoint)
  │
  ▼  Session ownership: _require_session_access() → 403 if mismatch
  │
  ▼  Plan limits: UserStore.within_limit() → 402 if exceeded
  │
  ▼  Input validation: Pydantic models on all request bodies
  │
  ▼  Parameterized SQL: no string interpolation in queries
  │
  ▼  Booking confirmation: payment_confirmed=true only after explicit user approval
  │
  ▼  API keys server-side only: frontend never receives SERPAPI_KEY, AMADEUS secrets
```

### Key security properties

| Property | Implementation |
|---|---|
| Session ID entropy | `secrets.token_urlsafe(32)` — 256-bit, server-generated |
| Cross-user isolation | `sessions.user_id` enforced on every `/api/*/{session_id}` endpoint |
| JWT algorithm | RS256 (asymmetric) — no shared secret to leak |
| CSRF | Stateless JWT eliminates CSRF for all API calls |
| SQL injection | Parameterized queries throughout (`%s` placeholders via psycopg2) |
| XSS | CSP restricts script/style sources; `marked.js` output is sandboxed |
| Agent data isolation | `TravelAgent(user_id=uid)` — preferences and trips scoped per user |
| Invite spam prevention | DB-backed `invite_logs` table; 20 invites/day per user (total), 3/day to the same email |
| Invite email integrity | Invite matching uses the Clerk-verified email from the JWT, not a self-reported value |
| Group trip visibility | Only members with `joined_at` set (i.e. who claimed their invite) can see other members' trips |

---

## Deployment Architecture

### Railway.app (Production)

```
Railway Project
├── Web Service  (Nixpacks → uvicorn server:app --host 0.0.0.0 --port $PORT)
│
└── PostgreSQL Service
    └── DATABASE_URL → injected via Railway reference variable

Environment Variables:
  Required:
    ANTHROPIC_API_KEY
    DATABASE_URL            ← ${{Postgres.DATABASE_URL}}
  Recommended:
    SERPAPI_KEY             ← Google Flights live prices
    CLERK_PUBLISHABLE_KEY   ← user auth (frontend)
    CLERK_JWKS_URL          ← user auth JWT verification (backend)
  Optional:
    AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET
    BRAVE_SEARCH_API_KEY
    LOG_LEVEL
```

### Local Development

```
uvicorn server:app --reload --port 8000

.env (copy from .env.example):
  ANTHROPIC_API_KEY=sk-ant-...
  DATABASE_URL=postgresql://...
  SERPAPI_KEY=...            (optional)
  CLERK_PUBLISHABLE_KEY=...  (optional — app runs anonymous without it)
  CLERK_JWKS_URL=...
```

### Zero-downtime migrations

Schema is managed with `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. No migration tool is required for additive changes. Alembic is on the Phase 2 roadmap for more complex schema evolution.
