"""Exact bounded scalars shared by preparation and venue risk observation."""
from decimal import Decimal, InvalidOperation
import re


def integer(value, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid bounded integer')
    return value


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise ValueError('Invalid public account identity')
    return value.lower()


def number(value, positive=True):
    # Floats are intentionally unsupported at the signing boundary.
    if type(value) not in (str, int, Decimal) or len(str(value)) > 64:
        raise ValueError('Exact bounded decimal required')
    try:
        n = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid decimal') from None
    if not n.is_finite() or abs(n) > Decimal('1e15') or n.as_tuple().exponent < -30:
        raise ValueError('Nonfinite/unbounded decimal')
    if positive and n <= 0 or not positive and n < 0:
        raise ValueError('Invalid decimal sign')
    return n


def wire(n):
    text = format(n, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text
