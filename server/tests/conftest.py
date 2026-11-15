from __future__ import annotations

import socket
from datetime import UTC, date, datetime

import pytest

from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry

FIXED_NOW = datetime(2025, 3, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any connection to a non-loopback address fails the test.

    Loopback stays allowed only because asyncio on Windows builds its event
    loop's self-pipe from a 127.0.0.1 socket pair.
    """
    real_connect = socket.socket.connect

    def guarded(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"tests must not touch the network ({host})")
        return real_connect(sock, address, *args, **kwargs)

    def refuse(*_args, **_kwargs):
        raise AssertionError("tests must not open outbound connections")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def seeded_store():
    store = AsOfStore(":memory:")
    seed_store(store, seed=7)
    return store


@pytest.fixture
def registry(seeded_store):
    return ToolRegistry(seeded_store, clock=lambda: FIXED_NOW)


@pytest.fixture
def tiny_store():
    """Hand-made rows for exact point-in-time assertions."""
    from analytics_assistant.asof_store import Observation

    store = AsOfStore(":memory:")
    store.register_series("X", "test series", "units")
    store.insert([
        Observation("X", date(2024, 1, 1), 1.0, date(2024, 1, 2)),
        Observation("X", date(2024, 1, 2), 2.0, date(2024, 1, 3)),
        Observation("X", date(2024, 1, 2), 2.5, date(2024, 1, 10)),  # revision
        Observation("X", date(2024, 1, 3), 3.0, date(2024, 1, 4)),
    ])
    return store
