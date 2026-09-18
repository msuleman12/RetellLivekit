"""The call's date in the firm's local time, for resolving 'yesterday' and friends."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

# US Central time: CST in winter, CDT in summer.
FIRM_TZ = ZoneInfo("America/Chicago")


def spoken_date(timestamp: float | None = None) -> str:
    """e.g. 'Friday, September 18, 2026' for the given Unix time (default: now)."""
    day = datetime.fromtimestamp(time.time() if timestamp is None else timestamp, tz=FIRM_TZ)
    return f"{day:%A, %B} {day.day}, {day.year}"
