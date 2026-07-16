# KL-67 — Qualidade do profiler + fix Reply-To emails

**Card:** KL-67 · **Prioridade:** High · **Data:** 2026-07-16

Filtros de qualidade na extração do profiler (telefone/redes/endereço/descrição),
flag de baixa confiança, edição admin de contatos, revalidação retroativa dos ~7,5k
perfis e Reply-To para `scan@klarim.net` em todos os e-mails.

---

## O que foi implementado (por bloco)

### Bloco 1 — Validações no profiler (`scanner/profiler.py`)
Funções **puras** (regex/heurística), aplicadas na extração — **nunca** sobrescrita por IA:
- `validate_phone` — DDDs BR válidos (`VALID_DDDS`); rejeita DDD inexistente (ex.: 04),
  formato errado, celular sem 9; tira o +55.
- `validate_social_handle` — rejeita handles genéricos (`people`/`share`/…, `SOCIAL_REJECTS`),
  URLs de compartilhamento (`SHARE_PATTERNS`) e handles curtos/longos.
- `handle_matches_domain` — heurística fuzzy (substring 4+): o Instagram bate o domínio?
- `validate_address` — rejeita CSS raspado (`navbar`/`d-flex`/…), exige indicador BR (rua/av/cep).
- `validate_description` — rejeita templates genéricos (YouTube/WordPress/lorem) e texto que
  quase certamente não é PT (`_PT_WORDS` distintamente português — removidas as ambíguas
  com inglês, como "a", que davam falso-positivo).
- **`apply_quality_filters(profile, domain)`** — orquestra tudo no fim de `build_profile`
  (todos os chamadores: scan worker + `enrich_profile`), zera lixo e popula
  `low_confidence_fields`.

### Bloco 2 — Baixa confiança + edição admin
- **Schema**: `site_profile.low_confidence_fields TEXT[]` (idempotente). Persistido no
  `upsert_site_profile`.
- **`update_site_profile_fields`** (store): agora edita **contatos** (phone/whatsapp/address/
  instagram/facebook/linkedin/youtube/tiktok) + `clear_fields`; limpa `low_confidence_fields`
  (operador revisou) e marca `edited_by_admin`. `_SP_ADMIN_EDITABLE` expandido → o enrich
  passa a **preservar** contatos corrigidos à mão (regra inviolável #4).
- **API** `PUT /targets/{id}/profile`: `ProfileEditBody` cobre todos os campos + `clear_fields`.
- **MCP** `update_site_profile`: expandida com os campos de contato + `clear_fields`.
- **UI** (`ProfileEditor.jsx`): inputs de contato + botão limpar (✕) + **banner ⚠️** e ícone
  por campo em `low_confidence_fields`.

### Bloco 3 — Reply-To (`notifier/email_client.py`)
- `REPLY_TO_DEFAULT = "scan@klarim.net"`; `_send`/`_send_batch` fazem
  `params.setdefault("reply_to", …)` → **TODO** e-mail (transacional + proativo) ganha o
  header. `send_contact` mantém o seu (e-mail do visitante) — o `setdefault` não sobrescreve.

### Bloco 4 — Revalidação + testes + docs
- **`POST /admin/revalidate-profiles?dry_run=`** (JWT admin, rate limit 5/min): aplica os
  validadores aos perfis **existentes** (sem re-scrape). Pula `edited_by_admin`. dry-run
  conta o impacto; apply zera os inválidos + grava os flags. Store: `list_site_profiles_min`,
  `apply_revalidation`. `adminApi.revalidateProfiles`.
- **Testes**: `tests/test_kl67_profiler.py` (validadores + `apply_quality_filters`);
  `tests/test_notifier.py` (Reply-To no single/batch + preservação no contato).
- **Docs**: `claude.md`, `docs/API.md`, `docs/SECURITY.md`.

---

## Regras invioláveis (respeitadas)
1. **IA nunca sobrescreve regex** — os validadores são filtros na extração (lixo → NULL);
   a IA só preenche campo NULL depois.
2. `contact_email` nunca exposto (endpoints públicos intocados).
3. Scanner/profiler seguem **passivos** (GET público).
4. **`edited_by_admin` nunca é sobrescrito** — a revalidação pula esses perfis; o enrich
   preserva os contatos editados à mão.
5. Rate limit Redis+fallback no `revalidate-profiles`.

## Desvios / decisões
- **`twitter`** não foi incluído: não há coluna `twitter` em `site_profile` e o profiler não
  extrai — ficaria um campo morto. Os demais contatos do card foram todos cobertos.
- A **UI de revalidação** ficou como função no `adminApi` (`revalidateProfiles`) + endpoint;
  não adicionei botão dedicado (o disparo é pontual/operacional). O dry-run roda por API/MCP.
- **Logging por rejeição** (o card sugeria `logger.debug` por campo): **não** implementado —
  a 7,5k perfis × N campos seria ruído. O `revalidate-profiles` reporta as **contagens**
  agregadas (é o que interessa para calibrar).

## Dry-run em produção
Números de `POST /admin/revalidate-profiles?dry_run=1` (campos que seriam limpos por tipo)
serão coletados **após o deploy** e anexados aqui / reportados ao dono antes do apply.

## Testes
Suíte offline **verde** (`pytest`). Validadores e Reply-To cobertos.

## Arquivos
- **Backend:** `scanner/profiler.py`, `discovery/store.py`, `api/main.py`,
  `notifier/email_client.py`, `mcp_server/tools/targets.py`.
- **Frontend:** `web/src/components/admin/ProfileEditor.jsx`, `web/src/lib/admin/adminApi.js`.
- **Testes/Docs:** `tests/{test_kl67_profiler,test_notifier}.py`, `claude.md`,
  `docs/{API,SECURITY}.md`, este relatório.
