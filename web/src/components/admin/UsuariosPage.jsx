import { useEffect, useMemo, useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { Card, StatCard, Loading, ErrorBox, Badge, SemaphoreDot, relativeTime, formatDate } from './ui'
import AdminShell from './AdminShell'

// KL-69 — página unificada "Usuários" (funde Gestão de Clientes + Assinantes) com ações
// reais: remover site de um usuário, desativar/reativar conta, limpeza de domínios
// bloqueados. Todas as escritas via /admin/users/* e /admin/clean-blocked-sites.
const PLAN_LABEL = { free: 'Free', pro: 'Pro', agency: 'Agency', basic: 'Básico', enterprise: 'Enterprise' }
const STATUS_COLOR = {
  trial: '#F0C000', active: '#00D26A', free: '#8B949E', expired: '#F85149', cancelled: '#6E7681',
}
const STATUS_LABEL = {
  trial: 'Trial', active: 'Ativo', free: 'Free', expired: 'Expirado', cancelled: 'Cancelado',
}

const inputCls = 'rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text'

function statusOf(u) { return u.sub_status || 'free' }
function planOf(u) { return u.sub_plan || u.plan || 'free' }

// KL-71 Bug 9 — distinção de papel no painel admin.
const ROLE_META = {
  owner: { label: '👤 Dono', color: '#8B949E' },
  technician: { label: '🔧 Técnico', color: '#FF6B35' },
  both: { label: '👤🔧 Ambos', color: '#F0C000' },
}
function RoleBadge({ role }) {
  const m = ROLE_META[role] || ROLE_META.owner
  return <span className="rounded px-1.5 py-0.5 text-xs font-semibold" style={{ background: m.color + '22', color: m.color }}>{m.label}</span>
}

export default function UsuariosPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState('')
  const [status, setStatus] = useState('')
  const [active, setActive] = useState('')
  const [query, setQuery] = useState('')
  const [toast, setToast] = useState('')

  async function load() {
    try { setData(await admin.users()) } catch (e) { setError(e.message || 'Erro ao carregar.') }
  }
  useEffect(() => { load() }, [])

  const clients = data?.clients || []
  const filtered = useMemo(() => clients.filter((u) => {
    if (plan && planOf(u) !== plan) return false
    if (status && statusOf(u) !== status) return false
    if (active === 'active' && !u.is_active) return false
    if (active === 'inactive' && u.is_active) return false
    if (query && !(u.email || '').toLowerCase().includes(query.toLowerCase())) return false
    return true
  }), [clients, plan, status, active, query])

  function notify(msg) { setToast(msg); setTimeout(() => setToast(''), 6000) }

  let body
  if (error) body = <ErrorBox message={error} />
  else if (data === null) body = <Loading />
  else body = (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Usuários</h1>
        <CleanBlockedButton onDone={(m) => { notify(m); load() }} />
      </div>

      {toast && (
        <div className="rounded-lg border border-green-500/40 bg-green-500/10 px-4 py-2.5 text-sm text-green-300">{toast}</div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Contas" value={data.total ?? 0} />
        <StatCard label="Ativas" value={data.active ?? 0} accent="#00D26A" />
        <StatCard label="Sites monitorados" value={data.total_sites ?? 0} />
        <StatCard label="Com dono" value={clients.filter((u) => (u.sites || []).some((s) => s.is_owner)).length} accent="#FF6B35" />
      </div>

      <div className="flex flex-wrap gap-2">
        <select value={plan} onChange={(e) => setPlan(e.target.value)} className={inputCls}>
          <option value="">Todos os planos</option>
          {['free', 'pro', 'agency'].map((p) => <option key={p} value={p}>{PLAN_LABEL[p]}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className={inputCls}>
          <option value="">Todos os status</option>
          {Object.keys(STATUS_LABEL).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        <select value={active} onChange={(e) => setActive(e.target.value)} className={inputCls}>
          <option value="">Ativos e inativos</option>
          <option value="active">Só ativos</option>
          <option value="inactive">Só inativos</option>
        </select>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar por e-mail…"
          className={`${inputCls} min-w-[180px] flex-1`} />
      </div>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-klarim-muted">
                <th className="py-2 pr-3">E-mail</th>
                <th className="py-2 pr-3">Nome</th>
                <th className="py-2 pr-3">Plano</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Sites</th>
                <th className="py-2 pr-3">Dono</th>
                <th className="py-2 pr-3">Perfil</th>
                <th className="py-2 pr-3">Criação</th>
                <th className="py-2 pr-3">Último login</th>
                <th className="py-2">Ativo</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => <UserRow key={u.id} u={u} onChanged={(m) => { if (m) notify(m); load() }} />)}
              {filtered.length === 0 && <tr><td colSpan={10} className="py-8 text-center text-klarim-muted">Nenhum usuário.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )

  return <AdminShell active="usuarios">{body}</AdminShell>
}

function UserRow({ u, onChanged }) {
  const [open, setOpen] = useState(false)
  const sites = u.sites || []
  const isOwner = sites.some((s) => s.is_owner)
  const st = statusOf(u)
  return (
    <>
      <tr className="cursor-pointer border-t border-klarim-border hover:bg-klarim-surface/50" onClick={() => setOpen(!open)}>
        <td className="py-2 pr-3 text-xs">{open ? '▾ ' : '▸ '}{u.email}</td>
        <td className="py-2 pr-3 text-klarim-muted">{u.name || '—'}</td>
        <td className="py-2 pr-3"><Badge>{PLAN_LABEL[planOf(u)] || planOf(u)}</Badge></td>
        <td className="py-2 pr-3"><span className="rounded px-1.5 py-0.5 text-xs font-semibold" style={{ background: (STATUS_COLOR[st] || '#8B949E') + '22', color: STATUS_COLOR[st] || '#8B949E' }}>{STATUS_LABEL[st] || st}</span></td>
        <td className="py-2 pr-3">{sites.length}</td>
        <td className="py-2 pr-3">{isOwner ? <span className="text-green-400">✓</span> : <span className="text-klarim-muted">—</span>}</td>
        <td className="py-2 pr-3"><RoleBadge role={u.role} /></td>
        <td className="py-2 pr-3 text-xs text-klarim-muted">{relativeTime(u.created_at)}</td>
        <td className="py-2 pr-3 text-xs text-klarim-muted">{u.last_login_at ? relativeTime(u.last_login_at) : '—'}</td>
        <td className="py-2">{u.is_active ? <span className="text-green-400">●</span> : <span className="text-red-400">●</span>}</td>
      </tr>
      {open && (
        <tr className="border-t border-klarim-border bg-klarim-surface/30">
          <td colSpan={10} className="px-3 py-4"><UserDetail u={u} onChanged={onChanged} /></td>
        </tr>
      )}
    </>
  )
}

function UserDetail({ u, onChanged }) {
  const sites = u.sites || []
  const isTech = u.role === 'technician' || u.role === 'both'
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-xs font-semibold uppercase text-klarim-muted">Perfil</span>
        <RoleBadge role={u.role} />
        {isTech && <span className="text-xs text-klarim-muted">— recebe laudos técnicos dos clientes vinculados</span>}
      </div>
      <div>
        <p className="mb-2 text-xs font-semibold uppercase text-klarim-muted">Sites monitorados ({sites.length})</p>
        {sites.length === 0 ? <p className="text-sm text-klarim-muted">Nenhum site.</p> : (
          <div className="space-y-2">
            {sites.map((s) => <SiteRow key={s.target_id} u={u} s={s} onChanged={onChanged} />)}
          </div>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <SubscriptionEditor u={u} onChanged={onChanged} />
        <div>
          <p className="mb-1 text-xs font-semibold uppercase text-klarim-muted">Ações</p>
          <AccountToggle u={u} onChanged={onChanged} />
        </div>
      </div>
    </div>
  )
}

// FIX gestão de planos (KL-69 P6 antecipado) — alterar plano / estender trial / resetar
// para free direto no detalhe do usuário. Reusa /admin/subscriptions/* (account_id ==
// users.id); change_plan já ajusta as vigílias e o status. Necessário p/ testar o boletim.
function SubscriptionEditor({ u, onChanged }) {
  const [plan, setPlanState] = useState(planOf(u))
  const [busy, setBusy] = useState('')
  const status = statusOf(u)

  async function changePlan(newPlan) {
    if (newPlan === plan) return
    setBusy('plan')
    try {
      await admin.changeUserPlan(u.id, newPlan)
      setPlanState(newPlan)
      onChanged(`Plano de ${u.email} alterado para ${PLAN_LABEL[newPlan] || newPlan}`)
    } catch (e) { onChanged(e.message || 'Falha ao alterar plano.') } finally { setBusy('') }
  }
  async function extendTrial() {
    setBusy('trial')
    try {
      await admin.extendUserTrial(u.id, 30)
      onChanged(`Trial de ${u.email} estendido por 30 dias`)
    } catch (e) { onChanged(e.message || 'Falha ao estender trial.') } finally { setBusy('') }
  }
  async function resetFree() {
    setBusy('reset')
    try {
      await admin.resetUserFree(u.id)
      setPlanState('free')
      onChanged(`Plano de ${u.email} resetado para Free`)
    } catch (e) { onChanged(e.message || 'Falha ao resetar.') } finally { setBusy('') }
  }

  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase text-klarim-muted">Assinatura</p>
      <p className="text-sm text-klarim-text">Status: {STATUS_LABEL[status] || status}</p>
      {u.trial_ends_at && <p className="text-xs text-klarim-muted">Trial até {formatDate(u.trial_ends_at)}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="text-xs text-klarim-muted">Plano</label>
        <select value={plan} disabled={busy === 'plan'} onChange={(e) => changePlan(e.target.value)} className={inputCls}>
          {['free', 'pro', 'agency'].map((p) => <option key={p} value={p}>{PLAN_LABEL[p]}</option>)}
        </select>
        {status === 'trial' && (
          <button onClick={extendTrial} disabled={busy === 'trial'}
            className="rounded-lg border border-klarim-border px-2.5 py-1 text-xs text-klarim-muted hover:text-klarim-text disabled:opacity-50">
            {busy === 'trial' ? '…' : 'Estender trial 30d'}
          </button>
        )}
        {plan !== 'free' && (
          <button onClick={resetFree} disabled={busy === 'reset'}
            className="rounded-lg border border-red-500/40 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-50">
            {busy === 'reset' ? '…' : 'Resetar para Free'}
          </button>
        )}
      </div>
    </div>
  )
}

function SiteRow({ u, s, onChanged }) {
  const [confirm, setConfirm] = useState(false)
  const [notifyUser, setNotifyUser] = useState(true)
  const [busy, setBusy] = useState(false)
  async function remove() {
    setBusy(true)
    try {
      const r = await admin.removeUserSite(u.id, s.target_id, notifyUser)
      onChanged(`${r.domain || s.domain} removido de ${u.email}${r.notified ? ' · usuário notificado' : ''}`)
    } catch (e) { onChanged(e.message || 'Falha ao remover.') } finally { setBusy(false) }
  }
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-2 font-mono text-xs text-klarim-text">
          {s.domain || s.url}
          <SemaphoreDot semaphore={s.last_semaphore} score={s.last_scan_score} />
          {s.is_owner && <span className="rounded bg-brand-500/15 px-1.5 py-0.5 text-[11px] text-brand-300">✓ Dono{s.verified_at ? ` · ${formatDate(s.verified_at)}` : ''}</span>}
        </span>
        {!confirm && <button onClick={() => setConfirm(true)} className="rounded border border-red-500/40 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/10">Remover</button>}
      </div>
      {confirm && (
        <div className="mt-2 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm">
          <p className="text-klarim-text">Remover <b>{s.domain || s.url}</b> do monitoramento de <b>{u.email}</b>?</p>
          <label className="mt-2 flex items-center gap-2 text-xs text-klarim-muted">
            <input type="checkbox" checked={notifyUser} onChange={(e) => setNotifyUser(e.target.checked)} />
            Notificar o usuário por e-mail
          </label>
          <div className="mt-2 flex gap-2">
            <button disabled={busy} onClick={remove} className="rounded bg-red-500/80 px-3 py-1 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50">{busy ? 'Removendo…' : 'Remover'}</button>
            <button onClick={() => setConfirm(false)} className="rounded border border-klarim-border px-3 py-1 text-xs text-klarim-muted">Cancelar</button>
          </div>
        </div>
      )}
    </div>
  )
}

function AccountToggle({ u, onChanged }) {
  const [confirm, setConfirm] = useState(false)
  const [notifyUser, setNotifyUser] = useState(true)
  const [busy, setBusy] = useState(false)
  const deactivate = u.is_active
  async function apply() {
    setBusy(true)
    try {
      const r = deactivate ? await admin.deactivateUser(u.id, notifyUser) : await admin.reactivateUser(u.id, notifyUser)
      onChanged(`${u.email} ${deactivate ? 'desativada' : 'reativada'}${r.notified ? ' · notificado' : ''}`)
    } catch (e) { onChanged(e.message || 'Falha.') } finally { setBusy(false) }
  }
  if (!confirm) {
    return (
      <button onClick={() => setConfirm(true)}
        className={`rounded-lg border px-3 py-1.5 text-sm ${deactivate ? 'border-red-500/40 text-red-300 hover:bg-red-500/10' : 'border-green-500/40 text-green-300 hover:bg-green-500/10'}`}>
        {deactivate ? 'Desativar conta' : 'Reativar conta'}
      </button>
    )
  }
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-bg p-3 text-sm">
      <p className="text-klarim-text">{deactivate ? 'Desativar' : 'Reativar'} a conta de <b>{u.email}</b>?</p>
      <label className="mt-2 flex items-center gap-2 text-xs text-klarim-muted">
        <input type="checkbox" checked={notifyUser} onChange={(e) => setNotifyUser(e.target.checked)} />
        Notificar o usuário por e-mail
      </label>
      <div className="mt-2 flex gap-2">
        <button disabled={busy} onClick={apply} className="rounded bg-brand-500 px-3 py-1 text-xs font-semibold text-slate-950 hover:bg-brand-400 disabled:opacity-50">{busy ? '…' : 'Confirmar'}</button>
        <button onClick={() => setConfirm(false)} className="rounded border border-klarim-border px-3 py-1 text-xs text-klarim-muted">Cancelar</button>
      </div>
    </div>
  )
}

function CleanBlockedButton({ onDone }) {
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  async function scan() {
    setBusy(true)
    try {
      const dry = await admin.cleanBlockedSites(true)
      if (!dry.found) { onDone('Nenhum site bloqueado encontrado.'); return }
      setPreview(dry)
    } catch (e) { onDone(e.message || 'Falha.') } finally { setBusy(false) }
  }
  async function confirm() {
    setBusy(true)
    try {
      const res = await admin.cleanBlockedSites(false)
      setPreview(null)
      onDone(`${res.removed} site(s) removido(s), ${res.notified} usuário(s) notificado(s).`)
    } catch (e) { onDone(e.message || 'Falha.') } finally { setBusy(false) }
  }
  if (preview) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm">
        <p className="text-klarim-text">{preview.found} site(s) serão removidos e os usuários notificados:</p>
        <ul className="mt-1 text-xs text-klarim-muted">
          {(preview.items || []).map((it, i) => <li key={i}>{it.domain} ({it.email || '—'})</li>)}
        </ul>
        <div className="mt-2 flex gap-2">
          <button disabled={busy} onClick={confirm} className="rounded bg-red-500/80 px-3 py-1 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50">{busy ? 'Removendo…' : 'Confirmar remoção'}</button>
          <button onClick={() => setPreview(null)} className="rounded border border-klarim-border px-3 py-1 text-xs text-klarim-muted">Cancelar</button>
        </div>
      </div>
    )
  }
  return (
    <button onClick={scan} disabled={busy}
      className="rounded-lg border border-klarim-border px-3 py-1.5 text-sm text-klarim-muted hover:text-klarim-text disabled:opacity-50">
      {busy ? 'Verificando…' : 'Remover sites bloqueados'}
    </button>
  )
}
