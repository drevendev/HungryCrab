"""Adversarial owned probes against pinned PR heads; no model or donor execution."""

from __future__ import annotations

import os
import socket
import socketserver
import threading
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

PR = os.environ["REVIEW_PR"]
discovery = pytest.mark.skipif(PR != "677", reason="different pinned PR")
acquisition = pytest.mark.skipif(PR != "687", reason="different pinned PR")


@pytest.fixture
def owned_session(tmp_path: Path) -> Any:
    from discovery_host_fixture import OwnedMeter
    from test_discovery_usage import setup_session

    from hungry_crab.discovery_host import host_meter

    meter = OwnedMeter()
    with host_meter(meter):
        directory, _, action, instant = setup_session(tmp_path)
        yield directory, action, instant, meter


@discovery
def test_control_known_model_use_and_known_cap_violation(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = 101
    sessions.execute_operation(
        directory, action["attempt"], "known-overrun", lambda _: None,
        kind="model", model_token_limit=100, now=instant,
    )
    result = sessions.report(directory)
    assert result["actual_model_tokens"] == 101
    assert result["budget_within_limits"] is False


@discovery
def test_late_actual_usage_must_enforce_the_original_operation_cap(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = None
    sessions.execute_operation(
        directory, action["attempt"], "late-overrun", lambda _: None,
        kind="model", model_token_limit=100, now=instant,
    )
    original = next(
        event["record"]["receipt"] for event in sessions._load(directory)["events"]
        if event["record"]["kind"] == "model-usage"
    )
    result = sessions.record_usage(directory, {**original, "model_tokens": 101})
    assert result["actual_model_tokens"] == 101
    assert result["budget_within_limits"] is False, result


@discovery
def test_stopped_in_flight_model_cost_must_be_unknown(owned_session: Any) -> None:
    from hungry_crab import discovery_session as sessions

    directory, action, instant, meter = owned_session
    meter.tokens = 23
    observed: dict[str, Any] = {}

    def invoke(_: int) -> None:
        sessions.stop(directory, "external-dependency", now=instant)
        observed.update(sessions.report(directory))

    sessions.execute_operation(
        directory, action["attempt"], "in-flight", invoke,
        kind="model", model_token_limit=100, now=instant,
    )
    assert sessions.report(directory)["actual_model_tokens"] == 23
    assert observed["actual_model_tokens"] is None, observed
    assert observed["model_usage_complete"] is False, observed


@discovery
def test_orphaned_model_cost_must_not_be_reported_as_zero(owned_session: Any) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter

    directory, action, instant, _ = owned_session

    class InterruptedMeter(OwnedMeter):
        def finish(self, ticket: object) -> int | None:
            raise KeyboardInterrupt("owned interruption of native usage retrieval")

    with host_meter(InterruptedMeter()), pytest.raises(KeyboardInterrupt):
        sessions.execute_operation(
            directory, action["attempt"], "orphan", lambda _: None,
            kind="model", model_token_limit=100, now=instant,
        )
    assert sessions.next_action(directory, now=instant)["action"] == "reconcile"
    report = sessions.report(directory)
    assert report["actual_model_tokens"] is None, report
    assert report["model_usage_complete"] is False, report


@discovery
def test_successful_begin_with_none_ticket_must_still_finish(owned_session: Any) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import host_meter

    directory, action, instant, _ = owned_session
    finished: list[object] = []

    class StatelessMeter(OwnedMeter):
        def begin(self, operation: str, token_limit: int) -> object:
            return None

        def finish(self, ticket: object) -> int | None:
            finished.append(ticket)
            return 23

    with host_meter(StatelessMeter()):
        sessions.execute_operation(
            directory, action["attempt"], "stateless", lambda _: None,
            kind="model", model_token_limit=100, now=instant,
        )
    assert finished == [None]
    assert sessions.report(directory)["actual_model_tokens"] == 23


@discovery
def test_expiry_during_meter_preflight_must_refuse_dispatch(
    owned_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from discovery_host_fixture import OwnedMeter

    from hungry_crab import discovery_session as sessions
    from hungry_crab.discovery_host import MeterIdentity, host_meter
    from hungry_crab.errors import CrabError

    directory, action, instant, _ = owned_session
    clock = [instant]
    effects: list[int] = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return clock[0]

    class SlowPreflight(OwnedMeter):
        def preflight(self) -> MeterIdentity:
            clock[0] = instant + timedelta(minutes=11)
            return super().preflight()

    monkeypatch.setattr(sessions, "datetime", Clock)
    with host_meter(SlowPreflight()):
        try:
            sessions.execute_operation(directory, action["attempt"], "expired", effects.append)
        except CrabError:
            pass
    assert effects == [], {"effects": effects, "clock": clock[0], "expires": action["expires_at"]}


@acquisition
def test_scope_exit_must_prevent_a_new_upstream_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab.fetch.acquisition import AcquisitionBudget

    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    quota = AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)
    ready = threading.Event()
    release = threading.Event()
    connected = threading.Event()
    finished = threading.Event()
    real_recv = socket.socket.recv
    real_connect = socket.create_connection
    header = bytearray()
    owner = threading.current_thread()
    effects: list[tuple[str, int]] = []

    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def recv(stream: socket.socket, count: int, *args: Any) -> bytes:
            data = real_recv(stream, count, *args)
            if threading.current_thread() is not owner and count == 1:
                header.extend(data)
                if header.endswith(b"\r\n\r\n"):
                    ready.set()
                    assert release.wait(10)
            return data

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            effects.append(address)
            connected.set()
            return upstream

        monkeypatch.setattr(socket.socket, "recv", recv)
        monkeypatch.setattr(socketserver.BaseRequestHandler, "finish", lambda _: finished.set())
        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        try:
            with quota.scope():
                assert quota._server is not None
                client = cleanup.enter_context(real_connect(quota._server.server_address, 3))
                client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
                assert ready.wait(3)
            # Final header byte was already received by the handler. It resumes
            # only after the stopped scope has returned to its caller.
            release.set()
            assert finished.wait(3)
            assert effects == [], effects
        finally:
            release.set()


@acquisition
def test_control_live_scope_connects_and_disposes_owned_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hungry_crab.fetch.acquisition import AcquisitionBudget

    monkeypatch.setattr("hungry_crab.fetch.acquisition.os.path.ismount", lambda _: True)
    monkeypatch.setattr("hungry_crab.fetch.acquisition.volume_capacity", lambda _: 4096)
    quota = AcquisitionBudget(tmp_path, tmp_path / "cache", 10, 4096)
    real_connect = socket.create_connection
    calls: list[tuple[str, int]] = []
    with ExitStack() as cleanup:
        upstream, peer = socket.socketpair()
        cleanup.enter_context(upstream)
        cleanup.enter_context(peer)
        peer.settimeout(3)

        def connect(address: tuple[str, int], *, timeout: float) -> socket.socket:
            calls.append(address)
            return upstream

        monkeypatch.setattr("hungry_crab.fetch.acquisition.socket.create_connection", connect)
        with quota.scope():
            assert quota._server is not None
            client = cleanup.enter_context(real_connect(quota._server.server_address, 3))
            client.sendall(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
            assert client.recv(4096).startswith(b"HTTP/1.1 200")
        assert peer.recv(1) == b""
    assert calls == [("github.com", 443)]
