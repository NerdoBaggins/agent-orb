import math


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite(value) -> bool:
    """A number that converts to a finite float: not NaN, not infinite, and not an integer too large to convert."""
    try:
        return is_number(value) and math.isfinite(value)
    except OverflowError:
        return False
