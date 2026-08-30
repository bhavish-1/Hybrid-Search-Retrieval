"""Fiscal-period derivation (SPEC §4.3, "Fiscal vs calendar periods").

Our 20 filers have fiscal year-ends spread across the calendar: NVDA 31 Jan,
MRVL 30 Jan, MCHP 31 Mar, LRCX 28 Jun, KLAC 30 Jun, MU 3 Sep, QCOM 27 Sep,
SWKS 2 Oct, AMAT 26 Oct, ADI 31 Oct, AVGO 1 Nov, INTC/AMD late Dec, LSCC 2 Jan.
Most are 52/53-week filers, so the year-end DATE drifts by up to a week and can
cross a month or even a calendar-year boundary. Naive month comparison gets these
wrong, so we anchor on the year-end date with a tolerance window.

Do NOT use the SEC fact's own `fy`/`fp` fields as the fact's period: those describe
the FILING the fact was reported in, not the fact. A FY2024 10-K reports FY2022,
FY2023 and FY2024 revenue all carrying fy=2024, fp=FY.
"""
from __future__ import annotations

from datetime import date, timedelta

# A 52/53-week year-end drifts; allow this much slack when snapping to a fiscal year.
DRIFT_TOLERANCE_DAYS = 7

QUARTER_DAYS = (80, 100)
ANNUAL_DAYS = (350, 380)


def normalize_fye(fye: str | None) -> tuple[int, int]:
    """SEC `fiscalYearEnd` is 'MMDD' from the most recent filing, so a 53-week year can
    report e.g. '0102' for what the filer calls a December year. Snap near-calendar
    year-ends to 31 Dec; leave genuine late-January enders (NVDA, MRVL) alone."""
    if not fye or len(fye) != 4 or not fye.isdigit():
        return 12, 31
    m, d = int(fye[:2]), int(fye[2:])
    if m == 1 and d <= 7:
        return 12, 31
    if m == 12 and d >= 25:
        return 12, 31
    return m, d


def _fye_date(year: int, m: int, d: int) -> date:
    try:
        return date(year, m, d)
    except ValueError:  # 29 Feb anchor
        return date(year, m, d - 1)


def fiscal_year_of(period_end: date, fye: str | None) -> tuple[int, date, date]:
    """Return (fiscal_year, fy_start_exclusive, fy_end) for the FY containing period_end.

    FY(y) spans (FYE(y-1), FYE(y)]. We pick the smallest candidate year-end that is not
    more than DRIFT_TOLERANCE_DAYS before period_end, which absorbs 52/53-week drift.
    """
    m, d = normalize_fye(fye)
    cutoff = period_end - timedelta(days=DRIFT_TOLERANCE_DAYS)
    for y in (period_end.year - 1, period_end.year, period_end.year + 1):
        end = _fye_date(y, m, d)
        if end >= cutoff:
            return y, _fye_date(y - 1, m, d), end
    y = period_end.year + 1
    return y, _fye_date(y - 1, m, d), _fye_date(y, m, d)


def quarter_of(period_end: date, fye: str | None) -> int:
    """Which fiscal quarter period_end falls in (1-4)."""
    _, fy_start, fy_end = fiscal_year_of(period_end, fye)
    span = (fy_end - fy_start).days or 365
    elapsed = (period_end - fy_start).days
    q = int((elapsed - 1) // (span / 4)) + 1
    return max(1, min(4, q))


def classify_duration(start: date | None, end: date) -> str | None:
    """'Q' for a single fiscal quarter, 'FY' for a full year, None for YTD/other.

    Dropping 6- and 9-month year-to-date durations is deliberate and load-bearing:
    a 10-Q reports both "three months ended" and "nine months ended" revenue. If both
    land in the store, a query for "Q3 revenue" can silently return the nine-month
    figure -- a wrong answer that looks entirely plausible.
    """
    if start is None:
        return None
    days = (end - start).days
    if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
        return "Q"
    if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]:
        return "FY"
    return None


def fiscal_label(fiscal_year: int, fiscal_period: str) -> str:
    """'FY2024Q3' / 'FY2024FY'."""
    return f"FY{fiscal_year}{fiscal_period}"


def derive(period_start: date | None, period_end: date, fye: str | None,
           period_type: str) -> tuple[str, int, str] | None:
    """(fiscal_label, fiscal_year, fiscal_period) or None if the period is unusable."""
    fy, _, fy_end = fiscal_year_of(period_end, fye)
    if period_type == "instant":
        # A year-end balance is labelled FY; other quarter-ends get their quarter.
        if abs((period_end - fy_end).days) <= DRIFT_TOLERANCE_DAYS:
            fp = "FY"
        else:
            fp = f"Q{quarter_of(period_end, fye)}"
    else:
        kind = classify_duration(period_start, period_end)
        if kind is None:
            return None
        fp = "FY" if kind == "FY" else f"Q{quarter_of(period_end, fye)}"
    return fiscal_label(fy, fp), fy, fp
