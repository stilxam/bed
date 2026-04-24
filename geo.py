"""
geo.py — IP-based geolocation and country → primary currency mapping.

Uses ip-api.com (free, no API key, 45 req/min).
Result is cached for the lifetime of the process so the API is only hit once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

# ISO 3166-1 alpha-2 country code → ISO 4217 currency code
_COUNTRY_CURRENCY: dict[str, str] = {
    "AD": "EUR", "AE": "AED", "AF": "AFN", "AL": "ALL", "AM": "AMD",
    "AO": "AOA", "AR": "ARS", "AT": "EUR", "AU": "AUD", "AZ": "AZN",
    "BA": "BAM", "BD": "BDT", "BE": "EUR", "BF": "XOF", "BG": "BGN",
    "BH": "BHD", "BI": "BIF", "BJ": "XOF", "BN": "BND", "BO": "BOB",
    "BR": "BRL", "BS": "BSD", "BT": "BTN", "BW": "BWP", "BY": "BYN",
    "BZ": "BZD", "CA": "CAD", "CD": "CDF", "CF": "XAF", "CG": "XAF",
    "CH": "CHF", "CI": "XOF", "CL": "CLP", "CM": "XAF", "CN": "CNY",
    "CO": "COP", "CR": "CRC", "CU": "CUP", "CV": "CVE", "CY": "EUR",
    "CZ": "CZK", "DE": "EUR", "DJ": "DJF", "DK": "DKK", "DO": "DOP",
    "DZ": "DZD", "EC": "USD", "EE": "EUR", "EG": "EGP", "ER": "ERN",
    "ES": "EUR", "ET": "ETB", "FI": "EUR", "FJ": "FJD", "FR": "EUR",
    "GA": "XAF", "GB": "GBP", "GE": "GEL", "GH": "GHS", "GM": "GMD",
    "GN": "GNF", "GQ": "XAF", "GR": "EUR", "GT": "GTQ", "GW": "XOF",
    "GY": "GYD", "HK": "HKD", "HN": "HNL", "HR": "EUR", "HT": "HTG",
    "HU": "HUF", "ID": "IDR", "IE": "EUR", "IL": "ILS", "IN": "INR",
    "IQ": "IQD", "IR": "IRR", "IS": "ISK", "IT": "EUR", "JM": "JMD",
    "JO": "JOD", "JP": "JPY", "KE": "KES", "KG": "KGS", "KH": "KHR",
    "KM": "KMF", "KR": "KRW", "KW": "KWD", "KZ": "KZT", "LA": "LAK",
    "LB": "LBP", "LI": "CHF", "LK": "LKR", "LR": "LRD", "LS": "LSL",
    "LT": "EUR", "LU": "EUR", "LV": "EUR", "LY": "LYD", "MA": "MAD",
    "MC": "EUR", "MD": "MDL", "ME": "EUR", "MG": "MGA", "MK": "MKD",
    "ML": "XOF", "MM": "MMK", "MN": "MNT", "MO": "MOP", "MR": "MRU",
    "MT": "EUR", "MU": "MUR", "MV": "MVR", "MW": "MWK", "MX": "MXN",
    "MY": "MYR", "MZ": "MZN", "NA": "NAD", "NE": "XOF", "NG": "NGN",
    "NI": "NIO", "NL": "EUR", "NO": "NOK", "NP": "NPR", "NZ": "NZD",
    "OM": "OMR", "PA": "PAB", "PE": "PEN", "PG": "PGK", "PH": "PHP",
    "PK": "PKR", "PL": "PLN", "PT": "EUR", "PY": "PYG", "QA": "QAR",
    "RO": "RON", "RS": "RSD", "RU": "RUB", "RW": "RWF", "SA": "SAR",
    "SD": "SDG", "SE": "SEK", "SG": "SGD", "SI": "EUR", "SK": "EUR",
    "SL": "SLL", "SN": "XOF", "SO": "SOS", "SR": "SRD", "SV": "USD",
    "SY": "SYP", "SZ": "SZL", "TD": "XAF", "TG": "XOF", "TH": "THB",
    "TJ": "TJS", "TL": "USD", "TM": "TMT", "TN": "TND", "TO": "TOP",
    "TR": "TRY", "TT": "TTD", "TW": "TWD", "TZ": "TZS", "UA": "UAH",
    "UG": "UGX", "US": "USD", "UY": "UYU", "UZ": "UZS", "VE": "VES",
    "VN": "VND", "YE": "YER", "ZA": "ZAR", "ZM": "ZMW", "ZW": "ZWL",
}


@dataclass
class GeoLocation:
    country_name: str
    iso_country_code: str  # ISO 3166-1 alpha-2
    currency_iso: str      # ISO 4217


# Module-level cache — fetched at most once per process
_cache: Optional[GeoLocation] = None
_fetched: bool = False


def get_location() -> GeoLocation | None:
    """
    Return the current location via IP geolocation.
    Result is cached; the network call is made at most once per process.
    Returns None if the request fails or the country has no known currency.
    """
    global _cache, _fetched
    if _fetched:
        return _cache

    _fetched = True
    try:
        resp = requests.get("http://ip-api.com/json/", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return None
        code = data.get("countryCode", "").upper()
        currency = _COUNTRY_CURRENCY.get(code)
        if not currency:
            return None
        _cache = GeoLocation(
            country_name=data.get("country", code),
            iso_country_code=code,
            currency_iso=currency,
        )
    except Exception:
        _cache = None

    return _cache


def get_currency() -> str | None:
    """Return the ISO currency code for the current location, or None."""
    loc = get_location()
    return loc.currency_iso if loc else None
