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
| 1. Provisionar a VM | ⏳ **Handoff ao operador** | Sem acesso ao projeto GCP a partir do ambiente do CLI (ver abaixo). Runbook pronto. |
| 2. Deploy manual | ⏳ **Handoff ao operador** | Idem — requer a VM provisionada. Runbook pronto. |
| 3. GitHub Actions CI/CD | ✅ **Feito** | `.github/workflows/deploy.yml` |
| 4. Script de deploy | ✅ **Feito** | `deploy/deploy.sh` (executável) |
| 5. Documentação | ✅ **Feito** | `claude.md`, `README.md`, este relatório |

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

| Secret | Valor / descrição |
|--------|-------------------|
| `GCP_SA_KEY` | JSON da service account (mín.: `compute.instances.get` + `compute.instances.setMetadata`) |
| `GCP_PROJECT_ID` | `project-b08050df-fa4e-49ac-919` |
| `GCP_INSTANCE` | `instance-20260706-112125` |
| `GCP_ZONE` | `us-central1-a` |
| `SSH_PRIVATE_KEY` | (opcional) caminho SSH direto, alternativa ao `gcloud compute ssh` |

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

- [ ] VM provisionada com Docker/Compose — **pendente do operador** (runbook Parte 1).
- [ ] Projeto rodando na VM via Compose — **pendente do operador** (runbook Parte 2).
- [ ] Scanner e API testados na VM — **pendente do operador** (comandos na Parte 2).
- [x] `.github/workflows/deploy.yml` criado e commitado.
- [x] `deploy/deploy.sh` criado e commitado.
- [x] `claude.md` e `README.md` atualizados.
- [x] Relatório criado.
- [x] Commit e push.

## Ação necessária do operador

1. Rodar os runbooks das Partes 1–2 na VM (ou conceder acesso a uma conta GCP
   para que o CLI os execute).
2. Configurar os GitHub Secrets da tabela acima.
3. Criar a service account de deploy com privilégio mínimo e baixar o JSON para
   `GCP_SA_KEY`.
4. Garantir conectividade SSH do runner à VM (firewall porta 22 ou IAP).
