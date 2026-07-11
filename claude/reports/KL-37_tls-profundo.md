# KL-37 — TLS profundo (cipher suites, cert chain, OCSP, força da chave)

**Card:** KL-37 · **Prioridade:** Média.
**Objetivo:** ir além de "certificado válido?" (check_03) e "TLS 1.2+?" (check_04) para
"TLS bem configurado?", nível SSL Labs. 4 checks novos (41–44), todos **A02:2025
Cryptographic Failures**, tier pago. O scanner passa a ter **44 checks**.

---

## Módulo compartilhado — `scanner/tls_analyzer.py`

O requisito central do card: os 4 checks **compartilham um único handshake TLS**, sem
reconectar 4×. `get_tls_info(host, port)` faz **um** handshake (em `asyncio.to_thread`,
com o rate limit por domínio) e **cacheia** o resultado por (host,porta) por ~2min. Como
o runner roda os checks em sequência, o check_41 conecta e os checks 42–44 reusam o mesmo
resultado do cache.

Extrai tudo de uma vez, parseando o DER com **`cryptography`** (já no requirements):
cipher negociado + protocolo (do `ssl`), e do certificado: subject/issuer/SAN/OCSP URI/
validade/self-signed + **força da chave**. Tenta o handshake **verificado**; em erro de
verificação, cai para **não-verificado** para ainda extrair o cert (self-signed/expirado).

Helpers puros (testáveis isolados): `weak_cipher_reason`, `has_forward_secrecy`,
`classify_key`. **Não enumera** todas as suites (exigiria N conexões, mais intrusivo/lento)
— avalia a **negociada** (a que o servidor prefere), abordagem pragmática do card. Usa só
`ssl`/`socket` (stdlib) + `cryptography` — **sem** pyOpenSSL.

## Os 4 checks

| # | Check | Sev. | CWE | Regra |
|---|-------|------|-----|-------|
| 41 | Cipher suites | Alta | CWE-327 | fraco (RC4/DES/3DES/NULL/EXPORT/anon) ou protocolo obsoleto → FAIL ALTA; TLS 1.2 sem forward secrecy → FAIL MÉDIA; TLS 1.3/ECDHE forte → PASS |
| 42 | Certificate chain | Média | CWE-295 | self-signed / cadeia que não valida → FAIL; válido → PASS (nota se expira em <30 dias; mostra emissor + SAN) |
| 43 | OCSP stapling | Baixa | CWE-299 | OCSP URI (AIA) presente → PASS c/ nota; ausente → FAIL BAIXA |
| 44 | Força da chave | Alta/Crítica | CWE-326 | RSA 2048+/ECDSA P-256+ → PASS; RSA 1024 → FAIL ALTA; RSA <1024 → FAIL CRÍTICA |

> **Limitação documentada (OCSP):** a `ssl` stdlib do Python não expõe se o servidor faz
> stapling de fato. Conforme o card, o check_43 reporta a presença do **OCSP URI** no
> certificado (a CA suporta OCSP) — o stapling em si não é verificável nesta análise passiva.

## Classificação e relatórios

`classifications.py`: 4 entradas (A02 + CWE-327/295/299/326 + Art. 46), carimbadas pelo
runner. `RISK_MESSAGES` (executivo: "fechadura que qualquer chaveiro abre em 5 segundos",
"senha de 4 caracteres"), `ACCESSIBLE` e `TECHNICAL` (com o cipher/chave e o fix nginx/
openssl) para os 4. Categorias de risco atualizadas (VAZAMENTO).

## Testes (`tests/test_kl37_tls.py`, 16)

Cipher (TLS 1.3/TLS 1.2 forte/RC4→ALTA/sem-FS→MÉDIA), cadeia (válida/expirando/self-signed/
não-verificada), OCSP (URI presente/ausente), chave (RSA 2048/1024→ALTA/ECDSA P-256),
conexão falha → INCONCLUSO nos 4, classificações, e os helpers puros (`weak_cipher_reason`,
`has_forward_secrecy`, `classify_key`). Mock de `get_tls_info` por check (sem simular
`ssl.SSLSocket`). Contagens ajustadas **40→44 / pagos 25→29** em `test_kl27_funnel.py`,
`test_classifications.py`, `test_checks_16_29.py`.

## Deploy

**Flush `scan:*` no Redis após deploy** (novos checks mudam scores). Docs: `claude.md` §35,
`README.md`. Sem novas dependências (`cryptography` já existia).

## Arquivos

**Novos:** `scanner/tls_analyzer.py`, `check_41_cipher_suites.py`, `check_42_cert_chain.py`,
`check_43_ocsp_stapling.py`, `check_44_key_strength.py`, `tests/test_kl37_tls.py`, este
relatório. **Alterados:** `scanner/checks/classifications.py`, `reporter/generator.py`,
`reporter/risk_messages.py`, `tests/test_checks_16_29.py`, `tests/test_kl27_funnel.py`,
`tests/test_classifications.py`, `claude.md`, `README.md`.
