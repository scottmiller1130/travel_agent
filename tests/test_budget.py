"""Tests for the budget / expense tracker tool."""

import pytest

from tools.budget import get_budget_status, log_expense

# ── log_expense ───────────────────────────────────────────────────────────────

def test_log_expense_returns_success():
    expenses = []
    result = log_expense(expenses, "flights", 450.00, "LHR to CDG return")
    assert result["status"] == "success"


def test_log_expense_appends_to_list():
    expenses = []
    log_expense(expenses, "hotels", 120.00, "Night 1")
    log_expense(expenses, "food", 30.00, "Dinner")
    assert len(expenses) == 2


def test_log_expense_running_total():
    expenses = []
    log_expense(expenses, "flights", 300.00, "Outbound")
    result = log_expense(expenses, "hotels", 200.00, "Hotel")
    assert result["running_total"] == pytest.approx(500.00)


def test_log_expense_unknown_category_maps_to_other():
    expenses = []
    log_expense(expenses, "INVALID_CATEGORY", 10.00, "Mystery spend")
    assert expenses[0]["category"] == "other"


def test_log_expense_valid_categories():
    valid = ["flights", "hotels", "food", "activities", "transport", "shopping", "other"]
    for cat in valid:
        expenses = []
        log_expense(expenses, cat, 10.00, "test")
        assert expenses[0]["category"] == cat


def test_log_expense_assigns_sequential_ids():
    expenses = []
    log_expense(expenses, "food", 10.00, "Lunch")
    log_expense(expenses, "food", 15.00, "Dinner")
    assert expenses[0]["id"] == 1
    assert expenses[1]["id"] == 2


def test_log_expense_strips_description_whitespace():
    expenses = []
    log_expense(expenses, "other", 5.00, "  Coffee  ")
    assert expenses[0]["description"] == "Coffee"


def test_log_expense_rounds_amount():
    expenses = []
    log_expense(expenses, "food", 10.555, "Rounding test")
    assert expenses[0]["amount_usd"] == pytest.approx(10.56, abs=0.01)


# ── get_budget_status ─────────────────────────────────────────────────────────

def test_budget_status_empty_expenses():
    result = get_budget_status([])
    assert result["status"] == "success"
    assert result["total_spent_usd"] == 0
    assert "No expenses" in result["message"]


def test_budget_status_totals_correctly():
    expenses = []
    log_expense(expenses, "flights", 400.00, "Flight")
    log_expense(expenses, "hotels", 200.00, "Hotel")
    log_expense(expenses, "food", 100.00, "Food")
    result = get_budget_status(expenses)
    assert result["total_spent_usd"] == pytest.approx(700.00)


def test_budget_status_by_category_breakdown():
    expenses = []
    log_expense(expenses, "flights", 500.00, "Flight")
    log_expense(expenses, "food", 100.00, "Food")
    result = get_budget_status(expenses)
    cats = {c["category"]: c["amount_usd"] for c in result["by_category"]}
    assert cats["flights"] == pytest.approx(500.00)
    assert cats["food"] == pytest.approx(100.00)


def test_budget_status_on_track():
    expenses = []
    log_expense(expenses, "flights", 400.00, "Flight")
    result = get_budget_status(expenses, trip_budget_usd=1000.00)
    assert result["budget_status"] == "on_track"
    assert result["remaining_usd"] == pytest.approx(600.00)
    assert result["pct_used"] == pytest.approx(40.0)


def test_budget_status_near_limit():
    expenses = []
    log_expense(expenses, "flights", 900.00, "Flight")
    result = get_budget_status(expenses, trip_budget_usd=1000.00)
    assert result["budget_status"] == "near_limit"


def test_budget_status_over_budget():
    expenses = []
    log_expense(expenses, "flights", 1200.00, "Flight")
    result = get_budget_status(expenses, trip_budget_usd=1000.00)
    assert result["budget_status"] == "over_budget"
    assert result["remaining_usd"] < 0
    assert "Over budget" in result["message"]


def test_budget_status_no_budget_provided():
    expenses = []
    log_expense(expenses, "food", 50.00, "Lunch")
    result = get_budget_status(expenses, trip_budget_usd=None)
    assert "budget_status" not in result
    assert result["total_spent_usd"] == pytest.approx(50.00)


def test_budget_status_recent_shows_last_five():
    expenses = []
    for i in range(7):
        log_expense(expenses, "food", float(i + 1), f"Meal {i + 1}")
    result = get_budget_status(expenses)
    assert len(result["recent"]) == 5
    # Most recent 5 are the last 5
    assert result["recent"][-1]["description"] == "Meal 7"


def test_budget_status_expense_count():
    expenses = []
    for _ in range(3):
        log_expense(expenses, "transport", 20.00, "Taxi")
    result = get_budget_status(expenses)
    assert result["expense_count"] == 3
