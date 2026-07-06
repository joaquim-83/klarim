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
| 7. Validação HTTPS + self-scan | ✅ (domínio **klarim.net**) |
| 8. Documentação | ✅ |

> **Domínio:** `klarim.net` (registro A apex + www → `35.238.72.10`, propagado).
> Certificado Let's Encrypt emitido, HTTPS no ar, site validado (ver adendo).

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

- [x] Nginx configurado com HTTPS (443 + redirect 80→443).
- [x] Certificado Let's Encrypt válido (`klarim.net` + `www`, expira 2026-10-04).
- [x] Security headers (HSTS, CSP, XFO, XCTO, Referrer-Policy).
- [x] Renovação automática (certbot.timer + certbot renew no deploy.sh).
- [x] Firewall GCP 443 aberto.
- [x] Endpoints via HTTPS (landing, /api/health).
- [x] Self-scan do Klarim passa (95→**100** após `server_tokens off`).
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

---

## Adendo — Emissão e validação (2026-07-06)

**Domínio:** `klarim.net` — `dig +short klarim.net` → `35.238.72.10` (apex e www,
confirmado via 8.8.8.8). Emissão: `sudo bash deploy/setup-https.sh klarim.net`
(webroot, sem downtime). Cert para `klarim.net` + `www.klarim.net`, expira
**2026-10-04**. `certbot.timer` ativo. `DOMAIN=klarim.net` gravado no `.env` e
`web` recriado em HTTPS (entrypoint detectou o cert).

**Validação (Parte 7):**

| Teste | Resultado |
|-------|-----------|
| `http://klarim.net` | `301 → https://klarim.net/` |
| `https://klarim.net` | `HTTP/2 200` + HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, CSP |
| `https://klarim.net/api/health` | `{"status":"ok"}` |
| Certificado | `CN=klarim.net`, issuer Let's Encrypt, TLS 1.2/1.3, HTTP/2 |

**Self-scan (o Klarim contra si mesmo):** primeira passada **95/100 🟢** — mas o
próprio scanner reprovou o **check 08 (Server header)**: o Nginx expunha
`Server: nginx/1.31.2`. Corrigido com **`server_tokens off;`** nas duas configs.
Após redeploy, re-scan → **100/100 🟢** (13 PASS, 1 INCONCLUSO no check 04 de TLS
legado, que é neutro; 0 FAIL). O Klarim passa nos próprios checks. 🎯

> Ironia proposital do card ("praticar o que prega") validada na prática: o
> self-scan pegou uma exposição real de versão na nossa própria stack.

## Follow-ups

- Refinar a CSP (hoje com `'unsafe-inline'` para script/style por causa da SPA) e
  avaliar `Permissions-Policy`.
- O redirect 80→443 usa `$host`; acesso por IP puro redireciona para
  `https://35.238.72.10` (sem cert) → erro de TLS. Esperado: acessar por domínio.
