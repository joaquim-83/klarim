"""Persistência de alvos (targets) e scans do Discovery Worker (PostgreSQL)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

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
-- Confiança da classificação de setor (refino do KL-11): 0.0–1.0. Permite
-- filtrar "classificação incerta" (< 0.5) para revisão manual no painel.
ALTER TABLE targets ADD COLUMN IF NOT EXISTS classification_confidence REAL DEFAULT 0.0;
-- Origem da classificação: auto (classificador) | domain (reclassify-domains) |
-- manual (operador corrigiu no painel). Manual nunca é sobrescrito pelo automático.
ALTER TABLE targets ADD COLUMN IF NOT EXISTS classification_source VARCHAR(20) DEFAULT 'auto';

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
-- Origem do scan (KL-17): public | discovery | admin | manual | rescan.
ALTER TABLE scans ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'discovery';

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

CREATE TABLE IF NOT EXISTS site_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    session_id VARCHAR(64),
    target_url TEXT,
    target_id INTEGER,
    page_url TEXT,
    referrer TEXT,
    utm_source VARCHAR(100),
    utm_medium VARCHAR(100),
    utm_campaign VARCHAR(100),
    utm_content VARCHAR(200),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_type ON site_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_session ON site_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_date ON site_events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_target ON site_events(target_id);
CREATE INDEX IF NOT EXISTS idx_events_utm ON site_events(utm_source, utm_campaign);

-- Blocklist de e-mails que bouncaram/denunciaram (KL-24): nunca reenviar. O
-- domínio fica guardado para análise, mas o bloqueio é por e-mail (não descarta
-- endereços irmãos válidos do mesmo domínio por engano).
CREATE TABLE IF NOT EXISTS email_blocklist (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    domain VARCHAR(255),
    reason VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_blocklist_email ON email_blocklist(email);
CREATE INDEX IF NOT EXISTS idx_blocklist_domain ON email_blocklist(domain);

-- Quem pediu o scan público (KL-25): liga o scan ao lead.
ALTER TABLE scans ADD COLUMN IF NOT EXISTS scanned_by_email VARCHAR(255);

-- Verificação de e-mail por código de 6 dígitos antes do scan público (KL-25).
CREATE TABLE IF NOT EXISTS scan_verifications (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code VARCHAR(6) NOT NULL,
    url TEXT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    ip_address VARCHAR(45)
);
CREATE INDEX IF NOT EXISTS idx_sv_email ON scan_verifications(email);
CREATE INDEX IF NOT EXISTS idx_sv_code ON scan_verifications(email, code);

-- Crédito de scan gratuito por e-mail (KL-25): 1 scan grátis por e-mail.
-- KL-27: rescan_credits = re-verificações gratuitas (1 por compra, "retorno médico").
CREATE TABLE IF NOT EXISTS scan_credits (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    free_scans_used INTEGER DEFAULT 0,
    first_scan_url TEXT,
    first_scan_at TIMESTAMP,
    rescan_credits INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sc_email ON scan_credits(email);
ALTER TABLE scan_credits ADD COLUMN IF NOT EXISTS rescan_credits INTEGER DEFAULT 0;
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

    async def ping(self) -> bool:
        """SELECT 1 — health check do PostgreSQL (KL-16)."""
        await asyncio.to_thread(self._run, lambda cur: cur.execute("SELECT 1"))
        return True

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
        confidence: float = 0.0, classification_source: str = "auto",
    ) -> int:
        def _fn(cur):
            # No conflito, a classificação MANUAL é preservada (o automático nunca
            # sobrescreve setor/tier/confiança de um alvo corrigido pelo operador).
            cur.execute(
                """
                INSERT INTO targets (url, domain, platform, sector, price_tier,
                                     contact_email, status, source,
                                     classification_confidence, classification_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                    platform = EXCLUDED.platform,
                    sector = CASE WHEN targets.classification_source = 'manual'
                                  THEN targets.sector ELSE EXCLUDED.sector END,
                    price_tier = CASE WHEN targets.classification_source = 'manual'
                                      THEN targets.price_tier ELSE EXCLUDED.price_tier END,
                    classification_confidence = CASE WHEN targets.classification_source = 'manual'
                                      THEN targets.classification_confidence
                                      ELSE EXCLUDED.classification_confidence END,
                    classification_source = CASE WHEN targets.classification_source = 'manual'
                                      THEN 'manual' ELSE EXCLUDED.classification_source END,
                    contact_email = COALESCE(EXCLUDED.contact_email, targets.contact_email)
                RETURNING id
                """,
                (url, domain, platform, sector, price_tier, contact_email, status,
                 source, confidence, classification_source),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def all_targets_for_reclassify(self) -> List[Dict[str, Any]]:
        """Alvos reclassificáveis (todos menos 'descartado') — id, url, setor +
        origem da classificação (o chamador pula os 'manual')."""
        def _fn(cur):
            cur.execute(
                "SELECT id, url, sector, price_tier, classification_source FROM targets "
                "WHERE status != 'descartado' ORDER BY id"
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def update_classification(
        self, target_id: int, sector: str, price_tier: str, confidence: float,
        classification_source: str = "auto",
    ) -> None:
        # Guarda extra: nunca mexe num alvo classificado manualmente.
        await asyncio.to_thread(
            self._run, lambda cur: cur.execute(
                "UPDATE targets SET sector = %s, price_tier = %s, "
                "classification_confidence = %s, classification_source = %s "
                "WHERE id = %s AND classification_source IS DISTINCT FROM 'manual'",
                (sector, price_tier, confidence, classification_source, target_id))
        )

    async def bulk_update_classification(self, updates: List[tuple]) -> None:
        """Atualiza setor/tier/confiança em lote (uma conexão, source='domain').
        updates: (sector, tier, confidence, target_id). Pula alvos manuais."""
        if not updates:
            return

        def _fn(cur):
            cur.executemany(
                "UPDATE targets SET sector = %s, price_tier = %s, "
                "classification_confidence = %s, classification_source = 'domain' "
                "WHERE id = %s AND classification_source IS DISTINCT FROM 'manual'",
                updates,
            )

        await asyncio.to_thread(self._run, _fn)

    async def manual_classify(
        self, target_id: int, sector: str, price_tier: str
    ) -> Optional[Dict[str, Any]]:
        """Classifica manualmente (operador): source='manual', confiança=1.0.
        Retorna o alvo atualizado (ou None se não existir)."""
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET sector = %s, price_tier = %s, "
                "classification_confidence = 1.0, classification_source = 'manual' "
                "WHERE id = %s RETURNING *",
                (sector, price_tier, target_id),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def manual_classify_batch(
        self, target_ids: List[int], sector: str, price_tier: str
    ) -> int:
        """Classificação manual em massa. Retorna quantos alvos foram atualizados."""
        if not target_ids:
            return 0

        def _fn(cur):
            cur.execute(
                "UPDATE targets SET sector = %s, price_tier = %s, "
                "classification_confidence = 1.0, classification_source = 'manual' "
                "WHERE id = ANY(%s)",
                (sector, price_tier, list(target_ids)),
            )
            return cur.rowcount

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

    async def get_target_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM targets WHERE url = %s LIMIT 1", (url,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def map_urls_to_target_ids(self, urls: List[str]) -> Dict[str, int]:
        """{url: target_id} para as URLs dadas (KL-17: vincular pagamentos a alvos)."""
        if not urls:
            return {}

        def _fn(cur):
            cur.execute("SELECT url, id FROM targets WHERE url = ANY(%s)", (list(set(urls)),))
            return {r[0]: r[1] for r in cur.fetchall()}

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

    async def update_target_status(self, target_id: int, status: str) -> Optional[Dict[str, Any]]:
        """Atualiza o status de um alvo (edição manual no painel). Retorna o alvo
        atualizado (ou None se não existir)."""
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET status = %s WHERE id = %s RETURNING *",
                (status, target_id),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def update_target_email(self, target_id: int, email: str) -> Optional[Dict[str, Any]]:
        """Atualiza o contact_email de um alvo. Se o alvo estava 'sem_contato' e
        agora ganhou e-mail, volta para 'discovered' (pode ser escaneado/alertado).
        Retorna o alvo atualizado (ou None se não existir)."""
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET contact_email = %s, "
                "status = CASE WHEN status = 'sem_contato' THEN 'discovered' ELSE status END "
                "WHERE id = %s RETURNING *",
                (email, target_id),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def list_target_emails(self) -> List[Dict[str, Any]]:
        """(id, contact_email) de todos os alvos com e-mail — p/ limpeza em massa."""
        def _fn(cur):
            cur.execute("SELECT id, contact_email FROM targets WHERE contact_email IS NOT NULL")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_targets(
        self, status: Optional[str] = None, platform: Optional[str] = None,
        sector: Optional[str] = None, source: Optional[str] = None,
        limit: int = 50, offset: int = 0, low_confidence: bool = False,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        def _fn(cur):
            where, params = [], []
            for col, val in (("status", status), ("platform", platform),
                             ("sector", sector), ("source", source)):
                if val:
                    where.append(f"t.{col} = %s")
                    params.append(val)
            if low_confidence:  # revisão manual: classificação incerta (< 0.5)
                where.append("t.classification_confidence < 0.5")
            if search and search.strip():
                # Busca case-insensitive + parcial em url, domínio e e-mail.
                like = f"%{search.strip().lower()}%"
                where.append("(LOWER(t.url) LIKE %s OR LOWER(t.domain) LIKE %s "
                             "OR LOWER(COALESCE(t.contact_email, '')) LIKE %s)")
                params.extend([like, like, like])
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            # JOIN traz o semáforo do último scan (KL-14: lista de alvos no painel).
            cur.execute(
                f"SELECT t.*, s.semaphore AS last_semaphore FROM targets t "
                f"LEFT JOIN scans s ON t.last_scan_id = s.id {clause} "
                f"ORDER BY t.discovered_at DESC LIMIT %s OFFSET %s",
                params,
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def count_discovered_today(self) -> int:
        """Alvos registrados desde 00:00 UTC de hoje (KL-15 — dashboard operacional)."""
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM targets WHERE discovered_at >= date_trunc('day', NOW())")
            return int(cur.fetchone()[0])

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
        source: str = "discovery", scanned_by_email: Optional[str] = None,
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO scans (target_id, url, score, semaphore, pass_count,
                                   fail_count, inconclusive_count, checks_json, source,
                                   scanned_by_email)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (target_id, url, score, semaphore, pass_count, fail_count,
                 inconclusive_count, json.dumps(checks_json), source, scanned_by_email),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    # --- verificação de e-mail + crédito de scan público (KL-25) ----------- #

    async def create_scan_verification(
        self, email: str, code: str, url: str, ttl_minutes: int = 10,
        ip_address: Optional[str] = None,
    ) -> None:
        """Grava um código de verificação (TTL 10min) e limpa os expirados do e-mail."""
        def _fn(cur):
            cur.execute("DELETE FROM scan_verifications WHERE expires_at < NOW()")
            cur.execute(
                "INSERT INTO scan_verifications (email, code, url, expires_at, ip_address) "
                "VALUES (%s, %s, %s, NOW() + (%s || ' minutes')::interval, %s)",
                (email, code, url, str(ttl_minutes), ip_address),
            )

        await asyncio.to_thread(self._run, _fn)

    async def count_verifications_since(
        self, email: Optional[str] = None, ip: Optional[str] = None, hours: int = 1
    ) -> int:
        """Códigos enviados por e-mail OU por IP na última janela (rate limit)."""
        def _fn(cur):
            if email is not None:
                cur.execute(
                    "SELECT COUNT(*) FROM scan_verifications WHERE email = %s "
                    "AND created_at > NOW() - (%s || ' hours')::interval", (email, str(hours)))
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM scan_verifications WHERE ip_address = %s "
                    "AND created_at > NOW() - (%s || ' hours')::interval", (ip, str(hours)))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def verify_scan_code(self, email: str, code: str, url: str) -> bool:
        """Marca a verificação válida (não usada, não expirada) como verified.
        Retorna True se casou; False se código inválido/expirado."""
        def _fn(cur):
            cur.execute(
                "UPDATE scan_verifications SET verified = TRUE "
                "WHERE id = (SELECT id FROM scan_verifications "
                "  WHERE email = %s AND code = %s AND url = %s "
                "    AND verified = FALSE AND expires_at > NOW() "
                "  ORDER BY created_at DESC LIMIT 1) RETURNING id",
                (email, code, url),
            )
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def get_scan_credit(self, email: str) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM scan_credits WHERE email = %s", (email,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def record_free_scan(self, email: str, url: str) -> None:
        """Consome o scan gratuito do e-mail (idempotente por e-mail — o 1º grava)."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO scan_credits (email, free_scans_used, first_scan_url, first_scan_at) "
                "VALUES (%s, 1, %s, NOW()) ON CONFLICT (email) DO NOTHING",
                (email, url),
            )

        await asyncio.to_thread(self._run, _fn)

    async def grant_rescan_credit(self, email: str, amount: int = 1) -> None:
        """Concede N re-verificações gratuitas ao e-mail (KL-27, 1 por compra).

        Cria a linha de crédito se não existir (comprador pode nunca ter feito o
        scan gratuito). Somamos o crédito — comprador recorrente ganha mais de um.
        """
        email = (email or "").strip().lower()
        if not email:
            return

        def _fn(cur):
            cur.execute(
                "INSERT INTO scan_credits (email, rescan_credits) VALUES (%s, %s) "
                "ON CONFLICT (email) DO UPDATE "
                "SET rescan_credits = scan_credits.rescan_credits + EXCLUDED.rescan_credits",
                (email, amount),
            )

        await asyncio.to_thread(self._run, _fn)

    async def consume_rescan_credit(self, email: str) -> bool:
        """Consome 1 re-verificação. Retorna True se havia crédito (decrementou)."""
        email = (email or "").strip().lower()
        if not email:
            return False

        def _fn(cur):
            cur.execute(
                "UPDATE scan_credits SET rescan_credits = rescan_credits - 1 "
                "WHERE email = %s AND rescan_credits > 0",
                (email,),
            )
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def public_scan_stats(self) -> Dict[str, Any]:
        """Métricas do funil de verificação pública (KL-25) para o dashboard."""
        def _fn(cur):
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE verified) FROM scan_verifications")
            codes_sent, verified = cur.fetchone()
            cur.execute("SELECT COUNT(DISTINCT email) FROM scan_verifications")
            emails = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM scan_credits")
            credits = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM scans WHERE scanned_by_email IS NOT NULL")
            public_scans = int(cur.fetchone()[0])
            return {"codes_sent": int(codes_sent or 0), "verified": int(verified or 0),
                    "distinct_emails": emails, "free_scans_used": credits,
                    "public_scans": public_scans}

        return await asyncio.to_thread(self._run, _fn)

    async def list_scans(
        self, target_id: Optional[int] = None, score_min: Optional[int] = None,
        score_max: Optional[int] = None, source: Optional[str] = None, limit: int = 50,
        distinct_url: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lista scans (mais recentes primeiro). ``distinct_url=True`` retorna apenas
        o scan MAIS RECENTE de cada URL — evita 3 linhas do mesmo site na "atividade
        recente" quando ele foi escaneado várias vezes (Fix pós-KL-27)."""
        cols = ("id, target_id, url, score, semaphore, pass_count, fail_count, "
                "inconclusive_count, source, scanned_at")

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
            if source:
                where.append("source = %s")
                params.append(source)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.append(limit)
            if distinct_url:
                # DISTINCT ON (url) pega o último por URL; reordena por data e limita.
                cur.execute(
                    f"SELECT * FROM (SELECT DISTINCT ON (url) {cols} FROM scans {clause} "
                    f"ORDER BY url, scanned_at DESC) t ORDER BY scanned_at DESC LIMIT %s",
                    params,
                )
            else:
                cur.execute(
                    f"SELECT {cols} FROM scans {clause} ORDER BY scanned_at DESC LIMIT %s",
                    params,
                )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    # --- dashboard admin (KL-14) ------------------------------------------- #

    async def scan_stats(self) -> Dict[str, Any]:
        """Média de score e distribuição por semáforo (todos os scans)."""
        def _fn(cur):
            cur.execute("SELECT COUNT(*), COALESCE(ROUND(AVG(score)), 0) FROM scans")
            total, avg = cur.fetchone()
            cur.execute("SELECT semaphore, COUNT(*) FROM scans GROUP BY semaphore")
            by_semaphore = {r[0]: int(r[1]) for r in cur.fetchall()}
            return {"total": int(total), "avg_score": int(avg), "by_semaphore": by_semaphore}

        return await asyncio.to_thread(self._run, _fn)

    # --- métricas operacionais (KL-16) ------------------------------------- #

    async def scan_today_stats(self) -> Dict[str, Any]:
        """Scans completados hoje + score médio de hoje."""
        def _fn(cur):
            cur.execute("SELECT COUNT(*), COALESCE(ROUND(AVG(score)), 0) FROM scans "
                        "WHERE scanned_at >= date_trunc('day', NOW())")
            c, avg = cur.fetchone()
            return {"count": int(c), "avg_score": int(avg)}

        return await asyncio.to_thread(self._run, _fn)

    async def count_rescan_eligible(self, days: int = 30) -> int:
        """Alvos com last_scan > N dias (próximos elegíveis a re-scan)."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM targets WHERE status IN ('scanned','alerted') "
                "AND contact_email IS NOT NULL AND last_scan_at IS NOT NULL "
                "AND last_scan_at < NOW() - (%s || ' days')::interval",
                (str(days),),
            )
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def email_metrics(self) -> Dict[str, int]:
        """E-mails proativos (alertas + evolução) enviados hoje/semana/mês."""
        def _fn(cur):
            out: Dict[str, int] = {}
            for key, interval in (("sent_today", "1 day"), ("sent_week", "7 days"),
                                  ("sent_month", "30 days")):
                cur.execute(
                    f"SELECT (SELECT COUNT(*) FROM alert_log WHERE status='sent' "
                    f"  AND sent_at > NOW() - INTERVAL '{interval}') + "
                    f"(SELECT COUNT(*) FROM rescan_log WHERE email_id IS NOT NULL "
                    f"  AND rescanned_at > NOW() - INTERVAL '{interval}')"
                )
                out[key] = int(cur.fetchone()[0])
            return out

        return await asyncio.to_thread(self._run, _fn)

    async def scans_daily(self, days: int = 30) -> List[Dict[str, Any]]:
        return await self._daily_counts("scans", "scanned_at", days)

    async def alerts_daily(self, days: int = 30) -> List[Dict[str, Any]]:
        return await self._daily_counts("alert_log", "sent_at", days, extra="status = 'sent'")

    async def _daily_counts(self, table: str, ts_col: str, days: int,
                            extra: Optional[str] = None) -> List[Dict[str, Any]]:
        """Série diária (últimos N dias) — [{day: 'YYYY-MM-DD', count: int}]."""
        def _fn(cur):
            where = f"{ts_col} > NOW() - (%s || ' days')::interval"
            if extra:
                where += f" AND {extra}"
            cur.execute(
                f"SELECT to_char(date_trunc('day', {ts_col}), 'YYYY-MM-DD') AS day, "
                f"COUNT(*) FROM {table} WHERE {where} GROUP BY day ORDER BY day",
                (str(days),),
            )
            return [{"day": r[0], "count": int(r[1])} for r in cur.fetchall()]

        return await asyncio.to_thread(self._run, _fn)

    async def get_scan(self, scan_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM scans WHERE id = %s", (scan_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_recent_scan_checks(self, url: str, max_age_minutes: int = 60) -> Optional[dict]:
        """checks_json do scan mais recente (< N min) para a URL, ou None.

        Deixa o PDF/summary pelo link do e-mail carregar do banco em vez de
        reescanear (~30s). Casa URL de forma tolerante a caixa e '/' final.
        """
        def _fn(cur):
            cur.execute(
                "SELECT checks_json FROM scans "
                "WHERE lower(rtrim(url, '/')) = lower(rtrim(%s, '/')) "
                "  AND checks_json IS NOT NULL "
                "  AND scanned_at > NOW() - (%s || ' minutes')::interval "
                "ORDER BY scanned_at DESC LIMIT 1",
                (url, str(max_age_minutes)),
            )
            row = cur.fetchone()
            return row[0] if row else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_last_scan_score(self, url: str) -> Optional[int]:
        """Score do scan mais recente da URL (qualquer idade), ou None. Usado na
        comparação antes/depois do re-scan (KL-27)."""
        def _fn(cur):
            cur.execute(
                "SELECT score FROM scans "
                "WHERE lower(rtrim(url, '/')) = lower(rtrim(%s, '/')) "
                "  AND score IS NOT NULL "
                "ORDER BY scanned_at DESC LIMIT 1",
                (url,),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else None

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

    async def count_eligible_targets_for_alert(self) -> int:
        """Backlog total de alvos elegíveis a alerta (mesma regra do get_, sem limit)."""
        def _fn(cur):
            cur.execute(
                """
                SELECT COUNT(*)
                FROM targets t
                JOIN scans s ON t.last_scan_id = s.id
                WHERE t.status = 'scanned'
                  AND t.contact_email IS NOT NULL
                  AND s.fail_count > 0
                  AND (t.last_alert_at IS NULL OR t.last_alert_at < NOW() - INTERVAL '30 days')
                """
            )
            return int(cur.fetchone()[0])

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

    # --- bounce handling / blocklist (KL-24) ------------------------------- #

    async def discard_target_by_email(self, email: str, reason: str = "bounced") -> int:
        """Marca como 'descartado' todos os alvos com esse e-mail (sai dos ciclos)."""
        def _fn(cur):
            cur.execute(
                "UPDATE targets SET status = 'descartado', "
                "notes = COALESCE(notes || ' | ', '') || %s "
                "WHERE contact_email = %s AND status != 'descartado'",
                (reason, email),
            )
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def block_email(self, email: str, reason: str = "bounced") -> None:
        """Adiciona o e-mail à blocklist (idempotente). Guarda o domínio p/ análise."""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return
        domain = email.rsplit("@", 1)[1]

        def _fn(cur):
            cur.execute(
                "INSERT INTO email_blocklist (email, domain, reason) VALUES (%s, %s, %s) "
                "ON CONFLICT (email) DO NOTHING",
                (email, domain, reason),
            )

        await asyncio.to_thread(self._run, _fn)

    async def is_email_blocked(self, email: str) -> bool:
        email = (email or "").strip().lower()
        if not email:
            return False

        def _fn(cur):
            cur.execute("SELECT 1 FROM email_blocklist WHERE email = %s LIMIT 1", (email,))
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def blocklist_size(self) -> int:
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM email_blocklist")
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def mark_alert_status_by_email_id(self, email_id: str, status: str) -> int:
        """Atualiza o status dos envios com esse email_id (ex.: 'bounced'/'complained')."""
        def _fn(cur):
            cur.execute("UPDATE alert_log SET status = %s WHERE email_id = %s",
                        (status, email_id))
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def get_sent_alerts_for_bounce_check(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """email_id + contact_email distintos dos alertas enviados (p/ checar no Resend)."""
        def _fn(cur):
            cur.execute(
                "SELECT DISTINCT ON (email_id) email_id, contact_email "
                "FROM alert_log WHERE email_id IS NOT NULL AND status = 'sent' "
                "ORDER BY email_id, sent_at DESC LIMIT %s",
                (limit,),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def email_health(self) -> Dict[str, Any]:
        """Métricas de bounce (KL-24) a partir do `alert_log` + tamanho da blocklist.

        `bounced`/`complained` refletem o que o webhook/backfill do Resend marcou.
        `total` = tentativas (sent + bounced + complained).
        """
        def _fn(cur):
            cur.execute(
                "SELECT "
                "COUNT(*) FILTER (WHERE status IN ('sent','bounced','complained')), "
                "COUNT(*) FILTER (WHERE status = 'bounced'), "
                "COUNT(*) FILTER (WHERE status = 'complained') "
                "FROM alert_log"
            )
            total, bounced, complained = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM email_blocklist")
            blocklist = int(cur.fetchone()[0])
            return {"total": int(total or 0), "bounced": int(bounced or 0),
                    "complained": int(complained or 0), "blocklist": blocklist}

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
            base = ("SELECT a.*, t.url FROM alert_log a "
                    "LEFT JOIN targets t ON a.target_id = t.id ")
            if target_id is not None:
                cur.execute(
                    base + "WHERE a.target_id = %s ORDER BY a.sent_at DESC LIMIT %s OFFSET %s",
                    (target_id, limit, offset),
                )
            else:
                cur.execute(
                    base + "ORDER BY a.sent_at DESC LIMIT %s OFFSET %s",
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

    async def count_proactive_emails_this_month(self) -> int:
        """Cota mensal GLOBAL (KL-23): alertas (alert_log) + evolução (rescan_log)
        enviados no mês corrente (calendário). Substitui o antigo throttle horário/
        diário — com o Resend Pro (50k/mês) o único teto é a cota mensal.
        """
        def _fn(cur):
            cur.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM alert_log WHERE status = 'sent' "
                "  AND sent_at >= date_trunc('month', NOW())) + "
                "(SELECT COUNT(*) FROM rescan_log WHERE email_id IS NOT NULL "
                "  AND rescanned_at >= date_trunc('month', NOW()))"
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
                where.append("r.target_id = %s")
                params.append(target_id)
            if evolution:
                where.append("r.evolution = %s")
                params.append(evolution)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            cur.execute(
                f"SELECT r.*, t.url FROM rescan_log r "
                f"LEFT JOIN targets t ON r.target_id = t.id {clause} "
                f"ORDER BY r.rescanned_at DESC LIMIT %s OFFSET %s",
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
            cur.execute("SELECT COUNT(*) FROM rescan_log WHERE rescanned_at >= date_trunc('day', NOW())")
            today = int(cur.fetchone()[0])
            return {"by_evolution": by_evolution, "total": total, "today": today}

        return await asyncio.to_thread(self._run, _fn)

    # --- tracking da jornada do lead (KL-21) ------------------------------- #

    async def log_event(
        self, event_type: str, session_id: Optional[str], target_url: Optional[str] = None,
        target_id: Optional[int] = None, page_url: Optional[str] = None,
        referrer: Optional[str] = None, utm_source: Optional[str] = None,
        utm_medium: Optional[str] = None, utm_campaign: Optional[str] = None,
        utm_content: Optional[str] = None, metadata: Optional[dict] = None,
    ) -> int:
        def _fn(cur):
            cur.execute(
                """
                INSERT INTO site_events (event_type, session_id, target_url, target_id,
                    page_url, referrer, utm_source, utm_medium, utm_campaign, utm_content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (event_type, session_id, target_url, target_id, page_url, referrer,
                 utm_source, utm_medium, utm_campaign, utm_content, json.dumps(metadata or {})),
            )
            return cur.fetchone()[0]

        return await asyncio.to_thread(self._run, _fn)

    async def count_events_last_minute(self, session_id: str) -> int:
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM site_events WHERE session_id = %s "
                "AND created_at > NOW() - INTERVAL '1 minute'",
                (session_id,),
            )
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def analytics_funnel(self, period: str = "7d") -> Dict[str, int]:
        since = _period_since(period)
        alert_since = _period_since(period, "sent_at")

        def _fn(cur):
            def distinct(where):
                cur.execute(f"SELECT COUNT(DISTINCT session_id) FROM site_events "
                            f"WHERE session_id IS NOT NULL AND {since} AND ({where})")
                return int(cur.fetchone()[0])

            cur.execute(f"SELECT COUNT(*) FROM alert_log WHERE status='sent' AND {alert_since}")
            emails_sent = int(cur.fetchone()[0])
            return {
                "emails_sent": emails_sent,
                "links_clicked": distinct("utm_medium = 'email'"),
                "results_viewed": distinct("event_type = 'result_viewed'"),
                "cta_clicked": distinct("event_type = 'cta_clicked'"),
                "payments_created": distinct("event_type = 'payment_created'"),
                "payments_completed": distinct("event_type = 'payment_completed'"),
                "reports_downloaded": distinct("event_type = 'report_downloaded'"),
            }

        return await asyncio.to_thread(self._run, _fn)

    async def analytics_abandoned(self, period: str = "7d", limit: int = 50) -> List[Dict[str, Any]]:
        since = _period_since(period)

        def _fn(cur):
            cur.execute(
                f"""
                SELECT DISTINCT ON (se.session_id) se.session_id, se.target_url,
                       se.metadata->>'amount' AS amount, se.created_at,
                       (SELECT EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at)))
                          FROM site_events e WHERE e.session_id = se.session_id) AS duration_seconds
                FROM site_events se
                WHERE se.event_type = 'payment_created' AND {since}
                  AND se.session_id NOT IN (
                    SELECT session_id FROM site_events
                    WHERE event_type = 'payment_completed' AND session_id IS NOT NULL)
                ORDER BY se.session_id, se.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def analytics_campaigns(self, period: str = "7d") -> List[Dict[str, Any]]:
        since = _period_since(period)

        def _fn(cur):
            cur.execute(
                f"""
                SELECT utm_campaign,
                    COUNT(DISTINCT session_id) AS clicks,
                    COUNT(DISTINCT session_id) FILTER (WHERE event_type='result_viewed') AS scans,
                    COUNT(DISTINCT session_id) FILTER (WHERE event_type='cta_clicked') AS ctas,
                    COUNT(DISTINCT session_id) FILTER (WHERE event_type='payment_completed') AS payments
                FROM site_events
                WHERE utm_campaign IS NOT NULL AND {since}
                GROUP BY utm_campaign ORDER BY clicks DESC
                """
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def analytics_pages(self, period: str = "7d", limit: int = 10) -> List[Dict[str, Any]]:
        since = _period_since(period)

        def _fn(cur):
            # Agrupa por page_url cru no banco (poucas linhas) e reúne as sessões
            # distintas; a fusão por página "limpa" (sem UTM, url= decodificado)
            # acontece em Python para não splittar a mesma página por UTM (KL-21).
            cur.execute(
                f"""
                SELECT page_url, COUNT(*) AS views,
                       array_agg(DISTINCT session_id) AS sessions
                FROM site_events WHERE event_type='page_view' AND {since}
                GROUP BY page_url
                """
            )
            merged: Dict[str, Dict[str, Any]] = {}
            for r in self._rows_to_dicts(cur):
                key = _clean_page_key(r["page_url"])
                agg = merged.setdefault(key, {"page_url": key, "views": 0, "_sessions": set()})
                agg["views"] += int(r["views"] or 0)
                for s in (r["sessions"] or []):
                    if s:
                        agg["_sessions"].add(s)
            rows = [
                {"page_url": a["page_url"], "views": a["views"], "sessions": len(a["_sessions"])}
                for a in merged.values()
            ]
            rows.sort(key=lambda x: x["views"], reverse=True)
            return rows[:limit]

        return await asyncio.to_thread(self._run, _fn)

    async def analytics_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(
                "SELECT event_type, session_id, target_url, page_url, utm_campaign, "
                "metadata, created_at FROM site_events ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)


# Períodos aceitos pelos endpoints de analytics (KL-21). Valores são constantes
# (nunca vêm do usuário direto) — seguro interpolar no SQL.
_PERIOD_BOUNDS = {
    "today": "date_trunc('day', NOW())",
    "7d": "NOW() - INTERVAL '7 days'",
    "30d": "NOW() - INTERVAL '30 days'",
}


def _period_since(period: str, col: str = "created_at") -> str:
    bound = _PERIOD_BOUNDS.get(period)
    return f"{col} >= {bound}" if bound else "TRUE"  # 'total'/desconhecido → sem filtro


_UTM_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def _clean_page_key(raw: Optional[str]) -> str:
    """Normaliza um page_url para agrupamento/exibição (KL-21).

    Remove os parâmetros UTM e, quando há ``?url=<alvo>``, troca a query pela
    forma legível ``<path> → <hostname do alvo>`` (ex.: ``/result → iclinic.com.br``).
    Espelha ``cleanPageUrl`` do frontend. Retorna só o path quando não há query útil.
    """
    if not raw:
        return raw or "/"
    try:
        base = raw if raw.startswith("http") else "https://klarim.net" + (raw if raw.startswith("/") else "/" + raw)
        parts = urlsplit(base)
        path = parts.path or "/"
        params = parse_qsl(parts.query, keep_blank_values=True)
        target = next((v for k, v in params if k == "url"), None)
        if target:
            host = urlsplit(target if target.startswith("http") else "https://" + target).hostname
            return f"{path} → {host}" if host else path
        rest = [k for k, _ in params if k not in _UTM_KEYS]
        return f"{path}?{'&'.join(rest)}" if rest else path
    except Exception:
        return raw


_store: Optional[TargetStore] = None


def get_target_store() -> TargetStore:
    global _store
    if _store is None:
        _store = TargetStore()
    return _store
