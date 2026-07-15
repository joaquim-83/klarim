import { useState } from 'react'
import { admin, adminDownload } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Button, Badge, formatDate } from './ui'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/ScanDetalhe.jsx (KL-51 fase 2). useParams → pathname;
// Link → <a href>. `id` extraído do pathname (client-only, window sempre existe).
const STATUS_ICON = { PASS: '✅', FAIL: '❌', INCONCLUSO: '⚪' }
const SEV_COLOR = { CRITICA: '#F85149', ALTA: '#FF6B35', MEDIA: '#F0C000', BAIXA: '#58A6FF' }

export default function ScanDetalhePage() {
  const id = window.location.pathname.split('/').filter(Boolean).pop()
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const { data, loading, error } = useAsync(() => admin.scan(id), [id])

  async function genPdf(kind) {
    setBusy(kind)
    setMsg('')
    try {
      await adminDownload(`/scans/${id}/report/${kind}`, `klarim_${kind}.pdf`)
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusy('')
    }
  }

  async function rescan() {
    if (!data?.target_id) return
    setBusy('rescan'); setMsg('')
    try {
      await admin.scanTarget(data.target_id)
      setMsg('Re-scan enfileirado ✓')
    } catch (e) { setMsg(e.message) } finally { setBusy('') }
  }

  let body
  if (loading) body = <Loading />
  else if (error) body = <ErrorBox message={error} />
  else {
    const results = data.checks_json?.results || []
    body = (
      <div className="space-y-5">
        <div className="flex items-center gap-3">
          <a href="/painel/scans" className="text-sm text-klarim-muted hover:text-klarim-text">← Scans</a>
          <h1 className="text-xl font-bold font-mono">{data.url}</h1>
        </div>

        <div className="flex flex-wrap items-center gap-6">
          <SemaphoreDotBig semaphore={data.semaphore} score={data.score} />
          <div className="text-sm text-klarim-muted">
            <div>{data.pass_count} PASS · {data.fail_count} FAIL · {data.inconclusive_count} inconclusivo(s)</div>
            <div>{formatDate(data.scanned_at)}{data.source ? ` · origem: ${data.source}` : ''}</div>
            {data.scanned_by_email && (
              <div>solicitado por <span className="text-klarim-text">{data.scanned_by_email}</span></div>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={busy === 'executive'} onClick={() => genPdf('executive')}>PDF executivo</Button>
            <Button disabled={busy === 'technical'} onClick={() => genPdf('technical')}>PDF técnico</Button>
            {data.target_id && <Button disabled={busy === 'rescan'} onClick={rescan}>Reescanear</Button>}
          </div>
        </div>
        {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

        <Card title={`Checks (${results.length})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Check</th>
                  <th className="py-2 pr-3">Severidade</th>
                  <th className="py-2">Evidência</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} className="border-t border-klarim-border align-top">
                    <td className="py-2 pr-3">{STATUS_ICON[r.status] || r.status}</td>
                    <td className="py-2 pr-3 font-medium">{r.name}</td>
                    <td className="py-2 pr-3"><Badge color={SEV_COLOR[r.severity] || '#8B949E'}>{r.severity}</Badge></td>
                    <td className="py-2 text-xs text-klarim-muted">{r.evidence || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    )
  }

  return <AdminShell active="scans">{body}</AdminShell>
}

function SemaphoreDotBig({ semaphore, score }) {
  const color = { verde: '#00D26A', amarelo: '#F0C000', vermelho: '#F85149' }[semaphore] || '#8B949E'
  return (
    <div className="flex h-24 w-24 flex-col items-center justify-center rounded-full" style={{ border: `7px solid ${color}` }}>
      <span className="text-3xl font-extrabold" style={{ color }}>{score}</span>
      <span className="text-xs text-klarim-muted">/ 100</span>
    </div>
  )
}
