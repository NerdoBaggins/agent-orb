import math


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite(value) -> bool:
    try:
        return is_number(value) and math.isfinite(value)
    except OverflowError:
        return False
