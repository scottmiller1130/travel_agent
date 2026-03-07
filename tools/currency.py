"""
Currency conversion tool — live exchange rates via Frankfurter API.

Frankfurter (https://www.frankfurter.app) is free, requires no API key,
and is backed by the European Central Bank reference rates. Rates are
updated every business day around 16:00 CET.
"""

import time

try:
    import httpx as _httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

from tools.cache import ttl_cache

FRANKFURTER_URL = "https://api.frankfurter.app/latest"

# Reasonable fallback rates if the API is unreachable (USD base, as of early 2025)
_FALLBACK_RATES: dict[str, float] = {
    "EUR": 0.92, "GBP": 0.79, "JPY": 149.5, "CAD": 1.35,
    "AUD": 1.53, "CHF": 0.89, "CNY": 7.24,  "INR": 83.1,
    "MXN": 17.2, "BRL": 4.95, "SGD": 1.34,  "HKD": 7.82,
    "NZD": 1.63, "SEK": 10.4, "NOK": 10.6,  "DKK": 6.88,
    "PLN": 3.97, "CZK": 23.2, "HUF": 360.0, "TRY": 32.1,
    "ZAR": 18.7, "AED": 3.67, "THB": 35.1,  "MYR": 4.71,
    "PHP": 56.3, "IDR": 15750, "VND": 24500, "KRW": 1330,
    "COP": 3890, "PEN": 3.72, "CLP": 960.0, "ARS": 870.0,
    "EGP": 30.9, "MAD": 10.0, "KES": 129.0,
}

CURRENCY_NAMES: dict[str, str] = {
    "USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound",
    "JPY": "Japanese Yen", "CAD": "Canadian Dollar", "AUD": "Australian Dollar",
    "CHF": "Swiss Franc", "CNY": "Chinese Yuan", "INR": "Indian Rupee",
    "MXN": "Mexican Peso", "BRL": "Brazilian Real", "SGD": "Singapore Dollar",
    "HKD": "Hong Kong Dollar", "NZD": "New Zealand Dollar", "SEK": "Swedish Krona",
    "NOK": "Norwegian Krone", "DKK": "Danish Krone", "PLN": "Polish Zloty",
    "CZK": "Czech Koruna", "HUF": "Hungarian Forint", "TRY": "Turkish Lira",
    "ZAR": "South African Rand", "AED": "UAE Dirham", "THB": "Thai Baht",
    "MYR": "Malaysian Ringgit", "PHP": "Philippine Peso", "IDR": "Indonesian Rupiah",
    "VND": "Vietnamese Dong", "KRW": "South Korean Won",
    "COP": "Colombian Peso", "PEN": "Peruvian Sol", "CLP": "Chilean Peso",
    "ARS": "Argentine Peso", "EGP": "Egyptian Pound", "MAD": "Moroccan Dirham",
    "KES": "Kenyan Shilling",
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$",
    "AUD": "A$", "CHF": "Fr", "CNY": "¥", "INR": "₹", "MXN": "MX$",
    "BRL": "R$", "SGD": "S$", "HKD": "HK$", "NZD": "NZ$", "SEK": "kr",
    "NOK": "kr", "DKK": "kr", "PLN": "zł", "CZK": "Kč", "HUF": "Ft",
    "TRY": "₺", "ZAR": "R", "AED": "د.إ", "THB": "฿", "MYR": "RM",
    "PHP": "₱", "IDR": "Rp", "KRW": "₩", "VND": "₫",
}


@ttl_cache(ttl=3600)  # ECB rates update once per day; 1h cache is plenty
def get_exchange_rate(
    from_currency: str = "USD",
    to_currency: str = "EUR",
    amount: float = 1.0,
) -> dict:
    """
    Get the live exchange rate and convert an amount between two currencies.

    Args:
        from_currency: Source currency code (e.g. "USD")
        to_currency:   Target currency code (e.g. "EUR"). Can also be a
                       comma-separated list: "EUR,GBP,JPY"
        amount:        Amount to convert (default 1.0)

    Returns:
        dict with rate, converted_amount, and formatted strings.
    """
    from_currency = from_currency.upper().strip()
    to_currencies = [c.strip().upper() for c in to_currency.replace(",", " ").split()]

    # Remove same-currency conversions
    to_currencies = [c for c in to_currencies if c != from_currency]
    if not to_currencies:
        return {
            "status": "success",
            "from_currency": from_currency,
            "to_currency": from_currency,
            "rate": 1.0,
            "amount": amount,
            "converted_amount": amount,
            "note": "Same currency — no conversion needed.",
        }

    rates: dict[str, float] = {}
    source = "European Central Bank (via Frankfurter)"

    if _HTTPX:
        try:
            r = _httpx.get(
                FRANKFURTER_URL,
                params={"from": from_currency, "to": ",".join(to_currencies)},
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                rates = data.get("rates", {})
        except Exception:
            pass

    if not rates:
        # Build fallback rates: convert from_currency → USD → to_currency
        source = "Estimated rates (API unavailable; approximate only)"
        from_to_usd = 1.0 / _FALLBACK_RATES.get(from_currency, 1.0) if from_currency != "USD" else 1.0
        for tc in to_currencies:
            if tc == "USD":
                rates[tc] = from_to_usd
            else:
                tc_rate = _FALLBACK_RATES.get(tc)
                if tc_rate:
                    rates[tc] = from_to_usd * tc_rate

    if not rates:
        return {
            "status": "error",
            "message": f"Could not retrieve exchange rates for {from_currency} → {', '.join(to_currencies)}",
        }

    # Build result
    conversions = []
    for tc, rate in rates.items():
        converted = round(amount * rate, 2)
        symbol    = CURRENCY_SYMBOLS.get(tc, tc)
        name      = CURRENCY_NAMES.get(tc, tc)
        conversions.append({
            "to_currency":       tc,
            "currency_name":     name,
            "symbol":            symbol,
            "rate":              round(rate, 4),
            "converted_amount":  converted,
            "formatted":         f"{symbol}{converted:,.2f} {tc}",
        })

    # Single-target convenience fields
    single = conversions[0] if len(conversions) == 1 else None
    result = {
        "status":        "success",
        "from_currency": from_currency,
        "from_name":     CURRENCY_NAMES.get(from_currency, from_currency),
        "amount":        amount,
        "conversions":   conversions,
        "source":        source,
        "note":          "Rates are indicative. Check your bank or card provider for exact rates.",
    }
    if single:
        result.update({
            "to_currency":      single["to_currency"],
            "rate":             single["rate"],
            "converted_amount": single["converted_amount"],
            "formatted":        single["formatted"],
        })
    return result
