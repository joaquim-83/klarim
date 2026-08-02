# KL-138 — Hardening: remover exposição de endpoints + bloquear exploit paths + redirect curto

**Data:** 2026-08-02

Varredura de segurança em 02/08 achou 2 exposições (bots já sondavam `.env`: 8 views de 1 IP).
A Klarim escaneia a segurança de outros sites — precisa estar impecável na própria.

---

## Fix 1 (Alta) — `GET /` deixou de expor o mapa da API

`api/main.py::root()` devolvia **sem auth** a superfície completa: lista de endpoints
(pagamento/e-mail/webhook), `scanner_version`, `payments_enabled`, `email_enabled`, `dev_mode`.
Como o nginx serve `/api/` → FastAPI `/`, era público. **Corrigido:** resposta mínima
`{"name": "Klarim API", "status": "ok"}` — nenhuma flag, versão ou endpoint.

## Fix 2 (Média) — Nginx bloqueia mais paths de exploit ANTES do fallback SPA

O fallback SPA do Astro devolvia **200 + HTML** para QUALQUER path não reconhecido → scanners
interpretam 200 como endpoint ativo e intensificam. Novo `location ~*` (em `http.conf` **e**
`https.conf.template`) devolve **404**:

```nginx
location ~* ^/(wp-config|phpmyadmin|swagger|redoc|graphql|_debug|config\.(json|yml|php)|dump\.sql|
    database\.sql|xmlrpc\.php|cgi-bin|shell|eval-stdin|vendor/phpunit|actuator|api-docs|v[23]/api-docs) {
    return 404;
}
```

Complementa os blocos JÁ existentes (que cobriam `.env`/`.git`/`.DS_Store`/`.htaccess`/`.htpasswd`
via `location ~ /\.`, `.php`/`.sql`/… por extensão, e `wp-admin`/`phpinfo`/`server-status`). Validado
com **`nginx -t`** (http.conf + https.conf.template renderizado com cert dummy, replicando o job de CI)
e por teste unitário Python: bloqueia todos os exploit paths e **NÃO** pega os legítimos
(`/a/`, `/site/`, `/setores`, `/scan`, `/blog`, `/api/account/me`, `/assets/`, `/painel/`).

## Fix 3 — Redirect curto `/a/{target_id}` para e-mails

Substitui o link direto dos e-mails (KL-137) por um link curto rastreável server-side.

**Backend (`api/main.py`):** `GET /a/{target_id}` → valida (inteiro, senão 422), busca o domínio
(`store.get_target_domain`; inexistente/descartado → 404), registra o clique e responde **302 →
`/site/{domain}`**. **Store (`discovery/store.py`):** tabela nova `email_clicks`
(`target_id`/`clicked_at`/`ip_masked`, 2 índices) + `get_target_domain` + `log_email_click`.

**Templates:** os 3 cold (`cold_alert.report_link(target_id)` via `build_cold_email(..., target_id=)`)
e o `profile_view` (`build_profile_view_text(domain, target_id)`) passaram a emitir `klarim.net/a/
{target_id}` — **sem UTM** (o rastreio virou server-side). Os call sites do `alert_worker` e o
`_profile_view_notify` (api) já dispõem do `target_id`.

**Nginx:** `location ~ ^/a/` → FastAPI sem strip (mesmo padrão do `/remover`), em ambos os configs.

### Revisão de segurança (obrigatória)
- **Sem open redirect:** o destino é FIXO — o domínio vem de `targets` (não de parâmetro de URL);
  `?url=…&next=…` são ignorados (teste `test_redirect_only_to_site_no_open_redirect`).
- **Anti-enumeração:** rate limit **30/min por IP** (`_redis_allow`, fallback in-memory) → 429.
- **LGPD:** o IP é **mascarado /24** antes de gravar (`mask_ip(ip, 3)`, mesmo padrão do KL-92); o IP
  completo nunca é persistido. Log de clique = só `target_id` + timestamp + IP mascarado.
- **Robustez:** o log de clique roda em try/except — uma falha de INSERT nunca derruba o redirect.
- **Regex nginx:** validado com `nginx -t` (regex inválido derruba o site).

## Testes

- **Novo `tests/test_kl138_hardening.py` (8):** root minimalista + não vaza superfície; redirect
  302→/site, log com IP mascarado /24, 404 (inexistente), 422 (não-inteiro), 429 (31ª/min),
  sem open redirect.
- Testes de e-mail atualizados (cold + profile_view) para o link curto `/a/{target_id}` sem UTM.
- Regex do nginx validado por Python (bloqueia exploit, preserva os legítimos).
- **1956 pytest passed, 1 skipped** (suite completa).

## Deploy / pós-deploy

- Commit + push → CI (Test + Build web + **Nginx config check** + Deploy). O job de nginx roda
  `nginx -t` nos dois configs — gate obrigatório (regex inválido = site fora).
- Nenhum flush Redis; a tabela `email_clicks` é criada no `ensure_schema` no boot da API.
- **Monitorar:** `email_clicks` deve começar a receber cliques; os 404 de exploit devem aparecer nos
  logs do nginx (bots) sem tocar o app.
