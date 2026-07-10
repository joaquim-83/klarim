// Cliente da API Klarim. Em produção, o Nginx faz proxy de /api -> api:8000.
// Em dev, o proxy do Vite faz o mesmo (ver vite.config.js).
const BASE = import.meta.env.VITE_API_BASE || '/api'

// Token de scan (KL-25): emitido pela verificação de e-mail; autoriza 1 scan.
const SCAN_TOKEN_KEY = 'klarim_scan_token'
export function setScanToken(token) {
  try { sessionStorage.setItem(SCAN_TOKEN_KEY, token) } catch { /* noop */ }
}
function getScanToken() {
  try { return sessionStorage.getItem(SCAN_TOKEN_KEY) || '' } catch { return '' }
}

function jsonPost(path, body) {
  return fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async (r) => ({ status: r.status, data: await r.json().catch(() => ({})) }))
}

// Estado do crédito de scan gratuito + re-verificação do e-mail para a URL.
export async function checkCredit(email, url) {
  const { data } = await jsonPost('/scan/check-credit', { email, url })
  // { has_free_scan, same_url_scanned, free_scans_used, rescan_credits, can_rescan }
  return data
}

// Re-verificação gratuita pós-compra (KL-27): valida o código, consome 1 crédito,
// roda o scan COMPLETO (29) e devolve o resultado completo + comparação. Guarda o
// scan token (full) para baixar os PDFs atualizados.
export async function rescanScan(email, code, url) {
  const { status, data } = await jsonPost('/scan/rescan', { email, code, url })
  if (status === 429) throw new Error(data.detail || 'Muitas tentativas. Aguarde.')
  if (data.status === 'ok' && data.scan_token) setScanToken(data.scan_token)
  return data  // { status: ok | invalid | no_credit, ...summary, comparison, scan_token }
}

// Envia o código de 6 dígitos. Retorna { status, ... }.
export async function requestCode(email, url) {
  const { status, data } = await jsonPost('/scan/request-code', { email, url })
  if (status === 429) throw new Error(data.detail || 'Muitas solicitações. Tente mais tarde.')
  return data  // { status: code_sent | limit_reached | already_scanned }
}

// Verifica o código. Em sucesso guarda o scan token. Retorna { status, ... }.
export async function verifyCode(email, code, url) {
  const { status, data } = await jsonPost('/scan/verify-code', { email, code, url })
  if (status === 429) throw new Error(data.detail || 'Muitas tentativas. Aguarde.')
  if (data.status === 'verified' && data.scan_token) setScanToken(data.scan_token)
  return data  // { status: verified | invalid, scan_token? }
}

// Executa o scan e retorna o resumo executivo (score, semáforo, contagens).
// Envia o scan token (se houver). auth_required => lança 'auth_required' (o
// visitante precisa verificar o e-mail). Atenção: o scan leva ~25-30s.
export async function fetchSummary(url) {
  const token = getScanToken()
  const resp = await fetch(`${BASE}/scan/summary?url=${encodeURIComponent(url)}`, {
    headers: token ? { 'X-Scan-Token': token } : {},
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`Falha na varredura (${resp.status}). ${detail}`)
  }
  const data = await resp.json()
  if (data && data.status === 'auth_required') throw new Error('auth_required')
  return data
}

// Cria uma cobrança PIX para a URL escaneada. Retorna { charge_id, br_code,
// qr_code_base64, amount_display, expires_at, ... }.
export async function createPayment(url, buyerEmail) {
  const resp = await fetch(`${BASE}/payment/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, buyer_email: buyerEmail || undefined }),
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`Falha ao criar cobrança (${resp.status}). ${detail}`)
  }
  return resp.json()
}

// Consulta o status do pagamento. Retorna { status, paid }.
export async function getPaymentStatus(chargeId) {
  const resp = await fetch(`${BASE}/payment/status?charge_id=${encodeURIComponent(chargeId)}`)
  if (!resp.ok) throw new Error(`Falha ao consultar pagamento (${resp.status}).`)
  return resp.json()
}

// URL absoluta de um relatório PDF (kind = "executive" | "technical").
// Anexa o scan token guardado (se houver): um token de re-verificação (full)
// autoriza o PDF sem cobrança (KL-27); tokens gratuitos são ignorados pelo backend.
export function reportUrl(kind, url, chargeId) {
  let u = `${BASE}/report/${kind}?url=${encodeURIComponent(url)}`
  if (chargeId) u += `&charge_id=${encodeURIComponent(chargeId)}`
  const token = getScanToken()
  if (token) u += `&scan_token=${encodeURIComponent(token)}`
  return u
}

async function downloadFromResponse(resp, fallbackName) {
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  const disposition = resp.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : fallbackName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}

// Baixa um PDF de relatório: busca o blob e dispara o download no navegador.
export async function downloadReport(kind, url, chargeId) {
  const resp = await fetch(reportUrl(kind, url, chargeId))
  if (resp.status === 402) throw new Error('Pagamento necessário para baixar o relatório.')
  if (!resp.ok) throw new Error(`Falha ao gerar o PDF (${resp.status}).`)
  await downloadFromResponse(resp, `klarim_${kind}.pdf`)
}

// --- Recuperação de relatórios (token temporário) --- //

export async function recoveryRequest(email) {
  const resp = await fetch(`${BASE}/recovery/request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!resp.ok) throw new Error(`Falha na solicitação (${resp.status}).`)
  return resp.json()
}

export async function recoveryValidate(token) {
  const resp = await fetch(`${BASE}/recovery/validate?token=${encodeURIComponent(token)}`)
  if (!resp.ok) throw new Error(`Falha ao validar (${resp.status}).`)
  return resp.json()
}

export async function recoveryDownload(token, chargeId, kind) {
  const resp = await fetch(
    `${BASE}/recovery/download?token=${encodeURIComponent(token)}&charge_id=${encodeURIComponent(chargeId)}&type=${kind}`,
  )
  if (!resp.ok) throw new Error(`Falha ao baixar (${resp.status}).`)
  await downloadFromResponse(resp, `klarim_${kind}.pdf`)
}
