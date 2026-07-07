import { useEffect, useRef, useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { createPayment, getPaymentStatus } from '../lib/api'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Payment() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState('')
  const [charge, setCharge] = useState(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [paid, setPaid] = useState(false)
  const [copied, setCopied] = useState(false)
  const creating_ref = useRef(false)

  useEffect(() => {
    if (!url) navigate('/', { replace: true })
  }, [url, navigate])

  async function submitEmail(e) {
    e.preventDefault()
    if (!EMAIL_RE.test(email)) {
      setEmailError('Digite um e-mail válido para receber o relatório.')
      return
    }
    setEmailError('')
    if (creating_ref.current) return
    creating_ref.current = true
    setCreating(true)
    try {
      const c = await createPayment(url, email)
      setCharge(c)
    } catch (err) {
      setError(err.message || 'Erro ao criar cobrança.')
    } finally {
      setCreating(false)
    }
  }

  // Polling do status a cada 3s até pagar.
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
            1500,
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

  if (paid) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="flex h-20 w-20 items-center justify-center rounded-full bg-klarim-ok text-4xl text-klarim-bg">
            ✓
          </div>
          <h1 className="mt-6 text-2xl font-bold text-klarim-ok">Pagamento confirmado!</h1>
          <p className="mt-2 text-klarim-muted">
            Enviamos o relatório para <span className="text-klarim-text">{email}</span>. Liberando o download…
          </p>
        </div>
      </Layout>
    )
  }

  // Etapa 1 — e-mail (antes de gerar a cobrança).
  if (!charge) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-6 text-center">
          <h1 className="text-2xl font-bold">Relatório completo — R$ 29</h1>
          <p className="mt-2 text-klarim-muted">
            Informe seu e-mail. Enviaremos os relatórios (executivo + técnico) assim
            que o pagamento for confirmado.
          </p>
          <form onSubmit={submitEmail} className="mt-8 text-left">
            <label className="text-sm text-klarim-muted">Seu e-mail</label>
            <input
              type="email"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="voce@exemplo.com"
              className="mt-1 w-full rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-alert focus:outline-none"
              aria-label="Seu e-mail"
            />
            {emailError && <p className="mt-2 text-sm text-klarim-fail">{emailError}</p>}
            <button
              type="submit"
              disabled={creating}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60"
            >
              {creating ? (
                <>
                  <span className="klarim-spinner h-5 w-5" /> Gerando cobrança…
                </>
              ) : (
                'Gerar cobrança PIX'
              )}
            </button>
          </form>
          <div className="mt-6">
            <Link to="/" className="text-sm text-klarim-muted hover:text-klarim-text">
              ← Cancelar
            </Link>
          </div>
        </div>
      </Layout>
    )
  }

  // Etapa 2 — pagamento PIX.
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
          A confirmação é automática assim que o PIX cair. O relatório será enviado
          para <span className="text-klarim-text">{email}</span>.
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
