import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Button, Badge } from './ui'
import AdminShell from './AdminShell'

// Serviços (KL-44 Guardião Digital, P1): os 3 planos (Free/Pro/Agency) com todos os
// limites, editáveis via modal. Contadores de assinantes por plano (do subStats).

const PLAN_COLOR = { free: '#8B949E', pro: '#00D26A', agency: '#3B82F6' }

const VIGILIAS = [
  ['vigilia_ssl', 'SSL'], ['vigilia_domain', 'Domínio'], ['vigilia_score', 'Score'],
  ['vigilia_email', 'E-mail'], ['vigilia_reputation', 'Reputação'],
  ['vigilia_changes', 'Mudanças'], ['vigilia_phishing', 'Phishing'], ['vigilia_uptime', 'Uptime'],
]

// Campos editáveis no modal, agrupados. type: num | bool | text | select(options).
const FIELDS = [
  { key: 'name', label: 'Nome', type: 'text' },
  { key: 'price_monthly', label: 'Preço mensal (centavos)', type: 'num' },
  { key: 'price_yearly', label: 'Preço anual (centavos)', type: 'num' },
  { key: 'max_sites', label: 'Máx. sites', type: 'num' },
  { key: 'scan_frequency', label: 'Frequência de scan', type: 'select',
    options: ['daily', 'weekly', 'biweekly', 'monthly'] },
  ...VIGILIAS.map(([key, label]) => ({ key, label: `Vigília: ${label}`, type: 'bool' })),
  { key: 'uptime_interval_minutes', label: 'Intervalo de uptime (min)', type: 'num' },
  { key: 'bulletin_frequency', label: 'Boletim', type: 'select',
    options: ['none', 'monthly', 'weekly', 'daily'] },
  { key: 'action_plan_limit', label: 'Planos de ação (0=ilimitado)', type: 'num' },
  { key: 'history_months', label: 'Histórico (meses, 0=ilimitado)', type: 'num' },
  { key: 'competitor_slots', label: 'Slots de concorrentes', type: 'num' },
  { key: 'lgpd_full', label: 'LGPD completo', type: 'bool' },
  { key: 'widget_type', label: 'Widget', type: 'select',
    options: ['badge', 'interactive', 'whitelabel'] },
  { key: 'pdf_report_frequency', label: 'Relatório PDF', type: 'select',
    options: ['none', 'monthly', 'weekly'] },
  { key: 'export_enabled', label: 'Exportação', type: 'bool' },
  { key: 'api_enabled', label: 'API', type: 'bool' },
  { key: 'is_active', label: 'Plano ativo', type: 'bool' },
]

function brl(cents) {
  return cents ? `R$ ${(cents / 100).toFixed(0)}` : 'Grátis'
}

function Yes({ on }) {
  return <span style={{ color: on ? '#00D26A' : '#6E7681' }}>{on ? '✅' : '❌'}</span>
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between border-t border-klarim-border/60 py-1 text-xs">
      <span className="text-klarim-muted">{label}</span>
      <span className="font-medium text-klarim-text">{value}</span>
    </div>
  )
}

export default function ServicosPage() {
  const { data, loading, error, reload } = useAsync(
    () => Promise.all([admin.plans(), admin.subStats().catch(() => ({}))])
      .then(([plans, stats]) => ({ plans: plans.plans || [], stats })), [])
  const [editing, setEditing] = useState(null)   // plano em edição
  const [msg, setMsg] = useState('')

  let body
  if (loading) body = <Loading />
  else if (error) body = <ErrorBox message={error} />
  else {
    const byPlan = data.stats?.by_plan || {}
    body = (
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Serviços & Planos</h1>
          {msg && <span className="text-sm text-klarim-muted">{msg}</span>}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {data.plans.map((p) => {
            const color = PLAN_COLOR[p.id] || '#8B949E'
            return (
              <div key={p.id} className="rounded-xl border border-klarim-border bg-klarim-surface p-5">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-lg font-extrabold" style={{ color }}>{p.name}</div>
                    <div className="text-sm text-klarim-muted">
                      {brl(p.price_monthly)}{p.price_monthly ? '/mês' : ''}
                      {p.price_yearly ? ` · ${brl(p.price_yearly)}/ano` : ''}
                    </div>
                  </div>
                  <Badge color={color}>{byPlan[p.id] ?? 0} assinante(s)</Badge>
                </div>

                <div className="mt-3">
                  <Row label="Sites" value={p.max_sites} />
                  <Row label="Frequência de scan" value={p.scan_frequency} />
                  <Row label="Boletim" value={p.bulletin_frequency} />
                  <Row label="Planos de ação" value={p.action_plan_limit === 0 ? 'ilimitado' : p.action_plan_limit} />
                  <Row label="Histórico" value={p.history_months === 0 ? 'ilimitado' : `${p.history_months} meses`} />
                  <Row label="Concorrentes" value={p.competitor_slots} />
                  <Row label="Uptime" value={p.uptime_interval_minutes ? `${p.uptime_interval_minutes} min` : '—'} />
                  <Row label="Widget" value={p.widget_type} />
                  <Row label="Relatório PDF" value={p.pdf_report_frequency} />
                  <Row label="LGPD completo" value={<Yes on={p.lgpd_full} />} />
                  <Row label="Exportação" value={<Yes on={p.export_enabled} />} />
                  <Row label="API" value={<Yes on={p.api_enabled} />} />
                </div>

                <div className="mt-3 flex flex-wrap gap-2 border-t border-klarim-border/60 pt-3">
                  {VIGILIAS.map(([key, label]) => (
                    <span key={key} className="text-[11px] text-klarim-muted">
                      <Yes on={p[key]} /> {label}
                    </span>
                  ))}
                </div>

                <div className="mt-4">
                  <Button variant="primary" className="w-full" onClick={() => setEditing(p)}>Editar</Button>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <AdminShell active="servicos">
      {body}
      {editing && (
        <EditPlanModal
          plan={editing}
          onClose={() => setEditing(null)}
          onSaved={(note) => { setMsg(note); setEditing(null); reload() }}
        />
      )}
    </AdminShell>
  )
}

function EditPlanModal({ plan, onClose, onSaved }) {
  const [form, setForm] = useState(() => {
    const f = {}
    for (const { key } of FIELDS) f[key] = plan[key]
    return f
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  function set(key, val) { setForm((f) => ({ ...f, [key]: val })) }

  async function save() {
    setBusy(true); setErr('')
    try {
      // números vêm como string dos inputs → converte; null quando vazio.
      const payload = {}
      for (const { key, type } of FIELDS) {
        const v = form[key]
        payload[key] = type === 'num' ? (v === '' || v === null ? null : Number(v)) : v
      }
      await admin.updatePlan(plan.id, payload)
      onSaved(`Plano ${plan.name} atualizado ✓`)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inputCls = 'w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert'

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-bold">Editar plano — {plan.name} <span className="text-xs text-klarim-muted">({plan.id})</span></h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {FIELDS.map((fld) => (
            <div key={fld.key} className={fld.type === 'bool' ? 'flex items-center gap-2' : ''}>
              {fld.type === 'bool' ? (
                <>
                  <input type="checkbox" checked={!!form[fld.key]} onChange={(e) => set(fld.key, e.target.checked)} />
                  <label className="text-sm text-klarim-text">{fld.label}</label>
                </>
              ) : (
                <>
                  <label className="mb-1 block text-xs uppercase text-klarim-muted">{fld.label}</label>
                  {fld.type === 'select' ? (
                    <select className={inputCls} value={form[fld.key] ?? ''} onChange={(e) => set(fld.key, e.target.value)}>
                      {fld.options.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input type={fld.type === 'num' ? 'number' : 'text'} className={inputCls}
                      value={form[fld.key] ?? ''} onChange={(e) => set(fld.key, e.target.value)} />
                  )}
                </>
              )}
            </div>
          ))}
        </div>
        {err && <div className="mt-3"><ErrorBox message={err} /></div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" disabled={busy} onClick={save}>{busy ? 'Salvando…' : 'Salvar plano'}</Button>
        </div>
      </div>
    </div>
  )
}
