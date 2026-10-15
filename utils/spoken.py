"""Spelled-out speech to written form: emails and report/claim numbers."""

import re

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_EMAIL_WORDS = {"dot": ".", "period": ".", "point": ".", "underscore": "_", "dash": "-", "hyphen": "-", "plus": "+"}
_DASH_WORDS = {"dash", "hyphen", "-"}
_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}")
_REFERENCE_RE = re.compile(r"[A-Z0-9]+(-[A-Z0-9]+)*")


def _numbers_to_digits(tokens: list[str], oh_is_zero: bool = False) -> list[str]:
    """"ninety eight" -> "98", "double l" -> "ll", "nineteen" -> "19"; other tokens pass through."""
    units = {**_UNITS, "oh": 0} if oh_is_zero else _UNITS
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in ("double", "triple") and nxt is not None and (nxt in units or len(nxt) == 1):
            piece = str(units[nxt]) if nxt in units else nxt
            out.append(piece * (2 if tok == "double" else 3))
            i += 2
            continue
        if tok in _TENS and nxt in units and units[nxt] != 0:
            out.append(str(_TENS[tok] + units[nxt]))
            i += 2
            continue
        if tok in _TENS:
            out.append(str(_TENS[tok]))
        elif tok in _TEENS:
            out.append(str(_TEENS[tok]))
        elif tok in units:
            out.append(str(units[tok]))
        else:
            out.append(tok)
        i += 1
    return out


def normalize_email(raw: str | None) -> str | None:
    """"harry l philips ninety eight at gmail dot com" -> "harrylphilips98@gmail.com", or None."""
    if not raw:
        return None
    text = raw.strip().lower()
    compact = re.sub(r"\s+", "", text)
    if _EMAIL_RE.fullmatch(compact):
        return compact

    tokens = _numbers_to_digits(re.findall(r"[a-z0-9]+|[@._+-]", text))
    if "@" not in tokens and "at" in tokens:
        last_at = len(tokens) - 1 - tokens[::-1].index("at")
        tokens[last_at] = "@"
    email = "".join(_EMAIL_WORDS.get(tok, tok) for tok in tokens)
    return email if _EMAIL_RE.fullmatch(email) else None


def _is_code_token(tok: str) -> bool:
    lower = tok.lower()
    return (
        len(tok) == 1
        or any(c.isdigit() for c in tok)
        or tok.isupper()
        or lower in _UNITS or lower in _TEENS or lower in _TENS
        or lower in ("oh", "double", "triple")
        or lower in _DASH_WORDS
    )


def normalize_reference(raw: str | None) -> str | None:
    """"one two three k l f" -> "123KLF". None when the value holds anything but a spelled code."""
    if not raw:
        return None
    tokens = re.findall(r"[A-Za-z0-9]+|-", raw)
    if not tokens or not all(_is_code_token(tok) for tok in tokens):
        return None
    pieces = _numbers_to_digits([tok.lower() for tok in tokens], oh_is_zero=True)
    code = "".join("-" if tok in _DASH_WORDS else tok for tok in pieces).upper()
    return code if _REFERENCE_RE.fullmatch(code) else None
