import { useState } from 'react'

// KL-152 P1 — bloco de código com "Copiar" (theme-aware, KL-87). Reusado no dashboard e no wizard.
// Scroll horizontal no mobile; nunca embute a API key crua (os snippets referenciam o secret do CI).

export function CopyBtn({ text, label = 'Copiar', className = '' }) {
  const [done, setDone] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setDone(true)
      setTimeout(() => setDone(false), 2000)
    } catch { /* clipboard bloqueado → o usuário copia manualmente */ }
  }
  return (
    <button type="button" onClick={copy}
      className={`inline-flex min-h-[32px] items-center rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-800 ${className}`}>
      {done ? 'Copiado ✓' : label}
    </button>
  )
}

export default function GateCodeBlock({ code, filename }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950/60">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-1.5">
        <span className="truncate font-mono text-xs text-slate-400">{filename || 'snippet'}</span>
        <CopyBtn text={code} />
      </div>
      <pre className="overflow-x-auto p-4 text-xs leading-relaxed text-slate-200"><code>{code}</code></pre>
    </div>
  )
}
