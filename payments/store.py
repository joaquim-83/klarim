"""Persistência de cobranças.

Backend PostgreSQL (psycopg2, já é dependência) com fallback em memória — assim
funciona em produção (Postgres do compose) e localmente/testes (sem Postgres).
Operações síncronas do psycopg2 rodam em thread para não bloquear o event loop.
"""

from __future__ import annotations

import asyncio
import os
from typing import Dict, Optional

from .models import Charge, PaymentStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    charge_id VARCHAR(100) UNIQUE NOT NULL,
    target_url TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS buyer_email TEXT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS report_email_sent BOOLEAN DEFAULT FALSE;
"""


class MemoryStore:
    """Store em memória (MVP / dev / testes)."""

    backend = "memory"

    def __init__(self) -> None:
        self._d: Dict[str, Charge] = {}

    async def ensure_schema(self) -> None:
        return None

    async def save(self, charge: Charge) -> None:
        self._d[charge.charge_id] = charge

    async def get(self, charge_id: str) -> Optional[Charge]:
        return self._d.get(charge_id)

    async def mark_status(self, charge_id: str, status: str, paid_at: Optional[str] = None) -> None:
        c = self._d.get(charge_id)
        if c:
            c.status = status
            if paid_at:
                c.paid_at = paid_at

    async def mark_email_sent(self, charge_id: str) -> None:
        c = self._d.get(charge_id)
        if c:
            c.report_email_sent = True


class PostgresStore:
    """Store em PostgreSQL via psycopg2 (executado em thread)."""

    backend = "postgres"

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn

    def _connect(self):
        import psycopg2  # import tardio: só necessário no backend Postgres

        # Preferir os POSTGRES_* individuais: a senha pode conter '/'/'+' (base64),
        # o que quebra o parsing de uma DATABASE_URL. Conectar por parâmetros
        # separados é imune a caracteres especiais na senha.
        host = os.environ.get("POSTGRES_HOST")
        if host:
            return psycopg2.connect(
                host=host,
                port=os.environ.get("POSTGRES_PORT", "5432"),
                user=os.environ.get("POSTGRES_USER"),
                password=os.environ.get("POSTGRES_PASSWORD"),
                dbname=os.environ.get("POSTGRES_DB"),
            )
        return psycopg2.connect(self._dsn)

    async def ensure_schema(self) -> None:
        await asyncio.to_thread(self._ensure_schema_sync)

    def _ensure_schema_sync(self) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(_SCHEMA)
        finally:
            conn.close()

    async def save(self, charge: Charge) -> None:
        await asyncio.to_thread(self._save_sync, charge)

    def _save_sync(self, charge: Charge) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO payments (charge_id, target_url, amount_cents, status, buyer_email)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (charge_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        buyer_email = COALESCE(EXCLUDED.buyer_email, payments.buyer_email)
                    """,
                    (charge.charge_id, charge.target_url, charge.amount_cents,
                     charge.status, charge.buyer_email),
                )
        finally:
            conn.close()

    async def get(self, charge_id: str) -> Optional[Charge]:
        return await asyncio.to_thread(self._get_sync, charge_id)

    def _get_sync(self, charge_id: str) -> Optional[Charge]:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT charge_id, target_url, amount_cents, status, created_at, paid_at, "
                    "buyer_email, report_email_sent FROM payments WHERE charge_id = %s",
                    (charge_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return Charge(
            charge_id=row[0],
            target_url=row[1],
            amount_cents=row[2],
            status=row[3],
            created_at=str(row[4]) if row[4] else None,
            paid_at=str(row[5]) if row[5] else None,
            buyer_email=row[6],
            report_email_sent=bool(row[7]),
        )

    async def mark_email_sent(self, charge_id: str) -> None:
        await asyncio.to_thread(self._mark_email_sent_sync, charge_id)

    def _mark_email_sent_sync(self, charge_id: str) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE payments SET report_email_sent = TRUE WHERE charge_id = %s",
                    (charge_id,),
                )
        finally:
            conn.close()

    async def mark_status(self, charge_id: str, status: str, paid_at: Optional[str] = None) -> None:
        await asyncio.to_thread(self._mark_status_sync, charge_id, status, paid_at)

    def _mark_status_sync(self, charge_id: str, status: str, paid_at: Optional[str]) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                if status in PaymentStatus.PAID_STATES:
                    cur.execute(
                        "UPDATE payments SET status = %s, paid_at = COALESCE(paid_at, NOW()) "
                        "WHERE charge_id = %s",
                        (status, charge_id),
                    )
                else:
                    cur.execute(
                        "UPDATE payments SET status = %s WHERE charge_id = %s",
                        (status, charge_id),
                    )
        finally:
            conn.close()


# Singleton + init com fallback.
_store = None


def get_store():
    global _store
    if _store is None:
        dsn = os.environ.get("DATABASE_URL")
        has_pg = bool(os.environ.get("POSTGRES_HOST") or dsn)
        _store = PostgresStore(dsn) if has_pg else MemoryStore()
    return _store


async def init_store():
    """Chamado no startup da API. Garante o schema; cai para memória se falhar."""
    global _store
    store = get_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001 - degrada para memória, não derruba a API
        print(f"[payments] Postgres indisponível ({exc!r}); usando store em memória.")
        _store = MemoryStore()
    return _store
