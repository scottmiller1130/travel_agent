#!/usr/bin/env python3
"""
Travel Agent CLI — interactive terminal interface.

Usage:
    python cli.py              # Start a conversation
    python cli.py --setup      # Set up your preferences
    python cli.py --trips      # View saved trips
"""

import sys
import os
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.markdown import Markdown
from rich.table import Table
from rich import box
from rich.rule import Rule

from agent.core import TravelAgent
from memory.preferences import PreferenceStore
from memory.trips import TripStore

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


def run_setup(pref_store: PreferenceStore):
    """Interactive preference setup wizard."""
    console.print(Rule("[bold cyan]Setup: Your Travel Preferences[/]"))
    console.print("[dim]Press Enter to skip any field.[/dim]\n")

    fields = [
        ("name", "Your name", str),
        ("email", "Your email (for booking confirmations)", str),
        ("home_city", "Your home city", str),
        ("home_airport", "Your home airport (IATA code, e.g. JFK)", str),
        ("cabin_class", "Preferred cabin class [economy/premium_economy/business/first]", str),
        ("seat_preference", "Preferred seat [window/aisle/middle]", str),
        ("hotel_min_stars", "Minimum hotel star rating [1-5]", int),
        ("max_budget_per_day_usd", "Max daily travel budget (USD)", int),
        ("travel_pace", "Travel pace [slow/moderate/fast]", str),
        ("currency", "Preferred currency (e.g. USD, EUR, GBP)", str),
    ]

    for key, label, cast in fields:
        current = pref_store.get(key)
        current_str = f" [dim](current: {current})[/dim]" if current else ""
        value = Prompt.ask(f"{label}{current_str}", default="")
        if value.strip():
            try:
                pref_store.set(key, cast(value.strip()))
            except ValueError:
                console.print(f"[red]Invalid value for {label}, skipping.[/red]")

    # List fields
    list_fields = [
        ("preferred_airlines", "Preferred airlines (comma-separated, e.g. Delta,United)"),
        ("avoided_airlines", "Airlines to avoid (comma-separated)"),
        ("dietary_restrictions", "Dietary restrictions (comma-separated, e.g. vegetarian,gluten-free)"),
        ("preferred_activities", "Preferred activity types (e.g. culture,food,nature,adventure)"),
    ]
    for key, label in list_fields:
        current = pref_store.get(key, [])
        current_str = f" [dim](current: {', '.join(current) if current else 'none'})[/dim]"
        value = Prompt.ask(f"{label}{current_str}", default="")
        if value.strip():
            pref_store.set(key, [v.strip() for v in value.split(",") if v.strip()])

    console.print("\n[bold green]Preferences saved![/bold green]\n")


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

    # First-time setup prompt
    user_name = pref_store.get("name")
    if not user_name:
        console.print("[dim]Tip: Run [bold]python cli.py --setup[/bold] to save your preferences for personalized recommendations.[/dim]\n")

    agent = TravelAgent(confirm_callback=make_confirm_callback())

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]You[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye! Safe travels.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q", "bye"):
            console.print("[dim]Goodbye! Safe travels.[/dim]")
            break

        if user_input.lower() == "reset":
            agent.reset()
            console.print("[dim]Conversation reset. Starting fresh.[/dim]\n")
            continue

        if user_input.lower() == "trips":
            show_trips(trip_store)
            continue

        if user_input.lower() == "prefs":
            show_preferences(pref_store)
            continue

        print_thinking()
        try:
            response = agent.chat(user_input)
            print_agent_response(response)
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}\n")


if __name__ == "__main__":
    main()
