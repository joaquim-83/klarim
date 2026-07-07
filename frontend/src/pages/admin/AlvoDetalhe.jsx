import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import {
  Card, Loading, ErrorBox, Button, PlatformBadge, StatusBadge, SemaphoreDot,
  EVOLUTION_META, formatDate, relativeTime,
} from '../../components/admin/ui'

function Field({ label, children }) {
  return (
    <div>
      <div className="text-xs uppercase text-klarim-muted">{label}</div>
      <div className="mt-0.5 text-sm">{children}</div>
    </div>
  )
}

export default function AlvoDetalhe() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const { data, loading, error, reload } = useAsync(
    () => Promise.all([
      admin.target(id), admin.scans({ target_id: id, limit: 50 }),
      admin.alerts({ target_id: id }), admin.rescans({ target_id: id }),
    ]).then(([target, scans, alerts, rescans]) => ({
      target, scans: scans.scans, alerts: alerts.alerts, rescans: rescans.rescans,
    })),
    [id],
  )

  async function act(fn, label, key) {
    setBusy(key)
    setMsg('')
    try {
      await fn(id)
      setMsg(`${label} ✓`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusy('')
    }
  }

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />

  const t = data.target

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Link to="/painel/alvos" className="text-sm text-klarim-muted hover:text-klarim-text">← Alvos</Link>
        <h1 className="text-xl font-bold">{t.domain || t.url}</h1>
        <StatusBadge status={t.status} />
      </div>

      {/* Ações */}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy === 'scan'} onClick={() => act(admin.scanTarget, 'Scan enfileirado', 'scan')}>Escanear agora</Button>
        <Button disabled={busy === 'alert' || !t.contact_email} onClick={() => act(admin.alertTarget, 'Alerta enviado', 'alert')}>Enviar alerta</Button>
        <Button disabled={busy === 'rescan'} onClick={() => act(admin.rescanTarget, 'Re-scan feito', 'rescan')}>Forçar re-scan</Button>
        <Button variant="danger" disabled={busy === 'discard'} onClick={() => act(admin.discardTarget, 'Descartado', 'discard')}>Marcar como descartado</Button>
      </div>
      {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

      {/* Ficha */}
      <Card title="Dados do alvo">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field label="URL"><a href={t.url} target="_blank" rel="noreferrer" className="text-klarim-alert hover:underline">{t.url}</a></Field>
          <Field label="Plataforma"><PlatformBadge platform={t.platform} /></Field>
          <Field label="Setor">{t.sector || 'outro'}</Field>
          <Field label="Preço (tier)">{t.price_tier || 'standard'}</Field>
          <Field label="E-mail">{t.contact_email || '—'}</Field>
          <Field label="Fonte">{t.source || '—'}</Field>
          <Field label="Score atual">{t.last_scan_score ?? '—'}</Field>
          <Field label="Último scan">{t.last_scan_at ? relativeTime(t.last_scan_at) : '—'}</Field>
          <Field label="Alertas enviados">{t.alert_count ?? 0}</Field>
        </div>
      </Card>

      {/* Histórico de scans */}
      <Card title={`Scans (${data.scans.length})`}>
        <HistTable rows={data.scans} cols={['Score', 'PASS/FAIL', 'Data', '']} render={(s) => (
          <tr key={s.id} className="border-t border-klarim-border">
            <td className="py-2 pr-3"><SemaphoreDot semaphore={s.semaphore} score={s.score} /></td>
            <td className="py-2 pr-3 text-xs text-klarim-muted">{s.pass_count}✓ / {s.fail_count}✗</td>
            <td className="py-2 pr-3 text-xs text-klarim-muted">{formatDate(s.scanned_at)}</td>
            <td className="py-2 text-right"><Link to={`/painel/scans/${s.id}`} className="text-klarim-alert hover:underline">ver</Link></td>
          </tr>
        )} empty="Nenhum scan." />
      </Card>

      {/* Histórico de alertas */}
      <Card title={`Alertas (${data.alerts.length})`}>
        <HistTable rows={data.alerts} cols={['Score', 'email_id', 'Status', 'Data']} render={(a) => (
          <tr key={a.id} className="border-t border-klarim-border">
            <td className="py-2 pr-3"><SemaphoreDot semaphore={a.semaphore} score={a.score} /></td>
            <td className="py-2 pr-3 font-mono text-xs text-klarim-muted">{a.email_id || '—'}</td>
            <td className="py-2 pr-3 text-xs">{a.status}</td>
            <td className="py-2 pr-3 text-xs text-klarim-muted">{formatDate(a.sent_at)}</td>
          </tr>
        )} empty="Nenhum alerta." />
      </Card>

      {/* Histórico de re-scans */}
      <Card title={`Re-scans (${data.rescans.length})`}>
        <HistTable rows={data.rescans} cols={['Evolução', 'Semáforo', 'email?', 'Data']} render={(r) => {
          const ev = EVOLUTION_META[r.evolution] || {}
          return (
            <tr key={r.id} className="border-t border-klarim-border">
              <td className="py-2 pr-3" style={{ color: ev.color }}>{ev.icon} {r.old_score} → {r.new_score}</td>
              <td className="py-2 pr-3 text-xs text-klarim-muted">{r.old_semaphore} → {r.new_semaphore}</td>
              <td className="py-2 pr-3 text-xs">{r.email_id ? 'sim' : 'não'}</td>
              <td className="py-2 pr-3 text-xs text-klarim-muted">{formatDate(r.rescanned_at)}</td>
            </tr>
          )
        }} empty="Nenhum re-scan." />
      </Card>
    </div>
  )
}

function HistTable({ rows, cols, render, empty }) {
  if (!rows || rows.length === 0) return <p className="text-sm text-klarim-muted">{empty}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-klarim-muted">
            {cols.map((c, i) => <th key={i} className="py-2 pr-3">{c}</th>)}
          </tr>
        </thead>
        <tbody>{rows.map(render)}</tbody>
      </table>
    </div>
  )
}
