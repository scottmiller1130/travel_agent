# ✈ Travel Agent — AI-Powered Trip Planning Platform

> Plan trips in natural language. Real flights, real hotels, real budgets — all from a single conversation.

---

## What It Is

Travel Agent is a production-ready SaaS platform that lets users plan complete trips through an AI chat interface. Users describe where they want to go, and the agent searches live flight and hotel data, builds day-by-day itineraries, tracks budgets, and generates shareable trip plans — all without leaving the chat.

**Built for teams and individuals.** Users can create collaborative workspaces to plan group trips together, with role-based access and shared itineraries. Groups let friends and colleagues see each other's saved trips in one place.

---

## Features

### AI Planning
- Natural language trip planning (Claude claude-sonnet-4-6)
- Day-by-day itinerary builder with draggable cards
- Multi-city trip support with connecting flight logic
- Season-aware recommendations (peak / shoulder / off)
- Budget tracking with per-person breakdown and travelers selector
- Deal hunting across flexible dates and months

### Live Data
- **Google Flights** via SerpAPI (real prices, real airlines, carbon data)
- **Hotel search** with star ratings and location filters
- **Weather forecasts** via Open-Meteo (free, no key needed)
- **Ground transport** — trains, buses, car rental
- **Currency conversion** with live exchange rates
- **Points of interest** via OpenStreetMap / Nominatim

### Enterprise
- **User accounts** — Clerk-powered auth (email/password, Google OAuth, magic links)
- **Per-user data isolation** — sessions, preferences, and trips scoped to each user
- **Collaborative workspaces** — invite team members by email, owner/editor/viewer roles
- **Groups** — persistent membership spaces where all joined members can see each other's saved trips; invite-spam prevention with DB-backed rate limiting (20 invites/day per user)
- **Plan limits** — Free / Pro / Team tiers with monthly usage metering
- **Shareable links** — read-only rendered itinerary URLs for anyone
- **Booking confirmation** — agent pauses and waits for user approval before any purchase

---

## Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/your-org/travel-agent
cd travel-agent
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env — only ANTHROPIC_API_KEY and DATABASE_URL are required.
# All other services have graceful fallbacks.
```

### 3. Run
```bash
uvicorn server:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000)

---

## Environment Variables

### Required
| Variable | Description | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key | [console.anthropic.com](https://console.anthropic.com) |
| `DATABASE_URL` | PostgreSQL connection string | Railway Postgres service |

### Recommended
| Variable | Description | Cost |
|---|---|---|
| `SERPAPI_KEY` | Google Flights live prices | 100 free/mo; $50/mo for 5K |
| `CLERK_PUBLISHABLE_KEY` | User auth (frontend) | Free < 10K MAUs |
| `CLERK_JWKS_URL` | User auth JWT verification (backend) | Same plan as above |

### Optional
| Variable | Description | Default |
|---|---|---|
| `AMADEUS_CLIENT_ID` + `SECRET` | Airline GDS fallback | Falls back to distance mock |
| `BRAVE_SEARCH_API_KEY` | Enhanced web research | Falls back to Wikipedia |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

---

## Architecture Overview

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
        ├─ workspaces        collaborative planning spaces + groups
        ├─ workspace_members roles: owner / editor / viewer
        ├─ invite_logs       rate-limit log for outgoing invites
        └─ share_tokens      read-only itinerary share links
```

### Key Design Decisions

| Decision | Why |
|---|---|
| SSE streaming | Tool calls appear live — no polling needed |
| Per-session in-memory agent cache | Avoids re-parsing long conversation histories |
| Session ownership via `sessions.user_id` | Prevents cross-user data disclosure |
| Auth-optional mode | All features work without Clerk configured (anonymous) |
| `ALTER TABLE … IF NOT EXISTS` migrations | Safe zero-downtime schema evolution |
| SerpAPI → Amadeus → mock pricing chain | Always returns flight results regardless of API keys |

---

## API Reference

### Public Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Public config: Clerk publishable key, auth_enabled flag |
| `GET` | `/api/health` | Component status: database, Anthropic, SerpAPI, Clerk |
| `GET` | `/s/{token}` | Read-only shared itinerary page |

### Session Endpoints (auth optional — scoped to user when authenticated)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/session/new` | Create session (links to user_id if authenticated) |
| `POST` | `/api/chat/{session_id}` | Chat — SSE stream of tool events + final response |
| `GET` | `/api/itinerary/{session_id}` | Fetch current itinerary JSON |
| `POST` | `/api/itinerary/{session_id}` | Save itinerary (drag reorder, travelers update, import) |
| `DELETE` | `/api/itinerary/{session_id}` | Clear itinerary |
| `GET` | `/api/trips/{session_id}` | List saved trips |
| `POST` | `/api/trips/{session_id}` | Save current itinerary as a named trip |
| `DELETE` | `/api/trips/{session_id}/{trip_id}` | Delete a saved trip |
| `GET` | `/api/preferences/{session_id}` | Get preferences |
| `POST` | `/api/preferences/{session_id}` | Set a preference |
| `POST` | `/api/share/{session_id}` | Create read-only share link |
| `POST` | `/api/reset/{session_id}` | Clear conversation (keeps trips/prefs) |
| `POST` | `/api/booking/confirm/{session_id}` | Confirm a pending booking |
| `POST` | `/api/booking/cancel/{session_id}` | Cancel a pending booking |

### Auth Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/me` | Upsert user from JWT claims (call on login) |
| `GET` | `/api/me` | Current user profile + usage + plan limits |

### Workspace Endpoints (auth required)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/workspaces` | List user's workspaces |
| `POST` | `/api/workspaces` | Create workspace |
| `GET` | `/api/workspaces/{id}` | Get workspace details + member list |
| `POST` | `/api/workspaces/{id}/invite` | Invite member by email |
| `DELETE` | `/api/workspaces/{id}/members/{email}` | Remove member |
| `POST` | `/api/workspaces/{id}/session` | Link planning session to workspace |
| `DELETE` | `/api/workspaces/{id}` | Delete workspace (owner only) |

### Group Endpoints (auth required)

Groups are persistent membership spaces where all joined members can view each other's saved trips. Invites are rate-limited at the database level to prevent spam.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/groups` | List groups the user has joined |
| `POST` | `/api/groups` | Create a new group |
| `GET` | `/api/groups/pending` | List pending group invites for the user's email |
| `GET` | `/api/groups/{id}` | Get group details + member list |
| `POST` | `/api/groups/{id}/invite` | Invite a member by email (owner only; rate-limited) |
| `POST` | `/api/groups/{id}/join` | Join a group by claiming a pending email invite |
| `GET` | `/api/groups/{id}/trips` | Get saved trips for all joined members |
| `DELETE` | `/api/groups/{id}/members/{email}` | Remove a member (owner only) |
| `DELETE` | `/api/groups/{id}` | Delete group (owner only) |

---

## Plans & Pricing Architecture

### Default Tier Limits

| Plan | Chat turns/month | Flight searches/month | Suggested price |
|---|---|---|---|
| Free | 20 | 10 | $0 |
| Pro | Unlimited | 200 | $19/month |
| Team | Unlimited | 500 | $49/month |

Limits are defined in `memory/users.py:PLAN_LIMITS` and can be adjusted without code changes.

### Unit Economics (per 100 active users/month)

| Cost | Estimate |
|---|---|
| Claude claude-sonnet-4-6 ($3/MTok in, $15/MTok out) | $120–300 |
| SerpAPI Google Flights (5,000 searches) | $50 |
| Railway web + Postgres | $25–40 |
| Clerk auth (free < 10K MAUs) | $0 |
| **Total infrastructure** | **~$200–390** |

At 50% Pro conversion on 100 users: **$950 MRR vs ~$300 costs ≈ 68% gross margin.**

---

## Deploying on Railway

1. Fork this repository
2. Create a Railway project → **Add PostgreSQL service**
3. Deploy this repo as a **Web Service**
4. Add reference variable: `DATABASE_URL = ${{Postgres.DATABASE_URL}}`
5. Add `ANTHROPIC_API_KEY` in Variables
6. Optionally add `SERPAPI_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`
7. Railway auto-deploys on every push to `main`

---

## Setting Up User Auth (Clerk)

1. Create account at [clerk.com](https://clerk.com)
2. Create application → enable **Email** + **Google OAuth**
3. In Railway Variables, add:
   - `CLERK_PUBLISHABLE_KEY` → your `pk_live_…` key from Clerk Dashboard → API Keys
   - `CLERK_JWKS_URL` → `https://<frontend-api>/.well-known/jwks.json`
     *(The "Frontend API" hostname is shown in Clerk Dashboard → API Keys)*
4. Redeploy — the sign-in prompt appears automatically in the sidebar

---

## Setting Up Google Flights (SerpAPI)

1. Create account at [serpapi.com](https://serpapi.com)
2. Copy your API key from the dashboard
3. Add `SERPAPI_KEY` in Railway Variables
4. Redeploy — flight searches now return real Google Flights data

Without this key, the app uses Amadeus GDS (if configured) or distance-calculated mock pricing. All three modes return the same response shape.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| AI | Anthropic Claude claude-sonnet-4-6 | 8096 max_tokens, agentic tool-call loop |
| Backend | FastAPI + uvicorn | Async, SSE streaming |
| Frontend | Vanilla JS SPA | No framework, Inter font, CSS custom properties |
| Database | PostgreSQL | psycopg2 ThreadedConnectionPool(1, 10) |
| Auth | Clerk | JWT/RS256 verified via PyJWT + JWKS |
| Flights | SerpAPI → Amadeus → mock | Graceful fallback chain |
| Weather | Open-Meteo | Free, no key |
| Maps | OpenStreetMap / Nominatim | Free, no key |
| Hosting | Railway.app | Single-region, auto-deploy from GitHub |

---

## Security

- **Session ownership** enforced on every `/api/*/{session_id}` endpoint
- **JWT verification** via Clerk JWKS (RS256, no shared secrets)
- **Per-user rate limiting** (40 req/min authenticated, 20 req/min anonymous)
- **Invite rate limiting** — DB-backed, 20 invites/day per user total and 3/day to the same email; persists across restarts
- **Invite email from JWT** — invite matching uses the Clerk-verified email, not a self-reported value
- **Parameterized queries** throughout — no SQL injection surface
- **CSRF**: stateless JWT auth eliminates CSRF for API calls
- **Security headers**: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **Session IDs**: `secrets.token_urlsafe(32)` — 256-bit entropy, never client-generated

---

## Roadmap

### Next (Phase 2)
- [ ] Stripe subscriptions + webhook for plan upgrades
- [ ] Email notifications for workspace and group invites (Resend / SendGrid)
- [ ] Redis agent cache for multi-instance deployments
- [ ] Admin dashboard: user list, revenue, usage metrics
- [ ] Alembic migrations (replace ad-hoc ALTER TABLE)

### Future (Phase 3)
- [ ] Real-time workspace collaboration (WebSocket push)
- [ ] Sentry error tracking
- [ ] PostHog product analytics
- [ ] Mobile app (React Native)
- [ ] Hotel booking via Amadeus production API
- [ ] Flight booking via NDC / aggregator

---

## License

MIT
