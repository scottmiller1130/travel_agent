#!/usr/bin/env python3
"""
Travel Agent CLI — interactive terminal interface.

Usage:
    python cli.py              # Start a conversation
    python cli.py --setup      # Set up your preferences
    python cli.py --trips      # View saved trips
"""

import logging
import os
import sys
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Configure file logging for CLI sessions so interactions can be reviewed later.
# File handler → travel_agent_cli.log, console handler only shows WARNING+.
_log_dir = Path(__file__).parent
_log_file = _log_dir / "travel_agent_cli.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),  # WARNING+ only — set below
    ],
)
# Quieten the console; the file gets everything
logging.getLogger().handlers[1].setLevel(logging.WARNING)
# Quieten noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

log = logging.getLogger("travel_agent.cli")

import click  # noqa: E402
from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Confirm, Prompt  # noqa: E402
from rich.rule import Rule  # noqa: E402
from rich.table import Table  # noqa: E402

from agent.core import TravelAgent  # noqa: E402
from memory.preferences import PreferenceStore  # noqa: E402
from memory.trips import TripStore  # noqa: E402

console = Console()


def make_confirm_callback() -> callable:
    """Returns a callback that prompts the user in the terminal to confirm bookings."""
    def confirm(description: str) -> bool:
        console.print()
        console.print(Panel(description, title="[bold yellow]Booking Confirmation Required[/]", border_style="yellow"))
        return Confirm.ask("[bold yellow]Confirm this booking?[/]", default=False)
    return confirm


def print_welcome():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]✈  Travel Agent[/bold cyan]\n"
        "[dim]Your personal AI travel planner — powered by Claude[/dim]\n\n"
        "[dim]Commands: 'quit' to exit · 'reset' for new trip · 'trips' to see saved trips · 'prefs' to view preferences[/dim]",
        border_style="cyan",
    ))
    console.print()


def print_agent_response(text: str):
    console.print()
    console.print(Panel(
        Markdown(text),
        title="[bold green]Agent[/]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()


def print_thinking():
    console.print("[dim italic]Researching and planning...[/dim italic]")


def show_trips(trip_store: TripStore):
    trips = trip_store.get_all_trips()
    if not trips:
        console.print("[dim]No trips saved yet.[/dim]")
        return

    table = Table(title="Saved Trips", box=box.ROUNDED, border_style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Destination", style="bold")
    table.add_column("Dates")
    table.add_column("Status", style="yellow")
    table.add_column("Budget")

    for t in trips:
        budget = t.get("budget", {})
        budget_str = f"${budget.get('total', '?')}" if budget else "—"
        table.add_row(
            t.get("id", "?")[-8:],
            t.get("destination", "Unknown"),
            f"{t.get('start_date', '?')} → {t.get('end_date', '?')}",
            t.get("status", "planned"),
            budget_str,
        )

    console.print(table)


def show_preferences(pref_store: PreferenceStore):
    prefs = pref_store.get_all()
    table = Table(title="Your Travel Preferences", box=box.ROUNDED, border_style="cyan")
    table.add_column("Preference", style="bold")
    table.add_column("Value")

    for key, value in prefs.items():
        label = key.replace("_", " ").title()
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) if value else "[dim]not set[/dim]"
        elif not value and value != 0:
            value = "[dim]not set[/dim]"
        table.add_row(label, str(value))

    console.print(table)


_PROFILE_DESCRIPTIONS = {
    "adventure": (
        "Adventure / Backpacker",
        "$40-120/day · hostels & dorms · slow travel · economy flights · overnight trains",
    ),
    "mid_range": (
        "Mid-Range",
        "$100-200/day · 3-star hotels or guesthouses · economy/premium economy · good value focus",
    ),
    "luxury": (
        "Luxury / Affluent",
        "$300-1000+/day · 4-5 star hotels & suites · business class · private transfers · curated picks",
    ),
}

_PROFILE_SMART_DEFAULTS = {
    "adventure": {
        "cabin_class": "economy",
        "hotel_min_stars": None,
        "max_budget_per_day_usd": 80,
        "travel_pace": "slow",
        "accommodation_preference": "hostel",
    },
    "mid_range": {
        "cabin_class": "economy",
        "hotel_min_stars": 3,
        "max_budget_per_day_usd": 150,
        "travel_pace": "moderate",
        "accommodation_preference": "hotel",
    },
    "luxury": {
        "cabin_class": "business",
        "hotel_min_stars": 4,
        "max_budget_per_day_usd": None,
        "travel_pace": "moderate",
        "accommodation_preference": "hotel",
    },
}


def run_setup(pref_store: PreferenceStore):
    """Interactive preference setup wizard — profile-first."""
    console.print(Rule("[bold cyan]Travel Profile Setup[/]"))
    console.print("[dim]This takes 2 minutes. Your answers personalize every recommendation.[/dim]\n")

    # ── Step 1: Traveler profile (the anchor for everything else) ─────────────
    console.print("[bold]Step 1 of 3 — What kind of traveler are you?[/bold]\n")
    for key, (name, desc) in _PROFILE_DESCRIPTIONS.items():
        console.print(f"  [bold cyan]{key}[/]  {name}")
        console.print(f"  [dim]     {desc}[/dim]")
    console.print()

    current_profile = pref_store.get("traveler_profile")
    current_str = f" [dim](current: {current_profile})[/dim]" if current_profile else ""
    profile = Prompt.ask(
        f"Your traveler profile [adventure/mid_range/luxury]{current_str}",
        default=current_profile or ""
    ).strip().lower()

    if profile in _PROFILE_SMART_DEFAULTS:
        pref_store.set("traveler_profile", profile)
        defaults = _PROFILE_SMART_DEFAULTS[profile]
        name, desc = _PROFILE_DESCRIPTIONS[profile]
        console.print(f"\n[bold green]Profile set: {name}[/bold green]")
        console.print(f"[dim]Smart defaults applied: {desc}[/dim]")
        # Apply profile smart defaults only for fields not already explicitly set
        for k, v in defaults.items():
            existing = pref_store.get(k)
            if existing is None or existing == "" or existing == []:
                if v is not None:
                    pref_store.set(k, v)
        console.print()
    elif profile:
        console.print(f"[yellow]Unknown profile '{profile}' — skipping profile step.[/yellow]\n")

    # ── Step 2: Personal basics ────────────────────────────────────────────────
    console.print("[bold]Step 2 of 3 — Personal basics[/bold]\n")
    basics = [
        ("name",         "Your name",                                               str),
        ("email",        "Email (for booking confirmations)",                        str),
        ("home_city",    "Home city",                                               str),
        ("home_airport", "Home airport (IATA code, e.g. JFK, LHR, SYD)",           str),
        ("currency",     "Preferred currency (e.g. USD, EUR, GBP)",                  str),
        ("companion_profile", "Who do you usually travel with? [solo/couple/family/group]", str),
    ]
    for key, label, cast in basics:
        current = pref_store.get(key)
        current_str = f" [dim](current: {current})[/dim]" if current else ""
        value = Prompt.ask(f"{label}{current_str}", default="").strip()
        if value:
            try:
                pref_store.set(key, cast(value))
            except ValueError:
                console.print("[red]Invalid value, skipping.[/red]")
    console.print()

    # ── Step 3: Travel specifics (profile-aware prompts) ──────────────────────
    console.print("[bold]Step 3 of 3 — Travel preferences[/bold]\n")

    profile = pref_store.get("traveler_profile") or profile  # re-read in case just set

    # Cabin class (show current/default so user knows what they're overriding)
    cabin_default = _PROFILE_SMART_DEFAULTS.get(profile, {}).get("cabin_class", "economy")
    current_cabin = pref_store.get("cabin_class") or cabin_default
    cabin = Prompt.ask(
        f"Cabin class [economy/premium_economy/business/first] [dim](default: {current_cabin})[/dim]",
        default=""
    ).strip()
    if cabin:
        pref_store.set("cabin_class", cabin)

    # Seat preference
    current_seat = pref_store.get("seat_preference") or "window"
    seat = Prompt.ask(
        f"Preferred seat [window/aisle/middle] [dim](default: {current_seat})[/dim]",
        default=""
    ).strip()
    if seat:
        pref_store.set("seat_preference", seat)

    # Budget (skip for luxury — no cap by default)
    if profile != "luxury":
        budget_default = _PROFILE_SMART_DEFAULTS.get(profile, {}).get("max_budget_per_day_usd")
        current_budget = pref_store.get("max_budget_per_day_usd")
        display_default = current_budget or budget_default or "not set"
        budget_str = Prompt.ask(
            f"Max daily travel budget in USD [dim](default: {display_default})[/dim]",
            default=""
        ).strip()
        if budget_str:
            try:
                pref_store.set("max_budget_per_day_usd", int(budget_str))
            except ValueError:
                console.print("[red]Invalid budget, skipping.[/red]")

    # Hotel stars (skip for adventure — no filter by default)
    if profile != "adventure":
        stars_default = _PROFILE_SMART_DEFAULTS.get(profile, {}).get("hotel_min_stars", 3)
        current_stars = pref_store.get("hotel_min_stars")
        display_default = current_stars or stars_default or "not set"
        stars_str = Prompt.ask(
            f"Minimum hotel star rating [1-5] [dim](default: {display_default})[/dim]",
            default=""
        ).strip()
        if stars_str:
            try:
                pref_store.set("hotel_min_stars", int(stars_str))
            except ValueError:
                console.print("[red]Invalid star rating, skipping.[/red]")

    # Travel pace
    pace_default = _PROFILE_SMART_DEFAULTS.get(profile, {}).get("travel_pace", "moderate")
    current_pace = pref_store.get("travel_pace") or pace_default
    pace = Prompt.ask(
        f"Travel pace [slow/moderate/fast] [dim](default: {current_pace})[/dim]",
        default=""
    ).strip()
    if pace:
        pref_store.set("travel_pace", pace)

    # List preferences
    console.print()
    list_fields = [
        ("preferred_airlines",    "Preferred airlines (comma-separated, e.g. Delta,Singapore Airlines)"),
        ("avoided_airlines",      "Airlines to avoid (comma-separated)"),
        ("dietary_restrictions",  "Dietary restrictions (e.g. vegetarian, gluten-free, halal)"),
        ("preferred_activities",  "Favourite activity types (e.g. culture, food, nature, adventure, wellness)"),
        ("values",                "Travel values (e.g. adventure, wellness, culture, relaxation, food, nature)"),
    ]
    for key, label in list_fields:
        current = pref_store.get(key, [])
        current_str = f" [dim](current: {', '.join(str(v) for v in current) if current else 'none'})[/dim]"
        value = Prompt.ask(f"{label}{current_str}", default="").strip()
        if value:
            pref_store.set(key, [v.strip() for v in value.split(",") if v.strip()])

    console.print("\n[bold green]Profile saved! The agent will use these to personalize every trip.[/bold green]\n")


@click.command()
@click.option("--setup", is_flag=True, help="Run the preferences setup wizard")
@click.option("--trips", is_flag=True, help="View saved trips and exit")
@click.option("--prefs", is_flag=True, help="View current preferences and exit")
def main(setup: bool, trips: bool, prefs: bool):
    pref_store = PreferenceStore()
    trip_store = TripStore()

    if setup:
        run_setup(pref_store)
        return

    if trips:
        show_trips(trip_store)
        return

    if prefs:
        show_preferences(pref_store)
        return

    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[bold red]Error:[/bold red] ANTHROPIC_API_KEY environment variable not set.")
        console.print("Create a .env file from .env.example and add your API key.")
        sys.exit(1)

    print_welcome()
    log.info("CLI session started")

    # First-time setup prompt
    user_name = pref_store.get("name")
    if not user_name:
        console.print("[dim]Tip: Run [bold]python cli.py --setup[/bold] to save your preferences for personalized recommendations.[/dim]\n")

    agent = TravelAgent(confirm_callback=make_confirm_callback())

    _turn = 0
    while True:
        try:
            user_input = Prompt.ask("[bold cyan]You[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye! Safe travels.[/dim]")
            log.info("CLI session ended (interrupt) after %d turns", _turn)
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q", "bye"):
            console.print("[dim]Goodbye! Safe travels.[/dim]")
            log.info("CLI session ended (quit) after %d turns", _turn)
            break

        if user_input.lower() == "reset":
            agent.reset()
            log.info("CLI conversation reset after %d turns", _turn)
            _turn = 0
            console.print("[dim]Conversation reset. Starting fresh.[/dim]\n")
            continue

        if user_input.lower() == "trips":
            log.debug("CLI: showing trips")
            show_trips(trip_store)
            continue

        if user_input.lower() == "prefs":
            log.debug("CLI: showing preferences")
            show_preferences(pref_store)
            continue

        _turn += 1
        log.info("CLI turn %d: msg_len=%d", _turn, len(user_input))
        print_thinking()
        try:
            response = agent.chat(user_input)
            log.info("CLI turn %d complete: response_len=%d", _turn, len(response or ""))
            print_agent_response(response)
        except Exception as e:
            log.error("CLI turn %d error: %s", _turn, e)
            console.print(f"\n[bold red]Error:[/bold red] {e}\n")


if __name__ == "__main__":
    main()
