# KL-6 — HTTPS com Let's Encrypt no Nginx

- **Card Jira:** KL-6
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-5 (web + Nginx)
- **VM:** `instance-20260706-112125`, IP `35.238.72.10`
- **Commit:** `feat(KL-6): add HTTPS with Let's Encrypt, security headers, and auto-renewal`

---

## Resumo de status

| Parte | Status |
|-------|--------|
| 1. Abordagem (Opção A — Certbot no host) | ✅ |
| 2. Nginx HTTPS + security headers | ✅ (templates) |
| 3. Compose: 443 + volumes | ✅ |
| 4. `deploy/setup-https.sh` | ✅ |
| 5. Firewall GCP 443 | ✅ (`klarim-allow-https`) |
| 6. Renovação no `deploy.sh` | ✅ |
| 7. Validação HTTPS + self-scan | ⏳ **depende do domínio** |
| 8. Documentação | ✅ |

> **Bloqueio real:** Let's Encrypt não emite certificado para IP — exige um
> domínio com registro A apontando para `35.238.72.10`. O domínio não foi
> informado neste prompt. **Todo o setup está pronto e parametrizado por
> `DOMAIN`**; falta só emitir o certificado (1 comando) quando o domínio existir.
> O site **continua no ar em HTTP** — nada foi quebrado.

---

## Decisão de arquitetura (desvio consciente do card)

O card (Parte 2) mostrava um `frontend/nginx.conf` estático já com HTTPS. **Commitar
isso quebraria o site**: o Nginx não sobe se `ssl_certificate` aponta para um
arquivo que ainda não existe, e o deploy automático (KL-3) recriaria o container
sem certificado → site fora do ar.

**Solução — Nginx self-healing por entrypoint.** Um hook
(`frontend/docker-entrypoint.d/40-klarim-tls.sh`) escolhe a config em runtime:

- `DOMAIN` vazio **ou** sem certificado no disco → **`nginx/http.conf`** (HTTP na
  80 + webroot do ACME). É o estado atual — deploy nunca quebra.
- `DOMAIN` definido **e** certificado presente → **`nginx/https.conf.template`**
  (envsubst do `${DOMAIN}`): redirect 80→443, `listen 443 ssl` e os headers.

Assim, o primeiro deploy sobe em HTTP; após `setup-https.sh` emitir o cert e
recriar o `web`, ele passa a HTTPS sozinho. **Zero downtime, à prova de deploy.**

## Componentes entregues

- **`frontend/nginx/http.conf`** e **`frontend/nginx/https.conf.template`** —
  validados com `nginx -t` (container descartável na rede do compose, cert dummy
  para o HTTPS). Ambos OK.
- **Security headers no HTTPS:** `Strict-Transport-Security` (com `includeSubDomains;
  preload`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `Content-Security-Policy`.
  Também TLS 1.2+ apenas (`ssl_protocols TLSv1.2 TLSv1.3`).
- **`frontend/Dockerfile`** — instala `gettext` (envsubst), copia templates +
  entrypoint, expõe 80/443.
- **`docker-compose.yml`** — `web` com portas 80/443, `DOMAIN` env, volumes
  `/etc/letsencrypt` e `/var/www/certbot` (ro).
- **`deploy/setup-https.sh`** — instala Certbot, emite via **webroot** (apex +
  www com fallback só-apex), grava `DOMAIN` no `.env`, recria o `web` em HTTPS.
- **`deploy/deploy.sh`** — `certbot renew --quiet` com deploy-hook que recria o
  `web` (no-op se não é hora / certbot ausente).
- **`.env.example`** — variável `DOMAIN`.
- **Firewall GCP:** `klarim-allow-https` (tcp:443, tag `http-server`).

## Como concluir (operador — quando o domínio existir)

1. Registrar o domínio e criar registro **A**: `dominio → 35.238.72.10` (e
   `www` opcional). Conferir: `dig <dominio> +short` → `35.238.72.10`.
2. Na VM: `sudo bash /opt/klarim/deploy/setup-https.sh <dominio>`.
3. Validar (Parte 7): `curl -I http://<dominio>` (301→https),
   `curl -I https://<dominio>` (200 + headers), `https://<dominio>/api/health`,
   e navegador (cadeado). Rodar o self-scan do Klarim contra o próprio domínio.

## Critérios de aceite

- [x] Nginx configurado com HTTPS (443 + redirect 80→443) — via template, ativa com cert.
- [ ] Certificado Let's Encrypt válido — **pendente do domínio**.
- [x] Security headers (HSTS, CSP, XFO, XCTO, Referrer-Policy) — na config HTTPS.
- [x] Renovação automática (certbot renew no deploy.sh + timer do pacote).
- [x] Firewall GCP 443 aberto.
- [ ] Endpoints via HTTPS — **pendente do domínio**.
- [ ] Self-scan do Klarim passa — **pendente do domínio**.
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- Assim que o domínio existir, emitir o cert e preencher a Parte 7 (adendo).
- Considerar o header `Permissions-Policy` e refinar a CSP (hoje com
  `'unsafe-inline'` para script/style por causa da SPA).
