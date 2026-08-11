import { useEffect, useRef, useState } from 'react'
import { maskCep, isValidCep, parseViaCepResponse, UF_LIST } from '../../lib/gate/address.js'

// KL-163 P2 — campos de endereço ESTRUTURADO com auto-preenchimento por CEP (ViaCEP). Controlado:
// o pai detém o objeto `value` ({cep,street,number,complement,neighborhood,city,state}) e recebe
// `onChange(next)`. A busca no ViaCEP é debounced (500ms) e roda quando o CEP tem 8 dígitos; se o
// ViaCEP estiver fora/CEP não existir, os campos ficam editáveis manualmente (NÃO bloqueia o form).
// A lógica pura (máscara/validação/parse/UFs) vive em ../../lib/gate/address.js (testada).

const fieldCls = 'h-12 w-full rounded-lg border bg-slate-900 px-3 text-base text-slate-100 focus:border-brand-500 focus:outline-none'
const okBorder = 'border-slate-700'
const errBorder = 'border-red-500'

export default function AddressFields({ value, onChange }) {
  const [loading, setLoading] = useState(false)
  const [cepMsg, setCepMsg] = useState('')
  const [touched, setTouched] = useState({})
  const valueRef = useRef(value)
  valueRef.current = value
  const lastFetched = useRef('')

  const set = (patch) => onChange({ ...valueRef.current, ...patch })
  const markTouched = (f) => setTouched((t) => ({ ...t, [f]: true }))
  const invalid = (f) => touched[f] && !String(value[f] || '').trim()

  const cepDigits = (value.cep || '').replace(/\D/g, '')
  useEffect(() => {
    if (cepDigits.length !== 8) { setCepMsg(''); return undefined }
    if (lastFetched.current === cepDigits) return undefined
    let alive = true
    const timer = setTimeout(async () => {
      lastFetched.current = cepDigits
      setLoading(true); setCepMsg('')
      try {
        const r = await fetch(`https://viacep.com.br/ws/${cepDigits}/json/`)
        const data = await r.json()
        const parsed = parseViaCepResponse(data)
        if (!alive) return
        if (parsed) {
          onChange({
            ...valueRef.current, cep: maskCep(cepDigits),
            street: parsed.street || valueRef.current.street,
            neighborhood: parsed.neighborhood || valueRef.current.neighborhood,
            city: parsed.city || valueRef.current.city,
            state: parsed.state || valueRef.current.state,
          })
        } else {
          setCepMsg('CEP não encontrado. Verifique e tente novamente.')
        }
      } catch {
        if (alive) setCepMsg('Não foi possível buscar o CEP agora. Preencha manualmente.')
      } finally {
        if (alive) setLoading(false)
      }
    }, 500)
    return () => { alive = false; clearTimeout(timer) }
  }, [cepDigits])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="grid gap-3">
      <label className="block">
        <span className="mb-1 block text-sm font-medium text-slate-200">CEP</span>
        <div className="flex items-center gap-2">
          <input inputMode="numeric" value={value.cep} onBlur={() => markTouched('cep')}
            onChange={(e) => set({ cep: maskCep(e.target.value) })} placeholder="00000-000"
            className={`${fieldCls} ${invalid('cep') || (value.cep && !isValidCep(value.cep)) ? errBorder : okBorder}`} />
          {loading && <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-slate-600 border-t-brand-500" aria-label="Buscando CEP" />}
        </div>
        {cepMsg && <span className="mt-1 block text-xs text-amber-400">{cepMsg}</span>}
      </label>

      <label className="block">
        <span className="mb-1 block text-sm font-medium text-slate-200">Rua / Logradouro</span>
        <input value={value.street} onBlur={() => markTouched('street')}
          onChange={(e) => set({ street: e.target.value })} placeholder="Rua XV de Novembro"
          className={`${fieldCls} ${invalid('street') ? errBorder : okBorder}`} />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-200">Número</span>
          <input value={value.number} onBlur={() => markTouched('number')}
            onChange={(e) => set({ number: e.target.value })} placeholder="123"
            className={`${fieldCls} ${invalid('number') ? errBorder : okBorder}`} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-200">Complemento <span className="text-slate-500">(opcional)</span></span>
          <input value={value.complement} onChange={(e) => set({ complement: e.target.value })}
            placeholder="Sala 4" className={`${fieldCls} ${okBorder}`} />
        </label>
      </div>

      <label className="block">
        <span className="mb-1 block text-sm font-medium text-slate-200">Bairro</span>
        <input value={value.neighborhood} onBlur={() => markTouched('neighborhood')}
          onChange={(e) => set({ neighborhood: e.target.value })} placeholder="Centro"
          className={`${fieldCls} ${invalid('neighborhood') ? errBorder : okBorder}`} />
      </label>

      <div className="grid grid-cols-[1fr_5rem] gap-3">
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-200">Cidade</span>
          <input value={value.city} onBlur={() => markTouched('city')}
            onChange={(e) => set({ city: e.target.value })} placeholder="Curitiba"
            className={`${fieldCls} ${invalid('city') ? errBorder : okBorder}`} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-200">UF</span>
          <select value={value.state} onBlur={() => markTouched('state')}
            onChange={(e) => set({ state: e.target.value })}
            className={`${fieldCls} ${invalid('state') ? errBorder : okBorder}`}>
            <option value="">—</option>
            {UF_LIST.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
          </select>
        </label>
      </div>
    </div>
  )
}
