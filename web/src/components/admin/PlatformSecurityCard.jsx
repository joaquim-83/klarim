import { useState, useEffect, useCallback, useRef } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { Card, formatDate } from './ui'
import {
  scanSemaphore, findingsSummary, isUnhealthy, scanButtonLabel, triggerMessage,
  severityColor, checkIcon, sortChecks,
} from '../../lib/admin/securityScan'

// KL-160 Parte 3 — "Segurança da plataforma": roda o Security Gate contra o klarim.net a partir do
// painel. Assíncrono (POST dispara, GET /status faz polling). Histórico com detalhe expandível.
// Destaca em vermelho quando score < 80 ou há finding crítico.
export default function PlatformSecurityCard() {
  const [status, setStatus] = useState(null)   // {running, last, history, target}
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState(null)  // {id, results} | {id, loading}
  const pollRef = useRef(null)

  const load = useCallback(async () => {
    try { setStatus(await admin.securityScanStatus()) } catch (e) { setMsg(e.message) }
  }, [])

  useEffect(() => { load() }, [load])

  // Enquanto uma varredura roda, faz polling a cada 5s e para quando termina.
  useEffect(() => {
    if (status?.running && !pollRef.current) {
      pollRef.current = setInterval(load, 5000)
    } else if (!status?.running && pollRef.current) {
      clearInterval(pollRef.current); pollRef.current = null
    }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  }, [status?.running, load])

  async function run() {
    setBusy(true); setMsg('')
    try {
      const resp = await admin.securityScan()
      setMsg(triggerMessage(resp))
      await load()   // pega o running=true e começa o polling
    } catch (e) {
      // 429 (cooldown) vem como erro do req(); extrai a mensagem amigável
      setMsg(/429/.test(e.message) ? 'Aguarde alguns minutos antes de rodar outra varredura.' : e.message)
    } finally { setBusy(false) }
  }

  async function toggle(runId) {
    if (expanded?.id === runId) { setExpanded(null); return }
    setExpanded({ id: runId, loading: true })
    try {
      const d = await admin.securityScanDetail(runId)
      setExpanded({ id: runId, results: d.scan?.results || [] })
    } catch (e) { setExpanded({ id: runId, error: e.message }) }
  }

  const last = status?.last
  const sem = scanSemaphore(last?.score)
  const unhealthy = isUnhealthy(last)
  const history = status?.history || []

  return (
    <Card title="🛡️ Segurança da plataforma">
      <p className="mb-3 text-sm text-klarim-muted">
        Varredura do Security Gate contra <code className="text-klarim-text">{status?.target || 'klarim.net'}</code> — exposição, headers, SSL, rate limit, DNS…
      </p>

      {/* Resumo da última varredura */}
      <div className={`mb-4 rounded-lg border px-4 py-3 ${unhealthy ? 'border-[#F85149]/50 bg-[#F85149]/10' : 'border-klarim-border bg-klarim-bg'}`}>
        {last ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="text-2xl" aria-hidden="true">{sem.dot}</span>
              <div>
                <div className="text-lg font-bold" style={{ color: sem.color }}>
                  {last.score != null ? `${last.score}/100` : 'erro'}
                </div>
                <div className="text-xs text-klarim-muted">Última: {formatDate(last.created_at)}</div>
              </div>
            </div>
            <div className="text-sm font-medium" style={{ color: unhealthy ? '#F85149' : undefined }}>
              {last.error ? `⚠️ ${last.error}` : findingsSummary(last)}
            </div>
          </div>
        ) : (
          <p className="text-sm text-klarim-muted">Nenhuma varredura ainda. Rode a primeira abaixo.</p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={run} disabled={busy || status?.running}
          className="inline-flex min-h-[40px] items-center rounded-lg bg-brand-500 px-4 text-sm font-semibold text-slate-950 transition-colors hover:bg-brand-400 disabled:opacity-50"
          style={{ backgroundColor: '#FF6B35', color: '#0D1117' }}>
          {scanButtonLabel({ running: status?.running, busy })}
        </button>
        {status?.running && <span className="text-sm text-klarim-muted">⏳ rodando… (atualiza sozinho)</span>}
        {msg && <span className="text-sm text-klarim-muted">{msg}</span>}
      </div>

      {/* Histórico com detalhe expandível */}
      {history.length > 0 && (
        <div className="mt-5">
          <h4 className="mb-2 text-sm font-semibold text-klarim-muted">Histórico</h4>
          <div className="divide-y divide-klarim-border/50">
            {history.map((h) => {
              const hs = scanSemaphore(h.score)
              const open = expanded?.id === h.id
              return (
                <div key={h.id}>
                  <button type="button" onClick={() => toggle(h.id)}
                    className="flex w-full items-center gap-3 py-2 text-left text-sm hover:bg-klarim-bg/50">
                    <span className="w-4 text-klarim-muted">{open ? '▾' : '▸'}</span>
                    <span className="w-32 whitespace-nowrap text-klarim-muted">{formatDate(h.created_at)}</span>
                    <span className="w-16 font-bold" style={{ color: hs.color }}>
                      {h.score != null ? `${h.score}/100` : '—'}
                    </span>
                    <span aria-hidden="true">{hs.dot}</span>
                    <span className="flex-1" style={{ color: isUnhealthy(h) ? '#F85149' : undefined }}>
                      {h.error ? `⚠️ ${h.error}` : findingsSummary(h)}
                    </span>
                  </button>
                  {open && (
                    <div className="border-l-2 border-klarim-border px-3 py-2">
                      {expanded.loading ? <p className="text-sm text-klarim-muted">Carregando…</p>
                        : expanded.error ? <p className="text-sm text-[#F85149]">{expanded.error}</p>
                        : (
                          <ul className="space-y-1 text-sm">
                            {sortChecks(expanded.results).map((c, i) => (
                              <li key={i} className="flex items-start gap-2">
                                <span aria-hidden="true">{checkIcon(c.status)}</span>
                                <span className="font-mono text-[10px] uppercase" style={{ color: severityColor(c.severity) }}>[{c.severity}]</span>
                                <span className="font-medium">{c.check}</span>
                                <span className="text-klarim-muted">{c.detail}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Card>
  )
}
