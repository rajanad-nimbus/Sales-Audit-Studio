"""Currency checks for calculations without an exchange-rate policy."""
import re
from collections.abc import Iterable


def validate_currency(currency: object) -> str:
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Currency must be a three-letter uppercase code")
    return currency


def single_currency(currencies: Iterable[object]) -> str:
    """Return the sole currency, or the legacy default for an empty store day."""
    codes = {validate_currency(currency) for currency in currencies}
    if len(codes) > 1:
        raise ValueError("Cannot calculate totals across multiple currencies without an exchange-rate policy")
    return next(iter(codes), "USD")
