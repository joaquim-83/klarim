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
-- KL-31: bônus de scan completo gratuito para sites com score 100 (email+URL).
ALTER TABLE scan_credits ADD COLUMN IF NOT EXISTS full_scan_credits INTEGER DEFAULT 0;
ALTER TABLE scan_credits ADD COLUMN IF NOT EXISTS full_scan_url TEXT;

-- Sites monitorados (KL-29): score 100 → selo público + re-scan semanal.
-- status: pending → active → suspended → active/removed.
CREATE TABLE IF NOT EXISTS monitored_sites (
    id SERIAL PRIMARY KEY,
    target_id INTEGER REFERENCES targets(id),
    domain VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    display_name VARCHAR(255),
    logo_url TEXT,
    contact_email VARCHAR(255) NOT NULL,
    approval_token VARCHAR(64) UNIQUE,
    approved BOOLEAN DEFAULT FALSE,
    approved_at TIMESTAMP,
    last_check_score INTEGER,
    last_check_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    suspended_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ms_domain_uniq ON monitored_sites(domain);
CREATE INDEX IF NOT EXISTS idx_ms_status ON monitored_sites(status);

-- Perfil comercial (KL-50): dados de negócio extraídos do HTML (não afeta o score).
-- Usa SERIAL/INTEGER (o schema não usa UUID); 1 perfil por target (UNIQUE).
CREATE TABLE IF NOT EXISTS site_profile (
    id SERIAL PRIMARY KEY,
    target_id INTEGER UNIQUE REFERENCES targets(id) ON DELETE CASCADE,
    company_name TEXT, phone TEXT, whatsapp TEXT, address TEXT, cnpj VARCHAR(20),
    commercial_email TEXT, business_hours TEXT, description TEXT, logo_url TEXT,
    instagram TEXT, facebook TEXT, linkedin TEXT, youtube TEXT, tiktok TEXT,
    google_maps_url TEXT, has_blog BOOLEAN DEFAULT FALSE, has_app BOOLEAN DEFAULT FALSE,
    technologies JSONB DEFAULT '{}',
    email_provider TEXT, hosting_provider TEXT, cdn TEXT, dns_provider TEXT,
    certificate_authority TEXT, maturity_score SMALLINT,
    extracted_at TIMESTAMP DEFAULT NOW(), extraction_sources TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_site_profile_cnpj ON site_profile(cnpj) WHERE cnpj IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_site_profile_maturity ON site_profile(maturity_score);

-- KL-55: descrição natural + tags de negócio no perfil (gerados pela IA).
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS business_type TEXT;

-- Classificação multi-setor via CNAE (KL-55): N classificações por alvo, cada uma
-- de uma fonte (receita/ai/manual/schema_org). CNAE = referência estrutural do IBGE.
CREATE TABLE IF NOT EXISTS target_classifications (
    id SERIAL PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    cnae_code TEXT NOT NULL,
    cnae_description TEXT,
    cnae_section TEXT,
    cnae_division TEXT,
    confidence REAL DEFAULT 0.0,
    source TEXT NOT NULL,            -- 'receita' | 'ai' | 'manual' | 'schema_org'
    rank INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (target_id, cnae_code)
);
CREATE INDEX IF NOT EXISTS idx_tc_target ON target_classifications(target_id);
CREATE INDEX IF NOT EXISTS idx_tc_cnae ON target_classifications(cnae_code);
CREATE INDEX IF NOT EXISTS idx_tc_division ON target_classifications(cnae_division);
CREATE INDEX IF NOT EXISTS idx_tc_section ON target_classifications(cnae_section);

-- Contas de usuário (KL-51 f3). Separadas do operador/admin (que é único, via
-- ADMIN_USER/ADMIN_PASSWORD). Senha com bcrypt; JWT de usuário no cookie.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT,
    plan TEXT NOT NULL DEFAULT 'free',        -- 'free' | 'basic' | 'enterprise'
    max_sites INTEGER NOT NULL DEFAULT 1,     -- 1 no free, 5 no basic
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Vínculo usuário ↔ target (site monitorado). is_owner = reivindicou propriedade.
CREATE TABLE IF NOT EXISTS user_sites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    is_owner BOOLEAN DEFAULT FALSE,
    UNIQUE(user_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_us_user ON user_sites(user_id);
CREATE INDEX IF NOT EXISTS idx_us_target ON user_sites(target_id);

-- Recuperação de senha: código 6 dígitos, TTL curto, rate-limited (KL-51 f3).
CREATE TABLE IF NOT EXISTS password_resets (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pwreset_email ON password_resets(email);
"""

# --------------------------------------------------------------------------- #
# Seleção de alvos que precisam de enriquecimento (perfil + IA) — usado por
# scripts/enrich_all.py. Três grupos disjuntos, do mais para o menos prioritário:
#   G1 sem perfil · G2 com perfil e classificação por REGEX · G3 com perfil +
#   setor por IA mas sem descrição. Sempre exclui 'descartado'.
# KL-54: com a expansão de 15 → 48 setores, TODA classificação por regex precisa
# ser revista pela IA — G2 não filtra mais por setor/confiança. Só **preserva** o
# que é 'manual' (operador) ou já é 'ai'.
# --------------------------------------------------------------------------- #

# EXISTS (não JOIN) — target_classifications tem N linhas por alvo; um JOIN
# multiplicaria as linhas do candidato. Só queremos "tem alguma classificação CNAE?".
_HAS_CNAE = "EXISTS (SELECT 1 FROM target_classifications tc WHERE tc.target_id = t.id)"

_ENRICH_G1 = "sp.id IS NULL"
_ENRICH_G2 = ("(sp.id IS NOT NULL AND t.classification_source IS DISTINCT FROM 'ai' "
              "AND t.classification_source IS DISTINCT FROM 'manual')")
_ENRICH_G3 = ("(sp.id IS NOT NULL AND (sp.description IS NULL OR sp.description = '') "
              "AND t.classification_source = 'ai')")
# KL-55 G4: alvo "completo" pelo KL-54 (perfil + classificação IA/manual + descrição)
# mas SEM classificação CNAE. É a reclassificação CNAE de todo o banco.
_ENRICH_G4 = ("(sp.id IS NOT NULL AND t.classification_source IN ('ai', 'manual') "
              "AND sp.description IS NOT NULL AND sp.description <> '' "
              f"AND NOT {_HAS_CNAE})")


def _enrichment_where(mode: str = "all") -> str:
    """Cláusula WHERE (sem a palavra WHERE) para os alvos que precisam de
    enriquecimento. `mode`: 'all' | 'only_ai' (sem G1/crawl) | 'sem_contato'."""
    parts = ["t.status <> 'descartado'"]
    if mode == "sem_contato":
        parts.append("t.status = 'sem_contato'")
    if mode == "only_ai":
        # Só alvos que já têm perfil — pula o crawl (G1 fica de fora).
        parts.append("sp.id IS NOT NULL")
        parts.append(f"({_ENRICH_G2} OR {_ENRICH_G3} OR {_ENRICH_G4})")
    else:
        parts.append(f"({_ENRICH_G1} OR {_ENRICH_G2} OR {_ENRICH_G3} OR {_ENRICH_G4})")
    return " AND ".join(parts)


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

    async def ai_update_classification(
        self, target_id: int, sector: str, price_tier: str, confidence: float,
    ) -> None:
        """Classificação por IA (KL-47A + KL-54) — revê **toda** classificação por regex.

        Com a expansão para 48 setores (KL-54), a IA passa a rever **qualquer**
        classificação feita por regex (auto/domain), independentemente do setor atual
        ou da confiança. Só **preserva** o que é ``manual`` (operador) ou já é ``ai``."""
        await asyncio.to_thread(
            self._run, lambda cur: cur.execute(
                "UPDATE targets SET sector = %s, price_tier = %s, "
                "classification_confidence = %s, classification_source = 'ai' "
                "WHERE id = %s AND classification_source IS DISTINCT FROM 'manual' "
                "AND classification_source IS DISTINCT FROM 'ai'",
                (sector, price_tier, confidence, target_id))
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

    async def grant_full_scan_credit(self, email: str, url: str,
                                     reason: str = "score100_bonus") -> None:
        """Concede 1 scan completo GRATUITO para o par (email, URL) — bônus de score
        100 (KL-31). Não acumula (fixa em 1); troca a URL se o mesmo e-mail ganhar o
        bônus para outro site."""
        email = (email or "").strip().lower()
        if not email or not url:
            return

        def _fn(cur):
            cur.execute(
                "INSERT INTO scan_credits (email, full_scan_credits, full_scan_url) "
                "VALUES (%s, 1, %s) ON CONFLICT (email) DO UPDATE "
                "SET full_scan_credits = 1, full_scan_url = EXCLUDED.full_scan_url",
                (email, url),
            )

        await asyncio.to_thread(self._run, _fn)

    async def consume_full_scan_credit(self, email: str, url: str) -> bool:
        """Consome o bônus de scan completo do par (email, URL). Retorna True se havia
        crédito (casamento de URL tolerante a caixa/'/'). Uso único (KL-31)."""
        email = (email or "").strip().lower()
        if not email or not url:
            return False

        def _fn(cur):
            cur.execute(
                "UPDATE scan_credits SET full_scan_credits = 0 "
                "WHERE email = %s AND full_scan_credits > 0 "
                "  AND lower(rtrim(full_scan_url, '/')) = lower(rtrim(%s, '/'))",
                (email, url),
            )
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    # --- sites monitorados (KL-29) ----------------------------------------- #

    _MS_COLS = ("id, target_id, domain, url, display_name, logo_url, contact_email, "
               "approved, approved_at, last_check_score, last_check_at, status, "
               "suspended_reason, created_at")

    async def upsert_monitoring_offer(
        self, domain: str, url: str, contact_email: str, approval_token: str,
        target_id: Optional[int] = None, score: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Cria (ou reusa) o registro de monitoramento de um domínio, em `pending`.

        Idempotente por domínio: se já existe `active` (ou `suspended`), NÃO cria um
        novo token/downgrade — devolve o registro atual. Só (re)emite token quando o
        registro está ausente ou `pending`/`removed`.
        """
        domain = (domain or "").strip().lower()

        def _fn(cur):
            cur.execute("SELECT * FROM monitored_sites WHERE domain = %s", (domain,))
            existing = self._rows_to_dicts(cur)
            if existing and existing[0]["status"] in ("active", "suspended"):
                return existing[0]  # já monitorado — não reoferece
            cur.execute(
                "INSERT INTO monitored_sites (target_id, domain, url, contact_email, "
                "approval_token, last_check_score, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending') "
                "ON CONFLICT (domain) DO UPDATE SET "
                "  status = 'pending', approval_token = EXCLUDED.approval_token, "
                "  approved = FALSE, url = EXCLUDED.url, "
                "  contact_email = EXCLUDED.contact_email, "
                "  last_check_score = EXCLUDED.last_check_score, "
                "  target_id = COALESCE(EXCLUDED.target_id, monitored_sites.target_id) "
                "RETURNING *",
                (target_id, domain, url, contact_email.strip().lower(),
                 approval_token, score),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_monitored_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM monitored_sites WHERE approval_token = %s", (token,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_monitored_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        domain = (domain or "").strip().lower()

        def _fn(cur):
            cur.execute("SELECT * FROM monitored_sites WHERE domain = %s", (domain,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def approve_monitored_site(
        self, token: str, display_name: Optional[str] = None, logo_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Aprova (uso único do token): marca active + invalida o token."""
        def _fn(cur):
            cur.execute(
                "UPDATE monitored_sites SET approved = TRUE, approved_at = NOW(), "
                "  status = 'active', approval_token = NULL, "
                "  display_name = COALESCE(%s, display_name), "
                "  logo_url = COALESCE(%s, logo_url) "
                "WHERE approval_token = %s AND status IN ('pending', 'removed') "
                "RETURNING *",
                ((display_name or "").strip() or None, logo_url, token),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def remove_monitored_site_by_domain(self, domain: str) -> bool:
        domain = (domain or "").strip().lower()

        def _fn(cur):
            cur.execute("UPDATE monitored_sites SET status = 'removed', approval_token = NULL "
                        "WHERE domain = %s AND status <> 'removed'", (domain,))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def get_active_monitored_sites(self) -> List[Dict[str, Any]]:
        """Sites `active` para a listagem pública (mais recentes primeiro)."""
        def _fn(cur):
            cur.execute(
                f"SELECT {self._MS_COLS} FROM monitored_sites "
                "WHERE status = 'active' ORDER BY approved_at DESC NULLS LAST, id DESC")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_monitored_for_rescan(self) -> List[Dict[str, Any]]:
        """Sites active/suspended (aprovados) para o re-scan de monitoramento."""
        def _fn(cur):
            cur.execute(
                f"SELECT {self._MS_COLS} FROM monitored_sites "
                "WHERE status IN ('active', 'suspended') AND approved = TRUE "
                "ORDER BY last_check_at ASC NULLS FIRST")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def update_monitor_check(self, site_id: int, score: int) -> None:
        def _fn(cur):
            cur.execute("UPDATE monitored_sites SET last_check_score = %s, "
                        "last_check_at = NOW() WHERE id = %s", (score, site_id))

        await asyncio.to_thread(self._run, _fn)

    async def suspend_monitored_site(self, site_id: int, reason: str) -> None:
        def _fn(cur):
            cur.execute("UPDATE monitored_sites SET status = 'suspended', "
                        "suspended_reason = %s WHERE id = %s AND status = 'active'",
                        (reason[:500], site_id))

        await asyncio.to_thread(self._run, _fn)

    async def restore_monitored_site(self, site_id: int) -> bool:
        def _fn(cur):
            cur.execute("UPDATE monitored_sites SET status = 'active', "
                        "suspended_reason = NULL WHERE id = %s AND status = 'suspended'",
                        (site_id,))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def list_monitored_sites(self, status: Optional[str] = None,
                                   limit: int = 100) -> List[Dict[str, Any]]:
        def _fn(cur):
            if status:
                cur.execute(f"SELECT {self._MS_COLS} FROM monitored_sites WHERE status = %s "
                            "ORDER BY id DESC LIMIT %s", (status, limit))
            else:
                cur.execute(f"SELECT {self._MS_COLS} FROM monitored_sites "
                            "ORDER BY id DESC LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_monitored(self, site_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(f"SELECT {self._MS_COLS} FROM monitored_sites WHERE id = %s", (site_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def set_monitored_status(self, site_id: int, status: str,
                                   reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("UPDATE monitored_sites SET status = %s, suspended_reason = %s "
                        "WHERE id = %s RETURNING " + self._MS_COLS,
                        (status, reason, site_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def monitored_stats(self) -> Dict[str, Any]:
        def _fn(cur):
            cur.execute("SELECT status, COUNT(*) FROM monitored_sites GROUP BY status")
            by_status = {r[0]: int(r[1]) for r in cur.fetchall()}
            return {"total": sum(by_status.values()), "by_status": by_status,
                    "active": by_status.get("active", 0),
                    "suspended": by_status.get("suspended", 0),
                    "pending": by_status.get("pending", 0)}

        return await asyncio.to_thread(self._run, _fn)

    async def count_active_monitored(self) -> int:
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM monitored_sites WHERE status = 'active'")
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    # --- perfil comercial (KL-50) ------------------------------------------ #

    _SP_FIELDS = ("company_name", "phone", "whatsapp", "address", "cnpj",
                  "commercial_email", "business_hours", "description", "logo_url",
                  "instagram", "facebook", "linkedin", "youtube", "tiktok",
                  "google_maps_url", "has_blog", "has_app", "email_provider",
                  "hosting_provider", "cdn", "dns_provider", "certificate_authority",
                  "maturity_score", "business_type")  # KL-55: business_type

    async def upsert_site_profile(self, target_id: int, profile: Dict[str, Any]) -> None:
        """Grava (ou atualiza) o perfil comercial de um alvo (1 por target)."""
        fields = list(self._SP_FIELDS)
        vals = [profile.get(f) for f in fields]
        tech = json.dumps(profile.get("technologies") or {})
        sources = list(profile.get("extraction_sources") or [])
        tags = list(profile.get("tags") or [])  # KL-55: TEXT[] (como extraction_sources)

        def _fn(cur):
            cols = ", ".join(fields) + ", technologies, extraction_sources, tags, extracted_at"
            ph = ", ".join(["%s"] * len(fields)) + ", %s, %s, %s, NOW()"
            updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in fields)
            cur.execute(
                f"INSERT INTO site_profile (target_id, {cols}) "
                f"VALUES (%s, {ph}) "
                f"ON CONFLICT (target_id) DO UPDATE SET {updates}, "
                f"  technologies = EXCLUDED.technologies, "
                f"  extraction_sources = EXCLUDED.extraction_sources, "
                f"  tags = EXCLUDED.tags, extracted_at = NOW()",
                [target_id, *vals, tech, sources, tags],
            )

        await asyncio.to_thread(self._run, _fn)

    async def get_site_profile(self, target_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM site_profile WHERE target_id = %s", (target_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    # --- classificação CNAE multi-setor (KL-55) ---------------------------- #

    async def upsert_target_classifications(
        self, target_id: int, classifications: List[Dict[str, Any]]
    ) -> None:
        """Grava N classificações CNAE de um alvo (idempotente por (target,cnae)).

        **Regra inviolável:** uma classificação `source='receita'` (oficial, IBGE via
        Receita) NUNCA é sobrescrita por `ai`/`schema_org` — só por `receita` nova ou
        `manual` (operador). Implementado no WHERE do ON CONFLICT."""
        rows = []
        for c in classifications or []:
            code = (c.get("cnae_code") or "").strip()
            if not code:
                continue
            rows.append((
                target_id, code, c.get("cnae_description"), c.get("cnae_section"),
                c.get("cnae_division"), float(c.get("confidence") or 0.0),
                c.get("source") or "ai", int(c.get("rank") or 1),
            ))
        if not rows:
            return

        def _fn(cur):
            cur.executemany(
                "INSERT INTO target_classifications "
                "(target_id, cnae_code, cnae_description, cnae_section, cnae_division, "
                " confidence, source, rank) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (target_id, cnae_code) DO UPDATE SET "
                "  cnae_description = EXCLUDED.cnae_description, "
                "  cnae_section = EXCLUDED.cnae_section, "
                "  cnae_division = EXCLUDED.cnae_division, "
                "  confidence = EXCLUDED.confidence, source = EXCLUDED.source, "
                "  rank = EXCLUDED.rank "
                "WHERE target_classifications.source IS DISTINCT FROM 'receita' "
                "   OR EXCLUDED.source IN ('receita', 'manual')",
                rows,
            )

        await asyncio.to_thread(self._run, _fn)

    async def get_target_classifications(self, target_id: int) -> List[Dict[str, Any]]:
        """Classificações CNAE de um alvo, ordenadas por rank (mais relevante 1º)."""
        def _fn(cur):
            cur.execute(
                "SELECT cnae_code, cnae_description, cnae_section, cnae_division, "
                "confidence, source, rank FROM target_classifications "
                "WHERE target_id = %s ORDER BY rank, confidence DESC", (target_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def has_receita_cnae(self, target_id: int) -> bool:
        """Se o alvo já tem CNAE oficial da Receita (evita reconsulta no batch)."""
        def _fn(cur):
            cur.execute("SELECT 1 FROM target_classifications "
                        "WHERE target_id = %s AND source = 'receita' LIMIT 1", (target_id,))
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def count_targets_without_cnae(self) -> int:
        """Alvos (não descartados) sem nenhuma classificação CNAE — G4 do enrich_all."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM targets t "
                "LEFT JOIN target_classifications tc ON tc.target_id = t.id "
                "WHERE tc.id IS NULL AND t.status <> 'descartado'")
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def cnae_division_avg_score(self, division: str) -> Dict[str, Any]:
        """Benchmark por divisão CNAE: média de score dos alvos classificados nela."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(DISTINCT t.id), COALESCE(ROUND(AVG(t.last_scan_score)), 0) "
                "FROM targets t JOIN target_classifications tc ON tc.target_id = t.id "
                "WHERE tc.cnae_division = %s AND t.last_scan_score IS NOT NULL", (division,))
            count, avg = cur.fetchone()
            return {"count": int(count or 0), "avg_score": int(avg or 0)}

        return await asyncio.to_thread(self._run, _fn)

    # --- contas de usuário (KL-51 f3) -------------------------------------- #

    _USER_COLS = ("id", "email", "name", "plan", "max_sites", "created_at",
                  "last_login_at", "is_active")

    async def create_user(self, email: str, password_hash: str,
                          name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cria um usuário. Retorna o dict do user (sem hash) ou ``None`` se o e-mail
        já existe (violação da UNIQUE constraint)."""
        def _fn(cur):
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) "
                    "RETURNING " + ", ".join(self._USER_COLS),
                    (email.lower().strip(), password_hash, name))
            except Exception:  # noqa: BLE001 - e-mail duplicado (UNIQUE) → None
                return None
            return self._rows_to_dicts(cur)[0]

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_by_email(self, email: str, *, with_hash: bool = False
                                ) -> Optional[Dict[str, Any]]:
        """Usuário pelo e-mail. `with_hash=True` inclui `password_hash` (só p/ login)."""
        cols = ", ".join(self._USER_COLS) + (", password_hash" if with_hash else "")

        def _fn(cur):
            cur.execute(f"SELECT {cols} FROM users WHERE email = %s", (email.lower().strip(),))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(f"SELECT {', '.join(self._USER_COLS)} FROM users WHERE id = %s",
                        (user_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def touch_user_login(self, user_id: int) -> None:
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            "UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,)))

    async def set_user_password(self, email: str, password_hash: str) -> bool:
        """Atualiza a senha (hash). Retorna True se um usuário foi afetado."""
        def _fn(cur):
            cur.execute("UPDATE users SET password_hash = %s WHERE email = %s",
                        (password_hash, email.lower().strip()))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def list_user_sites(self, user_id: int) -> List[Dict[str, Any]]:
        """Sites do usuário com dados do target (score, último scan, setor, semáforo)."""
        def _fn(cur):
            cur.execute(
                "SELECT us.target_id, us.is_owner, us.added_at, "
                "       t.url, t.domain, t.sector, t.last_scan_score, t.last_scan_at, "
                "       t.platform, s.semaphore AS last_semaphore "
                "FROM user_sites us "
                "JOIN targets t ON t.id = us.target_id "
                "LEFT JOIN LATERAL (SELECT semaphore FROM scans WHERE target_id = t.id "
                "                   ORDER BY scanned_at DESC LIMIT 1) s ON TRUE "
                "WHERE us.user_id = %s ORDER BY us.added_at DESC", (user_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def count_user_sites(self, user_id: int) -> int:
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM user_sites WHERE user_id = %s", (user_id,))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_site(self, user_id: int, target_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT id, user_id, target_id, is_owner FROM user_sites "
                        "WHERE user_id = %s AND target_id = %s", (user_id, target_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def link_user_site(self, user_id: int, target_id: int,
                             is_owner: bool = False) -> bool:
        """Vincula um target ao usuário (idempotente). Retorna True se inseriu."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO user_sites (user_id, target_id, is_owner) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, target_id) DO NOTHING", (user_id, target_id, is_owner))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def unlink_user_site(self, user_id: int, target_id: int) -> bool:
        def _fn(cur):
            cur.execute("DELETE FROM user_sites WHERE user_id = %s AND target_id = %s",
                        (user_id, target_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def set_user_site_owner(self, user_id: int, target_id: int,
                                  is_owner: bool = True) -> bool:
        def _fn(cur):
            cur.execute("UPDATE user_sites SET is_owner = %s "
                        "WHERE user_id = %s AND target_id = %s", (is_owner, user_id, target_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def get_targets_scanned_by_email(self, email: str, limit: int = 10) -> List[int]:
        """IDs de alvos que este e-mail já escaneou (KL-25, `scans.scanned_by_email`) ou
        cujo contato público bate o e-mail — mais recente primeiro. Para vincular o
        histórico de consultas a uma conta recém-criada (KL-51 f3)."""
        e = (email or "").lower().strip()

        def _fn(cur):
            cur.execute(
                "SELECT t.id, MAX(s.scanned_at) AS last_scan "
                "FROM targets t JOIN scans s ON s.target_id = t.id "
                "WHERE lower(s.scanned_by_email) = %s OR lower(t.contact_email) = %s "
                "GROUP BY t.id ORDER BY last_scan DESC NULLS LAST LIMIT %s", (e, e, limit))
            return [r[0] for r in cur.fetchall()]

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_sites_for_monitoring(self, age_days: int = 30) -> List[Dict[str, Any]]:
        """Sites de contas ATIVAS cujo último scan tem mais de `age_days` dias — o
        monitoramento mensal (KL-51 f3). Uma linha por (usuário, site): o mesmo site
        pode ter vários donos, cada um recebe seu e-mail (o scan é deduplicado no script)."""
        def _fn(cur):
            cur.execute(
                "SELECT us.user_id, u.email AS user_email, t.id AS target_id, t.url, "
                "       t.domain, t.last_scan_score "
                "FROM user_sites us "
                "JOIN users u ON u.id = us.user_id AND u.is_active = TRUE "
                "JOIN targets t ON t.id = us.target_id "
                "WHERE t.last_scan_at IS NULL "
                "   OR t.last_scan_at < NOW() - (%s || ' days')::interval "
                "ORDER BY t.id", (str(age_days),))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def latest_scan_meta(self, target_id: int) -> Optional[Dict[str, Any]]:
        """Score + fail_count do scan mais recente do alvo (p/ comparar evolução)."""
        def _fn(cur):
            cur.execute("SELECT score, fail_count FROM scans WHERE target_id = %s "
                        "ORDER BY scanned_at DESC LIMIT 1", (target_id,))
            row = cur.fetchone()
            return {"score": row[0], "fail_count": row[1]} if row else None

        return await asyncio.to_thread(self._run, _fn)

    # --- recuperação de senha (código 6 dígitos) --------------------------- #

    async def create_password_reset(self, email: str, code: str, ttl_seconds: int) -> None:
        """Grava um código de reset e limpa os expirados do mesmo e-mail (sem cron)."""
        def _fn(cur):
            cur.execute("DELETE FROM password_resets WHERE email = %s AND "
                        "(used = TRUE OR expires_at < NOW())", (email.lower().strip(),))
            cur.execute(
                "INSERT INTO password_resets (email, code, expires_at) "
                "VALUES (%s, %s, NOW() + (%s || ' seconds')::interval)",
                (email.lower().strip(), code, str(ttl_seconds)))

        await asyncio.to_thread(self._run, _fn)

    async def count_password_resets_last_hour(self, email: str) -> int:
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM password_resets WHERE email = %s "
                        "AND created_at > NOW() - INTERVAL '1 hour'", (email.lower().strip(),))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def verify_password_reset(self, email: str, code: str) -> bool:
        """Valida (e consome) um código de reset. True se válido, não usado, não expirado."""
        def _fn(cur):
            cur.execute(
                "SELECT id FROM password_resets WHERE email = %s AND code = %s "
                "AND used = FALSE AND expires_at > NOW() ORDER BY created_at DESC LIMIT 1",
                (email.lower().strip(), code))
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE password_resets SET used = TRUE WHERE id = %s", (row[0],))
            return True

        return await asyncio.to_thread(self._run, _fn)

    async def get_scan_history_for_email(self, email: str, limit: int = 20
                                         ) -> List[Dict[str, Any]]:
        """Histórico de consultas de um e-mail (KL-25 `scans.scanned_by_email`) — 1 linha
        por URL (o scan mais recente), mais recente primeiro. Alimenta o "Histórico de
        consultas" do dashboard (KL-51 f3 fix)."""
        e = (email or "").lower().strip()

        def _fn(cur):
            cur.execute(
                "SELECT * FROM (SELECT DISTINCT ON (url) id, url, score, semaphore, scanned_at "
                "FROM scans WHERE lower(scanned_by_email) = %s AND score IS NOT NULL "
                "ORDER BY url, scanned_at DESC) t ORDER BY scanned_at DESC LIMIT %s", (e, limit))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_public_profile_domains(self, limit: int = 50000) -> List[Dict[str, Any]]:
        """Domínios com **perfil público** (para o sitemap, KL-51 f4): sites com scan
        real (`scanned`/`alerted`, com score) e `site_profile`. Exclui descartado/
        sem_contato. `domain` + `last_scan_at` (lastmod), mais recente primeiro."""
        def _fn(cur):
            cur.execute(
                "SELECT t.domain, t.last_scan_at FROM targets t "
                "JOIN site_profile sp ON sp.target_id = t.id "
                "WHERE t.status IN ('scanned', 'alerted') AND t.last_scan_score IS NOT NULL "
                "  AND t.domain IS NOT NULL AND t.domain <> '' "
                "ORDER BY t.last_scan_at DESC NULLS LAST LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_users_with_sites(self) -> List[Dict[str, Any]]:
        """Contas de usuário (KL-51 f3) + os sites vinculados (via `user_sites`), para a
        Gestão de Clientes no painel admin. 2 queries numa conexão (evita N+1)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, email, name, plan, max_sites, created_at, last_login_at, is_active "
                "FROM users ORDER BY created_at DESC")
            users = self._rows_to_dicts(cur)
            cur.execute(
                "SELECT us.user_id, us.is_owner, t.id AS target_id, t.url, t.domain, "
                "       t.sector, t.last_scan_score, t.last_scan_at, s.semaphore AS last_semaphore "
                "FROM user_sites us JOIN targets t ON t.id = us.target_id "
                "LEFT JOIN LATERAL (SELECT semaphore FROM scans WHERE target_id = t.id "
                "                   ORDER BY scanned_at DESC LIMIT 1) s ON TRUE "
                "ORDER BY us.added_at DESC")
            by_user: Dict[int, List[Dict[str, Any]]] = {}
            for st in self._rows_to_dicts(cur):
                by_user.setdefault(st["user_id"], []).append(st)
            for u in users:
                u["sites"] = by_user.get(u["id"], [])
            return users

        return await asyncio.to_thread(self._run, _fn)

    async def count_enrichment_groups(self, mode: str = "all") -> Dict[str, int]:
        """Conta os alvos pendentes de enriquecimento por grupo (G1..G4) e o total.
        Ignora `limit` — é o panorama completo do backlog."""
        where = _enrichment_where(mode)

        def _fn(cur):
            cur.execute(
                f"SELECT "
                f"  COUNT(*) FILTER (WHERE {_ENRICH_G1}) AS g1, "
                f"  COUNT(*) FILTER (WHERE {_ENRICH_G2}) AS g2, "
                f"  COUNT(*) FILTER (WHERE {_ENRICH_G3}) AS g3, "
                f"  COUNT(*) FILTER (WHERE {_ENRICH_G4}) AS g4, "
                f"  COUNT(*) AS total "
                f"FROM targets t LEFT JOIN site_profile sp ON sp.target_id = t.id "
                f"WHERE {where}"
            )
            g1, g2, g3, g4, total = cur.fetchone()
            return {"group1": int(g1 or 0), "group2": int(g2 or 0),
                    "group3": int(g3 or 0), "group4": int(g4 or 0), "total": int(total or 0)}

        return await asyncio.to_thread(self._run, _fn)

    async def list_enrichment_candidates(
        self, limit: Optional[int] = 500, mode: str = "all",
    ) -> List[Dict[str, Any]]:
        """Alvos que precisam de enriquecimento, ordenados por prioridade
        (G1 antes de G2 antes de G3; dentro de G1, alerted > scanned > sem_contato
        > discovered). Traz os campos do perfil (LEFT JOIN) para evitar N+1:
        `profile_id`, `profile_description`, `profile_sources`."""
        where = _enrichment_where(mode)

        def _fn(cur):
            sql = (
                "SELECT t.id, t.url, t.domain, t.sector, t.status, t.contact_email, "
                "       t.classification_source, t.classification_confidence, "
                "       sp.id AS profile_id, sp.description AS profile_description, "
                "       sp.extraction_sources AS profile_sources, "
                f"       {_HAS_CNAE} AS has_cnae "
                "FROM targets t LEFT JOIN site_profile sp ON sp.target_id = t.id "
                f"WHERE {where} "
                "ORDER BY "
                f"  CASE WHEN {_ENRICH_G1} THEN 0 WHEN {_ENRICH_G2} THEN 1 "
                f"       WHEN {_ENRICH_G3} THEN 2 ELSE 3 END, "
                "  CASE t.status WHEN 'alerted' THEN 1 WHEN 'scanned' THEN 2 "
                "       WHEN 'sem_contato' THEN 3 WHEN 'discovered' THEN 4 ELSE 5 END, "
                "  t.id"
            )
            if limit is not None:
                cur.execute(sql + " LIMIT %s", (limit,))
            else:
                cur.execute(sql)
            return self._rows_to_dicts(cur)

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

    async def sector_avg_score(self, sector: str) -> Dict[str, Any]:
        """Média de score dos alvos de um setor (benchmark do resultado — KL-51 f2).
        Usa `targets.last_scan_score` (1 valor por alvo, já escaneado)."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), COALESCE(ROUND(AVG(last_scan_score)), 0) FROM targets "
                "WHERE sector = %s AND last_scan_score IS NOT NULL",
                (sector,))
            count, avg = cur.fetchone()
            return {"count": int(count or 0), "avg_score": int(avg or 0)}

        return await asyncio.to_thread(self._run, _fn)

    async def global_avg_score(self) -> Dict[str, Any]:
        """Média de score de todos os alvos escaneados (benchmark geral)."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), COALESCE(ROUND(AVG(last_scan_score)), 0) FROM targets "
                "WHERE last_scan_score IS NOT NULL")
            count, avg = cur.fetchone()
            return {"count": int(count or 0), "avg_score": int(avg or 0)}

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

    # KL-31: também elegíveis os sites com score 100 verde (0 falhas) — recebem o
    # convite de análise completa gratuita, não o alerta.
    _ALERT_ELIGIBLE_WHERE = (
        "t.status = 'scanned' AND t.contact_email IS NOT NULL "
        "AND (s.fail_count > 0 OR (s.score = 100 AND s.semaphore = 'verde')) "
        "AND (t.last_alert_at IS NULL OR t.last_alert_at < NOW() - INTERVAL '30 days')")

    async def get_eligible_targets_for_alert(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alvos escaneados com e-mail, sem alerta nos últimos 30d: com FALHAS
        (alerta) ou score 100 verde (convite de scan completo gratuito, KL-31)."""
        def _fn(cur):
            cur.execute(
                f"""
                SELECT t.*, s.score AS scan_score, s.fail_count AS scan_fail_count,
                       s.semaphore AS scan_semaphore, s.checks_json AS scan_checks
                FROM targets t
                JOIN scans s ON t.last_scan_id = s.id
                WHERE {self._ALERT_ELIGIBLE_WHERE}
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
                f"SELECT COUNT(*) FROM targets t JOIN scans s ON t.last_scan_id = s.id "
                f"WHERE {self._ALERT_ELIGIBLE_WHERE}"
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
