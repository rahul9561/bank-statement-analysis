"""JSON serialization strategy.

* ``Decimal``  -> string, quantised to two decimals for money ("1234.50").
  Non-money decimals (ratios, percentages) are produced by the analysis modules
  as already-rounded Decimals and are serialised the same way.
* ``date``     -> ``"YYYY-MM-DD"``
* ``datetime`` -> ISO 8601 including the UTC offset.
* dataclasses / objects exposing ``to_dict`` are converted recursively.
* tuples and sets become lists.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

TWO_PLACES = Decimal("0.01")


def decimal_to_str(value: Decimal) -> str:
    if not value.is_finite():
        return str(value)
    return str(value.quantize(TWO_PLACES))


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    return str(value)
