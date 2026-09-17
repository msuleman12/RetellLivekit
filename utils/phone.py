"""US phone number normalization and masking."""

import re

_WORD_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def extract_digits(raw: str) -> str:
    """Digits from typed or spoken input: "two one four, double five" -> "21455"."""
    tokens = re.sub(r"[^\w\s]", " ", raw.lower()).split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("double", "triple") and i + 1 < len(tokens) and tokens[i + 1] in _WORD_DIGITS:
            out.append(_WORD_DIGITS[tokens[i + 1]] * (2 if tok == "double" else 3))
            i += 2
            continue
        out.append(_WORD_DIGITS.get(tok) or re.sub(r"\D", "", tok))
        i += 1
    return "".join(out)


def normalize_us_phone(raw: str | None) -> str | None:
    """A valid 10-digit NANP number, or None. Never guesses a partial number."""
    if not raw:
        return None
    digits = extract_digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "23456789" or digits[3] not in "23456789":
        return None
    return digits


def mask_phone(raw: str | None) -> str:
    """Last four digits only, for logs."""
    digits = re.sub(r"\D", "", raw or "")
    return f"***{digits[-4:]}" if len(digits) >= 4 else "-"
