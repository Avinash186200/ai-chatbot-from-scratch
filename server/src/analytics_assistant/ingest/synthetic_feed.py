"""Write a made-up vendor drop folder, to exercise the drop-feed ingester.

One CSV per business day for the feed's series (RATE_SHORT, SPREAD_A), each
with a manifest sidecar and a modification time set to its arrival (the next
morning). Some files restate an earlier day (a vendor revision), one file
arrives several days late, and one is malformed so it is quarantined.

Nothing here resembles any real instrument, level or event.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from analytics_assistant.ingest.sftp_feed import MANIFEST_SUFFIX
from analytics_assistant.synthetic import business_days

ARRIVAL_TIME = time(6, 0, tzinfo=UTC)


def _set_arrival(path: Path, arrival: datetime) -> None:
    ts = arrival.timestamp()
    os.utime(path, (ts, ts))


def _write(path: Path, rows: list[tuple[str, date, float]], arrival: datetime,
           manifest: bool, file_date: date) -> None:
    body = "series,obs_date,value\n" + "".join(
        f"{s},{d.isoformat()},{v:.4f}\n" for s, d, v in rows)
    data = body.encode("utf-8")
    path.write_bytes(data)
    _set_arrival(path, arrival)
    if manifest:
        side = path.with_name(path.name + MANIFEST_SUFFIX)
        side.write_text(json.dumps({"file_date": file_date.isoformat(), "rows": len(rows),
                                    "sha256": hashlib.sha256(data).hexdigest()}) + "\n",
                        encoding="utf-8")
        _set_arrival(side, arrival)


def write_synthetic_feed(out_dir: str | os.PathLike[str], start: date = date(2024, 1, 2),
                         days: int = 60, seed: int = 11, feed: str = "vendor",
                         revision_probability: float = 0.15, late_index: int | None = 10,
                         late_by_days: int = 6, malformed: bool = True,
                         manifests: bool = True) -> list[Path]:
    """Write ``days`` business-day files into ``out_dir``; returns the data files."""
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calendar = business_days(start, start + timedelta(days=days * 2))[:days]
    short, spread = 2.0, 1.2
    history: dict[tuple[str, date], float] = {}
    written: list[Path] = []
    for i, d in enumerate(calendar):
        short += 0.02 * (2.0 - short) + rng.gauss(0.0, 0.03)
        spread += 0.05 * (1.2 - spread) + rng.gauss(0.0, 0.02)
        rows = [("RATE_SHORT", d, round(short, 4)), ("SPREAD_A", d, round(spread, 4))]
        if i > 0 and rng.random() < revision_probability:
            prev = calendar[i - 1]
            name = rng.choice(["RATE_SHORT", "SPREAD_A"])
            rows.append((name, prev, round(history[(name, prev)] + rng.gauss(0.0, 0.01), 4)))
        for s, od, v in rows:
            history[(s, od)] = v
        arrival = datetime.combine(d + timedelta(days=1), ARRIVAL_TIME)
        if late_index is not None and i == late_index:
            arrival += timedelta(days=late_by_days)
        path = out / f"{feed}_{d:%Y%m%d}.csv"
        _write(path, rows, arrival, manifests, d)
        written.append(path)
    if malformed and calendar:
        d = calendar[-1] + timedelta(days=1)
        path = out / f"{feed}_{d:%Y%m%d}.csv"
        path.write_text(f"series,obs_date,value\nSPREAD_A,{d.isoformat()},n/a\n",
                        encoding="utf-8")
        _set_arrival(path, datetime.combine(d + timedelta(days=1), ARRIVAL_TIME))
        written.append(path)
    return written
