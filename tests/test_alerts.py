from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from olibuguard.alerts.sink import AlertSink, NullAlertSink
from olibuguard.alerts.telegram import TelegramAlertSink
from olibuguard.failsafe import ErrorBudget

# ── NullAlertSink ────────────────────────────────────────────────────────────


def test_null_sink_satisfies_protocol() -> None:
    assert isinstance(NullAlertSink(), AlertSink)


def test_null_sink_send_does_not_raise() -> None:
    NullAlertSink().send("anything")  # must not raise


# ── TelegramAlertSink ────────────────────────────────────────────────────────


def _fake_urlopen(calls: list[dict[str, object]]) -> object:
    """Return a fake urlopen that records calls."""

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        calls.append(
            {
                "url": req.full_url,
                "data": json.loads(req.data),  # type: ignore[arg-type]
                "timeout": timeout,
            }
        )
        return FakeResponse()

    return urlopen


def test_telegram_satisfies_protocol() -> None:
    sink = TelegramAlertSink(token="tok", chat_id="123")
    assert isinstance(sink, AlertSink)


def test_telegram_sends_correct_payload() -> None:
    calls: list[dict[str, object]] = []
    with patch("urllib.request.urlopen", _fake_urlopen(calls)):
        TelegramAlertSink(token="mytoken", chat_id="42").send("hello world")

    assert len(calls) == 1
    data = calls[0]["data"]
    assert data == {"chat_id": "42", "text": "hello world"}
    assert "mytoken" in str(calls[0]["url"])


def test_telegram_uses_configured_timeout() -> None:
    calls: list[dict[str, object]] = []
    with patch("urllib.request.urlopen", _fake_urlopen(calls)):
        TelegramAlertSink(token="t", chat_id="1", timeout=9.5).send("msg")

    assert calls[0]["timeout"] == 9.5


def test_telegram_raises_on_network_error() -> None:
    def boom(req: object, timeout: float) -> None:
        raise OSError("network unreachable")

    sink = TelegramAlertSink(token="t", chat_id="1")
    with patch("urllib.request.urlopen", boom), pytest.raises(OSError):
        sink.send("msg")  # caller (run_safe) must catch this


# ── ErrorBudget.on_exhausted ─────────────────────────────────────────────────


def test_on_exhausted_not_called_before_threshold(tmp_path: Path) -> None:
    calls: list[int] = []
    budget = ErrorBudget("op", max_consecutive=3, on_exhausted=lambda: calls.append(1))
    budget.record_error(RuntimeError("e1"))
    budget.record_error(RuntimeError("e2"))
    assert calls == []


def test_on_exhausted_called_at_threshold(tmp_path: Path) -> None:
    calls: list[int] = []
    budget = ErrorBudget("op", max_consecutive=2, on_exhausted=lambda: calls.append(1))
    budget.record_error(RuntimeError("e1"))
    budget.record_error(RuntimeError("e2"))
    assert calls == [1]


def test_on_exhausted_called_exactly_once(tmp_path: Path) -> None:
    calls: list[int] = []
    budget = ErrorBudget("op", max_consecutive=1, on_exhausted=lambda: calls.append(1))
    budget.record_error(RuntimeError("e1"))
    budget.record_error(RuntimeError("e2"))  # after threshold — must NOT call again
    budget.record_error(RuntimeError("e3"))
    assert calls == [1]


def test_on_exhausted_with_kill_switch(tmp_path: Path) -> None:
    from olibuguard.kill_switch import KillSwitch

    ks = KillSwitch(tmp_path / "KS")
    calls: list[str] = []
    budget = ErrorBudget(
        "op",
        max_consecutive=1,
        kill_switch=ks,
        on_exhausted=lambda: calls.append("alerted"),
    )
    budget.record_error(RuntimeError("boom"))
    assert ks.is_active()
    assert calls == ["alerted"]  # both kill switch and callback fired
