"""Seed KL-90 P0 — popula o banco LOCAL com dados representativos p/ testar o
Dashboard v2 (e a plataforma como um todo) SEM depender de scans/discovery reais.

Cria:
  • 3 usuários (dono confirmado Pro trial · técnico · conta nova não-confirmada)
  • 5 sites monitorados pelo usuário 1 (scores variados 20..100, 3 setores)
  • 10 scans por site (50 no total) → histórico de score p/ o gráfico de tendência;
    o mais recente carrega os 48 checks (PASS/FAIL/INCONCLUSO) com evidência
  • 10 vigílias (ssl+score por site) — inclui a queda de score crítica de loja-exemplo
  • perfis comerciais públicos + alguns sites "de preenchimento" por setor
    (só p/ o benchmark setorial e o ranking ficarem realistas)

Os riscos NÃO são uma tabela: derivam dos checks FAIL via build_risk_summary
(KL-20). O site de score 42 falha SPF/HSTS/CSP (+ outros) com fix por plataforma.

Idempotente: apaga tudo que ELE criou (por e-mail/domínio conhecidos) e recria.
NÃO usar em produção — só docker-compose.dev.yml.

Uso:
  docker compose -f docker-compose.dev.yml exec api python -m scripts.seed_dev
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.store import get_target_store  # noqa: E402
from api import auth_users  # noqa: E402

try:  # carimba OWASP/CWE/LGPD nos checks (metadata real). Best-effort.
    from scanner.checks.classifications import CLASSIFICATIONS
except Exception:  # noqa: BLE001
    CLASSIFICATIONS = {}

NOW = datetime.now(timezone.utc)
DEV_PASSWORD = "dev123456"

# --------------------------------------------------------------------------- #
# Guarda de segurança: NUNCA rodar contra um banco que não seja de dev.
# --------------------------------------------------------------------------- #
def _guard_dev() -> None:
    host = (os.environ.get("POSTGRES_HOST") or "").lower()
    dev_mode = os.environ.get("KLARIM_DEV_MODE", "").lower() == "true"
    if not dev_mode and host not in ("db", "localhost", "127.0.0.1", ""):
        print(f"[seed_dev] RECUSADO: host '{host}' não parece dev "
              f"(defina KLARIM_DEV_MODE=true p/ forçar).", flush=True)
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Os 48 checks (id, nome, severidade) — canônico, casa com classifications.py
# e RISK_MESSAGES (reporter/risk_messages.py). Ordem = ORDER do check.
# --------------------------------------------------------------------------- #
_SEV = {"C": "CRITICA", "A": "ALTA", "M": "MEDIA", "B": "BAIXA"}
CHECKS = [
    ("check_01_https", "HTTPS ativo", "A"),
    ("check_02_hsts", "HSTS presente", "M"),
    ("check_03_ssl", "Certificado SSL válido", "A"),
    ("check_04_tls", "TLS 1.2+ only", "A"),
    ("check_05_csp", "Content-Security-Policy", "A"),
    ("check_06_xfo", "X-Frame-Options", "M"),
    ("check_07_xcto", "X-Content-Type-Options", "B"),
    ("check_08_server", "Server header exposto", "B"),
    ("check_09_sourcemaps", "Source maps expostos", "M"),
    ("check_10_sensitive", "Arquivos sensíveis expostos", "C"),
    ("check_11_dirlist", "Directory listing ativo", "A"),
    ("check_12_metatags", "Meta tags default", "B"),
    ("check_13_sri", "SRI ausente em scripts externos", "M"),
    ("check_14_risky_sources", "Scripts de fontes arriscadas", "M"),
    ("check_15_external_domains", "Domínios externos carregando scripts", "B"),
    ("check_16_api_docs", "Documentação de API exposta", "M"),
    ("check_17_cookies", "Cookies sem flags de segurança", "M"),
    ("check_18_cors", "CORS permissivo", "A"),
    ("check_19_redirect_domain", "Redirect para domínio diferente", "M"),
    ("check_20_info_disclosure", "Diferenciação 403/404 em paths sensíveis", "B"),
    ("check_21_spf", "SPF (proteção de e-mail)", "A"),
    ("check_22_dkim", "DKIM (assinatura de e-mail)", "M"),
    ("check_23_dmarc", "DMARC (proteção contra phishing)", "A"),
    ("check_24_mixed_content", "Mixed content (recursos HTTP em página HTTPS)", "A"),
    ("check_25_form_security", "Formulários inseguros", "A"),
    ("check_26_subdomains", "Subdomínios expostos (CT logs)", "B"),
    ("check_27_dangling_cname", "Dangling CNAME (subdomain takeover)", "C"),
    ("check_28_hibp", "Vazamentos de dados (HIBP)", "A"),
    ("check_29_safe_browsing", "Google Safe Browsing", "C"),
    ("check_30_vulnerable_components", "Componentes com vulnerabilidades conhecidas", "A"),
    ("check_31_permissions_policy", "Permissions-Policy", "B"),
    ("check_32_coop", "Cross-Origin-Opener-Policy (COOP)", "B"),
    ("check_33_coep", "Cross-Origin-Embedder-Policy (COEP)", "B"),
    ("check_34_corp", "Cross-Origin-Resource-Policy (CORP)", "B"),
    ("check_35_referrer_policy", "Referrer-Policy (qualidade)", "B"),
    ("check_36_cache_control_forms", "Cache-Control em páginas sensíveis", "M"),
    ("check_37_dnssec", "DNSSEC", "M"),
    ("check_38_caa", "CAA (Certificate Authority Authorization)", "B"),
    ("check_39_mta_sts", "MTA-STS", "B"),
    ("check_40_bimi", "BIMI", "B"),
    ("check_41_cipher_suites", "Cipher suites", "M"),
    ("check_42_cert_chain", "Certificate chain", "M"),
    ("check_43_ocsp_stapling", "OCSP stapling", "B"),
    ("check_44_key_strength", "Força da chave criptográfica", "A"),
    ("check_45_html_comments", "Informações sensíveis em comentários HTML", "B"),
    ("check_46_debug_mode", "Indicadores de modo debug em produção", "A"),
    ("check_47_open_redirect", "Padrões de open redirect", "A"),
    ("check_48_password_fields", "Campos de senha sem proteções", "A"),
]

# Evidência exibida quando o check FALHA (curta, no estilo do scanner real).
FAIL_EVIDENCE = {
    "check_01_https": "O site atende por HTTP sem redirecionar para HTTPS.",
    "check_02_hsts": "Header Strict-Transport-Security ausente na resposta.",
    "check_03_ssl": "Cadeia do certificado incompleta (intermediário ausente).",
    "check_05_csp": "Nenhum header Content-Security-Policy foi enviado.",
    "check_06_xfo": "X-Frame-Options ausente — página embutível em iframe.",
    "check_07_xcto": "X-Content-Type-Options: nosniff ausente.",
    "check_10_sensitive": "Arquivo /.env acessível publicamente (HTTP 200).",
    "check_11_dirlist": "Directory listing habilitado em /uploads/.",
    "check_17_cookies": "Cookie de sessão sem os atributos Secure e HttpOnly.",
    "check_18_cors": "Access-Control-Allow-Origin: * com credenciais permitidas.",
    "check_21_spf": "Nenhum registro SPF publicado no DNS do domínio.",
    "check_23_dmarc": "Nenhum registro DMARC — domínio sujeito a spoofing.",
    "check_24_mixed_content": "Imagens carregadas via http:// em página https://.",
    "check_25_form_security": "Formulário de login enviado por http://.",
    "check_28_hibp": "E-mails do domínio aparecem em vazamentos conhecidos.",
    "check_30_vulnerable_components": "jQuery 1.12.4 com CVEs conhecidas em uso.",
    "check_31_permissions_policy": "Permissions-Policy ausente.",
    "check_32_coop": "Cross-Origin-Opener-Policy ausente.",
    "check_33_coep": "Cross-Origin-Embedder-Policy ausente.",
    "check_34_corp": "Cross-Origin-Resource-Policy ausente.",
    "check_35_referrer_policy": "Referrer-Policy ausente ou permissiva.",
    "check_37_dnssec": "Domínio sem DNSSEC habilitado.",
    "check_39_mta_sts": "Política MTA-STS ausente.",
    "check_40_bimi": "Registro BIMI ausente.",
    "check_13_sri": "Script de CDN externo sem atributo integrity (SRI).",
    "check_46_debug_mode": "Stack trace de framework exposto em página de erro.",
    "check_48_password_fields": "Campo de senha em formulário servido por HTTP.",
}
# Evidência quando o resultado é INCONCLUSO.
INCONCLUSIVE_EVIDENCE = "Não foi possível verificar (serviço externo indisponível)."

# Fix por plataforma (só nos checks do site de score 42, p/ testar "Como corrigir").
FIX_BY_PLATFORM = {
    "check_21_spf": {
        "wordpress": "Publique um TXT SPF no DNS (não é no WP): "
                     "v=spf1 include:_spf.google.com ~all",
        "nginx": "SPF é DNS, não Nginx. Adicione um registro TXT: v=spf1 -all "
                 "se o domínio não envia e-mail.",
        "apache": "SPF é DNS, não Apache. Publique um TXT: v=spf1 include:seuprovedor ~all",
    },
    "check_02_hsts": {
        "wordpress": "Use um plugin de headers ou o .htaccess: "
                     "Header always set Strict-Transport-Security \"max-age=31536000\"",
        "nginx": "add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;",
        "apache": "Header always set Strict-Transport-Security \"max-age=31536000; includeSubDomains\"",
    },
    "check_05_csp": {
        "wordpress": "Adicione um Content-Security-Policy via plugin de segurança "
                     "(ex.: default-src 'self').",
        "nginx": "add_header Content-Security-Policy \"default-src 'self'\" always;",
        "apache": "Header always set Content-Security-Policy \"default-src 'self'\"",
    },
}

# --------------------------------------------------------------------------- #
# Definição dos 5 sites reais monitorados (usuário 1).
#   domain, sector(slug), score(final), semaphore(final),
#   trend(10 pontos, do mais antigo ao mais recente), fails(ids), inconclusive(ids)
# --------------------------------------------------------------------------- #
SITES = [
    {
        "domain": "hotel-exemplo.com.br", "sector": "hotelaria",
        "company": "Hotel Exemplo", "phone": "+55 11 3200-1000",
        "score": 83, "semaphore": "amarelo",
        "trend": [71, 73, 75, 78, 78, 80, 81, 81, 83, 83],
        "fails": ["check_02_hsts", "check_05_csp", "check_13_sri",
                  "check_31_permissions_policy", "check_35_referrer_policy",
                  "check_37_dnssec"],
        "inconclusive": ["check_28_hibp", "check_29_safe_browsing"],
        "edited": True,
    },
    {
        "domain": "clinica-exemplo.com.br", "sector": "saude",
        "company": "Clínica Exemplo", "phone": "+55 21 3555-2000",
        "score": 100, "semaphore": "verde",
        "trend": [88, 90, 92, 95, 96, 98, 99, 100, 100, 100],
        "fails": [],
        "inconclusive": ["check_40_bimi"],
        "edited": True,
    },
    {
        "domain": "loja-exemplo.com.br", "sector": "ecommerce",
        "company": "Loja Exemplo", "phone": "+55 11 4000-3000",
        "score": 42, "semaphore": "vermelho",
        "trend": [60, 55, 50, 48, 45, 44, 43, 42, 42, 42],
        "fails": ["check_21_spf", "check_02_hsts", "check_05_csp", "check_23_dmarc",
                  "check_06_xfo", "check_07_xcto", "check_17_cookies",
                  "check_24_mixed_content", "check_25_form_security",
                  "check_30_vulnerable_components", "check_10_sensitive",
                  "check_46_debug_mode", "check_31_permissions_policy",
                  "check_35_referrer_policy", "check_37_dnssec"],
        "inconclusive": ["check_28_hibp", "check_29_safe_browsing"],
        "edited": False,
    },
    {
        "domain": "blog-exemplo.com.br", "sector": "tecnologia",
        "company": "Blog Exemplo", "phone": "+55 31 3111-4000",
        "score": 65, "semaphore": "amarelo",
        "trend": [58, 60, 62, 63, 64, 64, 65, 65, 65, 65],
        "fails": ["check_02_hsts", "check_05_csp", "check_31_permissions_policy",
                  "check_32_coop", "check_33_coep", "check_34_corp",
                  "check_35_referrer_policy", "check_37_dnssec",
                  "check_39_mta_sts", "check_40_bimi"],
        "inconclusive": ["check_29_safe_browsing"],
        "edited": False,
    },
    {
        "domain": "empresa-exemplo.com.br", "sector": "servicos",
        "company": "Empresa Exemplo", "phone": "+55 41 3222-5000",
        "score": 20, "semaphore": "vermelho",
        "trend": [35, 32, 28, 25, 24, 22, 21, 20, 20, 20],
        "fails": ["check_01_https", "check_02_hsts", "check_03_ssl", "check_05_csp",
                  "check_06_xfo", "check_07_xcto", "check_18_cors", "check_21_spf",
                  "check_23_dmarc", "check_24_mixed_content", "check_25_form_security",
                  "check_10_sensitive", "check_11_dirlist", "check_30_vulnerable_components",
                  "check_46_debug_mode", "check_48_password_fields",
                  "check_31_permissions_policy", "check_35_referrer_policy",
                  "check_37_dnssec", "check_39_mta_sts"],
        "inconclusive": ["check_28_hibp", "check_29_safe_browsing"],
        "edited": False,
    },
]

# Setores "de preenchimento" (fillers) p/ o benchmark (min_count=10) e o ranking
# ficarem realistas. Não têm scans nem donos — só target + perfil público.
FILLERS = {"hotelaria": 12, "saude": 12, "ecommerce": 10, "tecnologia": 8, "servicos": 8}

SEED_EMAILS = ["dono@exemplo.com.br", "tecnico@agencia.com.br", "novo@teste.com.br",
               "nivel1@teste.com", "dono3@teste.com"]   # KL-99: níveis de conta
# KL-99: domínio dedicado do dono verificado (nível 3), limpo junto com os demais.
KL99_VERIFIED_DOMAIN = "verificado-exemplo.com.br"


def _sem_for(score: int) -> str:
    if score >= 90:
        return "verde"
    if score >= 50:
        return "amarelo"
    return "vermelho"


def _icon(sem: str) -> str:
    return {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}.get(sem, "🟡")


def _build_results(fails: list, inconclusive: list) -> list:
    """Monta a lista dos 48 checks com status/severidade/evidência + OWASP/CWE/LGPD."""
    fails_s, inc_s = set(fails), set(inconclusive)
    out = []
    for cid, name, sev_key in CHECKS:
        sev = _SEV[sev_key]
        if cid in fails_s:
            status, evidence = "FAIL", FAIL_EVIDENCE.get(cid, f"{name}: verificação falhou.")
        elif cid in inc_s:
            status, evidence = "INCONCLUSO", INCONCLUSIVE_EVIDENCE
        else:
            status, evidence = "PASS", ""
        details: dict = {}
        if status == "FAIL" and cid in FIX_BY_PLATFORM:
            details["fix_inline"] = FIX_BY_PLATFORM[cid]
        cls = CLASSIFICATIONS.get(cid)
        out.append({
            "name": name, "status": status, "severity": sev, "evidence": evidence,
            "check_id": cid, "details": details,
            "owasp": getattr(cls, "owasp", None), "cwe": getattr(cls, "cwe", None),
            "lgpd": getattr(cls, "lgpd", None),
        })
    return out


def _score_block(score: int, sem: str, results: list) -> dict:
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    inconclusive = sum(1 for r in results if r["status"] == "INCONCLUSO")
    fbs: dict = {}
    for r in results:
        if r["status"] == "FAIL":
            fbs[r["severity"]] = fbs.get(r["severity"], 0) + 1
    return {
        "score": score, "semaphore": sem, "grade_icon": _icon(sem),
        "earned_weight": score, "considered_weight": 100, "total_weight": 100,
        "passed": passed, "failed": failed, "inconclusive": inconclusive,
        "fails_by_severity": fbs,
    }


def _checks_json(url: str, score: int, sem: str, results: list, ts: datetime) -> dict:
    return {
        "url": url,
        "started_at": ts.isoformat(), "finished_at": ts.isoformat(), "duration_s": 3.2,
        "score": _score_block(score, sem, results),
        "results": results, "privacy": None, "status": "ok", "error_detail": "",
    }


def run() -> None:
    _guard_dev()
    store = get_target_store()
    asyncio.run(store.ensure_schema())  # garante o schema (idempotente)

    conn = store._connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        all_domains = [s["domain"] for s in SITES] + [KL99_VERIFIED_DOMAIN]  # KL-99
        for sector, n in FILLERS.items():
            all_domains += [f"filler-{sector}-{i}.com.br" for i in range(n)]

        # ---- limpeza idempotente (users cascateiam user_sites/subs/vigilias) ----
        cur.execute("SELECT id FROM targets WHERE domain = ANY(%s)", (all_domains,))
        old_ids = [r[0] for r in cur.fetchall()]
        cur.execute("DELETE FROM users WHERE email = ANY(%s)", (SEED_EMAILS,))
        if old_ids:
            cur.execute("DELETE FROM scans WHERE target_id = ANY(%s)", (old_ids,))
            cur.execute("DELETE FROM targets WHERE id = ANY(%s)", (old_ids,))

        # ---- usuários --------------------------------------------------------- #
        pw = auth_users.hash_password(DEV_PASSWORD)
        users = [
            ("dono@exemplo.com.br", "João Silva", True, 5, "owner"),
            ("tecnico@agencia.com.br", "Maria Dev", True, 5, "technician"),
            ("novo@teste.com.br", None, False, 1, "owner"),
        ]
        uids = []
        for email, name, confirmed, max_sites, role in users:
            cur.execute(
                "INSERT INTO users (email, password_hash, name, max_sites, is_active, "
                "  email_confirmed, email_confirmed_at, confirmation_source, role) "
                "VALUES (%s,%s,%s,%s,TRUE,%s,%s,%s,%s) RETURNING id",
                (email, pw, name, max_sites, confirmed,
                 NOW if confirmed else None, "link" if confirmed else None, role))
            uids.append(cur.fetchone()[0])
        u1 = uids[0]

        # ---- assinaturas (u1/u2 Pro trial · u3 Free) -------------------------- #
        cur.execute(
            "INSERT INTO subscriptions (account_id, plan_id, status, trial_ends_at, started_at) "
            "VALUES (%s,'pro','trial',%s,%s)", (u1, NOW + timedelta(days=24), NOW - timedelta(days=6)))
        cur.execute(
            "INSERT INTO subscriptions (account_id, plan_id, status, trial_ends_at, started_at) "
            "VALUES (%s,'pro','trial',%s,%s)", (uids[1], NOW + timedelta(days=30), NOW))
        cur.execute(
            "INSERT INTO subscriptions (account_id, plan_id, status, started_at) "
            "VALUES (%s,'free','free',%s)", (uids[2], NOW))

        # ---- 5 sites reais + scans + histórico + vínculo + perfil ------------- #
        n_scans = 0
        for idx, s in enumerate(SITES):
            domain = s["domain"]
            url = f"https://{domain}"
            sem = s["semaphore"]
            cur.execute(
                "INSERT INTO targets (url, domain, platform, sector, status, site_type, source) "
                "VALUES (%s,%s,'wordpress',%s,'scanned','institucional','seed') RETURNING id",
                (url, domain, s["sector"]))
            tid = cur.fetchone()[0]
            if idx == 0:
                first_site_tid = tid   # KL-99: alvo da verificação de domínio PENDENTE (teste)

            # 10 scans (mais antigo -> mais recente). O último tem os 48 checks.
            trend = s["trend"]
            last_scan_id, last_ts = None, None
            for i, sc in enumerate(trend):
                ts = NOW - timedelta(days=(len(trend) - 1 - i) * 3, hours=12)
                is_last = i == len(trend) - 1
                if is_last:
                    results = _build_results(s["fails"], s["inconclusive"])
                    cj = _checks_json(url, s["score"], sem, results, ts)
                    src = "manual"
                else:
                    hist_sem = _sem_for(sc)
                    cj = _checks_json(url, sc, hist_sem, [], ts)
                    src = "rescan"
                sb = cj["score"]
                cur.execute(
                    "INSERT INTO scans (target_id, url, score, semaphore, pass_count, "
                    "  fail_count, inconclusive_count, checks_json, source, status, scanned_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s) RETURNING id",
                    (tid, url, (s["score"] if is_last else sc), (sem if is_last else _sem_for(sc)),
                     sb["passed"], sb["failed"], sb["inconclusive"], json.dumps(cj), src, ts))
                last_scan_id, last_ts = cur.fetchone()[0], ts
                n_scans += 1

            cur.execute(
                "UPDATE targets SET last_scan_id=%s, last_scan_score=%s, last_scan_at=%s WHERE id=%s",
                (last_scan_id, s["score"], last_ts, tid))

            # perfil comercial público (company_name/phone/descrição).
            cur.execute(
                "INSERT INTO site_profile (target_id, company_name, phone, description, "
                "  business_type, public_visible, edited_by_admin, edited_by_admin_at) "
                "VALUES (%s,%s,%s,%s,%s,TRUE,%s,%s)",
                (tid, s["company"], s["phone"],
                 f"{s['company']} — negócio de exemplo do setor para testes locais.",
                 s["sector"], s["edited"], NOW if s["edited"] else None))

            # vínculo do usuário 1 (dono). added_at DECRESCENTE por índice => o
            # PRIMEIRO da lista (hotel-exemplo) fica com o added_at MAIS RECENTE
            # e vira o "site primário" do dashboard.
            added = NOW - timedelta(minutes=idx)
            cur.execute(
                "INSERT INTO user_sites (user_id, target_id, is_owner, added_at, "
                "  verified_at, verification_method) VALUES (%s,%s,TRUE,%s,%s,'auto_email')",
                (u1, tid, added, added))

            # 2 vigílias por site (ssl + score) => 10 no total.
            ssl_days = 247 if domain == "hotel-exemplo.com.br" else 90 + idx * 10
            cur.execute(
                "INSERT INTO vigilias (user_id, site_domain, tipo, enabled, last_status, "
                "  last_data, last_check_at, next_check_at) VALUES (%s,%s,'ssl',TRUE,'ok',%s,%s,%s)",
                (u1, domain, json.dumps({"ssl_days_remaining": ssl_days}),
                 NOW - timedelta(hours=6), NOW + timedelta(hours=6)))
            if domain == "loja-exemplo.com.br":
                score_status = "critical"
                score_data = {"detail": "Score caiu de 60 para 42", "old_score": 60, "new_score": 42}
            else:
                score_status = "ok"
                score_data = {"current_score": s["score"]}
            cur.execute(
                "INSERT INTO vigilias (user_id, site_domain, tipo, enabled, last_status, "
                "  last_data, last_check_at, next_check_at) VALUES (%s,%s,'score',TRUE,%s,%s,%s,%s)",
                (u1, domain, score_status, json.dumps(score_data),
                 NOW - timedelta(hours=6), NOW + timedelta(hours=6)))

        # ---- sites de preenchimento (benchmark/ranking realistas) ------------- #
        n_fillers = 0
        for sector, count in FILLERS.items():
            for i in range(count):
                domain = f"filler-{sector}-{i}.com.br"
                url = f"https://{domain}"
                score = max(15, min(99, 55 + ((i * 37) % 40) - 20))  # determinístico 15..99
                cur.execute(
                    "INSERT INTO targets (url, domain, platform, sector, status, site_type, "
                    "  source, last_scan_score, last_scan_at) "
                    "VALUES (%s,%s,'unknown',%s,'scanned','institucional','seed',%s,%s) RETURNING id",
                    (url, domain, sector, score, NOW - timedelta(days=5)))
                tid = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO site_profile (target_id, company_name, description, "
                    "  business_type, public_visible) VALUES (%s,%s,%s,%s,TRUE)",
                    (tid, f"Exemplo {sector.title()} {i + 1}",
                     f"Site de exemplo do setor {sector} (preenchimento p/ benchmark).", sector))
                n_fillers += 1

        # ---- KL-99: usuários de teste dos níveis de conta --------------------- #
        # Nível 1 — conta SEM senha (chegou pelo link do alerta → source hmac, e-mail confirmado).
        cur.execute(
            "INSERT INTO users (email, password_hash, name, max_sites, is_active, email_confirmed, "
            "  email_confirmed_at, confirmation_source, role, account_level, source) "
            "VALUES (%s, NULL, %s, 1, TRUE, TRUE, %s, 'hmac', 'owner', 1, 'hmac')",
            ("nivel1@teste.com", "Ana Sem-Senha", NOW))

        # Nível 3 — dono verificado por controle de domínio: conta + Pro trial + site próprio.
        cur.execute(
            "INSERT INTO users (email, password_hash, name, max_sites, is_active, email_confirmed, "
            "  email_confirmed_at, confirmation_source, role, account_level, source) "
            "VALUES (%s, %s, %s, 5, TRUE, TRUE, %s, 'link', 'owner', 3, 'signup') RETURNING id",
            ("dono3@teste.com", pw, "Carlos Dono", NOW))
        u_l3 = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO subscriptions (account_id, plan_id, status, trial_ends_at, started_at) "
            "VALUES (%s,'pro','trial',%s,%s)", (u_l3, NOW + timedelta(days=20), NOW))
        vd = KL99_VERIFIED_DOMAIN
        cur.execute(
            "INSERT INTO targets (url, domain, platform, sector, status, site_type, source, "
            "  last_scan_score, last_scan_at, owner_verified) "
            "VALUES (%s,%s,'wordpress','servicos','scanned','institucional','seed',88,%s,TRUE) "
            "RETURNING id", (f"https://{vd}", vd, NOW - timedelta(days=1)))
        t_v = cur.fetchone()[0]
        vr = _build_results([], [])
        vcj = _checks_json(f"https://{vd}", 88, "amarelo", vr, NOW - timedelta(days=1))
        vsb = vcj["score"]
        cur.execute(
            "INSERT INTO scans (target_id, url, score, semaphore, pass_count, fail_count, "
            "  inconclusive_count, checks_json, source, status, scanned_at) "
            "VALUES (%s,%s,88,'amarelo',%s,%s,%s,%s,'manual','ok',%s) RETURNING id",
            (t_v, f"https://{vd}", vsb["passed"], vsb["failed"], vsb["inconclusive"],
             json.dumps(vcj), NOW - timedelta(days=1)))
        cur.execute("UPDATE targets SET last_scan_id=%s WHERE id=%s", (cur.fetchone()[0], t_v))
        cur.execute(
            "INSERT INTO site_profile (target_id, company_name, phone, description, business_type, "
            "  public_visible, edited_by_admin, edited_by_admin_at) "
            "VALUES (%s,%s,%s,%s,'servicos',TRUE,TRUE,%s)",
            (t_v, "Serviços Verificados Ltda", "(11) 4000-0000",
             "Empresa verificada de exemplo (dono nível 3).", NOW))
        cur.execute(
            "INSERT INTO user_sites (user_id, target_id, is_owner, added_at, verified_at, "
            "  verification_method) VALUES (%s,%s,TRUE,%s,%s,'meta_tag')", (u_l3, t_v, NOW, NOW))
        cur.execute(
            "INSERT INTO ownership_verifications (user_id, target_id, method, token, domain, "
            "  status, verified_at, expires_at) "
            "VALUES (%s,%s,'meta_tag',%s,%s,'verified',%s, NOW() + INTERVAL '7 days')",
            (u_l3, t_v, "seedtok-verified-000000000000000001", vd, NOW))

        # ownership_verification PENDENTE (dono@exemplo no 1º site) — testa o fluxo de verificação.
        cur.execute(
            "INSERT INTO ownership_verifications (user_id, target_id, method, token, domain, "
            "  status, expires_at) "
            "VALUES (%s,%s,'dns_txt',%s,%s,'pending', NOW() + INTERVAL '7 days')",
            (u1, first_site_tid, "seedtok-pending-0000000000000000002", SITES[0]["domain"]))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print(f"Seed: {len(SEED_EMAILS)} users, {len(SITES)} sites, {n_scans} scans, "
          f"10 vigilias criados (+ {n_fillers} sites de preenchimento p/ benchmark).",
          flush=True)
    print("Login de teste: dono@exemplo.com.br / senha dev123456 (nível 2)", flush=True)
    print("KL-99: nivel1@teste.com (sem senha, nível 1) · dono3@teste.com / dev123456 "
          "(dono verificado, nível 3)", flush=True)


if __name__ == "__main__":
    run()
