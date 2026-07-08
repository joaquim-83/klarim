import { useState } from 'react'
import { Link } from 'react-router-dom'
import { admin, adminDownload } from '../../lib/adminApi'
import { Card, Button, ErrorBox, Spinner, Badge, PlatformBadge } from '../../components/admin/ui'

const STATUS_ICON = { PASS: '✅', FAIL: '❌', INCONCLUSO: '⚪' }
const SEV_COLOR = { CRITICA: '#F85149', ALTA: '#FF6B35', MEDIA: '#F0C000', BAIXA: '#58A6FF' }
const SEM_COLOR = { verde: '#00D26A', amarelo: '#F0C000', vermelho: '#F85149' }

export default function Escanear() {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [modal, setModal] = useState(false)

  async function scan() {
    setBusy(true); setError(''); setResult(null)
    try {
      const r = await admin.scanAndReport({ url: url.trim() })
      setResult(r)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-bold">Escanear</h1>

      {/* Etapa 1 — input */}
      <Card>
        <div className="flex flex-wrap gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && url.trim() && !busy && scan()}
            placeholder="Digite a URL para escanear (ex.: https://www.exemplo.com.br)"
            className="min-w-64 flex-1 rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
          />
          <Button variant="primary" size="md" disabled={busy || !url.trim()} onClick={scan}>
            {busy ? 'Escaneando…' : 'Escanear agora'}
          </Button>
        </div>
        {busy && (
          <div className="mt-4 flex items-center gap-3 text-sm text-klarim-muted">
            <Spinner size={20} /> Varredura passiva em andamento (~30s, ou instantâneo se em cache)…
          </div>
        )}
        {error && <div className="mt-4"><ErrorBox message={error} /></div>}
      </Card>

      {/* Etapa 2 — resultado inline */}
      {result && <ResultView result={result} onEmail={() => setModal(true)} />}

      {/* Etapa 3 — modal de envio */}
      {modal && result && (
        <EmailModal result={result} onClose={() => setModal(false)} />
      )}
    </div>
  )
}

function ResultView({ result, onEmail }) {
  const color = SEM_COLOR[result.semaphore] || '#8B949E'
  const results = result.checks || []
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  async function pdf(kind) {
    if (!result.scan_id) return
    setBusy(kind); setMsg('')
    try {
      await adminDownload(`/scans/${result.scan_id}/report/${kind}`, `klarim_${kind}.pdf`)
    } catch (e) { setMsg(e.message) } finally { setBusy('') }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex h-24 w-24 flex-col items-center justify-center rounded-full" style={{ border: `7px solid ${color}` }}>
          <span className="text-3xl font-extrabold" style={{ color }}>{result.score}</span>
          <span className="text-xs text-klarim-muted">/ 100</span>
        </div>
        <div className="space-y-1 text-sm">
          <div className="font-mono text-klarim-alert">{result.url}</div>
          <div className="text-klarim-muted">
            {result.pass_count} PASS · {result.fail_count} FAIL · {result.inconclusive} inconclusivo(s)
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <PlatformBadge platform={result.platform} />
            <Badge>{result.sector || 'outro'}</Badge>
            {result.contact_email
              ? <Badge color="#00D26A">✉ {result.contact_email}</Badge>
              : <Badge color="#8B949E">sem e-mail</Badge>}
            <Link to={`/painel/alvos/${result.target_id}`} className="text-xs text-klarim-alert hover:underline">
              ver alvo #{result.target_id}
            </Link>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy === 'executive'} onClick={() => pdf('executive')}>Baixar PDF Executivo</Button>
          <Button disabled={busy === 'technical'} onClick={() => pdf('technical')}>Baixar PDF Técnico</Button>
          <Button onClick={onEmail}>Enviar relatório por e-mail</Button>
        </div>
      </div>
      {msg && <div className="mt-3 text-sm text-klarim-muted">{msg}</div>}

      {(result.risk_messages || []).length > 0 && (
        <div className="mt-5">
          <h4 className="font-bold text-klarim-alert">⚠ O que pode acontecer com o site</h4>
          {result.risk_summary && <p className="mt-1 text-sm text-klarim-muted">{result.risk_summary}</p>}
          <div className="mt-2 space-y-2">
            {result.risk_messages.map((risk, i) => (
              <div key={i} className="rounded-lg border-l-4 border-klarim-alert bg-klarim-bg p-3">
                <div className="font-semibold">{risk.icon} {risk.headline}</div>
                <div className="mt-1 text-sm text-klarim-muted">{risk.risk}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-5 overflow-x-auto">
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
  )
}

function EmailModal({ result, onClose }) {
  const [email, setEmail] = useState(result.contact_email || '')
  const [type, setType] = useState('alert')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')
  const [error, setError] = useState('')

  async function send() {
    setBusy(true); setError(''); setDone('')
    try {
      const r = await admin.scanAndReport({
        url: result.url, send_email: true, email_to: email.trim(), email_type: type,
      })
      if (r.email_sent) setDone(`✅ Enviado para ${r.email_to || email}`)
      else setError(r.email_error || 'Falha no envio.')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-bold">Enviar relatório por e-mail</h3>
        <label className="mb-1 block text-xs uppercase text-klarim-muted">Destinatário</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="contato@exemplo.com.br"
          className="mb-4 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
        />
        <div className="mb-4 flex flex-col gap-2 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" checked={type === 'alert'} onChange={() => setType('alert')} />
            Enviar alerta (semáforo gratuito)
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={type === 'report'} onChange={() => setType('report')} />
            Enviar relatório completo (com PDFs)
          </label>
        </div>
        {done && <div className="mb-4 text-sm text-klarim-ok">{done}</div>}
        {error && <div className="mb-4"><ErrorBox message={error} /></div>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Fechar</Button>
          <Button variant="primary" disabled={busy || !email.trim()} onClick={send}>
            {busy ? 'Enviando…' : 'Enviar'}
          </Button>
        </div>
      </div>
    </div>
  )
}
