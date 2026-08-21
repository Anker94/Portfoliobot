"""Markt-Kalender: NYSE-Feiertage + DST-feste Zeitzone (Europe/Berlin).

Cron-Zeiten im SKILL.md sind Europe/Berlin. US-Boersenoeffnung liegt meist
15:30 Berlin, zur DST-Overlap-Zeit (März) nur 14:30. Diese Datei rechnet
dynamisch gegen NY-Zeit -> keine harten Offsets.

Nur Mo-Sa laufen (So hat P5/P7 Sonderlogik). NYSE-Feiertage werden geskipt.
"""
from datetime import datetime, timedelta
import pandas_market_calendars as mcal

try:
    import pytz
except Exception:
    pytz = None

BERLIN = "Europe/Berlin"
NY = "America/New_York"


def _nyse():
    return mcal.get_calendar("NYSE")


def is_trading_day(d=None):
    """True wenn d (Berlin-Zeit-Datum) ein NYSE-Handelstag ist."""
    if d is None:
        d = datetime.now()
    cal = _nyse()
    day = d.date() if isinstance(d, datetime) else d
    sched = cal.valid_days(start_date=day, end_date=day)
    return len(sched) > 0


def should_skip_today():
    """Sonntag ODER NYSE-Feiertag -> skip (außer Sonderjobs P5/P7 am So)."""
    now = datetime.now()
    if now.weekday() == 6:  # Sonntag
        return True
    return not is_trading_day(now)


def ny_open_berlin(d=None):
    """Uhrzeit der US-Oeroeffnung in Berlin-Zeit als datetime.time."""
    if d is None:
        d = datetime.now()
    cal = _nyse()
    day = d.date() if isinstance(d, datetime) else d
    sched = cal.schedule.loc[cal.schedule.index.date == day]
    if sched.empty:
        return None
    open_utc = sched.iloc[0]["market_open"]  # tz-aware UTC
    if pytz:
        berlin = pytz.timezone(BERLIN)
        open_berlin = open_utc.tz_convert(berlin)
        return open_berlin.time()
    # Fallback ohne pytz
    offset = 6 if _is_dst_overlap(d) else 7
    return (open_utc.hour + offset, open_utc.minute)


def _is_dst_overlap(d):
    """Naive Heuristik: US ist schon in DST, EU noch nicht (~2. Maerz-Woche)."""
    return d.month == 3 and 8 <= d.day <= 21


if __name__ == "__main__":
    print("Heute Handelstag?", is_trading_day())
    print("Skip heute?", should_skip_today())
    print("NY-Oeroeffnung (Berlin):", ny_open_berlin())
