import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, Loading, ErrorBox, Badge, relativeTime, formatDate } from '../../components/admin/ui'

const PERIODS = [
  { key: 'today', label: 'Hoje' },
  { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' },
  { key: 'total', label: 'Total' },
]

const FUNNEL = [
  { key: 'emails_sent', label: 'E-mails enviados', color: '#8B949E' },
  { key: 'links_clicked', label: 'Links clicados', color: '#2DD4BF' },
  { key: 'results_viewed', label: 'Resultados vistos', color: '#3B82F6' },
  { key: 'cta_clicked', label: 'CTA clicados', color: '#A855F7' },
  { key: 'payments_created', label: 'PIX gerados', color: '#F0C000' },
  { key: 'payments_completed', label: 'Pagamentos', color: '#00D26A' },
  { key: 'reports_downloaded', label: 'PDFs baixados', color: '#FF6B35' },
]

const EV_COLOR = {
  page_view: '#8B949E', scan_started: '#2DD4BF', scan_completed: '#2DD4BF',
  result_viewed: '#3B82F6', cta_clicked: '#A855F7', payment_created: '#F0C000',
  payment_completed: '#00D26A', report_downloaded: '#FF6B35', email_link_clicked: '#58A6FF',
}

function secs(s) {
  if (s == null) return '—'
  const n = Math.round(Number(s))
  return n < 60 ? `${n}s` : `${Math.floor(n / 60)}min`
}

// Limpa um page_url para exibição: remove UTM e, quando há ?url=<alvo>, mostra
// "<path> → <hostname>" (ex.: /result → iclinic.com.br). Idempotente — se o valor
// já vier limpo do backend (sem '?'), retorna como está. Espelha _clean_page_key.
function cleanPageUrl(raw) {
  if (!raw || !raw.includes('?')) return raw || ''
  try {
    const u = new URL(raw, 'https://klarim.net')
    const target = u.searchParams.get('url')
    if (target) {
      try {
        const t = new URL(target.startsWith('http') ? target : `https://${target}`)
        return `${u.pathname} → ${t.hostname}`
      } catch {
        return u.pathname
      }
    }
    for (const k of [...u.searchParams.keys()]) {
      if (k.startsWith('utm_')) u.searchParams.delete(k)
    }
    const rest = u.searchParams.toString()
    return rest ? `${u.pathname}?${rest}` : u.pathname
  } catch {
    return raw
  }
}

export default function Analytics() {
  const [period, setPeriod] = useState('7d')
  const { data, loading, error } = useAsync(
    () => Promise.all([
      admin.analyticsFunnel(period), admin.analyticsAbandoned(period),
      admin.analyticsCampaigns(period), admin.analyticsPages(period),
      admin.analyticsEvents(50), admin.publicScans(),
    ]).then(([funnel, abandoned, campaigns, pages, events, publicScans]) => ({
      funnel, abandoned: abandoned.abandoned, campaigns: campaigns.campaigns,
      pages: pages.pages, events: events.events, publicScans,
    })),
    [period],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Analytics</h1>
        <div className="flex gap-1 rounded-lg border border-klarim-border bg-klarim-surface p-1">
          {PERIODS.map((p) => (
            <button key={p.key} onClick={() => setPeriod(p.key)}
              className={`rounded px-3 py-1 text-sm ${period === p.key ? 'bg-klarim-alert text-klarim-bg font-semibold' : 'text-klarim-muted hover:text-klarim-text'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
        <>
          {/* Scans públicos verificados (KL-25) */}
          <Card title="Scans públicos (verificação por e-mail)">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ['Códigos enviados', data.publicScans.codes_sent],
                ['Verificados', data.publicScans.verified],
                ['E-mails distintos', data.publicScans.distinct_emails],
                ['Scans grátis usados', data.publicScans.free_scans_used],
                ['Scans públicos', data.publicScans.public_scans],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-center">
                  <div className="text-lg font-bold">{value ?? 0}</div>
                  <div className="text-xs text-klarim-muted">{label}</div>
                </div>
              ))}
            </div>
          </Card>

          {/* Funil */}
          <Card title="Funil de conversão">
            {(() => {
              const top = Math.max(data.funnel.emails_sent || 0, 1)
              return (
                <div className="space-y-2">
                  {FUNNEL.map((step) => {
                    const n = data.funnel[step.key] || 0
                    const pct = ((n / top) * 100)
                    return (
                      <div key={step.key} className="flex items-center gap-3">
                        <div className="w-36 shrink-0 text-sm text-klarim-muted">{step.label}</div>
                        <div className="h-6 flex-1 overflow-hidden rounded bg-klarim-bg">
                          <div className="h-full rounded" style={{ width: `${Math.max(pct, 2)}%`, backgroundColor: step.color }} />
                        </div>
                        <div className="w-20 shrink-0 text-right text-sm">
                          <span className="font-bold">{n}</span>
                          <span className="ml-1 text-xs text-klarim-muted">{pct.toFixed(1)}%</span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )
            })()}
          </Card>

          {/* Carrinho abandonado */}
          <Card title={`Carrinho abandonado (${data.abandoned.length})`}>
            {data.abandoned.length === 0 ? (
              <p className="text-sm text-klarim-muted">Nenhum PIX gerado sem pagamento no período.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase text-klarim-muted">
                      <th className="py-2 pr-3">Sessão</th>
                      <th className="py-2 pr-3">Site</th>
                      <th className="py-2 pr-3">Valor</th>
                      <th className="py-2 pr-3">Quando</th>
                      <th className="py-2">Tempo no site</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.abandoned.map((a) => (
                      <tr key={a.session_id} className="border-t border-klarim-border">
                        <td className="py-2 pr-3 font-mono text-[11px] text-klarim-muted">{(a.session_id || '').slice(0, 8)}</td>
                        <td className="py-2 pr-3">
                          <div className="max-w-[220px] truncate font-mono text-xs" title={a.target_url}>
                            {a.target_url}
                          </div>
                        </td>
                        <td className="py-2 pr-3">{a.amount ? `R$ ${(a.amount / 100).toFixed(2).replace('.', ',')}` : '—'}</td>
                        <td className="py-2 pr-3 text-klarim-muted">{relativeTime(a.created_at)}</td>
                        <td className="py-2 text-klarim-muted">{secs(a.duration_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* Campanhas */}
            <Card title="Atribuição por campanha">
              {data.campaigns.length === 0 ? (
                <p className="text-sm text-klarim-muted">Sem cliques atribuídos ainda.</p>
              ) : (
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase text-klarim-muted">
                      <th className="py-2 pr-2">Campanha</th>
                      <th className="py-2 pr-2">Cliques</th>
                      <th className="py-2 pr-2">Scans</th>
                      <th className="py-2 pr-2">CTAs</th>
                      <th className="py-2 pr-2">Pagos</th>
                      <th className="py-2">Conv.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.campaigns.map((c) => (
                      <tr key={c.utm_campaign} className="border-t border-klarim-border">
                        <td className="py-2 pr-2"><Badge color="#FF6B35">{c.utm_campaign}</Badge></td>
                        <td className="py-2 pr-2">{c.clicks}</td>
                        <td className="py-2 pr-2">{c.scans}</td>
                        <td className="py-2 pr-2">{c.ctas}</td>
                        <td className="py-2 pr-2">{c.payments}</td>
                        <td className="py-2 font-semibold">{c.clicks > 0 ? ((c.payments / c.clicks) * 100).toFixed(1) + '%' : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
            </Card>

            {/* Páginas */}
            <Card title="Páginas mais visitadas">
              {data.pages.length === 0 ? (
                <p className="text-sm text-klarim-muted">Sem page views ainda.</p>
              ) : (
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase text-klarim-muted">
                      <th className="py-2 pr-3">Página</th>
                      <th className="py-2 pr-3">Views</th>
                      <th className="py-2">Sessões</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.pages.map((p, i) => (
                      <tr key={i} className="border-t border-klarim-border">
                        <td className="py-2 pr-3">
                          <div className="max-w-[280px] truncate font-mono text-xs" title={p.page_url}>
                            {cleanPageUrl(p.page_url)}
                          </div>
                        </td>
                        <td className="py-2 pr-3">{p.views}</td>
                        <td className="py-2">{p.sessions}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
            </Card>
          </div>

          {/* Timeline de eventos */}
          <Card title={`Eventos recentes (${data.events.length})`}>
            {data.events.length === 0 ? (
              <p className="text-sm text-klarim-muted">Nenhum evento ainda.</p>
            ) : (
              <div className="max-h-96 space-y-1 overflow-y-auto">
                {data.events.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 border-b border-klarim-border/50 py-1.5 text-sm">
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase"
                      style={{ color: EV_COLOR[e.event_type] || '#8B949E', backgroundColor: `${EV_COLOR[e.event_type] || '#8B949E'}22` }}>
                      {e.event_type}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-klarim-muted"
                      title={e.target_url || e.page_url || ''}>
                      {e.target_url || cleanPageUrl(e.page_url)}{e.utm_campaign ? ` · ${e.utm_campaign}` : ''}
                    </span>
                    <span className="whitespace-nowrap text-xs text-klarim-muted">{formatDate(e.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
