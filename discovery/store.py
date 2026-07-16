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

-- KL-56: gestão da landing pública pelo operador.
--  public_visible = FALSE  -> /site/{dominio} some (mesmo comportamento de descartado)
--  edited_by_admin = TRUE  -> o enrich (worker/enrich_all) NÃO sobrescreve os campos
--                             editados à mão (description/business_type/tags/company_name).
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS public_visible BOOLEAN DEFAULT TRUE;
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS edited_by_admin BOOLEAN DEFAULT FALSE;
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS edited_by_admin_at TIMESTAMP;

-- KL-67: campos extraídos com baixa confiança (ex.: rede social que não bate o domínio).
-- Array de nomes de campo suspeitos, ex.: ['instagram','facebook']. O painel mostra ⚠️.
ALTER TABLE site_profile ADD COLUMN IF NOT EXISTS low_confidence_fields TEXT[] DEFAULT '{}';

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

-- KL-68: verificação de propriedade (ownership) em tiers. `is_owner` já existe;
-- registramos quando e como foi verificado (auditoria).
ALTER TABLE user_sites ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE user_sites ADD COLUMN IF NOT EXISTS verification_method VARCHAR(20);  -- 'auto_email' | 'code_verification' | NULL

-- Tentativas de verificação de propriedade por código (Tier 2, KL-68). Código enviado
-- ao contact_email do alvo (nunca exposto), TTL 30 min, máx 3 tentativas.
CREATE TABLE IF NOT EXISTS ownership_verifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    method VARCHAR(20) NOT NULL,           -- 'auto_email' | 'code_to_contact'
    code VARCHAR(6),
    attempts INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',  -- 'pending' | 'verified' | 'expired' | 'failed'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    verified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 minutes')
);
CREATE INDEX IF NOT EXISTS idx_ownership_verif_user_target ON ownership_verifications(user_id, target_id);
CREATE INDEX IF NOT EXISTS idx_ownership_verif_status ON ownership_verifications(status);

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

-- Inbox do scan@klarim.net (KL-56): mensagens recebidas via webhook da Hostinger
-- Agentic Mail. Independente do resto do sistema (nenhuma FK). Dedup por message_id.
CREATE TABLE IF NOT EXISTS inbox_messages (
    id SERIAL PRIMARY KEY,
    message_id TEXT UNIQUE,
    from_address TEXT NOT NULL,
    from_name TEXT,
    to_address TEXT DEFAULT 'scan@klarim.net',
    subject TEXT,
    body_preview TEXT,
    body_html TEXT,
    received_at TIMESTAMPTZ,
    is_read BOOLEAN DEFAULT FALSE,
    is_starred BOOLEAN DEFAULT FALSE,
    is_archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inbox_received ON inbox_messages(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_read ON inbox_messages(is_read);
-- KL-60: origem da mensagem — 'webhook' (e-mails da Hostinger) | 'contact_form'
-- (formulário do site, gravado direto no inbox mesmo se o e-mail via Resend falhar).
ALTER TABLE inbox_messages ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'webhook';

-- Leads (KL-61): 1 registro por e-mail verificado (`scans.scanned_by_email`). Camada
-- de agregação sobre scans/contas — PQL scoring. `classification` é SEMPRE derivada do
-- `lead_score` (nunca setada à mão). E-mail normalizado em lowercase (UNIQUE).
CREATE TABLE IF NOT EXISTS scan_leads (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    first_scan_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ,
    total_scans INTEGER DEFAULT 0,
    urls_scanned TEXT[] DEFAULT '{}',
    domains_scanned TEXT[] DEFAULT '{}',
    best_score INTEGER,
    worst_score INTEGER,
    last_score INTEGER,
    last_domain TEXT,
    has_account BOOLEAN DEFAULT FALSE,
    account_id INTEGER,
    has_monitoring BOOLEAN DEFAULT FALSE,
    lead_score INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'cold'
        CHECK (classification IN ('cold', 'warm', 'hot', 'pql')),
    sector TEXT,
    platform TEXT,
    is_corporate_email BOOLEAN DEFAULT FALSE,
    source TEXT DEFAULT 'scan',
    tags TEXT[] DEFAULT '{}',
    notes TEXT,
    last_email_sent_at TIMESTAMPTZ,
    emails_sent INTEGER DEFAULT 0,
    opted_out BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scan_leads_classification ON scan_leads(classification);
CREATE INDEX IF NOT EXISTS idx_scan_leads_lead_score ON scan_leads(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_scan_leads_last_activity ON scan_leads(last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_scan_leads_sector ON scan_leads(sector);
CREATE INDEX IF NOT EXISTS idx_scan_leads_has_account ON scan_leads(has_account);

-- KL-62: log unificado de TODO e-mail enviado pelo Resend (centralizado no
-- KlarimMailer). Cobre os 20 caminhos de envio por construção. Não substitui
-- alert_log/rescan_log (que continuam), mas unifica a contabilidade/blocklist.
CREATE TABLE IF NOT EXISTS email_log (
    id SERIAL PRIMARY KEY,
    email_id TEXT,
    to_email TEXT NOT NULL,
    email_type TEXT NOT NULL,
    subject TEXT,
    target_id INTEGER,
    domain TEXT,
    status TEXT DEFAULT 'sent',
    blocked_reason TEXT,
    error TEXT,
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    source TEXT,
    batch_id TEXT,
    from_domain TEXT
);
-- Migração da migração de remetente (klarimscan.com): coluna nova em tabelas já criadas.
ALTER TABLE email_log ADD COLUMN IF NOT EXISTS from_domain TEXT;
CREATE INDEX IF NOT EXISTS idx_email_log_sent_at ON email_log(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_log_to_email ON email_log(to_email);
CREATE INDEX IF NOT EXISTS idx_email_log_type ON email_log(email_type);
CREATE INDEX IF NOT EXISTS idx_email_log_status ON email_log(status);
CREATE INDEX IF NOT EXISTS idx_email_log_email_id ON email_log(email_id);

-- ===== KL-44 (Guardião Digital): planos, assinaturas e trial reverse de 30 dias ===== --
-- ⚠️ Não existe tabela `accounts` neste schema: a "conta" é o `users`. Portanto
-- `account_id` (nome do card e da API) referencia users(id).
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,                       -- 'free', 'pro', 'agency'
    name TEXT NOT NULL,
    price_monthly INTEGER NOT NULL DEFAULT 0,  -- centavos
    price_yearly INTEGER NOT NULL DEFAULT 0,   -- centavos
    max_sites INTEGER NOT NULL DEFAULT 1,
    scan_frequency TEXT NOT NULL DEFAULT 'monthly',  -- daily|weekly|biweekly|monthly
    vigilia_ssl BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_domain BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_score BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_email BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_reputation BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_changes BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_phishing BOOLEAN NOT NULL DEFAULT FALSE,
    vigilia_uptime BOOLEAN NOT NULL DEFAULT FALSE,
    uptime_interval_minutes INTEGER DEFAULT NULL,
    bulletin_frequency TEXT DEFAULT 'none',           -- none|monthly|weekly|daily
    action_plan_limit INTEGER NOT NULL DEFAULT 1,     -- 0 = ilimitado
    history_months INTEGER NOT NULL DEFAULT 3,        -- 0 = ilimitado
    competitor_slots INTEGER NOT NULL DEFAULT 0,
    lgpd_full BOOLEAN NOT NULL DEFAULT FALSE,
    widget_type TEXT NOT NULL DEFAULT 'badge',        -- badge|interactive|whitelabel
    pdf_report_frequency TEXT DEFAULT 'none',         -- none|monthly|weekly
    export_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    api_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Seed dos 3 planos (idempotente).
INSERT INTO plans (id, name, price_monthly, price_yearly, max_sites, scan_frequency,
    vigilia_ssl, vigilia_domain, vigilia_score, vigilia_email, vigilia_reputation,
    vigilia_changes, vigilia_phishing, vigilia_uptime, uptime_interval_minutes,
    bulletin_frequency, action_plan_limit, history_months, competitor_slots,
    lgpd_full, widget_type, pdf_report_frequency, export_enabled, api_enabled)
VALUES
    ('free', 'Free', 0, 0, 1, 'monthly',
     FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, NULL,
     'monthly', 1, 3, 0, FALSE, 'badge', 'none', FALSE, FALSE),
    ('pro', 'Pro', 1900, 9900, 5, 'weekly',
     TRUE, TRUE, TRUE, TRUE, TRUE, FALSE, FALSE, TRUE, 30,
     'weekly', 3, 12, 3, TRUE, 'interactive', 'monthly', FALSE, FALSE),
    ('agency', 'Agency', 4900, 0, 15, 'daily',
     TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, 5,
     'daily', 0, 0, 10, TRUE, 'whitelabel', 'weekly', TRUE, TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL DEFAULT 'trial',   -- trial|active|free|expired|cancelled
    trial_ends_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    billing_cycle TEXT DEFAULT 'monthly',   -- monthly|yearly
    last_payment_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(account_id)
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_account ON subscriptions(account_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_plan ON subscriptions(plan_id);

CREATE TABLE IF NOT EXISTS subscription_history (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL,
    old_plan_id TEXT,
    new_plan_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT 'system',  -- system|admin|user|payment
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subhist_account ON subscription_history(account_id);

-- ===== KL-44 P2 (Vigílias core): monitoramento silencioso contínuo ===== --
-- Uma vigília por (usuário, domínio, tipo). O worker roda os checks e cria alertas.
CREATE TABLE IF NOT EXISTS vigilias (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    site_domain TEXT NOT NULL,
    tipo TEXT NOT NULL,                           -- 'ssl','domain','score','email','reputation'
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_check_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ,
    last_status TEXT DEFAULT 'ok',                -- 'ok','warning','critical','error'
    last_data JSONB DEFAULT '{}',
    alert_count INTEGER NOT NULL DEFAULT 0,
    last_alert_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, site_domain, tipo)
);
CREATE INDEX IF NOT EXISTS idx_vigilias_next_check ON vigilias(next_check_at) WHERE enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_vigilias_user ON vigilias(user_id);

CREATE TABLE IF NOT EXISTS vigilia_alerts (
    id SERIAL PRIMARY KEY,
    vigilia_id INTEGER NOT NULL REFERENCES vigilias(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    site_domain TEXT NOT NULL,
    tipo TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',     -- 'info','warning','critical'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    action_text TEXT,
    data JSONB DEFAULT '{}',
    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
    email_id TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vigilia_alerts_user ON vigilia_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_vigilia_alerts_vigilia ON vigilia_alerts(vigilia_id);

-- KL-44 P4 — domínios suspeitos (typosquatting/phishing) detectados nos CT logs.
CREATE TABLE IF NOT EXISTS typosquat_alerts (
    id SERIAL PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suspicious_domain VARCHAR(255) NOT NULL,
    similarity_type VARCHAR(30) NOT NULL,   -- levenshtein|homoglyph|tld_variant
    distance INTEGER,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    notified BOOLEAN DEFAULT FALSE,
    dismissed BOOLEAN DEFAULT FALSE,
    UNIQUE(target_id, suspicious_domain)
);
CREATE INDEX IF NOT EXISTS idx_typosquat_target ON typosquat_alerts(target_id);

-- KL-44 P3 — papel do usuário (dono de negócio vs profissional de TI).
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'owner';  -- owner|technician|both

-- KL-44 P3 — vínculo dono ↔ técnico (o técnico recebe o laudo técnico do site).
CREATE TABLE IF NOT EXISTS technician_links (
    id SERIAL PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    technician_email VARCHAR(255) NOT NULL,
    technician_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) DEFAULT 'pending',   -- pending|active|revoked
    invite_code VARCHAR(16) UNIQUE,
    invited_at TIMESTAMPTZ DEFAULT NOW(),
    linked_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    last_access_at TIMESTAMPTZ,
    UNIQUE(owner_user_id, target_id, technician_email)
);
CREATE INDEX IF NOT EXISTS idx_tech_links_owner ON technician_links(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_tech_links_tech_user ON technician_links(technician_user_id);
CREATE INDEX IF NOT EXISTS idx_tech_links_email ON technician_links(technician_email);
CREATE INDEX IF NOT EXISTS idx_tech_links_invite ON technician_links(invite_code);

-- KL-44 P3 — laudos compartilháveis (link público /laudo/{code}, TTL 30 dias).
CREATE TABLE IF NOT EXISTS shared_reports (
    id SERIAL PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code VARCHAR(12) UNIQUE NOT NULL,
    scan_id INTEGER REFERENCES scans(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
    access_count INTEGER DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    technician_link_id INTEGER REFERENCES technician_links(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_shared_reports_code ON shared_reports(code);
CREATE INDEX IF NOT EXISTS idx_shared_reports_target ON shared_reports(target_id);

-- KL-44 P3 — histórico de boletins de segurança enviados (recorrentes por plano).
CREATE TABLE IF NOT EXISTS bulletins (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    scan_id INTEGER REFERENCES scans(id),
    bulletin_type VARCHAR(20) NOT NULL,     -- weekly|monthly|daily
    score INTEGER,
    previous_score INTEGER,
    score_trend VARCHAR(10),                -- up|down|stable
    vigilias_summary JSONB,
    top_action TEXT,
    shared_report_code VARCHAR(12),
    technician_notified BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bulletins_user ON bulletins(user_id);
CREATE INDEX IF NOT EXISTS idx_bulletins_target ON bulletins(target_id, sent_at);

-- Overrides de configuração ao vivo (admin) — o banco tem prioridade sobre o .env.
-- Guarda também ADMIN_PASSWORD_HASH (bcrypt) e MCP_API_KEY (rotação). Sem segredos em
-- texto puro no .env quando há hash no banco.
CREATE TABLE IF NOT EXISTS admin_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'admin',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
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

    async def list_targets_matching(self, pattern: str = "", limit: int = 500
                                    ) -> List[Dict[str, Any]]:
        """Alvos (não descartados) cujo `domain` ou `url` casa `%pattern%` (case-insensitive).
        Para o re-enrich forçado (`enrich_all --domain/--force`). `pattern` vazio = todos."""
        like = f"%{pattern}%"

        def _fn(cur):
            cur.execute(
                "SELECT id, url, domain, last_scan_score FROM targets "
                "WHERE status <> 'descartado' AND (domain ILIKE %s OR url ILIKE %s) "
                "ORDER BY id LIMIT %s", (like, like, limit))
            return self._rows_to_dicts(cur)

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
            # JOIN traz o semáforo do último scan (KL-14: lista de alvos no painel) e
            # o estado da landing pública (KL-56: has_profile + public_visible).
            cur.execute(
                f"SELECT t.*, s.semaphore AS last_semaphore, "
                f"       (sp.id IS NOT NULL) AS has_profile, "
                f"       COALESCE(sp.public_visible, TRUE) AS public_visible "
                f"FROM targets t "
                f"LEFT JOIN scans s ON t.last_scan_id = s.id "
                f"LEFT JOIN site_profile sp ON sp.target_id = t.id {clause} "
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

    async def list_unscanned_targets(self, limit: int = 500,
                                     status: str = "sem_contato") -> List[Dict[str, Any]]:
        """Alvos de um `status` que **nunca** foram escaneados (`last_scan_id IS NULL`).
        KL-60: reprocessar o backlog de `sem_contato` (agora desacoplado do e-mail).
        Retorna `id`+`url`, ordenado por id (batches estáveis)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, url FROM targets WHERE status = %s AND last_scan_id IS NULL "
                "ORDER BY id LIMIT %s", (status, limit))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def count_unscanned_targets(self, status: str = "sem_contato") -> int:
        """Total de alvos de um `status` sem scan (KL-60) — panorama do backlog."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM targets WHERE status = %s AND last_scan_id IS NULL",
                (status,))
            return int(cur.fetchone()[0])

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

    async def email_has_verified_scan(self, email: str) -> bool:
        """True se o e-mail já tem uma verificação de scan CONFIRMADA (KL-25). Usado no
        signup direcionado (KL-44 F-03b): quem já provou o e-mail no scan não re-verifica."""
        def _fn(cur):
            cur.execute(
                "SELECT 1 FROM scan_verifications WHERE email = %s AND verified = TRUE LIMIT 1",
                (email.lower().strip(),))
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

    # KL-56: campos que o operador edita à mão no painel — o enrich (worker/enrich_all)
    # NÃO os sobrescreve quando `edited_by_admin` está ligado (guard no ON CONFLICT).
    # KL-67: contatos também passam a ser preservados quando o operador corrige à mão.
    _SP_ADMIN_EDITABLE = ("company_name", "description", "business_type",
                          "phone", "whatsapp", "address",
                          "instagram", "facebook", "linkedin", "youtube", "tiktok")  # + tags (TEXT[])

    async def upsert_site_profile(self, target_id: int, profile: Dict[str, Any]) -> None:
        """Grava (ou atualiza) o perfil comercial de um alvo (1 por target).

        KL-56: se o perfil já foi editado pelo operador (`edited_by_admin=TRUE`), os
        campos editáveis à mão (company_name/description/business_type/tags) são
        **preservados** — o enrich automático só atualiza o resto. `public_visible` e
        `edited_by_admin` nunca são tocados aqui (o upsert não os inclui)."""
        fields = list(self._SP_FIELDS)
        vals = [profile.get(f) for f in fields]
        tech = json.dumps(profile.get("technologies") or {})
        sources = list(profile.get("extraction_sources") or [])
        tags = list(profile.get("tags") or [])  # KL-55: TEXT[] (como extraction_sources)
        lcf = list(profile.get("low_confidence_fields") or [])  # KL-67: TEXT[]

        def _upd(col: str) -> str:
            # Preserva a edição manual: mantém o valor antigo quando edited_by_admin.
            if col in self._SP_ADMIN_EDITABLE or col == "tags":
                return (f"{col} = CASE WHEN site_profile.edited_by_admin "
                        f"THEN site_profile.{col} ELSE EXCLUDED.{col} END")
            return f"{col} = EXCLUDED.{col}"

        def _fn(cur):
            cols = (", ".join(fields)
                    + ", technologies, extraction_sources, tags, low_confidence_fields, extracted_at")
            ph = ", ".join(["%s"] * len(fields)) + ", %s, %s, %s, %s, NOW()"
            updates = ", ".join(_upd(f) for f in fields)
            cur.execute(
                f"INSERT INTO site_profile (target_id, {cols}) "
                f"VALUES (%s, {ph}) "
                f"ON CONFLICT (target_id) DO UPDATE SET {updates}, "
                f"  technologies = EXCLUDED.technologies, "
                f"  extraction_sources = EXCLUDED.extraction_sources, "
                f"  {_upd('tags')}, "
                f"  low_confidence_fields = EXCLUDED.low_confidence_fields, extracted_at = NOW()",
                [target_id, *vals, tech, sources, tags, lcf],
            )

        await asyncio.to_thread(self._run, _fn)

    async def update_site_profile_fields(
        self, target_id: int, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Edição manual do perfil pelo operador (KL-56/67). Atualiza os campos editáveis
        (texto + contatos), aceita `clear_fields` (setar NULL), marca `edited_by_admin=TRUE`
        (o enrich passa a preservá-los) e limpa os flags de baixa confiança (o operador
        revisou). Retorna o perfil atualizado (ou None se o alvo não tem perfil)."""
        allowed = ("description", "business_type", "company_name", "phone", "whatsapp",
                   "address", "instagram", "facebook", "linkedin", "youtube", "tiktok")
        sets, params, touched = [], [], set()
        for col in allowed:
            if col in fields:
                sets.append(f"{col} = %s")
                params.append((str(fields[col]).strip() or None) if fields[col] is not None else None)
                touched.add(col)
        for col in (fields.get("clear_fields") or []):   # KL-67: limpar explicitamente
            if col in allowed and col not in touched:
                sets.append(f"{col} = NULL")
                touched.add(col)
        if "tags" in fields:
            raw = fields["tags"]
            tags = raw if isinstance(raw, list) else [
                t.strip() for t in str(raw or "").split(",") if t.strip()]
            sets.append("tags = %s")
            params.append(list(tags))
            touched.add("tags")
        if not sets:
            return await self.get_site_profile(target_id)
        sets.append("low_confidence_fields = '{}'")   # KL-67: operador revisou → limpa ⚠️
        sets.append("edited_by_admin = TRUE")
        sets.append("edited_by_admin_at = NOW()")

        def _fn(cur):
            cur.execute(
                f"UPDATE site_profile SET {', '.join(sets)} WHERE target_id = %s "
                f"RETURNING *",
                [*params, target_id],
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def list_site_profiles_min(self) -> List[Dict[str, Any]]:
        """Todos os perfis com os campos que passam por revalidação (KL-67) + o domínio
        (para as heurísticas). Inclui `edited_by_admin` para pular perfis editados à mão."""
        def _fn(cur):
            cur.execute(
                "SELECT sp.target_id, t.domain, sp.phone, sp.address, sp.description, "
                "       sp.instagram, sp.facebook, sp.linkedin, sp.youtube, sp.tiktok, "
                "       COALESCE(sp.edited_by_admin, FALSE) AS edited_by_admin "
                "FROM site_profile sp JOIN targets t ON t.id = sp.target_id")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    _REVALIDATE_COLS = {"phone", "address", "description",
                        "instagram", "facebook", "linkedin", "youtube", "tiktok"}

    async def apply_revalidation(self, target_id: int, null_fields: List[str],
                                 low_conf: List[str]) -> int:
        """Revalidação retroativa (KL-67): zera os campos inválidos e grava os flags de
        baixa confiança. NÃO marca `edited_by_admin` (não é edição manual)."""
        sets = [f"{c} = NULL" for c in null_fields if c in self._REVALIDATE_COLS]
        sets.append("low_confidence_fields = %s")

        def _fn(cur):
            cur.execute(f"UPDATE site_profile SET {', '.join(sets)} WHERE target_id = %s",
                        [list(low_conf), target_id])
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def set_profile_visibility(
        self, target_id: int, visible: bool
    ) -> Optional[Dict[str, Any]]:
        """Liga/desliga a landing pública (KL-56). `public_visible=FALSE` faz
        `/site/{dominio}` sumir e exclui o perfil do sitemap. Retorna o perfil."""
        def _fn(cur):
            cur.execute(
                "UPDATE site_profile SET public_visible = %s WHERE target_id = %s "
                "RETURNING *",
                (bool(visible), target_id),
            )
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

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
                  "last_login_at", "is_active", "role")

    async def create_user(self, email: str, password_hash: str,
                          name: Optional[str] = None,
                          role: str = "owner") -> Optional[Dict[str, Any]]:
        """Cria um usuário. `role`: owner|technician|both (KL-44 P3). Retorna o dict do
        user (sem hash) ou ``None`` se o e-mail já existe (violação da UNIQUE)."""
        r = role if role in ("owner", "technician", "both") else "owner"

        def _fn(cur):
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash, name, role) VALUES (%s, %s, %s, %s) "
                    "RETURNING " + ", ".join(self._USER_COLS),
                    (email.lower().strip(), password_hash, name, r))
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

    async def update_user_name(self, user_id: int, name: Optional[str]) -> bool:
        """Atualiza o nome do usuário (KL-57). Retorna True se afetou uma linha."""
        clean = (name or "").strip() or None

        def _fn(cur):
            cur.execute("UPDATE users SET name = %s WHERE id = %s", (clean, user_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def delete_user(self, user_id: int) -> bool:
        """Exclui a conta do usuário (KL-57). O `ON DELETE CASCADE` remove os vínculos
        em `user_sites`; `targets`/`scans`/`site_profile` são dados do sistema e
        **permanecem**. Retorna True se um usuário foi removido."""
        def _fn(cur):
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
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

    # --- verificação de propriedade (ownership, KL-68) --------------------- #

    async def site_has_owner(self, target_id: int, exclude_user_id: Optional[int] = None) -> bool:
        """True se algum usuário já é dono verificado do alvo (first-come-first-served).
        `exclude_user_id` ignora um usuário (ex.: o próprio, ao revalidar)."""
        def _fn(cur):
            if exclude_user_id is not None:
                cur.execute("SELECT 1 FROM user_sites WHERE target_id = %s AND is_owner = TRUE "
                            "AND user_id <> %s LIMIT 1", (target_id, exclude_user_id))
            else:
                cur.execute("SELECT 1 FROM user_sites WHERE target_id = %s AND is_owner = TRUE "
                            "LIMIT 1", (target_id,))
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def mark_site_verified(self, user_id: int, target_id: int, method: str) -> bool:
        """Marca o vínculo como dono verificado (KL-68): `is_owner=TRUE`, `verified_at=NOW()`
        e registra o método (`auto_email` | `code_verification`). Retorna True se afetou."""
        def _fn(cur):
            cur.execute(
                "UPDATE user_sites SET is_owner = TRUE, verified_at = NOW(), "
                "verification_method = %s WHERE user_id = %s AND target_id = %s",
                (method, user_id, target_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def create_ownership_verification(self, user_id: int, target_id: int,
                                            method: str, code: str) -> Dict[str, Any]:
        """Cria uma verificação pendente (Tier 2). Expira as pendências anteriores do
        mesmo (usuário, alvo) para não acumular códigos válidos. TTL 30 min (default do schema)."""
        def _fn(cur):
            cur.execute(
                "UPDATE ownership_verifications SET status = 'expired' "
                "WHERE user_id = %s AND target_id = %s AND status = 'pending'",
                (user_id, target_id))
            cur.execute(
                "INSERT INTO ownership_verifications (user_id, target_id, method, code) "
                "VALUES (%s, %s, %s, %s) RETURNING id, expires_at",
                (user_id, target_id, method, code))
            return self._rows_to_dicts(cur)[0]

        return await asyncio.to_thread(self._run, _fn)

    async def get_pending_ownership_verification(self, user_id: int, target_id: int
                                                 ) -> Optional[Dict[str, Any]]:
        """Verificação pendente, não expirada e com tentativas < 3 (a mais recente)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, code, attempts, status, expires_at FROM ownership_verifications "
                "WHERE user_id = %s AND target_id = %s AND status = 'pending' "
                "  AND expires_at > NOW() AND attempts < 3 "
                "ORDER BY created_at DESC LIMIT 1", (user_id, target_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def bump_ownership_attempt(self, verification_id: int) -> int:
        """Incrementa attempts; se chegar a 3, marca 'failed'. Retorna o novo attempts."""
        def _fn(cur):
            cur.execute(
                "UPDATE ownership_verifications SET attempts = attempts + 1, "
                "status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE status END "
                "WHERE id = %s RETURNING attempts", (verification_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 3

        return await asyncio.to_thread(self._run, _fn)

    async def mark_ownership_verified(self, verification_id: int) -> None:
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            "UPDATE ownership_verifications SET status = 'verified', verified_at = NOW() "
            "WHERE id = %s", (verification_id,)))

    async def get_target_owner(self, target_id: int) -> Optional[Dict[str, Any]]:
        """Dono verificado do alvo (admin): e-mail + quando/como verificou. None se não há."""
        def _fn(cur):
            cur.execute(
                "SELECT u.id AS user_id, u.email, us.verified_at, us.verification_method "
                "FROM user_sites us JOIN users u ON u.id = us.user_id "
                "WHERE us.target_id = %s AND us.is_owner = TRUE "
                "ORDER BY us.verified_at ASC NULLS LAST LIMIT 1", (target_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def set_user_active(self, user_id: int, active: bool) -> bool:
        """Ativa/desativa a conta do usuário (KL-69). Retorna True se afetou uma linha."""
        def _fn(cur):
            cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (active, user_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def mark_ownership_revoked(self, user_id: int, target_id: int) -> None:
        """Marca as verificações de propriedade de (usuário, alvo) como 'revoked' — usado
        ao remover um site do usuário (KL-69), para auditoria."""
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            "UPDATE ownership_verifications SET status = 'revoked' "
            "WHERE user_id = %s AND target_id = %s AND status IN ('pending', 'verified')",
            (user_id, target_id)))

    # --- KL-44 P3: técnico vinculado + laudo compartilhável + boletim ------- #

    _TL_COLS = ("id", "owner_user_id", "target_id", "technician_email",
                "technician_user_id", "status", "invite_code", "invited_at",
                "linked_at", "revoked_at", "last_access_at")

    async def create_technician_link(self, owner_user_id: int, target_id: int,
                                     technician_email: str, invite_code: str
                                     ) -> Optional[Dict[str, Any]]:
        """Cria (ou reativa) o vínculo dono→técnico. Idempotente por (owner,target,email):
        se já existe revogado, volta a pending. Retorna a linha (None em corrida)."""
        email = (technician_email or "").lower().strip()

        def _fn(cur):
            cur.execute(
                "INSERT INTO technician_links (owner_user_id, target_id, technician_email, "
                "  invite_code, status) VALUES (%s, %s, %s, %s, 'pending') "
                "ON CONFLICT (owner_user_id, target_id, technician_email) DO UPDATE SET "
                "  status = 'pending', invite_code = EXCLUDED.invite_code, "
                "  invited_at = NOW(), revoked_at = NULL "
                "RETURNING " + ", ".join(self._TL_COLS),
                (owner_user_id, target_id, email, invite_code))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_technician_links(self, owner_user_id: int,
                                   target_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Vínculos técnicos do dono (opcionalmente de um alvo)."""
        def _fn(cur):
            if target_id is not None:
                cur.execute(
                    "SELECT " + ", ".join(self._TL_COLS) + " FROM technician_links "
                    "WHERE owner_user_id = %s AND target_id = %s AND status <> 'revoked' "
                    "ORDER BY invited_at DESC", (owner_user_id, target_id))
            else:
                cur.execute(
                    "SELECT " + ", ".join(self._TL_COLS) + " FROM technician_links "
                    "WHERE owner_user_id = %s AND status <> 'revoked' "
                    "ORDER BY invited_at DESC", (owner_user_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_technician_link(self, link_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT " + ", ".join(self._TL_COLS) +
                        " FROM technician_links WHERE id = %s", (link_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def revoke_technician_link(self, link_id: int, owner_user_id: int) -> bool:
        """Revoga um vínculo (só o dono dono pode). Retorna True se afetou."""
        def _fn(cur):
            cur.execute(
                "UPDATE technician_links SET status = 'revoked', revoked_at = NOW() "
                "WHERE id = %s AND owner_user_id = %s AND status <> 'revoked'",
                (link_id, owner_user_id))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def accept_technician_invite(self, invite_code: str, technician_user_id: int) -> Optional[Dict[str, Any]]:
        """Vincula a conta do técnico a um convite pendente (status→active)."""
        def _fn(cur):
            cur.execute(
                "UPDATE technician_links SET technician_user_id = %s, status = 'active', "
                "  linked_at = NOW() WHERE invite_code = %s AND status = 'pending' "
                "RETURNING " + ", ".join(self._TL_COLS),
                (technician_user_id, invite_code))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def auto_link_technician_by_email(self, email: str, technician_user_id: int) -> int:
        """Vincula os convites pendentes de um e-mail à conta recém-criada (signup KL-44 P3)."""
        e = (email or "").lower().strip()

        def _fn(cur):
            cur.execute(
                "UPDATE technician_links SET technician_user_id = %s, status = 'active', "
                "  linked_at = NOW() WHERE lower(technician_email) = %s AND status = 'pending'",
                (technician_user_id, e))
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def get_technician_clients(self, technician_user_id: int) -> List[Dict[str, Any]]:
        """Sites vinculados ao técnico (dashboard do técnico): domínio, score, semáforo,
        e-mail do dono (mascarado no endpoint), último boletim."""
        def _fn(cur):
            cur.execute(
                "SELECT tl.id AS link_id, tl.target_id, tl.status, tl.last_access_at, "
                "       tl.owner_user_id, ou.email AS owner_email, "
                "       t.url, t.domain, t.last_scan_score, t.last_scan_at, "
                "       s.semaphore AS last_semaphore, "
                "       (SELECT MAX(sent_at) FROM bulletins b "
                "        WHERE b.target_id = tl.target_id AND b.user_id = tl.owner_user_id) AS last_bulletin_at "
                "FROM technician_links tl "
                "JOIN users ou ON ou.id = tl.owner_user_id "
                "JOIN targets t ON t.id = tl.target_id "
                "LEFT JOIN LATERAL (SELECT semaphore FROM scans WHERE target_id = t.id "
                "                   ORDER BY scanned_at DESC LIMIT 1) s ON TRUE "
                "WHERE tl.technician_user_id = %s AND tl.status = 'active' "
                "ORDER BY tl.owner_user_id, t.domain", (technician_user_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_active_technician_for_target(self, owner_user_id: int, target_id: int
                                               ) -> Optional[Dict[str, Any]]:
        """Técnico ativo de um site (para o boletim notificar). None se não há."""
        def _fn(cur):
            cur.execute(
                "SELECT " + ", ".join(self._TL_COLS) + " FROM technician_links "
                "WHERE owner_user_id = %s AND target_id = %s AND status = 'active' "
                "ORDER BY linked_at DESC LIMIT 1", (owner_user_id, target_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def touch_technician_access(self, link_id: int) -> None:
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            "UPDATE technician_links SET last_access_at = NOW() WHERE id = %s", (link_id,)))

    async def search_technician_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Busca um usuário TÉCNICO por e-mail (só id/name/role — nunca outros dados)."""
        e = (email or "").lower().strip()

        def _fn(cur):
            cur.execute("SELECT id, name, role FROM users WHERE email = %s "
                        "AND role IN ('technician', 'both') AND is_active = TRUE", (e,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    # --- laudos compartilháveis --- #

    async def get_latest_scan_id(self, target_id: int) -> Optional[Dict[str, Any]]:
        """Scan mais recente do alvo (id + score + semáforo) — p/ vincular ao laudo."""
        def _fn(cur):
            cur.execute("SELECT id, score, semaphore FROM scans WHERE target_id = %s "
                        "ORDER BY scanned_at DESC LIMIT 1", (target_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_latest_scan_full(self, target_id: int) -> Optional[Dict[str, Any]]:
        """Scan mais recente COM checks_json (para o boletim montar ação prioritária)."""
        def _fn(cur):
            cur.execute("SELECT id, score, semaphore, fail_count, checks_json, scanned_at "
                        "FROM scans WHERE target_id = %s ORDER BY scanned_at DESC LIMIT 1",
                        (target_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_target_vigilias(self, user_id: int, domain: str) -> Dict[str, str]:
        """{tipo: last_status} das vigílias ativas de um usuário para um domínio (boletim)."""
        def _fn(cur):
            cur.execute("SELECT tipo, last_status FROM vigilias "
                        "WHERE user_id = %s AND site_domain = %s AND enabled = TRUE",
                        (user_id, domain))
            return {r[0]: r[1] for r in cur.fetchall()}

        return await asyncio.to_thread(self._run, _fn)

    async def create_shared_report(self, target_id: int, owner_user_id: int, code: str,
                                   scan_id: Optional[int] = None,
                                   technician_link_id: Optional[int] = None
                                   ) -> Dict[str, Any]:
        def _fn(cur):
            cur.execute(
                "INSERT INTO shared_reports (target_id, owner_user_id, code, scan_id, "
                "  technician_link_id) VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id, code, expires_at, created_at",
                (target_id, owner_user_id, code, scan_id, technician_link_id))
            return self._rows_to_dicts(cur)[0]

        return await asyncio.to_thread(self._run, _fn)

    async def get_shared_report_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Laudo pelo código + snapshot do scan (checks/score/semáforo) + domínio. NÃO
        traz e-mail/dado interno do alvo (o endpoint filtra o que expõe)."""
        def _fn(cur):
            cur.execute(
                "SELECT sr.id, sr.code, sr.target_id, sr.owner_user_id, sr.scan_id, "
                "       sr.created_at, sr.expires_at, sr.access_count, sr.technician_link_id, "
                "       (sr.expires_at < NOW()) AS expired, "
                "       t.domain, t.url, "
                "       s.score, s.semaphore, s.checks_json, s.scanned_at, s.fail_count "
                "FROM shared_reports sr JOIN targets t ON t.id = sr.target_id "
                "LEFT JOIN scans s ON s.id = sr.scan_id "
                "WHERE sr.code = %s", (code,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def register_shared_report_access(self, code: str) -> None:
        """Incrementa o contador de acesso + last_accessed_at; propaga ao vínculo técnico."""
        def _fn(cur):
            cur.execute(
                "UPDATE shared_reports SET access_count = access_count + 1, "
                "  last_accessed_at = NOW() WHERE code = %s RETURNING technician_link_id",
                (code,))
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("UPDATE technician_links SET last_access_at = NOW() WHERE id = %s",
                            (row[0],))

        await asyncio.to_thread(self._run, _fn)

    # --- boletins --- #

    async def create_bulletin(self, *, user_id: int, target_id: int, scan_id: Optional[int],
                              bulletin_type: str, score: Optional[int], previous_score: Optional[int],
                              score_trend: str, vigilias_summary: Any, top_action: Optional[str],
                              shared_report_code: Optional[str], technician_notified: bool) -> None:
        vs = json.dumps(vigilias_summary or {})

        def _fn(cur):
            cur.execute(
                "INSERT INTO bulletins (user_id, target_id, scan_id, bulletin_type, score, "
                "  previous_score, score_trend, vigilias_summary, top_action, "
                "  shared_report_code, technician_notified) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (user_id, target_id, scan_id, bulletin_type, score, previous_score,
                 score_trend, vs, top_action, shared_report_code, technician_notified))

        await asyncio.to_thread(self._run, _fn)

    async def get_last_bulletin(self, user_id: int, target_id: int) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(
                "SELECT id, score, sent_at FROM bulletins WHERE user_id = %s AND target_id = %s "
                "ORDER BY sent_at DESC LIMIT 1", (user_id, target_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    _BULLETIN_FREQ_PLANS = {"weekly": ("pro",), "monthly": ("free", "basic"), "daily": ("agency",)}
    _BULLETIN_FREQ_INTERVAL = {"weekly": "7 days", "monthly": "30 days", "daily": "1 day"}

    async def list_users_due_bulletin(self, frequency: str) -> List[Dict[str, Any]]:
        """Contas (+ sites) que precisam de boletim agora, pela frequência do plano
        (free→monthly, pro→weekly, agency→daily). Uma linha por (usuário, site); o worker
        agrupa. Só sites já escaneados. Respeita o intervalo mínimo (ou nunca enviado)."""
        plans = self._BULLETIN_FREQ_PLANS.get(frequency)
        interval = self._BULLETIN_FREQ_INTERVAL.get(frequency)
        if not plans or not interval:
            return []

        def _fn(cur):
            cur.execute(
                "SELECT u.id AS user_id, u.email, u.name, u.role, "
                "       us.target_id, t.url, t.domain, t.last_scan_score "
                "FROM users u "
                "JOIN user_sites us ON us.user_id = u.id "
                "JOIN targets t ON t.id = us.target_id "
                "LEFT JOIN subscriptions sub ON sub.account_id = u.id "
                "WHERE u.is_active = TRUE AND t.last_scan_score IS NOT NULL "
                "  AND COALESCE(sub.plan_id, u.plan, 'free') = ANY(%s) "
                "  AND NOT EXISTS (SELECT 1 FROM bulletins b WHERE b.user_id = u.id "
                "        AND b.target_id = us.target_id AND b.sent_at > NOW() - %s::interval) "
                "ORDER BY u.id",
                (list(plans), interval))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def bulletin_stats(self) -> Dict[str, Any]:
        def _fn(cur):
            out: Dict[str, Any] = {}
            cur.execute("SELECT COUNT(*), "
                        "  COUNT(*) FILTER (WHERE sent_at >= CURRENT_DATE), "
                        "  COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '7 days'), "
                        "  COUNT(*) FILTER (WHERE technician_notified) FROM bulletins")
            r = cur.fetchone()
            out["total"], out["today"], out["week"], out["tech_notified"] = (
                int(r[0]), int(r[1]), int(r[2]), int(r[3]))
            cur.execute("SELECT bulletin_type, COUNT(*) FROM bulletins GROUP BY 1")
            out["by_type"] = {row[0]: int(row[1]) for row in cur.fetchall()}
            return out

        return await asyncio.to_thread(self._run, _fn)

    async def list_technician_links_admin(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Vínculos técnicos (admin): dono, alvo, e-mail do técnico, status."""
        def _fn(cur):
            cur.execute(
                "SELECT tl.id, tl.status, tl.technician_email, tl.invited_at, tl.linked_at, "
                "       tl.last_access_at, ou.email AS owner_email, t.domain "
                "FROM technician_links tl JOIN users ou ON ou.id = tl.owner_user_id "
                "JOIN targets t ON t.id = tl.target_id ORDER BY tl.invited_at DESC LIMIT %s",
                (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def revoke_ownership(self, target_id: int) -> int:
        """Admin override: remove a marca de dono de todos os vínculos do alvo. Retorna
        quantos foram afetados. Não remove o vínculo (o usuário segue monitorando)."""
        def _fn(cur):
            cur.execute(
                "UPDATE user_sites SET is_owner = FALSE, verified_at = NULL, "
                "verification_method = NULL WHERE target_id = %s AND is_owner = TRUE",
                (target_id,))
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def ownership_stats(self) -> Dict[str, Any]:
        """Métricas de verificação de propriedade (KL-68 / analytics KL-57)."""
        def _fn(cur):
            out: Dict[str, Any] = {}
            cur.execute("SELECT COUNT(*) FROM user_sites WHERE is_owner = TRUE")
            out["verified_owners"] = int(cur.fetchone()[0])
            cur.execute("SELECT COALESCE(verification_method,'legacy') m, COUNT(*) "
                        "FROM user_sites WHERE is_owner = TRUE GROUP BY 1")
            out["by_method"] = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT status, COUNT(*) FROM ownership_verifications GROUP BY status")
            out["verifications"] = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM user_sites")
            total_sites = int(cur.fetchone()[0])
            out["total_monitored"] = total_sites
            out["owner_rate"] = round(out["verified_owners"] / total_sites, 3) if total_sites else 0.0
            return out

        return await asyncio.to_thread(self._run, _fn)

    async def list_user_sites_min(self) -> List[Dict[str, Any]]:
        """(id do vínculo, user_id, target_id, domínio) de TODOS os vínculos — para a
        limpeza de domínios bloqueados (KL-68). Domínio derivado de `domain`/`url`."""
        def _fn(cur):
            cur.execute(
                "SELECT us.id, us.user_id, us.target_id, "
                "       COALESCE(t.domain, t.url) AS domain "
                "FROM user_sites us JOIN targets t ON t.id = us.target_id")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def remove_user_sites_by_ids(self, ids: List[int]) -> int:
        """Remove vínculos user_sites por id (KL-68 — limpeza de domínios bloqueados)."""
        if not ids:
            return 0

        def _fn(cur):
            cur.execute("DELETE FROM user_sites WHERE id = ANY(%s)", (list(ids),))
            return cur.rowcount

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

    # --- KL-44 P2: vigílias (monitoramento contínuo) ----------------------- #

    async def get_recent_scans_with_checks(self, target_id: int, limit: int = 2
                                           ) -> List[Dict[str, Any]]:
        """Os N scans mais recentes de um alvo COM `checks_json` (mais recente 1º) —
        as vigílias de score/email/reputação comparam os 2 últimos scans."""
        def _fn(cur):
            cur.execute(
                "SELECT id, score, semaphore, checks_json, scanned_at FROM scans "
                "WHERE target_id = %s AND checks_json IS NOT NULL "
                "ORDER BY scanned_at DESC LIMIT %s", (target_id, limit))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def upsert_vigilia(self, user_id: int, site_domain: str, tipo: str,
                             next_check_at: Any = None) -> int:
        """Cria (ou re-habilita) uma vigília. Idempotente por (user, domínio, tipo).
        Re-habilitar (upgrade) reagenda o próximo check; uma vigília já ativa mantém o
        agendamento existente. Retorna o id."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO vigilias (user_id, site_domain, tipo, next_check_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (user_id, site_domain, tipo) DO UPDATE SET "
                "  enabled = TRUE, updated_at = NOW(), "
                "  next_check_at = CASE WHEN vigilias.enabled THEN vigilias.next_check_at "
                "                       ELSE EXCLUDED.next_check_at END "
                "RETURNING id",
                (user_id, site_domain.lower().strip(), tipo, next_check_at))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    # --- KL-44 P4: typosquatting/phishing --- #

    async def get_typosquat_monitored_domains(self) -> List[Dict[str, Any]]:
        """Domínios com vigília `phishing` ativa (Agency) — o discovery compara os novos
        CT domains contra estes. Poucos (dezenas). {target_id, user_id, domain}."""
        def _fn(cur):
            cur.execute(
                "SELECT DISTINCT t.id AS target_id, vg.user_id, vg.site_domain AS domain "
                "FROM vigilias vg JOIN targets t ON t.domain = vg.site_domain "
                "WHERE vg.tipo = 'phishing' AND vg.enabled = TRUE")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def record_typosquat_alert(self, target_id: int, user_id: int, suspicious_domain: str,
                                     similarity_type: str, distance: Optional[int]) -> bool:
        """Registra um domínio suspeito (idempotente por target+domínio). True se novo."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO typosquat_alerts (target_id, user_id, suspicious_domain, "
                "  similarity_type, distance) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (target_id, suspicious_domain) DO NOTHING",
                (target_id, user_id, suspicious_domain.lower().strip(), similarity_type, distance))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def get_pending_typosquats(self, target_id: int) -> List[Dict[str, Any]]:
        """Suspeitos ainda não notificados e não descartados (para a vigília phishing avisar)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, suspicious_domain, similarity_type, distance FROM typosquat_alerts "
                "WHERE target_id = %s AND notified = FALSE AND dismissed = FALSE "
                "ORDER BY detected_at DESC", (target_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def mark_typosquats_notified(self, ids: List[int]) -> None:
        if not ids:
            return
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            "UPDATE typosquat_alerts SET notified = TRUE WHERE id = ANY(%s)", (list(ids),)))

    async def list_typosquat_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Alertas de typosquat (admin/MCP): domínio suspeito, tipo, dono, estado."""
        def _fn(cur):
            cur.execute(
                "SELECT ta.id, ta.suspicious_domain, ta.similarity_type, ta.distance, "
                "       ta.detected_at, ta.notified, ta.dismissed, t.domain, u.email AS owner_email "
                "FROM typosquat_alerts ta JOIN targets t ON t.id = ta.target_id "
                "JOIN users u ON u.id = ta.user_id ORDER BY ta.detected_at DESC LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def typosquat_stats(self) -> Dict[str, Any]:
        def _fn(cur):
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE dismissed), "
                        "COUNT(*) FILTER (WHERE notified) FROM typosquat_alerts")
            r = cur.fetchone()
            return {"total": int(r[0]), "dismissed": int(r[1]), "notified": int(r[2])}

        return await asyncio.to_thread(self._run, _fn)

    async def disable_user_vigilias_except(self, user_id: int, keep_types: List[str]) -> int:
        """Desabilita (não deleta) as vigílias do usuário cujo tipo NÃO está em
        `keep_types` — usado no downgrade de plano. Retorna quantas foram desabilitadas."""
        def _fn(cur):
            if keep_types:
                cur.execute(
                    "UPDATE vigilias SET enabled = FALSE, updated_at = NOW() "
                    "WHERE user_id = %s AND enabled = TRUE AND tipo <> ALL(%s)",
                    (user_id, list(keep_types)))
            else:
                cur.execute(
                    "UPDATE vigilias SET enabled = FALSE, updated_at = NOW() "
                    "WHERE user_id = %s AND enabled = TRUE", (user_id,))
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def get_due_vigilias(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Vigílias habilitadas com `next_check_at` vencido (ou nulo), da mais atrasada
        para a menos, com o e-mail do dono. **Exclui `uptime`** (KL-44 P4 — roda no loop
        curto). O worker faz o enforcement de plano."""
        def _fn(cur):
            cur.execute(
                "SELECT vg.id, vg.user_id, vg.site_domain, vg.tipo, vg.last_status, "
                "       vg.last_data, vg.alert_count, u.email AS user_email "
                "FROM vigilias vg "
                "JOIN users u ON u.id = vg.user_id AND u.is_active = TRUE "
                "WHERE vg.enabled = TRUE AND vg.tipo <> 'uptime' "
                "  AND (vg.next_check_at IS NULL OR vg.next_check_at <= NOW()) "
                "ORDER BY vg.next_check_at ASC NULLS FIRST LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_due_uptime_vigilias(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Vigílias de uptime vencidas (KL-44 P4) + o intervalo do plano (Pro=30/Agency=5)."""
        def _fn(cur):
            cur.execute(
                "SELECT vg.id, vg.user_id, vg.site_domain, vg.tipo, vg.last_status, "
                "       vg.last_data, vg.alert_count, u.email AS user_email, "
                "       COALESCE(p.uptime_interval_minutes, 30) AS interval_minutes "
                "FROM vigilias vg "
                "JOIN users u ON u.id = vg.user_id AND u.is_active = TRUE "
                "LEFT JOIN subscriptions sub ON sub.account_id = vg.user_id "
                "LEFT JOIN plans p ON p.id = COALESCE(sub.plan_id, u.plan) "
                "WHERE vg.enabled = TRUE AND vg.tipo = 'uptime' "
                "  AND (vg.next_check_at IS NULL OR vg.next_check_at <= NOW()) "
                "ORDER BY vg.next_check_at ASC NULLS FIRST LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def update_vigilia_after_check(self, vigilia_id: int, last_status: str,
                                         last_data: dict, next_check_at: Any,
                                         alerted: bool = False) -> None:
        """Grava o resultado de um check: status, dados, próximo agendamento; se gerou
        alerta, incrementa `alert_count` e marca `last_alert_at`."""
        def _fn(cur):
            cur.execute(
                "UPDATE vigilias SET last_check_at = NOW(), last_status = %s, "
                "  last_data = %s, next_check_at = %s, updated_at = NOW(), "
                "  alert_count = alert_count + %s, "
                "  last_alert_at = CASE WHEN %s THEN NOW() ELSE last_alert_at END "
                "WHERE id = %s",
                (last_status, json.dumps(last_data or {}), next_check_at,
                 1 if alerted else 0, alerted, vigilia_id))

        return await asyncio.to_thread(self._run, _fn)

    async def create_vigilia_alert(self, vigilia_id: int, user_id: int, site_domain: str,
                                   tipo: str, severity: str, title: str, message: str,
                                   action_text: Optional[str] = None,
                                   data: Optional[dict] = None) -> int:
        """Registra um alerta de vigília. Retorna o id."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO vigilia_alerts (vigilia_id, user_id, site_domain, tipo, "
                "  severity, title, message, action_text, data) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (vigilia_id, user_id, site_domain.lower().strip(), tipo, severity, title,
                 message, action_text, json.dumps(data or {})))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def mark_vigilia_alert_sent(self, alert_id: int, email_id: Optional[str]) -> None:
        def _fn(cur):
            cur.execute("UPDATE vigilia_alerts SET email_sent = TRUE, email_id = %s "
                        "WHERE id = %s", (email_id, alert_id))

        return await asyncio.to_thread(self._run, _fn)

    async def list_vigilias(self, tipo: Optional[str] = None, status: Optional[str] = None,
                            user_id: Optional[int] = None, domain: Optional[str] = None,
                            limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Lista vigílias (admin) com o e-mail do dono. Filtros combináveis."""
        def _fn(cur):
            where, params = [], []
            if tipo:
                where.append("vg.tipo = %s"); params.append(tipo)
            if status:
                where.append("vg.last_status = %s"); params.append(status)
            if user_id is not None:
                where.append("vg.user_id = %s"); params.append(user_id)
            if domain:
                where.append("vg.site_domain ILIKE %s"); params.append(f"%{domain.lower().strip()}%")
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            cur.execute(
                "SELECT vg.id, vg.user_id, vg.site_domain, vg.tipo, vg.enabled, "
                "  vg.last_check_at, vg.next_check_at, vg.last_status, vg.last_data, "
                "  vg.alert_count, vg.last_alert_at, u.email AS user_email "
                f"FROM vigilias vg JOIN users u ON u.id = vg.user_id {clause} "
                "ORDER BY vg.last_check_at DESC NULLS LAST, vg.id DESC LIMIT %s OFFSET %s",
                params)
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_vigilia(self, vigilia_id: int) -> Optional[Dict[str, Any]]:
        """Detalhe de uma vigília + histórico de alertas (mais recente 1º)."""
        def _fn(cur):
            cur.execute(
                "SELECT vg.*, u.email AS user_email FROM vigilias vg "
                "JOIN users u ON u.id = vg.user_id WHERE vg.id = %s", (vigilia_id,))
            rows = self._rows_to_dicts(cur)
            if not rows:
                return None
            vig = rows[0]
            cur.execute(
                "SELECT id, severity, title, message, action_text, data, email_sent, "
                "  read_at, created_at FROM vigilia_alerts WHERE vigilia_id = %s "
                "ORDER BY created_at DESC LIMIT 50", (vigilia_id,))
            vig["alerts"] = self._rows_to_dicts(cur)
            return vig

        return await asyncio.to_thread(self._run, _fn)

    async def list_vigilia_alerts(self, tipo: Optional[str] = None,
                                  severity: Optional[str] = None,
                                  user_id: Optional[int] = None, limit: int = 50,
                                  offset: int = 0) -> List[Dict[str, Any]]:
        """Lista alertas de vigília (admin) com o e-mail do dono."""
        def _fn(cur):
            where, params = [], []
            if tipo:
                where.append("va.tipo = %s"); params.append(tipo)
            if severity:
                where.append("va.severity = %s"); params.append(severity)
            if user_id is not None:
                where.append("va.user_id = %s"); params.append(user_id)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            cur.execute(
                "SELECT va.id, va.vigilia_id, va.user_id, va.site_domain, va.tipo, "
                "  va.severity, va.title, va.message, va.action_text, va.email_sent, "
                "  va.read_at, va.created_at, u.email AS user_email "
                f"FROM vigilia_alerts va JOIN users u ON u.id = va.user_id {clause} "
                "ORDER BY va.created_at DESC LIMIT %s OFFSET %s", params)
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def vigilia_stats(self) -> Dict[str, Any]:
        """Contagem por tipo, por status e alertas hoje/7d/30d."""
        def _fn(cur):
            cur.execute("SELECT tipo, COUNT(*) FROM vigilias WHERE enabled = TRUE GROUP BY tipo")
            by_type = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT last_status, COUNT(*) FROM vigilias WHERE enabled = TRUE "
                        "GROUP BY last_status")
            by_status = {(r[0] or "ok"): int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM vigilias WHERE enabled = TRUE")
            total = int(cur.fetchone()[0])
            cur.execute(
                "SELECT "
                "  COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW())), "
                "  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days'), "
                "  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '30 days') "
                "FROM vigilia_alerts")
            a_today, a_7d, a_30d = cur.fetchone()
            return {"total_vigilias": total, "by_type": by_type, "by_status": by_status,
                    "alerts_today": int(a_today), "alerts_7d": int(a_7d),
                    "alerts_30d": int(a_30d)}

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_vigilias(self, user_id: int) -> List[Dict[str, Any]]:
        """Vigílias ativas do próprio usuário (dashboard de conta)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, site_domain, tipo, enabled, last_check_at, next_check_at, "
                "  last_status, last_data, alert_count, last_alert_at "
                "FROM vigilias WHERE user_id = %s AND enabled = TRUE "
                "ORDER BY site_domain, tipo", (user_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_user_vigilia_alerts(self, user_id: int, limit: int = 50
                                      ) -> List[Dict[str, Any]]:
        """Alertas de vigília do próprio usuário (mais recente 1º)."""
        def _fn(cur):
            cur.execute(
                "SELECT id, vigilia_id, site_domain, tipo, severity, title, message, "
                "  action_text, read_at, created_at FROM vigilia_alerts "
                "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_all_monitored_sites(self) -> List[Dict[str, Any]]:
        """(user_id, site_domain) distintos de contas ativas com sites monitorados —
        para o seed de vigílias. Uma linha por (usuário, domínio)."""
        def _fn(cur):
            cur.execute(
                "SELECT DISTINCT us.user_id, t.domain AS site_domain "
                "FROM user_sites us "
                "JOIN users u ON u.id = us.user_id AND u.is_active = TRUE "
                "JOIN targets t ON t.id = us.target_id "
                "WHERE t.domain IS NOT NULL AND t.domain <> '' "
                "ORDER BY us.user_id")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    # --- KL-44: admin_settings (config ao vivo + senha/token) --------------- #

    async def get_admin_setting(self, key: str) -> Optional[str]:
        """Valor do override no banco, ou None se não houver."""
        def _fn(cur):
            cur.execute("SELECT value FROM admin_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

        return await asyncio.to_thread(self._run, _fn)

    async def upsert_admin_setting(self, key: str, value: str,
                                   updated_by: str = "admin") -> None:
        def _fn(cur):
            cur.execute(
                "INSERT INTO admin_settings (key, value, updated_by, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "  updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                (key, value, updated_by))

        return await asyncio.to_thread(self._run, _fn)

    async def delete_admin_setting(self, key: str) -> bool:
        def _fn(cur):
            cur.execute("DELETE FROM admin_settings WHERE key = %s", (key,))
            return cur.rowcount > 0

        return await asyncio.to_thread(self._run, _fn)

    async def list_admin_settings(self) -> Dict[str, Dict[str, Any]]:
        """Todos os overrides do banco, indexados por key (com metadados de auditoria)."""
        def _fn(cur):
            cur.execute("SELECT key, value, updated_by, updated_at FROM admin_settings")
            return {r[0]: {"value": r[1], "updated_by": r[2],
                           "updated_at": r[3].isoformat() if r[3] else None}
                    for r in cur.fetchall()}

        return await asyncio.to_thread(self._run, _fn)

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Resolução com prioridade: banco (`admin_settings`) → `.env` (os.environ) →
        `default`. **Fail-open**: qualquer erro no banco cai para o env (nunca derruba um
        worker por config). Usado pela API e pelos workers (que releem por ciclo)."""
        try:
            row = await self.get_admin_setting(key)
        except Exception as exc:  # noqa: BLE001 - DB fora → usa o env
            print(f"[settings] get_setting({key}) via env (db erro: {exc!r})", flush=True)
            row = None
        if row is not None:
            return row
        return os.environ.get(key, default)

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
        sem_contato e landings desligadas (`public_visible=FALSE`, KL-56).
        `domain` + `last_scan_at` (lastmod), mais recente primeiro."""
        def _fn(cur):
            cur.execute(
                "SELECT t.domain, t.last_scan_at FROM targets t "
                "JOIN site_profile sp ON sp.target_id = t.id "
                "WHERE t.status IN ('scanned', 'alerted') AND t.last_scan_score IS NOT NULL "
                "  AND t.domain IS NOT NULL AND t.domain <> '' "
                "  AND COALESCE(sp.public_visible, TRUE) = TRUE "  # KL-56: só landings ligadas
                "ORDER BY t.last_scan_at DESC NULLS LAST LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_users_with_sites(self) -> List[Dict[str, Any]]:
        """Contas de usuário (KL-51 f3) + os sites vinculados (via `user_sites`), para a
        Gestão de Clientes no painel admin. 2 queries numa conexão (evita N+1)."""
        def _fn(cur):
            # KL-69: junta a assinatura (status/trial) numa página unificada (sem N+1).
            cur.execute(
                "SELECT u.id, u.email, u.name, u.plan, u.max_sites, u.created_at, "
                "       u.last_login_at, u.is_active, "
                "       sub.status AS sub_status, sub.plan_id AS sub_plan, sub.trial_ends_at "
                "FROM users u LEFT JOIN subscriptions sub ON sub.account_id = u.id "
                "ORDER BY u.created_at DESC")
            users = self._rows_to_dicts(cur)
            cur.execute(
                "SELECT us.user_id, us.is_owner, us.verified_at, us.verification_method, "
                "       us.added_at, t.id AS target_id, t.url, t.domain, "
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

    async def dashboard_summary(self) -> Dict[str, Any]:
        """Totalizadores do painel admin (KL-57): alvos, scans (manual/automatizado),
        perfis/landings, contas e alertas — em poucas queries numa conexão (sem N+1,
        sem full scan caro). `manual` = scan com `scanned_by_email` (veio do site
        público, alguém digitou a URL); `automated` = sem e-mail (scan worker)."""
        def _fn(cur):
            # alvos
            cur.execute("SELECT status, COUNT(*) FROM targets GROUP BY status")
            by_status = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM targets WHERE last_scan_score = 100")
            score_100 = int(cur.fetchone()[0])

            # scans: total, média, manual vs automatizado, hoje, 7 dias
            cur.execute(
                "SELECT COUNT(*), COALESCE(ROUND(AVG(score)), 0), "
                "  COUNT(*) FILTER (WHERE scanned_by_email IS NOT NULL), "
                "  COUNT(*) FILTER (WHERE scanned_by_email IS NULL), "
                "  COUNT(*) FILTER (WHERE scanned_at >= date_trunc('day', NOW())), "
                "  COUNT(*) FILTER (WHERE scanned_at > NOW() - INTERVAL '7 days') "
                "FROM scans")
            s_total, s_avg, s_manual, s_auto, s_today, s_7d = cur.fetchone()
            cur.execute("SELECT semaphore, COUNT(*) FROM scans GROUP BY semaphore")
            by_semaphore = {r[0]: int(r[1]) for r in cur.fetchall()}

            # perfis / landings públicas
            cur.execute(
                "SELECT COUNT(*), "
                "  COUNT(*) FILTER (WHERE COALESCE(public_visible, TRUE)), "
                "  COUNT(*) FILTER (WHERE public_visible = FALSE), "
                "  COUNT(*) FILTER (WHERE description IS NOT NULL AND description <> '') "
                "FROM site_profile")
            p_total, p_public, p_hidden, p_ai = cur.fetchone()
            cur.execute("SELECT COUNT(DISTINCT target_id) FROM target_classifications")
            p_cnae = int(cur.fetchone()[0])

            # contas de usuário
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE)) "
                        "FROM users")
            u_total, u_active = cur.fetchone()
            cur.execute("SELECT COUNT(DISTINCT target_id) FROM user_sites")
            u_sites = int(cur.fetchone()[0])

            # alertas
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE status = 'sent'), "
                "  COUNT(*) FILTER (WHERE status = 'sent' "
                "                   AND sent_at >= date_trunc('day', NOW())) "
                "FROM alert_log")
            a_total, a_today = cur.fetchone()

            return {
                "targets": {"total": sum(by_status.values()), "by_status": by_status,
                            "score_100": score_100},
                "scans": {"total": int(s_total), "avg_score": int(s_avg),
                          "by_semaphore": by_semaphore, "manual": int(s_manual),
                          "automated": int(s_auto), "today": int(s_today),
                          "last_7_days": int(s_7d)},
                "profiles": {"total": int(p_total), "public": int(p_public),
                             "hidden": int(p_hidden), "with_ai": int(p_ai),
                             "with_cnae": p_cnae},
                "accounts": {"total": int(u_total), "active": int(u_active),
                             "sites_monitored": u_sites},
                "alerts": {"total": int(a_total), "today": int(a_today)},
            }

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
        distinct_url: bool = False, offset: int = 0,
        from_date: Optional[str] = None, to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lista scans (mais recentes primeiro). ``distinct_url=True`` retorna apenas
        o scan MAIS RECENTE de cada URL — evita 3 linhas do mesmo site na "atividade
        recente" quando ele foi escaneado várias vezes (Fix pós-KL-27). KL-56:
        ``offset`` (paginação real da página Scans) + ``from_date``/``to_date``
        (filtro por período, `YYYY-MM-DD`; `to_date` é inclusivo)."""
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
            if from_date:
                where.append("scanned_at >= %s::date")
                params.append(from_date)
            if to_date:  # inclusivo: cobre o dia inteiro de to_date
                where.append("scanned_at < (%s::date + INTERVAL '1 day')")
                params.append(to_date)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.extend([limit, offset])
            if distinct_url:
                # DISTINCT ON (url) pega o último por URL; reordena por data e limita.
                cur.execute(
                    f"SELECT * FROM (SELECT DISTINCT ON (url) {cols} FROM scans {clause} "
                    f"ORDER BY url, scanned_at DESC) t ORDER BY scanned_at DESC "
                    f"LIMIT %s OFFSET %s",
                    params,
                )
            else:
                cur.execute(
                    f"SELECT {cols} FROM scans {clause} ORDER BY scanned_at DESC "
                    f"LIMIT %s OFFSET %s",
                    params,
                )
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    # --- dashboard admin (KL-14) ------------------------------------------- #

    async def scan_stats(self) -> Dict[str, Any]:
        """Total, média, semáforo, **manual vs automatizado**, hoje/7d e score 100
        (fix MCP: espelha `dashboard_summary().scans` p/ MCP e painel baterem)."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), COALESCE(ROUND(AVG(score)), 0), "
                "  COUNT(*) FILTER (WHERE scanned_by_email IS NOT NULL), "
                "  COUNT(*) FILTER (WHERE scanned_by_email IS NULL), "
                "  COUNT(*) FILTER (WHERE scanned_at >= date_trunc('day', NOW())), "
                "  COUNT(*) FILTER (WHERE scanned_at > NOW() - INTERVAL '7 days'), "
                "  COUNT(*) FILTER (WHERE score = 100) "
                "FROM scans")
            total, avg, manual, auto, today, d7, s100 = cur.fetchone()
            cur.execute("SELECT semaphore, COUNT(*) FROM scans GROUP BY semaphore")
            by_semaphore = {r[0]: int(r[1]) for r in cur.fetchall()}
            return {"total": int(total), "avg_score": int(avg), "by_semaphore": by_semaphore,
                    "manual": int(manual), "automated": int(auto), "today": int(today),
                    "last_7_days": int(d7), "score_100_count": int(s100)}

        return await asyncio.to_thread(self._run, _fn)

    async def last_scan_at(self) -> Optional[str]:
        """Timestamp (ISO) do scan mais recente na tabela `scans` — a MESMA fonte que a
        página Scans do painel (`list_scans`). Usado no `get_system_status` para MCP e
        painel mostrarem o mesmo dado (fix da divergência: o heartbeat do worker avança
        além do banco — scans que não persistem, enrich pós-scan)."""
        def _fn(cur):
            cur.execute("SELECT MAX(scanned_at) FROM scans")
            v = cur.fetchone()[0]
            return v.isoformat() if v else None

        return await asyncio.to_thread(self._run, _fn)

    async def profile_counts(self) -> Dict[str, int]:
        """Contagem de perfis (`site_profile`): total, com descrição (IA rodou), com
        CNAE (`target_classifications`) e visíveis (`public_visible`) — fix MCP."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), "
                "  COUNT(*) FILTER (WHERE description IS NOT NULL AND description <> ''), "
                "  COUNT(*) FILTER (WHERE COALESCE(public_visible, TRUE)) "
                "FROM site_profile")
            total, with_desc, public = cur.fetchone()
            cur.execute("SELECT COUNT(DISTINCT target_id) FROM target_classifications")
            with_cnae = int(cur.fetchone()[0])
            return {"total": int(total), "with_description": int(with_desc),
                    "with_cnae": with_cnae, "public_visible": int(public)}

        return await asyncio.to_thread(self._run, _fn)

    # --- leads (KL-61) — PQL scoring ----------------------------------------- #

    @staticmethod
    def _lead_domain(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        try:
            from urllib.parse import urlparse
            d = (urlparse(url if "://" in url else "https://" + url).hostname or url).lower()
            return d[4:] if d.startswith("www.") else d
        except Exception:  # noqa: BLE001
            return url

    @staticmethod
    def _recalc_lead_row(cur, lead_id: int) -> None:
        """Recalcula lead_score + classification (SEMPRE derivada) na mesma transação."""
        from api.lead_scoring import calculate_lead_score
        cur.execute(
            "SELECT total_scans, urls_scanned, worst_score, has_account, has_monitoring, "
            "is_corporate_email, last_activity_at FROM scan_leads WHERE id = %s", (lead_id,))
        row = cur.fetchone()
        if not row:
            return
        total_scans, urls, worst, has_acc, has_mon, is_corp, last_act = row
        score, classification = calculate_lead_score({
            "total_scans": total_scans, "distinct_urls": len(urls or []),
            "worst_score": worst, "has_account": has_acc, "has_monitoring": has_mon,
            "is_corporate_email": is_corp, "last_activity_at": last_act})
        cur.execute("UPDATE scan_leads SET lead_score = %s, classification = %s, "
                    "updated_at = NOW() WHERE id = %s", (score, classification, lead_id))

    async def upsert_scan_lead(self, email: str, url: str, score: Optional[int],
                               sector: Optional[str] = None,
                               platform: Optional[str] = None) -> None:
        """Cria/atualiza o lead após um scan verificado (KL-61). Idempotente por e-mail
        (lowercase). Agrega e recalcula o score. Best-effort (o chamador é fire-and-forget)."""
        from api.lead_scoring import is_corporate_email
        email = (email or "").lower().strip()
        if not email or "@" not in email:
            return
        domain = self._lead_domain(url)
        is_corp = is_corporate_email(email)

        def _fn(cur):
            cur.execute(
                """
                INSERT INTO scan_leads (email, first_scan_at, last_activity_at, total_scans,
                    urls_scanned, domains_scanned, best_score, worst_score, last_score,
                    last_domain, sector, platform, is_corporate_email, source, updated_at)
                VALUES (%(email)s, NOW(), NOW(), 1,
                    CASE WHEN %(url)s IS NULL THEN '{}'::text[] ELSE ARRAY[%(url)s] END,
                    CASE WHEN %(domain)s IS NULL THEN '{}'::text[] ELSE ARRAY[%(domain)s] END,
                    %(score)s, %(score)s, %(score)s, %(domain)s, %(sector)s, %(platform)s,
                    %(is_corp)s, 'scan', NOW())
                ON CONFLICT (email) DO UPDATE SET
                    total_scans = scan_leads.total_scans + 1,
                    urls_scanned = CASE WHEN %(url)s IS NULL THEN scan_leads.urls_scanned
                        ELSE ARRAY(SELECT DISTINCT unnest(scan_leads.urls_scanned || ARRAY[%(url)s])) END,
                    domains_scanned = CASE WHEN %(domain)s IS NULL THEN scan_leads.domains_scanned
                        ELSE ARRAY(SELECT DISTINCT unnest(scan_leads.domains_scanned || ARRAY[%(domain)s])) END,
                    best_score = CASE WHEN %(score)s IS NULL THEN scan_leads.best_score
                        ELSE GREATEST(COALESCE(scan_leads.best_score, %(score)s), %(score)s) END,
                    worst_score = CASE WHEN %(score)s IS NULL THEN scan_leads.worst_score
                        ELSE LEAST(COALESCE(scan_leads.worst_score, %(score)s), %(score)s) END,
                    last_score = COALESCE(%(score)s, scan_leads.last_score),
                    last_domain = COALESCE(%(domain)s, scan_leads.last_domain),
                    last_activity_at = NOW(),
                    sector = COALESCE(scan_leads.sector, %(sector)s),
                    platform = COALESCE(scan_leads.platform, %(platform)s),
                    is_corporate_email = %(is_corp)s,
                    updated_at = NOW()
                RETURNING id
                """,
                {"email": email, "url": url, "domain": domain, "score": score,
                 "sector": sector, "platform": platform, "is_corp": is_corp})
            self._recalc_lead_row(cur, cur.fetchone()[0])

        await asyncio.to_thread(self._run, _fn)

    async def set_lead_account(self, email: str, account_id: int) -> None:
        """Marca o lead como tendo conta (KL-61 3b). Cria um lead mínimo se ainda não
        existe (signup sem lead prévio). Recalcula o score (+conta)."""
        from api.lead_scoring import is_corporate_email
        email = (email or "").lower().strip()
        if not email:
            return
        is_corp = is_corporate_email(email)

        def _fn(cur):
            cur.execute(
                "INSERT INTO scan_leads (email, has_account, account_id, source, "
                "  first_scan_at, last_activity_at, is_corporate_email, updated_at) "
                "VALUES (%s, TRUE, %s, 'account', NOW(), NOW(), %s, NOW()) "
                "ON CONFLICT (email) DO UPDATE SET has_account = TRUE, "
                "  account_id = EXCLUDED.account_id, last_activity_at = NOW(), updated_at = NOW() "
                "RETURNING id",
                (email, account_id, is_corp))
            self._recalc_lead_row(cur, cur.fetchone()[0])

        await asyncio.to_thread(self._run, _fn)

    async def set_lead_monitoring(self, email: str) -> None:
        """Marca o lead como tendo monitoramento (KL-61 3c). Só se o lead existe."""
        email = (email or "").lower().strip()
        if not email:
            return

        def _fn(cur):
            cur.execute("UPDATE scan_leads SET has_monitoring = TRUE, last_activity_at = NOW(), "
                        "updated_at = NOW() WHERE email = %s RETURNING id", (email,))
            row = cur.fetchone()
            if row:
                self._recalc_lead_row(cur, row[0])

        await asyncio.to_thread(self._run, _fn)

    _LEAD_SORTS = {
        "lead_score": "lead_score DESC, last_activity_at DESC NULLS LAST",
        "last_activity_at": "last_activity_at DESC NULLS LAST",
        "total_scans": "total_scans DESC",
        "worst_score": "worst_score ASC NULLS LAST",
    }

    async def list_leads(self, classification: Optional[str] = None,
                         sector: Optional[str] = None, has_account: Optional[bool] = None,
                         search: Optional[str] = None, sort: str = "lead_score",
                         limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """Lista paginada de leads + total + contagem por classificação (KL-61)."""
        order = self._LEAD_SORTS.get(sort, self._LEAD_SORTS["lead_score"])

        def _fn(cur):
            base, params = [], []
            if sector:
                base.append("sector = %s")
                params.append(sector)
            if has_account is not None:
                base.append("has_account = %s")
                params.append(bool(has_account))
            if search:
                like = f"%{search.lower().strip()}%"
                base.append("(LOWER(email) LIKE %s OR LOWER(COALESCE(last_domain, '')) LIKE %s "
                            "OR EXISTS (SELECT 1 FROM unnest(domains_scanned) d WHERE LOWER(d) LIKE %s))")
                params.extend([like, like, like])
            base_clause = ("WHERE " + " AND ".join(base)) if base else ""
            cur.execute(f"SELECT classification, COUNT(*) FROM scan_leads {base_clause} "
                        f"GROUP BY classification", params)
            by_class = {r[0]: int(r[1]) for r in cur.fetchall()}
            for c in ("cold", "warm", "hot", "pql"):
                by_class.setdefault(c, 0)
            where, wparams = list(base), list(params)
            if classification in ("cold", "warm", "hot", "pql"):
                where.append("classification = %s")
                wparams.append(classification)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            cur.execute(f"SELECT COUNT(*) FROM scan_leads {clause}", wparams)
            total = int(cur.fetchone()[0])
            cur.execute(f"SELECT * FROM scan_leads {clause} ORDER BY {order} "
                        f"LIMIT %s OFFSET %s", wparams + [limit, offset])
            return {"leads": self._rows_to_dicts(cur), "total": total,
                    "by_classification": by_class}

        return await asyncio.to_thread(self._run, _fn)

    async def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Detalhe do lead + os scans desse e-mail (JOIN por `scanned_by_email`)."""
        def _fn(cur):
            cur.execute("SELECT * FROM scan_leads WHERE id = %s", (lead_id,))
            rows = self._rows_to_dicts(cur)
            if not rows:
                return None
            lead = rows[0]
            cur.execute(
                "SELECT s.id, s.url, t.domain, s.score, s.semaphore, s.source, s.scanned_at "
                "FROM scans s LEFT JOIN targets t ON t.id = s.target_id "
                "WHERE LOWER(s.scanned_by_email) = %s ORDER BY s.scanned_at DESC LIMIT 50",
                ((lead.get("email") or "").lower(),))
            lead["scans"] = self._rows_to_dicts(cur)
            return lead

        return await asyncio.to_thread(self._run, _fn)

    async def lead_stats(self) -> Dict[str, Any]:
        """Totalizadores de leads + dados de analytics (KL-57): conversão por setor,
        setores com maior dor (menor avg worst_score), taxa PQL."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE has_account), "
                "  COUNT(*) FILTER (WHERE has_monitoring), COALESCE(ROUND(AVG(lead_score)), 0), "
                "  COUNT(*) FILTER (WHERE is_corporate_email), "
                "  COUNT(*) FILTER (WHERE total_scans >= 2), "
                "  COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW())), "
                "  COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') "
                "FROM scan_leads")
            total, w_acc, w_mon, avg, corp, multi, today, d7 = cur.fetchone()
            cur.execute("SELECT classification, COUNT(*) FROM scan_leads GROUP BY classification")
            by_class = {r[0]: int(r[1]) for r in cur.fetchall()}
            for c in ("cold", "warm", "hot", "pql"):
                by_class.setdefault(c, 0)
            cur.execute("SELECT sector, COUNT(*) FROM scan_leads "
                        "WHERE sector IS NOT NULL AND sector <> '' GROUP BY sector "
                        "ORDER BY COUNT(*) DESC LIMIT 5")
            top_sectors = [{"sector": r[0], "count": int(r[1])} for r in cur.fetchall()]
            cur.execute("SELECT sector, COUNT(*) FILTER (WHERE has_account) FROM scan_leads "
                        "WHERE sector IS NOT NULL AND sector <> '' GROUP BY sector "
                        "HAVING COUNT(*) FILTER (WHERE has_account) > 0 ORDER BY 2 DESC LIMIT 5")
            conv_by_sector = [{"sector": r[0], "accounts": int(r[1])} for r in cur.fetchall()]
            cur.execute("SELECT sector, COALESCE(ROUND(AVG(worst_score)), 0) FROM scan_leads "
                        "WHERE worst_score IS NOT NULL AND sector IS NOT NULL AND sector <> '' "
                        "GROUP BY sector HAVING COUNT(*) >= 2 ORDER BY 2 ASC LIMIT 5")
            pain_sectors = [{"sector": r[0], "avg_worst_score": int(r[1])} for r in cur.fetchall()]
            total = int(total)
            return {
                "total": total, "by_classification": by_class, "with_account": int(w_acc),
                "with_monitoring": int(w_mon), "avg_lead_score": int(avg),
                "corporate_emails": int(corp), "multi_scan": int(multi),
                "top_sectors": top_sectors, "today": int(today), "last_7_days": int(d7),
                "conversion_by_sector": conv_by_sector, "pain_sectors": pain_sectors,
                "pql_rate": round(100.0 * by_class.get("pql", 0) / total, 1) if total else 0.0,
            }

        return await asyncio.to_thread(self._run, _fn)

    async def lead_funnel(self) -> Dict[str, Any]:
        """Funil de conversão dos leads (KL-61): verificado → scan → conta → monitoramento."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE total_scans >= 1), "
                "  COUNT(*) FILTER (WHERE has_account), COUNT(*) FILTER (WHERE has_monitoring) "
                "FROM scan_leads")
            return cur.fetchone()

        total, scanned, acc, mon = await asyncio.to_thread(self._run, _fn)
        total, scanned, acc, mon = int(total), int(scanned), int(acc), int(mon)
        return {
            "email_verified": total, "scan_completed": scanned,
            "account_created": acc, "monitoring_added": mon,
            "conversion_rate_scan_to_account": round(100.0 * acc / total, 1) if total else 0.0,
            "conversion_rate_account_to_monitoring": round(100.0 * mon / acc, 1) if acc else 0.0,
        }

    async def update_lead(self, lead_id: int, tags=None, notes=None,
                          opted_out=None) -> bool:
        """Atualiza campos MANUAIS (tags/notes/opted_out) — nunca lead_score/classification
        (sempre calculados). Recalcula o score depois. Retorna True se afetou."""
        def _fn(cur):
            sets, params = [], []
            if tags is not None:
                sets.append("tags = %s")
                params.append(list(tags))
            if notes is not None:
                sets.append("notes = %s")
                params.append(notes)
            if opted_out is not None:
                sets.append("opted_out = %s")
                params.append(bool(opted_out))
            if not sets:
                cur.execute("SELECT id FROM scan_leads WHERE id = %s", (lead_id,))
                return cur.fetchone() is not None
            sets.append("updated_at = NOW()")
            cur.execute(f"UPDATE scan_leads SET {', '.join(sets)} WHERE id = %s RETURNING id",
                        params + [lead_id])
            row = cur.fetchone()
            if row:
                self._recalc_lead_row(cur, row[0])
            return row is not None

        return await asyncio.to_thread(self._run, _fn)

    async def recalculate_all_leads(self) -> int:
        """Recalcula o score+classificação de TODOS os leads (KL-61). Retorna a contagem."""
        def _fn(cur):
            cur.execute("SELECT id FROM scan_leads")
            ids = [r[0] for r in cur.fetchall()]
            for lid in ids:
                self._recalc_lead_row(cur, lid)
            return len(ids)

        return await asyncio.to_thread(self._run, _fn)

    async def backfill_leads(self) -> int:
        """Popula `scan_leads` a partir dos scans existentes (KL-61). Idempotente
        (ON CONFLICT DO UPDATE recomputa dos scans — a fonte da verdade); preserva os
        campos manuais (tags/notes/opted_out). Retorna a contagem de leads processados."""
        from api.lead_scoring import is_corporate_email, calculate_lead_score
        _AGG = """
            WITH scan_data AS (
                SELECT LOWER(s.scanned_by_email) AS email,
                    COUNT(*) AS total_scans,
                    array_agg(DISTINCT s.url) AS urls_scanned,
                    array_remove(array_agg(DISTINCT t.domain), NULL) AS domains_scanned,
                    MAX(s.score) AS best_score, MIN(s.score) AS worst_score,
                    (array_agg(s.score ORDER BY s.scanned_at DESC))[1] AS last_score,
                    (array_agg(t.domain ORDER BY s.scanned_at DESC))[1] AS last_domain,
                    (array_agg(t.sector ORDER BY s.scanned_at DESC))[1] AS sector,
                    (array_agg(t.platform ORDER BY s.scanned_at DESC))[1] AS platform,
                    MIN(s.scanned_at) AS first_scan_at, MAX(s.scanned_at) AS last_activity_at
                FROM scans s LEFT JOIN targets t ON s.target_id = t.id
                WHERE s.scanned_by_email IS NOT NULL AND s.scanned_by_email <> ''
                GROUP BY LOWER(s.scanned_by_email)
            )
            SELECT sd.*, u.id AS account_id, (u.id IS NOT NULL) AS has_account,
                COALESCE(EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id), FALSE)
                    AS has_monitoring
            FROM scan_data sd LEFT JOIN users u ON LOWER(u.email) = sd.email
        """
        _UPSERT = """
            INSERT INTO scan_leads (email, first_scan_at, last_activity_at, total_scans,
                urls_scanned, domains_scanned, best_score, worst_score, last_score,
                last_domain, has_account, account_id, has_monitoring, lead_score,
                classification, sector, platform, is_corporate_email, source, updated_at)
            VALUES (%(email)s, %(first)s, %(last)s, %(total)s, %(urls)s, %(domains)s,
                %(best)s, %(worst)s, %(last_score)s, %(last_domain)s, %(has_account)s,
                %(account_id)s, %(has_monitoring)s, %(score)s, %(classification)s,
                %(sector)s, %(platform)s, %(is_corp)s, 'scan', NOW())
            ON CONFLICT (email) DO UPDATE SET
                first_scan_at = LEAST(scan_leads.first_scan_at, EXCLUDED.first_scan_at),
                last_activity_at = GREATEST(scan_leads.last_activity_at, EXCLUDED.last_activity_at),
                total_scans = EXCLUDED.total_scans, urls_scanned = EXCLUDED.urls_scanned,
                domains_scanned = EXCLUDED.domains_scanned, best_score = EXCLUDED.best_score,
                worst_score = EXCLUDED.worst_score, last_score = EXCLUDED.last_score,
                last_domain = EXCLUDED.last_domain, has_account = EXCLUDED.has_account,
                account_id = EXCLUDED.account_id, has_monitoring = EXCLUDED.has_monitoring,
                lead_score = EXCLUDED.lead_score, classification = EXCLUDED.classification,
                sector = COALESCE(EXCLUDED.sector, scan_leads.sector),
                platform = COALESCE(EXCLUDED.platform, scan_leads.platform),
                is_corporate_email = EXCLUDED.is_corporate_email, updated_at = NOW()
        """

        def _fn(cur):
            cur.execute(_AGG)
            rows = self._rows_to_dicts(cur)
            n = 0
            for r in rows:
                email = (r["email"] or "").lower().strip()
                if not email or "@" not in email:
                    continue
                is_corp = is_corporate_email(email)
                score, classification = calculate_lead_score({
                    "total_scans": r["total_scans"],
                    "distinct_urls": len(r["urls_scanned"] or []),
                    "worst_score": r["worst_score"], "has_account": r["has_account"],
                    "has_monitoring": r["has_monitoring"], "is_corporate_email": is_corp,
                    "last_activity_at": r["last_activity_at"]})
                cur.execute(_UPSERT, {
                    "email": email, "first": r["first_scan_at"], "last": r["last_activity_at"],
                    "total": r["total_scans"], "urls": list(r["urls_scanned"] or []),
                    "domains": list(r["domains_scanned"] or []), "best": r["best_score"],
                    "worst": r["worst_score"], "last_score": r["last_score"],
                    "last_domain": r["last_domain"], "has_account": r["has_account"],
                    "account_id": r["account_id"], "has_monitoring": r["has_monitoring"],
                    "score": score, "classification": classification, "sector": r["sector"],
                    "platform": r["platform"], "is_corp": is_corp})
                n += 1
            return n

        return await asyncio.to_thread(self._run, _fn)

    async def backfill_leads_from_accounts(self) -> int:
        """Cria scan_leads para contas (users) SEM lead ainda (KL-44 P1 fix). O
        `backfill_leads` (KL-61) só cobre e-mails com `scanned_by_email`; contas que
        entraram via alerta→signup sem scan público ficavam de fora. Varremos `users`
        e criamos o lead com `has_account=True` (+ `has_monitoring` via user_sites).
        Idempotente (ON CONFLICT DO NOTHING — não sobrescreve leads já existentes)."""
        from api.lead_scoring import is_corporate_email, calculate_lead_score
        _SEL = """
            SELECT u.id AS account_id, LOWER(u.email) AS email, u.created_at,
                COALESCE(EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id), FALSE)
                    AS has_monitoring
            FROM users u
            LEFT JOIN scan_leads sl ON sl.email = LOWER(u.email)
            WHERE sl.id IS NULL AND u.email IS NOT NULL AND u.email <> ''
        """
        _INS = """
            INSERT INTO scan_leads (email, first_scan_at, last_activity_at, total_scans,
                has_account, account_id, has_monitoring, lead_score, classification,
                is_corporate_email, source, updated_at)
            VALUES (%(email)s, %(created)s, %(created)s, 0, TRUE, %(account_id)s,
                %(has_monitoring)s, %(score)s, %(classification)s, %(is_corp)s, 'account', NOW())
            ON CONFLICT (email) DO NOTHING
        """

        def _fn(cur):
            cur.execute(_SEL)
            rows = self._rows_to_dicts(cur)
            n = 0
            for r in rows:
                email = (r["email"] or "").lower().strip()
                if not email or "@" not in email:
                    continue
                is_corp = is_corporate_email(email)
                score, classification = calculate_lead_score({
                    "total_scans": 0, "distinct_urls": 0, "worst_score": None,
                    "has_account": True, "has_monitoring": r["has_monitoring"],
                    "is_corporate_email": is_corp, "last_activity_at": r["created_at"]})
                cur.execute(_INS, {
                    "email": email, "created": r["created_at"], "account_id": r["account_id"],
                    "has_monitoring": r["has_monitoring"], "score": score,
                    "classification": classification, "is_corp": is_corp})
                n += cur.rowcount or 0
            return n

        return await asyncio.to_thread(self._run, _fn)

    # ===== KL-44: planos & assinaturas ===================================== #

    async def list_plans(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Todos os planos (por padrão só os ativos), ordenados por preço."""
        def _fn(cur):
            cur.execute("SELECT * FROM plans" + (" WHERE is_active = TRUE" if active_only else "")
                        + " ORDER BY price_monthly")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM plans WHERE id = %s", (plan_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    # Colunas do plano editáveis pelo admin (tudo menos id/created_at).
    _PLAN_EDITABLE = (
        "name", "price_monthly", "price_yearly", "max_sites", "scan_frequency",
        "vigilia_ssl", "vigilia_domain", "vigilia_score", "vigilia_email",
        "vigilia_reputation", "vigilia_changes", "vigilia_phishing", "vigilia_uptime",
        "uptime_interval_minutes", "bulletin_frequency", "action_plan_limit",
        "history_months", "competitor_slots", "lgpd_full", "widget_type",
        "pdf_report_frequency", "export_enabled", "api_enabled", "is_active")

    async def update_plan(self, plan_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Atualiza os limites de um plano (só as colunas na whitelist). Retorna o plano."""
        cols = [(k, v) for k, v in (fields or {}).items() if k in self._PLAN_EDITABLE]

        def _fn(cur):
            if cols:
                sets = ", ".join(f"{k} = %s" for k, _ in cols) + ", updated_at = NOW()"
                cur.execute(f"UPDATE plans SET {sets} WHERE id = %s",
                            [v for _, v in cols] + [plan_id])
            cur.execute("SELECT * FROM plans WHERE id = %s", (plan_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def get_subscription_row(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Assinatura crua de uma conta (users.id), ou None."""
        def _fn(cur):
            cur.execute("SELECT * FROM subscriptions WHERE account_id = %s", (account_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def upsert_subscription(
        self, account_id: int, plan_id: str, status: str,
        trial_ends_at: Any = None, expires_at: Any = None,
        billing_cycle: str = "monthly",
    ) -> Dict[str, Any]:
        """Cria/atualiza a assinatura de uma conta (UNIQUE por account_id)."""
        def _fn(cur):
            cur.execute(
                """INSERT INTO subscriptions
                       (account_id, plan_id, status, trial_ends_at, expires_at, billing_cycle)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (account_id) DO UPDATE SET
                       plan_id = EXCLUDED.plan_id, status = EXCLUDED.status,
                       trial_ends_at = EXCLUDED.trial_ends_at, expires_at = EXCLUDED.expires_at,
                       billing_cycle = EXCLUDED.billing_cycle, updated_at = NOW()
                   RETURNING *""",
                (account_id, plan_id, status, trial_ends_at, expires_at, billing_cycle))
            return self._rows_to_dicts(cur)[0]

        return await asyncio.to_thread(self._run, _fn)

    async def update_subscription(self, account_id: int, **fields) -> Optional[Dict[str, Any]]:
        """Atualiza campos da assinatura (plan_id/status/trial_ends_at/expires_at/…)."""
        allowed = ("plan_id", "status", "trial_ends_at", "expires_at", "billing_cycle",
                   "last_payment_at", "cancelled_at", "notes")
        cols = [(k, v) for k, v in fields.items() if k in allowed]

        def _fn(cur):
            if cols:
                sets = ", ".join(f"{k} = %s" for k, _ in cols) + ", updated_at = NOW()"
                cur.execute(f"UPDATE subscriptions SET {sets} WHERE account_id = %s",
                            [v for _, v in cols] + [account_id])
            cur.execute("SELECT * FROM subscriptions WHERE account_id = %s", (account_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def log_subscription_change(
        self, account_id: int, old_plan_id: Optional[str], new_plan_id: str,
        old_status: Optional[str], new_status: str, changed_by: str = "system",
        reason: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(self._run, lambda cur: cur.execute(
            """INSERT INTO subscription_history
                   (account_id, old_plan_id, new_plan_id, old_status, new_status, changed_by, reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (account_id, old_plan_id, new_plan_id, old_status, new_status, changed_by, reason)))

    async def list_subscription_history(self, account_id: int) -> List[Dict[str, Any]]:
        def _fn(cur):
            cur.execute("SELECT * FROM subscription_history WHERE account_id = %s "
                        "ORDER BY created_at DESC", (account_id,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def subscription_group_counts(self) -> List[Dict[str, Any]]:
        """[{plan_id, status, n}] por conta — LEFT JOIN de users (conta sem assinatura
        conta como free/free)."""
        def _fn(cur):
            cur.execute(
                "SELECT COALESCE(s.plan_id, 'free') AS plan_id, "
                "COALESCE(s.status, 'free') AS status, COUNT(*) AS n "
                "FROM users u LEFT JOIN subscriptions s ON s.account_id = u.id "
                "GROUP BY COALESCE(s.plan_id, 'free'), COALESCE(s.status, 'free')")
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def count_trials_expiring(self, days: int = 7) -> int:
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE status = 'trial' "
                "AND trial_ends_at IS NOT NULL "
                "AND trial_ends_at BETWEEN NOW() AND NOW() + (%s || ' days')::interval",
                (str(days),))
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def list_subscribers(
        self, plan_id: Optional[str] = None, status: Optional[str] = None,
        search: Optional[str] = None, limit: int = 25, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Contas + assinatura + nº de sites monitorados (para a página Assinantes)."""
        like = f"%{search.strip()}%" if search else None

        def _fn(cur):
            cur.execute(
                """SELECT u.id AS account_id, u.email, u.name, u.created_at, u.last_login_at,
                          u.is_active,
                          COALESCE(s.plan_id, 'free') AS plan_id,
                          COALESCE(s.status, 'free') AS status,
                          s.trial_ends_at, s.started_at, s.expires_at, s.billing_cycle,
                          p.name AS plan_name, p.max_sites AS plan_max_sites,
                          (SELECT COUNT(*) FROM user_sites us WHERE us.user_id = u.id) AS sites
                   FROM users u
                   LEFT JOIN subscriptions s ON s.account_id = u.id
                   LEFT JOIN plans p ON p.id = COALESCE(s.plan_id, 'free')
                   WHERE (%(plan)s IS NULL OR COALESCE(s.plan_id, 'free') = %(plan)s)
                     AND (%(status)s IS NULL OR COALESCE(s.status, 'free') = %(status)s)
                     AND (%(like)s IS NULL OR u.email ILIKE %(like)s)
                   ORDER BY u.created_at DESC
                   LIMIT %(limit)s OFFSET %(offset)s""",
                {"plan": plan_id or None, "status": status or None, "like": like,
                 "limit": limit, "offset": offset})
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def users_without_subscription(self) -> List[Dict[str, Any]]:
        """Contas (users) que ainda não têm assinatura — para o seed do KL-44."""
        def _fn(cur):
            cur.execute(
                "SELECT u.id, u.email, u.created_at FROM users u "
                "LEFT JOIN subscriptions s ON s.account_id = u.id WHERE s.id IS NULL")
            return self._rows_to_dicts(cur)

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

    # --- rankings por setor (KL-42) ---------------------------------------- #

    async def list_sector_ranking(self, sector: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Top sites de um setor por score (KL-42) — só sites com scan público e
        landing ligada (`public_visible`, KL-56). Ordena por score DESC, domínio."""
        def _fn(cur):
            cur.execute(
                "SELECT t.domain, t.last_scan_score, t.last_scan_at "
                "FROM targets t JOIN site_profile sp ON sp.target_id = t.id "
                "WHERE t.sector = %s AND t.status IN ('scanned', 'alerted') "
                "  AND t.last_scan_score IS NOT NULL "
                "  AND t.domain IS NOT NULL AND t.domain <> '' "
                "  AND COALESCE(sp.public_visible, TRUE) = TRUE "
                "ORDER BY t.last_scan_score DESC, t.domain ASC LIMIT %s", (sector, limit))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def ranking_sectors_summary(self, min_count: int = 5) -> List[Dict[str, Any]]:
        """Setores com ranking público (≥ `min_count` sites com scan público): contagem,
        score médio e o domínio top de cada um (KL-42). Exclui `outro`."""
        def _fn(cur):
            cur.execute(
                "SELECT t.sector, COUNT(*) AS count, "
                "       COALESCE(ROUND(AVG(t.last_scan_score)), 0) AS avg_score "
                "FROM targets t JOIN site_profile sp ON sp.target_id = t.id "
                "WHERE t.status IN ('scanned', 'alerted') AND t.last_scan_score IS NOT NULL "
                "  AND t.sector IS NOT NULL AND t.sector <> '' AND t.sector <> 'outro' "
                "  AND COALESCE(sp.public_visible, TRUE) = TRUE "
                "GROUP BY t.sector HAVING COUNT(*) >= %s "
                "ORDER BY COUNT(*) DESC", (min_count,))
            rows = self._rows_to_dicts(cur)
            for r in rows:  # top site por setor (poucos setores → N+1 aceitável, 1 conexão)
                cur.execute(
                    "SELECT t.domain FROM targets t JOIN site_profile sp ON sp.target_id = t.id "
                    "WHERE t.sector = %s AND t.status IN ('scanned', 'alerted') "
                    "  AND t.last_scan_score IS NOT NULL "
                    "  AND COALESCE(sp.public_visible, TRUE) = TRUE "
                    "ORDER BY t.last_scan_score DESC, t.domain ASC LIMIT 1", (r["sector"],))
                row = cur.fetchone()
                r["top_domain"] = row[0] if row else None
            return rows

        return await asyncio.to_thread(self._run, _fn)

    async def get_sector_position(self, sector: str, target_id: int
                                  ) -> Optional[Dict[str, int]]:
        """Posição de um alvo no ranking do setor (KL-42) + total. Ranqueia entre TODOS
        os sites com score no setor (não exige perfil/landing) — a posição é do dono."""
        def _fn(cur):
            cur.execute(
                "WITH ranked AS ("
                "  SELECT id, "
                "         ROW_NUMBER() OVER (ORDER BY last_scan_score DESC, domain ASC) AS pos, "
                "         COUNT(*) OVER () AS total "
                "  FROM targets WHERE sector = %s AND status IN ('scanned', 'alerted') "
                "    AND last_scan_score IS NOT NULL) "
                "SELECT pos, total FROM ranked WHERE id = %s", (sector, target_id))
            row = cur.fetchone()
            return {"position": int(row[0]), "total": int(row[1])} if row else None

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

    async def email_metrics(self) -> Dict[str, Any]:
        """Métricas de e-mail do `email_log` unificado (KL-62) — TODOS os caminhos.

        Conta `status='sent'` (excluindo `email_type='test'`, que é diagnóstico) por
        janela, mais bloqueios/falhas de hoje e o volume por tipo (top). Mantém as
        chaves `sent_today/sent_week/sent_month` (consumidas pela página Sistema)."""
        def _fn(cur):
            out: Dict[str, Any] = {}
            for key, interval in (("sent_today", "1 day"), ("sent_week", "7 days"),
                                  ("sent_month", "30 days")):
                cur.execute(
                    f"SELECT COUNT(*) FROM email_log WHERE status='sent' "
                    f"  AND email_type <> 'test' AND sent_at > NOW() - INTERVAL '{interval}'"
                )
                out[key] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM email_log WHERE status='blocked' "
                        "  AND sent_at > NOW() - INTERVAL '1 day'")
            out["blocked_today"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM email_log WHERE status='failed' "
                        "  AND sent_at > NOW() - INTERVAL '1 day'")
            out["failed_today"] = int(cur.fetchone()[0])
            cur.execute("SELECT email_type, COUNT(*) FROM email_log "
                        "  WHERE status='sent' AND sent_at > NOW() - INTERVAL '1 day' "
                        "  GROUP BY email_type ORDER BY COUNT(*) DESC LIMIT 5")
            out["by_type"] = [{"email_type": r[0], "count": int(r[1])} for r in cur.fetchall()]
            return out

        return await asyncio.to_thread(self._run, _fn)

    # --- KL-62: log unificado de e-mails (centralizado no KlarimMailer) --------- #

    async def log_email(self, *, email_id: Optional[str], to_email: str, email_type: str,
                        subject: Optional[str] = None, target_id: Optional[int] = None,
                        domain: Optional[str] = None, status: str = "sent",
                        blocked_reason: Optional[str] = None, error: Optional[str] = None,
                        source: Optional[str] = None, batch_id: Optional[str] = None,
                        from_domain: Optional[str] = None) -> None:
        """Grava uma entrada no `email_log` (KL-62). Chamado pelo KlarimMailer em TODO
        envio. `from_domain` (migração klarimscan.com) registra de qual domínio o e-mail
        saiu. Best-effort — quem chama já envolve em try/except (nunca derruba o envio)."""
        to_email = (to_email or "").strip().lower()
        if not to_email:
            return

        def _fn(cur):
            cur.execute(
                "INSERT INTO email_log (email_id, to_email, email_type, subject, target_id, "
                "  domain, status, blocked_reason, error, source, batch_id, from_domain) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (email_id, to_email, email_type, (subject or None), target_id,
                 (domain or None), status, blocked_reason, (error[:2000] if error else None),
                 source, batch_id, (from_domain or None)))

        await asyncio.to_thread(self._run, _fn)

    async def count_alerts_sent_today(self) -> int:
        """Alertas PROATIVOS efetivamente enviados hoje (calendário) — controla o warmup
        do domínio novo. Conta `email_log` (status='sent') dos tipos de alerta cold."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM email_log "
                "WHERE status = 'sent' AND email_type IN ('alert', 'alert_score100') "
                "  AND sent_at >= date_trunc('day', NOW())")
            return int(cur.fetchone()[0])

        return await asyncio.to_thread(self._run, _fn)

    async def list_email_log(self, email_type: Optional[str] = None,
                             status: Optional[str] = None, to_email: Optional[str] = None,
                             source: Optional[str] = None, limit: int = 20,
                             offset: int = 0) -> Dict[str, Any]:
        """Lista paginada do `email_log` + total + contagem por status (KL-62)."""
        def _fn(cur):
            where, params = [], []
            if email_type:
                where.append("email_type = %s")
                params.append(email_type)
            if status:
                where.append("status = %s")
                params.append(status)
            if to_email:
                where.append("LOWER(to_email) LIKE %s")
                params.append(f"%{to_email.lower().strip()}%")
            if source:
                where.append("source = %s")
                params.append(source)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            cur.execute(f"SELECT status, COUNT(*) FROM email_log {clause} GROUP BY status", params)
            by_status = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(f"SELECT COUNT(*) FROM email_log {clause}", params)
            total = int(cur.fetchone()[0])
            cur.execute(f"SELECT * FROM email_log {clause} ORDER BY sent_at DESC "
                        f"LIMIT %s OFFSET %s", params + [limit, offset])
            return {"emails": self._rows_to_dicts(cur), "total": total,
                    "by_status": by_status}

        return await asyncio.to_thread(self._run, _fn)

    async def mark_email_status_by_email_id(self, email_id: str, status: str) -> int:
        """Atualiza o status no `email_log` por email_id (webhook/backfill de bounce, KL-62)."""
        if not email_id:
            return 0

        def _fn(cur):
            cur.execute("UPDATE email_log SET status = %s WHERE email_id = %s",
                        (status, email_id))
            return cur.rowcount

        return await asyncio.to_thread(self._run, _fn)

    async def get_sent_emails_for_bounce_check(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """email_id + to_email dos envios recentes (7d) do `email_log` p/ checar bounce
        no Resend (KL-62). Superset do antigo `get_sent_alerts_for_bounce_check`."""
        def _fn(cur):
            cur.execute(
                "SELECT DISTINCT ON (email_id) email_id, to_email AS contact_email "
                "FROM email_log WHERE email_id IS NOT NULL AND status = 'sent' "
                "  AND sent_at > NOW() - INTERVAL '7 days' "
                "ORDER BY email_id, sent_at DESC LIMIT %s",
                (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def list_email_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Últimas entradas do `email_log` para a timeline de atividade (KL-62)."""
        def _fn(cur):
            cur.execute(
                "SELECT email_type, to_email, status, domain, blocked_reason, sent_at "
                "FROM email_log ORDER BY sent_at DESC LIMIT %s", (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def migrate_email_log(self) -> Dict[str, int]:
        """Popula o `email_log` com o histórico de `alert_log` + `rescan_log` (KL-62).

        Idempotente: deduplica por (source, to_email, sent_at, email_id) via NOT EXISTS
        (IS NOT DISTINCT FROM trata email_id NULL). Rodar 2× não duplica."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO email_log (email_id, to_email, email_type, target_id, "
                "  status, sent_at, source) "
                "SELECT al.email_id, al.contact_email, "
                "  CASE WHEN al.score = 100 THEN 'alert_score100' ELSE 'alert' END, "
                "  al.target_id, "
                "  CASE WHEN al.status = 'bounced' THEN 'bounced' "
                "       WHEN al.status = 'complained' THEN 'complained' ELSE 'sent' END, "
                "  al.sent_at, 'alert_worker' "
                "FROM alert_log al "
                "WHERE NOT EXISTS (SELECT 1 FROM email_log el WHERE el.source = 'alert_worker' "
                "  AND el.to_email = LOWER(al.contact_email) AND el.sent_at = al.sent_at "
                "  AND el.email_id IS NOT DISTINCT FROM al.email_id)")
            n_alert = cur.rowcount
            cur.execute(
                "INSERT INTO email_log (email_id, to_email, email_type, target_id, "
                "  status, sent_at, source) "
                "SELECT rl.email_id, t.contact_email, 'evolution', rl.target_id, "
                "  'sent', rl.rescanned_at, 'rescan_worker' "
                "FROM rescan_log rl JOIN targets t ON t.id = rl.target_id "
                "WHERE rl.email_id IS NOT NULL AND t.contact_email IS NOT NULL "
                "  AND NOT EXISTS (SELECT 1 FROM email_log el WHERE el.source = 'rescan_worker' "
                "    AND el.email_id IS NOT DISTINCT FROM rl.email_id "
                "    AND el.sent_at = rl.rescanned_at)")
            n_rescan = cur.rowcount
            return {"alert_log": n_alert, "rescan_log": n_rescan}

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
        """Métricas de bounce (KL-24/62) a partir do `email_log` unificado + blocklist.

        Agora cobre TODOS os caminhos de envio (não só `alert_log`). `bounced`/
        `complained` refletem o que o webhook/backfill do Resend marcou. `total` =
        tentativas rastreáveis (sent + bounced + complained; exclui `test`)."""
        def _fn(cur):
            cur.execute(
                "SELECT "
                "COUNT(*) FILTER (WHERE status IN ('sent','bounced','complained')), "
                "COUNT(*) FILTER (WHERE status = 'bounced'), "
                "COUNT(*) FILTER (WHERE status = 'complained') "
                "FROM email_log WHERE email_type <> 'test'"
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

    async def analytics_events(self, limit: int = 50, event_type: Optional[str] = None
                               ) -> List[Dict[str, Any]]:
        """Últimos eventos do funil. `event_type` (opcional) filtra por tipo — usado
        pela aba 'Consultas de perfil' (profile_view) da página Alertas."""
        def _fn(cur):
            if event_type:
                cur.execute(
                    "SELECT event_type, session_id, target_url, page_url, utm_campaign, "
                    "metadata, created_at FROM site_events WHERE event_type = %s "
                    "ORDER BY created_at DESC LIMIT %s", (event_type, limit))
            else:
                cur.execute(
                    "SELECT event_type, session_id, target_url, page_url, utm_campaign, "
                    "metadata, created_at FROM site_events ORDER BY created_at DESC LIMIT %s",
                    (limit,))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    # --- inbox scan@klarim.net (KL-56) ------------------------------------- #

    _INBOX_COLS = ("id, message_id, from_address, from_name, to_address, subject, "
                   "body_preview, received_at, is_read, is_starred, is_archived, "
                   "source, created_at")

    async def insert_inbox_message(self, msg: Dict[str, Any]) -> bool:
        """Grava uma mensagem no inbox — webhook Hostinger ou formulário de contato
        (KL-60, `source`). Dedup por `message_id` (ON CONFLICT DO NOTHING). Retorna
        True se inseriu, False se já existia."""
        def _fn(cur):
            cur.execute(
                "INSERT INTO inbox_messages (message_id, from_address, from_name, "
                "  to_address, subject, body_preview, body_html, received_at, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (message_id) DO NOTHING RETURNING id",
                (msg.get("message_id"), msg.get("from_address") or "(desconhecido)",
                 msg.get("from_name"), msg.get("to_address") or "scan@klarim.net",
                 msg.get("subject"), msg.get("body_preview"), msg.get("body_html"),
                 msg.get("received_at"), msg.get("source") or "webhook"),
            )
            return cur.fetchone() is not None

        return await asyncio.to_thread(self._run, _fn)

    async def list_inbox_messages(
        self, box: str = "all", limit: int = 25, offset: int = 0,
        source: Optional[str] = None, search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lista mensagens (sem o corpo HTML). `box`: all|unread|starred|archived.
        `source` (KL-60): `webhook`|`contact_form`. `search` (fix MCP): texto no
        assunto/remetente/preview (ILIKE)."""
        def _fn(cur):
            where = []
            params: list = []
            if box == "unread":
                where.append("is_read = FALSE AND is_archived = FALSE")
            elif box == "starred":
                where.append("is_starred = TRUE AND is_archived = FALSE")
            elif box == "archived":
                where.append("is_archived = TRUE")
            else:  # all -> caixa de entrada (não-arquivadas)
                where.append("is_archived = FALSE")
            if source in ("webhook", "contact_form"):
                where.append("COALESCE(source, 'webhook') = %s")
                params.append(source)
            if search:
                like = f"%{search.strip()}%"
                where.append("(subject ILIKE %s OR from_address ILIKE %s "
                             "OR from_name ILIKE %s OR body_preview ILIKE %s)")
                params.extend([like, like, like, like])
            clause = "WHERE " + " AND ".join(where)
            params.extend([limit, offset])
            cur.execute(
                f"SELECT {self._INBOX_COLS} FROM inbox_messages {clause} "
                f"ORDER BY received_at DESC NULLS LAST, created_at DESC "
                f"LIMIT %s OFFSET %s", tuple(params))
            return self._rows_to_dicts(cur)

        return await asyncio.to_thread(self._run, _fn)

    async def get_inbox_message(self, msg_id: int) -> Optional[Dict[str, Any]]:
        """Detalhe completo (com body_html) de uma mensagem."""
        def _fn(cur):
            cur.execute("SELECT * FROM inbox_messages WHERE id = %s", (msg_id,))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def set_inbox_read(self, msg_id: int, read: bool = True) -> Optional[Dict[str, Any]]:
        return await self._inbox_update(msg_id, "is_read = %s", (read,))

    async def toggle_inbox_star(self, msg_id: int) -> Optional[Dict[str, Any]]:
        return await self._inbox_update(msg_id, "is_starred = NOT is_starred", ())

    async def set_inbox_archived(
        self, msg_id: int, archived: bool = True
    ) -> Optional[Dict[str, Any]]:
        return await self._inbox_update(msg_id, "is_archived = %s", (archived,))

    async def _inbox_update(
        self, msg_id: int, set_sql: str, params: tuple
    ) -> Optional[Dict[str, Any]]:
        def _fn(cur):
            cur.execute(
                f"UPDATE inbox_messages SET {set_sql} WHERE id = %s "
                f"RETURNING {self._INBOX_COLS}", (*params, msg_id))
            rows = self._rows_to_dicts(cur)
            return rows[0] if rows else None

        return await asyncio.to_thread(self._run, _fn)

    async def inbox_unread_count(self) -> int:
        """Não-lidas e não-arquivadas (badge do menu)."""
        def _fn(cur):
            cur.execute("SELECT COUNT(*) FROM inbox_messages "
                        "WHERE is_read = FALSE AND is_archived = FALSE")
            return int(cur.fetchone()[0])

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
