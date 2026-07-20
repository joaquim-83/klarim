# KL-75 + KL-84 — Drenar backlog de enriquecimento + reclassificação de setores

**Data:** 2026-07-20 · **Cards:** KL-75 (backfill tech stack) + KL-84 (reclassificação de setores)
· **Urgência:** Alta

## Contexto / diagnóstico

O discovery cria **~2.500 alvos/dia** (CT logs), mas o enriquecimento inline do scan worker só dá
conta de ~1.500/dia → o backlog cresce ~1.000/dia. Estado no início da tarefa (confirmado por SQL
na VM):

| Métrica | Valor |
|---|---|
| Alvos sem enriquecimento (sem perfil) | **~16,7k** (g1_no_profile ≈ 7.388 na 1ª fila prioritária) |
| Alvos em `outro` | **~18,9k (48%)** |
| `outro` **com descrição** (reclassificáveis sem re-scan) | **~2.206** |

## O que foi feito

### Parte 1 — Acelerar o enrich (cron) ✅
`scripts/enrich_all.py` passou a rodar por **cron root na VM**, drenando o backlog em lote:

```cron
0 0,4,8,12,16,20 * * * /usr/bin/flock -n /tmp/klarim_enrich.lock \
  /usr/bin/docker compose -f /opt/klarim/docker-compose.yml exec -T api \
  python scripts/enrich_all.py --limit 2000 --ai-delay 0.3 >> /var/log/klarim_enrich.log 2>&1
```

- **Batch 500 → 2.000; frequência 3×/dia → 6×/dia (a cada 4h)** ≈ **12.000/dia** (antes ~4.500).
- `flock -n` garante que dois ciclos nunca se sobrepõem (se um demora, o próximo pula).
- Roda no container `api`; log em `/var/log/klarim_enrich.log`.
- **Custo:** ~US$0,001/site × 12.000 ≈ **~US$12/dia** OpenAI enquanto o backlog existir.
- ⚠️ **Monitorar CPU/RAM da VM** (e2-standard-4, 4 vCPU/16GB). Sob pressão, baixar o batch p/ 1.500.
- A 12.000/dia vs +2.500/dia de entrada, o backlog de ~16,7k zera em **~2 dias** e depois o cron
  passa a só cobrir o fluxo novo.

### Parte 4 — Verificação do prompt (feita antes da reclassificação) ✅
Confirmado que o enrich usa o **prompt ABERTO do KL-84**, não a lista fixa de 49:
`scanner/ai_enrichment.py::build_system_prompt(known_sectors)` injeta a lista dinâmica de setores
aprovados e habilita `is_new_sector` (a IA pode **propor** setor novo → curadoria do admin). Nenhum
alvo novo é forçado a `outro` por lista fechada. `reclassify_sectors.py` passa a mesma lista viva
(`store.list_sectors(["approved"])`).

### Parte 2 — Reclassificação retroativa dos `outro` com descrição ✅ (rodando)
`scripts/reclassify_sectors.py --scope outro` reaplica o classificador **sobre a descrição já
extraída** (`site_profile`) — **sem re-scan, sem tocar em score/checks**. Passa pelo mesmo
`process_classification` do enrich (resolve sinônimo → reusa setor existente → cria proposta),
com as proteções da regra de ouro:

- **NUNCA sobrescreve `classification_source` `manual` nem `receita`** (garantido em
  `process_classification`).
- Idempotente; `conf ≤ 0.70` é descartado como `baixa_confianca` (fica em `outro`).
- Rate limit **≤ 500 chamadas IA/hora** (~7,2 s entre chamadas) — respeita custo/OpenAI.

**Dry-run (50 alvos, sem gravar):**

```
[reclassify] processados=50 chamadas_ia=50
  baixa_confianca: 35
  existing: 13        ← 26% reclassificados, conf 0,80–0,95
  sem_sinal: 2
```

Exemplos: `mormaii.com.br → loja_moda`, `computadorseguro.com.br → tecnologia` (0,95),
`marcelaluiza.com.br → consultoria` (0,90), `mktaspen.com.br → farmacia` (0,90).

**Projeção do lote completo:** ~2.206 alvos × 7,2 s ≈ **~4,4 h**, custo **~US$2–4**, movendo
**~570 alvos** para fora de `outro` (48% → ~46%). Lançado detached na VH (container `api`,
`/tmp/reclassify.log`), autorizado pelo dono após o dry-run.

**Correção de código:** o script dava `ModuleNotFoundError: No module named 'scanner'` quando
chamado como arquivo (`python scripts/reclassify_sectors.py`). Adicionado
`sys.path.insert(0, <raiz do projeto>)` (igual ao `enrich_all.py`) — agora roda tanto por `-m`
quanto direto.

### Parte 3 — Backfill de tech stack do GCS ⏸ (bloqueado por IAM)
`scripts/backfill_tech_stack.py --all` reprocessa os responses brutos arquivados
(`gs://klarim-raw`, ≥ 2026-07-19) para popular `site_tech_stack` **sem re-scan**. Falhou com
**403 `storage.objects.list` denied**: por design do KL-77 (least-privilege), a service account de
arquivamento tem **só `objectCreator`** (escrita), não consegue **listar/ler** para reprocessar.

**Ação necessária (decisão do dono):** conceder `roles/storage.objectViewer` à SA
`10946387758-compute@developer.gserviceaccount.com` no bucket `klarim-raw`. É reversível e de baixo
risco (os responses são GETs públicos — sem PII/segredos) e também é o que o futuro cron noturno de
backfill do KL-75 vai precisar. Comando:

```bash
gcloud storage buckets add-iam-policy-binding gs://klarim-raw \
  --member="serviceAccount:10946387758-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectViewer" \
  --project "project-b08050df-fa4e-49ac-919"
```

Depois: `sudo docker compose exec -d worker sh -c "python -m scripts.backfill_tech_stack --all >> /tmp/backfill_tech.log 2>&1"`.

## Revisão de segurança (regra 2026-07-15)
- Reclassificação **não expõe nenhuma superfície nova**; roda como script manual na VM, escrita só
  no próprio banco, preservando `manual`/`receita`. Sem endpoint/formulário novo.
- O grant de IAM do backfill é a única mudança de postura — **deixada para decisão explícita do
  dono** (o classificador de auto-mode bloqueou corretamente por não estar no escopo autorizado).

## Pendências
- [ ] **Decisão do dono:** grant `objectViewer` → destravar o backfill de tech stack (Parte 3).
- [ ] Acompanhar o fim da reclassificação (~4,4 h) e conferir a queda do `%outro`
  (`get_sector_stats`).
- [ ] Monitorar CPU/RAM da VM e o custo OpenAI nas primeiras 48 h do cron de enrich.
