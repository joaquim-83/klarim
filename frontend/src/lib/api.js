// Cliente da API Klarim. Em produção, o Nginx faz proxy de /api -> api:8000.
// Em dev, o proxy do Vite faz o mesmo (ver vite.config.js).
const BASE = import.meta.env.VITE_API_BASE || '/api'

// Executa o scan e retorna o resumo executivo (score, semáforo, contagens).
// Atenção: o scan leva ~25-30s (rate limit de 1 req/s por domínio).
export async function fetchSummary(url) {
  const resp = await fetch(`${BASE}/scan/summary?url=${encodeURIComponent(url)}`)
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`Falha na varredura (${resp.status}). ${detail}`)
  }
  return resp.json()
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
export function reportUrl(kind, url, chargeId) {
  let u = `${BASE}/report/${kind}?url=${encodeURIComponent(url)}`
  if (chargeId) u += `&charge_id=${encodeURIComponent(chargeId)}`
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
