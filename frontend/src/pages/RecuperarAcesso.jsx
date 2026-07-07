import { useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { recoveryValidate, recoveryDownload } from '../lib/api'

function RecoveryDownloadButton({ token, chargeId, kind, label }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  async function onClick() {
    setBusy(true)
    setFailed(false)
    try {
      await recoveryDownload(token, chargeId, kind)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60 ${failed ? 'bg-klarim-fail' : 'bg-klarim-alert'}`}
    >
      {busy ? 'Gerando…' : failed ? 'Erro — tentar' : label}
    </button>
  )
}

function fmtDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso.replace(' ', 'T')).toLocaleDateString('pt-BR')
  } catch {
    return iso
  }
}

export default function RecuperarAcesso() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [state, setState] = useState({ loading: true, data: null, error: '' })

  useEffect(() => {
    if (!token) {
      setState({ loading: false, data: null, error: 'Link inválido.' })
      return
    }
    recoveryValidate(token)
      .then((d) => setState({ loading: false, data: d, error: '' }))
      .catch(() => setState({ loading: false, data: null, error: 'Falha ao validar o link.' }))
  }, [token])

  if (state.loading) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="klarim-spinner h-14 w-14" />
          <p className="mt-6 text-klarim-muted">Validando link…</p>
        </div>
      </Layout>
    )
  }

  const d = state.data
  if (state.error || !d || !d.valid) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold text-klarim-fail">Link expirado ou inválido</h1>
          <p className="mt-3 text-sm text-klarim-muted">
            {(d && d.error) || state.error || 'Solicite um novo link.'}
          </p>
          <Link to="/recuperar" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Solicitar novo link
          </Link>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="mx-auto max-w-lg">
        <h1 className="text-center text-2xl font-bold">Seus relatórios</h1>
        <p className="mt-1 text-center text-sm text-klarim-muted">
          E-mail: <span className="font-mono">{d.email}</span>
        </p>

        <div className="mt-8 space-y-4">
          {d.reports.length === 0 && (
            <p className="text-center text-klarim-muted">Nenhum relatório encontrado.</p>
          )}
          {d.reports.map((r) => (
            <div key={r.charge_id} className="rounded-xl border border-klarim-border bg-klarim-surface p-4">
              <div className="break-all font-mono text-sm text-klarim-text">{r.target_url}</div>
              <div className="mt-1 text-xs text-klarim-muted">
                Pago em {fmtDate(r.paid_at)} · {r.amount_display}
              </div>
              <div className="mt-3 flex gap-2">
                <RecoveryDownloadButton token={token} chargeId={r.charge_id} kind="executive" label="Baixar Executivo" />
                <RecoveryDownloadButton token={token} chargeId={r.charge_id} kind="technical" label="Baixar Técnico" />
              </div>
            </div>
          ))}
        </div>

        <p className="mt-8 text-center text-xs text-klarim-muted">
          Este link expira em 24 horas. Para um novo, acesse{' '}
          <Link to="/recuperar" className="text-klarim-alert">klarim.net/recuperar</Link>.
        </p>
      </div>
    </Layout>
  )
}
