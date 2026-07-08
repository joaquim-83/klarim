import { useState } from 'react'
import { Link } from 'react-router-dom'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import {
  Card, Loading, ErrorBox, Button, Badge, PlatformBadge, StatusBadge, SourceBadge,
  SemaphoreDot, Pagination, relativeTime, STATUS_LABEL,
} from '../../components/admin/ui'

const STATUS_OPTS = ['discovered', 'scanned', 'alerted', 'sem_contato', 'unsubscribed', 'descartado']
const PLATFORM_OPTS = ['duda', 'wordpress', 'cra', 'wix', 'shopify', 'squarespace', 'unknown']
const SECTOR_OPTS = ['hotel', 'clinica', 'escola', 'restaurante', 'ecommerce', 'contabilidade',
  'juridico', 'condominio', 'imobiliaria', 'automotivo', 'outro']
const SOURCE_OPTS = ['public', 'discovery', 'admin', 'manual']
const PAGE_SIZE = 25

function Select({ value, onChange, options, allLabel, labels }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert"
    >
      <option value="">{allLabel}</option>
      {options.map((o) => <option key={o} value={o}>{labels?.[o] || o}</option>)}
    </select>
  )
}

// Setor com indicador visual de confiança da classificação (refino KL-11):
//  ≥0.8 badge normal · 0.5–0.79 borda pontilhada (provável) · <0.5 cinza com "?".
function SectorCell({ sector, confidence }) {
  const c = confidence == null ? null : Number(confidence)
  const label = sector || 'outro'
  const pct = c == null ? null : `${Math.round(c * 100)}%`
  if (c != null && c < 0.5) {
    return (
      <span title={`Classificação incerta${pct ? ` (${pct})` : ''}`}
        className="inline-block rounded-full border border-klarim-border bg-klarim-border/20 px-2 py-0.5 text-xs font-semibold text-klarim-muted">
        {label} ?
      </span>
    )
  }
  if (c != null && c < 0.8) {
    return (
      <span title={`Classificação provável (${pct})`}
        className="inline-block rounded-full border border-dashed border-klarim-alert/70 px-2 py-0.5 text-xs font-semibold text-klarim-text">
        {label}
      </span>
    )
  }
  return <span title={pct ? `Confiança ${pct}` : undefined}><Badge>{label}</Badge></span>
}

export default function Alvos() {
  const [status, setStatus] = useState('')
  const [platform, setPlatform] = useState('')
  const [sector, setSector] = useState('')
  const [source, setSource] = useState('')
  const [lowConf, setLowConf] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [msg, setMsg] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [reclassifying, setReclassifying] = useState(false)

  const { data, loading, error, reload } = useAsync(
    () => admin.targets({
      status, platform, sector, source, low_confidence: lowConf || undefined,
      limit: PAGE_SIZE, offset: page * PAGE_SIZE,
    }),
    [status, platform, sector, source, lowConf, page],
  )

  async function reclassifyDomains() {
    setReclassifying(true)
    setMsg('')
    try {
      const r = await admin.reclassifyDomains()
      setMsg(`Reclassificação por domínio: ${r.changed} de ${r.processed} alvos alterados.`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setReclassifying(false)
    }
  }

  async function act(id, fn, label) {
    setBusyId(id)
    setMsg('')
    try {
      await fn(id)
      setMsg(`${label} ✓`)
      reload()
    } catch (e) {
      setMsg(e.message)
    } finally {
      setBusyId(null)
    }
  }

  const rows = (data?.targets || []).filter((t) =>
    !search || (t.url || '').toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Alvos</h1>
        <div className="flex gap-2">
          <Button disabled={reclassifying} onClick={reclassifyDomains}>
            {reclassifying ? 'Reclassificando…' : 'Reclassificar domínios'}
          </Button>
          <Button variant="primary" onClick={() => setShowAdd(true)}>+ Adicionar alvo</Button>
        </div>
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap gap-2">
        <Select value={status} onChange={(v) => { setStatus(v); setPage(0) }} options={STATUS_OPTS} allLabel="Todos os status" labels={STATUS_LABEL} />
        <Select value={platform} onChange={(v) => { setPlatform(v); setPage(0) }} options={PLATFORM_OPTS} allLabel="Todas as plataformas" />
        <Select value={sector} onChange={(v) => { setSector(v); setPage(0) }} options={SECTOR_OPTS} allLabel="Todos os setores" />
        <Select value={source} onChange={(v) => { setSource(v); setPage(0) }} options={SOURCE_OPTS} allLabel="Todas as origens" />
        <button
          onClick={() => { setLowConf(!lowConf); setPage(0) }}
          className={`rounded-lg border px-3 py-1.5 text-sm ${lowConf ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-text' : 'border-klarim-border bg-klarim-surface text-klarim-muted hover:text-klarim-text'}`}
        >
          Classificação incerta
        </button>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Buscar por URL…"
          className="min-w-40 flex-1 rounded-lg border border-klarim-border bg-klarim-surface px-3 py-1.5 text-sm outline-none focus:border-klarim-alert"
        />
      </div>

      {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Plataforma</th>
                  <th className="py-2 pr-3">Setor</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Origem</th>
                  <th className="py-2 pr-3">E-mail</th>
                  <th className="py-2 pr-3">Último scan</th>
                  <th className="py-2">Ações</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className="border-t border-klarim-border align-middle">
                    <td className="py-2 pr-3">
                      <a href={t.url} target="_blank" rel="noreferrer" className="font-mono text-xs text-klarim-alert hover:underline">
                        {t.domain || t.url}
                      </a>
                    </td>
                    <td className="py-2 pr-3"><PlatformBadge platform={t.platform} /></td>
                    <td className="py-2 pr-3"><SectorCell sector={t.sector} confidence={t.classification_confidence} /></td>
                    <td className="py-2 pr-3">
                      {t.last_scan_score != null
                        ? <SemaphoreDot semaphore={t.last_semaphore} score={t.last_scan_score} />
                        : <span className="text-klarim-muted">—</span>}
                    </td>
                    <td className="py-2 pr-3"><StatusBadge status={t.status} /></td>
                    <td className="py-2 pr-3"><SourceBadge source={t.source} /></td>
                    <td className="py-2 pr-3 text-xs text-klarim-muted">{t.contact_email || '—'}</td>
                    <td className="py-2 pr-3 text-xs text-klarim-muted">{t.last_scan_at ? relativeTime(t.last_scan_at) : '—'}</td>
                    <td className="py-2">
                      <div className="flex gap-1">
                        <Button disabled={busyId === t.id} onClick={() => act(t.id, admin.scanTarget, 'Scan enfileirado')}>Escanear</Button>
                        <Button disabled={busyId === t.id || !t.contact_email} onClick={() => act(t.id, admin.alertTarget, 'Alerta enviado')}>Alertar</Button>
                        <Link to={`/painel/alvos/${t.id}`}><Button>Detalhes</Button></Link>
                      </div>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={9} className="py-8 text-center text-klarim-muted">Nenhum alvo.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={(data?.targets || []).length === PAGE_SIZE} />
      </Card>

      {showAdd && <AddTargetModal onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); reload() }} />}
    </div>
  )
}

function AddTargetModal({ onClose, onAdded }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    setBusy(true)
    setError('')
    try {
      await admin.addTarget(url.trim())
      onAdded()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-bold">Adicionar alvo</h3>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoFocus
          placeholder="https://www.exemplo.com.br"
          className="mb-4 w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm outline-none focus:border-klarim-alert"
        />
        {error && <div className="mb-4"><ErrorBox message={error} /></div>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" disabled={busy || !url.trim()} onClick={submit}>
            {busy ? 'Adicionando…' : 'Adicionar e escanear'}
          </Button>
        </div>
      </div>
    </div>
  )
}
