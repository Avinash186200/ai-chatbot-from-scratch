"""Load a vendor's daily "drop folder" of CSV files into the point-in-time store.

A data vendor drops one CSV file per business day into a folder (an SFTP
drop in production; a local directory in tests and the demo). Each file is

    <feed>_<YYYYMMDD>.csv          header: series,obs_date,value
    <feed>_<YYYYMMDD>.csv.manifest.json   optional sidecar:
                                   {"file_date": "YYYY-MM-DD", "rows": N, "sha256": "..."}

A file normally carries the values for its own business date and may restate
earlier dates (vendor revisions).

Rules, each one tested in tests/test_ingest.py:

* **Publication date comes from arrival, never from the file.** A row's
  ``published_at`` is the date the file arrived in the drop (its modification
  time), not the date written inside it, because a backtest must only see a
  value from the day we could actually have read it.
* **A late file never changes an answer already given.** In live mode the
  publication date is ``max(arrival date, load date, feed watermark)``: a file
  that sat unloaded in the drop for days, or that arrives with a back-dated
  timestamp, becomes visible from the day it is loaded, so every as-of query
  for an earlier date returns exactly what it returned before. (A query as of
  *today* can still change when a file lands later today; publication dates
  have day granularity.) In replay mode, used to rebuild a store from an
  archive of drops, the load-date floor is dropped but the watermark still
  keeps publication dates in load order.
* **Append only.** New observations are inserted; a restated value becomes a
  new revision row; an identical restatement writes nothing. A row that would
  replace a stored value at the same publication date, or be published before
  one, is a ``history_conflict`` and the whole file is quarantined.
* **Late originals do not supersede newer restatements.** Each key remembers
  the vendor file date that last set it; a row from an older vendor file that
  is loaded later (it arrived late) is skipped as ``stale``.
* **Idempotent by checksum.** Every file's SHA-256 is recorded in a ledger in
  the same transaction as its rows. The same bytes dropped again, under any
  name, are skipped. A corrected file under the same name is a different
  checksum and loads as revisions.
* **Malformed files are quarantined, not half-loaded.** The file is copied to
  the quarantine folder with a ``.reason.json`` next to it and recorded in the
  ledger with a reason code, so it is not retried until the vendor sends a
  corrected file. The drop folder itself is never modified (an SFTP drop is
  often read-only).
* **Partial uploads are left alone.** A file younger than ``settle_seconds``,
  or (with ``require_manifest``) one whose manifest has not landed yet, is
  reported as ``waiting`` and picked up on the next run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import posixpath
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from analytics_assistant.asof_store import AsOfStore, HistoryConflict, Observation

log = logging.getLogger(__name__)

MANIFEST_SUFFIX = ".manifest.json"
HEADER = ("series", "obs_date", "value")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

DEFAULT_FEED_SERIES: dict[str, tuple[str, str]] = {
    "RATE_SHORT": ("Synthetic short-maturity rate delivered by the vendor drop feed", "percent"),
    "SPREAD_A": ("Synthetic spread between two made-up rates, from the vendor drop feed",
                 "percent"),
}

# Reason codes recorded for a quarantined file.
REASONS = frozenset({
    "bad_filename", "manifest_invalid", "manifest_mismatch", "too_large", "not_utf8",
    "bad_header", "bad_row", "unknown_series", "obs_after_file_date",
    "obs_after_publication", "duplicate_key", "empty_file", "history_conflict",
})


def is_safe_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name \
        and "\x00" not in name


# ---------------------------------------------------------------------- sources


@dataclass(frozen=True)
class DropFile:
    name: str
    size: int
    modified_at: datetime  # timezone-aware, UTC


class DropSource(Protocol):
    """Where the vendor's files are. Listing and reading only; never writes."""

    def list_files(self) -> list[DropFile]: ...

    def read_bytes(self, name: str) -> bytes: ...


class LocalDropSource:
    """A local directory standing in for the SFTP drop."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)

    def list_files(self) -> list[DropFile]:
        out = []
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False) and is_safe_name(entry.name):
                    st = entry.stat(follow_symlinks=False)
                    out.append(DropFile(entry.name, st.st_size,
                                        datetime.fromtimestamp(st.st_mtime, UTC)))
        return out

    def read_bytes(self, name: str) -> bytes:
        if not is_safe_name(name):
            raise ValueError(f"unsafe file name {name!r}")
        return (self.directory / name).read_bytes()


class SFTPDropSource:
    """A remote drop directory read through an SFTP client.

    ``client`` is a connected ``paramiko.SFTPClient`` or anything with the same
    ``listdir_attr`` and ``open`` methods (the tests pass a fake). ``connect``
    builds one with paramiko, which is an optional extra (``pip install
    .[sftp]``) and never trusts an unknown host key.
    """

    def __init__(self, client: Any, remote_dir: str, on_close: Callable[[], None] | None = None
                 ) -> None:
        self.client = client
        self.remote_dir = remote_dir
        self._on_close = on_close

    @classmethod
    def connect(cls, host: str, username: str, key_filename: str, remote_dir: str,
                port: int = 22, known_hosts: str | None = None) -> SFTPDropSource:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SFTP needs the optional 'sftp' extra: "
                               "pip install 'analytics-assistant[sftp]'") from exc
        ssh = paramiko.SSHClient()
        if known_hosts:
            ssh.load_host_keys(known_hosts)
        else:
            ssh.load_system_host_keys()
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        ssh.connect(host, port=port, username=username, key_filename=key_filename,
                    allow_agent=False, look_for_keys=False, timeout=30)
        return cls(ssh.open_sftp(), remote_dir, on_close=ssh.close)

    def close(self) -> None:
        self.client.close()
        if self._on_close:
            self._on_close()

    def list_files(self) -> list[DropFile]:
        out = []
        for attr in self.client.listdir_attr(self.remote_dir):
            mode = getattr(attr, "st_mode", None)
            if mode is not None and not stat.S_ISREG(mode):
                continue
            if not is_safe_name(attr.filename):
                log.warning("skipping unsafe remote file name %r", attr.filename)
                continue
            out.append(DropFile(attr.filename, int(attr.st_size or 0),
                                datetime.fromtimestamp(attr.st_mtime or 0, UTC)))
        return out

    def read_bytes(self, name: str) -> bytes:
        if not is_safe_name(name):
            raise ValueError(f"unsafe file name {name!r}")
        with self.client.open(posixpath.join(self.remote_dir, name), "rb") as fh:
            return fh.read()


# ---------------------------------------------------------------------- config + results


@dataclass(frozen=True)
class FeedConfig:
    name: str = "vendor"
    # Series this feed may carry, with (description, unit). Anything else is
    # quarantined as unknown_series rather than silently creating a series.
    series: Mapping[str, tuple[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_FEED_SERIES))
    require_manifest: bool = False
    settle_seconds: int = 30
    max_bytes: int = 20_000_000

    @property
    def filename_re(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.name)}_(\d{{8}})\.csv$")


@dataclass(frozen=True)
class FileResult:
    name: str
    status: str  # loaded | duplicate | quarantined | waiting
    checksum: str | None = None
    reason: str | None = None
    detail: str | None = None
    published_at: str | None = None
    rows_new: int = 0
    rows_revised: int = 0
    rows_unchanged: int = 0
    rows_stale: int = 0


class Quarantine(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        assert reason in REASONS, reason
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------- ingester


class DropFeedIngester:
    def __init__(self, store: AsOfStore, source: DropSource,
                 quarantine_dir: str | os.PathLike[str], config: FeedConfig | None = None,
                 clock: Callable[[], datetime] | None = None, replay: bool = False) -> None:
        self.store = store
        self.source = source
        self.quarantine_dir = Path(quarantine_dir)
        self.config = config or FeedConfig()
        self.now = clock or (lambda: datetime.now(UTC))
        self.replay = replay

    def run(self) -> list[FileResult]:
        """Process every data file in the drop, oldest arrival first."""
        now = self.now()
        listing = self.source.list_files()
        names = {f.name for f in listing}
        data_files = sorted((f for f in listing if f.name.lower().endswith(".csv")),
                            key=lambda f: (f.modified_at, f.name))
        results = [self._process(f, names, now) for f in data_files]
        for r in results:
            log.info("feed %s: %s %s %s", self.config.name, r.name, r.status, r.reason or "")
        return results

    # -------------------------------------------------------------- one file

    def _process(self, f: DropFile, names: set[str], now: datetime) -> FileResult:
        if (now - f.modified_at).total_seconds() < self.config.settle_seconds:
            return FileResult(f.name, "waiting", reason="settling")
        manifest_name = f.name + MANIFEST_SUFFIX
        if self.config.require_manifest and manifest_name not in names:
            return FileResult(f.name, "waiting", reason="manifest_pending")

        data = self.source.read_bytes(f.name)
        checksum = hashlib.sha256(data).hexdigest()
        seen = self.store.feed_file(checksum)
        if seen is not None:
            return FileResult(f.name, "duplicate", checksum, reason=seen["status"])

        ledger = {"checksum": checksum, "feed": self.config.name, "filename": f.name,
                  "arrived_at": f.modified_at.isoformat(), "loaded_at": now.isoformat()}
        try:
            if len(data) > self.config.max_bytes:
                raise Quarantine("too_large", f"{len(data)} bytes > {self.config.max_bytes}")
            manifest = self._read_manifest(manifest_name, names)
            file_date = self._file_date(f.name, manifest)
            rows = self._parse(data, file_date)
            if manifest is not None:
                if manifest["sha256"] != checksum:
                    raise Quarantine("manifest_mismatch", "sha256 does not match the file")
                if manifest["rows"] != len(rows):
                    raise Quarantine("manifest_mismatch",
                                     f"manifest says {manifest['rows']} rows, file has {len(rows)}")
            published = self._publication_date(f.modified_at.date(), now.date())
            late = [k for k in rows if k[1] > published]
            if late:
                raise Quarantine("obs_after_publication",
                                 f"{late[0][0]} {late[0][1]} is after the publication date "
                                 f"{published}")
            for series in sorted({s for s, _ in rows}):
                description, unit = self.config.series[series]
                self.store.register_series(series, description, unit)
            ledger["published_at"] = published.isoformat()
            counts = self.store.append_feed_file(
                [Observation(s, d, v, published) for (s, d), v in rows.items()], ledger,
                vendor_date=file_date)
        except Quarantine as q:
            return self._quarantine(f, data, checksum, ledger, q.reason, q.detail)
        except HistoryConflict as exc:
            return self._quarantine(f, data, checksum, ledger, "history_conflict", str(exc))
        return FileResult(f.name, "loaded", checksum, published_at=published.isoformat(),
                          rows_new=counts.new, rows_revised=counts.revised,
                          rows_unchanged=counts.unchanged, rows_stale=counts.stale)

    def _publication_date(self, arrival: date, today: date) -> date:
        floors = [arrival]
        if not self.replay:
            floors.append(today)
        if (watermark := self.store.feed_watermark(self.config.name)) is not None:
            floors.append(watermark)
        return max(floors)

    def _read_manifest(self, manifest_name: str, names: set[str]) -> dict[str, Any] | None:
        if manifest_name not in names:
            return None
        try:
            raw = json.loads(self.source.read_bytes(manifest_name).decode("utf-8"))
            file_date = raw["file_date"]
            rows = raw["rows"]
            sha = raw["sha256"]
            if not (isinstance(file_date, str) and _ISO_DATE.match(file_date)):
                raise ValueError("file_date")
            if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
                raise ValueError("rows")
            if not (isinstance(sha, str) and _SHA256.match(sha)):
                raise ValueError("sha256")
            return {"file_date": date.fromisoformat(file_date), "rows": rows, "sha256": sha}
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise Quarantine("manifest_invalid", f"unreadable manifest ({exc})") from None

    def _file_date(self, name: str, manifest: dict[str, Any] | None) -> date:
        from_name = None
        if m := self.config.filename_re.match(name):
            try:
                from_name = datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                raise Quarantine("bad_filename", f"{m.group(1)} is not a date") from None
        if manifest is not None:
            if from_name is not None and from_name != manifest["file_date"]:
                raise Quarantine("manifest_mismatch",
                                 f"file name date {from_name} != manifest {manifest['file_date']}")
            return manifest["file_date"]
        if from_name is None:
            raise Quarantine("bad_filename",
                             f"expected {self.config.name}_YYYYMMDD.csv or a manifest")
        return from_name

    def _parse(self, data: bytes, file_date: date) -> dict[tuple[str, date], float]:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Quarantine("not_utf8", "file is not UTF-8 text") from None
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        if header is None or [h.strip().lower() for h in header] != list(HEADER):
            raise Quarantine("bad_header", f"expected header {','.join(HEADER)}")
        rows: dict[tuple[str, date], float] = {}
        for lineno, record in enumerate(reader, start=2):
            if not record or all(not c.strip() for c in record):
                continue
            if len(record) != len(HEADER):
                raise Quarantine("bad_row", f"line {lineno}: expected {len(HEADER)} fields, "
                                            f"got {len(record)}")
            series, raw_date, raw_value = (c.strip() for c in record)
            if series not in self.config.series:
                raise Quarantine("unknown_series", f"line {lineno}: {series!r} is not part of "
                                                   f"feed {self.config.name!r}")
            if not _ISO_DATE.match(raw_date):
                raise Quarantine("bad_row", f"line {lineno}: obs_date must be YYYY-MM-DD")
            try:
                obs = date.fromisoformat(raw_date)
            except ValueError:
                raise Quarantine("bad_row", f"line {lineno}: {raw_date} is not a date") from None
            if not _NUMBER.match(raw_value) or not math.isfinite(value := float(raw_value)):
                raise Quarantine("bad_row", f"line {lineno}: value is not a finite number")
            if obs > file_date:
                raise Quarantine("obs_after_file_date",
                                 f"line {lineno}: {obs} is after the file date {file_date}")
            if (series, obs) in rows:
                raise Quarantine("duplicate_key", f"line {lineno}: {series} {obs} appears twice")
            rows[(series, obs)] = value
        if not rows:
            raise Quarantine("empty_file", "no data rows")
        return rows

    def _quarantine(self, f: DropFile, data: bytes, checksum: str, ledger: dict[str, Any],
                    reason: str, detail: str) -> FileResult:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{checksum[:12]}_{_UNSAFE.sub('_', f.name)}"
        (self.quarantine_dir / stem).write_bytes(data)
        (self.quarantine_dir / f"{stem}.reason.json").write_text(json.dumps({
            "file": f.name, "feed": self.config.name, "checksum": checksum, "reason": reason,
            "detail": detail, "arrived_at": ledger["arrived_at"],
            "detected_at": ledger["loaded_at"]}, indent=2) + "\n", encoding="utf-8")
        self.store.record_feed_file({**ledger, "published_at": None, "status": "quarantined",
                                     "reason": reason, "detail": detail})
        log.warning("feed %s: quarantined %s (%s: %s)", self.config.name, f.name, reason, detail)
        return FileResult(f.name, "quarantined", checksum, reason=reason, detail=detail)


def summarise(results: list[FileResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out
