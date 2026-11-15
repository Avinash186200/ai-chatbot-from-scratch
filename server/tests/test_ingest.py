"""Vendor drop-feed ingestion: arrival-based publication, append-only revisions,
idempotency, quarantine, and no change to answers already given."""

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.ingest.sftp_feed import (
    DropFeedIngester,
    FeedConfig,
    LocalDropSource,
    SFTPDropSource,
    summarise,
)
from analytics_assistant.ingest.synthetic_feed import write_synthetic_feed
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry


def at(day: date, hour: int = 6) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


D1, D2, D3, D4, D5, D8 = (date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8))


class Drop:
    """A local drop folder with helpers to place files with a given arrival time."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "drop"
        self.dir.mkdir()
        self.quarantine = tmp_path / "quarantine"
        self.store = AsOfStore(":memory:")

    def put(self, name, text, arrived: datetime, manifest: dict | None = None):
        path = self.dir / name
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        os.utime(path, (arrived.timestamp(), arrived.timestamp()))
        if manifest is not None:
            side = self.dir / f"{name}.manifest.json"
            side.write_text(json.dumps(manifest), encoding="utf-8")
            os.utime(side, (arrived.timestamp(), arrived.timestamp()))
        return path

    def run(self, now: datetime, replay=False, **config):
        return DropFeedIngester(self.store, LocalDropSource(self.dir), self.quarantine,
                                FeedConfig(**config), clock=lambda: now, replay=replay).run()

    def count(self):
        return self.store._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]


def csv(*rows):
    return "series,obs_date,value\n" + "".join(f"{s},{d},{v}\n" for s, d, v in rows)


@pytest.fixture
def drop(tmp_path):
    return Drop(tmp_path)


# ------------------------------------------------------------------ publication date


def test_published_at_is_arrival_not_file_date(drop):
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    [r] = drop.run(at(D2, 9))
    assert r.status == "loaded" and r.published_at == "2024-01-02" and r.rows_new == 1
    assert drop.store.as_of("RATE_SHORT", D1) == []
    assert drop.store.as_of("RATE_SHORT", D2) == [(D1, 2.0)]


def test_revision_is_a_new_row_and_first_release_stays_visible(drop):
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.run(at(D2, 9))
    drop.put("vendor_20240102.csv", csv(("RATE_SHORT", D2, 2.1), ("RATE_SHORT", D1, 2.05)),
             at(D3))
    [_, r] = drop.run(at(D3, 9))
    assert (r.rows_new, r.rows_revised) == (1, 1)
    assert drop.count() == 3  # nothing overwritten
    assert dict(drop.store.as_of("RATE_SHORT", D2))[D1] == 2.0
    assert dict(drop.store.as_of("RATE_SHORT", D3))[D1] == 2.05


def test_identical_restatement_writes_nothing(drop):
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.put("vendor_20240102.csv", csv(("RATE_SHORT", D2, 2.1), ("RATE_SHORT", D1, 2.0)),
             at(D3))
    results = drop.run(at(D3, 9), replay=True)
    assert results[1].rows_unchanged == 1 and drop.count() == 2


def test_late_file_never_changes_an_earlier_answer(drop):
    drop.put("vendor_20240102.csv", csv(("RATE_SHORT", D2, 2.1)), at(D3))
    drop.run(at(D3, 9))
    registry = ToolRegistry(drop.store, plugin_factories=[], clock=lambda: at(D8, 12))
    before = registry.call("query_series", {"series": "RATE_SHORT", "as_of": "2024-01-04"})
    assert before["ok"]

    # The D1 file was lost and turns up on D5 with its original (back-dated)
    # timestamp, and a restatement of D2 arrives the same day.
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.put("vendor_20240103.csv", csv(("RATE_SHORT", D3, 2.2), ("RATE_SHORT", D2, 2.15)),
             at(D4))
    results = {r.name: r for r in drop.run(at(D5, 9))}
    assert results["vendor_20240101.csv"].published_at == "2024-01-05"  # load date, not D2
    assert results["vendor_20240103.csv"].published_at == "2024-01-05"

    after = registry.call("query_series", {"series": "RATE_SHORT", "as_of": "2024-01-04"})
    assert after == before  # same rows, same snapshot_id
    latest = registry.call("query_series", {"series": "RATE_SHORT", "as_of": "2024-01-05"})
    assert [o["value"] for o in latest["payload"]["result"]["observations"]] == [2.0, 2.15, 2.2]
    assert latest["provenance"]["snapshot_id"] != before["provenance"]["snapshot_id"]


def test_replay_uses_arrival_but_never_goes_below_the_watermark(drop):
    drop.put("vendor_20240103.csv", csv(("RATE_SHORT", D3, 2.2)), at(D4))
    drop.run(at(D8), replay=True)
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    [r] = [x for x in drop.run(at(D8), replay=True) if x.status == "loaded"]
    assert r.published_at == "2024-01-04"  # the watermark, not the earlier arrival


def test_late_original_does_not_supersede_a_newer_restatement(drop):
    # The D2 file restates D1 and arrives on time; the D1 original arrives later.
    drop.put("vendor_20240102.csv", csv(("RATE_SHORT", D2, 2.1), ("RATE_SHORT", D1, 2.05)),
             at(D3))
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D5))
    results = {r.name: r for r in drop.run(at(D8), replay=True)}
    assert results["vendor_20240101.csv"].rows_stale == 1
    assert dict(drop.store.as_of("RATE_SHORT", D8))[D1] == 2.05


# ------------------------------------------------------------------ idempotency


def test_rerun_is_idempotent(drop):
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.run(at(D2, 9))
    n = drop.count()
    [r] = drop.run(at(D3, 9))
    assert r.status == "duplicate" and drop.count() == n


def test_same_bytes_under_another_name_are_a_duplicate(drop):
    text = csv(("RATE_SHORT", D1, 2.0))
    drop.put("vendor_20240101.csv", text, at(D2))
    drop.run(at(D2, 9))
    drop.put("vendor_20240101_resend.csv", text, at(D3))
    results = drop.run(at(D3, 9))
    assert [r.status for r in results] == ["duplicate", "duplicate"]


def test_corrected_file_with_the_same_name_loads_as_revision(drop):
    path = drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.run(at(D2, 9))
    path.write_text(csv(("RATE_SHORT", D1, 2.01)), encoding="utf-8")
    os.utime(path, (at(D3).timestamp(), at(D3).timestamp()))
    [r] = drop.run(at(D3, 9))
    assert r.status == "loaded" and r.rows_revised == 1


# ------------------------------------------------------------------ quarantine

GOOD = csv(("RATE_SHORT", D1, 2.0))


@pytest.mark.parametrize("name,text,reason", [
    ("vendor_20240101.csv", "date,value\n2024-01-01,1\n", "bad_header"),
    ("vendor_20240101.csv", "", "bad_header"),
    ("vendor_20240101.csv", "series,obs_date,value\n", "empty_file"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", D1, "n/a")), "bad_row"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", D1, "nan")), "bad_row"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", D1, "1_000")), "bad_row"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", "01/01/2024", 1)), "bad_row"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", "20240101", 1)), "bad_row"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", "2024-02-30", 1)), "bad_row"),
    ("vendor_20240101.csv", "series,obs_date,value\nRATE_SHORT,2024-01-01\n", "bad_row"),
    ("vendor_20240101.csv", csv(("NOT_IN_FEED", D1, 1)), "unknown_series"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", D2, 1)), "obs_after_file_date"),
    ("vendor_20240101.csv", csv(("RATE_SHORT", D1, 1), ("RATE_SHORT", D1, 1)), "duplicate_key"),
    ("vendor_20240101.csv", b"series,obs_date,value\n\xff\xfe", "not_utf8"),
    ("vendor_2024.csv", GOOD, "bad_filename"),
    ("vendor_20241301.csv", GOOD, "bad_filename"),
])
def test_malformed_files_are_quarantined_with_a_reason(drop, name, text, reason):
    drop.put(name, text, at(D2))
    [r] = drop.run(at(D2, 9))
    assert (r.status, r.reason) == ("quarantined", reason)
    assert drop.count() == 0
    reason_files = list(drop.quarantine.glob("*.reason.json"))
    assert len(reason_files) == 1
    recorded = json.loads(reason_files[0].read_text(encoding="utf-8"))
    assert recorded["reason"] == reason and recorded["file"] == name
    assert (drop.dir / name).exists()  # the drop itself is never modified
    # The same bytes are not retried on the next run.
    assert drop.run(at(D3, 9))[0].status == "duplicate"


def manifest_for(text, **overrides):
    m = {"file_date": "2024-01-01", "rows": text.count("\n") - 1,
         "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return {**m, **overrides}


@pytest.mark.parametrize("overrides,reason", [
    ({"sha256": "0" * 64}, "manifest_mismatch"),
    ({"rows": 5}, "manifest_mismatch"),
    ({"file_date": "2024-01-02"}, "manifest_mismatch"),
    ({"rows": "1"}, "manifest_invalid"),
    ({"sha256": "abc"}, "manifest_invalid"),
    ({"file_date": None}, "manifest_invalid"),
])
def test_manifest_is_checked(drop, overrides, reason):
    drop.put("vendor_20240101.csv", GOOD, at(D2), manifest=manifest_for(GOOD, **overrides))
    [r] = drop.run(at(D2, 9))
    assert (r.status, r.reason) == ("quarantined", reason)


def test_manifest_gives_the_date_when_the_name_has_none(drop):
    drop.put("daily_extract.csv", GOOD, at(D2), manifest=manifest_for(GOOD))
    [r] = drop.run(at(D2, 9))
    assert r.status == "loaded"


def test_history_conflict_quarantines_the_whole_file(drop):
    drop.put("vendor_20240101.csv", csv(("RATE_SHORT", D1, 2.0)), at(D2))
    drop.run(at(D2, 9))
    # Same publication day, different value for D1, plus a new D2 row: nothing is written.
    drop.put("vendor_20240102.csv", csv(("RATE_SHORT", D2, 2.1), ("RATE_SHORT", D1, 2.5)),
             at(D2, 7))
    results = drop.run(at(D2, 10))
    assert (results[-1].status, results[-1].reason) == ("quarantined", "history_conflict")
    assert drop.count() == 1


# ------------------------------------------------------------------ partial uploads


def test_files_still_being_written_wait(drop):
    drop.put("vendor_20240101.csv", GOOD, at(D2, 9))
    [r] = drop.run(at(D2, 9) + timedelta(seconds=5))
    assert (r.status, r.reason) == ("waiting", "settling")
    assert drop.store.feed_file(hashlib.sha256(GOOD.encode()).hexdigest()) is None
    assert drop.run(at(D2, 10))[0].status == "loaded"


def test_required_manifest_not_yet_landed_waits(drop):
    drop.put("vendor_20240101.csv", GOOD, at(D2))
    [r] = drop.run(at(D2, 9), require_manifest=True)
    assert (r.status, r.reason) == ("waiting", "manifest_pending")


# ------------------------------------------------------------------ synthetic feed + tools


def test_synthetic_feed_end_to_end(tmp_path):
    files = write_synthetic_feed(tmp_path / "drop", days=40)
    assert len(files) == 41
    store = AsOfStore(":memory:")
    seed_store(store)
    results = DropFeedIngester(store, LocalDropSource(tmp_path / "drop"), tmp_path / "q",
                               clock=lambda: at(date(2024, 6, 3)), replay=True).run()
    assert summarise(results) == {"loaded": 40, "quarantined": 1}
    assert sum(r.rows_revised for r in results) > 0
    assert DropFeedIngester(store, LocalDropSource(tmp_path / "drop"), tmp_path / "q",
                            clock=lambda: at(date(2024, 6, 3))).run()[0].status == "duplicate"

    registry = ToolRegistry(store, clock=lambda: at(date(2024, 6, 3), 12))
    env = registry.call("detect_regime", {"series": "RATE_SHORT", "as_of": "2024-02-28"})
    assert env["ok"] and env["provenance"]["snapshot_id"].startswith("snap-")
    env = registry.call("forecast_series", {"series": "SPREAD_A", "as_of": "2024-02-28"})
    assert env["ok"]


def test_synthetic_feed_is_deterministic(tmp_path):
    a = [p.read_bytes() for p in write_synthetic_feed(tmp_path / "a", days=10)]
    b = [p.read_bytes() for p in write_synthetic_feed(tmp_path / "b", days=10)]
    assert a == b


# ------------------------------------------------------------------ SFTP source


class FakeSFTP:
    def __init__(self, files):
        self.files = files
        self.opened = []

    def listdir_attr(self, path):
        assert path == "/outbound"
        return [SimpleNamespace(filename=name, st_size=len(data), st_mtime=mtime,
                                st_mode=mode)
                for name, (data, mtime, mode) in self.files.items()]

    def open(self, path, mode):
        import io

        self.opened.append(path)
        return io.BytesIO(self.files[path.rsplit("/", 1)[1]][0])


def test_sftp_source_lists_regular_files_and_reads_them(drop):
    regular, directory = 0o100644, 0o040755
    data = GOOD.encode()
    fake = FakeSFTP({"vendor_20240101.csv": (data, at(D2).timestamp(), regular),
                     "archive": (b"", 0, directory),
                     "..": (b"", 0, regular)})
    source = SFTPDropSource(fake, "/outbound")
    assert [f.name for f in source.list_files()] == ["vendor_20240101.csv"]
    results = DropFeedIngester(drop.store, source, drop.quarantine,
                               clock=lambda: at(D2, 9)).run()
    assert results[0].status == "loaded" and fake.opened == ["/outbound/vendor_20240101.csv"]
    with pytest.raises(ValueError):
        source.read_bytes("../etc/passwd")


def test_sftp_connect_without_paramiko_says_how_to_install(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "paramiko", None)
    with pytest.raises(RuntimeError, match="sftp"):
        SFTPDropSource.connect("sftp.invalid", "user", "key", "/outbound")
