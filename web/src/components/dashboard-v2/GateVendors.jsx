import { useEffect, useState, useCallback } from 'react'
import { card, brandBtn, outlineBtn } from './shared.js'

// KL-152 P3 — avaliação de fornecedores (Enterprise). Só aparece se GET /api/gate/vendors não
// retornar 403 (o backend gateia por plano). Resultado de terceiro é REDIGIDO no servidor —
// mostramos contagens ("3 arquivos expostos"), nunca paths/credenciais.

async function req(path, opts = {}) {
  const r = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : {}, ...opts,
  })
  let data = {}
  try { data = await r.json() } catch { /* pode ser PDF/vazio */ }
  return { ok: r.ok, status: r.status, data }
}

const smBrand = 'inline-flex min-h-[40px] items-center rounded-lg bg-brand-500 px-3 py-1.5 text-sm font-semibold text-[var(--accent-text)] hover:bg-brand-400 disabled:opacity-40'
const smOutline = 'inline-flex min-h-[40px] items-center rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-40'

const STATUS = {
  approved: { label: 'Aprovado', dot: '🟢', cls: 'text-green-400' },
  attention: { label: 'Atenção', dot: '🟡', cls: 'text-yellow-400' },
  rejected: { label: 'Reprovado', dot: '🔴', cls: 'text-red-400' },
  pending: { label: 'Pendente', dot: '⚪', cls: 'text-slate-400' },
}
const semaColor = (s) => (s >= 90 ? '#22c55e' : s >= 50 ? '#eab308' : '#ef4444')

function Field({ label, hint, ...props }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-200">{label}</span>
      <input {...props}
        className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none" />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

function NewVendorModal({ onClose, onDone }) {
  const [f, setF] = useState({ name: '', url: '', approval_threshold: 80, critical_threshold: 0, notify_vendor: false, monitor_enabled: false })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })

  async function submit() {
    setBusy(true); setErr('')
    const { ok, data } = await req('/api/gate/vendors', { method: 'POST', body: JSON.stringify({
      name: f.name, url: f.url,
      approval_threshold: Number(f.approval_threshold), critical_threshold: Number(f.critical_threshold),
      notify_vendor: f.notify_vendor, monitor_enabled: f.monitor_enabled }) })
    setBusy(false)
    if (!ok) { setErr(data.detail || 'Erro ao avaliar.'); return }
    onDone()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true">
      <div className={`w-full max-w-lg ${card}`}>
        <h3 className="mb-4 text-lg font-bold text-white">Nova avaliação de fornecedor</h3>
        <div className="space-y-3">
          <Field label="Nome" placeholder="Ex: SaaS Alpha" value={f.name} onChange={set('name')} />
          <Field label="URL" placeholder="https://app.saasalpha.com.br" value={f.url} onChange={set('url')} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Threshold de aprovação" type="number" value={f.approval_threshold} onChange={set('approval_threshold')} hint="score mínimo" />
            <Field label="Máx. críticos" type="number" value={f.critical_threshold} onChange={set('critical_threshold')} hint="p/ aprovar" />
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-200">
            <input type="checkbox" checked={f.notify_vendor} onChange={set('notify_vendor')} className="h-4 w-4" />
            Notificar o fornecedor sobre a avaliação
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-200">
            <input type="checkbox" checked={f.monitor_enabled} onChange={set('monitor_enabled')} className="h-4 w-4" />
            Monitorar mensalmente
          </label>
        </div>
        {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className={smOutline}>Cancelar</button>
          <button type="button" onClick={submit} disabled={busy || !f.name || !f.url} className={smBrand}>
            {busy ? 'Avaliando…' : 'Avaliar →'}
          </button>
        </div>
      </div>
    </div>
  )
}

function VendorDetail({ vendorId, onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    req(`/api/gate/vendors/${vendorId}`).then(({ ok, data }) => ok ? setData(data) : setErr(data.detail || 'Erro'))
  }, [vendorId])
  if (err) return <tr><td colSpan={5} className="p-3 text-sm text-red-400">{err}</td></tr>
  if (!data) return <tr><td colSpan={5} className="p-3 text-sm text-slate-400">Carregando…</td></tr>
  const last = (data.scans || [])[0] || {}
  const s = last.summary || {}
  return (
    <tr><td colSpan={5} className="p-0">
      <div className="m-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-white">{data.vendor.domain}</span>
          <button onClick={onClose} className="text-xs text-slate-400 hover:text-slate-200">fechar ✕</button>
        </div>
        <p className="text-sm text-slate-300">
          {s.exposed_files ?? 0} arquivo(s) de configuração exposto(s) · {s.credentials ?? 0} credencial(is) detectada(s) · {s.unauth_endpoints ?? 0} endpoint(s) sem autenticação
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {catList(last).length === 0
            ? <span className="text-xs text-slate-500">Sem detalhes de categoria neste scan.</span>
            : catList(last).map((c) => (
                <span key={c.category} className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-200">
                  {c.status === 'pass' ? '✅' : c.status === 'warning' ? '⚠️' : '❌'} {c.label}
                </span>
              ))}
        </div>
        <p className="mt-2 text-xs text-slate-500">Paths e credenciais de terceiros são redigidos — apenas contagens são exibidas.</p>
      </div>
    </td></tr>
  )
}

// categorias derivadas do último scan (results já vêm agregáveis do backend via /vendors/{id})
function catList(scan) {
  const results = scan.results || []
  const order = [], agg = {}
  const LBL = { headers: 'Headers', ssl: 'SSL/TLS', exposure: 'Exposição', credentials: 'Credenciais', api: 'API', cors: 'CORS', cookies: 'Cookies' }
  for (const r of results) {
    const cat = r.category || 'outros'
    if (!agg[cat]) { agg[cat] = { category: cat, label: LBL[cat] || cat, status: 'pass' }; order.push(cat) }
    if (r.status === 'fail') agg[cat].status = 'fail'
    else if (r.status === 'error' && agg[cat].status !== 'fail') agg[cat].status = 'warning'
  }
  return order.map((c) => agg[c])
}

export default function GateVendors() {
  const [enabled, setEnabled] = useState(null)   // null=carregando · false=não Enterprise · true
  const [vendors, setVendors] = useState([])
  const [modal, setModal] = useState(false)
  const [openId, setOpenId] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [toast, setToast] = useState('')

  const load = useCallback(async () => {
    const { ok, status, data } = await req('/api/gate/vendors')
    if (status === 403) { setEnabled(false); return }
    setEnabled(true)
    if (ok) setVendors(data.vendors || [])
  }, [])
  useEffect(() => { load() }, [load])

  async function rescan(v) {
    setBusyId(v.id)
    const { ok } = await req(`/api/gate/vendors/${v.id}/scan`, { method: 'POST' })
    setBusyId(null)
    setToast(ok ? `${v.domain}: re-escaneado` : 'Falha ao re-escanear')
    load()
  }
  async function report() {
    if (!vendors.length) return
    const { ok, data } = await req('/api/gate/vendors/report', { method: 'POST', body: JSON.stringify({ vendor_ids: vendors.map((v) => v.id) }) })
    if (ok && data.report_id) window.open(`/api/gate/vendors/report/${data.report_id}`, '_blank', 'noopener')
    else setToast('Falha ao gerar relatório')
  }

  if (enabled === null || enabled === false) return null   // some p/ quem não é Enterprise

  return (
    <section className={`mb-6 ${card}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-bold text-white">🏢 Avaliação de Fornecedores</h2>
        <button onClick={() => setModal(true)} className={smBrand}>+ Avaliar novo fornecedor</button>
      </div>
      {toast && <p className="mb-2 text-xs text-slate-400">{toast}</p>}
      {vendors.length === 0 ? (
        <p className="text-sm text-slate-400">Nenhum fornecedor avaliado. Avalie um acima.</p>
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-slate-400"><th className="py-1">Fornecedor</th><th>URL</th><th>Score</th><th>Status</th><th>Ações</th></tr></thead>
          <tbody>
            {vendors.map((v) => {
              const st = STATUS[v.status] || STATUS.pending
              return (
                <>
                  <tr key={v.id} className="border-t border-slate-800 text-slate-200">
                    <td className="py-2 font-medium text-white">{v.name}</td>
                    <td className="truncate text-slate-400" style={{ maxWidth: 160 }}>{v.domain}</td>
                    <td>{v.last_scan_score != null ? <span style={{ color: semaColor(v.last_scan_score) }} className="font-bold">{v.last_scan_score}</span> : '—'} {st.dot}</td>
                    <td className={st.cls}>{st.label}</td>
                    <td>
                      <div className="flex gap-2">
                        <button title="Detalhe" onClick={() => setOpenId(openId === v.id ? null : v.id)} className="hover:opacity-70">📋</button>
                        <button title="Re-escanear" onClick={() => rescan(v)} disabled={busyId === v.id} className="hover:opacity-70 disabled:opacity-40">{busyId === v.id ? '⏳' : '🔄'}</button>
                        {v.notify_vendor && <span title="Notificação ao fornecedor habilitada">📧</span>}
                      </div>
                    </td>
                  </tr>
                  {openId === v.id && <VendorDetail vendorId={v.id} onClose={() => setOpenId(null)} />}
                </>
              )
            })}
          </tbody>
        </table>
      )}
      {vendors.length > 0 && (
        <button onClick={report} className={`mt-4 ${outlineBtn}`}>📄 Gerar relatório comparativo (PDF)</button>
      )}
      {modal && <NewVendorModal onClose={() => setModal(false)} onDone={() => { setModal(false); load() }} />}
    </section>
  )
}
