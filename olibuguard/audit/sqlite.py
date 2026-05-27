"""Audit sink backed by a dedicated SQLite DB (separate from Freqtrade's).

Requires the ``db`` extra (SQLAlchemy). Money and timestamps are stored as TEXT
(Decimal string / ISO-8601) to avoid float precision and timezone loss. The v1
schema is created with ``create_all``; alembic migrations come in before the
first schema change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from olibuguard.audit.records import DecisionAudit, EquityPoint


class _Base(DeclarativeBase):
    pass


class _AuditLogRow(_Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[str]
    symbol: Mapped[str]
    kind: Mapped[str]
    reference_price: Mapped[str]
    equity_quote: Mapped[str]
    approved: Mapped[bool]
    reason: Mapped[str]
    quote_amount: Mapped[str | None]
    code_version: Mapped[str]


class _EquityRow(_Base):
    __tablename__ = "equity_curve"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[str]
    equity_quote: Mapped[str]


class SQLiteAuditSink:
    def __init__(self, db_path: Path) -> None:
        self._engine = create_engine(f"sqlite:///{db_path}")
        _Base.metadata.create_all(self._engine)

    def record_decision(self, audit: DecisionAudit) -> None:
        row = _AuditLogRow(
            at=audit.at.isoformat(),
            symbol=audit.symbol,
            kind=audit.kind,
            reference_price=str(audit.reference_price),
            equity_quote=str(audit.equity_quote),
            approved=audit.approved,
            reason=audit.reason,
            quote_amount=None if audit.quote_amount is None else str(audit.quote_amount),
            code_version=audit.code_version,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def record_equity(self, point: EquityPoint) -> None:
        row = _EquityRow(at=point.at.isoformat(), equity_quote=str(point.equity_quote))
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def decisions(self) -> list[DecisionAudit]:
        with Session(self._engine) as session:
            rows = session.execute(select(_AuditLogRow)).scalars().all()
            return [
                DecisionAudit(
                    at=datetime.fromisoformat(row.at),
                    symbol=row.symbol,
                    kind=row.kind,
                    reference_price=Decimal(row.reference_price),
                    equity_quote=Decimal(row.equity_quote),
                    approved=row.approved,
                    reason=row.reason,
                    quote_amount=None if row.quote_amount is None else Decimal(row.quote_amount),
                    code_version=row.code_version,
                )
                for row in rows
            ]

    def peak_equity_quote(self) -> Decimal:
        """Return the maximum equity_quote ever recorded, or zero if no data."""
        with Session(self._engine) as session:
            result = session.execute(
                select(func.max(_EquityRow.equity_quote))
            ).scalar_one_or_none()
            return Decimal("0") if result is None else Decimal(result)

    def last_equity_point(self) -> EquityPoint | None:
        """Return the most recently recorded equity point, or None if no data."""
        with Session(self._engine) as session:
            row = session.execute(
                select(_EquityRow).order_by(_EquityRow.id.desc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return EquityPoint(
                at=datetime.fromisoformat(row.at),
                equity_quote=Decimal(row.equity_quote),
            )

    def equity_points(self) -> list[EquityPoint]:
        with Session(self._engine) as session:
            rows = session.execute(select(_EquityRow)).scalars().all()
            return [
                EquityPoint(
                    at=datetime.fromisoformat(row.at),
                    equity_quote=Decimal(row.equity_quote),
                )
                for row in rows
            ]
