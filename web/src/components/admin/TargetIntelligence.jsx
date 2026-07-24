// KL-104 P3 — Visão 360° do alvo: 4 seções colapsáveis (monitoramento, funil, visitantes,
// timeline) a partir de 1 fetch (`/admin/targets/{id}/intelligence`). Seção null/error →
// "Dados indisponíveis" (degradação graciosa). IPs já chegam mascarados (/24) do backend.
import { useState, useEffect, useCallback } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { DomainLink, formatDate } from './ui'

const FUNNEL_LABEL = {
  discovered: 'Descoberto', scanned: 'Escaneado', alerted: 'Alertado',
  account_created: 'Conta criada', monitoring: 'Monitorando', paid: 'Pago',
}
const PLAN_LABEL = { free: 'Free', pro: 'Pro', agency: 'Agency', basic: 'Basic', enterprise: 'Enterprise' }
const VIGILIA_ICON = { ok: '✅', warning: '⚠️', critical: '🔴', error: '❌' }
const LEAD_COLOR = { hot: '#00D26A', warm: '#F0C000', cold: '#8B949E' }
const SOURCE_LABEL = {
  direct: 'Direto', alert_email: 'E-mail de alerta', profile_view: 'Aviso de perfil',
  google: 'Google', internal: 'Interno', other: 'Outros',
}

function Section({ title, children, defaultOpen = false }) {
  return (
    <details open={defaultOpen} className="rounded-lg border border-klarim-border bg-klarim-surface">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-klarim-text hover:text-klarim-alert">
        {title}
      </summary>
      <div className="border-t border-klarim-border px-4 py-3">{children}</div>
    </details>
  )
}

function Unavailable() {
  return <p className="text-sm text-klarim-muted">Dados indisponíveis.</p>
}

function has(section) {
  return section && !section.error
}

// ---- Funil visual (6 etapas; atingidas em laranja, futuras em cinza tracejado) ---- //
function FunnelBar({ stages }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {stages.map((s, i) => (
        <div key={s.stage} className="flex items-center gap-1">
          <div title={s.at ? formatDate(s.at) : 'não atingido'}
            className={`rounded-md px-2 py-1 text-xs ${s.active
              ? 'bg-klarim-alert font-semibold text-black'
              : 'border border-dashed border-klarim-border text-klarim-muted'}`}>
            {FUNNEL_LABEL[s.stage] || s.stage}
          </div>
          {i < stages.length - 1 && <span className="text-klarim-muted">→</span>}
        </div>
      ))}
    </div>
  )
}

function MonitoringSection({ data }) {
  if (!has(data)) return <Unavailable />
  const { monitors = [], vigilias = [], owner_verified, technician } = data
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="text-xs uppercase text-klarim-muted">Monitorado por</div>
        {monitors.length === 0 ? <p className="text-klarim-muted">Ninguém ainda.</p> : (
          <ul className="mt-1 space-y-1">
            {monitors.map((m, i) => (
              <li key={i}>
                {m.user_email} <span className="text-klarim-muted">
                  ({PLAN_LABEL[m.plan] || m.plan}{m.level ? ` · nível ${m.level}` : ''}
                  {m.is_owner ? ' · dono' : ''}{m.since ? ` · desde ${formatDate(m.since)}` : ''})
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="text-xs uppercase text-klarim-muted">Vigílias</div>
        {vigilias.length === 0 ? <p className="text-klarim-muted">Nenhuma vigília ativa.</p> : (
          <div className="mt-1 flex flex-wrap gap-2">
            {vigilias.map((v, i) => (
              <span key={i} className="rounded border border-klarim-border px-2 py-0.5 text-xs">
                {VIGILIA_ICON[v.status] || '•'} {v.type}
                {v.enabled === false && <span className="text-klarim-muted"> (off)</span>}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-6">
        <div>
          <div className="text-xs uppercase text-klarim-muted">Dono verificado</div>
          <p className="mt-1">
            {owner_verified?.verified
              ? <>Sim <span className="text-klarim-muted">({owner_verified.method || '—'}
                {owner_verified.verified_at ? `, ${formatDate(owner_verified.verified_at)}` : ''})</span></>
              : <span className="text-klarim-muted">Não</span>}
          </p>
        </div>
        <div>
          <div className="text-xs uppercase text-klarim-muted">Técnico</div>
          <p className="mt-1">
            {technician
              ? <>{technician.email} <span className="text-klarim-muted">({technician.status})</span></>
              : <span className="text-klarim-muted">Nenhum</span>}
          </p>
        </div>
      </div>
    </div>
  )
}

function FunnelSection({ data }) {
  if (!has(data)) return <Unavailable />
  const { funnel_stages = [], emails_sent = [], emails_summary, lead_score } = data
  return (
    <div className="space-y-3 text-sm">
      <FunnelBar stages={funnel_stages} />
      {lead_score && (
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase text-klarim-muted">Lead score</span>
          <span className="rounded px-2 py-0.5 text-xs font-semibold text-black"
            style={{ background: LEAD_COLOR[lead_score.classification] || '#8B949E' }}>
            {lead_score.score} · {lead_score.classification}
          </span>
        </div>
      )}
      {emails_summary && (
        <p className="text-klarim-muted">
          {emails_summary.total} e-mail(s) enviado(s)
          {emails_summary.by_type && Object.keys(emails_summary.by_type).length > 0 &&
            ` (${Object.entries(emails_summary.by_type).map(([k, v]) => `${v} ${k}`).join(', ')})`}
          {emails_summary.last_sent_at ? ` · último: ${formatDate(emails_summary.last_sent_at)}` : ''}
        </p>
      )}
      {emails_sent.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left uppercase text-klarim-muted">
                <th className="py-1 pr-3">Tipo</th><th className="py-1 pr-3">Remetente</th>
                <th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Data</th>
              </tr>
            </thead>
            <tbody>
              {emails_sent.map((e, i) => (
                <tr key={i} className="border-t border-klarim-border">
                  <td className="py-1 pr-3">{e.type}</td>
                  <td className="py-1 pr-3 text-klarim-muted">{e.sender_domain || '—'}</td>
                  <td className="py-1 pr-3">{e.status}</td>
                  <td className="py-1 pr-3 text-klarim-muted">{formatDate(e.sent_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function VisitorsSection({ data }) {
  if (!has(data)) return <Unavailable />
  const { total_queries = 0, unique_ips = 0, top_ips = [], traffic_sources } = data
  return (
    <div className="space-y-3 text-sm">
      <p><b>{total_queries}</b> consultas · <b>{unique_ips}</b> IPs únicos
        <span className="text-klarim-muted"> (últimos 30 dias)</span></p>
      {traffic_sources && (
        <div className="flex flex-wrap gap-2 text-xs">
          {Object.entries(traffic_sources).map(([k, v]) => (
            <span key={k} className="rounded border border-klarim-border px-2 py-0.5">
              {SOURCE_LABEL[k] || k}: {v}
            </span>
          ))}
        </div>
      )}
      {top_ips.length === 0 ? <p className="text-klarim-muted">Nenhuma consulta humana no período.</p> : (
        <ul className="space-y-1">
          {top_ips.map((ip, i) => (
            <li key={i} className="border-b border-klarim-border py-1">
              <span className="font-mono text-xs">{ip.ip_masked}</span>
              {ip.country ? ` (${ip.country})` : ''} — {ip.queries} consulta(s)
              {ip.other_domains_queried && ip.other_domains_queried.length > 0 && (
                <span className="text-klarim-muted"> · também pesquisou: </span>
              )}
              {(ip.other_domains_queried || []).map((d, j) => (
                <span key={j}>
                  {j > 0 && <span className="text-klarim-muted">, </span>}
                  <DomainLink domain={d.domain} targetId={d.target_id} />
                </span>
              ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TimelineSection({ data, id }) {
  const [events, setEvents] = useState(has(data) ? (data.events || []) : [])
  const [cursor, setCursor] = useState(has(data) ? data.next_cursor : null)
  const [hasMore, setHasMore] = useState(has(data) ? data.has_more : false)
  const [loading, setLoading] = useState(false)

  const loadMore = useCallback(async () => {
    if (!cursor || loading) return
    setLoading(true)
    try {
      const r = await admin.targetIntelligence(id, { before: cursor })
      const tl = r.timeline
      if (tl && !tl.error) {
        setEvents((prev) => [...prev, ...(tl.events || [])])
        setCursor(tl.next_cursor)
        setHasMore(tl.has_more)
      } else { setHasMore(false) }
    } catch { setHasMore(false) } finally { setLoading(false) }
  }, [cursor, loading, id])

  if (!has(data)) return <Unavailable />
  if (events.length === 0) return <p className="text-sm text-klarim-muted">Nenhum evento.</p>
  return (
    <div className="space-y-2 text-sm">
      <ul className="space-y-1">
        {events.map((e, i) => (
          <li key={i} className="flex items-start gap-2 border-b border-klarim-border py-1">
            <span>{e.icon}</span>
            <span className="text-klarim-muted whitespace-nowrap text-xs">{formatDate(e.at)}</span>
            <span className="flex-1">
              {e.link
                ? <a href={e.link} className="text-klarim-alert hover:underline">{e.description}</a>
                : e.description}
              {e.detail && <span className="text-klarim-muted"> · {e.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button onClick={loadMore} disabled={loading}
          className="rounded-lg border border-klarim-border px-3 py-1.5 text-sm text-klarim-muted hover:text-klarim-text disabled:opacity-50">
          {loading ? 'Carregando…' : 'Carregar mais ↓'}
        </button>
      )}
    </div>
  )
}

export default function TargetIntelligence({ targetId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    admin.targetIntelligence(targetId)
      .then((d) => { if (alive) setData(d) })
      .catch((e) => { if (alive) setError(e.message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [targetId])

  if (loading) return <div className="text-sm text-klarim-muted">Carregando inteligência…</div>
  if (error) return <div className="text-sm text-klarim-fail">Falha ao carregar inteligência: {error}</div>
  if (!data) return null

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-bold text-klarim-muted">Inteligência 360° (KL-104)</h2>
      <Section title="🛡️ Monitoramento" defaultOpen><MonitoringSection data={data.monitoring} /></Section>
      <Section title="📈 Lead & Conversão" defaultOpen><FunnelSection data={data.funnel} /></Section>
      <Section title="👥 Visitantes (últimos 30 dias)"><VisitorsSection data={data.visitors} /></Section>
      <Section title="🕑 Timeline"><TimelineSection data={data.timeline} id={targetId} /></Section>
    </div>
  )
}
