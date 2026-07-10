import { useEffect, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { monitoringStatus, monitoringApprove } from '../lib/api'

export default function MonitorarAprovar() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [info, setInfo] = useState(null) // { valid, domain, status }
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!token) {
      setInfo({ valid: false })
      return
    }
    monitoringStatus(token).then(setInfo).catch(() => setInfo({ valid: false }))
  }, [token])

  async function confirm() {
    setBusy(true)
    setError('')
    try {
      await monitoringApprove(token, displayName)
      setDone(true)
    } catch (e) {
      setError(e.message || 'Não foi possível confirmar.')
    } finally {
      setBusy(false)
    }
  }

  if (info === null) {
    return (
      <Layout withFooter={false}>
        <div className="flex justify-center pt-16"><div className="klarim-spinner h-12 w-12" /></div>
      </Layout>
    )
  }

  if (!info.valid || info.status === 'active') {
    const already = info.valid && info.status === 'active'
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold">
            {already ? 'Já está monitorado ✅' : 'Link inválido'}
          </h1>
          <p className="mt-3 text-sm text-klarim-muted">
            {already
              ? `${info.domain} já faz parte da seção Sites Monitorados.`
              : 'Este link de confirmação é inválido, expirou ou já foi usado.'}
          </p>
          <Link to="/monitorados" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Ver Sites Monitorados
          </Link>
        </div>
      </Layout>
    )
  }

  if (done) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-klarim-ok text-3xl text-klarim-bg">✓</div>
          <h1 className="mt-4 text-2xl font-bold text-klarim-ok">Monitoramento ativado!</h1>
          <p className="mt-3 text-sm text-klarim-muted">
            <strong>{info.domain}</strong> agora aparece na seção Sites Monitorados e será
            verificado semanalmente.
          </p>
          <Link to="/monitorados" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Ver Sites Monitorados
          </Link>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="mx-auto max-w-md pt-8 text-center">
        <h1 className="text-2xl font-bold">Monitoramento de Segurança</h1>
        <p className="mt-2 text-klarim-muted">
          Confirmar monitoramento gratuito para{' '}
          <span className="font-mono text-klarim-text">{info.domain}</span>
        </p>

        <ul className="mx-auto mt-6 max-w-sm space-y-2 text-left text-sm">
          <li className="flex items-center gap-2"><span>✅</span> Verificação semanal (29 checks)</li>
          <li className="flex items-center gap-2"><span>✅</span> Alerta por e-mail se o score cair</li>
          <li className="flex items-center gap-2"><span>✅</span> Listagem na seção Sites Monitorados</li>
        </ul>

        <div className="mt-6 text-left">
          <label className="text-sm text-klarim-muted">Nome da empresa (opcional)</label>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={120}
            placeholder="Ex.: Pousada Costeira"
            className="mt-1 w-full rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-alert focus:outline-none"
          />
        </div>

        <button
          onClick={confirm}
          disabled={busy}
          className="mt-5 w-full rounded-lg bg-klarim-ok px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60"
        >
          {busy ? 'Confirmando…' : 'Confirmar'}
        </button>
        {error && <p className="mt-3 text-sm text-klarim-fail">{error}</p>}
      </div>
    </Layout>
  )
}
