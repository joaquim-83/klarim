# KL-9 — Cache de scan + feedback de envio de e-mail + UX de download

- **Card Jira:** KL-9
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-7 (pagamentos), KL-8 (e-mail)
- **Commit:** `feat(KL-9): add scan cache, email feedback, and PDF download UX`

---

## Parte 1 — Cache de ScanReport (Redis)

**Problema:** cada download de PDF (e o e-mail) disparava um scan completo
(~30s). Após pagar, o cliente esperava 30s+ por PDF.

**Solução:** `scanner/cache.py` (`ScanCache`) cacheia o `ScanReport` no **Redis**
(instância do compose, `REDIS_URL`) com **TTL 1h**.

- **Chave:** `scan:<sha256(url_normalizada)[:16]>` — url em `lower()`, sem `/`
  final. `https://X.com/` e `https://x.com` compartilham cache.
- **Serialização:** JSON via `ScanReport.to_dict()` + novo `ScanReport.from_dict()`
  (com `CheckResult.from_dict` e `ScoreBreakdown.from_dict`).
- **Integração:** `get_or_scan(url)` — cache hit → instantâneo; miss → `run_scan`
  + grava. Usado por `_safe_scan` (summary, PDFs, e-mail) e pela task de envio.
- **Degradação graciosa:** Redis fora do ar → `get` retorna None, `set` falha em
  silêncio → o scan simplesmente roda de novo. Inicializado no lifespan da API
  (com `ping`); se falhar, a API sobe sem cache.

**Resultado:** 1º scan ~30s; PDF/e-mail seguintes < 3s (cache hit); re-scan
após 1h.

## Parte 2 — Feedback de envio de e-mail

- Coluna nova em `payments`: **`email_status`** (`null|pending|sending|sent|failed`),
  migração `ADD COLUMN IF NOT EXISTS`.
- `GET /payment/status` agora devolve `buyer_email` + `email_status`.
- Transições: create com e-mail → `pending`; pagamento confirmado → `sending`
  (marcado **antes** de agendar a task, evita duplicação e dá feedback imediato);
  task → `sent` ou `failed`.
- **Frontend `/report`:** `EmailStatusBanner` faz polling do status e mostra
  "📧 Enviando para <e-mail>…" (spinner) → "✅ Enviado…" (verde) ou "⚠️ Não foi
  possível enviar…" (amarelo, com os botões de download como fallback).
- **Frontend `/pay`:** ao confirmar, "📧 Enviando relatório para <e-mail>…" e
  redireciona após 2s.

## Parte 3 — UX do botão de download

`DownloadButton`: ao clicar → "Gerando PDF…" com spinner; em erro → o botão fica
**vermelho** "Erro — tentar novamente".

## Validação

- Testes: `tests/test_cache.py` (round-trip JSON, normalização de chave,
  degradação com Redis quebrado) + `email_status` no store. Suíte: **40 passed,
  1 skipped**.
- **Produção (klarim.net):**
  - Log de boot: `[cache] Redis conectado — scans cacheados (TTL 1h)`.
  - **Cache:** `GET /scan/summary` do mesmo alvo — 1ª chamada (miss) **26,0s**;
    2ª chamada (hit) **0,47s** (~55× mais rápido). Como os PDFs usam o mesmo
    `get_or_scan`, o download pós-pagamento fica em ~1-2s (só a geração do PDF).
  - **email_status:** `GET /payment/status` de uma cobrança com e-mail devolveu
    `{"paid":false,"buyer_email":"…","email_status":"pending"}`. As transições
    `sending→sent` foram validadas ponta-a-ponta no KL-8 (auto-send) e o
    `set_email_status` é coberto por teste.

## Critérios de aceite

- [x] `scanner/cache.py` (get/set no Redis, TTL 1h).
- [x] Endpoints de scan/PDF usam cache (`get_or_scan`).
- [x] Download de PDF pós-pagamento < 5s (cache hit).
- [x] `GET /payment/status` retorna `email_status` e `buyer_email`.
- [x] Frontend mostra status do e-mail (pending → sending → sent/failed).
- [x] Botão de download com loading state.
- [x] Cache usa o Redis do compose existente.
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- Invalidar/renovar cache sob demanda (ex.: botão "re-escanear agora").
- Métricas de hit/miss do cache.
