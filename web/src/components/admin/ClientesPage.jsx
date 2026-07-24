import { useEffect, useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { Card, StatCard, Loading, ErrorBox, Badge, SemaphoreDot, relativeTime, formatDate, DomainLink } from './ui'
import AdminShell from './AdminShell'

// Gestão de Clientes (KL-51 f3 fix): contas de usuário (tabela `users`) + os sites que
// cada uma monitora (tabela `user_sites`). Portado de frontend/src/pages/admin/Clientes.jsx.
const PLAN_LABEL = { free: 'Gratuito', basic: 'Básico', enterprise: 'Enterprise' }

export default function ClientesPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [cleanMsg, setCleanMsg] = useState('')
  const [cleaning, setCleaning] = useState(false)

  useEffect(() => {
    admin.clients().then(setData).catch((e) => setError(e.message || 'Erro ao carregar.'))
  }, [])

  // KL-68 — limpeza retroativa de sites de domínio público/institucional (gmail.com…).
  async function cleanBlocked() {
    setCleaning(true); setCleanMsg('')
    try {
      const dry = await admin.cleanBlockedSites(true)
      if (!dry.found) { setCleanMsg('Nenhum site bloqueado encontrado.'); return }
      if (!window.confirm(`Remover ${dry.found} vínculo(s) de domínios bloqueados?\n${(dry.domains || []).join(', ')}`)) return
      const res = await admin.cleanBlockedSites(false)
      setCleanMsg(`${res.removed} vínculo(s) removido(s).`)
    } catch (e) {
      setCleanMsg(e.message || 'Falha na limpeza.')
    } finally {
      setCleaning(false)
    }
  }

  let body
  if (error) body = <ErrorBox message={error} />
  else if (data === null) body = <Loading />
  else {
    const clients = data.clients || []
    body = (
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Gestão de Clientes</h1>
          <div className="flex items-center gap-3">
            {cleanMsg && <span className="text-sm text-klarim-muted">{cleanMsg}</span>}
            <button onClick={cleanBlocked} disabled={cleaning}
              className="rounded-lg border border-klarim-border px-3 py-1.5 text-sm text-klarim-muted hover:text-klarim-text disabled:opacity-50">
              {cleaning ? 'Verificando…' : 'Remover sites bloqueados'}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard label="Contas" value={data.total ?? 0} />
          <StatCard label="Ativas" value={data.active ?? 0} accent="#00D26A" />
          <StatCard label="Sites monitorados" value={data.total_sites ?? 0} />
        </div>

        <Card title={`Contas (${clients.length})`}>
          {clients.length === 0 ? (
            <p className="text-sm text-klarim-muted">Nenhuma conta de usuário ainda.</p>
          ) : (
            <div className="space-y-3">
              {clients.map((c) => <ClientRow key={c.id} c={c} />)}
            </div>
          )}
        </Card>
      </div>
    )
  }

  return <AdminShell active="clientes">{body}</AdminShell>
}

function ClientRow({ c }) {
  const sites = c.sites || []
  return (
    <div className="rounded-lg border border-klarim-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-klarim-text">{c.email}</p>
          <p className="text-xs text-klarim-muted">
            {c.name ? `${c.name} · ` : ''}Criada {formatDate(c.created_at)} · Último login {relativeTime(c.last_login_at) || 'nunca'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge color="#8B949E">{PLAN_LABEL[c.plan] || c.plan}</Badge>
          <Badge color={c.is_active ? '#00D26A' : '#F85149'}>{c.is_active ? 'Ativo' : 'Inativo'}</Badge>
          <span className="text-xs text-klarim-muted">{sites.length}/{c.max_sites}</span>
        </div>
      </div>

      {sites.length > 0 ? (
        <div className="mt-3 space-y-1.5">
          {sites.map((s) => (
            <div key={s.target_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <DomainLink domain={s.domain || s.url} targetId={s.target_id} />
              <span className="flex items-center gap-3 text-klarim-muted">
                <SemaphoreDot semaphore={s.last_semaphore} score={s.last_scan_score} />
                <span className="text-xs">{s.last_scan_at ? formatDate(s.last_scan_at) : 'sem scan'}</span>
                {s.is_owner && <span className="rounded bg-klarim-alert/15 px-1.5 py-0.5 text-[10px] text-klarim-alert">dono</span>}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-klarim-muted">Nenhum site monitorado (limite {c.max_sites}).</p>
      )}
    </div>
  )
}
