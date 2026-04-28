## What does this PR change?

<!-- One or two sentences. What problem does it solve, what feature does it add? -->

## Critical journeys affected

<!-- Tick all that apply. If none apply, explain why. -->

- [ ] J1 — Trip lifecycle (save, list, delete)
- [ ] J2 — Share link (create, live sync)
- [ ] J3 — Booking confirmation / cancellation
- [ ] J4 — User preferences
- [ ] J5 — Session management (new session, clear board, reset)
- [ ] None of the above — pure refactor / docs / tooling

## Test plan

<!-- What did you do to verify this works? -->

- [ ] Ran `pytest` locally — all tests pass
- [ ] Ran `pytest tests/test_critical_journeys.py -v` — no regressions
- [ ] Added or updated tests for new behaviour
- [ ] Manually tested the affected UI flow in a browser (if frontend changed)

## Checklist

- [ ] No new lint errors (`ruff check .`)
- [ ] No console errors in the browser
- [ ] Shared links (`/s/<token>`) still render correctly (if sessions.py or server.py changed)
- [ ] No secrets or credentials committed
