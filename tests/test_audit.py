from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from olibuguard.audit.records import DecisionAudit, EquityPoint
from olibuguard.audit.sink import AuditReader, AuditSink, NullAuditSink
from olibuguard.audit.version import code_version


def _decision() -> DecisionAudit:
    return DecisionAudit(
        at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        symbol="BTC/USDT",
        kind="stake",
        reference_price=Decimal("100"),
        equity_quote=Decimal("1000"),
        approved=True,
        reason="approved",
        quote_amount=Decimal("20"),
        code_version="abc123",
    )


def test_null_sink_satisfies_protocol() -> None:
    assert isinstance(NullAuditSink(), AuditSink)


def test_null_sink_is_noop() -> None:
    sink = NullAuditSink()
    sink.record_decision(_decision())  # must not raise
    point = EquityPoint(at=datetime(2025, 1, 1, tzinfo=UTC), equity_quote=Decimal("1000"))
    sink.record_equity(point)  # must not raise


def test_code_version_returns_nonempty_string() -> None:
    version = code_version()
    assert isinstance(version, str)
    assert version != ""


def test_sqlite_sink_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    from olibuguard.audit.sqlite import SQLiteAuditSink

    sink = SQLiteAuditSink(tmp_path / "audit.sqlite")
    sink.record_decision(_decision())
    sink.record_equity(
        EquityPoint(at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC), equity_quote=Decimal("1000.5"))
    )

    decisions = sink.decisions()
    assert len(decisions) == 1
    assert decisions[0].symbol == "BTC/USDT"
    assert decisions[0].reason == "approved"
    assert decisions[0].quote_amount == Decimal("20")
    assert decisions[0].at == datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    points = sink.equity_points()
    assert len(points) == 1
    assert points[0].equity_quote == Decimal("1000.5")

    # AuditReader reconciliation helpers
    assert isinstance(sink, AuditReader)
    assert sink.peak_equity_quote() == Decimal("1000.5")
    last = sink.last_equity_point()
    assert last is not None
    assert last.equity_quote == Decimal("1000.5")


def test_sqlite_reader_empty_db(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    from olibuguard.audit.sqlite import SQLiteAuditSink

    sink = SQLiteAuditSink(tmp_path / "empty.sqlite")
    assert sink.peak_equity_quote() == Decimal("0")
    assert sink.last_equity_point() is None


def test_peak_equity_uses_numeric_not_lexicographic_max(tmp_path: Path) -> None:
    """Regression: equity_quote is stored as TEXT; func.max must CAST to numeric.

    Lexicographic ordering would rank "99.0" > "495.0" (because '9' > '4'),
    returning the wrong peak.  The fix CASTs to FLOAT before MAX so the result
    matches numeric ordering.
    """
    pytest.importorskip("sqlalchemy")
    from olibuguard.audit.sqlite import SQLiteAuditSink

    sink = SQLiteAuditSink(tmp_path / "peak.sqlite")
    for amount in ("99.0", "495.0", "100.0"):
        sink.record_equity(
            EquityPoint(at=datetime(2025, 1, 1, tzinfo=UTC), equity_quote=Decimal(amount))
        )
    assert sink.peak_equity_quote() == Decimal("495.0")


def test_null_sink_does_not_satisfy_audit_reader() -> None:
    assert not isinstance(NullAuditSink(), AuditReader)
