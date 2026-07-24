"""Checksum validators and OCR-confusion normalisation helpers.

These are used to validate structured identifiers (Aadhaar via Verhoeff,
payment cards via Luhn, PAN via structure) and to recover identifiers that
OCR has mangled with the classic character confusions (O<->0, I/l<->1,
S<->5, B<->8).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Verhoeff checksum (used by Aadhaar)
# ---------------------------------------------------------------------------

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_checksum(digits: str) -> int:
    """Return the Verhoeff check state for a digit string (0 means valid)."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c


def verhoeff_valid(digits: str) -> bool:
    """True if the digit string (including its final check digit) is valid."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    return verhoeff_checksum(digits) == 0


def verhoeff_generate(digits: str) -> str:
    """Append the correct Verhoeff check digit to a digit string."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return digits + str(_VERHOEFF_INV[c])


# ---------------------------------------------------------------------------
# Luhn checksum (payment cards)
# ---------------------------------------------------------------------------

def luhn_valid(digits: str) -> bool:
    """True if the digit string passes the Luhn check."""
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def luhn_generate(digits: str) -> str:
    """Append the correct Luhn check digit to a digit string."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # positions counted with the check digit appended
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return digits + str((10 - total % 10) % 10)


# ---------------------------------------------------------------------------
# PAN structure
# ---------------------------------------------------------------------------

_PAN_RE = re.compile(r"^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$")


def pan_structurally_valid(value: str) -> bool:
    """True if the value matches the Indian PAN structure.

    The 4th character encodes the holder type and must be one of the
    documented category letters; this alone rejects most random
    letter/digit strings that merely match ``[A-Z]{5}\\d{4}[A-Z]``.
    """
    return bool(_PAN_RE.match(value))


# ---------------------------------------------------------------------------
# OCR-confusion normalisation
# ---------------------------------------------------------------------------

# Characters OCR commonly swaps, in each direction.
TO_DIGIT = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0",
                          "I": "1", "l": "1", "i": "1", "|": "1",
                          "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
                          "G": "6", "g": "9"})
TO_ALPHA = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})


def normalize_digits(span: str) -> str:
    """Map likely-digit lookalikes to digits and drop separators."""
    return re.sub(r"[\s\-]", "", span).translate(TO_DIGIT)


def normalize_pan(span: str) -> str | None:
    """Positionally repair a 10-character PAN candidate.

    Characters 1-5 and 10 must be letters, characters 6-9 digits; repair
    each position with the appropriate confusion table and return the
    result, or None if the span is not 10 characters long.
    """
    compact = re.sub(r"[\s\-]", "", span).upper()
    if len(compact) != 10:
        return None
    fixed = (
        compact[:5].translate(TO_ALPHA)
        + compact[5:9].translate(TO_DIGIT)
        + compact[9].translate(TO_ALPHA)
    )
    return fixed
