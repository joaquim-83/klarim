import { useEffect, useRef, useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { createPayment, getPaymentStatus } from '../lib/api'

export default function Payment() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const navigate = useNavigate()

  const [charge, setCharge] = useState(null)
  const [error, setError] = useState('')
  const [paid, setPaid] = useState(false)
  const [copied, setCopied] = useState(false)
  const created = useRef(false)

  // 1) Cria a cobrança uma vez.
  useEffect(() => {
    if (!url) {
      navigate('/', { replace: true })
      return
    }
    if (created.current) return
    created.current = true
    createPayment(url)
      .then(setCharge)
      .catch((e) => setError(e.message || 'Erro ao criar cobrança.'))
  }, [url, navigate])

  // 2) Polling do status a cada 3s até pagar.
  useEffect(() => {
    if (!charge?.charge_id || paid) return
    const t = setInterval(async () => {
      try {
        const s = await getPaymentStatus(charge.charge_id)
        if (s.paid) {
          setPaid(true)
          clearInterval(t)
          setTimeout(
            () =>
              navigate(
                `/report?url=${encodeURIComponent(url)}&charge_id=${encodeURIComponent(charge.charge_id)}`,
                { replace: true },
              ),
            1200,
          )
        }
      } catch {
        /* mantém o polling */
      }
    }, 3000)
    return () => clearInterval(t)
  }, [charge, paid, url, navigate])

  function copy() {
    navigator.clipboard?.writeText(charge.br_code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (error) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold text-klarim-fail">Não foi possível iniciar o pagamento</h1>
          <p className="mt-3 text-sm text-klarim-muted">{error}</p>
          <Link to="/" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Voltar
          </Link>
        </div>
      </Layout>
    )
  }

  if (!charge) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="klarim-spinner h-14 w-14" />
          <p className="mt-6 text-klarim-muted">Gerando cobrança PIX…</p>
        </div>
      </Layout>
    )
  }

  if (paid) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="flex h-20 w-20 items-center justify-center rounded-full bg-klarim-ok text-4xl text-klarim-bg">
            ✓
          </div>
          <h1 className="mt-6 text-2xl font-bold text-klarim-ok">Pagamento confirmado!</h1>
          <p className="mt-2 text-klarim-muted">Liberando seu relatório…</p>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="mx-auto max-w-md text-center">
        <h1 className="text-2xl font-bold">Pague com PIX</h1>
        <p className="mt-1 text-klarim-muted">
          Relatório completo — <span className="font-bold text-klarim-text">{charge.amount_display}</span>
        </p>

        {charge.qr_code_base64 && (
          <div className="mx-auto mt-6 w-56 rounded-xl bg-white p-3">
            <img src={charge.qr_code_base64} alt="QR code PIX" className="w-full" />
          </div>
        )}

        <p className="mt-4 text-sm text-klarim-muted">
          Escaneie o QR code com o app do seu banco, ou use o copia-e-cola:
        </p>

        <div className="mt-3 flex items-stretch gap-2">
          <input
            readOnly
            value={charge.br_code || ''}
            className="min-w-0 flex-1 truncate rounded-lg border border-klarim-border bg-klarim-surface px-3 py-2 font-mono text-xs text-klarim-muted"
            aria-label="Código PIX copia-e-cola"
          />
          <button
            onClick={copy}
            className="shrink-0 rounded-lg bg-klarim-alert px-4 py-2 font-bold text-klarim-bg"
          >
            {copied ? 'Copiado!' : 'Copiar'}
          </button>
        </div>

        <div className="mt-8 flex items-center justify-center gap-3 text-klarim-muted">
          <div className="klarim-spinner h-5 w-5" />
          <span>Aguardando pagamento…</span>
        </div>

        <p className="mt-6 text-xs text-klarim-muted">
          A confirmação é automática assim que o PIX cair. Você pode deixar esta
          página aberta.
        </p>

        <div className="mt-8">
          <Link to="/" className="text-sm text-klarim-muted hover:text-klarim-text">
            ← Cancelar
          </Link>
        </div>
      </div>
    </Layout>
  )
}
