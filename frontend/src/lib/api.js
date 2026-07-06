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

// URL absoluta de um relatório PDF (kind = "executive" | "technical").
export function reportUrl(kind, url) {
  return `${BASE}/report/${kind}?url=${encodeURIComponent(url)}`
}

// Baixa um PDF de relatório: busca o blob e dispara o download no navegador.
export async function downloadReport(kind, url) {
  const resp = await fetch(reportUrl(kind, url))
  if (!resp.ok) throw new Error(`Falha ao gerar o PDF (${resp.status}).`)
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  const disposition = resp.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : `klarim_${kind}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}
