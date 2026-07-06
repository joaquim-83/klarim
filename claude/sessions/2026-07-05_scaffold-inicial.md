# Sessão — Scaffold inicial do Klarim

- **Data:** 2026-07-05
- **Tipo:** Execução (Claude CLI) + validação de mercado (Claude chat)
- **Commit resultante:** `325e6d0 — feat: initial scaffold with 12 security checks, CLI, API, and Docker setup`

---

## O que foi feito

Criado o **scaffold inicial** do Klarim a partir da especificação
(`klarim_mvp_spec.md`):

- **Checks passivos** de segurança (12 nesta sessão de scaffold; o conjunto é
  dinâmico e cresceu depois — ver [KL-2](../reports/KL-2_checks-13-15-supply-chain.md)),
  cada um seguindo a interface `async def check(url) -> CheckResult` e
  registrados em ordem em `scanner/checks/__init__.py`:
  1. HTTPS ativo · 2. HSTS · 3. Certificado SSL válido · 4. TLS 1.2+ only ·
  5. CSP · 6. X-Frame-Options · 7. X-Content-Type-Options · 8. Server header ·
  9. Source maps expostos · 10. Arquivos sensíveis · 11. Directory listing ·
  12. Meta tags default.
- **Engine:** `runner.py` (orquestra os checks em sequência + score),
  `scoring.py` (score 0–100 ponderado por severidade, semáforo 🔴🟡🟢),
  `checks/base.py` (`CheckResult`, rate limit 1 req/s por domínio, timeout 10s,
  User-Agent honesto).
- **CLI + Worker:** `scanner/main.py` (`python -m scanner.main <url>` e modo
  `--worker` consumindo fila Redis).
- **API FastAPI:** `api/main.py` (`/scan` técnico, `/scan/summary` semáforo grátis).
- **Infra:** `docker-compose.yml` (PostgreSQL + Redis + API + Worker),
  `Dockerfile`, `.env.example`, `.gitignore`, `requirements.txt`, `README.md`.
- **Testes:** `tests/test_checks.py` — 6 unit tests offline + 1 teste online opt-in.

## Resultado da validação técnica

- **Scan real em `https://www.verdegreen.com.br`: 100/100 🟢** (11 PASS, 1
  INCONCLUSO, 0 FAIL), em ~24s.
- O spec registrava o Verdegreen com **55/100 e "ausência total de security
  headers"**; o site **foi corrigido desde a validação manual** — agora tem HSTS
  (`max-age=31536000; preload`), CSP (`frame-ancestors`), `X-Frame-Options` e
  `nosniff`. O scanner leu o estado **real e atual** corretamente.
- O único `INCONCLUSO` (check 04, TLS legado) é esperado: o OpenSSL local (3.6.2)
  não negocia TLS 1.0/1.1, então não dá para testar aceitação de protocolo legado
  nesse ambiente — tratado explicitamente em vez de fingir `PASS`.

## Gaps de cobertura identificados

O caso dos hotéis Duda mostra que os checks então existentes **não cobrem** os achados
mais graves daquele padrão. Faltam checks **13–15** (candidatos a próximos cards):

- **13 — Subresource Integrity (SRI):** scripts externos sem atributo `integrity`.
- **14 — Supply chain / origem de scripts:** scripts carregados de repositórios
  pessoais (ex.: GitHub Pages) ou buckets S3 públicos.
- **15 — Domínios externos:** inventário de terceiros carregando scripts (quanto
  maior, maior a superfície de ataque).

## Modelo de negócio (validado nesta sessão)

- **Bottom-up:** vende barato ao dono do negócio (**R$ 19–49**, decisão de
  impulso); o dono encaminha à agência; a agência procura o Klarim organicamente.
- **Discovery por fingerprint de plataforma:** um insight ("sites Duda no
  turismo") gera um pipeline de centenas de leads com vulnerabilidades de padrão
  repetível.

## Casos validados (referência de mercado)

- **Green Condomínio** — SaaS condominial em CRA; dados biométricos (LGPD Art. 11),
  HTTP sem cripto, headers ausentes, subdomínios enumeráveis. Severidade **alta**.
- **ImgBB (cardápio de café)** — metadados expostos, download irrestrito.
  Severidade **baixa** (porta de entrada para conscientização).
- **360 Suítes** — 1.500+ unidades; repo GitHub público com IaC (Terraform/Ansible),
  backoffice exposto, credenciais AWS. Severidade **alta** (controla fechaduras).
- **3 hotéis Duda em João Pessoa** (CheckinWeb 70, Verdegreen 55, Atlântico 40) —
  vulnerabilidades sistêmicas da plataforma, não escolhas dos hotéis. Validou o
  discovery por fingerprint.

## Próximos passos

- **KL-1** (esta governança): criar `claude.md`, `/claude/` e setup de relatórios.
- Futuro: implementar checks 13–15; geração de PDF (WeasyPrint); Discovery Worker.
