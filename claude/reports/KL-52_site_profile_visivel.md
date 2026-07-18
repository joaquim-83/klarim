# KL-52 — Expor site_profile no MCP + API + painel admin

**Card:** KL-52 · **Prioridade:** High · **Data:** 2026-07-18

---

## Contexto

A `site_profile` (~10.800 perfis: nome, telefone, WhatsApp, endereço, CNPJ, redes sociais,
descrição, tipo, tags, maturidade, tecnologias, `extraction_sources`, `low_confidence_fields`,
`edited_by_admin`) aparecia na landing pública mas estava invisível internamente. Expor no
MCP, na API admin e no painel.

---

## O que já existia (verificado, sem alteração)

- **Store:** `get_site_profile(target_id)` (`discovery/store.py:1653`) — `SELECT * FROM
  site_profile`.
- **MCP:** tool `get_site_profile` (`mcp_server/tools/targets.py:78`, registrada com
  `@mcp.tool()`) + `get_target` já anexa `profile`. Já listada em `docs/API.md`.
- **API:** `GET /targets/{id}` (`api/main.py:4019`) já anexava `target["profile"]` +
  `classifications` + `owner`. Também existe `GET /targets/{id}/profile`.

Ou seja, **itens 1 e 2 do card já estavam prontos** — só faltava o painel (item 3), a
cobertura de testes e a doc do detalhe.

---

## O que foi feito

### Item 3 — painel: seção "Perfil comercial" no detalhe do alvo

`web/src/components/admin/AlvoDetalhePage.jsx` (painel Astro, ativo pós-KL-51):
- Novo `ProfileCard` (só leitura) após "Dados do alvo", exibindo do `t.profile`: Empresa,
  Tipo, Telefone, WhatsApp, Endereço, CNPJ, redes sociais, Plataforma, Maturidade digital,
  Tecnologias, Fontes de extração e Descrição — cada campo só se tiver valor.
- Aviso ⚠️ dos `low_confidence_fields` e selo ✏️ `edited_by_admin`.
- Botão **"Editar perfil"** que abre o `ProfileEditModal` (KL-67) já existente — antes só
  acessível pela lista de alvos (`AlvosPage`), agora também no detalhe.
- Estado vazio: "Nenhum perfil comercial extraído ainda." (com o botão de edição disponível).

**`contact_email` NUNCA é exibido** — o `ProfileCard` só lê `site_profile` (que não tem
`contact_email`; tem `cnpj`/`whatsapp`, visíveis só ao admin). O e-mail de contato do alvo
continua editável em "Dados do alvo" (comportamento admin existente), não no perfil.

### Doc

- `docs/API.md`: nota de que `GET /targets/{id}` anexa `profile`/`classifications`/`owner`.
- `CLAUDE.md`: entrada KL-52 na referência de cards.

---

## Testes

- **MCP** (`tests/test_mcp_server.py`): `get_site_profile` adicionada à `READ_TOOLS`;
  `test_get_site_profile_tool` (perfil completo) + `test_get_site_profile_tool_not_found`
  (erro sem perfil).
- **API** (`tests/test_target_edit.py`): `test_get_target_includes_profile` (campo `profile`
  presente) + `test_get_target_profile_null_when_missing` (`profile: null`).
- `pytest` → **1011 passed, 1 skipped**. Build Astro verde.

---

## Regras respeitadas

- `contact_email` nunca no response (o perfil vem de `site_profile`, que não o contém).
- Relatório PT-BR; docs atualizadas (`CLAUDE.md`, `docs/API.md`).
