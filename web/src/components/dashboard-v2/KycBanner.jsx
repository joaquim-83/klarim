import { useState } from 'react'
import { maskCPF, isValidCPF, canSubmitKyc } from '../../lib/gate/ux.js'
import { emptyAddress } from '../../lib/gate/address.js'
import AddressFields from './AddressFields.jsx'

// KL-158 — banner de KYC CLICÁVEL (antes era texto puro, sem ação). O usuário sabia que precisava
// completar o cadastro mas não chegava ao formulário. Agora: botão "Completar cadastro →" → modal
// com os 3 campos (CPF mascarado/validado, endereço, telefone) → POST /api/account/kyc → onDone
// (o chamador re-busca o scan para mostrar o resultado completo). Reusa a lógica do wizard (step 4).
// Componente ÚNICO usado em todos os lugares que mostram resultado sem KYC (dashboard scan rápido,
// último resultado). Erros inline (CPF inválido/duplicado, e-mail não confirmado).

async function postKyc(body) {
  const r = await fetch('/api/account/kyc', {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) { const e = new Error(data.detail || `Erro ${r.status}`); e.status = r.status; throw e }
  return data
}

function KycModal({ onClose, onDone }) {
  const [cpf, setCpf] = useState('')
  const [address, setAddress] = useState(emptyAddress())   // KL-163 P2 — endereço estruturado
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    setErr(''); setBusy(true)
    try {
      const r = await postKyc({ cpf, address, phone })   // `address` = objeto estruturado
      if (r.kyc_completed) { onDone?.() } else {
        setErr('Preencha CPF, endereço completo e telefone para liberar os detalhes.')
      }
    } catch (e) {
      setErr(e.status === 409 ? 'Este CPF já está vinculado a outra conta.'
        : e.status === 422 ? (e.message || 'Dados inválidos. Verifique CPF, CEP e UF.')
        : e.status === 403 ? 'Confirme seu e-mail antes de completar o cadastro.'
        : (e.message || 'Não foi possível salvar. Tente novamente.'))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Completar cadastro">
      <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <h3 className="text-lg font-bold text-white">Confirme sua identidade</h3>
        <p className="mt-1 text-sm text-slate-300">
          Para proteger domínios contra uso indevido, os detalhes das verificações exigem confirmação de
          identidade. Seus dados são protegidos pela LGPD e usados só para vincular consultas à sua conta.
        </p>
        <div className="mt-4 grid gap-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-200">CPF</span>
            <input inputMode="numeric" value={cpf} onChange={(e) => setCpf(maskCPF(e.target.value))}
              placeholder="000.000.000-00"
              className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
            {cpf && !isValidCPF(cpf) && <span className="mt-1 block text-xs text-red-400">CPF incompleto ou inválido.</span>}
          </label>
          <AddressFields value={address} onChange={setAddress} />
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-200">Telefone</span>
            <input inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
              placeholder="+55 (00) 00000-0000"
              className="h-12 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none" />
            <span className="mt-1 block text-xs text-slate-400">
              Seu telefone será verificado por SMS em breve. Por enquanto, os dados são validados pelo CPF e e-mail.
            </span>
          </label>
        </div>
        {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" onClick={submit} disabled={busy || !canSubmitKyc(cpf, address, phone)}
            className="inline-flex min-h-[44px] items-center rounded-xl bg-brand-500 px-5 text-sm font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 disabled:opacity-50">
            {busy ? 'Confirmando…' : 'Confirmar'}
          </button>
          <button type="button" onClick={onClose} className="text-sm text-slate-400 hover:text-slate-200">Cancelar</button>
        </div>
      </div>
    </div>
  )
}

export default function KycBanner({ message, onCompleted }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-amber-200">
          🔒 {message || 'Complete seu cadastro (CPF + endereço + telefone) para ver os detalhes de cada verificação.'}
        </p>
        <button type="button" onClick={() => setOpen(true)}
          className="inline-flex min-h-[40px] shrink-0 items-center rounded-lg bg-amber-500/90 px-3 py-1.5 text-sm font-semibold text-slate-900 transition-colors hover:bg-amber-400">
          Completar cadastro →
        </button>
      </div>
      {open && <KycModal onClose={() => setOpen(false)} onDone={() => { setOpen(false); onCompleted?.() }} />}
    </div>
  )
}
