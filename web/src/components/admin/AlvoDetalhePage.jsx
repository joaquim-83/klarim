import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import {
  Card, Loading, ErrorBox, Button, Badge, PlatformBadge, SourceBadge,
  SemaphoreDot, EVOLUTION_META, formatDate, relativeTime,
} from './ui'
import { SectorEditor } from './SectorEditor'
import { StatusEditor, EmailEditor } from './TargetEditors'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/AlvoDetalhe.jsx (KL-51 fase 2). useParams → pathname;
// Link → <a href>; useNavigate removido (não era usado).
const PAY_COLOR = { PAID: '#00D26A', PENDING: '#F0C000', EXPIRED: '#8B949E', CANCELLED: '#F85149' }

function Field({ label, children }) {
  return (
    <div>
      <div className="text-xs uppercase text-klarim-muted">{label}</div>
      <div className="mt-0.5 text-sm">{children}</div>
    </div>
  )
}

export default function AlvoDetalhePage() {
  const id = window.location.pathname.split('/').filter(Boolean).pop()
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const { data, loading, error, reload } = useAsync(
    () => Promise.all([
      admin.target(id), admin.scans({ target_id: id, limit: 50 }),
      admin.alerts({ target_id: id }), admin.rescans({ target_id: id }),
      admin.targetPayments(id),
    ]).then(([target, scans, alerts, rescans, payments]) => ({
      target, scans: scans.scans, alerts: alerts.alerts, rescans: rescans.rescans,
      payments: payments.payments,
    })),
    [id],
  )

  async function act(fn, label, key, arg) {
    setBusy(key)
    setMsg('')
    try {
      await fn(arg !== undefined ? arg : id)
      setMsg(`${label} ✓`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusy('')
    }
  }

  let body
  if (loading) body = <Loading />
  else if (error) body = <ErrorBox message={error} />
  else {
    const t = data.target
    body = (
      <div className="space-y-5">
        <div className="flex items-center gap-3">
          <a href="/painel/alvos" className="text-sm text-klarim-muted hover:text-klarim-text">← Alvos</a>
          <h1 className="text-xl font-bold">{t.domain || t.url}</h1>
          <StatusEditor target={t} onSaved={(_u, note) => { setMsg(note); reload() }} onError={(m) => setMsg(m)} />
        </div>

        {/* Ações */}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy === 'scan'} onClick={() => act(admin.scanTarget, 'Scan enfileirado', 'scan')}>Escanear agora</Button>
          <Button disabled={busy === 'alert' || !t.contact_email} onClick={() => act(admin.resendAlert, 'Alerta reenviado', 'alert')}>Reenviar alerta</Button>
          <Button disabled={busy === 'report' || !t.contact_email} onClick={() => act(admin.sendReport, 'Relatório enviado', 'report')}>Enviar relatório completo</Button>
          <Button disabled={busy === 'rescan'} onClick={() => act(admin.rescanTarget, 'Re-scan feito', 'rescan')}>Forçar re-scan</Button>
          <Button variant="danger" disabled={busy === 'discard'} onClick={() => act(admin.discardTarget, 'Descartado', 'discard')}>Marcar como descartado</Button>
        </div>
        {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

        {/* Ficha */}
        <Card title="Dados do alvo">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="URL"><a href={t.url} target="_blank" rel="noreferrer" className="text-klarim-alert hover:underline">{t.url}</a></Field>
            <Field label="Plataforma"><PlatformBadge platform={t.platform} /></Field>
            <Field label="Setor">
              <SectorEditor
                target={t}
                onSaved={(_u, note) => { setMsg(note); reload() }}
                onError={(m) => setMsg(m)}
              />
            </Field>
            <Field label="Preço (tier)">{t.price_tier || 'standard'}</Field>
            <Field label="E-mail">
              <EmailEditor target={t} onSaved={(_u, note) => { setMsg(note); reload() }} onError={(m) => setMsg(m)} />
            </Field>
            <Field label="Origem"><SourceBadge source={t.source} /></Field>
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
              <td className="py-2 text-right"><a href={`/painel/scans/${s.id}`} className="text-klarim-alert hover:underline">ver</a></td>
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

        {/* Pagamentos vinculados */}
        {data.payments && data.payments.length > 0 && (
          <Card title={`Pagamentos (${data.payments.length})`}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-3">Cobrança</th>
                    <th className="py-2 pr-3">Valor</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Relatório?</th>
                    <th className="py-2 pr-3">Data</th>
                    <th className="py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {data.payments.map((p) => (
                    <tr key={p.charge_id} className="border-t border-klarim-border">
                      <td className="py-2 pr-3 font-mono text-[11px] text-klarim-muted">{p.charge_id}</td>
                      <td className="py-2 pr-3 font-semibold">{p.amount_display}</td>
                      <td className="py-2 pr-3"><Badge color={PAY_COLOR[p.status] || '#8B949E'}>{p.status}</Badge></td>
                      <td className="py-2 pr-3 text-xs">{p.report_email_sent ? '✅' : (p.email_status === 'failed' ? '❌ falhou' : '—')}</td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{formatDate(p.created_at)}</td>
                      <td className="py-2 text-right">
                        {p.status === 'PAID' && (!p.report_email_sent || p.email_status === 'failed') && (
                          <Button disabled={busy === `pay-${p.charge_id}`}
                            onClick={() => act((cid) => admin.resendPayment(cid), 'Reenvio agendado', `pay-${p.charge_id}`, p.charge_id)}>
                            Reenviar relatório
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

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

  return <AdminShell active="alvos">{body}</AdminShell>
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
