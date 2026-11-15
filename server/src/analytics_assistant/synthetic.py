"""Synthetic market data, so the project runs and tests with no real data.

Three made-up daily series with business-day observations, a publication
lag (a value for day t is published on t+1) and occasional revisions (a
later re-publication of the same observation with a corrected value). The
revisions are what make the point-in-time store worth testing: an as-of
query before the revision date must return the first-release value.

Nothing here resembles any real instrument, level or event.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from analytics_assistant.asof_store import AsOfStore, Observation

SERIES = {
    "RATE_LONG": ("Synthetic long-maturity rate (mean-reverting random walk)", "percent"),
    "POLICY_RATE": ("Synthetic policy rate (step function with occasional moves)", "percent"),
    "VOL_INDEX": ("Synthetic volatility index (log-normal, mean-reverting)", "index points"),
}

PUBLICATION_LAG_DAYS = 1
REVISION_LAG_DAYS = 5
REVISION_PROBABILITY = 0.03


def business_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def generate(
    start: date = date(2020, 1, 1),
    end: date = date(2024, 12, 31),
    seed: int = 7,
) -> list[Observation]:
    rng = random.Random(seed)
    days = business_days(start, end)
    rows: list[Observation] = []

    rate, policy, logvol = 3.0, 2.0, 2.9
    for d in days:
        # Long rate: Ornstein-Uhlenbeck around 3% with 5bp daily noise.
        rate += 0.01 * (3.0 - rate) + rng.gauss(0.0, 0.05)
        # Policy rate: rare 25bp moves in either direction, floored at zero.
        u = rng.random()
        if u < 0.004:
            policy += 0.25
        elif u < 0.008:
            policy = max(0.0, policy - 0.25)
        # Volatility: mean-reverting log process.
        logvol += 0.05 * (2.9 - logvol) + rng.gauss(0.0, 0.06)

        for name, value in (("RATE_LONG", rate), ("POLICY_RATE", policy),
                            ("VOL_INDEX", 2.718281828 ** logvol)):
            first = round(value, 4)
            pub = d + timedelta(days=PUBLICATION_LAG_DAYS)
            rows.append(Observation(name, d, first, pub))
            if name != "POLICY_RATE" and rng.random() < REVISION_PROBABILITY:
                revised = round(first + rng.gauss(0.0, 0.02), 4)
                rows.append(Observation(name, d, revised, d + timedelta(days=REVISION_LAG_DAYS)))
    return rows


def seed_store(store: AsOfStore, seed: int = 7) -> int:
    for name, (description, unit) in SERIES.items():
        store.register_series(name, description, unit)
    return store.insert(generate(seed=seed))
