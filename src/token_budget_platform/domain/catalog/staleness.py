"""Calendar staleness logic.

Requirement 4.5: A Token_Budget_Record crosses 12 calendar months after the
Reporting_Period end date is automatically labeled stale.

Uses month-clamping: add 12 to the month, clamping to the last day of the
resulting month if the original day exceeds it.
"""

from __future__ import annotations

import calendar
from datetime import date


def is_stale(reporting_end: date, observation_date: date) -> bool:
    """Return True if observation_date is later than reporting_end + 12 months.

    Month-clamping: if the resulting day exceeds the new month's length,
    clamp to the last day of that month.
    """
    year = reporting_end.year + (reporting_end.month + 12 - 1) // 12
    month = (reporting_end.month + 12 - 1) % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(reporting_end.day, max_day)
    boundary = date(year, month, day)
    return observation_date > boundary
