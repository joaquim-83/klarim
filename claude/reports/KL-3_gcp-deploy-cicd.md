# KL-3 — Setup da VM GCP, deploy Docker Compose e CI/CD com GitHub Actions

- **Card Jira:** KL-3
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-2 (checks 13–15, descoberta dinâmica)
- **Commit desta tarefa:** `feat(KL-3): add GCP deploy, Docker setup, and GitHub Actions CI/CD`

---

## Resumo de status

| Parte | Status | Observação |
|-------|--------|------------|
| 1. Provisionar a VM | ✅ **Feito** (2026-07-06) | Docker 29.6.1 + Compose v5.3.0 na VM. Ver adendo "Execução Partes 1–2". |
| 2. Deploy manual | ✅ **Feito** (2026-07-06) | Stack no ar, scanner e API validados. Ver adendo. |
| 3. GitHub Actions CI/CD | ✅ **Feito** | `.github/workflows/deploy.yml` (WIF; `sudo bash deploy.sh`) |
| 4. Script de deploy | ✅ **Feito** | `deploy/deploy.sh` (executável) |
| 5. Documentação | ✅ **Feito** | `claude.md`, `README.md`, este relatório |

> As Partes 1–2 estavam pendentes de acesso GCP; após login com
> `klarimscan@gmail.com` (Owner) elas **foram executadas a partir do CLI**. Os
> runbooks abaixo permanecem como referência; os resultados reais estão no
> adendo final.

### Por que as Partes 1–2 não foram executadas aqui

Nenhuma das contas `gcloud` autenticadas no ambiente do CLI tem permissão no
projeto alvo:

```
$ gcloud compute instances describe instance-20260706-112125 \
    --zone us-central1-a --project project-b08050df-fa4e-49ac-919
ERROR: Required 'compute.instances.get' permission for
  'projects/project-b08050df-fa4e-49ac-919/.../instance-20260706-112125'
```

Contas testadas (todas negadas): `ads.igoove@gmail.com`, `jccidinei@gmail.com`,
`ciditrade.cidinei@gmail.com`, `paodecaixabr@gmail.com`. (Nota: a conta
credenciada é `jccidinei`, diferente do e-mail do operador `jscidinei`.)

Além do acesso, provisionar a VM (`apt upgrade`, instalar Docker) e subir a
stack de produção são operações interativas e outward-facing, próprias do
operador. Abaixo o runbook exato para executá-las.

---

## Parte 3 — GitHub Actions CI/CD (feito)

Arquivo: `.github/workflows/deploy.yml`.

- **Trigger:** push em `main` (+ `workflow_dispatch` para re-run manual).
- **`concurrency`:** serializa deploys (`cancel-in-progress: false`) para não
  sobrepor dois deploys.
- **Job `test`:** `actions/checkout` → `setup-python@v5` (3.12, cache pip) →
  instala libs de sistema do WeasyPrint (defensivo) → `pip install -r
  requirements.txt` → `pytest -q`.
- **Job `deploy`** (`needs: test`, `if: github.ref == 'refs/heads/main'`):
  `google-github-actions/auth@v2` (secret `GCP_SA_KEY`) →
  `setup-gcloud@v2` → `gcloud compute ssh … --command "bash
  /opt/klarim/deploy/deploy.sh"`.

`gcloud compute ssh` injeta uma chave SSH efêmera via metadata da instância
(por isso a SA precisa de `compute.instances.setMetadata`) e conecta pelo IP
externo (porta 22 liberada). Para setup sem porta 22 pública, usar
`--tunnel-through-iap` + `roles/iap.tunnelResourceAccessor` (comentado no YAML).

### Secrets no GitHub (configurar manualmente)

Autenticação **keyless via Workload Identity Federation** (ver adendo abaixo — o
projeto proíbe chaves de SA):

| Secret | Valor |
|--------|-------|
| `GCP_WIF_PROVIDER` | `projects/10946387758/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SA_EMAIL` | `klarim-deploy@project-b08050df-fa4e-49ac-919.iam.gserviceaccount.com` |
| `GCP_PROJECT_ID` | `project-b08050df-fa4e-49ac-919` |
| `GCP_INSTANCE` | `instance-20260706-112125` |
| `GCP_ZONE` | `us-central1-a` |

O repositório **não gera nem armazena** chaves/credenciais.

---

## Parte 4 — `deploy/deploy.sh` (feito)

Roda **na VM**. Faz `git pull --ff-only origin main` → valida que `.env` existe
(nunca sobrescreve) → `docker compose down --remove-orphans` → `up -d --build` →
`docker compose ps` → health check com retry em `http://localhost:8000/health`
(falha o deploy se a API não responder). `set -euo pipefail`. Executável (755).

---

## Parte 1 — Runbook de provisionamento (para o operador)

```bash
# 1. Conectar
gcloud compute ssh --zone "us-central1-a" "instance-20260706-112125" \
  --project "project-b08050df-fa4e-49ac-919"

# 2. Pacotes base
sudo apt update && sudo apt upgrade -y

# 3. Docker (repo oficial Debian)
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER    # reconecte a sessão para aplicar o grupo

# 4. Verificar
docker --version && docker compose version

# 5. Diretório de deploy
sudo mkdir -p /opt/klarim && sudo chown $USER:$USER /opt/klarim
```

## Parte 2 — Runbook do primeiro deploy (para o operador)

```bash
cd /opt/klarim
git clone https://github.com/joaquim-83/klarim.git .

# .env de produção (NÃO commitado). Gere senha forte para o Postgres:
cp .env.example .env
POSTGRES_PW=$(openssl rand -base64 24)
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PW}|" .env
sed -i "s|change-me|${POSTGRES_PW}|g" .env   # ajusta DATABASE_URL derivada
# revise o .env e ajuste os demais valores

# Subir
docker compose up -d --build
docker compose ps
docker compose logs --tail=20

# Testar scanner e API na VM
docker compose exec worker python -m scanner.main https://www.verdegreen.com.br
curl "http://localhost:8000/scan/summary?url=https://www.verdegreen.com.br"
```

Depois do primeiro deploy, os deploys seguintes são automáticos (push em `main`)
ou manuais via `bash /opt/klarim/deploy/deploy.sh`.

---

## Arquivos afetados

| Arquivo | Ação |
|---------|------|
| `.github/workflows/deploy.yml` | criado (CI/CD test + deploy) |
| `deploy/deploy.sh` | criado (executável, deploy na VM) |
| `claude.md` | + dados da VM, acesso SSH, seção 8 (Deploy e CI/CD) |
| `README.md` | + seção "Deploy e infraestrutura" e árvore atualizada |
| `claude/reports/KL-3_gcp-deploy-cicd.md` | este relatório |

## Validações locais

- `bash -n deploy/deploy.sh` → sintaxe OK.
- `deploy.yml` → YAML válido, jobs `test` + `deploy`.
- `pytest -q` → `17 passed, 1 skipped` (o mesmo que o job `test` roda).

## Critérios de aceite

- [x] VM provisionada com Docker/Compose (Docker 29.6.1, Compose v5.3.0).
- [x] Projeto rodando na VM via Compose (4 containers no ar).
- [x] Scanner e API testados na VM (score 86, `/health` ok, `/scan/summary` ok).
- [x] `.github/workflows/deploy.yml` criado e commitado.
- [x] `deploy/deploy.sh` criado e commitado.
- [x] `claude.md` e `README.md` atualizados.
- [x] Relatório criado.
- [x] Commit e push.

## Ação necessária do operador

1. Rodar os runbooks das Partes 1–2 na VM (ou conceder acesso a uma conta GCP
   para que o CLI os execute).
2. Configurar os GitHub Secrets da tabela acima (`GCP_WIF_PROVIDER`,
   `GCP_SA_EMAIL`, `GCP_PROJECT_ID`, `GCP_INSTANCE`, `GCP_ZONE`).
3. Garantir conectividade SSH do runner à VM (firewall porta 22 ou IAP) e que a
   SA `klarim-deploy` consiga fazer `gcloud compute ssh` (metadata SSH key).

---

## Adendo — Service account + Workload Identity Federation (executado)

Após login interativo com `klarimscan@gmail.com` (Owner do projeto), a infra de
autenticação foi criada **a partir do CLI**:

| Passo | Comando | Resultado |
|-------|---------|-----------|
| Criar SA | `gcloud iam service-accounts create klarim-deploy` | ✅ criada |
| Papel | `add-iam-policy-binding … roles/compute.instanceAdmin.v1` | ✅ concedido |
| Chave JSON | `keys create` | ❌ **bloqueado** por org policy |

**Por que não há chave JSON:** o projeto **impõe** a org policy
`constraints/iam.disableServiceAccountKeyCreation` (`enforced: true`). Chaves de
SA baixáveis são proibidas — uma boa prática de segurança. Migramos para
**Workload Identity Federation** (keyless), que é a recomendação do Google para
GitHub Actions e funciona dentro da policy.

**Recursos WIF criados:**

- Pool: `github-pool` (global).
- Provider OIDC: `github-provider`, issuer `https://token.actions.githubusercontent.com`,
  attribute-mapping `google.subject`/`attribute.repository`/`attribute.repository_owner`,
  **attribute-condition** `assertion.repository=='joaquim-83/klarim'`.
- Binding `roles/iam.workloadIdentityUser` na SA `klarim-deploy` para
  `principalSet://…/attribute.repository/joaquim-83/klarim` (só este repo pode
  impersonar a SA).
- APIs habilitadas: `iam`, `iamcredentials`, `sts`, `cloudresourcemanager`.

**Mudança no workflow:** o job `deploy` agora usa
`permissions: id-token: write` + `google-github-actions/auth` com
`workload_identity_provider` + `service_account` (em vez de `credentials_json`).

**Nota de privilégio:** `roles/compute.instanceAdmin.v1` é mais amplo que o
mínimo do card (`compute.instances.get` + `setMetadata`). Foi o papel pedido pelo
operador; para least-privilege estrito, criar um custom role com apenas essas
duas permissões e trocar o binding.

**Pendente do operador:** apenas configurar os 5 GitHub Secrets acima. A infra
GCP de autenticação já está pronta.

---

## Adendo — Execução das Partes 1–2 na VM (2026-07-06)

Executado via `gcloud compute ssh` como `cidineisilva` (usuário Linux da VM),
conta `klarimscan@gmail.com`.

### Provisionamento (Parte 1)

- SO da VM: **Debian 13 (trixie)**; usuário `cidineisilva`; sudo sem senha.
- `apt update && upgrade` + instalação do Docker pelo repo oficial. O Docker
  **publica para trixie** (pacotes `~debian.13~trixie`), então o fallback para
  bookworm no script de provisionamento não foi necessário.
- Versões instaladas: **Docker 29.6.1**, **Docker Compose v5.3.0**.
- `docker` habilitado no boot (`systemctl enable --now docker`).
- `/opt/klarim` criado (owner `cidineisilva`); usuário adicionado ao grupo
  `docker` (login seguinte já acessa o socket sem `sudo`).

### Primeiro deploy (Parte 2)

- Repositório clonado em `/opt/klarim`.
- `.env` de produção gerado **na VM** (senha do Postgres via `openssl rand`,
  **não commitada**; verificado que nenhum `change-me` restou).
- `docker compose up -d --build` — imagem construída, 4 containers no ar.

### Validação

```
$ docker compose ps
NAME              SERVICE   STATUS
klarim-api-1      api       Up
klarim-db-1       db        Up (healthy)
klarim-redis-1    redis     Up (healthy)
klarim-worker-1   worker    Up

$ curl /health                    -> {"status":"ok"}
$ curl /scan/summary?url=verdegreen ->
  {"score":86,"semaphore":"verde","summary":"0 crítico(s), 2 alto(s), 0 médio(s), 0 baixo(s)"}
$ docker compose exec -T worker python -m scanner.main https://www.verdegreen.com.br
  SCORE: 86/100  🟢 (VERDE)   PASS: 12  FAIL: 2  INCONCLUSO: 1
```

Score idêntico ao medido no ambiente local (KL-2) — os 15 checks rodam igual na VM.

### Correção do caminho de deploy do CI (cross-user)

O CI faz login como o **usuário Linux derivado da service account**, que **não é
dono de `/opt/klarim` nem está no grupo `docker`**. Rodar `deploy.sh` sem `sudo`
falharia (git pull sem permissão de escrita; socket do docker inacessível).

Testei o comando exato do CI na VM — `sudo bash /opt/klarim/deploy/deploy.sh` —
e ele rodou **fim a fim** (git pull → `compose down`/`up --build` → `ps` →
health check "API respondeu OK" → "Deploy concluído"). Como o GCE concede sudo
sem senha aos usuários SSH, o workflow foi ajustado para invocar
`sudo bash /opt/klarim/deploy/deploy.sh`. Operador logado como o usuário dono
pode rodar sem `sudo`.

### Observação de segurança de rede

`docker compose` publica `5432` (Postgres) e `6379` (Redis) em `0.0.0.0` na VM.
Se a VM tiver IP público com firewall aberto nessas portas, ficam expostos.
Recomendação (follow-up): restringir esses `ports:` a `127.0.0.1:` no compose de
produção ou fechar as portas no firewall do GCP — API só precisa expor `8000`.
