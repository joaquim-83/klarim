"""Persistência de alvos (targets) e scans do Discovery Worker (PostgreSQL)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    domain VARCHAR(255) NOT NULL,
    platform VARCHAR(50) DEFAULT 'unknown',
    sector VARCHAR(50) DEFAULT 'outro',
    price_tier VARCHAR(20) DEFAULT 'standard',
    contact_email VARCHAR(255),
    contact_source VARCHAR(20) DEFAULT 'scrape',
    status VARCHAR(20) DEFAULT 'discovered',
    last_scan_id INTEGER,
    last_scan_score INTEGER,
    last_scan_at TIMESTAMP,
    last_alert_at TIMESTAMP,
    alert_count INTEGER DEFAULT 0,
    discovered_at TIMESTAMP DEFAULT NOW(),
    source VARCHAR(30) DEFAULT 'ct_log',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_status ON targets(status);
CREATE INDEX IF NOT EXISTS idx_targets_domain ON targets(domain);
CREATE INDEX IF NOT EXISTS idx_targets_platform ON targets(platform);

CREATE TABLE IF NOT EXISTS scans (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES targets(id),
    url TEXT NOT NULL,
    score INTEGER,
    semaphore VARCHAR(10),
    pass_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    inconclusive_count INTEGER DEFAULT 0,
    checks_json JSONB,
    scanned_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scans_target ON scans(target_id);
CREATE INDEX IF NOT EXISTS idx_scans_date ON scans(scanned_at);

CREATE TABLE IF NOT EXISTS alert_log (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES targets(id),
    contact_email VARCHAR(255) NOT NULL,
    score INTEGER,
    semaphore VARCHAR(10),
    fail_count INTEGER,
    email_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'sent',
    sent_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alert_log_target ON alert_log(target_id);
CREATE INDEX IF NOT EXISTS idx_alert_log_date ON alert_log(sent_at);

CREATE TABLE IF NOT EXISTS rescan_log (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES targets(id),
    old_score INTEGER,
    new_score INTEGER,
    evolution VARCHAR(20),
    old_semaphore VARCHAR(10),
    new_semaphore VARCHAR(10),
    email_id VARCHAR(100),
    rescanned_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rescan_log_target ON rescan_log(target_id);
CREATE INDEX IF NOT EXISTS idx_rescan_log_date ON rescan_log(rescanned_at);
"""


class TargetStore:
    def _connect(self):
        import psycopg2

        host = os.environ.get("POSTGRES_HOST")
        if host:
            return psycopg2.connect(
                host=host,
                port=os.environ.get("POSTGRES_PORT", "5432"),
                user=os.environ.get("POSTGRES_USER"),
                password=os.environ.get("POSTGRES_PASSWORD"),
                dbname=os.environ.get("POSTGRES_DB"),
            )
        return psycopg2.connect(os.environ["DATABASE_URL"])

    async def ensure_schema(self) -> None:
        await asyncio.to_thread(self._run, lambda cur: cur.execute(_SCHEMA))

    # --- helper de execução ------------------------------------------------ #

    def _run(self, fn):
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                return fn(cur)
        finally:
            conn.close()

    @staticmethod
    def _rows_to_dicts(cur) -> List[Dict[str, Any]]:
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # --- targets ----------------------------------------------------------- #

    async def register_target(
        self, url: str, domain: str, platform: str, sector: str, price_tier: str,
        contact_email: Optional[str], source: str = "ct_log", status: str = "discovered",
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO targets (url, domain, platform, sector, price_tier,
                                     contact_email, status, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                    platform = EXCLUDED.platform,
                    sector = EXCLUDED.sector,
                    price_tier = EXCLUDED.price_tier,
                    contact_email = COALESCE(EXCLUDED.contact_email, targets.contact_email)
                RETURNING id
                """,
                (url, domain, platform, sector, price_tier, contact_email, status, source),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def domain_exists(self, domain: str) -> bool:
        def _fn(cur):
            cur.execute("SELECT 1 FROM targets WHERE domain = %s LIMIT 1", (domain,))
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def get_target_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM targets WHERE domain = %s LIMIT 1", (domain,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_target(self, target_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM targets WHERE id = %s", (target_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def update_status(self, target_id: int, status: str) -> None:
        await asyncio.to_thread(
            self._run, lambda cur: cur.execute(
                "UPDATE targets SET status = %s WHERE id = %s", (status, target_id))
        )

    async def update_scan_result(self, target_id: int, scan_id: int, score: int) -> None:
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET last_scan_id = %s, last_scan_score = %s, "
                "last_scan_at = NOW(), status = 'scanned' WHERE id = %s",
                (scan_id, score, target_id),
            )

        await asyncio.to_thread(self._run, _fn)

    async def list_targets(
        self, status: Optional[str] = None, platform: Optional[str] = None,
        sector: Optional[str] = None, limit: int = 50, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        def _fn(cur):
            where, params = [], []
            for col, val in (("status", status), ("platform", platform), ("sector", sector)):
                if val:
                    where.append(f"{col} = %s")
                    params.append(val)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            cur.execute(
                f"SELECT * FROM targets {clause} ORDER BY discovered_at DESC LIMIT %s OFFSET %s",
                params,
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def count_targets(self, status: Optional[str] = None) -> int:
        def _fn(cur):
            if status:
                cur.execute("SELECT COUNT(*) FROM targets WHERE status = %s", (status,))
            else:
                cur.execute("SELECT COUNT(*) FROM targets")
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def stats(self) -> Dict[str, Any]:
        def _fn(cur):
            out: Dict[str, Any] = {}
            for key, col in (("by_status", "status"), ("by_platform", "platform"), ("by_sector", "sector")):
                cur.execute(f"SELECT {col}, COUNT(*) FROM targets GROUP BY {col} ORDER BY COUNT(*) DESC")
                out[key] = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM targets")
            out["total"] = int(cur.fetchone()[0])
            return out

        return await asyncio.to_thread(self._run, _fn)

    async def get_targets_for_scan(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(
                "SELECT * FROM targets WHERE status = 'discovered' "
                "OR (last_scan_at IS NOT NULL AND last_scan_at < NOW() - INTERVAL '30 days') "
                "ORDER BY discovered_at ASC LIMIT %s",
                (limit,),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    # --- scans ------------------------------------------------------------- #

    async def save_scan(
        self, target_id: Optional[int], url: str, score: int, semaphore: str,
        pass_count: int, fail_count: int, inconclusive_count: int, checks_json: dict,
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO scans (target_id, url, score, semaphore, pass_count,
                                   fail_count, inconclusive_count, checks_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (target_id, url, score, semaphore, pass_count, fail_count,
                 inconclusive_count, json.dumps(checks_json)),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def list_scans(
        self, target_id: Optional[int] = None, score_min: Optional[int] = None,
        score_max: Optional[int] = None, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        def _fn(cur):
            where, params = [], []
            if target_id is not None:
                where.append("target_id = %s")
                params.append(target_id)
            if score_min is not None:
                where.append("score >= %s")
                params.append(score_min)
            if score_max is not None:
                where.append("score <= %s")
                params.append(score_max)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.append(limit)
            cur.execute(
                f"SELECT id, target_id, url, score, semaphore, pass_count, fail_count, "
                f"inconclusive_count, scanned_at FROM scans {clause} "
                f"ORDER BY scanned_at DESC LIMIT %s",
                params,
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_scan(self, scan_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM scans WHERE id = %s", (scan_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    # --- alertas ----------------------------------------------------------- #

    async def get_eligible_targets_for_alert(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alvos escaneados, com FALHAS, com e-mail, sem alerta nos últimos 30d."""
        def _fn(cur):
            cur.execute(
                """
                SELECT t.*, s.fail_count AS scan_fail_count, s.semaphore AS scan_semaphore,
                       s.checks_json AS scan_checks
                FROM targets t
                JOIN scans s ON t.last_scan_id = s.id
                WHERE t.status = 'scanned'
                  AND t.contact_email IS NOT NULL
                  AND s.fail_count > 0
                  AND (t.last_alert_at IS NULL OR t.last_alert_at < NOW() - INTERVAL '30 days')
                ORDER BY t.last_scan_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def mark_target_alerted(self, target_id: int) -> None:
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET status = 'alerted', last_alert_at = NOW(), "
                "alert_count = COALESCE(alert_count, 0) + 1 WHERE id = %s",
                (target_id,),
            )

        await asyncio.to_thread(self._run, _fn)

    async def mark_target_contacted(self, target_id: int) -> None:
        """Só toca last_alert_at (KL-13): após um e-mail de evolução, evita que o
        Alert Worker contate o mesmo alvo dentro da janela de 30 dias."""
        await asyncio.to_thread(
            self._run, lambda cur: cur.execute(
                "UPDATE targets SET last_alert_at = NOW() WHERE id = %s", (target_id,))
        )

    async def mark_unsubscribed(self, email: str) -> int:
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET status = 'unsubscribed' WHERE contact_email = %s",
                (email,),
            )
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def log_alert(
        self, target_id: int, contact_email: str, score: Optional[int],
        semaphore: Optional[str], fail_count: Optional[int], email_id: Optional[str],
        status: str = "sent",
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO alert_log (target_id, contact_email, score, semaphore,
                                       fail_count, email_id, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (target_id, contact_email, score, semaphore, fail_count, email_id, status),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def count_alerts_last_hours(self, hours: int) -> int:
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM alert_log WHERE status = 'sent' "
                "AND sent_at > NOW() - (%s || ' hours')::interval",
                (str(hours),),
            )
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def list_alerts(
        self, target_id: Optional[int] = None, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        def _fn(cur):
            if target_id is not None:
                cur.execute(
                    "SELECT * FROM alert_log WHERE target_id = %s "
                    "ORDER BY sent_at DESC LIMIT %s OFFSET %s",
                    (target_id, limit, offset),
                )
            else:
                cur.execute(
                    "SELECT * FROM alert_log ORDER BY sent_at DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def alert_stats(self) -> Dict[str, Any]:
        def _fn(cur):
            out = {}
            for key, interval in (("today", "1 day"), ("week", "7 days"), ("month", "30 days")):
                cur.execute(
                    f"SELECT COUNT(*) FROM alert_log WHERE status = 'sent' "
                    f"AND sent_at > NOW() - INTERVAL '{interval}'"
                )
                out[key] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM alert_log WHERE status = 'sent'")
            out["total"] = int(cur.fetchone()[0])
            return out

        return await asyncio.to_thread(self._run, _fn)

    async def count_proactive_emails_last_hours(self, hours: int) -> int:
        """Throttle GLOBAL: alertas (alert_log) + e-mails de evolução (rescan_log).

        Ambos são e-mails proativos e disputam o mesmo teto de reputação do domínio.
        """
        def _fn(cur):
            cur.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM alert_log WHERE status = 'sent' "
                "  AND sent_at > NOW() - (%s || ' hours')::interval) + "
                "(SELECT COUNT(*) FROM rescan_log WHERE email_id IS NOT NULL "
                "  AND rescanned_at > NOW() - (%s || ' hours')::interval)",
                (str(hours), str(hours)),
            )
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    # --- re-scan (KL-13) --------------------------------------------------- #

    async def get_targets_for_rescan(self, days: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
        """Alvos já engajados (scanned/alerted), com e-mail, escaneados há > N dias."""
        def _fn(cur):
            cur.execute(
                """
                SELECT t.*, s.semaphore AS old_semaphore, s.fail_count AS old_fail_count
                FROM targets t
                LEFT JOIN scans s ON t.last_scan_id = s.id
                WHERE t.status IN ('scanned', 'alerted')
                  AND t.contact_email IS NOT NULL
                  AND t.last_scan_at IS NOT NULL
                  AND t.last_scan_at < NOW() - (%s || ' days')::interval
                ORDER BY t.last_scan_at ASC
                LIMIT %s
                """,
                (str(days), limit),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def log_rescan(
        self, target_id: int, old_score: Optional[int], new_score: Optional[int],
        evolution: str, old_semaphore: Optional[str], new_semaphore: Optional[str],
        email_id: Optional[str] = None,
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO rescan_log (target_id, old_score, new_score, evolution,
                                        old_semaphore, new_semaphore, email_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (target_id, old_score, new_score, evolution, old_semaphore,
                 new_semaphore, email_id),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def update_rescan_email(self, rescan_id: int, email_id: str) -> None:
        await asyncio.to_thread(
            self._run, lambda cur: cur.execute(
                "UPDATE rescan_log SET email_id = %s WHERE id = %s", (email_id, rescan_id))
        )

    async def get_pending_evolution_emails(self, days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
        """Re-scans recentes cujo e-mail de evolução ficou pendente (throttle no ciclo anterior).

        Traz o que o e-mail precisa para reenvio: url, e-mail, tier, e o fail_count/
        checks_json do último scan (para a contagem por severidade).
        """
        def _fn(cur):
            cur.execute(
                """
                SELECT r.id AS rescan_id, r.target_id, r.old_score, r.new_score,
                       r.evolution, r.new_semaphore,
                       t.url, t.contact_email, t.price_tier,
                       s.fail_count, s.checks_json
                FROM rescan_log r
                JOIN targets t ON r.target_id = t.id
                LEFT JOIN scans s ON t.last_scan_id = s.id
                WHERE r.email_id IS NULL
                  AND t.status != 'unsubscribed'
                  AND t.contact_email IS NOT NULL
                  AND r.rescanned_at > NOW() - (%s || ' days')::interval
                ORDER BY r.rescanned_at ASC
                LIMIT %s
                """,
                (str(days), limit),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_rescans(
        self, target_id: Optional[int] = None, evolution: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        def _fn(cur):
            where, params = [], []
            if target_id is not None:
                where.append("target_id = %s")
                params.append(target_id)
            if evolution:
                where.append("evolution = %s")
                params.append(evolution)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            cur.execute(
                f"SELECT * FROM rescan_log {clause} "
                f"ORDER BY rescanned_at DESC LIMIT %s OFFSET %s",
                params,
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def rescan_stats(self) -> Dict[str, Any]:
        def _fn(cur):
            cur.execute("SELECT evolution, COUNT(*) FROM rescan_log GROUP BY evolution")
            by_evolution = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM rescan_log")
            total = int(cur.fetchone()[0])
            return {"by_evolution": by_evolution, "total": total}

        return await asyncio.to_thread(self._run, _fn)


_store: Optional[TargetStore] = None


def get_target_store() -> TargetStore:
    global _store
    if _store is None:
        _store = TargetStore()
    return _store
