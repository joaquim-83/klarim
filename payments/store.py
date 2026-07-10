"""Persistência de cobranças.

Backend PostgreSQL (psycopg2, já é dependência) com fallback em memória — assim
funciona em produção (Postgres do compose) e localmente/testes (sem Postgres).
Operações síncronas do psycopg2 rodam em thread para não bloquear o event loop.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .models import Charge, PaymentStatus, RecoveryToken, amount_display


def _utcnow() -> datetime:
    """UTC naive (consistente com o TIMESTAMP sem tz do Postgres)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

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
ALTER TABLE payments ADD COLUMN IF NOT EXISTS email_status VARCHAR(20);

CREATE TABLE IF NOT EXISTS recovery_tokens (
    id SERIAL PRIMARY KEY,
    token VARCHAR(64) UNIQUE NOT NULL,
    buyer_email VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recovery_email ON recovery_tokens (buyer_email);
"""


class MemoryStore:
    """Store em memória (MVP / dev / testes)."""

    backend = "memory"

    def __init__(self) -> None:
        self._d: Dict[str, Charge] = {}
        self._tokens: Dict[str, RecoveryToken] = {}

    async def ensure_schema(self) -> None:
        return None

    # --- recuperação ------------------------------------------------------- #

    async def list_paid_charges_by_email(self, email: str) -> List[Charge]:
        return [c for c in self._d.values() if c.buyer_email == email and c.is_paid]

    async def create_recovery_token(self, token: str, buyer_email: str, expires_at_iso: str) -> None:
        self._tokens[token] = RecoveryToken(
            token=token, buyer_email=buyer_email,
            created_at=_utcnow().isoformat(), expires_at=expires_at_iso,
        )

    async def get_valid_recovery_token(self, token: str) -> Optional[RecoveryToken]:
        t = self._tokens.get(token)
        if not t:
            return None
        try:
            if datetime.fromisoformat(t.expires_at) <= _utcnow():
                return None
        except (ValueError, TypeError):
            return None
        return t

    async def count_recent_recovery_requests(self, email: str) -> int:
        cutoff = _utcnow() - timedelta(hours=1)
        n = 0
        for t in self._tokens.values():
            if t.buyer_email != email or not t.created_at:
                continue
            try:
                if datetime.fromisoformat(t.created_at) > cutoff:
                    n += 1
            except (ValueError, TypeError):
                pass
        return n

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

    async def set_email_status(self, charge_id: str, status: str) -> None:
        c = self._d.get(charge_id)
        if c:
            c.email_status = status

    # --- admin (KL-14) ----------------------------------------------------- #

    async def list_charges(self, status: Optional[str] = None, limit: int = 50,
                           offset: int = 0) -> List[Charge]:
        rows = sorted(self._d.values(), key=lambda c: c.created_at or "", reverse=True)
        if status:
            rows = [c for c in rows if c.status == status]
        return rows[offset:offset + limit]

    async def list_charges_by_url(self, url: str) -> List[Charge]:
        rows = [c for c in self._d.values() if c.target_url == url]
        return sorted(rows, key=lambda c: c.created_at or "", reverse=True)

    async def payment_stats(self) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        revenue = 0
        real = [c for c in self._d.values() if not c.charge_id.startswith("demo_")]
        for c in real:  # cobranças demo não entram nas métricas (Fix pós-KL-27)
            by_status[c.status] = by_status.get(c.status, 0) + 1
            if c.is_paid:
                revenue += c.amount_cents
        return {"total": len(real), "by_status": by_status,
                "revenue_cents": revenue, "revenue_display": amount_display(revenue),
                "paid_count": by_status.get(PaymentStatus.PAID, 0)}


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
                    INSERT INTO payments
                        (charge_id, target_url, amount_cents, status, buyer_email, email_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (charge_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        buyer_email = COALESCE(EXCLUDED.buyer_email, payments.buyer_email)
                    """,
                    (charge.charge_id, charge.target_url, charge.amount_cents,
                     charge.status, charge.buyer_email, charge.email_status),
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
                    "buyer_email, report_email_sent, email_status FROM payments WHERE charge_id = %s",
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
            email_status=row[8],
        )

    async def set_email_status(self, charge_id: str, status: str) -> None:
        await asyncio.to_thread(self._set_email_status_sync, charge_id, status)

    def _set_email_status_sync(self, charge_id: str, status: str) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE payments SET email_status = %s WHERE charge_id = %s",
                    (status, charge_id),
                )
        finally:
            conn.close()

    # --- recuperação ------------------------------------------------------- #

    async def list_paid_charges_by_email(self, email: str) -> List[Charge]:
        return await asyncio.to_thread(self._list_paid_by_email_sync, email)

    def _list_paid_by_email_sync(self, email: str) -> List[Charge]:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT charge_id, target_url, amount_cents, status, created_at, paid_at, "
                    "buyer_email, report_email_sent, email_status FROM payments "
                    "WHERE buyer_email = %s AND status = 'PAID' ORDER BY paid_at DESC NULLS LAST",
                    (email,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Charge(
                charge_id=r[0], target_url=r[1], amount_cents=r[2], status=r[3],
                created_at=str(r[4]) if r[4] else None,
                paid_at=str(r[5]) if r[5] else None,
                buyer_email=r[6], report_email_sent=bool(r[7]), email_status=r[8],
            )
            for r in rows
        ]

    # --- admin (KL-14) ----------------------------------------------------- #

    async def list_charges(self, status: Optional[str] = None, limit: int = 50,
                           offset: int = 0) -> List[Charge]:
        return await asyncio.to_thread(self._list_charges_sync, status, limit, offset)

    def _list_charges_sync(self, status: Optional[str], limit: int, offset: int,
                           url: Optional[str] = None) -> List[Charge]:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                conds, params = [], []
                if status:
                    conds.append("status = %s")
                    params.append(status)
                if url:
                    conds.append("target_url = %s")
                    params.append(url)
                where = ("WHERE " + " AND ".join(conds)) if conds else ""
                params.extend([limit, offset])
                cur.execute(
                    "SELECT charge_id, target_url, amount_cents, status, created_at, paid_at, "
                    f"buyer_email, report_email_sent, email_status FROM payments {where} "
                    "ORDER BY created_at DESC NULLS LAST LIMIT %s OFFSET %s",
                    params,
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            Charge(
                charge_id=r[0], target_url=r[1], amount_cents=r[2], status=r[3],
                created_at=str(r[4]) if r[4] else None,
                paid_at=str(r[5]) if r[5] else None,
                buyer_email=r[6], report_email_sent=bool(r[7]), email_status=r[8],
            )
            for r in rows
        ]

    async def list_charges_by_url(self, url: str) -> List[Charge]:
        return await asyncio.to_thread(self._list_charges_sync, None, 100, 0, url)

    async def payment_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._payment_stats_sync)

    def _payment_stats_sync(self) -> Dict[str, Any]:
        conn = self._connect()
        # Cobranças demo (charge_id 'demo_...') não entram nas métricas (Fix pós-KL-27).
        demo = "charge_id NOT LIKE 'demo\\_%'"
        try:
            with conn, conn.cursor() as cur:
                cur.execute(f"SELECT status, COUNT(*) FROM payments WHERE {demo} GROUP BY status")
                by_status = {r[0]: int(r[1]) for r in cur.fetchall()}
                cur.execute(f"SELECT COALESCE(SUM(amount_cents), 0) FROM payments "
                            f"WHERE status = 'PAID' AND {demo}")
                revenue = int(cur.fetchone()[0])
                cur.execute(f"SELECT COUNT(*) FROM payments WHERE {demo}")
                total = int(cur.fetchone()[0])
        finally:
            conn.close()
        return {"total": total, "by_status": by_status,
                "revenue_cents": revenue, "revenue_display": amount_display(revenue),
                "paid_count": by_status.get(PaymentStatus.PAID, 0)}

    async def create_recovery_token(self, token: str, buyer_email: str, expires_at_iso: str) -> None:
        await asyncio.to_thread(self._create_token_sync, token, buyer_email, expires_at_iso)

    def _create_token_sync(self, token: str, buyer_email: str, expires_at_iso: str) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO recovery_tokens (token, buyer_email, expires_at) "
                    "VALUES (%s, %s, %s)",
                    (token, buyer_email, expires_at_iso),
                )
        finally:
            conn.close()

    async def get_valid_recovery_token(self, token: str) -> Optional[RecoveryToken]:
        return await asyncio.to_thread(self._get_valid_token_sync, token)

    def _get_valid_token_sync(self, token: str) -> Optional[RecoveryToken]:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT token, buyer_email, created_at, expires_at, used_at "
                    "FROM recovery_tokens WHERE token = %s AND expires_at > NOW()",
                    (token,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return RecoveryToken(
            token=row[0], buyer_email=row[1],
            created_at=str(row[2]) if row[2] else None,
            expires_at=str(row[3]) if row[3] else None,
            used_at=str(row[4]) if row[4] else None,
        )

    async def count_recent_recovery_requests(self, email: str) -> int:
        return await asyncio.to_thread(self._count_tokens_sync, email)

    def _count_tokens_sync(self, email: str) -> int:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM recovery_tokens "
                    "WHERE buyer_email = %s AND created_at > NOW() - INTERVAL '1 hour'",
                    (email,),
                )
                return int(cur.fetchone()[0])
        finally:
            conn.close()

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
