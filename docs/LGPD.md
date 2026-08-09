# ROPA — Registro de Operações de Tratamento de Dados Pessoais

Documento **interno** de conformidade LGPD do Klarim (Lei nº 13.709/2018). Não é uma página
pública — o canal público de direitos é `klarim.net/lgpd` (KL-161).

## Tabela de tratamento

| Dado | Titular | Finalidade | Base legal | Retenção | Operadores |
|---|---|---|---|---|---|
| Headers, DNS, SSL de sites | Sites públicos | Análise de segurança | Legítimo interesse (Art. 7º, IX) | Enquanto o perfil existir | — |
| E-mail, senha (hash) | Usuários registrados | Conta e autenticação | Consentimento | Enquanto a conta ativa | Resend |
| CPF, endereço, telefone | Devs (Security Gate) | KYC e auditoria | Consentimento | Conta ativa + 30 dias após cancelamento | — |
| IP, user-agent | Visitantes | Segurança e logs | Legítimo interesse | 90 dias (depois anonimizado) | — |
| Audit log (CPF + IP + URL scaneada) | Devs (Security Gate) | Auditoria e prevenção de abuso | Legítimo interesse | 2 anos | — |
| Cookies de analytics | Visitantes | Métricas de uso agregadas | Consentimento (opt-in) | Conforme a Política de Cookies | Google Analytics |
| Verificação de deliverability de e-mail | Titulares de e-mail | Reduzir bounce/reputação | Legítimo interesse | Cache temporário | Reoon |
| Solicitações LGPD (nome, e-mail, CPF, descrição) | Qualquer titular | Atender ao exercício de direitos | Obrigação legal (LGPD Art. 18) | Enquanto necessário ao atendimento + prova de conformidade | — |

## Encarregado (DPO)

Contato: formulário em **klarim.net/lgpd** ou **privacidade@klarim.net**.

## Canal de direitos (DSAR)

Formulário público em **klarim.net/lgpd** → `POST /api/lgpd/request` (grava em `lgpd_requests`,
confirma ao titular por e-mail e notifica o operador). Tipos atendidos: acesso, correção,
exclusão, portabilidade, revogação de consentimento e "outra".

**Prazo de resposta: até 15 dias úteis**, conforme a regulamentação da ANPD.

Fluxo operacional das solicitações (`lgpd_requests.status`):
`pending` → `in_progress` → `resolved` / `denied`. O e-mail do titular é o Reply-To da
notificação ao operador — basta responder para tratar do caso.

## Notas técnicas

- Remetente dos e-mails LGPD: `privacidade@klarim.net` (env `LGPD_FROM_EMAIL`). O domínio
  `klarim.net` já é verificado no Resend — não é preciso "alias" técnico para **enviar**.
- **Recebimento** em `privacidade@klarim.net`: se o domínio não tiver MX/forwarding configurado,
  o endereço é apenas remetente e o **formulário `/lgpd` é o canal oficial de recebimento** (as
  solicitações ficam gravadas em `lgpd_requests` e são notificadas ao operador em
  `klarimscan@gmail.com`, env `LGPD_ADMIN_EMAIL`).
- Rate limit do canal: 3 solicitações por e-mail por dia.
- `contact_email`, `cnpj`, `whatsapp` de sites **nunca** são expostos na API/perfil público.
- IP dos logs de acesso é anonimizado após 90 dias (trunca o último octeto IPv4 / /48 IPv6).

## Última atualização

Agosto de 2026 (KL-161).
