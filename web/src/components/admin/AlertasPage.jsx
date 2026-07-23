import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, StatCard, Loading, ErrorBox, SemaphoreDot, Pagination, formatDate } from './ui'
import AdminShell from './AdminShell'

// Portado de frontend/src/pages/admin/Alertas.jsx (KL-51 fase 2). Link → <a href>.
const PAGE_SIZE = 25

export default function AlertasPage() {
  // Abas: alertas do worker (KL-12/23) e consultas de perfil público (profile_view, KL-51 f4).
  const [tab, setTab] = useState('alertas')
  return (
    <AdminShell active="alertas">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold">Alertas</h1>
          <div className="flex gap-2">
            <TabBtn active={tab === 'alertas'} onClick={() => setTab('alertas')}>Alertas enviados</TabBtn>
            <TabBtn active={tab === 'perfil'} onClick={() => setTab('perfil')}>Consultas de perfil</TabBtn>
          </div>
        </div>
        {tab === 'alertas' ? <AlertsTab /> : <ProfileViewsTab />}
      </div>
    </AdminShell>
  )
}

function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg border px-3 py-1.5 text-sm ${
        active
          ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-text'
          : 'border-klarim-border bg-klarim-surface text-klarim-muted hover:text-klarim-text'
      }`}
    >
      {children}
    </button>
  )
}

// KL-96 — remetente do alerta (email_log.from_domain): os subdomínios cold do KL-91
// (alertas./aviso.klarim.net) em destaque; o antigo klarim.net/klarimscan.com em cinza.
function SenderBadge({ domain }) {
  if (!domain) return <span className="text-xs text-klarim-muted">—</span>
  const isCold = domain === 'alertas.klarim.net' || domain === 'aviso.klarim.net'
  return (
    <span
      className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
        isCold ? 'bg-emerald-500/15 text-emerald-400' : 'bg-klarim-border/40 text-klarim-muted'
      }`}
      title={isCold ? 'Subdomínio cold (KL-91)' : 'Remetente antigo/transacional'}
    >
      {domain}
    </span>
  )
}

function AlertsTab() {
  const [page, setPage] = useState(0)
  const stats = useAsync(() => admin.alertsStats(), [])
  const { data, loading, error } = useAsync(
    () => admin.alerts({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [page],
  )
  const rows = data?.alerts || []

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Hoje" value={stats.data?.today ?? 0} accent="#FF6B35" />
        <StatCard label="Semana" value={stats.data?.week ?? 0} />
        <StatCard label="Mês" value={stats.data?.month ?? 0} />
        <StatCard label="Total" value={stats.data?.total ?? 0} />
      </div>

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">E-mail</th>
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Remetente</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">email_id</th>
                  <th className="py-2">Enviado</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="border-t border-klarim-border">
                    <td className="py-2 pr-3 text-xs">{a.contact_email}</td>
                    <td className="py-2 pr-3 font-mono text-xs text-klarim-muted">{a.url || `alvo #${a.target_id}`}</td>
                    <td className="py-2 pr-3"><SenderBadge domain={a.from_domain} /></td>
                    <td className="py-2 pr-3"><SemaphoreDot semaphore={a.semaphore} score={a.score} /></td>
                    <td className="py-2 pr-3 text-xs">{a.status}</td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-klarim-muted">{a.email_id || '—'}</td>
                    <td className="py-2 text-xs text-klarim-muted">{formatDate(a.sent_at)}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-klarim-muted">Nenhum alerta.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
      </Card>
    </div>
  )
}

// Consultas de perfil público (evento profile_view do site_events, KL-51 f4). Mostra
// o domínio consultado + data/hora. O site_events não guarda IP (só domínio/sessão).
function ProfileViewsTab() {
  // KL-96 — contadores PRÓPRIOS (avisos profile_view enviados, do email_log), separados
  // dos alertas; a lista mostra as consultas (site_events) com origem/UTM.
  const stats = useAsync(() => admin.profileViewStats(), [])
  const { data, loading, error } = useAsync(() => admin.analyticsEvents(200, 'profile_view'), [])
  const rows = data?.events || []
  const domOf = (e) => (e.metadata && e.metadata.domain) || (e.target_url || '').replace(/^https?:\/\//, '') || '—'

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Hoje" value={stats.data?.today ?? 0} accent="#FF6B35" />
        <StatCard label="Semana" value={stats.data?.week ?? 0} />
        <StatCard label="Mês" value={stats.data?.month ?? 0} />
        <StatCard label="Total" value={stats.data?.total ?? 0} />
      </div>
    <Card>
      <p className="mb-3 text-sm text-klarim-muted">
        Avisos de "perfil consultado" enviados aos donos. Abaixo, quem consultou os perfis
        públicos <span className="font-mono">/site/&#123;dominio&#125;</span> — cada consulta
        pode disparar um aviso ao dono (se tiver e-mail de contato).
      </p>
      {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-klarim-muted">
                <th className="py-2 pr-3">Site consultado</th>
                <th className="py-2 pr-3">Origem</th>
                <th className="py-2">Quando</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e, i) => (
                <tr key={i} className="border-t border-klarim-border">
                  <td className="py-2 pr-3 font-mono text-xs text-klarim-text">
                    <a href={`/site/${domOf(e)}`} target="_blank" rel="noreferrer" className="hover:underline">{domOf(e)}</a>
                  </td>
                  <td className="py-2 pr-3 text-xs text-klarim-muted">{e.utm_campaign || '—'}</td>
                  <td className="py-2 text-xs text-klarim-muted">{formatDate(e.created_at)}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={3} className="py-8 text-center text-klarim-muted">Nenhuma consulta de perfil ainda.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
    </div>
  )
}
