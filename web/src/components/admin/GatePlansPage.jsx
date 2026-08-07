import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Button, Badge } from './ui'
import AdminShell from './AdminShell'

// KL-151 P3 — admin de planos do Security Gate: lista os planos (editáveis sem deploy — refletem no
// próximo scan via plano efetivo) + contas dev com uso + atribuição manual de plano. Ilha client:only
// (CSP relaxada no /painel).

const brl = (c) => (c > 0 ? `R$${Math.round(c / 100)}` : 'R$0')
const inf = (n) => (n === -1 ? '∞' : String(n))

function PlanModal({ plan, allChecks, onClose, onSaved }) {
  const isNew = !plan
  const [form, setForm] = useState(() => ({
    name: plan?.name || '', slug: plan?.slug || '',
    price_brl: plan?.price_brl ?? 0, scans_per_day: plan?.scans_per_day ?? 5,
    max_domains: plan?.max_domains ?? 1, history_days: plan?.history_days ?? 7,
    trial_days: plan?.trial_days ?? 0, scan_third_party: !!plan?.scan_third_party,
    active: plan?.active !== false,
    checks: new Set(Array.isArray(plan?.checks_allowed) ? plan.checks_allowed : ['headers']),
  }))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const allSelected = form.checks.has('all')

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  const toggleCheck = (c) => setForm((f) => {
    const s = new Set(f.checks)
    s.has(c) ? s.delete(c) : s.add(c)
    return { ...f, checks: s }
  })

  async function save() {
    setBusy(true); setErr('')
    const body = {
      name: form.name, price_brl: Number(form.price_brl), scans_per_day: Number(form.scans_per_day),
      max_domains: Number(form.max_domains), history_days: Number(form.history_days),
      trial_days: Number(form.trial_days), scan_third_party: form.scan_third_party,
      active: form.active, checks_allowed: [...form.checks],
    }
    try {
      if (isNew) await admin.gateCreatePlan({ ...body, slug: form.slug })
      else await admin.gateUpdatePlan(plan.id, body)
      onSaved()
    } catch (e) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-slate-900 p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-white">{isNew ? 'Novo plano' : `Editar ${plan.name}`}</h3>
        {err && <ErrorBox message={err} />}
        <div className="mt-4 grid grid-cols-2 gap-3">
          <Field label="Nome"><input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="Slug">
            <input className={inputCls} value={form.slug} disabled={!isNew}
              onChange={(e) => set('slug', e.target.value)} placeholder="pro" />
          </Field>
          <Field label="Preço (centavos)"><input type="number" className={inputCls} value={form.price_brl} onChange={(e) => set('price_brl', e.target.value)} /></Field>
          <Field label="Scans/dia (-1 = ∞)"><input type="number" className={inputCls} value={form.scans_per_day} onChange={(e) => set('scans_per_day', e.target.value)} /></Field>
          <Field label="Máx domínios (-1 = ∞)"><input type="number" className={inputCls} value={form.max_domains} onChange={(e) => set('max_domains', e.target.value)} /></Field>
          <Field label="Histórico (dias)"><input type="number" className={inputCls} value={form.history_days} onChange={(e) => set('history_days', e.target.value)} /></Field>
          <Field label="Trial (dias)"><input type="number" className={inputCls} value={form.trial_days} onChange={(e) => set('trial_days', e.target.value)} /></Field>
          <div className="flex items-end gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={form.scan_third_party} onChange={(e) => set('scan_third_party', e.target.checked)} /> Terceiros</label>
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} /> Ativo</label>
          </div>
        </div>

        <div className="mt-4">
          <div className="mb-2 text-sm font-semibold text-slate-300">Checks permitidos {allSelected && <Badge color="#00D26A">todos</Badge>}</div>
          <label className="mb-2 flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={allSelected} onChange={() => toggleCheck('all')} /> <strong>all</strong> (todos os checks)
          </label>
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
            {allChecks.map((c) => (
              <label key={c} className="flex items-center gap-2 text-xs text-slate-400">
                <input type="checkbox" disabled={allSelected} checked={form.checks.has(c)} onChange={() => toggleCheck(c)} /> {c}
              </label>
            ))}
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" onClick={save} disabled={busy || !form.name || (isNew && !form.slug)}>
            {busy ? 'Salvando…' : 'Salvar'}
          </Button>
        </div>
      </div>
    </div>
  )
}

const inputCls = 'w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-white'
function Field({ label, children }) {
  return <label className="block text-xs text-slate-400">{label}{children}</label>
}

function AccountsTable() {
  const { data, loading, error } = useAsync(() => admin.gateAccounts(), [])
  const { data: plansData } = useAsync(() => admin.gatePlans(), [])
  const [msg, setMsg] = useState('')
  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} />
  const accounts = data?.accounts || []
  const plans = plansData?.plans || []

  async function assign(accountId, planId) {
    if (!planId) return
    setMsg('')
    try { await admin.gateAssignPlan(accountId, Number(planId)); setMsg(`Plano atribuído à conta ${accountId}.`) }
    catch (e) { setMsg(e.message) }
  }

  return (
    <Card title={`Contas dev (${accounts.length})`}>
      {msg && <p className="mb-2 text-sm text-brand-400">{msg}</p>}
      {accounts.length === 0 ? <p className="text-sm text-slate-400">Nenhuma conta dev ainda.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-400">
              <th className="py-1">Email</th><th>Plano</th><th>Scans hoje</th><th>Projetos</th><th>Key</th><th>Atribuir</th>
            </tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id} className="border-t border-slate-800 text-slate-200">
                  <td className="py-1.5">{a.email}</td>
                  <td><Badge>{a.plan_slug || '—'}</Badge></td>
                  <td>{a.scans_today}</td>
                  <td>{a.project_count}</td>
                  <td className="font-mono text-xs text-slate-500">{a.key_prefix || '—'}</td>
                  <td>
                    <select className={inputCls} defaultValue="" onChange={(e) => assign(a.id, e.target.value)}>
                      <option value="">—</option>
                      {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function GatePlansPage() {
  const { data, loading, error, reload } = useAsync(() => admin.gatePlans(), [])
  const [modal, setModal] = useState(null) // { plan } | { plan: null } (novo)

  return (
    <AdminShell active="gate-plans">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Security Gate — Planos</h1>
        <Button variant="primary" onClick={() => setModal({ plan: null })}>+ Novo plano</Button>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-400">
                <th className="py-1">Plano</th><th>Preço</th><th>Scans</th><th>Domínios</th><th>Checks</th><th>Histórico</th><th>Status</th><th></th>
              </tr></thead>
              <tbody>
                {data.plans.map((p) => {
                  const nChecks = Array.isArray(p.checks_allowed) && p.checks_allowed.includes('all')
                    ? data.all_checks.length : (p.checks_allowed || []).length
                  return (
                    <tr key={p.id} className="border-t border-slate-800 text-slate-200">
                      <td className="py-1.5 font-semibold">{p.name} <span className="text-xs text-slate-500">{p.slug}</span></td>
                      <td>{brl(p.price_brl)}</td>
                      <td>{inf(p.scans_per_day)}/dia</td>
                      <td>{inf(p.max_domains)}</td>
                      <td>{nChecks}</td>
                      <td>{p.history_days === -1 ? '∞' : `${p.history_days}d`}</td>
                      <td>{p.active ? <Badge color="#00D26A">Ativo</Badge> : <Badge color="#F85149">Inativo</Badge>}</td>
                      <td><Button onClick={() => setModal({ plan: p })}>Editar</Button></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="mt-6"><AccountsTable /></div>

      {modal && (
        <PlanModal plan={modal.plan} allChecks={data?.all_checks || []}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); reload() }} />
      )}
    </AdminShell>
  )
}
