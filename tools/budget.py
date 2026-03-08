"""
Trip budget & expense tracker.

Inspired by Mindtrip's expense tracking feature. Tracks actual spending
against the planned trip budget. Data is stored per-session in the
preference store so it persists across conversation turns.

Tools exposed:
  log_expense(category, amount_usd, description, date) → add an expense
  get_budget_status(trip_budget_usd)                   → show spending vs budget
  clear_expenses()                                     → reset for a new trip
"""

from datetime import datetime


_CATEGORIES = {
    "flights":    "✈",
    "hotels":     "🏨",
    "food":       "🍽",
    "activities": "🗺",
    "transport":  "🚗",
    "shopping":   "🛍",
    "other":      "📦",
}


def log_expense(
    expenses: list,
    category: str,
    amount_usd: float,
    description: str,
    date: str | None = None,
) -> dict:
    """
    Add an expense to the running trip log.

    expenses: the current list (pass current state from preferences or [])
    category: one of flights, hotels, food, activities, transport, shopping, other
    amount_usd: amount in USD
    description: what this expense was for
    date: YYYY-MM-DD (defaults to today)
    """
    cat = category.lower()
    if cat not in _CATEGORIES:
        cat = "other"

    entry = {
        "id":          len(expenses) + 1,
        "category":    cat,
        "amount_usd":  round(float(amount_usd), 2),
        "description": description.strip(),
        "date":        date or datetime.now().strftime("%Y-%m-%d"),
    }
    expenses.append(entry)

    total = sum(e["amount_usd"] for e in expenses)
    return {
        "status":         "success",
        "expense_logged": entry,
        "running_total":  round(total, 2),
        "message":        f"Logged ${amount_usd:.0f} for {description}. Running total: ${total:.0f}.",
    }


def get_budget_status(expenses: list, trip_budget_usd: float | None = None) -> dict:
    """
    Summarize spending to date, broken down by category.

    expenses: list of expense entries from the session
    trip_budget_usd: optional total trip budget to compare against
    """
    if not expenses:
        return {
            "status":  "success",
            "message": "No expenses logged yet. Use log_expense to track your spending.",
            "total_spent_usd": 0,
            "by_category": {},
        }

    by_cat: dict[str, float] = {}
    for e in expenses:
        cat = e.get("category", "other")
        by_cat[cat] = round(by_cat.get(cat, 0) + e["amount_usd"], 2)

    total_spent = round(sum(by_cat.values()), 2)

    cat_summary = []
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        icon = _CATEGORIES.get(cat, "📦")
        pct = round(amt / total_spent * 100) if total_spent else 0
        cat_summary.append({
            "category":   cat,
            "icon":       icon,
            "amount_usd": amt,
            "pct_of_total": pct,
        })

    result: dict = {
        "status":          "success",
        "total_spent_usd": total_spent,
        "by_category":     cat_summary,
        "expense_count":   len(expenses),
        "recent":          expenses[-5:],  # last 5 expenses
    }

    if trip_budget_usd and trip_budget_usd > 0:
        remaining = round(trip_budget_usd - total_spent, 2)
        pct_used = round(total_spent / trip_budget_usd * 100, 1)
        result["budget_usd"]   = trip_budget_usd
        result["remaining_usd"] = remaining
        result["pct_used"]      = pct_used
        result["budget_status"] = (
            "on_track" if pct_used <= 80
            else "near_limit" if pct_used <= 100
            else "over_budget"
        )
        if remaining < 0:
            result["message"] = (
                f"Over budget by ${abs(remaining):.0f}! "
                f"Spent ${total_spent:.0f} of ${trip_budget_usd:.0f} ({pct_used}%)."
            )
        else:
            result["message"] = (
                f"${total_spent:.0f} spent, ${remaining:.0f} remaining "
                f"({pct_used}% of ${trip_budget_usd:.0f} budget)."
            )
    else:
        result["message"] = f"Total spent so far: ${total_spent:.0f} across {len(expenses)} expenses."

    return result
