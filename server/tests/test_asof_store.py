from datetime import date

import pytest

from analytics_assistant.asof_store import AsOfStore, Observation
from analytics_assistant.synthetic import SERIES, generate


def test_unpublished_values_are_invisible(tiny_store):
    # On Jan 2 only the Jan 1 value has been published.
    assert tiny_store.as_of("X", date(2024, 1, 2)) == [(date(2024, 1, 1), 1.0)]


def test_first_release_before_revision(tiny_store):
    rows = dict(tiny_store.as_of("X", date(2024, 1, 5)))
    assert rows[date(2024, 1, 2)] == 2.0


def test_revision_visible_after_its_publication(tiny_store):
    rows = dict(tiny_store.as_of("X", date(2024, 1, 10)))
    assert rows[date(2024, 1, 2)] == 2.5


def test_range_filters_on_observation_date(tiny_store):
    rows = tiny_store.as_of("X", date(2024, 2, 1), start=date(2024, 1, 2), end=date(2024, 1, 2))
    assert rows == [(date(2024, 1, 2), 2.5)]


def test_snapshot_id_changes_only_when_known_data_changes(tiny_store):
    a = tiny_store.snapshot_id("X", date(2024, 1, 5))
    b = tiny_store.snapshot_id("X", date(2024, 1, 9))  # nothing new published in between
    c = tiny_store.snapshot_id("X", date(2024, 1, 10))  # revision published
    assert a == b != c
    assert tiny_store.snapshot_id("X", date(2023, 12, 1)) is None


def test_rejects_publication_before_observation():
    store = AsOfStore(":memory:")
    with pytest.raises(ValueError):
        store.insert([Observation("X", date(2024, 1, 2), 1.0, date(2024, 1, 1))])


def test_synthetic_data_is_deterministic_and_lagged():
    a = generate(seed=3)
    assert a == generate(seed=3)
    assert a != generate(seed=4)
    assert {o.series for o in a} == set(SERIES)
    assert all(o.published_at > o.obs_date for o in a)


def test_synthetic_data_contains_revisions():
    rows = generate(seed=7)
    keys = [(o.series, o.obs_date) for o in rows]
    assert len(keys) > len(set(keys))
