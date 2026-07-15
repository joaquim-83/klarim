import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, StatCard, Loading, ErrorBox, Button, Badge, Pagination, formatDate, relativeTime } from './ui'
import AdminShell from './AdminShell'

// Assinantes (KL-44 P1): contas + assinatura + status de trial, com mudança de plano,
// extensão de trial, ação em grupo e histórico. Todas as escritas via /admin/subscriptions/*.

const PLAN_COLOR = { free: '#8B949E', pro: '#00D26A', agency: '#3B82F6' }
const STATUS_COLOR = {
  trial: '#F0C000', active: '#00D26A', free: '#8B949E', expired: '#F85149', cancelled: '#6E7681',
}
const STATUS_LABEL = {
  trial: 'Trial', active: 'Ativo', free: 'Free', expired: 'Expirado', cancelled: 'Cancelado',
}
const PLANS = ['free', 'pro', 'agency']
const STATUSES = ['trial', 'active', 'free', 'expired', 'cancelled']
const PAGE_SIZE = 25

function trialLabel(r) {
  if (r.status === 'expired') return 'expirado'
  if (r.status === 'trial' && r.trial_days_left != null) return `${r.trial_days_left} dias`
  return '—'
}

export default function AssinantesPage() {
  const [plan, setPlan] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState(() => new Set())
  const [msg, setMsg] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [historyOf, setHistoryOf] = useState(null)

  const stats = useAsync(() => admin.subStats(), [])
  const { data, loading, error, reload } = useAsync(
    () => admin.subscribers({ plan_id: plan || undefined, status: status || undefined,
      search: query || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [plan, status, query, page])
  const rows = data?.subscribers || []
  const st = stats.data || {}

  function toggleSel(id) {
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }
  function submitSearch(e) { e.preventDefault(); setQuery(search.trim()); setPage(0) }

  async function act(id, fn, label) {
    setBusyId(id); setMsg('')
    try { await fn(); setMsg(`${label} ✓`); reload(); stats.reload?.() }
    catch (e) { setMsg(e.message) } finally { setBusyId(null) }
  }

  async function bulk(action, extra) {
    const ids = [...selected]
    if (!ids.length) return
    setMsg('')
    try {
      const r = await admin.subBulk({ account_ids: ids, action, ...extra })
      setMsg(`${r.applied} conta(s) atualizadas.`)
      setSelected(new Set()); reload(); stats.reload?.()
    } catch (e) { setMsg(e.message) }
  }

  const inputCls = 'rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert'

  return (
    <AdminShell active="assinantes">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Assinantes</h1>
          {msg && <span className="text-sm text-klarim-muted">{msg}</span>}
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="Contas" value={st.total_accounts ?? 0} />
          <StatCard label="Free" value={st.by_plan?.free ?? 0} />
          <StatCard label="Pro" value={st.by_plan?.pro ?? 0} accent="#00D26A" />
          <StatCard label="Agency" value={st.by_plan?.agency ?? 0} accent="#3B82F6" />
          <StatCard label="Trials ativos" value={st.trials_active ?? 0} accent="#F0C000" />
          <StatCard label="Expira em 7d" value={st.trials_expiring_7d ?? 0} accent="#FF6B35"
            sub={`conversão ${st.conversion_rate ?? 0}%`} />
        </div>

        {/* Filtros */}
        <div className="flex flex-wrap items-center gap-2">
          <select value={plan} onChange={(e) => { setPlan(e.target.value); setPage(0) }} className={inputCls}>
            <option value="">Todos os planos</option>
            {PLANS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0) }} className={inputCls}>
            <option value="">Todos os status</option>
            {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
          <form onSubmit={submitSearch} className="flex flex-1 gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar por e-mail…"
              className={`min-w-40 flex-1 ${inputCls}`} />
            <Button type="submit">Buscar</Button>
          </form>
        </div>

        {/* Ação em grupo */}
        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-klarim-alert/40 bg-klarim-alert/10 px-3 py-2 text-sm">
            <span className="font-semibold">{selected.size} selecionado(s)</span>
            <span className="text-klarim-muted">→</span>
            <select className={inputCls} defaultValue="" onChange={(e) => {
              const v = e.target.value; e.target.value = ''
              if (v.startsWith('plan:')) bulk('change_plan', { plan_id: v.slice(5) })
              else if (v === 'extend7') bulk('extend_trial', { days: 7 })
              else if (v === 'extend30') bulk('extend_trial', { days: 30 })
              else if (v.startsWith('status:')) bulk('change_status', { status: v.slice(7) })
            }}>
              <option value="">Escolher ação…</option>
              <option value="plan:free">Mudar para Free</option>
              <option value="plan:pro">Mudar para Pro</option>
              <option value="plan:agency">Mudar para Agency</option>
              <option value="extend7">Estender trial +7 dias</option>
              <option value="extend30">Estender trial +30 dias</option>
              <option value="status:active">Status → Ativo</option>
              <option value="status:cancelled">Status → Cancelado</option>
            </select>
            <button onClick={() => setSelected(new Set())} className="text-klarim-muted hover:text-klarim-text">Limpar</button>
          </div>
        )}

        <Card>
          {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-2">
                      <input type="checkbox" aria-label="Selecionar todos"
                        checked={rows.length > 0 && rows.every((r) => selected.has(r.account_id))}
                        onChange={(e) => setSelected((prev) => {
                          const n = new Set(prev)
                          rows.forEach((r) => e.target.checked ? n.add(r.account_id) : n.delete(r.account_id))
                          return n
                        })} />
                    </th>
                    <th className="py-2 pr-3">E-mail</th>
                    <th className="py-2 pr-3">Plano</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Trial</th>
                    <th className="py-2 pr-3">Sites</th>
                    <th className="py-2 pr-3">Última atividade</th>
                    <th className="py-2">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.account_id} className="border-t border-klarim-border align-middle">
                      <td className="py-2 pr-2">
                        <input type="checkbox" checked={selected.has(r.account_id)} onChange={() => toggleSel(r.account_id)} />
                      </td>
                      <td className="py-2 pr-3 text-xs">{r.email}</td>
                      <td className="py-2 pr-3">
                        <select
                          value={r.plan_id}
                          disabled={busyId === r.account_id}
                          onChange={(e) => act(r.account_id, () => admin.subChangePlan(r.account_id, e.target.value), 'Plano alterado')}
                          className="rounded border px-1.5 py-0.5 text-xs outline-none"
                          style={{ borderColor: `${PLAN_COLOR[r.plan_id] || '#8B949E'}88`, color: PLAN_COLOR[r.plan_id] || '#8B949E', background: 'transparent' }}
                        >
                          {PLANS.map((p) => <option key={p} value={p} style={{ color: '#000' }}>{p}</option>)}
                        </select>
                      </td>
                      <td className="py-2 pr-3">
                        <Badge color={STATUS_COLOR[r.status] || '#8B949E'}>{STATUS_LABEL[r.status] || r.status}</Badge>
                      </td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{trialLabel(r)}</td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted">{r.sites ?? 0}/{r.plan_max_sites ?? '—'}</td>
                      <td className="py-2 pr-3 text-xs text-klarim-muted" title={formatDate(r.last_login_at)}>
                        {r.last_login_at ? relativeTime(r.last_login_at) : '—'}
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap items-center gap-1">
                          <Button disabled={busyId === r.account_id}
                            onClick={() => act(r.account_id, () => admin.subExtendTrial(r.account_id, 30), 'Trial +30d')}>
                            +30d trial
                          </Button>
                          <Button onClick={() => setHistoryOf(r)}>Histórico</Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && <tr><td colSpan={8} className="py-8 text-center text-klarim-muted">Nenhum assinante.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
        </Card>
      </div>

      {historyOf && <HistoryModal subscriber={historyOf} onClose={() => setHistoryOf(null)} />}
    </AdminShell>
  )
}

function HistoryModal({ subscriber, onClose }) {
  const { data, loading, error } = useAsync(() => admin.subHistory(subscriber.account_id), [])
  const rows = data?.history || []
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-bold">Histórico — {subscriber.email}</h3>
          <button onClick={onClose} className="text-klarim-muted hover:text-klarim-text">✕</button>
        </div>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : rows.length === 0 ? (
          <p className="text-sm text-klarim-muted">Sem histórico.</p>
        ) : (
          <div className="space-y-2">
            {rows.map((h, i) => (
              <div key={i} className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm">
                <div className="flex items-center justify-between">
                  <span>
                    {(h.old_plan_id || '—')} → <strong>{h.new_plan_id}</strong>
                    <span className="mx-2 text-klarim-muted">·</span>
                    {(h.old_status || '—')} → <strong>{h.new_status}</strong>
                  </span>
                  <span className="text-xs text-klarim-muted">{formatDate(h.created_at)}</span>
                </div>
                <div className="text-xs text-klarim-muted">por {h.changed_by}{h.reason ? ` — ${h.reason}` : ''}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
